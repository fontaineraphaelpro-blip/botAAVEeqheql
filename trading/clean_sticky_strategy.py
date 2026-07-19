"""Stratégie AAVE Clean Sticky — couleurs EMA 20/50 (TradingView).

Vert  : close > EMA rapide ET EMA rapide > EMA lente  -> LONG
Rouge : close < EMA rapide ET EMA rapide < EMA lente  -> SHORT
Gris  : sinon                                        -> flat (ferme)
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import IntEnum

import pandas as pd

from config import CleanStickyConfig


class ColorState(IntEnum):
    BEAR = -1
    NEUTRAL = 0
    BULL = 1

    @property
    def label(self) -> str:
        return {
            ColorState.BEAR: "rouge",
            ColorState.NEUTRAL: "gris",
            ColorState.BULL: "vert",
        }[self]

    @property
    def emoji(self) -> str:
        return {
            ColorState.BEAR: "🔴",
            ColorState.NEUTRAL: "⚪",
            ColorState.BULL: "🟢",
        }[self]


@dataclass(frozen=True)
class StickySignal:
    ts: pd.Timestamp
    close: float
    ema_fast: float
    ema_slow: float
    color: ColorState
    prev_color: ColorState

    @property
    def flipped(self) -> bool:
        return self.color != self.prev_color


class CleanStickyStrategy:
    def __init__(self, cfg: CleanStickyConfig) -> None:
        self.cfg = cfg

    def color_series(self, df: pd.DataFrame) -> pd.Series:
        close = df["close"]
        ema_f = close.ewm(span=self.cfg.ema_fast, adjust=False).mean()
        ema_s = close.ewm(span=self.cfg.ema_slow, adjust=False).mean()
        bull = (close > ema_f) & (ema_f > ema_s)
        bear = (close < ema_f) & (ema_f < ema_s)
        out = pd.Series(int(ColorState.NEUTRAL), index=df.index, dtype=int)
        out = out.mask(bull, int(ColorState.BULL))
        out = out.mask(bear, int(ColorState.BEAR))
        return out

    def compute(self, df: pd.DataFrame) -> StickySignal | None:
        need = self.cfg.ema_slow + 2
        if len(df) < need:
            return None

        close = df["close"]
        ema_f = close.ewm(span=self.cfg.ema_fast, adjust=False).mean()
        ema_s = close.ewm(span=self.cfg.ema_slow, adjust=False).mean()
        colors = self.color_series(df)

        c = float(close.iloc[-1])
        ef = float(ema_f.iloc[-1])
        es = float(ema_s.iloc[-1])
        color = ColorState(int(colors.iloc[-1]))
        prev = ColorState(int(colors.iloc[-2])) if len(colors) >= 2 else color

        return StickySignal(
            ts=df.index[-1],
            close=c,
            ema_fast=ef,
            ema_slow=es,
            color=color,
            prev_color=prev,
        )
