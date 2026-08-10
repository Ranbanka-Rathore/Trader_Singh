"""Which candidate signals have any cross-sectional edge in arena 3?

T2 measured what arena 3 requires: a 21-day-hold book needs IC ~0.05 to reach
Amendment A5's preferred 1.0, ~0.04 for its floor of 0.8. T1 then died with a
measured IC of +0.0009. The cheap move is therefore to measure IC FIRST, before
any sizing, fill model or backtest exists -- it would have killed tsmom in
minutes rather than after a full screen.

NOT A HYPOTHESIS. No claim registered, no config budget spent. But it spends
knowledge of every signal below, and the multiple-comparisons cost is real:
testing N signals and reporting the best one inflates that best IC. The
sqrt(2 ln N) bar is printed alongside for exactly that reason, and anything
registered from this list later is registered by someone who already knows the
answer.

METHOD. All signals are scored on the SAME rebalance dates with the SAME forward
return, or the ICs are not comparable. Window, gate and universe are T1's.

  - forward return: 21-day compounded roll-adjusted return
  - rank IC (Spearman) is the headline: returns are fat-tailed and Pearson IC is
    dominated by a handful of outliers
  - t is across rebalance periods, mean(IC) / (sd(IC)/sqrt(n)) -- with ~28
    periods the power is low, which is itself the point (T2 finding 4)

ROLL CONTAMINATION. Price signals use the roll-safe compounded index, never the
contract close. Volume and OI are front-contract quantities and DROP at every
monthly roll, so any OI or volume change measured over ~21 days almost always
spans a roll and is comparing two different contracts. Those signals are marked
[ROLL] below and their numbers should not be trusted -- they are included to show
the contamination rather than to hide it.
"""
import datetime as dt
import sys

import numpy as np

sys.path.insert(0, r"D:\Projects\Agentic_Trader")

from backtest import futures
from research import charter

GATE = "strict_legacy"
KIND = "stock"
HORIZON = 21          # forward return, matching T2's most favourable hold
WARMUP = 273          # 252 + 21, so every signal sees identical rebalance dates
MIN_FRAC = 0.8        # share of a window a name must actually have traded
MIN_NAMES = 30        # cross-section must be wide enough for an IC to mean anything


def window_ret(R, a, b):
    """Compounded return over (a, b], NaN where the name is not there throughout."""
    if a < 0:
        return None
    blk = R[a:b]
    if blk.shape[0] == 0:
        return None
    ok = np.isfinite(blk).sum(axis=0) >= blk.shape[0] * MIN_FRAC
    return np.where(ok, np.nanprod(np.nan_to_num(blk, nan=0.0) + 1.0, axis=0) - 1.0, np.nan)


def window_vol(R, a, b):
    if a < 0:
        return None
    blk = R[a:b]
    ok = np.isfinite(blk).sum(axis=0) >= blk.shape[0] * MIN_FRAC
    with np.errstate(invalid="ignore"):
        v = np.nanstd(blk, axis=0)
    return np.where(ok, v, np.nan)


def col_mean(M, a, b):
    if a < 0:
        return None
    blk = M[a:b]
    ok = np.isfinite(blk).sum(axis=0) >= blk.shape[0] * MIN_FRAC
    with np.errstate(invalid="ignore"):
        m = np.nanmean(blk, axis=0)
    return np.where(ok, m, np.nan)


def rank(a, mask):
    """Ranks among valid entries; NaN elsewhere."""
    out = np.full(a.shape, np.nan)
    idx = np.flatnonzero(mask)
    order = idx[np.argsort(a[idx])]
    out[order] = np.arange(len(order), dtype=float)
    return out


def ic(score, fwd, spearman=True):
    m = np.isfinite(score) & np.isfinite(fwd)
    if m.sum() < MIN_NAMES:
        return None
    x, y = score.copy(), fwd.copy()
    if spearman:
        x, y = rank(x, m), rank(y, m)
    xv, yv = x[m], y[m]
    if np.std(xv) == 0 or np.std(yv) == 0:
        return None
    c = np.corrcoef(xv, yv)[0, 1]
    return float(c) if np.isfinite(c) else None


