"""Moteur live du chasseur de shorts — cycle aligné sur les bougies 2h Binance."""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone

import aiohttp
import pandas as pd

from config import AppConfig
from trading.bot_ids import BOT_CHASSEUR, TAG_CHASSEUR, header
from trading.short_paper import ShortPortfolio, ShortTrade
from trading.short_strategy import ShortHunterStrategy, ShortSignal
from utils.logger import setup_logger

logger = setup_logger(__name__)

KLINES_SOURCES = (
    "https://data-api.binance.vision/api/v3/klines",
    "https://api.mexc.com/api/v3/klines",
)
ALT_BARS = 300          # ~25 jours de 2h — suffisant pour low_n=120 + ER + ATR
BTC_BARS = 3000         # ~250 jours — EMA 200j + pente 30j


def _ts() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")


class ShortHunterEngine:
    def __init__(self, config: AppConfig, telegram) -> None:
        self.cfg = config.shorts
        self.telegram = telegram
        self.strategy = ShortHunterStrategy(self.cfg)
        self.portfolio = ShortPortfolio(self.cfg)
        self._session: aiohttp.ClientSession | None = None
        self._last_bar_ts: pd.Timestamp | None = None
        self._regime_bear: bool | None = None

    # ------------------------------------------------------------- HTTP
    async def _get_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            connector = aiohttp.TCPConnector(resolver=aiohttp.ThreadedResolver(), limit=4)
            self._session = aiohttp.ClientSession(
                connector=connector,
                timeout=aiohttp.ClientTimeout(total=30),
                headers={"User-Agent": "AAVE-Short-Hunter/1.0"},
            )
        return self._session

    async def close(self) -> None:
        if self._session and not self._session.closed:
            await self._session.close()

    async def _fetch_klines(self, symbol: str, limit: int) -> pd.DataFrame | None:
        session = await self._get_session()
        for url in KLINES_SOURCES:
            try:
                async with session.get(
                    url, params={"symbol": symbol, "interval": "2h", "limit": min(limit, 1000)}
                ) as resp:
                    if resp.status != 200:
                        continue
                    batch = await resp.json()
                if not batch:
                    continue
                df = pd.DataFrame(
                    [[int(r[0]), float(r[1]), float(r[2]), float(r[3]), float(r[4]), float(r[5])]
                     for r in batch],
                    columns=["timestamp", "open", "high", "low", "close", "volume"],
                )
                df["timestamp"] = pd.to_datetime(df["timestamp"], unit="ms", utc=True)
                df = df.set_index("timestamp").sort_index()
                return df.iloc[:-1]  # écarte la bougie en cours
            except Exception as exc:
                logger.warning("Klines %s (%s): %s", symbol, url.split("/")[2], exc)
        return None

    async def _fetch_btc_long(self) -> pd.DataFrame | None:
        """BTC : pagine jusqu'à ~3000 bougies 2h pour l'EMA 200 jours."""
        session = await self._get_session()
        url = KLINES_SOURCES[0]
        rows: list[list] = []
        end_time: int | None = None
        try:
            while len(rows) < BTC_BARS:
                params: dict = {"symbol": "BTCUSDT", "interval": "2h", "limit": 1000}
                if end_time is not None:
                    params["endTime"] = end_time
                async with session.get(url, params=params) as resp:
                    resp.raise_for_status()
                    batch = await resp.json()
                if not batch:
                    break
                rows = list(batch) + rows
                end_time = int(batch[0][0]) - 1
                if len(batch) < 1000:
                    break
                await asyncio.sleep(0.2)
        except Exception as exc:
            logger.error("BTC historique: %s", exc)
            return None
        if not rows:
            return None
        df = pd.DataFrame(
            [[int(r[0]), float(r[1]), float(r[2]), float(r[3]), float(r[4]), float(r[5])]
             for r in rows],
            columns=["timestamp", "open", "high", "low", "close", "volume"],
        )
        df["timestamp"] = pd.to_datetime(df["timestamp"], unit="ms", utc=True)
        df = df.drop_duplicates(subset="timestamp").set_index("timestamp").sort_index()
        return df.iloc[:-1]

    # ------------------------------------------------------------- Telegram
    async def _send(self, text: str) -> None:
        try:
            await self.telegram.send_raw(text)
        except Exception as exc:
            logger.error("Telegram: %s", exc)

    def _msg_open(self, pos, sig: ShortSignal) -> str:
        return (
            f"{header(TAG_CHASSEUR, f'🔴 SHORT <code>{pos.symbol}</code>')}\n"
            f"Entrée <code>{pos.entry:.6g}</code> · stop <code>{pos.stop:.6g}</code>\n"
            f"Taille <code>{pos.notional:.0f}</code> USDT · "
            f"mom7j {sig.mom7d * 100:+.1f}% · ER {sig.er:.2f}\n"
            f"Pos {len(self.portfolio.positions)}/{self.cfg.max_positions} · {_ts()}"
        )

    def _msg_close(self, trade: ShortTrade) -> str:
        emoji = "✅" if trade.pnl > 0 else "🛑"
        s = self.portfolio.stats()
        return (
            f"{header(TAG_CHASSEUR, f'{emoji} Cover <code>{trade.symbol}</code>')}\n"
            f"<code>{trade.entry:.6g}</code> → <code>{trade.exit:.6g}</code>\n"
            f"PnL <code>{trade.pnl:+.2f}</code> USDT ({trade.pnl_pct:+.2f}%)\n"
            f"Solde <code>{s['balance']:.2f}</code> · {_ts()}"
        )

    # ------------------------------------------------------------- cycle
    async def run_cycle(self) -> None:
        """Un passage complet : régime BTC -> stops/trailing -> nouvelles entrées."""
        btc = await self._fetch_btc_long()
        if btc is None or btc.empty:
            logger.warning("Cycle sauté : pas de données BTC")
            return

        bar_ts = btc.index[-1]
        if self._last_bar_ts is not None and bar_ts <= self._last_bar_ts:
            logger.info("Pas de nouvelle bougie 2h (%s)", bar_ts)
            return
        self._last_bar_ts = bar_ts

        bear, info = self.strategy.btc_bear_regime(btc)
        if self._regime_bear is not None and bear != self._regime_bear:
            await self._send(
                f"{header(TAG_CHASSEUR, '🐻 Régime BTC' if bear else '🌤 Régime BTC')}\n"
                f"BTC <code>{info['price']:.0f}</code> vs EMA200 "
                f"<code>{info['ema200d']:.0f}</code>\n"
                f"{'Bear ACTIF — chasse ouverte' if bear else 'Hors bear — veille'}\n"
                f"{_ts()}"
            )
        self._regime_bear = bear

        # Données de tous les coins de l'univers (1 req/coin)
        frames: dict[str, pd.DataFrame] = {}
        for sym in self.cfg.universe:
            df = await self._fetch_klines(sym, ALT_BARS)
            if df is not None and not df.empty:
                frames[sym] = df
            await asyncio.sleep(0.12)

        prices = {s: float(d["close"].iloc[-1]) for s, d in frames.items()}

        # 1. Funding sur les positions ouvertes
        self.portfolio.apply_funding()

        # 2. Stops / trailing sur la dernière bougie 2h clôturée
        for sym in list(self.portfolio.positions):
            pos = self.portfolio.positions[sym]
            df = frames.get(sym)
            if df is None:
                continue
            bar = df.iloc[-1]
            atr = float(self.strategy._atr(df, self.cfg.atr_len).iloc[-1])
            if float(bar["high"]) >= pos.stop:
                trade = self.portfolio.close_short(sym, pos.stop, "stop")
                await self._send(self._msg_close(trade))
            else:
                new_stop = self.strategy.trail_stop(pos.stop, float(bar["low"]), atr)
                if new_stop < pos.stop:
                    pos.stop = new_stop
                    self.portfolio.save()

        # 3. Nouvelles entrées si régime bear
        if bear and self.portfolio.can_open():
            signals: list[ShortSignal] = []
            for sym, df in frames.items():
                if sym in self.portfolio.positions:
                    continue
                sig = self.strategy.compute(sym, df)
                if sig is not None:
                    signals.append(sig)
            signals.sort(key=lambda s: s.mom7d)  # plus faible d'abord
            slots = self.cfg.max_positions - len(self.portfolio.positions)
            for sig in signals[:slots]:
                stop = self.strategy.initial_stop(sig.close, sig.atr)
                pos = self.portfolio.open_short(sig.symbol, sig.close, stop)
                await self._send(self._msg_open(pos, sig))

        eq = self.portfolio.equity(prices)
        logger.info(
            "Cycle %s | bear=%s | positions=%d | équité=%.2f USDT",
            bar_ts, bear, len(self.portfolio.positions), eq,
        )

    def startup_message(self) -> str:
        s = self.portfolio.stats()
        return (
            f"{header(TAG_CHASSEUR, f'<b>{BOT_CHASSEUR}</b> démarré')}\n"
            f"Shorts multi-alts si BTC bear strict · cycle 2h\n"
            f"Solde <code>{s['balance']:.2f}</code> · "
            f"pos {s['open']}/{self.cfg.max_positions} · "
            f"{len(self.cfg.universe)} alts\n"
            f"{_ts()}"
        )

    def daily_message(self, prices: dict[str, float] | None = None) -> str:
        s = self.portfolio.stats()
        pos_lines = "".join(
            f"  • <code>{p.symbol}</code> @ <code>{p.entry:.6g}</code>\n"
            for p in self.portfolio.positions.values()
        ) or "  <i>aucune</i>\n"
        regime = "bear ✓" if self._regime_bear else "veille"
        return (
            f"{header(TAG_CHASSEUR, f'📊 {BOT_CHASSEUR}')}\n"
            f"Solde <code>{s['balance']:.2f}</code> ({s['pnl_pct']:+.2f}%) · BTC {regime}\n"
            f"Positions :\n{pos_lines}"
            f"Trades {s['n']} · WR {s['winrate']:.0f}%\n"
            f"{_ts()}"
        )
