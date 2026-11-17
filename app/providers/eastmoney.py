"""A-share data collection from Eastmoney's public quote endpoints.

These endpoints are unofficial and can change without notice. The provider is
deliberately small, retries transient failures, and keeps the raw field names so
data issues are easy to trace. For personal research only; respect the source's
terms and do not redistribute the data commercially.
"""

from __future__ import annotations

import math
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any

import requests
from requests.adapters import HTTPAdapter

from app.config import EASTMONEY_HEADERS, SNAPSHOT_FIELDS


class EastmoneyError(RuntimeError):
    pass


def _num(value: Any) -> float | None:
    """Eastmoney uses '-' for missing numbers; keep None instead of 0."""
    if value is None or value == "" or value == "-":
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if math.isnan(number) or math.isinf(number):
        return None
    return number


def _int(value: Any, default: int = 0) -> int:
    number = _num(value)
    return int(number) if number is not None else default


def secid_for(code: str) -> str:
    code = normalize_code(code)
    if code.startswith(("6", "5", "9")):
        return f"1.{code}"
    return f"0.{code}"


def normalize_code(code: str) -> str:
    text = str(code or "").strip().lower()
    for prefix in ("sh", "sz", "bj"):
        if text.startswith(prefix):
            text = text[len(prefix):]
    text = text.replace(".", "").strip()
    if text.isdigit():
        return text.zfill(6)
    return text


def _limit_pct(code: str, name: str) -> float:
    upper_name = (name or "").upper()
    if "ST" in upper_name:
        return 5.0
    if code.startswith(("300", "301", "688", "689")):
        return 20.0
    if code.startswith(("8", "4", "92")):
        return 30.0
    return 10.0


def normalize_stock(row: dict) -> dict | None:
    code = normalize_code(row.get("f12", ""))
    name = str(row.get("f14") or "").strip()
    price = _num(row.get("f2"))
    if not code or not name or price is None or price <= 0:
        return None
    pct = _num(row.get("f3"))
    amount = _num(row.get("f6")) or 0.0
    market_cap = _num(row.get("f20")) or 0.0
    pe = _num(row.get("f9"))
    pb = _num(row.get("f23"))
    main_inflow = _num(row.get("f62")) or 0.0
    source_ts = _num(row.get("f124"))
    limit_pct = _limit_pct(code, name)
    return {
        "code": code,
        "secid": secid_for(code),
        "name": name,
        "price": price,
        "pct": pct if pct is not None else 0.0,
        "change": _num(row.get("f4")) or 0.0,
        "volume": _num(row.get("f5")) or 0.0,
        "amount": amount,
        "amplitude": _num(row.get("f7")),
        "turnover": _num(row.get("f8")),
        "pe": pe,
        "pe_ttm": _num(row.get("f115")),
        "volume_ratio": _num(row.get("f10")),
        "high": _num(row.get("f15")),
        "low": _num(row.get("f16")),
        "open": _num(row.get("f17")),
        "prev_close": _num(row.get("f18")),
        "market_cap": market_cap,
        "float_cap": _num(row.get("f21")) or 0.0,
        "speed": _num(row.get("f22")),
        "pb": pb,
        "change60": _num(row.get("f24")),
        "change_ytd": _num(row.get("f25")),
        "main_inflow": main_inflow,
        "main_inflow_ratio": (main_inflow / amount) if amount > 0 else None,
        "industry": str(row.get("f100") or "未分类").strip(),
        "source_ts": int(source_ts) if source_ts else None,
        "source_date": str(row.get("f297") or ""),
        "limit_pct": limit_pct,
        "is_st": "ST" in name.upper() or "退" in name,
        "is_limit_up": (pct is not None and pct >= limit_pct - 0.25),
        "is_limit_down": (pct is not None and pct <= -(limit_pct - 0.25)),
        "is_suspended": price <= 0,
    }


