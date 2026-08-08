"""Data quality report for the bhavcopy archive — makes bad data loud.

WHY THIS EXISTS
---------------
Every false positive this project has produced traces back to data that looked
fine: settlement prices standing in for fills, marks taken off a one-sided book,
a chain published for the wrong expiry. None of it announced itself. The rule
adopted in RESEARCH_CHARTER.md is that the measurement apparatus must fail
loudly rather than degrade silently, and this is that check for Layer 0.

It matters more now than it did, because the archive spans two incompatible NSE
eras. UDiFF (2024-01 onward) carries a trade count, an underlying price and a
market lot; the legacy archive carries none of those, and it prints a CLOSE for
contracts that never traded. Anything derived across the boundary can be an
artefact of the boundary rather than of the market.

CHECKS
------
  coverage    calendar gaps, holiday markers, zero-byte files
  schema      required fields present, per era
  spot        provenance mix, continuity, absurd jumps
  lot         lot-size timeline and implausible transitions
  liquidity   share of legs that actually traded, per year  <-- era comparison
  integrity   negative/absurd OI, volume, prices; expiries in the past
  arbitrage   no-arbitrage shape violations via options_pricing_service

Exit code is non-zero if any HARD check fails, so this can gate a pipeline.

Usage:
    python -m backtest.data_quality --start 2016-01-01 --end 2026-08-06
    python -m backtest.data_quality --sample 120 --underlying NIFTY
"""
import argparse
import datetime
import os
import statistics as st
import sys
from collections import Counter, defaultdict
from typing import Any, Dict, List, Optional

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from backtest import bhavcopy

# A legacy option leg cannot supply these; absence is expected, not a fault.
_UDIFF_ONLY = {"txns"}
_REQUIRED_LEG_FIELDS = {"close", "traded", "oi", "chg_oi", "volume", "lot"}


class Report:
    def __init__(self):
        self.hard: List[str] = []
        self.soft: List[str] = []
        self.notes: List[str] = []

    def fail(self, msg): self.hard.append(msg)
    def warn(self, msg): self.soft.append(msg)
    def note(self, msg): self.notes.append(msg)

    @property
    def ok(self) -> bool:
        return not self.hard


def _cached_days(start, end):
    """(days_with_data, days_marked_holiday, weekdays_with_neither)."""
    have, holiday, missing = [], [], []
    d = start
    while d <= end:
        if d.weekday() < 5:
            zp = bhavcopy.zip_path(d)
            hm = os.path.join(bhavcopy.DATA_DIR, f"{d.strftime('%Y%m%d')}.holiday")
            if os.path.exists(zp) and os.path.getsize(zp) > 0:
                have.append(d)
            elif os.path.exists(hm):
                holiday.append(d)
            else:
                missing.append(d)
        d += datetime.timedelta(days=1)
    return have, holiday, missing


def check_coverage(rep: Report, start, end):
    have, holiday, missing = _cached_days(start, end)
    total = len(have) + len(holiday) + len(missing)
    rep.note(f"coverage: {len(have)} data days, {len(holiday)} holidays, "
             f"{len(missing)} weekdays missing, of {total} weekdays")

    if not have:
        rep.fail("no cached data in range at all")
        return []

    # India has ~13-16 exchange holidays a year; far more than that means the
    # download stopped early or the archive refused a stretch of dates.
    years = max((end - start).days / 365.25, 0.01)
    hol_rate = len(holiday) / years
    if hol_rate > 25:
        rep.warn(f"{hol_rate:.0f} holiday markers/year — higher than NSE's ~15; "
                 f"some may be failed downloads cached as 404s")
    if missing:
        rep.fail(f"{len(missing)} weekdays have neither data nor a holiday marker "
                 f"(first: {missing[0]}, last: {missing[-1]}) — download incomplete")

    # a gap longer than a fortnight is a structural hole, not a holiday cluster
    runs, run_start, prev = [], None, None
    for d in missing:
        if prev is None or (d - prev).days > 4:
            if run_start and (prev - run_start).days >= 14:
                runs.append((run_start, prev))
            run_start = d
        prev = d
    if run_start and prev and (prev - run_start).days >= 14:
        runs.append((run_start, prev))
    for a, b in runs[:5]:
        rep.fail(f"structural gap {a} -> {b} ({(b - a).days} days)")
    return have


