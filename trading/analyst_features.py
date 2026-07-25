"""Encodage de fenêtres OHLCV AAVE → vecteurs pour la mémoire de motifs."""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd


@dataclass(frozen=True)
class FeatureParams:
    lookback: int = 24
    horizon: int = 12
    flat_pct: float = 0.20  # |fwd return %| sous ce seuil = FLAT


def feature_dim(lookback: int) -> int:
    """ret (W-1) + range (W) + vol_rel (W) + 3 agrégats."""
    return (lookback - 1) + lookback + lookback + 3


def encode_window(df: pd.DataFrame, params: FeatureParams | None = None) -> np.ndarray:
    """Encode les `lookback` dernières lignes (colonnes open/high/low/close/volume).

    Normalisation L2 pour cosine similarity.
    """
    p = params or FeatureParams()
    if len(df) < p.lookback:
        raise ValueError(f"besoin de {p.lookback} barres, got {len(df)}")
    w = df.iloc[-p.lookback :]
    close = w["close"].to_numpy(dtype=np.float64)
    high = w["high"].to_numpy(dtype=np.float64)
    low = w["low"].to_numpy(dtype=np.float64)
    vol = w["volume"].to_numpy(dtype=np.float64)

    ret = np.diff(close) / np.maximum(close[:-1], 1e-12)
    rng = (high - low) / np.maximum(close, 1e-12)
    vol_mean = float(np.mean(vol)) if np.mean(vol) > 0 else 1.0
    vol_rel = vol / vol_mean

    # Agrégats : momentum fenêtre, ATR% moyen, volume trend
    mom = float(close[-1] / close[0] - 1.0)
    atr_pct = float(np.mean(rng))
    vol_trend = float(np.mean(vol_rel[-6:]) / max(np.mean(vol_rel[:6]), 1e-9) - 1.0)

    vec = np.concatenate([ret, rng, vol_rel, np.array([mom, atr_pct, vol_trend])])
    n = float(np.linalg.norm(vec))
    if n < 1e-12:
        return vec.astype(np.float32)
    return (vec / n).astype(np.float32)


def forward_return_pct(closes: np.ndarray, i: int, horizon: int) -> float:
    """Rendement % de close[i] → close[i+horizon]."""
    a = float(closes[i])
    b = float(closes[i + horizon])
    if a <= 0:
        return 0.0
    return (b / a - 1.0) * 100.0


def label_from_return(fwd_pct: float, flat_pct: float) -> int:
    """+1 UP, -1 DOWN, 0 FLAT."""
    if fwd_pct > flat_pct:
        return 1
    if fwd_pct < -flat_pct:
        return -1
    return 0


DIR_LABEL = {1: "UP", -1: "DOWN", 0: "FLAT"}
