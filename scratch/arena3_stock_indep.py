"""How many INDEPENDENT bets does the stock-futures universe supply?

Arena 3's index universe supplies 1.06 (rho_bar 0.914 across three symbols).
That is the binding constraint on anything registered there. This measures the
same quantity for single-stock futures, which is the only other 1-leg
instrument this archive covers.

Not a strategy result: no signal, no positions, no P&L. Unconditional
correlation of the instrument set.
"""
import datetime as dt
import math
import statistics as st
import sys

sys.path.insert(0, r"D:\Projects\Agentic_Trader")

from backtest import futures

WINDOWS = [
    ("modern", dt.date(2024, 1, 1), dt.date(2026, 8, 8)),
    ("ramp",   dt.date(2020, 1, 1), dt.date(2023, 12, 31)),
]
GATE = "strict_legacy"
MIN_BARS = 200          # a name must have this much history in the window


def corr(xs, ys):
    mx, my = st.mean(xs), st.mean(ys)
    num = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    dx = math.sqrt(sum((x - mx) ** 2 for x in xs))
    dy = math.sqrt(sum((y - my) ** 2 for y in ys))
    return num / (dx * dy) if dx > 0 and dy > 0 else None


def run(label, start, end):
    dates = futures.trading_dates(start, end)
    panel = futures.build_panel(dates, kind="stock", gate=GATE)
    print(f"\n=== {label}  {start} -> {end}  {len(dates)} sessions ===")
    print(f"panel: symbols={len(panel.series)} checked={panel.checked} "
          f"fillable={panel.fillable} pass={panel.pass_rate:.1f}%")

    # return-by-date per symbol, keeping only names with real history
    series = {}
    for sym, ser in panel.series.items():
        d = {b.date: r for b, r in zip(ser.bars, ser.rets) if r is not None}
        if len(d) >= MIN_BARS:
            series[sym] = d
    print(f"names with >={MIN_BARS} bars: {len(series)}")
    if len(series) < 5:
        return

    syms = sorted(series)
    # pairwise correlation on a common calendar
    common = set.intersection(*(set(series[s]) for s in syms))
    common = sorted(common)
    print(f"common sessions across all names: {len(common)}")
    if len(common) < 100:
        # too strict; fall back to pairwise-common dates
        common = None

    vals = []
    pairs_used = 0
    for i, a in enumerate(syms):
        for b in syms[i + 1:]:
            if common:
                da = [series[a][d] for d in common]
                db = [series[b][d] for d in common]
            else:
                dts = sorted(set(series[a]) & set(series[b]))
                if len(dts) < 150:
                    continue
                da = [series[a][d] for d in dts]
                db = [series[b][d] for d in dts]
            c = corr(da, db)
            if c is not None:
                vals.append(c)
                pairs_used += 1

    vals.sort()
    n = len(syms)
    rho = st.mean(vals)
    n_eff = n / (1.0 + (n - 1) * rho)
    print(f"pairs={pairs_used}  rho_bar={rho:.3f}  "
          f"median={vals[len(vals)//2]:.3f}  "
          f"p05={vals[int(0.05*len(vals))]:.3f}  p95={vals[int(0.95*len(vals))]:.3f}")
    print(f"N={n} names  ->  N_eff = {n_eff:.2f} independent bets")

    # what a Rs 15L account can actually hold, from arena 2's own finding:
    # ~6-8 positions. N_eff of the largest 8 by that same rho:
    for k in (6, 8, 12):
        print(f"   if only {k} names are holdable: N_eff = "
              f"{k / (1.0 + (k - 1) * rho):.2f}")


if __name__ == "__main__":
    for label, a, b in WINDOWS:
        run(label, a, b)
