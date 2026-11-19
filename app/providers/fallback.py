"""Second-source fallback: Sina full-market snapshot and Tencent K-line/quotes.

Eastmoney remains the primary provider because it carries industry, fund-flow
and extra valuation fields. These providers only take over per request when the
primary fails. A fallback snapshot is used as one coherent source; rows from
two snapshot providers are never silently blended.
"""

from __future__ import annotations

import json
import math
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from typing import Any

import requests
from requests.adapters import HTTPAdapter

from app.config import CN_TZ, EASTMONEY_HEADERS, INDEXES
from app.providers.eastmoney import (
    EastmoneyProvider,
    _limit_pct,
    _num,
    normalize_code,
    secid_for,
)

SINA_LIST = "https://vip.stock.finance.sina.com.cn/quotes_service/api/json_v2.php/Market_Center.getHQNodeData"
SINA_COUNT = "https://vip.stock.finance.sina.com.cn/quotes_service/api/json_v2.php/Market_Center.getHQNodeStockCount"
TENCENT_KLINE = "https://web.ifzq.gtimg.cn/appstock/app/fqkline/get"
TENCENT_QUOTE = "https://qt.gtimg.cn/q="


def _session(referer: str = "https://finance.sina.com.cn/") -> requests.Session:
    session = requests.Session()
    session.headers.update(
        {
            "User-Agent": EASTMONEY_HEADERS["User-Agent"],
            "Referer": referer,
            "Accept": "application/json,text/plain,*/*",
        }
    )
    adapter = HTTPAdapter(pool_connections=8, pool_maxsize=8, max_retries=0)
    session.mount("https://", adapter)
    return session


def _stamp() -> tuple[int, str]:
    now = datetime.now(CN_TZ)
    return int(now.timestamp()), now.strftime("%Y-%m-%d")


def _tencent_symbol(secid: str) -> str:
    market, _, raw = str(secid).partition(".")
    code = normalize_code(raw or secid)
    if market == "1":
        return f"sh{code}"
    if code.startswith(("8", "4", "92")):
        return f"bj{code}"
    return f"sz{code}"


def _secid_for_symbol(symbol: str, code: str) -> str:
    if symbol.startswith("sh"):
        return f"1.{code}"
    if symbol.startswith("bj"):
        return f"0.{code}"
    return f"0.{code}"


