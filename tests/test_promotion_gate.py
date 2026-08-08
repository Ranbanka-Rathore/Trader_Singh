"""Tests for the promotion gate — the binding between research and real money.

The ladder failed all four of Section 5's acceptance criteria and traded anyway,
because passing was something a human was supposed to check. Everything here
tests a refusal, and the two that matter most are the ones that must NOT refuse:
paper entries, and exits of any kind.

Run with:  PYTHONUTF8=1 python tests/test_promotion_gate.py
"""
import asyncio
import datetime
import json
import os
import shutil
import sys
import tempfile

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from research import paper_gate, promotion

PASS, FAIL = 0, 0


def check(name, cond):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  ✅ {name}")
    else:
        FAIL += 1
        print(f"  ❌ {name}")


def raises(fn, *a, **kw):
    try:
        fn(*a, **kw)
        return False, ""
    except Exception as exc:
        return True, str(exc)


_TMP = None
_SAVED_PATH = None
_SAVED_ENV = {}


def isolate():
    global _TMP, _SAVED_PATH
    _TMP = tempfile.mkdtemp(prefix="promotion_test_")
    _SAVED_PATH = promotion.PROMOTIONS_PATH
    promotion.PROMOTIONS_PATH = os.path.join(_TMP, "promotions.json")
    for k in ("LADDER_MODE", "TRADING_MODE"):
        _SAVED_ENV[k] = os.environ.get(k)
    os.environ["LADDER_MODE"] = "false"     # -> active structure is 'sniper'


def restore():
    promotion.PROMOTIONS_PATH = _SAVED_PATH
    for k, v in _SAVED_ENV.items():
        if v is None:
            os.environ.pop(k, None)
        else:
            os.environ[k] = v
    if _TMP and os.path.isdir(_TMP):
        shutil.rmtree(_TMP, ignore_errors=True)


def _clear():
    if os.path.exists(promotion.PROMOTIONS_PATH):
        os.remove(promotion.PROMOTIONS_PATH)


EVIDENCE = {"oos_metrics": {"n_trades": 120, "expectancy": 1400.0,
                            "profit_factor": 1.5, "sharpe": 0.95},
            "mc_bootstrap_dd": {"p50": 20000.0, "p95": 45000.0, "p99": 60000.0}}


def _paper(structure="sniper", covers=("BULL_PUT_SPREAD", "BEAR_CALL_SPREAD")):
    return promotion.promote_to_paper(structure, "hyp-1", list(covers), EVIDENCE)


# ── the default is 'no' ──────────────────────────────────────────────────────
def test_default_is_closed():
    print("\n[1] Anything unknown is at 'research' and cannot touch money")
    _clear()
    check("an unpromoted structure is at stage 'research'",
          promotion.stage("sniper") == promotion.RESEARCH)
    ok, why = promotion.may_enter("BULL_PUT_SPREAD", mode="LIVE", structure="sniper")
    check(f"...and a LIVE entry is refused ({why})", not ok and "unpromoted" in why)
    ok, why = promotion.may_enter("BULL_PUT_SPREAD", mode="LIVE", structure="anything")
    check("a structure nobody has ever heard of is refused too", not ok)


def test_paper_is_never_blocked():
    print("\n[2] PAPER entries are never blocked — paper trading IS the search")
    _clear()
    for st in ("BULL_PUT_SPREAD", "IRON_CONDOR", "SOMETHING_NEW", ""):
        ok, why = promotion.may_enter(st, mode="PAPER", structure="sniper")
        check(f"PAPER entry allowed for '{st or '(blank)'}' ({why})", ok)
    _paper()
    ok, _ = promotion.may_enter("IRON_CONDOR", mode="PAPER", structure="sniper")
    check("an uncovered strategy still paper-trades — that is how it gets evidence", ok)