def normalize_index(row: dict) -> dict:
    source_ts = _num(row.get("f124"))
    return {
        "code": normalize_code(row.get("f12", "")),
        "secid": f"{row.get('f13', 1)}.{normalize_code(row.get('f12', ''))}",
        "name": str(row.get("f14") or ""),
        "price": _num(row.get("f2")),
        "pct": _num(row.get("f3")) or 0.0,
        "change": _num(row.get("f4")) or 0.0,
        "amount": _num(row.get("f6")) or 0.0,
        "high": _num(row.get("f15")),
        "low": _num(row.get("f16")),
        "open": _num(row.get("f17")),
        "prev_close": _num(row.get("f18")),
        "source_ts": int(source_ts) if source_ts else None,
        "source_date": str(row.get("f297") or ""),
        "spark": [],
    }


def parse_kline_line(line: str) -> dict | None:
    parts = str(line).split(",")
    if len(parts) < 11:
        return None
    try:
        return {
            "date": parts[0],
            "open": float(parts[1]),
            "close": float(parts[2]),
            "high": float(parts[3]),
            "low": float(parts[4]),
            "volume": float(parts[5]),
            "amount": float(parts[6]),
            "amplitude": float(parts[7]),
            "pct": float(parts[8]),
            "change": float(parts[9]),
            "turnover": float(parts[10]),
        }
    except (TypeError, ValueError):
        return None


