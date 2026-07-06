"""
Phase 1 pricing pipeline tests — pure logic, no live infra.

Injects a synthetic option-premium chain into Redis (via monkeypatch) and
exercises: entry paper-fill pricing, mark-to-market P&L, and real-mark exit
decisions. Run with:  python tests/test_options_pricing.py
"""
import asyncio
import os
import sys
import types

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from backend.app.services import options_pricing_service as ps_mod
from backend.app.services.options_pricing_service import options_pricing_service as PS
from backend.app.services.redis_service import redis_service
from backend.app.services.execution_service import execution_service

_FAKE_CHAIN = {}  # mutated per scenario


async def _fake_get_json(key):
    if key.startswith("option_premiums:"):
        return _FAKE_CHAIN
    return None


redis_service.get_json = _fake_get_json  # monkeypatch


def _chain(spot, legs):
    """legs: {strike: {'ce'|'pe': (bid, ask, iv)}} -> premium payload."""
    import time
    strikes = {}
    for strike, sides in legs.items():
        node = {}
        for typ, (bid, ask, iv) in sides.items():
            node[typ] = {"ltp": round((bid + ask) / 2, 2), "bid": bid, "ask": ask,
                         "mid": round((bid + ask) / 2, 2), "iv": iv, "oi": 1000,
                         "delta": 0.3, "gamma": 0.001, "theta": -5.0, "vega": 4.0}
        strikes[f"{float(strike):.2f}"] = node
    return {"underlying": "NIFTY", "spot": spot, "expiry": "2026-07-07",
            "timestamp": time.time(), "source": "DHAN_LIVE", "strikes": strikes}


class Pos:
    def __init__(self, **kw):
        self.strategy_type = kw["strategy_type"]
        self.ticker = kw.get("ticker", "NIFTY")
        self.net_credit_per_share = kw["net_credit_per_share"]
        self.adjusted_net_credit = None
        self.lots_sized = kw.get("lots_sized", 1)
        self.learning_context = kw["learning_context"]
        self.highest_seen = kw.get("highest_seen", 0)


def approx(a, b, tol=0.01):
    return abs(a - b) <= tol


