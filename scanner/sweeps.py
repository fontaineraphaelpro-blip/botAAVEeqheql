"""Détection des sweeps — logique LuxAlgo (mèche au-delà du sweep_level)."""

from __future__ import annotations

from typing import List, Tuple

import pandas as pd

from models.liquidity_zone import LiquidityZone, ZoneType


def detect_sweeps(
    df: pd.DataFrame,
    zones: List[LiquidityZone],
    bar_index: int,
) -> List[Tuple[LiquidityZone, str, int]]:
    """
    Sweep EQH: high > sweep_level (top), uniquement si bar_index > created_bar_index.
    Sweep EQL: low < sweep_level (bottom).
    """
    if df.empty or not zones or bar_index < 0 or bar_index >= len(df):
        return []

    bar = df.iloc[bar_index]
    high = float(bar["high"])
    low = float(bar["low"])
    events: List[Tuple[LiquidityZone, str, int]] = []

    for zone in zones:
        if zone.is_swept:
            continue
        if bar_index <= zone.created_bar_index:
            continue

        if zone.zone_type == ZoneType.EQH and high > zone.sweep_level:
            zone.is_swept = True
            events.append((zone, "EQH_SWEEP", bar_index))
        elif zone.zone_type == ZoneType.EQL and low < zone.sweep_level:
            zone.is_swept = True
            events.append((zone, "EQL_SWEEP", bar_index))

    return events