class EastmoneyProvider:
    def __init__(self, timeout: float = 12.0, retries: int = 3):
        self.timeout = timeout
        self.retries = retries
        self._local = threading.local()
        self._session = self._build_session()
        self.last_snapshot_meta: dict = {}
        self.last_limit_up_total = 0

    @staticmethod
    def _build_session() -> requests.Session:
        session = requests.Session()
        session.headers.update(EASTMONEY_HEADERS)
        adapter = HTTPAdapter(pool_connections=24, pool_maxsize=24, max_retries=0)
        session.mount("https://", adapter)
        session.mount("http://", adapter)
        return session

    @property
    def session(self) -> requests.Session:
        # requests.Session is not fully thread-safe for mutation, but GETs with
        # a preconfigured session are fine in practice. Give each worker its own
        # connection pool to avoid contention during the 56-page snapshot fetch.
        if not hasattr(self._local, "session"):
            self._local.session = self._build_session()
        return self._local.session

    def _get_json(self, url: str, params: dict, timeout: float | None = None) -> dict:
        last_error: Exception | None = None
        for attempt in range(self.retries):
            try:
                response = self.session.get(
                    url, params=params, timeout=timeout or self.timeout
                )
                response.raise_for_status()
                payload = response.json()
                rc = payload.get("rc")
                if rc not in (0, None):
                    raise EastmoneyError(f"upstream rc={rc}")
                return payload
            except Exception as exc:  # noqa: BLE001 - retry network/JSON failures
                last_error = exc
                if attempt < self.retries - 1:
                    time.sleep(0.35 * (attempt + 1))
        raise EastmoneyError(f"request failed: {url}: {last_error}") from last_error

    def fetch_snapshot(self, page_size: int = 100, workers: int = 8) -> list[dict]:
        base = "https://push2.eastmoney.com/api/qt/clist/get"
        common = {
            "po": 1,
            "np": 1,
            "fltt": 2,
            "invt": 2,
            "fid": "f3",
            "fs": "m:0+t:6,m:0+t:80,m:1+t:2,m:1+t:23",
            "fields": SNAPSHOT_FIELDS,
        }
        first = self._get_json(
            base, {**common, "pn": 1, "pz": page_size}, timeout=20
        )
        data = first.get("data") or {}
        total = _int(data.get("total"))
        rows = list(data.get("diff") or [])
        if total <= 0:
            raise EastmoneyError("snapshot returned no total")
        pages = max(1, math.ceil(total / page_size))

        def fetch_page(page: int) -> list[dict]:
            if page == 1:
                return rows
            payload = self._get_json(
                base, {**common, "pn": page, "pz": page_size}, timeout=20
            )
            page_data = payload.get("data") or {}
            return list(page_data.get("diff") or [])

        collected = list(rows)
        failures = 0
        failed_pages: list[int] = []
        with ThreadPoolExecutor(max_workers=workers) as pool:
            futures = {pool.submit(fetch_page, page): page for page in range(2, pages + 1)}
            for future in as_completed(futures):
                try:
                    collected.extend(future.result())
                except Exception:  # noqa: BLE001 - retry this page below
                    failed_pages.append(futures[future])

        # A second, sequential pass is deliberately gentler: the source may
        # throttle a burst of 8 concurrent requests and losing 5% of the market
        # would silently distort breadth and turnover.
        for page in sorted(failed_pages):
            try:
                collected.extend(fetch_page(page))
            except Exception:  # noqa: BLE001 - coverage is reported below
                failures += 1

        unique = {row.get("f12"): row for row in collected if row.get("f12")}
        if len(unique) < max(120, total * 0.75):
            raise EastmoneyError(
                f"snapshot coverage too low: {len(unique)}/{total}, page failures={failures}"
            )
        normalized = [stock for row in unique.values() if (stock := normalize_stock(row))]
        normalized.sort(key=lambda item: item["code"])
        self.last_snapshot_meta = {
            "total": total,
            "returned": len(unique),
            "normalized": len(normalized),
            "page_failures": failures,
            "coverage": round(len(unique) / total, 4) if total else 0.0,
        }
        return normalized

    def fetch_indices(self) -> list[dict]:
        payload = self._get_json(
            "https://push2.eastmoney.com/api/qt/ulist.np/get",
            {
                "fltt": 2,
                "invt": 2,
                "fields": "f1,f2,f3,f4,f6,f12,f13,f14,f15,f16,f17,f18,f124,f297",
                "secids": ",".join(
                    [
                        "1.000001", "0.399001", "0.399006",
                        "1.000688", "1.000300", "0.399905",
                    ]
                ),
            },
            timeout=15,
        )
        rows = (payload.get("data") or {}).get("diff") or []
        return [normalize_index(row) for row in rows]

    def fetch_index_klines(self, secids: list[str], lmt: int = 30) -> dict[str, list[dict]]:
        with ThreadPoolExecutor(max_workers=min(6, len(secids))) as pool:
            futures = {
                pool.submit(self.fetch_kline, secid, lmt): secid for secid in secids
            }
            result: dict[str, list[dict]] = {}
            for future in as_completed(futures):
                secid = futures[future]
                try:
                    result[secid] = future.result()
                except Exception:  # noqa: BLE001 - spark lines are optional
                    result[secid] = []
            return result

    def fetch_limit_up_pool(self, date: str, page_size: int = 200) -> list[dict]:
        """Limit-up pool with consecutive-board count from Eastmoney.

        ``date`` is YYYYMMDD. The endpoint is a public board-statistics feed and
        may be unavailable outside trading days; callers should treat [] as
        "no pool for that date" rather than an error.
        """
        payload = self._get_json(
            "https://push2ex.eastmoney.com/getTopicZTPool",
            {
                "ut": "7eea3edcaed734bea9cbfc24409ed989",
                "dpt": "wz.ztzt",
                "Pageindex": 0,
                "pagesize": max(10, min(page_size, 300)),
                "sort": "fbt:asc",
                "date": date,
                "_": int(time.time() * 1000),
            },
            timeout=15,
        )
        data = payload.get("data") or {}
        rows = data.get("pool") or []
        result = []
        for row in rows:
            code = normalize_code(row.get("c", ""))
            name = str(row.get("n") or "").strip()
            price = _num(row.get("p"))
            if not code or not name:
                continue
            first_time = str(row.get("fbt") or "").zfill(6)
            last_time = str(row.get("lbt") or "").zfill(6)
            stat = row.get("zttj") or {}
            result.append(
                {
                    "code": code,
                    "secid": f"{row.get('m', 1)}.{code}",
                    "name": name,
                    "price": round(price / 1000, 2) if price else None,
                    "pct": round(_num(row.get("zdp")) or 0.0, 2),
                    "amount": _num(row.get("amount")) or 0.0,
                    "float_cap": _num(row.get("ltsz")) or 0.0,
                    "total_cap": _num(row.get("tshare")) or 0.0,
                    "turnover": _num(row.get("hs")),
                    "board_count": _int(row.get("lbc"), 1),
                    "first_time": f"{first_time[:2]}:{first_time[2:4]}:{first_time[4:6]}",
                    "last_time": f"{last_time[:2]}:{last_time[2:4]}:{last_time[4:6]}",
                    "open_times": _int(row.get("zbc")),
                    "seal_fund": _num(row.get("fund")) or 0.0,
                    "industry": str(row.get("hybk") or "未分类").strip(),
                    "stat_days": _int(stat.get("days"), 1),
                    "stat_count": _int(stat.get("ct"), 1),
                    "market": _int(row.get("m")),
                    "date": date,
                }
            )
        self.last_limit_up_total = _int(data.get("tc"))
        return result

    def fetch_sectors(self, limit: int = 12, ascending: bool = False) -> list[dict]:
        payload = self._get_json(
            "https://push2.eastmoney.com/api/qt/clist/get",
            {
                "pn": 1,
                "pz": max(1, min(limit, 100)),
                "po": 0 if ascending else 1,
                "np": 1,
                "fltt": 2,
                "invt": 2,
                "fid": "f3",
                "fs": "m:90+t:2",
                "fields": "f2,f3,f4,f12,f13,f14,f62,f104,f105,f128,f136,f140,f141",
            },
            timeout=15,
        )
        rows = (payload.get("data") or {}).get("diff") or []
        result = []
        for row in rows:
            name = str(row.get("f14") or "").strip()
            if not name:
                continue
            result.append(
                {
                    "code": str(row.get("f12") or ""),
                    "name": name,
                    "pct": _num(row.get("f3")) or 0.0,
                    "price": _num(row.get("f2")),
                    "main_inflow": _num(row.get("f62")) or 0.0,
                    "up_count": _int(row.get("f104")),
                    "down_count": _int(row.get("f105")),
                    "leader": str(row.get("f128") or ""),
                    "leader_code": str(row.get("f140") or ""),
                    "leader_pct": _num(row.get("f136")),
                }
            )
        return result

    def fetch_quotes(self, secids: list[str]) -> list[dict]:
        secids = [item for item in secids if item]
        if not secids:
            return []
        payload = self._get_json(
            "https://push2.eastmoney.com/api/qt/ulist.np/get",
            {
                "fltt": 2,
                "invt": 2,
                "fields": (
                    "f2,f3,f4,f5,f6,f8,f10,f12,f13,f14,f15,f16,f17,f18,"
                    "f20,f21,f62,f100,f124,f297"
                ),
                "secids": ",".join(secids[:50]),
            },
            timeout=15,
        )
        rows = (payload.get("data") or {}).get("diff") or []
        result = []
        for row in rows:
            stock = normalize_stock(row)
            if stock:
                result.append(stock)
        return result

    def fetch_kline(self, secid: str, lmt: int = 320) -> list[dict]:
        payload = self._get_json(
            "https://push2his.eastmoney.com/api/qt/stock/kline/get",
            {
                "secid": secid,
                "klt": 101,
                "fqt": 1,
                "lmt": max(20, min(lmt, 1000)),
                "end": "20500101",
                "fields1": "f1,f2,f3,f4,f5,f6",
                "fields2": "f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61",
            },
            timeout=15,
        )
        lines = (payload.get("data") or {}).get("klines") or []
        bars = [bar for line in lines if (bar := parse_kline_line(line))]
        return bars
