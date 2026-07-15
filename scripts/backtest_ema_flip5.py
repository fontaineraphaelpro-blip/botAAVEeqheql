"""Walk-forward des meilleures configs v4 + variantes ER pour la fréquence."""

from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
from backtest_ema_flip4 import load, run  # noqa: E402

CANDIDATES = [
    # tf, ema, stopA, trailA, htf, er
    ("1h", 20, 2.5, 2.0, "ema", 0.35),
    ("1h", 20, 3.5, 2.0, "ema", 0.35),
    ("30min", 20, 2.5, 3.0, "ema", 0.35),
    ("30min", 50, 2.5, 3.0, "ema", 0.35),
    ("30min", 20, 2.5, 3.0, "ema", 0.30),
    ("30min", 20, 2.5, 3.0, "ema", 0.25),
    ("15min", 20, 2.5, 3.0, "ema", 0.35),
    ("15min", 20, 2.5, 4.0, "ema", 0.40),
]


def main() -> None:
    rows = []
    for tf, ema, stopa, traila, htf, er in CANDIDATES:
        df = load(tf)
        mid = df.index[len(df) // 2]
        q = [df.index[k * len(df) // 4] for k in range(1, 4)]
        periods = {
            "full": df,
            "Q1": df[df.index < q[0]],
            "Q2": df[(df.index >= q[0]) & (df.index < q[1])],
            "Q3": df[(df.index >= q[1]) & (df.index < q[2])],
            "Q4": df[df.index >= q[2]],
        }
        row = {"tf": tf, "ema": ema, "stopA": stopa, "trailA": traila, "er": er}
        for name, sub in periods.items():
            r = run(sub, tf, ema, stopa, traila, htf, er)
            row[name] = round(r["total_pct"], 1)
            if name == "full":
                row["dd"] = round(r["maxdd_pct"], 1)
                row["trades"] = r["trades"]
                row["t/j"] = round(r["t_per_day"], 2)
                row["wr"] = round(r["winrate"], 1)
        rows.append(row)

    res = pd.DataFrame(rows)
    pd.set_option("display.width", 220)
    print(res.to_string(index=False))


if __name__ == "__main__":
    main()
