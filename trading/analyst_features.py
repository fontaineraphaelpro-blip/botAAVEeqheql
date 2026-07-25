"""Encodage riche AAVE — OHLCV + indicateurs + corrélations intra-fenêtre."""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

WARMUP = 80


@dataclass(frozen=True)
class FeatureParams:
    lookback: int = 24
    horizon: int = 12
    flat_pct: float = 0.20


DIR_LABEL = {1: "UP", -1: "DOWN", 0: "FLAT"}

INDICATOR_KEYS = (
    "rsi",
    "macd_hist",
    "bb_pct",
    "atr_pct",
    "stoch_k",
    "ema_spread",
    "er",
    "mom",
    "vol_trend",
    "corr_pv",
    "corr_ret_vol",
    "autocorr",
)


def feature_dim(lookback: int) -> int:
    series_n = 6 * lookback
    return (lookback - 1) + lookback + series_n + len(INDICATOR_KEYS)


def _rsi(close: pd.Series, n: int = 14) -> pd.Series:
    d = close.diff()
    up = d.clip(lower=0.0)
    down = (-d).clip(lower=0.0)
    ma_up = up.ewm(alpha=1 / n, adjust=False).mean()
    ma_dn = down.ewm(alpha=1 / n, adjust=False).mean()
    rs = ma_up / ma_dn.replace(0, np.nan)
    return (100 - (100 / (1 + rs))).fillna(50.0)


def _atr_pct(df: pd.DataFrame, n: int = 14) -> pd.Series:
    hl = df["high"] - df["low"]
    hc = (df["high"] - df["close"].shift()).abs()
    lc = (df["low"] - df["close"].shift()).abs()
    tr = pd.concat([hl, hc, lc], axis=1).max(axis=1)
    atr = tr.ewm(alpha=1 / n, adjust=False).mean()
    return (atr / df["close"]).fillna(0.0)


def _stoch_k(df: pd.DataFrame, n: int = 14) -> pd.Series:
    low_n = df["low"].rolling(n).min()
    high_n = df["high"].rolling(n).max()
    denom = (high_n - low_n).replace(0, np.nan)
    return (100 * (df["close"] - low_n) / denom).fillna(50.0)


def _efficiency_ratio(close: pd.Series, n: int = 20) -> pd.Series:
    change = (close - close.shift(n)).abs()
    vol = close.diff().abs().rolling(n).sum()
    return (change / vol.replace(0, np.nan)).fillna(0.0)


def _safe_corr(a: np.ndarray, b: np.ndarray) -> float:
    if len(a) < 5:
        return 0.0
    if np.std(a) < 1e-12 or np.std(b) < 1e-12:
        return 0.0
    c = float(np.corrcoef(a, b)[0, 1])
    if np.isnan(c):
        return 0.0
    return c


def enrich(df: pd.DataFrame) -> pd.DataFrame:
    """Ajoute colonnes d'indicateurs (une passe sur tout le DF)."""
    out = df.copy()
    c = out["close"]
    ema12 = c.ewm(span=12, adjust=False).mean()
    ema26 = c.ewm(span=26, adjust=False).mean()
    macd = ema12 - ema26
    signal = macd.ewm(span=9, adjust=False).mean()
    ema20 = c.ewm(span=20, adjust=False).mean()
    ema50 = c.ewm(span=50, adjust=False).mean()
    mid = c.rolling(20).mean()
    std = c.rolling(20).std().replace(0, np.nan)

    out["rsi"] = _rsi(c, 14)
    out["macd_hist"] = macd - signal
    out["bb_pct"] = ((c - mid) / (2 * std)).clip(-1.5, 1.5).fillna(0.0)
    out["atr_pct"] = _atr_pct(out, 14)
    out["stoch_k"] = _stoch_k(out, 14)
    out["ema_spread"] = ((ema20 - ema50) / c).fillna(0.0)
    out["er"] = _efficiency_ratio(c, 20)
    out["range_pct"] = ((out["high"] - out["low"]) / c).fillna(0.0)
    return out


def min_bars(params: FeatureParams) -> int:
    return params.lookback + WARMUP


def _window_corrs(close: np.ndarray, vol: np.ndarray, rsi: np.ndarray) -> dict[str, float]:
    ret = np.diff(close) / np.maximum(close[:-1], 1e-12)
    vol_r = vol[1:] if len(vol) > 1 else vol
    return {
        "corr_pv": _safe_corr(close, vol),
        "corr_ret_vol": _safe_corr(ret, vol_r[: len(ret)]),
        "autocorr": _safe_corr(ret[1:], ret[:-1]) if len(ret) > 2 else 0.0,
        "corr_rsi_ret": _safe_corr(rsi[1:], ret),
    }


