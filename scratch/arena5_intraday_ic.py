"""
Arena 5, screen 1 — does anything predict NIFTY intraday index direction?

Registered as 'intraday-ic-modern' BEFORE this was run (see
scratch/register_arena5.py, committed at 340f4f1). Claim, kill criterion,
signals, horizons and thresholds are all fixed in the kill log; this file only
computes them.

The kill criterion is a conjunction of three:
  (a) |t| >= 2.35 on the pooled Spearman IC          [Section 4, N=16]
  (b) the same IC sign in EVERY calendar year >= 60 days   [Amendment D5]
  (c) implied gross edge >= 2x the E5 net target      [Amendment E5]

Nothing here executes a fill, so no liquidity gate applies; a surviving cell
would still have to be re-run as a strategy under a real gate before it meant
anything.

Usage:  ./venv/Scripts/python.exe scratch/arena5_intraday_ic.py
"""
import datetime
import json
import os
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, os.getcwd())

from backend.app.core import scrip_master  # noqa: E402
from backend.app.core.friction_model import round_trip_friction  # noqa: E402
from research import charter  # noqa: E402

LOT = scrip_master.get_lot_size("NIFTY")
# ATM long option: the cheapest vehicle Amendment E4 says this capital can hold.
DELTA = 0.5
SPOT = 24350.0
# E4: at Rs 50k and 1% risk the affordable premium is ~Rs 19/unit.
OPTION_PREMIUM = 19.0


# backtest/real_backtester.py Config.slippage_per_leg. friction_model prices
# brokerage and statutory charges only — it carries NO bid/ask. Leaving the
# spread out is the single most expensive mistake available here: on a Rs 19
# option this is ~4% per side, and it is paid in BOTH directions. Phase 1 learned
# exactly this ("the spread is paid in both directions") when it stopped a
# well-motivated but wrong long-vol hypothesis.
SLIPPAGE_PER_LEG = 0.75


def rt_friction(premium: float, qty: int, slippage_per_leg: float = SLIPPAGE_PER_LEG) -> float:
    """Round-trip cost for a 1-leg long option: charges AND the spread crossed twice."""
    charges = round_trip_friction(
        [{"side": "BUY", "price": premium, "quantity": qty, "instrument": "option"}],
        [{"side": "SELL", "price": premium, "quantity": qty, "instrument": "option"}],
    )["total"]
    spread = 2.0 * slippage_per_leg * qty  # crossed on entry and again on exit
    return charges + spread

SRC = os.path.join("data", "intraday", "NIFTY", "index.parquet")
OUT = os.path.join("scratch", "arena5_ic_results.json")

HORIZONS = [5, 15, 30, 60]
SIGNALS = ["mom_h", "vwap_dev", "or_pos", "rvol_ratio"]
N_CONFIGS = 16
MIN_DAYS_PER_YEAR = 60

SESSION_START = datetime.time(9, 15)
SESSION_END = datetime.time(15, 30)

# Amendment E5, at the registered equity (bottom of the range = conservative).
EQUITY = min(charter.TRADING_CAPITAL_RANGE_RS)
TARGET_CAGR = 0.15


def load() -> pd.DataFrame:
    if not os.path.exists(SRC):
        sys.exit(f"missing {SRC} — run: python -m backtest.intraday_archive --index")
    df = pd.read_parquet(SRC)
    t = df["ts"].dt.time
    before = len(df)
    # The archive deliberately keeps Dhan's post-close rollup bars and flags them
    # rather than cleaning them away. A correlation study must drop them: they
    # are flat repeats of the close and would manufacture spurious zero returns.
    df = df[(t >= SESSION_START) & (t <= SESSION_END)].copy()
    df["date"] = df["ts"].dt.date
    df["year"] = df["ts"].dt.year
    print(f"loaded {before:,} bars, kept {len(df):,} in-session "
          f"({before - len(df)} out-of-session dropped)")
    print(f"range {df['ts'].min()} .. {df['ts'].max()}, "
          f"{df['date'].nunique()} trading days")
    return df


def build_signals(df: pd.DataFrame) -> pd.DataFrame:
    """All four signals, computed WITHIN each session so nothing leaks overnight."""
    out = []
    for _, g in df.groupby("date", sort=True):
        g = g.sort_values("ts").reset_index(drop=True)
        if len(g) < 120:  # need enough of a session for 60-min horizons
            continue
        px = g["close"].astype(float)

        # session VWAP (index has no real volume; typical price is the honest proxy)
        typical = (g["high"] + g["low"] + g["close"]) / 3.0
        g["vwap"] = typical.expanding().mean()
        sd30 = px.rolling(30).std()
        g["vwap_dev"] = (px - g["vwap"]) / sd30.replace(0, np.nan)

        # opening range = first 15 minutes
        oh, ol = g["high"].iloc[:15].max(), g["low"].iloc[:15].min()
        rng = oh - ol
        g["or_pos"] = (px - ol) / rng if rng > 0 else np.nan
        g.loc[:14, "or_pos"] = np.nan  # not knowable until the range is formed

        # realised vol state: trailing 30-min stdev of 1-min returns, normalised.
        #
        # The normaliser MUST be past-only. An earlier version of this file used
        # rv30.median() over the whole session, which let the 10:00 value know
        # what happened at 15:00 — and that lookahead made rvol_ratio the
        # strongest signal in the screen by a wide margin. Expanding median sees
        # only bars already printed.
        r1 = px.pct_change()
        rv30 = r1.rolling(30).std()
        g["rvol_ratio"] = rv30 / rv30.expanding().median().replace(0, np.nan)

        for h in HORIZONS:
            g[f"mom_{h}"] = px.pct_change(h)
            # forward return, strictly within the session: the last h bars have
            # no forward value and must be NaN, never filled from the next day.
            g[f"fwd_{h}"] = px.shift(-h) / px - 1.0
        out.append(g)
    return pd.concat(out, ignore_index=True)


