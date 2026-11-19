"""Snapshot disk-cache fallback tests (no network)."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from app.cache import DiskJsonCache
from app.services.market import MarketService


class FailingProvider:
    def fetch_snapshot(self):
        raise RuntimeError("source down")


class SnapshotFallbackTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.service = MarketService(provider=FailingProvider())
        # The service may have loaded the real local cache during __init__;
        # isolate the test from the user's runtime data.
        self.service._snapshot = None
        self.service._snapshot_at = 0.0
        self.service._snapshot_stale = False
        self.service._snapshot_failed_at = 0.0
        self.service.snapshot_cache = DiskJsonCache(Path(self.temp.name))
        self.rows = [{"code": f"{index:06d}", "name": "x", "price": 1.0} for index in range(120)]

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_uses_disk_snapshot_when_source_is_down(self) -> None:
        self.service.snapshot_cache.write(
            "snapshot",
            {"savedAt": 1.0, "rows": self.rows, "meta": {"coverage": 0.99}},
        )
        rows = self.service.get_snapshot(force=True)
        self.assertEqual(len(rows), 120)
        self.assertTrue(self.service._snapshot_stale)
        self.assertEqual(self.service._snapshot_meta.get("coverage"), 0.99)

    def test_missing_cache_still_raises(self) -> None:
        with self.assertRaises(RuntimeError):
            self.service.get_snapshot(force=True)


if __name__ == "__main__":
    unittest.main()
