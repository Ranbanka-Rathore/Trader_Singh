"""Characterise arena 3 before drafting a hypothesis in it.

NOT a strategy result. This measures the unconditional properties of the
instrument set -- drift, vol, correlation, and how many independent bets the
universe can actually supply. If three index futures move as one, then a
"30 trades" sample in this arena is not 30 independent observations, and no
signal can clear the Section 4 bar honestly. That is worth knowing before
spending a registration, and it is disclosed in ARENAS.md.
"""
import datetime as dt
import math
import os
import statistics as st
import sys

sys.path.insert(0, r"D:\Projects\Agentic_Trader")

from backtest import futures

START = dt.date(2016, 1, 1)
END = dt.date(2026, 8, 8)
GATE = "strict_legacy"

ERAS = [
    ("early",  dt.date(2016, 1, 1), dt.date(2019, 12, 31)),
    ("ramp",   dt.date(2020, 1, 1), dt.date(2023, 12, 31)),
    ("modern", dt.date(2024, 1, 1), dt.date(2026, 8, 8)),
]


def ann_stats(rets):
    r = [x for x in rets if x is not None]
    if len(r) < 20:
        return None
    mu = st.mean(r)
    sd = st.stdev(r)
    drift = ((1.0 + mu) ** 252 - 1.0) * 100.0
    vol = sd * math.sqrt(252) * 100.0
    sharpe = (mu / sd) * math.sqrt(252) if sd > 0 else float("nan")
    return {"n": len(r), "drift_pct": drift, "vol_pct": vol, "sharpe": sharpe}


def corr(a, b):
    pairs = [(x, y) for x, y in zip(a, b) if x is not None and y is not None]
    if len(pairs) < 30:
        return None
    xs = [p[0] for p in pairs]
    ys = [p[1] for p in pairs]
    mx, my = st.mean(xs), st.mean(ys)
    num = sum((x - mx) * (y - my) for x, y in pairs)
    dx = math.sqrt(sum((x - mx) ** 2 for x in xs))
    dy = math.sqrt(sum((y - my) ** 2 for y in ys))
    return num / (dx * dy) if dx > 0 and dy > 0 else None


def main():
    dates = futures.trading_dates(START, END)
    print(f"window {START} -> {END}   {len(dates)} sessions   gate={GATE}")
    panel = futures.build_panel(dates, kind="index", gate=GATE,
                                symbols=("NIFTY", "BANKNIFTY", "FINNIFTY"))
    print(f"panel: checked={panel.checked} fillable={panel.fillable} "
          f"pass={panel.pass_rate:.1f}% rolls={panel.rolls} "
          f"missing_lot={panel.missing_lot}")
    print(f"refusals: {panel.refusals}")
    print()

    # per symbol, full window and per era
    print(f"{'symbol':<11}{'era':<8}{'bars':>7}{'drift%/y':>11}{'vol%/y':>9}{'Sharpe':>8}")
    by_date_ret = {}
    for sym in sorted(panel.series):
        ser = panel.series[sym]
        for label, lo, hi in [("ALL", START, END)] + [(e, a, b) for e, a, b in ERAS]:
            idx = [i for i, b in enumerate(ser.bars) if lo <= b.date <= hi]
            s = ann_stats([ser.rets[i] for i in idx])
            if s is None:
                print(f"{sym:<11}{label:<8}{'-':>7}{'thin':>11}")
                continue
            print(f"{sym:<11}{label:<8}{s['n']:>7}{s['drift_pct']:>11.2f}"
                  f"{s['vol_pct']:>9.2f}{s['sharpe']:>8.2f}")
        print()
        d = {}
        for i, b in enumerate(ser.bars):
            d[b.date] = ser.rets[i]
        by_date_ret[sym] = d

    # correlation on common dates
    syms = sorted(by_date_ret)
    print("daily-return correlation (full window, common sessions)")
    print(f"{'':<11}" + "".join(f"{s:>11}" for s in syms))
    for a in syms:
        row = f"{a:<11}"
        common = sorted(set(by_date_ret[a]) & set(by_date_ret[a]))
        for b in syms:
            dts = sorted(set(by_date_ret[a]) & set(by_date_ret[b]))
            c = corr([by_date_ret[a][d] for d in dts],
                     [by_date_ret[b][d] for d in dts])
            row += f"{c:>11.3f}" if c is not None else f"{'-':>11}"
        print(row)
    print()

    # how many sessions does each symbol actually cover?
    for s in syms:
        bars = panel.series[s].bars
        if bars:
            print(f"{s:<11} first={bars[0].date}  last={bars[-1].date}  "
                  f"bars={len(bars)}")


if __name__ == "__main__":
    main()
