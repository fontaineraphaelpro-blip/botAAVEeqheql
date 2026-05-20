"""Configuration centrale — logique alignée LuxAlgo EQH/EQL."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parent / ".env")


def _default_exchange() -> str:
    """Railway : MEXC (1 req, pas de 429). Local : Bybit. Override via EXCHANGE."""
    if os.getenv("EXCHANGE"):
        return os.getenv("EXCHANGE", "bybit")
    if os.getenv("RAILWAY_ENVIRONMENT"):
        return "kucoin"
    return "bybit"


def _env_int(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return default
    return int(raw)


def _env_float(name: str, default: float) -> float:
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return default
    return float(raw)


@dataclass(frozen=True)
class PivotConfig:
    """Défauts LuxAlgo pour 5m (AAVE scalping)."""
    pivot_left: int = field(default_factory=lambda: _env_int("PIVOT_LEFT", 10))
    pivot_right: int = field(default_factory=lambda: _env_int("PIVOT_RIGHT", 2))
    threshold_pct: float = field(default_factory=lambda: _env_float("THRESHOLD_PCT", 0.03))
    max_pivot_history: int = 50
    max_active_zones: int = 60


TF_SECONDS = {"1m": 60, "5m": 300, "15m": 900, "1h": 3600}


@dataclass(frozen=True)
class ScanConfig:
    symbols: tuple[str, ...] = ("AAVE/USDT",)
    timeframes: tuple[str, ...] = ("5m",)
    ohlcv_limit: int = 300
    """Secondes après clôture 5m avant requête KuCoin (donnée dispo côté exchange)."""
    candle_close_buffer_sec: float = field(
        default_factory=lambda: _env_float("CANDLE_CLOSE_BUFFER_SEC", 10.0)
    )
    """Délai minimum entre deux appels API (anti-429, sans bloquer le timing bougie)."""
    min_api_gap_sec: float = field(
        default_factory=lambda: _env_float("MIN_API_GAP_SEC", 5.0)
    )
    stale_retry_max: int = field(default_factory=lambda: _env_int("STALE_RETRY_MAX", 3))
    """Au demarrage, re-scanne les N dernieres bougies fermees et envoie les notifs manquees."""
    catchup_bars: int = field(default_factory=lambda: _env_int("CATCHUP_BARS", 48))
    catchup_max_bars: int = field(default_factory=lambda: _env_int("CATCHUP_MAX_BARS", 96))
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
class TelegramConfig:
    token: str = field(default_factory=lambda: os.getenv("TELEGRAM_BOT_TOKEN", ""))
    chat_id: str = field(default_factory=lambda: os.getenv("TELEGRAM_CHAT_ID", ""))


@dataclass
class AppConfig:
    exchange: ExchangeConfig = field(default_factory=ExchangeConfig)
    telegram: TelegramConfig = field(default_factory=TelegramConfig)
    pivot: PivotConfig = field(default_factory=PivotConfig)
    scan: ScanConfig = field(default_factory=ScanConfig)
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
