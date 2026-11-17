"""Tests for the plain-language execution guidance."""

from __future__ import annotations

import unittest

from app.services.market import MarketService
from app.services.scoring import build_execution_guide


PLAN = {
    "entry_low": 10.0,
    "entry_high": 11.0,
    "stop": 9.5,
    "target1": 12.0,
    "suggested_weight_pct": 8.0,
    "risk_budget_pct": 0.5,
}


class ExecutionGuideTests(unittest.TestCase):
    def test_wait_when_price_above_zone(self) -> None:
        guide = build_execution_guide(PLAN, {"price": 11.5, "pct": 3.0}, "medium", 0.01)
        self.assertEqual(guide["tone"], "wait")
        self.assertIn("不追", guide["headline"])
        self.assertEqual(len(guide["steps"]), 3)

    def test_action_when_price_inside_zone(self) -> None:
        guide = build_execution_guide(PLAN, {"price": 10.5, "pct": 1.0}, "medium", 0.02)
        self.assertEqual(guide["tone"], "action")
        self.assertIn("4.0", guide["headline"])  # half of 8%
        self.assertIn("9.50", " ".join(guide["steps"]))

    def test_observe_when_model_edge_is_weak(self) -> None:
        guide = build_execution_guide(PLAN, {"price": 10.5, "pct": 1.0}, "low", 0.001)
        self.assertEqual(guide["tone"], "observe")
        self.assertIn("不执行", guide["headline"])

    def test_invalid_when_stop_is_breached(self) -> None:
        guide = build_execution_guide(PLAN, {"price": 9.4, "pct": -6.0}, "medium", 0.01)
        self.assertEqual(guide["tone"], "danger")
        self.assertIn("止损", guide["headline"])

    def test_success_when_target_is_reached(self) -> None:
        guide = build_execution_guide(PLAN, {"price": 12.2, "pct": 2.0}, "medium", 0.01)
        self.assertEqual(guide["tone"], "success")
        self.assertIn("止盈", guide["headline"])


class WatchNextActionTests(unittest.TestCase):
    def test_buy_zone(self) -> None:
        action = MarketService._next_action(
            {"entry_low": 10.0, "entry_high": 11.0, "stop": 9.5, "target1": 12.0},
            {"price": 10.5, "pct": 1.0, "volume_ratio": 1.0},
        )
        self.assertEqual(action["tone"], "action")
        self.assertEqual(action["label"], "可分批")

    def test_stop_loss(self) -> None:
        action = MarketService._next_action(
            {"entry_low": 10.0, "entry_high": 11.0, "stop": 9.5, "target1": 12.0},
            {"price": 9.4, "pct": -6.0, "volume_ratio": 1.0},
        )
        self.assertEqual(action["tone"], "danger")
        self.assertEqual(action["label"], "执行止损")

    def test_missing_plan(self) -> None:
        action = MarketService._next_action({"code": "600000"}, {"price": 10.0, "pct": 0.0})
        self.assertEqual(action["tone"], "muted")
        self.assertEqual(action["label"], "仅跟踪")


class LeaderScoreTests(unittest.TestCase):
    STOCK = {
        "amount": 500_000_000,
        "pct": 10.0,
        "turnover": 8.0,
        "change60": 20.0,
    }

    def test_higher_board_scores_higher(self) -> None:
        one = MarketService._leader_score(self.STOCK, {"board_count": 1, "seal_fund": 0}, 2.0, 1e9)
        three = MarketService._leader_score(self.STOCK, {"board_count": 3, "seal_fund": 0}, 2.0, 1e9)
        self.assertGreater(three, one)

    def test_limit_up_pool_gets_board_premium(self) -> None:
        pool = MarketService._leader_score(
            self.STOCK, {"board_count": 2, "seal_fund": 50_000_000}, 2.0, 1e9
        )
        non_pool = MarketService._leader_score(self.STOCK, None, 2.0, 1e9)
        self.assertGreater(pool, non_pool)


if __name__ == "__main__":
    unittest.main()