def spearman_ic(x: pd.Series, y: pd.Series):
    """Spearman IC and its t-stat. Returns (ic, t, n)."""
    m = x.notna() & y.notna() & np.isfinite(x) & np.isfinite(y)
    x, y = x[m], y[m]
    n = len(x)
    if n < 100:
        return None, None, n
    ic = x.rank().corr(y.rank())
    if ic is None or not np.isfinite(ic) or abs(ic) >= 1.0:
        return None, None, n
    # Overlapping forward windows make consecutive observations dependent, so the
    # naive sqrt(n-2) t is badly overstated. Corrected below in `t_corrected`.
    t = ic * np.sqrt((n - 2) / max(1e-12, 1 - ic * ic))
    return float(ic), float(t), n


def t_corrected(ic: float, n: int, horizon: int) -> float:
    """t-stat with the overlap of h-minute forward windows accounted for.

    Consecutive 1-minute observations of an h-minute forward return share h-1
    minutes of their path, so they are not independent draws. Treating them as
    independent inflates |t| by roughly sqrt(h). The honest effective sample is
    n/h, which is what this uses. Without this every cell below would clear
    Section 4's bar trivially on sample size alone.
    """
    n_eff = max(n / float(horizon), 2.0)
    return ic * np.sqrt((n_eff - 2) / max(1e-12, 1 - ic * ic))


