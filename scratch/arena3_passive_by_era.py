"""Does A5's individual floor of 0.8 always dominate passive beta?

T1 anchors its `--require sharpe` to Amendment A5's individual floor (0.8)
rather than to the measured passive Sharpe, on the grounds that 0.8 is the
stricter of the two. That is true in the modern window -- passive EW stock
futures pay Sharpe 0.24 there. It is a fact about the window, not a rule.

If any era pays passive Sharpe ABOVE 0.8, then in that era a book that harvests
beta and does nothing else clears A5, and A5 alone stops being a sufficient bar.
That is the question Amendment D would exist to answer, so it should be settled
with a measurement rather than an intuition.

Long-only, buy-and-hold, roll-adjusted, no signal. gate=strict_legacy so the
txns-column era break does not silently truncate anything (see ARENAS Finding 5).
"""
import datetime as dt
import math
import statistics as st
import sys

sys.path.insert(0, r"D:\Projects\Agentic_Trader")

from backtest import futures
from research import charter

GATE = "strict_legacy"
MIN_BARS = 150
MIN_NAMES_PER_DAY = 15


def stats(series):
    if len(series) < 2:
        return None, None
    m = st.mean(series)
    sd = st.pstdev(series) * math.sqrt(len(series) / (len(series) - 1))
    sharpe = (m / sd) * math.sqrt(252) if sd > 0 else None
    eq = 1.0
    for r in series:
        eq *= (1.0 + r)
    yrs = len(series) / 252.0
    dr = ((eq ** (1.0 / yrs)) - 1.0) * 100.0 if yrs > 0 and eq > 0 else None
    return sharpe, dr


def main():
    today = dt.date.today()
    print(f"{'era':8s} {'window':26s} {'names':>6s} {'days':>6s} "
          f"{'drift/yr':>10s} {'SHARPE':>8s}   verdict vs A5 floor 0.8")
    print("-" * 92)
    for era in ("early", "ramp", "modern"):
        start, end = charter.era_window(era, cap=today)
        dates = futures.trading_dates(start, end)
        panel = futures.build_panel(dates, kind="stock", gate=GATE)
        by_sym = {}
        for sym, ser in panel.series.items():
            d = {b.date: r for b, r in zip(ser.bars, ser.rets) if r is not None}
            if len(d) >= MIN_BARS:
                by_sym[sym] = d
        series = []
        for d in dates:
            vals = [by_sym[s][d] for s in by_sym if d in by_sym[s]]
            if len(vals) >= MIN_NAMES_PER_DAY:
                series.append(sum(vals) / len(vals))
        sharpe, dr = stats(series)
        if sharpe is None:
            print(f"{era:8s} {str(start)+' -> '+str(end):26s} "
                  f"{len(by_sym):6d} {len(series):6d}  (insufficient)")
            continue
        verdict = ("PASSIVE BEATS A5 -- beta alone clears the bar"
                   if sharpe >= charter.MIN_OOS_SHARPE else "A5 floor is stricter")
        print(f"{era:8s} {str(start)+' -> '+str(end):26s} "
              f"{len(by_sym):6d} {len(series):6d} {dr:+9.2f}% {sharpe:8.3f}   {verdict}")


if __name__ == "__main__":
    main()
