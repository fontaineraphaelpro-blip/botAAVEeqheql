"""Configuration centrale — logique alignée LuxAlgo EQH/EQL."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parent / ".env")


def _default_exchange() -> str:
    """Par defaut KuCoin (aligne TradingView KUCOIN:AAVEUSDT)."""
    if os.getenv("EXCHANGE"):
        return os.getenv("EXCHANGE", "kucoin")
    if os.getenv("RAILWAY_ENVIRONMENT"):
        return "kucoin"
    return "kucoin"


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
    max_pivot_history: int = field(default_factory=lambda: _env_int("MAX_PIVOT_HISTORY", 80))
    max_active_zones: int = 60


TF_SECONDS = {"1m": 60, "5m": 300, "15m": 900, "1h": 3600}


@dataclass(frozen=True)
class ScanConfig:
    symbols: tuple[str, ...] = ("AAVE/USDT",)
    timeframes: tuple[str, ...] = ("5m",)
    ohlcv_limit: int = field(default_factory=lambda: _env_int("OHLCV_LIMIT", 600))
    """Secondes après clôture 5m avant requête KuCoin (donnée dispo côté exchange)."""
    candle_close_buffer_sec: float = field(
        default_factory=lambda: _env_float("CANDLE_CLOSE_BUFFER_SEC", 20.0)
    )
    """Délai minimum entre deux appels API (anti-429, sans bloquer le timing bougie)."""
    min_api_gap_sec: float = field(
        default_factory=lambda: _env_float("MIN_API_GAP_SEC", 10.0)
    )
    stale_retry_max: int = field(default_factory=lambda: _env_int("STALE_RETRY_MAX", 3))
    """Pages KuCoin au demarrage (150 barres/page, espacement 15s)."""
    kucoin_startup_pages: int = field(
        default_factory=lambda: _env_int("KUCOIN_STARTUP_PAGES", 3)
    )
    kucoin_startup_page_gap_sec: float = field(
        default_factory=lambda: _env_float("KUCOIN_STARTUP_PAGE_GAP_SEC", 15.0)
    )
    gap_fill_max_bars: int = field(default_factory=lambda: _env_int("GAP_FILL_MAX_BARS", 96))
    min_bars: int = 80


@dataclass(frozen=True)
class ExchangeConfig:
    id: str = field(default_factory=_default_exchange)
    fallback: bool = field(
        default_factory=lambda: os.getenv("EXCHANGE_FALLBACK", "true").lower()
        in ("1", "true", "yes")
    )
    api_key: str = field(default_factory=lambda: os.getenv("EXCHANGE_API_KEY", "") or os.getenv("BINANCE_API_KEY", ""))
    api_secret: str = field(default_factory=lambda: os.getenv("EXCHANGE_API_SECRET", "") or os.getenv("BINANCE_API_SECRET", ""))


@dataclass(frozen=True)
class TradingConfig:
    """Paramètres du bot de paper trading (validés par backtest 12 mois).

    Règle de base : clôture au-dessus de l'EMA (ligne grise) => LONG,
    en dessous => SHORT. Filtres : tendance 4h (EMA50>EMA200) + efficiency
    ratio pour éviter le chop. Stop ATR initial + trailing chandelier.
    """

    start_balance: float = field(default_factory=lambda: _env_float("START_BALANCE", 1000.0))
    position_pct: float = field(default_factory=lambda: _env_float("POSITION_PCT", 100.0))
    signal_tf_min: int = field(default_factory=lambda: _env_int("SIGNAL_TF_MIN", 30))
    ema_len: int = field(default_factory=lambda: _env_int("EMA_LEN", 20))
    atr_len: int = field(default_factory=lambda: _env_int("ATR_LEN", 14))
    stop_atr: float = field(default_factory=lambda: _env_float("STOP_ATR", 2.5))
    trail_atr: float = field(default_factory=lambda: _env_float("TRAIL_ATR", 3.0))
    er_len: int = field(default_factory=lambda: _env_int("ER_LEN", 20))
    er_min: float = field(default_factory=lambda: _env_float("ER_MIN", 0.35))
    htf_fast: int = field(default_factory=lambda: _env_int("HTF_FAST", 50))
    htf_slow: int = field(default_factory=lambda: _env_int("HTF_SLOW", 200))
    htf_tf_min: int = field(default_factory=lambda: _env_int("HTF_TF_MIN", 240))
    fee_pct: float = field(default_factory=lambda: _env_float("FEE_PCT", 0.05))
    slippage_pct: float = field(default_factory=lambda: _env_float("SLIPPAGE_PCT", 0.03))
    state_file: str = field(
        default_factory=lambda: os.getenv("PAPER_STATE_FILE", "data/tendance_state.json")
    )
    daily_report_hour_utc: int = field(
        default_factory=lambda: _env_int("DAILY_REPORT_HOUR_UTC", 7)
    )


SHORT_UNIVERSE = (
    "ETHUSDT", "BNBUSDT", "XRPUSDT", "ADAUSDT", "SOLUSDT", "DOGEUSDT",
    "DOTUSDT", "LTCUSDT", "LINKUSDT", "AVAXUSDT", "UNIUSDT", "ATOMUSDT",
    "ETCUSDT", "XLMUSDT", "FILUSDT", "AAVEUSDT", "SANDUSDT", "MANAUSDT",
    "AXSUSDT", "NEARUSDT", "ALGOUSDT", "CRVUSDT", "COMPUSDT", "SNXUSDT",
    "SUSHIUSDT", "1INCHUSDT", "GALAUSDT", "CHZUSDT", "ENJUSDT", "THETAUSDT",
    "XTZUSDT", "GRTUSDT", "RUNEUSDT", "KSMUSDT",
)


@dataclass(frozen=True)
class ShortsConfig:
    """Chasseur de shorts multi-alts (backtest 2017-2026 : +225%, DD -32%).

    Short uniquement quand BTC est en bear strict (prix < EMA 200j ET EMA en
    baisse sur 30j). Entrée sur cassure du plus-bas 10 jours avec chute
    directionnelle (ER). Max K positions, priorité aux alts les plus faibles.
    """

    start_balance: float = field(default_factory=lambda: _env_float("SHORTS_START_BALANCE", 1000.0))
    max_positions: int = field(default_factory=lambda: _env_int("SHORTS_MAX_POSITIONS", 5))
    bar_tf_min: int = 120
    low_n: int = field(default_factory=lambda: _env_int("SHORTS_LOW_N", 120))
    er_len: int = field(default_factory=lambda: _env_int("SHORTS_ER_LEN", 20))
    er_min: float = field(default_factory=lambda: _env_float("SHORTS_ER_MIN", 0.35))
    atr_len: int = field(default_factory=lambda: _env_int("SHORTS_ATR_LEN", 14))
    stop_atr: float = field(default_factory=lambda: _env_float("SHORTS_STOP_ATR", 3.0))
    trail_atr: float = field(default_factory=lambda: _env_float("SHORTS_TRAIL_ATR", 4.0))
    btc_ema_days: int = 200
    btc_slope_days: int = 30
    fee_pct: float = field(default_factory=lambda: _env_float("SHORTS_FEE_PCT", 0.05))
    slippage_pct: float = field(default_factory=lambda: _env_float("SHORTS_SLIPPAGE_PCT", 0.05))
    funding_pct_8h: float = field(default_factory=lambda: _env_float("SHORTS_FUNDING_PCT_8H", 0.01))
    state_file: str = field(
        default_factory=lambda: os.getenv("SHORTS_STATE_FILE", "data/chasseur_state.json")
    )
    universe: tuple[str, ...] = SHORT_UNIVERSE
    candle_close_buffer_sec: float = 45.0


@dataclass(frozen=True)
class AnalystConfig:
    """Mémoire de motifs AAVE 5m + paper trading pour apprendre des erreurs."""

    lookback: int = field(default_factory=lambda: _env_int("ANALYST_LOOKBACK", 24))
    horizon: int = field(default_factory=lambda: _env_int("ANALYST_HORIZON", 12))
    top_k: int = field(default_factory=lambda: _env_int("ANALYST_TOP_K", 40))
    min_matches: int = field(default_factory=lambda: _env_int("ANALYST_MIN_MATCHES", 1))
    min_confidence: float = field(
        default_factory=lambda: _env_float("ANALYST_MIN_CONFIDENCE", 0.0)
    )
    flat_pct: float = field(default_factory=lambda: _env_float("ANALYST_FLAT_PCT", 0.20))
    max_distance: float = field(
        default_factory=lambda: _env_float("ANALYST_MAX_DISTANCE", 1.0)
    )
    memory_file: str = field(
        default_factory=lambda: os.getenv(
            "ANALYST_MEMORY_FILE", "models/analyst_memory.npz"
        )
    )
    runtime_memory_file: str = field(
        default_factory=lambda: os.getenv(
            "ANALYST_RUNTIME_MEMORY", "data/analyst_memory.npz"
        )
    )
    state_file: str = field(
        default_factory=lambda: os.getenv(
            "ANALYST_STATE_FILE", "data/analyst_state.json"
        )
    )
    paper_state_file: str = field(
        default_factory=lambda: os.getenv(
            "ANALYST_PAPER_STATE", "data/analyst_paper.json"
        )
    )
    start_balance: float = field(
        default_factory=lambda: _env_float("ANALYST_START_BALANCE", 1000.0)
    )
    position_pct: float = field(
        default_factory=lambda: _env_float("ANALYST_POSITION_PCT", 100.0)
    )
    fee_pct: float = field(default_factory=lambda: _env_float("ANALYST_FEE_PCT", 0.05))
    slippage_pct: float = field(
        default_factory=lambda: _env_float("ANALYST_SLIPPAGE_PCT", 0.03)
    )
    stop_pct: float = field(default_factory=lambda: _env_float("ANALYST_STOP_PCT", 2.5))
    alert_cooldown_bars: int = field(
        default_factory=lambda: _env_int("ANALYST_ALERT_COOLDOWN", 1)
    )
    memory_save_every: int = field(
        default_factory=lambda: _env_int("ANALYST_MEMORY_SAVE_EVERY", 5)
    )
    correlations_file: str = field(
        default_factory=lambda: os.getenv(
            "ANALYST_CORR_FILE", "data/analyst_correlations.json"
        )
    )
    # Analyse + prédiction à chaque bougie 5m ; paper dès qu'une direction sort
    continuous: bool = field(
        default_factory=lambda: os.getenv("ANALYST_CONTINUOUS", "true").lower()
        in ("1", "true", "yes")
    )
    # Telegram à chaque prédiction (sinon seulement trades open/close)
    telegram_every_pred: bool = field(
        default_factory=lambda: os.getenv("ANALYST_TG_EVERY_PRED", "false").lower()
        in ("1", "true", "yes")
    )


@dataclass(frozen=True)
class TelegramConfig:
    token: str = field(default_factory=lambda: os.getenv("TELEGRAM_BOT_TOKEN", ""))
    chat_id: str = field(default_factory=lambda: os.getenv("TELEGRAM_CHAT_ID", ""))


@dataclass
class AppConfig:
    exchange: ExchangeConfig = field(default_factory=ExchangeConfig)
    telegram: TelegramConfig = field(default_factory=TelegramConfig)
    trading: TradingConfig = field(default_factory=TradingConfig)
    shorts: ShortsConfig = field(default_factory=ShortsConfig)
    analyst: AnalystConfig = field(default_factory=AnalystConfig)
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
