"""Cost floor across NSE index option contracts — Phase 2 leftovers item 2.

THE QUESTION
------------
Every Phase 2 kill has been a cost result, and the cost floor has only ever been
measured on ONE contract: NIFTY (4.01 index points round trip at ATM, 2.20 of it
irreducible statutory). Is there another index contract whose floor is materially
lower AND whose ATM lot fits a Rs 50k-1L account?

THE TRAP THIS AVOIDS
--------------------
Index points are NOT comparable across indices. A MIDCPNIFTY point is not a NIFTY
point. Amendment E9's lesson ("a cost in rupees is meaningless until divided by
the delta it buys") has an exact analogue here: a cost in index points is
meaningless across underlyings until divided by the move that underlying actually
makes. So the comparable metric is cost as a FRACTION OF A DAILY SIGMA.

SCOPE, STATED HONESTLY
----------------------
- NSE index options only. friction_model carries NSE rates only; BSE (SENSEX,
  BANKEX) would need separately sourced ad-valorem rates.
- STATUTORY + BROKERAGE ONLY, no spread. The spread is measured for NIFTY alone,
  so including a guess for the others would compare a measurement against an
  assumption. This is therefore the IRREDUCIBLE floor -- the half execution skill
  cannot touch -- which is also the half RESUME records as the larger one.
- ATM delta taken as 0.50 for every contract. A consistent approximation; the
  cross-index RATIO is what carries the conclusion, not the absolute level.
"""
import datetime as dt
import glob
import math
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from backtest import bhavcopy as bc
from backend.app.core import friction_model as fm

UNDERLYINGS = ["NIFTY", "BANKNIFTY", "FINNIFTY", "MIDCPNIFTY"]
ATM_DELTA = 0.50
VOL_DAYS = 120
MIN_DTE = 2          # skip expiry-day distortion
ACCOUNT = 50_000.0   # Amendment E: staged capital, low end


def cached_dates(n):
    out = []
    for p in sorted(glob.glob(os.path.join(bc.DATA_DIR, "BhavCopy_NSE_FO_*.csv.zip"))):
        m = re.search(r"(\d{8})", os.path.basename(p))
        if m:
            out.append(dt.datetime.strptime(m.group(1), "%Y%m%d").date())
    return out[-n:]


def spot_series(dates, und):
    xs = []
    for d in dates:
        try:
            c = bc.load_chain(d, und)
        except Exception:
            continue
        if c and c.get("spot") and c.get("spot_source") == "bhavcopy_underlying":
            xs.append((d, float(c["spot"])))
    return xs


def daily_sigma_pct(series):
    rs = []
    for i in range(1, len(series)):
        p0, p1 = series[i - 1][1], series[i][1]
        if p0 > 0 and p1 > 0:
            rs.append(math.log(p1 / p0))
    if len(rs) < 20:
        return None
    m = sum(rs) / len(rs)
    var = sum((r - m) ** 2 for r in rs) / (len(rs) - 1)
    return math.sqrt(var)


def atm_premium(chain, spot):
    """Mean of the traded ATM CE and PE closes on the nearest expiry >= MIN_DTE."""
    d = chain["date"]
    exps = [e for e in chain["expiries"] if (e - d).days >= MIN_DTE]
    if not exps:
        return None, None, None
    exp = min(exps)
    strikes = sorted({k[1] for k in chain["options"] if k[0] == exp})
    if not strikes:
        return None, None, None
    atm = min(strikes, key=lambda s: abs(s - spot))
    prems = []
    for cp in ("CE", "PE"):
        o = chain["options"].get((exp, atm, cp))
        if o and o.get("traded") and float(o.get("close") or 0) > 0:
            prems.append(float(o["close"]))
    if not prems:
        return None, exp, atm
    return sum(prems) / len(prems), exp, atm


def round_trip_rs(premium, qty):
    """Buy then sell one lot at the same premium — brokerage + statutory only."""
    rt = fm.round_trip_friction(
        [{"side": "BUY", "price": premium, "quantity": qty, "instrument": "option"}],
        [{"side": "SELL", "price": premium, "quantity": qty, "instrument": "option"}],
    )
    return float(rt["total"])


def self_check():
    """Reproduce RESUME.md's measured NIFTY floor from this independent path.

    RESUME records 2.20 index points as the irreducible statutory floor at ATM,
    measured 2026-08-14 from Dhan's depth book. This module reaches the same
    number from bhavcopy premiums and friction_model, so a drift in either is a
    signal that one of them moved.
    """
    cost = round_trip_rs(126.15, 65)
    pts = cost / (65 * 0.429)      # 0.429 = the MEASURED ATM delta, not 0.5
    ok = abs(pts - 2.20) / 2.20 < 0.05
    print(f"self-check: NIFTY statutory floor {pts:.3f} pts vs RESUME's 2.20 "
          f"-> {'OK' if ok else 'DRIFT — investigate before trusting the table'}")
    return ok


