"""T2 — is arena 3 huntable at all, whatever the signal?

`tsmom-stock-modern` died at book Sharpe 0.27 and `trend-donchian-modern` died
before it. Two dead signals do not establish that the arena is dead: that claim
is universal over signals, and no single registered config can test it. The loop
screens one config, so registering the universal claim against one run would put
a statement in the kill log broader than the run supports.

This is the instrument that CAN address it. Every selection signal, whatever its
internals, is summarised for a ranking rule by its information coefficient — the
cross-sectional correlation between its score and the forward return it is
trying to predict. So instead of asking "does signal X work", parameterise over
IC and ask **what IC would be required** to reach Amendment A5's bar at
`max_open=8` in this universe, then compare that to the IC signals actually have.

NOT A HYPOTHESIS. No claim is registered and no config budget is spent — this is
a measurement of the instrument set, the same class as the 2026-08-09 survey and
the passive benchmark. It is recorded in ARENAS.md so it cannot later be
presented as a discovery.

**The bound is deliberately generous**, so that failing it is decisive while
passing it proves nothing:

  - no whole-lot granularity (arena 2 measured 35% capacity fill; here lots are
    infinitely divisible)
  - no margin constraint and no capacity refusal
  - zero friction in the headline row
  - the selection sees a perfectly standardised cross-section

A signal that cannot clear a bound this generous cannot clear the real thing.

Window, gate and universe are `tsmom-stock-modern`'s exactly, so the numbers are
comparable to its 0.27 and to the passive 0.629 of ARENAS Finding 6.
"""
import datetime as dt
import sys

import numpy as np

sys.path.insert(0, r"D:\Projects\Agentic_Trader")

from backtest import futures
from research import charter

GATE = "strict_legacy"
KIND = "stock"
MAX_OPEN = 8
HORIZONS = (21, 63)          # trading days held between rebalances
IC_GRID = (0.0, 0.02, 0.05, 0.10, 0.15, 0.20, 0.30, 0.50, 0.75, 1.0)
DRAWS = 200                  # noise draws per IC, since one draw is itself noisy
FRICTION_BPS = (0.0, 5.0)    # round-trip, charged at each rebalance
TSMOM_LOOKBACK = 126         # T1's registered defaults
TSMOM_SKIP = 21
SEED = 20260810


def zscore(a):
    """Cross-sectional standardisation, NaN-safe."""
    m, s = np.nanmean(a), np.nanstd(a)
    return (a - m) / s if s > 0 else np.zeros_like(a)


def build_matrix():
    start, end = charter.era_window("modern", cap=dt.date.today())
    dates = futures.trading_dates(start, end)
    panel = futures.build_panel(dates, kind=KIND, gate=GATE)
    syms = sorted(panel.series)
    idx = {d: i for i, d in enumerate(dates)}
    R = np.full((len(dates), len(syms)), np.nan)
    for j, s in enumerate(syms):
        ser = panel.series[s]
        for b, r in zip(ser.bars, ser.rets):
            if r is not None and b.date in idx:
                R[idx[b.date], j] = r
    return dates, syms, R, panel


def fwd_returns(R, t, h):
    """Compounded return over (t, t+h], NaN unless the name is there throughout."""
    blk = R[t + 1: t + 1 + h]
    if blk.shape[0] < h:
        return None
    valid = np.isfinite(blk).sum(axis=0) >= h * 0.8
    out = np.where(valid, np.nanprod(np.nan_to_num(blk, nan=0.0) + 1.0, axis=0) - 1.0, np.nan)
    return out


def trailing_tsmom(R, t):
    """T1's own score: return over t-(L+K) -> t-K, on the roll-safe series."""
    a, b = t - (TSMOM_LOOKBACK + TSMOM_SKIP), t - TSMOM_SKIP
    if a < 0:
        return None
    blk = R[a:b]
    valid = np.isfinite(blk).sum(axis=0) >= (b - a) * 0.8
    return np.where(valid, np.nanprod(np.nan_to_num(blk, nan=0.0) + 1.0, axis=0) - 1.0, np.nan)


