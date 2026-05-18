"""Zones de liquidité EQH / EQL (modèle aligné LuxAlgo)."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum


class ZoneType(str, Enum):
    EQH = "EQH"
    EQL = "EQL"


@dataclass
class PivotPoint:
    price: float
    bar_index: int
    volume: float


@dataclass
class LiquidityZone:
    zone_id: str
    symbol: str
    timeframe: str
    zone_type: ZoneType
    top: float
    bottom: float
    mid: float
    sweep_level: float
    total_vol: float
    created_bar_index: int
    pivot_a_idx: int
    pivot_b_idx: int
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    is_swept: bool = False
    score: float = 0.0

    @property
    def display_symbol(self) -> str:
        return self.symbol.replace("/", "").upper()

    @property
    def price(self) -> float:
        return self.sweep_level

    def dedupe_key(self) -> str:
        return (
            f"{self.display_symbol}:{self.timeframe}:{self.zone_type.value}:"
            f"{self.sweep_level:.6f}:{self.pivot_a_idx}:{self.pivot_b_idx}"
        )
