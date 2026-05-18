"""OHLCV Binance via ccxt async avec cache incrémental."""

from __future__ import annotations

import asyncio
from typing import Dict, Tuple

import ccxt.async_support as ccxt
import pandas as pd

from config import AppConfig
from utils.logger import setup_logger

logger = setup_logger(__name__)

CacheKey = Tuple[str, str]


class MarketDataService:
    def __init__(self, config: AppConfig) -> None:
        self.config = config
        self._cache: Dict[CacheKey, pd.DataFrame] = {}
        self._locks: Dict[CacheKey, asyncio.Lock] = {}
        self._exchange: ccxt.binance | None = None

    async def start(self) -> None:
        opts: dict = {"enableRateLimit": True, "options": {"defaultType": "spot"}}
        if self.config.binance.api_key:
            opts["apiKey"] = self.config.binance.api_key
            opts["secret"] = self.config.binance.api_secret
        self._exchange = ccxt.binance(opts)
        await self._exchange.load_markets()
        logger.info("Binance connecté")

    async def close(self) -> None:
        if self._exchange:
            await self._exchange.close()
            self._exchange = None

    def _lock(self, key: CacheKey) -> asyncio.Lock:
        if key not in self._locks:
            self._locks[key] = asyncio.Lock()
        return self._locks[key]

    async def fetch_ohlcv(self, symbol: str, timeframe: str) -> pd.DataFrame:
        key = (symbol, timeframe)
        async with self._lock(key):
            ex = self._exchange
            if ex is None:
                raise RuntimeError("MarketDataService non démarré")

            limit = self.config.scan.ohlcv_limit
            cached = self._cache.get(key)

            if cached is None or len(cached) < self.config.scan.min_bars:
                raw = await ex.fetch_ohlcv(symbol, timeframe=timeframe, limit=limit)
                df = self._to_df(raw)
                self._cache[key] = df
                return df.copy()

            since_ms = int(cached["timestamp"].iloc[-2].timestamp() * 1000)
            raw = await ex.fetch_ohlcv(symbol, timeframe=timeframe, since=since_ms, limit=50)
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

    def last_closed_ts(self, df: pd.DataFrame) -> int:
        return int(df["timestamp"].iloc[-1].timestamp())
