"""
smc_engine.py — Smart Money Concepts engine for LEORIX Edge (Track 2)
Detects: BOS, Liquidity Sweep, Order Block, Confluence Score
Outputs: tradeable signal with entry, SL, TP, RR

v2 fixes:
  - detect_order_block tightened: the OB must be RELEVANT — its midpoint must
    sit within 3×ATR of current price. Previously any opposing candle in the
    last 10 scored a free +1 confluence, inflating signal quality.
  - Signal.to_dict() added so the live agent (dict-based) can consume the
    dataclass without restructuring.
"""

from dataclasses import dataclass, field, asdict
from typing import Optional


@dataclass
class Signal:
    symbol: str
    direction: str          # LONG | SHORT | NO_SIGNAL
    entry: float
    sl: float
    tp: float
    rr: float
    confluence: int         # 0-5
    reasons: list
    regime: str             # BULL | BEAR | NEUTRAL
    ob_high: float = 0.0
    ob_low: float = 0.0

    def to_dict(self) -> dict:
        """Dict shape consumed by the live agent (Track 1)."""
        return asdict(self)


def _is_bullish(c: dict) -> bool:
    return c["close"] > c["open"]

def _is_bearish(c: dict) -> bool:
    return c["close"] < c["open"]

def _avg_volume(candles: list, period: int = 20) -> float:
    vols = [c["volume"] for c in candles[-period:]]
    return sum(vols) / len(vols) if vols else 0


def detect_bos(candles: list) -> Optional[str]:
    """
    Break of Structure — checks last 3 candles against a 5-candle swing window.
    Returns the most recent BOS direction found.
    """
    if len(candles) < 8:
        return None

    for i in range(1, 4):
        current = candles[-i]
        lookback = candles[-(i + 6):-(i)]
        if len(lookback) < 5:
            continue

        swing_high = max(c["high"] for c in lookback)
        swing_low = min(c["low"] for c in lookback)

        if current["close"] > swing_high:
            return "BULLISH"
        if current["close"] < swing_low:
            return "BEARISH"

    return None


def detect_liquidity_sweep(candles: list) -> Optional[str]:
    """Liquidity sweep — wick beyond previous swing then close back inside."""
    if len(candles) < 6:
        return None

    c0 = candles[-1]
    lookback = candles[-6:-1]

    prev_low = min(c["low"] for c in lookback)
    prev_high = max(c["high"] for c in lookback)

    if c0["low"] < prev_low and c0["close"] > prev_low:
        return "BULLISH_SWEEP"

    if c0["high"] > prev_high and c0["close"] < prev_high:
        return "BEARISH_SWEEP"

    return None


def detect_order_block(candles: list, direction: str, atr: float = 0.0) -> Optional[dict]:
    """
    Order Block — last opposing candle before the current move.

    v2 TIGHTENED: the OB only counts if its midpoint is within 3×ATR of the
    current price (or 5% if ATR unavailable). A red candle 10 candles ago and
    8% away is not a tradeable order block — it was previously scoring a free
    confluence point on nearly every signal.
    """
    if len(candles) < 5:
        return None

    current_price = candles[-1]["close"]
    max_dist = (atr * 3.0) if atr > 0 else (current_price * 0.05)

    lookback = candles[-10:-1]

    if direction == "LONG":
        for c in reversed(lookback):
            if _is_bearish(c):
                ob_mid = (c["open"] + c["close"]) / 2
                if abs(current_price - ob_mid) <= max_dist:
                    return {
                        "ob_high": c["open"],
                        "ob_low": c["close"],
                        "ob_mid": ob_mid,
                    }
                return None  # nearest opposing candle too far — no valid OB

    elif direction == "SHORT":
        for c in reversed(lookback):
            if _is_bullish(c):
                ob_mid = (c["open"] + c["close"]) / 2
                if abs(current_price - ob_mid) <= max_dist:
                    return {
                        "ob_high": c["close"],
                        "ob_low": c["open"],
                        "ob_mid": ob_mid,
                    }
                return None

    return None


def detect_volume_spike(candles: list, multiplier: float = 1.5) -> bool:
    """Volume on current candle > multiplier × 20-period average."""
    if len(candles) < 21:
        return False
    avg = _avg_volume(candles[:-1], period=20)
    return candles[-1]["volume"] > avg * multiplier


def calculate_atr(candles: list, period: int = 14) -> float:
    """Average True Range for SL sizing."""
    if len(candles) < period + 1:
        return 0.0
    trs = []
    for i in range(1, period + 1):
        c = candles[-i]
        prev_close = candles[-(i + 1)]["close"]
        tr = max(
            c["high"] - c["low"],
            abs(c["high"] - prev_close),
            abs(c["low"] - prev_close),
        )
        trs.append(tr)
    return sum(trs) / len(trs)


