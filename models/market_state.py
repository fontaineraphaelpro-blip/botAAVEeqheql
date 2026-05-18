"""État incrémental par symbole / timeframe (historique pivots + zones actives)."""

from __future__ import annotations

from collections import deque

from models.liquidity_zone import LiquidityZone, PivotPoint


class MarketState:
    def __init__(self, max_pivot_history: int = 50, max_active_zones: int = 60) -> None:
        self.historical_highs: deque[PivotPoint] = deque(maxlen=max_pivot_history)
        self.historical_lows: deque[PivotPoint] = deque(maxlen=max_pivot_history)
        self.active_zones: list[LiquidityZone] = []
        self.max_active_zones = max_active_zones
        self.last_processed_ts: int | None = None
