"""
agent.py — LEORIX Edge Track 1 — Autonomous Trading Agent
Stack: CMC Signal → SMC Engine (unified brain) → Circuit Breaker → TWAK Execution
Chain: set via .env (CHAIN=smartchain for mainnet)
OCO: TP + SL placed after entry. When one fires, the other is cancelled.
State: persisted to disk — survives restarts.

v2 fixes:
  - dotenv: .env loads from the script's own directory regardless of how the
    process is launched (nohup/systemd/cron start with a clean environment —
    without this, os.getenv fell back to defaults and DRY_RUN wrongly read true).
  - UNIFIED BRAIN: signals come from Track-2 smc_engine.generate_signal (the
    proper SMC engine with proximity-validated order blocks + regime alignment),
    not the weaker skill.py logic.
  - Counter-trend protection preserved: BEAR regime blocks LONGs, BULL blocks
    SHORTs (smc_engine scores alignment but doesn't block — we block here).
  - REAL BALANCE: circuit breaker is fed the actual wallet balance via
    executor.get_portfolio_usd(), falling back to STARTING_BALANCE_USD env.
  - Startup credential check logged (without printing secrets).
"""

import os, sys, json, time, re
from datetime import datetime

# ── Load .env from this script's directory (works under nohup/systemd) ───────
try:
    from dotenv import load_dotenv
    _env_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")
    load_dotenv(_env_path)
except ImportError:
    print("[WARN] python-dotenv not installed — run: pip install python-dotenv")

sys.path.insert(0, "../Track-2")
from cmc_client import CMCClient
from smc_engine import generate_signal          # v2: unified brain (was skill.py)

sys.path.insert(0, ".")
from circuit_breaker import CircuitBreaker
from executor import (get_price, swap_quote, swap_execute, _run,
                      get_portfolio_usd, WALLET_ADDRESS)

# ── Config ────────────────────────────────────────────────────────────────────
CMC_API_KEY    = os.getenv("CMC_API_KEY", "")
TRADE_USDT     = float(os.getenv("TRADE_AMOUNT_USD", "5"))
MIN_CONFLUENCE = int(os.getenv("MIN_CONFLUENCE", "3"))
SCAN_INTERVAL  = int(os.getenv("SCAN_INTERVAL_SEC", "300"))
DRY_RUN        = os.getenv("DRY_RUN", "true").lower() == "true"
CHAIN          = os.getenv("CHAIN", "smartchain-testnet").strip('"')
FALLBACK_BAL   = float(os.getenv("STARTING_BALANCE_USD", "50"))
SIGNAL_SYMBOLS = ["BTC", "ETH", "BNB"]
STATE_FILE     = "logs/state.json"

# ── Logging ───────────────────────────────────────────────────────────────────
def log(level: str, msg: str):
    ts = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{ts}] [{level}] {msg}", flush=True)

def save_log(entry: dict):
    os.makedirs("logs", exist_ok=True)
    with open("logs/agent.jsonl", "a") as f:
        f.write(json.dumps(entry) + "\n")

# ── State persistence ─────────────────────────────────────────────────────────
def load_state() -> dict:
    if os.path.exists(STATE_FILE):
        try:
            with open(STATE_FILE, "r") as f:
                data = json.load(f)
                if data:
                    log("INFO", f"Restored {len(data)} open position(s) from state file")
                return data
        except Exception as e:
            log("WARN", f"Failed to parse state file: {e}. Starting fresh.")
    return {}

def save_state(open_positions: dict):
    try:
        os.makedirs("logs", exist_ok=True)
        tmp = f"{STATE_FILE}.tmp"
        with open(tmp, "w") as f:
            json.dump(open_positions, f, indent=2)
        os.replace(tmp, STATE_FILE)
    except Exception as e:
        log("ERR", f"Failed to save state: {e}")

# ── Balance helper ────────────────────────────────────────────────────────────
def get_real_balance() -> float:
    """Real wallet USD via TWAK; falls back to STARTING_BALANCE_USD env."""
    usd = get_portfolio_usd()
    if usd and usd > 0:
        return usd
    log("WARN", f"Could not parse wallet balance — using STARTING_BALANCE_USD=${FALLBACK_BAL}")
    return FALLBACK_BAL

