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

    def test_settles_on_the_bar_immediately_after_signal_day(self) -> None:
        # A later bar must never be mistaken for "the next session".
        self.store.record_day("2026-11-18", "balanced", picks())
        bars = [
            {"date": "2026-11-18", "open": 9.0, "close": 9.0},
            {"date": "2026-11-19", "open": 10.0, "close": 10.5},
            {"date": "2026-11-20", "open": 20.0, "close": 40.0},
        ]
        summary = self.store.refresh(lambda secid: bars, "2026-11-20", True)
        self.assertEqual(summary["resolvedTrades"], 1)
        self.assertEqual(summary["latest"]["entryDate"], "2026-11-19")
        self.assertEqual(summary["latest"]["positions"][0]["entryOpen"], 10.0)

    def test_does_not_guess_when_window_lost_the_signal_day(self) -> None:
        # The K-line window starts after the signal day: the true next bar is
        # unknown, so the trade must stay pending instead of settling on 11-25.
        self.store.record_day("2026-11-18", "balanced", picks())
        bars = [
            {"date": "2026-11-25", "open": 11.0, "close": 12.0},
            {"date": "2026-11-26", "open": 12.0, "close": 13.0},
        ]
        summary = self.store.refresh(lambda secid: bars, "2026-11-26", True)
        self.assertEqual(summary["resolvedTrades"], 0)
        self.assertEqual(summary["pendingTrades"], 1)
        self.assertEqual(summary["totalPnl"], 0.0)

    def test_marks_trade_unresolvable_when_signal_day_is_too_old(self) -> None:
        # Older than the settle window: drop it explicitly rather than persist a
        # wrong P&L computed from a much later session.
        self.store.record_day("2026-10-01", "balanced", picks())
        bars = [
            {"date": "2026-11-25", "open": 11.0, "close": 12.0},
            {"date": "2026-11-26", "open": 12.0, "close": 13.0},
        ]
        summary = self.store.refresh(lambda secid: bars, "2026-11-26", True)
        self.assertEqual(summary["resolvedTrades"], 0)
        self.assertEqual(summary["pendingTrades"], 0)
        self.assertEqual(summary["expiredTrades"], 1)
        self.assertIsNone(summary["latest"])
        self.assertIsNone(summary["latestPending"])
    def test_expires_old_trade_when_bars_never_reach_the_signal_day(self) -> None:
        # Suspended or delisted names keep returning old bars only; the trade must
        # not sit in "pending" forever.
        self.store.record_day("2026-10-01", "balanced", picks())
        bars = [{"date": "2026-09-30", "open": 9.0, "close": 9.0}]
        summary = self.store.refresh(lambda secid: bars, "2026-11-26", True)
        self.assertEqual(summary["expiredTrades"], 1)
        self.assertEqual(summary["pendingTrades"], 0)
        self.assertEqual(summary["resolvedTrades"], 0)

    def test_empty_kline_never_permanently_expires_a_trade(self) -> None:
        # A transient empty response must be retried, not treated as proof that the
        # window moved past the signal day.
        self.store.record_day("2026-10-01", "balanced", picks())
        summary = self.store.refresh(lambda secid: [], "2026-11-26", True)
        self.assertEqual(summary["expiredTrades"], 0)
        self.assertEqual(summary["pendingTrades"], 1)


if __name__ == "__main__":
    unittest.main()
