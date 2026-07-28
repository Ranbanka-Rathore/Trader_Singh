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

_FAKE_CHAIN = {}      # served for the primary `option_premiums:{ticker}` key
_FAKE_BY_EXPIRY = {}  # {expiry: payload} served for `option_premiums:{ticker}:{exp}`
_SET_JSON = {}        # captures redis_service.set_json writes


async def _fake_get_json(key):
    if key.startswith("option_premiums:"):
        parts = key.split(":")
        if len(parts) == 3:                     # per-expiry key
            return _FAKE_BY_EXPIRY.get(parts[2])
        return _FAKE_CHAIN
    return None


async def _fake_set_json(key, value, expire=None):
    _SET_JSON[key] = value


redis_service.get_json = _fake_get_json  # monkeypatch
redis_service.set_json = _fake_set_json


def _chain(spot, legs, expiry="2026-07-07"):
    """legs: {strike: {'ce'|'pe': (bid, ask, iv)}} -> premium payload.

    Mirrors dhan_integration._extract_leg_premium: mid falls back to ltp when a
    book side is empty, which is precisely the trap mark_from_quote must avoid.
    """
    import time
    strikes = {}
    for strike, sides in legs.items():
        node = {}
        for typ, spec in sides.items():
            bid, ask, iv = spec[0], spec[1], spec[2]
            ltp = spec[3] if len(spec) > 3 else round((bid + ask) / 2, 2)
            mid = round((bid + ask) / 2, 2) if (bid > 0 and ask > 0) else ltp
            node[typ] = {"ltp": ltp, "bid": bid, "ask": ask,
                         "mid": mid, "iv": iv, "oi": 1000,
                         "delta": 0.3, "gamma": 0.001, "theta": -5.0, "vega": 4.0}
        strikes[f"{float(strike):.2f}"] = node
    return {"underlying": "NIFTY", "spot": spot, "expiry": expiry,
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

    # --- 7. EXPIRY GUARD: never mark a position off a different expiry -------
    # Regression for the 2026-07-28 fabricated take-profit: the ladder's chain
    # publisher rolls to a new expiry as the held one ages out of the 30-45 DTE
    # window, and the same strikes exist there, so the lookup silently succeeded.
    _FAKE_CHAIN = _chain(24000, {
        23800: {"pe": (60.0, 62.0, 13.5)},
        23750: {"pe": (48.0, 50.0, 14.0)},
    }, expiry="2026-07-07")
    entry7 = await PS.price_spread_entry(spread)
    assert entry7["pricing_source"] == "DHAN_LIVE" and entry7["expiry"] == "2026-07-07"
    pos7 = Pos(strategy_type="BULL_PUT_SPREAD", net_credit_per_share=entry7["net_credit_per_share"],
               lots_sized=1, learning_context={"entry_pricing": entry7})

    # same expiry -> marks fine
    _FAKE_CHAIN = _chain(24050, {
        23800: {"pe": (30.0, 32.0, 12.0)},
        23750: {"pe": (24.0, 26.0, 12.5)},
    }, expiry="2026-07-07")
    assert await PS.mark_position_pnl(pos7, current_spot=24050) is not None

    # chain rolled to a LATER expiry, same strikes present, richer premiums:
    # this is the exact shape that booked +₹4,577 on a ₹1,745-max spread.
    _FAKE_CHAIN = _chain(24050, {
        23800: {"pe": (208.0, 210.0, 12.0)},
        23750: {"pe": (252.0, 254.0, 12.5)},
    }, expiry="2026-07-14")
    assert await PS.mark_position_pnl(pos7, current_spot=24050) is None, \
        "wrong-expiry chain must refuse to mark"

    # a routed leg carrying its own mismatched expiry is refused too
    _FAKE_CHAIN = _chain(24050, {
        23800: {"pe": (30.0, 32.0, 12.0)},
        23750: {"pe": (24.0, 26.0, 12.5)},
    }, expiry="2026-07-07")
    entry7b = dict(entry7)
    entry7b["legs"] = [dict(l) for l in entry7["legs"]]
    entry7b["legs"][0]["expiry"] = "2026-07-14"   # router override drifted
    pos7b = Pos(strategy_type="BULL_PUT_SPREAD", net_credit_per_share=9.80,
                lots_sized=1, learning_context={"entry_pricing": entry7b})
    assert await PS.mark_position_pnl(pos7b, current_spot=24050) is None, \
        "per-leg expiry mismatch must refuse to mark"

    # an entry with no recorded expiry cannot be verified -> refuse
    entry7c = {k: v for k, v in entry7.items() if k != "expiry"}
    pos7c = Pos(strategy_type="BULL_PUT_SPREAD", net_credit_per_share=9.80,
                lots_sized=1, learning_context={"entry_pricing": entry7c})
    assert await PS.mark_position_pnl(pos7c, current_spot=24050) is None, \
        "unverifiable expiry must refuse to mark"
    print("[7] expiry guard: match marks / rolled chain, leg drift, missing expiry all refused  OK")
    passed += 1

    # --- 8. QUOTE QUALITY: never mark off a stale ltp with an empty book -----
    # At 09:15 the book is empty, _extract_leg_premium sets mid = ltp, and that
    # ltp is the previous session's print. Both fabricated exits marked off it.
    assert ps_mod.mark_from_quote({"bid": 30.0, "ask": 32.0, "ltp": 31.0}) == 31.0
    assert ps_mod.mark_from_quote({"bid": 0.0, "ask": 0.0, "ltp": 253.70}) is None
    assert ps_mod.mark_from_quote({"bid": 30.0, "ask": 0.0, "ltp": 253.70}) is None
    assert ps_mod.mark_from_quote({"bid": 0.0, "ask": 32.0, "ltp": 253.70}) is None
    assert ps_mod.mark_from_quote({"bid": 40.0, "ask": 32.0, "ltp": 36.0}) is None  # crossed

    # end-to-end: empty book on ONE leg is enough to refuse the whole mark
    _FAKE_CHAIN = _chain(24050, {
        23800: {"pe": (30.0, 32.0, 12.0)},
        23750: {"pe": (0.0, 0.0, 12.5, 253.70)},   # no book, stale ltp
    }, expiry="2026-07-07")
    assert await PS.mark_position_pnl(pos7, current_spot=24050) is None, \
        "empty book must refuse to mark, not fall back to ltp"
    print("[8] quote quality: empty/one-sided/crossed books refused, no ltp fallback  OK")
    passed += 1

    # --- 9. OPEN WARM-UP: no exit decisions in the first minutes after 09:15 --
    import datetime as _dt2
    from backend.app.services import execution_service as ex_mod
    assert ex_mod.MARKET_OPEN_WARMUP_MIN > 0, "warm-up should be on by default"
    d = _dt2.date(2026, 7, 28)
    assert ex_mod._in_open_warmup(_dt2.datetime.combine(d, _dt2.time(9, 15, 8))) is True
    assert ex_mod._in_open_warmup(_dt2.datetime.combine(d, _dt2.time(9, 19, 59))) is True
    assert ex_mod._in_open_warmup(_dt2.datetime.combine(d, _dt2.time(9, 20, 0))) is False
    assert ex_mod._in_open_warmup(_dt2.datetime.combine(d, _dt2.time(9, 14, 59))) is False
    assert ex_mod._in_open_warmup(_dt2.datetime.combine(d, _dt2.time(15, 15, 0))) is False
    _prev_warm = ex_mod.MARKET_OPEN_WARMUP_MIN
    ex_mod.MARKET_OPEN_WARMUP_MIN = 0   # opt-out knob
    assert ex_mod._in_open_warmup(_dt2.datetime.combine(d, _dt2.time(9, 15, 8))) is False
    ex_mod.MARKET_OPEN_WARMUP_MIN = _prev_warm
    print("[9] open warm-up: 09:15-09:19 suppressed, 09:20 live, knob disables  OK")
    passed += 1

    # --- 10. PER-EXPIRY CHAIN: an aged position marks off its OWN contracts ---
    # The real fix: the harvester publishes option_premiums:{ticker}:{expiry} for
    # every HELD expiry, so a position stays markable after the entry selector
    # has rolled on. Primary key = Sep-01 (rolled), held key = Aug-25.
    global _FAKE_BY_EXPIRY
    held = _chain(24000, {
        23800: {"pe": (60.0, 62.0, 13.5)},
        23750: {"pe": (48.0, 50.0, 14.0)},
    }, expiry="2026-08-25")
    _FAKE_CHAIN = held
    entry10 = await PS.price_spread_entry(spread)
    assert entry10["expiry"] == "2026-08-25"
    pos10 = Pos(strategy_type="BULL_PUT_SPREAD", net_credit_per_share=entry10["net_credit_per_share"],
                lots_sized=1, learning_context={"entry_pricing": entry10})

    # entry selector has rolled: primary is now Sep-01 with much richer premiums
    _FAKE_CHAIN = _chain(24000, {
        23800: {"pe": (208.0, 210.0, 13.5)},
        23750: {"pe": (252.0, 254.0, 14.0)},
    }, expiry="2026-09-01")

    # ...and with no per-expiry chain published yet, we must still refuse
    _FAKE_BY_EXPIRY = {}
    assert await PS.mark_position_pnl(pos10, current_spot=24000) is None, \
        "rolled primary must not be substituted for the held expiry"

    # ...once the harvester publishes the held expiry, marking works again
    _FAKE_BY_EXPIRY = {"2026-08-25": _chain(24050, {
        23800: {"pe": (30.0, 32.0, 12.0)},
        23750: {"pe": (24.0, 26.0, 12.5)},
    }, expiry="2026-08-25")}
    mark10 = await PS.mark_position_pnl(pos10, current_spot=24050)
    assert mark10 is not None and approx(mark10["pnl_per_share"], 3.80), mark10
    assert await PS.get_premium_chain("NIFTY", expiry="2026-08-25") is not None
    # a per-expiry key that somehow holds a different expiry is still rejected
    _FAKE_BY_EXPIRY = {"2026-08-25": _chain(24050, {
        23800: {"pe": (30.0, 32.0, 12.0)},
        23750: {"pe": (24.0, 26.0, 12.5)},
    }, expiry="2026-09-01")}
    assert await PS.mark_position_pnl(pos10, current_spot=24050) is None, \
        "mislabelled per-expiry chain must still be rejected"
    _FAKE_BY_EXPIRY = {}
    print(f"[10] per-expiry chain: held expiry marks at ₹{mark10['pnl_per_share']}/sh, "
          f"rolled primary refused  OK")
    passed += 1

    # --- 11. held-expiry publication (what the harvester consumes) -----------
    import datetime as _dt3

    class _P:
        def __init__(self, ticker, exp):
            self.ticker = ticker
            self.learning_context = {"entry_pricing": {"expiry": exp}}

    _SET_JSON.clear()
    out = await execution_service._publish_held_expiries([
        _P("NIFTY", "2026-08-25"),
        _P("NIFTY", "2026-09-01"),
        _P("NIFTY", "2026-08-25"),   # dedup
        _P("NSEI", "2026-08-25"),    # ticker normalised to NIFTY
        _P("NIFTY", None),           # unpriced position ignored
    ])
    assert _SET_JSON["held_expiries:NIFTY"] == ["2026-08-25", "2026-09-01"], _SET_JSON
    assert out == ["2026-08-25", "2026-09-01"], out
    _SET_JSON.clear()
    assert await execution_service._publish_held_expiries([]) == []
    assert "held_expiries:NIFTY" not in _SET_JSON
    print("[11] held expiries published deduped/normalised for the harvester  OK")
    passed += 1

    print(f"\n✅ ALL {passed}/11 PRICING TESTS PASSED")


if __name__ == "__main__":
    asyncio.run(main())