# ── the candidates ───────────────────────────────────────────────────────────
# Each returns a score where HIGHER is predicted to be BETTER (so reversal
# signals are already negated). `t` is the rebalance index.
def build_signals():
    return {
        "mom_252_21   (12-1)":      lambda R, V, O, t: window_ret(R, t - 273, t - 21),
        "mom_126_21   (T1's)":      lambda R, V, O, t: window_ret(R, t - 147, t - 21),
        "mom_63_21":                lambda R, V, O, t: window_ret(R, t - 84, t - 21),
        "mom_21_0":                 lambda R, V, O, t: window_ret(R, t - 21, t),
        "rev_5        (-5d ret)":   lambda R, V, O, t: _neg(window_ret(R, t - 5, t)),
        "rev_21       (-21d ret)":  lambda R, V, O, t: _neg(window_ret(R, t - 21, t)),
        "lowvol_63    (-vol)":      lambda R, V, O, t: _neg(window_vol(R, t - 63, t)),
        "high_52w":                 lambda R, V, O, t: _high52(R, t),
        "accel        (21d-126d)":  lambda R, V, O, t: _accel(R, t),
        "oi_chg_21    [ROLL]":      lambda R, V, O, t: _chg(O, t),
        "vol_trend_21 [ROLL]":      lambda R, V, O, t: _ratio(V, t),
    }


def _neg(a):
    return None if a is None else -a


def _high52(R, t):
    """Index level relative to its own trailing 252-day maximum."""
    a = t - 252
    if a < 0:
        return None
    blk = R[a:t]
    ok = np.isfinite(blk).sum(axis=0) >= blk.shape[0] * MIN_FRAC
    lvl = np.nancumprod(np.nan_to_num(blk, nan=0.0) + 1.0, axis=0)
    return np.where(ok, lvl[-1] / np.maximum(lvl.max(axis=0), 1e-12), np.nan)


def _accel(R, t):
    a, b = window_ret(R, t - 21, t), window_ret(R, t - 126, t)
    return None if a is None or b is None else a - b


def _chg(M, t):
    a, b = col_mean(M, t - 5, t), col_mean(M, t - 26, t - 21)
    if a is None or b is None:
        return None
    with np.errstate(invalid="ignore", divide="ignore"):
        return np.where((b > 0) & np.isfinite(a), a / np.maximum(b, 1e-9) - 1.0, np.nan)


def _ratio(M, t):
    a, b = col_mean(M, t - 21, t), col_mean(M, t - 126, t)
    if a is None or b is None:
        return None
    with np.errstate(invalid="ignore", divide="ignore"):
        return np.where((b > 0) & np.isfinite(a), a / np.maximum(b, 1e-9), np.nan)


def implied_sharpe(ic_val):
    """Interpolate T2's measured long/short ceiling table (21d hold, 0 bps)."""
    xs = [0.00, 0.02, 0.05, 0.10, 0.15, 0.20, 0.30, 0.50, 0.75, 1.00]
    ys = [-0.017, 0.438, 0.978, 1.990, 2.730, 3.325, 4.326, 5.921, 7.490, 8.637]
    return float(np.interp(abs(ic_val), xs, ys))


