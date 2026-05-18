"""Détection EQH/EQL — reproduction logique LuxAlgo Pine Script."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Set, Tuple

import pandas as pd

from config import AppConfig
from models.liquidity_zone import LiquidityZone, PivotPoint, ZoneType
from models.market_state import MarketState
from scanner.sweeps import detect_sweeps
from utils.logger import setup_logger
from utils.pivots import ta_pivot_high, ta_pivot_low

logger = setup_logger(__name__)

StateKey = Tuple[str, str]


@dataclass
class ScanResult:
    new_zones: List[LiquidityZone] = field(default_factory=list)
    sweeps: List[Tuple[LiquidityZone, str]] = field(default_factory=list)


class LiquidityDetector:
    def __init__(self, config: AppConfig) -> None:
        self.config = config
        self._states: Dict[StateKey, MarketState] = {}
        self._alerted_zones: Set[str] = set()

    def _state(self, symbol: str, timeframe: str) -> MarketState:
        key = (symbol, timeframe)
        if key not in self._states:
            self._states[key] = MarketState(
                max_pivot_history=self.config.pivot.max_pivot_history,
                max_active_zones=self.config.pivot.max_active_zones,
            )
        return self._states[key]

    def process(
        self,
        symbol: str,
        timeframe: str,
        df: pd.DataFrame,
        last_closed_ts: int,
    ) -> ScanResult:
        """Traite uniquement les nouvelles bougies fermées (évite doublons)."""
        state = self._state(symbol, timeframe)
        if state.last_processed_ts == last_closed_ts:
            return ScanResult()

        state.last_processed_ts = last_closed_ts

        left = self.config.pivot.pivot_left
        right = self.config.pivot.pivot_right
        thr = self.config.pivot.threshold_pct

        bar_index = len(df) - 1
        highs = df["high"].to_numpy()
        lows = df["low"].to_numpy()
        volumes = df["volume"].to_numpy()

        result = ScanResult()

        p_h, pivot_hi_idx = ta_pivot_high(highs, left, right)
        if p_h is not None and pivot_hi_idx is not None:
            current_vol = float(volumes[pivot_hi_idx])
            zone = self._try_eqh(symbol, timeframe, state, p_h, pivot_hi_idx, current_vol, thr, bar_index)
            if zone:
                if zone.dedupe_key() not in self._alerted_zones:
                    zone.score = self._score(df, zone)
                    result.new_zones.append(zone)
                    self._alerted_zones.add(zone.dedupe_key())
                state.active_zones.append(zone)

            state.historical_highs.appendleft(
                PivotPoint(price=p_h, bar_index=pivot_hi_idx, volume=current_vol)
            )

        p_l, pivot_lo_idx = ta_pivot_low(lows, left, right)
        if p_l is not None and pivot_lo_idx is not None:
            current_vol = float(volumes[pivot_lo_idx])
            zone = self._try_eql(symbol, timeframe, state, p_l, pivot_lo_idx, current_vol, thr, bar_index)
            if zone:
                if zone.dedupe_key() not in self._alerted_zones:
                    zone.score = self._score(df, zone)
                    result.new_zones.append(zone)
                    self._alerted_zones.add(zone.dedupe_key())
                state.active_zones.append(zone)

            state.historical_lows.appendleft(
                PivotPoint(price=p_l, bar_index=pivot_lo_idx, volume=current_vol)
            )

        unswept = [z for z in state.active_zones if not z.is_swept]
        result.sweeps = detect_sweeps(df, unswept, bar_index)
        state.active_zones = [z for z in state.active_zones if not z.is_swept]

        while len(state.active_zones) > state.max_active_zones:
            state.active_zones.pop(0)

        return result

    def _try_eqh(
        self,
        symbol: str,
        timeframe: str,
        state: MarketState,
        p_h: float,
        pivot_idx: int,
        current_vol: float,
        thr: float,
        bar_index: int,
    ) -> LiquidityZone | None:
        for prev in state.historical_highs:
            diff = abs(p_h - prev.price) / prev.price * 100.0
            if diff <= thr:
                top = max(p_h, prev.price)
                bottom = min(p_h, prev.price)
                mid = (top + bottom) / 2.0
                total_vol = prev.volume + current_vol
                zone_id = f"{symbol}_{timeframe}_EQH_{bar_index}_{top:.4f}"
                return LiquidityZone(
                    zone_id=zone_id,
                    symbol=symbol,
                    timeframe=timeframe,
                    zone_type=ZoneType.EQH,
                    top=top,
                    bottom=bottom,
                    mid=mid,
                    sweep_level=top,
                    total_vol=total_vol,
                    created_bar_index=bar_index,
                    pivot_a_idx=prev.bar_index,
                    pivot_b_idx=pivot_idx,
                )
        return None

    def _try_eql(
        self,
        symbol: str,
        timeframe: str,
        state: MarketState,
        p_l: float,
        pivot_idx: int,
        current_vol: float,
        thr: float,
        bar_index: int,
    ) -> LiquidityZone | None:
        for prev in state.historical_lows:
            diff = abs(p_l - prev.price) / prev.price * 100.0
            if diff <= thr:
                top = max(p_l, prev.price)
                bottom = min(p_l, prev.price)
                mid = (top + bottom) / 2.0
                total_vol = prev.volume + current_vol
                zone_id = f"{symbol}_{timeframe}_EQL_{bar_index}_{bottom:.4f}"
                return LiquidityZone(
                    zone_id=zone_id,
                    symbol=symbol,
                    timeframe=timeframe,
                    zone_type=ZoneType.EQL,
                    top=top,
                    bottom=bottom,
                    mid=mid,
                    sweep_level=bottom,
                    total_vol=total_vol,
                    created_bar_index=bar_index,
                    pivot_a_idx=prev.bar_index,
                    pivot_b_idx=pivot_idx,
                )
        return None

    def _score(self, df: pd.DataFrame, zone: LiquidityZone) -> float:
        last_close = float(df["close"].iloc[-1])
        dist_pct = abs(last_close - zone.sweep_level) / zone.sweep_level * 100 if zone.sweep_level else 99.0
        proximity = max(0.0, 100.0 - dist_pct * 8.0)
        vol_tail = df["volume"].tail(20)
        vol_ratio = float(vol_tail.iloc[-1] / vol_tail.mean()) if vol_tail.mean() > 0 else 1.0
        vol_part = min(25.0, vol_ratio * 12.0)
        return round(min(100.0, 35.0 + proximity * 0.45 + vol_part), 1)
