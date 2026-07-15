"""Backtest : 1 trade par alerte Telegram (filtres bot actuels)."""

from __future__ import annotations

import sys
from dataclasses import dataclass
from pathlib import Path
from typing import List

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from config import get_config
from models.liquidity_zone import ZoneType
from scanner.liquidity_detector import LiquidityDetector
from utils.signal_filter import FilterVerdict, filter_sweep, filter_sweep_confirm_bar, filter_zone

SYMBOL, TF = "AAVE/USDT", "5m"
FEE = 0.12  # % aller-retour approx


@dataclass
class LiveAlert:
    bar: int
    kind: str  # EQH_SWEEP, EQL_SWEEP, EQH, EQL
    side: str
    top: float
    bottom: float
    sweep_level: float
    score: float


def collect_alerts(df: pd.DataFrame) -> List[LiveAlert]:
    config = get_config()
    filt = config.filter
    det = LiquidityDetector(config)
    min_bars = config.scan.min_bars
    out: List[LiveAlert] = []
    pending: dict[str, tuple] = {}

    for i in range(min_bars, len(df)):
        sl = df.iloc[: i + 1]
        ts = int(sl["timestamp"].iloc[-1].timestamp())

        for zid in list(pending.keys()):
            z, st, p_bar, p_exp = pending[zid]
            if i > p_exp:
                del pending[zid]
            elif i == p_bar + 1:
                del pending[zid]
                if filter_sweep_confirm_bar(z, sl, i, filt).verdict == FilterVerdict.PASS:
                    side = "short" if st == "EQH_SWEEP" else "long"
                    out.append(
                        LiveAlert(i, st, side, z.top, z.bottom, z.sweep_level, z.score)
                    )

        r = det.process(SYMBOL, TF, sl, ts)

        for z in r.new_zones:
            if filter_zone(z, sl, z.created_bar_index, filt).verdict != FilterVerdict.PASS:
                continue
            side = "short" if z.zone_type == ZoneType.EQH else "long"
            kind = z.zone_type.value
            out.append(LiveAlert(i, kind, side, z.top, z.bottom, z.sweep_level, z.score))

        for z, st, bar in r.sweeps:
            fr = filter_sweep(z, st, sl, bar, filt)
            if fr.verdict == FilterVerdict.REJECT:
                continue
            if fr.verdict == FilterVerdict.PENDING:
                pending[z.zone_id] = (z, st, bar, bar + filt.sweep_confirm_max_bars)
                continue
            side = "short" if st == "EQH_SWEEP" else "long"
            out.append(LiveAlert(bar, st, side, z.top, z.bottom, z.sweep_level, z.score))

    return out


def simulate_trades(
    df: pd.DataFrame,
    alerts: List[LiveAlert],
    *,
    rr: float = 2.0,
    max_hold: int = 24,
    sl_buffer: float = 0.0006,
    one_at_a_time: bool = True,
) -> dict:
    closes = df["close"].to_numpy()
    highs = df["high"].to_numpy()
    lows = df["low"].to_numpy()
    n = len(df)
    pnls: List[float] = []
    busy_until = 0

    for a in alerts:
        if a.bar >= n - 1:
            continue
        if one_at_a_time and a.bar < busy_until:
            continue

        entry_bar = a.bar
        entry_price = float(closes[entry_bar])

        if a.side == "short":
            sl = max(a.top, float(highs[entry_bar])) * (1 + sl_buffer)
            risk = sl - entry_price
            if risk <= 0:
                risk = entry_price * 0.003
                sl = entry_price + risk
            tp = entry_price - rr * risk
        else:
            sl = min(a.bottom, float(lows[entry_bar])) * (1 - sl_buffer)
            risk = entry_price - sl
            if risk <= 0:
                risk = entry_price * 0.003
                sl = entry_price - risk
            tp = entry_price + rr * risk

        exit_pnl = None
        for j in range(entry_bar + 1, min(entry_bar + max_hold + 1, n)):
            hi, lo = float(highs[j]), float(lows[j])
            if a.side == "long":
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
            if a.side == "long":
                exit_pnl = (cl - entry_price) / entry_price * 100 - FEE
            else:
                exit_pnl = (entry_price - cl) / entry_price * 100 - FEE

        if exit_pnl is not None:
            pnls.append(exit_pnl)
            if one_at_a_time:
                busy_until = entry_bar + max_hold + 1

    if not pnls:
        return {"n": 0, "wr": 0, "pnl": 0, "pf": 0, "avg": 0}

    wins = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p <= 0]
    gp = sum(wins) if wins else 0
    gl = abs(sum(losses)) if losses else 1e-9
    return {
        "n": len(pnls),
        "wr": len(wins) / len(pnls) * 100,
        "pnl": sum(pnls),
        "pf": gp / gl if gl else 0,
        "avg": sum(pnls) / len(pnls),
    }


