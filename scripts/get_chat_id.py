"""Recupere TELEGRAM_CHAT_ID apres /start envoye au bot."""

from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

from dotenv import load_dotenv
from telegram import Bot

ROOT = Path(__file__).resolve().parents[1]
load_dotenv(ROOT / ".env")


async def main() -> None:
    token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
    if not token:
        print("Definis TELEGRAM_BOT_TOKEN dans .env")
        sys.exit(1)

    bot = Bot(token=token)
    updates = await bot.get_updates(limit=30)
    if not updates:
        print("Aucun message trouve.")
        print("1. Ouvre ton bot sur Telegram (@AAVE_EQHEQL_bot)")
        print("2. Clique Demarrer ou envoie /start")
        print("3. Relance: python scripts/get_chat_id.py")
        sys.exit(1)

    seen: set[int] = set()
    print("Copie la bonne ligne dans ton .env :\n")
    for u in reversed(updates):
        if not u.message or not u.message.chat:
            continue
        chat = u.message.chat
        if chat.id in seen:
            continue
        seen.add(chat.id)
        name = chat.first_name or chat.title or "?"
        print(f"TELEGRAM_CHAT_ID={chat.id}")
        print(f"  -> {chat.type} | {name}\n")

    if len(seen) == 1:
        print("(Un seul chat — utilise cet ID dans .env)")


if __name__ == "__main__":
    asyncio.run(main())
