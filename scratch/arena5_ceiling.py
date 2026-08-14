"""
Arena 5 ceiling screen — the in-sample upper bound on intraday index prediction.

Registered as 'intraday-ceiling-modern' BEFORE this ran (da56d7c). Claim, kill
criterion, horizons, thresholds and cost floors are all fixed in the kill log.

>> THIS CAN ONLY CLOSE THE ARENA, NEVER OPEN IT. <<

Everything below is deliberately biased in favour of the hypothesis:
  * 20+ features instead of screen 1's four
  * an IN-SAMPLE fit with NO hold-out — pure overfitting, on purpose
  * the best horizon and the best conditioning threshold chosen after seeing all
  * the cheapest instrument costed at each horizon

So the edge produced is an upper bound. If it fails the economics, no honest
out-of-sample strategy can pass them. If it clears, that is NOT a finding — an
overfit fit clearing a bar is expected and carries no out-of-sample content.

Every feature is past-only. Screen 1 was nearly derailed by an rvol_ratio
normalised on the whole-session median, which made it the strongest signal in
the screen until the lookahead was found.

Usage:  ./venv/Scripts/python.exe scratch/arena5_ceiling.py
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
OUT = os.path.join("scratch", "arena5_ceiling_results.json")

HORIZONS = [15, 30, 60, 120, 240, 375]
THRESHOLDS = [0.0, 1.0, 1.5, 2.0, 2.5]

SESSION_START = datetime.time(9, 15)
SESSION_END = datetime.time(15, 30)

# Amendment E9, measured 2026-08-14.
OPT_TXN_PTS = 4.01           # ATM option, round trip, transaction only
OPT_THETA_PTS_PER_HR = 5.45  # 4 DTE ATM
FUT_ALLIN_PTS = 7.71         # futures round trip, no theta

EQUITY = min(charter.TRADING_CAPITAL_RANGE_RS)


def cheapest_cost_pts(h_min: int) -> tuple:
    """Cheapest measured round-trip cost at horizon h, and which instrument.

    Options pay theta by the hour; futures do not. Whichever is lower is used,
    which favours the hypothesis — the point of a ceiling.
    """
    opt = OPT_TXN_PTS + OPT_THETA_PTS_PER_HR * (h_min / 60.0)
    return (opt, "option") if opt < FUT_ALLIN_PTS else (FUT_ALLIN_PTS, "future")


def load() -> pd.DataFrame:
    if not os.path.exists(SRC):
        sys.exit(f"missing {SRC}")
    df = pd.read_parquet(SRC)
    t = df["ts"].dt.time
    df = df[(t >= SESSION_START) & (t <= SESSION_END)].copy()
    df["date"] = df["ts"].dt.date
    print(f"loaded {len(df):,} in-session bars, {df['date'].nunique()} days "
          f"({df['ts'].min().date()} .. {df['ts'].max().date()})")
    return df


def build(df: pd.DataFrame) -> tuple:
    """Feature matrix + forward returns. Every feature is strictly past-only."""
    out, feat_names = [], None
    for _, g in df.groupby("date", sort=True):
        g = g.sort_values("ts").reset_index(drop=True)
        if len(g) < 200:
            continue
        px = g["close"].astype(float)
        hi, lo = g["high"].astype(float), g["low"].astype(float)
        r1 = px.pct_change()
        f = pd.DataFrame(index=g.index)

        # multi-lag momentum
        for lag in (1, 2, 5, 10, 15, 30, 60, 120):
            f[f"mom{lag}"] = px.pct_change(lag)
        # acceleration: recent momentum vs the momentum before it
        f["accel15"] = px.pct_change(15) - px.pct_change(30).shift(15)
        f["accel60"] = px.pct_change(60) - px.pct_change(120).shift(60)

        # VWAP deviation (expanding = past only)
        typical = (hi + lo + px) / 3.0
        vwap = typical.expanding().mean()
        sd30 = px.rolling(30).std()
        f["vwap_dev"] = (px - vwap) / sd30.replace(0, np.nan)
        f["vwap_dev_chg"] = f["vwap_dev"].diff(15)

        # opening range (first 15 min), unknowable before it forms
        oh, ol = hi.iloc[:15].max(), lo.iloc[:15].min()
        rng = oh - ol
        f["or_pos"] = (px - ol) / rng if rng > 0 else np.nan
        f["or_width"] = (rng / px) if rng > 0 else np.nan
        f.loc[:14, ["or_pos", "or_width"]] = np.nan

        # realised-vol state, normalised past-only
        rv30 = r1.rolling(30).std()
        f["rvol"] = rv30
        f["rvol_ratio"] = rv30 / rv30.expanding().median().replace(0, np.nan)
        f["rvol_chg"] = rv30 / rv30.shift(30).replace(0, np.nan)

        # position within the session range so far (expanding = past only)
        run_hi, run_lo = hi.expanding().max(), lo.expanding().min()
        span = (run_hi - run_lo).replace(0, np.nan)
        f["day_pos"] = (px - run_lo) / span
        f["day_span"] = span / px

        # intrabar shape and its persistence
        f["bar_range"] = (hi - lo) / px
        f["bar_range_ma"] = f["bar_range"].rolling(30).mean()
        f["close_loc"] = ((px - lo) / (hi - lo).replace(0, np.nan))
        f["close_loc_ma"] = f["close_loc"].rolling(15).mean()

        # time of day, as a smooth pair rather than a raw index
        frac = pd.Series(np.arange(len(g)) / len(g), index=g.index)
        f["tod_sin"] = np.sin(2 * np.pi * frac)
        f["tod_cos"] = np.cos(2 * np.pi * frac)

        # autocorrelation-ish: sign persistence over the last 15 bars
        f["updown15"] = np.sign(r1).rolling(15).mean()

        for h in HORIZONS:
            f[f"fwd_{h}"] = px.shift(-h) / px - 1.0

        if feat_names is None:
            feat_names = [c for c in f.columns if not c.startswith("fwd_")]
        out.append(f)

    allf = pd.concat(out, ignore_index=True)
    print(f"built {len(feat_names)} features over {len(allf):,} rows")
    return allf, feat_names


def fit_in_sample(X: np.ndarray, y: np.ndarray) -> np.ndarray:
    """OLS fit, no hold-out. Deliberately overfitted — that is the point."""
    Xa = np.column_stack([np.ones(len(X)), X])
    coef, *_ = np.linalg.lstsq(Xa, y, rcond=None)
    return Xa @ coef


def main():
    df = load()
    feats, names = build(df)

    print(f"\ncost floors (Amendment E9), cheapest instrument per horizon:")
    for h in HORIZONS:
        c, which = cheapest_cost_pts(h)
        print(f"  h={h:>4}min  {c:6.2f} pts  ({which})")

    spot = float(df["close"].median())
    results, best = [], None
    print(f"\n{'h':>5} {'n':>9} {'IS |IC|':>8} {'sd fwd':>9} {'thr':>5} "
          f"{'edge pts':>9} {'cost pts':>9} {'net pts':>9} {'trades/yr':>10}")
    print("-" * 88)

    for h in HORIZONS:
        y_col = f"fwd_{h}"
        sub = feats[names + [y_col]].replace([np.inf, -np.inf], np.nan).dropna()
        if len(sub) < 5000:
            print(f"{h:>5}  insufficient rows ({len(sub)})")
            continue
        X = sub[names].to_numpy(dtype=float)
        y = sub[y_col].to_numpy(dtype=float)

        # standardise so the conditioning threshold is in z units
        mu, sd = X.mean(0), X.std(0)
        sd[sd == 0] = 1.0
        pred = fit_in_sample((X - mu) / sd, y)

        ic = float(pd.Series(pred).rank().corr(pd.Series(y).rank()))
        sd_fwd = float(np.std(y))
        z = (pred - pred.mean()) / (pred.std() or 1.0)
        cost, which = cheapest_cost_pts(h)

        for thr in THRESHOLDS:
            m = np.abs(z) >= thr
            n_sel = int(m.sum())
            if n_sel < 200:
                continue
            # realised directional capture, in index points: the mean signed
            # forward move when following the model's sign on selected bars.
            capture = float(np.mean(np.sign(z[m]) * y[m])) * spot
            frac = n_sel / len(z)
            trades_yr = (375 // h) * 250 * frac
            net = capture - cost
            results.append({
                "horizon": h, "n": len(sub), "is_ic": round(ic, 5),
                "sd_fwd": round(sd_fwd, 6), "threshold_z": thr,
                "selected": n_sel, "selected_frac": round(frac, 4),
                "edge_pts": round(capture, 3), "cost_pts": round(cost, 3),
                "instrument": which, "net_pts": round(net, 3),
                "trades_per_year": round(trades_yr, 1),
                "annual_pts": round(net * trades_yr, 1),
            })
            if best is None or net > best["net_pts"]:
                best = results[-1]
            print(f"{h:>5} {len(sub):>9,} {abs(ic):>8.4f} {sd_fwd:>9.6f} {thr:>5.1f} "
                  f"{capture:>9.3f} {cost:>9.2f} {net:>+9.3f} {trades_yr:>10.0f}")

    # ---- verdict -----------------------------------------------------------
    print("\n" + "=" * 88)
    positives = [r for r in results if r["net_pts"] > 0]
    print(f"cells with POSITIVE net edge (in-sample, overfitted): "
          f"{len(positives)} of {len(results)}")

    if not positives:
        verdict = "killed"
        print("\nVERDICT: KILLED — and this closes the arena on a measured ceiling.")
        print("  The bound is overfitted, hold-out-free, and evaluated at its own best")
        print("  horizon and threshold against the cheapest instrument. It still fails")
        print("  the economics everywhere. No out-of-sample strategy on intraday NIFTY")
        print("  index direction can beat an in-sample fit on the same features, so")
        print("  there is nothing left in this arena to find.")
    else:
        verdict = "not_closed"
        b = best
        print(f"\nbest cell: h={b['horizon']} thr={b['threshold_z']} "
              f"edge {b['edge_pts']:.3f} - cost {b['cost_pts']:.2f} "
              f"= {b['net_pts']:+.3f} pts, {b['trades_per_year']:.0f} trades/yr")
        print(f"  implied annual: {b['annual_pts']:.0f} index points")
        print("\nVERDICT: NOT CLOSED — and this is NOT a finding.")
        print("  Per the registered kill criterion: an overfit in-sample fit clearing")
        print("  a bar is the least surprising result in statistics. It carries no")
        print("  out-of-sample content whatsoever. The arena is left exactly as open")
        print("  as it was, and the ONLY thing this licenses is an honest walk-forward")
        print("  of a specific combination, registered separately.")

    with open(OUT, "w", encoding="utf-8") as f:
        json.dump({"run_at": datetime.datetime.now().isoformat(),
                   "hypothesis": "intraday-ceiling-modern",
                   "verdict": verdict, "n_features": len(names),
                   "features": names, "best": best, "results": results}, f, indent=2)
    print(f"\nraw -> {OUT}")


if __name__ == "__main__":
    main()
