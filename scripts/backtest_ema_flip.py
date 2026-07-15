"""Backtest stratégie EMA-flip long/short sur AAVE/USDT 5m (12 mois).

Règle de base (demande utilisateur) :
- Clôture au-dessus de la ligne grise (EMA lente) -> LONG
- Clôture en dessous -> SHORT
Toujours en position (stop-and-reverse).

Variantes testées pour limiter les faux signaux :
- Timeframe (5m, 15m, 30m, 1h) par resampling
- Longueur EMA
- Marge de franchissement (buffer % ou ATR)
- Nombre de bougies de confirmation
Frais + slippage réalistes appliqués à chaque flip.
"""

from __future__ import annotations

import itertools
import sys
from pathlib import Path

import numpy as np
import pandas as pd

DATA = Path(__file__).resolve().parent.parent / "data" / "aave_5m_kucoin_12m.csv"

FEE_PCT = 0.0005      # 0.05% taker (perp-like) par côté
SLIP_PCT = 0.0003     # 0.03% slippage par côté
COST_PER_SIDE = FEE_PCT + SLIP_PCT


def load(tf: str) -> pd.DataFrame:
    df = pd.read_csv(DATA, parse_dates=["timestamp"])
    df = df.set_index("timestamp").sort_index()
    if tf != "5min":
        df = df.resample(tf).agg(
            {"open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"}
        ).dropna()
    return df


def atr(df: pd.DataFrame, n: int = 14) -> pd.Series:
    hl = df["high"] - df["low"]
    hc = (df["high"] - df["close"].shift()).abs()
    lc = (df["low"] - df["close"].shift()).abs()
    tr = pd.concat([hl, hc, lc], axis=1).max(axis=1)
    return tr.ewm(alpha=1 / n, adjust=False).mean()


def backtest(df: pd.DataFrame, ema_len: int, buffer_atr: float, confirm: int) -> dict:
    close = df["close"]
    ema = close.ewm(span=ema_len, adjust=False).mean()
    a = atr(df)

    above = close > ema + buffer_atr * a
    below = close < ema - buffer_atr * a

    if confirm > 1:
        above = above.rolling(confirm).sum() == confirm
        below = below.rolling(confirm).sum() == confirm

    # Signal état : +1 long, -1 short, gardé jusqu'au signal opposé
    raw = pd.Series(np.where(above, 1, np.where(below, -1, 0)), index=df.index)
    pos = raw.replace(0, np.nan).ffill().fillna(0)

    # Exécution à l'open de la bougie suivante
    pos_exec = pos.shift(1).fillna(0)
    ret = close.pct_change().fillna(0)
    strat_ret = pos_exec * ret

    flips = pos_exec.diff().abs().fillna(0)
    # flip 0->1 coûte 1 côté, 1->-1 coûte 2 côtés
    costs = flips * COST_PER_SIDE
    strat_ret = strat_ret - costs

    equity = (1 + strat_ret).cumprod()
    total = equity.iloc[-1] - 1
    n_trades = int((flips > 0).sum())

    # Max drawdown
    peak = equity.cummax()
    dd = (equity / peak - 1).min()

    # Win rate par trade (segments de position constante)
    seg = (pos_exec != pos_exec.shift()).cumsum()
    trade_rets = (1 + strat_ret).groupby(seg).prod() - 1
    active = pos_exec.groupby(seg).first() != 0
    trade_rets = trade_rets[active]
    wr = (trade_rets > 0).mean() if len(trade_rets) else 0.0

    days = (df.index[-1] - df.index[0]).days or 1
    return {
        "total_pct": total * 100,
        "maxdd_pct": dd * 100,
        "trades": n_trades,
        "trades_per_day": n_trades / days,
        "winrate": wr * 100,
    }


def main() -> None:
    tfs = ["5min", "15min", "30min", "1h"]
    ema_lens = [20, 50, 100, 200]
    buffers = [0.0, 0.15, 0.3, 0.5]
    confirms = [1, 2, 3]

    rows = []
    for tf in tfs:
        df = load(tf)
        for ema_len, buf, conf in itertools.product(ema_lens, buffers, confirms):
            r = backtest(df, ema_len, buf, conf)
            r.update({"tf": tf, "ema": ema_len, "buffer": buf, "confirm": conf})
            rows.append(r)

    res = pd.DataFrame(rows)
    res = res.sort_values("total_pct", ascending=False)
    cols = ["tf", "ema", "buffer", "confirm", "total_pct", "maxdd_pct", "trades", "trades_per_day", "winrate"]
    pd.set_option("display.width", 200)
    print("=== TOP 25 (12 mois, frais 0.05% + slippage 0.03% par côté) ===")
    print(res[cols].head(25).to_string(index=False, float_format=lambda x: f"{x:.2f}"))
    print()
    print("=== Buy & hold sur la période ===")
    df5 = load("5min")
    print(f"{(df5['close'].iloc[-1] / df5['close'].iloc[0] - 1) * 100:.1f}%")


if __name__ == "__main__":
    main()
