"""
agent.py — LEORIX Edge Track 1 — Autonomous Trading Agent
Stack: CMC Signal → SMC Momentum Strategy → Circuit Breaker → TWAK Execution
Chain: BSC Testnet
Execution: BNB/USDT swaps only (BSC native, cleanest pair)
"""

import os, sys, json, time
from datetime import datetime

sys.path.insert(0, "../Track-2")
from cmc_client import CMCClient
from skill import generate_signal

sys.path.insert(0, ".")
from circuit_breaker import CircuitBreaker
from executor import (
    get_price, get_portfolio, swap_quote, swap_execute,
    list_automations, WALLET_ADDRESS
)

# ── Config ────────────────────────────────────────────────────────────────────
CMC_API_KEY    = os.getenv("CMC_API_KEY", "2d25132563a8497893597470798e861e")
TRADE_USDT     = float(os.getenv("TRADE_AMOUNT_USD", "5"))   # USDT to spend per trade
MIN_CONFLUENCE = int(os.getenv("MIN_CONFLUENCE", "3"))
SCAN_INTERVAL  = int(os.getenv("SCAN_INTERVAL_SEC", "300"))
DRY_RUN        = os.getenv("DRY_RUN", "true").lower() == "true"

# For BSC execution we trade BNB/USDT only — native pair, no precision issues
# Signal is derived from BTC/ETH/BNB but execution is always BNB swap
SIGNAL_SYMBOLS  = ["BTC", "ETH", "BNB"]
EXEC_SYMBOL     = "BNB"   # always execute on BNB — BSC native

# ── Logging ───────────────────────────────────────────────────────────────────
def log(level: str, msg: str):
    ts = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{ts}] [{level}] {msg}", flush=True)

def save_log(entry: dict):
    os.makedirs("logs", exist_ok=True)
    with open("logs/agent.jsonl", "a") as f:
        f.write(json.dumps(entry) + "\n")

# ── Execution logic ───────────────────────────────────────────────────────────
def execute_signal(signal: dict, regime: str) -> dict:
    """
    LONG  → buy BNB with USDT  (USDT → BNB)
    SHORT → sell BNB for USDT  (BNB → USDT)
    Amount: always TRADE_USDT worth
    """
    direction = signal["direction"]

    if direction == "LONG":
        from_tok = "USDT"
        to_tok   = "BNB"
        amount   = TRADE_USDT          # spend $5 USDT to buy BNB
    else:
        from_tok = "BNB"
        to_tok   = "USDT"
        # Convert USDT amount to BNB quantity
        bnb_price = get_price("BNB") or 600.0
        amount    = round(TRADE_USDT / bnb_price, 6)  # e.g. 5/600 = 0.008333 BNB

    return from_tok, to_tok, amount


