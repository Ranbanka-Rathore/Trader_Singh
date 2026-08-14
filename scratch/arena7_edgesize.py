"""
Does ANY intraday edge reach 8 index points? — walk-forward, out of sample.

Registered as 'intraday-edgesize-modern' BEFORE this ran (2bfc21a). The 8.0-point
bar is arena intraday_index's own reopening condition #2.

METHOD, and why it differs from the ceiling screen
--------------------------------------------------
intraday-ceiling-modern fitted in-sample with no hold-out and reached 80 points
at extreme thresholds — pure overfitting, and its registration said in advance
that this would carry no information. So here the model is trained ONLY on data
preceding each test year and evaluated on the year it never saw. Everything is
past-only: the volatility terciles are cut on training data and applied to the
test year, never re-cut on it.

Kill conditions, all required:
  (a) OOS gross edge >= 8.0 index points per non-overlapping trade
  (b) net edge > 0 after the measured Amendment E9 cost floor
  (c) net > 0 in EVERY test year, not merely pooled
  (d) pooled |t| >= 2.40
  (e) >= 30 trades/year

Usage:  ./venv/Scripts/python.exe scratch/arena7_edgesize.py
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
OUT = os.path.join("scratch", "arena7_edgesize_results.json")

BAR_PTS = 8.0
HORIZONS = [60, 120]
THRESHOLDS = [1.0, 1.5, 2.0]
REGIMES = ["low", "mid", "high"]
TEST_YEARS = [2023, 2024, 2025, 2026]
N_CONFIGS = 18
MIN_TRADES_PER_YEAR = 30

SESSION_START = datetime.time(9, 15)
SESSION_END = datetime.time(15, 30)

OPT_TXN_PTS, OPT_THETA_PER_HR, FUT_ALLIN_PTS = 4.01, 5.45, 7.71


def cheapest_cost(h):
    opt = OPT_TXN_PTS + OPT_THETA_PER_HR * (h / 60.0)
    return (opt, "option") if opt < FUT_ALLIN_PTS else (FUT_ALLIN_PTS, "future")


def load_and_build():
    df = pd.read_parquet(SRC)
    t = df["ts"].dt.time
    df = df[(t >= SESSION_START) & (t <= SESSION_END)].copy()
    df["date"] = df["ts"].dt.date
    print(f"loaded {len(df):,} in-session bars, {df['date'].nunique()} days")

    out, names = [], None
    for _, g in df.groupby("date", sort=True):
        g = g.sort_values("ts").reset_index(drop=True)
        if len(g) < 200:
            continue
        px = g["close"].astype(float)
        hi, lo = g["high"].astype(float), g["low"].astype(float)
        r1 = px.pct_change()
        f = pd.DataFrame(index=g.index)

        for lag in (1, 2, 5, 10, 15, 30, 60, 120):
            f[f"mom{lag}"] = px.pct_change(lag)
        f["accel15"] = px.pct_change(15) - px.pct_change(30).shift(15)
        f["accel60"] = px.pct_change(60) - px.pct_change(120).shift(60)

        typical = (hi + lo + px) / 3.0
        vwap = typical.expanding().mean()
        sd30 = px.rolling(30).std()
        f["vwap_dev"] = (px - vwap) / sd30.replace(0, np.nan)
        f["vwap_dev_chg"] = f["vwap_dev"].diff(15)

        oh, ol = hi.iloc[:15].max(), lo.iloc[:15].min()
        rng = oh - ol
        f["or_pos"] = (px - ol) / rng if rng > 0 else np.nan
        f["or_width"] = (rng / px) if rng > 0 else np.nan
        f.loc[:14, ["or_pos", "or_width"]] = np.nan

        rv30 = r1.rolling(30).std()
        f["rvol"] = rv30
        f["rvol_ratio"] = rv30 / rv30.expanding().median().replace(0, np.nan)
        f["rvol_chg"] = rv30 / rv30.shift(30).replace(0, np.nan)

        run_hi, run_lo = hi.expanding().max(), lo.expanding().min()
        span = (run_hi - run_lo).replace(0, np.nan)
        f["day_pos"] = (px - run_lo) / span
        f["day_span"] = span / px

        f["bar_range"] = (hi - lo) / px
        f["bar_range_ma"] = f["bar_range"].rolling(30).mean()
        f["close_loc"] = (px - lo) / (hi - lo).replace(0, np.nan)
        f["close_loc_ma"] = f["close_loc"].rolling(15).mean()

        frac = pd.Series(np.arange(len(g)) / len(g), index=g.index)
        f["tod_sin"] = np.sin(2 * np.pi * frac)
        f["tod_cos"] = np.cos(2 * np.pi * frac)
        f["updown15"] = np.sign(r1).rolling(15).mean()

        # regime variable: TRAILING realised vol, past-only
        f["_regime_rv"] = rv30
        f["_ts"] = g["ts"]
        f["_date"] = g["date"]
        f["_year"] = g["ts"].dt.year
        for h in HORIZONS:
            f[f"fwd_{h}"] = px.shift(-h) / px - 1.0
        if names is None:
            names = [c for c in f.columns
                     if not c.startswith(("fwd_", "_"))]
        out.append(f)

    allf = pd.concat(out, ignore_index=True)
    print(f"built {len(names)} features over {len(allf):,} rows")
    return allf, names


def non_overlapping_mask(sub: pd.DataFrame, take: np.ndarray, h: int) -> np.ndarray:
    """Keep only signals that would actually open a position, holding h bars."""
    keep = np.zeros(len(sub), dtype=bool)
    dates = sub["_date"].to_numpy()
    i = 0
    n = len(sub)
    while i < n:
        if take[i]:
            keep[i] = True
            d = dates[i]
            j = i + 1
            # skip h bars, but never across a session boundary
            while j < n and j < i + h and dates[j] == d:
                j += 1
            i = j
        else:
            i += 1
    return keep


def main():
    feats, names = load_and_build()
    bar_t = charter.noise_threshold(N_CONFIGS)
    print(f"\nSection 4 bar at N={N_CONFIGS}: |t| >= {bar_t:.2f}")
    print(f"edge bar: {BAR_PTS} index points OUT OF SAMPLE\n")

    spot = 24366.0
    rows = []

    for h in HORIZONS:
        cost, which = cheapest_cost(h)
        fwd = f"fwd_{h}"
        cols = names + [fwd, "_year", "_date", "_regime_rv"]
        data = feats[cols].replace([np.inf, -np.inf], np.nan).dropna().copy()

        # walk forward: train on everything before the test year
        preds, regs = {}, {}
        for ty in TEST_YEARS:
            tr = data[data["_year"] < ty]
            te = data[data["_year"] == ty]
            if len(tr) < 20000 or len(te) < 2000:
                continue
            Xtr = tr[names].to_numpy(float)
            mu, sd = Xtr.mean(0), Xtr.std(0)
            sd[sd == 0] = 1.0
            A = np.column_stack([np.ones(len(Xtr)), (Xtr - mu) / sd])
            coef, *_ = np.linalg.lstsq(A, tr[fwd].to_numpy(float), rcond=None)

            Xte = (te[names].to_numpy(float) - mu) / sd
            p = np.column_stack([np.ones(len(Xte)), Xte]) @ coef
            # standardise the prediction on TRAIN moments only
            ptr = A @ coef
            z = (p - ptr.mean()) / (ptr.std() or 1.0)
            preds[ty] = pd.DataFrame({"z": z, "fwd": te[fwd].to_numpy(float),
                                      "_date": te["_date"].to_numpy(),
                                      "rv": te["_regime_rv"].to_numpy(float)},
                                     index=te.index)
            # vol terciles cut on TRAIN, applied to test — never re-cut on test
            regs[ty] = np.quantile(tr["_regime_rv"].to_numpy(float), [1/3, 2/3])

        if not preds:
            continue

        for regime in REGIMES:
            for thr in THRESHOLDS:
                per_year, all_sel = {}, []
                for ty, pdf in preds.items():
                    q1, q2 = regs[ty]
                    if regime == "low":
                        rmask = pdf["rv"] <= q1
                    elif regime == "mid":
                        rmask = (pdf["rv"] > q1) & (pdf["rv"] <= q2)
                    else:
                        rmask = pdf["rv"] > q2
                    sub = pdf[rmask].sort_index()
                    if sub.empty:
                        continue
                    take = (np.abs(sub["z"].to_numpy()) >= thr)
                    keep = non_overlapping_mask(sub, take, h)
                    sel = sub[keep]
                    if sel.empty:
                        continue
                    gross = np.sign(sel["z"].to_numpy()) * sel["fwd"].to_numpy() * spot
                    net = gross - cost
                    per_year[ty] = {"n": int(len(sel)),
                                    "gross": float(gross.mean()),
                                    "net": float(net.mean())}
                    all_sel.append(pd.DataFrame({"gross": gross, "net": net,
                                                 "year": ty}))
                if not all_sel:
                    continue
                allp = pd.concat(all_sel, ignore_index=True)
                n = len(allp)
                g_mean = float(allp["gross"].mean())
                n_mean = float(allp["net"].mean())
                sd_net = float(allp["net"].std())
                t = n_mean / (sd_net / math.sqrt(max(n, 2))) if sd_net > 0 else 0.0
                yrs = len(per_year)
                yrs_pos = sum(1 for v in per_year.values() if v["net"] > 0)
                trades_yr = n / max(yrs, 1)

                rec = {
                    "horizon": h, "regime": regime, "threshold": thr,
                    "instrument": which, "cost_pts": round(cost, 2),
                    "n": n, "trades_per_year": round(trades_yr, 1),
                    "oos_gross_pts": round(g_mean, 3),
                    "oos_net_pts": round(n_mean, 3),
                    "t": round(t, 2), "years": yrs, "years_positive": yrs_pos,
                    "per_year": {str(k): {kk: round(vv, 3) if isinstance(vv, float) else vv
                                          for kk, vv in v.items()}
                                 for k, v in per_year.items()},
                    "a_gross_ge_8": bool(g_mean >= BAR_PTS),
                    "b_net_positive": bool(n_mean > 0),
                    "c_every_year": bool(yrs > 0 and yrs_pos == yrs),
                    "d_significant": bool(abs(t) >= bar_t and n_mean > 0),
                    "e_enough_trades": bool(trades_yr >= MIN_TRADES_PER_YEAR),
                }
                rec["clears_all"] = all(rec[k] for k in
                                        ("a_gross_ge_8", "b_net_positive",
                                         "c_every_year", "d_significant",
                                         "e_enough_trades"))
                rows.append(rec)

    rows.sort(key=lambda r: -r["oos_gross_pts"])
    print(f"{'h':>4} {'regime':>7} {'thr':>5} {'n':>6} {'tr/yr':>7} {'GROSS':>8} "
          f"{'cost':>6} {'net':>8} {'t':>7} {'yrs+':>6} {'>=8?':>5}")
    print("-" * 82)
    for r in rows:
        print(f"{r['horizon']:>4} {r['regime']:>7} {r['threshold']:>5.1f} {r['n']:>6,} "
              f"{r['trades_per_year']:>7.0f} {r['oos_gross_pts']:>8.2f} "
              f"{r['cost_pts']:>6.2f} {r['oos_net_pts']:>+8.2f} {r['t']:>+7.2f} "
              f"{r['years_positive']}/{r['years']:<4} "
              f"{'YES' if r['a_gross_ge_8'] else 'no':>5}")

    print("\n" + "=" * 82)
    for k, lab in (("a_gross_ge_8", f"(a) OOS gross >= {BAR_PTS} pts"),
                   ("b_net_positive", "(b) net > 0"),
                   ("c_every_year", "(c) net > 0 in every test year"),
                   ("d_significant", f"(d) |t| >= {bar_t:.2f}"),
                   ("e_enough_trades", "(e) >= 30 trades/yr")):
        print(f"  {lab:<34} {sum(1 for r in rows if r[k])} of {len(rows)}")

    best = max(rows, key=lambda r: r["oos_gross_pts"]) if rows else None
    survivors = [r for r in rows if r["clears_all"]]
    verdict = "advance" if survivors else "killed"
    print()
    if survivors:
        print(f"VERDICT: {len(survivors)} cell(s) clear ALL FIVE.")
        for r in survivors:
            print(f"  h={r['horizon']} {r['regime']} thr={r['threshold']}: "
                  f"gross {r['oos_gross_pts']:.2f} net {r['oos_net_pts']:+.2f} pts, "
                  f"{r['trades_per_year']:.0f} tr/yr")
        print("\nB5: this means 'worth a strategy-level walk-forward', not 'works'.")
    else:
        print("VERDICT: KILLED. No intraday edge reaches 8 index points out of sample.")
        if best:
            print(f"  best OOS gross anywhere: {best['oos_gross_pts']:.2f} pts "
                  f"(h={best['horizon']}, {best['regime']} vol, thr={best['threshold']}) "
                  f"vs the {BAR_PTS}-point bar")
        print("  Arena intraday_index's reopening condition #2 is demonstrably UNMET.")
        print("  Regime conditioning does not rescue the edge: applying the same")
        print("  skill where the distribution is widest scales the noise with it.")

    with open(OUT, "w", encoding="utf-8") as f:
        json.dump({"run_at": datetime.datetime.now().isoformat(),
                   "hypothesis": "intraday-edgesize-modern", "bar_pts": BAR_PTS,
                   "t_bar": bar_t, "verdict": verdict, "best": best,
                   "results": rows}, f, indent=2)
    print(f"\nraw -> {OUT}")


if __name__ == "__main__":
    main()
