"""Détection EQH/EQL — reproduction logique LuxAlgo Pine Script."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Set, Tuple

import pandas as pd

from config import AppConfig, TF_SECONDS
from models.liquidity_zone import LiquidityZone, PivotPoint, ZoneType
from models.market_state import MarketState
from scanner.sweeps import detect_sweeps
from utils.logger import setup_logger
from utils.pivots import pivot_high_at_bar, pivot_low_at_bar

logger = setup_logger(__name__)

StateKey = Tuple[str, str]


@dataclass
class ScanResult:
    new_zones: List[LiquidityZone] = field(default_factory=list)
    sweeps: List[Tuple[LiquidityZone, str, int]] = field(default_factory=list)


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
        *,
        record_only: bool = False,
    ) -> ScanResult:
        """
        Traite chaque bougie fermee non encore analysee (pas seulement la derniere).
        Evite le retard cumule et les EQH/EQL manques.
        """
        state = self._state(symbol, timeframe)
        min_bars = self.config.scan.min_bars
        if len(df) < min_bars:
            return ScanResult()

        merged = ScanResult()
        start_bar = state.last_processed_bar + 1
        end_bar = len(df) - 1

        if not record_only and start_bar > end_bar:
            if state.last_processed_ts == last_closed_ts:
                return ScanResult()
            start_bar = max(min_bars, end_bar)

        for bar_index in range(max(start_bar, min_bars), end_bar + 1):
            bar_result = self._process_one_bar(
                symbol, timeframe, df, bar_index, record_only=record_only
            )
            merged.new_zones.extend(bar_result.new_zones)
            merged.sweeps.extend(bar_result.sweeps)

        if not record_only:
            state.last_processed_bar = end_bar
            state.last_processed_ts = last_closed_ts

        return merged

    def _process_one_bar(
        self,
        symbol: str,
        timeframe: str,
        df: pd.DataFrame,
        bar_index: int,
        *,
        record_only: bool,
    ) -> ScanResult:
        state = self._state(symbol, timeframe)
        left = self.config.pivot.pivot_left
        right = self.config.pivot.pivot_right
        thr = self.config.pivot.threshold_pct

        highs = df["high"].to_numpy()
        lows = df["low"].to_numpy()
        volumes = df["volume"].to_numpy()

        result = ScanResult()

        p_h, pivot_hi_idx = pivot_high_at_bar(highs, bar_index, left, right)
        if p_h is not None and pivot_hi_idx is not None:
            current_vol = float(volumes[pivot_hi_idx])
            if not self._has_pivot(state.historical_highs, pivot_hi_idx):
                state.historical_highs.appendleft(
                    PivotPoint(price=p_h, bar_index=pivot_hi_idx, volume=current_vol)
                )
            zone = self._try_eqh(symbol, timeframe, state, p_h, pivot_hi_idx, current_vol, thr, bar_index)
            if zone and zone.dedupe_key() not in self._alerted_zones:
                zone.score = self._score(df, zone)
                result.new_zones.append(zone)
                if not record_only:
                    self._alerted_zones.add(zone.dedupe_key())
                state.active_zones.append(zone)

        p_l, pivot_lo_idx = pivot_low_at_bar(lows, bar_index, left, right)
        if p_l is not None and pivot_lo_idx is not None:
            current_vol = float(volumes[pivot_lo_idx])
            if not self._has_pivot(state.historical_lows, pivot_lo_idx):
                state.historical_lows.appendleft(
                    PivotPoint(price=p_l, bar_index=pivot_lo_idx, volume=current_vol)
                )
            zone = self._try_eql(symbol, timeframe, state, p_l, pivot_lo_idx, current_vol, thr, bar_index)
            if zone and zone.dedupe_key() not in self._alerted_zones:
                zone.score = self._score(df, zone)
                result.new_zones.append(zone)
                if not record_only:
                    self._alerted_zones.add(zone.dedupe_key())
                state.active_zones.append(zone)

        unswept = [z for z in state.active_zones if not z.is_swept]
        result.sweeps = detect_sweeps(df, unswept, bar_index)
        state.active_zones = [z for z in state.active_zones if not z.is_swept]

        while len(state.active_zones) > state.max_active_zones:
            state.active_zones.pop(0)

        return result

    @staticmethod
    def _has_pivot(history, bar_index: int) -> bool:
        return any(p.bar_index == bar_index for p in history)

    def warmup(self, symbol: str, timeframe: str, df: pd.DataFrame) -> int:
        closed = df.iloc[:-1] if len(df) > 1 else df
        min_bars = self.config.scan.min_bars
        zones_found = 0

        for i in range(min_bars, len(closed)):
            slice_df = closed.iloc[: i + 1]
            ts = int(slice_df["timestamp"].iloc[-1].timestamp())
            result = self.process(symbol, timeframe, slice_df, ts, record_only=True)
            zones_found += len(result.new_zones)

        state = self._state(symbol, timeframe)
        state.last_processed_bar = len(closed) - 1
        state.last_processed_ts = int(closed["timestamp"].iloc[-1].timestamp())
        logger.info(
            "Warmup %s %s: %d barres, highs=%d lows=%d, zones=%d",
            symbol,
            timeframe,
            len(closed) - min_bars,
            len(state.historical_highs),
            len(state.historical_lows),
            zones_found,
        )
        return zones_found

    def _fill_gap(
        self,
        symbol: str,
        timeframe: str,
        df: pd.DataFrame,
        closed_ts: int,
        bars: int,
    ) -> ScanResult:
        """Rejoue les bougies manquees pendant une panne API (live seulement)."""
        closed = df.iloc[:-1] if len(df) > 1 else df
        if len(closed) < self.config.scan.min_bars:
            return ScanResult()

        state = self._state(symbol, timeframe)
        start = max(self.config.scan.min_bars, len(closed) - bars)
        state.last_processed_bar = start - 1
        state.last_processed_ts = None
        result = self.process(symbol, timeframe, closed, closed_ts, record_only=False)
        if result.new_zones:
            logger.info(
                "Gap fill %s %s: %d barres, %d EQH/EQL",
                symbol,
                timeframe,
                len(closed) - start,
                len(result.new_zones),
            )
        return result

    def missed_candles(self, symbol: str, timeframe: str, closed_ts: int) -> int:
        """Nombre de bougies 5m probablement sautees depuis le dernier scan."""
        state = self._state(symbol, timeframe)
        if state.last_processed_ts is None:
            return 0
        tf_sec = TF_SECONDS.get(timeframe, 300)
        gap = closed_ts - state.last_processed_ts
        if gap <= tf_sec:
            return 0
        return max(0, int(gap / tf_sec) - 1)

    def scan_live(
        self,
        symbol: str,
        timeframe: str,
        df: pd.DataFrame,
        closed_ts: int,
        *,
        max_gap_bars: int = 48,
    ) -> ScanResult:
        """Scan live : bougie par bougie + comble un trou API sans louper d'alerte."""
        missed = self.missed_candles(symbol, timeframe, closed_ts)
        if missed >= 1:
            bars = min(missed + 3, max_gap_bars)
            return self._fill_gap(symbol, timeframe, df, closed_ts, bars)
        return self.process(symbol, timeframe, df, closed_ts, record_only=False)

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
            if prev.bar_index == pivot_idx:
                continue
            if prev.price <= 0:
                continue
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
            if prev.bar_index == pivot_idx:
                continue
            if prev.price <= 0:
                continue
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
