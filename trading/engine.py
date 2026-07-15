"""Moteur live — boucle bougie 5m, signaux TF, exécution paper, alertes Telegram."""

from __future__ import annotations

from datetime import datetime, timezone

import pandas as pd

from config import AppConfig
from scanner.market_data import MarketDataService
from trading import notifications as notif
from trading.paper import PaperTrader
from trading.strategy import EmaFlipStrategy
from trading.warmup import fetch_history_tf
from utils.logger import setup_logger

logger = setup_logger(__name__)

SYMBOL = "AAVE/USDT"
TF_5M = "5m"


class TradingEngine:
    def __init__(self, config: AppConfig, market: MarketDataService, telegram) -> None:
        self.config = config
        self.cfg = config.trading
        self.market = market
        self.telegram = telegram
        self.strategy = EmaFlipStrategy(self.cfg)
        self.trader = PaperTrader(self.cfg)

        self._tf_history: pd.DataFrame | None = None
        self._last_tf_ts: pd.Timestamp | None = None
        self._last_5m_ts: pd.Timestamp | None = None
        self._be_notified = False
        # Initialisé à aujourd'hui pour ne pas envoyer un rapport à chaque redémarrage
        self._last_report_date: str | None = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    # ------------------------------------------------------------------ warmup
    async def warmup(self) -> None:
        scale = max(1, self.cfg.htf_tf_min // self.cfg.signal_tf_min)
        bars_needed = self.cfg.htf_slow * scale * 2  # 2x le span de l'EMA la plus lente
        self._tf_history = await fetch_history_tf(self.cfg.signal_tf_min, bars_needed)
        self._last_tf_ts = self._tf_history.index[-1]
        pos = self.trader.state.position
        self._be_notified = bool(
            pos and (
                (pos.side == 1 and pos.stop >= pos.entry)
                or (pos.side == -1 and pos.stop <= pos.entry)
            )
        )
        logger.info(
            "Warmup TF%dmin : %d bougies, dernière %s",
            self.cfg.signal_tf_min, len(self._tf_history), self._last_tf_ts,
        )

    # ------------------------------------------------------------------ helpers
    async def _send(self, text: str) -> None:
        try:
            await self.telegram.send_raw(text)
        except Exception as exc:
            logger.error("Telegram: %s", exc)

    def _merged_tf(self, df_5m_closed: pd.DataFrame) -> pd.DataFrame:
        """Fusionne l'historique warmup avec les bougies TF issues du flux 5m."""
        fresh = self.strategy.resample(df_5m_closed)
        if self._tf_history is None:
            return fresh
        if fresh.empty:
            return self._tf_history
        merged = pd.concat([self._tf_history, fresh])
        merged = merged[~merged.index.duplicated(keep="last")].sort_index()
        # Borne la mémoire : garde 3x le besoin en barres
        scale = max(1, self.cfg.htf_tf_min // self.cfg.signal_tf_min)
        max_bars = self.cfg.htf_slow * scale * 3
        self._tf_history = merged.tail(max_bars)
        return self._tf_history

    # ------------------------------------------------------------------ cœur
    async def on_new_5m_close(self) -> None:
        """Appelé après chaque rafraîchissement du cache 5m."""
        df = self.market.get_cached(SYMBOL, TF_5M)
        closed = self.market.closed_bars(df).set_index("timestamp")
        if closed.empty:
            return
        last_5m = closed.iloc[-1]
        last_5m_ts = closed.index[-1]
        if self._last_5m_ts is not None and last_5m_ts <= self._last_5m_ts:
            return
        self._last_5m_ts = last_5m_ts

        # 1. Stop intra-bougie sur la 5m qui vient de clôturer (réactivité max)
        if self.trader.in_position and self.trader.stop_hit(
            float(last_5m["low"]), float(last_5m["high"])
        ):
            pos = self.trader.state.position
            was_protected = self._be_notified
            trade = self.trader.close(pos.stop, "trailing" if was_protected else "stop")
            self._be_notified = False
            await self._send(notif.msg_close(trade, self.trader))

        # 2. Nouvelle bougie TF signal ?
        tf_df = self._merged_tf(closed)
        if tf_df.empty:
            return
        new_tf_ts = tf_df.index[-1]
        if self._last_tf_ts is not None and new_tf_ts <= self._last_tf_ts:
            await self._maybe_daily_report(float(last_5m["close"]))
            return
        self._last_tf_ts = new_tf_ts

        sig = self.strategy.compute(tf_df)
        if sig is None:
            return
        logger.info(
            "TF%dmin close=%.3f ema=%.3f bias=%+d htf=%s er=%.2f",
            self.cfg.signal_tf_min, sig.close, sig.ema, sig.bias,
            "bull" if sig.htf_bull else "bear" if sig.htf_bear else "flat", sig.er,
        )

        # 3. Trailing stop sur clôture TF
        if self.trader.in_position:
            pos = self.trader.state.position
            bar = tf_df.iloc[-1]
            new_stop = self.strategy.trail_stop(
                pos.side, pos.stop, float(bar["high"]), float(bar["low"]), sig.atr
            )
            if self.trader.update_stop(new_stop):
                logger.info("Trailing stop -> %.3f", new_stop)
                protected = (pos.side == 1 and pos.stop >= pos.entry) or (
                    pos.side == -1 and pos.stop <= pos.entry
                )
                if protected and not self._be_notified:
                    self._be_notified = True
                    await self._send(notif.msg_breakeven(pos))

        # 4. Entrée si flat
        if not self.trader.in_position and sig.direction != 0:
            entry = sig.close
            stop = self.strategy.initial_stop(sig.direction, entry, sig.atr)
            pos = self.trader.open(sig.direction, entry, sig.atr, stop)
            self._be_notified = False
            await self._send(notif.msg_open(pos, sig, self.trader.state.balance))

        await self._maybe_daily_report(float(last_5m["close"]))

    # ------------------------------------------------------------------ rapport
    async def _maybe_daily_report(self, price: float) -> None:
        now = datetime.now(timezone.utc)
        today = now.strftime("%Y-%m-%d")
        if now.hour >= self.cfg.daily_report_hour_utc and self._last_report_date != today:
            self._last_report_date = today
            await self._send(notif.msg_daily(self.trader, price))
