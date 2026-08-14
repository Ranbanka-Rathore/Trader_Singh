"""
Arena 5, screen 3 — does conditioning on signal strength clear the cost floor?

Registered as 'intraday-conditional-modern' BEFORE this ran (6119532). Signals,
horizons, thresholds, DIRECTION and all four kill conditions are fixed in the
kill log; this file only computes them.

Nothing is fitted. The signals are screen 1's exact formulas, the standardisation
is expanding and past-only, and the threshold is the only free parameter.
Direction was declared in advance as MOMENTUM: long when z is high.

Kill conditions, all four required:
  (a) net edge > 0 in EVERY calendar year with >= 60 trading days   [D5 on NET]
  (b) pooled net edge > 0 with |t| >= 2.35                          [Section 4]
  (c) >= 30 trades/year                                             [Section 3]
  (d) annual net > 7% of the capital required                       [Section 1]

Usage:  ./venv/Scripts/python.exe scratch/arena5_conditional.py
"""
import datetime
import json
import math
import os
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, os.getcwd())

from research import charter  # noqa: E402

SRC = os.path.join("data", "intraday", "NIFTY", "index.parquet")
OUT = os.path.join("scratch", "arena5_conditional_results.json")

SIGNALS = ["vwap_dev", "or_pos"]
HORIZONS = [60, 120]
THRESHOLDS = [1.0, 1.5, 2.0, 2.5]
N_CONFIGS = 16
MIN_DAYS_PER_YEAR = 60
MIN_TRADES_PER_YEAR = 30
WARMUP_SESSIONS = 20

SESSION_START = datetime.time(9, 15)
SESSION_END = datetime.time(15, 30)

# Amendment E9, measured.
OPT_TXN_PTS = 4.01
OPT_THETA_PTS_PER_HR = 5.45
FUT_ALLIN_PTS = 7.71
OPT_CAPITAL_PER_LOT = 7609.0     # ATM option, 1 lot
FUT_MARGIN = 190_000.0           # ~12% of notional
LOT = 65
FD_RATE = 0.07


def cheapest_cost(h_min):
    opt = OPT_TXN_PTS + OPT_THETA_PTS_PER_HR * (h_min / 60.0)
    if opt < FUT_ALLIN_PTS:
        return opt, "option", OPT_CAPITAL_PER_LOT
    return FUT_ALLIN_PTS, "future", FUT_MARGIN


def load():
    df = pd.read_parquet(SRC)
    t = df["ts"].dt.time
    df = df[(t >= SESSION_START) & (t <= SESSION_END)].copy()
    df["date"] = df["ts"].dt.date
    df["year"] = df["ts"].dt.year
    print(f"loaded {len(df):,} in-session bars, {df['date'].nunique()} trading days")
    return df


def build(df):
    """Screen 1's exact signal formulas, plus forward returns. Past-only throughout."""
    out = []
    for _, g in df.groupby("date", sort=True):
        g = g.sort_values("ts").reset_index(drop=True)
        if len(g) < 200:
            continue
        px = g["close"].astype(float)

        typical = (g["high"] + g["low"] + g["close"]) / 3.0
        vwap = typical.expanding().mean()
        sd30 = px.rolling(30).std()
        g["vwap_dev"] = (px - vwap) / sd30.replace(0, np.nan)

        oh, ol = g["high"].iloc[:15].max(), g["low"].iloc[:15].min()
        rng = oh - ol
        g["or_pos"] = (px - ol) / rng if rng > 0 else np.nan
        g.loc[:14, "or_pos"] = np.nan

        for h in HORIZONS:
            # forward return must complete inside the session: no overnight carry
            g[f"fwd_{h}"] = px.shift(-h) / px - 1.0
        out.append(g)
    return pd.concat(out, ignore_index=True)


