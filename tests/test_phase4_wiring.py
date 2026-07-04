"""
Phase 4 live-wiring tests — position sizer math and chain-based strike
refinement. Pure logic; Redis is monkeypatched.
Run with:  PYTHONUTF8=1 python tests/test_phase4_wiring.py
"""
import asyncio
import os
import sys
import time

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from backend.app.core import position_sizer as sz
from backend.app.services.redis_service import redis_service
from backend.app.services.options_pricing_service import options_pricing_service as PS

PASS, FAIL = 0, 0


def check(name, cond):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  ✅ {name}")
    else:
        FAIL += 1
        print(f"  ❌ {name}")


def test_sizer():
    print("\n[1] Position sizer")
    # width 200, credit 25, lot 75 -> max loss/lot = 175*75 = 13125
    lots, d = sz.size_lots(equity=1_000_000, width=200, credit=25, lot_size=75)
    check("L_risk = floor(1.5% x 1M / 13125) = 1", d["l_risk"] == 1 and lots == 1)
    lots2, d2 = sz.size_lots(equity=10_000_000, width=200, credit=25, lot_size=75)
    check("scales with equity (11 lots @ 1cr)", d2["l_risk"] == 11)
    # min-lot exception: 500k -> 1.5% = 7500 < 13125, but 13125 <= 3% (15000)
    lots3, d3 = sz.size_lots(equity=500_000, width=200, credit=25, lot_size=75)
    check("min-lot exception under 3% cap", lots3 == 1)
    # too big even for the cap: tiny account
    lots4, _ = sz.size_lots(equity=100_000, width=200, credit=25, lot_size=75)
    check("0 lots when 1 lot > 3% equity", lots4 == 0)
    # vol targeting binds when vol is high
    lots5, d5 = sz.size_lots(equity=10_000_000, width=200, credit=25, lot_size=75,
                             spot=24000, dnet=0.15, realized_vol_ann=0.25)
    check("L_vol computed and binding or not", d5["l_vol"] is not None and lots5 <= d5["l_risk"])
    # Kelly: 75% wins of 2000, 25% losses of 6000 -> b=0.333, f* ~ 0
    pnls = ([2000.0] * 15 + [-6000.0] * 5) * 2
    f = sz.kelly_fraction(pnls)
    check(f"kelly f* ~ 0 for the observed book ({f:.3f})", abs(f) < 0.01)
    lots6, d6 = sz.size_lots(equity=10_000_000, width=200, credit=25, lot_size=75,
                             recent_pnls=pnls)
    check("f*<=0 -> probe 1 lot", lots6 == 1 or (f > 0 and lots6 >= 1))
    # healthy edge -> kelly allows more
    good = [2000.0] * 30 + [-1500.0] * 10
    f2 = sz.kelly_fraction(good)
    check(f"healthy book f* > 0.5 ({f2:.3f})", f2 > 0.5)


