"""Alertes Telegram formatées."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from telegram import Bot
from telegram.constants import ParseMode

from config import AppConfig
from models.liquidity_zone import LiquidityZone, ZoneType
from utils.logger import setup_logger
from utils.resilience import retry_async
from utils.trade_bias import get_bias

logger = setup_logger(__name__)


def format_vol(v: float) -> str:
    if v >= 1_000_000:
        return f"{v / 1_000_000:.1f}M"
    if v >= 1_000:
        return f"{v / 1_000:.1f}K"
    return f"{v:.0f}"


class TelegramNotifier:
    def __init__(self, config: AppConfig) -> None:
        self.config = config
        self._bot: Optional[Bot] = None

    async def start(self) -> None:
        token = self.config.telegram.token.strip()
        if not token:
            raise ValueError("TELEGRAM_BOT_TOKEN manquant — configure .env")
        self._bot = Bot(token=token)
        logger.info("Telegram initialisé")

    @staticmethod
    def _ts() -> str:
        return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")

    async def send_raw(self, text: str) -> None:
        if not self._bot:
            raise RuntimeError("Telegram non démarré")
        chat_id = self.config.telegram.chat_id.strip()
        if not chat_id:
            raise ValueError(
                "TELEGRAM_CHAT_ID manquant — envoie /start au bot puis lance: python scripts/get_chat_id.py"
            )

        async def _send() -> None:
            await self._bot.send_message(
                chat_id=chat_id,
                text=text,
                parse_mode=ParseMode.HTML,
                disable_web_page_preview=True,
            )

        await retry_async(_send, label="telegram_send")

    def _bias_block(self, zone: LiquidityZone, *, is_sweep: bool) -> str:
        bias = get_bias(zone, is_sweep=is_sweep)
        return f"\n{bias.line}\n"

    async def notify_eqh(self, zone: LiquidityZone, *, chart: str = "") -> None:
        chart_line = f"Chart TV : <code>{chart}</code>\n" if chart else ""
        msg = (
            f"🔴 <b>EQH détecté</b> (equal highs — sommets)\n"
            f"{self._bias_block(zone, is_sweep=False)}"
            f"{chart_line}"
            f"Pair : <code>{zone.display_symbol}</code>\n"
            f"TF : <code>{zone.timeframe}</code>\n"
            f"Prix : <code>{zone.sweep_level:.4f}</code>\n"
            f"Zone : <code>{zone.bottom:.4f}</code> – <code>{zone.top:.4f}</code>\n"
            f"Vol : <code>{format_vol(zone.total_vol)}</code>\n"
            f"Score : <code>{zone.score}</code>\n"
            f"🕐 {self._ts()}"
        )
        await self.send_raw(msg)

    async def notify_eql(self, zone: LiquidityZone, *, chart: str = "") -> None:
        chart_line = f"Chart TV : <code>{chart}</code>\n" if chart else ""
        msg = (
            f"🟢 <b>EQL détecté</b> (equal lows — creux)\n"
            f"{self._bias_block(zone, is_sweep=False)}"
            f"{chart_line}"
            f"Pair : <code>{zone.display_symbol}</code>\n"
            f"TF : <code>{zone.timeframe}</code>\n"
            f"Prix : <code>{zone.sweep_level:.4f}</code>\n"
            f"Zone : <code>{zone.bottom:.4f}</code> – <code>{zone.top:.4f}</code>\n"
            f"Vol : <code>{format_vol(zone.total_vol)}</code>\n"
            f"Score : <code>{zone.score}</code>\n"
            f"🕐 {self._ts()}"
        )
        await self.send_raw(msg)

    async def notify_eqh_sweep(self, zone: LiquidityZone) -> None:
        msg = (
            f"⚠️ <b>EQH SWEEP</b>\n"
            f"{self._bias_block(zone, is_sweep=True)}"
            f"Pair : <code>{zone.display_symbol}</code>\n"
            f"TF : <code>{zone.timeframe}</code>\n"
            f"Niveau : <code>{zone.sweep_level:.4f}</code>\n"
            f"Liquidity taken above highs\n"
            f"Vol zone : <code>{format_vol(zone.total_vol)}</code>\n"
            f"🕐 {self._ts()}"
        )
        await self.send_raw(msg)

    async def notify_eql_sweep(self, zone: LiquidityZone) -> None:
        msg = (
            f"⚠️ <b>EQL SWEEP</b>\n"
            f"{self._bias_block(zone, is_sweep=True)}"
            f"Pair : <code>{zone.display_symbol}</code>\n"
            f"TF : <code>{zone.timeframe}</code>\n"
            f"Niveau : <code>{zone.sweep_level:.4f}</code>\n"
            f"Liquidity taken below lows\n"
            f"Vol zone : <code>{format_vol(zone.total_vol)}</code>\n"
            f"🕐 {self._ts()}"
        )
        await self.send_raw(msg)

    async def notify_zone(self, zone: LiquidityZone, *, chart: str = "") -> None:
        if zone.zone_type == ZoneType.EQH:
            await self.notify_eqh(zone, chart=chart)
        else:
            await self.notify_eql(zone, chart=chart)

    async def notify_sweep(self, zone: LiquidityZone, sweep_type: str) -> None:
        if sweep_type == "EQH_SWEEP":
            await self.notify_eqh_sweep(zone)
        else:
            await self.notify_eql_sweep(zone)
