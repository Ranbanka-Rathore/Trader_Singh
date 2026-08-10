"""Can the remaining arenas detect an A5-admissible strategy at all?

Arenas 2 and 3 were checked with information coefficient, because both are
selection strategies and IC is what summarises a ranking rule. `event_vol` and
`index_structures` are not selection strategies -- one sells volatility into
scheduled events, the other trades option structures in a regime -- so IC is the
wrong instrument and forcing it would be a category error.

The general form of the question, which does apply to all four, is: **is the
effect a strategy needs in order to be worth trading larger or smaller than the
effect the available data can distinguish from zero?**

There is a clean way to ask it. For a strategy with annualised Sharpe S observed
over Y years, the t-statistic of its mean is approximately

    t  ~=  S * sqrt(Y)

independent of trade frequency -- trading more often buys more trades but each
carries proportionally less. So the smallest Sharpe an arena can distinguish from
noise at a bar of `b` is `S_min = b / sqrt(Y)`, and since Section 4 sets
`b = sqrt(2 ln N)` for N configurations, **the config budget and the detectable
Sharpe are the same quantity seen twice.**

The approximation is checked against all six closed hypotheses below rather than
asserted.

Second question, which is Amendment A5's and is about supply rather than power:
A5 requires OOS Sharpe >= 0.8 on **>= 100 OOS trades**. `walk_forward` is
anchored with TRAIN_MONTHS=6 and one-month test folds, so every month after the
first six is out-of-sample. That makes the required trade RATE computable, and
comparable against what each arena has actually produced.

NOT A HYPOTHESIS. No claim registered, no budget spent. It reads results already
in the kill log and adds no new backtest.
"""
import datetime as dt
import glob
import json
import math
import os
import sys

sys.path.insert(0, r"D:\Projects\Agentic_Trader")

from research import charter

TRAIN_MONTHS = 6          # backtest/walkforward.py:54
A5_OOS_TRADES = charter.MIN_OOS_TRADES


def years(w):
    a, b = dt.date.fromisoformat(w[0]), dt.date.fromisoformat(w[1])
    return (b - a).days / 365.25


def load_results():
    out = []
    for p in sorted(glob.glob(os.path.join("research", "results", "*.json"))):
        d = json.load(open(p, encoding="utf-8"))
        s, h = d["screen"], d["hypothesis"]
        g = s["gates"].get(s["gate"]) or list(s["gates"].values())[-1]
        out.append({
            "id": h["id"], "arena": h["arena"], "n": g["n_trades"],
            "t": g["t"], "sharpe": g["sharpe"], "years": years(h["window"]),
        })
    return out


def main():
    rows = load_results()

    print("=" * 78)
    print("1. Is  t ~= Sharpe * sqrt(years)  actually true here?")
    print("=" * 78)
    print(f"{'hypothesis':24s} {'sharpe':>7} {'yrs':>6} {'predicted t':>12} "
          f"{'actual t':>9} {'ratio':>7}")
    ratios = []
    for r in rows:
        pred = r["sharpe"] * math.sqrt(r["years"])
        ratio = (r["t"] / pred) if pred else float("nan")
        ratios.append(ratio)
        print(f"{r['id']:24s} {r['sharpe']:+7.2f} {r['years']:6.2f} "
              f"{pred:+12.2f} {r['t']:+9.2f} {ratio:7.2f}")
    lo, hi = min(ratios), max(ratios)
    print(f"\n  ratios span {lo:.2f}-{hi:.2f}, mean {sum(ratios)/len(ratios):.2f}. "
          f"Good enough to reason with,")
    print(f"  not exact -- overlapping trades and uneven trade timing move it. "
          f"Treat as +/-30%.")

    print("\n" + "=" * 78)
    print("2. What Sharpe can each window detect, and what config budget does")
    print("   that leave? (S_min = sqrt(2 ln N) / sqrt(Y))")
    print("=" * 78)
    for label, y in (("modern era", 3.60), ("full archive", 10.6)):
        print(f"\n  {label} ({y:.2f} years, sqrt = {math.sqrt(y):.2f}):")
        print(f"    {'N configs':>10} {'noise bar':>10} {'detectable Sharpe':>19}")
        for n in (1, 2, 3, 4, 8, 11, 20):
            bar = charter.noise_threshold(n)
            print(f"    {n:>10} {bar:>10.2f} {bar/math.sqrt(y):>19.2f}"
                  + ("   <- A5 floor 0.8 still visible"
                     if bar / math.sqrt(y) <= charter.MIN_OOS_SHARPE else ""))
        # largest N whose bar keeps A5's floor detectable
        best = max([n for n in range(1, 200)
                    if charter.noise_threshold(n) <= charter.MIN_OOS_SHARPE * math.sqrt(y)],
                   default=0)
        print(f"    => at most {best} configuration(s) if a Sharpe-0.8 strategy "
              f"is to be detectable at all")

    print("\n" + "=" * 78)
    print(f"3. Can each arena supply A5's {A5_OOS_TRADES} OOS trades?")
    print(f"   (anchored walk-forward, {TRAIN_MONTHS}-month train, monthly test "
          f"folds -> OOS = all but the first {TRAIN_MONTHS} months)")
    print("=" * 78)
    print(f"{'hypothesis':24s} {'trades/yr':>10} {'OOS mo':>7} {'OOS trades':>11} "
          f"{'rate needed':>12} {'verdict':>10}")
    for r in rows:
        months = r["years"] * 12.0
        oos_months = max(0.0, months - TRAIN_MONTHS)
        rate = r["n"] / r["years"]
        oos_trades = rate / 12.0 * oos_months
        needed = A5_OOS_TRADES / oos_months * 12.0 if oos_months > 0 else float("inf")
        ok = "OK" if oos_trades >= A5_OOS_TRADES else f"{A5_OOS_TRADES/oos_trades:.1f}x short"
        print(f"{r['id']:24s} {rate:10.1f} {oos_months:7.0f} {oos_trades:11.0f} "
              f"{needed:12.1f} {ok:>10}")

    print(f"\n  A hypothesis on the modern era needs "
          f"{A5_OOS_TRADES / (3.60*12 - TRAIN_MONTHS) * 12:.1f} trades/year "
          f"to reach {A5_OOS_TRADES} OOS trades.")
    print(f"  Over the full archive it needs only "
          f"{A5_OOS_TRADES / (10.6*12 - TRAIN_MONTHS) * 12:.1f}/year -- but for "
          f"OPTIONS that means pooling")
    print(f"  across the liquidity ramp B3 was written for, where the era break "
          f"is real (10% -> 64%")
    print(f"  of legs tradeable). Amendment D2 scoped B3 out for stock futures "
          f"because their")
    print(f"  pass rate is flat; it does not and cannot do the same for the "
          f"option book.")


if __name__ == "__main__":
    main()
