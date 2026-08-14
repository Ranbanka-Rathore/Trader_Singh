"""
Register Arena 5's first screen BEFORE its result exists.

This is run and COMMITTED before scratch/arena5_intraday_ic.py is executed even
once. Phase 1's single most useful discipline was exactly this: the carry signal
was declared, with direction and thresholds, in a commit made before the number
existed, which is the only reason its negative result could not later be re-read
as "short carry works".

Why this is registered rather than run as an unregistered measurement: ARENAS.md
records that arena 3's signal-quality study measured eleven candidate signals'
ICs *without* a registration, and the charter's own conclusion was that anything
registered in arena 3 afterwards "is registered by someone who already knows the
shape of the answer". Running this screen unregistered would spend Arena 5's
signals the same way. So it is registered, and it is charged for its configs.

Usage:  ./venv/Scripts/python.exe scratch/register_arena5.py
"""
import os
import sys

sys.path.insert(0, os.getcwd())

from research import charter, registry  # noqa: E402

HID = "intraday-ic-modern"

# 4 signals x 4 horizons. Section 4's bar is sqrt(2 ln N); at N=16 that is 2.36.
N_CONFIGS = 16

SIGNALS = {
    "mom_h": "return over the trailing h minutes (momentum if IC>0, reversal if IC<0)",
    "vwap_dev": "(price - session VWAP) / trailing 30-min stdev of price",
    "or_pos": "position in the first-15-min opening range: (p - OR_low)/(OR_high - OR_low)",
    "rvol_ratio": "trailing 30-min realised vol / that session's median 30-min realised vol",
}
HORIZONS = [5, 15, 30, 60]

CLAIM = (
    "At least one of four pre-declared signals (mom_h, vwap_dev, or_pos, rvol_ratio) "
    "measured on NIFTY 1-minute index bars carries directional information about the "
    "forward h-minute index return at h in {5,15,30,60}, strongly enough to matter "
    "economically at Amendment E's capital. Specifically: some (signal, horizon) cell "
    "shows a Spearman IC whose |t| clears the Section 4 noise bar for N=16 (|t| >= 2.36), "
    "with the SAME IC sign in each of the four calendar years measured separately "
    "(2022 partial, 2023, 2024, 2025, 2026 partial — Amendment D5's sign-consistency "
    "rule applied to time rather than liquidity era), AND whose implied gross edge per "
    "trade at that horizon is at least 2x the net per-trade target from Amendment E5. "
    "Direction is NOT predicted: both momentum (IC>0) and reversal (IC<0) count, which "
    "is why the bar is two-sided and the sign-consistency requirement is what stops a "
    "sign flip across years being read as a finding."
)

KILL = (
    "KILLED if no (signal, horizon) cell satisfies all three conditions together: "
    "(a) |t| >= 2.36 on the pooled Spearman IC, (b) the same IC sign in every calendar "
    "year with at least 60 trading days of data, and (c) implied gross edge per trade "
    ">= 2x the Amendment E5 net target at that horizon's achievable trade frequency. "
    "A cell that clears (a) but fails (b) is a sign-unstable estimate and is NOT a "
    "finding — that is the D5 lesson. A cell that clears (a) and (b) but fails (c) is "
    "real but too small to trade at this capital, and is recorded as such rather than "
    "carried forward. This screen can only kill or advance; per Amendment B5 it cannot "
    "promote anything."
)

NOTE = (
    "Arena 5, screen 1, and the RESUME.md Step-4 'measure before building' item. "
    "Runs on data/intraday/NIFTY/index.parquet (370,626 1-min bars, 992 trading days, "
    "2022-08-16..2026-08-14), archived from Dhan on 2026-08-14. "
    "gate='strict' is recorded for form only: this screen computes correlations on "
    "index bars and executes no fills, so no liquidity gate applies to it. Any "
    "STRATEGY descending from a surviving cell must be re-run under a real gate. "
    "equity is registered at the BOTTOM of Amendment E's Rs 50k-1L range, which is the "
    "conservative choice for the economic condition (c). "
    "Killing this hypothesis does not by itself close arena 'intraday_index' — "
    "Section 7 leaves arena closure an operator decision recorded with its grounds, "
    "and Section 8 fixes no screen allotment. It would, however, close the specific "
    "idea that a single unconditional signal predicts intraday index direction."
)


def main():
    existing = registry.get(HID)
    if existing:
        print(f"'{HID}' is already registered at {existing['registered_at']} "
              f"(status {existing['status']}). Ids are permanent; not re-registering.")
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
            "signals": SIGNALS,
            "horizons_min": HORIZONS,
            "bar_minutes": 1,
            "source": "data/intraday/NIFTY/index.parquet",
            "ic_method": "spearman",
            "session_only": "09:15-15:30, out-of-session bars excluded",
            "min_days_per_year_for_sign_test": 60,
        },
        underlying="NIFTY",
        equity=min(charter.TRADING_CAPITAL_RANGE_RS),
        engine="intraday_ic",
        note=NOTE,
    )
    bar = charter.noise_threshold(N_CONFIGS) if hasattr(charter, "noise_threshold") else 2.36
    print(f"registered '{h['id']}' in arena '{h['arena']}'")
    print(f"  n_configs      {h['n_configs']}  -> Section 4 bar |t| >= {bar:.2f}")
    print(f"  window         {h['window'][0]} .. {h['window'][1]}")
    print(f"  equity         Rs {h['equity']:,.0f}")
    print(f"  fingerprint    {h['fingerprint'][:16]}")
    print(f"  registered_at  {h['registered_at']}")
    print("\nCOMMIT THIS BEFORE RUNNING THE SCREEN.")


if __name__ == "__main__":
    main()
