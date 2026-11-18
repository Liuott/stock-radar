"""A small fixed-capital paper portfolio for daily candidate tracking.

Rules:
- Every recorded candidate day allocates CNY 10,000 equally across the picks.
- Entry is the next trading day's open; exit is that day's close.
- Fractional shares are used so the research simulation does not get distorted
  by the 100-share lot size. Costs are approximated at 0.15% round trip.
- A pending trade resolves after the next session has closed; same-day partial
  bars are never used as the final result.
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
    def __init__(self, path: Path, initial_capital: float = 10_000.0, cost_rate: float = 0.0015):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.initial_capital = initial_capital
        self.cost_rate = cost_rate
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
                    next_bar = next(
                        (bar for bar in bars if bar.get("date", "") > signal_date),
                        None,
                    )
                    if not next_bar:
                        missing = True
                        break
                    eval_date = next_bar.get("date") or ""
                    if eval_date > today:
                        missing = True
                        break
                    if eval_date == today and not after_close:
                        missing = True
                        break
                    open_price = float(next_bar.get("open") or 0)
                    close_price = float(next_bar.get("close") or 0)
                    if open_price <= 0 or close_price <= 0:
                        missing = True
                        break
                    allocation = float(position.get("allocation") or 0)
                    pnl = allocation * (close_price / open_price - 1.0) - allocation * self.cost_rate
                    evaluated.append(
                        {
                            **position,
                            "entryDate": eval_date,
                            "entryOpen": round(open_price, 3),
                            "exitClose": round(close_price, 3),
                            "shares": round(allocation / open_price, 4),
                            "returnPct": round(pnl / allocation * 100, 3) if allocation else 0.0,
                            "pnl": round(pnl, 2),
                        }
                    )
                if missing or not evaluated:
                    continue
                total_pnl = sum(float(item["pnl"]) for item in evaluated)
                trade["positions"] = evaluated
                trade["status"] = "resolved"
                trade["entryDate"] = min(item["entryDate"] for item in evaluated)
                trade["exitDate"] = max(item["entryDate"] for item in evaluated)
                trade["totalPnl"] = round(total_pnl, 2)
                trade["totalReturnPct"] = round(total_pnl / self.initial_capital * 100, 3)
                changed = True
            if changed:
                self._save_locked()
            return self._summary_locked()

    def _summary_locked(self) -> dict:
        trades = self._state.get("trades") or []
        resolved = [trade for trade in trades if trade.get("status") == "resolved"]
        pending = [trade for trade in trades if trade.get("status") == "pending"]
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
            "winRate": round(wins / len(resolved), 4) if resolved else None,
            "latest": resolved[-1] if resolved else None,
            "latestPending": pending[-1] if pending else None,
            "recentTrades": resolved[-8:][::-1],
            "note": "每日固定 10,000 元等权模拟；次日开盘买入、次日收盘计值，含 0.15% 估算成本；不复利，忽略 100 股整数限制。",
        }

    def summary(self) -> dict:
        with self._lock:
            return self._summary_locked()
