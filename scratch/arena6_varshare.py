"""
Arena 6 (intraday_option) — is any intraday window's variance share below its
time share by enough to trade?

Registered as 'intraday-varshare-modern' BEFORE this ran (4fceff7). Windows,
conditionings, all four kill conditions and the poor prior are fixed in the kill
log.

Non-directional by construction: the measured quantity is variance, which has no
sign.

Kill conditions, ALL required:
  (a) variance share < time share, |t| >= 2.23
  (b) resulting edge >= 2x the measured cost of 4 orders
  (c) holds in EVERY calendar year with >= 60 days
  (d) LEFT TAIL survivable — mandatory; it is what killed Arena 1

Usage:  ./venv/Scripts/python.exe scratch/arena6_varshare.py
"""
import datetime
import json
import math
import os
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, os.getcwd())

from backend.app.core import scrip_master  # noqa: E402
from backend.app.core.bs_math import price as bs_price  # noqa: E402
from backend.app.core.friction_model import round_trip_friction  # noqa: E402
from research import charter  # noqa: E402

SRC = os.path.join("data", "intraday", "NIFTY", "index.parquet")
OUT = os.path.join("scratch", "arena6_varshare_results.json")

SESSION_START = datetime.time(9, 15)
SESSION_END = datetime.time(15, 30)
SESSION_MIN = 375

WINDOWS = [
    ("09:15-10:00", datetime.time(9, 15), datetime.time(10, 0)),
    ("10:00-11:00", datetime.time(10, 0), datetime.time(11, 0)),
    ("11:00-13:00", datetime.time(11, 0), datetime.time(13, 0)),
    ("13:00-14:30", datetime.time(13, 0), datetime.time(14, 30)),
    ("14:30-15:30", datetime.time(14, 30), datetime.time(15, 30)),
    ("11:00-14:30", datetime.time(11, 0), datetime.time(14, 30)),
]
CONDITIONINGS = ["all", "prior_day_low_vol"]
N_CONFIGS = 12
MIN_DAYS_PER_YEAR = 60

LOT = scrip_master.get_lot_size("NIFTY")
EQUITY = min(charter.TRADING_CAPITAL_RANGE_RS)
TAIL_LIMIT = 0.15 * EQUITY          # condition (d): 15% of equity
IV = 0.12                            # NIFTY ATM IV, the level used for sizing theta
DTE_FOR_STRADDLE = 4                 # a typical short-dated ATM straddle


def straddle_cost_4_orders(premium_per_leg: float) -> float:
    """Measured cost of a 1-lot short straddle round trip: 2 legs, 4 orders.

    Amendment E9: statutory charges from friction_model plus the measured
    half-spread of Rs 0.25/leg crossed on entry and exit, per leg.
    """
    legs_open = [{"side": "SELL", "price": premium_per_leg, "quantity": LOT,
                  "instrument": "option"} for _ in range(2)]
    legs_close = [{"side": "BUY", "price": premium_per_leg, "quantity": LOT,
                   "instrument": "option"} for _ in range(2)]
    statutory = round_trip_friction(legs_open, legs_close)["total"]
    spread = 2 * 2 * 0.25 * LOT     # 2 legs x (entry+exit) x Rs 0.25 half-spread
    return statutory + spread


def load():
    df = pd.read_parquet(SRC)
    t = df["ts"].dt.time
    df = df[(t >= SESSION_START) & (t <= SESSION_END)].copy()
    df["date"] = df["ts"].dt.date
    df["year"] = df["ts"].dt.year
    df["time"] = df["ts"].dt.time
    print(f"loaded {len(df):,} in-session bars, {df['date'].nunique()} sessions "
          f"({df['ts'].min().date()} .. {df['ts'].max().date()})")
    return df


