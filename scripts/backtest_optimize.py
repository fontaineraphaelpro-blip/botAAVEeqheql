"""Recherche de parametres rentables — local only."""

from __future__ import annotations

import sys
from dataclasses import dataclass
from itertools import product
from pathlib import Path
from typing import List, Literal, Optional

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from config import get_config
from models.liquidity_zone import ZoneType
from scanner.liquidity_detector import LiquidityDetector

SYMBOL, TF = "AAVE/USDT", "5m"
FEE = 0.12


@dataclass
class Signal:
    bar: int
    kind: str  # EQH_SWEEP, EQL_SWEEP, EQH_detect, EQL_detect
    side: str
    top: float
    bottom: float
    sweep_level: float
    score: float
    high: float
    low: float
    close: float


def ema(series: pd.Series, span: int) -> pd.Series:
    return series.ewm(span=span, adjust=False).mean()


def build_signals(df: pd.DataFrame) -> List[Signal]:
    config = get_config()
    det = LiquidityDetector(config)
    min_bars = config.scan.min_bars
    out: List[Signal] = []

    for i in range(min_bars, len(df)):
        sl = df.iloc[: i + 1]
        ts = int(sl["timestamp"].iloc[-1].timestamp())
        r = det.process(SYMBOL, TF, sl, ts)
        row = df.iloc[i]
        h, lo, c = float(row["high"]), float(row["low"]), float(row["close"])

        for z in r.new_zones:
            side = "short" if z.zone_type == ZoneType.EQH else "long"
            out.append(Signal(i, f"{z.zone_type.value}_detect", side, z.top, z.bottom, z.sweep_level, z.score, h, lo, c))

        for z, st in r.sweeps:
            side = "short" if st == "EQH_SWEEP" else "long"
            out.append(Signal(i, st, side, z.top, z.bottom, z.sweep_level, z.score, h, lo, c))
    return out


def simulate(
    df: pd.DataFrame,
    signals: List[Signal],
    *,
    signal_kinds: set[str],
    entry: Literal["immediate", "confirm_next", "confirm_same"],
    min_score: float,
    min_zone_pct: float,
    max_zone_pct: float,
    side_filter: Literal["both", "short", "long"],
    trend_mode: Literal["none", "counter_ema", "with_ema"],
    ema_span: int,
    min_body_ratio: float,
    rr: float,
    max_hold: int,
    sl_buffer: float,
) -> dict:
    closes = df["close"].to_numpy()
    highs = df["high"].to_numpy()
    lows = df["low"].to_numpy()
    opens = df["open"].to_numpy()
    ema_line = ema(df["close"], ema_span).to_numpy()

    trades_pnl: List[float] = []
    n = len(df)
    busy_until = 0
    signals_by_bar: dict[int, list[Signal]] = {}
    for s in signals:
        if s.kind in signal_kinds:
            signals_by_bar.setdefault(s.bar, []).append(s)

    i = 0
    while i < n:
        if i < busy_until:
            i += 1
            continue

        sigs_at_i = signals_by_bar.get(i, [])
        if not sigs_at_i:
            i += 1
            continue

        entered = False
        for s in sigs_at_i:
            if s.score < min_score:
                continue
            zone_pct = (s.top - s.bottom) / s.sweep_level * 100
            if zone_pct < min_zone_pct or zone_pct > max_zone_pct:
                continue
            if side_filter == "short" and s.side != "short":
                continue
            if side_filter == "long" and s.side != "long":
                continue

            entry_bar = i
            entry_price = s.close

            if entry == "confirm_next":
                if i + 1 >= n:
                    continue
                nb = i + 1
                nc = float(closes[nb])
                if s.side == "short" and nc >= s.sweep_level:
                    continue
                if s.side == "long" and nc <= s.sweep_level:
                    continue
                entry_bar, entry_price = nb, nc
            elif entry == "confirm_same":
                if s.side == "short" and s.close >= s.sweep_level:
                    continue
                if s.side == "long" and s.close <= s.sweep_level:
                    continue

            if trend_mode == "with_ema":
                e = ema_line[entry_bar]
                if s.side == "long" and entry_price < e:
                    continue
                if s.side == "short" and entry_price > e:
                    continue
            elif trend_mode == "counter_ema":
                e = ema_line[entry_bar]
                if s.side == "short" and entry_price < e:
                    continue
                if s.side == "long" and entry_price > e:
                    continue

            if min_body_ratio > 0:
                b = entry_bar
                body = abs(float(closes[b]) - float(opens[b]))
                rng = float(highs[b]) - float(lows[b])
                if rng <= 0 or body / rng < min_body_ratio:
                    continue

            if s.side == "short":
                sl = max(s.top, float(highs[entry_bar])) * (1 + sl_buffer)
                risk = sl - entry_price
                if risk <= 0:
                    risk = entry_price * 0.003
                    sl = entry_price + risk
                tp = entry_price - rr * risk
            else:
                sl = min(s.bottom, float(lows[entry_bar])) * (1 - sl_buffer)
                risk = entry_price - sl
                if risk <= 0:
                    risk = entry_price * 0.003
                    sl = entry_price - risk
                tp = entry_price + rr * risk

            exit_pnl = None
            for j in range(entry_bar + 1, min(entry_bar + max_hold + 1, n)):
                hi, lo, cl = float(highs[j]), float(lows[j]), float(closes[j])
                if s.side == "long":
                    if lo <= sl:
                        exit_pnl = (sl - entry_price) / entry_price * 100 - FEE
                        break
                    if hi >= tp:
                        exit_pnl = (tp - entry_price) / entry_price * 100 - FEE
                        break
                else:
                    if hi >= sl:
                        exit_pnl = (entry_price - sl) / entry_price * 100 - FEE
                        break
                    if lo <= tp:
                        exit_pnl = (entry_price - tp) / entry_price * 100 - FEE
                        break
            if exit_pnl is None and entry_bar + max_hold < n:
                cl = float(closes[entry_bar + max_hold])
                if s.side == "long":
                    exit_pnl = (cl - entry_price) / entry_price * 100 - FEE
                else:
                    exit_pnl = (entry_price - cl) / entry_price * 100 - FEE

            if exit_pnl is not None:
                trades_pnl.append(exit_pnl)
                busy_until = entry_bar + max_hold + 1
                entered = True
                break
        if not entered:
            i += 1
        else:
            i = busy_until

    if len(trades_pnl) < 30:
        return {"n": len(trades_pnl), "wr": 0, "pnl": -999, "pf": 0, "avg": 0}

    wins = [p for p in trades_pnl if p > 0]
    losses = [p for p in trades_pnl if p <= 0]
    wr = len(wins) / len(trades_pnl) * 100
    pnl = sum(trades_pnl)
    gp = sum(wins) if wins else 0
    gl = abs(sum(losses)) if losses else 1e-9
    pf = gp / gl
    return {"n": len(trades_pnl), "wr": wr, "pnl": pnl, "pf": pf, "avg": pnl / len(trades_pnl)}


