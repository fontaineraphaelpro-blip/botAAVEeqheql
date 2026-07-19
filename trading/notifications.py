"""Messages Telegram — AAVE Tendance (EMA flip 30m + filtres)."""

from __future__ import annotations

from datetime import datetime, timezone

from trading.bot_ids import BOT_TENDANCE, TAG_TENDANCE, header
from trading.paper import ClosedTrade, PaperTrader, Position
from trading.strategy import Signal


def _ts() -> str:
    return datetime.now(timezone.utc).strftime("%H:%M UTC")


def _pct(v: float) -> str:
    return f"{v:+.2f}%"


def msg_startup(trader: PaperTrader, cfg, source: str) -> str:
    s = trader.stats()
    pos = trader.state.position
    pos_line = (
        f"Pos : <b>{pos.side_label}</b> @ <code>{pos.entry:.3f}</code>"
        if pos
        else "Pos : flat"
    )
    return (
        f"{header(TAG_TENDANCE, f'<b>{BOT_TENDANCE}</b> démarré')}\n"
        f"EMA{cfg.ema_len} flip {cfg.signal_tf_min}m + tendance 4h + ER≥{cfg.er_min}\n"
        f"Stop {cfg.stop_atr}×ATR · trail {cfg.trail_atr}×ATR\n"
        f"Solde : <code>{s['balance']:.2f}</code> USDT · {pos_line}\n"
        f"<i>{source} · {_ts()}</i>"
    )


def msg_open(pos: Position, sig: Signal, balance: float) -> str:
    emoji = "🟢" if pos.side == 1 else "🔴"
    risk = abs(pos.entry - pos.stop) * pos.qty
    htf = "bull" if sig.htf_bull else "bear" if sig.htf_bear else "flat"
    return (
        f"{header(TAG_TENDANCE, f'{emoji} <b>{pos.side_label}</b> @ <code>{pos.entry:.3f}</code>')}\n"
        f"Stop <code>{pos.stop:.3f}</code> (risque ≈ {risk:.1f}$) · "
        f"EMA <code>{sig.ema:.3f}</code> · HTF {htf} · ER {sig.er:.2f}\n"
        f"Taille : <code>{pos.qty:.3f}</code> AAVE · solde <code>{balance:.2f}</code>\n"
        f"{_ts()}"
    )


def msg_close(trade: ClosedTrade, trader: PaperTrader) -> str:
    emoji = "✅" if trade.pnl > 0 else "🛑"
    reason_txt = {
        "stop": "stop",
        "trailing": "trailing",
        "flip": "flip",
        "manual": "manuel",
    }.get(trade.reason, trade.reason)
    s = trader.stats()
    return (
        f"{header(TAG_TENDANCE, f'{emoji} Close {trade.side} ({reason_txt})')}\n"
        f"<code>{trade.entry:.3f}</code> → <code>{trade.exit:.3f}</code>\n"
        f"PnL : <code>{trade.pnl:+.2f}</code> USDT ({_pct(trade.pnl_pct)})\n"
        f"Solde : <code>{s['balance']:.2f}</code> · {_ts()}"
    )


def msg_breakeven(pos: Position) -> str:
    return (
        f"{header(TAG_TENDANCE, f'🔒 {pos.side_label} protégé')}\n"
        f"Stop <code>{pos.stop:.3f}</code> ≥ entrée <code>{pos.entry:.3f}</code>\n"
        f"{_ts()}"
    )


def msg_daily(trader: PaperTrader, price: float) -> str:
    s = trader.stats()
    equity = trader.state.equity(price)
    pos = trader.state.position
    if pos:
        pos_line = (
            f"Pos : <b>{pos.side_label}</b> @ <code>{pos.entry:.3f}</code> "
            f"(latent {pos.unrealized(price):+.2f})"
        )
    else:
        pos_line = "Pos : flat"
    return (
        f"{header(TAG_TENDANCE, f'📊 {BOT_TENDANCE}')}\n"
        f"Équité : <code>{equity:.2f}</code> ({_pct(s['pnl_pct'])})\n"
        f"{pos_line}\n"
        f"Trades : {s['n']} · WR {s['winrate']:.0f}% · AAVE <code>{price:.3f}</code>\n"
        f"{_ts()}"
    )


def section_daily(trader: PaperTrader, price: float) -> str:
    s = trader.stats()
    equity = trader.state.equity(price)
    pos = trader.state.position
    if pos:
        pos_txt = f"{pos.side_label} @{pos.entry:.3f} ({pos.unrealized(price):+.1f})"
    else:
        pos_txt = "flat"
    return (
        f"<b>[{TAG_TENDANCE}]</b> {BOT_TENDANCE}\n"
        f"Équité <code>{equity:.2f}</code> ({_pct(s['pnl_pct'])}) · "
        f"{s['n']}t WR {s['winrate']:.0f}% · {pos_txt}"
    )
