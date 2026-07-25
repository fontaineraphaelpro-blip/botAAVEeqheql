"""Identité des bots paper — noms clairs pour Telegram."""

from __future__ import annotations

# Paper trader : EMA flip 30m + tendance 4h + efficiency ratio
BOT_TENDANCE = "AAVE Tendance"
TAG_TENDANCE = "TENDANCE"

# Short Hunter : shorts multi-alts quand BTC en bear
BOT_CHASSEUR = "Chasseur Shorts"
TAG_CHASSEUR = "CHASSEUR"


def header(tag: str, title: str) -> str:
    """En-tête commun pour séparer clairement les bots."""
    return f"<b>[{tag}]</b> {title}"
