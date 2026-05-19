"""Filtres de signaux live — réduit les faux EQH/EQL et sweeps."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import TYPE_CHECKING

import pandas as pd

from models.liquidity_zone import LiquidityZone, ZoneType

if TYPE_CHECKING:
    from config import AlertFilterConfig


class FilterVerdict(str, Enum):
    PASS = "pass"
    REJECT = "reject"
    PENDING = "pending"


@dataclass
class FilterResult:
    verdict: FilterVerdict
    reason: str = ""

    @property
    def ok(self) -> bool:
        return self.verdict == FilterVerdict.PASS


def _zone_width_pct(zone: LiquidityZone) -> float:
    if zone.sweep_level <= 0:
        return 99.0
    return (zone.top - zone.bottom) / zone.sweep_level * 100.0


def _pivot_distance(zone: LiquidityZone) -> int:
    return abs(zone.pivot_b_idx - zone.pivot_a_idx)


def _bar_hour(df: pd.DataFrame, bar_index: int) -> int:
    ts = df["timestamp"].iloc[bar_index]
    return int(ts.hour)


def _volume_ok(df: pd.DataFrame, bar_index: int, min_ratio: float, lookback: int) -> bool:
    if min_ratio <= 0:
        return True
    tail = df["volume"].iloc[max(0, bar_index - lookback + 1) : bar_index + 1]
    if len(tail) < 3:
        return True
    mean_vol = float(tail.mean())
    if mean_vol <= 0:
        return True
    return float(df["volume"].iloc[bar_index]) >= mean_vol * min_ratio


def _in_utc_window(hour: int, cfg: AlertFilterConfig) -> bool:
    if not cfg.utc_hours_enabled:
        return True
    start, end = cfg.utc_hour_start, cfg.utc_hour_end
    if start <= end:
        return start <= hour < end
    return hour >= start or hour < end


def _sweep_rejected(zone: LiquidityZone, close: float) -> bool:
    if zone.zone_type == ZoneType.EQH:
        return close < zone.sweep_level
    return close > zone.sweep_level


def filter_zone(
    zone: LiquidityZone,
    df: pd.DataFrame,
    bar_index: int,
    cfg: AlertFilterConfig,
) -> FilterResult:
    if not cfg.alert_zone_detection:
        return FilterResult(FilterVerdict.REJECT, "detection EQH/EQL desactivee")

    if zone.score < cfg.min_zone_score:
        return FilterResult(FilterVerdict.REJECT, f"score {zone.score:.0f} < {cfg.min_zone_score:.0f}")

    width = _zone_width_pct(zone)
    if width > cfg.max_zone_width_pct:
        return FilterResult(FilterVerdict.REJECT, f"zone trop large {width:.3f}%")

    dist = _pivot_distance(zone)
    if dist < cfg.min_pivot_bars_apart:
        return FilterResult(FilterVerdict.REJECT, f"pivots trop proches ({dist} barres)")

    hour = _bar_hour(df, bar_index)
    if not _in_utc_window(hour, cfg):
        return FilterResult(FilterVerdict.REJECT, f"hors fenetre UTC {hour}h")

    if not _volume_ok(df, bar_index, cfg.volume_min_ratio, cfg.volume_lookback):
        return FilterResult(FilterVerdict.REJECT, "volume insuffisant")

    return FilterResult(FilterVerdict.PASS)


def filter_sweep(
    zone: LiquidityZone,
    sweep_type: str,
    df: pd.DataFrame,
    bar_index: int,
    cfg: AlertFilterConfig,
) -> FilterResult:
    if not cfg.alert_sweeps:
        return FilterResult(FilterVerdict.REJECT, "alertes sweep desactivees")

    if zone.score < cfg.min_sweep_score:
        return FilterResult(FilterVerdict.REJECT, f"score {zone.score:.0f} < {cfg.min_sweep_score:.0f}")

    width = _zone_width_pct(zone)
    if width > cfg.max_zone_width_pct:
        return FilterResult(FilterVerdict.REJECT, f"zone trop large {width:.3f}%")

    dist = _pivot_distance(zone)
    if dist < cfg.min_pivot_bars_apart:
        return FilterResult(FilterVerdict.REJECT, f"pivots trop proches ({dist} barres)")

    hour = _bar_hour(df, bar_index)
    if not _in_utc_window(hour, cfg):
        return FilterResult(FilterVerdict.REJECT, f"hors fenetre UTC {hour}h")

    if not _volume_ok(df, bar_index, cfg.volume_min_ratio, cfg.volume_lookback):
        return FilterResult(FilterVerdict.REJECT, "volume insuffisant")

    if cfg.sweep_confirm_next_bar:
        return FilterResult(FilterVerdict.PENDING, "attente confirmation bougie suivante")

    close = float(df["close"].iloc[bar_index])
    if cfg.sweep_require_rejection and not _sweep_rejected(zone, close):
        return FilterResult(FilterVerdict.REJECT, "pas de rejet (close) sur le sweep")

    return FilterResult(FilterVerdict.PASS)


def filter_sweep_confirm_bar(
    zone: LiquidityZone,
    df: pd.DataFrame,
    bar_index: int,
    cfg: AlertFilterConfig,
) -> FilterResult:
    """Confirmation sur bougie apres le sweep (confirm_next)."""
    close = float(df["close"].iloc[bar_index])
    if not _sweep_rejected(zone, close):
        return FilterResult(FilterVerdict.REJECT, "confirmation: close ne rejette pas")

    hour = _bar_hour(df, bar_index)
    if not _in_utc_window(hour, cfg):
        return FilterResult(FilterVerdict.REJECT, f"hors fenetre UTC {hour}h")

    return FilterResult(FilterVerdict.PASS, "confirm_next")