def test_refine():
    print("\n[2] Chain-based strike refinement")
    # synthetic chain: PE deltas shrink with distance; target 0.18 in band
    strikes = {}
    spot = 24000.0
    deltas = {23900: 0.35, 23800: 0.27, 23700: 0.19, 23600: 0.13, 23500: 0.09,
              23400: 0.06, 23300: 0.04, 23200: 0.03, 23100: 0.02, 23000: 0.015,
              22900: 0.01, 22800: 0.008}
    prices = {23900: 90.0, 23800: 65.0, 23700: 45.0, 23600: 30.0, 23500: 20.0,
              23400: 13.0, 23300: 8.0, 23200: 5.0, 23100: 3.0, 23000: 2.0,
              22900: 1.5, 22800: 1.0}
    for k, dlt in deltas.items():
        p = prices[k]
        strikes[f"{float(k):.2f}"] = {"pe": {
            "ltp": p, "bid": p - 0.5, "ask": p + 0.5, "mid": p,
            "iv": 13.0, "delta": -dlt, "oi": 1000}}
    strikes[f"{24000.0:.2f}"] = {"pe": {"ltp": 150.0, "bid": 149.5, "ask": 150.5,
                                        "mid": 150.0, "iv": 13.2, "delta": -0.5, "oi": 1000}}
    chain = {"underlying": "NIFTY", "spot": spot, "expiry": "2099-01-05",
             "timestamp": time.time(), "source": "DHAN_LIVE", "strikes": strikes}

    async def fake_get_json(key):
        return chain if key.startswith("option_premiums:") else None
    redis_service.get_json = fake_get_json

    spread = {"ticker": "NIFTY", "strategy_type": "BULL_PUT_SPREAD",
              "spot_price": spot, "leg_1_sell": 23750, "leg_2_buy": 23700,
              "net_credit_per_share": 7.5, "coi_pcr": 1.4}
    out = asyncio.run(PS.refine_spread_with_chain(dict(spread)))
    check("sell strike -> 23700 (delta 0.19 nearest 0.18)", out["leg_1_sell"] == 23700.0)
    check("buy strike 200 lower (4x50)", out["leg_2_buy"] == 23500.0)
    # credit = (44.5-0.10) - (20.5+0.10) = 23.80
    check(f"credit re-estimated from quotes ({out['net_credit_per_share']})",
          abs(out["net_credit_per_share"] - 23.80) < 0.02)
    check("ATM IV extracted for regime gate", abs(out["_chain_atm_iv"] - 0.132) < 0.001)
    check("selection tagged", "DELTA_TARGETED" in out.get("strike_selection", ""))

    # non-credit strategies untouched
    cal = asyncio.run(PS.refine_spread_with_chain(
        {"ticker": "NIFTY", "strategy_type": "CALENDAR_SPREAD", "leg_1_sell": 24000}))
    check("calendar untouched", "strike_selection" not in cal)


def test_refine_iron_condor():
    print("\n[3] Iron condor refinement (dark-shipped; gates keep it off)")
    spot = 24000.0
    strikes = {}
    pe = {23900: (0.35, 90.0), 23800: (0.27, 65.0), 23700: (0.19, 45.0),
          23600: (0.13, 30.0), 23500: (0.09, 20.0), 23400: (0.06, 13.0),
          23300: (0.04, 8.0), 23200: (0.03, 5.0), 23100: (0.02, 3.0)}
    ce = {24100: (0.35, 85.0), 24200: (0.26, 60.0), 24300: (0.18, 42.0),
          24400: (0.12, 27.0), 24500: (0.08, 17.0), 24600: (0.05, 11.0),
          24700: (0.035, 7.0), 24800: (0.025, 4.5), 24900: (0.015, 2.5)}
    for k, (d, p) in pe.items():
        strikes.setdefault(f"{float(k):.2f}", {})["pe"] = {
            "ltp": p, "bid": p - 0.5, "ask": p + 0.5, "mid": p, "iv": 13.0,
            "delta": -d, "oi": 1000}
    for k, (d, p) in ce.items():
        strikes.setdefault(f"{float(k):.2f}", {})["ce"] = {
            "ltp": p, "bid": p - 0.5, "ask": p + 0.5, "mid": p, "iv": 13.0,
            "delta": d, "oi": 1000}
    strikes[f"{24000.0:.2f}"] = {"pe": {"ltp": 150.0, "bid": 149.5, "ask": 150.5,
                                        "mid": 150.0, "iv": 13.2, "delta": -0.5, "oi": 1000}}
    chain = {"underlying": "NIFTY", "spot": spot, "expiry": "2099-01-05",
             "timestamp": time.time(), "source": "DHAN_LIVE", "strikes": strikes}

    async def fake_get_json(key):
        return chain if key.startswith("option_premiums:") else None
    redis_service.get_json = fake_get_json

    out = asyncio.run(PS.refine_spread_with_chain(
        {"ticker": "NIFTY", "strategy_type": "IRON_CONDOR",
         "spot_price": spot, "leg_1_sell": 0, "leg_2_buy": 0, "coi_pcr": 1.0}))
    check("put short 23700 (d 0.19)", out.get("leg_1_sell") == 23700.0)
    check("call short 24300 (d 0.18)", out.get("ic_call_sell") == 24300.0)
    check("hedges 200 out", out.get("leg_2_buy") == 23500.0
          and out.get("ic_call_buy") == 24500.0)
    # credit = (44.5-0.1)-(20.5+0.1) + (41.5-0.1)-(17.5+0.1) = 23.8 + 23.8
    check(f"total credit ({out.get('net_credit_per_share')})",
          abs(out.get("net_credit_per_share", 0) - 47.60) < 0.05)
    check("IC tagged", "IC_DELTA_TARGETED" in out.get("strike_selection", ""))

    # leg_specs expands the 4 legs for pricing/routing
    from backend.app.services.options_pricing_service import leg_specs
    legs = leg_specs("IRON_CONDOR", out["leg_1_sell"], out["leg_2_buy"],
                     extra={"ic_call_sell": out["ic_call_sell"],
                            "ic_call_buy": out["ic_call_buy"]})
    check("4 legs", legs is not None and len(legs) == 4)
    check("2 sells + 2 buys", sum(1 for l in legs if l["side"] == "SELL") == 2)


