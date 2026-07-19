"""Lance les bots paper trading dans un seul process (Railway).

- Clean Sticky AAVE : EMA 20/50 vert/gris/rouge, levier x10
- Paper Trader AAVE : EMA flip 30min + filtres ER
- Short Hunter : shorts multi-alts en bear BTC

Chaque bot a sa propre boucle de redémarrage.
"""

from __future__ import annotations

import asyncio

import clean_sticky_main
import shorts_main
import trader_main


async def main() -> None:
    await asyncio.gather(
        clean_sticky_main.main(),
        trader_main.main(),
        shorts_main.main(),
    )


if __name__ == "__main__":
    asyncio.run(main())
