"""Recherche WR>=50% ciblee — 12 mois, grille reduite mais complete."""

from __future__ import annotations

import pickle
import sys
from itertools import product
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scripts.backtest_optimize import build_signals
from scripts.backtest_wr50_search import simulate_v2

SIG_CACHE = ROOT / "data" / "signals_12m.pkl"


def main() -> None:
    df = pd.read_csv(ROOT / "data" / "aave_5m_kucoin_12m.csv", parse_dates=["timestamp"])
    df["timestamp"] = pd.to_datetime(df["timestamp"], utc=True)
    hours = df["timestamp"].dt.hour
    print(f"12 mois | {len(df)} bougies | {df['timestamp'].iloc[0].date()} -> {df['timestamp'].iloc[-1].date()}")

    if SIG_CACHE.exists():
        signals = pickle.loads(SIG_CACHE.read_bytes())
        print(f"signaux cache: {len(signals)}")
    else:
        print("build signaux...")
        signals = build_signals(df)
        SIG_CACHE.write_bytes(pickle.dumps(signals))
        print(f"signaux saved: {len(signals)}")

    hits = []
    configs = []
    for kinds, side, entry, rr, hold, zmax, body, hrs, tpm, ftp in product(
        [{"EQH_SWEEP"}, {"EQL_SWEEP"}, {"EQH_SWEEP", "EQL_SWEEP"}],
        ["short", "long", "both"],
        ["immediate", "confirm_next", "confirm_same"],
        [0.4, 0.6, 0.8, 1.0],
        [4, 6, 8, 12],
        [0.2, 0.5, 99.0],
        [0.0, 0.55],
        [(0, 24), (8, 20), (12, 22)],
        ["fixed", "mid", "rr"],
        [0.2, 0.3, 0.4, 0.5],
    ):
        if kinds == {"EQH_SWEEP"} and side == "long":
            continue
        if kinds == {"EQL_SWEEP"} and side == "short":
            continue
        if tpm == "rr" and ftp != 0.2:
            continue
        if tpm in ("fixed", "mid") and rr != 1.0:
            continue
        configs.append((kinds, side, entry, rr, hold, zmax, body, hrs, tpm, ftp))

    print(f"Grille: {len(configs)} configs\n")

    for i, g in enumerate(configs):
        m = simulate_v2(
            df,
            signals,
            hours,
            signal_kinds=set(g[0]),
            entry=g[2],
            side_filter=g[1],
            rr=g[3],
            max_hold=g[4],
            max_zone_pct=g[5],
            min_body=g[6],
            min_score=0,
            trend="none",
            hour_rng=g[7],
            tp_mode=g[8],
            fixed_tp_pct=g[9],
            sl_buffer=0.0005,
            use_detect=False,
        )
        if m["n"] >= 40 and m["wr"] >= 50:
            hits.append((m["wr"], m["pnl"], m["pf"], m["n"], g))
        if (i + 1) % 500 == 0:
            print(f"  {i+1}/{len(configs)} | hits: {len(hits)}")

    # aussi profitable WR>=50
    hits.sort(key=lambda x: (x[0], x[2], x[1]), reverse=True)

    print("\n" + "=" * 76)
    print("TOP 20 WIN RATE (min 40 trades, WR>=50%) — 12 mois KuCoin AAVE 5m")
    print("=" * 76)
    for row in hits[:20]:
        wr, pnl, pf, n, g = row
        print(
            f"WR {wr:.1f}% | PnL {pnl:+.1f}% | PF {pf:.2f} | n={n} | "
            f"{g[0]} {g[1]} {g[2]} tp={g[8]} ftp={g[9]}% hold={g[4]} zone<{g[5]} h{g[7]}"
        )

    print("\nTOP 10 WR>=50% ET PnL POSITIF:")
    prof = [h for h in hits if h[1] > 0]
    if not prof:
        print("  (aucune — WR eleve mais PnL negatif sur beaucoup de configs scalping)")
    for row in prof[:10]:
        wr, pnl, pf, n, g = row
        print(f"WR {wr:.1f}% | PnL {pnl:+.1f}% | PF {pf:.2f} | n={n} | tp={g[8]} ftp={g[9]}% {g[0]} {g[1]}")

    if hits:
        print("\n--- Walk-forward meilleur WR ---")
        best = hits[0][4]
        mid = len(df) // 2
        for lab, part in [("H1", df.iloc[:mid]), ("H2", df.iloc[mid:])]:
            m = simulate_v2(
                part,
                build_signals(part),
                part["timestamp"].dt.hour,
                signal_kinds=set(best[0]),
                entry=best[2],
                side_filter=best[1],
                rr=best[3],
                max_hold=best[4],
                max_zone_pct=best[5],
                min_body=best[6],
                min_score=0,
                trend="none",
                hour_rng=best[7],
                tp_mode=best[8],
                fixed_tp_pct=best[9],
                sl_buffer=0.0005,
                use_detect=False,
            )
            print(f"  {lab}: n={m['n']} WR={m['wr']:.1f}% PnL={m['pnl']:+.1f}% PF={m['pf']:.2f}")

    print(f"\nTotal configs WR>=50% (n>=40): {len(hits)} / {len(configs)}")


if __name__ == "__main__":
    main()
