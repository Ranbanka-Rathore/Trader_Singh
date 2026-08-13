"""Does a ~2 vol point premium survive friction on weekly NIFTY?

The term-structure measurement found a real variance risk premium at both tenors
-- near +2.38 vol points, far +1.84 -- and no difference between them worth
trading. So arena 1's remaining question is whether the LEVEL is harvestable at
all, or whether costs eat it before it reaches the account.

The clean way to ask that is to put friction in the same units as the premium.
An option's vega says how many rupees a vol point is worth; the friction model
says how many rupees a round trip costs. Divide, and the cost becomes a number of
vol points that can be compared directly against 2.38.

    friction_vol_points = round_trip_friction_Rs / (net_short_vega_per_vol_point)

If that exceeds the premium, no entry rule, exit rule or parameter fixes it --
the structure is paying more to put on than the edge is worth. This is a property
of the instrument and the cost schedule, not of any strategy, so it is a
measurement and not a hypothesis.

Costs are the project's own, not invented here: `friction_model.basket_friction`
(brokerage, STT, exchange, SEBI, IPFT, stamp, GST) plus the backtester's
`slippage_per_leg = 0.75` per leg per side.

THE KEY ASYMMETRY BEING TESTED. Defined-risk structures buy a wing to cap the
loss, and the wing gives back vega. Friction scales with the NUMBER OF LEGS while
the edge scales with NET SHORT VEGA, so a narrow spread pays four legs of cost to
keep a small fraction of the premium. That is the mechanism that would explain
the iron condor's death (-Rs 4,936 over 18 OOS trades, "double the friction of a
directional spread"), and it is what this quantifies.

NOT A HYPOTHESIS. No claim registered, no budget spent, no P&L path.
"""
import collections
import datetime as dt
import math
import statistics as st
import sys

sys.path.insert(0, r"D:\Projects\Agentic_Trader")

from backend.app.core import bs_math, friction_model
from backtest import bhavcopy, futures
from backtest.liquidity_gate import LiquidityGate, gate_by_name
from research import charter

UNDERLYING = "NIFTY"
GATE = "strict_legacy"
ENTRY_DTE = (7, 6, 8, 5)
SLIPPAGE_PER_LEG = 0.75          # real_backtester.py:51
VRP_NEAR_VOL_PTS = 2.38          # measured, arena1_term_vrp.py

# Structures, as (name, [(side, strike_offset_in_steps, side_of_chain)]).
# Offsets are in strike intervals from ATM; CE above, PE below.
STRUCTURES = {
    "short straddle (naked, 2 legs)": [("SELL", 0, "CE"), ("SELL", 0, "PE")],
    "short strangle 2x (naked, 2 legs)": [("SELL", 2, "CE"), ("SELL", -2, "PE")],
    "iron condor 2/6 (4 legs)": [("SELL", 2, "CE"), ("BUY", 6, "CE"),
                                 ("SELL", -2, "PE"), ("BUY", -6, "PE")],
    "iron condor 2/4 (4 legs, narrow)": [("SELL", 2, "CE"), ("BUY", 4, "CE"),
                                         ("SELL", -2, "PE"), ("BUY", -4, "PE")],
    "iron butterfly 0/6 (4 legs)": [("SELL", 0, "CE"), ("BUY", 6, "CE"),
                                    ("SELL", 0, "PE"), ("BUY", -6, "PE")],
}


def vega(s, k, t, sigma):
    if t <= 0 or sigma <= 0:
        return 0.0
    d1 = bs_math._d1(s, k, t, sigma, bs_math.DEFAULT_R)
    return s * bs_math._norm_pdf(d1) * math.sqrt(t)


def gated(chain, expiry, strike, side, gate):
    o = chain["options"].get((expiry, float(strike), side))
    if not o:
        return None
    ok, _ = gate.leg_ok({"close": o.get("close"), "traded": o.get("traded"),
                         "volume": o.get("volume"), "txns": o.get("txns"),
                         "oi": o.get("oi")})
    return float(o["close"]) if ok else None