def print_report(label: str, df: pd.DataFrame, alerts: List[LiveAlert]) -> None:
    days = max(1, (df["timestamp"].iloc[-1] - df["timestamp"].iloc[0]).days)
    kinds = {}
    for a in alerts:
        kinds[a.kind] = kinds.get(a.kind, 0) + 1

    print(f"\n{'='*60}")
    print(label)
    print(f"{'='*60}")
    print(f"Periode: {df['timestamp'].iloc[0].date()} -> {df['timestamp'].iloc[-1].date()} ({days} j)")
    print(f"Alertes filtrees: {len(alerts)} (~{len(alerts)/days:.1f}/jour)")
    for k, v in sorted(kinds.items()):
        print(f"  - {k}: {v}")

    print("\n--- Entree au CLOSE de l'alerte | 1 position max | frais {FEE}% ---")
    for rr in (2.0, 3.0, 4.0):
        for hold in (12, 24, 48):
            m = simulate_trades(df, alerts, rr=rr, max_hold=hold)
            if m["n"] < 10:
                continue
            tag = "OK" if m["pnl"] > 0 and m["pf"] >= 1.0 else "  "
            print(
                f"{tag} RR{rr:.0f} hold{hold:2d} bars: "
                f"n={m['n']:4d} WR={m['wr']:5.1f}% PnL={m['pnl']:+7.1f}% "
                f"PF={m['pf']:.2f} avg={m['avg']:+.3f}%/trade"
            )

    m = simulate_trades(df, alerts, rr=2.0, max_hold=24, one_at_a_time=False)
    print(
        f"\nTous signaux en parallele (RR2 hold24): "
        f"n={m['n']} WR={m['wr']:.1f}% PnL={m['pnl']:+.1f}% PF={m['pf']:.2f}"
    )

    shorts = [a for a in alerts if a.side == "short"]
    longs = [a for a in alerts if a.side == "long"]
    for name, sub in [("SHORT (EQH sweep)", shorts), ("LONG (EQL sweep)", longs)]:
        m = simulate_trades(df, sub, rr=2.0, max_hold=24)
        if m["n"]:
            print(
                f"  {name}: n={m['n']} WR={m['wr']:.1f}% PnL={m['pnl']:+.1f}% PF={m['pf']:.2f}"
            )


def main() -> None:
    cfg = get_config().filter
    print("Config bot actuelle:")
    print(f"  zones={cfg.alert_zone_detection} sweeps={cfg.alert_sweeps} confirm_next={cfg.sweep_confirm_next_bar}")
    print(f"  score>={cfg.min_sweep_score} zone<={cfg.max_zone_width_pct}% pivots>={cfg.min_pivot_bars_apart}")

    for name, path in [
        ("6 MOIS", ROOT / "data/aave_5m_kucoin_6m.csv"),
        ("12 MOIS", ROOT / "data/aave_5m_kucoin_12m.csv"),
    ]:
        if not path.exists():
            print(f"\nSkip {name}: {path.name} absent")
            continue
        df = pd.read_csv(path, parse_dates=["timestamp"])
        df["timestamp"] = pd.to_datetime(df["timestamp"], utc=True)
        print(f"\nCollecte alertes {name}...")
        alerts = collect_alerts(df)
        print_report(name, df, alerts)


if __name__ == "__main__":
    main()
