"""Application configuration and shared constants."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

APP_NAME = "A股雷达"
APP_NAME_EN = "StockLens"
APP_VERSION = "0.6.1"
APP_TAGLINE = "用公开数据做可解释的观察，不预测确定的未来"

ROOT_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT_DIR / "data"
CACHE_DIR = DATA_DIR / "cache"
KLINE_CACHE_DIR = CACHE_DIR / "klines"
WEB_DIR = ROOT_DIR / "web"
WATCHLIST_FILE = DATA_DIR / "watchlist.json"
LEARNING_FILE = DATA_DIR / "learning.json"
PAPER_FILE = DATA_DIR / "paper.json"

CN_TZ = timezone(timedelta(hours=8))

INDEXES = [
    {"secid": "1.000001", "code": "000001", "name": "上证指数", "short": "上证"},
    {"secid": "0.399001", "code": "399001", "name": "深证成指", "short": "深成"},
    {"secid": "0.399006", "code": "399006", "name": "创业板指", "short": "创业板"},
    {"secid": "1.000688", "code": "000688", "name": "科创50", "short": "科创50"},
    {"secid": "1.000300", "code": "000300", "name": "沪深300", "short": "沪深300"},
    {"secid": "0.399905", "code": "399905", "name": "中证500", "short": "中证500"},
]

BENCHMARK_SECID = "1.000300"

PROFILES = {
    "conservative": {
        "key": "conservative",
        "label": "稳健",
        "description": "低波动、大市值、趋势确认，宁可错过不做错。",
        "max_picks": 4,
        "max_atr_pct": 0.055,
        "min_market_cap": 20_000_000_000,
        "min_trend_score": 52,
        "max_pct_change": 5.0,
        "max_per_industry": 2,
        "risk_budget_pct": 0.004,
        "max_weight_pct": 0.12,
    },
    "balanced": {
        "key": "balanced",
        "label": "均衡",
        "description": "趋势与估值兼顾，接受中等波动，适合大多数观察场景。",
        "max_picks": 5,
        "max_atr_pct": 0.075,
        "min_market_cap": 8_000_000_000,
        "min_trend_score": 45,
        "max_pct_change": 7.0,
        "max_per_industry": 2,
        "risk_budget_pct": 0.005,
        "max_weight_pct": 0.15,
    },
    "aggressive": {
        "key": "aggressive",
        "label": "进攻",
        "description": "更高弹性与动量，波动和回撤容忍度更高，仅适合小仓位研究。",
        "max_picks": 6,
        "max_atr_pct": 0.10,
        "min_market_cap": 3_000_000_000,
        "min_trend_score": 38,
        "max_pct_change": 9.0,
        "max_per_industry": 1,
        "risk_budget_pct": 0.006,
        "max_weight_pct": 0.18,
    },
}

DEFAULT_PROFILE = "balanced"

FACTOR_WEIGHTS = {
    "trend": 0.22,
    "momentum": 0.18,
    "volume": 0.12,
    "rsi": 0.08,
    "macd": 0.10,
    "relative": 0.10,
    "risk": 0.12,
    "flow": 0.08,
}

FACTOR_META = {
    "trend": {"label": "趋势结构", "weight": 0.22, "hint": "价格与均线排列、MA20 斜率"},
    "momentum": {"label": "动量强度", "weight": 0.18, "hint": "5/20 日涨幅，过滤过热"},
    "volume": {"label": "量能配合", "weight": 0.12, "hint": "量比、5/20 日均量比"},
    "rsi": {"label": "超买超卖", "weight": 0.08, "hint": "RSI(14)，偏好 50–68"},
    "macd": {"label": "MACD 动能", "weight": 0.10, "hint": "柱体方向与拐点"},
    "relative": {"label": "相对强弱", "weight": 0.10, "hint": "相对沪深300的 5 日超额"},
    "risk": {"label": "波动风险", "weight": 0.12, "hint": "ATR、乖离率、回撤"},
    "flow": {"label": "资金/估值", "weight": 0.08, "hint": "主力净流入占比、PE 合理性"},
}

FEATURE_LABELS = {
    "ret5": "5日动量",
    "ret20": "20日动量",
    "trend": "MA20乖离",
    "ma_gap": "MA5/MA20",
    "rsi": "RSI(14)",
    "macd": "MACD柱",
    "vol_ratio": "量比",
    "atr_pct": "ATR波动",
    "rel5": "5日超额",
    "dist_high20": "距20日高点",
    "market_ma": "市场MA20",
}

EASTMONEY_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    ),
    "Referer": "https://quote.eastmoney.com/",
    "Accept": "application/json,text/plain,*/*",
}

SNAPSHOT_FIELDS = ",".join(
    [
        "f2", "f3", "f4", "f5", "f6", "f7", "f8", "f9", "f10",
        "f12", "f13", "f14", "f15", "f16", "f17", "f18", "f20", "f21",
        "f22", "f23", "f24", "f25", "f62", "f100", "f115",
    ]
)

DISCLAIMER = (
    "本工具仅用于个人研究、数据整理与风险教育，不构成任何投资建议或收益承诺。"
    "行情来自东方财富公开接口，可能存在延迟、缺失或错误；A股实行 T+1，"
    "实际交易还需考虑手续费、滑点、涨跌停与流动性。股市有风险，决策需独立判断。"
)


def now_cn() -> datetime:
    return datetime.now(CN_TZ)


def market_phase(now: datetime | None = None) -> dict:
    """Return a coarse A-share session phase.

    Holidays are inferred from data freshness rather than a holiday calendar;
    the UI also shows the quote timestamp so a stale holiday snapshot is visible.
    """
    now = now or now_cn()
    if now.weekday() >= 5:
        return {"key": "closed", "label": "周末休市", "is_open": False, "is_trading_day": False}

    minutes = now.hour * 60 + now.minute
    if 9 * 60 + 15 <= minutes < 9 * 60 + 25:
        return {"key": "auction", "label": "集合竞价", "is_open": True, "is_trading_day": True}
    if 9 * 60 + 25 <= minutes < 9 * 60 + 30:
        return {"key": "preopen", "label": "等待开盘", "is_open": False, "is_trading_day": True}
    if 9 * 60 + 30 <= minutes <= 11 * 60 + 30:
        return {"key": "open", "label": "交易中", "is_open": True, "is_trading_day": True}
    if 11 * 60 + 30 < minutes < 13 * 60:
        return {"key": "lunch", "label": "午间休市", "is_open": False, "is_trading_day": True}
    if 13 * 60 <= minutes <= 15 * 60:
        return {"key": "open", "label": "交易中", "is_open": True, "is_trading_day": True}
    if minutes < 9 * 60 + 15:
        return {"key": "premarket", "label": "盘前", "is_open": False, "is_trading_day": True}
    return {"key": "closed", "label": "已收盘", "is_open": False, "is_trading_day": True}