# ── Agent loop ────────────────────────────────────────────────────────────────
def run_agent():
    log("INFO", "LEORIX Edge Agent starting")
    log("INFO", f"Wallet   : {WALLET_ADDRESS}")
    log("INFO", f"DRY RUN  : {DRY_RUN}")
    log("INFO", f"Trade    : ${TRADE_USDT} USDT per signal")
    log("INFO", f"Execution: BNB/USDT on BSC")
    log("INFO", f"Min confluence: {MIN_CONFLUENCE}/5")

    client = CMCClient(CMC_API_KEY)
    cb = CircuitBreaker(
        max_daily_loss_pct=5.0,
        max_open_positions=3,
        max_risk_per_trade_pct=1.0,
    )
    cb.update_balance(TRADE_USDT * 10)

    log("INFO", "Agent ready. Starting scan loop...\n")

    while True:
        scan_time = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
        log("SCAN", f"─── New scan at {scan_time} ───")

        try:
            regime = client.get_market_regime()
            log("INFO", f"Market regime: {regime}")

            can_trade, block_reason = cb.can_trade(TRADE_USDT * 10)
            if not can_trade:
                log("BLOCK", f"Circuit breaker: {block_reason}")
                time.sleep(SCAN_INTERVAL)
                continue

            best_signal = None
            best_confluence = 0

            # Scan all symbols, pick highest confluence signal
            for symbol in SIGNAL_SYMBOLS:
                try:
                    candles = client.get_ohlcv_historical(symbol, interval="4h", limit=100)
                    signal = generate_signal(symbol, candles, regime, min_rr=2.0)

                    if signal["direction"] == "NO_SIGNAL":
                        log("SKIP", f"{symbol}: {signal.get('reason', 'No signal')}")
                        continue

                    if signal["confluence"] < MIN_CONFLUENCE:
                        log("SKIP", f"{symbol}: Low confluence {signal['confluence']}/5")
                        continue

                    log("SIGNAL", (
                        f"{symbol} {signal['direction']} | "
                        f"Confluence: {signal['confluence']}/5 | "
                        f"Entry: {signal['entry']} | "
                        f"RR: {signal['rr']}"
                    ))

                    if signal["confluence"] > best_confluence:
                        best_confluence = signal["confluence"]
                        best_signal = signal
                        best_signal["symbol"] = symbol

                except Exception as e:
                    log("ERR", f"{symbol}: {e}")
                    continue

            # Execute best signal only
            if best_signal:
                symbol = best_signal["symbol"]
                direction = best_signal["direction"]

                for r in best_signal["reasons"]:
                    log("REASON", f"  • {r}")

                from_tok, to_tok, amount = execute_signal(best_signal, regime)
                log("TRADE", f"Executing: {amount} {from_tok} → {to_tok} (${TRADE_USDT} notional)")

                # Get quote first
                quote = swap_quote(from_tok, to_tok, amount)
                log("QUOTE", f"{quote['raw'][:120]}")

                entry = {
                    "time": scan_time,
                    "symbol": symbol,
                    "direction": direction,
                    "entry": best_signal["entry"],
                    "sl": best_signal["sl"],
                    "tp": best_signal["tp"],
                    "rr": best_signal["rr"],
                    "confluence": best_signal["confluence"],
                    "regime": regime,
                    "from_token": from_tok,
                    "to_token": to_tok,
                    "amount": amount,
                    "dry_run": DRY_RUN,
                    "reasons": best_signal["reasons"],
                }

                if DRY_RUN:
                    log("DRY", f"Signal logged, execution skipped (DRY_RUN=true)")
                    entry["status"] = "DRY_RUN"
                    save_log(entry)
                else:
                    log("EXEC", f"Sending swap: {amount} {from_tok} → {to_tok}")
                    result = swap_execute(from_tok, to_tok, amount)
                    if result["success"]:
                        log("OK", f"✅ Swap executed — TX: {result['tx_hash']}")
                        entry["status"] = "EXECUTED"
                        entry["tx_hash"] = result["tx_hash"]
                        cb.record_trade_open()
                    else:
                        log("ERR", f"Swap failed — {result['raw'][:150]}")
                        entry["status"] = "FAILED"
                        entry["error"] = result["raw"][:200]
                    save_log(entry)
            else:
                log("INFO", "No qualifying signal this scan")

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


def run_once():
    log("INFO", "Single scan mode")
    client = CMCClient(CMC_API_KEY)
    regime = client.get_market_regime()
    log("INFO", f"Regime: {regime}")

    for symbol in SIGNAL_SYMBOLS:
        candles = client.get_ohlcv_historical(symbol, interval="4h", limit=100)
        signal = generate_signal(symbol, candles, regime, min_rr=2.0)
        print(f"\n{symbol}: {signal['direction']} | confluence {signal.get('confluence',0)}/5")
        for r in signal.get("reasons", []):
            print(f"  • {r}")

        if signal["direction"] != "NO_SIGNAL":
            from_tok, to_tok, amount = execute_signal(signal, regime)
            print(f"  → Would swap: {amount} {from_tok} → {to_tok}")


if __name__ == "__main__":
    if "--once" in sys.argv:
        run_once()
    else:
        run_agent()