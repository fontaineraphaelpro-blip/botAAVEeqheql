import sys
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import pandas as pd
from scripts.backtest_optimize import build_signals, simulate

df = pd.read_csv(ROOT / "data/aave_5m_kucoin_6m.csv", parse_dates=["timestamp"])
df["timestamp"] = pd.to_datetime(df["timestamp"], utc=True)
hours = df["timestamp"].dt.hour
signals = build_signals(df)
filt = [s for s in signals if 12 <= hours.iloc[s.bar] < 22]

base = dict(
    signal_kinds={"EQH_SWEEP"},
    entry="confirm_next",
    min_score=0,
    min_zone_pct=0,
    max_zone_pct=0.2,
    side_filter="short",
    trend_mode="none",
    ema_span=50,
    min_body_ratio=0.65,
    sl_buffer=0.0006,
)

print("=== Affinage EQH SHORT (12h-22h UTC) ===")
best = None
for rr in [3, 4, 5, 6, 7, 8]:
    for hold in [6, 8, 12, 16, 24, 36]:
        m = simulate(df, filt, **base, rr=rr, max_hold=hold)
        if m["n"] >= 20 and m["pf"] >= 1.05:
            line = f"RR{rr} hold{hold}: n={m['n']} WR={m['wr']:.1f}% PnL={m['pnl']:+.1f}% PF={m['pf']:.2f}"
            print(line)
            if best is None or m["pnl"] > best[0]:
                best = (m["pnl"], m["pf"], rr, hold, m)

print("\n=== Walk-forward 50/50 ===")
mid = len(df) // 2
for label, part in [("Nov-Fev", df.iloc[:mid]), ("Fev-Mai", df.iloc[mid:])]:
    sig = build_signals(part)
    h = part["timestamp"].dt.hour
    hf = [s for s in sig if 12 <= h.iloc[s.bar] < 22]
    m = simulate(part, hf, **base, rr=4, max_hold=24)
    print(f"{label}: n={m['n']} WR={m['wr']:.1f}% PnL={m['pnl']:+.1f}% PF={m['pf']:.2f}")

print("\n=== EQL LONG (memes filtres) ===")
base2 = {**base, "signal_kinds": {"EQL_SWEEP"}, "side_filter": "long"}
m = simulate(df, filt, **base2, rr=4, max_hold=24)
print(f"6m: n={m['n']} WR={m['wr']:.1f}% PnL={m['pnl']:+.1f}% PF={m['pf']:.2f}")

if best:
    print(f"\nMeilleur PnL: RR{best[2]} hold{best[3]} -> {best[1]:.2f} PF, {best[0]:+.1f}%")