def main() -> None:
    cache = ROOT / "data" / "aave_5m_kucoin_6m.csv"
    df = pd.read_csv(cache, parse_dates=["timestamp"])
    df["timestamp"] = pd.to_datetime(df["timestamp"], utc=True)
    print(f"Donnees: {len(df)} bougies\nGeneration signaux...")
    signals = build_signals(df)
    sweeps = [s for s in signals if "SWEEP" in s.kind]
    print(f"Signaux total: {len(signals)} | sweeps: {len(sweeps)}\n")

    results = []

    sweep_kinds = {"EQH_SWEEP", "EQL_SWEEP"}
    configs = []

    for entry, rr, max_hold, min_score, trend, side_f, zone_max, body_r in product(
        ["confirm_next", "confirm_same"],
        [2.0, 2.5, 3.0, 4.0],
        [12, 24, 36, 48],
        [0, 60, 70],
        ["none", "counter_ema"],
        ["both", "short", "long"],
        [0.2, 0.6],
        [0.0, 0.5],
    ):
        configs.append(
            {
                "signal_kinds": sweep_kinds,
                "entry": entry,
                "min_score": min_score,
                "min_zone_pct": 0,
                "max_zone_pct": zone_max,
                "side_filter": side_f,
                "trend_mode": trend,
                "ema_span": 50,
                "min_body_ratio": body_r,
                "rr": rr,
                "max_hold": max_hold,
                "sl_buffer": 0.0008,
            }
        )

    print(f"Test de {len(configs)} combinaisons...")
    for idx, cfg in enumerate(configs):
        m = simulate(df, signals, **cfg)
        if m["n"] >= 40 and m["pf"] >= 1.0:
            results.append((m["pnl"], m["pf"], m["wr"], m["n"], cfg))
        if (idx + 1) % 200 == 0:
            print(f"  {idx+1}/{len(configs)}...")

    results.sort(key=lambda x: (x[0], x[1]), reverse=True)

    print("\n" + "=" * 72)
    print("TOP 15 — PnL cumule (sweeps, min 40 trades, PF >= 1)")
    print("=" * 72)
    if not results:
        print("Aucune config PF>=1 avec 40+ trades. Elargissement...\n")
        all_r = []
        for cfg in configs:
            m = simulate(df, signals, **cfg)
            if m["n"] >= 25:
                all_r.append((m["pnl"], m["pf"], m["wr"], m["n"], cfg))
        all_r.sort(key=lambda x: (x[1], x[0]), reverse=True)
        results = all_r[:15]

    for pnl, pf, wr, n, cfg in results[:15]:
        print(
            f"PnL {pnl:+.1f}% | PF {pf:.2f} | WR {wr:.1f}% | n={n} | "
            f"entry={cfg['entry']} rr={cfg['rr']} hold={cfg['max_hold']} "
            f"score>={cfg['min_score']} trend={cfg['trend_mode']} side={cfg['side_filter']} "
            f"zone<={cfg['max_zone_pct']}% body>={cfg['min_body_ratio']}"
        )

    if results and results[0][1] >= 1.0:
        best = results[0]
        print("\n" + "=" * 72)
        print("MEILLEURE CONFIG (re-test detaille)")
        print("=" * 72)
        m = simulate(df, signals, **best[4])
        print(f"Trades: {m['n']} | Win rate: {m['wr']:.1f}% | PnL: {m['pnl']:+.2f}% | PF: {m['pf']:.2f}")
        print(f"PnL moyen/trade: {m['avg']:+.3f}%")
        print(f"Params: {best[4]}")

        # split EQH vs EQL on best config
        for kind_only in [{"EQH_SWEEP"}, {"EQL_SWEEP"}]:
            c2 = {**best[4], "signal_kinds": kind_only}
            m2 = simulate(df, signals, **c2)
            print(f"  {kind_only}: n={m2['n']} wr={m2['wr']:.1f}% pnl={m2['pnl']:+.1f}% pf={m2['pf']:.2f}")
    else:
        print("\nAucune strategie hyper rentable trouvee sur 6 mois avec cette grille.")
        print("Meilleur compromis PF:")
        if results:
            pnl, pf, wr, n, cfg = results[0]
            print(f"  PnL {pnl:+.1f}% PF {pf:.2f} WR {wr:.1f}% n={n}")


if __name__ == "__main__":
    main()
