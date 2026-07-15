"""Stratégie chasseur de shorts — signaux par coin + régime BTC strict.

Validée par backtest 2017-2026 (35 alts) : +225 %, drawdown max -32 %.
Ne short que pendant les vrais bear markets ; dormante le reste du temps.
"""

from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

from config import ShortsConfig

BARS_PER_DAY = 12  # bougies 2h


@dataclass(frozen=True)
class ShortSignal:
    symbol: str
    close: float
    atr: float
    er: float
    low_break: float       # plus-bas N barres cassé
    mom7d: float           # momentum 7 jours (tri : plus faible en premier)


class ShortHunterStrategy:
    def __init__(self, cfg: ShortsConfig) -> None:
        self.cfg = cfg

    # ------------------------------------------------------------- régime BTC
    def btc_bear_regime(self, btc: pd.DataFrame) -> tuple[bool, dict]:
        """Bear strict : prix < EMA 200j ET EMA 200j plus basse qu'il y a 30j."""
        ema = btc["close"].ewm(span=self.cfg.btc_ema_days * BARS_PER_DAY, adjust=False).mean()
        shift_bars = self.cfg.btc_slope_days * BARS_PER_DAY
        slope_down = bool(ema.iloc[-1] < ema.iloc[-1 - shift_bars]) if len(ema) > shift_bars else False
        below = bool(btc["close"].iloc[-1] < ema.iloc[-1])
        info = {
            "price": float(btc["close"].iloc[-1]),
            "ema200d": float(ema.iloc[-1]),
            "slope_down": slope_down,
        }
        return below and slope_down, info

    # ------------------------------------------------------------- indicateurs
    @staticmethod
    def _atr(df: pd.DataFrame, n: int) -> pd.Series:
        hl = df["high"] - df["low"]
        hc = (df["high"] - df["close"].shift()).abs()
        lc = (df["low"] - df["close"].shift()).abs()
        tr = pd.concat([hl, hc, lc], axis=1).max(axis=1)
        return tr.ewm(alpha=1 / n, adjust=False).mean()

    def _er(self, close: pd.Series) -> pd.Series:
        n = self.cfg.er_len
        change = (close - close.shift(n)).abs()
        vol = close.diff().abs().rolling(n).sum()
        return (change / vol).fillna(0)

    # ------------------------------------------------------------- signal coin
    def compute(self, symbol: str, df: pd.DataFrame) -> ShortSignal | None:
        """Signal short sur la dernière bougie 2h clôturée, ou None."""
        cfg = self.cfg
        need = max(cfg.low_n + 2, cfg.er_len + 2, cfg.atr_len + 2, 7 * BARS_PER_DAY + 1)
        if len(df) < need:
            return None

        close = float(df["close"].iloc[-1])
        # plus-bas des N barres PRÉCÉDENTES (shift 1, comme le backtest)
        low_n = float(df["low"].iloc[-(cfg.low_n + 1):-1].min())
        if close >= low_n:
            return None

        er_val = float(self._er(df["close"]).iloc[-1])
        if er_val < cfg.er_min:
            return None

        atr_val = float(self._atr(df, cfg.atr_len).iloc[-1])
        mom = float(df["close"].iloc[-1] / df["close"].iloc[-1 - 7 * BARS_PER_DAY] - 1)
        return ShortSignal(
            symbol=symbol, close=close, atr=atr_val, er=er_val,
            low_break=low_n, mom7d=mom,
        )

    # ------------------------------------------------------------- stops
    def initial_stop(self, entry: float, atr: float) -> float:
        return entry + self.cfg.stop_atr * atr

    def trail_stop(self, current_stop: float, bar_low: float, atr: float) -> float:
        return min(current_stop, bar_low + self.cfg.trail_atr * atr)
