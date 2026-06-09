# LEORIX Edge

> AI-powered crypto trading strategy skill built for BNB Hack: AI Trading Agent Edition  
> **Track 2 — Strategy Skills** | CoinMarketCap × Trust Wallet × BNB Chain

---

## What is LEORIX Edge?

LEORIX Edge is a CMC Skill that generates backtestable trading strategies using a combination of **EMA trend detection** and **Smart Money Concepts (SMC)** — the same institutional logic used by professional traders.

It reads live market data from CoinMarketCap, derives the current market regime, and outputs structured entry/exit signals with full backtest performance metrics.

**No emotions. No guessing. Just rules.**

---

## Strategy Logic

```
EMA 20 / EMA 50 → Trend Direction (LONG / SHORT)
      ↓
Market Regime (CMC Global Metrics) → Regime Filter
      ↓
SMC Confluence:
  +2  Break of Structure (BOS)
  +1  Liquidity Sweep
  +1  Order Block
  +1  Volume Spike
      ↓
Confluence ≥ 2 → Signal fires
      ↓
Entry | SL (ATR × 2.0) | TP (RR 2.0)
```

### Why SMC?

Smart Money Concepts model how institutional traders (banks, hedge funds) move markets — through liquidity sweeps, order blocks, and structure breaks. Retail traders get stopped out at obvious levels. LEORIX Edge trades *with* smart money, not against it.

---

## Backtest Results

**Timeframe:** 4H | **Period:** ~83 days | **Assets:** BTC, ETH, BNB  
**Min Confluence:** 2/5 | **Min RR:** 2.0 | **Regime Filter:** ON

| Symbol | Trades | Win Rate | Total PnL | Expectancy | Max DD |
|--------|--------|----------|-----------|------------|--------|
| BTC    | 10     | 40.0%    | +2.0R     | +0.20R     | 4.0R   |
| ETH    | 11     | 54.5%    | +7.0R     | +0.64R     | 5.0R   |
| BNB    | 10     | 10.0%    | -7.0R     | -0.70R     | 7.0R   |
| **ALL**| **31** | **35.5%**| **+2.0R** | **+0.06R** | 7.0R   |

> ETH showed strongest performance at 54.5% win rate with +7R over the period.  
> Combined portfolio remains positive expectancy with controlled drawdown.

---

## CMC Integration

LEORIX Edge uses the **CoinMarketCap Data API** for:

- `GET /v2/cryptocurrency/quotes/latest` — Live price, volume, % changes for BTC/ETH/BNB
- `GET /v1/global-metrics/quotes/latest` — Total market cap, BTC dominance, ETH dominance
- **Market Regime derivation** — BULL / BEAR / NEUTRAL from BTC dominance + 7d performance

Price history (OHLCV) is sourced from Binance public API — no auth required, consistent with live trading data.

---

## Project Structure

```
Leorix-Edge/
└── Track-2/
    ├── skill.py          # Main CMC Skill — signal generator + backtester
    ├── smc_engine.py     # SMC detection: BOS, Liquidity Sweep, Order Block
    ├── cmc_client.py     # CMC API + Binance OHLCV wrapper
    ├── backtest.py       # Standalone backtest runner
    └── results/
        ├── trades.json   # Full trade log
        └── report.json   # Performance report
```

---

## Quickstart

```bash
git clone https://github.com/SwwapnilKambley/Leorix-edge.git
cd Leorix-edge/Track-2

pip install requests

CMC_API_KEY=your_key python3 skill.py
```

---

## Built By

**Swwapnil Kambley** — Cloud & DevOps Engineer  
Building LEORIX: an AI-powered crypto futures trading platform  
*Learn. Earn. Grow.*

> LEORIX Edge is the public, hackathon-ready extract of the LEORIX trading engine.  
> The full platform trades live on Binance Futures using the same SMC logic.