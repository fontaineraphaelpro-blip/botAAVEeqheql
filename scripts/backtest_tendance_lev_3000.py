"""Backtest AAVE Tendance — 3000€, leviers 1/5/10/15/20, 1 an.

Même logique live : EMA20 30m + HTF 4h + ER>=0.35, stop 2.5 ATR, trail 3 ATR.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
from backtest_ema_flip4 import load  # noqa: E402
from backtest_leverage import run_trades  # noqa: E402

START = 3000.0
LEVERS = (1, 5, 10, 15, 20)
OUT = Path(__file__).resolve().parents[1] / "data" / "backtest_tendance_lev_3000.json"
LIQ_MARGIN = 0.90  # liquidation si perte latente >= 90% de la marge


def simulate(trades: np.ndarray, worst: np.ndarray, leverage: float, start: float = START) -> dict:
    eq = start
    peak = eq
    maxdd = 0.0
    liquidated = False
    liq_trade = None
    n_done = 0

    for i, (r, w) in enumerate(zip(trades, worst)):
        # excursion adverse * levier sur la marge
        if w * leverage <= -LIQ_MARGIN:
            liquidated = True
            liq_trade = i + 1
            eq = 0.0
            break
        eq *= 1 + leverage * r
        n_done += 1
        if eq <= 0:
            liquidated = True
            liq_trade = i + 1
            eq = 0.0
            break
        peak = max(peak, eq)
        maxdd = min(maxdd, eq / peak - 1)

    pnl = eq - start
    return {
        "leverage": leverage,
        "final": round(eq, 2),
        "pnl": round(pnl, 2),
        "pnl_pct": round(pnl / start * 100, 2),
        "maxdd_pct": round(maxdd * 100, 2),
        "liquidated": liquidated,
        "liq_at_trade": liq_trade,
        "trades_done": n_done,
        "monthly_pnl": round(pnl / 12, 2),
        "daily_pnl": None,  # filled later
    }


def main() -> None:
    df = load("30min")
    days = max(1, (df.index[-1] - df.index[0]).days)
    trades, worst = run_trades(df, "30min", 20, 2.5, 3.0, 0.35)
    trades_a = np.array(trades)
    worst_a = np.array(worst[: len(trades_a)])

    print(f"AAVE Tendance — {df.index[0].date()} -> {df.index[-1].date()} ({days}j)")
    print(f"Trades spot : {len(trades_a)} | WR {(trades_a > 0).mean()*100:.0f}%")
    print(f"Retour spot moyen/trade : {trades_a.mean()*100:+.3f}%")
    print(f"Pire trade spot : {trades_a.min()*100:.2f}% | pire excursion : {worst_a.min()*100:.2f}%")
    print(f"Capital départ : {START:.0f} €\n")

    rows = []
    print(
        f"{'Lev':>5} {'Final':>12} {'PnL':>12} {'%':>8} {'MaxDD':>8} "
        f"{'€/mois':>10} {'Liq?':>6}"
    )
    for L in LEVERS:
        r = simulate(trades_a, worst_a, L)
        r["daily_pnl"] = round(r["pnl"] / days, 2)
        r["period_days"] = days
        r["n_trades_spot"] = len(trades_a)
        rows.append(r)
        liq = f"OUI@{r['liq_at_trade']}" if r["liquidated"] else "non"
        print(
            f"{L:>4}x {r['final']:>11.0f}€ {r['pnl']:>+11.0f}€ {r['pnl_pct']:>+7.1f}% "
            f"{r['maxdd_pct']:>7.1f}% {r['monthly_pnl']:>+9.0f}€ {liq:>6}"
        )

    # projections 2-3 ans si pas liquidé (composé sur rendement annuel observé)
    projections = []
    for r in rows:
        if r["liquidated"] or r["final"] <= 0:
            projections.append(
                {"leverage": r["leverage"], "y1": 0, "y2": 0, "y3": 0, "note": "liquidé"}
            )
            continue
        ann = r["final"] / START
        projections.append(
            {
                "leverage": r["leverage"],
                "y1": round(r["final"], 2),
                "y2": round(START * ann**2, 2),
                "y3": round(START * ann**3, 2),
                "note": "ok",
            }
        )

    print("\nProjection composé (si même année se répète) :")
    print(f"{'Lev':>5} {'1 an':>12} {'2 ans':>12} {'3 ans':>12}")
    for p in projections:
        if p["note"] == "liquidé":
            print(f"{p['leverage']:>4}x {'LIQUIDÉ':>12} {'—':>12} {'—':>12}")
        else:
            print(
                f"{p['leverage']:>4}x {p['y1']:>11.0f}€ {p['y2']:>11.0f}€ {p['y3']:>11.0f}€"
            )

    out = {
        "bot": "AAVE Tendance",
        "start_eur": START,
        "period": {"start": str(df.index[0]), "end": str(df.index[-1]), "days": days},
        "params": "EMA20 30m + HTF + ER>=0.35 | stop 2.5 ATR | trail 3 ATR",
        "spot_trades": len(trades_a),
        "spot_winrate": round(float((trades_a > 0).mean() * 100), 1),
        "results": rows,
        "projections": projections,
        "disclaimer": "Paper backtest — levier amplifie gains et liquidations. Pas une garantie.",
    }
    OUT.write_text(json.dumps(out, indent=2), encoding="utf-8")
    print(f"\nÉcrit : {OUT}")


if __name__ == "__main__":
    main()
