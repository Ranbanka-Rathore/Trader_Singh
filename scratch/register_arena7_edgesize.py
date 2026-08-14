"""
Register the edge-SIZE screen before it runs.

The question the operator asked: does ANY intraday edge reach ~8 index points?
That number is not arbitrary — it is Arena 5's own closure condition #2, the
gross edge that would justify a charter amendment reopening it. So this screen
tests a pre-authorised reopening condition rather than sneaking around a closure.

It registers in arena 'intraday_session' (open), not 'intraday_index' (closed on
2026-08-14). That is the honest home: the lever being tested is conditioning on
REGIME AND SESSION STRUCTURE — volatility state, time of day, days to expiry —
rather than on a better directional signal, which is precisely what Arena 5's
closure said would NOT qualify.

WHY THIS IS A WALK-FORWARD AND NOT ANOTHER IN-SAMPLE CEILING
------------------------------------------------------------
intraday-ceiling-modern already showed that an overfitted in-sample bound clears
any bar you set and tells you nothing — it reached 80 points at extreme
thresholds purely by concentrating its own noise. Repeating that would waste a
registration. So this fits on the PAST and measures on the FUTURE: train on
everything before test year Y, evaluate on Y, for Y in 2023..2026. A positive
result here would therefore mean something, and a negative one closes the
question rather than merely failing to open it.

WHAT THE BOUND ACTUALLY HAS TO CLOSE
------------------------------------
Perfect foresight — knowing the sign of every move — is worth 26.3 points at
h=30, 36.8 at h=60 and 52.1 at h=120. So the raw material is ample and the
binding constraint is skill, not volatility: 8 points at h=60 means capturing
~22% of the average absolute move, an IC-equivalent near 0.18 against a measured
~0.05. That is the gap.

Usage:  ./venv/Scripts/python.exe scratch/register_arena7_edgesize.py
"""
import os
import sys

sys.path.insert(0, os.getcwd())

from research import charter, registry  # noqa: E402

HID = "intraday-edgesize-modern"

# 2 horizons x 3 volatility regimes x 3 thresholds
N_CONFIGS = 18

CLAIM = (
    "Some combination of horizon (60 or 120 min), past-only trailing-volatility regime "
    "(low/mid/high tercile) and signal-strength threshold (|z| >= 1.0, 1.5, 2.0) yields "
    "an OUT-OF-SAMPLE gross edge of at least 8.0 index points per non-overlapping round "
    "trip, using a multivariate model trained only on data preceding each test year. "
    "8.0 points is Arena 5's registered reopening threshold: it is the level at which a "
    "gross edge clears the measured 7.71-point all-in cost floor with margin to spare. "
    "The mechanism under test is REGIME conditioning, not signal improvement — the same "
    "predictive skill applied where the forward distribution is widest should scale the "
    "edge in points even at unchanged IC, because edge = IC x sd and sd varies several-"
    "fold across volatility regimes."
)

KILL = (
    "KILLED unless at least one (horizon, vol regime, threshold) cell satisfies ALL of: "
    "(a) mean OUT-OF-SAMPLE gross edge >= 8.0 index points per non-overlapping trade, "
    "pooled across test years 2023-2026; "
    "(b) mean NET edge > 0 after the measured Amendment E9 cost floor for the cheapest "
    "instrument at that horizon; "
    "(c) net edge > 0 in EVERY test year, not merely pooled — D5's sign-consistency "
    "applied out-of-sample; "
    "(d) pooled |t| >= 2.41, the Section 4 bar at N=18; "
    "(e) >= 30 trades per year, so Section 3's detectability is satisfiable. "
    "If NOTHING clears, the answer to 'does any intraday edge reach 8 points' is NO for "
    "this feature family under regime conditioning, Arena 5's reopening condition #2 is "
    "demonstrably unmet, and arena 'intraday_session' is recommended for closure "
    "alongside it. Per Amendment B5 clearing means 'worth a walk-forward at strategy "
    "level', never 'works'."
)

NOTE = (
    "Prompted by the operator's question after arena intraday_index was closed on "
    "2026-08-14. Tests that arena's own reopening condition #2 (a gross edge >= 8 index "
    "points from a genuinely different family) via the one lever its three screens did "
    "NOT test: conditioning on the volatility REGIME rather than on the signal. "
    "Registered in intraday_session because that is the open arena whose definition "
    "covers session and regime structure; it is not a backdoor registration into the "
    "closed arena, and if it clears, reopening intraday_index would still require the "
    "charter amendment its closure record demands. "
    "Runs on data/intraday/NIFTY/index.parquet (370,626 1-min bars, 992 days). "
    "Walk-forward by calendar year: train on all prior data, test on the year. "
    "Costs are the measured Amendment E9 figures (options 4.01 + 5.45/hr theta at 4 "
    "DTE; futures 7.71 all-in), cheaper instrument per horizon. "
    "All conditioning variables must be past-only; volatility terciles are cut on "
    "TRAILING realised vol using thresholds fixed from the training period only, never "
    "from the test year. Only forward windows closing inside the same session count."
)


def main():
    if registry.get(HID):
        h = registry.get(HID)
        print(f"'{HID}' already registered at {h['registered_at']} "
              f"(status {h['status']}). Not re-registering.")
        return

    h = registry.register(
        hid=HID,
        arena="intraday_session",
        claim=CLAIM,
        kill_criterion=KILL,
        window=["2022-08-16", "2026-08-14"],
        gate="strict",
        n_configs=N_CONFIGS,
        config={
            "bar_index_points": 8.0,
            "bar_source": "arena intraday_index closure, reopening condition #2",
            "method": "walk-forward by calendar year; train on all prior, test on year",
            "test_years": [2023, 2024, 2025, 2026],
            "horizons_min": [60, 120],
            "vol_regimes": "terciles of trailing 30-bar realised vol, cut on TRAIN only",
            "thresholds_z": [1.0, 1.5, 2.0],
            "mechanism": "regime conditioning (edge = IC x sd; sd varies by regime)",
            "perfect_foresight_bound_pts": {"30": 26.34, "60": 36.84, "120": 52.12},
            "cost_index_points": {"option_txn": 4.01,
                                  "option_theta_per_hour_4dte": 5.45,
                                  "future_all_in": 7.71},
            "source": "data/intraday/NIFTY/index.parquet",
        },
        underlying="NIFTY",
        equity=min(charter.TRADING_CAPITAL_RANGE_RS),
        engine="intraday_edgesize",
        note=NOTE,
    )
    print(f"registered '{h['id']}' in arena '{h['arena']}'")
    print(f"  n_configs {h['n_configs']} -> Section 4 bar "
          f"|t| >= {charter.noise_threshold(N_CONFIGS):.2f}")
    print(f"  bar: 8.0 index points OUT-OF-SAMPLE gross")
    print(f"  fingerprint {h['fingerprint'][:16]}")
    print("\nCOMMIT BEFORE RUNNING.")


if __name__ == "__main__":
    main()
