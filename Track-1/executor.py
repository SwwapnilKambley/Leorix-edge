"""
executor.py — TWAK execution wrapper for LEORIX Edge Track 1
Calls twak CLI for price, swap, portfolio, and automate commands.

v2 fixes:
  - swap_execute: success now REQUIRES a tx hash. Exit code 0 with no tx hash
    previously reported success on phantom/failed swaps — the agent would then
    place OCO exits against a position that didn't exist.
  - get_portfolio_usd(): parses a real USD balance from `twak wallet portfolio`
    (tries --json first, falls back to regex). Returns None if unparseable so
    the caller can fall back to STARTING_BALANCE_USD env.
  - create_limit_order now accepts a chain param (was silently chain-less,
    diverging from agent.py's own chain-aware place_limit_order).
"""

import os
import subprocess
import json
import re
from typing import Optional


WALLET_ADDRESS = os.getenv("WALLET_ADDRESS", "0xb77F7280684a06fA5abbb9C168fE4C183019A64F").strip('"')


def _run(args: list, timeout: int = 30) -> tuple:
    """Run a twak CLI command. Returns (success, output)."""
    try:
        result = subprocess.run(
            ["twak"] + args,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        output = result.stdout.strip() or result.stderr.strip()
        return result.returncode == 0, output
    except subprocess.TimeoutExpired:
        return False, "Command timed out"
    except FileNotFoundError:
        return False, "twak not found"
    except Exception as e:
        return False, str(e)


def get_price(symbol: str) -> Optional[float]:
    ok, output = _run(["price", symbol])
    if not ok:
        return None
    match = re.search(r"\$([0-9,]+\.?[0-9]*)", output)
    return float(match.group(1).replace(",", "")) if match else None


def get_portfolio() -> dict:
    """Raw portfolio output (legacy)."""
    ok, output = _run(["wallet", "portfolio"])
    return {"success": ok, "raw": output}


def get_portfolio_usd() -> Optional[float]:
    """
    Best-effort real USD balance of the wallet.

    Strategy:
      1. Try `twak wallet portfolio --json` and look for a total USD field.
      2. Fall back to plain output: look for a 'total' line with a $ amount.
      3. Last resort: sum token balances we can identify (BNB×price + USDT).
    Returns None if nothing parseable — caller falls back to env config.
    """
    # 1) JSON attempt
    ok, output = _run(["wallet", "portfolio", "--json"])
    if ok:
        try:
            data = json.loads(output)
            # Common shapes: {"total_usd": X} or {"totalValueUsd": X} or list of tokens
            for key in ("total_usd", "totalUsd", "totalValueUsd", "total_value_usd", "total"):
                if isinstance(data, dict) and key in data:
                    return float(data[key])
            if isinstance(data, dict) and "tokens" in data:
                total = 0.0
                for t in data["tokens"]:
                    v = t.get("value_usd") or t.get("valueUsd") or t.get("usd_value")
                    if v is not None:
                        total += float(v)
                if total > 0:
                    return round(total, 2)
        except Exception:
            pass

    # 2) Plain-text 'total' line
    ok, output = _run(["wallet", "portfolio"])
    if ok:
        for line in output.splitlines():
            if "total" in line.lower():
                m = re.search(r"\$([0-9,]+\.?[0-9]*)", line)
                if m:
                    return float(m.group(1).replace(",", ""))

        # 3) Reconstruct: BNB balance × price + USDT balance
        try:
            bnb_bal = None
            usdt_bal = 0.0
            for line in output.splitlines():
                lu = line.upper()
                m = re.search(r"([0-9]+\.?[0-9]*)", line)
                if not m:
                    continue
                amt = float(m.group(1))
                if "BNB" in lu and bnb_bal is None:
                    bnb_bal = amt
                elif "USDT" in lu:
                    usdt_bal = amt
            if bnb_bal is not None:
                price = get_price("BNB") or 0.0
                if price > 0:
                    return round(bnb_bal * price + usdt_bal, 2)
        except Exception:
            pass

    return None


def swap_quote(from_token: str, to_token: str, amount: float) -> dict:
    ok, output = _run(["swap", str(amount), from_token, to_token, "--quote-only"])
    return {"success": ok, "raw": output}


def swap_execute(from_token: str, to_token: str, amount: float, chain: str = "smartchain-testnet") -> dict:
    """
    Execute a swap.

    v2: success requires BOTH a clean exit code AND a tx hash in the output.
    No tx hash = no confirmed on-chain trade = failure, regardless of exit code.
    This prevents the agent from tracking phantom positions.
    """
    ok, output = _run(["swap", str(amount), from_token, to_token, "--chain", chain], timeout=60)
    tx_hash = _extract_tx_hash(output)

    success = bool(ok and tx_hash)
    if ok and not tx_hash:
        output = f"[NO TX HASH — treated as FAILURE] {output}"

    return {"success": success, "raw": output, "tx_hash": tx_hash}


def create_limit_order(from_token: str, to_token: str, amount: float,
                       target_price: float, chain: str = None) -> dict:
    """v2: chain-aware (was silently chain-less)."""
    args = [
        "automate", "add",
        "--type", "limit",
        "--from", from_token,
        "--to", to_token,
        "--amount", str(amount),
        "--price", str(target_price),
    ]
    if chain:
        args += ["--chain", chain]
    ok, output = _run(args, timeout=30)
    return {"success": ok, "raw": output}


def list_automations() -> dict:
    ok, output = _run(["automate", "list"])
    return {"success": ok, "raw": output}


def delete_automation(automation_id: str) -> dict:
    ok, output = _run(["automate", "delete", automation_id])
    return {"success": ok, "raw": output}


def _extract_tx_hash(output: str) -> Optional[str]:
    match = re.search(r"0x[a-fA-F0-9]{64}", output)
    return match.group(0) if match else None


# ── Quick test ────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    print("── Wallet Address ──")
    print(f"  {WALLET_ADDRESS}")

    print("\n── Live Prices ──")
    for symbol in ["BTC", "ETH", "BNB"]:
        price = get_price(symbol)
        print(f"  {symbol}: ${price:,.2f}" if price else f"  {symbol}: error")

    print("\n── Portfolio USD (parsed) ──")
    usd = get_portfolio_usd()
    print(f"  ${usd:,.2f}" if usd else "  Could not parse — agent will use STARTING_BALANCE_USD env")

    print("\n── Swap Quote: 10 USDT → BNB ──")
    quote = swap_quote("USDT", "BNB", 10)
    print(f"  {quote['raw'][:200]}")