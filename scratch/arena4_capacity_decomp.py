"""Arena 4, earnings half — what actually costs the 93.4% of events not traded?

ARENAS.md records the constraint as capacity: "the binding constraint is capacity,
not event supply ... the engine filled 6.6% of the straddles it wanted at Rs 15L".
The stored skip histogram does not support that reading, so this decomposes it
before any structure is proposed on the strength of it.

Reports FILL DIAGNOSTICS ONLY — never P&L. The arena is open, and the question
being asked here is "can this be filled", which is a property of the market and
the risk cap. Measuring returns at the same time would spend edge knowledge on a
question that does not need it.

Run: PYTHONUTF8=1 python scratch/arena4_capacity_decomp.py
"""
import datetime
import os
import sys
from collections import Counter

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from research.engines.eventvol import EventVolEngine, EventVolConfig
from research.screen import trading_dates

START, END = datetime.date(2023, 1, 1), datetime.date(2026, 8, 8)

# Skip reasons grouped by WHAT KIND of constraint they are. The point of the
# exercise: "unfillable" in the summary lumps together a market fact, a schema
# artefact and a self-imposed risk rule, and only the first is capacity.
KIND = {
    "one_lot_exceeds_risk_cap": "RISK CAP (self-imposed sizing rule)",
    "credit_too_thin": "PRICE (credit below the friction floor)",
    "notice_too_short": "CALENDAR (lookahead guard)",
    "not_yet_announced": "CALENDAR (lookahead guard)",
    "max_concurrent": "SELF-IMPOSED (concurrency cap)",
    "window_outside_archive": "ARCHIVE EDGE",
    "no_wings_listed": "MARKET (strikes not listed)",
    "unknown_lot": "DATA",
    "leg_missing": "MARKET (leg absent from chain)",
    "exit_unfillable": "MARKET (cannot close)",
}


def kind_of(reason: str) -> str:
    if reason in KIND:
        return KIND[reason]
    if "txns_unknown" in reason:
        return "GATE SCHEMA (txns NaN pre-2024 — Finding 5)"
    if "settle_only" in reason or "not_traded" in reason:
        return "MARKET (leg did not trade)"
    if reason.startswith("entry_") or reason.startswith("exit_"):
        return "MARKET/GATE (other leg refusal)"
    return f"other:{reason}"


def run(label, **over):
    eng = EventVolEngine()
    cfg = EventVolConfig(**over)
    dates = trading_dates(START, END)
    res = eng.run(cfg, dates)
    s = res["summary"]
    ex = s["engine_extras"] if "engine_extras" in s else s
    considered = ex.get("events_considered", 0)
    traded = ex.get("events_traded", 0)
    rate = ex.get("capacity_fill_rate_pct", 0.0)

    buckets = Counter()
    for reason, n in res["skip_reasons"].items():
        buckets[kind_of(reason)] += n

    print(f"\n{'='*74}\n{label}")
    print(f"  considered {considered:>5}   traded {traded:>4}   fill {rate:>5.1f}%   "
          f"trades/yr {traded/3.6:>5.1f}  (A5 needs 32.3)")
    for k, n in buckets.most_common():
        print(f"    {100*n/max(considered,1):>5.1f}%  {n:>5}  {k}")
    return traded, rate


if __name__ == "__main__":
    print("Arena 4 earnings — decomposing the 6.6% fill rate")
    print("FILL DIAGNOSTICS ONLY. No P&L is computed or printed here.")

    # 1. Reproduce the registered run exactly.
    run("BASELINE — as registered (gate=strict, wing_pct=0.10)")

    # 2. One variable: the gate. Finding 5 says `strict` refuses all of 2023
    #    because the legacy schema has no txns column.
    run("GATE — strict_legacy (recovers 2023, Finding 5)", gate="strict_legacy")

    # 3. One variable: the wing. Narrower wings cut max loss per lot, which is
    #    what `one_lot_exceeds_risk_cap` tests against.
    for w in (0.05, 0.075, 0.15):
        run(f"WING — wing_pct={w} (gate=strict, as registered)", wing_pct=w)

    # 4. Both levers together.
    for w in (0.05, 0.075):
        run(f"BOTH — strict_legacy + wing_pct={w}", gate="strict_legacy", wing_pct=w)
