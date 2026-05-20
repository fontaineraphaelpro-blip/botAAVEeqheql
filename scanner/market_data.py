"""OHLCV AAVE/USDT — requêtes alignées clôture + fallback anti-429."""

from __future__ import annotations

import asyncio
import os
import time
from typing import Dict, List, Tuple

import aiohttp
import pandas as pd

from config import AppConfig, TF_SECONDS
from scanner.ohlcv_providers import RAILWAY_CHAIN, OhlcvProvider, create_provider
from utils.logger import setup_logger

logger = setup_logger(__name__)

CacheKey = Tuple[str, str]
BOT_DATA_VERSION = "2026-05-21-robust-v4"


class MarketDataService:
    def __init__(self, config: AppConfig) -> None:
        self.config = config
        self._cache: Dict[CacheKey, pd.DataFrame] = {}
        self._locks: Dict[CacheKey, asyncio.Lock] = {}
        self._last_api_fetch: Dict[CacheKey, float] = {}
        self._last_closed_ts: Dict[CacheKey, int] = {}
        self._stale_retries: Dict[CacheKey, int] = {}
        self._provider: OhlcvProvider | None = None
        self._provider_chain: List[str] = []
        self._providers: Dict[str, OhlcvProvider] = {}
        self.exchange_id: str = ""

    async def start(self) -> None:
        primary = self.config.exchange.id.lower().strip()
        chain: List[str] = [primary]

        if self.config.exchange.fallback or os.getenv("RAILWAY_ENVIRONMENT"):
            for pid in RAILWAY_CHAIN:
                if pid not in chain:
                    chain.append(pid)

        self._provider_chain = chain
        errors: List[str] = []
        for provider_id in chain:
            try:
                provider = self._get_provider(provider_id)
                symbol = self.config.scan.symbols[0]
                tf = self.config.scan.timeframes[0]
                await provider.fetch(symbol, tf, limit=5)
                self._provider = provider
                self.exchange_id = provider.name
                logger.info(
                    "Donnees marche connectees : %s (data %s)",
                    provider.name,
                    BOT_DATA_VERSION,
                )
                return
            except Exception as exc:
                errors.append(f"{provider_id}: {exc}")
                logger.warning("Echec %s — %s", provider_id, exc)

        raise RuntimeError(
            "Aucune source OHLCV disponible. "
            "Sur Railway utilise EXCHANGE=mexc (recommandé). "
            "Details: " + " | ".join(errors)
        )

    def _get_provider(self, provider_id: str) -> OhlcvProvider:
        if provider_id not in self._providers:
            self._providers[provider_id] = create_provider(provider_id)
        return self._providers[provider_id]

    async def _fetch_with_fallback(
        self, symbol: str, timeframe: str, limit: int
    ) -> Tuple[list, str]:
        errors: List[str] = []
        chain = list(self._provider_chain)
        if self._provider and self._provider.name not in chain:
            chain.insert(0, self._provider.name)

        for provider_id in chain:
            provider = self._get_provider(provider_id)
            try:
                raw = await provider.fetch(symbol, timeframe, limit=limit)
                self._provider = provider
                self.exchange_id = provider.name
                return raw, provider.name
            except aiohttp.ClientResponseError as exc:
                errors.append(f"{provider_id}: HTTP {exc.status}")
                if exc.status == 429:
                    logger.warning("429 sur %s — essai provider suivant", provider_id)
                    continue
                raise
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
        nxt = self.next_refresh_at(symbol, timeframe)
        if nxt is None:
            return 0.0

        key = (symbol, timeframe)
        retries = self._stale_retries.get(key, 0)
        if retries > 0:
            return min(20.0, 8.0 * retries)

        delay = (nxt - pd.Timestamp.now(tz="UTC")).total_seconds()
        return max(0.0, delay)

    def _should_refresh(self, key: CacheKey, timeframe: str, cached: pd.DataFrame) -> bool:
        min_gap = self.config.scan.min_api_gap_sec
        if time.monotonic() - self._last_api_fetch.get(key, 0.0) < min_gap:
            return False

        if len(cached) < 2:
            return True

        nxt = self.next_refresh_at(key[0], key[1])
        if nxt is None:
            return True

        return pd.Timestamp.now(tz="UTC") >= nxt

    async def fetch_ohlcv(self, symbol: str, timeframe: str) -> pd.DataFrame:
        key = (symbol, timeframe)
        async with self._lock(key):
            limit = self.config.scan.ohlcv_limit
            cached = self._cache.get(key)
            prev_closed_ts = self._last_closed_ts.get(key)

            if cached is None or len(cached) < self.config.scan.min_bars:
                try:
                    raw, src = await self._fetch_with_fallback(symbol, timeframe, limit)
                    df = self._to_df(raw)
                    self._cache[key] = df
                    self._last_api_fetch[key] = time.monotonic()
                    self._last_closed_ts[key] = self.last_closed_ts(df)
                    self._stale_retries[key] = 0
                    logger.info("Historique charge via %s (%d barres)", src, len(df))
                    return df.copy()
                except Exception as exc:
                    logger.error("Chargement initial OHLCV impossible: %s", exc)
                    raise

            if not self._should_refresh(key, timeframe, cached):
                return cached.copy()

            try:
                raw, src = await self._fetch_with_fallback(symbol, timeframe, limit=20)
                self._last_api_fetch[key] = time.monotonic()
                logger.debug("Refresh %s via %s", symbol, src)
            except Exception as exc:
                logger.warning(
                    "Refresh OHLCV echoue (%s) — cache conserve: %s", symbol, exc
                )
                return cached.copy()

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
            if prev_closed_ts is not None and new_closed_ts == prev_closed_ts:
                retries = self._stale_retries.get(key, 0) + 1
                self._stale_retries[key] = retries
                if retries >= 2:
                    self._last_api_fetch[key] = 0.0
            else:
                self._stale_retries[key] = 0
                self._last_closed_ts[key] = new_closed_ts

            return df.copy()

    def has_cache(self, symbol: str, timeframe: str) -> bool:
        key = (symbol, timeframe)
        cached = self._cache.get(key)
        return cached is not None and len(cached) >= self.config.scan.min_bars

    @staticmethod
    def _to_df(raw: list) -> pd.DataFrame:
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
        return int(closed["timestamp"].iloc[-1].timestamp())
