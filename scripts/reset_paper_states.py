"""Remet les historiques paper a zero — JAMAIS Tendance par defaut.

Usage:
  python scripts/reset_paper_states.py              # refuse (affiche l'aide)
  python scripts/reset_paper_states.py --analyst    # reset Analyst seul
  python scripts/reset_paper_states.py --chasseur   # reset Chasseur seul
  python scripts/reset_paper_states.py --all        # TOUT (demande confirmation)
  python scripts/reset_paper_states.py --tendance --yes  # reset Tendance (explicite)
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from config import data_dir

DATA = data_dir()

STATES = {
    "tendance": (
        "tendance_state.json",
        {
            "balance": 1000.0,
            "start_balance": 1000.0,
            "position": None,
            "trades": [],
            "bot": "AAVE Tendance",
        },
    ),
    "chasseur": (
        "chasseur_state.json",
        {
            "balance": 1000.0,
            "start_balance": 1000.0,
            "positions": {},
            "trades": [],
            "bot": "Chasseur Shorts",
        },
    ),
    "analyst": (
        "analyst_paper.json",
        {
            "balance": 1000.0,
            "start_balance": 1000.0,
            "position": None,
            "trades": [],
            "bot": "AAVE Analyst",
        },
    ),
}

LEGACY = (
    "couleur_state.json",
    "clean_sticky_state.json",
    "paper_state.json",
    "shorts_state.json",
)


def main() -> None:
    ap = argparse.ArgumentParser(
        description="Reset paper states. Tendance n'est JAMAIS touche sans --tendance/--all."
    )
    ap.add_argument("--tendance", action="store_true", help="Reset AAVE Tendance")
    ap.add_argument("--chasseur", action="store_true", help="Reset Chasseur Shorts")
    ap.add_argument("--analyst", action="store_true", help="Reset AAVE Analyst paper")
    ap.add_argument("--all", action="store_true", help="Reset les 3 bots")
    ap.add_argument(
        "--yes",
        action="store_true",
        help="Confirme le reset Tendance / --all (obligatoire)",
    )
    args = ap.parse_args()

    selected: list[str] = []
    if args.all:
        selected = ["tendance", "chasseur", "analyst"]
    else:
        if args.tendance:
            selected.append("tendance")
        if args.chasseur:
            selected.append("chasseur")
        if args.analyst:
            selected.append("analyst")

    if not selected:
        print(
            "Aucun bot selectionne.\n"
            "  --analyst     reset Analyst seulement\n"
            "  --chasseur    reset Chasseur seulement\n"
            "  --tendance --yes   reset Tendance (historique perdu)\n"
            "  --all --yes   reset les 3\n"
            f"Dossier: {DATA}"
        )
        sys.exit(1)

    if ("tendance" in selected or args.all) and not args.yes:
        print(
            "REFUSE: reset Tendance / --all demande --yes "
            "(protege l'historique). Exemple: --tendance --yes"
        )
        sys.exit(2)

    DATA.mkdir(parents=True, exist_ok=True)
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    for key in selected:
        name, payload = STATES[key]
        path = DATA / name
        # Backup avant wipe
        if path.exists():
            bak = path.with_suffix(path.suffix + f".bak-{now.replace(':', '').replace(' ', '-')}")
            bak.write_bytes(path.read_bytes())
            print(f"[BAK] {name} -> {bak.name}")
        data = {**payload, "updated_at": now, "reset_note": "PnL reset"}
        path.write_text(json.dumps(data, indent=2), encoding="utf-8")
        print(f"[OK] {key} reset -> {path}")

    for name in LEGACY:
        path = DATA / name
        if path.exists():
            path.unlink()
            print(f"[DEL] ancien {name}")

    print(f"Termine dans {DATA}")


if __name__ == "__main__":
    main()
