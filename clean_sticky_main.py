"""Clean Sticky AAVE/USDT — paper trading couleurs EMA 20/50.

Règles (TradingView « AAVEUSDT — Clean Sticky Minimal ») :
- Ligne EMA rapide verte  → LONG  (close > EMA20 > EMA50)
- Ligne grise             → ferme
- Ligne rouge             → SHORT (close < EMA20 < EMA50)

Mise 1000 USDT, levier x10, 100 % de la marge à chaque trade.

Lancer : python clean_sticky_main.py
"""

from __future__ import annotations

import asyncio
import traceback

from config import AppConfig, get_config
from notifier.bot import TelegramNotifier
from scanner.market_data import MarketDataService
from trading import clean_sticky_notifications as notif
from trading.clean_sticky_engine import SYMBOL, CleanStickyEngine, _tf_str
from utils.logger import setup_logger
from utils.resilience import retry_async

logger = setup_logger("clean_sticky")

RESTART_DELAY_SEC = 30


class CleanStickyBot:
    def __init__(self, config: AppConfig) -> None:
        self.config = config
        self.market = MarketDataService(config)
        self.telegram = TelegramNotifier(config)
        self.engine = CleanStickyEngine(config, self.market, self.telegram)
        self.tf = _tf_str(config.clean_sticky.signal_tf_min)

    async def _bootstrap(self) -> None:
        await retry_async(self.market.start, attempts=8, label="market")
        await retry_async(self.telegram.start, attempts=4, label="telegram")

        async def _hist() -> object:
            return await self.market.load_history(SYMBOL, self.tf)

        await retry_async(_hist, attempts=6, label=f"history-{self.tf}")

        try:
            await self.telegram.send_raw(
                notif.msg_startup(
                    self.engine.trader,
                    self.config.clean_sticky,
                    self.market.data_source_label(),
                )
            )
        except Exception as exc:
            logger.error("Telegram démarrage: %s", exc)

        await self.engine.on_new_bar()

    async def _run_loop(self) -> None:
        while True:
            try:
                wait = self.market.seconds_until_refresh(SYMBOL, self.tf)
                await asyncio.sleep(wait)
                await self.market.refresh_if_due(SYMBOL, self.tf)
                await self.engine.on_new_bar()
            except Exception as exc:
                logger.error("Boucle: %s", exc)
                logger.debug(traceback.format_exc())
                await asyncio.sleep(30)

    async def run(self) -> None:
        await self._bootstrap()
        logger.info(
            "Clean Sticky actif — %s | TF %s | EMA %d/%d | x%.0f | solde %.2f",
            self.market.data_source_label(),
            self.tf,
            self.config.clean_sticky.ema_fast,
            self.config.clean_sticky.ema_slow,
            self.config.clean_sticky.leverage,
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
    setup_logger("clean_sticky", config.log_level, config.log_file)

    while True:
        bot = CleanStickyBot(config)
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
