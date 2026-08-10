"""Does arena 3's IC problem also close arena 2 (cross-sectional)?

Arena 3 closed because its detection threshold and its profitability threshold
are the same size: on `modern` the smallest detectable IC (0.0551) exceeds the
~0.04-0.05 needed to be tradeable. Arena 2 draws on the SAME stock-futures
universe, so the detectability half plausibly carries over unchanged. The
required half does not carry over automatically -- it depends on breadth, and
arena 2 is built differently.

  arena 3 (futures_trend): directional, max_open=8, net beta.
  arena 2 (cross_sectional): dollar-neutral, n_per_side=5 -> 10 positions
    WANTED, rebalanced every 21 CALENDAR days (~15 trading days).

The catch is that `xsect-mom-modern` filled only **35%** of the positions it
wanted at Rs 15L. So the effective breadth is ~3.5 names, not 10 -- which is
LOWER than arena 3's 8, and lower breadth means a HIGHER required IC. This
measures whether that is enough to close arena 2 too.

NOT A HYPOTHESIS. No claim registered, no config budget spent. It does spend
knowledge of arena 2, which is still OPEN, so it is disclosed in ARENAS.md and
anything registered there afterwards is registered knowing this.

The bound is generous in the same way T2's was: no whole lots, no margin cap, no
per-name lot limit, and a 0 bps row. Failing it is decisive; clearing it is not.
"""
import datetime as dt
import sys

import numpy as np

sys.path.insert(0, r"D:\Projects\Agentic_Trader")

from backtest import futures
from research import charter

GATE = "strict_legacy"
HORIZON = 15                  # ~21 calendar days, arena 2's rebalance_days
WARMUP = 273                  # 252 + 21, arena 2's 12-1 lookback
IC_GRID = (0.0, 0.02, 0.05, 0.10, 0.15, 0.20, 0.30, 0.50, 1.0)
DRAWS = 200
# n_per_side: the engine wants 5; xsect-mom-modern actually filled 35% of the
# positions it asked for, so ~2 per side is what Rs 15L delivered.
SIDES = (5, 2)
FRICTION_BPS = (0.0, 10.0)    # round trip at the engine's own 5 bps per leg
MIN_FRAC = 0.8
SEED = 20260810


def window_ret(R, a, b):
    if a < 0 or b > R.shape[0]:
        return None
    blk = R[a:b]
    if blk.shape[0] == 0:
        return None
    ok = np.isfinite(blk).sum(axis=0) >= blk.shape[0] * MIN_FRAC
    return np.where(ok, np.nanprod(np.nan_to_num(blk, nan=0.0) + 1.0, axis=0) - 1.0, np.nan)


def zscore(a):
    m, s = np.nanmean(a), np.nanstd(a)
    return (a - m) / s if s > 0 else np.zeros_like(a)


def rank_of(a, mask):
    out = np.full(a.shape, np.nan)
    idx = np.flatnonzero(mask)
    out[idx[np.argsort(a[idx])]] = np.arange(idx.size, dtype=float)
    return out


def main():
    start, end = charter.era_window("modern", cap=dt.date.today())
    dates = futures.trading_dates(start, end)
    panel = futures.build_panel(dates, kind="stock", gate=GATE)
    syms = sorted(panel.series)
    idx = {d: i for i, d in enumerate(dates)}
    T, N = len(dates), len(syms)
    R = np.full((T, N), np.nan)
    for j, s in enumerate(syms):
        ser = panel.series[s]
        for b, r in zip(ser.bars, ser.rets):
            i = idx.get(b.date)
            if i is not None and r is not None:
                R[i, j] = r

    rebals = [t for t in range(WARMUP, T - HORIZON, HORIZON)]
    print(f"window {dates[0]} -> {dates[-1]}   {T} sessions   {N} symbols   "
          f"gate={GATE}")
    print(f"horizon {HORIZON}d (arena 2 rebalances every 21 calendar days), "
          f"{len(rebals)} rebalances\n")

    truth, ics = [], []
    for t in rebals:
        f = window_ret(R, t, t + HORIZON)
        if f is None:
            continue
        truth.append((t, f))
        mom = window_ret(R, t - 273, t - 21)          # arena 2's own 12-1 signal
        if mom is not None:
            m = np.isfinite(f) & np.isfinite(mom)
            if m.sum() > 30:
                c = np.corrcoef(rank_of(mom, m)[m], rank_of(f, m)[m])[0, 1]
                if np.isfinite(c):
                    ics.append(c)

    ics = np.array(ics)
    sd, n = ics.std(ddof=1), len(ics)
    t_stat = ics.mean() / (sd / np.sqrt(n))
    print(f"--- arena 2's own signal, 12-1 momentum, at its own horizon ---")
    print(f"  rank IC {ics.mean():+.4f}   t {t_stat:+.2f}   n {n}   sd {sd:.4f}")

    for bar, label in ((np.sqrt(2 * np.log(2)), "1 signal"),
                       (np.sqrt(2 * np.log(11)), "11 signals")):
        print(f"  smallest detectable IC at the {label} bar "
              f"(|t|>={bar:.2f}): {bar * sd / np.sqrt(n):.4f}")

    rng = np.random.default_rng(SEED)
    for k in SIDES:
        for fr in FRICTION_BPS:
            print(f"\n--- dollar-neutral ceiling, {k}+{k} names, "
                  f"friction {fr:.0f}bps ---")
            print(f"    {'IC':>6} {'median':>8} {'p05':>8} {'p95':>8}")
            for ic in IC_GRID:
                vals = []
                for _ in range(1 if ic >= 1.0 else DRAWS):
                    daily = np.zeros(T)
                    for i, (t, f) in enumerate(truth):
                        zf = zscore(f)
                        sc = zf if ic >= 1.0 else (
                            ic * zf + np.sqrt(1 - ic * ic) * zscore(
                                rng.standard_normal(f.shape)))
                        sc = np.where(np.isfinite(f), sc, np.nan)
                        ok = np.flatnonzero(np.isfinite(sc))
                        if ok.size < 2 * k:
                            continue
                        order = ok[np.argsort(sc[ok])]
                        lo, sh = order[-k:], order[:k]
                        t1 = truth[i + 1][0] if i + 1 < len(truth) else min(t + HORIZON, T - 1)
                        for d in range(t + 1, min(t1 + 1, T)):
                            rl, rs = R[d, lo], R[d, sh]
                            v = (np.nanmean(rl) if np.isfinite(rl).any() else 0.0)
                            v -= (np.nanmean(rs) if np.isfinite(rs).any() else 0.0)
                            daily[d] = v
                        if fr and t + 1 < T:
                            daily[t + 1] -= fr / 10000.0
                    s = daily.std(ddof=1)
                    vals.append((daily.mean() / s) * np.sqrt(252) if s > 0 else 0.0)
                v = np.array(vals)
                if len(v) == 1:
                    print(f"    {ic:6.2f} {v[0]:8.3f} {'(oracle)':>8}")
                else:
                    print(f"    {ic:6.2f} {np.median(v):8.3f} "
                          f"{np.percentile(v,5):8.3f} {np.percentile(v,95):8.3f}")


if __name__ == "__main__":
    main()
