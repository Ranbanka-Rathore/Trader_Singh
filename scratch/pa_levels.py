"""
Screen for `pa-levels-modern` (Amendment F, registered 2026-08-18 in 7b95a15).

Runs the rule exactly as pinned in research/PREREGISTRATION-price-action.md.
Nothing here may be tuned: if a number in this file disagrees with that
document, this file is wrong.

CAUSALITY
---------
Levels for session D are built from bars STRICTLY BEFORE D's 09:15 open, never
from D's own bars. That is stricter than the operator's method (who can see the
session forming) and it is deliberate: it makes partial-bar leakage on the
higher timeframes impossible. A 60-minute bar covering 09:15-10:15 is not known
at 09:30, and a screen that used it would be `rvol_ratio` again.

Usage:  ./venv/Scripts/python.exe scratch/pa_levels.py
"""
import datetime as dt
import json
import os
import sys
import time

sys.path.insert(0, os.getcwd())

import numpy as np
import pandas as pd

from research import price_action as pa

SRC = os.path.join("data", "intraday", "NIFTY", "index.parquet")
OUT = os.path.join("scratch", "pa_levels_results.json")
PCR_CACHE = os.path.join("scratch", "pa_pcr_cache.json")

WINDOW = (dt.date(2022, 8, 16), dt.date(2026, 8, 14))   # registered window
CONFLUENCE_THRESHOLDS = (2, 3)                          # preregistration §5
BAR = 8.0                                               # index points, gross


# ── PCR (validated definition, reused rather than reinvented) ────────────────
def load_pcr(days):
    if os.path.exists(PCR_CACHE):
        with open(PCR_CACHE, encoding="utf-8") as f:
            return {dt.date.fromisoformat(k): v for k, v in json.load(f).items()}
    from backtest import bhavcopy as bc, real_backtester as rb
    out = {}
    t0 = time.time()
    for i, d in enumerate(days):
        try:
            c = bc.load_chain(d, "NIFTY")
            if not c:
                continue
            e = bc.nearest_expiry(c, d)
            if e:
                out[d] = float(rb.chain_pcr(c, e))
        except Exception:
            continue
        if i % 200 == 0:
            print(f"    pcr {i}/{len(days)} ({time.time()-t0:.0f}s)")
    with open(PCR_CACHE, "w", encoding="utf-8") as f:
        json.dump({k.isoformat(): v for k, v in out.items()}, f)
    return out


def pcr_extreme(pcr_by_day, days_sorted, d):
    """PCR outside its trailing 20-session 10th/90th percentile. Causal."""
    i = days_sorted.index(d)
    hist = [pcr_by_day[x] for x in days_sorted[max(0, i - 20):i] if x in pcr_by_day]
    if len(hist) < 10 or d not in pcr_by_day:
        return False
    lo, hi = np.percentile(hist, 10), np.percentile(hist, 90)
    v = pcr_by_day[d]
    return bool(v <= lo or v >= hi)


