"""Recherche elargie — local only."""

from __future__ import annotations

import sys
from itertools import product
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scripts.backtest_optimize import Signal, build_signals, simulate

cache = ROOT / "data" / "aave_5m_kucoin_6m.csv"
df = pd.read_csv(cache, parse_dates=["timestamp"])
df["timestamp"] = pd.to_datetime(df["timestamp"], utc=True)
hours = df["timestamp"].dt.hour

print("Chargement signaux...")
signals = build_signals(df)

best = []
configs = []

for kinds, side, entry, rr, hold, zmax, body, hour_window in product(
    [{"EQH_SWEEP"}, {"EQL_SWEEP"}, {"EQH_SWEEP", "EQL_SWEEP"}],
    ["short", "long", "both"],
    ["confirm_next", "confirm_same"],
    [3.0, 4.0, 5.0, 6.0],
    [6, 8, 12, 16, 24],
    [0.1, 0.2, 0.35],
    [0.45, 0.55, 0.65],
    [(0, 24), (7, 17), (12, 22)],
):
    if kinds == {"EQH_SWEEP"} and side == "long":
        continue
    if kinds == {"EQL_SWEEP"} and side == "short":
        continue
    configs.append((kinds, side, entry, rr, hold, zmax, body, hour_window))

print(f"Test {len(configs)} configs filtrees...")


def simulate_session(df, signals, hour_start, hour_end, **kw):
  # filter signals by hour
  filt = [s for s in signals if hour_start <= hours.iloc[s.bar] < hour_end]
  return simulate(df, filt, **kw)

for kinds, side, entry, rr, hold, zmax, body, (hs, he) in configs:
    m = simulate_session(
        df,
        signals,
        hs,
        he,
        signal_kinds=kinds,
        entry=entry,
        min_score=0,
        min_zone_pct=0,
        max_zone_pct=zmax,
        side_filter=side,
        trend_mode="none",
        ema_span=50,
        min_body_ratio=body,
        rr=rr,
        max_hold=hold,
        sl_buffer=0.0006,
    )
    if m["n"] >= 25:
        best.append((m["pnl"], m["pf"], m["wr"], m["n"], kinds, side, entry, rr, hold, zmax, body, hs, he))

best.sort(key=lambda x: (x[1], x[0]), reverse=True)

print("\nTOP 20 par Profit Factor (min 25 trades)")
print("-" * 80)
profitable = [b for b in best if b[1] >= 1.0 and b[0] > 0]
for row in (profitable[:20] if profitable else best[:20]):
    pnl, pf, wr, n, kinds, side, entry, rr, hold, zmax, body, hs, he = row
    print(
        f"PnL {pnl:+.1f}% PF {pf:.2f} WR {wr:.1f}% n={n} | {kinds} {side} | "
        f"{entry} RR{rr} hold{hold} zone<{zmax}% body>{body} hrs{hs}-{he}"
    )

if profitable:
    print(f"\n>>> {len(profitable)} configs rentables (PF>=1, PnL>0)")
else:
    print("\n>>> Aucune config rentable. Meilleur PnL:")
    best_pnl = sorted(best, key=lambda x: x[0], reverse=True)[:5]
    for row in best_pnl:
        print(f"  PnL {row[0]:+.1f}% PF {row[1]:.2f} WR {row[2]:.1f}% n={row[3]}")