# ── TWAK helpers ──────────────────────────────────────────────────────────────
def place_limit_order(from_tok: str, to_tok: str, amount: float,
                      price: float, condition: str):
    ok, output = _run([
        "automate", "add",
        "--from", from_tok, "--to", to_tok,
        "--chain", CHAIN,
        "--amount", str(amount),
        "--price", str(round(price, 2)),
        "--condition", condition,
        "--max-runs", "1",
        "--json",
    ])
    if ok:
        try:
            data = json.loads(output)
            return data.get("id")
        except Exception:
            match = re.search(r'"id":\s*"([a-f0-9\-]{36})"', output)
            return match.group(1) if match else None
    return None

def cancel_order(automation_id: str) -> bool:
    ok, _ = _run(["automate", "delete", automation_id])
    return ok

def get_active_automation_ids() -> set:
    ok, output = _run(["automate", "list", "--json"])
    if not ok:
        log("ERR", "Failed to query active automation registry")
        return set()
    try:
        data = json.loads(output)
        automations = data if isinstance(data, list) else data.get("automations", [])
        return {str(a.get("id")) for a in automations if a.get("id")}
    except Exception as e:
        log("ERR", f"Error parsing automation registry JSON: {e}")
        return set()

# ── OCO exit placement ────────────────────────────────────────────────────────
def place_oco_exits(direction: str, tp: float, sl: float) -> tuple:
    bnb_price = get_price("BNB") or 600.0
    exit_amount_bnb = round(TRADE_USDT / bnb_price, 6)

    if direction == "LONG":
        from_tok, to_tok = "BNB", "USDT"
        tp_id = place_limit_order(from_tok, to_tok, exit_amount_bnb, tp, "above")
        sl_id = place_limit_order(from_tok, to_tok, exit_amount_bnb, sl, "below")
    else:
        from_tok, to_tok = "USDT", "BNB"
        tp_id = place_limit_order(from_tok, to_tok, TRADE_USDT, tp, "below")
        sl_id = place_limit_order(from_tok, to_tok, TRADE_USDT, sl, "above")

    return tp_id, sl_id