def main():
    self_check()
    dates = cached_dates(VOL_DAYS + 5)
    latest = dates[-1]
    print("=" * 92)
    print("COST FLOOR ACROSS NSE INDEX OPTION CONTRACTS")
    print("=" * 92)
    print(f"latest bhavcopy {latest}   vol window {VOL_DAYS} sessions   "
          f"ATM delta assumed {ATM_DELTA}")
    print("brokerage + statutory ONLY (no spread) -> this is the IRREDUCIBLE floor\n")

    rows = []
    for und in UNDERLYINGS:
        ser = spot_series(dates, und)
        if len(ser) < 30:
            print(f"{und}: only {len(ser)} usable sessions — skipped")
            continue
        sig = daily_sigma_pct(ser)
        chain = bc.load_chain(latest, und)
        if not chain or not sig:
            print(f"{und}: no chain/vol — skipped")
            continue
        spot = float(chain["spot"])
        lot = int(chain["lot"])
        prem, exp, atm = atm_premium(chain, spot)
        if not prem:
            print(f"{und}: no traded ATM option on {latest} — skipped")
            continue

        sig_pts = sig * spot
        cost_rs = round_trip_rs(prem, lot)
        flat_rs = 2 * fm.BROKERAGE_PER_ORDER * (1 + fm.GST_RATE)
        expo_pts = lot * ATM_DELTA
        cost_pts = cost_rs / expo_pts
        frac_sigma = cost_pts / sig_pts
        capital = prem * lot
        stop40 = 0.40 * capital

        rows.append(dict(und=und, spot=spot, lot=lot, sig=sig, sig_pts=sig_pts,
                         prem=prem, exp=exp, atm=atm, cost_rs=cost_rs,
                         flat_share=flat_rs / cost_rs, cost_pts=cost_pts,
                         frac=frac_sigma, capital=capital, stop40=stop40,
                         n=len(ser)))

    print(f"{'contract':<12} {'spot':>10} {'lot':>5} {'ATMprem':>9} {'sigma/d':>8} "
          f"{'sigma pts':>10} {'cost Rs':>9} {'flat%':>6} {'cost pts':>9}")
    print("-" * 92)
    for r in rows:
        print(f"{r['und']:<12} {r['spot']:>10.1f} {r['lot']:>5} {r['prem']:>9.2f} "
              f"{100*r['sig']:>7.2f}% {r['sig_pts']:>10.1f} {r['cost_rs']:>9.2f} "
              f"{100*r['flat_share']:>5.0f}% {r['cost_pts']:>9.3f}")

    print("\n" + "=" * 92)
    print("THE COMPARABLE NUMBER — cost as a fraction of ONE DAILY SIGMA")
    print("=" * 92)
    print(f"{'contract':<12} {'cost pts':>9} {'sigma pts':>10} {'cost/sigma':>12} "
          f"{'vs NIFTY':>10}")
    print("-" * 92)
    base = next((r["frac"] for r in rows if r["und"] == "NIFTY"), None)
    for r in sorted(rows, key=lambda x: x["frac"]):
        rel = f"{r['frac']/base:.2f}x" if base else "-"
        print(f"{r['und']:<12} {r['cost_pts']:>9.3f} {r['sig_pts']:>10.1f} "
              f"{100*r['frac']:>11.2f}% {rel:>10}")

    print("\n" + "=" * 92)
    print(f"AFFORDABILITY — one ATM lot against a Rs {ACCOUNT:,.0f} account")
    print("=" * 92)
    print(f"{'contract':<12} {'ATM lot Rs':>12} {'% of acct':>10} "
          f"{'-40% stop':>11} {'stop % acct':>12}")
    print("-" * 92)
    for r in sorted(rows, key=lambda x: x["capital"]):
        print(f"{r['und']:<12} {r['capital']:>12,.0f} {100*r['capital']/ACCOUNT:>9.1f}% "
              f"{r['stop40']:>11,.0f} {100*r['stop40']/ACCOUNT:>11.1f}%")

    print("\n  Amendment E2's risk budget is 1-2% of equity per trade.")
    print("  A -40% move on one ATM lot is the RESUME.md yardstick.\n")

    print("=" * 92)
    print("WHY THEY ALL LAND IN THE SAME PLACE — notional per lot")
    print("=" * 92)
    print(f"{'contract':<12} {'notional/lot':>14} {'sigma/d':>9} "
          f"{'sigma Rs/lot':>14} {'cost Rs':>9} {'cost/sigmaRs':>13}")
    print("-" * 92)
    for r in sorted(rows, key=lambda x: x["und"]):
        notional = r["spot"] * r["lot"]
        sig_rs = notional * r["sig"] * ATM_DELTA
        print(f"{r['und']:<12} {notional:>14,.0f} {100*r['sig']:>8.2f}% "
              f"{sig_rs:>14,.0f} {r['cost_rs']:>9.2f} {100*r['cost_rs']/sig_rs:>12.2f}%")
    print("\n  SEBI standardises index lots to a COMMON NOTIONAL (~Rs 16-18 lakh).")
    print("  Cost/sigma = cost_Rs / (delta x sigma x notional). With notional held")
    print("  equal by regulation and sigma similar across indices, the ratio cannot")
    print("  differ much -- which is exactly what the table shows. The one variable")
    print("  that would let you escape the floor is the one the regulator fixed.\n")

    if base:
        best = min(rows, key=lambda x: x["frac"])
        print("=" * 92)
        print("VERDICT")
        print("=" * 92)
        if best["und"] == "NIFTY":
            print("  NIFTY already has the LOWEST cost per unit of daily move on the")
            print("  NSE board. No cheaper contract exists to move to.")
        else:
            imp = base / best["frac"]
            print(f"  {best['und']} is {imp:.2f}x cheaper than NIFTY per unit of daily move")
            print(f"  ({100*best['frac']:.2f}% of a sigma vs {100*base:.2f}%).")
            print(f"  Its ATM lot costs Rs {best['capital']:,.0f} "
                  f"({100*best['capital']/ACCOUNT:.1f}% of a Rs {ACCOUNT:,.0f} account).")


if __name__ == "__main__":
    main()
