"""Explainable rule scoring and trade-plan construction.

The model probability is one input, never the whole answer. Every candidate is
also checked for trend quality, volume confirmation, overbought risk, relative
strength, volatility and capital flow. All outputs are research observations.
"""

from __future__ import annotations

from app.config import FACTOR_META, FACTOR_WEIGHTS


def _clamp(value: float, low: float = 0.0, high: float = 100.0) -> float:
    return max(low, min(high, value))


def _piecewise(value: float, points: list[tuple[float, float]]) -> float:
    """Linear interpolation with flat extrapolation at both ends."""
    if not points:
        return 50.0
    if value <= points[0][0]:
        return points[0][1]
    if value >= points[-1][0]:
        return points[-1][1]
    for (x0, y0), (x1, y1) in zip(points, points[1:]):
        if x0 <= value <= x1:
            if x1 == x0:
                return y1
            ratio = (value - x0) / (x1 - x0)
            return y0 + ratio * (y1 - y0)
    return points[-1][1]


def _fmt_pct(value: float | None) -> str:
    if value is None:
        return "—"
    return f"{value * 100:+.1f}%"


def _subscore(key: str, score: float, value: float | None, note: str) -> dict:
    meta = FACTOR_META[key]
    return {
        "key": key,
        "label": meta["label"],
        "weight": meta["weight"],
        "score": round(_clamp(score), 1),
        "value": value,
        "note": note,
        "hint": meta["hint"],
    }


