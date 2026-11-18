"""Regression tests for the logistic model and its calibration.

The 2026-11 review found the isotonic calibration could pool every bin into a
single point, which made every candidate show the same probability. These tests
pin that behaviour down so it cannot come back silently.
"""

from __future__ import annotations

import unittest
from datetime import date, timedelta

import numpy as np

from app.config import FEATURE_LABELS
from app.services.model import _calibrate, _fit_isotonic, predict, train_model

FEATURE_COUNT = len(FEATURE_LABELS)


def _constant_hit_rate_case() -> tuple[np.ndarray, np.ndarray]:
    """Scores whose ten deciles all have the same 50% hit rate."""
    probabilities = np.arange(1000, dtype=float) / 1000.0
    labels = (np.arange(1000) % 2).astype(float)
    return probabilities, labels


def _separable_case() -> tuple[np.ndarray, np.ndarray]:
    rng = np.random.default_rng(3)
    probabilities = rng.uniform(0.1, 0.9, size=1000)
    labels = (probabilities > 0.5).astype(float)
    return probabilities, labels


class IsotonicTests(unittest.TestCase):
    def test_uniform_hit_rates_do_not_produce_a_constant_curve(self) -> None:
        # PAVA cannot separate equal bins; before the fix this returned a
        # one-point curve that mapped every input to the same probability.
        probabilities, labels = _constant_hit_rate_case()
        self.assertIsNone(_fit_isotonic(probabilities, labels))

    def test_separable_scores_still_yield_a_curve(self) -> None:
        # The guard must not over-reject a genuinely useful calibration.
        probabilities, labels = _separable_case()
        curve = _fit_isotonic(probabilities, labels)
        self.assertIsNotNone(curve)
        self.assertGreaterEqual(len(curve["x"]), 2)
        self.assertGreater(curve["y"][-1], curve["y"][0])

    def test_degenerate_stored_curve_cannot_flatten_predictions(self) -> None:
        probabilities = np.asarray([0.2, 0.45, 0.8])
        calibration = {"type": "isotonic", "x": [0.4546], "y": [0.4546]}
        calibrated = _calibrate(probabilities, calibration)
        self.assertEqual(len(np.unique(np.round(calibrated, 6))), 3)


class TrainModelTests(unittest.TestCase):
    def _samples(self) -> list[dict]:
        rng = np.random.default_rng(11)
        start = date(2026, 1, 1)
        rows: list[dict] = []
        for day_index in range(120):
            day = (start + timedelta(days=day_index)).isoformat()
            for _ in range(20):
                vector = rng.normal(0.0, 1.0, size=FEATURE_COUNT)
                # Real but noisy signal on the first feature.
                score = vector[0] + rng.normal(0.0, 0.8)
                rows.append(
                    {
                        "date": day,
                        "x": vector.tolist(),
                        "y": 1.0 if score > 0 else 0.0,
                        "fwd": score / 100.0,
                    }
                )
        return rows

    def test_training_exposes_raw_auc_and_usable_artifact(self) -> None:
        artifact = train_model(self._samples(), horizon=10)
        metrics = artifact["metrics"]
        self.assertGreater(metrics["auc_raw"], 0.55)
        self.assertTrue(metrics["usable"])
        self.assertIn(metrics["calibration_type"], {"isotonic", "platt"})

    def test_predictions_vary_across_feature_vectors(self) -> None:
        artifact = train_model(self._samples(), horizon=10)
        padding = [0.0] * (FEATURE_COUNT - 1)
        low = predict(artifact, [-2.0] + padding)
        high = predict(artifact, [2.0] + padding)
        self.assertGreater(high["probability"], low["probability"])


if __name__ == "__main__":
    unittest.main()
