"""Backtest 1 an — Tendance + Chasseur.

Paramètres alignés sur la config paper actuelle.
Solde de départ : 1000 USDT par bot.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from backtest_ema_flip4 import atr_series, efficiency_ratio  # noqa: E402
from backtest_short_hunter import load_universe, run as run_shorts  # noqa: E402

AAVE_5M = ROOT / "data" / "aave_5m_kucoin_12m.csv"
OUT = ROOT / "data" / "backtest_fleet_1y.json"

START = 1000.0
# frais+slippage par côté (aligné config ~0.05+0.03)
COST_SIDE = 0.0008


def load_aave_5m() -> pd.DataFrame:
    df = pd.read_csv(AAVE_5M, parse_dates=["timestamp"])
    df = df.set_index("timestamp").sort_index()
    if df.index.tz is None:
        df.index = df.index.tz_localize("UTC")
    return df


def stats_from_equity(eq: pd.Series, trades: list[float], start: float = START) -> dict:
    eq = eq.dropna()
    if eq.empty:
        return {
            "final": start,
            "pnl": 0.0,
            "pnl_pct": 0.0,
            "maxdd_pct": 0.0,
            "trades": 0,
            "winrate": 0.0,
            "days": 0,
        }
    peak = eq.cummax()
    dd = float((eq / peak - 1).min() * 100)
    tr = np.array(trades) if trades else np.array([])
    days = max(1, (eq.index[-1] - eq.index[0]).days)
    return {
        "final": float(eq.iloc[-1]),
        "pnl": float(eq.iloc[-1] - start),
        "pnl_pct": float((eq.iloc[-1] / start - 1) * 100),
        "maxdd_pct": dd,
        "trades": int(len(tr)),
        "winrate": float((tr > 0).mean() * 100) if len(tr) else 0.0,
        "days": int(days),
        "start": eq.index[0].isoformat(),
        "end": eq.index[-1].isoformat(),
    }


def backtest_tendance(df5: pd.DataFrame) -> dict:
    """AAVE Tendance — 30m EMA20 + HTF + ER>=0.35, stop 2.5 ATR, trail 3 ATR."""
    df = df5.resample("30min").agg(
        {"open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"}
    ).dropna()
    close = df["close"].to_numpy()
    high = df["high"].to_numpy()
    low = df["low"].to_numpy()
    open_ = df["open"].to_numpy()
    ema = df["close"].ewm(span=20, adjust=False).mean().to_numpy()
    a = atr_series(df).to_numpy()
    scale = 240 // 30
    ema_f = df["close"].ewm(span=50 * scale, adjust=False).mean().to_numpy()
    ema_s = df["close"].ewm(span=200 * scale, adjust=False).mean().to_numpy()
    er = efficiency_ratio(df["close"]).to_numpy()

    n = len(df)
    pos = 0
    entry = stop = 0.0
    equity = START
    trades: list[float] = []
    eq = np.full(n, np.nan)
    er_min, stop_atr, trail_atr = 0.35, 2.5, 3.0

    for i in range(1, n - 1):
        px = close[i]
        if pos != 0:
            hit = (pos == 1 and low[i] <= stop) or (pos == -1 and high[i] >= stop)
            if hit:
                ret = pos * (stop / entry - 1) - 2 * COST_SIDE
                equity *= 1 + ret
                trades.append(ret)
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
            elif px < ema[i] and allow_short:
                pos, entry = -1, open_[i + 1]
                stop = entry + stop_atr * a[i]
        eq[i] = equity

    series = pd.Series(eq, index=df.index)
    out = stats_from_equity(series, trades)
    out["name"] = "AAVE Tendance"
    out["tag"] = "TENDANCE"
    out["params"] = "EMA20 30m + HTF + ER>=0.35"
    return out


def backtest_chasseur(end: pd.Timestamp) -> dict:
    """Chasseur Shorts — params live, dernière année jusqu'à `end`."""
    index, coins = load_universe()
    start = end - pd.Timedelta(days=365)
    mask = (index >= start) & (index <= end)
    idx = index[mask]
    # reindex coins already handled in run via full index — slice result period
    r = run_shorts(
        index,
        coins,
        entry_mode="breakdown",
        ema_len=50,
        low_n=120,
        er_min=0.35,
        stop_atr=3.0,
        trail_atr=4.0,
        max_pos=5,
        use_btc_filter="strict",
    )
    # run() uses full history from 2017 — we need last year only.
    # Re-run with truncated data:
    coins_y = {}
    for sym, df in coins.items():
        coins_y[sym] = df.reindex(idx).dropna(how="all")
    # Keep only bars present in BTC
    btc = coins_y["BTC"].dropna(subset=["close"])
    idx2 = btc.index
    coins_y = {s: d.reindex(idx2) for s, d in coins_y.items()}
    r = run_shorts(
        idx2,
        coins_y,
        entry_mode="breakdown",
        ema_len=50,
        low_n=120,
        er_min=0.35,
        stop_atr=3.0,
        trail_atr=4.0,
        max_pos=5,
        use_btc_filter="strict",
    )
    return {
        "name": "Chasseur Shorts",
        "tag": "CHASSEUR",
        "params": "breakdown low120 ER0.35 BTC bear strict K=5",
        "final": float(r["final"]),
        "pnl": float(r["final"] - START),
        "pnl_pct": float(r["total_pct"]),
        "maxdd_pct": float(r["maxdd_pct"]),
        "trades": int(r["trades"]),
        "winrate": float(r["winrate"]),
        "days": int((idx2[-1] - idx2[0]).days),
        "start": idx2[0].isoformat(),
        "end": idx2[-1].isoformat(),
    }