def book_sharpe(R, picks, dates_n, friction_bps, long_short):
    """Daily book return series -> annualised Sharpe, as engines.summarise does."""
    daily = np.zeros(dates_n)
    for (t0, t1, longs, shorts) in picks:
        for d in range(t0 + 1, min(t1 + 1, dates_n)):
            rl = R[d, longs]
            v = np.nanmean(rl) if np.isfinite(rl).any() else 0.0
            if long_short and len(shorts):
                rs = R[d, shorts]
                v -= np.nanmean(rs) if np.isfinite(rs).any() else 0.0
            daily[d] = v
        if friction_bps and t0 + 1 < dates_n:
            daily[t0 + 1] -= friction_bps / 10000.0
    sd = daily.std(ddof=1)
    return (daily.mean() / sd) * np.sqrt(252) if sd > 0 else 0.0


def select(score, k, long_short):
    """Rank among VALID names only.

    Filling invalid entries with -inf and sorting the whole array puts the
    unusable names at the bottom of the order, so a short leg taken as
    `order[:k]` selects NaNs rather than the worst-scoring real names. That
    silently turns a long/short book into a long-only one.
    """
    ok = np.flatnonzero(np.isfinite(score))
    if ok.size < 2 * k:
        return np.array([], int), np.array([], int)
    order = ok[np.argsort(score[ok])]
    if long_short:
        half = k // 2
        return order[-half:], order[:half]
    return order[-k:], np.array([], int)


def run(R, dates, h, long_short, friction, rng):
    T = R.shape[0]
    rebals = list(range(TSMOM_LOOKBACK + TSMOM_SKIP, T - h, h))

    truth, tsmom_ic = [], []
    for t in rebals:
        f = fwd_returns(R, t, h)
        if f is None:
            continue
        tm = trailing_tsmom(R, t)
        if tm is not None:
            m = np.isfinite(f) & np.isfinite(tm)
            if m.sum() > 10:
                c = np.corrcoef(tm[m], f[m])[0, 1]
                if np.isfinite(c):
                    tsmom_ic.append(c)
        truth.append((t, f))

    results = {}
    for ic in IC_GRID:
        vals = []
        draws = 1 if ic >= 1.0 else DRAWS
        for _ in range(draws):
            picks = []
            for i, (t, f) in enumerate(truth):
                zf = zscore(f)
                if ic >= 1.0:
                    score = zf
                else:
                    noise = rng.standard_normal(f.shape)
                    score = ic * zf + np.sqrt(1 - ic * ic) * zscore(noise)
                score = np.where(np.isfinite(f), score, np.nan)
                longs, shorts = select(score, MAX_OPEN, long_short)
                t1 = truth[i + 1][0] if i + 1 < len(truth) else min(t + h, T - 1)
                if len(longs):
                    picks.append((t, t1, longs, shorts))
            vals.append(book_sharpe(R, picks, T, friction, long_short))
        results[ic] = vals
    return results, (float(np.mean(tsmom_ic)) if tsmom_ic else float("nan")), len(rebals)


def main():
    dates, syms, R, panel = build_matrix()
    print(f"window {dates[0]} -> {dates[-1]}   {len(dates)} sessions   "
          f"{len(syms)} symbols   gate={GATE}  pass={panel.pass_rate:.2f}%")
    print(f"bound is GENEROUS: no whole lots, no margin cap, no capacity refusal.\n")
    rng = np.random.default_rng(SEED)

    for h in HORIZONS:
        for long_short in (False, True):
            for friction in FRICTION_BPS:
                res, ic_meas, n_reb = run(R, dates, h, long_short, friction, rng)
                mode = "long/short 4+4" if long_short else "long-only  8  "
                print(f"=== hold {h}d | {mode} | friction {friction:.0f}bps | "
                      f"{n_reb} rebalances | measured tsmom IC = {ic_meas:+.4f} ===")
                print(f"    {'IC':>6} {'median':>8} {'p05':>8} {'p95':>8}")
                for ic, vals in res.items():
                    v = np.array(vals)
                    if len(v) == 1:
                        print(f"    {ic:6.2f} {v[0]:8.3f} {'(oracle)':>8}")
                    else:
                        print(f"    {ic:6.2f} {np.median(v):8.3f} "
                              f"{np.percentile(v,5):8.3f} {np.percentile(v,95):8.3f}")
                print()


if __name__ == "__main__":
    main()
