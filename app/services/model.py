"""A small, walk-forward validated logistic model for forward direction.

This is intentionally not a deep-learning black box. With public daily bars and a
retail data budget, a regularized linear model is easier to validate, explain and
monitor for decay. The model is trained only on data available before each label
and evaluated on a later, untouched time slice.

Known limitation: the training universe is preselected from today's liquid and
active stocks, so the validation sample carries selection/survivorship bias. It
is a sanity check, not an institutional-grade backtest.
"""

from __future__ import annotations

import math
from datetime import datetime

import numpy as np

from app.config import CN_TZ, FEATURE_LABELS


def _sigmoid(values: np.ndarray) -> np.ndarray:
    values = np.clip(values, -30.0, 30.0)
    return 1.0 / (1.0 + np.exp(-values))


def _fit_logistic(
    x: np.ndarray,
    y: np.ndarray,
    l2: float = 0.03,
    epochs: int = 420,
    learning_rate: float = 0.18,
) -> tuple[np.ndarray, float]:
    n_samples, n_features = x.shape
    weights = np.zeros(n_features, dtype=float)
    base = float(np.clip(y.mean(), 0.05, 0.95))
    bias = math.log(base / (1.0 - base))
    for _ in range(epochs):
        probabilities = _sigmoid(x @ weights + bias)
        error = probabilities - y
        grad_w = (x.T @ error) / n_samples + l2 * weights
        grad_b = float(error.mean())
        weights -= learning_rate * grad_w
        bias -= learning_rate * grad_b
    return weights, float(bias)


def _auc(y: np.ndarray, probabilities: np.ndarray) -> float:
    positives = int(y.sum())
    negatives = len(y) - positives
    if positives == 0 or negatives == 0:
        return 0.5
    order = np.argsort(probabilities, kind="mergesort")
    sorted_p = probabilities[order]
    ranks = np.empty(len(probabilities), dtype=float)
    index = 0
    while index < len(sorted_p):
        end = index + 1
        while end < len(sorted_p) and sorted_p[end] == sorted_p[index]:
            end += 1
        average_rank = (index + 1 + end) / 2.0
        ranks[order[index:end]] = average_rank
        index = end
    positive_rank_sum = ranks[y == 1].sum()
    return float(
        (positive_rank_sum - positives * (positives + 1) / 2.0)
        / (positives * negatives)
    )


def _fit_platt(logits: np.ndarray, y: np.ndarray) -> tuple[float, float]:
    """Fit a one-dimensional logistic calibration on validation logits."""
    if len(logits) < 200 or len(np.unique(y)) < 2:
        return 1.0, 0.0
    a = 1.0
    b = 0.0
    learning_rate = 0.08
    for _ in range(220):
        p = _sigmoid(a * logits + b)
        error = p - y
        grad_a = float((error * logits).mean())
        grad_b = float(error.mean())
        a -= learning_rate * grad_a
        b -= learning_rate * grad_b
    if not math.isfinite(a) or not math.isfinite(b) or a <= 0.1:
        return 1.0, 0.0
    return float(min(a, 4.0)), float(max(-4.0, min(4.0, b)))


def _fit_isotonic(probabilities: np.ndarray, y: np.ndarray, bins: int = 10) -> dict | None:
    """Binned isotonic calibration (PAVA) from validation predictions.

    A single logistic slope often leaves the tails miscalibrated. Ten equal-count
    bins with monotonic smoothing give a more honest view of the observed hit
    rate at each score level, while staying simple enough to audit.
    """
    if len(probabilities) < 500 or len(np.unique(y)) < 2:
        return None
    order = np.argsort(probabilities)
    chunks = np.array_split(order, bins)
    xs: list[float] = []
    ys: list[float] = []
    weights: list[float] = []
    for chunk in chunks:
        if len(chunk) == 0:
            continue
        xs.append(float(probabilities[chunk].mean()))
        ys.append(float(y[chunk].mean()))
        weights.append(float(len(chunk)))
    if len(xs) < 3:
        return None

    # Pool adjacent violators so the calibration curve is non-decreasing.
    index = 0
    while index < len(ys) - 1:
        if ys[index] <= ys[index + 1] + 1e-12:
            index += 1
            continue
        total_weight = weights[index] + weights[index + 1]
        merged_y = (ys[index] * weights[index] + ys[index + 1] * weights[index + 1]) / total_weight
        merged_x = (xs[index] * weights[index] + xs[index + 1] * weights[index + 1]) / total_weight
        xs[index:index + 2] = [merged_x]
        ys[index:index + 2] = [merged_y]
        weights[index:index + 2] = [total_weight]
        if index > 0:
            index -= 1
    return {
        "type": "isotonic",
        "x": [round(value, 6) for value in xs],
        "y": [round(min(0.95, max(0.05, value)), 6) for value in ys],
    }


