"""Lance les deux bots paper trading dans un seul process (Railway).

- Paper Trader AAVE/USDT (trader_main) : EMA flip 30min long/short
- Short Hunter (shorts_main) : shorts multi-alts en bear market

Chaque bot a sa propre boucle de redémarrage — un crash de l'un
n'arrête pas l'autre.
"""

from __future__ import annotations

import asyncio

import shorts_main
import trader_main


async def main() -> None:
    await asyncio.gather(trader_main.main(), shorts_main.main())


if __name__ == "__main__":
    asyncio.run(main())