def test_stage_ladder():
    print("\n[3] research -> paper -> live, and paper still means no money")
    _clear()
    entry = _paper()
    check("promotion from a survived hypothesis lands at 'paper'",
          entry["stage"] == promotion.PAPER)
    ok, why = promotion.may_enter("BULL_PUT_SPREAD", mode="LIVE", structure="sniper")
    check(f"stage 'paper' still refuses LIVE entries ({why})",
          not ok and "stage_paper" in why)

    ok, msg = raises(promotion.promote_to_live, "ladder", {"n_trades": 40})
    check("cannot promote a structure with no record at all", ok)

    promotion.promote_to_live("sniper", {"n_trades": 40})
    ok, why = promotion.may_enter("BULL_PUT_SPREAD", mode="LIVE", structure="sniper")
    check(f"stage 'live' permits it ({why})", ok and why == "promoted")

    check("the transition history is kept",
          [h["to"] for h in promotion.record("sniper")["history"]]
          == [promotion.PAPER, promotion.LIVE])


def test_no_skipping_paper():
    print("\n[4] There is no path from research straight to live")
    _clear()
    ok, msg = raises(promotion.promote_to_live, "sniper", {"n_trades": 99})
    check(f"promote_to_live on an unpromoted structure is refused ({msg[:40]}...)", ok)
    check("...and says there is no such path", "has not passed" in msg)

    _paper()
    promotion.promote_to_live("sniper", {"n_trades": 40})
    ok, msg = raises(promotion.promote_to_live, "sniper", {"n_trades": 41})
    check("promoting an already-live structure again is refused", ok)


def test_coverage():
    print("\n[5] Amendment C5 — a promotion covers named strategy types only")
    _clear()
    _paper(covers=("BULL_PUT_SPREAD",))
    promotion.promote_to_live("sniper", {"n_trades": 40})

    ok, why = promotion.may_enter("BULL_PUT_SPREAD", mode="LIVE", structure="sniper")
    check("the covered strategy trades", ok)
    ok, why = promotion.may_enter("IRON_CONDOR", mode="LIVE", structure="sniper")
    check(f"an uncovered strategy is refused ({why})", not ok and "uncovered" in why)
    check("...naming the strategy that was refused", "IRON_CONDOR" in why)
    ok, msg = raises(promotion.promote_to_paper, "x", "h", [], EVIDENCE)
    check("a promotion with no covers at all is refused", ok)


def test_expiry():
    print("\n[6] Amendment C4 — a promotion is evidence, and evidence has a date")
    _clear()
    _paper()
    promotion.promote_to_live("sniper", {"n_trades": 40})
    rec = promotion.record("sniper")
    review = datetime.date.fromisoformat(rec["review_by"])
    check(f"a review date is set ({review})",
          review > datetime.date.today())

    ok, _ = promotion.may_enter("BULL_PUT_SPREAD", mode="LIVE", structure="sniper",
                                today=review - datetime.timedelta(days=1))
    check("inside the window it trades", ok)
    ok, why = promotion.may_enter("BULL_PUT_SPREAD", mode="LIVE", structure="sniper",
                                  today=review + datetime.timedelta(days=1))
    check(f"past it, LIVE entries are refused ({why})", not ok and "expired" in why)


def test_revoke():
    print("\n[7] Revocation stops entries immediately")
    _clear()
    _paper()
    promotion.promote_to_live("sniper", {"n_trades": 40})
    promotion.revoke("sniper", "drawdown model falsified")

    ok, why = promotion.may_enter("BULL_PUT_SPREAD", mode="LIVE", structure="sniper")
    check(f"a revoked structure cannot enter ({why})", not ok and "revoked" in why)
    check("...and drops back to stage 'research'",
          promotion.stage("sniper") == promotion.RESEARCH)
    ok, _ = promotion.may_enter("BULL_PUT_SPREAD", mode="PAPER", structure="sniper")
    check("but it may still paper trade, to re-earn the stage", ok)
    ok, msg = raises(promotion.promote_to_live, "sniper", {"n_trades": 40})
    check("and cannot be waved straight back to live", ok)


