"""Envoie des exemples d'alertes EQH / EQL / SWEEP (format reel du bot)."""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from config import get_config
from models.liquidity_zone import LiquidityZone, ZoneType
from notifier.bot import TelegramNotifier


def _zone(
    zone_type: ZoneType,
    tf: str,
    top: float,
    bottom: float,
    vol: float,
    score: float,
) -> LiquidityZone:
    sweep = top if zone_type == ZoneType.EQH else bottom
    return LiquidityZone(
        zone_id="demo",
        symbol="AAVE/USDT",
        timeframe=tf,
        zone_type=zone_type,
        top=top,
        bottom=bottom,
        mid=(top + bottom) / 2,
        sweep_level=sweep,
        total_vol=vol,
        created_bar_index=0,
        pivot_a_idx=0,
        pivot_b_idx=0,
        score=score,
    )


async def main() -> None:
    config = get_config()
    tg = TelegramNotifier(config)
    await tg.start()

    await tg.notify_eqh(
        _zone(ZoneType.EQH, "5m", top=88.42, bottom=88.35, vol=125_400, score=72.5)
    )
    await asyncio.sleep(0.5)

    await tg.notify_eql(
        _zone(ZoneType.EQL, "15m", top=87.18, bottom=87.05, vol=89_200, score=68.0)
    )
    await asyncio.sleep(0.5)

    await tg.notify_eqh_sweep(
        _zone(ZoneType.EQH, "5m", top=88.42, bottom=88.35, vol=125_400, score=72.5)
    )
    await asyncio.sleep(0.5)

    await tg.notify_eql_sweep(
        _zone(ZoneType.EQL, "5m", top=87.18, bottom=87.05, vol=89_200, score=68.0)
    )

    print("[OK] 4 messages demo envoyes (EQH, EQL, EQH SWEEP, EQL SWEEP)")


if __name__ == "__main__":
    asyncio.run(main())
