"""Construit mémoire de motifs + livre de corrélations (indicateurs).

Usage:
  python scripts/build_analyst_memory.py
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from config import get_config
from trading.analyst_correlations import CorrelationBook
from trading.analyst_features import (
    FeatureParams,
    encode_window,
    enrich,
    forward_return_pct,
    label_from_return,
    min_bars,
    snapshot_from_enriched,
)
from trading.analyst_memory import PatternMemory


def load_csv(path: Path) -> pd.DataFrame:
    df = pd.read_csv(path, parse_dates=["timestamp"])
    df = df.set_index("timestamp").sort_index()
    if df.index.tz is None:
        df.index = df.index.tz_localize("UTC")
    return df


def seed_correlations(df: pd.DataFrame, params: FeatureParams, stride: int) -> CorrelationBook:
    book = CorrelationBook(min_samples=30)
    en = enrich(df)
    closes = en["close"].to_numpy(dtype=float)
    last_i = len(en) - params.horizon - 1
    start_i = min_bars(params) - 1
    for i in range(start_i, last_i + 1, max(1, stride)):
        try:
            snap = snapshot_from_enriched(en, i, params)
        except ValueError:
            continue
        fwd = forward_return_pct(closes, i, params.horizon)
        lab = label_from_return(fwd, params.flat_pct)
        book.observe(snap, lab)
    return book


def main() -> None:
    cfg = get_config().analyst
    ap = argparse.ArgumentParser()
    ap.add_argument("--csv", default=str(ROOT / "data" / "aave_5m_kucoin_12m.csv"))
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
        df, params, stride=args.stride, source=csv_path.name
    )
    out = Path(args.out)
    mem.save(out)
    labels = mem.labels
    print(
        f"Memoire: {mem.size} motifs dim={mem.vectors.shape[1]} -> {out}\n"
        f"  UP={int((labels == 1).sum())} DOWN={int((labels == -1).sum())} "
        f"FLAT={int((labels == 0).sum())}"
    )

    book = seed_correlations(df, params, args.stride)
    corr_seed = ROOT / "models" / "analyst_correlations.json"
    corr_runtime = Path(cfg.correlations_file)
    book.save(corr_seed)
    book.save(corr_runtime)
    print(f"Correlations seed -> {corr_seed}")
    print("Top edges:")
    for line in book.top_edges(8):
        print(f"  {line}")

    window = df.iloc[-min_bars(params) :]
    vec = encode_window(window, params)
    pred = mem.query(
        vec,
        top_k=cfg.top_k,
        max_distance=cfg.max_distance,
        always=True,
        prefer_direction=True,
    )
    print(
        f"Smoke: {pred.label} conf={pred.confidence:.2f} n={pred.n_matches} "
        f"avg={pred.avg_fwd_pct:+.2f}%"
    )


if __name__ == "__main__":
    main()
