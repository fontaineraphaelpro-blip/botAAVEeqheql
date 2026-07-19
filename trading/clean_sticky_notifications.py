"""Messages Telegram — AAVE Couleur (EMA vert / gris / rouge)."""

from __future__ import annotations

from datetime import datetime, timezone

from trading.bot_ids import BOT_COULEUR, TAG_COULEUR, header
from trading.clean_sticky_paper import CleanStickyPaper, StickyPosition, StickyTrade
from trading.clean_sticky_strategy import ColorState, StickySignal


def _ts() -> str:
    return datetime.now(timezone.utc).strftime("%H:%M UTC")


def _pct(v: float) -> str:
    return f"{v:+.2f}%"


def msg_startup(trader: CleanStickyPaper, cfg, source: str) -> str:
    s = trader.stats()
    pos = trader.state.position
    pos_line = (
        f"Pos : <b>{pos.side_label}</b> @ <code>{pos.entry:.3f}</code>"
        if pos
        else "Pos : flat"
    )
    return (
        f"{header(TAG_COULEUR, f'<b>{BOT_COULEUR}</b> démarré')}\n"
        f"Vert=LONG · gris=ferme · rouge=SHORT\n"
        f"TF <code>{cfg.signal_tf_min}m</code> · EMA{cfg.ema_fast}/{cfg.ema_slow} · "
        f"x<code>{cfg.leverage:.0f}</code>\n"
        f"Solde : <code>{s['balance']:.2f}</code> USDT · {pos_line}\n"
        f"<i>{source} · {_ts()}</i>"
    )


def msg_open(pos: StickyPosition, sig: StickySignal, balance: float) -> str:
    emoji = "🟢" if pos.side == 1 else "🔴"
    return (
        f"{header(TAG_COULEUR, f'{emoji} <b>{pos.side_label}</b> @ <code>{pos.entry:.3f}</code>')}\n"
        f"Couleur : {sig.color.emoji} {sig.color.label} · x{pos.leverage:.0f}\n"
        f"Notionnel : <code>{pos.notional:.0f}</code> USDT ({pos.qty:.2f} AAVE)\n"
        f"Solde : <code>{balance:.2f}</code> · {_ts()}"
    )


def msg_close(trade: StickyTrade, trader: CleanStickyPaper, color: ColorState) -> str:
    emoji = "✅" if trade.pnl > 0 else "🛑"
    reason_txt = {
        "gray": "gris",
        "flip": "flip",
        "liquidation": "liq",
        "manual": "manuel",
    }.get(trade.reason, trade.reason)
    s = trader.stats()
    return (
        f"{header(TAG_COULEUR, f'{emoji} Close {trade.side} ({reason_txt})')}\n"
        f"<code>{trade.entry:.3f}</code> → <code>{trade.exit:.3f}</code>\n"
        f"PnL : <code>{trade.pnl:+.2f}</code> USDT ({_pct(trade.pnl_pct)}) · {color.emoji}\n"
        f"Solde : <code>{s['balance']:.2f}</code> · {_ts()}"
    )


def msg_daily(trader: CleanStickyPaper, price: float) -> str:
    """Rapport solo (si bot lancé seul)."""
    s = trader.stats()
    equity = trader.state.equity(price)
    pos = trader.state.position
    pos_line = (
        f"Pos : <b>{pos.side_label}</b> @ <code>{pos.entry:.3f}</code> ({pos.unrealized(price):+.2f})"
        if pos
        else "Pos : flat"
    )
    return (
        f"{header(TAG_COULEUR, f'📊 {BOT_COULEUR}')}\n"
        f"Équité : <code>{equity:.2f}</code> ({_pct(s['pnl_pct'])})\n"
        f"{pos_line}\n"
        f"Trades : {s['n']} · WR {s['winrate']:.0f}% · AAVE <code>{price:.3f}</code>\n"
        f"{_ts()}"
    )


def section_daily(trader: CleanStickyPaper, price: float) -> str:
    """Bloc pour le rapport unifié des 3 bots."""
    s = trader.stats()
    equity = trader.state.equity(price)
    pos = trader.state.position
    if pos:
        pos_txt = f"{pos.side_label} @{pos.entry:.3f} ({pos.unrealized(price):+.1f})"
    else:
        pos_txt = "flat"
    return (
        f"<b>[{TAG_COULEUR}]</b> {BOT_COULEUR}\n"
        f"Équité <code>{equity:.2f}</code> ({_pct(s['pnl_pct'])}) · "
        f"{s['n']}t WR {s['winrate']:.0f}% · {pos_txt}"
    )