class SinaSnapshotProvider:
    source_name = "新浪财经（降级）"

    def __init__(self, timeout: float = 10.0, retries: int = 6, workers: int = 4):
        self.timeout = timeout
        self.retries = retries
        self.workers = workers
        self.session = _session()
        self.last_snapshot_meta: dict[str, Any] = {}
        self.last_limit_up_total = 0

    def _get(self, url: str, params: dict | None = None) -> Any:
        last: Exception | None = None
        for attempt in range(self.retries):
            try:
                response = self.session.get(url, params=params, timeout=self.timeout)
                response.raise_for_status()
                return response.json()
            except Exception as exc:  # noqa: BLE001
                last = exc
                if attempt < self.retries - 1:
                    time.sleep(min(4.0, 0.6 * (attempt + 1)))
        raise RuntimeError(f"sina request failed: {last}") from last

    def _normalize(self, row: dict) -> dict | None:
        code = normalize_code(row.get("symbol") or row.get("code"))
        name = str(row.get("name") or "").strip()
        price = _num(row.get("trade"))
        if not code or not name or price is None or price <= 0:
            return None
        pct = _num(row.get("changepercent"))
        prev = _num(row.get("settlement"))
        ts, date = _stamp()
        tick = str(row.get("ticktime") or "")
        try:
            hour, minute, second = (int(part) for part in tick.split(":"))
            stamp = datetime.now(CN_TZ).replace(hour=hour, minute=minute, second=second)
            ts = int(stamp.timestamp())
        except (ValueError, TypeError):
            pass
        limit = _limit_pct(code, name)
        market_cap = (_num(row.get("mktcap")) or 0.0) * 10_000.0
        float_cap = (_num(row.get("nmc")) or 0.0) * 10_000.0
        symbol = str(row.get("symbol") or "")
        return {
            "code": code,
            "secid": _secid_for_symbol(symbol, code),
            "symbol": symbol,
            "name": name,
            "price": price,
            "pct": pct if pct is not None else 0.0,
            "change": _num(row.get("pricechange")) or 0.0,
            "volume": _num(row.get("volume")) or 0.0,
            "amount": _num(row.get("amount")) or 0.0,
            "amplitude": None,
            "turnover": _num(row.get("turnoverratio")),
            "pe": _num(row.get("per")),
            "pe_ttm": None,
            "volume_ratio": None,
            "high": _num(row.get("high")),
            "low": _num(row.get("low")),
            "open": _num(row.get("open")),
            "prev_close": prev,
            "market_cap": market_cap,
            "float_cap": float_cap,
            "speed": None,
            "pb": _num(row.get("pb")),
            "change60": None,
            "change_ytd": None,
            "main_inflow": 0.0,
            "main_inflow_ratio": None,
            "industry": "未分类",
            "source_ts": ts,
            "source_date": date,
            "limit_pct": limit,
            "is_st": "ST" in name.upper() or "退" in name,
            "is_limit_up": pct is not None and pct >= limit - 0.25,
            "is_limit_down": pct is not None and pct <= -(limit - 0.25),
            "is_suspended": False,
            "source": "sina",
            "degraded": True,
        }

    def fetch_snapshot(self, page_size: int = 100, workers: int | None = None) -> list[dict]:
        count = int(self._get(SINA_COUNT, {"node": "hs_a"}))
        page_size = max(20, min(page_size, 100))
        pages = max(1, math.ceil(count / page_size))

        def fetch_page(page: int) -> list[dict]:
            rows = self._get(
                SINA_LIST,
                {
                    "page": page,
                    "num": page_size,
                    "sort": "symbol",
                    "asc": 1,
                    "node": "hs_a",
                    "symbol": "",
                    "_s_r_a": "page",
                },
            )
            return rows if isinstance(rows, list) else []

        collected: list[dict] = []
        with ThreadPoolExecutor(max_workers=workers or self.workers) as pool:
            futures = [pool.submit(fetch_page, page) for page in range(1, pages + 1)]
            for future in as_completed(futures):
                try:
                    collected.extend(future.result())
                except Exception:  # noqa: BLE001 - coverage check below
                    continue
        unique = {str(row.get("code")): row for row in collected if row.get("code")}
        normalized = [item for row in unique.values() if (item := self._normalize(row))]
        normalized.sort(key=lambda item: item["code"])
        self.last_snapshot_meta = {
            "total": count,
            "returned": len(unique),
            "normalized": len(normalized),
            "pages": pages,
            "coverage": round(len(unique) / count, 4) if count else 0.0,
            "source": "sina",
            "degraded": True,
        }
        if len(unique) < max(120, count * 0.75):
            raise RuntimeError(
                f"sina snapshot coverage too low: {len(unique)}/{count}"
            )
        return normalized

    def fetch_limit_up_pool(self, date: str, page_size: int = 200) -> list[dict]:
        return []