def main():
    df = load()
    print("\nbuilding signals...")
    df = build_signals(df)

    bar = charter.noise_threshold(N_CONFIGS)
    print(f"Section 4 bar at N={N_CONFIGS}: |t| >= {bar:.2f}")
    print(f"Amendment E5 net target at Rs {EQUITY:,.0f}: "
          f"Rs {EQUITY * TARGET_CAGR:,.0f}/yr\n")

    results = []
    print(f"{'signal':<12} {'h':>4} {'IC':>8} {'t_naive':>9} {'t_corr':>8} "
          f"{'n':>9} {'yr signs':>14} {'(b)':>4}")
    print("-" * 78)

    for sig in SIGNALS:
        for h in HORIZONS:
            col = f"mom_{h}" if sig == "mom_h" else sig
            fwd = f"fwd_{h}"
            ic, t, n = spearman_ic(df[col], df[fwd])
            if ic is None:
                print(f"{sig:<12} {h:>4}  insufficient data (n={n})")
                continue
            tc = t_corrected(ic, n, h)

            # (b) sign consistency across calendar years
            signs, per_year = [], {}
            for yr, g in df.groupby("year"):
                if g["date"].nunique() < MIN_DAYS_PER_YEAR:
                    continue
                yic, _, yn = spearman_ic(g[col], g[fwd])
                if yic is None:
                    continue
                per_year[int(yr)] = round(yic, 5)
                signs.append(np.sign(yic))
            consistent = bool(signs) and len(set(signs)) == 1

            results.append({
                "signal": sig, "horizon": h, "ic": round(ic, 5),
                "t_naive": round(t, 2), "t_corrected": round(tc, 2),
                "n": n, "per_year_ic": per_year,
                "sign_consistent": consistent,
                "clears_section4": bool(abs(tc) >= bar),
            })
            sgn = "".join("+" if v > 0 else "-" for v in
                          [per_year[k] for k in sorted(per_year)])
            print(f"{sig:<12} {h:>4} {ic:>8.5f} {t:>9.2f} {tc:>8.2f} {n:>9,} "
                  f"{sgn:>14} {'OK' if consistent else 'no':>4}")

    # ---- verdict --------------------------------------------------------------
    print("\n" + "=" * 78)
    passed_a = [r for r in results if r["clears_section4"]]
    passed_ab = [r for r in passed_a if r["sign_consistent"]]

    print(f"(a) cells clearing |t| >= {bar:.2f} (overlap-corrected): "
          f"{len(passed_a)} of {len(results)}")
    for r in passed_a:
        print(f"      {r['signal']} h={r['horizon']}  IC={r['ic']:+.5f}  "
              f"t={r['t_corrected']:+.2f}")
    print(f"(b) of those, sign-consistent across every year: {len(passed_ab)}")
    for r in passed_ab:
        print(f"      {r['signal']} h={r['horizon']}  per-year {r['per_year_ic']}")

    # (c) economic translation.
    #
    # The first version of this compared implied gross against the net CAGR
    # target and never subtracted friction — which is precisely the error
    # Amendment E5 exists to prevent, and it "advanced" three cells that in fact
    # lose money on every single trade. Friction is the whole question at this
    # capital, so it is priced explicitly here.
    #
    # Translation chain, stated so it can be checked:
    #   expected index move  = |IC| x sd(forward return) x spot      [points]
    #   gross P&L for 1 lot  = move x option delta x lot_size        [rupees]
    #   net                  = gross - round-trip friction
    print("\n(c) economic test — the same numbers, with friction priced in:")
    print(f"    1 lot = {LOT} units, ATM delta ~{DELTA}, spot ~{SPOT:,.0f}, "
          f"premium Rs {OPTION_PREMIUM}")
    print(f"    cost = statutory charges + spread at Rs {SLIPPAGE_PER_LEG}/leg "
          f"crossed twice = Rs {rt_friction(OPTION_PREMIUM, LOT):.2f}/round trip")
    print(f"    (charges alone would be Rs "
          f"{rt_friction(OPTION_PREMIUM, LOT, slippage_per_leg=0.0):.2f}; the "
          f"spread is the larger half and friction_model does not carry it)")
    economic = []
    for r in passed_ab:
        h = r["horizon"]
        trades_yr = (375 // h) * 250
        net_target = EQUITY * TARGET_CAGR / trades_yr
        s = float(df[f"fwd_{h}"].std())

        move_pts = abs(r["ic"]) * s * SPOT
        gross = move_pts * DELTA * LOT
        friction = rt_friction(OPTION_PREMIUM, LOT)
        net = gross - friction

        r.update(trades_per_year=trades_yr,
                 net_target_per_trade=round(net_target, 2),
                 fwd_sd=round(s, 6),
                 implied_move_points=round(move_pts, 3),
                 gross_per_trade=round(gross, 2),
                 friction_per_trade=round(friction, 2),
                 net_per_trade=round(net, 2),
                 annual_friction_bill=round(friction * trades_yr, 2),
                 clears_economic=bool(net >= 2 * net_target))
        economic.append(r)
        print(f"    {r['signal']:<11} h={h:<3} move {move_pts:6.3f} pts -> gross "
              f"Rs {gross:7.2f}  friction Rs {friction:6.2f}  net Rs {net:8.2f}  "
              f"(need Rs {2*net_target:.2f})  "
              f"{'CLEARS' if r['clears_economic'] else 'FAILS'}")
        print(f"                {trades_yr:,} trades/yr would pay "
              f"Rs {friction*trades_yr:,.0f}/yr in friction alone, on a "
              f"Rs {EQUITY:,.0f} account "
              f"({friction*trades_yr/EQUITY:.0%} of it).")

    # The verdict must not hinge on one assumed spread. Section 5 stresses
    # surviving candidates at 2x slippage; here the whole (c) test is swept, so
    # the reader can see at what spread — if any — each cell turns positive.
    if passed_ab:
        print("\n(c) sensitivity — net Rs/trade vs assumed spread per leg:")
        sweeps = [0.0, 0.10, 0.25, 0.50, 0.75, 1.50]
        print(f"    {'signal':<11} {'h':>3} " +
              " ".join(f"{s:>8.2f}" for s in sweeps))
        for r in passed_ab:
            h = r["horizon"]
            s = float(df[f"fwd_{h}"].std())
            gross = abs(r["ic"]) * s * SPOT * DELTA * LOT
            cells = [gross - rt_friction(OPTION_PREMIUM, LOT, slippage_per_leg=sl)
                     for sl in sweeps]
            print(f"    {r['signal']:<11} {h:>3} " +
                  " ".join(f"{c:>8.2f}" for c in cells))
        print(f"    (the project's registered default is "
              f"{SLIPPAGE_PER_LEG}/leg; Section 5 also demands survival at 2x)")

    survivors = [r for r in economic if r.get("clears_economic")]
    verdict = "advance" if survivors else "killed"
    print("\n" + "=" * 78)
    if survivors:
        print(f"VERDICT: {len(survivors)} cell(s) satisfy (a) AND (b) AND (c).")
        print("Screen ADVANCES them. Per Amendment B5 a screen cannot promote —")
        print("these are candidates for walk-forward, not findings.")
    else:
        print("VERDICT: KILLED. No (signal, horizon) cell satisfies all three of")
        print("  (a) Section 4 significance, (b) sign consistency, (c) the 2x")
        print("  economic bar. Per the registered kill criterion this closes the")
        print("  idea that a single unconditional signal predicts intraday index")
        print("  direction at 5-60 minute horizons.")

    with open(OUT, "w", encoding="utf-8") as f:
        json.dump({"run_at": datetime.datetime.now().isoformat(),
                   "hypothesis": "intraday-ic-modern",
                   "bar": bar, "equity": EQUITY,
                   "verdict": verdict, "results": results}, f, indent=2)
    print(f"\nraw -> {OUT}")


if __name__ == "__main__":
    main()
