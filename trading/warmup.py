"""Historique long TF signal au démarrage (EMA tendance 4h ≈ 100+ jours requis)."""

from __future__ import annotations

import asyncio

import aiohttp
import pandas as pd

from utils.logger import setup_logger

logger = setup_logger(__name__)

BINANCE_INTERVALS = {5: "5m", 15: "15m", 30: "30m", 60: "1h", 240: "4h"}
MAX_PER_REQ = 1000

SOURCES = (
    ("binance_vision", "https://data-api.binance.vision/api/v3/klines"),
    ("mexc", "https://api.mexc.com/api/v3/klines"),
)


async def fetch_history_tf(tf_min: int, bars: int, symbol: str = "AAVEUSDT") -> pd.DataFrame:
    """Télécharge `bars` bougies clôturées du TF demandé (paginé, avec fallback)."""
    interval = BINANCE_INTERVALS.get(tf_min)
    if interval is None:
        raise ValueError(f"TF non supporté pour le warmup: {tf_min}min")

    last_exc: Exception | None = None
    connector = aiohttp.TCPConnector(resolver=aiohttp.ThreadedResolver())
    async with aiohttp.ClientSession(
        connector=connector,
        timeout=aiohttp.ClientTimeout(total=30),
        headers={"User-Agent": "AAVE-Paper-Trader/1.0"},
    ) as session:
        for name, url in SOURCES:
            try:
                df = await _fetch_paginated(session, url, symbol, interval, bars)
                logger.info(
                    "Warmup %s : %d bougies %dmin (%s -> %s)",
                    name, len(df), tf_min, df.index[0], df.index[-1],
                )
                return df
            except Exception as exc:
                last_exc = exc
                logger.warning("Warmup %s échoué: %s", name, exc)

    raise RuntimeError(f"Warmup impossible: {last_exc}")


async def _fetch_paginated(
    session: aiohttp.ClientSession, url: str, symbol: str, interval: str, bars: int
) -> pd.DataFrame:
    rows: list[list] = []
    end_time: int | None = None

    while len(rows) < bars:
        params: dict = {
            "symbol": symbol,
            "interval": interval,
            "limit": min(MAX_PER_REQ, bars - len(rows)),
        }
        if end_time is not None:
            params["endTime"] = end_time
        async with session.get(url, params=params) as resp:
            resp.raise_for_status()
            batch = await resp.json()
        if not batch:
            break
        rows = list(batch) + rows
        end_time = int(batch[0][0]) - 1
        if len(batch) < 50:
            break
        await asyncio.sleep(0.35)

    if not rows:
        raise RuntimeError("Aucune donnée")

    df = pd.DataFrame(
        [[int(r[0]), float(r[1]), float(r[2]), float(r[3]), float(r[4]), float(r[5])] for r in rows],
        columns=["timestamp", "open", "high", "low", "close", "volume"],
    )
    df["timestamp"] = pd.to_datetime(df["timestamp"], unit="ms", utc=True)
    df = df.drop_duplicates(subset="timestamp").sort_values("timestamp").set_index("timestamp")
    # Écarte la dernière bougie (potentiellement en cours)
    return df.iloc[:-1]
