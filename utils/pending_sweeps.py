"""Sweeps en attente de confirmation (bougie suivante)."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, Tuple

from models.liquidity_zone import LiquidityZone

StateKey = Tuple[str, str]


@dataclass
class PendingSweep:
    zone: LiquidityZone
    sweep_type: str
    sweep_bar_index: int
    expires_bar_index: int


class PendingSweepStore:
    def __init__(self) -> None:
        self._items: Dict[StateKey, Dict[str, PendingSweep]] = {}

    def add(self, symbol: str, timeframe: str, pending: PendingSweep) -> None:
        key = (symbol, timeframe)
        self._items.setdefault(key, {})[pending.zone.zone_id] = pending

    def pop(self, symbol: str, timeframe: str, zone_id: str) -> PendingSweep | None:
        return self._items.get((symbol, timeframe), {}).pop(zone_id, None)

    def for_pair(self, symbol: str, timeframe: str) -> list[PendingSweep]:
        return list(self._items.get((symbol, timeframe), {}).values())

    def remove(self, symbol: str, timeframe: str, zone_id: str) -> None:
        self._items.get((symbol, timeframe), {}).pop(zone_id, None)

    def prune_expired(self, symbol: str, timeframe: str, current_bar: int) -> None:
        bucket = self._items.get((symbol, timeframe), {})
        expired = [zid for zid, p in bucket.items() if current_bar > p.expires_bar_index]
        for zid in expired:
            del bucket[zid]
