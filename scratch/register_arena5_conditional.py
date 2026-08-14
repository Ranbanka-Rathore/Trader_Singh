"""
Register Arena 5's conditional screen BEFORE it runs.

The last cheap question in this arena. Screen 1 (intraday-ic-modern) tested
UNCONDITIONAL signals and said so explicitly in its registration; the ceiling
screen (intraday-ceiling-modern) then showed that 25 extra features plus
unrestricted overfitting add +0.0002 IC over one hand-picked signal, so there is
no point hunting for better features. What is left is whether taking only the
STRONGEST readings of the signals we already have produces an edge that clears
the measured cost floor.

WHY THIS ONE CANNOT OVERFIT
---------------------------
The signals are the exact fixed formulas from screen 1. Nothing is fitted: no
coefficients, no weights, no feature selection. The only free parameter is the
threshold, and it is swept over a declared grid and charged to the
multiple-comparisons budget. The standardisation used to define the threshold is
EXPANDING (past-only), so a live system could compute the same number at the same
instant.

DIRECTION IS PREDICTED, NOT FITTED
----------------------------------
Screen 1 measured positive IC for both signals: a high reading precedes a higher
forward return. So this registers MOMENTUM — long when z is high, short when z is
low. If the conditional edge comes out negative, that is a failure, not a
discovery of a reversal effect. Declaring the sign in advance is the only reason
the result can be read at all; it is what made Phase 1's carry test trustworthy.

Usage:  ./venv/Scripts/python.exe scratch/register_arena5_conditional.py
"""
import os
import sys

sys.path.insert(0, os.getcwd())

from research import charter, registry  # noqa: E402

HID = "intraday-conditional-modern"

# 2 signals x 2 horizons x 4 thresholds
N_CONFIGS = 16

CLAIM = (
    "Taking only the strongest readings of screen 1's two surviving unfitted signals "
    "(vwap_dev and or_pos) produces a per-trade edge that clears the measured cost "
    "floor. Specifically: for some (signal, horizon in {60,120}, threshold in "
    "{1.0,1.5,2.0,2.5}) cell, going LONG when the past-only standardised signal z >= "
    "+threshold and SHORT when z <= -threshold — MOMENTUM, the direction screen 1 "
    "measured — yields a mean net edge per trade, after the Amendment E9 cost of the "
    "cheapest instrument at that horizon, that is positive in every calendar year, "
    "significant against the Section 4 bar for N=16, frequent enough to be validated, "
    "and large enough to beat a 7% fixed deposit on the capital required to run it. "
    "Nothing is fitted: the signals are screen 1's fixed formulas, the standardisation "
    "is expanding and past-only, and the threshold is the sole free parameter."
)

KILL = (
    "KILLED unless at least one cell satisfies ALL FOUR: "
    "(a) mean net edge per trade > 0 in EVERY calendar year holding >= 60 trading days "
    "— Amendment D5's sign-consistency rule applied to the NET figure, not the IC, so "
    "a signal that only works in one regime cannot pass; "
    "(b) pooled net edge > 0 with |t| >= 2.35, the Section 4 bar at N=16; "
    "(c) >= 30 trades per year, so Section 3's detectability rule is satisfiable inside "
    "the 2027-01-07 stop date; "
    "(d) implied annual net return > 7% of the capital required to run it — Section 1's "
    "benchmark — counting Rs 7,609 per ATM option lot or ~Rs 1,90,000 of futures margin "
    "as appropriate to the instrument the cost was taken from. "
    "A cell clearing (a)-(c) but failing (d) is real but not worth doing, and is "
    "recorded as such rather than carried forward. "
    "If NOTHING clears, arena 'intraday_index' is recommended for closure: screen 1 "
    "killed the unconditional case, the ceiling screen showed the feature space is "
    "exhausted, and this closes the conditional case. Per Amendment B5 this screen "
    "cannot promote — clearing means 'worth a walk-forward', never 'works'."
)

NOTE = (
    "Arena 5, screen 3 and the last cheap question in it. Prompted by "
    "intraday-ic-modern (unconditional, killed on economics: 2.995 index points against "
    "a 4.01-7.71 point floor) and intraday-ceiling-modern (not closed, but showed 26 "
    "overfitted features add +0.0002 IC over one signal). "
    "Runs on data/intraday/NIFTY/index.parquet, 370,626 1-min bars, 992 trading days. "
    "Costs are the MEASURED ones from Amendment E9 rather than the engine default: "
    "options 4.01 points transaction + 5.45 pts/hr theta at 4 DTE, futures 7.71 points "
    "all-in with no theta; the cheaper at each horizon is used, which favours the claim. "
    "gate='strict' recorded for form; this executes no fills. "
    "Standardisation MUST be expanding/past-only — screen 1 was nearly derailed by a "
    "whole-session median normaliser, and the same trap applies to the z used here. "
    "Only bars whose forward window closes inside the same session are counted; no "
    "overnight carry, which is a different strategy class."
)


def main():
    if registry.get(HID):
        h = registry.get(HID)
        print(f"'{HID}' already registered at {h['registered_at']} "
              f"(status {h['status']}). Not re-registering.")
        return

    h = registry.register(
        hid=HID,
        arena="intraday_index",
        claim=CLAIM,
        kill_criterion=KILL,
        window=["2022-08-16", "2026-08-14"],
        gate="strict",
        n_configs=N_CONFIGS,
        config={
            "signals": {
                "vwap_dev": "(price - expanding session VWAP) / rolling 30-bar sd of price",
                "or_pos": "(price - OR_low)/(OR_high - OR_low), OR = first 15 min",
            },
            "direction": "MOMENTUM — long when z >= +thr, short when z <= -thr",
            "horizons_min": [60, 120],
            "thresholds_z": [1.0, 1.5, 2.0, 2.5],
            "standardisation": "expanding, past-only, min 20 sessions warmup",
            "fitting": "NONE — no coefficients, no weights, no feature selection",
            "cost_index_points": {"option_txn": 4.01,
                                  "option_theta_per_hour_4dte": 5.45,
                                  "future_all_in": 7.71,
                                  "source": "Amendment E9"},
            "source": "data/intraday/NIFTY/index.parquet",
        },
        underlying="NIFTY",
        equity=min(charter.TRADING_CAPITAL_RANGE_RS),
        engine="intraday_conditional",
        note=NOTE,
    )
    print(f"registered '{h['id']}' in arena '{h['arena']}'")
    print(f"  n_configs {h['n_configs']} -> Section 4 bar "
          f"|t| >= {charter.noise_threshold(N_CONFIGS):.2f}")
    print(f"  direction MOMENTUM, declared in advance")
    print(f"  fingerprint {h['fingerprint'][:16]}")
    print("\nCOMMIT THIS BEFORE RUNNING THE SCREEN.")


if __name__ == "__main__":
    main()
