"""Paper Trader AAVE/USDT — bot autonome, notifications Telegram.

Boucle : attend la clôture de chaque bougie 5m -> 1 requête OHLCV ->
signaux sur TF 30min (EMA flip + filtres) -> exécution paper -> Telegram.

Lancer : python trader_main.py
"""

from __future__ import annotations

import asyncio
import traceback

from config import AppConfig, get_config
from scanner.market_data import MarketDataService
from notifier.bot import TelegramNotifier
from trading import notifications as notif
from trading.engine import SYMBOL, TF_5M, TradingEngine
from utils.logger import setup_logger
from utils.resilience import retry_async

logger = setup_logger("trader")

RESTART_DELAY_SEC = 30


class PaperTradingBot:
    def __init__(self, config: AppConfig) -> None:
        self.config = config
        self.market = MarketDataService(config)
        self.telegram = TelegramNotifier(config)
        self.engine = TradingEngine(config, self.market, self.telegram)

    async def _bootstrap(self) -> None:
        await retry_async(self.market.start, attempts=8, label="market")
        await retry_async(self.telegram.start, attempts=4, label="telegram")
        await retry_async(self.engine.warmup, attempts=6, label="warmup")

        async def _hist() -> object:
            return await self.market.load_history(SYMBOL, TF_5M)

        await retry_async(_hist, attempts=6, label="history-5m")

        try:
            await self.telegram.send_raw(
                notif.msg_startup(
                    self.engine.trader,
                    self.config.trading,
                    self.market.data_source_label(),
                )
            )
        except Exception as exc:
            logger.error("Telegram démarrage: %s", exc)

        # Traite immédiatement l'état courant (position à gérer, signal en attente)
        await self.engine.on_new_5m_close()

    async def _run_loop(self) -> None:
        while True:
            try:
                wait = self.market.seconds_until_refresh(SYMBOL, TF_5M)
                await asyncio.sleep(wait)
                await self.market.refresh_if_due(SYMBOL, TF_5M)
                await self.engine.on_new_5m_close()
            except Exception as exc:
                logger.error("Boucle: %s", exc)
                logger.debug(traceback.format_exc())
                await asyncio.sleep(30)

    async def run(self) -> None:
        await self._bootstrap()
        logger.info(
            "Paper trader actif — %s | TF signal %dmin | solde %.2f USDT",
            self.market.data_source_label(),
            self.config.trading.signal_tf_min,
            self.engine.trader.state.balance,
        )
        await self._run_loop()

    async def shutdown(self) -> None:
        try:
            await self.market.close()
        except Exception:
            pass


async def main() -> None:
    config = get_config()
    setup_logger("trader", config.log_level, config.log_file)

    while True:
        bot = PaperTradingBot(config)
        try:
            await bot.run()
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.error("Redémarrage dans %ds: %s", RESTART_DELAY_SEC, exc)
            logger.debug(traceback.format_exc())
            try:
                await bot.shutdown()
            except Exception:
                pass
            await asyncio.sleep(RESTART_DELAY_SEC)


if __name__ == "__main__":
    asyncio.run(main())
