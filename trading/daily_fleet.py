"""Rapport quotidien unique — Tendance + Chasseur + Analyst."""

from __future__ import annotations

from datetime import datetime, timezone

from trading import notifications as tendance_notif
from trading.analyst_notifications import section_daily as section_analyst
from trading.bot_ids import (
    BOT_ANALYST,
    BOT_CHASSEUR,
    BOT_TENDANCE,
    TAG_ANALYST,
    TAG_CHASSEUR,
    TAG_TENDANCE,
)
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
    tendance: PaperTrader,
    chasseur_engine,
    analyst_engine,
    aave_price: float,
) -> str:
    eq_t = tendance.state.equity(aave_price)
    eq_s = chasseur_engine.portfolio.stats()["balance"]
    eq_a = analyst_engine.trader.state.equity(aave_price)
    total = eq_t + eq_s + eq_a
    start = (
        tendance.state.start_balance
        + chasseur_engine.portfolio.start_balance
        + analyst_engine.trader.state.start_balance
    )
    return (
        f"📊 <b>Rapport quotidien — 3 bots</b>\n"
        f"Paper ≈ <code>{total:.2f}</code> USDT "
        f"({_pct((total / start - 1) * 100 if start else 0)})\n"
        f"────────────────\n"
        f"{tendance_notif.section_daily(tendance, aave_price)}\n"
        f"────────────────\n"
        f"{section_chasseur(chasseur_engine)}\n"
        f"────────────────\n"
        f"{section_analyst(analyst_engine)}\n"
        f"────────────────\n"
        f"AAVE <code>{aave_price:.3f}</code> · {_ts()}"
    )


def msg_fleet_startup(source: str) -> str:
    return (
        f"🤖 <b>Flotte paper démarrée</b>\n"
        f"• <b>[{TAG_TENDANCE}]</b> {BOT_TENDANCE} — EMA flip 30m + filtres\n"
        f"• <b>[{TAG_CHASSEUR}]</b> {BOT_CHASSEUR} — shorts alts si BTC bear\n"
        f"• <b>[{TAG_ANALYST}]</b> {BOT_ANALYST} — motifs AAVE → paper + apprentissage\n"
        f"1 rapport/jour à 07:00 UTC · source AAVE : <code>{source}</code>\n"
        f"{_ts()}"
    )
