"""Configuration centrale — logique alignée LuxAlgo EQH/EQL."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parent / ".env")


@dataclass(frozen=True)
class PivotConfig:
    """Défauts LuxAlgo pour 5m (AAVE scalping)."""
    pivot_left: int = 10
    pivot_right: int = 2
    threshold_pct: float = 0.03
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
class VolumeFilterConfig:
    enabled: bool = False
    lookback: int = 20
    min_ratio: float = 0.8


@dataclass(frozen=True)
class CooldownConfig:
    signal_cooldown_sec: int = 300
    sweep_cooldown_sec: int = 180


@dataclass(frozen=True)
class TelegramConfig:
    token: str = field(default_factory=lambda: os.getenv("TELEGRAM_BOT_TOKEN", ""))
    chat_id: str = field(default_factory=lambda: os.getenv("TELEGRAM_CHAT_ID", ""))


@dataclass(frozen=True)
class BinanceConfig:
    api_key: str = field(default_factory=lambda: os.getenv("BINANCE_API_KEY", ""))
    api_secret: str = field(default_factory=lambda: os.getenv("BINANCE_API_SECRET", ""))


@dataclass
class AppConfig:
    binance: BinanceConfig = field(default_factory=BinanceConfig)
    telegram: TelegramConfig = field(default_factory=TelegramConfig)
    pivot: PivotConfig = field(default_factory=PivotConfig)
    scan: ScanConfig = field(default_factory=ScanConfig)
    volume: VolumeFilterConfig = field(default_factory=VolumeFilterConfig)
    cooldown: CooldownConfig = field(default_factory=CooldownConfig)
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
