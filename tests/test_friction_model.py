"""
Phase 3 friction model tests — pure math, no infra.

Verifies the per-leg charge formulas (options + futures, BUY vs SELL),
basket aggregation across mixed leg shapes, and the round-trip helper.
Run with:  PYTHONUTF8=1 python tests/test_friction_model.py
"""
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from backend.app.core import friction_model as fm


def approx(a, b, tol=0.01):
    return abs(a - b) <= tol


PASS, FAIL = 0, 0


def check(name, cond):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  ✅ {name}")
    else:
        FAIL += 1
        print(f"  ❌ {name}")


def test_option_sell_leg():
    print("\n[1] Option SELL leg (premium 100, qty 65)")
    # turnover = 6500
    f = fm.leg_friction(side="SELL", price=100.0, quantity=65, instrument="option")
    check("brokerage 20", f["brokerage"] == 20.0)
    check("STT 0.1% = 6.50", approx(f["stt"], 6.50))
    check("exchange txn 0.03503% = 2.2770", approx(f["exchange_txn"], 2.2770, 0.001))
    check("SEBI 0.0001% = 0.0065", approx(f["sebi"], 0.0065, 0.0001))
    check("IPFT 0.0005% = 0.0325", approx(f["ipft"], 0.0325, 0.0001))
    check("no stamp on SELL", f["stamp_duty"] == 0.0)
    expected_gst = 0.18 * (20.0 + 2.27695 + 0.0065 + 0.0325)
    check(f"GST 18% = {expected_gst:.4f}", approx(f["gst"], expected_gst, 0.001))
    expected_total = 20.0 + 6.50 + 2.27695 + 0.0065 + 0.0325 + expected_gst
    check(f"total = {expected_total:.2f}", approx(f["total"], expected_total, 0.02))


def test_option_buy_leg():
    print("\n[2] Option BUY leg (premium 40, qty 65)")
    # turnover = 2600
    f = fm.leg_friction(side="BUY", price=40.0, quantity=65, instrument="option")
    check("no STT on BUY", f["stt"] == 0.0)
    check("stamp 0.003% = 0.078", approx(f["stamp_duty"], 0.078, 0.001))
    check("exchange txn = 0.9108", approx(f["exchange_txn"], 0.91078, 0.001))
    check("total > brokerage+GST floor", f["total"] > 20.0 * 1.18)


def test_future_legs():
    print("\n[3] Futures legs (price 25000, qty 65)")
    # notional = 1,625,000
    s = fm.leg_friction(side="SELL", price=25000.0, quantity=65, instrument="fut")
    b = fm.leg_friction(side="BUY", price=25000.0, quantity=65, instrument="future")
    check("SELL STT 0.02% = 325", approx(s["stt"], 325.0, 0.01))
    check("BUY STT zero", b["stt"] == 0.0)
    check("BUY stamp 0.002% = 32.50", approx(b["stamp_duty"], 32.50, 0.01))
    check("SELL stamp zero", s["stamp_duty"] == 0.0)
    check("txn 0.00173% = 28.11", approx(s["exchange_txn"], 28.1125, 0.01))


def test_zero_turnover():
    print("\n[4] Zero-price leg -> zero friction (no phantom brokerage)")
    f = fm.leg_friction(side="BUY", price=0.0, quantity=65)
    check("total 0", f["total"] == 0.0)
    check("brokerage 0", f["brokerage"] == 0.0)


def test_basket_aggregation():
    print("\n[5] Basket aggregation (router-shaped + pricing-shaped legs)")
    router_legs = [
        {"side": "SELL", "opt_type": "pe", "quantity": 65, "entry_fill": 100.0},
        {"side": "BUY", "opt_type": "pe", "quantity": 65, "entry_fill": 40.0},
    ]
    agg = fm.basket_friction(router_legs)
    f1 = fm.leg_friction(side="SELL", price=100.0, quantity=65)
    f2 = fm.leg_friction(side="BUY", price=40.0, quantity=65)
    check("sum of legs", approx(agg["total"], f1["total"] + f2["total"], 0.02))

    # pricing-service shape: no quantity -> default_quantity kicks in
    pricing_legs = [
        {"side": "SELL", "opt_type": "pe", "entry_fill": 100.0},
        {"side": "BUY", "opt_type": "pe", "entry_fill": 40.0},
    ]
    agg2 = fm.basket_friction(pricing_legs, default_quantity=65)
    check("default_quantity path matches", approx(agg2["total"], agg["total"], 0.02))

    # missing quantity with no default must raise
    try:
        fm.basket_friction(pricing_legs)
        check("raises without default_quantity", False)
    except ValueError:
        check("raises without default_quantity", True)


