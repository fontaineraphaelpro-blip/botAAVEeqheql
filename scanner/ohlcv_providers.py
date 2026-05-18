"""OHLCV via API publiques HTTP — sans load_markets (compatible Railway US)."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import List, Optional

import asyncio
import aiohttp

BINANCE_TF = {"1m": "1m", "5m": "5m", "15m": "15m", "1h": "1h"}
KUCOIN_TF = {"1m": "1min", "5m": "5min", "15m": "15min", "1h": "1hour"}
MEXC_TF = BINANCE_TF


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
                headers={"User-Agent": "AAVE-EQH-EQL-Bot/1.0"},
            )
        return self._session

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
        tf = KUCOIN_TF.get(timeframe)
        if not tf:
            raise ValueError(f"Timeframe non supporte: {timeframe}")

        pair = _symbol_dash(symbol)
        url = "https://api.kucoin.com/api/v1/market/candles"

        if since_ms:
            params: dict = {"type": tf, "symbol": pair, "startAt": since_ms // 1000}
            session = await self._get_session()
            async with session.get(url, params=params) as resp:
                resp.raise_for_status()
                body = await resp.json()
            if body.get("code") != "200000":
                raise RuntimeError(f"KuCoin API: {body}")
            rows = body.get("data") or []
            ohlcv = _parse_kucoin_rows(rows, limit)
            return ohlcv[-limit:]

        import time as _time

        all_rows: list = []
        end_at = int(_time.time())
        session = await self._get_session()
        while len(all_rows) < limit:
            params = {"type": tf, "symbol": pair, "endAt": end_at}
            async with session.get(url, params=params) as resp:
                resp.raise_for_status()
                body = await resp.json()
            if body.get("code") != "200000":
                raise RuntimeError(f"KuCoin API: {body}")
            batch = body.get("data") or []
            if not batch:
                break
            parsed = _parse_kucoin_rows(batch, limit * 2)
            if not parsed:
                break
            all_rows = parsed + all_rows
            oldest_ts = parsed[0][0] // 1000
            end_at = oldest_ts - 1
            if len(batch) < 100:
                break
            await asyncio.sleep(0.12)

        dedup = {r[0]: r for r in all_rows}
        merged = sorted(dedup.values(), key=lambda x: x[0])
        return merged[-limit:]


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
        tf = MEXC_TF.get(timeframe)
        if not tf:
            raise ValueError(f"Timeframe non supporte: {timeframe}")

        url = "https://api.mexc.com/api/v3/klines"
        params: dict = {"symbol": _symbol_usdt(symbol), "interval": tf, "limit": min(limit, 1000)}
        if since_ms:
            params["startTime"] = since_ms

        session = await self._get_session()
        async with session.get(url, params=params) as resp:
            resp.raise_for_status()
            raw = await resp.json()
        return [[int(r[0]), float(r[1]), float(r[2]), float(r[3]), float(r[4]), float(r[5])] for r in raw]


class BinanceVisionProvider(_HttpProvider):
    name = "binance_vision"

    async def fetch(
        self,
        symbol: str,
        timeframe: str,
        limit: int,
        since_ms: Optional[int] = None,
    ) -> List[list]:
        tf = BINANCE_TF.get(timeframe)
        if not tf:
            raise ValueError(f"Timeframe non supporte: {timeframe}")

        url = "https://data-api.binance.vision/api/v3/klines"
        params: dict = {"symbol": _symbol_usdt(symbol), "interval": tf, "limit": min(limit, 1000)}
        if since_ms:
            params["startTime"] = since_ms

        session = await self._get_session()
        async with session.get(url, params=params) as resp:
            resp.raise_for_status()
            raw = await resp.json()
        return [[int(r[0]), float(r[1]), float(r[2]), float(r[3]), float(r[4]), float(r[5])] for r in raw]


class BybitProvider(_HttpProvider):
    """Bybit v5 klines — fonctionne en local (EU), souvent bloque sur Railway US."""

    name = "bybit"

    async def fetch(
        self,
        symbol: str,
        timeframe: str,
        limit: int,
        since_ms: Optional[int] = None,
    ) -> List[list]:
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
        if since_ms:
            params["start"] = since_ms

        session = await self._get_session()
        async with session.get(url, params=params) as resp:
            resp.raise_for_status()
            body = await resp.json()
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

# Railway US : Bybit + Binance.com bloques → KuCoin / MEXC
RAILWAY_CHAIN = ("kucoin", "mexc", "binance_vision")


def create_provider(provider_id: str) -> OhlcvProvider:
    pid = provider_id.lower().strip()
    if pid not in PROVIDERS:
        raise ValueError(f"Provider inconnu: {provider_id}. Choix: {', '.join(PROVIDERS)}")
    return PROVIDERS[pid]()
