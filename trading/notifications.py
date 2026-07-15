"""Messages Telegram du paper trader."""

from __future__ import annotations

from datetime import datetime, timezone

from trading.paper import ClosedTrade, PaperTrader, Position
from trading.strategy import Signal


def _ts() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")


def _pct(v: float) -> str:
    return f"{v:+.2f}%"


def msg_startup(trader: PaperTrader, cfg, source: str) -> str:
    s = trader.stats()
    pos = trader.state.position
    pos_line = (
        f"Position en cours : <b>{pos.side_label}</b> @ <code>{pos.entry:.3f}</code>\n"
        if pos else "Position : <i>aucune</i>\n"
    )
    return (
        f"🤖 <b>Paper Trader AAVE/USDT démarré</b>\n"
        f"Solde : <code>{s['balance']:.2f} USDT</code> "
        f"(départ <code>{trader.state.start_balance:.2f}</code>)\n"
        f"{pos_line}"
        f"Stratégie : EMA{cfg.ema_len} flip {cfg.signal_tf_min}min "
        f"+ tendance 4h + ER≥{cfg.er_min}\n"
        f"Stop : {cfg.stop_atr}×ATR initial, trailing {cfg.trail_atr}×ATR\n"
        f"Source : <code>{source}</code>\n"
        f"🕐 {_ts()}"
    )


def msg_open(pos: Position, sig: Signal, balance: float) -> str:
    emoji = "🟢" if pos.side == 1 else "🔴"
    risk = abs(pos.entry - pos.stop) * pos.qty
    return (
        f"{emoji} <b>OUVERTURE {pos.side_label}</b> AAVE/USDT\n"
        f"Entrée : <code>{pos.entry:.3f}</code>\n"
        f"Stop : <code>{pos.stop:.3f}</code> "
        f"(risque ≈ <code>{risk:.2f} USDT</code>)\n"
        f"Taille : <code>{pos.qty:.4f} AAVE</code> "
        f"(<code>{pos.qty * pos.entry:.2f} USDT</code>)\n"
        f"Contexte : prix {'au-dessus' if pos.side == 1 else 'en dessous'} de l'EMA "
        f"(<code>{sig.ema:.3f}</code>), tendance 4h "
        f"{'haussière' if sig.htf_bull else 'baissière' if sig.htf_bear else 'neutre'}, "
        f"ER <code>{sig.er:.2f}</code>\n"
        f"Solde : <code>{balance:.2f} USDT</code>\n"
        f"🕐 {_ts()}"
    )


def msg_close(trade: ClosedTrade, trader: PaperTrader) -> str:
    win = trade.pnl > 0
    emoji = "✅" if win else "🛑"
    reason_txt = {
        "stop": "Stop touché",
        "trailing": "Trailing stop touché",
        "flip": "Signal inverse",
        "manual": "Fermeture manuelle",
    }.get(trade.reason, trade.reason)
    s = trader.stats()
    return (
        f"{emoji} <b>FERMETURE {trade.side}</b> — {reason_txt}\n"
        f"Entrée : <code>{trade.entry:.3f}</code> → Sortie : <code>{trade.exit:.3f}</code>\n"
        f"PnL : <code>{trade.pnl:+.2f} USDT</code> ({_pct(trade.pnl_pct)})\n"
        f"Solde : <code>{s['balance']:.2f} USDT</code> "
        f"({_pct(s['pnl_pct'])} depuis le départ)\n"
        f"Stats : {s['n']} trades, {s['winrate']:.0f}% gagnants\n"
        f"🕐 {_ts()}"
    )


def msg_breakeven(pos: Position) -> str:
    return (
        f"🔒 <b>Position {pos.side_label} protégée</b>\n"
        f"Le trailing stop (<code>{pos.stop:.3f}</code>) a dépassé l'entrée "
        f"(<code>{pos.entry:.3f}</code>) — trade sans risque.\n"
        f"🕐 {_ts()}"
    )


def msg_daily(trader: PaperTrader, price: float) -> str:
    s = trader.stats()
    equity = trader.state.equity(price)
    pos = trader.state.position
    if pos:
        upnl = pos.unrealized(price)
        pos_line = (
            f"Position : <b>{pos.side_label}</b> @ <code>{pos.entry:.3f}</code>, "
            f"latent <code>{upnl:+.2f} USDT</code>, stop <code>{pos.stop:.3f}</code>\n"
        )
    else:
        pos_line = "Position : <i>aucune</i>\n"
    return (
        f"📊 <b>Rapport quotidien — Paper Trader AAVE</b>\n"
        f"Équité : <code>{equity:.2f} USDT</code> "
        f"({_pct((equity / trader.state.start_balance - 1) * 100)} depuis le départ)\n"
        f"Solde réalisé : <code>{s['balance']:.2f} USDT</code>\n"
        f"{pos_line}"
        f"Trades : <code>{s['n']}</code> au total, "
        f"<code>{s['winrate']:.0f}%</code> gagnants\n"
        f"Prix AAVE : <code>{price:.3f}</code>\n"
        f"🕐 {_ts()}"
    )