def test_fails_closed():
    print("\n[8] An unreadable store refuses LIVE, it does not wave trades through")
    _clear()
    _paper()
    promotion.promote_to_live("sniper", {"n_trades": 40})
    ok, _ = promotion.may_enter("BULL_PUT_SPREAD", mode="LIVE", structure="sniper")
    check("sanity: it trades while the store is intact", ok)

    with open(promotion.PROMOTIONS_PATH, "w", encoding="utf-8") as f:
        f.write("{ this is not json")
    ok, why = promotion.may_enter("BULL_PUT_SPREAD", mode="LIVE", structure="sniper")
    check(f"a corrupt store refuses LIVE ({why})", not ok)
    check("...and stage() degrades to 'research' rather than raising",
          promotion.stage("sniper") == promotion.RESEARCH)
    ok, _ = promotion.may_enter("BULL_PUT_SPREAD", mode="PAPER", structure="sniper")
    check("PAPER is still allowed — a broken file must not stop the search", ok)
    _clear()


def test_banner():
    print("\n[9] The boot banner states eligibility without naming a strategy")
    _clear()
    line = promotion.gate_banner("LIVE")
    check("an unpromoted structure warns that live entries will be refused",
          "REFUSED" in line)
    check("...and says exits still run", "Exits" in line)

    _paper()
    promotion.promote_to_live("sniper", {"n_trades": 40})
    line = promotion.gate_banner("LIVE")
    check(f"a promoted structure reports LIVE-eligible ({line.strip()[:44]}...)",
          "LIVE-eligible" in line)
    check("...quoting the hypothesis it rests on", "hyp-1" in line)
    check("PAPER mode says entries count toward the sample",
          "sample" in promotion.gate_banner("PAPER"))


# ── the paper sample ─────────────────────────────────────────────────────────
def _trades(n, pnl=1500.0, live=True, start_day=1, sd=2800.0, seed=7):
    """A sample with realistic dispersion around `pnl`.

    sd matters: the charter puts per-trade standard deviation on this instrument
    class at ~Rs 2,800, which is what makes the standard error at n=30 roughly
    Rs 511 and the model-consistency check meaningfully loose. A fixture with
    tidy, low-variance P&L would make every deviation look like falsification.
    Seeded, so the numbers are the same on every run.
    """
    import random
    rng = random.Random(seed)
    return [{"id": i, "exit_date": f"2026-09-{(start_day + i) % 28 + 1:02d}",
             "realized_pnl": round(rng.gauss(pnl, sd), 2),
             "live_priced": live, "strategy_type": "BULL_PUT_SPREAD"}
            for i in range(n)]


def test_paper_sample():
    print("\n[10] Amendment C2 — what a paper sample has to clear")
    modelled = {"expectancy": 1400.0, "dd_p99": 60000.0}

    rep = paper_gate.evaluate(_trades(40), modelled)
    check(f"a clean 40-trade sample passes ({rep['verdict']})",
          rep["verdict"] == "pass")
    check(f"expectancy is reported (Rs {rep['expectancy']:,.0f})",
          rep["expectancy"] > 0)

    rep = paper_gate.evaluate(_trades(29), modelled)
    check("29 trades is not the pre-committed 30",
          rep["verdict"] == "fail" and "sample_size" in rep["failed"])

    rep = paper_gate.evaluate(_trades(40, pnl=-800.0), modelled)
    check("a losing sample fails on expectancy",
          "positive_expectancy" in rep["failed"])

    # one synthetic row fails the whole sample: the pricing guard has a hole
    dirty = _trades(40)
    dirty[7]["live_priced"] = False
    rep = paper_gate.evaluate(dirty, modelled)
    check("a single non-live-priced row fails the sample",
          "ledger_is_live_priced" in rep["failed"])
    check("...and it is not quietly excluded and passed anyway",
          rep["verdict"] == "fail" and rep["n_synthetic"] == 1)
    check("...with the offending id named",
          "7" in [c["detail"] for c in rep["checks"]
                  if c["check"] == "ledger_is_live_priced"][0])


def test_model_falsification():
    print("\n[11] The sample is asked not to contradict the model, not to reprove it")
    # a sample well below the model, but within noise at n=30, is not a failure
    modelled = {"expectancy": 1400.0, "dd_p99": 60000.0}
    rep = paper_gate.evaluate(_trades(40, pnl=1250.0), modelled)
    check(f"modestly under the model still passes (z={rep['adverse_z']})",
          rep["verdict"] == "pass" and rep["adverse_z"] < 0)

    # a sample far below it falsifies the model that earned the promotion
    optimistic = {"expectancy": 25000.0, "dd_p99": 60000.0}
    rep = paper_gate.evaluate(_trades(40), optimistic)
    check(f"far below the model fails (z={rep['adverse_z']})",
          "consistent_with_model" in rep["failed"])
    check("...and beating the model is never a failure",
          paper_gate.evaluate(_trades(40, pnl=5000.0),
                              modelled)["verdict"] == "pass")


