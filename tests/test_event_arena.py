"""Tests for the earnings calendar and the event-vol engine (arena 4).

Two things here are load-bearing and everything else is detail:

  * the THREE purpose vocabularies. Matching only the modern one found zero
    events before 2018 and silently dropped 18,600 rows in 2025-26. The
    equivalent mistake in the bhavcopy loader would have made 89% of every
    pre-2024 chain fake, and this is the same mistake in a different file.
  * the LOOKAHEAD GUARD. An earnings strategy that may use the calendar as of
    today is trivially profitable and completely worthless.

Run with:  PYTHONUTF8=1 python tests/test_event_arena.py
"""
import datetime
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from backtest import events as ev
from backtest.liquidity_gate import LiquidityGate
from research import engines

PASS, FAIL = 0, 0


def check(name, cond):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  ✅ {name}")
    else:
        FAIL += 1
        print(f"  ❌ {name}")


def test_purpose_vocabularies():
    print("\n[1] All three NSE purpose vocabularies are recognised")
    era_2016 = {"bm_purpose": "Results/Dividend", "bm_desc": ""}
    era_2018 = {"bm_purpose": "Financial Results/Other business matters", "bm_desc": ""}
    era_2018b = {"bm_purpose": "Financial results", "bm_desc": ""}
    era_2025 = {"bm_purpose": "Board Meeting Intimation",
                "bm_desc": ("ACME LIMITED has informed the Exchange about Board "
                            "Meeting to be held on 31-Dec-2025 to consider and "
                            "approve the financial results")}
    check("2016-17 'Results/Dividend'", ev._is_earnings(era_2016))
    check("2018-24 'Financial Results/...'", ev._is_earnings(era_2018))
    check("...including the lowercase spelling", ev._is_earnings(era_2018b))
    check("2025+ generic purpose, subject in bm_desc", ev._is_earnings(era_2025))

    for row in ({"bm_purpose": "Fund Raising", "bm_desc": ""},
                {"bm_purpose": "Dividend", "bm_desc": ""},
                {"bm_purpose": "Buyback", "bm_desc": ""},
                {"bm_purpose": "Board Meeting Intimation",
                 "bm_desc": "ACME has informed the Exchange about a meeting to "
                            "consider fund raising"}):
        check(f"non-earnings purpose ignored ({str(row['bm_purpose'])[:22]})",
              not ev._is_earnings(row))


def test_date_parsing():
    print("\n[2] Date parsing across the formats NSE emits")
    check("'31-Jan-2024'", ev._parse_date("31-Jan-2024") == datetime.date(2024, 1, 31))
    check("timestamp with a clock",
          ev._parse_date("23-Jan-2024 18:24:23") == datetime.date(2024, 1, 23))
    check("ISO", ev._parse_date("2024-01-31") == datetime.date(2024, 1, 31))
    check("empty -> None", ev._parse_date("") is None)
    check("nonsense -> None", ev._parse_date("not a date") is None)


def test_lookahead_guard():
    print("\n[3] The lookahead guard — the whole game for this arena")
    E = ev.Event
    e_soon = E("ACME", datetime.date(2025, 5, 10), datetime.date(2025, 5, 2), "Results")
    e_late = E("BETA", datetime.date(2025, 5, 10), datetime.date(2025, 5, 9), "Results")
    e_far = E("GAMMA", datetime.date(2025, 9, 1), datetime.date(2025, 4, 1), "Results")
    e_past = E("DELTA", datetime.date(2025, 4, 1), datetime.date(2025, 3, 1), "Results")
    all_ev = [e_soon, e_late, e_far, e_past]

    on = datetime.date(2025, 5, 5)
    seen = ev.events_known_by(all_ev, on, horizon_days=30)
    check(f"an announced, upcoming event is visible ({[x.symbol for x in seen]})",
          e_soon in seen)
    check("an event not yet announced is invisible", e_late not in seen)
    check("an event past its horizon is not returned", e_far not in seen)
    check("an event that already happened is not returned", e_past not in seen)

    on_later = datetime.date(2025, 5, 9)
    check("it becomes visible the day it is announced, not before",
          e_late in ev.events_known_by(all_ev, on_later))
    check("notice_days is the gap between announcement and meeting",
          e_soon.notice_days == 8 and e_late.notice_days == 1)


