"""
Register the non-directional screen for arena intraday_option, before it runs.

THE CONSTRAINT THAT SHAPES THIS SCREEN
--------------------------------------
Amendment E6: "No options hypothesis may be registered against option history
that was not archived before expiry." Dhan deletes option intraday history at
expiry, and the archive only starts 2026-07-27, so there are NO expiry-day option
bars on disk yet — Aug 18 is still ahead. A 0-DTE screen cannot be run on
history and would be forward-testing for months.

So this screen asks the same economic question WITHOUT needing option history,
by using the identity that connects them.

THE IDENTITY
------------
For a delta-neutral short option position over a window,

    P&L  ~  0.5 * gamma * S^2 * (implied variance - realised variance)

and theta and gamma are linked by the Black-Scholes relation theta ~ -0.5 *
gamma * S^2 * sigma^2. So the SIGN of a short-premium trade over any window is
decided entirely by implied variance over that window versus realised variance
over it — nothing else.

If implied vol is flat across the session, implied variance accrues LINEARLY with
clock time. That removes the need for an implied-vol series altogether and turns
the question into one measurable from index bars alone:

    does any intraday window's share of the day's REALISED VARIANCE fall
    materially short of its share of the day's CLOCK?

A window taking 30% of the session's minutes but only 15% of its variance is a
window where a short seller collects 30% of the day's decay against 15% of the
day's risk. That is the entire non-directional case, stated so it can be measured
today rather than after months of archiving.

HONEST PRIOR, RECORDED BEFORE THE RUN
-------------------------------------
The U-shaped intraday volatility curve is textbook, not a discovery, and this
screen does NOT claim to find it. Everyone knows midday is quieter. The claim is
strictly quantitative: whether the gap is large enough to pay for four orders of
transaction cost plus the left tail. Arena 1 already killed short volatility on
weekly NIFTY with the finding that the +2.38 vol-point premium is payment for a
left tail, and both structures were negative BEFORE friction. That closure was
held-to-expiry and weekly, so the intraday time-of-day shape is genuinely
untested — but the prior it sets is poor and is recorded as poor.

Usage:  ./venv/Scripts/python.exe scratch/register_arena6_varshare.py
"""
import os
import sys

sys.path.insert(0, os.getcwd())

from research import charter, registry  # noqa: E402

HID = "intraday-varshare-modern"

# 6 windows x 2 day-conditionings (all days / low trailing-vol days)
N_CONFIGS = 12

CLAIM = (
    "Some intraday window of the NIFTY session captures a share of the day's realised "
    "variance materially BELOW its share of the day's clock time, by a margin large "
    "enough that a 1-lot short ATM straddle opened and closed inside that window has "
    "positive expectancy after the measured Amendment E9 transaction cost of four "
    "orders, AND survives its left tail. Under the Black-Scholes theta/gamma relation "
    "the sign of any short-premium trade over a window is decided solely by implied "
    "variance versus realised variance over it; with implied vol flat across the "
    "session, implied variance accrues linearly in clock time, so a variance share "
    "below the time share IS the edge and requires no implied-vol series to detect. "
    "The U-shape of intraday volatility is textbook and is NOT the claim; the claim is "
    "that the gap is large enough to trade at this capital."
)

KILL = (
    "KILLED unless some (window, day-conditioning) cell satisfies ALL of: "
    "(a) mean variance share below time share, with the shortfall significant at "
    "|t| >= 2.23 (the Section 4 bar at N=12); "
    "(b) the resulting theta-minus-realised edge, in rupees for one lot, exceeds the "
    "measured cost of FOUR orders (a straddle is two legs, opened and closed) by at "
    "least 2x — the same 2x margin Amendment E5 requires elsewhere; "
    "(c) the shortfall holds in EVERY calendar year with >= 60 trading days (D5 applied "
    "to the variance share); "
    "(d) THE LEFT TAIL IS SURVIVABLE: the worst single day's loss must not exceed 15% "
    "of equity at the bottom of Amendment E's Rs 50k-1L range, and the worst 1% of "
    "days must not erase the mean edge accumulated over a year. This condition is "
    "mandatory and non-negotiable because it is exactly what killed Arena 1 — the "
    "premium there was real and was payment for a tail that arrived. "
    "A cell clearing (a)-(c) but failing (d) is NOT a finding; it is Arena 1 again. "
    "If nothing clears, arena 'intraday_option' is recommended for closure, which "
    "would close the last open arena and complete the Phase 2 survey."
)

NOTE = (
    "Arena 6 (intraday_option), the last open arena. Non-directional by construction: "
    "the quantity measured is variance, which has no sign. "
    "Runs on data/intraday/NIFTY/index.parquet (370,626 1-min bars, 989 sessions, "
    "2022-08-16..2026-08-14) — the INDEX series, deliberately, because Amendment E6 "
    "forbids resting an options hypothesis on option history that was not archived "
    "before expiry and no expiry-day option bars exist on disk yet. "
    "Costs are the measured Amendment E9 figures. A straddle is 2 legs and a round "
    "trip is 4 orders, so the cost is roughly double the single-leg figures used in "
    "arena 5. "
    "gate='strict' recorded for form; this executes no fills. "
    "Windows are fixed clock intervals declared in config, not searched. "
    "Every conditioning variable is past-only: the low-vol day filter uses the PRIOR "
    "day's realised vol, never the current day's."
)


def main():
    if registry.get(HID):
        h = registry.get(HID)
        print(f"'{HID}' already registered at {h['registered_at']} "
              f"(status {h['status']}). Not re-registering.")
        return

    h = registry.register(
        hid=HID,
        arena="intraday_option",
        claim=CLAIM,
        kill_criterion=KILL,
        window=["2022-08-16", "2026-08-14"],
        gate="strict",
        n_configs=N_CONFIGS,
        config={
            "measured_quantity": "realised variance share vs clock-time share",
            "why_no_iv_series_needed": (
                "BS theta/gamma relation: short-premium P&L sign over a window is "
                "implied variance minus realised variance; flat intraday IV makes "
                "implied variance linear in clock time"),
            "windows": [
                "09:15-10:00", "10:00-11:00", "11:00-13:00",
                "13:00-14:30", "14:30-15:30", "11:00-14:30",
            ],
            "day_conditioning": ["all", "prior_day_low_vol"],
            "structure_costed": "1-lot short ATM straddle, 2 legs, 4 orders round trip",
            "tail_test": "worst day, worst 1% of days, vs 15% of Rs 50,000",
            "source": "data/intraday/NIFTY/index.parquet",
            "prior": "poor — Arena 1 killed weekly short vol; that was held-to-expiry",
        },
        underlying="NIFTY",
        equity=min(charter.TRADING_CAPITAL_RANGE_RS),
        engine="intraday_varshare",
        note=NOTE,
    )
    print(f"registered '{h['id']}' in arena '{h['arena']}'")
    print(f"  n_configs {h['n_configs']} -> Section 4 bar "
          f"|t| >= {charter.noise_threshold(N_CONFIGS):.2f}")
    print(f"  left-tail condition (d) is MANDATORY — it is what killed Arena 1")
    print(f"  fingerprint {h['fingerprint'][:16]}")
    print("\nCOMMIT BEFORE RUNNING.")


if __name__ == "__main__":
    main()