def test_drawdown_rule():
    print("\n[12] Amendment A3/C3 — the modelled p99 is a hard line")
    tight = {"expectancy": 1400.0, "dd_p99": 500.0}
    losers = _trades(20, pnl=-3000.0) + _trades(25, pnl=6000.0, start_day=20)
    rep = paper_gate.evaluate(losers, tight)
    check(f"a drawdown past p99 fails (Rs {rep['max_drawdown']:,.0f} vs Rs 500)",
          "drawdown_within_model" in rep["failed"])
    check("...described as falsification, not bad luck",
          any("falsified" in c["detail"] for c in rep["checks"]
              if c["check"] == "drawdown_within_model"))

    rep = paper_gate.evaluate(_trades(40), {"expectancy": 1400.0, "dd_p99": 0.0})
    check("a promotion with no modelled p99 cannot clear the check",
          "drawdown_within_model" in rep["failed"])

    rep = paper_gate.evaluate(_trades(40), {"expectancy": 1400.0, "dd_p99": 250_000.0})
    check(f"a p99 above the Rs 1L budget yields a size multiplier "
          f"({rep['suggested_size_multiplier']}x)",
          rep["suggested_size_multiplier"] < 1.0)


def test_modelled_from():
    print("\n[13] Model numbers come from the stored evidence, not from typing")
    m = paper_gate.modelled_from({"evidence": EVIDENCE})
    check("expectancy is read from the OOS metrics", m["expectancy"] == 1400.0)
    check("p99 is read from the bootstrap", m["dd_p99"] == 60000.0)
    empty = paper_gate.modelled_from({})
    check("a record with no evidence yields zeros, which fail the DD check",
          empty["dd_p99"] == 0.0)


# ── the live binding ─────────────────────────────────────────────────────────
def test_order_router_binding():
    print("\n[14] The order router actually refuses — this is the whole point")
    from backend.app.services.order_router import order_router

    legs = [{"opt_type": "pe", "strike": 24000.0, "side": "SELL", "limit_price": 100.0},
            {"opt_type": "pe", "strike": 23800.0, "side": "BUY", "limit_price": 60.0}]

    def route(intent, mode, strategy="BULL_PUT_SPREAD"):
        os.environ["TRADING_MODE"] = mode
        return asyncio.run(order_router.route_basket(
            None, ticker="NIFTY", strategy_type=strategy, legs=legs,
            lots=1, intent=intent))

    _clear()
    res = route("ENTRY", "LIVE")
    check(f"an unpromoted LIVE entry is refused ({res.get('reason')})",
          res["status"] == "FAILED" and str(res["reason"]).startswith("not_promoted"))
    check("...before any order was placed", res["legs"] == [])

    # An EXIT must never be gated. It will fail later for want of a scrip master
    # in this test, and that is the assertion: it got PAST the promotion gate.
    res = route("EXIT", "LIVE")
    check(f"an EXIT is not gated ({res.get('reason')})",
          not str(res.get("reason", "")).startswith("not_promoted"))
    res = route("UNWIND", "LIVE")
    check("an UNWIND is not gated either",
          not str(res.get("reason", "")).startswith("not_promoted"))

    res = route("ENTRY", "PAPER")
    check(f"a PAPER entry is not gated ({res.get('reason')})",
          not str(res.get("reason", "")).startswith("not_promoted"))

    _paper()
    res = route("ENTRY", "LIVE")
    check("stage 'paper' still refuses the LIVE entry",
          str(res.get("reason", "")).startswith("not_promoted_stage_paper"))

    promotion.promote_to_live("sniper", {"n_trades": 40})
    res = route("ENTRY", "LIVE")
    check(f"once promoted, the gate lets it through ({res.get('reason')})",
          not str(res.get("reason", "")).startswith("not_promoted"))
    res = route("ENTRY", "LIVE", strategy="IRON_CONDOR")
    check("but an uncovered strategy is still refused",
          str(res.get("reason", "")).startswith("not_promoted_uncovered"))
    _clear()
    os.environ["TRADING_MODE"] = "PAPER"


