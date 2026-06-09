"""
skill.py — LEORIX Edge CMC Skill (Track 2)
Strategy: EMA Trend + SMC Confluence
- EMA 20/50 crossover defines trend direction
- BOS confirms structure break
- Liquidity sweep + OB as confluence boosters
- Regime filter via CMC global metrics
Outputs: backtestable strategy spec + trade log
"""

import os, sys, json
from datetime import datetime
sys.path.insert(0, ".")
from cmc_client import CMCClient


# ── Indicators ────────────────────────────────────────────────────────────────

def ema(candles: list, period: int) -> float:
    """Exponential Moving Average of close prices."""
    closes = [c["close"] for c in candles]
    k = 2 / (period + 1)
    val = closes[0]
    for price in closes[1:]:
        val = price * k + val * (1 - k)
    return val


def calculate_atr(candles: list, period: int = 14) -> float:
    if len(candles) < period + 1:
        return 0.0
    trs = []
    for i in range(1, period + 1):
        c = candles[-i]
        prev_close = candles[-(i + 1)]["close"]
        tr = max(c["high"] - c["low"],
                 abs(c["high"] - prev_close),
                 abs(c["low"] - prev_close))
        trs.append(tr)
    return sum(trs) / len(trs)


def avg_volume(candles: list, period: int = 20) -> float:
    return sum(c["volume"] for c in candles[-period:]) / period


# ── SMC Detectors ─────────────────────────────────────────────────────────────

def detect_bos(candles: list, direction: str) -> bool:
    """BOS: close breaks 5-candle swing high (LONG) or swing low (SHORT)."""
    if len(candles) < 7:
        return False
    current = candles[-1]
    lookback = candles[-6:-1]
    if direction == "LONG":
        return current["close"] > max(c["high"] for c in lookback)
    else:
        return current["close"] < min(c["low"] for c in lookback)


def detect_sweep(candles: list, direction: str) -> bool:
    """Liquidity sweep: wick beyond prev extreme, close back inside."""
    if len(candles) < 6:
        return False
    c0 = candles[-1]
    lookback = candles[-6:-1]
    if direction == "LONG":
        prev_low = min(c["low"] for c in lookback)
        return c0["low"] < prev_low and c0["close"] > prev_low
    else:
        prev_high = max(c["high"] for c in lookback)
        return c0["high"] > prev_high and c0["close"] < prev_high


def detect_ob(candles: list, direction: str) -> bool:
    """Order Block: last opposing candle exists before current move."""
    lookback = candles[-8:-1]
    if direction == "LONG":
        return any(c["close"] < c["open"] for c in lookback)
    else:
        return any(c["close"] > c["open"] for c in lookback)


# ── Signal Generator ──────────────────────────────────────────────────────────

def generate_signal(symbol: str, candles: list, regime: str, min_rr: float = 2.0) -> dict:
    if len(candles) < 60:
        return {"direction": "NO_SIGNAL", "reason": "Insufficient data"}

    e20 = ema(candles, 20)
    e50 = ema(candles, 50)
    atr = calculate_atr(candles)
    current_price = candles[-1]["close"]
    vol_spike = candles[-1]["volume"] > avg_volume(candles) * 1.3

    # EMA trend direction
    if e20 > e50:
        trend = "LONG"
    elif e20 < e50:
        trend = "SHORT"
    else:
        return {"direction": "NO_SIGNAL", "reason": "EMA flat — no trend"}

    # Regime filter
    if regime == "BEAR" and trend == "LONG":
        return {"direction": "NO_SIGNAL", "reason": "Counter-trend blocked (BEAR regime)"}
    if regime == "BULL" and trend == "SHORT":
        return {"direction": "NO_SIGNAL", "reason": "Counter-trend blocked (BULL regime)"}

    # Confluence scoring
    confluence = 0
    reasons = []

    if detect_bos(candles, trend):
        confluence += 2
        reasons.append("BOS confirmed")

    if detect_sweep(candles, trend):
        confluence += 1
        reasons.append("Liquidity sweep")

    if detect_ob(candles, trend):
        confluence += 1
        reasons.append("Order Block present")

    if vol_spike:
        confluence += 1
        reasons.append("Volume spike")

    reasons.insert(0, f"EMA trend: {trend} (EMA20={e20:.2f} / EMA50={e50:.2f})")
    reasons.append(f"Regime: {regime}")

    # Minimum confluence to trade
    if confluence < 2:
        return {"direction": "NO_SIGNAL", "reason": f"Low confluence ({confluence}/5)"}

    # Entry / SL / TP
    sl_buffer = atr * 2.0
    if trend == "LONG":
        entry = current_price
        sl = entry - sl_buffer
        tp = entry + sl_buffer * min_rr
    else:
        entry = current_price
        sl = entry + sl_buffer
        tp = entry - sl_buffer * min_rr

    rr = round(abs(tp - entry) / abs(entry - sl), 2)

    return {
        "symbol": symbol,
        "direction": trend,
        "entry": round(entry, 4),
        "sl": round(sl, 4),
        "tp": round(tp, 4),
        "rr": rr,
        "confluence": confluence,
        "ema20": round(e20, 2),
        "ema50": round(e50, 2),
        "atr": round(atr, 4),
        "regime": regime,
        "reasons": reasons,
    }


