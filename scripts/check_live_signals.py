"""Verifie ce que le bot detecterait maintenant vs TradingView."""

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
    config = get_config()
    market = MarketDataService(config)
    det = LiquidityDetector(config)

    await market.start()
    symbol, tf = "AAVE/USDT", "5m"
    df = await market.fetch_ohlcv(symbol, tf)
    closed = market.closed_bars(df)

    print(f"Source: {market.exchange_id}")
    print(f"Bougies: {len(df)} total | {len(closed)} fermees")
    print(f"Derniere fermee: {closed['timestamp'].iloc[-1]}")
    print(f"Prix close: {closed['close'].iloc[-1]:.4f}\n")

    det.warmup(symbol, tf, df)
    det._state(symbol, tf).last_processed_ts = None

    ts = market.last_closed_ts(df)
    r = det.process(symbol, tf, closed, ts)

    print(f"Zones NOUVELLES (alerte Telegram): {len(r.new_zones)}")
    for z in r.new_zones:
        print(f"  -> {z.zone_type.value} @ {z.sweep_level:.4f}")

    print(f"Sweeps: {len(r.sweeps)}")
    for z, st in r.sweeps:
        print(f"  -> {st} @ {z.sweep_level:.4f}")

    active = det._state(symbol, tf).active_zones
    print(f"\nZones actives en memoire: {len(active)}")
    for z in active[-5:]:
        print(f"  {z.zone_type.value} @ {z.sweep_level:.4f} (swept={z.is_swept})")

    if not r.new_zones and not r.sweeps:
        print(
            "\nAucun signal sur la derniere bougie fermee.\n"
            "Normal si EQH/EQL sur TV sont plus anciens ou autre exchange (BYBIT vs KUCOIN)."
        )

    await market.close()


if __name__ == "__main__":
    asyncio.run(main())
