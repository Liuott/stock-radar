"""Persistent watchlist with optional plan levels."""

from __future__ import annotations

import json
import os
import tempfile
import threading
from datetime import datetime
from pathlib import Path

from app.config import CN_TZ


class WatchlistStore:
    def __init__(self, path: Path, limit: int = 30):
        self.path = Path(path)
        self.limit = limit
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        if not self.path.exists():
            self._write([])

    def _read(self) -> list[dict]:
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
            if isinstance(payload, list):
                return [item for item in payload if isinstance(item, dict) and item.get("code")]
        except (OSError, ValueError):
            pass
        return []

    def _write(self, items: list[dict]) -> None:
        handle, temp_name = tempfile.mkstemp(
            prefix=".watchlist.", suffix=".tmp", dir=str(self.path.parent)
        )
        try:
            with os.fdopen(handle, "w", encoding="utf-8") as stream:
                json.dump(items, stream, ensure_ascii=False, indent=2)
            os.replace(temp_name, self.path)
        finally:
            if os.path.exists(temp_name):
                try:
                    os.unlink(temp_name)
                except OSError:
                    pass

    def list(self) -> list[dict]:
        with self._lock:
            return self._read()

    def add(self, item: dict) -> list[dict]:
        code = str(item.get("code") or "").strip()
        if not code:
            raise ValueError("缺少股票代码")
        with self._lock:
            items = self._read()
            existing = next((row for row in items if row.get("code") == code), None)
            if existing:
                for key in ("name", "entry_low", "entry_high", "stop", "target1", "profile", "note", "plan_source"):
                    if item.get(key) is not None:
                        existing[key] = item[key]
                existing["updated_at"] = datetime.now(CN_TZ).isoformat(timespec="seconds")
            else:
                if len(items) >= self.limit:
                    raise ValueError(f"自选最多 {self.limit} 只")
                record = {
                    "code": code,
                    "name": str(item.get("name") or code),
                    "added_at": datetime.now(CN_TZ).isoformat(timespec="seconds"),
                    "entry_low": item.get("entry_low"),
                    "entry_high": item.get("entry_high"),
                    "stop": item.get("stop"),
                    "target1": item.get("target1"),
                    "profile": item.get("profile"),
                    "plan_source": item.get("plan_source"),
                    "note": item.get("note"),
                }
                items.append(record)
            self._write(items)
            return items

    def remove(self, code: str) -> list[dict]:
        with self._lock:
            items = [row for row in self._read() if row.get("code") != code]
            self._write(items)
            return items

    def clear(self) -> None:
        with self._lock:
            self._write([])
