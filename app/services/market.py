"""Market orchestration: snapshot, breadth, sectors, picks, watchlist, detail."""

from __future__ import annotations

import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from statistics import median
from threading import Lock

from app.cache import DiskJsonCache
from app.config import (
    BENCHMARK_SECID,
    CN_TZ,
    DEFAULT_PROFILE,
    DISCLAIMER,
    INDEXES,
    KLINE_CACHE_DIR,
    LEARNING_FILE,
    PAPER_FILE,
    PROFILES,
    WATCHLIST_FILE,
    market_phase,
    now_cn,
)
from app.providers.eastmoney import EastmoneyProvider, normalize_code, secid_for
from app.services.indicators import (
    build_samples,
    chart_payload,
    current_features,
    market_map,
)
from app.services.learning import LearningStore
from app.services.model import predict, train_model
from app.services.paper import PaperStore
from app.services.scoring import (
    build_execution_guide,
    build_plan,
    risk_flags,
    score_candidate,
)
from app.store import WatchlistStore

SNAPSHOT_TTL = 25
OVERVIEW_TTL = 10
SECTOR_TTL = 25
INDEX_SPARK_TTL = 600
KLINE_TTL_OPEN = 300
KLINE_TTL_CLOSED = 21_600
ANALYSIS_TTL = 240
PICK_TTL = 90


def _clamp(value: float, low: float = 0.0, high: float = 100.0) -> float:
    return max(low, min(high, value))


def _safe_mean(values: list[float]) -> float:
    return sum(values) / len(values) if values else 0.0


def _round(value: float | None, digits: int = 2) -> float | None:
    return round(value, digits) if value is not None else None


def _session_progress(now: datetime) -> float:
    """Fraction of the 240-minute A-share continuous session elapsed."""
    if now.weekday() >= 5:
        return 0.0
    minutes = now.hour * 60 + now.minute + now.second / 60.0
    morning_open = 9 * 60 + 30
    morning_close = 11 * 60 + 30
    afternoon_open = 13 * 60
    afternoon_close = 15 * 60
    if minutes < morning_open:
        return 0.0
    if morning_open <= minutes <= morning_close:
        return min(1.0, (minutes - morning_open) / 240.0)
    if morning_close < minutes < afternoon_open:
        return 0.5
    if afternoon_open <= minutes <= afternoon_close:
        return min(1.0, (120.0 + minutes - afternoon_open) / 240.0)
    return 1.0