def score_candidate(features: dict, quote: dict) -> dict:
    values = features["values"]
    price = features["price"]
    trend = values["trend"]
    ma_gap = values["ma_gap"]
    slope = features.get("ma20_slope") or 0.0
    ret5 = values["ret5"]
    ret20 = values["ret20"]
    vol_ratio = values["vol_ratio"]
    rsi = values["rsi"]
    hist = values["macd"]
    rel5 = values["rel5"]
    atr_pct = values["atr_pct"]
    dist_high = values["dist_high20"]
    main_ratio = quote.get("main_inflow_ratio")
    pe = quote.get("pe")

    trend_score = 50.0
    trend_score += 18 if trend > 0 else -14
    trend_score += 10 if ma_gap > 0 else -7
    trend_score += 12 if slope > 0.002 else (-8 if slope < -0.002 else 0)
    trend_score += 10 if price > (features.get("ma60") or price) else -6
    trend_note = (
        "站上 MA20，均线多头" if trend > 0 and ma_gap > 0
        else "跌破 MA20，趋势待修复" if trend <= 0
        else "均线粘合，方向待选择"
    )

    momentum_score = 0.6 * _piecewise(
        ret20, [(-0.15, 15), (-0.05, 32), (0, 50), (0.08, 72), (0.18, 88), (0.30, 65), (0.45, 32)]
    ) + 0.4 * _piecewise(
        ret5, [(-0.08, 20), (-0.02, 38), (0, 48), (0.03, 65), (0.08, 82), (0.14, 48), (0.20, 25)]
    )

    volume_score = 0.7 * _piecewise(
        vol_ratio, [(0.5, 28), (0.8, 45), (1.2, 70), (2.0, 88), (2.8, 76), (4.5, 38)]
    ) + 0.3 * _piecewise(
        features.get("obv_slope") or 0.0,
        [(-0.12, 30), (0, 50), (0.05, 68), (0.15, 82)],
    )

    rsi_score = _piecewise(
        rsi,
        [(20, 20), (30, 32), (40, 50), (50, 70), (60, 85), (68, 78), (75, 48), (85, 18)],
    )

    if hist > 0 and features.get("dif", 0) > 0:
        macd_score = 86
    elif hist > 0:
        macd_score = 70
    elif features.get("dif", 0) > 0:
        macd_score = 50
    else:
        macd_score = 30

    relative_score = _piecewise(
        rel5, [(-0.10, 18), (-0.04, 32), (0, 50), (0.03, 68), (0.08, 82), (0.15, 88)]
    )

    risk_score = 0.5 * _piecewise(
        atr_pct, [(0.010, 52), (0.020, 80), (0.035, 78), (0.050, 60), (0.070, 40), (0.10, 18)]
    ) + 0.3 * _piecewise(
        trend, [(-0.10, 30), (0, 78), (0.04, 84), (0.08, 65), (0.12, 42), (0.18, 18)]
    ) + 0.2 * _piecewise(
        dist_high, [(-0.30, 25), (-0.15, 48), (-0.08, 68), (-0.03, 80), (0, 76)]
    )

    if main_ratio is None:
        flow_component = 50.0
    else:
        flow_component = _piecewise(
            main_ratio,
            [(-0.20, 18), (-0.08, 34), (0, 52), (0.05, 68), (0.12, 82), (0.25, 88)],
        )
    if pe is None:
        pe_component = 50.0
    elif pe <= 0:
        pe_component = 35.0
    else:
        pe_component = _piecewise(pe, [(8, 60), (15, 78), (30, 72), (50, 58), (80, 40), (150, 22), (250, 12)])
    flow_score = 0.65 * flow_component + 0.35 * pe_component

    subscores = {
        "trend": _subscore("trend", trend_score, trend, trend_note),
        "momentum": _subscore(
            "momentum", momentum_score, ret20,
            f"20日 {_fmt_pct(ret20)} / 5日 {_fmt_pct(ret5)}",
        ),
        "volume": _subscore(
            "volume", volume_score, vol_ratio,
            f"量比 {vol_ratio:.2f}，OBV 10日 {_fmt_pct(features.get('obv_slope') or 0)}",
        ),
        "rsi": _subscore(
            "rsi", rsi_score, rsi,
            f"RSI {rsi:.1f}，" + ("偏热" if rsi > 72 else "偏弱" if rsi < 40 else "中性偏强" if rsi >= 55 else "中性"),
        ),
        "macd": _subscore(
            "macd", macd_score, hist,
            "MACD 柱在零轴上方" if hist > 0 else "MACD 柱仍在零轴下方",
        ),
        "relative": _subscore(
            "relative", relative_score, rel5,
            f"5日相对沪深300 {_fmt_pct(rel5)}",
        ),
        "risk": _subscore(
            "risk", risk_score, atr_pct,
            f"ATR {atr_pct * 100:.2f}%，MA20乖离 {_fmt_pct(trend)}",
        ),
        "flow": _subscore(
            "flow", flow_score, main_ratio,
            (
                "主力净流入占比 —" if main_ratio is None
                else f"主力净流入占成交额 {main_ratio * 100:+.1f}%"
            ) + (f"，PE {pe:.1f}" if pe and pe > 0 else "，PE 为负或缺失"),
        ),
    }
    total = sum(subscores[key]["score"] * FACTOR_WEIGHTS[key] for key in FACTOR_WEIGHTS)

    reasons = [
        f"{item['label']}：{item['note']}"
        for item in sorted(
            subscores.values(),
            key=lambda item: item["score"] * item["weight"],
            reverse=True,
        )
        if item["score"] >= 68
    ][:3]
    risks = [
        f"{item['label']}偏弱（{item['score']:.0f}分）：{item['note']}"
        for item in sorted(subscores.values(), key=lambda item: item["score"])
        if item["score"] <= 45
    ][:3]
    if quote.get("is_st"):
        risks.append("ST 标的，退市与流动性风险显著，默认不建议参与")
    if (quote.get("pct") or 0) >= 7:
        risks.append("当日涨幅已较大，追高的日内回撤风险上升")
    if (quote.get("turnover") or 0) > 25:
        risks.append("换手率过高，筹码交换剧烈，需警惕分歧")
    if quote.get("pe") is not None and quote["pe"] > 120:
        risks.append("市盈率处于高位，估值对业绩兑现要求高")

    confidence = "high" if total >= 70 and risk_score >= 55 else "medium" if total >= 55 else "low"
    return {
        "rule_score": round(total, 1),
        "trend_score": round(subscores["trend"]["score"], 1),
        "risk_score": round(subscores["risk"]["score"], 1),
        "confidence": confidence,
        "subscores": list(subscores.values()),
        "reasons": reasons or ["因子信号中性，等待更明确的趋势与量能确认"],
        "risks": risks or ["未触发硬性风险项，但仍需遵守止损与仓位约束"],
    }


