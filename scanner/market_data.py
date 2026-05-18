"""OHLCV via ccxt async — Binance (local) ou Bybit/OKX (Railway / zones restreintes)."""

from __future__ import annotations

import asyncio
from typing import Any, Dict, List, Tuple

import ccxt.async_support as ccxt
import pandas as pd
from ccxt.base.errors import ExchangeNotAvailable

from config import AppConfig
from utils.logger import setup_logger

logger = setup_logger(__name__)

CacheKey = Tuple[str, str]

# Binance.com renvoie 451 depuis les datacenters US/EU de Railway
FALLBACK_CHAIN = ("bybit", "okx", "binance")


def _exchange_options(exchange_id: str, config: AppConfig) -> dict:
    opts: dict = {
        "enableRateLimit": True,
        "options": {"defaultType": "spot"},
    }
    if config.exchange.api_key:
        opts["apiKey"] = config.exchange.api_key
        opts["secret"] = config.exchange.api_secret

    if exchange_id == "binance":
        opts["urls"] = {
            "api": {
                "public": "https://data-api.binance.vision/api/v3",
                "private": "https://api.binance.com/api/v3",
            },
        }
    return opts


class MarketDataService:
    def __init__(self, config: AppConfig) -> None:
        self.config = config
        self._cache: Dict[CacheKey, pd.DataFrame] = {}
        self._locks: Dict[CacheKey, asyncio.Lock] = {}
        self._exchange: Any = None
        self.exchange_id: str = ""

    async def start(self) -> None:
        primary = self.config.exchange.id.lower().strip()
        chain: List[str] = [primary]
        if self.config.exchange.fallback:
            for ex in FALLBACK_CHAIN:
                if ex not in chain:
                    chain.append(ex)

        errors: List[str] = []
        for exchange_id in chain:
            ex = None
            try:
                ex = self._create_exchange(exchange_id)
                await self._probe(ex)
                self._exchange = ex
                self.exchange_id = exchange_id
                logger.info("Exchange connecte : %s", exchange_id)
                return
            except Exception as exc:
                errors.append(f"{exchange_id}: {exc}")
                logger.warning("Echec connexion %s — %s", exchange_id, exc)
                if ex is not None:
                    await ex.close()

        raise RuntimeError(
            "Aucun exchange disponible. Binance est bloque (451) sur Railway — "
            "definis EXCHANGE=bybit dans les variables Railway. Details: " + " | ".join(errors)
        )

    def _create_exchange(self, exchange_id: str) -> Any:
        if not hasattr(ccxt, exchange_id):
            raise ValueError(f"Exchange inconnu: {exchange_id}")
        cls = getattr(ccxt, exchange_id)
        return cls(_exchange_options(exchange_id, self.config))

    async def _probe(self, exchange: Any) -> None:
        """Test OHLCV sans load_markets (evite exchangeInfo geo-bloque)."""
        symbol = self.config.scan.symbols[0]
        tf = self.config.scan.timeframes[0]
        await exchange.fetch_ohlcv(symbol, timeframe=tf, limit=5)

    async def close(self) -> None:
        if self._exchange:
            try:
                await self._exchange.close()
            except Exception as exc:
                logger.warning("Fermeture exchange: %s", exc)
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
                raise RuntimeError("MarketDataService non demarre")

            limit = self.config.scan.ohlcv_limit
            cached = self._cache.get(key)

            try:
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
            except ExchangeNotAvailable:
                logger.error("Exchange %s indisponible (geo-block?) — redemarrage requis", self.exchange_id)
                raise

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
