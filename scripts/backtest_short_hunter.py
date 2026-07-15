"""Chasseur de shorts — backtest portefeuille multi-alts 2h, 2020-2026.

Logique :
- Régime : shorts autorisés seulement si BTC < EMA 200 jours (le marché sombre).
- Signal par coin : cassure baissière (close < EMA ou nouveau plus-bas N barres)
  + efficiency ratio >= seuil (chute directionnelle, pas du bruit).
- Sélection : max K positions simultanées, priorité aux plus faibles (pire momentum 7j).
- Sortie : stop initial 2.5×ATR au-dessus, trailing chandelier vers le bas.
- Coûts : 0.05% frais + 0.05% slippage par côté, funding 0.01%/8h payé pendant le short.
- Taille : équité/K par position, pas de levier.

Note : univers = coins encore listés (les morts auraient été de meilleurs shorts,
le résultat est donc plutôt conservateur).
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

DATA = Path(__file__).resolve().parent.parent / "data" / "alts_2h"
COST_SIDE = 0.001          # 0.05% frais + 0.05% slippage
FUNDING_PER_BAR = 0.0001 / 4   # 0.01%/8h -> par bougie 2h
BARS_PER_DAY = 12


def load_universe() -> tuple[pd.DatetimeIndex, dict[str, pd.DataFrame]]:
    coins: dict[str, pd.DataFrame] = {}
    for f in sorted(DATA.glob("*.csv")):
        df = pd.read_csv(f, parse_dates=["timestamp"]).set_index("timestamp")
        coins[f.stem] = df
    btc = coins.pop("BTCUSDT")
    index = btc.index
    return index, {"BTC": btc, **coins}


def atr_np(df: pd.DataFrame, n: int = 14) -> pd.Series:
    hl = df["high"] - df["low"]
    hc = (df["high"] - df["close"].shift()).abs()
    lc = (df["low"] - df["close"].shift()).abs()
    tr = pd.concat([hl, hc, lc], axis=1).max(axis=1)
    return tr.ewm(alpha=1 / n, adjust=False).mean()


def er_np(close: pd.Series, n: int = 20) -> pd.Series:
    change = (close - close.shift(n)).abs()
    vol = close.diff().abs().rolling(n).sum()
    return (change / vol).fillna(0)


def run(index: pd.DatetimeIndex, coins: dict[str, pd.DataFrame], *,
        entry_mode: str, ema_len: int, low_n: int, er_min: float,
        stop_atr: float, trail_atr: float, max_pos: int, use_btc_filter: bool) -> dict:
    btc = coins["BTC"].reindex(index)
    btc_ema200d = btc["close"].ewm(span=200 * BARS_PER_DAY, adjust=False).mean()
    if use_btc_filter == "strict":
        # Bear confirmé : prix sous l'EMA 200j ET EMA en baisse depuis 30 jours
        slope_down = btc_ema200d < btc_ema200d.shift(30 * BARS_PER_DAY)
        regime_bear = ((btc["close"] < btc_ema200d) & slope_down).to_numpy()
    else:
        regime_bear = (btc["close"] < btc_ema200d).to_numpy()

    # Précalcul par coin
    data = {}
    for sym, df in coins.items():
        if sym == "BTC":
            continue
        d = df.reindex(index)
        close = d["close"]
        data[sym] = {
            "open": d["open"].to_numpy(),
            "high": d["high"].to_numpy(),
            "low": d["low"].to_numpy(),
            "close": close.to_numpy(),
            "ema": close.ewm(span=ema_len, adjust=False).mean().to_numpy(),
            "atr": atr_np(d).to_numpy(),
            "er": er_np(close).to_numpy(),
            "lown": d["low"].rolling(low_n).min().shift(1).to_numpy(),
            "mom7d": close.pct_change(7 * BARS_PER_DAY).to_numpy(),
        }

    n = len(index)
    equity = 1000.0
    positions: dict[str, dict] = {}   # sym -> {qty, entry, stop, notional}
    pending: list[str] = []
    trades: list[float] = []
    eq_curve = np.full(n, np.nan)

    for i in range(1, n - 1):
        # 1. exécuter les entrées décidées à la bougie précédente
        for sym in pending:
            if len(positions) >= max_pos or sym in positions:
                continue
            px = data[sym]["open"][i]
            a = data[sym]["atr"][i - 1]
            if np.isnan(px) or np.isnan(a) or px <= 0:
                continue
            notional = equity / max_pos
            positions[sym] = {
                "entry": px, "qty": notional / px,
                "stop": px + stop_atr * a, "notional": notional,
            }
            equity -= notional * COST_SIDE
        pending = []

        # 2. gérer les positions (stop, trailing, funding)
        for sym in list(positions):
            p = positions[sym]
            hi = data[sym]["high"][i]
            lo = data[sym]["low"][i]
            a = data[sym]["atr"][i]
            if np.isnan(hi):
                continue
            equity -= p["notional"] * FUNDING_PER_BAR
            if hi >= p["stop"]:
                exit_px = p["stop"]
                pnl = p["qty"] * (p["entry"] - exit_px)
                equity += pnl - p["qty"] * exit_px * COST_SIDE
                trades.append(pnl / p["notional"])
                del positions[sym]
            else:
                p["stop"] = min(p["stop"], lo + trail_atr * a)

        # 3. nouveaux signaux (exécutés à l'open suivant)
        allow = regime_bear[i] if use_btc_filter else True
        if allow and len(positions) < max_pos:
            cands = []
            for sym, d in data.items():
                if sym in positions:
                    continue
                c, e, er_v = d["close"][i], d["ema"][i], d["er"][i]
                if np.isnan(c) or np.isnan(e):
                    continue
                if er_v < er_min:
                    continue
                if entry_mode == "ema":
                    sig = c < e
                else:  # breakdown
                    sig = not np.isnan(d["lown"][i]) and c < d["lown"][i]
                if sig:
                    cands.append((d["mom7d"][i], sym))
            cands.sort()  # plus faible momentum d'abord
            for _, sym in cands[: max_pos - len(positions)]:
                pending.append(sym)

        # 4. mark-to-market
        mtm = equity
        for sym, p in positions.items():
            c = data[sym]["close"][i]
            if not np.isnan(c):
                mtm += p["qty"] * (p["entry"] - c)
        eq_curve[i] = mtm

    eq = pd.Series(eq_curve, index=index).dropna()
    yearly = eq.resample("YE").last() / eq.resample("YE").first() - 1
    peak = eq.cummax()
    dd = (eq / peak - 1).min()
    tr = np.array(trades)
    return {
        "final": eq.iloc[-1],
        "total_pct": (eq.iloc[-1] / 1000 - 1) * 100,
        "maxdd_pct": dd * 100,
        "trades": len(tr),
        "winrate": (tr > 0).mean() * 100 if len(tr) else 0,
        "yearly": {ts.year: v * 100 for ts, v in yearly.items()},
    }


def main() -> None:
    index, coins = load_universe()
    print(f"Univers : {len(coins)-1} alts + BTC | {index[0].date()} -> {index[-1].date()}\n")

    configs = [
        # le meilleur du 1er passage
        dict(entry_mode="breakdown", ema_len=50, low_n=84, er_min=0.35, stop_atr=3.0,
             trail_atr=4.0, max_pos=5, use_btc_filter=True),
        # même chose avec régime strict (EMA 200j en baisse)
        dict(entry_mode="breakdown", ema_len=50, low_n=84, er_min=0.35, stop_atr=3.0,
             trail_atr=4.0, max_pos=5, use_btc_filter="strict"),
        # variantes autour du meilleur
        dict(entry_mode="breakdown", ema_len=50, low_n=84, er_min=0.40, stop_atr=3.0,
             trail_atr=4.0, max_pos=5, use_btc_filter="strict"),
        dict(entry_mode="breakdown", ema_len=50, low_n=120, er_min=0.35, stop_atr=3.0,
             trail_atr=4.0, max_pos=5, use_btc_filter="strict"),
        dict(entry_mode="breakdown", ema_len=50, low_n=84, er_min=0.35, stop_atr=2.5,
             trail_atr=3.0, max_pos=3, use_btc_filter="strict"),
        dict(entry_mode="breakdown", ema_len=50, low_n=120, er_min=0.40, stop_atr=3.0,
             trail_atr=4.0, max_pos=3, use_btc_filter="strict"),
    ]

    for cfg in configs:
        r = run(index, coins, **cfg)
        years = " ".join(f"{y}:{v:+.0f}%" for y, v in r["yearly"].items())
        print(
            f"[{cfg['entry_mode']:>9} ema{cfg['ema_len']} er{cfg['er_min']} "
            f"btc={'on' if cfg['use_btc_filter'] else 'off'} K={cfg['max_pos']}] "
            f"1000€ -> {r['final']:.0f}€ ({r['total_pct']:+.1f}%) | DD {r['maxdd_pct']:.1f}% | "
            f"{r['trades']} trades, WR {r['winrate']:.0f}%"
        )
        print(f"    {years}\n")


if __name__ == "__main__":
    main()
