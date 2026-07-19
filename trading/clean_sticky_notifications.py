"""Messages Telegram — bot AAVE Clean Sticky (concis)."""

from __future__ import annotations

from trading.clean_sticky_paper import CleanStickyPaper, StickyPosition, StickyTrade
from trading.clean_sticky_strategy import ColorState, StickySignal


def _pct(v: float) -> str:
    return f"{v:+.2f}%"


def msg_startup(trader: CleanStickyPaper, cfg, source: str) -> str:
    pos = trader.state.position
    pos_txt = f"{pos.side_label} @{pos.entry:.2f}" if pos else "flat"
    return (
        f"Clean Sticky ON — TF{cfg.signal_tf_min}m x{cfg.leverage:.0f} | "
        f"{trader.state.balance:.0f} USDT | {pos_txt}"
    )


def msg_open(pos: StickyPosition, sig: StickySignal, balance: float) -> str:
    emoji = "🟢" if pos.side == 1 else "🔴"
    return (
        f"{emoji} <b>{pos.side_label}</b> @{pos.entry:.3f} "
        f"x{pos.leverage:.0f} | {sig.color.emoji}"
    )


def msg_close(trade: StickyTrade, trader: CleanStickyPaper, color: ColorState) -> str:
    emoji = "✅" if trade.pnl > 0 else "🛑"
    return (
        f"{emoji} Close {trade.side} {_pct(trade.pnl_pct)} "
        f"({trade.pnl:+.1f}$) | solde {trader.state.balance:.0f} | {color.emoji}"
    )


def msg_daily(trader: CleanStickyPaper, price: float) -> str:
    s = trader.stats()
    eq = trader.state.equity(price)
    pos = trader.state.position
    pos_txt = f"{pos.side_label}@{pos.entry:.2f}" if pos else "flat"
    return (
        f"📊 Clean Sticky — {eq:.0f}$ ({_pct(s['pnl_pct'])}) | "
        f"{s['n']}t {s['winrate']:.0f}% | {pos_txt}"
    )
