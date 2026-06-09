"""
executor.py — TWAK execution wrapper for LEORIX Edge Track 1
Calls twak CLI for price, swap, portfolio, and automate commands.
All trades on BSC Testnet during hackathon window.
"""

import subprocess
import re
from typing import Optional


WALLET_ADDRESS = "0xb77F7280684a06fA5abbb9C168fE4C183019A64F"


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
    ok, output = _run(["wallet", "portfolio"])
    return {"success": ok, "raw": output}


def swap_quote(from_token: str, to_token: str, amount: float) -> dict:
    ok, output = _run(["swap", str(amount), from_token, to_token, "--quote-only"])
    return {"success": ok, "raw": output}


def swap_execute(from_token: str, to_token: str, amount: float, chain: str = "smartchain-testnet") -> dict:
    ok, output = _run(["swap", str(amount), from_token, to_token, "--chain", chain], timeout=60)
    return {"success": ok, "raw": output, "tx_hash": _extract_tx_hash(output)}


def create_limit_order(from_token: str, to_token: str, amount: float, target_price: float) -> dict:
    ok, output = _run([
        "automate", "add",
        "--type", "limit",
        "--from", from_token,
        "--to", to_token,
        "--amount", str(amount),
        "--price", str(target_price),
    ], timeout=30)
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

    print("\n── Swap Quote: 10 USDT → BNB ──")
    quote = swap_quote("USDT", "BNB", 10)
    print(f"  {quote['raw']}")

    print("\n── Active Automations ──")
    automations = list_automations()
    print(f"  {automations['raw']}")