"""A small fixed-capital paper portfolio for daily candidate tracking.

Rules:
- Every recorded candidate day allocates CNY 10,000 equally across the picks.
- A-share T+1: entry is the signal day's close; exit is the next trading day's
  close. (Buying at next open and selling that same close would be illegal.)
- Fractional shares are used so the research simulation does not get distorted
  by the 100-share lot size. Costs are approximated at 0.15% round trip.
- A pending trade resolves after the next session has closed; same-day partial
  bars are never used as the final result.
- Settlement only accepts the signal bar and the bar immediately after it. When
  the K-line window no longer reaches back to the signal day the trade is marked
  ``unresolvable`` instead of being settled on a wrong, much later bar.
"""

from __future__ import annotations

import json
import os
import tempfile
from datetime import datetime
from pathlib import Path
from threading import Lock
from typing import Callable

from app.config import CN_TZ


class PaperStore:
    def __init__(
        self,
        path: Path,
        initial_capital: float = 10_000.0,
        cost_rate: float = 0.0015,
        max_settle_lag_days: int = 20,
    ):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.initial_capital = initial_capital
        self.cost_rate = cost_rate
        self.max_settle_lag_days = max_settle_lag_days
        self._lock = Lock()
        self._state = self._load()

    @staticmethod
    def _default_state() -> dict:
        return {"version": 1, "trades": []}

    def _load(self) -> dict:
        if not self.path.exists():
            return self._default_state()
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
            if not isinstance(payload, dict) or not isinstance(payload.get("trades"), list):
                raise ValueError("invalid paper state")
            return payload
        except (OSError, ValueError):
            try:
                self.path.replace(self.path.with_suffix(".broken.json"))
            except OSError:
                pass
            return self._default_state()

    def _save_locked(self) -> None:
        handle, temp_name = tempfile.mkstemp(
            prefix=".paper.", suffix=".tmp", dir=str(self.path.parent)
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

    def record_day(self, date: str, profile: str, picks: list[dict]) -> None:
        if not picks:
            return
        with self._lock:
            existing = next(
                (
                    trade for trade in self._state["trades"]
                    if trade.get("signalDate") == date and trade.get("profile") == profile
                ),
                None,
            )
            if existing and existing.get("status") == "resolved":
                return
            count = min(len(picks), 10)
            allocation = self.initial_capital / count
            positions = [
                {
                    "code": pick.get("code"),
                    "name": pick.get("name"),
                    "secid": pick.get("secid"),
                    "allocation": round(allocation, 2),
                    "weightPct": round(100.0 / count, 2),
                    "predicted": pick.get("probability"),
                    "score": pick.get("score"),
                }
                for pick in picks[:count]
            ]
            trade = {
                "id": f"{date}-{profile}",
                "signalDate": date,
                "profile": profile,
                "capital": self.initial_capital,
                "status": "pending",
                "createdAt": datetime.now(CN_TZ).isoformat(timespec="seconds"),
                "entryDate": None,
                "exitDate": None,
                "totalPnl": None,
                "totalReturnPct": None,
                "positions": positions,
            }
            trades = [
                item for item in self._state["trades"]
                if not (item.get("signalDate") == date and item.get("profile") == profile)
            ]
            trades.append(trade)
            trades.sort(key=lambda item: (item.get("signalDate") or "", item.get("profile") or ""))
            self._state["trades"] = trades[-120:]
            self._save_locked()

    def refresh(
        self,
        kline_loader: Callable[[str], list[dict]],
        today: str,
        after_close: bool,
    ) -> dict:
        with self._lock:
            changed = False
            for trade in self._state["trades"]:
                if trade.get("status") != "pending":
                    continue
                signal_date = trade.get("signalDate") or ""
                if signal_date >= today:
                    continue
                evaluated = []
                missing = False
                expired = False
                expiry_reason = ""
                for position in trade.get("positions") or []:
                    secid = position.get("secid")
                    if not secid:
                        missing = True
                        break
                    try:
                        bars = kline_loader(secid)
                    except Exception:  # noqa: BLE001 - retry next refresh
                        missing = True
                        break
                    entry_bar = next(
                        (bar for bar in bars if bar.get("date") == signal_date),
                        None,
                    )
                    next_bar, reason = self._bar_after(bars, signal_date)
                    if entry_bar is None:
                        stale = (
                            self._calendar_days(signal_date, today)
                            > self.max_settle_lag_days
                        )
                        if stale and bars:
                            expired = True
                            expiry_reason = (
                                "信号日已超出可回溯的日K窗口，无法确认信号日收盘价"
                                if reason == "window"
                                else "该标的日K长期未包含信号日"
                            )
                        else:
                            missing = True
                        break
                    if next_bar is None:
                        # Only real bars can prove the trade is past the window we can
                        # still reach. An empty response is a transient data problem,
                        # and expiring on it would destroy the record for good.
                        stale = (
                            self._calendar_days(signal_date, today)
                            > self.max_settle_lag_days
                        )
                        if stale and bars:
                            expired = True
                            expiry_reason = (
                                "信号日已超出可回溯的日K窗口，无法确认紧邻的次日 bar"
                                if reason == "window"
                                else "该标的日K长期未推进到信号日之后"
                            )
                        else:
                            missing = True
                        break
                    eval_date = next_bar.get("date") or ""
                    if eval_date > today:
                        missing = True
                        break
                    if eval_date == today and not after_close:
                        missing = True
                        break
                    entry_close = float(entry_bar.get("close") or 0)
                    close_price = float(next_bar.get("close") or 0)
                    if entry_close <= 0 or close_price <= 0:
                        missing = True
                        break
                    allocation = float(position.get("allocation") or 0)
                    pnl = allocation * (close_price / entry_close - 1.0) - allocation * self.cost_rate
                    evaluated.append(
                        {
                            **position,
                            "entryDate": signal_date,
                            "entryClose": round(entry_close, 3),
                            "exitDate": eval_date,
                            "exitClose": round(close_price, 3),
                            "shares": round(allocation / entry_close, 4),
                            "returnPct": round(pnl / allocation * 100, 3) if allocation else 0.0,
                            "pnl": round(pnl, 2),
                        }
                    )
                if expired:
                    trade["status"] = "unresolvable"
                    trade["reason"] = f"{expiry_reason}；为避免用错价格结算，本条标记为失效"
                    trade["totalPnl"] = None
                    changed = True
                    continue
                if missing or not evaluated:
                    continue
                total_pnl = sum(float(item["pnl"]) for item in evaluated)
                trade["positions"] = evaluated
                trade["status"] = "resolved"
                trade["entryDate"] = min(item["entryDate"] for item in evaluated)
                trade["exitDate"] = max(item["exitDate"] for item in evaluated)
                trade["totalPnl"] = round(total_pnl, 2)
                trade["totalReturnPct"] = round(total_pnl / self.initial_capital * 100, 3)
                changed = True
            if changed:
                self._save_locked()
            return self._summary_locked()

    @staticmethod
    def _bar_after(bars: list[dict], signal_date: str) -> tuple[dict | None, str]:
        """Return the bar that directly follows ``signal_date``.

        The K-line window is short (about 20 sessions), so a pending trade from
        weeks ago no longer contains the signal day. In that case the caller must
        not guess: taking the first newer bar would silently settle the trade on
        the wrong session. ``reason`` is ``"ok"``, ``"pending"`` (the bar does not
        exist yet, or the response was empty) or ``"window"`` (the window already
        starts after the signal day, so the true bar is unknowable).
        """
        dates = [bar.get("date") or "" for bar in bars]
        if not dates:
            # An empty response is a transient data problem, not evidence that the
            # window has moved past the signal day. Expiring on it would destroy the
            # record for good, so treat it as "not available yet".
            return None, "pending"
        index = -1
        for position, value in enumerate(dates):
            if value == signal_date:
                index = position
        if index >= 0:
            if index + 1 < len(bars):
                return bars[index + 1], "ok"
            return None, "pending"
        if dates[-1] and dates[-1] <= signal_date:
            return None, "pending"
        return None, "window"

    @staticmethod
    def _calendar_days(start: str, end: str) -> int:
        try:
            first = datetime.strptime(start[:10], "%Y-%m-%d").date()
            second = datetime.strptime(end[:10], "%Y-%m-%d").date()
        except (TypeError, ValueError):
            return 0
        return (second - first).days

    def _summary_locked(self) -> dict:
        trades = self._state.get("trades") or []
        resolved = [trade for trade in trades if trade.get("status") == "resolved"]
        pending = [trade for trade in trades if trade.get("status") == "pending"]
        expired = [trade for trade in trades if trade.get("status") == "unresolvable"]
        total_pnl = sum(float(trade.get("totalPnl") or 0.0) for trade in resolved)
        wins = sum(1 for trade in resolved if float(trade.get("totalPnl") or 0) > 0)
        return {
            "initialCapital": self.initial_capital,
            "costRate": self.cost_rate,
            "equity": round(self.initial_capital + total_pnl, 2),
            "totalPnl": round(total_pnl, 2),
            "totalReturnPct": round(total_pnl / self.initial_capital * 100, 3),
            "resolvedTrades": len(resolved),
            "pendingTrades": len(pending),
            "expiredTrades": len(expired),
            "winRate": round(wins / len(resolved), 4) if resolved else None,
            "latest": resolved[-1] if resolved else None,
            "latestPending": pending[-1] if pending else None,
            "recentTrades": resolved[-8:][::-1],
            "note": (
                "每日固定 10,000 元等权模拟；信号日收盘买入、下一交易日收盘计值（T+1），"
                "含 0.15% 估算成本；不复利，忽略 100 股整数限制；"
                "超出日K窗口仍无法确认次日 bar 的旧记录标记为失效，不参与统计。"
            ),
        }

    def summary(self) -> dict:
        with self._lock:
            return self._summary_locked()