def standardise_past_only(s: pd.Series, sessions: pd.Series) -> pd.Series:
    """z of `s` using only bars already seen. Expanding mean/std over history.

    This is the trap screen 1 nearly fell into from the other direction: a
    threshold defined against a full-sample or full-session moment is not
    knowable at the moment of the trade. Expanding moments are.
    """
    mu = s.expanding().mean()
    sd = s.expanding().std()
    z = (s - mu) / sd.replace(0, np.nan)
    # discard the warmup, where expanding moments are meaningless
    first_sessions = sessions.drop_duplicates().head(WARMUP_SESSIONS)
    z[sessions.isin(first_sessions)] = np.nan
    return z


def _non_overlapping(sub: pd.DataFrame, thr: float, h: int) -> pd.DataFrame:
    """Rows where a trade would actually be OPENED, holding h bars each.

    Walks each session in order and takes a signal only when no position is
    already open. Without this, overlapping bars are counted as separate trades
    and both the trade count and the annual return are inflated by roughly h.
    """
    keep = []
    for _, g in sub.groupby("date", sort=True):
        g = g.sort_values("ts") if "ts" in g.columns else g
        zs = g["z"].to_numpy()
        idx = g.index.to_numpy()
        busy_until = -1
        for i in range(len(zs)):
            if i <= busy_until:
                continue
            if abs(zs[i]) >= thr:
                keep.append(idx[i])
                busy_until = i + h - 1
    return sub.loc[keep].copy()


