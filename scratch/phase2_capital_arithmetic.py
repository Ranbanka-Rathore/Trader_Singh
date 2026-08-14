"""
Phase 2 — what does Rs 50k-1L actually buy, against this project's own friction
model and NIFTY's real lot size?

Written BEFORE Amendment E, so the amendment records measured numbers rather
than plausible ones. RESUME.md §4 asserts "1-lot intraday long option = Rs 66
round trip = ~1.0 index point"; that was computed at an assumed premium and is
checked here across the premium range this capital can actually reach.

The question the charter needs answered:

  at Rs 50k-1L, with a 65-unit NIFTY lot, which options are affordable at a
  sane per-trade risk, and what does friction cost as a FRACTION of them?

Because friction is dominated by flat brokerage (Rs 20/order + GST), it is
close to constant per trade in rupees, so its share of a position rises sharply
as the premium falls. That is the whole story at this capital.

Usage:  ./venv/Scripts/python.exe scratch/phase2_capital_arithmetic.py
"""
import os
import sys

sys.path.insert(0, os.getcwd())

from backend.app.core import scrip_master  # noqa: E402
from backend.app.core.friction_model import round_trip_friction  # noqa: E402

LOT = scrip_master.get_lot_size("NIFTY")
CAPITAL_LOW, CAPITAL_HIGH = 50_000, 100_000
PREMIUMS = [10, 20, 30, 50, 75, 100, 150, 200, 300]

# Per-trade risk budgets as a fraction of capital. 1% is the conventional
# ceiling for a strategy that must survive a losing streak; 2% is aggressive.
RISK_BUDGETS = [0.01, 0.02]
STOP_FRACTION = 0.40  # a long option stopped out at -40% of premium paid


def rt(premium: float, qty: int) -> float:
    """Round-trip friction for a 1-leg long option: buy then sell."""
    f = round_trip_friction(
        [{"side": "BUY", "price": premium, "quantity": qty, "instrument": "option"}],
        [{"side": "SELL", "price": premium, "quantity": qty, "instrument": "option"}],
    )
    return f["total"]


def main():
    print("=" * 78)
    print("PHASE 2 CAPITAL ARITHMETIC — Rs 50k-1L, NIFTY 1-leg long option")
    print(f"NIFTY lot = {LOT} units (scrip master)")
    print("=" * 78)

    print("\n1. COST OF ONE LOT, AND WHAT FRICTION COSTS AS A SHARE OF IT")
    print(f"{'premium':>8} {'1 lot cost':>12} {'round-trip':>11} {'friction':>10} "
          f"{'move to':>9} {'% of 50k':>9} {'% of 1L':>8}")
    print(f"{'(Rs)':>8} {'(Rs)':>12} {'friction Rs':>11} {'as % pos':>10} "
          f"{'breakeven':>9} {'':>9} {'':>8}")
    print("-" * 78)
    for p in PREMIUMS:
        cost = p * LOT
        f = rt(p, LOT)
        pct = 100 * f / cost
        # premium move needed just to cover friction, in rupees per unit
        be = f / LOT
        print(f"{p:>8} {cost:>12,.0f} {f:>11,.0f} {pct:>9.2f}% {be:>8.2f} "
              f"{100*cost/CAPITAL_LOW:>8.1f}% {100*cost/CAPITAL_HIGH:>7.1f}%")

    print("\n2. WHAT IS AFFORDABLE AT A SANE PER-TRADE RISK")
    print(f"   (stop at -{STOP_FRACTION:.0%} of premium; 1 lot = {LOT} units)")
    for cap in (CAPITAL_LOW, CAPITAL_HIGH):
        print(f"\n   capital Rs {cap:,}")
        for budget in RISK_BUDGETS:
            risk_rs = cap * budget
            max_prem = risk_rs / (STOP_FRACTION * LOT)
            f = rt(max_prem, LOT)
            print(f"     risk {budget:.0%} = Rs {risk_rs:>6,.0f}/trade  ->  "
                  f"max premium Rs {max_prem:5.1f}/unit  "
                  f"(1 lot = Rs {max_prem*LOT:6,.0f}, friction Rs {f:,.0f} "
                  f"= {100*f/(max_prem*LOT):.1f}% of position)")

    print("\n3. THE EDGE REQUIRED, PER TRADE, TO HIT THE MONTHLY-PROFIT PREFERENCE")
    print("   Target: beat a 7% FD. On Rs 75,000 that is Rs 5,250/yr just to match.")
    for cap in (CAPITAL_LOW, 75_000, CAPITAL_HIGH):
        fd = cap * 0.07
        for target_cagr in (0.15, 0.30):
            need = cap * target_cagr
            for trades_per_year in (100, 250, 500):
                per_trade = need / trades_per_year
                print(f"   cap {cap:>7,}  CAGR {target_cagr:.0%} = Rs {need:>7,.0f}/yr  "
                      f"@ {trades_per_year:>3} trades/yr -> Rs {per_trade:>6,.0f}/trade net")
            break  # one CAGR row per capital is enough to make the point
        print(f"      (FD alternative on this capital: Rs {fd:,.0f}/yr, zero hours)")

    print("\n4. THE BINDING CONSTRAINT, STATED PLAINLY")
    print("   Friction here is ~90% flat brokerage (Rs 20/order + GST), so it is")
    print("   near-CONSTANT in rupees per trade. Its share therefore rises as the")
    print("   position shrinks — and small capital forces small positions.")
    print()
    for cap, budget in ((50_000, 0.01), (75_000, 0.01), (100_000, 0.02)):
        risk_rs = cap * budget
        max_prem = risk_rs / (STOP_FRACTION * LOT)
        pos = max_prem * LOT
        f = rt(max_prem, LOT)
        for tpy in (250,):
            need_net = cap * 0.15 / tpy
            gross = need_net + f
            print(f"   Rs {cap:>7,} @ {budget:.0%} risk: option ~Rs {max_prem:4.0f}/unit "
                  f"(pos Rs {pos:>5,.0f}), friction Rs {f:.0f} = {100*f/pos:.1f}% of pos")
            print(f"       15% CAGR over {tpy} trades/yr = Rs {need_net:.0f} net/trade "
                  f"-> Rs {gross:.0f} gross = {100*gross/pos:.1f}% avg move")
            print(f"       ** friction is {100*f/gross:.0f}% of the gross edge required **")
    print()
    print("   So the honest statement is NOT that the required move is implausible —")
    print("   a 5% average move on a cheap option is a real but ordinary edge. It is")
    print("   that HALF the gross edge is consumed by friction before anything is")
    print("   left over — and that this barely improves with capital across the whole")
    print("   Rs 50k-1L range (62% -> 49%). It does not improve because the net target")
    print("   scales with capital while friction stays flat, so the two move together.")
    print("   Capital is therefore NOT the lever on this ratio; trade frequency is.")
    print("   Halving trades/yr halves the friction bill and doubles the per-trade")
    print("   target, which is the trade-off Phase 2's arena design has to make.")
    print()
    print("   Compare Phase 1's dead arenas: the 4-leg earnings straddle paid 23x its")
    print("   edge in friction. Paying 0.5x is a different regime entirely — the")
    print("   vehicle is no longer the thing killing the trade. But it does mean the")
    print("   gross edge must be ~2x the net target, and that is what Phase 2 has to")
    print("   find before anything else is worth building.")


if __name__ == "__main__":
    main()
