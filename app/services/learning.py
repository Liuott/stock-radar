"""Online learning from resolved candidates.

The loop is deliberately simple and conservative:

    candidates at n-2  ->  next trading day's close at n-1  ->  calibrate n

We persist the candidates that were actually shown, resolve them against the
next daily bar, and fit an intercept-only recalibration with a strong prior.
Factor efficacy is displayed as a diagnostic, not used to rewrite factor
weights: with a handful of picks per day, automatic reweighting would overfit.
"""

from __future__ import annotations

import json
import math
import os
import tempfile
from datetime import datetime
from pathlib import Path
from threading import Lock
from typing import Callable

from app.config import CN_TZ, FACTOR_META, now_cn


def _sigmoid(value: float) -> float:
    value = max(-30.0, min(30.0, value))
    return 1.0 / (1.0 + math.exp(-value))


def _logit(value: float) -> float:
    value = max(0.001, min(0.999, value))
    return math.log(value / (1.0 - value))


def _parse_date(value: str):
    try:
        return datetime.strptime(value, "%Y-%m-%d").date()
    except (TypeError, ValueError):
        return None


class LearningStore:
    def __init__(self, path: Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = Lock()
        self._state = self._load()

    @staticmethod
    def _default_state() -> dict:
        return {
            "version": 1,
            "snapshots": [],
            "resolved": [],
            "calibration": {
                "b": 0.0,
                "samples": 0,
                "hitRate": None,
                "avgPredicted": None,
                "avgReturn": None,
                "adjustmentPpAt50": 0.0,
                "updatedAt": None,
            },
        }

    def _load(self) -> dict:
        if not self.path.exists():
            return self._default_state()
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
            if not isinstance(payload, dict):
                raise ValueError("state is not an object")
            state = self._default_state()
            state.update(payload)
            if not isinstance(state.get("snapshots"), list):
                state["snapshots"] = []
            if not isinstance(state.get("resolved"), list):
                state["resolved"] = []
            if not isinstance(state.get("calibration"), dict):
                state["calibration"] = self._default_state()["calibration"]
            return state
        except (OSError, ValueError):
            try:
                self.path.replace(self.path.with_suffix(".broken.json"))
            except OSError:
                pass
            return self._default_state()

    def _save_locked(self) -> None:
        handle, temp_name = tempfile.mkstemp(
            prefix=".learning.", suffix=".tmp", dir=str(self.path.parent)
        )
        try:
            with os.fdopen(handle, "w", encoding="utf-8") as stream:
                json.dump(self._state, stream, ensure_ascii=False, separators=(",", ":"))
            os.replace(temp_name, self.path)
        finally:
            if os.path.exists(temp_name):
                try:
                    os.unlink(temp_name)
                except OSError:
                    pass

    def record_snapshot(self, date: str, profile: str, picks: list[dict]) -> None:
        if not picks:
            return
        with self._lock:
            clean_picks = []
            for pick in picks[:10]:
                subscores = {
                    item.get("key"): item.get("score")
                    for item in (pick.get("subscores") or [])
                    if item.get("key")
                }
                clean_picks.append(
                    {
                        "code": pick.get("code"),
                        "name": pick.get("name"),
                        "secid": pick.get("secid"),
                        "price": pick.get("price"),
                        "probability_raw": pick.get("probability_raw", pick.get("probability")),
                        "probability": pick.get("probability"),
                        "score": pick.get("score"),
                        "subscores": subscores,
                    }
                )
            snapshot = {
                "date": date,
                "profile": profile,
                "createdAt": datetime.now(CN_TZ).isoformat(timespec="seconds"),
                "resolved": False,
                "picks": clean_picks,
            }
            snapshots = [
                item for item in self._state["snapshots"]
                if not (item.get("date") == date and item.get("profile") == profile)
            ]
            snapshots.append(snapshot)
            snapshots.sort(key=lambda item: (item.get("date") or "", item.get("profile") or ""))
            self._state["snapshots"] = snapshots[-60:]
            self._save_locked()

    def refresh(
        self,
        kline_loader: Callable[[str], list[dict]],
        today: str,
        market_open: bool,
    ) -> dict:
        with self._lock:
            changed = False
            resolved_keys = {
                (item.get("snapshotDate"), item.get("code"))
                for item in self._state["resolved"]
            }
            done_keys = set(resolved_keys)
            today_date = _parse_date(today)
            for snapshot in self._state["snapshots"]:
                if snapshot.get("resolved"):
                    continue
                snapshot_date = snapshot.get("date") or ""
                if snapshot_date >= today:
                    continue
                snapshot_day = _parse_date(snapshot_date)
                if snapshot_day and today_date and (today_date - snapshot_day).days > 14:
                    snapshot["resolved"] = True
                    changed = True
                    continue
                for pick in snapshot.get("picks") or []:
                    key = (snapshot_date, pick.get("code"))
                    if key in resolved_keys or key in done_keys:
                        continue
                    secid = pick.get("secid")
                    entry = pick.get("price")
                    if not secid or not entry:
                        continue
                    try:
                        bars = kline_loader(secid)
                    except Exception:  # noqa: BLE001 - try again next refresh
                        continue
                    next_bar = next(
                        (bar for bar in bars if bar.get("date", "") > snapshot_date),
                        None,
                    )
                    if not next_bar:
                        continue
                    eval_date = next_bar.get("date") or ""
                    # Only completed prior sessions are used. Using today's
                    # partial bar (or a close cached before 15:00) would leak
                    # an incomplete outcome into the calibration.
                    if eval_date >= today:
                        continue
                    next_close = float(next_bar.get("close") or 0)
                    if next_close <= 0:
                        continue
                    actual_return = next_close / float(entry) - 1.0
                    self._state["resolved"].append(
                        {
                            "snapshotDate": snapshot_date,
                            "evalDate": eval_date,
                            "code": pick.get("code"),
                            "name": pick.get("name"),
                            "profile": snapshot.get("profile"),
                            "entryPrice": round(float(entry), 3),
                            "nextClose": round(next_close, 3),
                            "predicted": round(float(pick.get("probability_raw") or 0.5), 4),
                            "score": pick.get("score"),
                            "actualReturn": round(actual_return, 5),
                            "actualUp": 1 if actual_return > 0 else 0,
                            "subscores": pick.get("subscores") or {},
                        }
                    )
                    resolved_keys.add(key)
                    done_keys.add(key)
                    changed = True
                if all(
                    (snapshot_date, pick.get("code")) in done_keys
                    for pick in (snapshot.get("picks") or [])
                ):
                    snapshot["resolved"] = True
                    changed = True
            if changed:
                self._state["resolved"] = self._state["resolved"][-1000:]
                self._recalculate_locked(today)
                self._save_locked()
            return self._summary_locked(today)

    def _recalculate_locked(self, today: str) -> None:
        records = [
            item for item in self._state["resolved"]
            if item.get("actualUp") in (0, 1) and item.get("predicted") is not None
        ]
        if not records:
            self._state["calibration"] = {
                "b": 0.0,
                "samples": 0,
                "hitRate": None,
                "avgPredicted": None,
                "avgReturn": None,
                "adjustmentPpAt50": 0.0,
                "updatedAt": datetime.now(CN_TZ).isoformat(timespec="seconds"),
            }
            return
        today_date = _parse_date(today)
        weighted_hit = 0.0
        weighted_pred = 0.0
        weight_sum = 0.0
        returns = []
        for item in records:
            snapshot_day = _parse_date(item.get("snapshotDate") or "")
            age = max(0, (today_date - snapshot_day).days) if today_date and snapshot_day else 30
            weight = max(0.05, 0.5 ** (age / 15.0))
            weighted_hit += weight * float(item["actualUp"])
            weighted_pred += weight * float(item["predicted"])
            weight_sum += weight
            returns.append(float(item.get("actualReturn") or 0.0))
        hit = weighted_hit / weight_sum if weight_sum else 0.5
        avg_pred = weighted_pred / weight_sum if weight_sum else 0.5
        raw_bias = _logit(hit) - _logit(avg_pred)
        n = len(records)
        # Strong shrinkage: five picks a day is far too little to trust an
        # unconstrained online model. At 30 samples the prior still carries 50%.
        b = raw_bias * (n / (n + 30.0))
        b = max(-0.30, min(0.30, b))
        self._state["calibration"] = {
            "b": round(b, 4),
            "samples": n,
            "hitRate": round(sum(int(item["actualUp"]) for item in records) / n, 4),
            "avgPredicted": round(sum(float(item["predicted"]) for item in records) / n, 4),
            "avgReturn": round(sum(returns) / n, 5),
            "adjustmentPpAt50": round((_sigmoid(b) - 0.5) * 100, 2),
            "updatedAt": datetime.now(CN_TZ).isoformat(timespec="seconds"),
        }

    def _summary_locked(self, today: str) -> dict:
        calibration = self._state.get("calibration") or {}
        resolved = [
            item for item in self._state["resolved"]
            if item.get("actualUp") in (0, 1)
        ]
        previous = []
        if resolved:
            latest_snapshot = max(item.get("snapshotDate") or "" for item in resolved)
            latest = [item for item in resolved if item.get("snapshotDate") == latest_snapshot]
            seen = set()
            for item in latest:
                if item.get("code") in seen:
                    continue
                seen.add(item.get("code"))
                previous.append(
                    {
                        "code": item.get("code"),
                        "name": item.get("name"),
                        "snapshotDate": item.get("snapshotDate"),
                        "evalDate": item.get("evalDate"),
                        "predicted": item.get("predicted"),
                        "predictedPct": round(float(item.get("predicted") or 0.5) * 100, 1),
                        "actualReturn": item.get("actualReturn"),
                        "actualPct": round(float(item.get("actualReturn") or 0.0) * 100, 2),
                        "actualUp": item.get("actualUp"),
                        "score": item.get("score"),
                        "profile": item.get("profile"),
                    }
                )
            previous.sort(key=lambda item: item.get("actualReturn") or 0, reverse=True)

        days: dict[str, list[dict]] = {}
        for item in resolved:
            days.setdefault(item.get("snapshotDate") or "", []).append(item)
        recent_days = []
        for date in sorted(days)[-10:]:
            rows = days[date]
            recent_days.append(
                {
                    "date": date,
                    "evalDate": rows[0].get("evalDate"),
                    "count": len(rows),
                    "hitRate": round(sum(int(row["actualUp"]) for row in rows) / len(rows), 4),
                    "avgReturn": round(
                        sum(float(row.get("actualReturn") or 0.0) for row in rows) / len(rows), 5
                    ),
                }
            )

        factor_values: dict[str, dict[str, list[float]]] = {}
        for item in resolved:
            for key, value in (item.get("subscores") or {}).items():
                if value is None:
                    continue
                group = "win" if item["actualUp"] else "loss"
                factor_values.setdefault(key, {"win": [], "loss": []})[group].append(float(value))
        factors = []
        for key, groups in factor_values.items():
            win = groups["win"]
            loss = groups["loss"]
            if not win or not loss:
                continue
            win_avg = sum(win) / len(win)
            loss_avg = sum(loss) / len(loss)
            factors.append(
                {
                    "key": key,
                    "label": (FACTOR_META.get(key) or {}).get("label", key),
                    "winAvg": round(win_avg, 1),
                    "lossAvg": round(loss_avg, 1),
                    "diff": round(win_avg - loss_avg, 1),
                    "samples": len(win) + len(loss),
                }
            )
        factors.sort(key=lambda item: abs(item["diff"]), reverse=True)

        return {
            "available": bool(resolved),
            "samples": len(resolved),
            "hitRate": calibration.get("hitRate"),
            "avgPredicted": calibration.get("avgPredicted"),
            "avgReturn": calibration.get("avgReturn"),
            "calibration": calibration,
            "previous": previous,
            "previousDate": previous[0]["snapshotDate"] if previous else None,
            "previousEvalDate": previous[0]["evalDate"] if previous else None,
            "recentDays": recent_days,
            "factors": factors[:8],
            "note": (
                "还没有可复盘样本。先让工具在交易日记录候选，下一交易日收盘后会自动复盘。"
                if not resolved
                else "只做温和的概率校准，不自动改因子权重；样本少时收缩很强，"
                "结果只能说明最近几笔候选的偏差，不能外推为稳定收益。"
            ),
            "method": "n-2 候选 → n-1 次日收盘结果 → 加权命中率/预测偏差 → 截距校准",
        }

    def summary(self, today: str | None = None) -> dict:
        with self._lock:
            return self._summary_locked(today or now_cn().strftime("%Y-%m-%d"))

    def calibrate(self, probability: float) -> tuple[float, float, float]:
        with self._lock:
            b = float((self._state.get("calibration") or {}).get("b") or 0.0)
        adjusted = _sigmoid(_logit(probability) + b)
        adjusted = max(0.05, min(0.95, adjusted))
        return round(adjusted, 4), round((adjusted - probability) * 100, 2), round(b, 4)
