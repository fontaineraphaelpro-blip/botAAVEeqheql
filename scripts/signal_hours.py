"""Répartition horaire des signaux backtest (UTC)."""

from __future__ import annotations

import pickle
import sys
from collections import Counter
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

with open(ROOT / "data/signals_12m.pkl", "rb") as f:
    signals = pickle.load(f)

df = pd.read_csv(ROOT / "data/aave_5m_kucoin_12m.csv", parse_dates=["timestamp"])
df["timestamp"] = pd.to_datetime(df["timestamp"], utc=True)
hours = df["timestamp"].dt.hour

print(f"Periode : {df['timestamp'].iloc[0]} -> {df['timestamp'].iloc[-1]} (UTC)")
print(f"Signaux total : {len(signals)}\n")


def show(label: str, kinds: list[str]) -> None:
    sub = [s for s in signals if s.kind in kinds]
    c = Counter(int(hours.iloc[s.bar]) for s in sub)
    print(f"=== {label} ({len(sub)}) ===")
    mx = max(c.values()) if c else 1
    for h in range(24):
        n = c.get(h, 0)
        if n:
            bar = "#" * max(1, n * 35 // mx)
            print(f"  {h:02d}h UTC : {n:4d}  {bar}")
    in_win = sum(v for h, v in c.items() if 12 <= h < 22)
    if sub:
        print(f"  12h-22h UTC : {in_win} ({100 * in_win / len(sub):.1f}%)")
        print(f"  hors fenetre : {len(sub) - in_win} ({100 * (len(sub) - in_win) / len(sub):.1f}%)\n")


show("Sweeps EQH + EQL", ["EQH_SWEEP", "EQL_SWEEP"])
show("EQH sweep", ["EQH_SWEEP"])
show("EQL sweep", ["EQL_SWEEP"])

sub = [s for s in signals if s.kind in ("EQH_SWEEP", "EQL_SWEEP")]
c = Counter(int(hours.iloc[s.bar]) for s in sub)
print("Top 5 heures (sweeps) :", ", ".join(f"{h}h ({n})" for h, n in c.most_common(5)))
