"""Validation robustesse — config candidates sur sous-périodes (walk-forward simple)."""

from __future__ import annotations

from pathlib import Path

import pandas as pd

import sys
sys.path.insert(0, str(Path(__file__).resolve().parent))
from backtest_ema_flip2 import load, run  # noqa: E402

CANDIDATES = [
    # tf, ema, stopA, trailA, htf, flip
    ("30min", 20, 3.5, 3.0, True, False),
    ("30min", 20, 1.5, 3.0, True, False),
    ("30min", 20, 2.5, 3.0, True, False),
    ("15min", 20, 2.5, 4.0, True, False),
    ("15min", 20, 3.5, 4.0, True, False),
    ("1h", 20, 3.5, 2.0, True, False),
]


def main() -> None:
    rows = []
    for tf, ema, stopa, traila, htf, flip in CANDIDATES:
        df = load(tf)
        mid = df.index[len(df) // 2]
        q1 = df.index[len(df) // 4]
        q3 = df.index[3 * len(df) // 4]
        periods = {
            "full": df,
            "H1": df[df.index < mid],
            "H2": df[df.index >= mid],
            "Q1": df[df.index < q1],
            "Q2": df[(df.index >= q1) & (df.index < mid)],
            "Q3": df[(df.index >= mid) & (df.index < q3)],
            "Q4": df[df.index >= q3],
        }
        row = {"tf": tf, "ema": ema, "stopA": stopa, "trailA": traila}
        for name, sub in periods.items():
            r = run(sub, ema, stopa, traila, htf, flip)
            row[name] = round(r["total_pct"], 1)
            if name == "full":
                row["dd"] = round(r["maxdd_pct"], 1)
                row["trades"] = r["trades"]
                row["wr"] = round(r["winrate"], 1)
        rows.append(row)

    res = pd.DataFrame(rows)
    pd.set_option("display.width", 220)
    print(res.to_string(index=False))


if __name__ == "__main__":
    main()
