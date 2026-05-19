"""Compare signaux bruts vs filtres sur 48 bougies KuCoin."""

from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

os.environ["EXCHANGE"] = "kucoin"

from config import get_config
from scanner.liquidity_detector import LiquidityDetector
from scanner.market_data import MarketDataService
from utils.signal_filter import FilterVerdict, filter_sweep, filter_zone


async def main() -> None:
    config = get_config()
    market = MarketDataService(config)
    det = LiquidityDetector(config)
    filt = config.filter

    await market.start()
    symbol, tf = "AAVE/USDT", "5m"
    df = await market.fetch_ohlcv(symbol, tf)
    closed = market.closed_bars(df)
    det.warmup(symbol, tf, df)

    lookback = 48
    start = max(config.scan.min_bars, len(closed) - lookback)
    raw_z, raw_s, ok_z, ok_s = 0, 0, 0, 0

    for i in range(start, len(closed)):
        sl = closed.iloc[: i + 1]
        ts = int(sl["timestamp"].iloc[-1].timestamp())
        det._state(symbol, tf).last_processed_bar = -1
        det._state(symbol, tf).last_processed_ts = None
        r = det.process(symbol, tf, sl, ts)
        for z in r.new_zones:
            raw_z += 1
            if filter_zone(z, sl, z.created_bar_index, filt).verdict == FilterVerdict.PASS:
                ok_z += 1
        for z, st, bar in r.sweeps:
            raw_s += 1
            fr = filter_sweep(z, st, sl, bar, filt)
            if fr.verdict in (FilterVerdict.PASS, FilterVerdict.PENDING):
                ok_s += 1

    print(f"Fenetre: {lookback} bougies 5m | Filtres: {filt.alert_zone_detection=} {filt.alert_sweeps=}")
    print(f"EQH/EQL bruts: {raw_z} -> passes filtre zone: {ok_z}")
    print(f"Sweeps bruts: {raw_s} -> passes/pending filtre: {ok_s}")
    await market.close()


if __name__ == "__main__":
    asyncio.run(main())