class TencentProvider:
    source_name = "腾讯财经（降级）"

    def __init__(self, timeout: float = 10.0, retries: int = 3):
        self.timeout = timeout
        self.retries = retries
        self.session = _session("https://gu.qq.com/")
        self.last_limit_up_total = 0

    def _get(self, url: str, params: dict | None = None) -> Any:
        last: Exception | None = None
        for attempt in range(self.retries):
            try:
                response = self.session.get(url, params=params, timeout=self.timeout)
                response.raise_for_status()
                return response.json()
            except Exception as exc:  # noqa: BLE001
                last = exc
                if attempt < self.retries - 1:
                    time.sleep(0.5 * (attempt + 1))
        raise RuntimeError(f"tencent request failed: {last}") from last

    def _quote_text(self, secids: list[str]) -> str:
        symbols = ",".join(_tencent_symbol(secid) for secid in secids[:50])
        last: Exception | None = None
        for attempt in range(self.retries):
            try:
                response = self.session.get(
                    TENCENT_QUOTE + symbols, timeout=self.timeout
                )
                response.raise_for_status()
                return response.content.decode("gbk", "replace")
            except Exception as exc:  # noqa: BLE001
                last = exc
                if attempt < self.retries - 1:
                    time.sleep(0.5 * (attempt + 1))
        raise RuntimeError(f"tencent quote failed: {last}") from last

    def fetch_quotes(self, secids: list[str]) -> list[dict]:
        text = self._quote_text(secids)
        result = []
        for line in text.splitlines():
            if "=" not in line:
                continue
            payload = line.split("=", 1)[1].strip().strip('";')
            f = payload.split("~")
            if len(f) < 38:
                continue
            code = normalize_code(f[2])
            name = str(f[1] or "").strip()
            price = _num(f[3])
            if not code or not name or price is None or price <= 0:
                continue
            pct = _num(f[32])
            limit = _limit_pct(code, name)
            amount = (_num(f[37]) or 0.0) * 10_000.0
            ts, date = _stamp()
            try:
                raw = f[30]
                stamp = datetime.strptime(raw, "%Y%m%d%H%M%S").replace(tzinfo=CN_TZ)
                ts, date = int(stamp.timestamp()), stamp.strftime("%Y-%m-%d")
            except (ValueError, TypeError):
                pass
            result.append(
                {
                    "code": code,
                    "secid": secid_for(code),
                    "symbol": _tencent_symbol(f"0.{code}"),
                    "name": name,
                    "price": price,
                    "pct": pct if pct is not None else 0.0,
                    "change": _num(f[31]) or 0.0,
                    "volume": _num(f[6]) or 0.0,
                    "amount": amount,
                    "amplitude": None,
                    "turnover": _num(f[38]),
                    "pe": _num(f[39]),
                    "pe_ttm": None,
                    "volume_ratio": None,
                    "high": _num(f[33]),
                    "low": _num(f[34]),
                    "open": _num(f[5]),
                    "prev_close": _num(f[4]),
                    "market_cap": 0.0,
                    "float_cap": 0.0,
                    "speed": None,
                    "pb": None,
                    "change60": None,
                    "change_ytd": None,
                    "main_inflow": 0.0,
                    "main_inflow_ratio": None,
                    "industry": "未分类",
                    "source_ts": ts,
                    "source_date": date,
                    "limit_pct": limit,
                    "is_st": "ST" in name.upper() or "退" in name,
                    "is_limit_up": pct is not None and pct >= limit - 0.25,
                    "is_limit_down": pct is not None and pct <= -(limit - 0.25),
                    "is_suspended": False,
                    "source": "tencent",
                    "degraded": True,
                }
            )
        return result

    def fetch_indices(self) -> list[dict]:
        text = self._quote_text([item["secid"] for item in INDEXES])
        by_code = {item["code"]: item for item in INDEXES}
        result = []
        for line in text.splitlines():
            if "=" not in line:
                continue
            f = line.split("=", 1)[1].strip().strip('";').split("~")
            if len(f) < 35:
                continue
            code = normalize_code(f[2])
            meta = by_code.get(code)
            if not meta:
                continue
            result.append(
                {
                    "code": code,
                    "secid": meta["secid"],
                    "name": str(f[1] or meta["name"]),
                    "price": _num(f[3]),
                    "pct": _num(f[32]) or 0.0,
                    "change": _num(f[31]) or 0.0,
                    "amount": (_num(f[37]) or 0.0) * 10_000.0,
                    "high": _num(f[33]),
                    "low": _num(f[34]),
                    "open": _num(f[5]),
                    "prev_close": _num(f[4]),
                    "spark": [],
                    "source": "tencent",
                }
            )
        return result

    def fetch_kline(self, secid: str, lmt: int = 320) -> list[dict]:
        symbol = _tencent_symbol(secid)
        payload = self._get(
            TENCENT_KLINE,
            {"param": f"{symbol},day,,,{max(20, min(lmt, 1000))},qfq"},
        )
        node = ((payload.get("data") or {}).get(symbol) or {})
        rows = node.get("qfqday") or node.get("day") or []
        bars = []
        for row in rows:
            if not isinstance(row, list) or len(row) < 6:
                continue
            try:
                bars.append(
                    {
                        "date": str(row[0]),
                        "open": float(row[1]),
                        "close": float(row[2]),
                        "high": float(row[3]),
                        "low": float(row[4]),
                        "volume": float(row[5]),
                        "amount": 0.0,
                        "source": "tencent",
                    }
                )
            except (TypeError, ValueError):
                continue
        bars.sort(key=lambda item: item["date"])
        return bars

    def fetch_index_klines(self, secids: list[str], lmt: int = 30) -> dict[str, list[dict]]:
        with ThreadPoolExecutor(max_workers=min(4, len(secids))) as pool:
            futures = {pool.submit(self.fetch_kline, secid, lmt): secid for secid in secids}
            result = {}
            for future in as_completed(futures):
                secid = futures[future]
                try:
                    result[secid] = future.result()
                except Exception:  # noqa: BLE001
                    result[secid] = []
            return result

    def fetch_limit_up_pool(self, date: str, page_size: int = 200) -> list[dict]:
        return []

    def fetch_sectors(self, limit: int = 12, ascending: bool = False) -> list[dict]:
        return []


