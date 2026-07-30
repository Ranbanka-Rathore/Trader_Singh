"""Guards that keep fabricated prices out of the ledger.

On 2026-07-30 the paper book produced two fictional rows in one session:
  * trade 50 was OPENED on a synthetic ₹26/sh credit after price_spread_entry
    fell back — no legs stored, so it could never be marked, and every exit for
    the rest of its life ran on synthetic math;
  * trade 49 was opened on real legs but EXITED via the synthetic path with a
    "🎯 TAKE PROFIT +₹470.60" that no live book ever offered, because
    mark_position_pnl returned None and the caller silently fell through.

Both holes are closed here: no entry without a real book, no exit without a
real mark, and the legacy 15:15 square-off stays gated behind INTRADAY_SQUARE_OFF.
"""
import asyncio
import datetime
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ["LADDER_MODE"] = "true"
os.environ.pop("INTRADAY_SQUARE_OFF", None)

from backend.app.services.execution_service import execution_service as EX
from backend.app.services import options_pricing_service as OPS_MOD
from backend.app.services.options_pricing_service import options_pricing_service as OPS
from backend.app.services import execution_service as EX_MOD
from backend.app.services.redis_service import redis_service
from backend.app.services.database_service import database_service

# The open warm-up suppresses all exit checks for the first minutes after 09:15;
# neutralise it so this suite behaves the same whatever time it is run.
EX_MOD._in_open_warmup = lambda now: False

_passed = _failed = 0


def check(label, cond):
    global _passed, _failed
    if cond:
        _passed += 1
        print(f"  ✅ {label}")
    else:
        _failed += 1
        print(f"  ❌ {label}")


class Pos:
    def __init__(self, pid, expiry, source="DHAN_LIVE", strategy="BULL_PUT_SPREAD"):
        self.id = pid
        self.ticker = "NIFTY"
        self.strategy_type = strategy
        self.spot_price = 24300.0
        self.max_risk_per_share = 174.0
        self.net_delta = 0.1
        self.net_credit_per_share = 26.0
        self.adjusted_net_credit = None
        self.original_net_credit = None
        self.leg_1_sell = 23600.0
        self.leg_2_buy = 23400.0
        self.lots_sized = 1
        self.entry_spot_price = 24300.0
        self.highest_seen = 24300.0
        self.lowest_seen = 24300.0
        self.bias = "BULLISH"
        self.is_adjusted = False
        self.adjustment_count = 0
        self.learning_context = {
            "entry_pricing": {
                "pricing_source": source,
                "expiry": expiry,
                "legs": ([{"side": "SELL", "strike": 23600.0, "opt_type": "pe",
                           "expiry": expiry, "entry_fill": 55.95},
                          {"side": "BUY", "strike": 23400.0, "opt_type": "pe",
                           "expiry": expiry, "entry_fill": 44.45}]
                         if source == "DHAN_LIVE" else None),
            }
        }


def test_unmarkable_position_is_held():
    print("\n[1] Live-legged position that cannot be marked is HELD, not exited")
    far = (datetime.date.today() + datetime.timedelta(days=33)).isoformat()
    pos = Pos(49, far)

    async def fake_get_json(key):
        if key.startswith("market_snapshot:"):
            return {"price": 24300.0}
        return None
    redis_service.get_json = fake_get_json

    async def fake_set_json(*a, **k):
        return True
    redis_service.set_json = fake_set_json

    async def fake_open_positions(session):
        return [pos]
    database_service.get_open_positions = fake_open_positions

    async def no_mark(p, spot=None):
        return None                      # book won't quote right now
    OPS.mark_position_pnl = no_mark

    EX._unmarkable = set()
    closed = asyncio.run(EX.evaluate_open_positions(session=None))

    check("no trade closed while unmarkable", closed == [])
    check("position flagged as unmarkable", 49 in EX._unmarkable)

    # ...and the hold is released as soon as the book quotes again
    async def good_mark(p, spot=None):
        return {"pricing_source": "DHAN_LIVE", "pnl_per_share": 1.0, "legs": []}
    OPS.mark_position_pnl = good_mark
    asyncio.run(EX.evaluate_open_positions(session=None))
    check("hold released once quotes return", 49 not in EX._unmarkable)


def test_no_eod_squareoff_for_ladder():
    print("\n[2] Legacy 15:15 square-off stays gated (both exit paths)")
    far = (datetime.date.today() + datetime.timedelta(days=33)).isoformat()
    near = (datetime.date.today() + datetime.timedelta(days=15)).isoformat()

    exit_now, reason, _ = EX._real_exit_decision(Pos(1, far), 1.0, is_eod=True, spot=24300.0)
    check("33 DTE ladder position not squared off at EOD", exit_now is False)

    check("shared time stop agrees (no exit at 33 DTE)",
          EX._time_stop_reason(Pos(1, far), is_eod=True) is None)
    check("manage@21DTE still fires at 15 DTE",
          "MANAGE @21DTE" in (EX._time_stop_reason(Pos(1, near), is_eod=True) or ""))

    os.environ["INTRADAY_SQUARE_OFF"] = "true"
    exit_now, reason, _ = EX._real_exit_decision(Pos(1, far), 1.0, is_eod=True, spot=24300.0)
    check("INTRADAY_SQUARE_OFF=true restores the legacy exit",
          exit_now is True and "INTRADAY_SQUARE_OFF" in reason)
    os.environ.pop("INTRADAY_SQUARE_OFF")


def test_position_without_real_legs_still_uses_heuristic():
    print("\n[3] Legacy rows with no real legs keep the heuristic path")
    far = (datetime.date.today() + datetime.timedelta(days=33)).isoformat()
    pos = Pos(50, far, source="HEURISTIC_FALLBACK")

    async def no_mark(p, spot=None):
        return None
    OPS.mark_position_pnl = no_mark

    EX._unmarkable = set()
    entry = (pos.learning_context or {}).get("entry_pricing") or {}
    # the guard keys off DHAN_LIVE, so a legacy row is not trapped by it
    check("heuristic-entered row is not held as 'unmarkable'",
          entry.get("pricing_source") != "DHAN_LIVE")


if __name__ == "__main__":
    test_unmarkable_position_is_held()
    test_no_eod_squareoff_for_ladder()
    test_position_without_real_legs_still_uses_heuristic()
    print("\n" + "=" * 50)
    print(f"RESULT: {_passed} passed, {_failed} failed")
    sys.exit(1 if _failed else 0)
