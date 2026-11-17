"""Small JSON cache used for daily bars and index history."""

from __future__ import annotations

import json
import os
import re
import tempfile
import threading
import time
from pathlib import Path
from typing import Callable


class DiskJsonCache:
    def __init__(self, directory: Path):
        self.directory = Path(directory)
        self.directory.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()

    def _path(self, key: str) -> Path:
        safe = re.sub(r"[^A-Za-z0-9_.-]+", "_", key)
        return self.directory / f"{safe}.json"

    def read(self, key: str, ttl_seconds: int) -> tuple[object | None, bool]:
        path = self._path(key)
        if not path.exists():
            return None, False
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            fetched_at = float(payload.get("fetched_at") or 0)
            fresh = (time.time() - fetched_at) <= ttl_seconds
            return payload.get("data"), fresh
        except (OSError, ValueError, TypeError):
            return None, False

    def write(self, key: str, data: object) -> None:
        path = self._path(key)
        payload = {"fetched_at": time.time(), "data": data}
        with self._lock:
            handle, temp_name = tempfile.mkstemp(
                prefix=f".{path.stem}.", suffix=".tmp", dir=str(self.directory)
            )
            try:
                with os.fdopen(handle, "w", encoding="utf-8") as stream:
                    json.dump(payload, stream, ensure_ascii=False, separators=(",", ":"))
                os.replace(temp_name, path)
            finally:
                if os.path.exists(temp_name):
                    try:
                        os.unlink(temp_name)
                    except OSError:
                        pass

    def get_or_fetch(
        self,
        key: str,
        ttl_seconds: int,
        fetcher: Callable[[], object],
        allow_stale: bool = True,
    ) -> tuple[object, bool]:
        cached, fresh = self.read(key, ttl_seconds)
        if cached is not None and fresh:
            return cached, False
        try:
            data = fetcher()
            if data is None:
                raise ValueError("empty cache result")
            self.write(key, data)
            return data, True
        except Exception:
            if cached is not None and allow_stale:
                return cached, True
            raise