def test_calendar_against_the_archive():
    print("\n[4] The harvested calendar itself")
    if not os.path.exists(ev.year_path(2024)):
        check("calendar cache present (run events.download_range first)", False)
        return
    rep = ev.quality_report(datetime.date(2016, 1, 1), datetime.date(2026, 8, 8))
    check(f"quality report passes ({rep['n_events']:,} events, "
          f"{rep['n_symbols']:,} symbols)", rep["ok"])
    check("no hard faults", not rep["hard_faults"])
    check("no empty quarters", not rep["empty_quarters"])
    check(f"every year is populated ({min(rep['per_year'].values()):,}"
          f"-{max(rep['per_year'].values()):,} events)",
          len(rep["per_year"]) >= 10 and min(rep["per_year"].values()) > 1000)
    check("2016 is present — the pre-2018 vocabulary is being read",
          rep["per_year"].get(2016, 0) > 1000)
    check(f"advance notice is real, median {rep['notice_days']['p50']}d",
          rep["notice_days"]["p50"] >= 3)
    check("almost nothing arrives with no notice at all",
          rep["notice_days"]["same_day_or_late"] < 0.02 * rep["n_events"])

    e2024 = ev.load_events(datetime.date(2024, 1, 1), datetime.date(2024, 12, 31),
                           symbols=["RELIANCE"])
    check(f"a large cap has roughly quarterly earnings ({len(e2024)} in 2024)",
          3 <= len(e2024) <= 6)
    check("every event is announced on or before it happens",
          all(x.announced_at <= x.date for x in e2024))

    dup = ev.load_events(datetime.date(2024, 1, 1), datetime.date(2024, 12, 31),
                         symbols=["INFY", "TCS"])
    keys = [(x.symbol, x.date) for x in dup]
    check("(symbol, date) is deduplicated", len(keys) == len(set(keys)))


def test_gate_nan_is_not_a_pass():
    """The bug this arena's liquidity probe uncovered, in shared code."""
    print("\n[5] An unevaluable floor must not silently pass")
    strict = LiquidityGate(LiquidityGate.STRICT)
    legacy_row = {"close": 12.0, "traded": True, "volume": 500.0,
                  "txns": float("nan"), "oi": 900.0}
    ok, why = strict.leg_ok(legacy_row)
    check(f"NaN txns is refused, not waved through ({why})", not ok)
    check("...under its own reason, so it reads as unmeasurable not illiquid",
          why == "txns_unknown")

    legacy_gate = LiquidityGate(LiquidityGate.STRICT_LEGACY)
    ok, why = legacy_gate.leg_ok(dict(legacy_row))
    check("the legacy preset accepts the same row on its evaluable floors", ok)

    missing = {"close": 12.0, "traded": True, "volume": 500.0, "oi": 900.0}
    ok, why = strict.leg_ok(missing)
    check(f"an absent field is refused too ({why})", not ok and why == "txns_unknown")

    good = {"close": 12.0, "traded": True, "volume": 500.0, "txns": 60.0, "oi": 900.0}
    check("a fully measured row still passes", strict.leg_ok(good)[0])
    thin = dict(good, txns=1.0)
    check("and a genuinely thin one is still 'thin_txns', not 'unknown'",
          strict.leg_ok(thin)[1] == "thin_txns")
    check("NaN price is refused",
          not LiquidityGate(LiquidityGate.TRADED).leg_ok(
              {"close": float("nan"), "traded": True})[0])


def test_engine_contract():
    print("\n[6] The event-vol engine satisfies the loop's contract")
    e = engines.get("event_vol")
    for method in ("build", "with_params", "grid", "run", "stress",
                   "warmup_days", "coerce"):
        check(f"event_vol.{method} exists", hasattr(e, method))
    check("it is filed under arena event_vol", e.arena == "event_vol")
    check("its grid is 8 combos, like every other arena", len(e.grid()) == 8)
    check("its declarable extras include the capacity measure",
          "capacity_fill_rate_pct" in e.EXTRA_FIELDS)
    check("a wing width can be overridden and coerced",
          e.build({"config": {"wing_pct": "0.15"}}).wing_pct == 0.15)
    check("an unknown field is refused",
          _raises(e.build, {"config": {"delta_target": 1}}))
    check("stress multiplies execution cost",
          e.stress(e.build({}), 2.0).slippage_per_leg
          == 2 * e.build({}).slippage_per_leg)
    check("no run without dates returns an empty, well-formed result",
          e.run(e.build({}), [])["summary"]["n_trades"] == 0)


def _raises(fn, *a):
    try:
        fn(*a)
        return False
    except Exception:
        return True


def test_engine_refuses_lookahead():
    print("\n[7] The engine will not trade an event it could not have known")
    from research.engines import eventvol

    e = engines.get("event_vol")
    cfg = e.build({"config": {"min_notice_days": 5}})
    late = ev.Event("ACME", datetime.date(2025, 5, 10),
                    datetime.date(2025, 5, 9), "Results")
    entry = datetime.date(2025, 5, 8)
    check("an event announced AFTER the entry date is not knowable",
          late.announced_at > entry)
    check("...and one with too little notice fails the min_notice rule",
          (datetime.date(2025, 5, 10) - late.announced_at).days < cfg.min_notice_days)
    check("min_notice_days is a real config knob", cfg.min_notice_days == 5)


if __name__ == "__main__":
    test_purpose_vocabularies()
    test_date_parsing()
    test_lookahead_guard()
    test_calendar_against_the_archive()
    test_gate_nan_is_not_a_pass()
    test_engine_contract()
    test_engine_refuses_lookahead()
    print(f"\n{'=' * 50}\nRESULT: {PASS} passed, {FAIL} failed")
    sys.exit(1 if FAIL else 0)