def main():
    df = load()
    bar_t = charter.noise_threshold(N_CONFIGS)

    # per-session realised variance, total and per window
    print("computing per-session variance shares...")
    recs = []
    for d, g in df.groupby("date", sort=True):
        g = g.sort_values("ts")
        r = g["close"].astype(float).pct_change()
        tot = float(np.nansum(r ** 2))
        if tot <= 0:
            continue
        row = {"date": d, "year": g["year"].iloc[0], "total_var": tot,
               "spot": float(g["close"].iloc[0])}
        tt = g["time"].to_numpy()
        rr = (r ** 2).to_numpy()
        for name, a, b in WINDOWS:
            m = (tt >= a) & (tt < b)
            row[name] = float(np.nansum(rr[m]))
        recs.append(row)
    var = pd.DataFrame(recs).sort_values("date").reset_index(drop=True)
    # past-only day conditioning: PRIOR day's realised vol, never today's
    var["prior_var"] = var["total_var"].shift(1)
    var = var.dropna(subset=["prior_var"])
    low_cut = var["prior_var"].expanding().quantile(0.33)
    var["is_low_prior"] = var["prior_var"] <= low_cut

    spot = float(var["spot"].median())
    # an ATM straddle's premium at IV=12%, 4 DTE, and its total gamma-theta scale
    T = DTE_FOR_STRADDLE / 365.0
    atm_leg = bs_price(spot, round(spot / 50) * 50, T, IV, "CE")
    cost_4 = straddle_cost_4_orders(atm_leg)
    print(f"\nATM leg premium at IV {IV:.0%}, {DTE_FOR_STRADDLE} DTE: Rs {atm_leg:,.2f}")
    print(f"cost of a 1-lot short straddle round trip (4 orders): Rs {cost_4:,.2f}")
    print(f"Section 4 bar at N={N_CONFIGS}: |t| >= {bar_t:.2f}")
    print(f"left-tail limit (d): Rs {TAIL_LIMIT:,.0f} = 15% of Rs {EQUITY:,.0f}\n")

    results = []
    print(f"{'window':<13} {'cond':<18} {'days':>5} {'time%':>7} {'var%':>7} "
          f"{'short':>7} {'t':>7} {'edge Rs':>9} {'cost Rs':>8} {'yrs+':>6} {'worst Rs':>10}")
    print("-" * 108)

    for name, a, b in WINDOWS:
        wmin = (datetime.datetime.combine(datetime.date.today(), b) -
                datetime.datetime.combine(datetime.date.today(), a)).seconds / 60.0
        time_share = wmin / SESSION_MIN

        for cond in CONDITIONINGS:
            sub = var if cond == "all" else var[var["is_low_prior"]]
            sub = sub[sub[name] > 0]
            if len(sub) < 100:
                continue

            share = (sub[name] / sub["total_var"]).to_numpy()
            shortfall = time_share - share           # >0 means quieter than the clock
            mean_short = float(shortfall.mean())
            sd = float(shortfall.std())
            t = mean_short / (sd / math.sqrt(len(share))) if sd > 0 else 0.0

            # Translate to rupees. Over the window a short straddle collects theta
            # proportional to time_share of the day's decay, and pays realised
            # variance. Using the BS identity, the net is
            #   0.5 * gamma * S^2 * (implied_var_over_window - realised_var)
            # and implied_var_over_window = total_implied_day_var * time_share.
            # Scale = 0.5 * gamma * S^2 for a 1-lot ATM straddle; for an ATM
            # option gamma*S^2 ~ premium/(sigma^2 * T) per unit, so the whole
            # bracket reduces to premium-per-unit scaled by the variance ratio.
            # bracket is in YEARS: dt for the window, minus RV expressed in
            # variance-time via RV/sigma^2.
            realised_win = (sub[name]).to_numpy() / (IV ** 2)
            implied_win = time_share / 252.0
            # Rupees for a 1-lot 2-leg straddle.
            #   P&L_short = |theta_annual| * (dt - RV/sigma^2)
            # and for an ATM option theta_annual ~ premium/(2T), so a straddle's
            # scale is 2 * LOT * premium/(2T) = LOT * premium / T.
            # An earlier version used 2*LOT*premium/(IV^2 * T) against a bracket
            # that already carried IV^2 — exactly 2x too large, which produced an
            # "edge" of Rs 1,706 against only Rs 1,354 of theta actually available
            # over the window. An edge above the total decay collected is
            # impossible, which is how the error surfaced.
            scale = LOT * atm_leg / T
            pnl = scale * (implied_win - realised_win)
            edge = float(pnl.mean())
            worst = float(pnl.min())
            p1 = float(np.quantile(pnl, 0.01))

            per_year, yrs_pos = {}, 0
            for yr, gy in sub.groupby("year"):
                if len(gy) < MIN_DAYS_PER_YEAR:
                    continue
                sh = float((time_share - gy[name] / gy["total_var"]).mean())
                per_year[int(yr)] = round(sh, 5)
                if sh > 0:
                    yrs_pos += 1
            n_years = len(per_year)
            every_year = n_years > 0 and yrs_pos == n_years

            # (d) tail: worst day within 15% of equity, and worst 1% must not
            # erase a year of mean edge (250 sessions x mean, vs 2.5 worst days)
            n_worst = max(int(0.01 * len(pnl)), 1)
            worst_sum = float(np.sort(pnl)[:n_worst].sum())
            year_edge = edge * 250
            tail_ok = (abs(worst) <= TAIL_LIMIT) and (year_edge + worst_sum > 0)

            rec = {
                "window": name, "conditioning": cond, "days": int(len(sub)),
                "time_share": round(time_share, 4),
                "var_share": round(float(share.mean()), 4),
                "shortfall": round(mean_short, 5), "t": round(t, 2),
                "edge_rs": round(edge, 2), "cost_rs": round(cost_4, 2),
                "worst_day_rs": round(worst, 2), "p1_rs": round(p1, 2),
                "worst1pct_sum_rs": round(worst_sum, 2),
                "year_edge_rs": round(year_edge, 2),
                "per_year_shortfall": per_year,
                "years": n_years, "years_positive": yrs_pos,
                "a_significant": bool(mean_short > 0 and t >= bar_t),
                "b_edge_2x_cost": bool(edge >= 2 * cost_4),
                "c_every_year": bool(every_year),
                "d_tail_ok": bool(tail_ok),
            }
            rec["clears_all"] = all(rec[k] for k in
                                    ("a_significant", "b_edge_2x_cost",
                                     "c_every_year", "d_tail_ok"))
            results.append(rec)
            print(f"{name:<13} {cond:<18} {len(sub):>5} {100*time_share:>6.1f}% "
                  f"{100*share.mean():>6.1f}% {mean_short:>+7.4f} {t:>+7.2f} "
                  f"{edge:>9,.0f} {cost_4:>8,.0f} {yrs_pos}/{n_years:<4} {worst:>10,.0f}")

    print("\n" + "=" * 108)
    for k, lab in (("a_significant", f"(a) variance share < time share, |t| >= {bar_t:.2f}"),
                   ("b_edge_2x_cost", "(b) edge >= 2x cost of 4 orders"),
                   ("c_every_year", "(c) holds in every year"),
                   ("d_tail_ok", "(d) LEFT TAIL survivable")):
        print(f"  {lab:<48} {sum(1 for r in results if r[k])} of {len(results)}")

    survivors = [r for r in results if r["clears_all"]]
    verdict = "advance" if survivors else "killed"
    print()
    if survivors:
        print(f"VERDICT: {len(survivors)} cell(s) clear ALL FOUR.")
        for r in survivors:
            print(f"  {r['window']} ({r['conditioning']}): edge Rs {r['edge_rs']:,.0f} "
                  f"vs cost Rs {r['cost_rs']:,.0f}, worst day Rs {r['worst_day_rs']:,.0f}")
        print("\nB5: 'worth a walk-forward at strategy level', never 'works'.")
    else:
        print("VERDICT: KILLED. No window's variance shortfall is tradeable.")
        print("  Arena 'intraday_option' is RECOMMENDED FOR CLOSURE, which would")
        print("  close the last open arena and complete the Phase 2 survey.")

    with open(OUT, "w", encoding="utf-8") as f:
        json.dump({"run_at": datetime.datetime.now().isoformat(),
                   "hypothesis": "intraday-varshare-modern", "t_bar": bar_t,
                   "cost_4_orders_rs": cost_4, "atm_leg_premium": atm_leg,
                   "tail_limit_rs": TAIL_LIMIT,
                   "verdict": verdict, "results": results}, f, indent=2)
    print(f"\nraw -> {OUT}")


if __name__ == "__main__":
    main()
