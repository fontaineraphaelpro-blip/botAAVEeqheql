"""Bot EQH/EQL AAVE — Binance + Telegram (LuxAlgo)."""

from __future__ import annotations

import asyncio
import time
from typing import Dict

from config import AppConfig, get_config
from models.liquidity_zone import LiquidityZone
from scanner.liquidity_detector import LiquidityDetector
from scanner.market_data import MarketDataService
from telegram.bot import TelegramNotifier
from utils.logger import setup_logger

logger = setup_logger("main")


class SignalCooldown:
    def __init__(self, config: AppConfig) -> None:
        self.config = config
        self._last: Dict[str, float] = {}

    def allow(self, key: str, is_sweep: bool = False) -> bool:
        now = time.monotonic()
        cooldown = (
            self.config.cooldown.sweep_cooldown_sec
            if is_sweep
            else self.config.cooldown.signal_cooldown_sec
        )
        if now - self._last.get(key, 0.0) < cooldown:
            return False
        self._last[key] = now
        return True


class AAVEEqhEqlBot:
    def __init__(self, config: AppConfig) -> None:
        self.config = config
        self.market = MarketDataService(config)
        self.detector = LiquidityDetector(config)
        self.telegram = TelegramNotifier(config)
        self.cooldown = SignalCooldown(config)

    async def run(self) -> None:
        await self.market.start()
        await self.telegram.start()

        symbols = ", ".join(self.config.scan.symbols)
        tfs = ", ".join(self.config.scan.timeframes)
        await self.telegram.send_raw(
            f"✅ <b>Bot EQH/EQL démarré</b>\n"
            f"Pair(s) : <code>{symbols}</code>\n"
            f"TF : <code>{tfs}</code>\n"
            f"Pivot L/R : <code>{self.config.pivot.pivot_left}</code> / "
            f"<code>{self.config.pivot.pivot_right}</code>\n"
            f"Seuil : <code>{self.config.pivot.threshold_pct}%</code>"
        )
        logger.info("Bot démarré — %s | %s", symbols, tfs)

        try:
            while True:
                tasks = [
                    self._scan(symbol, tf)
                    for symbol in self.config.scan.symbols
                    for tf in self.config.scan.timeframes
                ]
                await asyncio.gather(*tasks, return_exceptions=True)
                await asyncio.sleep(self.config.scan.poll_interval_sec)
        finally:
            await self.market.close()

    async def _scan(self, symbol: str, timeframe: str) -> None:
        try:
            df = await self.market.fetch_ohlcv(symbol, timeframe)
            if len(df) < self.config.scan.min_bars:
                return

            closed_ts = self.market.last_closed_ts(df)
            result = self.detector.process(symbol, timeframe, df, closed_ts)

            for zone in result.new_zones:
                key = f"detect:{zone.dedupe_key()}"
                if self.cooldown.allow(key):
                    await self.telegram.notify_zone(zone)
                    logger.info(
                        "%s %s %s @ %.4f",
                        zone.zone_type.value,
                        symbol,
                        timeframe,
                        zone.sweep_level,
                    )

            for zone, stype in result.sweeps:
                key = f"sweep:{zone.zone_id}"
                if self.cooldown.allow(key, is_sweep=True):
                    await self.telegram.notify_sweep(zone, stype)
                    logger.info("SWEEP %s %s %s @ %.4f", stype, symbol, timeframe, zone.sweep_level)

        except Exception as exc:
            logger.exception("Erreur %s %s: %s", symbol, timeframe, exc)


async def main() -> None:
    config = get_config()
    setup_logger("main", config.log_level, config.log_file)
    bot = AAVEEqhEqlBot(config)
    await bot.run()


if __name__ == "__main__":
    asyncio.run(main())