def _calibrate(probabilities: np.ndarray, calibration: dict) -> np.ndarray:
    if calibration.get("type") == "isotonic":
        xs = np.asarray(calibration["x"], dtype=float)
        ys = np.asarray(calibration["y"], dtype=float)
        return np.interp(probabilities, xs, ys)
    a = float(calibration.get("a", 1.0))
    b = float(calibration.get("b", 0.0))
    logits = np.log(np.clip(probabilities, 1e-6, 1 - 1e-6) / (1 - np.clip(probabilities, 1e-6, 1 - 1e-6)))
    return _sigmoid(a * logits + b)


def _metrics(y: np.ndarray, probabilities: np.ndarray, returns: np.ndarray) -> dict:
    auc = _auc(y, probabilities)
    accuracy = float(((probabilities >= 0.5) == y).mean()) if len(y) else 0.0
    base_rate = float(y.mean()) if len(y) else 0.5
    count = max(1, int(len(y) * 0.10))
    top_indices = np.argsort(probabilities)[-count:]
    top_hit = float(y[top_indices].mean()) if len(top_indices) else base_rate
    top_return = float(returns[top_indices].mean()) if len(top_indices) else 0.0
    baseline_return = float(returns.mean()) if len(returns) else 0.0
    brier = float(((probabilities - y) ** 2).mean()) if len(y) else 1.0
    return {
        "auc": round(auc, 4),
        "accuracy": round(accuracy, 4),
        "base_rate": round(base_rate, 4),
        "top_decile_hit": round(top_hit, 4),
        "top_decile_return": round(top_return, 5),
        "baseline_return": round(baseline_return, 5),
        "edge": round(top_return - baseline_return, 5),
        "brier": round(brier, 4),
        "validation_samples": int(len(y)),
        "top_decile_samples": int(len(top_indices)),
    }