class FallbackProvider:
    """Eastmoney primary, Sina snapshot, Tencent K-line/quotes."""

    def __init__(self):
        self.primary = EastmoneyProvider()
        self.sina = SinaSnapshotProvider()
        self.tencent = TencentProvider()
        self.last_snapshot_meta: dict[str, Any] = {}
        self.last_limit_up_total = 0
        self.current_source = "东方财富"
        self.last_errors: list[str] = []

    @property
    def source_name(self) -> str:
        return self.current_source

    def _remember(self, exc: Exception) -> None:
        self.last_errors.append(str(exc))
        self.last_errors = self.last_errors[-5:]

    def fetch_snapshot(self, page_size: int = 100, workers: int = 8) -> list[dict]:
        try:
            rows = self.primary.fetch_snapshot(page_size, workers)
            self.current_source = "东方财富"
            self.last_snapshot_meta = dict(self.primary.last_snapshot_meta)
            self.last_snapshot_meta["source"] = "eastmoney"
            self.last_snapshot_meta["source_label"] = "东方财富公开行情"
            return rows
        except Exception as exc:  # noqa: BLE001
            self._remember(exc)
        try:
            rows = self.sina.fetch_snapshot(page_size, workers=4)
            self.current_source = "新浪财经（降级）"
            self.last_snapshot_meta = dict(self.sina.last_snapshot_meta)
            self.last_snapshot_meta["fallback"] = True
            self.last_snapshot_meta["source"] = "sina"
            self.last_snapshot_meta["source_label"] = "新浪财经（降级）"
            return rows
        except Exception as exc:  # noqa: BLE001
            self._remember(exc)
            raise

    def fetch_quotes(self, secids: list[str]) -> list[dict]:
        try:
            return self.primary.fetch_quotes(secids)
        except Exception as exc:  # noqa: BLE001
            self._remember(exc)
            return self.tencent.fetch_quotes(secids)

    def fetch_indices(self) -> list[dict]:
        try:
            return self.primary.fetch_indices()
        except Exception as exc:  # noqa: BLE001
            self._remember(exc)
            return self.tencent.fetch_indices()

    def fetch_kline(self, secid: str, lmt: int = 320) -> list[dict]:
        try:
            return self.primary.fetch_kline(secid, lmt)
        except Exception as exc:  # noqa: BLE001
            self._remember(exc)
            return self.tencent.fetch_kline(secid, lmt)

    def fetch_index_klines(self, secids: list[str], lmt: int = 30) -> dict[str, list[dict]]:
        try:
            return self.primary.fetch_index_klines(secids, lmt)
        except Exception as exc:  # noqa: BLE001
            self._remember(exc)
            return self.tencent.fetch_index_klines(secids, lmt)

    def fetch_limit_up_pool(self, date: str, page_size: int = 200) -> list[dict]:
        try:
            rows = self.primary.fetch_limit_up_pool(date, page_size)
            self.last_limit_up_total = self.primary.last_limit_up_total
            return rows
        except Exception as exc:  # noqa: BLE001
            self._remember(exc)
            self.last_limit_up_total = 0
            return []

    def fetch_sectors(self, limit: int = 12, ascending: bool = False) -> list[dict]:
        try:
            return self.primary.fetch_sectors(limit, ascending)
        except Exception as exc:  # noqa: BLE001
            self._remember(exc)
            return []
