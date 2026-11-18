"""Tests for the deterministic candidate ranking.

Ties are the normal case in a weak-signal regime: several candidates share the
same model probability, and the row order that arrives from the concurrent K-line
fetches is network completion order. Without an explicit tie-break the displayed
percentile and the final order change between refreshes.
"""

from __future__ import annotations

import unittest

from app.services.market import rank_candidates

BASE = {
    "601717": (0.4546, 81.4, 74.3),
    "000543": (0.4546, 78.1, 70.2),
    "002223": (0.4546, 76.9, 72.0),
    "301606": (0.4546, 70.0, 68.0),
}


def _rows(order: list[str]) -> list[dict]:
    return [
        {
            "code": code,
            "probability": BASE[code][0],
            "rule_score": BASE[code][1],
            "risk_score": BASE[code][2],
        }
        for code in order
    ]


class RankCandidatesTests(unittest.TestCase):
    def test_equal_probabilities_rank_the_same_whatever_the_input_order(self) -> None:
        first = _rows(["601717", "000543", "002223", "301606"])
        second = _rows(["301606", "002223", "000543", "601717"])
        rank_candidates(first, False)
        rank_candidates(second, False)
        self.assertEqual([row["code"] for row in first], [row["code"] for row in second])
        self.assertEqual(
            {row["code"]: row["model_percentile"] for row in first},
            {row["code"]: row["model_percentile"] for row in second},
        )

    def test_unusable_model_percentile_does_not_steer_the_score(self) -> None:
        rows = _rows(["601717", "000543", "002223", "301606"])
        rank_candidates(rows, False)
        for row in rows:
            expected = round(
                0.15 * 50.0 + 0.68 * row["rule_score"] + 0.17 * row["risk_score"], 1
            )
            self.assertEqual(row["score"], expected)

    def test_usable_model_percentile_does_steer_the_score(self) -> None:
        rows = _rows(["601717", "000543", "002223", "301606"])
        rank_candidates(rows, True)
        neutral = round(0.15 * 50.0 + 0.68 * 81.4 + 0.17 * 74.3, 1)
        top = next(row for row in rows if row["code"] == "601717")
        self.assertAlmostEqual(top["model_percentile"], 1.0)
        self.assertNotEqual(top["score"], neutral)


if __name__ == "__main__":
    unittest.main()