# ── the screen ───────────────────────────────────────────────────────────────
def run():
    df = pd.read_parquet(SRC)
    df["d"] = df["ts"].dt.date
    df = df[(df["d"] >= WINDOW[0]) & (df["d"] <= WINDOW[1])]
    df = df.set_index("ts").sort_index()
    days = sorted(df["d"].unique())
    print(f"{len(df):,} bars, {len(days)} sessions, {days[0]} .. {days[-1]}")

    print("  loading PCR (cached after first run)...")
    pcr_by_day = load_pcr(days)
    print(f"  PCR available for {len(pcr_by_day)} sessions")

    # Precompute timeframe frames ONCE. Slicing a precomputed resample to
    # strictly-prior bars is safe; resampling per day would be 994x slower and
    # identical.
    px = df[["open", "high", "low", "close"]]
    frames = {tf: pa.resample(px, tf) for tf in pa.TIMEFRAMES}
    tr = {tf: float(pa.true_range(frames[tf]).dropna().median()) for tf in frames}
    tol = {tf: pa.TOLERANCE_TR_MULT * tr[tf] for tf in frames}
    print("  median TR / tolerance: " + "  ".join(
        f"{tf} {tr[tf]:.1f}/{tol[tf]:.1f}" for tf in pa.TIMEFRAMES))

    trades = {k: [] for k in CONFLUENCE_THRESHOLDS}
    t0 = time.time()

    for n, d in enumerate(days):
        if n < pa.LOOKBACK_SESSIONS:
            continue
        open_ts = pd.Timestamp(dt.datetime.combine(d, dt.time(9, 15)))
        lb_start = pd.Timestamp(dt.datetime.combine(
            days[n - pa.LOOKBACK_SESSIONS], dt.time(0, 0)))

        # --- levels, from STRICTLY PRIOR sessions only --------------------
        levels = []
        for tf in pa.TIMEFRAMES:
            f = frames[tf]
            prior = f[(f.index >= lb_start) & (f.index < open_ts)]
            pa.assert_causal(prior, open_ts, f"levels[{tf}]")
            if len(prior) < 10:
                continue
            for lv in pa.touched_levels(prior, tol[tf], tr[tf]):
                levels.append(pa.Level(lv.price, "touched", tf, lv.touches))
            if tf == "1D":
                levels.extend(pa.gap_levels(prior, float(prior["close"].iloc[-1])))

        dprior = frames["1D"][frames["1D"].index < open_ts]
        if dprior.empty:
            continue
        spot0 = float(dprior["close"].iloc[-1])
        levels.extend(pa.round_levels(spot0, tol["1D"]))
        if not levels:
            continue

        struct = pa.structure(pa.swings(
            dprior[dprior.index >= lb_start], tr["1D"]))
        pcr_x = pcr_extreme(pcr_by_day, days, d)

        # --- walk today's COMPLETED 5-min bars ----------------------------
        day5 = frames["5min"][(frames["5min"].index >= open_ts) &
                              (frames["5min"].index < open_ts + pd.Timedelta(hours=6, minutes=30))]
        if len(day5) < 12:
            continue
        # Session VWAP proxy. The index has no real volume, so this reuses
        # arena5_intraday_ic.py's definition verbatim -- typical price (H+L+C)/3,
        # expanding -- rather than inventing a second one. Same proxy, same
        # comparability, one less free choice.
        vwap = (((day5["high"] + day5["low"] + day5["close"]) / 3.0)
                .expanding().mean())
        t5, tol5 = tr["5min"], tol["5min"]

        for lv in levels:
            state, touch_i, away_i, side = "idle", None, None, None
            for i in range(1, len(day5)):
                bar = day5.iloc[i]
                prev = day5.iloc[i - 1]
                c = float(bar["close"])
                near = abs(c - lv.price) <= tol5

                if state == "idle" and near:
                    # approach direction sets the side: coming DOWN into it =
                    # support = long; coming UP into it = resistance = short.
                    ref = float(day5["close"].iloc[max(0, i - 3)])
                    side = "long" if ref > lv.price else "short"
                    state, touch_i = "touched", i
                elif state == "touched":
                    # No timeout waiting for the move away. Preregistration §3B:
                    # a "wait at most N bars" rule would be a free parameter the
                    # document never pinned, so the setup stays live until the
                    # move away happens or the session ends.
                    away = (c - lv.price) if side == "long" else (lv.price - c)
                    if away >= pa.REVERSAL_TR_MULT * t5:
                        state, away_i = "away", i
                elif state == "away":
                    # The 10-bar retest clock starts HERE, at the completed move
                    # away -- not at the touch. Run 1 measured it from the touch,
                    # so one window had to contain both legs, and that produced
                    # 0.20 retests per session. Preregistration §3B.
                    if i - away_i > pa.RETEST_MAX_BARS:
                        state = "idle"
                        continue
                    if not near:
                        continue
                    # ---- RETEST: this is the entry bar ----
                    pats = pa.candlestick(float(bar["open"]), float(bar["high"]),
                                          float(bar["low"]), c,
                                          po=float(prev["open"]), pc=float(prev["close"]))
                    conf = 0
                    if lv.touches >= pa.MIN_TOUCHES:
                        conf += 1
                    if (struct == "LH-LL" and side == "long") or \
                       (struct == "HH-HL" and side == "short"):
                        conf += 1
                    if pa.confirms(pats, side):
                        conf += 1
                    if lv.kind == "gap":
                        conf += 1
                    if lv.kind == "round":
                        conf += 1
                    v = float(vwap.iloc[i])
                    if (side == "long" and c >= v) or (side == "short" and c <= v):
                        conf += 1
                    if pcr_x:
                        conf += 1

                    # ---- exit: next opposing level / adverse 1xTR / close ----
                    entry = c
                    sgn = 1.0 if side == "long" else -1.0
                    stop = lv.price - sgn * t5
                    opp = [x.price for x in levels
                           if (x.price - entry) * sgn > tol5]
                    target = (min(opp) if side == "long" else max(opp)) if opp else None
                    exit_px = float(day5["close"].iloc[-1])
                    for j in range(i + 1, len(day5)):
                        hj, lj = float(day5["high"].iloc[j]), float(day5["low"].iloc[j])
                        if (side == "long" and lj <= stop) or \
                           (side == "short" and hj >= stop):
                            exit_px = stop
                            break
                        if target is not None and \
                           ((side == "long" and hj >= target) or
                                (side == "short" and lj <= target)):
                            exit_px = target
                            break
                    gross = (exit_px - entry) * sgn
                    for th in CONFLUENCE_THRESHOLDS:
                        if conf >= th:
                            trades[th].append({"date": d.isoformat(), "side": side,
                                               "conf": conf, "gross_pts": gross,
                                               "level": lv.price, "kind": lv.kind,
                                               "tf": lv.timeframe})
                    state = "idle"
        if n % 100 == 0:
            print(f"    {n}/{len(days)} sessions ({time.time()-t0:.0f}s) "
                  f"trades@2={len(trades[2])}")

    return trades, days


