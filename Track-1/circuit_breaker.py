"""
circuit_breaker.py — Risk management for LEORIX Edge Track 1
Guards: daily drawdown cap, max open positions, per-trade risk limit

v2 fixes:
  - State PERSISTS to disk (logs/cb_state.json). Previously a restart wiped
    the daily loss counter and any tripped state — the kill switch had amnesia.
  - starting_balance is now set from the REAL wallet balance (passed in by
    the agent), refreshed each new day. Previously hardcoded to a fake $50.
"""

import os
import json
from dataclasses import dataclass, field
from datetime import date


CB_STATE_FILE = "logs/cb_state.json"


@dataclass
class CircuitBreaker:
    max_daily_loss_pct: float = 5.0      # kill switch if down 5% on the day
    max_open_positions: int = 3           # max simultaneous trades
    max_risk_per_trade_pct: float = 1.0  # max 1% account per trade
    starting_balance: float = 0.0

    # Runtime state (persisted)
    daily_pnl: float = 0.0
    open_positions: int = 0
    trades_today: int = 0
    tripped: bool = False
    trip_reason: str = ""
    last_reset: date = field(default_factory=date.today)

    def __post_init__(self):
        self._load_state()

    # ── Persistence ───────────────────────────────────────────────────────────
    def _load_state(self):
        """Restore daily counters + trip state from disk (survives restarts)."""
        if not os.path.exists(CB_STATE_FILE):
            return
        try:
            with open(CB_STATE_FILE, "r") as f:
                s = json.load(f)
            saved_date = date.fromisoformat(s.get("last_reset", str(date.today())))
            if saved_date == date.today():
                # Same day — restore everything including trip state
                self.daily_pnl = float(s.get("daily_pnl", 0.0))
                self.open_positions = int(s.get("open_positions", 0))
                self.trades_today = int(s.get("trades_today", 0))
                self.tripped = bool(s.get("tripped", False))
                self.trip_reason = s.get("trip_reason", "")
                self.starting_balance = float(s.get("starting_balance", 0.0))
                self.last_reset = saved_date
            # Older date — fresh day, defaults stand (counters reset naturally)
        except Exception as e:
            print(f"[CB WARN] Failed to load circuit breaker state: {e}")

    def _save_state(self):
        try:
            os.makedirs(os.path.dirname(CB_STATE_FILE), exist_ok=True)
            tmp = f"{CB_STATE_FILE}.tmp"
            with open(tmp, "w") as f:
                json.dump({
                    "daily_pnl": self.daily_pnl,
                    "open_positions": self.open_positions,
                    "trades_today": self.trades_today,
                    "tripped": self.tripped,
                    "trip_reason": self.trip_reason,
                    "starting_balance": self.starting_balance,
                    "last_reset": self.last_reset.isoformat(),
                }, f, indent=2)
            os.replace(tmp, CB_STATE_FILE)
        except Exception as e:
            print(f"[CB ERR] Failed to save circuit breaker state: {e}")

    # ── Daily lifecycle ───────────────────────────────────────────────────────
    def reset_daily(self):
        """Reset daily counters — called automatically on day change."""
        today = date.today()
        if self.last_reset < today:
            self.daily_pnl = 0.0
            self.trades_today = 0
            self.tripped = False
            self.trip_reason = ""
            self.last_reset = today
            # starting_balance refreshed by next update_balance() call
            self.starting_balance = 0.0
            self._save_state()

    def update_balance(self, balance: float):
        """
        Call with the REAL wallet balance. Sets the day's starting balance
        (the base for the 5% loss limit) once per day.
        """
        self.reset_daily()
        if self.starting_balance <= 0.0 and balance > 0:
            self.starting_balance = balance
            self._save_state()

    # ── Trade lifecycle ───────────────────────────────────────────────────────
    def record_trade_open(self):
        self.open_positions += 1
        self.trades_today += 1
        self._save_state()

    def record_trade_close(self, pnl_usd: float):
        self.open_positions = max(0, self.open_positions - 1)
        self.daily_pnl += pnl_usd
        self._check_drawdown()
        self._save_state()

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

    def can_trade(self, balance: float) -> tuple:
        """Returns (allowed, reason). Call before every trade attempt."""
        self.reset_daily()

        if self.tripped:
            return False, f"Circuit breaker tripped: {self.trip_reason}"

        if self.open_positions >= self.max_open_positions:
            return False, f"Max open positions reached ({self.max_open_positions})"

        if balance <= 0:
            return False, "Zero or negative balance"

        return True, "OK"

    def position_size_usd(self, balance: float, sl_pct: float) -> float:
        """Risk-based position sizing: returns position size in USD."""
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
    import json as _json

    # Clean slate for test
    if os.path.exists(CB_STATE_FILE):
        os.remove(CB_STATE_FILE)

    cb = CircuitBreaker(max_daily_loss_pct=5.0, max_open_positions=3,
                        max_risk_per_trade_pct=1.0)
    balance = 1000.0
    cb.update_balance(balance)

    print("── Initial ──")
    print(_json.dumps(cb.status(), indent=2))

    print("\n── 3 losses of $20 ──")
    for _ in range(3):
        cb.record_trade_open()
        cb.record_trade_close(-20.0)
    print(_json.dumps(cb.status(), indent=2))
    ok, reason = cb.can_trade(balance)
    print(f"  Can trade? {ok} — {reason}")

    print("\n── One more $30 loss (total -9%) → should trip ──")
    cb.record_trade_open()
    cb.record_trade_close(-30.0)
    ok, reason = cb.can_trade(balance)
    print(f"  Can trade? {ok} — {reason}")

    print("\n── SIMULATE RESTART (new instance, same day) ──")
    cb2 = CircuitBreaker()
    ok, reason = cb2.can_trade(balance)
    print(f"  Can trade after restart? {ok} — {reason}")
    print("  ✅ Trip state SURVIVED restart" if not ok else "  ❌ BUG: trip forgotten")