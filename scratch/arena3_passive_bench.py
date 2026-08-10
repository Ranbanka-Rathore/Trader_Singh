"""What does PASSIVE long exposure pay in arena 3's stock universe?

Finding 5 (2026-08-09) said a long-biased hypothesis must declare
`--require sharpe>=X` against the measured passive Sharpe for the IDENTICAL
window. That number does not exist yet for stock futures, and T1 cannot be
registered honestly without it.

Window is T1's own: `--era modern` resolves through charter.era_window() to
2023-01-01 -> today, NOT the 2024-01-01 the 2026-08-09 survey happened to use.
Gate is `strict`, matching T1's registration, not stock_indep.py's strict_legacy.

Not a strategy result: no signal, no entries, no exits. Buy everything, hold it,
roll it. That is the thing a long-biased signal has to beat to have said anything.
"""
import datetime as dt
import math
import random
import statistics as st
import sys

sys.path.insert(0, r"D:\Projects\Agentic_Trader")

from backtest import futures
from research import charter

GATE = "strict"
MIN_BARS = 200        # a name needs real history to join the book
MIN_NAMES_PER_DAY = 20
BOOT_DRAWS = 2000
BOOT_K = (6, 8, 12)
SEED = 20260810


def sharpe(series):
    """Annualised, computed the way research.engines.summarise does."""
    if len(series) < 2:
        return None
    m = st.mean(series)
    sd = st.pstdev(series) * math.sqrt(len(series) / (len(series) - 1))
    return (m / sd) * math.sqrt(252) if sd > 0 else None


def drift(series):
    """Compounded annualised drift, in percent."""
    eq = 1.0
    for r in series:
        eq *= (1.0 + r)
    yrs = len(series) / 252.0
    return ((eq ** (1.0 / yrs)) - 1.0) * 100.0 if yrs > 0 and eq > 0 else None


def ew_series(by_sym, dates, min_names):
    """Equal-weight daily return across whatever is available that day."""
    out = []
    for d in dates:
        vals = [by_sym[s][d] for s in by_sym if d in by_sym[s]]
        if len(vals) >= min_names:
            out.append(sum(vals) / len(vals))
    return out


def main():
    start, end = charter.era_window("modern", cap=dt.date.today())
    print(f"T1 window (charter.era_window('modern')): {start} -> {end}")

    dates = futures.trading_dates(start, end)
    panel = futures.build_panel(dates, kind="stock", gate=GATE)
    print(f"sessions={len(dates)}  symbols={len(panel.series)}  "
          f"checked={panel.checked}  fillable={panel.fillable}  "
          f"pass={panel.pass_rate:.2f}%")
    if panel.refusals:
        print(f"refusals: {dict(list(panel.refusals.items())[:5])}")

    by_sym = {}
    for sym, ser in panel.series.items():
        d = {b.date: r for b, r in zip(ser.bars, ser.rets) if r is not None}
        if len(d) >= MIN_BARS:
            by_sym[sym] = d
    print(f"names with >={MIN_BARS} bars: {len(by_sym)}")

    # ── 1. the diversification ceiling: hold the whole universe ──────────────
    full = ew_series(by_sym, dates, MIN_NAMES_PER_DAY)
    print(f"\n--- passive EW, ALL {len(by_sym)} names ({len(full)} days) ---")
    print(f"  annualised drift {drift(full):+.2f}%/yr   "
          f"vol {st.pstdev(full)*math.sqrt(252)*100:.2f}%   "
          f"SHARPE {sharpe(full):.3f}")

    # ── 2. what a Rs 15L account can actually hold ──────────────────────────
    rng = random.Random(SEED)
    syms = sorted(by_sym)
    for k in BOOT_K:
        if len(syms) < k:
            continue
        vals = []
        for _ in range(BOOT_DRAWS):
            pick = rng.sample(syms, k)
            sub = {s: by_sym[s] for s in pick}
            s_ = sharpe(ew_series(sub, dates, max(2, k // 2)))
            if s_ is not None:
                vals.append(s_)
        vals.sort()
        print(f"\n--- passive EW, random {k}-name books "
              f"({len(vals)} draws) ---")
        print(f"  SHARPE  p05 {vals[int(0.05*len(vals))]:.3f}   "
              f"median {vals[len(vals)//2]:.3f}   "
              f"p95 {vals[int(0.95*len(vals))]:.3f}")
        # The number that matters for --require: how often does a book with NO
        # signal at all clear the bar we are thinking of declaring?
        print("  P(passive book clears a --require sharpe bar):")
        for bar in (0.30, 0.50, 0.62, 0.80, 1.00):
            hit = sum(1 for v in vals if v >= bar) / len(vals)
            print(f"     sharpe>={bar:.2f}  ->  {hit*100:5.1f}% of random "
                  f"{k}-name buy-and-hold books already pass")

    # ── 3. index reference on the SAME window ───────────────────────────────
    ipanel = futures.build_panel(dates, kind="index", gate=GATE)
    for sym in ("NIFTY", "BANKNIFTY"):
        ser = ipanel.series.get(sym)
        if not ser:
            continue
        r = [x for x in ser.rets if x is not None]
        print(f"\n--- passive long {sym} ({len(r)} days) ---")
        print(f"  annualised drift {drift(r):+.2f}%/yr   "
              f"vol {st.pstdev(r)*math.sqrt(252)*100:.2f}%   "
              f"SHARPE {sharpe(r):.3f}")


if __name__ == "__main__":
    main()
