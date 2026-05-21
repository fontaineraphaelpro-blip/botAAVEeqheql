"""OHLCV — refresh aligné bougie, scan depuis cache (pas de spam API)."""

from __future__ import annotations

import asyncio
import os
import time
from typing import Dict, List, Tuple

import aiohttp
import pandas as pd

from config import AppConfig, TF_SECONDS
from scanner.ohlcv_providers import (
    KUCOIN_MAX_LIMIT,
    RAILWAY_CHAIN,
    OhlcvProvider,
    RateLimitError,
    create_provider,
)
from utils.api_budget import kucoin_is_banned
from utils.logger import setup_logger

logger = setup_logger(__name__)

CacheKey = Tuple[str, str]
BOT_DATA_VERSION = "2026-05-21-scan-v11"
LIVE_TAIL_BARS = 15
RETRY_AFTER_FETCH_SEC = 15.0


def _live_use_kucoin() -> bool:
    return os.getenv("LIVE_USE_KUCOIN", "false").lower() in ("1", "true", "yes")


def _default_live_provider() -> str:
    explicit = os.getenv("LIVE_REFRESH_PROVIDER", "").strip().lower()
    if explicit:
        return explicit
    if os.getenv("RAILWAY_ENVIRONMENT") and not _live_use_kucoin():
        return "binance_vision"
    return ""


