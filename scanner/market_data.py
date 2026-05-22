"""OHLCV — une seule source par session (alignée TradingView)."""

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
    KucoinProvider,
    OhlcvProvider,
    RateLimitError,
    create_provider,
)
from utils.api_budget import kucoin_is_banned
from utils.logger import setup_logger

logger = setup_logger(__name__)

CacheKey = Tuple[str, str]
BOT_DATA_VERSION = "2026-05-22-source-v13"
LIVE_TAIL_BARS = 15
RETRY_AFTER_FETCH_SEC = 15.0

TRADINGVIEW_CHART = {
    "kucoin": "KUCOIN:AAVEUSDT",
    "binance_vision": "BINANCE:AAVEUSDT",
    "binance": "BINANCE:AAVEUSDT",
    "mexc": "MEXC:AAVEUSDT",
    "bybit": "BYBIT:AAVEUSDT",
}


def _normalize_provider_id(provider_id: str) -> str:
    pid = provider_id.lower().strip()
    if pid == "binance":
        return "binance_vision"
    return pid


def _configured_provider(config: AppConfig) -> str:
    explicit = os.getenv("DATA_PROVIDER", "").strip().lower()
    if explicit:
        return _normalize_provider_id(explicit)
    return _normalize_provider_id(config.exchange.id)


