"""Compare validation AUC across forward horizons using cached daily bars."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.services.indicators import build_samples
from app.services.market import MarketService
from app.services.model import train_model


def main() -> int:
    service = MarketService()
    snap = service.get_snapshot()
    candidates = service._preselect(snap, 64)
    bars_map = service._load_klines(candidates)
    benchmark = service._load_kline("1.000300", 320)
    today = service._analysis["today"] if service._analysis else __import__("app.config", fromlist=["now_cn"]).now_cn().strftime("%Y-%m-%d")
    completed = {
        code: [bar for bar in bars if bar["date"] != today]
        for code, bars in bars_map.items()
    }
    completed = {code: bars for code, bars in completed.items() if len(bars) >= 90}
    print("stocks", len(completed))
    for horizon in (1, 2, 3, 5, 10):
        samples = build_samples(completed, benchmark, horizon)
        try:
            artifact = train_model(samples)
            m = artifact["metrics"]
            print(
                f"h={horizon:>2} n={len(samples):>6} auc={m['auc']:.4f} "
                f"acc={m['accuracy']:.4f} base={m['base_rate']:.4f} "
                f"top_hit={m['top_decile_hit']:.4f} edge={m['edge']:+.4f} "
                f"usable={m['usable']}"
            )
        except Exception as exc:
            print(f"h={horizon}: {exc}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
