"""Arena 2's ONE pre-registered signal: cross-sectional carry (futures basis).

Declared in research/ARENAS.md section X1 and committed BEFORE this was run.
Signal, direction and decision thresholds were all fixed in that commit:

  carry_i = (F_front,i - S_i) / S_i  x  365 / days_to_expiry
  LONG high carry, SHORT low carry
  required rank IC 0.054 | detectable 0.0249 on modern | bar |t| >= 1.18

A significantly NEGATIVE IC refutes the signal. It does not license flipping the
sign — T2b established that mirrored signals are not extra evidence.

METHOD, matching scratch/arena3_signal_ic.py so the numbers are comparable:
same gate, same universe, same rank-IC definition, t across rebalance periods.
Two deliberate differences, both declared:

  - HORIZON = 15 trading days, which is ARENA 2's horizon (its rebalance_days=21
    is calendar days). The eleven were scored at arena 3's 21.
  - WARMUP is short. Carry is a point-in-time observable and needs no trailing
    window, so it earns more rebalances than any lookback signal can. That is a
    real power advantage of this signal, not a methodological liberty.

Rank invariance note: Indian stock futures share a common monthly expiry, so on
most dates `365/DTE` is one positive constant across the whole cross-section and
cannot change a rank. It is applied per-name anyway, for the dates where the
front expiry does differ.

Amendment D5: per-era ICs are computed first and
`charter.pooled_estimate_admissible` decides whether a pooled figure may be
quoted at all. Fails closed.

Run: PYTHONUTF8=1 python scratch/arena2_carry_ic.py
"""
import datetime as dt
import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from backtest import bhavcopy, futures
from research import charter

GATE = "strict_legacy"
KIND = "stock"
HORIZON = 15          # arena 2's holding horizon, in TRADING days
WARMUP = 21           # carry needs no lookback; this only lets returns settle
MIN_FRAC = 0.8
MIN_NAMES = 30
STOCK_TYPES = ("STF", "FUTSTK")

REQUIRED_IC = 0.054   # declared in ARENAS.md X1 before this ran
BAR_T = 1.18          # Section 4 at one pre-registered configuration


def rank(a, mask):
    out = np.full(a.shape, np.nan)
    idx = np.flatnonzero(mask)
    order = idx[np.argsort(a[idx])]
    out[order] = np.arange(len(order), dtype=float)
    return out


def ic(score, fwd):
    m = np.isfinite(score) & np.isfinite(fwd)
    if m.sum() < MIN_NAMES:
        return None
    x, y = rank(score.copy(), m), rank(fwd.copy(), m)
    xv, yv = x[m], y[m]
    if np.std(xv) == 0 or np.std(yv) == 0:
        return None
    c = np.corrcoef(xv, yv)[0, 1]
    return float(c) if np.isfinite(c) else None


def carry_row(d, syms_idx, out):
    """Fill `out` with (F_front - S)/S * 365/DTE for one session."""
    df = bhavcopy._read_df(d)
    if df is None:
        return 0
    futs = df[df["FinInstrmTp"].isin(STOCK_TYPES)]
    best = {}
    for row in futs.itertuples(index=False):
        sym = str(row.TckrSymb)
        j = syms_idx.get(sym)
        if j is None:
            continue
        try:
            expiry = dt.date.fromisoformat(str(row.XpryDt)[:10])
        except (ValueError, TypeError):
            continue
        dte = (expiry - d).days
        if dte <= 0:
            continue
        # front contract only, and it must have actually traded
        if float(row.TtlTradgVol or 0.0) <= 0:
            continue
        spot = float(getattr(row, "UndrlygPric", 0.0) or 0.0)
        close = float(row.ClsPric or 0.0) or float(row.SttlmPric or 0.0)
        if spot <= 0 or close <= 0:
            continue
        if j not in best or dte < best[j][0]:
            best[j] = (dte, (close - spot) / spot * 365.0 / dte)
    for j, (_dte, c) in best.items():
        out[j] = c
    return len(best)


def main():
    start, end = dt.date(2016, 1, 1), dt.date.today()
    dates = futures.trading_dates(start, end)
    panel = futures.build_panel(dates, kind=KIND, gate=GATE)
    syms = sorted(panel.series)
    sidx = {s: j for j, s in enumerate(syms)}
    didx = {d: i for i, d in enumerate(dates)}
    T, N = len(dates), len(syms)
    print(f"panel {dates[0]} -> {dates[-1]}   {T} sessions   {N} symbols")

    R = np.full((T, N), np.nan)
    for s in syms:
        ser = panel.series[s]
        for b, r in zip(ser.bars, ser.rets):
            i = didx.get(b.date)
            if i is not None and r is not None:
                R[i, sidx[s]] = r

    rebals = [t for t in range(WARMUP, T - HORIZON, HORIZON)]
    print(f"rebalances at a {HORIZON}-trading-day horizon: {len(rebals)}")

    rows = []          # (date, ic, era)
    for t in rebals:
        d = dates[t]
        C = np.full(N, np.nan)
        n = carry_row(d, sidx, C)
        if n < MIN_NAMES:
            continue
        blk = R[t:t + HORIZON]
        ok = np.isfinite(blk).sum(axis=0) >= blk.shape[0] * MIN_FRAC
        fwd = np.where(ok, np.nanprod(np.nan_to_num(blk, nan=0.0) + 1.0, axis=0) - 1.0,
                       np.nan)
        v = ic(C, fwd)
        if v is not None:
            rows.append((d, v, charter.era_of(d) or "unknown"))

    def stats(vals):
        if len(vals) < 2:
            return None, None, len(vals)
        m = float(np.mean(vals))
        se = float(np.std(vals, ddof=1) / np.sqrt(len(vals)))
        return m, (m / se if se > 0 else None), len(vals)

    print(f"\n{'era':<10} {'rank IC':>9} {'t':>7} {'n':>5}")
    per_era = {}
    for era in ("early", "ramp", "modern"):
        vals = [v for _d, v, e in rows if e == era]
        m, tt, n = stats(vals)
        if m is None:
            print(f"{era:<10} {'-':>9} {'-':>7} {n:>5}")
            continue
        per_era[era] = (m, n)
        print(f"{era:<10} {m:>+9.4f} {tt:>+7.2f} {n:>5}")

    allv = [v for _d, v, _e in rows]
    pm, pt, pn = stats(allv)
    print(f"{'POOLED':<10} {pm:>+9.4f} {pt:>+7.2f} {pn:>5}")

    ok, why = charter.pooled_estimate_admissible(per_era)
    print(f"\nD5 pooling admissible: {ok}")
    for r in why:
        print(f"  - {r}")

    basis = "pooled" if ok else "modern"
    m, tt, n = stats([v for _d, v, e in rows if ok or e == "modern"])
    print(f"\nDECISION BASIS: {basis}  (D1 makes `modern` the basis when pooling fails)")
    print(f"  rank IC {m:+.4f}   t {tt:+.2f}   n {n}")
    print(f"  required IC {REQUIRED_IC}   |t| bar {BAR_T}")
    passes = (m >= REQUIRED_IC) and (tt is not None and abs(tt) >= BAR_T)
    print(f"\n  VERDICT: {'REGISTER a hypothesis' if passes else 'FAILS the declared bar'}")
    if not passes:
        short = REQUIRED_IC - m
        print(f"  falls short of the required IC by {short:+.4f} "
              f"({m/REQUIRED_IC*100:.0f}% of what is needed)")


if __name__ == "__main__":
    main()
