"""Bot EQH/EQL AAVE — alertes Telegram à chaque détection EQH/EQL."""

from __future__ import annotations

import asyncio
import os

from config import AppConfig, get_config
from scanner.liquidity_detector import LiquidityDetector
from scanner.market_data import BOT_DATA_VERSION, MarketDataService
from notifier.bot import TelegramNotifier
from utils.logger import setup_logger

logger = setup_logger("main")


def _alert_sweeps() -> bool:
    return os.getenv("ALERT_SWEEPS", "false").strip().lower() in ("1", "true", "yes", "on")


class AAVEEqhEqlBot:
    def __init__(self, config: AppConfig) -> None:
        self.config = config
        self.market = MarketDataService(config)
        self.detector = LiquidityDetector(config)
        self.telegram = TelegramNotifier(config)

    async def run(self) -> None:
        try:
            await self.market.start()
            await self.telegram.start()

            symbols = ", ".join(self.config.scan.symbols)
            tfs = ", ".join(self.config.scan.timeframes)
            sweeps = _alert_sweeps()
            await self.telegram.send_raw(
                f"✅ <b>Bot EQH/EQL démarré</b>\n"
                f"Exchange : <code>{self.market.exchange_id}</code>\n"
                f"Build : <code>{BOT_DATA_VERSION}</code>\n"
                f"Pair(s) : <code>{symbols}</code>\n"
                f"TF : <code>{tfs}</code>\n"
                f"Pivot L/R : <code>{self.config.pivot.pivot_left}</code> / "
                f"<code>{self.config.pivot.pivot_right}</code>\n"
                f"Seuil : <code>{self.config.pivot.threshold_pct}%</code>\n"
                f"Timing : <code>aligné clôture {self.config.scan.timeframes[0]} "
                f"(+{self.config.scan.candle_close_buffer_sec:.0f}s)</code>\n"
                f"Alertes : <code>EQH + EQL à la détection</code>"
                + (f" + sweeps" if sweeps else "")
            )
            logger.info("Bot demarre — %s | %s | %s", self.market.exchange_id, symbols, tfs)

            for symbol in self.config.scan.symbols:
                for tf in self.config.scan.timeframes:
                    df = await self.market.fetch_ohlcv(symbol, tf)
                    if len(df) >= self.config.scan.min_bars:
                        n = self.detector.warmup(symbol, tf, df)
                        await self.telegram.send_raw(
                            f"📊 Warmup <code>{symbol}</code> {tf} — "
                            f"<code>{n}</code> zones en mémoire. "
                            f"Prochaines alertes = nouveaux EQH/EQL en live."
                        )

            while True:
                delays = [
                    self.market.seconds_until_refresh(symbol, tf)
                    for symbol in self.config.scan.symbols
                    for tf in self.config.scan.timeframes
                ]
                wait = min(delays) if delays else 5.0
                if wait > 0.5:
                    logger.debug("Prochain scan dans %.1fs", wait)
                    await asyncio.sleep(wait)

                tasks = [
                    self._scan(symbol, tf)
                    for symbol in self.config.scan.symbols
                    for tf in self.config.scan.timeframes
                ]
                await asyncio.gather(*tasks, return_exceptions=True)
        finally:
            await self.market.close()

    async def _scan(self, symbol: str, timeframe: str) -> None:
        try:
            df = await self.market.fetch_ohlcv(symbol, timeframe)
            closed = self.market.closed_bars(df)
            if len(closed) < self.config.scan.min_bars:
                return

            closed_ts = self.market.last_closed_ts(df)
            result = self.detector.process(symbol, timeframe, closed, closed_ts)

            for zone in result.new_zones:
                await self.telegram.notify_zone(zone)
                logger.info(
                    "%s %s %s @ %.4f",
                    zone.zone_type.value,
                    symbol,
                    timeframe,
                    zone.sweep_level,
                )

            if _alert_sweeps():
                for zone, stype, _bar in result.sweeps:
                    await self.telegram.notify_sweep(zone, stype)
                    logger.info("SWEEP %s %s %s @ %.4f", stype, symbol, timeframe, zone.sweep_level)

        except Exception as exc:
            logger.warning("Scan ignore %s %s: %s", symbol, timeframe, exc)


async def main() -> None:
    config = get_config()
    setup_logger("main", config.log_level, config.log_file)
    bot = AAVEEqhEqlBot(config)
    await bot.run()


if __name__ == "__main__":
    asyncio.run(main())
