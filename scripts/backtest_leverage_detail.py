"""Détail complet du backtest 12 mois avec levier 1x / 2x / 3x / 5x."""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
from backtest_ema_flip4 import load  # noqa: E402
from backtest_leverage import run_trades  # noqa: E402


def main() -> None:
    df = load("30min")
    trades, worst = run_trades(df, "30min", 20, 2.5, 3.0, 0.35)
    trades = np.array(trades)
    worst = np.array(worst[: len(trades)])
    days = (df.index[-1] - df.index[0]).days

    wins = trades[trades > 0]
    losses = trades[trades <= 0]
    # série perdante max
    streak = max_streak = 0
    for t in trades:
        streak = streak + 1 if t <= 0 else 0
        max_streak = max(max_streak, streak)

    print("=== STATS DE BASE (12 mois, 30min EMA20 + filtres) ===")
    print(f"Trades           : {len(trades)} ({len(trades)/days:.2f}/jour en moyenne)")
    print(f"Winrate          : {len(wins)/len(trades)*100:.1f}%")
    print(f"Gain moyen       : {wins.mean()*100:+.2f}% | Perte moyenne : {losses.mean()*100:+.2f}%")
    print(f"Profit factor    : {wins.sum()/abs(losses.sum()):.2f}")
    print(f"Meilleur trade   : {trades.max()*100:+.2f}% | Pire : {trades.min()*100:+.2f}%")
    print(f"Série perdante max : {max_streak} trades d'affilée")
    print()

    for L in [1, 2, 3, 5]:
        eq = 1000.0
        peak, maxdd = eq, 0.0
        curve = []
        liquidated = False
        for r, w in zip(trades, worst):
            if w * L <= -0.90 or eq * (1 + L * r) <= 0:
                liquidated = True
                eq = 0.0
                curve.append(eq)
                break
            eq *= 1 + L * r
            curve.append(eq)
            peak = max(peak, eq)
            maxdd = min(maxdd, eq / peak - 1)

        print(f"=== LEVIER {L}x ===")
        if liquidated:
            print("  LIQUIDÉ — capital à zéro avant la fin de l'année\n")
            continue
        print(f"  1000€ -> {eq:.0f}€  ({(eq/1000-1)*100:+.1f}% | {(eq-1000)/days:+.2f}€/jour moyen)")
        print(f"  Drawdown max : {maxdd*100:.1f}%")
        print(f"  Pire trade   : {trades.min()*L*100:+.1f}% du capital")
        print(f"  Série perdante max ({max_streak} trades) : "
              f"{(np.prod(1 + L*np.sort(trades)[:max_streak]) - 1)*100:+.1f}% (approx pire cas)")
        # équité par trimestre
        n = len(curve)
        marks = [curve[n//4 - 1], curve[n//2 - 1], curve[3*n//4 - 1], curve[-1]]
        print(f"  Équité fin T1/T2/T3/T4 : {marks[0]:.0f}€ / {marks[1]:.0f}€ / {marks[2]:.0f}€ / {marks[3]:.0f}€")
        print()


if __name__ == "__main__":
    main()
