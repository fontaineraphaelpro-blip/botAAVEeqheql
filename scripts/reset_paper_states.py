"""Remet les historiques paper a zero (fichiers d'etat separes).

Usage: python scripts/reset_paper_states.py
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"

STATES = {
    "tendance_state.json": {
        "balance": 1000.0,
        "start_balance": 1000.0,
        "position": None,
        "trades": [],
        "bot": "AAVE Tendance",
    },
    "chasseur_state.json": {
        "balance": 1000.0,
        "start_balance": 1000.0,
        "positions": {},
        "trades": [],
        "bot": "Chasseur Shorts",
    },
    "analyst_paper.json": {
        "balance": 1000.0,
        "start_balance": 1000.0,
        "position": None,
        "trades": [],
        "bot": "AAVE Analyst",
    },
}

LEGACY = (
    "couleur_state.json",
    "clean_sticky_state.json",
    "paper_state.json",
    "shorts_state.json",
    "analyst_state.json",
)


def main() -> None:
    DATA.mkdir(parents=True, exist_ok=True)
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    for name, payload in STATES.items():
        path = DATA / name
        data = {**payload, "updated_at": now, "reset_note": "PnL reset"}
        path.write_text(json.dumps(data, indent=2), encoding="utf-8")
        print(f"[OK] {name} -> solde 1000, 0 trade")

    for name in LEGACY:
        path = DATA / name
        if path.exists():
            path.unlink()
            print(f"[DEL] ancien {name}")

    print("Reset termine — redemarre les bots / redeploy Railway.")


if __name__ == "__main__":
    main()
