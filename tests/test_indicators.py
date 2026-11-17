"""Unit tests for the indicator and scoring primitives."""

from __future__ import annotations

import math
import unittest
from datetime import date, timedelta

from app.services.indicators import (
    atr,
    build_samples,
    compute_series,
    current_features,
    rsi,
    sma,
)
from app.services.scoring import build_plan, score_candidate


def make_bars(count: int = 140, start: float = 10.0, step: float = 0.08) -> list[dict]:
    bars = []
    current = date(2025, 1, 2)
    price = start
    for index in range(count):
        open_price = price
        price = start + index * step + math.sin(index / 5.0) * 0.15
        high = max(open_price, price) + 0.08
        low = min(open_price, price) - 0.08
        bars.append(
            {
                "date": (current + timedelta(days=index)).isoformat(),
                "open": round(open_price, 3),
                "close": round(price, 3),
                "high": round(high, 3),
                "low": round(low, 3),
                "volume": 1_000_000 + index * 8000,
                "amount": 20_000_000 + index * 100_000,
                "amplitude": 1.0,
                "pct": 0.5,
                "change": 0.05,
                "turnover": 2.0,
            }
        )
    return bars


class IndicatorTests(unittest.TestCase):
    def test_sma(self) -> None:
        result = sma([1, 2, 3, 4, 5], 3)
        self.assertIsNone(result[1])
        self.assertEqual(result[2], 2)
        self.assertEqual(result[4], 4)

    def test_rsi_all_up_is_100(self) -> None:
        closes = [float(index) for index in range(1, 40)]
        result = rsi(closes, 14)
        self.assertAlmostEqual(result[14], 100.0)
        self.assertAlmostEqual(result[-1], 100.0)

    def test_series_and_atr(self) -> None:
        bars = make_bars()
        series = compute_series(bars)
        self.assertEqual(len(series["ma20"]), len(bars))
        self.assertIsNotNone(series["ma20"][-1])
        self.assertGreater(series["atr14"][-1], 0)
        self.assertGreater(series["rsi14"][-1], 0)

    def test_build_samples_has_labels_and_forward_return(self) -> None:
        bars = make_bars(130)
        samples = build_samples({"600000": bars}, bars, horizon=10)
        self.assertGreater(len(samples), 20)
        sample = samples[0]
        self.assertEqual(len(sample["x"]), 11)
        self.assertIn(sample["y"], (0, 1))
        self.assertIn("fwd", sample)

    def test_current_features_vector_length(self) -> None:
        bars = make_bars(130)
        quote = {
            "price": bars[-1]["close"],
            "high": bars[-1]["high"],
            "low": bars[-1]["low"],
            "open": bars[-1]["open"],
            "volume": bars[-1]["volume"],
            "amount": bars[-1]["amount"],
            "volume_ratio": 1.8,
        }
        features = current_features(bars, quote, bars, "2099-01-01")
        self.assertIsNotNone(features)
        self.assertEqual(len(features["vector"]), 11)
        self.assertGreater(features["atr"], 0)


class ScoringTests(unittest.TestCase):
    def setUp(self) -> None:
        self.bars = make_bars(130)
        quote = {
            "price": self.bars[-1]["close"],
            "high": self.bars[-1]["high"],
            "low": self.bars[-1]["low"],
            "open": self.bars[-1]["open"],
            "volume": self.bars[-1]["volume"],
            "amount": self.bars[-1]["amount"],
            "volume_ratio": 1.8,
            "pct": 1.2,
            "pe": 28.0,
            "main_inflow_ratio": 0.04,
            "turnover": 3.5,
            "market_cap": 20_000_000_000,
        }
        self.quote = quote
        self.features = current_features(self.bars, quote, self.bars, "2099-01-01")
        self.assertIsNotNone(self.features)

    def test_scores_are_bounded(self) -> None:
        score = score_candidate(self.features, self.quote)
        self.assertGreaterEqual(score["rule_score"], 0)
        self.assertLessEqual(score["rule_score"], 100)
        self.assertGreaterEqual(score["trend_score"], 0)
        self.assertLessEqual(score["trend_score"], 100)
        self.assertGreaterEqual(len(score["subscores"]), 8)

    def test_plan_is_ordered(self) -> None:
        profile = {
            "max_weight_pct": 0.15,
            "risk_budget_pct": 0.005,
        }
        plan = build_plan(self.features, self.quote, profile)
        self.assertLess(plan["entry_low"], plan["entry_high"])
        self.assertLess(plan["stop"], plan["entry_low"])
        self.assertGreater(plan["target1"], plan["entry_high"])
        self.assertGreaterEqual(plan["risk_reward"], 1.0)
        self.assertGreater(plan["suggested_weight_pct"], 0)
        self.assertLessEqual(plan["suggested_weight_pct"], 15.0)


if __name__ == "__main__":
    unittest.main()