# ── Backtester ────────────────────────────────────────────────────────────────

def run_backtest(symbol: str, candles: list, regime: str) -> list:
    trades = []
    i = 60
    while i < len(candles) - 20:
        window = candles[:i + 1]
        sig = generate_signal(symbol, window, regime)

        if sig["direction"] == "NO_SIGNAL":
            i += 1
            continue

        future = candles[i + 1: i + 21]
        outcome = "OPEN"
        exit_time = None

        for fc in future:
            if sig["direction"] == "LONG":
                if fc["low"] <= sig["sl"]:
                    outcome, exit_time = "LOSS", fc["time"]
                    break
                if fc["high"] >= sig["tp"]:
                    outcome, exit_time = "WIN", fc["time"]
                    break
            else:
                if fc["high"] >= sig["sl"]:
                    outcome, exit_time = "LOSS", fc["time"]
                    break
                if fc["low"] <= sig["tp"]:
                    outcome, exit_time = "WIN", fc["time"]
                    break

        if outcome == "OPEN":
            i += 1
            continue

        pnl_r = sig["rr"] if outcome == "WIN" else -1.0
        trades.append({
            "symbol": symbol,
            "direction": sig["direction"],
            "entry_time": datetime.utcfromtimestamp(candles[i]["time"] / 1000).strftime("%Y-%m-%d %H:%M"),
            "exit_time": datetime.utcfromtimestamp(exit_time / 1000).strftime("%Y-%m-%d %H:%M"),
            "entry": sig["entry"],
            "sl": sig["sl"],
            "tp": sig["tp"],
            "rr": sig["rr"],
            "confluence": sig["confluence"],
            "outcome": outcome,
            "pnl_r": pnl_r,
            "reasons": sig["reasons"],
        })

        # Advance past this trade
        i += 21

    return trades


def performance_report(trades: list, symbol: str) -> dict:
    if not trades:
        return {"symbol": symbol, "total_trades": 0}
    wins = [t for t in trades if t["outcome"] == "WIN"]
    total = len(trades)
    total_r = round(sum(t["pnl_r"] for t in trades), 2)
    cumulative, peak, max_dd = 0, 0, 0
    for t in trades:
        cumulative += t["pnl_r"]
        peak = max(peak, cumulative)
        max_dd = max(max_dd, peak - cumulative)
    return {
        "symbol": symbol,
        "total_trades": total,
        "wins": len(wins),
        "losses": total - len(wins),
        "win_rate_pct": round(len(wins) / total * 100, 1),
        "total_pnl_r": total_r,
        "avg_rr": round(sum(t["rr"] for t in trades) / total, 2),
        "max_drawdown_r": round(max_dd, 2),
        "expectancy_r": round(total_r / total, 2),
    }


# ── Main ──────────────────────────────────────────────────────────────────────

def run():
    API_KEY = os.getenv("CMC_API_KEY", "2d25132563a8497893597470798e861e")
    client = CMCClient(API_KEY)
    regime = client.get_market_regime()

    print(f"LEORIX Edge — SMC Momentum Strategy Skill")
    print(f"Regime   : {regime}")
    print(f"Strategy : EMA 20/50 Trend + BOS + Liquidity Sweep + OB")
    print(f"Timeframe: 4H  |  Window: 500 candles (~83 days)")
    print("=" * 60)

    all_trades = []
    reports = []

    for symbol in ["BTC", "ETH", "BNB"]:
        candles = client.get_ohlcv_historical(symbol, interval="4h", limit=500)
        trades = run_backtest(symbol, candles, regime)
        report = performance_report(trades, symbol)
        all_trades.extend(trades)
        reports.append(report)

        print(f"\n{symbol}")
        if report["total_trades"] == 0:
            print("  No trades generated")
        else:
            print(f"  Trades    : {report['total_trades']}")
            print(f"  Win Rate  : {report['win_rate_pct']}%")
            print(f"  Total PnL : {report['total_pnl_r']}R")
            print(f"  Expectancy: {report['expectancy_r']}R/trade")
            print(f"  Max DD    : {report['max_drawdown_r']}R")

    combined = performance_report(all_trades, "ALL")
    print(f"\n{'='*60}")
    print("COMBINED")
    print(f"  Trades    : {combined.get('total_trades', 0)}")
    print(f"  Win Rate  : {combined.get('win_rate_pct', 0)}%")
    print(f"  Total PnL : {combined.get('total_pnl_r', 0)}R")
    print(f"  Expectancy: {combined.get('expectancy_r', 0)}R/trade")
    print(f"  Max DD    : {combined.get('max_drawdown_r', 0)}R")

    os.makedirs("results", exist_ok=True)
    with open("results/trades.json", "w") as f:
        json.dump(all_trades, f, indent=2)
    with open("results/report.json", "w") as f:
        json.dump({"regime": regime, "per_symbol": reports, "combined": combined}, f, indent=2)

    print(f"\nSaved to results/")


if __name__ == "__main__":
    run()