def main() -> None:
    df5 = load_aave_5m()
    print(f"AAVE 5m : {df5.index[0]} -> {df5.index[-1]} ({len(df5)} barres)")

    tendance = backtest_tendance(df5)
    chasseur = backtest_chasseur(df5.index[-1])

    bots = [tendance, chasseur]
    total_start = START * 2
    total_final = sum(b["final"] for b in bots)
    total_pnl = total_final - total_start

    print("\n=== BACKTEST 1 AN ===\n")
    for b in bots:
        print(
            f"[{b['tag']}] {b['name']}\n"
            f"  {b['params']}\n"
            f"  {START:.0f} -> {b['final']:.0f} USDT ({b['pnl_pct']:+.1f}%) | "
            f"PnL {b['pnl']:+.0f} | DD {b['maxdd_pct']:.1f}% | "
            f"{b['trades']} trades WR {b['winrate']:.0f}% | {b['days']}j\n"
        )

    print(
        f"FLOTTE (2x{START:.0f})\n"
        f"  {total_start:.0f} -> {total_final:.0f} USDT ({(total_final / total_start - 1) * 100:+.1f}%)\n"
        f"  PnL total : {total_pnl:+.0f} USDT\n"
    )

    # Projections (linéaire / composé sur le taux annuel observé)
    ann_retour = total_final / total_start - 1
    projections = []
    for years in (1, 2, 3):
        compounded = total_start * ((1 + ann_retour) ** years)
        projections.append(
            {
                "years": years,
                "if_same_return": round(compounded, 2),
                "pnl": round(compounded - total_start, 2),
            }
        )

    # Projection mensuelle moyenne
    months = max(1, max(b["days"] for b in bots) / 30.44)
    monthly_pnl = total_pnl / months

    result = {
        "period_aave": {"start": str(df5.index[0]), "end": str(df5.index[-1])},
        "start_per_bot": START,
        "bots": bots,
        "fleet": {
            "start": total_start,
            "final": round(total_final, 2),
            "pnl": round(total_pnl, 2),
            "pnl_pct": round((total_final / total_start - 1) * 100, 2),
            "monthly_pnl_avg": round(monthly_pnl, 2),
        },
        "projections_compound": projections,
        "disclaimer": (
            "Paper backtest historique — pas une garantie. "
            "Frais/funding réels peuvent différer."
        ),
    }
    OUT.write_text(json.dumps(result, indent=2), encoding="utf-8")
    print(f"Écrit : {OUT}")
    print(json.dumps(result["fleet"], indent=2))
    print("Projections (si même rendement annuel composé) :")
    for p in projections:
        print(f"  {p['years']} an(s) : {p['if_same_return']:.0f} USDT (PnL {p['pnl']:+.0f})")


if __name__ == "__main__":
    main()