def main():
    start, end = charter.era_window("modern", cap=dt.date.today())
    sessions = set(futures.trading_dates(start, end))
    gate = LiquidityGate(gate_by_name(GATE))

    seen = set()
    for d in sorted(sessions)[::3]:
        df = bhavcopy.read_df_cached(d)
        if df is None:
            continue
        sym = df[(df["TckrSymb"] == UNDERLYING) & (df["FinInstrmTp"] == "IDO")]
        for v in sym["XpryDt"].unique():
            try:
                seen.add(dt.date.fromisoformat(str(v)[:10]))
            except ValueError:
                pass
    expiries = sorted(x for x in seen if start <= x <= end)

    out = collections.defaultdict(list)
    n_cycles = 0
    for near in expiries:
        entry = next((near - dt.timedelta(days=k) for k in ENTRY_DTE
                      if (near - dt.timedelta(days=k)) in sessions), None)
        if entry is None:
            continue
        chain = bhavcopy.load_chain(entry, UNDERLYING)
        if not chain or not chain.get("spot"):
            continue
        spot = float(chain["spot"])
        step = bhavcopy.infer_strike_interval(chain, near, spot) or 50
        atm = float(round(spot / step) * step)
        lot = (chain.get("lot_by_expiry") or {}).get(near) or chain.get("lot") or 0
        if not lot:
            continue
        t = max((near - entry).days, 1) / 365.0
        n_cycles += 1

        for name, legs in STRUCTURES.items():
            prices, vegas, ok = [], [], True
            for side, off, cp in legs:
                k = atm + off * step
                px = gated(chain, near, k, cp, gate)
                if px is None or px <= 0.05:
                    ok = False
                    break
                iv = bs_math.implied_vol(px, spot, k, t, cp)
                if not iv or iv <= 0:
                    ok = False
                    break
                v = vega(spot, k, t, iv)
                prices.append((side, cp, px))
                vegas.append(-v if side == "SELL" else +v)
            if not ok:
                continue

            # Net SHORT vega is what earns the premium. Sign: SELL contributes
            # negative vega, so net short exposure is -sum.
            net_short_vega = -sum(vegas)
            if net_short_vega <= 0:
                continue
            # Rupees per vol POINT per lot (vega is per 1.00 of sigma).
            rs_per_vol_pt = net_short_vega * lot / 100.0

            qty = lot
            basket = [{"side": s, "opt_type": cp.lower(), "price": px,
                       "quantity": qty} for s, cp, px in prices]
            # Round trip: charged on the way in and on the way out.
            fr = (friction_model.basket_friction(basket)["total"]
                  + friction_model.basket_friction(basket)["total"])
            slip = SLIPPAGE_PER_LEG * len(legs) * 2 * qty
            total = fr + slip

            out[name].append({
                "vol_pts": total / rs_per_vol_pt if rs_per_vol_pt > 0 else float("inf"),
                "friction": total, "rs_per_vol_pt": rs_per_vol_pt,
                "slip_share": slip / total if total else 0.0,
            })

    print(f"NIFTY weekly, {start} -> {end}   {n_cycles} cycles")
    print(f"premium to beat: {VRP_NEAR_VOL_PTS:.2f} vol points "
          f"(near-tenor VRP, measured)\n")
    print(f"{'structure':36s} {'n':>4} {'Rs/volpt':>10} {'friction Rs':>12} "
          f"{'COST vol pts':>13} {'slip%':>6}  verdict")
    print("-" * 104)
    for name in STRUCTURES:
        rows = out.get(name) or []
        if not rows:
            print(f"{name:36s} {'-':>4}  (never fully tradeable)")
            continue
        vp = sorted(r["vol_pts"] for r in rows)
        med = vp[len(vp) // 2]
        verdict = ("SURVIVES" if med < VRP_NEAR_VOL_PTS
                   else f"{med/VRP_NEAR_VOL_PTS:.1f}x the premium")
        print(f"{name:36s} {len(rows):>4} "
              f"{st.median(r['rs_per_vol_pt'] for r in rows):>10,.0f} "
              f"{st.median(r['friction'] for r in rows):>12,.0f} "
              f"{med:>13.2f} "
              f"{100*st.median(r['slip_share'] for r in rows):>5.0f}%  {verdict}")

    print(f"\n  COST vol pts = round-trip friction expressed in vol points of the")
    print(f"  structure's own net short vega. Below {VRP_NEAR_VOL_PTS:.2f} the")
    print(f"  premium survives; above it, no entry or exit rule can rescue it.")


if __name__ == "__main__":
    main()
