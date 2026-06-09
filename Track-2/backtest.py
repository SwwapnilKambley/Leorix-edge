"""
backtest.py — LEORIX Edge SMC Strategy Backtester (Track 2)
Runs smc_engine over historical 4H candles and logs every signal.
Outputs: trade log + performance report
"""

import os, sys, json
from datetime import datetime
sys.path.insert(0, ".")

from cmc_client import CMCClient
from smc_engine import generate_signal


def timestamp_to_date(ts_ms: int) -> str:
    return datetime.utcfromtimestamp(ts_ms / 1000).strftime("%Y-%m-%d %H:%M")


def run_backtest(
    symbol: str,
    candles: list,
    regime: str,
    min_confluence: int = 3,
    min_rr: float = 2.0,
    lookback_window: int = 60,
) -> list:
    trades = []
    last_signal_candle = -1   # deduplicate — one trade per unique entry candle

    for i in range(lookback_window, len(candles) - 11):
        # Skip if we already took a trade at this candle
        if i <= last_signal_candle:
            continue

        window = candles[:i + 1]
        signal = generate_signal(symbol, window, regime, min_rr=min_rr)

        if signal.direction == "NO_SIGNAL":
            continue
        if signal.confluence < min_confluence:
            continue

        # Regime filter — only trade with the trend
        if regime == "BEAR" and signal.direction == "LONG":
            continue
        if regime == "BULL" and signal.direction == "SHORT":
            continue

        last_signal_candle = i  # lock out next candles until this trade closes

        entry_candle = candles[i]
        entry_time = timestamp_to_date(entry_candle["time"])

        # Simulate outcome over next 10 candles
        future = candles[i + 1: i + 11]
        outcome = "OPEN"
        exit_price = None
        exit_time = None

        for fc in future:
            if signal.direction == "LONG":
                if fc["low"] <= signal.sl:
                    outcome = "LOSS"
                    exit_price = signal.sl
                    exit_time = timestamp_to_date(fc["time"])
                    break
                if fc["high"] >= signal.tp:
                    outcome = "WIN"
                    exit_price = signal.tp
                    exit_time = timestamp_to_date(fc["time"])
                    break
            elif signal.direction == "SHORT":
                if fc["high"] >= signal.sl:
                    outcome = "LOSS"
                    exit_price = signal.sl
                    exit_time = timestamp_to_date(fc["time"])
                    break
                if fc["low"] <= signal.tp:
                    outcome = "WIN"
                    exit_price = signal.tp
                    exit_time = timestamp_to_date(fc["time"])
                    break

        if outcome == "OPEN":
            continue

        # Advance past this trade's future window
        last_signal_candle = i + 11

        pnl_r = signal.rr if outcome == "WIN" else -1.0

        trades.append({
            "symbol": symbol,
            "direction": signal.direction,
            "entry_time": entry_time,
            "exit_time": exit_time,
            "entry": signal.entry,
            "sl": signal.sl,
            "tp": signal.tp,
            "rr": signal.rr,
            "confluence": signal.confluence,
            "outcome": outcome,
            "pnl_r": pnl_r,
            "reasons": signal.reasons,
        })

    return trades


def performance_report(trades: list, symbol: str) -> dict:
    if not trades:
        return {"symbol": symbol, "total_trades": 0}

    wins = [t for t in trades if t["outcome"] == "WIN"]
    losses = [t for t in trades if t["outcome"] == "LOSS"]
    total = len(trades)
    win_rate = round(len(wins) / total * 100, 1)
    total_r = round(sum(t["pnl_r"] for t in trades), 2)
    avg_rr = round(sum(t["rr"] for t in trades) / total, 2)

    cumulative = 0
    peak = 0
    max_dd = 0
    for t in trades:
        cumulative += t["pnl_r"]
        if cumulative > peak:
            peak = cumulative
        dd = peak - cumulative
        if dd > max_dd:
            max_dd = dd

    return {
        "symbol": symbol,
        "total_trades": total,
        "wins": len(wins),
        "losses": len(losses),
        "win_rate_pct": win_rate,
        "total_pnl_r": total_r,
        "avg_rr": avg_rr,
        "max_drawdown_r": round(max_dd, 2),
        "expectancy_r": round(total_r / total, 2),
    }


def run_full_backtest():
    API_KEY = os.getenv("CMC_API_KEY", "2d25132563a8497893597470798e861e")
    client = CMCClient(API_KEY)
    regime = client.get_market_regime()

    print(f"Market Regime : {regime}")
    print(f"Strategy      : SMC — BOS + Liquidity Sweep + Order Block")
    print(f"Timeframe     : 4H")
    print(f"Candles       : 500 (≈83 days)")
    print(f"Min Confluence: 3/5")
    print(f"Min RR        : 2.0")
    print(f"Regime Filter : ON (no counter-trend trades)")
    print("=" * 60)

    all_trades = []
    all_reports = []

    for symbol in ["BTC", "ETH", "BNB"]:
        print(f"\nRunning backtest for {symbol}...")
        candles = client.get_ohlcv_historical(symbol, interval="4h", limit=500)
        trades = run_backtest(symbol, candles, regime, min_confluence=3, min_rr=2.0)
        report = performance_report(trades, symbol)
        all_trades.extend(trades)
        all_reports.append(report)

        print(f"  Trades      : {report['total_trades']}")
        if report["total_trades"] > 0:
            print(f"  Win Rate    : {report['win_rate_pct']}%")
            print(f"  Total PnL   : {report['total_pnl_r']}R")
            print(f"  Avg RR      : {report['avg_rr']}")
            print(f"  Max Drawdown: {report['max_drawdown_r']}R")
            print(f"  Expectancy  : {report['expectancy_r']}R per trade")

    print("\n" + "=" * 60)
    print("COMBINED PORTFOLIO REPORT")
    print("=" * 60)
    if all_trades:
        combined = performance_report(all_trades, "ALL")
        for k, v in combined.items():
            print(f"  {k:<22}: {v}")

    os.makedirs("results", exist_ok=True)
    with open("results/trades.json", "w") as f:
        json.dump(all_trades, f, indent=2)
    with open("results/report.json", "w") as f:
        json.dump({
            "regime": regime,
            "per_symbol": all_reports,
            "combined": performance_report(all_trades, "ALL"),
        }, f, indent=2)

    print(f"\nResults saved to results/")

    if all_trades:
        print("\nLast 5 trades:")
        for t in all_trades[-5:]:
            print(f"  [{t['outcome']}] {t['symbol']} {t['direction']} "
                  f"@ {t['entry']} → {t['exit_time']}  "
                  f"PnL: {t['pnl_r']}R  Confluence: {t['confluence']}/5")


if __name__ == "__main__":
    run_full_backtest()