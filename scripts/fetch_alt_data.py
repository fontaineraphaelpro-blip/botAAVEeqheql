"""Télécharge l'historique 2h Binance (spot) d'un univers d'alts + BTC.

Cache : data/alts_2h/<SYMBOL>.csv — relance = ne retélécharge pas.
Note survivorship : univers = coins liquides encore listés aujourd'hui. Les coins
morts/délistés (qui auraient été d'excellents shorts) sont absents, donc le
backtest short est plutôt CONSERVATEUR sur ce point.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import aiohttp
import pandas as pd

OUT = Path(__file__).resolve().parent.parent / "data" / "alts_2h"
URL = "https://data-api.binance.vision/api/v3/klines"

UNIVERSE = [
    "BTCUSDT", "ETHUSDT", "BNBUSDT", "XRPUSDT", "ADAUSDT", "SOLUSDT",
    "DOGEUSDT", "DOTUSDT", "LTCUSDT", "LINKUSDT", "AVAXUSDT", "UNIUSDT",
    "ATOMUSDT", "ETCUSDT", "XLMUSDT", "FILUSDT", "AAVEUSDT", "SANDUSDT",
    "MANAUSDT", "AXSUSDT", "NEARUSDT", "ALGOUSDT", "EOSUSDT", "CRVUSDT",
    "COMPUSDT", "SNXUSDT", "SUSHIUSDT", "1INCHUSDT", "GALAUSDT", "CHZUSDT",
    "ENJUSDT", "THETAUSDT", "XTZUSDT", "GRTUSDT", "RUNEUSDT", "KSMUSDT",
]


async def fetch_symbol(session: aiohttp.ClientSession, symbol: str) -> None:
    out_file = OUT / f"{symbol}.csv"
    if out_file.exists():
        print(f"{symbol}: cache OK")
        return

    rows: list[list] = []
    end_time: int | None = None
    while True:
        params: dict = {"symbol": symbol, "interval": "2h", "limit": 1000}
        if end_time is not None:
            params["endTime"] = end_time
        async with session.get(URL, params=params) as resp:
            if resp.status != 200:
                print(f"{symbol}: HTTP {resp.status} — ignoré")
                return
            batch = await resp.json()
        if not batch:
            break
        rows = list(batch) + rows
        end_time = int(batch[0][0]) - 1
        if len(batch) < 1000:
            break
        await asyncio.sleep(0.15)

    if not rows:
        print(f"{symbol}: aucune donnée — ignoré")
        return

    df = pd.DataFrame(
        [[int(r[0]), float(r[1]), float(r[2]), float(r[3]), float(r[4]), float(r[5])] for r in rows],
        columns=["timestamp", "open", "high", "low", "close", "volume"],
    )
    df["timestamp"] = pd.to_datetime(df["timestamp"], unit="ms", utc=True)
    df = df.drop_duplicates(subset="timestamp").sort_values("timestamp")
    df.iloc[:-1].to_csv(out_file, index=False)
    print(f"{symbol}: {len(df)} bougies ({df['timestamp'].iloc[0].date()} -> {df['timestamp'].iloc[-1].date()})")


async def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    connector = aiohttp.TCPConnector(resolver=aiohttp.ThreadedResolver(), limit=4)
    async with aiohttp.ClientSession(
        connector=connector, timeout=aiohttp.ClientTimeout(total=60)
    ) as session:
        for sym in UNIVERSE:
            try:
                await fetch_symbol(session, sym)
            except Exception as exc:
                print(f"{sym}: erreur {exc}")


if __name__ == "__main__":
    asyncio.run(main())