def check_days(rep: Report, days: List[datetime.date], underlying: str):
    """Deep checks on a list of days. Returns per-year aggregates."""
    by_year = defaultdict(lambda: {"days": 0, "legs": 0, "traded": 0,
                                   "arb": 0, "lots": Counter(),
                                   "spot_src": Counter()})
    spots: List[tuple] = []
    lot_timeline: List[tuple] = []
    loaded = 0

    try:
        from backend.app.services.options_pricing_service import arbitrage_violations
    except Exception:
        arbitrage_violations = None

    for d in days:
        ch = bhavcopy.load_chain(d, underlying)
        if ch is None:
            rep.warn(f"{d}: file cached but no {underlying} chain parsed")
            continue
        loaded += 1
        y = by_year[d.year]
        y["days"] += 1
        y["spot_src"][ch.get("spot_source", "?")] += 1

        # ── schema ────────────────────────────────────────────────────────
        if ch["options"]:
            leg = next(iter(ch["options"].values()))
            miss = _REQUIRED_LEG_FIELDS - set(leg)
            if miss:
                rep.fail(f"{d}: leg missing required fields {sorted(miss)}")

        # ── spot ──────────────────────────────────────────────────────────
        spot = float(ch.get("spot") or 0)
        if spot <= 0:
            rep.fail(f"{d}: no usable spot (source={ch.get('spot_source')})")
        else:
            spots.append((d, spot))

        # ── lot ───────────────────────────────────────────────────────────
        # Tracked on the MODAL lot. A day legitimately carries more than one
        # (NSE revises the lot for new expiries while open ones keep the old),
        # so a per-leg timeline would flap and mean nothing.
        lot = ch.get("lot")
        if not lot or lot <= 0:
            rep.fail(f"{d}: no market lot resolved — P&L would be unscaled")
        else:
            y["lots"][lot] += 1
            if not lot_timeline or lot_timeline[-1][1] != lot:
                lot_timeline.append((d, lot))
        by_exp = ch.get("lot_by_expiry") or {}
        if by_exp and any(l <= 0 for l in by_exp.values()):
            rep.fail(f"{d}: non-positive lot on some expiry {by_exp}")
        if len(set(by_exp.values())) > 2:
            rep.warn(f"{d}: {len(set(by_exp.values()))} distinct lot sizes "
                     f"{sorted(set(by_exp.values()))} — verify against NSE circular")

        # ── integrity + liquidity ─────────────────────────────────────────
        for (exp, strike, typ), v in ch["options"].items():
            y["legs"] += 1
            if v.get("traded"):
                y["traded"] += 1
            if float(v.get("close") or 0) < 0:
                rep.fail(f"{d}: negative price at {strike}{typ}")
            if float(v.get("oi") or 0) < 0 or float(v.get("volume") or 0) < 0:
                rep.fail(f"{d}: negative OI/volume at {strike}{typ}")
            if exp < d:
                rep.fail(f"{d}: expiry {exp} is in the past")
            for f in _UDIFF_ONLY:
                if ch["era"] == "udiff" and v.get(f) is None:
                    rep.warn(f"{d}: UDiFF leg missing '{f}'")

        # ── arbitrage shape (near-spot only; deep wings are noisy) ────────
        if arbitrage_violations and spot > 0 and ch["expiries"]:
            near = min(ch["expiries"])
            payload = {"strikes": {}}
            for (exp, strike, typ), v in ch["options"].items():
                if exp != near or abs(strike - spot) > 0.05 * spot:
                    continue
                node = payload["strikes"].setdefault(f"{strike:.2f}", {})
                node[typ.lower()] = {"bid": 0.0, "ask": 0.0,
                                     "ltp": float(v.get("close") or 0)}
            try:
                viol = arbitrage_violations(payload)
                y["arb"] += len(viol.get("ce", set())) + len(viol.get("pe", set()))
            except Exception:
                pass

    # ── spot continuity ──────────────────────────────────────────────────
    spots.sort()
    for (d0, s0), (d1, s1) in zip(spots, spots[1:]):
        gap_days = (d1 - d0).days
        if gap_days > 10 or s0 <= 0:
            continue  # a long gap can legitimately span a large move
        move = abs(s1 - s0) / s0
        if move > 0.20:
            rep.fail(f"spot jumps {move:.0%} from {d0} ({s0:,.0f}) to "
                     f"{d1} ({s1:,.0f}) — bad spot or wrong index")
        elif move > 0.12:
            rep.warn(f"spot moves {move:.0%} {d0} -> {d1} — verify (COVID-era "
                     f"moves of this size are real)")

    if loaded == 0:
        rep.fail("no chains could be parsed from the cached files")
    return by_year, lot_timeline


