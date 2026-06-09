# LEORIX Edge — Track 1: AI Trading Agent

> Autonomous crypto trading agent built for BNB Hack: AI Trading Agent Edition  
> **Track 1 — Live AI Trading Agent** | CoinMarketCap × Trust Wallet × BNB Chain

---

## What it does

LEORIX Edge is a fully autonomous trading agent that:

1. Reads live market data from **CoinMarketCap** (prices, BTC dominance, market regime)
2. Runs **Smart Money Concepts + EMA momentum** strategy to detect high-probability setups
3. Executes trades on **BSC via Trust Wallet AgentKit (TWAK)**
4. Places OCO (One-Cancels-Other) **TP + SL limit orders** after every entry
5. Protects capital via a **circuit breaker** (5% daily drawdown kill switch)
6. Persists state to disk — **survives restarts** without losing position tracking

---

## Architecture

```
CoinMarketCap API
  └─ Prices, BTC dominance, market regime (BULL/BEAR/NEUTRAL)
        ↓
SMC Momentum Signal Engine
  └─ EMA 20/50 trend filter
  └─ Break of Structure (BOS)
  └─ Liquidity Sweep detection
  └─ Order Block identification
  └─ Confluence scoring (0–5)
        ↓
Circuit Breaker
  └─ 5% daily loss limit
  └─ Max 3 simultaneous positions
  └─ 1% account risk per trade
        ↓
Trust Wallet AgentKit (TWAK)
  └─ Entry swap execution (BNB/USDT on BSC)
  └─ TP limit order (twak automate add)
  └─ SL limit order (twak automate add)
  └─ OCO cancel on exit
        ↓
BNB AI Agent SDK (ERC-8004)
  └─ On-chain agent identity registered
  └─ Agent ID: #1328
  └─ TX: 0xe0821a98f8877b5b51ad75c170f6f67e278e4e2fb1108f0087b8456fb1bda29e
```

---

## Strategy

**Entry conditions (all must pass):**
- EMA 20 > EMA 50 → LONG bias | EMA 20 < EMA 50 → SHORT bias
- Regime filter: BEAR market = SHORT only, BULL market = LONG only
- Break of Structure confirmed (close above/below 5-candle swing)
- Confluence score ≥ 3/5

**Exit:**
- Take Profit: ATR × 2.0 × RR (default 2.0R)
- Stop Loss: ATR × 2.0
- OCO: when TP fires → SL cancelled, when SL fires → TP cancelled

**Risk:**
- Max 5% daily drawdown → circuit breaker trips, no more trades
- Max 3 open positions simultaneously
- $5 USDT per trade (configurable)

---

## Sponsor Integrations

| Sponsor | Integration |
|---|---|
| CoinMarketCap | Live quotes, global metrics, market regime via CMC API |
| Trust Wallet AgentKit | Swap execution + OCO limit orders via TWAK CLI |
| BNB AI Agent SDK | ERC-8004 on-chain agent identity on BSC Testnet |

---

## On-Chain Identity

```
Agent ID  : #1328
Network   : BSC Testnet
Wallet    : 0x198E4C33195774C1483fC8F4F4DD3BD29224B011
TX Hash   : 0xe0821a98f8877b5b51ad75c170f6f67e278e4e2fb1108f0087b8456fb1bda29e
```

---

## File Structure

```
Track-1/
├── agent.py           # Main agent loop — signal → execute → monitor
├── circuit_breaker.py # Risk management — drawdown cap, position limits
├── executor.py        # TWAK CLI wrapper — swap, quote, automate
├── identity.py        # BNBAgent ERC-8004 registration (run once)
├── .env.example       # Environment variables template
└── logs/
    ├── agent.jsonl    # Full trade log (JSON lines)
    └── state.json     # Open position state (persists across restarts)
```

---

## Quickstart

```bash
git clone https://github.com/SwwapnilKambley/Leorix-edge.git
cd Leorix-edge/Track-1

# Install TWAK
curl -fsSL https://agent-kit.trustwallet.com/install.sh | bash

# Install Python deps
python3 -m venv venv
source venv/bin/activate
pip install requests bnbagent python-dotenv

# Configure
cp .env.example .env
# Edit .env with your CMC API key and TWAK credentials

# Run (dry run)
DRY_RUN=true python3 agent.py

# Run live
DRY_RUN=false CHAIN=smartchain python3 agent.py
```

---

## Environment Variables

```env
CMC_API_KEY=your_cmc_key
TRADE_AMOUNT_USD=5
MIN_CONFLUENCE=3
SCAN_INTERVAL_SEC=300
DRY_RUN=true
CHAIN=smartchain
TW_ACCESS_ID=your_twak_access_id
TW_HMAC_SECRET=your_twak_hmac_secret
```

---

## Built By

**Swwapnil Kambley** — Cloud & DevOps Engineer  
Building LEORIX: an AI-powered crypto futures trading platform  
*Learn. Earn. Grow.*