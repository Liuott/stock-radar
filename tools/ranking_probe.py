"""Inspect the model-vs-rule ranking on the current snapshot."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.services.market import MarketService


def main() -> int:
    service = MarketService()
    analysis = service._build_analysis(force=True)
    rows = analysis["rows"]
    model = analysis["model"]
    print("model", model["metrics"] if model else None)
    print("\nTOP BY MODEL PROBABILITY")
    for row in sorted(rows, key=lambda item: item["probability"], reverse=True)[:12]:
        print(
            f"{row['code']} {row['name'][:6]:<6} p={row['probability']:.3f} "
            f"score={row['score']:.1f} rule={row['rule_score']:.1f} "
            f"trend={row['trend_score']:.0f} risk={row['risk_score']:.0f} "
            f"pct={row['pct']:+.1f} atr={row['atr_pct']*100:.1f}%"
        )
    print("\nTOP BY COMBINED SCORE")
    for row in rows[:12]:
        print(
            f"{row['code']} {row['name'][:6]:<6} score={row['score']:.1f} "
            f"p={row['probability']:.3f} rule={row['rule_score']:.1f} "
            f"trend={row['trend_score']:.0f} risk={row['risk_score']:.0f}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
