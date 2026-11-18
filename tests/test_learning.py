"""Tests for the n-2 -> n-1 feedback calibration store."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from app.services.learning import LearningStore


def sample_pick(code: str = "600000", price: float = 10.0) -> dict:
    return {
        "code": code,
        "name": "测试股",
        "secid": "1.600000",
        "price": price,
        "probability_raw": 0.5,
        "probability": 0.5,
        "score": 60.0,
        "subscores": [
            {"key": "trend", "score": 70},
            {"key": "momentum", "score": 60},
        ],
    }


class LearningStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / "learning.json"
        self.store = LearningStore(self.path)

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_resolves_next_bar_and_calibrates_up(self) -> None:
        self.store.record_snapshot("2026-11-16", "balanced", [sample_pick()])
        bars = [
            {"date": "2026-11-16", "close": 10.0},
            {"date": "2026-11-17", "close": 11.0},
        ]
        summary = self.store.refresh(lambda secid: bars, "2026-11-18", False)
        self.assertEqual(summary["samples"], 1)
        self.assertEqual(summary["hitRate"], 1.0)
        self.assertEqual(summary["previous"][0]["actualUp"], 1)
        adjusted, delta, bias = self.store.calibrate(0.5)
        self.assertGreater(adjusted, 0.5)
        self.assertGreater(delta, 0)
        self.assertGreater(bias, 0)
        self.assertLessEqual(bias, 0.30)

    def test_open_session_does_not_resolve_today(self) -> None:
        self.store.record_snapshot("2026-11-16", "balanced", [sample_pick()])
        bars = [
            {"date": "2026-11-16", "close": 10.0},
            {"date": "2026-11-17", "close": 11.0},
        ]
        summary = self.store.refresh(lambda secid: bars, "2026-11-17", True)
        self.assertEqual(summary["samples"], 0)

    def test_missing_next_bar_stays_unresolved(self) -> None:
        self.store.record_snapshot("2026-11-17", "balanced", [sample_pick()])
        summary = self.store.refresh(
            lambda secid: [{"date": "2026-11-17", "close": 10.0}],
            "2026-11-17",
            False,
        )
        self.assertEqual(summary["samples"], 0)


if __name__ == "__main__":
    unittest.main()