class MarketDataService:
    def __init__(self, config: AppConfig) -> None:
        self.config = config
        self._cache: Dict[CacheKey, pd.DataFrame] = {}
        self._cache_source: Dict[CacheKey, str] = {}
        self._locks: Dict[CacheKey, asyncio.Lock] = {}
        self._last_api_fetch: Dict[CacheKey, float] = {}
        self._last_closed_ts: Dict[CacheKey, int] = {}
        self._retry_at: Dict[CacheKey, float] = {}
        self._data_provider = _configured_provider(config)
        self._provider: OhlcvProvider | None = None
        self._providers: Dict[str, OhlcvProvider] = {}
        self.exchange_id: str = self._data_provider

    def tradingview_chart(self) -> str:
        src = self._data_provider
        if self._cache_source:
            src = next(iter(self._cache_source.values()), src)
        return TRADINGVIEW_CHART.get(src, "BINANCE:AAVEUSDT")

    def data_source_label(self) -> str:
        return self.tradingview_chart().split(":")[0]

    def _fetch_chain(self) -> List[str]:
        """Une seule source — pas de melange Binance + KuCoin dans le cache."""
        primary = self._data_provider
        chain = [primary]
        if primary == "kucoin" and kucoin_is_banned():
            chain = []
        if self.config.exchange.fallback:
            for pid in ("mexc", "bybit", "binance_vision"):
                if pid != primary and pid not in chain:
                    if pid == "kucoin" and kucoin_is_banned():
                        continue
                    chain.append(pid)
        return chain

    def _kucoin_startup_pages(self) -> int:
        return max(1, self.config.scan.kucoin_startup_pages)

    def _history_limit(self) -> int:
        limit = self.config.scan.ohlcv_limit
        if self._data_provider == "kucoin":
            cap = KUCOIN_MAX_LIMIT * self._kucoin_startup_pages()
            return min(limit, cap)
        return limit

    async def _fetch_kucoin_history(
        self, symbol: str, timeframe: str, limit: int
    ) -> list:
        provider = self._get_provider("kucoin")
        if not isinstance(provider, KucoinProvider):
            raise TypeError("Provider kucoin attendu")
        return await provider.fetch_paginated(
            symbol,
            timeframe,
            limit,
            max_pages=self._kucoin_startup_pages(),
            page_gap_sec=self.config.scan.kucoin_startup_page_gap_sec,
        )

    async def start(self) -> None:
        errors: List[str] = []
        for provider_id in self._fetch_chain():
            try:
                provider = self._get_provider(provider_id)
                symbol = self.config.scan.symbols[0]
                tf = self.config.scan.timeframes[0]
                await provider.fetch(symbol, tf, limit=5)
                self._provider = provider
                self._data_provider = provider.name
                self.exchange_id = provider.name
                logger.info(
                    "Source unique : %s | Chart TV : %s | build %s",
                    provider.name,
                    self.tradingview_chart(),
                    BOT_DATA_VERSION,
                )
                return
            except Exception as exc:
                errors.append(f"{provider_id}: {exc}")
                logger.warning("Echec %s — %s", provider_id, exc)

        raise RuntimeError(
            "Aucune source OHLCV disponible. Details: " + " | ".join(errors)
        )

    def _get_provider(self, provider_id: str) -> OhlcvProvider:
        pid = _normalize_provider_id(provider_id)
        if pid not in self._providers:
            self._providers[pid] = create_provider(pid)
        return self._providers[pid]

    async def _fetch_with_fallback(
        self, symbol: str, timeframe: str, limit: int
    ) -> Tuple[list, str]:
        errors: List[str] = []
        logged_429: set[str] = set()

        for provider_id in self._fetch_chain():
            provider = self._get_provider(provider_id)
            try:
                raw = await provider.fetch(symbol, timeframe, limit=limit)
                if not raw:
                    errors.append(f"{provider_id}: empty")
                    continue
                self._provider = provider
                self.exchange_id = provider.name
                return raw, provider.name
            except RateLimitError:
                errors.append(f"{provider_id}: 429")
                if provider_id not in logged_429:
                    logger.warning("429 %s — fallback", provider_id)
                    logged_429.add(provider_id)
                continue
            except aiohttp.ClientResponseError as exc:
                errors.append(f"{provider_id}: HTTP {exc.status}")
                if exc.status == 429 and provider_id not in logged_429:
                    logger.warning("429 %s — fallback", provider_id)
                    logged_429.add(provider_id)
                    continue
                raise
            except ValueError as exc:
                errors.append(f"{provider_id}: {exc}")
                continue
            except Exception as exc:
                errors.append(f"{provider_id}: {exc}")
                logger.warning("Echec fetch %s — %s", provider_id, exc)

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

    def _store_cache(self, key: CacheKey, df: pd.DataFrame, source: str) -> None:
        prev = self._cache_source.get(key)
        if prev and prev != source:
            logger.warning(
                "Changement source %s -> %s — remplacement cache (pas de melange)",
                prev,
                source,
            )
        self._cache[key] = df
        self._cache_source[key] = source
        self._data_provider = source
        self.exchange_id = source

    async def load_history(self, symbol: str, timeframe: str) -> pd.DataFrame:
        key = (symbol, timeframe)
        async with self._lock(key):
            limit = self._history_limit()
            primary = _configured_provider(self.config)

            if primary == "kucoin" and "kucoin" in self._fetch_chain():
                try:
                    raw = await self._fetch_kucoin_history(symbol, timeframe, limit)
                    src = "kucoin"
                except Exception as exc:
                    logger.warning("Historique KuCoin pagine echoue: %s — fallback", exc)
                    raw, src = await self._fetch_with_fallback(symbol, timeframe, limit)
            else:
                raw, src = await self._fetch_with_fallback(symbol, timeframe, limit)

            df = self._to_df(raw)
            self._store_cache(key, df, src)
            self._last_api_fetch[key] = time.monotonic()
            self._last_closed_ts[key] = self.last_closed_ts(df)
            hours = (len(df) - 1) * TF_SECONDS.get(timeframe, 300) / 3600
            logger.info(
                "Historique %s (%d barres, ~%.1fh) — chart TV : %s",
                src,
                len(df),
                hours,
                TRADINGVIEW_CHART.get(src, "?"),
            )
            return df.copy()

    async def refresh_if_due(self, symbol: str, timeframe: str) -> bool:
        key = (symbol, timeframe)
        async with self._lock(key):
            cached = self._cache.get(key)
            if cached is None or len(cached) < self.config.scan.min_bars:
                return False

            if not self._should_refresh(key, cached):
                return False

            prev_closed_ts = self._last_closed_ts.get(key)
            limit = self._history_limit()
            expected_src = self._cache_source.get(key, self._data_provider)

            try:
                raw, src = await self._fetch_with_fallback(
                    symbol, timeframe, LIVE_TAIL_BARS
                )
            except Exception as exc:
                logger.warning("Refresh %s %s: %s — cache", symbol, timeframe, exc)
                return False

            self._last_api_fetch[key] = time.monotonic()

            if src != expected_src:
                logger.warning(
                    "Refresh source %s != cache %s — recharge fenetre complete",
                    src,
                    expected_src,
                )
                raw_full, src = await self._fetch_with_fallback(
                    symbol, timeframe, limit
                )
                df = self._to_df(raw_full)
                self._store_cache(key, df, src)
            elif raw:
                new_df = self._to_df(raw)
                merged = (
                    pd.concat([cached, new_df])
                    .drop_duplicates(subset=["timestamp"])
                    .sort_values("timestamp")
                )
                self._store_cache(
                    key, merged.tail(limit).reset_index(drop=True), expected_src
                )

            df = self._cache[key]
            new_closed_ts = self.last_closed_ts(df)
            if prev_closed_ts is not None and new_closed_ts <= prev_closed_ts:
                self._retry_at[key] = time.monotonic() + RETRY_AFTER_FETCH_SEC
                logger.info(
                    "API en retard %s %s — retry dans %.0fs",
                    symbol,
                    timeframe,
                    RETRY_AFTER_FETCH_SEC,
                )
                return False

            self._retry_at.pop(key, None)
            self._last_closed_ts[key] = new_closed_ts
            logger.info(
                "Nouvelle bougie %s %s (%s)",
                symbol,
                timeframe,
                pd.Timestamp(new_closed_ts, unit="s", tz="UTC"),
            )
            return True

    async def fetch_ohlcv(self, symbol: str, timeframe: str) -> pd.DataFrame:
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
