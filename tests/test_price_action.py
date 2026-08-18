"""Tests for the price-action primitives behind `pa-levels-modern`.

The single most valuable test in this file is the lookahead one. `rvol_ratio` was
the strongest signal in the entire Phase 2 survey -- IC 0.084, t 5.75,
sign-consistent in every calendar year -- and it was an artifact, because its
normaliser was a whole-session median and so the 10:00 value knew 15:00. A level
built from a bar the trade could not have seen is that same error in different
clothes, and it would look exactly like a discovery.

The rest pin the pinned parameters: if a constant here drifts from
research/PREREGISTRATION-price-action.md, the screen is no longer testing the
hypothesis that was registered.

Run with:  PYTHONUTF8=1 python tests/test_price_action.py
"""
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import numpy as np
import pandas as pd

from research import price_action as pa

PASS, FAIL = 0, 0


def check(name, cond):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  PASS  {name}")
    else:
        FAIL += 1
        print(f"  FAIL  {name}")


def bars(vals, start="2026-01-01 09:15", freq="1min"):
    idx = pd.date_range(start, periods=len(vals), freq=freq)
    o = [v[0] for v in vals]; h = [v[1] for v in vals]
    l = [v[2] for v in vals]; c = [v[3] for v in vals]
    return pd.DataFrame({"open": o, "high": h, "low": l, "close": c}, index=idx)


def test_pinned_constants():
    print("\npinned constants match the preregistration")
    check("tolerance multiple is 0.26", pa.TOLERANCE_TR_MULT == 0.26)
    check("lookback is 60 sessions", pa.LOOKBACK_SESSIONS == 60)
    check("min touches is 2", pa.MIN_TOUCHES == 2)
    check("retest window is 10 bars", pa.RETEST_MAX_BARS == 10)
    check("reversal threshold is 1.0x TR", pa.REVERSAL_TR_MULT == 1.0)
    check("four timeframes", pa.TIMEFRAMES == ("5min", "15min", "60min", "1D"))


def test_lookahead_guard():
    print("\nlookahead guard -- the rvol_ratio trap")
    df = bars([(100, 101, 99, 100)] * 20)
    decision = df.index[10]
    ok = df.loc[:decision - pd.Timedelta(minutes=1)]
    try:
        pa.assert_causal(ok, decision, "t")
        clean = True
    except pa.LookaheadError:
        clean = False
    check("a strictly-prior window passes", clean)

    bad = df.loc[:decision]           # includes the decision bar itself
    try:
        pa.assert_causal(bad, decision, "t")
        caught = False
    except pa.LookaheadError:
        caught = True
    check("a window INCLUDING the decision bar is refused", caught)

    try:
        pa.assert_causal(df, decision, "t")   # includes bars after
        caught2 = False
    except pa.LookaheadError:
        caught2 = True
    check("a window with future bars is refused", caught2)

    try:
        pa.build_levels(df, decision, 100.0)
        blocked = False
    except pa.LookaheadError:
        blocked = True
    check("build_levels refuses a non-causal window", blocked)
    check("empty window is not an error", pa.assert_causal(
        df.iloc[0:0], decision, "t") is None)


def test_candlesticks():
    print("\ncandlestick definitions")
    # doji: body <= 10% of range
    check("doji detected", "doji" in pa.candlestick(100, 105, 95, 100.2))
    check("wide body is not a doji", "doji" not in pa.candlestick(100, 105, 95, 104))
    # hammer: long lower shadow >= 2x body, small upper
    check("hammer detected", "hammer" in pa.candlestick(103, 104, 95, 103.5))
    check("inverted hammer detected",
          "inverted_hammer" in pa.candlestick(96, 105, 95, 96.5))
    # two-bar, coloured
    check("bullish harami", "bullish_harami" in
          pa.candlestick(99, 100, 98, 99.5, po=103, pc=97))
    check("bearish harami", "bearish_harami" in
          pa.candlestick(99.5, 100, 98, 99, po=97, pc=103))
    check("bullish engulfing", "bullish_engulfing" in
          pa.candlestick(97, 104, 96, 103, po=102, pc=98))
    check("bearish engulfing", "bearish_engulfing" in
          pa.candlestick(103, 104, 96, 97, po=98, pc=102))


def test_direction_comes_from_context():
    print("\nshape is neutral; the level supplies direction")
    check("hammer confirms a LONG at support", pa.confirms(["hammer"], "long"))
    check("hammer ALSO confirms a SHORT at resistance (hanging man)",
          pa.confirms(["hammer"], "short"))
    check("doji confirms either side", pa.confirms(["doji"], "long")
          and pa.confirms(["doji"], "short"))
    check("bullish engulfing confirms long only",
          pa.confirms(["bullish_engulfing"], "long")
          and not pa.confirms(["bullish_engulfing"], "short"))
    check("bearish harami confirms short only",
          pa.confirms(["bearish_harami"], "short")
          and not pa.confirms(["bearish_harami"], "long"))
    check("no pattern confirms nothing", not pa.confirms([], "long"))


def test_structure():
    print("\nswing structure")
    piv = [(0, 110, "H"), (0, 100, "L"), (0, 108, "H"), (0, 96, "L")]
    check("LH-LL detected", pa.structure(piv) == "LH-LL")
    piv_up = [(0, 100, "H"), (0, 90, "L"), (0, 106, "H"), (0, 95, "L")]
    check("HH-HL detected", pa.structure(piv_up) == "HH-HL")
    mixed = [(0, 100, "H"), (0, 90, "L"), (0, 106, "H"), (0, 85, "L")]
    check("mixed structure is None", pa.structure(mixed) is None)
    check("too few pivots is None", pa.structure(piv[:2]) is None)


def test_tolerance_scales_with_timeframe():
    print("\ntolerance scales with the timeframe's own true range")
    rng = np.random.default_rng(0)
    px = 24000 + np.cumsum(rng.normal(0, 3, 2000))
    df = pd.DataFrame({"open": px, "high": px + 4, "low": px - 4, "close": px},
                      index=pd.date_range("2026-01-01 09:15", periods=2000, freq="1min"))
    t5 = pa.tolerance_for(pa.resample(df, "5min"))
    t60 = pa.tolerance_for(pa.resample(df, "60min"))
    check("5-min tolerance is positive", t5 > 0)
    check("longer timeframe has wider tolerance", t60 > t5)
    tr5 = float(pa.true_range(pa.resample(df, "5min")).dropna().median())
    check("tolerance is exactly 0.26 x median TR", abs(t5 - 0.26 * tr5) < 1e-9)


def test_gap_levels():
    print("\ngap levels")
    d = bars([(100, 102, 99, 101),      # day 0: high 102
              (105, 107, 104, 106),     # day 1: low 104 -> gap 102..104
              (106, 108, 105, 107)],    # never returns below 104
             freq="1D")
    lv = pa.gap_levels(d, 107)
    prices = sorted(l.price for l in lv)
    check("unfilled gap yields two edges", len(lv) == 2)
    check("gap edges are the prior high and next low", prices == [102.0, 104.0])

    d2 = bars([(100, 102, 99, 101), (105, 107, 104, 106), (105, 106, 100, 101)],
              freq="1D")
    check("a gap traded back through is not a level", pa.gap_levels(d2, 101) == [])


def main():
    test_pinned_constants()
    test_lookahead_guard()
    test_candlesticks()
    test_direction_comes_from_context()
    test_structure()
    test_tolerance_scales_with_timeframe()
    test_gap_levels()
    print(f"\n{'=' * 50}\nRESULT: {PASS} passed, {FAIL} failed")
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
