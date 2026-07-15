"""Backtest v4 — règle EMA flip + filtres réalistes (zéro look-ahead).

Direction: close > EMA => LONG, close < EMA => SHORT (règle utilisateur).
Filtre tendance: EMA "4h équivalent" calculée en continu sur le TF de base
(span mis à l'échelle), donc utilisable en live tel quel.
Filtres additionnels testés: pente EMA, distance min, ADX-like (efficiency ratio).
Gestion: stop ATR initial + trailing chandelier, exécution à l'open suivant.
"""

from __future__ import annotations

import itertools
from pathlib import Path

import numpy as np
import pandas as pd

DATA = Path(__file__).resolve().parent.parent / "data" / "aave_5m_kucoin_12m.csv"
COST = 0.0008  # frais+slippage par côté

TF_MIN = {"5min": 5, "15min": 15, "30min": 30, "1h": 60}


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


def efficiency_ratio(close: pd.Series, n: int = 20) -> pd.Series:
    change = (close - close.shift(n)).abs()
    vol = close.diff().abs().rolling(n).sum()
    return (change / vol).fillna(0)


def run(df: pd.DataFrame, tf: str, ema_len: int, stop_atr: float, trail_atr: float,
        htf_filter: str, er_min: float) -> dict:
    close = df["close"].to_numpy()
    high = df["high"].to_numpy()
    low = df["low"].to_numpy()
    open_ = df["open"].to_numpy()
    ema = df["close"].ewm(span=ema_len, adjust=False).mean().to_numpy()
    a = atr_series(df).to_numpy()

    scale = 240 // TF_MIN[tf]  # nb de barres par bougie 4h
    ema_f = df["close"].ewm(span=50 * scale, adjust=False).mean().to_numpy()
    ema_s = df["close"].ewm(span=200 * scale, adjust=False).mean().to_numpy()
    er = efficiency_ratio(df["close"]).to_numpy()

    n = len(df)
    pos = 0
    entry = stop = 0.0
    equity = 1.0
    trades = []
    eq_curve = np.ones(n)

    for i in range(1, n - 1):
        px = close[i]
        if pos != 0:
            hit = (pos == 1 and low[i] <= stop) or (pos == -1 and high[i] >= stop)
            if hit:
                ret = pos * (stop / entry - 1) - 2 * COST
                equity *= 1 + ret
                trades.append(ret)
                pos = 0
            else:
                if pos == 1:
                    stop = max(stop, high[i] - trail_atr * a[i])
                else:
                    stop = min(stop, low[i] + trail_atr * a[i])

        if htf_filter == "ema":
            allow_long = ema_f[i] > ema_s[i]
            allow_short = ema_f[i] < ema_s[i]
        elif htf_filter == "price":
            allow_long = px > ema_f[i]
            allow_short = px < ema_f[i]
        else:
            allow_long = allow_short = True

        if er_min > 0:
            trending = er[i] >= er_min
            allow_long = allow_long and trending
            allow_short = allow_short and trending

        sig_long = px > ema[i] and allow_long
        sig_short = px < ema[i] and allow_short

        if pos == 0:
            if sig_long:
                pos, entry = 1, open_[i + 1]
                stop = entry - stop_atr * a[i]
            elif sig_short:
                pos, entry = -1, open_[i + 1]
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
        "avg_trade_pct": tr.mean() * 100 if len(tr) else 0,
    }


def main() -> None:
    rows = []
    for tf in ["15min", "30min", "1h"]:
        df = load(tf)
        for ema_len, stop_a, trail_a, htf, er_min in itertools.product(
            [20, 50, 100], [1.5, 2.5, 3.5], [2.0, 3.0, 4.0],
            ["ema", "price", "none"], [0.0, 0.25, 0.35],
        ):
            r = run(df, tf, ema_len, stop_a, trail_a, htf, er_min)
            r.update({"tf": tf, "ema": ema_len, "stopA": stop_a, "trailA": trail_a,
                      "htf": htf, "er": er_min})
            rows.append(r)

    res = pd.DataFrame(rows).sort_values("total_pct", ascending=False)
    cols = ["tf", "ema", "stopA", "trailA", "htf", "er",
            "total_pct", "maxdd_pct", "trades", "t_per_day", "winrate", "avg_trade_pct"]
    pd.set_option("display.width", 240)
    print(res[cols].head(35).to_string(index=False, float_format=lambda x: f"{x:.2f}"))


if __name__ == "__main__":
    main()
