"""Paper trading dédié AAVE Analyst — hold jusqu'à l'horizon (ou stop / flip)."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from trading.pnl_ledger import (
    append_trade,
    ledger_path,
    load_trades,
    merge_trades,
    recover_balance,
    rewrite_ledger,
)
from utils.logger import setup_logger

logger = setup_logger(__name__)


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def _from_dict(cls, raw: dict | None):
    if not raw:
        return None
    from dataclasses import fields as dc_fields

    allowed = {f.name for f in dc_fields(cls)}
    return cls(**{k: v for k, v in raw.items() if k in allowed})


@dataclass
class AnalystPosition:
    side: int  # +1 long, -1 short
    qty: float
    entry: float
    stop: float
    entry_fee: float
    opened_at: str
    entry_bar_ts: str
    confidence: float
    pred_label: str

    @property
    def side_label(self) -> str:
        return "LONG" if self.side == 1 else "SHORT"

    def unrealized(self, price: float) -> float:
        return self.side * self.qty * (price - self.entry)


@dataclass
class AnalystTrade:
    side: str
    qty: float
    entry: float
    exit: float
    pnl: float
    pnl_pct: float
    reason: str
    opened_at: str
    closed_at: str
    pred_label: str
    confidence: float


@dataclass
class AnalystPaperState:
    balance: float
    start_balance: float
    position: AnalystPosition | None = None
    trades: list[AnalystTrade] = field(default_factory=list)

    @property
    def total_pnl(self) -> float:
        return self.balance - self.start_balance

    def equity(self, price: float) -> float:
        if self.position is None:
            return self.balance
        return self.balance + self.position.unrealized(price)


class AnalystPaper:
    def __init__(
        self,
        *,
        state_file: str,
        start_balance: float = 1000.0,
        position_pct: float = 100.0,
        fee_pct: float = 0.05,
        slippage_pct: float = 0.03,
    ) -> None:
        self.state_path = Path(state_file)
        self.start_balance = start_balance
        self.position_pct = position_pct
        self.fee_pct = fee_pct
        self.slippage_pct = slippage_pct
        self.ledger_path = ledger_path(self.state_path)
        self.state = self._load()

    def _cost_rate(self) -> float:
        return (self.fee_pct + self.slippage_pct) / 100.0

    def _hydrate_pnl(self, st: AnalystPaperState) -> AnalystPaperState:
        ledger = load_trades(self.ledger_path, AnalystTrade)
        merged = merge_trades(st.trades, ledger)
        if len(merged) > len(st.trades):
            logger.info(
                "Analyst PnL récupéré depuis ledger (%d → %d)",
                len(st.trades),
                len(merged),
            )
        st.trades = merged
        new_bal = recover_balance(st.start_balance, st.trades, st.balance)
        if abs(new_bal - st.balance) > 1e-6:
            logger.warning("Analyst solde reconstruit: %.2f → %.2f", st.balance, new_bal)
            st.balance = new_bal
        if len(st.trades) > len(ledger):
            rewrite_ledger(self.ledger_path, st.trades)
        return st

    def _load(self) -> AnalystPaperState:
        if self.state_path.exists():
            try:
                raw = json.loads(self.state_path.read_text(encoding="utf-8"))
                pos = _from_dict(AnalystPosition, raw.get("position"))
                trades = [
                    t
                    for t in (_from_dict(AnalystTrade, x) for x in raw.get("trades", []))
                    if t is not None
                ]
                st = AnalystPaperState(
                    balance=float(raw["balance"]),
                    start_balance=float(raw.get("start_balance", self.start_balance)),
                    position=pos,
                    trades=trades,
                )
                st = self._hydrate_pnl(st)
                logger.info(
                    "Analyst paper RECHARGÉ — solde %.2f, %d trades (PnL intact)",
                    st.balance,
                    len(st.trades),
                )
                return st
            except Exception as exc:
                logger.error("Analyst paper illisible (%s) — tente ledger", exc)
                ledger = load_trades(self.ledger_path, AnalystTrade)
                if ledger:
                    bal = recover_balance(self.start_balance, ledger, self.start_balance)
                    return AnalystPaperState(
                        balance=bal, start_balance=self.start_balance, trades=ledger
                    )
        ledger = load_trades(self.ledger_path, AnalystTrade)
        if ledger:
            bal = recover_balance(self.start_balance, ledger, self.start_balance)
            logger.warning("Analyst: state manquant, ledger PnL restauré (%d)", len(ledger))
            st = AnalystPaperState(
                balance=bal, start_balance=self.start_balance, trades=ledger
            )
            self.state = st
            self.save()
            return st
        return AnalystPaperState(
            balance=self.start_balance, start_balance=self.start_balance
        )

    def save(self) -> None:
        data = {
            "balance": self.state.balance,
            "start_balance": self.state.start_balance,
            "position": asdict(self.state.position) if self.state.position else None,
            "trades": [asdict(t) for t in self.state.trades],
            "bot": "AAVE Analyst",
            "updated_at": _now_iso(),
        }
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.state_path.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
        tmp.replace(self.state_path)

    @property
    def in_position(self) -> bool:
        return self.state.position is not None

    def open(
        self,
        side: int,
        price: float,
        *,
        entry_bar_ts: str,
        confidence: float,
        pred_label: str,
        stop_pct: float = 2.5,
    ) -> AnalystPosition:
        if self.state.position is not None:
            raise RuntimeError("Position déjà ouverte")
        notional = self.state.balance * self.position_pct / 100.0
        if notional < 1.0 or price <= 0:
            raise RuntimeError("Solde trop bas pour ouvrir")
        qty = notional / price
        fee = notional * self._cost_rate()
        if side == 1:
            stop = price * (1.0 - stop_pct / 100.0)
        else:
            stop = price * (1.0 + stop_pct / 100.0)
        pos = AnalystPosition(
            side=side,
            qty=qty,
            entry=price,
            stop=stop,
            entry_fee=fee,
            opened_at=_now_iso(),
            entry_bar_ts=entry_bar_ts,
            confidence=confidence,
            pred_label=pred_label,
        )
        self.state.position = pos
        self.save()
        logger.info(
            "ANALYST OPEN %s %.4f @ %.3f stop %.3f conf=%.0f%%",
            pos.side_label,
            qty,
            price,
            stop,
            confidence * 100,
        )
        return pos

    def close(self, price: float, reason: str) -> AnalystTrade:
        pos = self.state.position
        if pos is None:
            raise RuntimeError("Aucune position")
        exit_fee = pos.qty * price * self._cost_rate()
        gross = pos.side * pos.qty * (price - pos.entry)
        pnl = gross - pos.entry_fee - exit_fee
        engaged = pos.qty * pos.entry
        trade = AnalystTrade(
            side=pos.side_label,
            qty=pos.qty,
            entry=pos.entry,
            exit=price,
            pnl=pnl,
            pnl_pct=(pnl / engaged * 100.0) if engaged else 0.0,
            reason=reason,
            opened_at=pos.opened_at,
            closed_at=_now_iso(),
            pred_label=pos.pred_label,
            confidence=pos.confidence,
        )
        self.state.balance += pnl
        self.state.trades.append(trade)
        self.state.position = None
        append_trade(self.ledger_path, trade)
        self.save()
        logger.info(
            "ANALYST CLOSE %s @ %.3f PnL %+.2f (%+.2f%%) solde %.2f [%s]",
            trade.side,
            price,
            pnl,
            trade.pnl_pct,
            self.state.balance,
            reason,
        )
        return trade

    def stop_hit(self, bar_low: float, bar_high: float) -> bool:
        pos = self.state.position
        if pos is None:
            return False
        return bar_low <= pos.stop if pos.side == 1 else bar_high >= pos.stop

    def stats(self) -> dict:
        trades = self.state.trades
        wins = [t for t in trades if t.pnl > 0]
        return {
            "n": len(trades),
            "wins": len(wins),
            "winrate": len(wins) / len(trades) * 100 if trades else 0.0,
            "pnl_total": self.state.total_pnl,
            "pnl_pct": (
                self.state.total_pnl / self.state.start_balance * 100
                if self.state.start_balance
                else 0.0
            ),
            "balance": self.state.balance,
        }