def main():
    pooled = len(sys.argv) > 1 and sys.argv[1] == "pooled"
    if pooled:
        # Amendment D2: signal-property estimation may pool eras for an
        # instrument whose era-defining property has been measured flat.
        # Stock futures qualify (scratch/arena3_era_break.py).
        start, end = dt.date(2016, 1, 1), dt.date.today()
    else:
        start, end = charter.era_window("modern", cap=dt.date.today())
    dates = futures.trading_dates(start, end)
    panel = futures.build_panel(dates, kind=KIND, gate=GATE)
    syms = sorted(panel.series)
    idx = {d: i for i, d in enumerate(dates)}
    T, N = len(dates), len(syms)
    R = np.full((T, N), np.nan)
    V = np.full((T, N), np.nan)
    O = np.full((T, N), np.nan)
    for j, s in enumerate(syms):
        ser = panel.series[s]
        for b, r in zip(ser.bars, ser.rets):
            i = idx.get(b.date)
            if i is None:
                continue
            if r is not None:
                R[i, j] = r
            V[i, j] = b.volume
            O[i, j] = b.oi

    sigs = build_signals()
    rebals = [t for t in range(WARMUP, T - HORIZON, HORIZON)]
    print(f"window {dates[0]} -> {dates[-1]}   {T} sessions   {N} symbols   "
          f"gate={GATE}")
    print(f"{len(rebals)} rebalances, {HORIZON}d forward return, "
          f"{len(sigs)} candidate signals")
    bar = np.sqrt(2 * np.log(len(sigs)))
    print(f"multiple-comparisons bar for {len(sigs)} signals: "
          f"|t| >= sqrt(2 ln N) = {bar:.2f}\n")

    rows, per_era = [], {}
    for name, fn in sigs.items():
        rank_ics, pear_ics, by_era = [], [], {}
        for t in rebals:
            fwd = window_ret(R, t, t + HORIZON)
            sc = fn(R, V, O, t)
            if fwd is None or sc is None:
                continue
            a, b = ic(sc, fwd, True), ic(sc, fwd, False)
            if a is not None:
                rank_ics.append(a)
                by_era.setdefault(charter.era_of(dates[t]) or "?", []).append(a)
            if b is not None:
                pear_ics.append(b)
        if len(rank_ics) < 5:
            continue
        r = np.array(rank_ics)
        t_stat = r.mean() / (r.std(ddof=1) / np.sqrt(len(r))) if r.std(ddof=1) > 0 else 0.0
        rows.append((name, r.mean(), float(np.mean(pear_ics)), t_stat,
                     float((r > 0).mean()), len(r), float(r.std(ddof=1))))
        per_era[name] = {}
        for k, v in by_era.items():
            a = np.array(v)
            s = a.std(ddof=1) if len(a) > 1 else 0.0
            te = a.mean() / (s / np.sqrt(len(a))) if s > 0 else 0.0
            per_era[name][k] = (float(a.mean()), len(a), float(te))

    rows.sort(key=lambda x: -abs(x[1]))
    print(f"{'signal':26s} {'rankIC':>8} {'pearsIC':>8} {'t':>7} "
          f"{'hit%':>6} {'n':>4}  {'implied Sharpe':>14}")
    print("-" * 88)
    for name, ric, pic, t_stat, hit, n, sd in rows:
        flag = "  <-- clears MC bar" if abs(t_stat) >= bar else ""
        print(f"{name:26s} {ric:+8.4f} {pic:+8.4f} {t_stat:+7.2f} "
              f"{hit*100:5.1f}% {n:4d}  {implied_sharpe(ric):14.2f}{flag}")

    # The decision-relevant number: with this much data, how big would an IC
    # have to be before this sample could tell it apart from zero at all?
    sds = [r[6] for r in rows]
    n_per = rows[0][5]
    sd_typ = float(np.median(sds))
    detectable = bar * sd_typ / np.sqrt(n_per)
    print(f"\nPOWER. Median per-period IC sd across candidates: {sd_typ:.4f} "
          f"over n={n_per} rebalances.")
    print(f"  Smallest IC this sample could distinguish from zero at the "
          f"multiple-comparisons bar: {detectable:.4f}")
    print(f"  IC required to be worth trading (T2): ~0.04-0.05")
    print(f"  -> the detection threshold and the profitability threshold are "
          f"the SAME SIZE. This window cannot confirm a tradeable signal here.")

    if pooled:
        # DIAGNOSTIC on Amendment D, not a justification for it. D was decided
        # on instrument properties and deliberately never looked at signal ICs
        # by era. This is the check: if a signal's IC swings wildly across eras
        # the pooled mean is an average over different markets after all, and
        # that must be said out loud rather than buried in the pooled number.
        print(f"\nPER-ERA BREAKDOWN (diagnostic on Amendment D's pooling)")
        eras = ["early", "ramp", "modern"]
        print(f"{'signal':26s} " + " ".join(f"{e:>16s}" for e in eras))
        print("-" * 78)
        verdicts = {}
        for name, ric, *_ in rows:
            cells = []
            for e in eras:
                m = per_era.get(name, {}).get(e)
                cells.append(f"{m[0]:+8.4f} t{m[2]:+5.2f}" if m else f"{'-':>15s}")
            # Amendment D5, as the charter implements it -- not a local rule.
            ok, why = charter.pooled_estimate_admissible(
                {e: (v[0], v[1]) for e, v in per_era.get(name, {}).items()})
            verdicts[name] = (ok, why)
            print(f"{name:26s} " + " ".join(cells) +
                  ("" if ok else "   D5: REFUSED"))
        n_ok = sum(1 for ok, _ in verdicts.values() if ok)
        print(f"\n  Amendment D5 admits {n_ok} of {len(verdicts)} pooled "
              f"estimates. Reasons for the rest:")
        for name, (ok, why) in verdicts.items():
            if not ok:
                print(f"    {name:26s} {why[0]}")
        print(f"\n  per-era t is against |t| >= {bar:.2f} (11 signals); "
              f"across both windows and three eras the honest bar is higher.")

    print(f"\nreference: T2 says rank IC ~0.04 reaches A5's floor 0.8, "
          f"~0.05 reaches its preferred 1.0")
    if pooled:
        b2 = np.sqrt(2 * np.log(2 * len(sigs)))
        print(f"NOTE: these eleven were already measured on `modern` "
              f"(ARENAS T2b). This is a SECOND look at the same set, so "
              f"anything chosen from across both windows is priced at "
              f"{2*len(sigs)} looks, |t| >= {b2:.2f}, not {bar:.2f}.")
    print("[ROLL] = front-contract volume/OI, which drops at every monthly roll; "
          "a 21d change spans one, so those two rows are contaminated by "
          "construction and are shown, not trusted.")


if __name__ == "__main__":
    main()
