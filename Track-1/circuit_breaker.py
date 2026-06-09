"""
circuit_breaker.py — Risk management for LEORIX Edge Track 1
Guards: daily drawdown cap, max open positions, per-trade risk limit
"""

from dataclasses import dataclass, field
from datetime import datetime, date
from typing import Optional


@dataclass
class CircuitBreaker:
    max_daily_loss_pct: float = 5.0      # kill switch if down 5% on the day
    max_open_positions: int = 3           # max simultaneous trades
    max_risk_per_trade_pct: float = 1.0  # max 1% account per trade
    starting_balance: float = 0.0        # set on init

    # Runtime state
    daily_pnl: float = 0.0
    open_positions: int = 0
    trades_today: int = 0
    tripped: bool = False
    trip_reason: str = ""
    last_reset: date = field(default_factory=date.today)

    def reset_daily(self):
        """Reset daily counters — call at start of each day."""
        today = date.today()
        if self.last_reset < today:
            self.daily_pnl = 0.0
            self.trades_today = 0
            self.tripped = False
            self.trip_reason = ""
            self.last_reset = today

    def update_balance(self, balance: float):
        """Call when balance changes — updates starting balance if new day."""
        self.reset_daily()
        if self.starting_balance == 0.0:
            self.starting_balance = balance

    def record_trade_open(self):
        self.open_positions += 1
        self.trades_today += 1

    def record_trade_close(self, pnl_usd: float):
        self.open_positions = max(0, self.open_positions - 1)
        self.daily_pnl += pnl_usd
        self._check_drawdown()

    def _check_drawdown(self):
        if self.starting_balance <= 0:
            return
        loss_pct = (self.daily_pnl / self.starting_balance) * 100
        if loss_pct <= -self.max_daily_loss_pct:
            self.tripped = True
            self.trip_reason = (
                f"Daily loss limit hit: {loss_pct:.2f}% "
                f"(limit: -{self.max_daily_loss_pct}%)"
            )

    def can_trade(self, balance: float) -> tuple[bool, str]:
        """
        Returns (allowed, reason).
        Call before every trade attempt.
        """
        self.reset_daily()

        if self.tripped:
            return False, f"Circuit breaker tripped: {self.trip_reason}"

        if self.open_positions >= self.max_open_positions:
            return False, f"Max open positions reached ({self.max_open_positions})"

        if balance <= 0:
            return False, "Zero or negative balance"

        return True, "OK"

    def position_size_usd(self, balance: float, sl_pct: float) -> float:
        """
        Risk-based position sizing.
        balance: current account balance in USD
        sl_pct: stop loss distance as % of entry price
        Returns: position size in USD
        """
        if sl_pct <= 0:
            return 0.0
        risk_usd = balance * (self.max_risk_per_trade_pct / 100)
        return round(risk_usd / (sl_pct / 100), 2)

    def status(self) -> dict:
        return {
            "tripped": self.tripped,
            "trip_reason": self.trip_reason,
            "daily_pnl_usd": round(self.daily_pnl, 4),
            "open_positions": self.open_positions,
            "trades_today": self.trades_today,
            "starting_balance": self.starting_balance,
            "daily_loss_pct": round(
                (self.daily_pnl / self.starting_balance * 100)
                if self.starting_balance > 0 else 0, 2
            ),
        }


# ── Quick test ────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    import json

    cb = CircuitBreaker(
        max_daily_loss_pct=5.0,
        max_open_positions=3,
        max_risk_per_trade_pct=1.0,
    )

    balance = 1000.0
    cb.update_balance(balance)

    print("── Initial Status ──")
    print(json.dumps(cb.status(), indent=2))

    print("\n── Can trade? ──")
    ok, reason = cb.can_trade(balance)
    print(f"  {ok} — {reason}")

    print("\n── Position size for 2% SL ──")
    size = cb.position_size_usd(balance, sl_pct=2.0)
    print(f"  ${size} position (risking 1% = $10)")

    print("\n── Simulate 3 losses ──")
    cb.record_trade_open()
    cb.record_trade_close(-20.0)
    cb.record_trade_open()
    cb.record_trade_close(-20.0)
    cb.record_trade_open()
    cb.record_trade_close(-20.0)
    print(json.dumps(cb.status(), indent=2))

    print("\n── Can trade after losses? ──")
    ok, reason = cb.can_trade(balance)
    print(f"  {ok} — {reason}")

    print("\n── Simulate hitting drawdown limit ──")
    cb.record_trade_open()
    cb.record_trade_close(-30.0)  # total -90 = -9% → should trip
    print(json.dumps(cb.status(), indent=2))

    ok, reason = cb.can_trade(balance)
    print(f"\n  Can trade? {ok} — {reason}")