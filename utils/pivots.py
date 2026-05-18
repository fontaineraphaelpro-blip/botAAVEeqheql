"""Pivots TradingView ta.pivothigh / ta.pivotlow."""

from __future__ import annotations

import numpy as np


def ta_pivot_high(highs: np.ndarray, left: int, right: int) -> tuple[float | None, int | None]:
    """
    Retourne (prix pivot, index pivot) confirmé à la dernière barre, ou (None, None).
    Équivalent Pine: ta.pivothigh(left, right) sur bar_index = len-1.
    """
    n = len(highs)
    if n < left + right + 1:
        return None, None

    pivot_idx = n - 1 - right
    window = highs[pivot_idx - left : pivot_idx + right + 1]
    h = float(highs[pivot_idx])
    if h == float(np.max(window)) and int(np.sum(window == h)) == 1:
        return h, pivot_idx
    return None, None


def ta_pivot_low(lows: np.ndarray, left: int, right: int) -> tuple[float | None, int | None]:
    n = len(lows)
    if n < left + right + 1:
        return None, None

    pivot_idx = n - 1 - right
    window = lows[pivot_idx - left : pivot_idx + right + 1]
    lo = float(lows[pivot_idx])
    if lo == float(np.min(window)) and int(np.sum(window == lo)) == 1:
        return lo, pivot_idx
    return None, None
