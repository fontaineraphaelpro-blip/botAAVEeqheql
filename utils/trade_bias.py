"""Biais LONG / SHORT selon logique SMC (liquidité EQH / EQL)."""

from __future__ import annotations

from dataclasses import dataclass

from models.liquidity_zone import LiquidityZone, ZoneType


@dataclass(frozen=True)
class TradeBias:
    direction: str
    emoji: str
    action: str
    reason: str
    is_entry: bool

    @property
    def line(self) -> str:
        return f"{self.emoji} <b>{self.action}</b>\n<i>{self.reason}</i>"


def get_bias(zone: LiquidityZone, *, is_sweep: bool) -> TradeBias:
    """
    SMC :
    - EQH = liquidité au-dessus → après sweep, recherche de SHORT
    - EQL = liquidité en dessous → après sweep, recherche de LONG
    """
    if zone.zone_type == ZoneType.EQH:
        if is_sweep:
            return TradeBias(
                direction="SHORT",
                emoji="📉",
                action="SIGNAL SHORT",
                reason="EQH sweepé — liquidité des highs prise, biais baissier",
                is_entry=True,
            )
        return TradeBias(
            direction="SHORT",
            emoji="📉",
            action="BIAIS SHORT",
            reason="EQH en formation — liquidité au-dessus, viser short après sweep",
            is_entry=False,
        )

    if is_sweep:
        return TradeBias(
            direction="LONG",
            emoji="📈",
            action="SIGNAL LONG",
            reason="EQL sweepé — liquidité des lows prise, biais haussier",
            is_entry=True,
        )
    return TradeBias(
        direction="LONG",
        emoji="📈",
        action="BIAIS LONG",
        reason="EQL en formation — liquidité en dessous, viser long après sweep",
        is_entry=False,
    )
