# Bot EQH/EQL AAVE — Binance + Telegram

Détection automatique des **Equal Highs (EQH)**, **Equal Lows (EQL)** et **liquidity sweeps**, reproduisant la logique de l’indicateur LuxAlgo *EQH/EQL Liquidity Zones* — sans TradingView.

Optimisé pour **AAVE/USDT** en scalping sur **5m** uniquement.

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
├── notifier/
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

## Déploiement Railway (24/7)

Le bot est prêt pour un **worker** Railway (processus long, pas de site web).

### Checklist avant deploy

1. `python scripts/test_setup.py` → tout en `[OK]` en local
2. Repo GitHub connecté : `fontaineraphaelpro-blip/botAAVEeqheql`
3. Token Telegram valide (pas de double `8889855484:8889855484:...`)

### Étapes Railway

1. [railway.app](https://railway.app) → **New Project** → **Deploy from GitHub**
2. Sélectionne le repo `botAAVEeqheql`
3. **Variables** (onglet Variables) :

| Variable | Valeur |
|----------|--------|
| `TELEGRAM_BOT_TOKEN` | ton token BotFather |
| `TELEGRAM_CHAT_ID` | `6381593262` (ou le tien via `get_chat_id.py`) |
| `LOG_LEVEL` | `INFO` |
| `EXCHANGE` | `kucoin` (**obligatoire sur Railway USA**) |
| `EXCHANGE_FALLBACK` | `true` (essaie mexc puis binance_vision) |

Ne mets **pas** de `.env` sur GitHub — uniquement dans Railway.

**Bybit sur Railway** : bloque aussi (403 CloudFront USA). Bybit fonctionne **en local** (PC France). Sur Railway utilise **KuCoin** — graphique TV : KUCOIN:AAVEUSDT.

4. **Settings** → Start Command : `python main.py` (déjà dans `railway.toml`)
5. **Deploy** — dans les logs tu dois voir `Bot démarré`
6. Sur Telegram : message **Bot EQH/EQL démarré**

### Comportement en cloud

- Au **redémarrage** Railway, l’historique pivots/zones est **réinitialisé** (normal) — les alertes repartent sur les nouvelles bougies.
- Pas de clé Binance requise (données publiques).
- Coût : selon ton plan Railway (crédits / abonnement).

### Vérifier que ça tourne

- Message Telegram au démarrage après chaque deploy
- Logs Railway sans erreur `InvalidToken` ou `Chat not found`

## Sécurité

- Ne commite **jamais** `.env` (déjà dans `.gitignore`).
- Si ton token a été exposé, régénère-le via `/revoke` sur BotFather.

## Avertissement

Outil d’alertes uniquement — pas de conseil financier. Teste avant tout usage réel.