def snapshot_from_enriched(en: pd.DataFrame, end_i: int, params: FeatureParams) -> dict[str, float]:
    """Snapshot à l'index `end_i` (inclus) sur DF déjà enrichi."""
    p = params
    start = end_i - p.lookback + 1
    if start < 0 or end_i >= len(en):
        raise ValueError("index hors bornes")
    w = en.iloc[start : end_i + 1]
    close = w["close"].to_numpy(dtype=np.float64)
    vol = w["volume"].to_numpy(dtype=np.float64)
    rsi = w["rsi"].to_numpy(dtype=np.float64)
    vol_mean = float(np.mean(vol)) if np.mean(vol) > 0 else 1.0
    vol_rel = vol / vol_mean
    last = en.iloc[end_i]
    corrs = _window_corrs(close, vol, rsi)
    return {
        "rsi": float(last["rsi"]),
        "macd_hist": float(last["macd_hist"] / max(abs(float(last["close"])), 1e-9)),
        "bb_pct": float(last["bb_pct"]),
        "atr_pct": float(last["atr_pct"]),
        "stoch_k": float(last["stoch_k"]),
        "ema_spread": float(last["ema_spread"]),
        "er": float(last["er"]),
        "mom": float(close[-1] / close[0] - 1.0),
        "vol_trend": float(
            np.mean(vol_rel[-6:]) / max(np.mean(vol_rel[:6]), 1e-9) - 1.0
        ),
        **corrs,
    }


def encode_from_enriched(en: pd.DataFrame, end_i: int, params: FeatureParams) -> np.ndarray:
    """Encode la fenêtre se terminant à end_i (DF déjà enrichi)."""
    p = params
    start = end_i - p.lookback + 1
    if start < 0:
        raise ValueError("fenêtre trop courte")
    w = en.iloc[start : end_i + 1]
    close = w["close"].to_numpy(dtype=np.float64)
    vol = w["volume"].to_numpy(dtype=np.float64)

    ret = np.diff(close) / np.maximum(close[:-1], 1e-12)
    vol_mean = float(np.mean(vol)) if np.mean(vol) > 0 else 1.0
    vol_rel = vol / vol_mean

    rsi_n = (w["rsi"].to_numpy(dtype=np.float64) - 50.0) / 50.0
    macd_n = w["macd_hist"].to_numpy(dtype=np.float64) / np.maximum(close, 1e-9)
    bb = w["bb_pct"].to_numpy(dtype=np.float64)
    atr = w["atr_pct"].to_numpy(dtype=np.float64)
    stoch = (w["stoch_k"].to_numpy(dtype=np.float64) - 50.0) / 50.0
    ema_sp = w["ema_spread"].to_numpy(dtype=np.float64)

    snap = snapshot_from_enriched(en, end_i, p)
    agg = np.array([snap[k] for k in INDICATOR_KEYS], dtype=np.float64)

    vec = np.concatenate([ret, vol_rel, rsi_n, macd_n, bb, atr, stoch, ema_sp, agg])
    vec = np.nan_to_num(vec, nan=0.0, posinf=0.0, neginf=0.0)
    vec = np.clip(vec, -5.0, 5.0)
    n = float(np.linalg.norm(vec))
    if n < 1e-12:
        return vec.astype(np.float32)
    return (vec / n).astype(np.float32)


def encode_window(df: pd.DataFrame, params: FeatureParams | None = None) -> np.ndarray:
    """Encode lookback + indicateurs (enrichit si besoin)."""
    p = params or FeatureParams()
    if len(df) < min_bars(p):
        raise ValueError(f"besoin de {min_bars(p)} barres, got {len(df)}")
    if "rsi" in df.columns:
        en = df
    else:
        en = enrich(df)
    return encode_from_enriched(en, len(en) - 1, p)


def snapshot_indicators(df: pd.DataFrame, params: FeatureParams | None = None) -> dict[str, float]:
    p = params or FeatureParams()
    if len(df) < min_bars(p):
        raise ValueError(f"besoin de {min_bars(p)} barres")
    en = df if "rsi" in df.columns else enrich(df)
    return snapshot_from_enriched(en, len(en) - 1, p)


def forward_return_pct(closes: np.ndarray, i: int, horizon: int) -> float:
    a = float(closes[i])
    b = float(closes[i + horizon])
    if a <= 0:
        return 0.0
    return (b / a - 1.0) * 100.0


def label_from_return(fwd_pct: float, flat_pct: float) -> int:
    if fwd_pct > flat_pct:
        return 1
    if fwd_pct < -flat_pct:
        return -1
    return 0
