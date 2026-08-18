"""
Record the kill of `pa-levels-modern` and re-close `intraday_index`.

Amendment F4: the condition-2 reopening was granted ONCE, spent on this
hypothesis. It is killed, so the arena returns to closed and no second
price-action variant reopens it.

The two confluence arms are recorded SEPARATELY because they establish different
things, and collapsing them into one "KILLED" would overstate the result:

  >=2  n=234  gross -1.845  95% CI [-6.00, +2.31]   8.0 EXCLUDED (4.64 SE away)
  >=3  n= 51  gross +0.663  95% CI [-8.85, +10.18]  8.0 INSIDE  (1.51 SE away)

Usage:  ./venv/Scripts/python.exe scratch/close_price_action.py
"""
import json
import os
import sys

sys.path.insert(0, os.getcwd())

from research import registry  # noqa: E402

HID = "pa-levels-modern"
ARENA = "intraday_index"

DETAIL = {
    "window": "2022-08-16..2026-08-14",
    "sessions": 992,
    "bar_index_points": 8.0,
    "runs": {
        "run1": "DID NOT TEST THE HYPOTHESIS — retest clock measured from the "
                "touch instead of the completed move away, yielding 0.20 retests "
                "per session. Corrected in preregistration §3B before run 2. "
                "Kept at scratch/pa_levels_results_run1.json as context only.",
        "run2": "the test",
    },
    "confluence_ge_2": {
        "n": 234, "gross_pts": -1.845, "t": -0.87,
        "ci95": [-6.00, 2.31], "bar_excluded": True,
        "se_from_bar": 4.64,
        "per_year": {"2022": 20.684, "2023": -1.012, "2024": 1.588,
                     "2025": -4.485, "2026": -5.996},
        "verdict": "MEASURED ABSENCE — 8.0 is excluded by the data",
    },
    "confluence_ge_3": {
        "n": 51, "gross_pts": 0.663, "t": 0.14,
        "ci95": [-8.85, 10.18], "bar_excluded": False,
        "se_from_bar": 1.51,
        "trades_needed_for_power": 76, "trades_per_year": 13,
        "years_needed": 6.0,
        "per_year": {"2022": -20.049, "2023": 7.850, "2024": -6.302,
                     "2025": 4.858, "2026": 6.507},
        "sign_stability_pass_is_degenerate": (
            "only 2024 clears the 20-trade eligibility floor, so 'every eligible "
            "year has the same sign' is trivially true with one eligible year. "
            "The raw per-year means (-20.0/+7.9/-6.3/+4.9/+6.5) are visibly "
            "unstable. This PASS is not evidence."),
        "verdict": "ABSENCE OF EVIDENCE — too selective to test in 4 years",
    },
    "funnel": {"levels_per_session": 9.2, "touches_per_session": 2.2,
               "retests_per_session_run1": 0.20},
}

GROUNDS = (
    "KILLED on criterion (a). The registered bar was a gross edge >= 8.0 index "
    "points per round trip, sign-stable across years. Neither confluence arm "
    "came close: >=2 gave -1.845 points over 234 trades, >=3 gave +0.663 over 51.\n\n"
    "The two arms are NOT the same result and are recorded separately.\n\n"
    "At confluence >=2 this is a MEASURED ABSENCE. With 234 trades the 95% "
    "interval is [-6.00, +2.31] and 8.0 sits 4.64 standard errors away. The bar "
    "is excluded by the data, not merely unmet.\n\n"
    "At confluence >=3 it is an ABSENCE OF EVIDENCE, and saying otherwise would "
    "overstate it. 51 trades give a 95% interval of [-8.85, +10.18], which "
    "CONTAINS 8.0. Detecting an 8-point effect against the measured 34.7-point "
    "per-trade standard deviation needs ~76 trades; the rule generates ~13 a "
    "year, so it needs ~6 years and the window holds 4. Its (b) PASS is "
    "degenerate — only 2024 clears the 20-trade floor, so sign-consistency is "
    "trivially satisfied by a single eligible year while the raw per-year means "
    "(-20.0/+7.9/-6.3/+4.9/+6.5) are visibly unstable.\n\n"
    "What this does and does not say. It does NOT say chart-reading does not "
    "work, and it does not evaluate the operator's discretionary judgement — "
    "levels here are built from strictly prior SESSIONS, which is stricter than "
    "a human who watches the session form, and the excluded patterns (wedges, "
    "H&S, cup-and-handle, double tops) were never tested. What it does say is "
    "that this mechanical encoding of levels + swing structure + candlestick "
    "confirmation + retest, at >=2 confluences, has no edge at the 8-point scale "
    "the cost floor demands; and that at >=3 it is too selective to be tested "
    "with four years of NIFTY data."
)

REOPEN = (
    "NOTHING. Amendment F4 granted the condition-2 reopening ONCE and it is now "
    "spent: a second price-action variant does not reopen this arena, and "
    "Section 7 forbids tuning a hypothesis that failed. Condition 1 (an all-in "
    "cost below ~3.0 index points for an instrument this account can hold) was "
    "tested on 2026-08-18 and failed on its affordability clause. Condition 3 (a "
    "different holding regime — overnight or multi-day) remains untouched and "
    "would be a NEW arena, not a reopening.\n\n"
    "The one honest loose end is recorded rather than used as a door: the "
    ">=3-confluence arm was never decisively tested, and would need ~6 years of "
    "data at its natural trade rate. That is a reason the question is OPEN, not "
    "a reason to re-run it now — and re-running it in 2028 would still need a "
    "fresh registration, not this one."
)


def main() -> int:
    registry.add_event(
        HID, stage="screen", verdict="killed", detail=DETAIL, status="killed")
    print(f"KILLED {HID}")

    if registry.arena_closure(ARENA):
        print(f"{ARENA} already closed")
    else:
        registry.close_arena(
            ARENA, grounds=GROUNDS, reopen_requires=REOPEN,
            evidence=["scratch/pa_levels.py",
                      "scratch/pa_levels_results.json",
                      "scratch/pa_levels_results_run1.json",
                      "research/PREREGISTRATION-price-action.md",
                      "RESEARCH_CHARTER.md Amendment F"])
        print(f"RE-CLOSED {ARENA} — Amendment F's condition-2 reopening is spent")

    hist = registry.arena_history(ARENA)
    print(f"\narena_history holds {len(hist)} prior closure(s); the original "
          f"2026-08-14 kill and its grounds are preserved.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
