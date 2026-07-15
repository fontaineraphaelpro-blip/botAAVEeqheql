"""Stratégie EMA-flip AAVE/USDT — validée par backtest 12 mois.

Règle de direction (celle de l'indicateur TradingView) :
- Clôture au-dessus de la ligne grise (EMA) => biais LONG
- Clôture en dessous => biais SHORT

Améliorations validées en backtest (sans elles la règle brute perd ~-60 %/an) :
- Filtre tendance 4h : EMA50 > EMA200 (équivalent continu) pour autoriser les longs,
  inverse pour les shorts. Évite de trader contre la tendance de fond.
- Efficiency ratio (Kaufman) >= seuil : ne trade que quand le marché est directionnel,
  élimine le chop qui détruit les stratégies de croisement.
- Stop initial ATR + trailing chandelier : coupe court les pertes, laisse courir les gains.
"""

from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

from config import TradingConfig


@dataclass(frozen=True)
class Signal:
    """Instantané de la stratégie sur la dernière bougie clôturée du TF signal."""

    ts: pd.Timestamp
    close: float
    ema: float
    atr: float
    bias: int              # +1 au-dessus de la ligne grise, -1 en dessous
    htf_bull: bool         # EMA50 4h > EMA200 4h
    htf_bear: bool
    er: float              # efficiency ratio
    long_ok: bool          # tous les filtres alignés pour un long
    short_ok: bool

    @property
    def direction(self) -> int:
        if self.long_ok:
            return 1
        if self.short_ok:
            return -1
        return 0


class EmaFlipStrategy:
    def __init__(self, cfg: TradingConfig) -> None:
        self.cfg = cfg

    def resample(self, df_5m: pd.DataFrame) -> pd.DataFrame:
        """Bougies 5m (clôturées) -> TF signal. Ne garde que les bougies TF complètes."""
        tf_min = self.cfg.signal_tf_min
        df = df_5m.set_index("timestamp") if "timestamp" in df_5m.columns else df_5m
        out = df.resample(f"{tf_min}min").agg(
            {"open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"}
        ).dropna()
        if out.empty:
            return out
        # La dernière bougie TF n'est complète que si sa fin <= dernière bougie 5m close
        last_5m_end = df.index[-1] + pd.Timedelta(minutes=5)
        last_tf_end = out.index[-1] + pd.Timedelta(minutes=tf_min)
        if last_tf_end > last_5m_end:
            out = out.iloc[:-1]
        return out

    @staticmethod
    def _atr(df: pd.DataFrame, n: int) -> pd.Series:
        hl = df["high"] - df["low"]
        hc = (df["high"] - df["close"].shift()).abs()
        lc = (df["low"] - df["close"].shift()).abs()
        tr = pd.concat([hl, hc, lc], axis=1).max(axis=1)
        return tr.ewm(alpha=1 / n, adjust=False).mean()

    def _efficiency_ratio(self, close: pd.Series) -> pd.Series:
        n = self.cfg.er_len
        change = (close - close.shift(n)).abs()
        vol = close.diff().abs().rolling(n).sum()
        return (change / vol).fillna(0)

    def compute(self, df_tf: pd.DataFrame) -> Signal | None:
        """Calcule le signal sur la dernière bougie TF clôturée."""
        cfg = self.cfg
        if len(df_tf) < max(cfg.ema_len, cfg.er_len, cfg.atr_len) + 2:
            return None

        close = df_tf["close"]
        ema = close.ewm(span=cfg.ema_len, adjust=False).mean()
        atr = self._atr(df_tf, cfg.atr_len)
        er = self._efficiency_ratio(close)

        # EMA 4h "équivalent continu" sur le TF signal (span mis à l'échelle)
        scale = max(1, cfg.htf_tf_min // cfg.signal_tf_min)
        ema_f = close.ewm(span=cfg.htf_fast * scale, adjust=False).mean()
        ema_s = close.ewm(span=cfg.htf_slow * scale, adjust=False).mean()

        c = float(close.iloc[-1])
        e = float(ema.iloc[-1])
        bias = 1 if c > e else -1 if c < e else 0
        htf_bull = bool(ema_f.iloc[-1] > ema_s.iloc[-1])
        htf_bear = bool(ema_f.iloc[-1] < ema_s.iloc[-1])
        er_val = float(er.iloc[-1])
        trending = er_val >= cfg.er_min

        return Signal(
            ts=df_tf.index[-1],
            close=c,
            ema=e,
            atr=float(atr.iloc[-1]),
            bias=bias,
            htf_bull=htf_bull,
            htf_bear=htf_bear,
            er=er_val,
            long_ok=bias == 1 and htf_bull and trending,
            short_ok=bias == -1 and htf_bear and trending,
        )

    def initial_stop(self, direction: int, entry: float, atr: float) -> float:
        if direction == 1:
            return entry - self.cfg.stop_atr * atr
        return entry + self.cfg.stop_atr * atr

    def trail_stop(self, direction: int, current_stop: float, bar_high: float,
                   bar_low: float, atr: float) -> float:
        """Trailing chandelier — ne recule jamais."""
        if direction == 1:
            return max(current_stop, bar_high - self.cfg.trail_atr * atr)
        return min(current_stop, bar_low + self.cfg.trail_atr * atr)
