"""
agent.py — LEORIX Edge Track 1 — Autonomous Trading Agent
Stack: CMC Signal → SMC Momentum Strategy → Circuit Breaker → TWAK Execution
Chain: BSC Testnet
"""

import os, sys, json, time
from datetime import datetime

sys.path.insert(0, "../Track-2")  # reuse cmc_client + skill from Track 2

from cmc_client import CMCClient
from skill import generate_signal

sys.path.insert(0, ".")
from circuit_breaker import CircuitBreaker
from executor import (
    get_price, get_portfolio, swap_quote, swap_execute,
    list_automations, WALLET_ADDRESS
)

# ── Config ────────────────────────────────────────────────────────────────────
CMC_API_KEY     = os.getenv("CMC_API_KEY", "2d25132563a8497893597470798e861e")
TRADE_AMOUNT    = float(os.getenv("TRADE_AMOUNT_USD", "5"))   # USD per trade (small for testnet)
MIN_CONFLUENCE  = int(os.getenv("MIN_CONFLUENCE", "3"))
SCAN_INTERVAL   = int(os.getenv("SCAN_INTERVAL_SEC", "300"))  # 5 min
DRY_RUN         = os.getenv("DRY_RUN", "true").lower() == "true"
SYMBOLS         = ["BTC", "ETH", "BNB"]

# ── Logging ───────────────────────────────────────────────────────────────────
def log(level: str, msg: str):
    ts = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{ts}] [{level}] {msg}", flush=True)

def save_log(entry: dict):
    os.makedirs("logs", exist_ok=True)
    path = "logs/agent.jsonl"
    with open(path, "a") as f:
        f.write(json.dumps(entry) + "\n")

# ── Agent ─────────────────────────────────────────────────────────────────────
def run_agent():
    log("INFO", f"LEORIX Edge Agent starting")
    log("INFO", f"Wallet : {WALLET_ADDRESS}")
    log("INFO", f"DRY RUN: {DRY_RUN}")
    log("INFO", f"Trade  : ${TRADE_AMOUNT} per signal")
    log("INFO", f"Min confluence: {MIN_CONFLUENCE}/5")

    client = CMCClient(CMC_API_KEY)
    cb = CircuitBreaker(
        max_daily_loss_pct=5.0,
        max_open_positions=3,
        max_risk_per_trade_pct=1.0,
    )

    # Set starting balance (use TRADE_AMOUNT * 10 as proxy for testnet)
    starting_balance = TRADE_AMOUNT * 10
    cb.update_balance(starting_balance)

    log("INFO", "Agent ready. Starting scan loop...\n")

    while True:
        scan_time = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
        log("SCAN", f"─── New scan at {scan_time} ───")

        try:
            regime = client.get_market_regime()
            log("INFO", f"Market regime: {regime}")

            can_trade, block_reason = cb.can_trade(starting_balance)
            if not can_trade:
                log("BLOCK", f"Circuit breaker: {block_reason}")
                time.sleep(SCAN_INTERVAL)
                continue

            for symbol in SYMBOLS:
                try:
                    candles = client.get_ohlcv_historical(symbol, interval="4h", limit=100)
                    signal = generate_signal(symbol, candles, regime, min_rr=2.0)

                    if signal["direction"] == "NO_SIGNAL":
                        log("SKIP", f"{symbol}: {signal.get('reason', 'No signal')}")
                        continue

                    if signal["confluence"] < MIN_CONFLUENCE:
                        log("SKIP", f"{symbol}: Low confluence {signal['confluence']}/{5}")
                        continue

                    log("SIGNAL", (
                        f"{symbol} {signal['direction']} | "
                        f"Entry: {signal['entry']} | "
                        f"SL: {signal['sl']} | "
                        f"TP: {signal['tp']} | "
                        f"RR: {signal['rr']} | "
                        f"Confluence: {signal['confluence']}/5"
                    ))
                    for r in signal["reasons"]:
                        log("REASON", f"  • {r}")

                    # Determine swap direction
                    # LONG BNB = buy BNB with USDT
                    # SHORT BNB = sell BNB for USDT
                    if signal["direction"] == "LONG":
                        from_tok, to_tok = "USDT", symbol if symbol != "BTC" else "WBTC"
                    else:
                        from_tok, to_tok = symbol if symbol != "BTC" else "WBTC", "USDT"

                    # Get quote first
                    quote = swap_quote(from_tok, to_tok, TRADE_AMOUNT)
                    log("QUOTE", f"{symbol}: {quote['raw'][:80]}")

                    entry = {
                        "time": scan_time,
                        "symbol": symbol,
                        "direction": signal["direction"],
                        "entry": signal["entry"],
                        "sl": signal["sl"],
                        "tp": signal["tp"],
                        "rr": signal["rr"],
                        "confluence": signal["confluence"],
                        "regime": regime,
                        "dry_run": DRY_RUN,
                        "reasons": signal["reasons"],
                    }

                    if DRY_RUN:
                        log("DRY", f"{symbol}: Signal logged, execution skipped (DRY_RUN=true)")
                        entry["status"] = "DRY_RUN"
                        save_log(entry)
                    else:
                        log("EXEC", f"{symbol}: Executing swap {from_tok} → {to_tok} ${TRADE_AMOUNT}")
                        result = swap_execute(from_tok, to_tok, TRADE_AMOUNT)
                        if result["success"]:
                            log("OK", f"{symbol}: Swap executed — TX: {result['tx_hash']}")
                            entry["status"] = "EXECUTED"
                            entry["tx_hash"] = result["tx_hash"]
                            cb.record_trade_open()
                        else:
                            log("ERR", f"{symbol}: Swap failed — {result['raw'][:100]}")
                            entry["status"] = "FAILED"
                            entry["error"] = result["raw"][:200]

                        save_log(entry)

                except Exception as e:
                    log("ERR", f"{symbol}: {e}")
                    continue

            # Circuit breaker status
            status = cb.status()
            log("CB", (
                f"Daily PnL: {status['daily_pnl_usd']}  "
                f"Open: {status['open_positions']}  "
                f"Trades: {status['trades_today']}  "
                f"Tripped: {status['tripped']}"
            ))

        except Exception as e:
            log("ERR", f"Scan error: {e}")

        log("WAIT", f"Next scan in {SCAN_INTERVAL}s...\n")
        time.sleep(SCAN_INTERVAL)


# ── Single scan mode (for testing) ────────────────────────────────────────────
def run_once():
    """Run one scan and exit — for testing."""
    log("INFO", "Single scan mode")
    client = CMCClient(CMC_API_KEY)
    regime = client.get_market_regime()
    log("INFO", f"Regime: {regime}")

    for symbol in SYMBOLS:
        candles = client.get_ohlcv_historical(symbol, interval="4h", limit=100)
        signal = generate_signal(symbol, candles, regime, min_rr=2.0)
        print(f"\n{symbol}: {signal['direction']} | confluence {signal.get('confluence',0)}/5")
        for r in signal.get("reasons", []):
            print(f"  • {r}")


if __name__ == "__main__":
    import sys
    if "--once" in sys.argv:
        run_once()
    else:
        run_agent()