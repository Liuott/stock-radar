"""Tests for the fixed 10,000 CNY paper portfolio."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from app.services.paper import PaperStore


def picks() -> list[dict]:
    return [
        {
            "code": "600000",
            "name": "浦发银行",
            "secid": "1.600000",
            "probability": 0.56,
            "score": 70,
        },
        {
            "code": "000001",
            "name": "平安银行",
            "secid": "0.000001",
            "probability": 0.54,
            "score": 62,
        },
    ]


class PaperStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.store = PaperStore(Path(self.temp.name) / "paper.json")

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_records_equal_weight_allocations(self) -> None:
        self.store.record_day("2026-11-18", "balanced", picks())
        summary = self.store.summary()
        self.assertEqual(summary["initialCapital"], 10000)
        self.assertEqual(summary["pendingTrades"], 1)
        self.assertEqual(len(summary["latestPending"]["positions"]), 2)
        self.assertEqual(summary["latestPending"]["positions"][0]["allocation"], 5000)

    def test_resolves_next_open_to_next_close(self) -> None:
        self.store.record_day("2026-11-18", "balanced", picks())
        bars = [
            {"date": "2026-11-18", "open": 9.0, "close": 9.0},
            {"date": "2026-11-19", "open": 10.0, "close": 11.0},
        ]
        summary = self.store.refresh(lambda secid: bars, "2026-11-19", True)
        self.assertEqual(summary["resolvedTrades"], 1)
        self.assertAlmostEqual(summary["totalPnl"], 985.0)
        self.assertAlmostEqual(summary["equity"], 10985.0)
        self.assertEqual(summary["latest"]["positions"][0]["entryOpen"], 10.0)

    def test_does_not_resolve_before_close(self) -> None:
        self.store.record_day("2026-11-18", "balanced", picks())
        bars = [
            {"date": "2026-11-18", "open": 9.0, "close": 9.0},
            {"date": "2026-11-19", "open": 10.0, "close": 11.0},
        ]
        summary = self.store.refresh(lambda secid: bars, "2026-11-19", False)
        self.assertEqual(summary["pendingTrades"], 1)
        self.assertEqual(summary["resolvedTrades"], 0)

    def test_no_picks_does_not_create_trade(self) -> None:
        self.store.record_day("2026-11-18", "balanced", [])
        summary = self.store.summary()
        self.assertEqual(summary["pendingTrades"], 0)
        self.assertEqual(summary["resolvedTrades"], 0)


if __name__ == "__main__":
    unittest.main()
