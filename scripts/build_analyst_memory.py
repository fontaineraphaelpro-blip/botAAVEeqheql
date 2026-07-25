"""Construit la mémoire de motifs AAVE Analyst depuis le CSV historique.

Usage:
  python scripts/build_analyst_memory.py
  python scripts/build_analyst_memory.py --csv data/aave_5m_kucoin_12m.csv --stride 3
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from config import get_config
from trading.analyst_features import FeatureParams
from trading.analyst_memory import PatternMemory


def load_csv(path: Path) -> pd.DataFrame:
    df = pd.read_csv(path, parse_dates=["timestamp"])
    df = df.set_index("timestamp").sort_index()
    if df.index.tz is None:
        df.index = df.index.tz_localize("UTC")
    return df


def main() -> None:
    cfg = get_config().analyst
    ap = argparse.ArgumentParser()
    ap.add_argument(
        "--csv",
        default=str(ROOT / "data" / "aave_5m_kucoin_12m.csv"),
        help="OHLCV AAVE 5m",
    )
    ap.add_argument("--out", default=cfg.memory_file)
    ap.add_argument("--stride", type=int, default=3)
    ap.add_argument("--lookback", type=int, default=cfg.lookback)
    ap.add_argument("--horizon", type=int, default=cfg.horizon)
    ap.add_argument("--flat-pct", type=float, default=cfg.flat_pct)
    args = ap.parse_args()

    csv_path = Path(args.csv)
    if not csv_path.exists():
        raise SystemExit(f"CSV introuvable: {csv_path}")

    params = FeatureParams(
        lookback=args.lookback,
        horizon=args.horizon,
        flat_pct=args.flat_pct,
    )
    df = load_csv(csv_path)
    print(f"CSV {csv_path.name}: {len(df)} barres ({df.index[0]} -> {df.index[-1]})")

    mem = PatternMemory.build_from_df(
        df,
        params,
        stride=args.stride,
        source=csv_path.name,
    )
    out = Path(args.out)
    mem.save(out)
    labels = mem.labels
    n_up = int((labels == 1).sum())
    n_dn = int((labels == -1).sum())
    n_fl = int((labels == 0).sum())
    print(
        f"Memoire: {mem.size} motifs -> {out}\n"
        f"  UP={n_up} DOWN={n_dn} FLAT={n_fl}\n"
        f"  lookback={params.lookback} horizon={params.horizon} "
        f"flat={params.flat_pct}% stride={args.stride}"
    )

    # Smoke query sur la dernière fenêtre
    window = df.iloc[-params.lookback :]
    from trading.analyst_features import encode_window

    vec = encode_window(window, params)
    pred = mem.query(vec, top_k=cfg.top_k, max_distance=cfg.max_distance)
    print(
        f"Smoke dernière barre: {pred.label} conf={pred.confidence:.2f} "
        f"n={pred.n_matches} avg={pred.avg_fwd_pct:+.2f}% dist={pred.distance:.3f}"
    )


if __name__ == "__main__":
    main()
