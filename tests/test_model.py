"""Tests for cross-sectional diagnostics."""

from __future__ import annotations

import unittest

import numpy as np

from app.services.model import _daily_metrics


class DailyMetricsTests(unittest.TestCase):
    def test_perfect_within_day_ranking(self) -> None:
        rows = [{"date": "2026-01-05"}] * 8 + [{"date": "2026-01-06"}] * 8
        probabilities = np.asarray([0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8] * 2)
        returns = np.asarray(
            [-0.04, -0.03, -0.02, -0.01, 0.01, 0.02, 0.03, 0.04] * 2
        )
        result = _daily_metrics(rows, probabilities, returns)
        self.assertEqual(result["ic_days"], 2)
        self.assertAlmostEqual(result["daily_auc"], 1.0)
        self.assertAlmostEqual(result["rank_ic"], 1.0)

    def test_insufficient_day_is_skipped(self) -> None:
        rows = [{"date": "2026-01-05"}] * 3
        result = _daily_metrics(
            rows,
            np.asarray([0.2, 0.5, 0.8]),
            np.asarray([-0.01, 0.0, 0.02]),
        )
        self.assertEqual(result["ic_days"], 0)
        self.assertIsNone(result["daily_auc"])


if __name__ == "__main__":
    unittest.main()
