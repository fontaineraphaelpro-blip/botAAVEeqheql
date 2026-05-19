"""Configuration centrale — logique alignée LuxAlgo EQH/EQL."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parent / ".env")


def _default_exchange() -> str:
    """Railway US : KuCoin (Bybit/Binance geo-bloques). Local : Bybit."""
    if os.getenv("EXCHANGE"):
        return os.getenv("EXCHANGE", "bybit")
    return "kucoin" if os.getenv("RAILWAY_ENVIRONMENT") else "bybit"


def _env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in ("1", "true", "yes", "on")


def _env_float(name: str, default: float) -> float:
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return default
    return float(raw)


def _env_int(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return default
    return int(raw)


def _env_int_opt(name: str) -> int | None:
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return None
    return int(raw)


@dataclass(frozen=True)
class PivotConfig:
    """Défauts LuxAlgo pour 5m (AAVE scalping)."""
    pivot_left: int = field(default_factory=lambda: _env_int("PIVOT_LEFT", 10))
    pivot_right: int = field(default_factory=lambda: _env_int("PIVOT_RIGHT", 2))
    threshold_pct: float = field(default_factory=lambda: _env_float("THRESHOLD_PCT", 0.03))
    max_pivot_history: int = 50
    max_active_zones: int = 60


@dataclass(frozen=True)
class ScanConfig:
    symbols: tuple[str, ...] = ("AAVE/USDT",)
    timeframes: tuple[str, ...] = ("5m",)
    ohlcv_limit: int = 300
    poll_interval_sec: float = 5.0
    min_bars: int = 80


@dataclass(frozen=True)
class ExchangeConfig:
    id: str = field(default_factory=_default_exchange)
    fallback: bool = field(
        default_factory=lambda: os.getenv(
            "EXCHANGE_FALLBACK",
            "true" if os.getenv("RAILWAY_ENVIRONMENT") else "false",
        ).lower()
        in ("1", "true", "yes")
    )
    api_key: str = field(default_factory=lambda: os.getenv("EXCHANGE_API_KEY", "") or os.getenv("BINANCE_API_KEY", ""))
    api_secret: str = field(default_factory=lambda: os.getenv("EXCHANGE_API_SECRET", "") or os.getenv("BINANCE_API_SECRET", ""))


@dataclass(frozen=True)
class VolumeFilterConfig:
    enabled: bool = False
    lookback: int = 20
    min_ratio: float = 0.8


@dataclass(frozen=True)
class CooldownConfig:
    signal_cooldown_sec: int = 300
    sweep_cooldown_sec: int = 180


@dataclass(frozen=True)
class AlertFilterConfig:
    """
    Filtres anti faux signaux (calibrés sur backtest KuCoin 6m).
    Par défaut : pas d'alerte EQH/EQL « formation », sweeps confirmés uniquement.
    """
    alert_zone_detection: bool = field(
        default_factory=lambda: _env_bool("ALERT_ZONE_DETECTION", False)
    )
    alert_sweeps: bool = field(default_factory=lambda: _env_bool("ALERT_SWEEPS", True))
    min_zone_score: float = field(default_factory=lambda: _env_float("MIN_ZONE_SCORE", 55.0))
    min_sweep_score: float = field(default_factory=lambda: _env_float("MIN_SWEEP_SCORE", 45.0))
    max_zone_width_pct: float = field(default_factory=lambda: _env_float("MAX_ZONE_WIDTH_PCT", 0.15))
    min_pivot_bars_apart: int = field(default_factory=lambda: _env_int("MIN_PIVOT_BARS_APART", 8))
    sweep_require_rejection: bool = field(
        default_factory=lambda: _env_bool("SWEEP_REQUIRE_REJECTION", True)
    )
    sweep_confirm_next_bar: bool = field(
        default_factory=lambda: _env_bool("SWEEP_CONFIRM_NEXT_BAR", True)
    )
    sweep_confirm_max_bars: int = field(
        default_factory=lambda: _env_int("SWEEP_CONFIRM_MAX_BARS", 2)
    )
    utc_hours_enabled: bool = field(
        default_factory=lambda: _env_bool("FILTER_UTC_HOURS", False)
    )
    utc_hour_start: int = field(default_factory=lambda: _env_int("UTC_HOUR_START", 12))
    utc_hour_end: int = field(default_factory=lambda: _env_int("UTC_HOUR_END", 22))
    volume_min_ratio: float = field(default_factory=lambda: _env_float("VOLUME_MIN_RATIO", 0.0))
    volume_lookback: int = 20


@dataclass(frozen=True)
class TelegramConfig:
    token: str = field(default_factory=lambda: os.getenv("TELEGRAM_BOT_TOKEN", ""))
    chat_id: str = field(default_factory=lambda: os.getenv("TELEGRAM_CHAT_ID", ""))


@dataclass
class AppConfig:
    exchange: ExchangeConfig = field(default_factory=ExchangeConfig)
    telegram: TelegramConfig = field(default_factory=TelegramConfig)
    pivot: PivotConfig = field(default_factory=PivotConfig)
    scan: ScanConfig = field(default_factory=ScanConfig)
    volume: VolumeFilterConfig = field(default_factory=VolumeFilterConfig)
    cooldown: CooldownConfig = field(default_factory=CooldownConfig)
    filter: AlertFilterConfig = field(default_factory=AlertFilterConfig)
    log_level: str = os.getenv("LOG_LEVEL", "INFO")
    log_file: str | None = field(
        default_factory=lambda: (
            None
            if os.getenv("RAILWAY_ENVIRONMENT")
            else os.getenv("LOG_FILE", "logs/bot.log") or None
        )
    )


def get_config() -> AppConfig:
    return AppConfig()
