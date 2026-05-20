"""Bot EQH/EQL AAVE — robuste, alertes Telegram, ne s'arrête pas."""

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

RESTART_DELAY_SEC = 45


def _alert_sweeps() -> bool:
    return os.getenv("ALERT_SWEEPS", "false").strip().lower() in ("1", "true", "yes", "on")


class AAVEEqhEqlBot:
    def __init__(self, config: AppConfig) -> None:
        self.config = config
        self.market = MarketDataService(config)
        self.detector = LiquidityDetector(config)
        self.telegram = TelegramNotifier(config)

    async def _notify_zones(self, zones: list[LiquidityZone]) -> int:
        sent = 0
        for zone in zones:
            try:
                await self.telegram.notify_zone(zone)
                sent += 1
                logger.info(
                    "%s %s %s @ %.4f",
                    zone.zone_type.value,
                    zone.symbol,
                    zone.timeframe,
                    zone.sweep_level,
                )
            except Exception as exc:
                logger.error("Echec envoi Telegram %s: %s", zone.zone_type.value, exc)
        return sent

    async def _handle_result(
        self, symbol: str, timeframe: str, result: ScanResult
    ) -> None:
        await self._notify_zones(result.new_zones)
        if _alert_sweeps():
            for zone, stype, _bar in result.sweeps:
                try:
                    await self.telegram.notify_sweep(zone, stype)
                    logger.info(
                        "SWEEP %s %s %s @ %.4f", stype, symbol, timeframe, zone.sweep_level
                    )
                except Exception as exc:
                    logger.error("Echec sweep Telegram: %s", exc)

    async def _bootstrap(self) -> None:
        await retry_async(self.market.start, label="market_start")
        await self.telegram.start()

        symbols = ", ".join(self.config.scan.symbols)
        tfs = ", ".join(self.config.scan.timeframes)
        await self.telegram.send_raw(
            f"✅ <b>Bot EQH/EQL démarré</b>\n"
            f"Exchange : <code>{self.market.exchange_id}</code>\n"
            f"Build : <code>{BOT_DATA_VERSION}</code>\n"
            f"Pair(s) : <code>{symbols}</code>\n"
            f"TF : <code>{tfs}</code>\n"
            f"Pivot L/R : <code>{self.config.pivot.pivot_left}</code> / "
            f"<code>{self.config.pivot.pivot_right}</code>\n"
            f"Seuil : <code>{self.config.pivot.threshold_pct}%</code>\n"
            f"Rattrapage : <code>{self.config.scan.catchup_bars}</code> barres\n"
            f"Alertes : <code>EQH + EQL</code>"
        )

        for symbol in self.config.scan.symbols:
            for tf in self.config.scan.timeframes:
                async def _fetch_df(sym: str = symbol, tframe: str = tf) -> object:
                    return await self.market.fetch_ohlcv(sym, tframe)

                df = await retry_async(_fetch_df, label="warmup_fetch")
                if len(df) < self.config.scan.min_bars:
                    logger.warning("Pas assez de barres pour %s %s", symbol, tf)
                    continue
                n = self.detector.warmup(symbol, tf, df)
                catchup = self.detector.catchup_recent(
                    symbol, tf, df, self.config.scan.catchup_bars
                )
                sent = await self._notify_zones(catchup.new_zones)
                await self.telegram.send_raw(
                    f"📊 Warmup <code>{symbol}</code> {tf} — "
                    f"<code>{n}</code> zones, rattrapage <code>{sent}</code> alerte(s)."
                )

    async def _scan(self, symbol: str, timeframe: str) -> None:
        df = await self.market.fetch_ohlcv(symbol, timeframe)
        closed = self.market.closed_bars(df)
        if len(closed) < self.config.scan.min_bars:
            return

        closed_ts = self.market.last_closed_ts(df)
        result = self.detector.scan(
            symbol,
            timeframe,
            closed,
            closed_ts,
            catchup_max_bars=self.config.scan.catchup_max_bars,
        )
        await self._handle_result(symbol, timeframe, result)

    async def _run_loop(self) -> None:
        while True:
            delays = [
                self.market.seconds_until_refresh(symbol, tf)
                for symbol in self.config.scan.symbols
                for tf in self.config.scan.timeframes
            ]
            wait = min(delays) if delays else 5.0
            if wait > 0.5:
                await asyncio.sleep(wait)

            for symbol in self.config.scan.symbols:
                for tf in self.config.scan.timeframes:
                    try:
                        await self._scan(symbol, tf)
                    except Exception as exc:
                        logger.error("Scan %s %s: %s", symbol, tf, exc)

    async def run(self) -> None:
        await self._bootstrap()
        logger.info(
            "Bot actif — %s | %s",
            self.market.exchange_id,
            ", ".join(self.config.scan.symbols),
        )
        await self._run_loop()

    async def shutdown(self) -> None:
        try:
            await self.market.close()
        except Exception as exc:
            logger.warning("Fermeture market: %s", exc)


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
            logger.error("Crash bot — redemarrage dans %ds: %s", RESTART_DELAY_SEC, exc)
            logger.debug(traceback.format_exc())
            try:
                await bot.shutdown()
            except Exception:
                pass
            try:
                await bot.telegram.send_raw(
                    f"⚠️ <b>Bot redémarre</b>\nErreur : <code>{exc}</code>\n"
                    f"Relance dans {RESTART_DELAY_SEC}s…"
                )
            except Exception:
                pass
            await asyncio.sleep(RESTART_DELAY_SEC)


if __name__ == "__main__":
    asyncio.run(main())