def build_plan(features: dict, quote: dict, profile: dict) -> dict:
    price = float(features["price"])
    atr = float(features["atr"] or price * 0.03)
    pct = float(quote.get("pct") or 0.0)
    dist_high = features["values"]["dist_high20"]

    if pct >= 4.0:
        entry_low = price - 0.80 * atr
        entry_high = price - 0.15 * atr
        action = "回踩分批"
        action_note = "当日涨幅偏大，等待回踩而非直接追高"
    elif dist_high > -0.02 and features["vol_ratio"] >= 1.15:
        entry_low = price - 0.15 * atr
        entry_high = price + 0.25 * atr
        action = "突破跟随"
        action_note = "接近20日高点且量能配合，突破确认后再分批"
    else:
        entry_low = price - 0.35 * atr
        entry_high = price + 0.15 * atr
        action = "区间分批"
        action_note = "在计划区间内分批，不一次性满仓"

    entry_low = max(0.01, entry_low)
    entry_high = max(entry_low + 0.01, entry_high)
    entry_mid = (entry_low + entry_high) / 2.0
    stop = entry_low - 1.05 * atr
    stop = max(stop, price * 0.90)
    if stop >= entry_low:
        stop = entry_low * 0.97
    stop_dist = max(entry_mid - stop, price * 0.015)
    target1 = entry_mid + 2.0 * stop_dist
    target2 = entry_mid + 3.2 * stop_dist
    stop_pct = stop_dist / entry_mid if entry_mid > 0 else 0.05
    suggested_weight = min(
        profile["max_weight_pct"],
        profile["risk_budget_pct"] / max(stop_pct, 0.01),
    )
    suggested_weight = max(0.02, min(profile["max_weight_pct"], round(suggested_weight, 3)))

    return {
        "action": action,
        "action_note": action_note,
        "entry_low": round(entry_low, 2),
        "entry_high": round(entry_high, 2),
        "entry_mid": round(entry_mid, 2),
        "stop": round(stop, 2),
        "target1": round(target1, 2),
        "target2": round(target2, 2),
        "risk_reward": round((target1 - entry_mid) / stop_dist, 2),
        "stop_pct": round(stop_pct * 100, 2),
        "upside1_pct": round((target1 / price - 1.0) * 100, 2),
        "upside2_pct": round((target2 / price - 1.0) * 100, 2),
        "suggested_weight_pct": round(suggested_weight * 100, 1),
        "risk_budget_pct": round(profile["risk_budget_pct"] * 100, 2),
        "horizon": "5–15个交易日",
        "invalid": "收盘跌破止损价，或所属板块资金连续两日净流出",
        "t1_note": "A股 T+1：当日买入最早下一交易日才能卖出，计划需预留隔夜风险",
    }


def risk_flags(features: dict, quote: dict) -> list[str]:
    flags: list[str] = []
    if quote.get("is_st"):
        flags.append("ST")
    if quote.get("is_limit_up"):
        flags.append("涨停")
    if features["values"]["atr_pct"] > 0.075:
        flags.append("高波动")
    if features["values"]["trend"] > 0.12:
        flags.append("乖离偏大")
    if (quote.get("turnover") or 0) > 22:
        flags.append("高换手")
    if quote.get("pe") is not None and quote["pe"] <= 0:
        flags.append("亏损")
    return flags


