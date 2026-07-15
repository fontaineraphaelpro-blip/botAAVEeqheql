"""Impact du levier sur la stratégie validée (30min EMA20, filtres, trailing).

Applique un levier L aux retours par trade :
- retour trade avec levier = L * retour spot - coûts * L (frais sur le notionnel)
- liquidation si le retour d'un trade <= -90 % de l'équité (marge épuisée avant le stop)
- funding perp ~0.01 %/8h ignoré (mineur mais réel)
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
from backtest_ema_flip4 import load, run, atr_series, efficiency_ratio, COST, TF_MIN  # noqa: E402


def run_trades(df: pd.DataFrame, tf: str, ema_len: int, stop_atr: float, trail_atr: float,
               er_min: float) -> list[float]:
    """Même logique que backtest_ema_flip4.run mais retourne la liste des retours par trade."""
    close = df["close"].to_numpy()
    high = df["high"].to_numpy()
    low = df["low"].to_numpy()
    open_ = df["open"].to_numpy()
    ema = df["close"].ewm(span=ema_len, adjust=False).mean().to_numpy()
    a = atr_series(df).to_numpy()
    scale = 240 // TF_MIN[tf]
    ema_f = df["close"].ewm(span=50 * scale, adjust=False).mean().to_numpy()
    ema_s = df["close"].ewm(span=200 * scale, adjust=False).mean().to_numpy()
    er = efficiency_ratio(df["close"]).to_numpy()

    n = len(df)
    pos = 0
    entry = stop = 0.0
    trades: list[float] = []
    worst_intra: list[float] = []

    for i in range(1, n - 1):
        px = close[i]
        if pos != 0:
            # pire excursion intra-trade sur cette bougie (pour la liquidation)
            adverse = (low[i] / entry - 1) if pos == 1 else -(high[i] / entry - 1)
            worst_intra[-1] = min(worst_intra[-1], adverse)
            hit = (pos == 1 and low[i] <= stop) or (pos == -1 and high[i] >= stop)
            if hit:
                trades.append(pos * (stop / entry - 1) - 2 * COST)
                pos = 0
            else:
                if pos == 1:
                    stop = max(stop, high[i] - trail_atr * a[i])
                else:
                    stop = min(stop, low[i] + trail_atr * a[i])

        allow_long = ema_f[i] > ema_s[i] and er[i] >= er_min
        allow_short = ema_f[i] < ema_s[i] and er[i] >= er_min

        if pos == 0:
            if px > ema[i] and allow_long:
                pos, entry = 1, open_[i + 1]
                stop = entry - stop_atr * a[i]
                worst_intra.append(0.0)
            elif px < ema[i] and allow_short:
                pos, entry = -1, open_[i + 1]
                stop = entry + stop_atr * a[i]
                worst_intra.append(0.0)

    return trades, worst_intra


def main() -> None:
    df = load("30min")
    trades, worst = run_trades(df, "30min", 20, 2.5, 3.0, 0.35)
    trades = np.array(trades)
    worst = np.array(worst[: len(trades)])
    days = (df.index[-1] - df.index[0]).days

    print(f"{len(trades)} trades sur {days} jours")
    print(f"Retour spot moyen/trade : {trades.mean()*100:+.3f}% | pire trade {trades.min()*100:.2f}%")
    print(f"Pire excursion intra-trade : {worst.min()*100:.2f}%\n")

    print(f"{'Levier':>7} {'Final (1000€)':>14} {'€/jour moy':>11} {'MaxDD':>8} {'Liquidé?':>9}")
    for L in [1, 2, 3, 5, 10, 25]:
        eq = 1000.0
        peak = eq
        maxdd = 0.0
        liquidated = False
        for r, w in zip(trades, worst):
            # liquidation si l'excursion adverse * levier consomme ~90% de la marge
            if w * L <= -0.90:
                liquidated = True
                eq = 0.0
                break
            eq *= 1 + L * r
            if eq <= 0:
                liquidated = True
                eq = 0.0
                break
            peak = max(peak, eq)
            maxdd = min(maxdd, eq / peak - 1)
        per_day = (eq - 1000.0) / days
        print(f"{L:>6}x {eq:>13.0f}€ {per_day:>10.2f}€ {maxdd*100:>7.1f}% {'OUI' if liquidated else 'non':>9}")


if __name__ == "__main__":
    main()
