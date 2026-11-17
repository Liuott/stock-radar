"""One-off probe for the Eastmoney public quote endpoints.

Run: python tools/probe.py
"""

from __future__ import annotations

import json
import sys
import time

import requests

H = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Referer": "https://quote.eastmoney.com/",
    "Accept": "application/json,text/plain,*/*",
}
S = requests.Session()
S.headers.update(H)


def show(label: str, payload: object, limit: int = 2600) -> None:
    text = json.dumps(payload, ensure_ascii=False, indent=2)
    print(f"\n===== {label} =====")
    print(text[:limit])


def main() -> int:
    t0 = time.time()
    r = S.get(
        "https://push2.eastmoney.com/api/qt/clist/get",
        params={
            "pn": 1,
            "pz": 6000,
            "po": 1,
            "np": 1,
            "fltt": 2,
            "invt": 2,
            "fid": "f3",
            "fs": "m:0+t:6,m:0+t:80,m:1+t:2,m:1+t:23",
            "fields": "f2,f3,f4,f5,f6,f7,f8,f9,f10,f12,f13,f14,f15,f16,f17,f18,f20,f21,f22,f23,f24,f25,f62,f100,f115",
        },
        timeout=30,
    )
    print("snapshot status", r.status_code, "bytes", len(r.content), "seconds", round(time.time() - t0, 2))
    data = r.json().get("data") or {}
    rows = data.get("diff") or []
    print("total", data.get("total"), "returned", len(rows))
    show("snapshot first 2", rows[:2])

    r2 = S.get(
        "https://push2.eastmoney.com/api/qt/ulist.np/get",
        params={
            "fltt": 2,
            "invt": 2,
            "fields": "f1,f2,f3,f4,f6,f12,f13,f14,f15,f16,f17,f18",
            "secids": "1.000001,0.399001,0.399006,1.000688,1.000300,0.399905",
        },
        timeout=20,
    )
    show("indices", r2.json())

    r3 = S.get(
        "https://push2.eastmoney.com/api/qt/clist/get",
        params={
            "pn": 1,
            "pz": 8,
            "po": 1,
            "np": 1,
            "fltt": 2,
            "invt": 2,
            "fid": "f3",
            "fs": "m:90+t:2",
            "fields": "f2,f3,f4,f12,f13,f14,f62,f104,f105,f128,f136,f140,f141",
        },
        timeout=20,
    )
    show("sectors", r3.json())

    r4 = S.get(
        "https://push2his.eastmoney.com/api/qt/stock/kline/get",
        params={
            "secid": "1.600519",
            "klt": 101,
            "fqt": 1,
            "lmt": 4,
            "end": "20500101",
            "fields1": "f1,f2,f3,f4,f5,f6",
            "fields2": "f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61",
        },
        timeout=20,
    )
    show("kline", r4.json())
    return 0


if __name__ == "__main__":
    sys.exit(main())
