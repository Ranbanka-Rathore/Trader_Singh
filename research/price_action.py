"""
Price-action primitives for `pa-levels-modern` (Amendment F).

Every parameter here is PINNED by research/PREREGISTRATION-price-action.md and
may not be tuned. This module deliberately exposes no defaults that differ from
that document, and no "just try 0.3 instead" knob, because the whole reason the
arena was allowed to reopen is that its degrees of freedom were fixed in advance.

THE ONE THING THAT MATTERS MOST
-------------------------------
Every function that builds a level, a swing, or a candlestick signal takes bars
STRICTLY PRIOR to the decision bar. `rvol_ratio` was the strongest signal in the
whole Phase 2 survey (IC 0.084, t 5.75, sign-consistent every year) and it was an
artifact: its normaliser was a whole-session median, so the 10:00 value knew
15:00. The same error wearing different clothes here would be a level built from
a bar the trade could not have seen -- and it would look like a discovery.

So `assert_causal()` exists and is called on every window this module hands out.
It is not a debug aid; it is the thing standing between this screen and a false
positive.
"""
from dataclasses import dataclass
from typing import Dict, List, Optional, Sequence, Tuple

import numpy as np
import pandas as pd

# ── pinned constants (preregistration §3.1-§3.3) ─────────────────────────────
TOLERANCE_TR_MULT = 0.26     # operator's "50 pts daily" / 195.3 median daily TR
LOOKBACK_SESSIONS = 60       # measured: ~95% of gaps touched within 60 sessions
MIN_TOUCHES = 2              # operator: "hit 2-3 times"
REVERSAL_TR_MULT = 1.0       # a touch counts only if price reverses >= 1x TR
RETEST_MAX_BARS = 10         # operator: "retracement of 5-10 candles"
ROUND_NUMBER_STEP = 100.0    # psychological levels
TIMEFRAMES = ("5min", "15min", "60min", "1D")


class LookaheadError(AssertionError):
    """Raised when a window contains a bar at or after the decision time."""


def assert_causal(window: pd.DataFrame, decision_ts: pd.Timestamp, what: str) -> None:
    """Refuse any window that includes the decision bar or anything after it.

    Called on EVERY window this module uses. The cost of the check is nothing;
    the cost of not having it is a screen that reports an artifact as an edge.
    """
    if window.empty:
        return
    last = window.index[-1] if isinstance(window.index, pd.DatetimeIndex) \
        else window["ts"].iloc[-1]
    if last >= decision_ts:
        raise LookaheadError(
            f"{what}: window ends {last} but the decision is made at "
            f"{decision_ts}. A level built from a bar the trade could not have "
            f"seen is the rvol_ratio error in different clothes.")


# ── bars ─────────────────────────────────────────────────────────────────────
def resample(bars: pd.DataFrame, tf: str) -> pd.DataFrame:
    """1-min OHLC -> timeframe OHLC. `bars` must be indexed by timestamp."""
    o = bars.resample(tf).agg(open=("open", "first"), high=("high", "max"),
                              low=("low", "min"), close=("close", "last"))
    return o.dropna()


def true_range(df: pd.DataFrame) -> pd.Series:
    prev = df["close"].shift()
    return np.maximum(df["high"] - df["low"],
                      np.maximum((df["high"] - prev).abs(),
                                 (df["low"] - prev).abs()))


def tolerance_for(df: pd.DataFrame) -> float:
    """0.26 x median true range of this timeframe. Preregistration §3.1."""
    tr = true_range(df).dropna()
    return float(TOLERANCE_TR_MULT * tr.median()) if len(tr) else float("nan")


# ── candlestick patterns (standard OHLC definitions, preregistration §3.2.3) ──
def _body(o, c):
    return abs(c - o)


def _range(h, l):
    return max(h - l, 1e-9)


