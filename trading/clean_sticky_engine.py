"""Moteur live Clean Sticky — couleurs EMA + confirmation multi-bougies."""

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
        self._last_report_date: str | None = None
        self._logged_wait = False

    async def _send(self, text: str) -> None:
        try:
            await self.telegram.send_raw(text)
        except Exception as exc:
            logger.error("Telegram: %s", exc)

    def _confirmed_side(self, colors: pd.Series) -> int:
        """+1 long / -1 short / 0 flat — exige N bougies identiques (vert ou rouge)."""
        n = max(1, self.cfg.confirm_bars)
        if len(colors) < n:
            return 0
        last = colors.iloc[-n:]
        if (last == int(ColorState.BULL)).all():
            return 1
        if (last == int(ColorState.BEAR)).all():
            return -1
        return 0

    def _exit_confirmed(self, colors: pd.Series, side: int) -> bool:
        """True si N bougies d'affilée ne supportent plus la position."""
        n = max(1, self.cfg.exit_confirm_bars)
        if len(colors) < n:
            return False
        last = colors.iloc[-n:]
        want = int(ColorState.BULL if side == 1 else ColorState.BEAR)
        # Sortie si aucune des N bougies n'est plus de la bonne couleur
        # (toutes gris ou opposées)
        return bool((last != want).all())

    async def _maybe_open(self, sig: StickySignal, side: int) -> None:
        if self.trader.in_position or self.trader.state.balance <= 1.0 or side == 0:
            return
        pos = self.trader.open(side, sig.close, sig.color.label)
        await self._send(notif.msg_open(pos, sig, self.trader.state.balance))
        logger.info(
            "Entrée confirmée %s après %d bougies %s",
            pos.side_label,
            self.cfg.confirm_bars,
            sig.color.label,
        )

    async def _maybe_close(self, sig: StickySignal, reason: str) -> None:
        if not self.trader.in_position:
            return
        trade = self.trader.close(sig.close, reason)
        await self._send(notif.msg_close(trade, self.trader, sig.color))

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

        # 1. Liquidation (levier) — immédiate
        if self.trader.in_position:
            liq_px = self.trader.liquidation_hit(float(bar["low"]), float(bar["high"]))
            if liq_px is not None:
                trade = self.trader.close(liq_px, "liquidation")
                await self._send(
                    notif.msg_close(trade, self.trader, ColorState.NEUTRAL)
                )

        sig = self.strategy.compute(closed)
        if sig is None:
            await self._maybe_daily_report(float(bar["close"]))
            return

        colors = self.strategy.color_series(closed)
        confirmed = self._confirmed_side(colors)

        logger.info(
            "Couleur %s (confirm entrée=%d sortie=%d) close=%.3f ema%d=%.3f ema%d=%.3f",
            sig.color.label,
            self.cfg.confirm_bars,
            self.cfg.exit_confirm_bars,
            sig.close,
            self.cfg.ema_fast,
            sig.ema_fast,
            self.cfg.ema_slow,
            sig.ema_slow,
        )

        # 2. Sortie confirmée (N bougies plus alignées)
        if self.trader.in_position:
            pos = self.trader.state.position
            if self._exit_confirmed(colors, pos.side):
                reason = "gray" if sig.color == ColorState.NEUTRAL else "flip"
                await self._maybe_close(sig, reason)
            else:
                logger.debug(
                    "Position %s gardée — sortie pas encore confirmée (%d bougies)",
                    pos.side_label,
                    self.cfg.exit_confirm_bars,
                )

        # 3. Entrée confirmée (N bougies vert ou rouge d'affilée)
        if not self.trader.in_position:
            if confirmed != 0:
                await self._maybe_open(sig, confirmed)
            elif not self._logged_wait:
                logger.info(
                    "Attente confirmation : besoin de %d bougies %s/%s d'affilée",
                    self.cfg.confirm_bars,
                    "vert",
                    "rouge",
                )
                self._logged_wait = True

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
