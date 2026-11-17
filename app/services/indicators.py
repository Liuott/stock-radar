"""Technical indicators and feature construction.

All functions are deliberately dependency-light and operate on plain lists of
daily bars. Training samples only use information available at the signal close;
the label is the next trading day's close-to-close direction.
"""

from __future__ import annotations

import math
from statistics import fmean

FEATURE_KEYS = [
    "ret5",
    "ret20",
    "trend",
    "ma_gap",
    "rsi",
    "macd",
    "vol_ratio",
    "atr_pct",
    "rel5",
    "dist_high20",
    "market_ma",
]


def _finite(value: float | None) -> bool:
    return value is not None and math.isfinite(value)


def sma(values: list[float], period: int) -> list[float | None]:
    result: list[float | None] = [None] * len(values)
    if period <= 0 or len(values) < period:
        return result
    window_sum = sum(values[:period])
    result[period - 1] = window_sum / period
    for index in range(period, len(values)):
        window_sum += values[index] - values[index - period]
        result[index] = window_sum / period
    return result


def ema(values: list[float], period: int) -> list[float]:
    if not values:
        return []
    alpha = 2.0 / (period + 1.0)
    result = [float(values[0])]
    for value in values[1:]:
        result.append(alpha * value + (1.0 - alpha) * result[-1])
    return result


def rsi(closes: list[float], period: int = 14) -> list[float | None]:
    result: list[float | None] = [None] * len(closes)
    if len(closes) <= period:
        return result
    gains: list[float] = []
    losses: list[float] = []
    for index in range(1, len(closes)):
        change = closes[index] - closes[index - 1]
        gains.append(max(change, 0.0))
        losses.append(max(-change, 0.0))
    avg_gain = sum(gains[:period]) / period
    avg_loss = sum(losses[:period]) / period
    result[period] = 100.0 if avg_loss == 0 else 100.0 - 100.0 / (1.0 + avg_gain / avg_loss)
    for index in range(period + 1, len(closes)):
        gain = gains[index - 1]
        loss = losses[index - 1]
        avg_gain = (avg_gain * (period - 1) + gain) / period
        avg_loss = (avg_loss * (period - 1) + loss) / period
        result[index] = 100.0 if avg_loss == 0 else 100.0 - 100.0 / (1.0 + avg_gain / avg_loss)
    return result


def macd(closes: list[float], fast: int = 12, slow: int = 26, signal: int = 9) -> tuple[list[float], list[float], list[float]]:
    ema_fast = ema(closes, fast)
    ema_slow = ema(closes, slow)
    dif = [fast_value - slow_value for fast_value, slow_value in zip(ema_fast, ema_slow)]
    dea = ema(dif, signal)
    histogram = [(d - e) * 2.0 for d, e in zip(dif, dea)]
    return dif, dea, histogram


def atr(bars: list[dict], period: int = 14) -> list[float | None]:
    result: list[float | None] = [None] * len(bars)
    if len(bars) <= period:
        return result
    true_ranges: list[float] = []
    for index, bar in enumerate(bars):
        if index == 0:
            true_ranges.append(bar["high"] - bar["low"])
            continue
        prev_close = bars[index - 1]["close"]
        true_ranges.append(
            max(
                bar["high"] - bar["low"],
                abs(bar["high"] - prev_close),
                abs(bar["low"] - prev_close),
            )
        )
    current = sum(true_ranges[:period]) / period
    result[period - 1] = current
    for index in range(period, len(bars)):
        current = (current * (period - 1) + true_ranges[index]) / period
        result[index] = current
    return result


def obv(bars: list[dict]) -> list[float]:
    result: list[float] = []
    current = 0.0
    for index, bar in enumerate(bars):
        if index == 0:
            current = bar["volume"]
        elif bar["close"] > bars[index - 1]["close"]:
            current += bar["volume"]
        elif bar["close"] < bars[index - 1]["close"]:
            current -= bar["volume"]
        result.append(current)
    return result


