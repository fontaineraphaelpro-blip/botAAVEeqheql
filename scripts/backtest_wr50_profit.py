import pickle, sys
from pathlib import Path
from itertools import product
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import pandas as pd
from scripts.backtest_wr50_search import simulate_v2

df = pd.read_csv(ROOT / "data/aave_5m_kucoin_12m.csv", parse_dates=["timestamp"])
df["timestamp"] = pd.to_datetime(df["timestamp"], utc=True)
hours = df["timestamp"].dt.hour
signals = pickle.loads((ROOT / "data/signals_12m.pkl").read_bytes())

good = []
for kinds, side, entry, rr, hold, zmax, body, hrs, tpm, ftp in product(
    [{"EQH_SWEEP"}, {"EQL_SWEEP"}],
    ["short", "long"],
    ["confirm_next", "confirm_same", "immediate"],
    [0.6, 0.8, 1.0, 1.2, 1.5, 2.0],
    [8, 12, 18, 24, 36, 48],
    [0.15, 0.25, 0.5],
    [0.0, 0.55, 0.65],
    [(12, 22), (0, 24), (8, 20)],
    ["rr", "mid", "fixed"],
    [0.5, 0.8, 1.0, 1.2, 1.5],
):
    if kinds == {"EQH_SWEEP"} and side == "long":
        continue
    if kinds == {"EQL_SWEEP"} and side == "short":
        continue
    if tpm == "rr" and ftp != 0.5:
        continue
    if tpm in ("fixed", "mid") and rr != 1.0:
        continue
    m = simulate_v2(
        df, signals, hours,
        signal_kinds=set(kinds), entry=entry, side_filter=side, rr=rr,
        max_hold=hold, max_zone_pct=zmax, min_body=body, min_score=0,
        trend="none", hour_rng=hrs, tp_mode=tpm, fixed_tp_pct=ftp,
        sl_buffer=0.0005, use_detect=False,
    )
    if m["n"] >= 40 and m["wr"] >= 50 and m["pnl"] > 0 and m["pf"] >= 1.0:
        good.append((m["wr"], m["pnl"], m["pf"], m["n"], kinds, side, entry, tpm, rr, ftp, hold, body, hrs))

good.sort(key=lambda x: (x[0], x[1]), reverse=True)
print("WR>=50% + PnL>0 + PF>=1 + n>=40:", len(good))
for r in good[:20]:
    print(
        f"WR {r[0]:.1f}% | PnL {r[1]:+.1f}% | PF {r[2]:.2f} | n={r[3]} | "
        f"{r[4]} {r[5]} | {r[6]} | tp={r[7]} rr={r[8]} ftp={r[9]}% hold={r[10]} body>={r[11]} h{r[12]}"
    )

if not good:
    print("\nMeilleur compromis WR>=50% (meme si PnL<0) deja connu: ~70% WR scalping EQL long tp 0.2%")
    print("Recherche WR>=48% PnL>0...")
    alt = []
    for rr, hold, body in product([0.8, 1.0, 1.2, 1.5, 2.0], [12, 24, 36], [0.55, 0.65]):
        m = simulate_v2(
            df, signals, hours,
            signal_kinds={"EQH_SWEEP"}, entry="confirm_next", side_filter="short", rr=rr,
            max_hold=hold, max_zone_pct=0.2, min_body=body, min_score=0,
            trend="none", hour_rng=(12, 22), tp_mode="rr", fixed_tp_pct=0.5,
            sl_buffer=0.0005, use_detect=False,
        )
        if m["n"] >= 30 and m["pnl"] > 0:
            alt.append((m["wr"], m["pnl"], m["pf"], m["n"], rr, hold, body))
    alt.sort(key=lambda x: (x[0], x[1]), reverse=True)
    for r in alt[:15]:
        print(f"WR {r[0]:.1f}% PnL {r[1]:+.1f}% PF {r[2]:.2f} n={r[3]} | EQH short rr={r[4]} hold={r[5]} body>={r[6]}")