def report(trades):
    print("\n" + "=" * 78)
    print("pa-levels-modern — SCREEN RESULT")
    print("=" * 78)
    print(f"KILL BAR: gross edge >= {BAR} index points, sign-stable every year\n")
    out = {}
    for th in CONFLUENCE_THRESHOLDS:
        tr_ = trades[th]
        print(f"--- confluence >= {th} ---")
        if not tr_:
            print("  no trades\n")
            out[th] = {"n": 0}
            continue
        g = np.array([t["gross_pts"] for t in tr_])
        n = len(g)
        mean = float(g.mean())
        se = float(g.std(ddof=1) / np.sqrt(n)) if n > 1 else float("nan")
        t_stat = mean / se if se and np.isfinite(se) and se > 0 else float("nan")
        by_year = {}
        for t in tr_:
            y = t["date"][:4]
            by_year.setdefault(y, []).append(t["gross_pts"])
        print(f"  trades {n:,}   gross edge {mean:+.3f} pts   t {t_stat:+.2f}")
        signs = {}
        for y in sorted(by_year):
            a = np.array(by_year[y])
            signs[y] = float(a.mean())
            print(f"    {y}  n={len(a):>5}  mean {a.mean():+.3f} pts")
        eligible = {y: v for y, v in signs.items() if len(by_year[y]) >= 20}
        stable = len(set(np.sign(list(eligible.values())))) == 1 if eligible else False
        a_pass = mean >= BAR
        print(f"  (a) edge >= {BAR}      : {'PASS' if a_pass else 'FAIL'} ({mean:+.3f})")
        print(f"  (b) sign-stable       : {'PASS' if stable else 'FAIL'}")
        print(f"  (c) |t| >= 1.18 (N=2) : "
              f"{'PASS' if abs(t_stat) >= 1.18 else 'FAIL'} ({t_stat:+.2f})")
        print(f"  VERDICT: {'ADVANCE' if (a_pass and stable) else 'KILLED'}\n")
        out[th] = {"n": n, "mean_gross_pts": mean, "t": t_stat,
                   "by_year": signs, "a": a_pass, "b": stable}
    return out


if __name__ == "__main__":
    trades, days = run()
    res = report(trades)
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump({"window": [WINDOW[0].isoformat(), WINDOW[1].isoformat()],
                   "bar_index_points": BAR, "results": res,
                   "n_trades": {k: len(v) for k, v in trades.items()}}, f, indent=2)
    print(f"written -> {OUT}")
