"""Verification complete: KuCoin -> detection -> Telegram (1 cycle)."""

from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv
load_dotenv(ROOT / ".env")

os.environ.setdefault("EXCHANGE", "kucoin")

from config import get_config
from main import AAVEEqhEqlBot


async def main() -> None:
    print("=== VERIFICATION BOT AAVE EQH/EQL ===\n")
    config = get_config()
    bot = AAVEEqhEqlBot(config)

    ok = True

    # 1. Market
    try:
        await bot.market.start()
        print(f"[OK] Donnees marche: {bot.market.exchange_id}")
        df = await bot.market.fetch_ohlcv("AAVE/USDT", "5m")
        closed = bot.market.closed_bars(df)
        print(f"[OK] OHLCV: {len(closed)} bougies fermees, close={closed['close'].iloc[-1]:.4f}")
    except Exception as e:
        print(f"[FAIL] Marche: {e}")
        ok = False
        return

    # 2. Telegram
    try:
        await bot.telegram.start()
        me = await bot.telegram._bot.get_me()
        print(f"[OK] Telegram: @{me.username}")
    except Exception as e:
        print(f"[FAIL] Telegram: {e}")
        ok = False

    # 3. Warmup + detection
    try:
        n = bot.detector.warmup("AAVE/USDT", "5m", df)
        print(f"[OK] Warmup: {n} zones historiques, pivots charges")
        bot.detector._state("AAVE/USDT", "5m").last_processed_ts = None
        r = bot.detector.process(
            "AAVE/USDT", "5m", closed, bot.market.last_closed_ts(df)
        )
        print(f"[OK] Scan live: {len(r.new_zones)} zones, {len(r.sweeps)} sweeps")
        for z in r.new_zones:
            print(f"     -> {z.zone_type.value} @ {z.sweep_level:.4f}")
        for z, st in r.sweeps:
            print(f"     -> {st} @ {z.sweep_level:.4f}")
    except Exception as e:
        print(f"[FAIL] Detection: {e}")
        ok = False

    # 4. Envoi Telegram test (message + simulation alerte si signal)
    if ok:
        try:
            await bot.telegram.send_raw(
                "🧪 <b>Verification OK</b>\n"
                f"Exchange: <code>{bot.market.exchange_id}</code>\n"
                "Le bot est pret a detecter EQH/EQL/SWEEP en live."
            )
            print("[OK] Message verification envoye sur Telegram")
        except Exception as e:
            print(f"[FAIL] Envoi Telegram: {e}")
            ok = False

    await bot.market.close()

    print("\n" + ("=" * 50))
    if ok:
        print("RESULTAT: TOUT OK — redeploy Railway pour appliquer en prod.")
    else:
        print("RESULTAT: ECHEC — corrige .env ou logs ci-dessus.")
    print("=" * 50)


if __name__ == "__main__":
    asyncio.run(main())
