"""Lance les bots paper trading dans un seul process (Railway).

- Clean Sticky + Paper Trader partagent UNE seule source KuCoin AAVE/USDT 5m
  (évite le double scan / 429).
- Short Hunter : Binance Vision/MEXC 2h (historique profond multi-alts).

Chaque groupe a sa propre boucle de redémarrage.
"""

from __future__ import annotations

import asyncio
import traceback

import shorts_main
from config import get_config
from notifier.bot import TelegramNotifier
from scanner.market_data import MarketDataService
from trading import clean_sticky_notifications as cs_notif
from trading import notifications as trader_notif
from trading.clean_sticky_engine import CleanStickyEngine
from trading.clean_sticky_engine import SYMBOL as AAVE
from trading.clean_sticky_engine import _tf_str
from trading.engine import TF_5M, TradingEngine
from utils.logger import setup_logger
from utils.resilience import retry_async

logger = setup_logger("both")

RESTART_DELAY_SEC = 30


async def run_aave_bots() -> None:
    """Clean Sticky + Paper Trader sur le même cache KuCoin 5m."""
    config = get_config()
    setup_logger("both", config.log_level, config.log_file)

    while True:
        market = MarketDataService(config)
        telegram = TelegramNotifier(config)
        sticky = CleanStickyEngine(config, market, telegram)
        trader = TradingEngine(config, market, telegram)
        tf = _tf_str(config.clean_sticky.signal_tf_min)
        if tf != TF_5M:
            logger.warning(
                "Clean Sticky TF=%s != trader 5m — refresh sur les deux TF",
                tf,
            )

        try:
            await retry_async(market.start, attempts=8, label="market-shared")
            await retry_async(telegram.start, attempts=4, label="telegram-shared")
            await retry_async(trader.warmup, attempts=6, label="trader-warmup")

            async def _hist() -> object:
                return await market.load_history(AAVE, TF_5M)

            await retry_async(_hist, attempts=6, label="history-5m-shared")
            if tf != TF_5M:

                async def _hist_tf() -> object:
                    return await market.load_history(AAVE, tf)

                await retry_async(_hist_tf, attempts=4, label=f"history-{tf}")

            src = market.data_source_label()
            if "kucoin" not in src.lower():
                logger.error("Source live AAVE attendue KUCOIN, obtenu %s", src)

            try:
                await telegram.send_raw(
                    cs_notif.msg_startup(sticky.trader, config.clean_sticky, src)
                )
                await telegram.send_raw(
                    trader_notif.msg_startup(trader.trader, config.trading, src)
                )
            except Exception as exc:
                logger.error("Telegram démarrage AAVE: %s", exc)

            await sticky.on_new_bar()
            await trader.on_new_5m_close()

            logger.info(
                "AAVE bots actifs — source=%s | Clean Sticky %s + Trader 5m→%dmin",
                src,
                tf,
                config.trading.signal_tf_min,
            )

            while True:
                wait = market.seconds_until_refresh(AAVE, TF_5M)
                await asyncio.sleep(wait)
                await market.refresh_if_due(AAVE, TF_5M)
                if tf != TF_5M:
                    await market.refresh_if_due(AAVE, tf)
                await sticky.on_new_bar()
                await trader.on_new_5m_close()

        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.error("AAVE bots redémarrage dans %ds: %s", RESTART_DELAY_SEC, exc)
            logger.debug(traceback.format_exc())
            try:
                await market.close()
            except Exception:
                pass
            await asyncio.sleep(RESTART_DELAY_SEC)


async def main() -> None:
    await asyncio.gather(run_aave_bots(), shorts_main.main())


if __name__ == "__main__":
    asyncio.run(main())