async def main():
    global _FAKE_CHAIN
    passed = 0

    # --- 1. Entry pricing: BULL_PUT_SPREAD sell 23800pe / buy 23750pe ---
    _FAKE_CHAIN = _chain(24000, {
        23800: {"pe": (60.0, 62.0, 13.5)},
        23750: {"pe": (48.0, 50.0, 14.0)},
    })
    spread = {"ticker": "NIFTY", "strategy_type": "BULL_PUT_SPREAD",
              "spot_price": 24000, "leg_1_sell": 23800, "leg_2_buy": 23750,
              "net_credit_per_share": 7.5}  # synthetic guess to be overridden
    entry = await PS.price_spread_entry(spread)
    assert entry["pricing_source"] == "DHAN_LIVE", entry
    # sell fill = bid-0.10 = 59.90 ; buy fill = ask+0.10 = 50.10 ; credit = 9.80
    assert approx(entry["net_credit_per_share"], 9.80), entry["net_credit_per_share"]
    print(f"[1] entry credit = ₹{entry['net_credit_per_share']}/sh (legs {len(entry['legs'])})  OK")
    passed += 1

    # --- 2. MTM P&L after decay (credit spread profits as it narrows) ---
    pos = Pos(strategy_type="BULL_PUT_SPREAD", net_credit_per_share=entry["net_credit_per_share"],
              lots_sized=5, learning_context={"entry_pricing": entry})
    _FAKE_CHAIN = _chain(24050, {
        23800: {"pe": (30.0, 32.0, 12.0)},   # mid 31
        23750: {"pe": (24.0, 26.0, 12.5)},   # mid 25
    })
    mark = await PS.mark_position_pnl(pos, current_spot=24050)
    # pnl = credit(9.80) - close_cost(mid 31 - mid 25 = 6.0) = 3.80
    assert approx(mark["pnl_per_share"], 3.80), mark["pnl_per_share"]
    print(f"[2] MTM pnl = ₹{mark['pnl_per_share']}/sh after decay  OK")
    passed += 1

    # --- 3. Exit decisions (Phase 4 stack: TP 0.5x, touch stop, backstop, T-1) ---
    # This section tests the SNIPER exit stack, which only applies when
    # LADDER_MODE is off. Pin it off explicitly so the suite is isolated from
    # the .env LADDER_MODE flag (ladder uses a MANAGE @21DTE exit instead).
    _prev_ladder = os.environ.get("LADDER_MODE")
    os.environ["LADDER_MODE"] = ""
    import datetime as _dt
    # keep the test stable as real time passes: expiry 5 days out
    pos.learning_context["entry_pricing"]["expiry"] = (
        _dt.date.today() + _dt.timedelta(days=5)).isoformat()
    pos.leg_1_sell = 23800.0

    hold = execution_service._real_exit_decision(pos, 3.80, is_eod=False)  # < 0.5*9.80=4.90
    assert hold is not None and hold[0] is False, hold
    tp = execution_service._real_exit_decision(pos, 5.0, is_eod=False)     # >= 4.90
    assert tp[0] is True and "TAKE PROFIT" in tp[1], tp
    mid = execution_service._real_exit_decision(pos, -10.2, is_eod=False)  # > -14.70 backstop
    assert mid[0] is False, mid
    sl = execution_service._real_exit_decision(pos, -15.0, is_eod=False)   # <= -1.5*9.80
    assert sl[0] is True and "STOP LOSS" in sl[1], sl
    touch = execution_service._real_exit_decision(pos, -2.0, is_eod=False, spot=23795.0)
    assert touch[0] is True and "STRIKE TOUCHED" in touch[1], touch
    no_touch = execution_service._real_exit_decision(pos, -2.0, is_eod=False, spot=23900.0)
    assert no_touch[0] is False, no_touch
    # T-1 time stop: expiry tomorrow + EOD window
    pos.learning_context["entry_pricing"]["expiry"] = (
        _dt.date.today() + _dt.timedelta(days=1)).isoformat()
    tstop = execution_service._real_exit_decision(pos, 1.0, is_eod=True)
    assert tstop[0] is True and "TIME STOP" in tstop[1], tstop
    # not EOD yet, T-1 -> hold
    thold = execution_service._real_exit_decision(pos, 1.0, is_eod=False)
    assert thold[0] is False, thold
    pos.learning_context["entry_pricing"]["expiry"] = (
        _dt.date.today() + _dt.timedelta(days=5)).isoformat()
    print(f"[3] exits: hold/TP/backstop/SL/touch/T-1 all correct  OK")
    passed += 1
    # restore whatever LADDER_MODE the environment had before section 3
    if _prev_ladder is None:
        os.environ.pop("LADDER_MODE", None)
    else:
        os.environ["LADDER_MODE"] = _prev_ladder

    # --- 4. Fallback: calendar spread cannot be priced (multi-expiry) ---
    cal = await PS.price_spread_entry({"ticker": "NIFTY", "strategy_type": "CALENDAR_SPREAD",
                                       "spot_price": 24000, "leg_1_sell": 24000, "leg_2_buy": 24000,
                                       "net_credit_per_share": 96.0})
    assert cal["pricing_source"] == "HEURISTIC_FALLBACK", cal
    print(f"[4] calendar -> heuristic fallback ({cal['reason']})  OK")
    passed += 1

    # --- 5. Fallback: missing quote for a strike -> fallback ---
    _FAKE_CHAIN = _chain(24000, {23800: {"pe": (60.0, 62.0, 13.5)}})  # 23750 absent
    miss = await PS.price_spread_entry(spread)
    assert miss["pricing_source"] == "HEURISTIC_FALLBACK", miss
    print(f"[5] missing leg quote -> heuristic fallback ({miss['reason']})  OK")
    passed += 1

    # --- 6. Debit spread net (pay ask, receive bid) ---
    _FAKE_CHAIN = _chain(24000, {
        24000: {"ce": (100.0, 102.0, 15.0)},  # buy ATM call
        24050: {"ce": (78.0, 80.0, 15.5)},    # sell OTM call
    })
    deb = await PS.price_spread_entry({"ticker": "NIFTY", "strategy_type": "DEBIT_BULL_SPREAD",
                                       "spot_price": 24000, "leg_1_sell": 24050, "leg_2_buy": 24000,
                                       "net_credit_per_share": 40.0})
    # buy 24000 ce = ask+0.10 = 102.10 ; sell 24050 ce = bid-0.10 = 77.90 ; net debit = 24.20
    assert deb["pricing_source"] == "DHAN_LIVE"
    assert approx(deb["net_premium_per_share"], 24.20), deb["net_premium_per_share"]
    print(f"[6] debit spread net = ₹{deb['net_premium_per_share']}/sh  OK")
    passed += 1

    print(f"\n✅ ALL {passed}/6 PRICING TESTS PASSED")


if __name__ == "__main__":
    asyncio.run(main())
