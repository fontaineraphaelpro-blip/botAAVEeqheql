"""Backtest v2 — EMA direction + gestion du risque (stop ATR, trailing, filtres).

Direction = ta règle : close > EMA grise => biais LONG, close < EMA => biais SHORT.
Améliorations testées :
- Stop initial ATR + trailing chandelier
- Filtre tendance HTF (EMA 4h) : ne prendre que les trades alignés
- Filtre volatilité (ATR ratio, comme l'indicateur)
- Sortie sur flip OU sur stop (pas toujours en position)
"""

from __future__ import annotations

import itertools
from pathlib import Path

import numpy as np
import pandas as pd

DATA = Path(__file__).resolve().parent.parent / "data" / "aave_5m_kucoin_12m.csv"
COST = 0.0008  # frais + slippage par côté


def load(tf: str) -> pd.DataFrame:
    df = pd.read_csv(DATA, parse_dates=["timestamp"])
    df = df.set_index("timestamp").sort_index()
    if tf != "5min":
        df = df.resample(tf).agg(
            {"open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"}
        ).dropna()
    return df


def atr_series(df: pd.DataFrame, n: int = 14) -> pd.Series:
    hl = df["high"] - df["low"]
    hc = (df["high"] - df["close"].shift()).abs()
    lc = (df["low"] - df["close"].shift()).abs()
    tr = pd.concat([hl, hc, lc], axis=1).max(axis=1)
    return tr.ewm(alpha=1 / n, adjust=False).mean()


def run(df: pd.DataFrame, ema_len: int, stop_atr: float, trail_atr: float,
        use_htf: bool, exit_on_flip: bool) -> dict:
    close = df["close"].to_numpy()
    high = df["high"].to_numpy()
    low = df["low"].to_numpy()
    open_ = df["open"].to_numpy()
    ema = df["close"].ewm(span=ema_len, adjust=False).mean().to_numpy()
    a = atr_series(df).to_numpy()

    # HTF filter: EMA50/EMA200 sur 4h reconstruit.
    # shift(1) => on n'utilise que la dernière bougie 4h CLÔTURÉE (pas de look-ahead).
    htf = df["close"].resample("4h").last().dropna()
    f = htf.ewm(span=50, adjust=False).mean()
    s = htf.ewm(span=200, adjust=False).mean()
    bull_htf = ((htf > f) & (f > s)).shift(1).fillna(False)
    bear_htf = ((htf < f) & (f < s)).shift(1).fillna(False)
    bull_htf = bull_htf.reindex(df.index, method="ffill").fillna(False).to_numpy()
    bear_htf = bear_htf.reindex(df.index, method="ffill").fillna(False).to_numpy()

    n = len(df)
    pos = 0            # +1 / -1 / 0
    entry = stop = 0.0
    equity = 1.0
    trades = []
    eq_curve = np.ones(n)

    for i in range(1, n - 1):
        px = close[i]
        if pos != 0:
            # check stop intra-bougie
            hit = (pos == 1 and low[i] <= stop) or (pos == -1 and high[i] >= stop)
            if hit:
                fill = stop
                ret = pos * (fill / entry - 1) - 2 * COST
                equity *= 1 + ret
                trades.append(ret)
                pos = 0
            else:
                # trailing
                if pos == 1:
                    stop = max(stop, high[i] - trail_atr * a[i])
                else:
                    stop = min(stop, low[i] + trail_atr * a[i])

        sig_long = px > ema[i]
        sig_short = px < ema[i]
        if use_htf:
            sig_long = sig_long and bull_htf[i]
            sig_short = sig_short and bear_htf[i]

        if pos == 1 and exit_on_flip and close[i] < ema[i]:
            ret = 1 * (open_[i + 1] / entry - 1) - 2 * COST
            equity *= 1 + ret
            trades.append(ret)
            pos = 0
        elif pos == -1 and exit_on_flip and close[i] > ema[i]:
            ret = -1 * (open_[i + 1] / entry - 1) - 2 * COST
            equity *= 1 + ret
            trades.append(ret)
            pos = 0

        if pos == 0:
            if sig_long:
                pos = 1
                entry = open_[i + 1]
                stop = entry - stop_atr * a[i]
            elif sig_short:
                pos = -1
                entry = open_[i + 1]
                stop = entry + stop_atr * a[i]

        eq_curve[i] = equity

    eq_curve[-1] = equity
    peak = np.maximum.accumulate(eq_curve)
    dd = (eq_curve / peak - 1).min()
    tr = np.array(trades)
    days = (df.index[-1] - df.index[0]).days or 1
    return {
        "total_pct": (equity - 1) * 100,
        "maxdd_pct": dd * 100,
        "trades": len(tr),
        "t_per_day": len(tr) / days,
        "winrate": (tr > 0).mean() * 100 if len(tr) else 0,
    }


def main() -> None:
    rows = []
    for tf in ["15min", "30min", "1h"]:
        df = load(tf)
        for ema_len, stop_a, trail_a, htf, flip in itertools.product(
            [20, 50, 100], [1.5, 2.5, 3.5], [2.0, 3.0, 4.0], [True, False], [True, False]
        ):
            r = run(df, ema_len, stop_a, trail_a, htf, flip)
            r.update({"tf": tf, "ema": ema_len, "stopA": stop_a, "trailA": trail_a,
                      "htf": htf, "flip": flip})
            rows.append(r)

    res = pd.DataFrame(rows).sort_values("total_pct", ascending=False)
    cols = ["tf", "ema", "stopA", "trailA", "htf", "flip",
            "total_pct", "maxdd_pct", "trades", "t_per_day", "winrate"]
    pd.set_option("display.width", 220)
    print(res[cols].head(30).to_string(index=False, float_format=lambda x: f"{x:.2f}"))


if __name__ == "__main__":
    main()
