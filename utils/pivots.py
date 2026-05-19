"""Pivots TradingView ta.pivothigh / ta.pivotlow."""

from __future__ import annotations

import numpy as np


def pivot_high_at_bar(
    highs: np.ndarray, confirm_bar: int, left: int, right: int
) -> tuple[float | None, int | None]:
    """
    Pivot haut confirme a la bougie confirm_bar (comme Pine sur cette barre).
    Le sommet reel est a confirm_bar - right.
    """
    pivot_idx = confirm_bar - right
    if pivot_idx < left or confirm_bar >= len(highs):
        return None, None

    window = highs[pivot_idx - left : pivot_idx + right + 1]
    h = float(highs[pivot_idx])
    if h == float(np.max(window)) and int(np.sum(window == h)) == 1:
        return h, pivot_idx
    return None, None


def pivot_low_at_bar(
    lows: np.ndarray, confirm_bar: int, left: int, right: int
) -> tuple[float | None, int | None]:
    pivot_idx = confirm_bar - right
    if pivot_idx < left or confirm_bar >= len(lows):
        return None, None

    window = lows[pivot_idx - left : pivot_idx + right + 1]
    lo = float(lows[pivot_idx])
    if lo == float(np.min(window)) and int(np.sum(window == lo)) == 1:
        return lo, pivot_idx
    return None, None


def ta_pivot_high(highs: np.ndarray, left: int, right: int) -> tuple[float | None, int | None]:
    """Derniere barre = barre de confirmation."""
    if len(highs) < left + right + 1:
        return None, None
    return pivot_high_at_bar(highs, len(highs) - 1, left, right)


def ta_pivot_low(lows: np.ndarray, left: int, right: int) -> tuple[float | None, int | None]:
    if len(lows) < left + right + 1:
        return None, None
    return pivot_low_at_bar(lows, len(lows) - 1, left, right)
