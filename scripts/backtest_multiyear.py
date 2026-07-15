"""Backtest de la stratégie année par année depuis 2021 (données Binance 30min).

Répond à : « que donne la stratégie dans une année haussière ? »
"""

from __future__ import annotations

import asyncio
import sys
import time
from pathlib import Path

import aiohttp
import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
from backtest_leverage import run_trades  # noqa: E402

CACHE = Path(__file__).resolve().parent.parent / "data" / "aave_30m_binance_full.csv"
URL = "https://data-api.binance.vision/api/v3/klines"


async def fetch_full_history() -> pd.DataFrame:
    if CACHE.exists():
        df = pd.read_csv(CACHE, parse_dates=["timestamp"]).set_index("timestamp")
        print(f"Cache : {len(df)} bougies ({df.index[0]} -> {df.index[-1]})")
        return df

    rows: list[list] = []
    end_time: int | None = None
    connector = aiohttp.TCPConnector(resolver=aiohttp.ThreadedResolver())
    async with aiohttp.ClientSession(
        connector=connector, timeout=aiohttp.ClientTimeout(total=30)
    ) as session:
        while True:
            params: dict = {"symbol": "AAVEUSDT", "interval": "30m", "limit": 1000}
            if end_time is not None:
                params["endTime"] = end_time
            async with session.get(URL, params=params) as resp:
                resp.raise_for_status()
                batch = await resp.json()
            if not batch:
                break
            rows = list(batch) + rows
            end_time = int(batch[0][0]) - 1
            print(f"\r{len(rows)} bougies téléchargées...", end="", flush=True)
            if len(batch) < 1000:
                break
            await asyncio.sleep(0.25)

    print()
    df = pd.DataFrame(
        [[int(r[0]), float(r[1]), float(r[2]), float(r[3]), float(r[4]), float(r[5])] for r in rows],
        columns=["timestamp", "open", "high", "low", "close", "volume"],
    )
    df["timestamp"] = pd.to_datetime(df["timestamp"], unit="ms", utc=True)
    df = df.drop_duplicates(subset="timestamp").sort_values("timestamp").set_index("timestamp")
    df.iloc[:-1].to_csv(CACHE)
    return df.iloc[:-1]


def yearly_backtest(df: pd.DataFrame) -> None:
    years = sorted(set(df.index.year))
    print(f"\n{'Année':>6} {'AAVE (b&h)':>11} {'Stratégie 1x':>13} {'Strat 2x':>10} {'Trades':>7} {'WR':>6} {'DD 1x':>7}")
    for y in years:
        sub = df[df.index.year == y]
        if len(sub) < 2000:  # année incomplète (<~42 jours)
            continue
        bh = (sub["close"].iloc[-1] / sub["close"].iloc[0] - 1) * 100
        trades, worst = run_trades(sub, "30min", 20, 2.5, 3.0, 0.35)
        trades = np.array(trades)
        if len(trades) == 0:
            print(f"{y:>6} {bh:>10.1f}% {'—':>13}")
            continue
        eq1 = float(np.prod(1 + trades))
        eq2 = float(np.prod(1 + 2 * trades))
        curve = np.cumprod(1 + trades)
        dd = float((curve / np.maximum.accumulate(curve) - 1).min())
        wr = (trades > 0).mean() * 100
        print(f"{y:>6} {bh:>10.1f}% {(eq1-1)*100:>12.1f}% {(eq2-1)*100:>9.1f}% {len(trades):>7} {wr:>5.1f}% {dd*100:>6.1f}%")


async def main() -> None:
    df = await fetch_full_history()
    yearly_backtest(df)


if __name__ == "__main__":
    asyncio.run(main())