def build_execution_guide(
    plan: dict,
    quote: dict,
    confidence: str = "medium",
    relative_edge: float | None = None,
) -> dict:
    """Turn a plan into a short, conditional action guide.

    The wording is intentionally conditional: it explains what to wait for and
    what invalidates the idea. It never says "buy this now" as an unconditional
    instruction.
    """
    price = float(quote.get("price") or plan.get("entry_mid") or 0.0)
    pct = float(quote.get("pct") or 0.0)
    entry_low = float(plan.get("entry_low") or 0.0)
    entry_high = float(plan.get("entry_high") or entry_low)
    stop = float(plan.get("stop") or 0.0)
    target = float(plan.get("target1") or 0.0)
    weight = float(plan.get("suggested_weight_pct") or 5.0)
    first_tranche = max(1.0, round(weight / 2.0, 1))
    weak_model = confidence == "low" or (
        relative_edge is not None and relative_edge < 0.005
    )
    entry_text = f"{entry_low:.2f}–{entry_high:.2f}"
    stop_text = f"{stop:.2f}"
    target_text = f"{target:.2f}"
    high_abort = round(entry_high * 1.02, 2)

    if price and stop and price <= stop:
        return {
            "tone": "danger",
            "label": "计划失效",
            "headline": f"现价已跌破止损 {stop_text}，不补仓、不摊薄，先退出观察。",
            "steps": [
                "已持仓：按止损纪律处理，不用“再等等”替代执行。",
                "未持仓：本次计划作废，等重新站回关注区间再评估。",
                "记录这次失败原因，不因为排名靠前就放宽条件。",
            ],
            "avoid": "跌破止损后继续补仓，是这类计划最常见的亏损放大方式。",
            "position": f"计划仓位上限 {weight:.1f}%，当前不应再加。",
            "checklist": ["收盘价是否收回止损上方", "板块是否仍在净流出", "下一次信号是否重新满足条件"],
        }

    if price and target and price >= target:
        return {
            "tone": "success",
            "label": "到达目标",
            "headline": f"现价已到第一目标 {target_text}，按计划分批止盈，不把盈利单拖成亏损单。",
            "steps": [
                "先减一部分仓位，剩余仓位用移动止损保护利润。",
                "不要因为“还能涨”而把止损上移到成本下方。",
                "若放量滞涨或跌破 MA5，优先兑现。",
            ],
            "avoid": "目标位不是必须卖光的点，但必须是重新评估风险的点。",
            "position": f"原计划仓位上限 {weight:.1f}%，建议先兑现一部分。",
            "checklist": ["是否放量滞涨", "是否跌破 MA5", "剩余仓位的止损是否上移"],
        }

    if weak_model:
        return {
            "tone": "observe",
            "label": "只观察",
            "headline": "模型相对优势很弱，先不执行；把它当作跟踪对象，而不是买入指令。",
            "steps": [
                "先加入自选，观察 1–3 个交易日，不因为排名靠前就下单。",
                f"等价格回到 {entry_text} 且量能/趋势转强，再重新评估。",
                "没有触发条件就保持空仓，空仓也是策略的一部分。",
            ],
            "avoid": "“来都来了”不是买入理由；弱信号期降低仓位比提高频率更重要。",
            "position": f"即使参与，首笔也不超过 {first_tranche:.1f}%，总仓位不超过 {weight:.1f}%。",
            "checklist": ["模型优势是否转正", "价格是否进入区间", "大盘是否配合"],
        }

    if price and entry_low <= price <= entry_high:
        return {
            "tone": "action",
            "label": "已进买区",
            "headline": f"价格在关注区内，可按计划小仓分批；首笔不超过 {first_tranche:.1f}%，不要一次买满。",
            "steps": [
                f"第 1 笔：现价附近先买计划仓位的一半（≤{first_tranche:.1f}%）。",
                "第 2 笔：回踩不破区间下沿、分时企稳后再补；不企稳就不补。",
                f"防守：收盘跌破 {stop_text} 停止计划，不补仓。",
            ],
            "avoid": f"若盘中冲高超过 {high_abort:.2f} 再追，盈亏比会明显变差。",
            "position": f"首笔 ≤{first_tranche:.1f}% · 总仓位 ≤{weight:.1f}% · 单笔风险预算 {plan.get('risk_budget_pct', 0.5)}%。",
            "checklist": ["价格仍在区间内", "分时没有放量跳水", "大盘温度不是过热"],
        }

    if price and price > entry_high:
        return {
            "tone": "wait",
            "label": "等回踩",
            "headline": f"现价高于计划上沿，不追；等回到 {entry_text} 再考虑，错过就等下一次。",
            "steps": [
                "现在只加入自选，不按现价追单。",
                f"价格回到 {entry_text} 且没有放量跌破，再分 2 笔。",
                f"若直接高开超过 {high_abort:.2f}，放弃本次计划。",
            ],
            "avoid": "追高的主要代价不是买贵几分钱，而是止损距离被拉大、盈亏比变差。",
            "position": f"计划首笔 ≤{first_tranche:.1f}%，总仓位 ≤{weight:.1f}%。",
            "checklist": ["是否回到区间", "回踩是否缩量", "是否守住区间下沿"],
        }

    return {
        "tone": "watch",
        "label": "观察收回",
        "headline": f"价格低于关注区但未破止损，先不急着买；等重新站回 {entry_low:.2f} 再评估。",
        "steps": [
            "不补仓，不摊薄，先看是否能在 1–2 日内收回区间下沿。",
            "收回且量能配合，再按计划分批；收不回就放弃。",
            f"跌破 {stop_text} 直接执行止损纪律。",
        ],
        "avoid": "下跌中的“便宜”不是买点，趋势没有修复前不要用仓位试错。",
        "position": f"当前不新增仓位；原计划上限 {weight:.1f}%。",
        "checklist": ["是否收回区间下沿", "是否缩量止跌", "板块是否同步修复"],
    }