def test_ladder_wiring():
    print("\n[4] Ladder live wiring (gate + 21-DTE management)")
    import datetime
    from backend.app.services.regime_service import regime_service

    # inject regime state so no bhavcopy load happens
    n = 30
    regime_service._closes = [24000.0 / (1.0005 ** (n - i)) for i in range(n)]
    regime_service._iv_hist = [0.13] * 80
    regime_service._built_for = datetime.date.today()
    regime_service._underlying = "NIFTY"

    side, mult, reason = regime_service.evaluate_ladder(
        ticker="NIFTY", pcr=1.40, spot=24000.0, live_iv=0.13)
    check("confirmed PCR+EMA -> bull put", side == "BULL_PUT_SPREAD")
    check("flat IVR -> mult 1.0", abs(mult - 1.0) < 0.01)
    side2, mult2, _ = regime_service.evaluate_ladder(
        ticker="NIFTY", pcr=1.00, spot=24000.0, live_iv=0.13)
    check("middle PCR -> EMA tilt still trades", side2 == "BULL_PUT_SPREAD")
    regime_service._iv_hist = [0.10 + 0.10 * (i % 10) / 9 for i in range(80)]
    side3, mult3, _ = regime_service.evaluate_ladder(
        ticker="NIFTY", pcr=1.00, spot=24000.0, live_iv=0.25)
    check(f"rich IV -> mult ~1.5 ({mult3})", side3 is not None and mult3 >= 1.4)

    # 21-DTE management exit (env-driven)
    from backend.app.services.execution_service import execution_service

    class _Pos:
        strategy_type = "BULL_PUT_SPREAD"
        ticker = "NIFTY"
        net_credit_per_share = 30.0
        adjusted_net_credit = None
        leg_1_sell = 23700.0
        learning_context = {}

    pos = _Pos()
    for dte, env_on, expect_exit in ((20, True, True), (25, True, False), (20, False, False)):
        pos.learning_context = {"entry_pricing": {"expiry": (
            datetime.date.today() + datetime.timedelta(days=dte)).isoformat()}}
        os.environ["LADDER_MODE"] = "true" if env_on else ""
        r = execution_service._real_exit_decision(pos, 1.0, is_eod=False)
        label = f"DTE {dte} ladder={'on' if env_on else 'off'} -> {'exit' if expect_exit else 'hold'}"
        check(label, r is not None and r[0] is expect_exit
              and (("MANAGE" in r[1]) if expect_exit else True))
    os.environ["LADDER_MODE"] = ""


if __name__ == "__main__":
    test_sizer()
    test_refine()
    test_refine_iron_condor()
    test_ladder_wiring()
    print(f"\n{'='*50}\nRESULT: {PASS} passed, {FAIL} failed")
    sys.exit(1 if FAIL else 0)
