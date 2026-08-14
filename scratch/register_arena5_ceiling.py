"""
Register Arena 5's CEILING screen before it runs.

Precedent: Phase 1's single most efficient move was measuring a ceiling before
building anything under it (`scratch/arena3_ceiling.py` — "a 21-day-hold book
needs IC ~= 0.05 to reach Sharpe 1.0, and tsmom's measured IC is ~0.000"). That
closed an arena in an afternoon instead of a month.

WHAT MAKES THIS A CEILING AND NOT ANOTHER SCREEN
------------------------------------------------
Screen 1 tested four hand-picked signals and found the best at IC 0.0537, worth
2.995 index points — against a measured cost floor of 4.01 (options, before
theta) to 7.71 (futures). The obvious objection is "you picked the wrong four
signals". This screen removes that objection by construction:

  * a RICH feature set (20+ intraday features), not four
  * combined by an IN-SAMPLE fit, deliberately overfitted, with no hold-out
  * evaluated at the BEST horizon and the BEST conditioning threshold
  * costed at the CHEAPEST instrument available at each horizon

Every one of those choices is biased in FAVOUR of the hypothesis. The resulting
edge is therefore an UPPER BOUND that no honest out-of-sample strategy can
exceed. That asymmetry is the point:

  >> THIS SCREEN CAN ONLY CLOSE THE ARENA, NEVER OPEN IT. <<

If the overfit bound fails to clear the cost floor, nothing will, and the arena
closes on a measured ceiling. If it clears, that proves nothing whatever — an
in-sample fit clearing a bar is the least surprising result in statistics — and
the arena merely stays open. Amendment B5 ("screens cannot promote") is not a
constraint here so much as a description.

Usage:  ./venv/Scripts/python.exe scratch/register_arena5_ceiling.py
"""
import os
import sys

sys.path.insert(0, os.getcwd())

from research import charter, registry  # noqa: E402

HID = "intraday-ceiling-modern"

# The count is declared for the record. Section 4's noise bar is not the binding
# test here — an in-sample fit will clear any |t| bar trivially, which is exactly
# why the kill criterion below is ECONOMIC rather than statistical.
N_CONFIGS = 6  # horizons; features and thresholds are swept inside each

HORIZONS = [15, 30, 60, 120, 240, 375]

CLAIM = (
    "An IN-SAMPLE optimal linear combination of a rich intraday feature set (20+ "
    "features: multi-lag momentum, VWAP deviation, opening-range position, realised-vol "
    "state, range position, acceleration, time-of-day) achieves, at its best horizon "
    "and best signal-strength threshold, a per-trade edge in INDEX POINTS exceeding the "
    "cheapest measured round-trip cost at that horizon. Costs are the measured ones from "
    "Amendment E9: NIFTY options 4.01 points transaction plus theta of 5.45 pts/hr at 4 "
    "DTE, or NIFTY futures 7.71 points with no theta; the cheaper of the two is used at "
    "each horizon, which favours the claim. Because the fit is in-sample and overfitted "
    "with no hold-out, and because horizon and threshold are chosen after seeing every "
    "result, the edge produced is an UPPER BOUND on what any out-of-sample strategy in "
    "this arena could achieve."
)

KILL = (
    "KILLED, and arena 'intraday_index' recommended for closure, if the in-sample "
    "overfitted upper-bound edge fails to exceed the cheapest measured cost floor at "
    "EVERY horizon in {15,30,60,120,240,375} minutes and at every conditioning "
    "threshold tested. The logic is one-directional and deliberate: no out-of-sample "
    "strategy can beat an in-sample fit on the same features, so a bound that fails "
    "the economics closes the space rather than merely this attempt. "
    "IF THE BOUND CLEARS, THAT IS NOT A FINDING and must not be reported as one — an "
    "overfit in-sample fit clearing a bar is expected, carries no out-of-sample "
    "content, and leaves the arena exactly as open as it was. In that case the "
    "hypothesis is recorded as 'not closed' rather than 'survived', and the next step "
    "would be an honest walk-forward of the specific combination, registered separately."
)

NOTE = (
    "Arena 5, ceiling screen, prompted by screen 1's kill (intraday-ic-modern) and by "
    "the cost measurement in Amendment E9. Runs on data/intraday/NIFTY/index.parquet "
    "(370,626 1-min bars, 992 trading days, 2022-08-16..2026-08-14). "
    "gate='strict' recorded for form: this computes correlations and a linear fit on "
    "index bars and executes no fills. "
    "Every feature must be past-only — rolling or expanding, never a session-wide "
    "aggregate. Screen 1 was nearly derailed by an rvol_ratio normalised on the whole "
    "session median, which made it the strongest signal in the screen until the "
    "lookahead was found. "
    "equity registered at the bottom of Amendment E's Rs 50k-1L range."
)


def main():
    if registry.get(HID):
        h = registry.get(HID)
        print(f"'{HID}' already registered at {h['registered_at']} "
              f"(status {h['status']}). Ids are permanent; not re-registering.")
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
            "kind": "in-sample upper bound (deliberately overfitted)",
            "horizons_min": HORIZONS,
            "conditioning_thresholds_z": [0.0, 1.0, 1.5, 2.0, 2.5],
            "source": "data/intraday/NIFTY/index.parquet",
            "cost_floor_index_points": {
                "option_transaction": 4.01,
                "option_theta_pts_per_hour_4dte": 5.45,
                "future_all_in": 7.71,
                "source": "Amendment E9 / backtest/option_spread.py",
            },
            "direction": "can only close the arena, never open it",
        },
        underlying="NIFTY",
        equity=min(charter.TRADING_CAPITAL_RANGE_RS),
        engine="intraday_ceiling",
        note=NOTE,
    )
    print(f"registered '{h['id']}' in arena '{h['arena']}'")
    print(f"  n_configs {h['n_configs']} -> Section 4 bar "
          f"|t| >= {charter.noise_threshold(N_CONFIGS):.2f} (not the binding test)")
    print(f"  window    {h['window'][0]} .. {h['window'][1]}")
    print(f"  fingerprint {h['fingerprint'][:16]}")
    print("\nThis screen can only CLOSE the arena. COMMIT BEFORE RUNNING.")


if __name__ == "__main__":
    main()
