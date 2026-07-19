"""Paper trading Clean Sticky — levier, marge 100 %, liquidation."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from config import CleanStickyConfig
from utils.logger import setup_logger

logger = setup_logger(__name__)


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


@dataclass
class StickyPosition:
    side: int
    qty: float
    entry: float
    margin: float
    notional: float
    leverage: float
    entry_fee: float
    opened_at: str
    color_at_entry: str

    @property
    def side_label(self) -> str:
        return "LONG" if self.side == 1 else "SHORT"

    def unrealized(self, price: float) -> float:
        return self.side * self.qty * (price - self.entry)

    def liq_price(self, liq_margin_pct: float) -> float:
        """Prix de liquidation paper (perte latente = X % de la marge)."""
        max_loss = self.margin * (liq_margin_pct / 100.0)
        move = max_loss / self.qty if self.qty else 0.0
        return self.entry - move if self.side == 1 else self.entry + move


@dataclass
class StickyTrade:
    side: str
    qty: float
    entry: float
    exit: float
    pnl: float
    pnl_pct: float
    reason: str
    leverage: float
    opened_at: str
    closed_at: str


@dataclass
class StickyState:
    balance: float
    start_balance: float
    position: StickyPosition | None = None
    trades: list[StickyTrade] = field(default_factory=list)

    @property
    def total_pnl(self) -> float:
        return self.balance - self.start_balance

    def equity(self, price: float) -> float:
        if self.position is None:
            return self.balance
        return self.balance + self.position.unrealized(price)


class CleanStickyPaper:
    def __init__(self, cfg: CleanStickyConfig) -> None:
        self.cfg = cfg
        self.state_path = Path(cfg.state_file)
        self.state = self._load()

    def _load(self) -> StickyState:
        if self.state_path.exists():
            try:
                raw = json.loads(self.state_path.read_text(encoding="utf-8"))
                pos = StickyPosition(**raw["position"]) if raw.get("position") else None
                trades = [StickyTrade(**t) for t in raw.get("trades", [])]
                state = StickyState(
                    balance=raw["balance"],
                    start_balance=raw.get("start_balance", self.cfg.start_balance),
                    position=pos,
                    trades=trades,
                )
                logger.info(
                    "État Clean Sticky rechargé — solde %.2f, %d trades, pos: %s",
                    state.balance,
                    len(trades),
                    pos.side_label if pos else "aucune",
                )
                return state
            except Exception as exc:
                logger.error("État Clean Sticky illisible (%s) — reset", exc)
        return StickyState(
            balance=self.cfg.start_balance,
            start_balance=self.cfg.start_balance,
        )

    def save(self) -> None:
        data = {
            "balance": self.state.balance,
            "start_balance": self.state.start_balance,
            "position": asdict(self.state.position) if self.state.position else None,
            "trades": [asdict(t) for t in self.state.trades],
            "updated_at": _now_iso(),
        }
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.state_path.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
        tmp.replace(self.state_path)

    @property
    def in_position(self) -> bool:
        return self.state.position is not None

    def _cost_rate(self) -> float:
        return (self.cfg.fee_pct + self.cfg.slippage_pct) / 100.0

    def open(self, side: int, price: float, color_label: str) -> StickyPosition:
        if self.state.position is not None:
            raise RuntimeError("Position déjà ouverte")
        if self.state.balance <= 0:
            raise RuntimeError("Solde insuffisant")

        margin = self.state.balance * self.cfg.position_pct / 100.0
        notional = margin * self.cfg.leverage
        qty = notional / price
        fee = notional * self._cost_rate()
        # Frais prélevés sur le solde (marge)
        self.state.balance -= fee

        pos = StickyPosition(
            side=side,
            qty=qty,
            entry=price,
            margin=margin,
            notional=notional,
            leverage=self.cfg.leverage,
            entry_fee=fee,
            opened_at=_now_iso(),
            color_at_entry=color_label,
        )
        self.state.position = pos
        self.save()
        logger.info(
            "OPEN %s %.4f AAVE @ %.3f | marge %.2f | notionnel %.2f (x%.0f)",
            pos.side_label,
            qty,
            price,
            margin,
            notional,
            self.cfg.leverage,
        )
        return pos

    def close(self, price: float, reason: str) -> StickyTrade:
        pos = self.state.position
        if pos is None:
            raise RuntimeError("Aucune position à fermer")

        exit_fee = pos.qty * price * self._cost_rate()
        gross = pos.side * pos.qty * (price - pos.entry)
        pnl = gross - exit_fee
        # Remet la marge + PnL (les frais d'entrée ont déjà été débités)
        self.state.balance += pnl

        trade = StickyTrade(
            side=pos.side_label,
            qty=pos.qty,
            entry=pos.entry,
            exit=price,
            pnl=pnl,
            pnl_pct=(pnl / pos.margin * 100.0) if pos.margin else 0.0,
            reason=reason,
            leverage=pos.leverage,
            opened_at=pos.opened_at,
            closed_at=_now_iso(),
        )
        self.state.trades.append(trade)
        self.state.position = None
        self.save()
        logger.info(
            "CLOSE %s @ %.3f | PnL %+.2f (%+.1f%% marge) | solde %.2f | %s",
            trade.side,
            price,
            pnl,
            trade.pnl_pct,
            self.state.balance,
            reason,
        )
        return trade

    def liquidation_hit(self, bar_low: float, bar_high: float) -> float | None:
        """Retourne le prix de liq si touché sur la bougie, sinon None."""
        pos = self.state.position
        if pos is None:
            return None
        liq = pos.liq_price(self.cfg.liq_margin_pct)
        if pos.side == 1 and bar_low <= liq:
            return liq
        if pos.side == -1 and bar_high >= liq:
            return liq
        return None

    def stats(self) -> dict:
        trades = self.state.trades
        wins = [t for t in trades if t.pnl > 0]
        return {
            "n": len(trades),
            "wins": len(wins),
            "winrate": len(wins) / len(trades) * 100 if trades else 0.0,
            "pnl_total": self.state.total_pnl,
            "pnl_pct": self.state.total_pnl / self.state.start_balance * 100
            if self.state.start_balance
            else 0.0,
            "balance": self.state.balance,
        }
