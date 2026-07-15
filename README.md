# Paper Trader autonome AAVE/USDT + Bot EQH/EQL

## 🤖 Paper Trader (`trader_main.py`) — NOUVEAU

Bot de trading **autonome en paper trading** : il part d'une mise de départ virtuelle
(1000 USDT par défaut), prend des positions LONG/SHORT sur AAVE/USDT et notifie
chaque action sur Telegram.

### Stratégie (validée par backtest 12 mois : +69 %, drawdown -28 %, ~0.6 trade/jour)

Règle de base de l'indicateur TradingView : **clôture au-dessus de la ligne grise
(EMA20 en 30min) → LONG, en dessous → SHORT.**

La règle brute perd de l'argent en marché sans tendance (~-60 %/an testé), donc
trois protections validées par backtest s'y ajoutent :

| Amélioration | Rôle |
|--------------|------|
| Filtre tendance 4h (EMA50 > EMA200) | Longs uniquement en tendance de fond haussière, shorts en baissière |
| Efficiency ratio ≥ 0.35 | Ne trade que quand le marché est directionnel (élimine le chop) |
| Stop 2.5×ATR + trailing chandelier 3×ATR | Coupe les pertes court, laisse courir les gains |

### Notifications Telegram

- 🟢/🔴 Ouverture LONG/SHORT (entrée, stop, taille, contexte)
- ✅/🛑 Fermeture (PnL du trade, solde, stats globales)
- 🔒 Position protégée (trailing stop passé au-dessus de l'entrée)
- 📊 Rapport quotidien (équité, winrate, position en cours)

### Lancement

```powershell
python trader_main.py
```

L'état (solde, position, historique des trades) est sauvegardé dans
`data/paper_state.json` — le bot reprend où il en était après un redémarrage.

Paramètres réglables dans `.env` (voir `.env.example`) : `START_BALANCE`,
`SIGNAL_TF_MIN`, `EMA_LEN`, `STOP_ATR`, `TRAIL_ATR`, `ER_MIN`, etc.

---

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
