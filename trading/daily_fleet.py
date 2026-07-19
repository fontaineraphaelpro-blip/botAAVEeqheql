"""Rapport quotidien unique — les 3 bots."""

from __future__ import annotations

from datetime import datetime, timezone

from trading import clean_sticky_notifications as cs_notif
from trading import notifications as tendance_notif
from trading.bot_ids import (
    BOT_CHASSEUR,
    BOT_COULEUR,
    BOT_TENDANCE,
    TAG_CHASSEUR,
    TAG_COULEUR,
    TAG_TENDANCE,
)
from trading.clean_sticky_paper import CleanStickyPaper
from trading.paper import PaperTrader


def _ts() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")


def _pct(v: float) -> str:
    return f"{v:+.2f}%"


def section_chasseur(engine) -> str:
    """Bloc Chasseur Shorts (ShortHunterEngine)."""
    s = engine.portfolio.stats()
    n_pos = len(engine.portfolio.positions)
    regime = "bear ✓" if engine._regime_bear else "veille"
    if engine.portfolio.positions:
        pos_txt = ", ".join(
            f"{p.symbol.replace('USDT', '')}"
            for p in list(engine.portfolio.positions.values())[:5]
        )
        if n_pos > 5:
            pos_txt += f" +{n_pos - 5}"
    else:
        pos_txt = "flat"
    return (
        f"<b>[{TAG_CHASSEUR}]</b> {BOT_CHASSEUR}\n"
        f"Équité <code>{s['balance']:.2f}</code> ({_pct(s['pnl_pct'])}) · "
        f"{s['n']}t WR {s['winrate']:.0f}% · BTC {regime} · {pos_txt}"
    )


def msg_unified_daily(
    *,
    couleur: CleanStickyPaper,
    tendance: PaperTrader,
    chasseur_engine,
    aave_price: float,
) -> str:
    eq_c = couleur.state.equity(aave_price)
    eq_t = tendance.state.equity(aave_price)
    eq_s = chasseur_engine.portfolio.stats()["balance"]
    total = eq_c + eq_t + eq_s
    start = (
        couleur.state.start_balance
        + tendance.state.start_balance
        + chasseur_engine.portfolio.start_balance
    )
    return (
        f"📊 <b>Rapport quotidien — 3 bots</b>\n"
        f"Total ≈ <code>{total:.2f}</code> USDT "
        f"({_pct((total / start - 1) * 100 if start else 0)})\n"
        f"────────────────\n"
        f"{cs_notif.section_daily(couleur, aave_price)}\n"
        f"────────────────\n"
        f"{tendance_notif.section_daily(tendance, aave_price)}\n"
        f"────────────────\n"
        f"{section_chasseur(chasseur_engine)}\n"
        f"────────────────\n"
        f"AAVE <code>{aave_price:.3f}</code> · {_ts()}"
    )


def msg_fleet_startup(source: str) -> str:
    return (
        f"🤖 <b>Flotte paper démarrée</b>\n"
        f"• <b>[{TAG_COULEUR}]</b> {BOT_COULEUR} — EMA couleurs 5m x10\n"
        f"• <b>[{TAG_TENDANCE}]</b> {BOT_TENDANCE} — EMA flip 30m + filtres\n"
        f"• <b>[{TAG_CHASSEUR}]</b> {BOT_CHASSEUR} — shorts alts si BTC bear\n"
        f"1 rapport/jour à 07:00 UTC · source AAVE : <code>{source}</code>\n"
        f"{_ts()}"
    )