def candlestick(o: float, h: float, l: float, c: float,
                po: Optional[float] = None, pc: Optional[float] = None) -> List[str]:
    """Reversal patterns present on this bar. `po`/`pc` are the PRIOR bar's
    open/close, needed for the two-bar patterns (harami, engulfing)."""
    out: List[str] = []
    rng, body = _range(h, l), _body(o, c)
    upper, lower = h - max(o, c), min(o, c) - l

    if body <= 0.10 * rng:
        out.append("doji")
    # hammer / inverted hammer: small body, one long shadow >= 2x body
    if body > 0 and body <= 0.35 * rng:
        if lower >= 2.0 * body and upper <= 0.30 * rng:
            out.append("hammer")
        if upper >= 2.0 * body and lower <= 0.30 * rng:
            out.append("inverted_hammer")

    if po is not None and pc is not None:
        pbody_lo, pbody_hi = min(po, pc), max(po, pc)
        body_lo, body_hi = min(o, c), max(o, c)
        prior_up = pc > po
        # harami: this body contained inside the prior body, opposite colour
        if body_lo >= pbody_lo and body_hi <= pbody_hi and _body(po, pc) > 0:
            if prior_up and c < o:
                out.append("bearish_harami")
            elif not prior_up and c > o:
                out.append("bullish_harami")
        # engulfing: this body contains the prior body, opposite colour
        if body_lo <= pbody_lo and body_hi >= pbody_hi and body > 0:
            if prior_up and c < o:
                out.append("bearish_engulfing")
            elif not prior_up and c > o:
                out.append("bullish_engulfing")
    return out


# Shape alone does NOT carry direction, and pretending it does was an error in
# the first draft of this module. A hammer at a low is bullish; the identical
# shape at a high is a hanging man and is bearish. An inverted hammer at a low is
# bullish; the same shape at a high is a shooting star. So the single-bar shapes
# are direction-NEUTRAL reversal signals and the direction comes from which kind
# of level they occur at -- which the symmetric rule already supplies for free.
# The two-bar patterns do carry a colour, because the engulf/contain relationship
# is defined against the prior bar's direction.
NEUTRAL_REVERSAL = {"doji", "hammer", "inverted_hammer"}
BULLISH = {"bullish_harami", "bullish_engulfing"}
BEARISH = {"bearish_harami", "bearish_engulfing"}


def confirms(patterns: Sequence[str], side: str) -> bool:
    """Does this bar's pattern set confirm a reversal in `side` ('long'/'short')?

    Neutral shapes confirm either side; coloured two-bar patterns confirm only
    their own. `side` is set by the level (long at support, short at resistance),
    so context supplies the direction exactly as a chart reader would.
    """
    s = set(patterns)
    if s & NEUTRAL_REVERSAL:
        return True
    return bool(s & (BULLISH if side == "long" else BEARISH))


# ── swing structure ──────────────────────────────────────────────────────────
def swings(df: pd.DataFrame, tr: float) -> List[Tuple[pd.Timestamp, float, str]]:
    """Alternating swing highs/lows via a 1x-TR zigzag.

    A fixed fractal width would be another free parameter; scaling the reversal
    threshold by the same true range that sets tolerance keeps the whole module
    on one constant.
    """
    if df.empty or not np.isfinite(tr) or tr <= 0:
        return []
    piv: List[Tuple[pd.Timestamp, float, str]] = []
    lo_t, lo_v = df.index[0], float(df["low"].iloc[0])
    hi_t, hi_v = df.index[0], float(df["high"].iloc[0])
    direction = 0
    for t, row in df.iterrows():
        h, l = float(row["high"]), float(row["low"])
        if h > hi_v:
            hi_t, hi_v = t, h
        if l < lo_v:
            lo_t, lo_v = t, l
        if direction >= 0 and h - lo_v >= tr and lo_v < hi_v:
            if direction == 0 or piv[-1][2] != "L":
                piv.append((lo_t, lo_v, "L"))
            direction = 1
            hi_t, hi_v = t, h
        elif direction <= 0 and hi_v - l >= tr:
            if direction == 0 or piv[-1][2] != "H":
                piv.append((hi_t, hi_v, "H"))
            direction = -1
            lo_t, lo_v = t, l
    return piv