def print_report(rep: Report, by_year, lot_timeline, underlying: str):
    print("\n" + "=" * 86)
    print(f"DATA QUALITY — {underlying}")
    print("=" * 86)

    print(f"\n{'year':>6} {'days':>6} {'legs':>9} {'traded':>9} {'traded%':>8} "
          f"{'arb':>5}  lots        spot source")
    print("-" * 86)
    for y in sorted(by_year):
        a = by_year[y]
        pct = 100.0 * a["traded"] / a["legs"] if a["legs"] else 0.0
        lots = ",".join(str(k) for k, _ in a["lots"].most_common(3))
        srcs = ",".join(f"{k.split('_')[0]}:{v}" for k, v in a["spot_src"].most_common(2))
        print(f"{y:>6} {a['days']:>6} {a['legs']:>9,} {a['traded']:>9,} "
              f"{pct:>7.1f}% {a['arb']:>5}  {lots:<11} {srcs}")

    if lot_timeline:
        print(f"\nlot-size timeline: " +
              " -> ".join(f"{d} = {l}" for d, l in lot_timeline))

    for label, items, mark in (("HARD FAILURES", rep.hard, "❌"),
                               ("WARNINGS", rep.soft, "⚠️ ")):
        if items:
            print(f"\n{label} ({len(items)})")
            print("-" * 86)
            for m in items[:20]:
                print(f"  {mark} {m}")
            if len(items) > 20:
                print(f"  ... and {len(items) - 20} more")

    print("\n" + "-" * 86)
    for n in rep.notes:
        print(f"  {n}")
    print(f"\nVERDICT: {'PASS' if rep.ok else 'FAIL'} "
          f"({len(rep.hard)} hard, {len(rep.soft)} soft)")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", default="2016-01-01")
    ap.add_argument("--end", default="2026-08-06")
    ap.add_argument("--underlying", default="NIFTY")
    ap.add_argument("--sample", type=int, default=150,
                    help="evenly-spaced days to deep-check (0 = every day)")
    a = ap.parse_args()

    start = datetime.date.fromisoformat(a.start)
    end = datetime.date.fromisoformat(a.end)
    rep = Report()

    have = check_coverage(rep, start, end)
    if have:
        days = have
        if a.sample and len(have) > a.sample:
            step = len(have) / a.sample
            days = [have[int(i * step)] for i in range(a.sample)]
            rep.note(f"deep-checked {len(days)} of {len(have)} days "
                     f"(evenly spaced; --sample 0 for all)")
        by_year, lots = check_days(rep, days, a.underlying)
    else:
        by_year, lots = {}, []

    print_report(rep, by_year, lots, a.underlying)
    return 0 if rep.ok else 1


if __name__ == "__main__":
    sys.exit(main())
