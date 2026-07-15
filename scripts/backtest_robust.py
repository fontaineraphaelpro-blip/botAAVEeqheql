"""Recherche de réglages robustes sur TOUT l'historique 2020-2026.

Critère : pas le meilleur rendement total, mais le pire rendement annuel le plus
haut possible (robustesse multi-régimes), avec les frais habituels.
"""

from __future__ import annotations

import itertools
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
from backtest_leverage import run_trades  # noqa: E402

CACHE = Path(__file__).resolve().parent.parent / "data" / "aave_30m_binance_full.csv"


def load_tf(tf: str) -> pd.DataFrame:
    df = pd.read_csv(CACHE, parse_dates=["timestamp"]).set_index("timestamp")
    if tf != "30min":
        df = df.resample(tf).agg(
            {"open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"}
        ).dropna()
    return df


def main() -> None:
    grid_tf = ["30min", "1h"]
    grid = list(itertools.product([20, 50, 100], [2.5], [3.0, 4.0], [0.35, 0.45]))

    rows = []
    for tf in grid_tf:
        df = load_tf(tf)
        years = [y for y in sorted(set(df.index.year))
                 if len(df[df.index.year == y]) > (2000 if tf == "30min" else 1000)]
        for ema, stopa, traila, er in grid:
            yearly = {}
            for y in years:
                sub = df[df.index.year == y]
                trades, _ = run_trades(sub, tf, ema, stopa, traila, er)
                trades = np.array(trades)
                yearly[y] = float(np.prod(1 + trades) - 1) * 100 if len(trades) else 0.0
            vals = list(yearly.values())
            total = float(np.prod([1 + v / 100 for v in vals]) - 1) * 100
            rows.append({
                "tf": tf, "ema": ema, "trail": traila, "er": er,
                "total": total, "pire_an": min(vals), "median_an": float(np.median(vals)),
                **{str(y): round(v, 1) for y, v in yearly.items()},
            })

    res = pd.DataFrame(rows).sort_values("pire_an", ascending=False)
    pd.set_option("display.width", 250)
    print(res.head(20).to_string(index=False, float_format=lambda x: f"{x:.1f}"))


if __name__ == "__main__":
    main()