class MarketDataService:
    def __init__(self, config: AppConfig) -> None:
        self.config = config
        self._cache: Dict[CacheKey, pd.DataFrame] = {}
        self._locks: Dict[CacheKey, asyncio.Lock] = {}
        self._last_api_fetch: Dict[CacheKey, float] = {}
        self._last_closed_ts: Dict[CacheKey, int] = {}
        self._retry_at: Dict[CacheKey, float] = {}
        self._live_provider: str | None = _default_live_provider() or None
        self._provider: OhlcvProvider | None = None
        self._provider_chain: List[str] = []
        self._providers: Dict[str, OhlcvProvider] = {}
        self.exchange_id: str = ""

    def _build_provider_chain(self) -> List[str]:
        primary = self.config.exchange.id.lower().strip()
        chain: List[str] = [primary]
        if self.config.exchange.fallback or os.getenv("RAILWAY_ENVIRONMENT"):
            for pid in RAILWAY_CHAIN:
                if pid not in chain:
                    chain.append(pid)
        return chain

    async def start(self) -> None:
        self._provider_chain = self._build_provider_chain()
        errors: List[str] = []
        for provider_id in self._chain_for_history():
            try:
                provider = self._get_provider(provider_id)
                symbol = self.config.scan.symbols[0]
                tf = self.config.scan.timeframes[0]
                await provider.fetch(symbol, tf, limit=5)
                self._provider = provider
                self.exchange_id = provider.name
                if not self._live_provider:
                    self._live_provider = provider.name
                logger.info(
                    "Donnees marche connectees : %s (live=%s, build %s)",
                    provider.name,
                    self._live_provider or provider.name,
                    BOT_DATA_VERSION,
                )
                return
            except Exception as exc:
                errors.append(f"{provider_id}: {exc}")
                logger.warning("Echec %s — %s", provider_id, exc)

        raise RuntimeError(
            "Aucune source OHLCV disponible. "
            "Details: " + " | ".join(errors)
        )

    def _get_provider(self, provider_id: str) -> OhlcvProvider:
        if provider_id not in self._providers:
            self._providers[provider_id] = create_provider(provider_id)
        return self._providers[provider_id]

    def _chain_for_history(self) -> List[str]:
        chain = list(self._provider_chain)
        if self._provider and self._provider.name not in chain:
            chain.insert(0, self._provider.name)
        chain = [p for p in chain if p != "kucoin" or not kucoin_is_banned()]
        limit = self.config.scan.ohlcv_limit
        if limit > KUCOIN_MAX_LIMIT:
            chain = [p for p in chain if p != "kucoin"]
        preferred = ["binance_vision", "mexc", "bybit"]
        return [p for p in preferred if p in chain] + [p for p in chain if p not in preferred]

    def _chain_for_live(self) -> List[str]:
        """Refresh live : provider stable, KuCoin seulement si explicitement active."""
        chain = list(self._provider_chain)
        if not _live_use_kucoin() or kucoin_is_banned():
            chain = [p for p in chain if p != "kucoin"]

        preferred: List[str] = []
        if self._live_provider and self._live_provider in chain:
            preferred.append(self._live_provider)
        default_live = _default_live_provider()
        if default_live and default_live not in preferred:
            preferred.append(default_live)
        for pid in ("binance_vision", "mexc", "bybit"):
            if pid not in preferred:
                preferred.append(pid)

        return [p for p in preferred if p in chain] + [p for p in chain if p not in preferred]

    async def _fetch_with_fallback(
        self, symbol: str, timeframe: str, limit: int, *, live: bool
    ) -> Tuple[list, str]:
        errors: List[str] = []
        chain = self._chain_for_live() if live else self._chain_for_history()
        logged_429: set[str] = set()

        for provider_id in chain:
            provider = self._get_provider(provider_id)
            try:
                raw = await provider.fetch(symbol, timeframe, limit=limit)
                if not raw:
                    errors.append(f"{provider_id}: empty")
                    continue
                self._provider = provider
                self.exchange_id = provider.name
                if live:
                    self._live_provider = provider.name
                return raw, provider.name
            except RateLimitError:
                errors.append(f"{provider_id}: 429")
                if provider_id not in logged_429:
                    logger.warning("429 %s — fallback", provider_id)
                    logged_429.add(provider_id)
                continue
            except aiohttp.ClientResponseError as exc:
                errors.append(f"{provider_id}: HTTP {exc.status}")
                if exc.status == 429:
                    if provider_id not in logged_429:
                        logger.warning("429 %s — fallback", provider_id)
                        logged_429.add(provider_id)
                    continue
                raise
            except ValueError as exc:
                errors.append(f"{provider_id}: {exc}")
                continue
            except Exception as exc:
                errors.append(f"{provider_id}: {exc}")
                logger.warning("Echec %s — %s", provider_id, exc)

        raise RuntimeError("Tous les providers OHLCV ont echoue: " + " | ".join(errors))

    async def close(self) -> None:
        for provider in self._providers.values():
            try:
                await provider.close()
            except Exception as exc:
                logger.warning("Fermeture provider: %s", exc)
        self._providers.clear()
        self._provider = None

    def _lock(self, key: CacheKey) -> asyncio.Lock:
        if key not in self._locks:
            self._locks[key] = asyncio.Lock()
        return self._locks[key]

    def _last_closed_timestamp(self, cached: pd.DataFrame) -> pd.Timestamp:
        if len(cached) < 2:
            return cached["timestamp"].iloc[-1]
        return cached["timestamp"].iloc[-2]

    def next_refresh_at(self, symbol: str, timeframe: str) -> pd.Timestamp | None:
        key = (symbol, timeframe)
        cached = self._cache.get(key)
        if cached is None or len(cached) < 2:
            return None

        tf_sec = TF_SECONDS.get(timeframe, 300)
        buffer = self.config.scan.candle_close_buffer_sec
        last_closed = self._last_closed_timestamp(cached)
        return last_closed + pd.Timedelta(seconds=tf_sec + buffer)

    def seconds_until_refresh(self, symbol: str, timeframe: str) -> float:
        """Attente jusqu'à la prochaine clôture 5m (+ buffer) ou retry court."""
        key = (symbol, timeframe)
        retry_at = self._retry_at.get(key)
        if retry_at is not None:
            return max(1.0, retry_at - time.monotonic())

        nxt = self.next_refresh_at(symbol, timeframe)
        if nxt is None:
            return 30.0
        delay = (nxt - pd.Timestamp.now(tz="UTC")).total_seconds()
        return max(1.0, delay)

    def _should_refresh(self, key: CacheKey, cached: pd.DataFrame) -> bool:
        min_gap = self.config.scan.min_api_gap_sec
        if time.monotonic() - self._last_api_fetch.get(key, 0.0) < min_gap:
            return False

        nxt = self.next_refresh_at(key[0], key[1])
        if nxt is None:
            return True
        return pd.Timestamp.now(tz="UTC") >= nxt

    async def load_history(self, symbol: str, timeframe: str) -> pd.DataFrame:
        """Bootstrap : une requete bulk (Binance Vision)."""
        key = (symbol, timeframe)
        async with self._lock(key):
            limit = self.config.scan.ohlcv_limit
            raw, src = await self._fetch_with_fallback(
                symbol, timeframe, limit, live=False
            )
            df = self._to_df(raw)
            self._cache[key] = df
            self._last_api_fetch[key] = time.monotonic()
            self._last_closed_ts[key] = self.last_closed_ts(df)
            if not self._live_provider:
                self._live_provider = src
            logger.info("Historique via %s (%d barres)", src, len(df))
            return df.copy()

    async def refresh_if_due(self, symbol: str, timeframe: str) -> bool:
        """
        Max 1 requete API par bougie 5m. Retourne True si une nouvelle bougie fermee
        a ete ajoutee au cache.
        """
        key = (symbol, timeframe)
        async with self._lock(key):
            cached = self._cache.get(key)
            if cached is None or len(cached) < self.config.scan.min_bars:
                return False

            if not self._should_refresh(key, cached):
                return False

            prev_closed_ts = self._last_closed_ts.get(key)
            limit = self.config.scan.ohlcv_limit

            try:
                raw, src = await self._fetch_with_fallback(
                    symbol, timeframe, LIVE_TAIL_BARS, live=True
                )
            except Exception as exc:
                logger.warning("Refresh %s %s: %s — cache", symbol, timeframe, exc)
                return False

            self._last_api_fetch[key] = time.monotonic()

            if raw:
                new_df = self._to_df(raw)
                merged = (
                    pd.concat([cached, new_df])
                    .drop_duplicates(subset=["timestamp"])
                    .sort_values("timestamp")
                )
                self._cache[key] = merged.tail(limit).reset_index(drop=True)

            df = self._cache[key]
            new_closed_ts = self.last_closed_ts(df)
            if prev_closed_ts is not None and new_closed_ts <= prev_closed_ts:
                self._retry_at[key] = time.monotonic() + RETRY_AFTER_FETCH_SEC
                logger.info(
                    "API en retard %s %s — retry scan dans %.0fs",
                    symbol,
                    timeframe,
                    RETRY_AFTER_FETCH_SEC,
                )
                return False

            self._retry_at.pop(key, None)
            self._last_closed_ts[key] = new_closed_ts
            logger.info(
                "Nouvelle bougie %s %s via %s (ts=%s)",
                symbol,
                timeframe,
                src,
                pd.Timestamp(new_closed_ts, unit="s", tz="UTC"),
            )
            return True

    async def fetch_ohlcv(self, symbol: str, timeframe: str) -> pd.DataFrame:
        """Compat : historique ou refresh + cache."""
        if not self.has_cache(symbol, timeframe):
            return await self.load_history(symbol, timeframe)
        await self.refresh_if_due(symbol, timeframe)
        return self.get_cached(symbol, timeframe)

    def has_cache(self, symbol: str, timeframe: str) -> bool:
        key = (symbol, timeframe)
        cached = self._cache.get(key)
        return cached is not None and len(cached) >= self.config.scan.min_bars

    def get_cached(self, symbol: str, timeframe: str) -> pd.DataFrame:
        key = (symbol, timeframe)
        cached = self._cache.get(key)
        if cached is None:
            raise RuntimeError(f"Pas de cache pour {symbol} {timeframe}")
        return cached.copy()

    @staticmethod
    def _to_df(raw: list) -> pd.DataFrame:
        if not raw:
            raise ValueError("OHLCV vide")
        df = pd.DataFrame(
            raw, columns=["timestamp", "open", "high", "low", "close", "volume"]
        )
        df["timestamp"] = pd.to_datetime(df["timestamp"], unit="ms", utc=True)
        for col in ("open", "high", "low", "close", "volume"):
            df[col] = df[col].astype(float)
        return df

    @staticmethod
    def closed_bars(df: pd.DataFrame) -> pd.DataFrame:
        if len(df) < 2:
            return df
        return df.iloc[:-1].copy()

    def last_closed_ts(self, df: pd.DataFrame) -> int:
        closed = self.closed_bars(df)
        if closed.empty:
            raise ValueError("Aucune bougie fermee")
        return int(closed["timestamp"].iloc[-1].timestamp())
