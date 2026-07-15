"""Compare 3 variantes — usage local uniquement, ne pas push."""

from __future__ import annotations

import sys
from dataclasses import dataclass
from pathlib import Path
from typing import List, Literal, Optional

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from config import get_config
from scanner.liquidity_detector import LiquidityDetector
from scripts.backtest import (
    SYMBOL,
    TF,
    BacktestResult,
    Trade,
    _sl_tp,
    fetch_kucoin_history,
)

Variant = Literal["v1_sweep", "v2_sweep_confirm", "v3_sweep_score"]


@dataclass
class PendingSignal:
    side: str
    alert_type: str
    zone: object
    sweep_bar: int
    expires_bar: int


def _close_trade(t: Trade, i: int, close: float, fee: float) -> None:
    t.exit_bar = i
    if t.side == "long":
        raw = (t.exit_price - t.entry_price) / t.entry_price * 100
    else:
        raw = (t.entry_price - t.exit_price) / t.entry_price * 100
    t.pnl_pct = raw - fee * 2


def run_variant(df: pd.DataFrame, variant: Variant, min_score: float = 55.0) -> BacktestResult:
    config = get_config()
    detector = LiquidityDetector(config)
    min_bars = config.scan.min_bars
    rr, max_hold, fee = 2.0, 48, 0.06

    trades: List[Trade] = []
    open_trade: Optional[Trade] = None
    pending: Optional[PendingSignal] = None

    labels = {
        "v1_sweep": "V1 — Sweep mecanique (baseline)",
        "v2_sweep_confirm": "V2 — Sweep + rejet (close sous/au-dessus niveau)",
        "v3_sweep_score": f"V3 — Sweep + score >= {min_score}",
    }

    for i in range(min_bars, len(df)):
        slice_df = df.iloc[: i + 1].copy()
        ts = int(slice_df["timestamp"].iloc[-1].timestamp())
        result = detector.process(SYMBOL, TF, slice_df, ts)

        bar = df.iloc[i]
        high, low, close = float(bar["high"]), float(bar["low"]), float(bar["close"])

        if open_trade is not None:
            t = open_trade
            if t.side == "long":
                if low <= t.sl:
                    t.exit_price, t.exit_reason = t.sl, "SL"
                elif high >= t.tp:
                    t.exit_price, t.exit_reason = t.tp, "TP"
            else:
                if high >= t.sl:
                    t.exit_price, t.exit_reason = t.sl, "SL"
                elif low <= t.tp:
                    t.exit_price, t.exit_reason = t.tp, "TP"
            if not t.exit_reason and i - t.entry_bar >= max_hold:
                t.exit_price, t.exit_reason = close, "TIME"
            if t.exit_reason:
                _close_trade(t, i, close, fee)
                trades.append(t)
                open_trade = None

        if open_trade is not None:
            continue

        # V2 : entree sur bougie de confirmation apres sweep
        if variant == "v2_sweep_confirm" and pending is not None:
            if i > pending.expires_bar:
                pending = None
            else:
                z = pending.zone
                ok = (
                    pending.side == "short" and close < z.sweep_level
                ) or (pending.side == "long" and close > z.sweep_level)
                if ok:
                    sl, tp = _sl_tp(pending.side, close, z.top, z.bottom, high, low)
                    if pending.side == "short":
                        tp = close - rr * (sl - close)
                    else:
                        tp = close + rr * (close - sl)
                    open_trade = Trade(
                        side=pending.side,
                        alert_type=pending.alert_type + "_confirm",
                        entry_bar=i,
                        entry_price=close,
                        sl=sl,
                        tp=tp,
                    )
                pending = None

        if open_trade is not None:
            continue

        for zone, stype in result.sweeps:
            side = "short" if stype == "EQH_SWEEP" else "long"

            if variant == "v3_sweep_score" and zone.score < min_score:
                continue

            if variant == "v2_sweep_confirm":
                pending = PendingSignal(
                    side=side,
                    alert_type=stype,
                    zone=zone,
                    sweep_bar=i,
                    expires_bar=i + 3,
                )
                break

            # V1 et V3 : entree immediate au sweep
            entry = close
            sl, tp = _sl_tp(side, entry, zone.top, zone.bottom, high, low)
            if side == "short":
                tp = entry - rr * (sl - entry)
            else:
                tp = entry + rr * (entry - sl)
            open_trade = Trade(
                side=side,
                alert_type=stype,
                entry_bar=i,
                entry_price=entry,
                sl=sl,
                tp=tp,
            )
            break

    r = BacktestResult(trades=trades, mode=labels[variant])
    return r


async def main() -> None:
    import asyncio

    cache = ROOT / "data" / "aave_5m_kucoin_6m.csv"
    if cache.exists():
        df = pd.read_csv(cache, parse_dates=["timestamp"])
        df["timestamp"] = pd.to_datetime(df["timestamp"], utc=True)
        print(f"[cache] {len(df)} bougies\n")
    else:
        df = await fetch_kucoin_history(6)

    variants: list[Variant] = ["v1_sweep", "v2_sweep_confirm", "v3_sweep_score"]
    rows = []

    for v in variants:
        print(f"Calcul {v}...")
        bt = run_variant(df, v)
        bt.months = 6
        closed = [t for t in bt.trades if t.exit_bar >= 0]
        if not closed:
            rows.append((v, 0, 0.0, 0.0, 0.0))
            continue
        wins = sum(1 for t in closed if t.pnl_pct > 0)
        wr = wins / len(closed) * 100
        pnl = sum(t.pnl_pct for t in closed)
        gp = sum(t.pnl_pct for t in closed if t.pnl_pct > 0)
        gl = abs(sum(t.pnl_pct for t in closed if t.pnl_pct <= 0))
        pf = gp / gl if gl else 0
        rows.append((bt.mode, len(closed), wr, pnl, pf))
        print(bt.summary())

    print("\n" + "=" * 70)
    print("TABLEAU COMPARATIF — 6 mois AAVE 5m KuCoin")
    print("=" * 70)
    print(f"{'Variante':<45} {'Trades':>7} {'Win%':>7} {'PnL%':>9} {'PF':>6}")
    print("-" * 70)
    for name, n, wr, pnl, pf in rows:
        short = name[:44]
        print(f"{short:<45} {n:>7} {wr:>6.1f}% {pnl:>+8.1f}% {pf:>6.2f}")
    print("=" * 70)


if __name__ == "__main__":
    import asyncio
    asyncio.run(main())
