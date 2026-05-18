"""Récupère ton TELEGRAM_CHAT_ID après avoir envoyé un message au bot."""

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
        print("Définis TELEGRAM_BOT_TOKEN dans .env")
        sys.exit(1)

    bot = Bot(token=token)
    updates = await bot.get_updates(limit=10)
    if not updates:
        print("Aucun message. Ouvre Telegram, envoie /start à ton bot, relance ce script.")
        sys.exit(1)

    for u in reversed(updates):
        if u.message and u.message.chat:
            chat = u.message.chat
            print(f"TELEGRAM_CHAT_ID={chat.id}")
            print(f"Chat: {chat.type} | {chat.first_name or chat.title}")
            return

    print("Pas de chat trouvé dans les updates.")
    sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
