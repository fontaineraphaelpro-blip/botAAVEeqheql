"""OHLCV AAVE/USDT — providers HTTP (Railway) ou Bybit local."""

from __future__ import annotations

import asyncio
import os
from typing import Dict, List, Tuple

import pandas as pd

from config import AppConfig
from scanner.ohlcv_providers import RAILWAY_CHAIN, OhlcvProvider, create_provider
from utils.logger import setup_logger

logger = setup_logger(__name__)

CacheKey = Tuple[str, str]


class MarketDataService:
    def __init__(self, config: AppConfig) -> None:
        self.config = config
        self._cache: Dict[CacheKey, pd.DataFrame] = {}
        self._locks: Dict[CacheKey, asyncio.Lock] = {}
        self._provider: OhlcvProvider | None = None
        self.exchange_id: str = ""

    async def start(self) -> None:
        primary = self.config.exchange.id.lower().strip()
        chain: List[str] = [primary]

        if self.config.exchange.fallback or os.getenv("RAILWAY_ENVIRONMENT"):
            for pid in RAILWAY_CHAIN:
                if pid not in chain:
                    chain.append(pid)

        errors: List[str] = []
        for provider_id in chain:
            provider = None
            try:
                provider = create_provider(provider_id)
                symbol = self.config.scan.symbols[0]
                tf = self.config.scan.timeframes[0]
                await provider.fetch(symbol, tf, limit=5)
                self._provider = provider
                self.exchange_id = provider.name
                logger.info("Donnees marche connectees : %s", provider.name)
                return
            except Exception as exc:
                errors.append(f"{provider_id}: {exc}")
                logger.warning("Echec %s — %s", provider_id, exc)
                if provider is not None:
                    await provider.close()

        raise RuntimeError(
            "Aucune source OHLCV disponible depuis Railway (USA). "
            "Bybit/Binance sont geo-bloques. Mets EXCHANGE=kucoin sur Railway. "
            "Details: " + " | ".join(errors)
        )

    async def close(self) -> None:
        if self._provider:
            try:
                await self._provider.close()
            except Exception as exc:
                logger.warning("Fermeture provider: %s", exc)
            self._provider = None

    def _lock(self, key: CacheKey) -> asyncio.Lock:
        if key not in self._locks:
            self._locks[key] = asyncio.Lock()
        return self._locks[key]

    async def fetch_ohlcv(self, symbol: str, timeframe: str) -> pd.DataFrame:
        key = (symbol, timeframe)
        async with self._lock(key):
            provider = self._provider
            if provider is None:
                raise RuntimeError("MarketDataService non demarre")

            limit = self.config.scan.ohlcv_limit
            cached = self._cache.get(key)

            if cached is None or len(cached) < self.config.scan.min_bars:
                raw = await provider.fetch(symbol, timeframe, limit=limit)
                df = self._to_df(raw)
                self._cache[key] = df
                return df.copy()

            since_ms = int(cached["timestamp"].iloc[-2].timestamp() * 1000)
            raw = await provider.fetch(symbol, timeframe, limit=50, since_ms=since_ms)
            if raw:
                new_df = self._to_df(raw)
                merged = (
                    pd.concat([cached, new_df])
                    .drop_duplicates(subset=["timestamp"])
                    .sort_values("timestamp")
                )
                self._cache[key] = merged.tail(limit).reset_index(drop=True)
            return self._cache[key].copy()

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
        """Exclut la derniere bougie (souvent en cours de formation sur l'API)."""
        if len(df) < 2:
            return df
        return df.iloc[:-1].copy()

    def last_closed_ts(self, df: pd.DataFrame) -> int:
        closed = self.closed_bars(df)
        return int(closed["timestamp"].iloc[-1].timestamp())
