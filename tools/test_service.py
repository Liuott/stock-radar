"""Integration smoke test for the market service (live data)."""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.services.market import MarketService


def main() -> int:
    service = MarketService()
    t0 = time.time()
    overview = service.get_overview(force=True)
    print("overview seconds", round(time.time() - t0, 2))
    print(
        json.dumps(
            {
                "asOf": overview["asOf"],
                "market": overview["market"],
                "breadth": overview["breadth"],
                "temperature": overview["temperature"],
                "turnover": overview["turnover"],
                "indices": [
                    {"name": item["name"], "price": item["price"], "pct": item["pct"], "spark": len(item.get("spark") or [])}
                    for item in overview["indices"]
                ],
                "sector_leaders": [
                    {"name": item["name"], "pct": item["pct"]}
                    for item in overview["sectors"]["leaders"][:3]
                ],
                "dataQuality": overview["dataQuality"],
            },
            ensure_ascii=False,
            indent=2,
        )
    )

    t1 = time.time()
    picks = service.get_picks("balanced", force=True)
    print("\npicks seconds", round(time.time() - t1, 2))
    print(
        json.dumps(
            {
                "asOf": picks["asOf"],
                "candidateCount": picks["candidateCount"],
                "klineCount": picks["klineCount"],
                "sampleCount": picks["sampleCount"],
                "model": picks["model"],
                "picks": [
                    {
                        "rank": item["rank"],
                        "code": item["code"],
                        "name": item["name"],
                        "industry": item["industry"],
                        "price": item["price"],
                        "pct": item["pct"],
                        "score": item["score"],
                        "probability_pct": item["probability_pct"],
                        "trend_score": item["trend_score"],
                        "risk_score": item["risk_score"],
                        "flags": item["flags"],
                        "plan": item["plan"],
                        "reasons": item["reasons"],
                    }
                    for item in picks["picks"]
                ],
            },
            ensure_ascii=False,
            indent=2,
        )
    )

    if picks["picks"]:
        detail = service.get_stock(picks["picks"][0]["code"])
        print("\ndetail", detail["quote"]["name"], len(detail["chart"]["dates"]), detail["score"]["rule_score"])
        watch = service.add_watch(
            {
                "code": picks["picks"][0]["code"],
                "entry_low": picks["picks"][0]["plan"]["entry_low"],
                "entry_high": picks["picks"][0]["plan"]["entry_high"],
                "stop": picks["picks"][0]["plan"]["stop"],
                "target1": picks["picks"][0]["plan"]["target1"],
                "profile": "balanced",
            }
        )
        print("watch", json.dumps(watch, ensure_ascii=False)[:700])
        service.remove_watch(picks["picks"][0]["code"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