def main():
    df = load()
    df = build(df)
    print("signals built (screen 1 formulas, nothing fitted)")

    spot = float(df["close"].median())
    bar = charter.noise_threshold(N_CONFIGS)
    print(f"\nSection 4 bar at N={N_CONFIGS}: |t| >= {bar:.2f}")
    for h in HORIZONS:
        c, which, cap = cheapest_cost(h)
        print(f"  h={h:>4}min cost {c:5.2f} pts ({which}, capital Rs {cap:,.0f})")

    results = []
    print(f"\n{'signal':<10} {'h':>4} {'thr':>5} {'n':>7} {'tr/yr':>7} {'edge':>8} "
          f"{'cost':>6} {'net':>8} {'t':>7} {'yrs+':>6} {'ann.ret':>9}")
    print("-" * 92)

    for sig in SIGNALS:
        for h in HORIZONS:
            cost, which, capital = cheapest_cost(h)
            fwd = f"fwd_{h}"
            sub = df[["ts", sig, fwd, "year", "date"]].dropna().copy()
            if sub.empty:
                continue
            sub["z"] = standardise_past_only(sub[sig], sub["date"])
            sub = sub.dropna(subset=["z"])

            for thr in THRESHOLDS:
                # NON-OVERLAPPING trades only. Counting every qualifying bar as a
                # trade overstates frequency by ~h (at h=60 it produced 19,354
                # trades/yr — 77 a day, when only ~6 non-overlapping positions
                # fit in a 375-bar session) and carries that error straight into
                # the annual return. A position is opened only when none is open.
                sel = _non_overlapping(sub, thr, h)
                if len(sel) < 200:
                    continue
                # DIRECTION DECLARED IN ADVANCE: momentum. Long high z, short low z.
                sel["pos"] = np.sign(sel["z"])
                sel["gross_pts"] = sel["pos"] * sel[fwd] * spot
                sel["net_pts"] = sel["gross_pts"] - cost

                n = len(sel)
                mean_net = float(sel["net_pts"].mean())
                sd_net = float(sel["net_pts"].std())
                # Trades are now non-overlapping by construction, so they are
                # independent draws and the naive t applies. Screen 1 needed an
                # n/h correction precisely because its observations overlapped;
                # applying it here as well would double-count the same fix.
                t = mean_net / (sd_net / math.sqrt(max(n, 2))) if sd_net > 0 else 0.0

                per_year, yrs_pos = {}, 0
                for yr, gy in sel.groupby("year"):
                    if gy["date"].nunique() < MIN_DAYS_PER_YEAR:
                        continue
                    m = float(gy["net_pts"].mean())
                    per_year[int(yr)] = round(m, 3)
                    if m > 0:
                        yrs_pos += 1
                n_years = len(per_year)
                all_years_pos = n_years > 0 and yrs_pos == n_years

                span_days = sub["date"].nunique()
                trades_yr = n / (span_days / 250.0)
                # rupees: index points x lot, since exposure is delta-adjusted
                # into points already for options and delta=1 for futures
                annual_rs = mean_net * LOT * trades_yr
                annual_ret = annual_rs / capital

                rec = {
                    "signal": sig, "horizon": h, "threshold": thr,
                    "instrument": which, "n": n,
                    "trades_per_year": round(trades_yr, 1),
                    "edge_pts": round(float(sel["gross_pts"].mean()), 3),
                    "cost_pts": round(cost, 2), "net_pts": round(mean_net, 3),
                    "t": round(t, 2), "per_year_net": per_year,
                    "years": n_years, "years_positive": yrs_pos,
                    "annual_rs": round(annual_rs, 0),
                    "capital_required": capital,
                    "annual_return": round(annual_ret, 4),
                    "a_all_years_positive": bool(all_years_pos),
                    "b_significant": bool(abs(t) >= bar and mean_net > 0),
                    "c_enough_trades": bool(trades_yr >= MIN_TRADES_PER_YEAR),
                    "d_beats_fd": bool(annual_ret > FD_RATE),
                }
                rec["clears_all"] = bool(rec["a_all_years_positive"] and
                                         rec["b_significant"] and
                                         rec["c_enough_trades"] and rec["d_beats_fd"])
                results.append(rec)
                print(f"{sig:<10} {h:>4} {thr:>5.1f} {n:>7,} {trades_yr:>7.0f} "
                      f"{rec['edge_pts']:>8.2f} {cost:>6.2f} {mean_net:>+8.2f} "
                      f"{t:>+7.2f} {yrs_pos}/{n_years:<4} {100*annual_ret:>8.1f}%")

    # ---- verdict ------------------------------------------------------------
    print("\n" + "=" * 92)
    for key, label in (("a_all_years_positive", "(a) net > 0 in every year"),
                       ("b_significant", f"(b) |t| >= {bar:.2f} and net > 0"),
                       ("c_enough_trades", "(c) >= 30 trades/yr"),
                       ("d_beats_fd", "(d) annual return > 7% FD")):
        print(f"  {label:<34} {sum(1 for r in results if r[key])} of {len(results)}")

    survivors = [r for r in results if r["clears_all"]]
    verdict = "advance" if survivors else "killed"
    print()
    if survivors:
        print(f"VERDICT: {len(survivors)} cell(s) clear ALL FOUR conditions.")
        for r in survivors:
            print(f"  {r['signal']} h={r['horizon']} thr={r['threshold']}: "
                  f"net {r['net_pts']:+.2f} pts, {r['trades_per_year']:.0f} tr/yr, "
                  f"{100*r['annual_return']:.1f}% on Rs {r['capital_required']:,.0f}")
        print("\nPer Amendment B5 a screen cannot promote. This means 'worth a")
        print("walk-forward', never 'works'.")
    else:
        print("VERDICT: KILLED. No cell clears all four conditions.")
        print("  Screen 1 killed the unconditional case; the ceiling screen showed the")
        print("  feature space is exhausted (+0.0002 IC from 26 overfitted features);")
        print("  this closes the conditional case. Arena 'intraday_index' is")
        print("  RECOMMENDED FOR CLOSURE — an operator decision under Section 7.")

    with open(OUT, "w", encoding="utf-8") as f:
        json.dump({"run_at": datetime.datetime.now().isoformat(),
                   "hypothesis": "intraday-conditional-modern",
                   "bar": bar, "verdict": verdict, "results": results}, f, indent=2)
    print(f"\nraw -> {OUT}")


if __name__ == "__main__":
    main()
