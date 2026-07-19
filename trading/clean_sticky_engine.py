"""Moteur live Clean Sticky — bougies TF, sync couleur → position, paper x10."""

from __future__ import annotations

from datetime import datetime, timezone

import pandas as pd

from config import AppConfig
from scanner.market_data import MarketDataService
from trading import clean_sticky_notifications as notif
from trading.clean_sticky_paper import CleanStickyPaper
from trading.clean_sticky_strategy import ColorState, CleanStickyStrategy, StickySignal
from utils.logger import setup_logger

logger = setup_logger(__name__)

SYMBOL = "AAVE/USDT"


def _tf_str(tf_min: int) -> str:
    return {1: "1m", 5: "5m", 15: "15m", 60: "1h"}.get(tf_min, f"{tf_min}m")


class CleanStickyEngine:
    def __init__(
        self,
        config: AppConfig,
        market: MarketDataService,
        telegram,
        *,
        daily_reports: bool = True,
    ) -> None:
        self.config = config
        self.cfg = config.clean_sticky
        self.market = market
        self.telegram = telegram
        self.strategy = CleanStickyStrategy(self.cfg)
        self.trader = CleanStickyPaper(self.cfg)
        self.tf = _tf_str(self.cfg.signal_tf_min)
        self.daily_reports = daily_reports

        self._last_bar_ts: pd.Timestamp | None = None
        self._last_color: ColorState | None = None
        self._last_report_date: str | None = None

    async def _send(self, text: str) -> None:
        try:
            await self.telegram.send_raw(text)
        except Exception as exc:
            logger.error("Telegram: %s", exc)

    async def _sync_to_color(self, sig: StickySignal, *, reason_flip: bool) -> None:
        """Aligne la position sur la couleur : vert=LONG, rouge=SHORT, gris=flat."""
        if self.trader.in_position:
            pos = self.trader.state.position
            should_close = (
                sig.color == ColorState.NEUTRAL
                or (pos.side == 1 and sig.color != ColorState.BULL)
                or (pos.side == -1 and sig.color != ColorState.BEAR)
            )
            if should_close:
                reason = "gray" if sig.color == ColorState.NEUTRAL else "flip"
                trade = self.trader.close(sig.close, reason)
                await self._send(notif.msg_close(trade, self.trader, sig.color))

        if not self.trader.in_position and self.trader.state.balance > 1.0:
            if sig.color == ColorState.BULL:
                pos = self.trader.open(1, sig.close, sig.color.label)
                await self._send(notif.msg_open(pos, sig, self.trader.state.balance))
            elif sig.color == ColorState.BEAR:
                pos = self.trader.open(-1, sig.close, sig.color.label)
                await self._send(notif.msg_open(pos, sig, self.trader.state.balance))
            elif reason_flip:
                logger.info("Couleur gris — reste flat")

    async def on_new_bar(self) -> None:
        df = self.market.get_cached(SYMBOL, self.tf)
        closed = self.market.closed_bars(df).set_index("timestamp")
        if closed.empty:
            return

        last_ts = closed.index[-1]
        if self._last_bar_ts is not None and last_ts <= self._last_bar_ts:
            return
        bar = closed.iloc[-1]
        self._last_bar_ts = last_ts

        # 1. Liquidation intra-bougie (levier)
        if self.trader.in_position:
            liq_px = self.trader.liquidation_hit(float(bar["low"]), float(bar["high"]))
            if liq_px is not None:
                trade = self.trader.close(liq_px, "liquidation")
                color = self._last_color or ColorState.NEUTRAL
                await self._send(notif.msg_close(trade, self.trader, color))

        sig = self.strategy.compute(closed)
        if sig is None:
            await self._maybe_daily_report(float(bar["close"]))
            return

        # Premier passage : entre tout de suite (rouge→SHORT, vert→LONG, gris→flat)
        if self._last_color is None:
            self._last_color = sig.color
            logger.info(
                "Couleur initiale %s close=%.3f ema%d=%.3f ema%d=%.3f — sync position",
                sig.color.label,
                sig.close,
                self.cfg.ema_fast,
                sig.ema_fast,
                self.cfg.ema_slow,
                sig.ema_slow,
            )
            await self._sync_to_color(sig, reason_flip=False)
            await self._maybe_daily_report(sig.close)
            return

        if sig.color == self._last_color:
            logger.debug(
                "Couleur stable %s close=%.3f",
                sig.color.label,
                sig.close,
            )
            await self._maybe_daily_report(sig.close)
            return

        prev = self._last_color
        self._last_color = sig.color
        logger.info(
            "Flip %s → %s close=%.3f ema%d=%.3f ema%d=%.3f",
            prev.label,
            sig.color.label,
            sig.close,
            self.cfg.ema_fast,
            sig.ema_fast,
            self.cfg.ema_slow,
            sig.ema_slow,
        )
        await self._sync_to_color(sig, reason_flip=True)
        await self._maybe_daily_report(sig.close)

    async def _maybe_daily_report(self, price: float) -> None:
        if not self.daily_reports:
            return
        now = datetime.now(timezone.utc)
        today = now.strftime("%Y-%m-%d")
        if (
            now.hour >= self.cfg.daily_report_hour_utc
            and self._last_report_date != today
        ):
            self._last_report_date = today
            await self._send(notif.msg_daily(self.trader, price))
