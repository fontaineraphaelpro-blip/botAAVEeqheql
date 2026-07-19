"""Messages Telegram — bot AAVE Clean Sticky."""

from __future__ import annotations

from datetime import datetime, timezone

from trading.clean_sticky_paper import CleanStickyPaper, StickyPosition, StickyTrade
from trading.clean_sticky_strategy import ColorState, StickySignal


def _ts() -> str:
    return datetime.now(timezone.utc).strftime("%H:%M UTC")


def _pct(v: float) -> str:
    return f"{v:+.2f}%"


def msg_startup(trader: CleanStickyPaper, cfg, source: str) -> str:
    s = trader.stats()
    pos = trader.state.position
    if pos:
        pos_line = f"Position : <b>{pos.side_label}</b> @ <code>{pos.entry:.3f}</code>"
    else:
        pos_line = "Position : flat"
    return (
        f"<b>Clean Sticky AAVE</b> démarré\n"
        f"TF <code>{cfg.signal_tf_min}m</code> · EMA{cfg.ema_fast}/{cfg.ema_slow} · "
        f"x<code>{cfg.leverage:.0f}</code>\n"
        f"Solde : <code>{s['balance']:.2f}</code> USDT\n"
        f"{pos_line}\n"
        f"<i>{source} · {_ts()}</i>"
    )


def msg_open(pos: StickyPosition, sig: StickySignal, balance: float) -> str:
    emoji = "🟢" if pos.side == 1 else "🔴"
    return (
        f"{emoji} <b>{pos.side_label}</b> AAVE @ <code>{pos.entry:.3f}</code>\n"
        f"Couleur : {sig.color.emoji} {sig.color.label} · levier x{pos.leverage:.0f}\n"
        f"Notionnel : <code>{pos.notional:.0f}</code> USDT "
        f"({pos.qty:.2f} AAVE)\n"
        f"Solde : <code>{balance:.2f}</code> · {_ts()}"
    )


def msg_close(trade: StickyTrade, trader: CleanStickyPaper, color: ColorState) -> str:
    win = trade.pnl > 0
    emoji = "✅" if win else "🛑"
    reason_txt = {
        "gray": "gris",
        "flip": "flip couleur",
        "liquidation": "liquidation",
        "manual": "manuel",
    }.get(trade.reason, trade.reason)
    s = trader.stats()
    return (
        f"{emoji} <b>Close {trade.side}</b> ({reason_txt})\n"
        f"<code>{trade.entry:.3f}</code> → <code>{trade.exit:.3f}</code>\n"
        f"PnL : <code>{trade.pnl:+.2f}</code> USDT ({_pct(trade.pnl_pct)}) · "
        f"{color.emoji}\n"
        f"Solde : <code>{s['balance']:.2f}</code> · {_ts()}"
    )


def msg_daily(trader: CleanStickyPaper, price: float) -> str:
    s = trader.stats()
    equity = trader.state.equity(price)
    pos = trader.state.position
    if pos:
        upnl = pos.unrealized(price)
        pos_line = (
            f"Pos : <b>{pos.side_label}</b> @ <code>{pos.entry:.3f}</code> "
            f"(latent {upnl:+.2f})"
        )
    else:
        pos_line = "Pos : flat"
    return (
        f"📊 <b>Clean Sticky</b> — rapport\n"
        f"Équité : <code>{equity:.2f}</code> ({_pct(s['pnl_pct'])})\n"
        f"{pos_line}\n"
        f"Trades : {s['n']} · winrate {s['winrate']:.0f}% · "
        f"AAVE <code>{price:.3f}</code>\n"
        f"{_ts()}"
    )
