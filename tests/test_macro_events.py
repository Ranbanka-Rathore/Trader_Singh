"""Tests for the macro calendar — mostly that it refuses to overstate itself.

This module's value is not the dates it has; it is that a source which cannot
cover a window says so instead of returning nothing. A short calendar produces
no trades, and no trades is indistinguishable from a strategy that does not
work — which is how 47 hand-typed dates sat in the codebase for months looking
like a feature.

Run with:  PYTHONUTF8=1 python tests/test_macro_events.py
"""
import datetime
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from backtest import macro_events as me

PASS, FAIL = 0, 0


def check(name, cond):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  ✅ {name}")
    else:
        FAIL += 1
        print(f"  ❌ {name}")


def test_fomc_parsing():
    print("\n[1] FOMC parsing, including the month-straddling meetings")
    hist = ("2017 FOMC Meetings January 31-February 1 Meeting Minutes March 14-15 "
            "Meeting Minutes (Released April 05) May 2-3 Meeting "
            "October 31-November 1 Meeting December 12-13 Meeting")
    got = me.parse_fomc(hist, 2017)
    check(f"same-month ranges resolve to the closing day ({got[:2]})",
          datetime.date(2017, 3, 15) in got and datetime.date(2017, 5, 3) in got)
    check("a January-February meeting resolves to February 1",
          datetime.date(2017, 2, 1) in got)
    check("an October-November meeting resolves to November 1",
          datetime.date(2017, 11, 1) in got)
    check(f"five meetings, no duplicates ({len(got)})",
          len(got) == 5 and len(set(got)) == 5)
    check("a minutes release date is not mistaken for a meeting",
          datetime.date(2017, 4, 5) not in got)

    live = ("2026 FOMC Meetings January 27-28 Statement: PDF Minutes (Released "
            "February 18, 2026) March 17-18* Statement: PDF "
            "2025 FOMC Meetings June 16-17 Statement: PDF")
    got26 = me.parse_fomc(live, 2026)
    check("the live-calendar format parses without the word 'Meeting'",
          datetime.date(2026, 1, 28) in got26)
    check("...and a multi-year page is sliced to the year asked for",
          datetime.date(2025, 6, 17) not in got26
          and all(d.year == 2026 for d in got26))
    check("the '(Released February 18, 2026)' noise is ignored",
          datetime.date(2026, 2, 18) not in got26)

    check("an unscheduled single-day meeting is picked up",
          datetime.date(2020, 3, 15) in me.parse_fomc(
              "2020 FOMC Meetings March 15 (unscheduled) Statement", 2020))


def test_manual_sources_are_read():
    print("\n[2] Manual sources are ISO strings, not date objects")
    check("an ISO string parses", me._as_date("2024-02-08") == datetime.date(2024, 2, 8))
    check("a date passes through", me._as_date(datetime.date(2024, 2, 8))
          == datetime.date(2024, 2, 8))
    check("junk yields None", me._as_date("not a date") is None)

    rbi, where = me._manual("rbi")
    check(f"RBI dates load from regime_filters ({len(rbi)} of them)", len(rbi) > 10)
    check("...all as real dates", all(isinstance(d, datetime.date) for d in rbi))
    check("and the provenance names where they came from", "regime_filters" in where)
    check("budget dates load too", len(me._manual("budget")[0]) > 0)
    check("provenance is recorded per source",
          me.PROVENANCE["fomc"] == "harvested" and me.PROVENANCE["rbi"] == "manual")


def test_coverage_is_enforced():
    print("\n[3] A source refuses a window it cannot cover")
    cov = me.coverage("rbi")
    check(f"RBI declares its real coverage ({cov[0]}..{cov[1]})", cov is not None)
    check("...which is nowhere near the full archive", cov[0].year >= 2024)

    try:
        me.load_macro(datetime.date(2016, 1, 1), datetime.date(2026, 8, 8), "rbi")
        check("a window past the coverage is refused", False)
    except me.CoverageError as exc:
        check(f"a window past the coverage is refused ({str(exc)[:40]}...)", True)
        check("...and the message says what it DOES cover", "covers" in str(exc))
        check("...and why silence would be worse",
              "indistinguishable" in str(exc))

    inside = me.load_macro(cov[0], cov[1], "rbi")
    check(f"inside its coverage it serves normally ({len(inside)} events)",
          len(inside) > 5)
    check("events carry the source as their symbol",
          all(e.symbol == "RBI" for e in inside))
    check("an unknown source raises", _raises(me.load_macro, cov[0], cov[1], "moon"))


def test_announced_at_is_conservative():
    print("\n[4] Macro notice is modelled, and modelled pessimistically")
    cov = me.coverage("rbi")
    evs = me.load_macro(cov[0], cov[1], "rbi", notice_days=21)
    check("every event is announced before it happens",
          all(e.announced_at < e.date for e in evs))
    check("notice is the modelled 21 days", all(e.notice_days == 21 for e in evs))
    short = me.load_macro(cov[0], cov[1], "rbi", notice_days=5)
    check("the modelled notice is a knob, not a constant",
          all(e.notice_days == 5 for e in short))
    check("shorter notice is the conservative direction — it can only remove "
          "trades", len(short) == len(evs))


def test_quality_report_fails_loudly():
    print("\n[5] The quality report refuses to bless an incomplete harvest")
    if not os.path.exists(me.fomc_path(2019)):
        check("FOMC cache present (run download_fomc_range first)", False)
        return
    rep = me.quality_report(datetime.date(2016, 1, 1), datetime.date(2026, 8, 8))
    check("the report knows all three sources", set(rep["sources"]) == set(me.SOURCES))
    check("each source reports its provenance",
          all("provenance" in v for v in rep["sources"].values()))
    check("each source reports the window it covers",
          all("coverage" in v for v in rep["sources"].values()))
    check("short sources are flagged as soft faults", len(rep["soft_faults"]) >= 2)

    # The point of this test: the harvest is NOT clean, and the report says so.
    check(f"the report does not pass ({len(rep['hard_faults'])} hard fault(s))",
          not rep["ok"])
    check("...naming the cadence shortfall, not a vague failure",
          any("cadence" in h for h in rep["hard_faults"]))
    check("FOMC is nonetheless mostly harvested",
          rep["sources"]["fomc"]["n_events"] > 70)


def _raises(fn, *a):
    try:
        fn(*a)
        return False
    except Exception:
        return True


if __name__ == "__main__":
    test_fomc_parsing()
    test_manual_sources_are_read()
    test_coverage_is_enforced()
    test_announced_at_is_conservative()
    test_quality_report_fails_loudly()
    print(f"\n{'=' * 50}\nRESULT: {PASS} passed, {FAIL} failed")
    sys.exit(1 if FAIL else 0)