def test_cli_promotion_guards():
    print("\n[15] The CLI will not promote from anything but a survived verdict")
    from research import loop, registry

    saved = (registry.KILL_LOG_PATH, registry.SURVIVORS_DIR, registry.RESULTS_DIR)
    registry.KILL_LOG_PATH = os.path.join(_TMP, "kill_log.json")
    registry.SURVIVORS_DIR = os.path.join(_TMP, "survivors")
    registry.RESULTS_DIR = os.path.join(_TMP, "results")
    _clear()
    try:
        registry.register(hid="open-one", arena="index_structures",
                          claim="a claim", kill_criterion="a kill",
                          window=["2023-01-01", "2024-12-31"],
                          config={"delta_target": 0.15})
        rc = loop.main(["promote", "open-one", "--structure", "sniper",
                        "--covers", "BULL_PUT_SPREAD"])
        check(f"a merely registered hypothesis cannot promote (rc={rc})", rc == 2)
        check("...and nothing was written", promotion.record("sniper") is None)

        registry.register(hid="killed-one", arena="index_structures",
                          claim="a claim", kill_criterion="a kill",
                          window=["2023-01-01", "2024-12-31"],
                          config={"delta_target": 0.16})
        registry.add_event("killed-one", "screen", "kill", {}, status="killed")
        rc = loop.main(["promote", "killed-one", "--structure", "sniper",
                        "--covers", "BULL_PUT_SPREAD"])
        check(f"a killed hypothesis cannot promote (rc={rc})", rc == 2)

        # survived, but with the status set and no evidence behind it
        registry.register(hid="hollow", arena="index_structures",
                          claim="a claim", kill_criterion="a kill",
                          window=["2023-01-01", "2024-12-31"],
                          config={"delta_target": 0.17})
        registry.add_event("hollow", "screen", "advance", {}, status="survived")
        rc = loop.main(["promote", "hollow", "--structure", "sniper",
                        "--covers", "BULL_PUT_SPREAD"])
        check(f"a 'survived' status with no walk-forward evidence is refused (rc={rc})",
              rc == 2)

        registry.register(hid="real-winner", arena="index_structures",
                          claim="a claim", kill_criterion="a kill",
                          window=["2023-01-01", "2024-12-31"],
                          config={"delta_target": 0.18})
        registry.add_event("real-winner", "walk_forward", "survived",
                           EVIDENCE, status="survived")
        rc = loop.main(["promote", "real-winner", "--structure", "sniper",
                        "--covers", "BULL_PUT_SPREAD,BEAR_CALL_SPREAD"])
        check(f"a real survived verdict promotes to paper (rc={rc})", rc == 0)
        check("...at stage 'paper', not live",
              promotion.stage("sniper") == promotion.PAPER)
        check("...carrying its evidence for the paper gate to check against",
              paper_gate.modelled_from(promotion.record("sniper"))["dd_p99"] == 60000.0)

        check("promotions lists it", loop.main(["promotions"]) == 0)
        check("revoke works from the CLI",
              loop.main(["revoke", "--structure", "sniper",
                         "--reason", "testing"]) == 0)
        check("...and it is now revoked",
              promotion.record("sniper")["revoked"] is not None)
    finally:
        registry.KILL_LOG_PATH, registry.SURVIVORS_DIR, registry.RESULTS_DIR = saved
        _clear()


if __name__ == "__main__":
    isolate()
    try:
        test_default_is_closed()
        test_paper_is_never_blocked()
        test_stage_ladder()
        test_no_skipping_paper()
        test_coverage()
        test_expiry()
        test_revoke()
        test_fails_closed()
        test_banner()
        test_paper_sample()
        test_model_falsification()
        test_drawdown_rule()
        test_modelled_from()
        test_order_router_binding()
        test_cli_promotion_guards()
    finally:
        restore()
    print(f"\n{'=' * 50}\nRESULT: {PASS} passed, {FAIL} failed")
    sys.exit(1 if FAIL else 0)
