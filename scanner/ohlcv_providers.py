"""OHLCV HTTP — budget API strict, pas de 429."""

from __future__ import annotations

import asyncio
import logging
import time
from abc import ABC, abstractmethod
from typing import List, Optional

import aiohttp

from utils.api_budget import acquire_request_slot, ban_kucoin, kucoin_is_banned

logger = logging.getLogger(__name__)

BINANCE_TF = {"1m": "1m", "5m": "5m", "15m": "15m", "1h": "1h"}
KUCOIN_TF = {"1m": "1min", "5m": "5min", "15m": "15min", "1h": "1hour"}
MEXC_TF = BINANCE_TF

# KuCoin : 1 seule requete (pas de pagination)
KUCOIN_MAX_LIMIT = 150


class RateLimitError(Exception):
    """Leve sur 429 → fallback immediat (pas 6 retries)."""

    def __init__(self, provider: str) -> None:
        self.provider = provider
        super().__init__(f"{provider}: rate limit")


def _symbol_usdt(symbol: str) -> str:
    return symbol.replace("/", "").upper()


def _symbol_dash(symbol: str) -> str:
    base, quote = symbol.split("/")
    return f"{base}-{quote}"


class OhlcvProvider(ABC):
    name: str

    @abstractmethod
    async def fetch(
        self,
        symbol: str,
        timeframe: str,
        limit: int,
        since_ms: Optional[int] = None,
    ) -> List[list]:
        pass

    async def close(self) -> None:
        pass


class _HttpProvider(OhlcvProvider):
    def __init__(self) -> None:
        self._session: aiohttp.ClientSession | None = None

    async def _get_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=30),
                headers={"User-Agent": "AAVE-EQH-EQL-Bot/3.0"},
            )
        return self._session

    async def _get_json(self, url: str, params: dict) -> dict | list:
        await acquire_request_slot()
        session = await self._get_session()
        async with session.get(url, params=params) as resp:
            if resp.status == 429:
                if self.name == "kucoin":
                    ban_kucoin()
                    logger.warning("KuCoin 429 — ban 10min, fallback")
                raise RateLimitError(self.name)
            resp.raise_for_status()
            return await resp.json()

    async def close(self) -> None:
        if self._session and not self._session.closed:
            await self._session.close()
        self._session = None


class KucoinProvider(_HttpProvider):
    name = "kucoin"

    async def fetch(
        self,
        symbol: str,
        timeframe: str,
        limit: int,
        since_ms: Optional[int] = None,
    ) -> List[list]:
        del since_ms
        if kucoin_is_banned():
            raise RateLimitError("kucoin-circuit")
        if limit > KUCOIN_MAX_LIMIT:
            raise ValueError(
                f"KuCoin max {KUCOIN_MAX_LIMIT} bougies/req — utilise binance_vision pour historique"
            )

        tf = KUCOIN_TF.get(timeframe)
        if not tf:
            raise ValueError(f"Timeframe non supporte: {timeframe}")

        pair = _symbol_dash(symbol)
        url = "https://api.kucoin.com/api/v1/market/candles"
        params = {"type": tf, "symbol": pair, "endAt": int(time.time())}
        body = await self._get_json(url, params)
        if body.get("code") != "200000":
            raise RuntimeError(f"KuCoin API: {body}")
        rows = body.get("data") or []
        return _parse_kucoin_rows(rows, limit)[-limit:]


def _parse_kucoin_rows(rows: list, cap: int) -> list:
    ohlcv = []
    for r in reversed(rows[:cap]):
        ts, o, c, h, lo, vol = int(r[0]), float(r[1]), float(r[2]), float(r[3]), float(r[4]), float(r[5])
        ohlcv.append([ts * 1000, o, h, lo, c, vol])
    return ohlcv


class MexcProvider(_HttpProvider):
    name = "mexc"

    async def fetch(
        self,
        symbol: str,
        timeframe: str,
        limit: int,
        since_ms: Optional[int] = None,
    ) -> List[list]:
        del since_ms
        tf = MEXC_TF.get(timeframe)
        if not tf:
            raise ValueError(f"Timeframe non supporte: {timeframe}")

        url = "https://api.mexc.com/api/v3/klines"
        params: dict = {
            "symbol": _symbol_usdt(symbol),
            "interval": tf,
            "limit": min(limit, 1000),
        }
        body = await self._get_json(url, params)
        return [[int(r[0]), float(r[1]), float(r[2]), float(r[3]), float(r[4]), float(r[5])] for r in body]


class BinanceVisionProvider(_HttpProvider):
    """1 requete = jusqu'a 1000 bougies — ideal historique Railway."""

    name = "binance_vision"

    async def fetch(
        self,
        symbol: str,
        timeframe: str,
        limit: int,
        since_ms: Optional[int] = None,
    ) -> List[list]:
        del since_ms
        tf = BINANCE_TF.get(timeframe)
        if not tf:
            raise ValueError(f"Timeframe non supporte: {timeframe}")

        url = "https://data-api.binance.vision/api/v3/klines"
        params: dict = {
            "symbol": _symbol_usdt(symbol),
            "interval": tf,
            "limit": min(limit, 1000),
        }
        body = await self._get_json(url, params)
        return [[int(r[0]), float(r[1]), float(r[2]), float(r[3]), float(r[4]), float(r[5])] for r in body]


class BybitProvider(_HttpProvider):
    name = "bybit"

    async def fetch(
        self,
        symbol: str,
        timeframe: str,
        limit: int,
        since_ms: Optional[int] = None,
    ) -> List[list]:
        del since_ms
        tf_map = {"1m": "1", "5m": "5", "15m": "15", "1h": "60"}
        interval = tf_map.get(timeframe)
        if not interval:
            raise ValueError(f"Timeframe non supporte: {timeframe}")

        url = "https://api.bybit.com/v5/market/kline"
        params: dict = {
            "category": "spot",
            "symbol": _symbol_usdt(symbol),
            "interval": interval,
            "limit": min(limit, 1000),
        }
        body = await self._get_json(url, params)
        if body.get("retCode") != 0:
            raise RuntimeError(f"Bybit API: {body}")

        rows = body.get("result", {}).get("list") or []
        ohlcv = []
        for r in reversed(rows):
            ohlcv.append([int(r[0]), float(r[1]), float(r[2]), float(r[3]), float(r[4]), float(r[5])])
        return ohlcv


PROVIDERS = {
    "kucoin": KucoinProvider,
    "mexc": MexcProvider,
    "binance_vision": BinanceVisionProvider,
    "binance": BinanceVisionProvider,
    "bybit": BybitProvider,
}

# Binance Vision d'abord (1 req) — KuCoin seulement refresh court
RAILWAY_CHAIN = ("binance_vision", "mexc", "kucoin")


def create_provider(provider_id: str) -> OhlcvProvider:
    pid = provider_id.lower().strip()
    if pid not in PROVIDERS:
        raise ValueError(f"Provider inconnu: {provider_id}. Choix: {', '.join(PROVIDERS)}")
    return PROVIDERS[pid]()
