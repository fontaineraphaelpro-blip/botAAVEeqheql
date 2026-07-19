"""Short Hunter — bot paper trading multi-alts, notifications Telegram.

Cycle : à chaque clôture de bougie 2h (00h, 02h, 04h... UTC + buffer),
scan des 34 alts -> gestion des stops -> nouveaux shorts si BTC en bear strict.

Lancer : python shorts_main.py
"""

from __future__ import annotations

import asyncio
import traceback
from datetime import datetime, timezone

from config import AppConfig, get_config
from notifier.bot import TelegramNotifier
from trading.short_engine import ShortHunterEngine
from utils.logger import setup_logger
from utils.resilience import retry_async

logger = setup_logger("shorts")

RESTART_DELAY_SEC = 30
BAR_SEC = 2 * 3600
DAILY_REPORT_HOUR_UTC = 7


def _seconds_until_next_bar(buffer_sec: float) -> float:
    now = datetime.now(timezone.utc).timestamp()
    next_close = (int(now // BAR_SEC) + 1) * BAR_SEC
    return max(5.0, next_close + buffer_sec - now)


class ShortHunterBot:
    def __init__(self, config: AppConfig, *, daily_reports: bool = True) -> None:
        self.config = config
        self.telegram = TelegramNotifier(config)
        self.engine = ShortHunterEngine(config, self.telegram)
        self.daily_reports = daily_reports
        self._last_report_date: str | None = None

    async def _maybe_daily_report(self) -> None:
        if not self.daily_reports:
            return
        now = datetime.now(timezone.utc)
        today = now.strftime("%Y-%m-%d")
        if now.hour >= DAILY_REPORT_HOUR_UTC and self._last_report_date != today:
            self._last_report_date = today
            try:
                await self.telegram.send_raw(self.engine.daily_message())
            except Exception as exc:
                logger.error("Rapport quotidien: %s", exc)

    async def run(self) -> None:
        await retry_async(self.telegram.start, attempts=4, label="telegram")
        try:
            await self.telegram.send_raw(self.engine.startup_message())
        except Exception as exc:
            logger.error("Telegram démarrage: %s", exc)

        # Premier cycle immédiat (traite la dernière bougie clôturée)
        await retry_async(self.engine.run_cycle, attempts=3, label="cycle")
        logger.info("Short Hunter actif — prochain cycle à la clôture 2h")

        while True:
            try:
                wait = _seconds_until_next_bar(self.config.shorts.candle_close_buffer_sec)
                logger.info("Attente %.0f min", wait / 60)
                await asyncio.sleep(wait)
                await self.engine.run_cycle()
                await self._maybe_daily_report()
            except Exception as exc:
                logger.error("Boucle: %s", exc)
                logger.debug(traceback.format_exc())
                await asyncio.sleep(60)

    async def shutdown(self) -> None:
        try:
            await self.engine.close()
        except Exception:
            pass


async def main() -> None:
    config = get_config()
    setup_logger("shorts", config.log_level, config.log_file)

    while True:
        bot = ShortHunterBot(config)
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
