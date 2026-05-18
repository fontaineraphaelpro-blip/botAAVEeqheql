"""Test rapide : .env, Telegram, Binance."""

from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
load_dotenv(ROOT / ".env")


async def test_telegram() -> bool:
    token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
    chat_id = os.getenv("TELEGRAM_CHAT_ID", "").strip()
    if not token:
        print("[FAIL] TELEGRAM_BOT_TOKEN manquant dans .env")
        return False
    if not chat_id:
        print("[FAIL] TELEGRAM_CHAT_ID manquant dans .env")
        return False

    from telegram import Bot

    bot = Bot(token=token)
    me = await bot.get_me()
    print(f"[OK] Bot Telegram : @{me.username}")

    await bot.send_message(
        chat_id=chat_id,
        text="Test bot AAVE EQH/EQL - connexion OK",
    )
    print("[OK] Message test envoye sur Telegram")
    return True


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
