"""Bot EQH/EQL AAVE — Binance + Telegram (LuxAlgo)."""

from __future__ import annotations

import asyncio
import time
from typing import Dict, List, Tuple

import pandas as pd

from config import AppConfig, get_config
from models.liquidity_zone import LiquidityZone
from scanner.liquidity_detector import LiquidityDetector
from scanner.market_data import MarketDataService
from notifier.bot import TelegramNotifier
from utils.logger import setup_logger
from utils.pending_sweeps import PendingSweep, PendingSweepStore
from utils.signal_filter import (
    FilterVerdict,
    filter_sweep,
    filter_sweep_confirm_bar,
    filter_zone,
)

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
        self.pending_sweeps = PendingSweepStore()
        self._filter_rejects = 0

    def _filter_summary(self) -> str:
        f = self.config.filter
        parts = [
            f"zones={'on' if f.alert_zone_detection else 'off'}",
            f"sweeps={'on' if f.alert_sweeps else 'off'}",
            f"score>={f.min_sweep_score:.0f}",
            f"zone<={f.max_zone_width_pct:.2f}%",
            f"pivots>={f.min_pivot_bars_apart}b",
        ]
        if f.sweep_confirm_next_bar:
            parts.append("confirm_next")
        elif f.sweep_require_rejection:
            parts.append("rejet_close")
        if f.utc_hours_enabled:
            parts.append(f"UTC {f.utc_hour_start}-{f.utc_hour_end}h")
        return ", ".join(parts)

    async def run(self) -> None:
        try:
            await self.market.start()
            await self.telegram.start()

            symbols = ", ".join(self.config.scan.symbols)
            tfs = ", ".join(self.config.scan.timeframes)
            await self.telegram.send_raw(
                f"✅ <b>Bot EQH/EQL démarré</b>\n"
                f"Exchange : <code>{self.market.exchange_id}</code>\n"
                f"Pair(s) : <code>{symbols}</code>\n"
                f"TF : <code>{tfs}</code>\n"
                f"Pivot L/R : <code>{self.config.pivot.pivot_left}</code> / "
                f"<code>{self.config.pivot.pivot_right}</code>\n"
                f"Seuil : <code>{self.config.pivot.threshold_pct}%</code>\n"
                f"Filtres : <code>{self._filter_summary()}</code>"
            )
            logger.info("Bot demarre — %s | %s | %s", self.market.exchange_id, symbols, tfs)

            for symbol in self.config.scan.symbols:
                for tf in self.config.scan.timeframes:
                    df = await self.market.fetch_ohlcv(symbol, tf)
                    if len(df) >= self.config.scan.min_bars:
                        n = self.detector.warmup(symbol, tf, df)
                        await self.telegram.send_raw(
                            f"📊 Warmup <code>{symbol}</code> {tf} — "
                            f"<code>{n}</code> zones historiques (pas d'alerte retro). "
                            f"Signaux filtres en live uniquement."
                        )

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
            closed = self.market.closed_bars(df)
            if len(closed) < self.config.scan.min_bars:
                return

            closed_ts = self.market.last_closed_ts(df)
            end_bar = len(closed) - 1
            self.pending_sweeps.prune_expired(symbol, timeframe, end_bar)

            await self._check_pending_confirms(symbol, timeframe, closed, end_bar)

            result = self.detector.process(symbol, timeframe, closed, closed_ts)
            filt = self.config.filter

            for zone in result.new_zones:
                bar_index = zone.created_bar_index
                fr = filter_zone(zone, closed, bar_index, filt)
                if fr.verdict != FilterVerdict.PASS:
                    self._filter_rejects += 1
                    logger.debug("Zone filtree: %s — %s", zone.zone_type.value, fr.reason)
                    continue
                key = f"detect:{zone.dedupe_key()}"
                if self.cooldown.allow(key):
                    await self.telegram.notify_zone(zone)
                    logger.info(
                        "%s %s %s @ %.4f (filtre OK)",
                        zone.zone_type.value,
                        symbol,
                        timeframe,
                        zone.sweep_level,
                    )

            for zone, stype, bar_index in result.sweeps:
                fr = filter_sweep(zone, stype, closed, bar_index, filt)
                if fr.verdict == FilterVerdict.REJECT:
                    self._filter_rejects += 1
                    logger.debug("Sweep filtre: %s — %s", stype, fr.reason)
                    continue
                if fr.verdict == FilterVerdict.PENDING:
                    expires = bar_index + filt.sweep_confirm_max_bars
                    self.pending_sweeps.add(
                        symbol,
                        timeframe,
                        PendingSweep(zone, stype, bar_index, expires),
                    )
                    logger.debug("Sweep en attente confirm: %s bar %d", stype, bar_index)
                    continue
                await self._alert_sweep(symbol, timeframe, zone, stype)

        except Exception as exc:
            logger.exception("Erreur %s %s: %s", symbol, timeframe, exc)

    async def _check_pending_confirms(
        self,
        symbol: str,
        timeframe: str,
        closed: pd.DataFrame,
        end_bar: int,
    ) -> None:
        filt = self.config.filter
        for pending in list(self.pending_sweeps.for_pair(symbol, timeframe)):
            if end_bar <= pending.sweep_bar_index:
                continue
            confirm_bar = pending.sweep_bar_index + 1
            if confirm_bar > end_bar:
                continue
            if end_bar > pending.expires_bar_index:
                self.pending_sweeps.remove(symbol, timeframe, pending.zone.zone_id)
                logger.debug("Sweep expire sans confirm: %s", pending.zone.zone_id)
                continue

            fr = filter_sweep_confirm_bar(pending.zone, closed, confirm_bar, filt)
            self.pending_sweeps.remove(symbol, timeframe, pending.zone.zone_id)
            if fr.verdict != FilterVerdict.PASS:
                self._filter_rejects += 1
                logger.debug("Confirm sweep filtree: %s", fr.reason)
                continue
            await self._alert_sweep(symbol, timeframe, pending.zone, pending.sweep_type)

    async def _alert_sweep(
        self,
        symbol: str,
        timeframe: str,
        zone: LiquidityZone,
        stype: str,
    ) -> None:
        key = f"sweep:{zone.zone_id}"
        if not self.cooldown.allow(key, is_sweep=True):
            return
        await self.telegram.notify_sweep(zone, stype)
        logger.info("SWEEP %s %s %s @ %.4f (filtre OK)", stype, symbol, timeframe, zone.sweep_level)


async def main() -> None:
    config = get_config()
    setup_logger("main", config.log_level, config.log_file)
    bot = AAVEEqhEqlBot(config)
    await bot.run()


if __name__ == "__main__":
    asyncio.run(main())
