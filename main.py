"""Bot EQH/EQL — alertes live uniquement, ne crash pas."""

from __future__ import annotations

import asyncio
import os
import traceback

from config import AppConfig, get_config
from models.liquidity_zone import LiquidityZone
from scanner.liquidity_detector import LiquidityDetector, ScanResult
from scanner.market_data import BOT_DATA_VERSION, MarketDataService
from notifier.bot import TelegramNotifier
from utils.logger import setup_logger
from utils.resilience import retry_async

logger = setup_logger("main")

RESTART_DELAY_SEC = 30


def _alert_sweeps() -> bool:
    return os.getenv("ALERT_SWEEPS", "false").strip().lower() in ("1", "true", "yes", "on")


class AAVEEqhEqlBot:
    def __init__(self, config: AppConfig) -> None:
        self.config = config
        self.market = MarketDataService(config)
        self.detector = LiquidityDetector(config)
        self.telegram = TelegramNotifier(config)
        self._warmup_info: tuple[int, float, int] | None = None

    async def _notify_zones(self, zones: list[LiquidityZone]) -> None:
        for zone in zones:
            try:
                await self.telegram.notify_zone(zone)
                logger.info(
                    "%s %s %s @ %.4f",
                    zone.zone_type.value,
                    zone.symbol,
                    zone.timeframe,
                    zone.sweep_level,
                )
            except Exception as exc:
                logger.error("Telegram %s: %s", zone.zone_type.value, exc)

    async def _handle_result(self, result: ScanResult, symbol: str, timeframe: str) -> None:
        await self._notify_zones(result.new_zones)
        if not _alert_sweeps():
            return
        for zone, stype, _bar in result.sweeps:
            try:
                await self.telegram.notify_sweep(zone, stype)
            except Exception as exc:
                logger.error("Telegram sweep: %s", exc)

    async def _load_history(self, symbol: str, timeframe: str) -> bool:
        for attempt in range(12):
            try:
                async def _fetch(sym: str = symbol, tf: str = timeframe) -> object:
                    return await self.market.fetch_ohlcv(sym, tf)

                df = await retry_async(_fetch, attempts=3, label="history")
                if len(df) >= self.config.scan.min_bars:
                    closed_n = max(0, len(df) - 1)
                    zones = self.detector.warmup(symbol, timeframe, df)
                    hours = closed_n * 5 / 60
                    self._warmup_info = (closed_n, hours, zones)
                    return True
            except Exception as exc:
                logger.warning(
                    "Chargement historique %s %s (%d/12): %s",
                    symbol,
                    timeframe,
                    attempt + 1,
                    exc,
                )
            await asyncio.sleep(15)
        return False

    async def _bootstrap(self) -> None:
        await retry_async(self.market.start, attempts=6, label="market")
        await self.telegram.start()

        await self.telegram.send_raw(
            f"✅ <b>Bot EQH/EQL actif</b>\n"
            f"Exchange : <code>{self.market.exchange_id}</code>\n"
            f"Build : <code>{BOT_DATA_VERSION}</code>\n"
            f"Mode : <code>alertes LIVE uniquement</code>\n"
            f"Chaque nouveau EQH/EQL → notification."
        )

        for symbol in self.config.scan.symbols:
            for tf in self.config.scan.timeframes:
                ok = await self._load_history(symbol, tf)
                if ok and self._warmup_info:
                    bars, hours, zones = self._warmup_info
                    await self.telegram.send_raw(
                        f"📊 Historique <code>{symbol}</code> {tf}\n"
                        f"<code>{bars}</code> bougies 5m (~<code>{hours:.0f}h</code>) — "
                        f"<code>{zones}</code> zones en mémoire.\n"
                        f"Alertes = nouveaux EQH/EQL seulement."
                    )
                elif not ok:
                    logger.error("Historique indisponible %s %s — retry en boucle", symbol, tf)

    async def _scan(self, symbol: str, timeframe: str) -> None:
        try:
            df = await self.market.fetch_ohlcv(symbol, timeframe)
        except Exception as exc:
            logger.warning("Fetch %s %s: %s — cache", symbol, timeframe, exc)
            if not self.market.has_cache(symbol, timeframe):
                return
            df = self.market.get_cached(symbol, timeframe)

        closed = self.market.closed_bars(df)
        if len(closed) < self.config.scan.min_bars:
            return

        closed_ts = self.market.last_closed_ts(df)
        result = self.detector.scan_live(
            symbol, timeframe, closed, closed_ts,
            max_gap_bars=self.config.scan.gap_fill_max_bars,
        )
        await self._handle_result(result, symbol, timeframe)

    async def _run_loop(self) -> None:
        while True:
            try:
                delays = [
                    self.market.seconds_until_refresh(s, t)
                    for s in self.config.scan.symbols
                    for t in self.config.scan.timeframes
                ]
                wait = min(delays) if delays else 10.0
                if wait > 0.5:
                    await asyncio.sleep(wait)

                for symbol in self.config.scan.symbols:
                    for tf in self.config.scan.timeframes:
                        await self._scan(symbol, tf)
            except Exception as exc:
                logger.error("Boucle scan: %s", exc)
                await asyncio.sleep(5)

    async def run(self) -> None:
        await self._bootstrap()
        logger.info("Live scan — %s", self.market.exchange_id)
        await self._run_loop()

    async def shutdown(self) -> None:
        try:
            await self.market.close()
        except Exception:
            pass


async def main() -> None:
    config = get_config()
    setup_logger("main", config.log_level, config.log_file)

    while True:
        bot = AAVEEqhEqlBot(config)
        try:
            await bot.run()
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.error("Redemarrage dans %ds: %s", RESTART_DELAY_SEC, exc)
            logger.debug(traceback.format_exc())
            try:
                await bot.shutdown()
            except Exception:
                pass
            await asyncio.sleep(RESTART_DELAY_SEC)


if __name__ == "__main__":
    asyncio.run(main())
