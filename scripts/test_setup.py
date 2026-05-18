"""Test rapide : .env, Telegram, Binance."""

from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

from dotenv import load_dotenv
from telegram import Bot
from telegram.error import BadRequest, Forbidden, InvalidToken

ROOT = Path(__file__).resolve().parents[1]
load_dotenv(ROOT / ".env")


def _parse_chat_id(raw: str) -> int | str:
    raw = raw.strip().strip('"').strip("'")
    try:
        return int(raw)
    except ValueError:
        return raw


async def _suggest_chat_id(bot: Bot) -> None:
    updates = await bot.get_updates(limit=20)
    if not updates:
        print("  1. Ouvre @AAVE_EQHEQL_bot sur Telegram")
        print("  2. Envoie /start")
        print("  3. Lance: python scripts/get_chat_id.py")
        return
    print("  Chats trouves via getUpdates :")
    seen: set[int] = set()
    for u in updates:
        if u.message and u.message.chat:
            cid = u.message.chat.id
            if cid in seen:
                continue
            seen.add(cid)
            name = u.message.chat.first_name or u.message.chat.title or "?"
            print(f"    TELEGRAM_CHAT_ID={cid}  ({u.message.chat.type}, {name})")


async def test_telegram() -> bool:
    token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
    chat_id_raw = os.getenv("TELEGRAM_CHAT_ID", "").strip()
    if not token:
        print("[FAIL] TELEGRAM_BOT_TOKEN manquant dans .env")
        return False
    if not chat_id_raw:
        print("[FAIL] TELEGRAM_CHAT_ID manquant dans .env")
        return False

    bot = Bot(token=token)
    try:
        me = await bot.get_me()
    except InvalidToken:
        print("[FAIL] Token invalide (Not Found)")
        print("  Copie le NOUVEAU token depuis @BotFather dans .env local :")
        print("  TELEGRAM_BOT_TOKEN=123456789:ABC...")
        print("  Pas d'espaces, pas de guillemets. Ne le mets pas sur GitHub.")
        return False
    print(f"[OK] Bot Telegram : @{me.username}")

    chat_id = _parse_chat_id(chat_id_raw)
    try:
        await bot.send_message(
            chat_id=chat_id,
            text="Test bot AAVE EQH/EQL - connexion OK",
        )
        print("[OK] Message test envoye sur Telegram")
        return True
    except BadRequest as e:
        if "chat not found" in str(e).lower():
            print(f"[FAIL] Chat not found (ID={chat_id_raw})")
            print("  Le bot ne peut pas ecrire a ce chat.")
            await _suggest_chat_id(bot)
        else:
            print(f"[FAIL] Telegram: {e}")
        return False
    except Forbidden:
        print("[FAIL] Tu as bloque le bot — debloque-le sur Telegram puis /start")
        return False


async def test_binance() -> bool:
    import ccxt.async_support as ccxt

    exchange = ccxt.binance({"enableRateLimit": True})
    try:
        ohlcv = await exchange.fetch_ohlcv("AAVE/USDT", "5m", limit=5)
        last = ohlcv[-1]
        print(f"[OK] Binance AAVE/USDT 5m - derniere close: {last[4]}")
        return True
    finally:
        await exchange.close()


async def main() -> None:
    print("=== Test configuration bot AAVE ===\n")
    ok_tg = await test_telegram()
    print()
    ok_bn = await test_binance()
    print()
    if ok_tg and ok_bn:
        print("[OK] Tout est pret - lance: python main.py")
        sys.exit(0)
    print("[FAIL] Corrige .env puis relance ce script")
    sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
