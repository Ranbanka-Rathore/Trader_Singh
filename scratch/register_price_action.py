"""
Reopen `intraday_index` under Amendment F, and register `pa-levels-modern`
BEFORE the screen that tests it exists.

This is run and COMMITTED before scratch/pa_levels.py is executed even once, for
the same reason scratch/register_arena5.py was: the only thing that stops a
negative result being re-read later as "price action works" is that the claim and
the kill criterion were fixed in a commit made before the number existed.

It matters more here than it did for Arena 5. A price-action family has far more
researcher degrees of freedom than four signals x four horizons -- level
tolerance, lookback, touch counts, which candlestick patterns, how many
confluences -- and `intraday-ceiling-modern` already measured what happens when
degrees of freedom go unmanaged: 26 features fitted in-sample with no hold-out
bought +0.0002 IC over one hand-picked signal. So every parameter is pinned in
research/PREREGISTRATION-price-action.md, and this script copies the claim and
kill criterion from that document verbatim.

Usage:  ./venv/Scripts/python.exe scratch/register_price_action.py
"""
import os
import sys

sys.path.insert(0, os.getcwd())

from research import registry  # noqa: E402

HID = "pa-levels-modern"
ARENA = "intraday_index"

# Pre-registration §5. Two confluence thresholds; direction is tested jointly,
# not as a separate axis, so a long/short sign flip is a failure not a finding.
N_CONFIGS = 2

CLAIM = (
    "On NIFTY 1-minute index bars, entries taken at a pre-identified horizontal "
    "level -- confirmed by at least 2 independent confluence signals and entered "
    "only on a RETEST rather than first touch -- produce a gross edge of >= 8.0 "
    "index points per round trip, with the same sign in every calendar year "
    "having >= 60 trading days of data. Levels are built from: price touched "
    "within 0.26x the median true range of its timeframe at least twice, each "
    "touch reversing away by >= 1x that true range, over a 60-session lookback, "
    "on 5-min / 15-min / 1-hour / daily; plus unfilled gap edges; plus round "
    "numbers. Confluence signals are: level touched >=2 times; approach "
    "structure aligned (LH-LL into support, HH-HL into resistance); a "
    "candlestick reversal at the level on the 5-min (hammer, inverted hammer, "
    "doji, bullish harami, bearish harami, engulfing); level coincides with an "
    "unfilled gap edge; level coincides with a round number; price on the "
    "favourable side of session VWAP; PCR at a same-day extreme outside its "
    "trailing 20-session 10th/90th percentile. Entry requires touch, then a move "
    "away of >= 1x TR(5-min), then a return within tolerance inside 10 five-"
    "minute bars. Exit is the first of: next opposing level within tolerance, "
    "adverse excursion of 1x TR(5-min) beyond the level, or session close -- no "
    "overnight holds. Direction is NOT predicted: the rule is symmetric and long-"
    "at-support and short-at-resistance are tested together. Every parameter "
    "above is fixed in advance from either measurement (the 60-session lookback "
    "is measured from 300 gaps over 1,972 sessions 2016-2023, where ~95% of gaps "
    "are touched within 60) or from the operator's statement made on 2026-08-18 "
    "before any result of this family existed (0.26 = 50 points on the daily "
    "divided by NIFTY's 195.3-point median daily true range; >=2 confluences "
    "from 'if there is 2-3 indications I take the trade'; the retest rule from "
    "'there is a retracement of 5-10 candles, then the actual reversal')."
)

KILL = (
    "KILLED unless all three hold together: (a) gross edge >= 8.0 index points "
    "per round trip pooled over 2022-08-16..2026-08-14, (b) the same sign in "
    "every calendar year with >= 60 trading days of data, and (c) |t| on the "
    "per-trade edge clears the Section 4 bar for the number of configurations "
    "actually run, after correction for overlapping forward windows. Clearing "
    "(a) but failing (b) is a sign-unstable estimate and is NOT a finding "
    "(Amendment D5). Clearing (a) and (b) but failing (c) is recorded as real "
    "but unproven and is NOT carried forward. Per Amendment B5 this screen "
    "cannot promote anything; clearing the bar earns a Section 5 walk-forward "
    "test and nothing more. On failure, intraday_index returns to CLOSED and "
    "Amendment F's condition-2 reopening is SPENT -- a second price-action "
    "variant does not reopen it, and the hypothesis is closed, not tuned."
)

JUSTIFICATION = (
    "Amendment F, condition 2: a signal family genuinely unlike those tested. "
    "The tested family recorded in this arena's own closure is multi-lag "
    "momentum, VWAP deviation, opening-range position, realised-vol state, "
    "range position, acceleration and time-of-day. Horizontal support/resistance "
    "levels, swing structure (LH-LL / HH-HL), candlestick reversal patterns and "
    "gap edges appear nowhere on that list. VWAP is on it, and is therefore "
    "admitted here only as one confluence input among seven, never as the "
    "signal. Condition 1 was tested on 2026-08-18 and FAILED (live all-in cost "
    "2.73 index points, but only in a premium bucket costing 65% of account "
    "equity; every affordable bucket is >= 3.67), which is the template: a "
    "reopening needs the whole condition, not the threshold."
)


def main() -> int:
    if registry.arena_closure(ARENA):
        rec = registry.reopen_arena(
            ARENA, amendment="F", condition="2",
            justification=JUSTIFICATION, spent_on=HID)
        print(f"REOPENED {ARENA} under Amendment F, condition 2")
        print(f"  closure preserved in arena_history "
              f"(closed {rec['closed_at'][:10]}, reopened {rec['reopened_at'][:10]})")
    else:
        print(f"{ARENA} is already open — nothing to reopen")

    if any(h["id"] == HID for h in registry.load()["hypotheses"]):
        print(f"{HID} is already registered; ids are permanent. Nothing written.")
        return 0

    h = registry.register(
        hid=HID,
        arena=ARENA,
        claim=CLAIM,
        kill_criterion=KILL,
        window=["2022-08-16", "2026-08-14"],
        gate="strict",
        n_configs=N_CONFIGS,
        underlying="NIFTY",
        equity=50_000.0,
        engine="pa_levels",
        note="Amendment F. Parameters pinned in "
             "research/PREREGISTRATION-price-action.md and may not be tuned. "
             "Registered before scratch/pa_levels.py existed.",
    )
    print(f"\nREGISTERED {h['id']} in {h['arena']}")
    print(f"  window   {h['window'][0]} .. {h['window'][1]}")
    print(f"  configs  {h['n_configs']}  (Section 4 bar at N=2: |t| >= ~1.18)")
    print(f"  status   {h.get('status')}")
    print("\nThe bar is 8.0 index points GROSS, sign-stable across years.")
    print("Pre-committed expectation (preregistration §7): most likely a kill on")
    print("(a) -- the best edge Phase 2 measured anywhere was 5.39 points.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
