"""Estime les alertes Telegram/jour avec les filtres actuels."""

from __future__ import annotations

import sys
from collections import defaultdict
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from config import get_config
from scanner.liquidity_detector import LiquidityDetector
from utils.signal_filter import FilterVerdict, filter_sweep, filter_sweep_confirm_bar, filter_zone


def main() -> None:
    config = get_config()
    filt = config.filter
    det = LiquidityDetector(config)
    min_bars = config.scan.min_bars

    df = pd.read_csv(ROOT / "data/aave_5m_kucoin_12m.csv", parse_dates=["timestamp"])
    df["timestamp"] = pd.to_datetime(df["timestamp"], utc=True)
    calendar_days = max(1, (df["timestamp"].iloc[-1] - df["timestamp"].iloc[0]).days)

    alert_days: dict[str, int] = defaultdict(int)
    raw_sweeps = 0
    filtered_alerts = 0
    pending: dict[str, tuple] = {}

    for i in range(min_bars, len(df)):
        sl = df.iloc[: i + 1]
        ts = int(sl["timestamp"].iloc[-1].timestamp())
        day = str(sl["timestamp"].iloc[-1].date())

        for zid in list(pending.keys()):
            z, _st, p_bar, p_exp = pending[zid]
            if i > p_exp:
                del pending[zid]
            elif i == p_bar + 1:
                del pending[zid]
                if filter_sweep_confirm_bar(z, sl, i, filt).verdict == FilterVerdict.PASS:
                    filtered_alerts += 1
                    alert_days[day] += 1

        r = det.process("AAVE/USDT", "5m", sl, ts)

        for z in r.new_zones:
            if filter_zone(z, sl, z.created_bar_index, filt).verdict == FilterVerdict.PASS:
                filtered_alerts += 1
                alert_days[day] += 1

        for z, st, bar in r.sweeps:
            raw_sweeps += 1
            fr = filter_sweep(z, st, sl, bar, filt)
            if fr.verdict == FilterVerdict.REJECT:
                continue
            if fr.verdict == FilterVerdict.PENDING:
                pending[z.zone_id] = (z, st, bar, bar + filt.sweep_confirm_max_bars)
                continue
            filtered_alerts += 1
            alert_days[day] += 1

    vals = list(alert_days.values())
    active_days = len(vals)
    avg_all = filtered_alerts / calendar_days
    avg_active = sum(vals) / active_days if active_days else 0

    print(f"Periode: {calendar_days} jours calendaires (KuCoin 12m)")
    print(f"Filtres: zones={filt.alert_zone_detection}, sweeps={filt.alert_sweeps}, confirm_next={filt.sweep_confirm_next_bar}")
    print(f"Score sweep>={filt.min_sweep_score}, zone<={filt.max_zone_width_pct}%, pivots>={filt.min_pivot_bars_apart}b")
    print(f"UTC hours: {'off' if not filt.utc_hours_enabled else f'{filt.utc_hour_start}-{filt.utc_hour_end}h'}")
    print()
    print(f"Sweeps BRUTS: {raw_sweeps} total (~{raw_sweeps/calendar_days:.1f}/jour)")
    print(f"Alertes FILTREES (Telegram): {filtered_alerts} total (~{avg_all:.1f}/jour sur calendrier)")
    print(f"  jours avec au moins 1 alerte: {active_days}/{calendar_days}")
    if vals:
        print(f"  quand il y en a: ~{avg_active:.1f}/jour | min={min(vals)} max={max(vals)}")
        print(f"  jours sans alerte: {calendar_days - active_days}")


if __name__ == "__main__":
    main()
