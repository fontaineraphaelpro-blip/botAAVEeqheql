"""Messages Telegram — bot AAVE Clean Sticky."""

from __future__ import annotations

from datetime import datetime, timezone

from trading.clean_sticky_paper import CleanStickyPaper, StickyPosition, StickyTrade
from trading.clean_sticky_strategy import ColorState, StickySignal


def _ts() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")


def _pct(v: float) -> str:
    return f"{v:+.2f}%"


def msg_startup(trader: CleanStickyPaper, cfg, source: str) -> str:
    s = trader.stats()
    pos = trader.state.position
    pos_line = (
        f"Position : <b>{pos.side_label}</b> @ <code>{pos.entry:.3f}</code> "
        f"(x{pos.leverage:.0f})\n"
        if pos
        else "Position : <i>aucune</i>\n"
    )
    return (
        f"🟢⚪🔴 <b>Clean Sticky AAVE démarré</b>\n"
        f"Solde : <code>{s['balance']:.2f} USDT</code> "
        f"(départ <code>{trader.state.start_balance:.2f}</code>)\n"
        f"{pos_line}"
        f"Règles : EMA{cfg.ema_fast}/{cfg.ema_slow} — "
        f"vert=LONG, gris=ferme, rouge=SHORT\n"
        f"TF : <code>{cfg.signal_tf_min}min</code> | "
        f"Levier <code>x{cfg.leverage:.0f}</code> | marge 100%\n"
        f"Source : <code>{source}</code>\n"
        f"🕐 {_ts()}"
    )


def msg_open(pos: StickyPosition, sig: StickySignal, balance: float) -> str:
    emoji = "🟢" if pos.side == 1 else "🔴"
    return (
        f"{emoji} <b>OUVERTURE {pos.side_label}</b> Clean Sticky\n"
        f"Couleur : {sig.color.emoji} {sig.color.label} "
        f"(avant {sig.prev_color.emoji})\n"
        f"Entrée : <code>{pos.entry:.3f}</code>\n"
        f"Taille : <code>{pos.qty:.4f} AAVE</code> "
        f"(notionnel <code>{pos.notional:.2f}</code> USDT)\n"
        f"Marge : <code>{pos.margin:.2f}</code> | levier <code>x{pos.leverage:.0f}</code>\n"
        f"EMA{sig.ema_fast:.3f} / EMA{sig.ema_slow:.3f}\n"
        f"Solde : <code>{balance:.2f} USDT</code>\n"
        f"🕐 {_ts()}"
    )


def msg_close(trade: StickyTrade, trader: CleanStickyPaper, color: ColorState) -> str:
    win = trade.pnl > 0
    emoji = "✅" if win else "🛑"
    reason_txt = {
        "gray": "Passage au gris",
        "flip": "Retournement de couleur",
        "liquidation": "Liquidation (levier)",
        "manual": "Fermeture manuelle",
    }.get(trade.reason, trade.reason)
    s = trader.stats()
    return (
        f"{emoji} <b>FERMETURE {trade.side}</b> — {reason_txt}\n"
        f"Couleur : {color.emoji} {color.label}\n"
        f"Entrée : <code>{trade.entry:.3f}</code> → "
        f"Sortie : <code>{trade.exit:.3f}</code>\n"
        f"PnL : <code>{trade.pnl:+.2f} USDT</code> "
        f"({_pct(trade.pnl_pct)} sur marge, x{trade.leverage:.0f})\n"
        f"Solde : <code>{s['balance']:.2f} USDT</code> "
        f"({_pct(s['pnl_pct'])} depuis le départ)\n"
        f"Stats : {s['n']} trades, {s['winrate']:.0f}% gagnants\n"
        f"🕐 {_ts()}"
    )


def msg_daily(trader: CleanStickyPaper, price: float) -> str:
    s = trader.stats()
    equity = trader.state.equity(price)
    pos = trader.state.position
    if pos:
        upnl = pos.unrealized(price)
        pos_line = (
            f"Position : <b>{pos.side_label}</b> @ <code>{pos.entry:.3f}</code>, "
            f"latent <code>{upnl:+.2f}</code>, liq ≈ "
            f"<code>{pos.liq_price(trader.cfg.liq_margin_pct):.3f}</code>\n"
        )
    else:
        pos_line = "Position : <i>aucune</i>\n"
    return (
        f"📊 <b>Rapport — Clean Sticky AAVE</b>\n"
        f"Équité : <code>{equity:.2f} USDT</code> "
        f"({_pct((equity / trader.state.start_balance - 1) * 100)} )\n"
        f"Solde : <code>{s['balance']:.2f} USDT</code>\n"
        f"{pos_line}"
        f"Trades : <code>{s['n']}</code>, "
        f"<code>{s['winrate']:.0f}%</code> gagnants\n"
        f"Prix AAVE : <code>{price:.3f}</code>\n"
        f"🕐 {_ts()}"
    )
