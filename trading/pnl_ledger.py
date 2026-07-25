"""Journal PnL append-only — l'historique des trades survit aux resets partiels."""

from __future__ import annotations

import json
from dataclasses import asdict, fields
from pathlib import Path
from typing import Any, TypeVar

T = TypeVar("T")


def _filter(cls: type[T], raw: dict[str, Any]) -> T | None:
    if not raw:
        return None
    allowed = {f.name for f in fields(cls)}
    try:
        return cls(**{k: v for k, v in raw.items() if k in allowed})  # type: ignore[arg-type]
    except Exception:
        return None


def ledger_path(state_file: str | Path, suffix: str = "_pnl.jsonl") -> Path:
    """tendance_state.json → tendance_state_pnl.jsonl (même dossier)."""
    p = Path(state_file)
    return p.with_name(p.stem + suffix)


def load_trades(path: Path, trade_cls: type[T]) -> list[T]:
    if not path.exists():
        return []
    out: list[T] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            raw = json.loads(line)
        except json.JSONDecodeError:
            continue
        t = _filter(trade_cls, raw)
        if t is not None:
            out.append(t)
    return out


def append_trade(path: Path, trade: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(asdict(trade), ensure_ascii=False) + "\n")


def rewrite_ledger(path: Path, trades: list[Any]) -> None:
    """Réécrit le ledger (après sync / recovery). N'efface jamais sans backup."""
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        bak = path.with_suffix(path.suffix + ".bak")
        bak.write_bytes(path.read_bytes())
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8") as f:
        for t in trades:
            f.write(json.dumps(asdict(t), ensure_ascii=False) + "\n")
    tmp.replace(path)


def merge_trades(state_trades: list[T], ledger_trades: list[T]) -> list[T]:
    """Prend l'historique le plus long (ledger prioritaire si plus riche)."""
    if len(ledger_trades) >= len(state_trades):
        return list(ledger_trades)
    return list(state_trades)


def recover_balance(start_balance: float, trades: list[Any], current_balance: float) -> float:
    """Si l'état a l'air reset (balance=start) mais des trades existent → reconstruit."""
    if not trades:
        return current_balance
    rebuilt = float(start_balance) + sum(float(getattr(t, "pnl", 0.0)) for t in trades)
    # État reset typique : balance == start alors que des trades existent
    if abs(current_balance - start_balance) < 1e-9 and abs(rebuilt - start_balance) > 1e-6:
        return rebuilt
    return current_balance