class MarketService:
    def __init__(self, provider: EastmoneyProvider | None = None):
        self.provider = provider or EastmoneyProvider()
        self.cache = DiskJsonCache(KLINE_CACHE_DIR)
        self.watchlist = WatchlistStore(WATCHLIST_FILE)
        self.learning = LearningStore(LEARNING_FILE)
        self.paper = PaperStore(PAPER_FILE)
        self._snapshot: list[dict] | None = None
        self._snapshot_at = 0.0
        self._snapshot_stale = False
        self._snapshot_meta: dict = {}
        self._snapshot_lock = Lock()
        self._overview: dict | None = None
        self._overview_at = 0.0
        self._overview_lock = Lock()
        self._sectors: dict | None = None
        self._sectors_at = 0.0
        self._sector_lock = Lock()
        self._index_sparks: dict[str, list[dict]] | None = None
        self._index_sparks_at = 0.0
        self._index_lock = Lock()
        self._turnover_avg: float | None = None
        self._turnover_at = 0.0
        self._turnover_lock = Lock()
        self._analysis: dict | None = None
        self._analysis_at = 0.0
        self._analysis_lock = Lock()
        self._leaders: dict | None = None
        self._leaders_at = 0.0
        self._leaders_lock = Lock()
        self._pick_cache: dict[str, tuple[float, dict]] = {}
        self._errors: list[str] = []

    # ------------------------------------------------------------------ data
    def get_snapshot(self, force: bool = False) -> list[dict]:
        with self._snapshot_lock:
            now = time.time()
            if (
                not force
                and self._snapshot is not None
                and now - self._snapshot_at < SNAPSHOT_TTL
            ):
                return self._snapshot
            try:
                rows = self.provider.fetch_snapshot()
                deduped = {row["code"]: row for row in rows}
                self._snapshot = list(deduped.values())
                self._snapshot_at = time.time()
                self._snapshot_stale = False
                self._snapshot_meta = dict(getattr(self.provider, "last_snapshot_meta", {}))
                self._errors = [error for error in self._errors if not error.startswith("snapshot:")]
                return self._snapshot
            except Exception as exc:  # noqa: BLE001 - stale cache is useful
                if self._snapshot is not None:
                    self._snapshot_stale = True
                    self._errors.append(f"snapshot: {exc}")
                    self._errors = self._errors[-5:]
                    return self._snapshot
                raise

    def _kline_ttl(self) -> int:
        phase = market_phase()
        return KLINE_TTL_OPEN if phase["is_open"] else KLINE_TTL_CLOSED

    def _load_kline(self, secid: str, limit: int = 320) -> list[dict]:
        ttl = self._kline_ttl()
        data, _ = self.cache.get_or_fetch(
            f"kline_{secid}_{limit}",
            ttl,
            lambda: self.provider.fetch_kline(secid, limit),
        )
        return data if isinstance(data, list) else []

    def _load_klines(self, stocks: list[dict]) -> dict[str, list[dict]]:
        result: dict[str, list[dict]] = {}
        with ThreadPoolExecutor(max_workers=8) as pool:
            futures = {
                pool.submit(self._load_kline, stock["secid"], 320): stock
                for stock in stocks
            }
            for future in as_completed(futures):
                stock = futures[future]
                try:
                    bars = future.result()
                    if len(bars) >= 80:
                        result[stock["code"]] = bars
                except Exception:  # noqa: BLE001 - one bad symbol must not kill the scan
                    continue
        return result

    # -------------------------------------------------------------- overview
    def _get_index_sparks(self) -> dict[str, list[dict]]:
        with self._index_lock:
            if self._index_sparks is not None and time.time() - self._index_sparks_at < INDEX_SPARK_TTL:
                return self._index_sparks
            secids = [item["secid"] for item in INDEXES]
            try:
                self._index_sparks = self.provider.fetch_index_klines(secids, lmt=30)
                self._index_sparks_at = time.time()
            except Exception:  # noqa: BLE001 - optional decoration
                self._index_sparks = self._index_sparks or {}
            return self._index_sparks

    def _get_indices(self) -> list[dict]:
        indices = self.provider.fetch_indices()
        sparks = self._get_index_sparks()
        by_secid = {item.get("secid"): item for item in indices}
        ordered: list[dict] = []
        for meta in INDEXES:
            item = by_secid.get(meta["secid"])
            if not item:
                item = {
                    "code": meta["code"],
                    "secid": meta["secid"],
                    "name": meta["name"],
                    "price": None,
                    "pct": 0.0,
                    "change": 0.0,
                    "amount": 0.0,
                    "spark": [],
                }
            bars = sparks.get(meta["secid"]) or []
            item["spark"] = [round(bar["close"], 2) for bar in bars[-26:]]
            item["spark_dates"] = [bar["date"] for bar in bars[-26:]]
            item["short"] = meta["short"]
            ordered.append(item)
        return ordered

    def _get_sectors(self) -> dict:
        with self._sector_lock:
            if self._sectors is not None and time.time() - self._sectors_at < SECTOR_TTL:
                return self._sectors
            leaders: list[dict] = []
            laggards: list[dict] = []
            with ThreadPoolExecutor(max_workers=2) as pool:
                leader_future = pool.submit(self.provider.fetch_sectors, 12, False)
                laggard_future = pool.submit(self.provider.fetch_sectors, 6, True)
                try:
                    leaders = leader_future.result()
                except Exception:  # noqa: BLE001
                    leaders = []
                try:
                    laggards = laggard_future.result()
                except Exception:  # noqa: BLE001
                    laggards = []
            seen = set()
            clean_leaders = []
            for item in leaders:
                if item["code"] in seen:
                    continue
                seen.add(item["code"])
                clean_leaders.append(item)
            clean_laggards = []
            for item in laggards:
                if item["code"] in seen:
                    continue
                seen.add(item["code"])
                clean_laggards.append(item)
            self._sectors = {
                "leaders": clean_leaders[:10],
                "laggards": clean_laggards[:5],
            }
            self._sectors_at = time.time()
            return self._sectors

    def _get_turnover_avg(self) -> tuple[float | None, str | None]:
        with self._turnover_lock:
            if self._turnover_avg is not None and time.time() - self._turnover_at < 1200:
                return self._turnover_avg, None
            try:
                sh = self._load_kline("1.000001", 12)
                sz = self._load_kline("0.399001", 12)
                sh_by_date = {bar["date"]: bar["amount"] for bar in sh}
                sz_by_date = {bar["date"]: bar["amount"] for bar in sz}
                dates = sorted(set(sh_by_date) & set(sz_by_date))
                today = now_cn().strftime("%Y-%m-%d")
                completed = [date for date in dates if date < today][-5:]
                if not completed:
                    return None, "历史成交额样本不足"
                values = [sh_by_date[date] + sz_by_date[date] for date in completed]
                self._turnover_avg = _safe_mean(values)
                self._turnover_at = time.time()
                return self._turnover_avg, None
            except Exception as exc:  # noqa: BLE001
                return None, f"历史成交额获取失败: {exc}"

    @staticmethod
    def _breadth(snap: list[dict]) -> dict:
        valid = [item for item in snap if not item.get("is_suspended") and item.get("price")]
        up = sum(1 for item in valid if item["pct"] > 0.01)
        down = sum(1 for item in valid if item["pct"] < -0.01)
        flat = len(valid) - up - down
        limit_up = sum(1 for item in valid if item["is_limit_up"])
        limit_down = sum(1 for item in valid if item["is_limit_down"])
        strong = sum(1 for item in valid if item["pct"] >= 5)
        weak = sum(1 for item in valid if item["pct"] <= -5)
        amount = sum(item["amount"] for item in valid)
        pcts = [item["pct"] for item in valid]
        total = max(1, len(valid))
        return {
            "universe": len(valid),
            "up": up,
            "down": down,
            "flat": flat,
            "up_ratio": round(up / total, 4),
            "down_ratio": round(down / total, 4),
            "limit_up": limit_up,
            "limit_down": limit_down,
            "strong": strong,
            "weak": weak,
            "median_pct": round(median(pcts), 3) if pcts else 0.0,
            "amount": round(amount, 2),
            "amount_yi": round(amount / 1e8, 1),
            "advance_decline_ratio": round(up / max(down, 1), 2),
        }

    @staticmethod
    def _temperature(breadth: dict, indices: list[dict], turnover_ratio: float | None) -> dict:
        up_ratio = breadth["up_ratio"]
        index_pct = _safe_mean([item["pct"] for item in indices if item.get("price")])
        breadth_score = _clamp(up_ratio * 100)
        index_score = _clamp(50 + index_pct * 12)
        limit_total = breadth["limit_up"] + breadth["limit_down"]
        limit_score = _clamp(50 + (breadth["limit_up"] - breadth["limit_down"]) / max(limit_total, 20) * 55)
        components = [
            (0.48, breadth_score),
            (0.27, index_score),
            (0.15, limit_score),
        ]
        if turnover_ratio is not None:
            components.append((0.10, _clamp(50 + (turnover_ratio - 1.0) * 55)))
        weight_sum = sum(weight for weight, _ in components)
        score = sum(weight * value for weight, value in components) / weight_sum
        score = round(_clamp(score), 1)
        if score < 30:
            label = "冰点"
        elif score < 45:
            label = "偏冷"
        elif score < 55:
            label = "中性"
        elif score < 70:
            label = "偏热"
        else:
            label = "过热"
        return {
            "score": score,
            "label": label,
            "breadth_component": round(breadth_score, 1),
            "index_component": round(index_score, 1),
            "limit_component": round(limit_score, 1),
            "turnover_component": round(components[-1][1], 1) if turnover_ratio is not None else None,
            "note": "情绪温度是描述性指标，不是涨跌预测；过热时更应控制追高。",
        }

    def _movers(self, snap: list[dict]) -> dict:
        liquid = [
            item for item in snap
            if not item.get("is_st") and not item.get("is_suspended") and item["amount"] > 5e7
        ]
        gainers = sorted(liquid, key=lambda item: item["pct"], reverse=True)[:5]
        active = sorted(liquid, key=lambda item: item["amount"], reverse=True)[:5]

        def slim(item: dict) -> dict:
            return {
                "code": item["code"],
                "name": item["name"],
                "price": item["price"],
                "pct": item["pct"],
                "amount_yi": round(item["amount"] / 1e8, 2),
                "industry": item["industry"],
                "is_limit_up": item["is_limit_up"],
            }

        return {"gainers": [slim(item) for item in gainers], "active": [slim(item) for item in active]}

    def get_overview(self, force: bool = False) -> dict:
        with self._overview_lock:
            now = time.time()
            if not force and self._overview and now - self._overview_at < OVERVIEW_TTL:
                return self._overview
            snap = self.get_snapshot(force)
            phase = market_phase()
            try:
                indices = self._get_indices()
                index_error = None
            except Exception as exc:  # noqa: BLE001
                indices = []
                index_error = str(exc)
            sectors = self._get_sectors()
            breadth = self._breadth(snap)
            turnover_avg, turnover_error = self._get_turnover_avg()
            latest_bar = max(
                (
                    date
                    for index in indices
                    for date in (index.get("spark_dates") or [])
                ),
                default=None,
            )
            today = now_cn().strftime("%Y-%m-%d")
            phase_note = None
            if (
                phase["is_open"]
                and latest_bar
                and latest_bar < today
                and now_cn().hour * 60 + now_cn().minute >= 10 * 60
            ):
                phase_note = f"指数最新数据停留在 {latest_bar}，当前可能休市或行情源未更新。"
                phase = {
                    "key": "closed",
                    "label": "休市 / 数据未更新",
                    "is_open": False,
                    "is_trading_day": True,
                    "note": phase_note,
                }
            progress = 1.0 if phase_note else _session_progress(now_cn())
            raw_ratio = (
                breadth["amount"] / turnover_avg
                if turnover_avg and breadth["amount"] > 0
                else None
            )
            projected_amount = (
                breadth["amount"] / progress
                if 0.08 <= progress < 0.999
                else breadth["amount"]
            )
            turnover_ratio = (
                projected_amount / turnover_avg
                if turnover_avg and projected_amount > 0
                else None
            )
            temperature = self._temperature(breadth, indices, turnover_ratio)
            overview = {
                "app": "A股雷达",
                "asOf": datetime.now(CN_TZ).isoformat(timespec="seconds"),
                "market": {
                    **phase,
                    "quoteDate": latest_bar or today,
                    "phaseNote": phase_note,
                },
                "indices": indices,
                "breadth": breadth,
                "temperature": temperature,
                "turnover": {
                    "amount": breadth["amount"],
                    "amount_yi": breadth["amount_yi"],
                    "avg5_yi": round(turnover_avg / 1e8, 1) if turnover_avg else None,
                    "ratio": round(turnover_ratio, 3) if turnover_ratio else None,
                    "rawRatio": round(raw_ratio, 3) if raw_ratio else None,
                    "projected_yi": round(projected_amount / 1e8, 1),
                    "progressPct": round(progress * 100, 1),
                    "note": turnover_error or "按时段进度线性折算全天成交额，再与沪深两市5日均值比较（估算）",
                },
                "sectors": sectors,
                "movers": self._movers(snap),
                "dataQuality": {
                    "source": "东方财富公开行情",
                    "snapshotAgeSec": round(max(0.0, time.time() - self._snapshot_at), 1),
                    "stale": self._snapshot_stale,
                    "universe": breadth["universe"],
                    "quoteDate": latest_bar or today,
                    "coverage": self._snapshot_meta.get("coverage"),
                    "pageFailures": self._snapshot_meta.get("page_failures"),
                    "error": index_error or (self._errors[-1] if self._errors else None),
                    "latencyNote": "公开接口通常有数秒延迟，非 Level-2；盘中数据仅供参考",
                },
                "profiles": list(PROFILES.values()),
                "disclaimer": DISCLAIMER,
            }
            self._overview = overview
            self._overview_at = time.time()
            return overview

    # --------------------------------------------------------------- leaders
    @staticmethod
    def _leader_score(stock: dict, pool_entry: dict | None, sector_pct: float, max_amount: float) -> float:
        amount_ratio = min(1.0, (stock.get("amount") or 0) / max_amount) if max_amount else 0.0
        pct = float(stock.get("pct") or 0)
        turnover = float(stock.get("turnover") or 0)
        change60 = float(stock.get("change60") or 0)
        sector_score = _clamp(50 + sector_pct * 10)
        if pool_entry:
            board = min(100.0, 40.0 + (int(pool_entry.get("board_count") or 1) - 1) * 15.0)
            amount_value = max(1.0, float(stock.get("amount") or 0))
            seal = _clamp(float(pool_entry.get("seal_fund") or 0) / amount_value * 500)
            return round(
                0.50 * board
                + 0.18 * amount_ratio * 100
                + 0.08 * _clamp(pct / 10 * 100)
                + 0.06 * _clamp(turnover / 20 * 100)
                + 0.10 * sector_score
                + 0.08 * seal
                + min(30.0, int(pool_entry.get("board_count") or 1) * 8.0),
                1,
            )
        return round(
            0.30 * amount_ratio * 100
            + 0.18 * _clamp(pct / 10 * 100)
            + 0.12 * _clamp(turnover / 20 * 100)
            + 0.22 * sector_score
            + 0.18 * _clamp((change60 + 20) / 80 * 100),
            1,
        )

    def _leader_card(
        self,
        stock: dict,
        pool_entry: dict | None,
        sector_pct: float,
        max_amount: float,
    ) -> dict:
        amount = float(stock.get("amount") or 0)
        turnover = stock.get("turnover")
        board_count = int(pool_entry.get("board_count") or 0) if pool_entry else 0
        score = self._leader_score(stock, pool_entry, sector_pct, max_amount)
        tags = []
        if board_count:
            tags.append(f"{board_count}连板")
        if sector_pct:
            tags.append(f"板块 {sector_pct:+.2f}%")
        if amount:
            tags.append(f"成交 {amount / 1e8:.1f}亿")
        if pool_entry and pool_entry.get("seal_fund"):
            tags.append(f"封单 {float(pool_entry['seal_fund']) / 1e8:.1f}亿")
        if pool_entry and pool_entry.get("open_times"):
            tags.append(f"开板 {int(pool_entry['open_times'])}次")
        risks = []
        if board_count >= 3:
            risks.append("高位连板，次日分歧与回撤风险显著")
        if pool_entry and int(pool_entry.get("open_times") or 0) >= 3:
            risks.append("封板反复，封单质量一般")
        if turnover and float(turnover) > 20:
            risks.append("换手率过高，筹码交换剧烈")
        if not pool_entry:
            risks.append("非涨停股，强度来自成交/趋势，不是连板龙头")
        return {
            "code": stock["code"],
            "secid": stock["secid"],
            "name": stock["name"],
            "industry": stock.get("industry") or "未分类",
            "price": stock.get("price"),
            "pct": stock.get("pct") or 0.0,
            "amount": amount,
            "amount_yi": round(amount / 1e8, 2),
            "turnover": turnover,
            "volume_ratio": stock.get("volume_ratio"),
            "market_cap_yi": round((stock.get("market_cap") or 0) / 1e8, 1),
            "change60": stock.get("change60"),
            "main_inflow": stock.get("main_inflow") or 0.0,
            "main_inflow_yi": round((stock.get("main_inflow") or 0) / 1e8, 2),
            "sector_pct": round(sector_pct, 2),
            "board_count": board_count,
            "first_time": pool_entry.get("first_time") if pool_entry else None,
            "last_time": pool_entry.get("last_time") if pool_entry else None,
            "open_times": int(pool_entry.get("open_times") or 0) if pool_entry else 0,
            "seal_fund_yi": round(float(pool_entry.get("seal_fund") or 0) / 1e8, 2) if pool_entry else 0.0,
            "stat_days": int(pool_entry.get("stat_days") or 0) if pool_entry else 0,
            "is_limit_up": bool(pool_entry),
            "score": score,
            "tags": tags,
            "risks": risks or ["未触发额外风险项，仍需遵守仓位与止损"],
            "reason": " · ".join(tags) if tags else "成交/趋势相对靠前",
        }

    def get_leaders(self, force: bool = False) -> dict:
        with self._leaders_lock:
            now = time.time()
            if not force and self._leaders and now - self._leaders_at < 15:
                return self._leaders
            snap = self.get_snapshot()
            by_code = {item["code"]: item for item in snap}
            sectors = self._get_sectors()
            sector_rows = [*(sectors.get("leaders") or []), *(sectors.get("laggards") or [])]
            sector_pct_by_name = {row["name"]: row["pct"] for row in sector_rows}
            benchmark = self._load_kline(BENCHMARK_SECID, 10)
            today = now_cn().strftime("%Y-%m-%d")
            trade_date = (
                benchmark[-1]["date"].replace("-", "")
                if benchmark
                else now_cn().strftime("%Y%m%d")
            )
            pool: list[dict] = []
            pool_error = None
            try:
                pool = self.provider.fetch_limit_up_pool(trade_date)
            except Exception as exc:  # noqa: BLE001 - fall back to trend leaders
                pool_error = str(exc)
            pool_by_code = {entry["code"]: entry for entry in pool}
            liquid = [
                item for item in snap
                if not item.get("is_st") and not item.get("is_suspended") and item["amount"] > 5e7
            ]
            max_amount = max((item["amount"] for item in liquid), default=1.0)

            pool_cards = []
            for entry in pool:
                stock = by_code.get(entry["code"])
                if not stock:
                    continue
                sector_pct = sector_pct_by_name.get(stock.get("industry") or entry.get("industry"), 0.0)
                card = self._leader_card(stock, entry, sector_pct, max_amount)
                pool_cards.append(card)

            non_pool = sorted(
                [
                    item for item in liquid
                    if item["code"] not in pool_by_code
                    and item["pct"] > 0
                ],
                key=lambda item: (item["pct"], item["amount"]),
                reverse=True,
            )[:20]
            non_pool_cards = []
            for stock in non_pool:
                sector_pct = sector_pct_by_name.get(stock.get("industry"), 0.0)
                non_pool_cards.append(self._leader_card(stock, None, sector_pct, max_amount))

            market_leader = None
            if pool_cards:
                market_leader = sorted(
                    pool_cards,
                    key=lambda card: (card["board_count"], card.get("stat_days") or 0, card["amount"]),
                    reverse=True,
                )[0]
            elif non_pool_cards:
                market_leader = sorted(non_pool_cards, key=lambda card: card["score"], reverse=True)[0]

            all_cards = sorted(pool_cards + non_pool_cards, key=lambda card: card["score"], reverse=True)
            candidates = []
            industry_count: dict[str, int] = {}
            for card in all_cards:
                industry = card["industry"]
                if industry_count.get(industry, 0) >= 2:
                    continue
                candidates.append(card)
                industry_count[industry] = industry_count.get(industry, 0) + 1
                if len(candidates) >= 8:
                    break

            def leader_for(key: str, label: str, hint: str, selector, metric) -> dict:
                source = pool_cards if key == "space" and pool_cards else all_cards
                if not source:
                    return {"key": key, "label": label, "hint": hint, "stock": None}
                best = selector(source)
                return {
                    "key": key,
                    "label": label,
                    "hint": hint,
                    "stock": best,
                    "metric": metric(best),
                }

            types = [
                leader_for(
                    "space", "空间龙头", "连板高度与封板质量",
                    lambda cards: max(cards, key=lambda c: (c["board_count"], c["score"])),
                    lambda c: f"{c['board_count']}连板" if c["board_count"] else f"涨幅 {c['pct']:+.2f}%",
                ),
                leader_for(
                    "turnover", "成交龙头", "全市场成交额与资金关注度",
                    lambda cards: max(cards, key=lambda c: c["amount"]),
                    lambda c: f"成交 {c['amount_yi']:.1f}亿",
                ),
                leader_for(
                    "trend", "趋势龙头", "60日强度与中期趋势",
                    lambda cards: max(cards, key=lambda c: c.get("change60") or -999),
                    lambda c: f"60日 {c['change60']:+.1f}%" if c.get("change60") is not None else "—",
                ),
                leader_for(
                    "capital", "资金龙头", "主力净流入与承接",
                    lambda cards: max(cards, key=lambda c: c["main_inflow"]),
                    lambda c: f"净流入 {c['main_inflow_yi']:+.2f}亿",
                ),
            ]

            sector_leaders = []
            for sector in (sectors.get("leaders") or [])[:6]:
                code = normalize_code(sector.get("leader_code") or "")
                stock = by_code.get(code)
                if not stock:
                    continue
                pool_entry = pool_by_code.get(code)
                card = self._leader_card(
                    stock,
                    pool_entry,
                    float(sector.get("pct") or 0.0),
                    max_amount,
                )
                card["sector_name"] = sector.get("name")
                card["sector_pct"] = sector.get("pct")
                card["sector_up"] = sector.get("up_count")
                card["sector_down"] = sector.get("down_count")
                card["sector_inflow_yi"] = round(float(sector.get("main_inflow") or 0) / 1e8, 2)
                sector_leaders.append(card)

            result = {
                "asOf": datetime.now(CN_TZ).isoformat(timespec="seconds"),
                "tradeDate": f"{trade_date[:4]}-{trade_date[4:6]}-{trade_date[6:]}",
                "market": market_phase(),
                "marketLeader": market_leader,
                "types": types,
                "sectorLeaders": sector_leaders,
                "candidates": candidates,
                "stats": {
                    "limitUpCount": int(
                        getattr(self.provider, "last_limit_up_total", 0) or len(pool_cards)
                    ),
                    "matchedCount": len(pool_cards),
                    "poolTotal": int(getattr(self.provider, "last_limit_up_total", 0) or len(pool_cards)),
                    "maxBoard": max((card["board_count"] for card in pool_cards), default=0),
                    "maxBoardName": market_leader["name"] if market_leader and market_leader["board_count"] else "—",
                    "sectorCount": len(sector_leaders),
                    "tradeDate": f"{trade_date[:4]}-{trade_date[4:6]}-{trade_date[6:]}",
                },
                "poolError": pool_error,
                "method": (
                    "龙头雷达综合连板高度、封板质量、成交额、板块强度、主力净流入与趋势；"
                    "空间龙头按连板高度优先，其余类型按对应维度取最大值。"
                ),
                "disclaimer": DISCLAIMER,
                "isFallback": not bool(pool_cards),
            }
            self._leaders = result
            self._leaders_at = time.time()
            return result

    # --------------------------------------------------------------- analysis
    @staticmethod
    def _preselect(snap: list[dict], limit: int = 64) -> list[dict]:
        pool: list[dict] = []
        for item in snap:
            price = item.get("price") or 0
            amount = item.get("amount") or 0
            pct = item.get("pct") or 0
            turnover = item.get("turnover") or 0
            volume_ratio = item.get("volume_ratio") or 0
            if item.get("is_st") or item.get("is_suspended"):
                continue
            if not (3.0 <= price <= 300.0):
                continue
            if amount < 120_000_000:
                continue
            if (item.get("market_cap") or 0) < 5_000_000_000:
                continue
            if not (0.8 <= turnover <= 22.0):
                continue
            if not (-3.5 <= pct <= 7.8):
                continue
            if volume_ratio and volume_ratio < 0.55:
                continue
            change60 = item.get("change60")
            if change60 is not None and (change60 > 90 or change60 < -35):
                continue
            flow_ratio = item.get("main_inflow_ratio") or 0.0
            pre_score = (
                0.30 * _clamp((pct + 2.0) / 8.0, 0, 1)
                + 0.22 * _clamp((volume_ratio - 0.7) / 2.8, 0, 1)
                + 0.23 * _clamp((flow_ratio + 0.08) / 0.28, 0, 1)
                + 0.15 * _clamp(((change60 or 0) + 20) / 80, 0, 1)
                + 0.10 * (1 - min(1, abs(turnover - 8) / 18))
            )
            candidate = dict(item)
            candidate["prescore"] = pre_score
            pool.append(candidate)
        pool.sort(key=lambda item: item["prescore"], reverse=True)

        selected: list[dict] = []
        industry_count: dict[str, int] = {}
        for item in pool:
            industry = item["industry"]
            if industry_count.get(industry, 0) >= 6:
                continue
            selected.append(item)
            industry_count[industry] = industry_count.get(industry, 0) + 1
            if len(selected) >= limit:
                break
        if len(selected) < 20:
            selected = pool[: min(limit, len(pool))]
        return selected

    def _build_analysis(self, force: bool = False) -> dict:
        with self._analysis_lock:
            now = time.time()
            if not force and self._analysis and now - self._analysis_at < ANALYSIS_TTL:
                return self._analysis
            snap = self.get_snapshot(force)
            candidates = self._preselect(snap, 64)
            if len(candidates) < 8:
                raise RuntimeError("当前可分析候选不足，可能处于休市或数据异常")
            bars_map = self._load_klines(candidates)
            if len(bars_map) < 6:
                raise RuntimeError("历史K线获取不足，无法建立模型")
            benchmark_bars = self._load_kline(BENCHMARK_SECID, 320)
            today = now_cn().strftime("%Y-%m-%d")
            phase = market_phase()
            try:
                learning_summary = self.learning.refresh(
                    self._load_kline, today, phase["is_open"]
                )
            except Exception:  # noqa: BLE001 - learning must not break picks
                learning_summary = self.learning.summary(today)
            completed = {
                code: [bar for bar in bars if bar["date"] != today]
                for code, bars in bars_map.items()
            }
            completed = {code: bars for code, bars in completed.items() if len(bars) >= 80}
            model_artifact = None
            model_error = None
            samples = build_samples(completed, benchmark_bars, horizon=10)
            try:
                model_artifact = train_model(samples, horizon=10)
            except Exception as exc:  # noqa: BLE001 - rule score still works
                model_error = str(exc)

            rows: list[dict] = []
            quote_by_code = {item["code"]: item for item in candidates}
            base_rate = (
                float(model_artifact["metrics"].get("base_rate", 0.5))
                if model_artifact
                else 0.5
            )
            calibration_b = float(
                (learning_summary.get("calibration") or {}).get("b") or 0.0
            )
            for code, bars in bars_map.items():
                quote = quote_by_code.get(code)
                if not quote:
                    continue
                features = current_features(bars, quote, benchmark_bars, today)
                if not features:
                    continue
                score = score_candidate(features, quote)
                model_prediction = (
                    predict(model_artifact, features["vector"])
                    if model_artifact
                    else {"probability": 0.5, "raw_probability": 0.5, "edge_vs_base": 0.0, "logit": 0.0}
                )
                raw_probability = model_prediction["probability"]
                probability, adjustment_pp, calibration_b = self.learning.calibrate(
                    raw_probability
                )
                confidence = score["confidence"]
                if model_artifact and model_artifact["confidence"] == "low":
                    confidence = "low" if confidence == "low" else "medium"
                if model_artifact and probability - base_rate < 0.005:
                    confidence = "low"
                spark = [round(bar["close"], 2) for bar in bars[-30:]]
                rows.append(
                    {
                        "code": code,
                        "secid": quote["secid"],
                        "name": quote["name"],
                        "industry": quote["industry"],
                        "price": quote["price"],
                        "pct": quote["pct"],
                        "amount": quote["amount"],
                        "amount_yi": round(quote["amount"] / 1e8, 2),
                        "market_cap_yi": round((quote.get("market_cap") or 0) / 1e8, 1),
                        "turnover": quote.get("turnover"),
                        "volume_ratio": quote.get("volume_ratio"),
                        "pe": quote.get("pe"),
                        "pb": quote.get("pb"),
                        "change60": quote.get("change60"),
                        "score": 0.0,
                        "probability": probability,
                        "probability_raw": raw_probability,
                        "probability_adjustment_pp": adjustment_pp,
                        "probability_pct": round(probability * 100, 1),
                        "base_rate": base_rate,
                        "relative_edge": round(probability - base_rate, 4),
                        "model_edge": model_prediction.get("edge_vs_base", 0.0),
                        "model_percentile": 0.0,
                        "model_rank": 0,
                        "rule_score": score["rule_score"],
                        "risk_score": score["risk_score"],
                        "trend_score": score["trend_score"],
                        "confidence": confidence,
                        "subscores": score["subscores"],
                        "reasons": score["reasons"],
                        "risks": score["risks"],
                        "flags": risk_flags(features, quote),
                        "spark": spark,
                        "atr_pct": features["values"]["atr_pct"],
                        "trend": features["values"]["trend"],
                        "rel5": features["values"]["rel5"],
                        "rsi": features["values"]["rsi"],
                        "ma20": features["ma20"],
                        "ma60": features["ma60"],
                        "dist_high20": features["values"]["dist_high20"],
                    }
                )

            # Rank the model cross-section rather than trusting an absolute
            # probability in a weak-signal regime. This keeps the candidate list
            # honest when the base rate itself is below 50%.
            model_ordered = sorted(rows, key=lambda item: item["probability"])
            row_count = max(1, len(model_ordered))
            for index, row in enumerate(model_ordered):
                row["model_percentile"] = round((index + 1) / row_count, 4)
                row["model_rank"] = row_count - index
            model_usable = bool(model_artifact and model_artifact["metrics"].get("usable"))
            for row in rows:
                percentile_score = row["model_percentile"] * 100.0
                if model_usable:
                    final_score = (
                        0.50 * percentile_score
                        + 0.35 * row["rule_score"]
                        + 0.15 * row["risk_score"]
                    )
                else:
                    final_score = (
                        0.15 * percentile_score
                        + 0.68 * row["rule_score"]
                        + 0.17 * row["risk_score"]
                    )
                row["score"] = round(final_score, 1)
            rows.sort(key=lambda item: item["score"], reverse=True)
            self._analysis = {
                "rows": rows,
                "model": model_artifact,
                "model_error": model_error,
                "candidates": len(candidates),
                "kline_count": len(bars_map),
                "sample_count": len(samples),
                "snapshot_at": self._snapshot_at,
                "built_at": time.time(),
                "today": today,
                "universe": len(snap),
                "benchmark_bars": benchmark_bars,
                "learning": learning_summary,
                "calibration_b": calibration_b,
            }
            self._analysis_at = time.time()
            self._pick_cache.clear()
            return self._analysis

    def get_picks(self, profile_key: str = DEFAULT_PROFILE, force: bool = False) -> dict:
        profile_key = profile_key if profile_key in PROFILES else DEFAULT_PROFILE
        profile = PROFILES[profile_key]
        now = time.time()
        cached = self._pick_cache.get(profile_key)
        if not force and cached and now - cached[0] < PICK_TTL:
            return cached[1]
        analysis = self._build_analysis(force)
        model = analysis["model"]
        base_rate = float(model["metrics"].get("base_rate", 0.5)) if model else 0.5
        model_usable = bool(model and model["metrics"].get("usable"))
        rows = []
        excluded = 0
        for row in analysis["rows"]:
            flags = row["flags"]
            if "ST" in flags or "涨停" in flags:
                excluded += 1
                continue
            if model_usable and row["probability"] < base_rate:
                excluded += 1
                continue
            if model_usable and row["model_percentile"] < 0.55:
                excluded += 1
                continue
            if row["rule_score"] < 42 or row["risk_score"] < 35:
                excluded += 1
                continue
            if row["atr_pct"] > profile["max_atr_pct"]:
                excluded += 1
                continue
            if (row.get("market_cap_yi") or 0) * 1e8 < profile["min_market_cap"]:
                excluded += 1
                continue
            if row["trend_score"] < profile["min_trend_score"]:
                excluded += 1
                continue
            if row["pct"] > profile["max_pct_change"]:
                excluded += 1
                continue
            rows.append(row)

        rows.sort(key=lambda item: item["score"], reverse=True)
        selected: list[dict] = []
        industry_count: dict[str, int] = {}
        for row in rows:
            industry = row["industry"]
            if industry_count.get(industry, 0) >= profile["max_per_industry"]:
                continue
            selected.append(row)
            industry_count[industry] = industry_count.get(industry, 0) + 1
            if len(selected) >= profile["max_picks"]:
                break

        no_pick_reason = None
        if not selected:
            no_pick_reason = (
                "当前没有同时满足模型相对优势、因子质量与风险约束的标的。"
                "空仓等待也是策略的一部分，不建议为了凑数而降低标准。"
            )
        elif len(selected) == 1:
            no_pick_reason = (
                "今日仅 1 只通过全部筛选，样本不足以构成分散组合。"
                "建议降低单笔仓位，或继续等待更清晰的信号，不为了凑数而放宽标准。"
            )

        picks = []
        for index, row in enumerate(selected, start=1):
            plan = build_plan(
                {
                    "price": row["price"],
                    "atr": row["atr_pct"] * row["price"],
                    "values": {"dist_high20": row["dist_high20"]},
                    "vol_ratio": row["volume_ratio"] or 1.0,
                },
                {"pct": row["pct"]},
                profile,
            )
            item = dict(row)
            item.pop("plan_seed", None)
            item["rank"] = index
            item["plan"] = plan
            item["profile"] = profile_key
            item["execution"] = build_execution_guide(
                plan, item, item.get("confidence", "medium"), item.get("relative_edge")
            )
            picks.append(item)

        model = analysis["model"]
        metrics = model["metrics"] if model else None
        learning_summary = analysis.get("learning") or self.learning.summary(analysis["today"])
        if picks:
            self.learning.record_snapshot(analysis["today"], profile_key, picks)
            if profile_key == DEFAULT_PROFILE:
                self.paper.record_day(analysis["today"], profile_key, picks)
            learning_summary = self.learning.summary(analysis["today"])
        now = now_cn()
        after_close = now.hour * 60 + now.minute >= 15 * 60 + 5
        if after_close:
            paper_summary = self.paper.refresh(
                lambda secid: self.provider.fetch_kline(secid, 20),
                analysis["today"],
                True,
            )
        else:
            paper_summary = self.paper.summary()
        response = {
            "asOf": datetime.now(CN_TZ).isoformat(timespec="seconds"),
            "profile": profile,
            "profiles": list(PROFILES.values()),
            "picks": picks,
            "candidateCount": analysis["candidates"],
            "klineCount": analysis["kline_count"],
            "universeCount": analysis.get("universe", 0),
            "qualifiedCount": len(rows),
            "excludedCount": excluded,
            "noPickReason": no_pick_reason,
            "baseRate": round(base_rate, 4),
            "learning": learning_summary,
            "paper": paper_summary,
            "model": {
                "available": model is not None,
                "error": analysis["model_error"],
                "confidence": model["confidence"] if model else "low",
                "method": model["method"] if model else "规则评分（模型样本不足）",
                "horizonDays": 10,
                "probabilityLabel": "10日上涨概率",
                "metrics": metrics,
                "featureImportance": model["feature_importance"] if model else [],
                "limitations": model["limitations"] if model else "模型不可用，仅展示技术因子评分。",
                "trainedAt": model["trained_at"] if model else None,
            },
            "sampleCount": analysis["sample_count"],
            "dataDate": analysis["today"],
            "nextRefreshSec": PICK_TTL,
            "disclaimer": DISCLAIMER,
        }
        self._pick_cache[profile_key] = (time.time(), response)
        return response

    # --------------------------------------------------------------- learning
    def get_learning(self) -> dict:
        today = now_cn().strftime("%Y-%m-%d")
        phase = market_phase()
        try:
            summary = self.learning.refresh(self._load_kline, today, phase["is_open"])
        except Exception:  # noqa: BLE001 - still return what has been resolved
            summary = self.learning.summary(today)
        summary["generatedAt"] = datetime.now(CN_TZ).isoformat(timespec="seconds")
        summary["market"] = phase
        now = now_cn()
        after_close = now.hour * 60 + now.minute >= 15 * 60 + 5
        if after_close:
            try:
                summary["paper"] = self.paper.refresh(
                    lambda secid: self.provider.fetch_kline(secid, 20),
                    today,
                    True,
                )
            except Exception:  # noqa: BLE001 - paper tracking must not break learning
                summary["paper"] = self.paper.summary()
        else:
            summary["paper"] = self.paper.summary()
        return summary

    # --------------------------------------------------------------- detail
    def get_stock(self, code: str, profile_key: str = DEFAULT_PROFILE) -> dict:
        code = normalize_code(code)
        if not code or not code.isdigit():
            raise ValueError("股票代码格式不正确")
        profile_key = profile_key if profile_key in PROFILES else DEFAULT_PROFILE
        quote = None
        try:
            snap = self.get_snapshot()
            quote = next((item for item in snap if item["code"] == code), None)
        except Exception:  # noqa: BLE001 - individual quote fallback below
            quote = None
        if quote is None:
            quotes = self.provider.fetch_quotes([secid_for(code)])
            quote = quotes[0] if quotes else None
        if quote is None:
            raise ValueError(f"未找到股票 {code} 的行情")
        bars = self._load_kline(quote["secid"], 220)
        if len(bars) < 60:
            raise ValueError("历史K线不足，无法计算指标")
        benchmark = self._load_kline(BENCHMARK_SECID, 320)
        today = now_cn().strftime("%Y-%m-%d")
        features = current_features(bars, quote, benchmark, today)
        if not features:
            raise ValueError("指标计算失败，可能是停牌或数据不足")
        score = score_candidate(features, quote)
        plan = build_plan(features, quote, PROFILES[profile_key])
        probability = None
        composite_score = None
        model_percentile = None
        relative_edge = None
        base_rate = None
        if self._analysis:
            match = next((row for row in self._analysis["rows"] if row["code"] == code), None)
            if match:
                probability = match["probability"]
                composite_score = match["score"]
                model_percentile = match["model_percentile"]
                relative_edge = match["relative_edge"]
                base_rate = match["base_rate"]
        chart = chart_payload(bars, 120)
        execution = build_execution_guide(
            plan, quote, score.get("confidence", "medium"), relative_edge
        )
        return {
            "asOf": datetime.now(CN_TZ).isoformat(timespec="seconds"),
            "quote": quote,
            "features": {
                "values": {key: round(value, 5) for key, value in features["values"].items()},
                "ma5": _round(features["ma5"]),
                "ma10": _round(features["ma10"]),
                "ma20": _round(features["ma20"]),
                "ma60": _round(features["ma60"]),
                "atr": _round(features["atr"]),
                "high20": _round(features["high20"]),
                "low20": _round(features["low20"]),
                "rsi": _round(features["rsi"]),
            },
            "score": score,
            "flags": risk_flags(features, quote),
            "plan": plan,
            "execution": execution,
            "probability": probability,
            "composite_score": composite_score,
            "model_percentile": model_percentile,
            "relative_edge": relative_edge,
            "base_rate": base_rate,
            "chart": chart,
            "spark": [round(bar["close"], 2) for bar in bars[-30:]],
            "profile": PROFILES[profile_key],
            "disclaimer": DISCLAIMER,
        }

    # ------------------------------------------------------------- watchlist
    @staticmethod
    def _alerts(item: dict, quote: dict) -> list[dict]:
        alerts: list[dict] = []
        price = quote.get("price")
        pct = quote.get("pct") or 0.0
        if price is None:
            return [{"level": "muted", "text": "暂无有效报价"}]
        stop = item.get("stop")
        target = item.get("target1")
        entry_low = item.get("entry_low")
        entry_high = item.get("entry_high")
        if stop and price <= float(stop):
            alerts.append({"level": "danger", "text": f"触及止损 {stop}"})
        elif target and price >= float(target):
            alerts.append({"level": "success", "text": f"到达目标 {target}"})
        if entry_low and entry_high and float(entry_low) <= price <= float(entry_high):
            alerts.append({"level": "info", "text": "进入计划买区"})
        if pct >= (quote.get("limit_pct") or 10) - 0.8:
            alerts.append({"level": "warn", "text": "接近涨停"})
        if pct <= -5:
            alerts.append({"level": "danger", "text": "当日快速走弱"})
        if (quote.get("volume_ratio") or 0) >= 2.5 and pct > 2:
            alerts.append({"level": "info", "text": "放量走强"})
        if (quote.get("volume_ratio") or 0) >= 1.8 and pct < -3:
            alerts.append({"level": "warn", "text": "放量回落"})
        return alerts[:3]

    @staticmethod
    def _next_action(item: dict, quote: dict) -> dict:
        """A single, plain-language next step for the watchlist row."""
        price = quote.get("price")
        pct = float(quote.get("pct") or 0.0)
        if not price:
            return {
                "label": "等待报价",
                "tone": "muted",
                "detail": "暂无有效行情，先检查是否停牌。",
            }
        entry_low = float(item["entry_low"]) if item.get("entry_low") else None
        entry_high = float(item["entry_high"]) if item.get("entry_high") else None
        stop = float(item["stop"]) if item.get("stop") else None
        target = float(item["target1"]) if item.get("target1") else None
        if not (entry_low and entry_high and stop):
            return {
                "label": "仅跟踪",
                "tone": "muted",
                "detail": "没有记录关注区和止损；建议先补计划，再谈执行。",
            }
        if price <= stop:
            return {
                "label": "执行止损",
                "tone": "danger",
                "detail": f"已跌破 {stop:.2f}，不补仓、不摊薄，先退出观察。",
            }
        if target and price >= target:
            return {
                "label": "分批止盈",
                "tone": "success",
                "detail": f"已到目标 {target:.2f}，先减一部分，剩余用移动止损保护。",
            }
        if pct <= -5:
            return {
                "label": "暂停买入",
                "tone": "danger",
                "detail": "当日快速走弱，先不接；等止跌并收回买区再评估。",
            }
        if entry_low <= price <= entry_high:
            return {
                "label": "可分批",
                "tone": "action",
                "detail": f"已进买区，首笔减半、分 2 笔；跌破 {stop:.2f} 停止计划。",
            }
        if price > entry_high:
            if pct >= 5 or (quote.get("volume_ratio") or 0) >= 3.5:
                return {
                    "label": "不追高",
                    "tone": "warn",
                    "detail": f"涨幅/量能已偏热，等回到 {entry_low:.2f}–{entry_high:.2f}。",
                }
            return {
                "label": "等回踩",
                "tone": "wait",
                "detail": f"未到买区，等价格回到 {entry_low:.2f}–{entry_high:.2f}。",
            }
        if price < entry_low:
            return {
                "label": "观察收回",
                "tone": "watch",
                "detail": f"低于买区但未破止损，等重新站回 {entry_low:.2f} 再评估。",
            }
        return {
            "label": "持有观察",
            "tone": "hold",
            "detail": f"未触发新信号；按原止损 {stop:.2f} 管理。",
        }

    def get_watchlist(self) -> dict:
        records = self.watchlist.list()
        if not records:
            return {
                "items": [],
                "count": 0,
                "asOf": datetime.now(CN_TZ).isoformat(timespec="seconds"),
                "disclaimer": DISCLAIMER,
            }
        quotes = self.provider.fetch_quotes([secid_for(item["code"]) for item in records])
        items = self._merge_watch_items(records, quotes)
        return {
            "items": items,
            "count": len(items),
            "asOf": datetime.now(CN_TZ).isoformat(timespec="seconds"),
            "disclaimer": DISCLAIMER,
        }

    def _merge_watch_items(self, records: list[dict], quotes: list[dict]) -> list[dict]:
        by_code = {quote["code"]: quote for quote in quotes}
        items = []
        for record in records:
            quote = by_code.get(record["code"])
            if not quote:
                quote = {
                    "code": record["code"],
                    "name": record.get("name") or record["code"],
                    "price": None,
                    "pct": 0.0,
                    "amount": 0.0,
                    "amount_yi": 0.0,
                    "volume_ratio": None,
                    "turnover": None,
                    "industry": "—",
                    "limit_pct": 10,
                }
            merged = {
                **record,
                **quote,
                "plan": {
                    "entry_low": record.get("entry_low"),
                    "entry_high": record.get("entry_high"),
                    "stop": record.get("stop"),
                    "target1": record.get("target1"),
                },
            }
            merged["alerts"] = self._alerts(record, quote)
            merged["nextAction"] = self._next_action(record, quote)
            if quote.get("price") and record.get("stop"):
                merged["distance_stop_pct"] = round(
                    (float(record["stop"]) / quote["price"] - 1) * 100, 2
                )
            if quote.get("source_ts"):
                merged["source_age_sec"] = round(
                    max(0.0, time.time() - float(quote["source_ts"])), 1
                )
            items.append(merged)
        return items

    def get_live(self) -> dict:
        """Low-latency snapshot for the watchlist and indices.

        This path is intentionally small: one batch quote request for the
        watchlist and one index request. It is designed to be polled every
        second while the browser is visible. It still cannot remove the
        upstream vendor's own quote delay; the response exposes that age.
        """
        started = time.perf_counter()
        records = self.watchlist.list()
        quotes: list[dict] = []
        indices: list[dict] = []
        quote_error = None
        if records:
            try:
                quotes = self.provider.fetch_quotes(
                    [secid_for(item["code"]) for item in records]
                )
            except Exception as exc:  # noqa: BLE001 - fall back to indices
                quote_error = str(exc)
        try:
            indices = self.provider.fetch_indices()
        except Exception as exc:  # noqa: BLE001 - report if both failed
            if not quotes:
                raise RuntimeError(f"实时行情暂时不可用：{exc}") from exc
            indices = []
        items = self._merge_watch_items(records, quotes)
        for index in indices:
            if index.get("source_ts"):
                index["source_age_sec"] = round(
                    max(0.0, time.time() - float(index["source_ts"])), 1
                )
        source_times = [
            float(item["source_ts"])
            for item in [*items, *indices]
            if item.get("source_ts")
        ]
        max_source_ts = max(source_times) if source_times else None
        server_ms = round((time.perf_counter() - started) * 1000, 1)
        phase = market_phase()
        return {
            "asOf": datetime.now(CN_TZ).isoformat(timespec="seconds"),
            "serverMs": server_ms,
            "pollIntervalMs": 1000,
            "sourceTime": (
                datetime.fromtimestamp(max_source_ts, CN_TZ).isoformat(timespec="seconds")
                if max_source_ts
                else None
            ),
            "sourceAgeSec": (
                round(max(0.0, time.time() - max_source_ts), 1)
                if max_source_ts
                else None
            ),
            "market": phase,
            "watch": {
                "items": items,
                "count": len(items),
                "error": quote_error,
            },
            "indices": indices,
            "disclaimer": DISCLAIMER,
        }

    def add_watch(self, payload: dict) -> dict:
        code = normalize_code(payload.get("code", ""))
        if not code or not code.isdigit():
            raise ValueError("请输入 6 位股票代码")
        quotes = self.provider.fetch_quotes([secid_for(code)])
        if not quotes:
            raise ValueError(f"未找到股票 {code}，请检查代码")
        quote = quotes[0]
        profile_key = payload.get("profile") or DEFAULT_PROFILE
        profile = PROFILES.get(profile_key, PROFILES[DEFAULT_PROFILE])
        entry_low = payload.get("entry_low")
        entry_high = payload.get("entry_high")
        stop = payload.get("stop")
        target1 = payload.get("target1")
        plan_source = "provided" if all(value is not None for value in (entry_low, entry_high, stop, target1)) else None
        if plan_source is None:
            # Adding from search should still produce actionable levels. Without
            # this, the watchlist would show a useless "仅跟踪" row with no plan.
            try:
                bars = self._load_kline(quote["secid"], 220)
                benchmark = self._load_kline(BENCHMARK_SECID, 320)
                features = current_features(
                    bars, quote, benchmark, now_cn().strftime("%Y-%m-%d")
                )
                if features:
                    auto_plan = build_plan(features, quote, profile)
                    entry_low = auto_plan["entry_low"]
                    entry_high = auto_plan["entry_high"]
                    stop = auto_plan["stop"]
                    target1 = auto_plan["target1"]
                    plan_source = "auto"
            except Exception:  # noqa: BLE001 - the stock is still watchable
                plan_source = None
        record = {
            "code": quote["code"],
            "name": quote["name"],
            "entry_low": entry_low,
            "entry_high": entry_high,
            "stop": stop,
            "target1": target1,
            "profile": profile_key,
            "plan_source": plan_source,
            "note": payload.get("note"),
        }
        self.watchlist.add(record)
        return self.get_watchlist()

    def remove_watch(self, code: str) -> dict:
        self.watchlist.remove(normalize_code(code))
        return self.get_watchlist()

    # --------------------------------------------------------------- search
    def search(self, query: str, limit: int = 8) -> list[dict]:
        text = str(query or "").strip().lower()
        if not text:
            return []
        snap = self.get_snapshot()
        matches: list[tuple[int, dict]] = []
        for item in snap:
            code_hit = text in item["code"]
            name_hit = text in item["name"].lower()
            if not code_hit and not name_hit:
                continue
            priority = 0
            if item["code"].startswith(text):
                priority = 0
            elif item["name"].lower().startswith(text):
                priority = 1
            else:
                priority = 2
            matches.append((priority, item))
        matches.sort(key=lambda pair: (pair[0], pair[1]["code"]))
        result = []
        for _, item in matches[:limit]:
            result.append(
                {
                    "code": item["code"],
                    "name": item["name"],
                    "price": item["price"],
                    "pct": item["pct"],
                    "industry": item["industry"],
                }
            )
        return result

    # --------------------------------------------------------------- method
    @staticmethod
    def method_card() -> dict:
        return {
            "app": "A股雷达",
            "version": "0.1.0",
            "pipeline": [
                {"step": "01", "title": "数据层", "text": "东方财富公开行情：全市场快照、指数、板块、日K。内存与磁盘双层缓存，失败时降级到陈旧数据并明确标记。"},
                {"step": "02", "title": "因子层", "text": "趋势、动量、量能、RSI、MACD、相对强弱、波动风险、资金/估值八组可解释因子。"},
                {"step": "03", "title": "模型层", "text": "L2 正则逻辑回归，按时间切分训练/验证，等频保序校准；输出未来10个交易日上涨概率与验证 AUC。"},
                {"step": "04", "title": "组合层", "text": "按稳健/均衡/进攻三档过滤波动、市值、涨幅，限制单行业数量，生成观察名单。"},
                {"step": "05", "title": "风控层", "text": "ATR 止损、目标位、单笔风险预算、仓位上限、T+1 提示；不接券商、不自动下单。"},
            ],
            "factors": [
                {"key": "trend", "label": "趋势结构", "weight": 22, "desc": "价格与 MA5/MA20/MA60、MA20 斜率"},
                {"key": "momentum", "label": "动量强度", "weight": 18, "desc": "5/20 日涨幅，过热时反向扣分"},
                {"key": "volume", "label": "量能配合", "weight": 12, "desc": "量比、5/20 日均量、OBV"},
                {"key": "rsi", "label": "超买超卖", "weight": 8, "desc": "RSI(14)，偏好 50–68"},
                {"key": "macd", "label": "MACD 动能", "weight": 10, "desc": "柱体零轴与方向"},
                {"key": "relative", "label": "相对强弱", "weight": 10, "desc": "相对沪深300的 5 日超额"},
                {"key": "risk", "label": "波动风险", "weight": 12, "desc": "ATR、乖离率、距20日高点"},
                {"key": "flow", "label": "资金/估值", "weight": 8, "desc": "主力净流入占比、PE"},
            ],
            "principles": [
                "先定义风险，再谈收益：每只候选都给出失效条件与止损位。",
                "概率不是结论：模型输出必须与因子解释、市场环境一起阅读。",
                "滚动验证：只用历史可见数据训练，用后段时间切片验证，避免用未来函数。",
                "模型会衰减：页面展示验证 AUC、样本量和置信度；低置信度不包装成高胜率。",
                "不接自动交易：工具负责整理信息与纪律，下单决策仍由人完成。",
            ],
            "limitations": [
                "公开行情有数秒延迟，非 Level-2，无法还原盘口与真实成交队列。",
                "训练池来自当日流动性候选，存在选择/幸存者偏差，验证结果偏乐观。",
                "未覆盖公告、财报、政策、突发事件与社区情绪。",
                "A 股 T+1、涨跌停、停牌、滑点和手续费会显著改变实际结果。",
                "任何模型都无法保证未来收益；历史 AUC 不代表实盘胜率。",
            ],
            "sources": [
                {"name": "东方财富公开行情接口", "url": "https://quote.eastmoney.com/", "note": "行情数据源，非官方文档，接口可能变更"},
                {"name": "幻方量化", "url": "https://www.high-flyer.cn/", "note": "方法论参考：AI 量化、因子工业化、算力与风险约束"},
                {"name": "AkShare", "url": "https://github.com/akfamily/akshare", "note": "开源财经数据接口的工程化参考"},
                {"name": "Qlib", "url": "https://github.com/microsoft/qlib", "note": "因子研究、模型与滚动验证流程参考"},
                {"name": "TradingView", "url": "https://www.tradingview.com/", "note": "图表与筛选器交互参考"},
                {"name": "Finviz", "url": "https://finviz.com/", "note": "市场热力图与筛选器信息层级参考"},
            ],
            "disclaimer": DISCLAIMER,
        }
