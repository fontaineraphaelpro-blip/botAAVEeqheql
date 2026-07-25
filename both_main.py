"""Lance les 3 bots paper dans un seul process (Railway).

Noms :
- AAVE Tendance — EMA flip 30m + filtres
- Chasseur Shorts — multi-alts si BTC bear
- AAVE Analyst — motifs AAVE → paper trade + apprentissage

1 source KuCoin AAVE 5m + 1 rapport quotidien unifié.
"""

from __future__ import annotations

import asyncio
import traceback
from datetime import datetime, timezone

from config import get_config
from notifier.bot import TelegramNotifier
from scanner.market_data import MarketDataService
from trading import notifications as tendance_notif
from trading.analyst_engine import AnalystEngine
from trading.daily_fleet import msg_fleet_startup, msg_unified_daily
from trading.engine import SYMBOL as AAVE
from trading.engine import TF_5M, TradingEngine
from trading.short_engine import ShortHunterEngine
from utils.logger import setup_logger
from utils.resilience import retry_async

logger = setup_logger("both")

RESTART_DELAY_SEC = 30
SHORTS_BAR_SEC = 2 * 3600
DAILY_HOUR_UTC = 7


def _seconds_until_next_2h(buffer_sec: float) -> float:
    now = datetime.now(timezone.utc).timestamp()
    next_close = (int(now // SHORTS_BAR_SEC) + 1) * SHORTS_BAR_SEC
    return max(5.0, next_close + buffer_sec - now)


class Fleet:
    """État partagé des 3 bots + rapport quotidien unique."""

    def __init__(self) -> None:
        self.config = get_config()
        self.market = MarketDataService(self.config)
        self.telegram = TelegramNotifier(self.config)
        self.tendance = TradingEngine(
            self.config, self.market, self.telegram, daily_reports=False
        )
        self.chasseur = ShortHunterEngine(self.config, self.telegram)
        self.analyst = AnalystEngine(self.config, self.market, self.telegram)
        self._last_report_date: str | None = None
        self._report_lock = asyncio.Lock()

    async def maybe_daily(self) -> None:
        now = datetime.now(timezone.utc)
        today = now.strftime("%Y-%m-%d")
        if now.hour < DAILY_HOUR_UTC:
            return
        async with self._report_lock:
            if self._last_report_date == today:
                return
            self._last_report_date = today
            try:
                price = float(
                    self.market.get_cached(AAVE, TF_5M)["close"].iloc[-1]
                )
            except Exception:
                price = 0.0
            try:
                await self.telegram.send_raw(
                    msg_unified_daily(
                        tendance=self.tendance.trader,
                        chasseur_engine=self.chasseur,
                        analyst_engine=self.analyst,
                        aave_price=price,
                    )
                )
                logger.info("Rapport quotidien unifié envoyé")
            except Exception as exc:
                logger.error("Rapport quotidien: %s", exc)
                self._last_report_date = None

    async def bootstrap(self) -> None:
        await retry_async(self.market.start, attempts=8, label="market")
        await retry_async(self.telegram.start, attempts=4, label="telegram")
        await retry_async(self.tendance.warmup, attempts=6, label="tendance-warmup")

        self.analyst.load_state()
        self.analyst.load_memory()

        async def _hist() -> object:
            return await self.market.load_history(AAVE, TF_5M)

        await retry_async(_hist, attempts=6, label="history-5m")

        src = self.market.data_source_label()
        try:
            await self.telegram.send_raw(msg_fleet_startup(src))
            await self.telegram.send_raw(
                tendance_notif.msg_startup(
                    self.tendance.trader, self.config.trading, src
                )
            )
            await self.telegram.send_raw(self.chasseur.startup_message())
            await self.telegram.send_raw(self.analyst.startup_message(src))
        except Exception as exc:
            logger.error("Telegram démarrage flotte: %s", exc)

        await self.tendance.on_new_5m_close()
        await self.analyst.on_new_5m_close()
        await retry_async(self.chasseur.run_cycle, attempts=3, label="chasseur-cycle")
        await self.maybe_daily()

        logger.info(
            "Flotte active — [TENDANCE] %dmin | [CHASSEUR] 2h | [ANALYST] %d motifs",
            self.config.trading.signal_tf_min,
            self.analyst.memory.size if self.analyst.memory else 0,
        )

    async def aave_loop(self) -> None:
        while True:
            wait = self.market.seconds_until_refresh(AAVE, TF_5M)
            await asyncio.sleep(wait)
            await self.market.refresh_if_due(AAVE, TF_5M)
            await self.tendance.on_new_5m_close()
            await self.analyst.on_new_5m_close()
            await self.maybe_daily()

    async def chasseur_loop(self) -> None:
        buf = self.config.shorts.candle_close_buffer_sec
        while True:
            wait = _seconds_until_next_2h(buf)
            logger.info("Chasseur — attente %.0f min", wait / 60)
            await asyncio.sleep(wait)
            await self.chasseur.run_cycle()
            await self.maybe_daily()

    async def close(self) -> None:
        try:
            await self.market.close()
        except Exception:
            pass
        try:
            await self.chasseur.close()
        except Exception:
            pass
        try:
            self.analyst.shutdown()
        except Exception:
            pass


async def main() -> None:
    config = get_config()
    setup_logger("both", config.log_level, config.log_file)

    while True:
        fleet = Fleet()
        try:
            await fleet.bootstrap()
            await asyncio.gather(fleet.aave_loop(), fleet.chasseur_loop())
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.error("Flotte redémarrage dans %ds: %s", RESTART_DELAY_SEC, exc)
            logger.debug(traceback.format_exc())
            try:
                await fleet.close()
            except Exception:
                pass
            await asyncio.sleep(RESTART_DELAY_SEC)


if __name__ == "__main__":
    asyncio.run(main())
