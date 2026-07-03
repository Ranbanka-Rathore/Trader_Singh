"""
Phase 2 order-router tests — fake broker, no live infra, no DB.

Covers: leg sequencing, paper-mode instant fills, live fill via polling,
rejection -> unwind of filled legs, unresolvable leg -> abort before any order,
limit timeout -> market chase, and exit-basket reversal with real-fill P&L.
Contract resolution runs against the real api-scrip-master.csv.
Run: PYTHONUTF8=1 python tests/test_order_router.py
"""
import asyncio
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from backend.app.services import broker_service as bs_mod
from backend.app.services.order_router import order_router, sequence_legs
from backend.app.services.execution_service import execution_service

EXPIRY = "2026-07-07"  # known NIFTY weekly in the checked-in scrip master
import datetime
EXPIRY_D = datetime.date(2026, 7, 7)


class FakeBroker:
    """Scripted broker. behaviors per placed order (in order):
    'PAPER'  -> returns PAPER-... id (simulates the trading-mode gate)
    'FILL'   -> real-looking id, get_order_details reports TRADED
    'REJECT' -> real-looking id, reports REJECTED
    'HANG'   -> real-looking id, stays PENDING forever (forces timeout)
    'NONE'   -> place_order returns None
    """
    def __init__(self, behaviors):
        self.behaviors = behaviors
        self.placed = []          # every place_order call, in order
        self.cancelled = []
        self._status = {}         # order_id -> behavior
        self._n = 0

    async def place_order(self, security_id, exchange_segment, side, order_type, quantity, price=0.0):
        b = self.behaviors[self._n] if self._n < len(self.behaviors) else "FILL"
        self._n += 1
        self.placed.append({"security_id": str(security_id), "segment": exchange_segment,
                            "side": side, "type": order_type, "qty": quantity, "price": price})
        if b == "NONE":
            return None
        if b == "PAPER":
            return f"PAPER-{side}-{security_id}-{self._n}"
        oid = f"ORD{self._n}"
        self._status[oid] = b
        return oid

    async def get_order_details(self, order_id):
        b = self._status.get(str(order_id), "FILL")
        if b == "FILL":
            # fill at the placed limit price (or 55.5 for market orders)
            for p in reversed(self.placed):
                if True:
                    px = p["price"] or 55.5
                    break
            return {"status": "TRADED", "avg_price": px, "filled_qty": 65, "reason": ""}
        if b == "REJECT":
            return {"status": "REJECTED", "avg_price": 0, "filled_qty": 0, "reason": "margin"}
        return {"status": "PENDING", "avg_price": 0, "filled_qty": 0, "reason": ""}

    async def cancel_order(self, order_id):
        self.cancelled.append(str(order_id))
        return True

    def get_futures_security_id(self, ticker):
        return "424242"


def use_fake(behaviors):
    fake = FakeBroker(behaviors)
    bs_mod.broker_service.get_broker = lambda: fake
    return fake


def approx(a, b, tol=0.01):
    return abs(a - b) <= tol