# ── Position monitor (OCO logic) ──────────────────────────────────────────────
def monitor_positions(open_positions: dict, cb: CircuitBreaker):
    closed = []
    active_ids = get_active_automation_ids()

    for symbol, pos in open_positions.items():
        tp_id    = pos.get("tp_id")
        sl_id    = pos.get("sl_id")

        tp_alive = str(tp_id) in active_ids if tp_id else False
        sl_alive = str(sl_id) in active_ids if sl_id else False

        if tp_id and not tp_alive:
            log("CLOSE", f"{symbol}: TP hit @ {pos['tp']:.2f} 🎯")
            if sl_id and sl_alive:
                cancelled = cancel_order(sl_id)
                log("OCO", f"{symbol}: SL cancelled {'✅' if cancelled else '⚠️'}")
            cb.record_trade_close(pos["rr"] * TRADE_USDT * 0.01)
            save_log({**pos, "outcome": "WIN", "pnl_r": pos["rr"],
                      "close_time": datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")})
            closed.append(symbol)

        elif sl_id and not sl_alive:
            log("CLOSE", f"{symbol}: SL hit @ {pos['sl']:.2f} 🛑")
            if tp_id and tp_alive:
                cancelled = cancel_order(tp_id)
                log("OCO", f"{symbol}: TP cancelled {'✅' if cancelled else '⚠️'}")
            cb.record_trade_close(-TRADE_USDT * 0.01)
            save_log({**pos, "outcome": "LOSS", "pnl_r": -1.0,
                      "close_time": datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")})
            closed.append(symbol)

    for symbol in closed:
        del open_positions[symbol]

    if closed:
        save_state(open_positions)

# ── Swap params ───────────────────────────────────────────────────────────────
def get_swap_params(direction: str):
    if direction == "LONG":
        return "USDT", "BNB", TRADE_USDT
    else:
        bnb_price = get_price("BNB") or 600.0
        return "BNB", "USDT", round(TRADE_USDT / bnb_price, 6)

# ── Agent loop ────────────────────────────────────────────────────────────────
def run_agent():
    log("INFO", "LEORIX Edge Agent starting (v2 — unified SMC brain)")
    log("INFO", f"Wallet   : {WALLET_ADDRESS}")
    log("INFO", f"DRY RUN  : {DRY_RUN}")
    log("INFO", f"Trade    : ${TRADE_USDT} USDT per signal")
    log("INFO", f"Chain    : {CHAIN}")
    log("INFO", f"Min confluence: {MIN_CONFLUENCE}/5")
    log("INFO", f"Credentials loaded: PK={'YES' if os.getenv('PRIVATE_KEY') else 'NO'} | "
                f"TWAK={'YES' if os.getenv('TW_ACCESS_ID') else 'NO'} | "
                f"CMC={'YES' if CMC_API_KEY else 'NO'}")

    client = CMCClient(CMC_API_KEY)
    cb = CircuitBreaker(max_daily_loss_pct=5.0, max_open_positions=3,
                        max_risk_per_trade_pct=1.0)

    # Real wallet balance feeds the circuit breaker (5% limit on REAL money)
    real_balance = get_real_balance()
    cb.update_balance(real_balance)
    log("INFO", f"Balance  : ${real_balance:.2f} (circuit breaker base)")

    open_positions = load_state()

    log("INFO", "Agent ready. Starting scan loop...\n")

    while True:
        scan_time = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
        log("SCAN", f"─── New scan at {scan_time} ───")

        try:
            if open_positions:
                log("MONITOR", f"Checking {len(open_positions)} open position(s)...")
                monitor_positions(open_positions, cb)

            regime = client.get_market_regime()
            log("INFO", f"Market regime: {regime}")

            # Refresh balance for the breaker (handles day rollover)
            current_balance = get_real_balance()
            cb.update_balance(current_balance)

            can_trade, block_reason = cb.can_trade(current_balance)
            if not can_trade:
                log("BLOCK", f"Circuit breaker: {block_reason}")
                time.sleep(SCAN_INTERVAL)
                continue

            best_signal = None
            best_confluence = 0

            for symbol in SIGNAL_SYMBOLS:
                if symbol in open_positions:
                    log("SKIP", f"{symbol}: already in position")
                    continue
                try:
                    candles = client.get_ohlcv_historical(symbol, interval="4h", limit=100)
                    sig = generate_signal(symbol, candles, regime, min_rr=2.0)
                    signal = sig.to_dict()    # v2: smc_engine returns Signal dataclass

                    if signal["direction"] == "NO_SIGNAL":
                        reason = "; ".join(signal.get("reasons", [])) or "No signal"
                        log("SKIP", f"{symbol}: {reason}")
                        continue

                    # ── Counter-trend protection (preserved from skill.py) ──
                    if regime == "BEAR" and signal["direction"] == "LONG":
                        log("SKIP", f"{symbol}: counter-trend LONG blocked (BEAR regime)")
                        continue
                    if regime == "BULL" and signal["direction"] == "SHORT":
                        log("SKIP", f"{symbol}: counter-trend SHORT blocked (BULL regime)")
                        continue

                    if signal["confluence"] < MIN_CONFLUENCE:
                        log("SKIP", f"{symbol}: Low confluence {signal['confluence']}/5")
                        continue

                    log("SIGNAL", (
                        f"{symbol} {signal['direction']} | "
                        f"Confluence: {signal['confluence']}/5 | "
                        f"Entry: {signal['entry']:.2f} | "
                        f"SL: {signal['sl']:.2f} | "
                        f"TP: {signal['tp']:.2f} | "
                        f"RR: {signal['rr']}"
                    ))

                    if signal["confluence"] > best_confluence:
                        best_confluence = signal["confluence"]
                        best_signal = {**signal, "symbol": symbol}

                except Exception as e:
                    log("ERR", f"{symbol}: {e}")
                    continue

            if best_signal:
                symbol    = best_signal["symbol"]
                direction = best_signal["direction"]
                tp        = best_signal["tp"]
                sl        = best_signal["sl"]

                for r in best_signal["reasons"]:
                    log("REASON", f"  • {r}")

                from_tok, to_tok, amount = get_swap_params(direction)
                log("TRADE", f"Entry: {amount} {from_tok} → {to_tok}")

                quote = swap_quote(from_tok, to_tok, amount)
                log("QUOTE", f"{quote['raw'][:120]}")

                entry_log = {
                    "time": scan_time, "symbol": symbol,
                    "direction": direction,
                    "entry": best_signal["entry"],
                    "sl": sl, "tp": tp, "rr": best_signal["rr"],
                    "confluence": best_signal["confluence"],
                    "regime": regime, "dry_run": DRY_RUN,
                    "reasons": best_signal["reasons"],
                }

                if DRY_RUN:
                    log("DRY", "Signal logged, execution skipped (DRY_RUN=true)")
                    entry_log["status"] = "DRY_RUN"
                    save_log(entry_log)
                else:
                    result = swap_execute(from_tok, to_tok, amount, chain=CHAIN)
                    # v2: swap_execute now requires a tx hash for success —
                    # phantom swaps can no longer be tracked as real positions.
                    if result["success"]:
                        log("OK", f"✅ Entry executed — TX: {result['tx_hash']}")
                        cb.record_trade_open()
                        entry_log["status"] = "EXECUTED"
                        entry_log["entry_tx"] = result["tx_hash"]

                        log("OCO", f"Placing TP @ {tp:.2f} and SL @ {sl:.2f}...")
                        tp_id, sl_id = place_oco_exits(direction, tp, sl)

                        # ── Atomic Exit Guard ──
                        if not tp_id or not sl_id:
                            log("CRITICAL", "Partial exit automation placement failure! Initiating emergency rollback counter-swap.")
                            if tp_id: cancel_order(tp_id)
                            if sl_id: cancel_order(sl_id)

                            rb_from, rb_to, rb_amt = get_swap_params("SHORT" if direction == "LONG" else "LONG")
                            rb = swap_execute(rb_from, rb_to, rb_amt, chain=CHAIN)
                            if not rb["success"]:
                                log("CRITICAL", f"⚠️ ROLLBACK FAILED — MANUAL INTERVENTION REQUIRED. Naked position on {symbol}!")
                            cb.record_trade_close(0.0)   # position closed (or attempted)

                            entry_log["status"] = "REJECTED_EXIT_FAILURE"
                            entry_log["rollback_success"] = rb["success"]
                            save_log(entry_log)
                            continue

                        log("OCO", f"TP id: {tp_id}  SL id: {sl_id}")
                        entry_log["tp_id"] = tp_id
                        entry_log["sl_id"] = sl_id

                        pos = {
                            "symbol": symbol, "direction": direction,
                            "tp": tp, "sl": sl, "rr": best_signal["rr"],
                            "tp_id": tp_id, "sl_id": sl_id,
                            "entry_tx": result["tx_hash"],
                            "open_time": scan_time,
                        }
                        open_positions[symbol] = pos
                        save_state(open_positions)
                    else:
                        log("ERR", f"Entry failed — {result['raw'][:150]}")
                        entry_log["status"] = "FAILED"
                        entry_log["error"] = result["raw"][:200]

                    save_log(entry_log)
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
    log("INFO", f"DRY RUN  : {DRY_RUN}")
    log("INFO", f"Chain    : {CHAIN}")
    log("INFO", f"Credentials loaded: PK={'YES' if os.getenv('PRIVATE_KEY') else 'NO'} | "
                f"TWAK={'YES' if os.getenv('TW_ACCESS_ID') else 'NO'} | "
                f"CMC={'YES' if CMC_API_KEY else 'NO'}")
    client = CMCClient(CMC_API_KEY)
    regime = client.get_market_regime()
    log("INFO", f"Regime: {regime}")
    for symbol in SIGNAL_SYMBOLS:
        candles = client.get_ohlcv_historical(symbol, interval="4h", limit=100)
        sig = generate_signal(symbol, candles, regime, min_rr=2.0)
        signal = sig.to_dict()
        print(f"\n{symbol}: {signal['direction']} | confluence {signal.get('confluence',0)}/5")
        for r in signal.get("reasons", []):
            print(f"  • {r}")
        if signal["direction"] != "NO_SIGNAL":
            from_tok, to_tok, amount = get_swap_params(signal["direction"])
            print(f"  → Entry: {amount} {from_tok} → {to_tok}")
            print(f"  → TP: {signal['tp']:.2f}  SL: {signal['sl']:.2f}")


if __name__ == "__main__":
    if "--once" in sys.argv:
        run_once()
    else:
        run_agent()