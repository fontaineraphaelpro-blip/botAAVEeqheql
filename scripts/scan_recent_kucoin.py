"""Montre les EQH/EQL detectes sur les dernieres bougies 5m KuCoin."""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from config import get_config
from scanner.liquidity_detector import LiquidityDetector
from scanner.market_data import MarketDataService


async def main() -> None:
    import os
    os.environ["EXCHANGE"] = "kucoin"

    config = get_config()
    market = MarketDataService(config)
    det = LiquidityDetector(config)

    await market.start()
    symbol, tf = "AAVE/USDT", "5m"
    df = await market.fetch_ohlcv(symbol, tf)
    closed = market.closed_bars(df)

    print(f"Source: {market.exchange_id.upper()}")
    print(f"Derniere bougie fermee: {closed['timestamp'].iloc[-1]} UTC")
    print(f"Close: {closed['close'].iloc[-1]:.4f}\n")

    det.warmup(symbol, tf, df)
    det._alerted_zones.clear()

    lookback = 48
    start = max(config.scan.min_bars, len(closed) - lookback)
    events = []

    for i in range(start, len(closed)):
        slice_df = closed.iloc[: i + 1]
        ts = int(slice_df["timestamp"].iloc[-1].timestamp())
        det._state(symbol, tf).last_processed_ts = None
        r = det.process(symbol, tf, slice_df, ts)
        t = slice_df["timestamp"].iloc[-1]
        for z in r.new_zones:
            events.append((t, z.zone_type.value, z.sweep_level, "ZONE"))
        for z, st in r.sweeps:
            events.append((t, st, z.sweep_level, "SWEEP"))

    print(f"Signaux sur les {lookback} dernieres bougies 5m fermees:\n")
    if not events:
        print("  Aucun — le bot n'aurait rien envoye sur cette fenetre.")
        print("  Si TV montre des zones plus vieilles, c'est normal (pas d'alerte retro).")
    else:
        for t, kind, price, typ in events:
            print(f"  {t} UTC | {typ} {kind} @ {price:.4f}")

    print(f"\nZones actives maintenant: {len(det._state(symbol, tf).active_zones)}")
    await market.close()


if __name__ == "__main__":
    asyncio.run(main())