def compute_series(bars: list[dict]) -> dict:
    closes = [bar["close"] for bar in bars]
    highs = [bar["high"] for bar in bars]
    lows = [bar["low"] for bar in bars]
    volumes = [bar["volume"] for bar in bars]
    ma5 = sma(closes, 5)
    ma10 = sma(closes, 10)
    ma20 = sma(closes, 20)
    ma60 = sma(closes, 60)
    rsi14 = rsi(closes, 14)
    dif, dea, histogram = macd(closes)
    atr14 = atr(bars, 14)
    vol_ma5 = sma(volumes, 5)
    vol_ma20 = sma(volumes, 20)
    obv_values = obv(bars)
    ma20_slope: list[float | None] = [None] * len(bars)
    for index in range(5, len(bars)):
        if ma20[index] and ma20[index - 5]:
            ma20_slope[index] = ma20[index] / ma20[index - 5] - 1.0
    return {
        "closes": closes,
        "highs": highs,
        "lows": lows,
        "volumes": volumes,
        "ma5": ma5,
        "ma10": ma10,
        "ma20": ma20,
        "ma60": ma60,
        "rsi14": rsi14,
        "dif": dif,
        "dea": dea,
        "macd_hist": histogram,
        "atr14": atr14,
        "vol_ma5": vol_ma5,
        "vol_ma20": vol_ma20,
        "obv": obv_values,
        "ma20_slope": ma20_slope,
    }


def market_map(index_bars: list[dict]) -> dict[str, dict]:
    """Benchmark state keyed by date, used as a market-regime feature."""
    if len(index_bars) < 25:
        return {}
    series = compute_series(index_bars)
    result: dict[str, dict] = {}
    for index, bar in enumerate(index_bars):
        ma20 = series["ma20"][index]
        if ma20 is None or index < 5:
            continue
        close = bar["close"]
        result[bar["date"]] = {
            "market_ma": close / ma20 - 1.0,
            "market_ret5": close / series["closes"][index - 5] - 1.0,
        }
    return result


def _sample_features(series: dict, index: int, index_state: dict | None) -> list[float] | None:
    close = series["closes"][index]
    ma20 = series["ma20"][index]
    ma5 = series["ma5"][index]
    ma60 = series["ma60"][index]
    rsi_value = series["rsi14"][index]
    hist = series["macd_hist"][index]
    atr_value = series["atr14"][index]
    vol20 = series["vol_ma20"][index]
    previous5 = series["closes"][index - 5] if index >= 5 else None
    previous20 = series["closes"][index - 20] if index >= 20 else None
    highs20 = series["highs"][max(0, index - 19): index + 1]
    if not all(
        _finite(value)
        for value in (close, ma20, ma5, ma60, rsi_value, hist, atr_value, vol20, previous5, previous20)
    ):
        return None
    if close <= 0 or ma20 <= 0 or vol20 <= 0 or not highs20:
        return None
    ret5 = close / previous5 - 1.0
    ret20 = close / previous20 - 1.0
    trend = close / ma20 - 1.0
    ma_gap = ma5 / ma20 - 1.0
    vol_ratio = series["volumes"][index] / vol20
    atr_pct = atr_value / close
    rel5 = ret5 - (index_state or {}).get("market_ret5", 0.0)
    dist_high20 = close / max(highs20) - 1.0
    market_ma = (index_state or {}).get("market_ma", 0.0)
    return [
        ret5,
        ret20,
        trend,
        ma_gap,
        rsi_value / 100.0 - 0.5,
        hist / close,
        vol_ratio,
        atr_pct,
        rel5,
        dist_high20,
        market_ma,
    ]


def build_samples(
    stocks_bars: dict[str, list[dict]],
    index_bars: list[dict],
    horizon: int = 1,
) -> list[dict]:
    """Build pooled forward-return direction samples from completed bars.

    ``horizon`` is in trading days. A 5-day horizon better matches a swing plan;
    the caller can validate which horizon is actually supported by the data.
    """
    index_states = market_map(index_bars)
    samples: list[dict] = []
    for code, bars in stocks_bars.items():
        if len(bars) < 80 + horizon:
            continue
        series = compute_series(bars)
        for index in range(60, len(bars) - horizon):
            features = _sample_features(series, index, index_states.get(bars[index]["date"]))
            if features is None:
                continue
            forward = bars[index + horizon]["close"] / bars[index]["close"] - 1.0
            label = 1 if forward > 0 else 0
            samples.append(
                {
                    "code": code,
                    "date": bars[index]["date"],
                    "x": features,
                    "y": label,
                    "fwd": forward,
                }
            )
    samples.sort(key=lambda item: item["date"])
    return samples


