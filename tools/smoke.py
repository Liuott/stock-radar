"""Compact end-to-end smoke test against live public data."""

from __future__ import annotations

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.services.market import MarketService


def main() -> int:
    service = MarketService()
    started = time.time()
    overview = service.get_overview(force=True)
    print(
        f"overview: {len(overview['indices'])} indices, "
        f"universe={overview['breadth']['universe']}, "
        f"temperature={overview['temperature']['score']} {overview['temperature']['label']}, "
        f"stale={overview['dataQuality']['stale']}"
    )
    for profile in ("conservative", "balanced", "aggressive"):
        data = service.get_picks(profile)
        metrics = (data.get("model") or {}).get("metrics") or {}
        print(
            f"picks[{profile}]: {len(data['picks'])} picks, qualified={data['qualifiedCount']}, "
            f"auc={metrics.get('auc')}, horizon={data['model'].get('horizonDays')}, "
            f"reason={bool(data.get('noPickReason'))}"
        )
        for pick in data["picks"]:
            print(
                f"  {pick['rank']}. {pick['code']} {pick['name']} "
                f"score={pick['score']} p={pick['probability_pct']}% "
                f"base={pick['base_rate'] * 100:.1f}% "
                f"plan={pick['plan']['entry_low']}-{pick['plan']['entry_high']} "
                f"stop={pick['plan']['stop']}"
            )
        if data["picks"]:
            detail = service.get_stock(data["picks"][0]["code"], profile)
            print(
                f"  detail: {detail['quote']['name']} bars={len(detail['chart']['dates'])} "
                f"composite={detail.get('composite_score')} flags={detail.get('flags')}"
            )

    results = service.search("600")
    print(f"search: {len(results)} results")
    if results:
        code = results[0]["code"]
        before = len(service.get_watchlist()["items"])
        service.add_watch({"code": code, "profile": "balanced"})
        after_add = len(service.get_watchlist()["items"])
        service.remove_watch(code)
        after_remove = len(service.get_watchlist()["items"])
        print(f"watchlist: {before} -> {after_add} -> {after_remove}")
    print(f"elapsed: {time.time() - started:.2f}s")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