async def main():
    order_router.poll_interval = 0.01
    order_router.leg_fill_timeout = 0.05
    passed = 0

    # --- 1. Sequencing: BUY legs always execute before SELL legs ---
    legs = [{"opt_type": "pe", "strike": 29450, "side": "SELL"},
            {"opt_type": "pe", "strike": 28900, "side": "BUY"}]
    ordered = sequence_legs(legs)
    assert ordered[0]["side"] == "BUY" and ordered[1]["side"] == "SELL"
    print("[1] BUY-before-SELL sequencing  OK")
    passed += 1

    # --- 2. Paper-mode basket: instant fills at limit, correct net credit ---
    fake = use_fake(["PAPER", "PAPER"])
    basket = await order_router.route_basket(
        None, ticker="NIFTY", strategy_type="BULL_PUT_SPREAD",
        legs=[{"opt_type": "pe", "strike": 29450, "side": "SELL", "limit_price": 60.0},
              {"opt_type": "pe", "strike": 28900, "side": "BUY", "limit_price": 20.0}],
        lots=2, intent="ENTRY", expiry=EXPIRY_D)
    assert basket["status"] == "FILLED", basket
    assert fake.placed[0]["side"] == "BUY" and fake.placed[1]["side"] == "SELL"
    assert fake.placed[0]["qty"] == 130, fake.placed  # 2 lots x 65
    assert fake.placed[0]["segment"] == "NSE_FNO"
    assert approx(basket["net_premium_per_share"], 20.0 - 60.0), basket  # -40 = credit 40
    print(f"[2] paper basket filled, net premium {basket['net_premium_per_share']:+.2f}/sh, qty 130  OK")
    passed += 1

    # --- 3. Live-mode fill via polling ---
    use_fake(["FILL", "FILL"])
    basket = await order_router.route_basket(
        None, ticker="NIFTY", strategy_type="BULL_PUT_SPREAD",
        legs=[{"opt_type": "pe", "strike": 29450, "side": "SELL", "limit_price": 60.0},
              {"opt_type": "pe", "strike": 28900, "side": "BUY", "limit_price": 20.0}],
        lots=1, intent="ENTRY", expiry=EXPIRY_D)
    assert basket["status"] == "FILLED", basket
    assert all(l["status"] == "FILLED" for l in basket["legs"])
    print("[3] live-mode polling fill  OK")
    passed += 1

    # --- 4. Second leg rejected -> first (filled) leg unwound ---
    fake = use_fake(["FILL", "REJECT", "FILL"])  # buy fills, sell rejects, unwind fills
    basket = await order_router.route_basket(
        None, ticker="NIFTY", strategy_type="BULL_PUT_SPREAD",
        legs=[{"opt_type": "pe", "strike": 29450, "side": "SELL", "limit_price": 60.0},
              {"opt_type": "pe", "strike": 28900, "side": "BUY", "limit_price": 20.0}],
        lots=1, intent="ENTRY", expiry=EXPIRY_D)
    assert basket["status"] == "FAILED", basket
    assert len(fake.placed) == 3, fake.placed
    unwind = fake.placed[2]
    # the BUY 28900 leg was filled, so the unwind must SELL the same security as MARKET
    assert unwind["side"] == "SELL" and unwind["type"] == "MARKET"
    assert unwind["security_id"] == fake.placed[0]["security_id"]
    assert basket["legs"][0]["status"] == "UNWOUND"
    print("[4] mid-basket rejection -> filled leg unwound with MARKET  OK")
    passed += 1

    # --- 5. Unresolvable strike -> abort BEFORE any order ---
    fake = use_fake(["FILL", "FILL"])
    basket = await order_router.route_basket(
        None, ticker="NIFTY", strategy_type="BULL_PUT_SPREAD",
        legs=[{"opt_type": "pe", "strike": 99999, "side": "SELL", "limit_price": 60.0},
              {"opt_type": "pe", "strike": 28900, "side": "BUY", "limit_price": 20.0}],
        lots=1, intent="ENTRY", expiry=EXPIRY_D)
    assert basket["status"] == "FAILED" and "unresolvable" in basket["reason"], basket
    assert len(fake.placed) == 0, "orders were placed despite unresolvable leg!"
    print("[5] unresolvable leg aborts basket before any order  OK")
    passed += 1

    # --- 6. Limit timeout -> cancel + market chase fills ---
    fake = use_fake(["HANG", "FILL"])  # limit hangs; market chase fills
    basket = await order_router.route_basket(
        None, ticker="NIFTY", strategy_type="CASH_SECURED_PUT",
        legs=[{"opt_type": "pe", "strike": 29450, "side": "SELL", "limit_price": 60.0}],
        lots=1, intent="ENTRY", expiry=EXPIRY_D)
    assert basket["status"] == "FILLED", basket
    assert len(fake.cancelled) == 1, fake.cancelled
    assert fake.placed[1]["type"] == "MARKET"
    print("[6] limit timeout -> cancel + market chase  OK")
    passed += 1

    # --- 7. Exit basket: sides reversed, P&L from actual fills ---
    class Pos:
        ticker = "NIFTY"
        strategy_type = "BULL_PUT_SPREAD"
        lots_sized = 1
        id = 77
        learning_context = {"entry_pricing": {
            "pricing_source": "DHAN_LIVE",
            "expiry": EXPIRY,
            "legs": [
                {"opt_type": "pe", "strike": 29450, "side": "SELL", "entry_fill": 60.0},
                {"opt_type": "pe", "strike": 28900, "side": "BUY", "entry_fill": 20.0},
            ],
        }}
    real_mark = {"pricing_source": "DHAN_LIVE", "legs": [
        {"opt_type": "pe", "strike": 29450, "side": "SELL", "entry_fill": 60.0, "current_mark": 30.0},
        {"opt_type": "pe", "strike": 28900, "side": "BUY", "entry_fill": 20.0, "current_mark": 10.0},
    ]}
    fake = use_fake(["PAPER", "PAPER"])
    routed = await execution_service._route_exit_basket(None, Pos(), real_mark)
    assert routed is not None
    # exit sides flipped: entry SELL 29450 -> exit BUY; entry BUY 28900 -> exit SELL.
    # BUY-first sequencing => first placed order is the BUY-back of the short leg.
    assert fake.placed[0]["side"] == "BUY" and approx(fake.placed[0]["price"], 30.0)
    assert fake.placed[1]["side"] == "SELL" and approx(fake.placed[1]["price"], 10.0)
    # P&L: short leg 60->30 = +30 ; long leg 20->10 = -10 ; net +20/share
    assert approx(routed["pnl_per_share"], 20.0), routed
    print(f"[7] exit basket reversed + pnl ₹{routed['pnl_per_share']}/sh from fills  OK")
    passed += 1

    print(f"\n✅ ALL {passed}/7 ORDER ROUTER TESTS PASSED")


if __name__ == "__main__":
    asyncio.run(main())