def test_round_trip():
    print("\n[6] Round trip (entry + exit) for a 1-lot NIFTY credit spread")
    entry = [
        {"side": "SELL", "opt_type": "pe", "quantity": 65, "entry_fill": 100.0},
        {"side": "BUY", "opt_type": "pe", "quantity": 65, "entry_fill": 40.0},
    ]
    exit_ = [
        {"side": "BUY", "opt_type": "pe", "quantity": 65, "entry_fill": 20.0},
        {"side": "SELL", "opt_type": "pe", "quantity": 65, "entry_fill": 5.0},
    ]
    rt = fm.round_trip_friction(entry, exit_)
    check("has entry/exit/total", all(k in rt for k in ("entry", "exit", "total")))
    check("total = entry+exit", approx(rt["total"], rt["entry"]["total"] + rt["exit"]["total"], 0.02))
    # 4 leg-orders: brokerage alone is 80 + 18% GST = 94.4 minimum
    check("round trip > brokerage+GST floor (94.4)", rt["total"] > 94.4)
    print(f"     round-trip friction: Rs {rt['total']:.2f} "
          f"(entry {rt['entry']['total']:.2f} + exit {rt['exit']['total']:.2f})")

    # At the system's minimum sizing (5 lots -> qty 325) the ad-valorem parts
    # scale 5x and comfortably bury the old flat Rs 120 assumption.
    entry5 = [{**l, "quantity": 325} for l in entry]
    exit5 = [{**l, "quantity": 325} for l in exit_]
    rt5 = fm.round_trip_friction(entry5, exit5)
    check("5-lot round trip > Rs 120 flat assumption", rt5["total"] > 120.0)
    print(f"     5-lot round-trip friction: Rs {rt5['total']:.2f}")


def test_backtester_leg_shape():
    print("\n[7] Backtester leg shape ({price, quantity})")
    legs = [{"side": "SELL", "opt_type": "ce", "price": 80.0, "quantity": 130}]
    agg = fm.basket_friction(legs)
    ref = fm.leg_friction(side="SELL", price=80.0, quantity=130)
    check("'price' key accepted", approx(agg["total"], ref["total"], 0.01))


def test_explicit_instrument_key():
    """A leg saying instrument='future' must be charged FUTURES rates.

    basket_friction originally inferred the instrument solely from
    opt_type == 'fut' and silently ignored an `instrument` key. Option STT is
    0.1% of PREMIUM; futures STT is 0.02% of NOTIONAL. So a futures leg labelled
    only with `instrument` was charged option rates against a base ~1000x
    larger — an ~8x overcharge (Rs 3,014 instead of Rs 468 on one NIFTY lot,
    46.4 index points instead of 7.2). It looked plausible, which is what made
    it dangerous.
    """
    print("\n[8] Explicit instrument key is honoured")
    px, qty = 24400.0, 65

    by_key = fm.basket_friction([{"side": "SELL", "price": px, "quantity": qty,
                                  "instrument": "future"}])
    by_conv = fm.basket_friction([{"side": "SELL", "opt_type": "FUT", "price": px,
                                   "quantity": qty}])
    ref_fut = fm.leg_friction(side="SELL", price=px, quantity=qty,
                              instrument="future")
    ref_opt = fm.leg_friction(side="SELL", price=px, quantity=qty,
                              instrument="option")

    check("instrument='future' matches futures rates",
          approx(by_key["total"], ref_fut["total"], 0.01))
    check("instrument='future' agrees with opt_type='FUT'",
          approx(by_key["total"], by_conv["total"], 0.01))
    check("and is NOT charged option rates",
          not approx(by_key["total"], ref_opt["total"], 1.0))
    check("the overcharge it prevents is ~8x",
          ref_opt["total"] / max(ref_fut["total"], 1e-9) > 5.0)

    # 'option' stays explicit-able, and the default is unchanged.
    by_opt = fm.basket_friction([{"side": "SELL", "price": 80.0, "quantity": 130,
                                  "instrument": "option"}])
    check("instrument='option' matches option rates",
          approx(by_opt["total"],
                 fm.leg_friction(side="SELL", price=80.0, quantity=130)["total"], 0.01))
    check("no instrument and no opt_type still defaults to option",
          approx(fm.basket_friction([{"side": "SELL", "price": 80.0, "quantity": 130}])["total"],
                 by_opt["total"], 0.01))

    # A typo must fail loudly rather than silently pick a rate table.
    try:
        fm.basket_friction([{"side": "SELL", "price": px, "quantity": qty,
                             "instrument": "futures_typo"}])
        check("an unknown instrument raises", False)
    except ValueError:
        check("an unknown instrument raises", True)


if __name__ == "__main__":
    test_option_sell_leg()
    test_option_buy_leg()
    test_future_legs()
    test_zero_turnover()
    test_basket_aggregation()
    test_round_trip()
    test_backtester_leg_shape()
    test_explicit_instrument_key()
    print(f"\n{'='*50}\nRESULT: {PASS} passed, {FAIL} failed")
    sys.exit(1 if FAIL else 0)
