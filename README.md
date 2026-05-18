# Bot EQH/EQL AAVE — Binance + Telegram

Détection automatique des **Equal Highs (EQH)**, **Equal Lows (EQL)** et **liquidity sweeps**, reproduisant la logique de l’indicateur LuxAlgo *EQH/EQL Liquidity Zones* — sans TradingView.

Optimisé pour **AAVE/USDT** en scalping sur **1m**, **5m** et **15m**.

## Fonctionnalités

- Connexion Binance (ccxt async) — OHLCV public, pas de clé API requise
- Pivots LuxAlgo : left=10, right=2, seuil=0.03%
- Zones actives en mémoire (max 60), anti-doublons, cooldown Telegram
- Alertes : EQH, EQL, EQH SWEEP, EQL SWEEP
- Architecture modulaire, asyncio parallèle

## Prérequis

- Python **3.12+**
- Compte Telegram + bot [@BotFather](https://t.me/BotFather)

## Installation

```powershell
cd "C:\Users\jeanr\OneDrive\Desktop\bot AAVE"
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

## Configuration

1. Copie `.env.example` vers `.env` (déjà présent si tu as cloné le projet).
2. `TELEGRAM_BOT_TOKEN` : token fourni par BotFather.
3. **Chat ID** :
   - Ouvre ton bot sur Telegram et envoie `/start`
   - Lance :

```powershell
python scripts/get_chat_id.py
```

4. Colle la valeur dans `.env` :

```env
TELEGRAM_CHAT_ID=123456789
```

### Paramètres pivots (`config.py`)

| Paramètre | Défaut | Description |
|-----------|--------|-------------|
| `pivot_left` | 10 | Barres à gauche du pivot |
| `pivot_right` | 2 | Barres à droite (confirmation) |
| `threshold_pct` | 0.03 | Écart max % entre deux pivots « égaux » |
| `max_active_zones` | 60 | Zones non sweepées max |

## Lancement

```powershell
python main.py
```

Tu dois recevoir un message **Bot EQH/EQL démarré**, puis les signaux EQH/EQL/SWEEP sur AAVE.

## Structure du projet

```
bot AAVE/
├── main.py
├── config.py
├── scanner/
│   ├── market_data.py
│   ├── liquidity_detector.py
│   └── sweeps.py
├── telegram/
│   └── bot.py
├── models/
│   ├── liquidity_zone.py
│   └── market_state.py
├── utils/
│   ├── pivots.py
│   └── logger.py
└── scripts/
    └── get_chat_id.py
```

## Exemples d’alertes

**EQH détecté**
```
🔴 EQH détecté
Pair : AAVEUSDT
TF : 5m
Prix : 284.1500
```

**EQH SWEEP**
```
⚠️ EQH SWEEP
Pair : AAVEUSDT
Liquidity taken above highs
```

## Sécurité

- Ne commite **jamais** `.env` (déjà dans `.gitignore`).
- Si ton token a été exposé, régénère-le via `/revoke` sur BotFather.

## Avertissement

Outil d’alertes uniquement — pas de conseil financier. Teste avant tout usage réel.