def structure(piv: Sequence[Tuple[pd.Timestamp, float, str]]) -> Optional[str]:
    """'LH-LL' (downtrend), 'HH-HL' (uptrend), or None from the last 4 pivots."""
    if len(piv) < 4:
        return None
    highs = [p for p in piv if p[2] == "H"][-2:]
    lows = [p for p in piv if p[2] == "L"][-2:]
    if len(highs) < 2 or len(lows) < 2:
        return None
    lh, ll = highs[1][1] < highs[0][1], lows[1][1] < lows[0][1]
    hh, hl = highs[1][1] > highs[0][1], lows[1][1] > lows[0][1]
    if lh and ll:
        return "LH-LL"
    if hh and hl:
        return "HH-HL"
    return None


# ── levels ───────────────────────────────────────────────────────────────────
@dataclass(frozen=True)
class Level:
    price: float
    kind: str          # 'touched' | 'gap' | 'round'
    timeframe: str
    touches: int


def touched_levels(df: pd.DataFrame, tol: float, tr: float) -> List[Level]:
    """Prices the market reversed away from at least MIN_TOUCHES times.

    A touch is a swing pivot; it counts only if price then moved >= 1x TR away,
    which is what `swings` already enforces by construction.
    """
    piv = swings(df, tr * REVERSAL_TR_MULT)
    out: List[Level] = []
    used = [False] * len(piv)
    for i, (_, v, k) in enumerate(piv):
        if used[i]:
            continue
        group = [j for j in range(i, len(piv))
                 if not used[j] and piv[j][2] == k and abs(piv[j][1] - v) <= tol]
        if len(group) >= MIN_TOUCHES:
            for j in group:
                used[j] = True
            out.append(Level(price=float(np.mean([piv[j][1] for j in group])),
                             kind="touched", timeframe="", touches=len(group)))
    return out


def gap_levels(daily: pd.DataFrame, spot: float) -> List[Level]:
    """Edges of gaps still unfilled as of the last bar in `daily`."""
    out: List[Level] = []
    h, l = daily["high"].values, daily["low"].values
    for i in range(1, len(daily)):
        if l[i] > h[i - 1]:
            lo, hi, up = h[i - 1], l[i], True     # jumped UP from lo to hi
        elif h[i] < l[i - 1]:
            lo, hi, up = h[i], l[i - 1], False    # jumped DOWN from hi to lo
        else:
            continue
        # "Traded through" depends on WHICH WAY the gap opened. A gap up is
        # closed by price coming back DOWN through it; price rallying further
        # away does not touch it. Treating any later extreme as a fill (the
        # first draft) deleted live gaps in trending markets -- which is
        # precisely when an unfilled gap is the level worth having.
        if up:
            after = l[i + 1:].min() if i + 1 < len(daily) else np.inf
            if after <= lo:
                continue
        else:
            after = h[i + 1:].max() if i + 1 < len(daily) else -np.inf
            if after >= hi:
                continue
        out.append(Level(price=float(hi), kind="gap", timeframe="1D", touches=0))
        out.append(Level(price=float(lo), kind="gap", timeframe="1D", touches=0))
    return out


def round_levels(spot: float, tol: float) -> List[Level]:
    base = round(spot / ROUND_NUMBER_STEP) * ROUND_NUMBER_STEP
    return [Level(price=float(base + k * ROUND_NUMBER_STEP), kind="round",
                  timeframe="", touches=0) for k in (-1, 0, 1)]


def build_levels(bars_prior: pd.DataFrame, decision_ts: pd.Timestamp,
                 spot: float) -> List[Level]:
    """All levels visible STRICTLY BEFORE `decision_ts`. Preregistration §3.1."""
    assert_causal(bars_prior, decision_ts, "build_levels")
    levels: List[Level] = []
    for tf in TIMEFRAMES:
        df = resample(bars_prior, tf)
        if len(df) < 10:
            continue
        tr = float(true_range(df).dropna().median())
        tol = TOLERANCE_TR_MULT * tr
        if not np.isfinite(tol) or tol <= 0:
            continue
        for lv in touched_levels(df, tol, tr):
            levels.append(Level(lv.price, lv.kind, tf, lv.touches))
        if tf == "1D":
            levels.extend(gap_levels(df, spot))
    daily = resample(bars_prior, "1D")
    if len(daily):
        tr_d = float(true_range(daily).dropna().median())
        levels.extend(round_levels(spot, TOLERANCE_TR_MULT * tr_d))
    return levels