def train_model(samples: list[dict], horizon: int = 1) -> dict:
    if len(samples) < 800:
        raise ValueError(f"not enough samples to train: {len(samples)}")
    dates = sorted({sample["date"] for sample in samples})
    if len(dates) < 60:
        raise ValueError("not enough distinct dates to train")
    cutoff_index = max(40, int(len(dates) * 0.70))
    cutoff = dates[min(cutoff_index, len(dates) - 1)]
    train_rows = [sample for sample in samples if sample["date"] < cutoff]
    validation_rows = [sample for sample in samples if sample["date"] >= cutoff]
    if len(train_rows) < 500 or len(validation_rows) < 150:
        raise ValueError(
            f"time split too small: train={len(train_rows)} val={len(validation_rows)}"
        )

    x_train = np.asarray([sample["x"] for sample in train_rows], dtype=float)
    y_train = np.asarray([sample["y"] for sample in train_rows], dtype=float)
    x_val = np.asarray([sample["x"] for sample in validation_rows], dtype=float)
    y_val = np.asarray([sample["y"] for sample in validation_rows], dtype=float)
    returns_val = np.asarray([sample.get("fwd", 0.0) for sample in validation_rows], dtype=float)

    mean = x_train.mean(axis=0)
    std = x_train.std(axis=0)
    std[std < 1e-9] = 1.0
    x_train_scaled = (x_train - mean) / std
    x_val_scaled = (x_val - mean) / std

    weights, bias = _fit_logistic(x_train_scaled, y_train)
    val_logits = x_val_scaled @ weights + bias
    val_probabilities = _sigmoid(val_logits)
    calibration = _fit_isotonic(val_probabilities, y_val)
    if calibration is None:
        a, b = _fit_platt(val_logits, y_val)
        calibration = {"type": "platt", "a": a, "b": b}
    calibrated_val = _calibrate(val_probabilities, calibration)
    metrics = _metrics(y_val, calibrated_val, returns_val)
    raw_metrics = _metrics(y_val, val_probabilities, returns_val)

    # Refit on all available data after the holdout has done its job.
    x_all = np.asarray([sample["x"] for sample in samples], dtype=float)
    y_all = np.asarray([sample["y"] for sample in samples], dtype=float)
    x_all_scaled = (x_all - mean) / std
    final_weights, final_bias = _fit_logistic(x_all_scaled, y_all)

    importance = []
    for index, name in enumerate(FEATURE_LABELS):
        importance.append(
            {
                "key": name,
                "label": FEATURE_LABELS[name],
                "weight": round(float(final_weights[index]), 4),
                "abs_weight": round(abs(float(final_weights[index])), 4),
            }
        )
    importance.sort(key=lambda item: item["abs_weight"], reverse=True)

    auc = metrics["auc"]
    if auc >= 0.56 and metrics["validation_samples"] >= 1500:
        confidence = "high"
    elif auc >= 0.53:
        confidence = "medium"
    else:
        confidence = "low"

    return {
        "weights": [float(value) for value in final_weights],
        "bias": float(final_bias),
        "mean": [float(value) for value in mean],
        "std": [float(value) for value in std],
        "calibration": calibration,
        "metrics": {
            **metrics,
            "raw_brier": raw_metrics["brier"],
            "train_samples": int(len(train_rows)),
            "all_samples": int(len(samples)),
            "date_from": dates[0],
            "date_to": dates[-1],
            "validation_from": validation_rows[0]["date"],
            "validation_to": validation_rows[-1]["date"],
            "cutoff": cutoff,
            "usable": bool(auc >= 0.52),
            "horizon_days": int(horizon),
        },
        "feature_importance": importance[:6],
        "confidence": confidence,
        "trained_at": datetime.now(CN_TZ).isoformat(timespec="seconds"),
        "method": f"L2 正则逻辑回归 + 时间切分验证 + 等频保序校准（{horizon}日）",
        "limitations": (
            "训练池来自当日流动性候选，存在选择/幸存者偏差；标签为未来收盘涨跌，"
            "10日窗口彼此重叠，验证样本并非独立，AUC 可能偏乐观；"
            "未覆盖盘中滑点、涨跌停无法成交与重大事件。"
        ),
    }


def predict(artifact: dict, vector: list[float]) -> dict:
    x = np.asarray(vector, dtype=float)
    mean = np.asarray(artifact["mean"], dtype=float)
    std = np.asarray(artifact["std"], dtype=float)
    weights = np.asarray(artifact["weights"], dtype=float)
    scaled = (x - mean) / std
    logit = float(scaled @ weights + artifact["bias"])
    raw = float(_sigmoid(np.asarray([logit]))[0])
    calibration = artifact.get("calibration") or {"type": "platt", "a": 1.0, "b": 0.0}
    calibrated = float(_calibrate(np.asarray([raw]), calibration)[0])
    base = float(artifact["metrics"].get("base_rate", 0.5))
    probability = 0.92 * calibrated + 0.08 * base
    if not artifact["metrics"].get("usable", False):
        probability = 0.55 * probability + 0.45 * base
    probability = max(0.05, min(0.95, probability))
    return {
        "probability": round(probability, 4),
        "raw_probability": round(raw, 4),
        "logit": round(logit, 4),
        "edge_vs_base": round(probability - base, 4),
    }