def generate_signal(symbol: str, candles: list, regime: str, min_rr: float = 2.0) -> Signal:
    """
    Main signal generator. Runs all SMC checks and returns a Signal.
    Confluence score 0-5:
      +1 BOS confirmed
      +1 Liquidity sweep
      +1 Order Block found (must be within 3×ATR of price — v2)
      +1 Volume spike
      +1 Regime aligned
    """
    reasons = []
    confluence = 0
    direction = "NO_SIGNAL"

    if len(candles) < 60:
        return Signal(
            symbol=symbol, direction="NO_SIGNAL",
            entry=0, sl=0, tp=0, rr=0,
            confluence=0, reasons=["Insufficient data"], regime=regime
        )

    current_price = candles[-1]["close"]
    atr = calculate_atr(candles)

    # ── 1. BOS ────────────────────────────────────────────────────────────────
    bos = detect_bos(candles)
    if bos == "BULLISH":
        direction = "LONG"
        confluence += 1
        reasons.append("BOS: Close broke above 5-candle swing high")
    elif bos == "BEARISH":
        direction = "SHORT"
        confluence += 1
        reasons.append("BOS: Close broke below 5-candle swing low")
    else:
        return Signal(
            symbol=symbol, direction="NO_SIGNAL",
            entry=0, sl=0, tp=0, rr=0,
            confluence=0, reasons=["No BOS detected — price ranging"], regime=regime
        )

    # ── 2. Liquidity Sweep ───────────────────────────────────────────────────
    sweep = detect_liquidity_sweep(candles)
    if (direction == "LONG" and sweep == "BULLISH_SWEEP") or \
       (direction == "SHORT" and sweep == "BEARISH_SWEEP"):
        confluence += 1
        reasons.append(f"Liquidity sweep confirmed: {sweep}")

    # ── 3. Order Block (v2: proximity-validated) ─────────────────────────────
    ob = detect_order_block(candles, direction, atr=atr)
    ob_high, ob_low = 0.0, 0.0
    if ob:
        confluence += 1
        ob_high = ob["ob_high"]
        ob_low = ob["ob_low"]
        reasons.append(f"Order Block (within 3×ATR): {ob_low:.2f} – {ob_high:.2f}")

    # ── 4. Volume Spike ──────────────────────────────────────────────────────
    if detect_volume_spike(candles):
        confluence += 1
        reasons.append("Volume spike: >1.5× 20-period average")

    # ── 5. Regime Alignment ──────────────────────────────────────────────────
    if (direction == "LONG" and regime == "BULL") or \
       (direction == "SHORT" and regime == "BEAR"):
        confluence += 1
        reasons.append(f"Regime aligned: {regime}")

    # ── SL / TP / RR ─────────────────────────────────────────────────────────
    sl_buffer = atr * 2.5

    if direction == "LONG":
        entry = current_price
        sl = entry - sl_buffer
        tp = entry + (sl_buffer * min_rr)
    else:
        entry = current_price
        sl = entry + sl_buffer
        tp = entry - (sl_buffer * min_rr)

    risk = abs(entry - sl)
    reward = abs(tp - entry)
    rr = round(reward / risk, 2) if risk > 0 else 0

    return Signal(
        symbol=symbol,
        direction=direction,
        entry=round(entry, 4),
        sl=round(sl, 4),
        tp=round(tp, 4),
        rr=rr,
        confluence=confluence,
        reasons=reasons,
        regime=regime,
        ob_high=round(ob_high, 4),
        ob_low=round(ob_low, 4),
    )


# ── Quick test ────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    import os, sys
    sys.path.insert(0, ".")
    from cmc_client import CMCClient

    API_KEY = os.getenv("CMC_API_KEY", "")
    client = CMCClient(API_KEY)
    regime = client.get_market_regime()

    print(f"Market Regime: {regime}\n")

    for symbol in ["BTC", "ETH", "BNB"]:
        print(f"\n{'─'*50}")
        print(f"  {symbol} — 4H Signal Analysis")
        print(f"{'─'*50}")

        candles = client.get_ohlcv_historical(symbol, interval="4h", limit=100)
        signal = generate_signal(symbol, candles, regime)

        print(f"  Direction  : {signal.direction}")
        print(f"  Entry      : {signal.entry}")
        print(f"  SL         : {signal.sl}")
        print(f"  TP         : {signal.tp}")
        print(f"  RR         : {signal.rr}R")
        print(f"  Confluence : {signal.confluence}/5")
        print(f"  Regime     : {signal.regime}")
        print(f"  Reasons    :")
        for r in signal.reasons:
            print(f"    • {r}")