def current_features(
    bars: list[dict],
    quote: dict,
    index_bars: list[dict],
    today: str,
) -> dict | None:
    """Features at the live price, using completed bars plus today's quote.

    The current session's partial volume is noisy for daily indicators, so the
    provider's volume ratio is used when available. Price-based indicators do
    include the live price.
    """
    if len(bars) < 65:
        return None
    completed = [bar for bar in bars if bar["date"] != today]
    if len(completed) < 60:
        completed = bars[:-1]
    if len(completed) < 60:
        return None

    price = float(quote.get("price") or completed[-1]["close"])
    if price <= 0:
        return None
    today_high = float(quote.get("high") or price)
    today_low = float(quote.get("low") or price)
    today_open = float(quote.get("open") or price)
    today_volume = float(quote.get("volume") or 0.0)
    extended_closes = [bar["close"] for bar in completed] + [price]
    extended_bars = completed + [
        {
            "date": today,
            "open": today_open,
            "close": price,
            "high": max(today_high, price),
            "low": min(today_low, price),
            "volume": today_volume,
            "amount": float(quote.get("amount") or 0.0),
        }
    ]
    series = compute_series(extended_bars)
    last = len(extended_closes) - 1
    ma5 = series["ma5"][last]
    ma20 = series["ma20"][last]
    ma60 = series["ma60"][last]
    rsi_value = series["rsi14"][last]
    hist = series["macd_hist"][last]
    atr_value = series["atr14"][last]
    if not all(_finite(value) for value in (ma5, ma20, ma60, rsi_value, hist, atr_value)):
        return None
    if ma20 <= 0 or ma5 <= 0:
        return None

    index_states = market_map(index_bars)
    index_state = index_states.get(today)
    if index_state is None and index_states:
        index_state = index_states[max(index_states)]
    ret5 = price / extended_closes[-6] - 1.0
    ret20 = price / extended_closes[-21] - 1.0
    trend = price / ma20 - 1.0
    ma_gap = ma5 / ma20 - 1.0
    quote_volume_ratio = quote.get("volume_ratio")
    vol20 = series["vol_ma20"][last]
    if quote_volume_ratio is not None and quote_volume_ratio > 0:
        vol_ratio = float(quote_volume_ratio)
    elif vol20 and vol20 > 0 and today_volume > 0:
        vol_ratio = today_volume / vol20
    else:
        vol_ratio = 1.0
    atr_pct = atr_value / price
    rel5 = ret5 - (index_state or {}).get("market_ret5", 0.0)
    highs20 = series["highs"][-20:]
    dist_high20 = price / max(highs20) - 1.0
    market_ma = (index_state or {}).get("market_ma", 0.0)

    vector = [
        ret5,
        ret20,
        trend,
        ma_gap,
        rsi_value / 100.0 - 0.5,
        hist / price,
        vol_ratio,
        atr_pct,
        rel5,
        dist_high20,
        market_ma,
    ]
    obv_values = series["obv"]
    obv_slope = 0.0
    if len(obv_values) > 10 and obv_values[-10] > 0:
        obv_slope = obv_values[-1] / obv_values[-10] - 1.0
    return {
        "vector": vector,
        "values": {
            "ret5": ret5,
            "ret20": ret20,
            "trend": trend,
            "ma_gap": ma_gap,
            "rsi": rsi_value,
            "macd": hist / price,
            "vol_ratio": vol_ratio,
            "atr_pct": atr_pct,
            "rel5": rel5,
            "dist_high20": dist_high20,
            "market_ma": market_ma,
        },
        "price": price,
        "ma5": ma5,
        "ma10": series["ma10"][last],
        "ma20": ma20,
        "ma60": ma60,
        "atr": atr_value,
        "high20": max(highs20),
        "low20": min(series["lows"][-20:]),
        "rsi": rsi_value,
        "macd_hist": hist,
        "dif": series["dif"][last],
        "dea": series["dea"][last],
        "obv_slope": obv_slope,
        "ma20_slope": series["ma20_slope"][last] or 0.0,
        "vol_ratio": vol_ratio,
        "series": series,
    }


def chart_payload(bars: list[dict], limit: int = 120) -> dict:
    bars = bars[-limit:]
    series = compute_series(bars)
    dates = [bar["date"] for bar in bars]
    return {
        "dates": dates,
        "ohlc": [
            [bar["open"], bar["close"], bar["low"], bar["high"]] for bar in bars
        ],
        "volumes": [bar["volume"] for bar in bars],
        "amounts": [bar["amount"] for bar in bars],
        "ma5": [round(value, 3) if value is not None else None for value in series["ma5"]],
        "ma10": [round(value, 3) if value is not None else None for value in series["ma10"]],
        "ma20": [round(value, 3) if value is not None else None for value in series["ma20"]],
        "ma60": [round(value, 3) if value is not None else None for value in series["ma60"]],
        "vol_ma5": [
            round(value, 2) if value is not None else None for value in series["vol_ma5"]
        ],
    }


def mean(values: list[float]) -> float:
    return fmean(values) if values else 0.0
