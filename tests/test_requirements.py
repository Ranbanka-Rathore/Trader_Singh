"""Tests for machine-checked kill criteria.

The failure this exists to prevent: a hypothesis registered with "capacity fill
rate below 50%" in its kill text, where nothing in the system ever checked it and
the verdict had to be confirmed by re-running the engine by hand. Every test here
is about a threshold either firing, or being refused for being uncheckable.

Run with:  PYTHONUTF8=1 python tests/test_requirements.py
"""
import datetime
import os
import shutil
import sys
import tempfile

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from research import engines, registry, requirements, screen

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


def test_parsing():
    print("\n[1] Parsing a declared threshold")
    r = requirements.parse("capacity_fill_rate_pct>=50")
    check("field, operator and value are read",
          (r.field, r.op, r.value) == ("capacity_fill_rate_pct", ">=", 50.0))
    check("it round-trips to text for the kill log",
          str(r) == "capacity_fill_rate_pct>=50")
    check("whitespace is tolerated",
          requirements.parse("  n_trades >= 30  ").value == 30.0)
    check("'>=' is not misread as '>'",
          requirements.parse("n_trades>=30").op == ">=")
    check("negatives parse", requirements.parse("expectancy>-100").value == -100.0)
    check("floats parse", requirements.parse("profit_factor>=1.25").value == 1.25)

    for op in (">=", "<=", ">", "<", "==", "!="):
        check(f"operator '{op}' is supported",
              requirements.parse(f"n_trades{op}5").op == op)

    for bad in ("n_trades", "n_trades>=", ">=30", "n_trades =! 3", "n_trades>=abc"):
        ok, _ = raises(requirements.parse, bad)
        check(f"'{bad}' is refused", ok)

    check("the check name encodes the whole requirement",
          requirements.parse("capacity_fill_rate_pct>=50").check_name
          == "require_capacity_fill_rate_pct_ge_50")


def test_field_validation():
    print("\n[2] A threshold on a field that cannot exist is refused")
    xs = engines.get("cross_sectional")
    opt = engines.get("real_backtester")

    ok = requirements.parse_all(["capacity_fill_rate_pct>=50"], xs)
    check("an engine's own field is accepted", len(ok) == 1)
    check("standard metrics are accepted on any engine",
          len(requirements.parse_all(["n_trades>=30", "profit_factor>=1.25"], opt)) == 2)

    did, msg = raises(requirements.parse_all, ["capacity_fill_rate_pct>=50"], opt)
    check(f"...but not on an engine that never reports it ({msg[:40]}...)", did)
    check("...and the message lists what IS available", "Available:" in msg)

    did, msg = raises(requirements.parse_all, ["capacity_fill_rat>=50"], xs)
    check("a misspelt field is refused rather than silently never firing", did)
    check("...which is the whole point", "could never fire" in msg)


def test_metric_fields_match_the_screen():
    print("\n[3] The declarable metric list matches what the screen produces")
    m = screen.metrics({
        "summary": {"n_trades": 5, "total_net_pnl": 1.0, "profit_factor": 1.0,
                    "win_rate": 0.5, "sharpe_annualized": 0.1, "max_drawdown": 2.0},
        "trades": [{"net_pnl": 1.0}, {"net_pnl": -1.0}, {"net_pnl": 2.0}],
        "liquidity_gate": {"pass_rate_pct": 90.0}, "skip_reasons": {}})
    produced = {k for k in m if k not in ("top_skip", "extras")}
    check(f"every declarable field is actually produced "
          f"({sorted(set(requirements.METRIC_FIELDS) - produced) or 'none missing'})",
          set(requirements.METRIC_FIELDS) <= produced)
    check(f"and nothing produced is undeclarable "
          f"({sorted(produced - set(requirements.METRIC_FIELDS)) or 'none extra'})",
          produced <= set(requirements.METRIC_FIELDS))


def test_evaluation():
    print("\n[4] Evaluation, including the cases that must fail closed")
    metrics = {"n_trades": 151, "t": 0.126, "profit_factor": 1.03,
               "max_drawdown": 1_398_363.0,
               "extras": {"capacity_fill_rate_pct": 35.0}}

    def one(text):
        return requirements.evaluate([requirements.parse(text)], metrics)[0]

    c = one("capacity_fill_rate_pct>=50")
    check(f"the real xsect case fails ({c['detail'][:44]}...)", not c["passed"])
    check("...reading the value out of the engine extras", "35" in c["detail"])
    check("...and saying it was declared up front", "registration" in c["detail"])

    check("a satisfied requirement passes", one("n_trades>=30")["passed"])
    check("a drawdown ceiling fires", not one("max_drawdown<=100000")["passed"])
    check("an equality works", one("n_trades==151")["passed"])
    check("a strict inequality is strict", not one("n_trades>151")["passed"])

    absent = one("symbols_traded>=5")
    check("a field the run did not report FAILS, it does not pass",
          not absent["passed"])
    check("...and says it could not be checked", "could not be checked" in absent["detail"])

    undef = requirements.evaluate([requirements.parse("t>=1.0")],
                                  {"n_trades": 1, "t": None})[0]
    check("a None value (t on a 1-trade sample) fails closed", not undef["passed"])

    check("no requirements yields no checks", requirements.evaluate([], metrics) == [])


def test_screen_enforces_them():
    print("\n[5] The screen kills on a declared threshold, end to end")
    tmp = tempfile.mkdtemp(prefix="reqs_")
    saved = (registry.KILL_LOG_PATH, registry.SURVIVORS_DIR, registry.RESULTS_DIR)
    registry.KILL_LOG_PATH = os.path.join(tmp, "kill_log.json")
    registry.SURVIVORS_DIR = os.path.join(tmp, "survivors")
    registry.RESULTS_DIR = os.path.join(tmp, "results")
    try:
        from tests.test_futures_arenas import _bar, _loader, _monthly_expiry, _weekdays

        dates = _weekdays(datetime.date(2023, 1, 2), 400)
        days = {}
        for i, d in enumerate(dates):
            e = _monthly_expiry(d)
            days[d] = {f"S{k:02d}": {e: _bar(d, f"S{k:02d}", e,
                                             100.0 * (1.0 + (k - 6) * 0.0006) ** i,
                                             volume=100000.0, lot=500)}
                       for k in range(12)}

        cfg = {"mom_lookback": 120, "adv_lookback": 10, "min_adv_rs": 0.0,
               "n_per_side": 4, "rebalance_days": 30}
        h = registry.register(
            hid="req-impossible", arena="cross_sectional", engine="cross_sectional",
            claim="c", kill_criterion="k", window=["2023-01-01", "2024-12-31"],
            gate="strict", n_configs=1, config=cfg,
            # unreachable by construction: a fill rate cannot exceed 100%, so
            # this isolates the requirement as the cause of the kill
            requires=["capacity_fill_rate_pct>=101"])
        rep = screen.screen(h, dates, provider=_loader(days))

        names = [c["check"] for c in rep["checks"]]
        check("the declared requirement appears as a check",
              "require_capacity_fill_rate_pct_ge_101" in names)
        check("...and it killed the hypothesis",
              rep["verdict"] == "kill"
              and "require_capacity_fill_rate_pct_ge_101" in rep["failed"])
        check("the report records what was declared",
              rep["requires"] == ["capacity_fill_rate_pct>=101"])
        charter_checks = [c for c in rep["checks"] if not c["check"].startswith("require_")]
        check("charter checks are listed first, before the per-hypothesis ones",
              names.index("has_trades") < names.index("require_capacity_fill_rate_pct_ge_101")
              and len(charter_checks) >= 5)

        h2 = registry.register(
            hid="req-satisfiable", arena="cross_sectional", engine="cross_sectional",
            claim="c", kill_criterion="k", window=["2023-01-01", "2024-12-31"],
            gate="strict", n_configs=1, config=dict(cfg, n_per_side=3),
            requires=["capacity_fill_rate_pct>=1", "n_trades>=1"])
        rep2 = screen.screen(h2, dates, provider=_loader(days))
        req_checks = [c for c in rep2["checks"] if c["check"].startswith("require_")]
        check(f"satisfiable thresholds pass ({len(req_checks)} of them)",
              req_checks and all(c["passed"] for c in req_checks))
    finally:
        registry.KILL_LOG_PATH, registry.SURVIVORS_DIR, registry.RESULTS_DIR = saved
        shutil.rmtree(tmp, ignore_errors=True)


def test_requirements_are_fingerprinted():
    print("\n[6] A threshold cannot be loosened after the result is seen")
    tmp = tempfile.mkdtemp(prefix="reqs_fp_")
    saved = (registry.KILL_LOG_PATH, registry.SURVIVORS_DIR, registry.RESULTS_DIR)
    registry.KILL_LOG_PATH = os.path.join(tmp, "kill_log.json")
    registry.SURVIVORS_DIR = os.path.join(tmp, "survivors")
    registry.RESULTS_DIR = os.path.join(tmp, "results")
    try:
        registry.register(hid="fp", arena="cross_sectional",
                          engine="cross_sectional", claim="c", kill_criterion="k",
                          window=["2023-01-01", "2024-12-31"], n_configs=1,
                          requires=["capacity_fill_rate_pct>=50"])
        check("it runs while untouched",
              registry.open_for_running("fp")["id"] == "fp")

        log = registry.load()
        for x in log["hypotheses"]:
            if x["id"] == "fp":
                x["requires"] = ["capacity_fill_rate_pct>=10"]   # loosened
        registry.save(log)

        did, msg = raises(registry.open_for_running, "fp")
        check(f"loosening the threshold is caught ({msg[:40]}...)", did)
        check("...as no longer matching its registration",
              "no longer matches" in msg)

        log = registry.load()
        for x in log["hypotheses"]:
            if x["id"] == "fp":
                x["requires"] = ["capacity_fill_rate_pct>=50"]   # put it back
        registry.save(log)
        check("restoring it makes the hypothesis runnable again",
              registry.open_for_running("fp")["id"] == "fp")

        # two hypotheses differing ONLY by a requirement are different hypotheses
        registry.register(hid="fp2", arena="cross_sectional",
                          engine="cross_sectional", claim="c", kill_criterion="k",
                          window=["2023-01-01", "2024-12-31"], n_configs=1,
                          requires=["capacity_fill_rate_pct>=60"])
        check("a different threshold is a different fingerprint",
              registry.get("fp")["fingerprint"] != registry.get("fp2")["fingerprint"])
    finally:
        registry.KILL_LOG_PATH, registry.SURVIVORS_DIR, registry.RESULTS_DIR = saved
        shutil.rmtree(tmp, ignore_errors=True)


def test_cli():
    print("\n[7] The command line refuses uncheckable thresholds")
    from research import loop

    tmp = tempfile.mkdtemp(prefix="reqs_cli_")
    saved = (registry.KILL_LOG_PATH, registry.SURVIVORS_DIR, registry.RESULTS_DIR)
    registry.KILL_LOG_PATH = os.path.join(tmp, "kill_log.json")
    registry.SURVIVORS_DIR = os.path.join(tmp, "survivors")
    registry.RESULTS_DIR = os.path.join(tmp, "results")
    try:
        rc = loop.main(["register", "--id", "c1", "--arena", "cross_sectional",
                        "--engine", "cross_sectional", "--era", "modern",
                        "--require", "capacity_fill_rate_pct>=50",
                        "--require", "n_trades>=30",
                        "--claim", "momentum survives capacity",
                        "--kill", "either threshold fails"])
        check(f"two thresholds register (rc={rc})", rc == 0)
        check("...and are stored on the hypothesis",
              registry.get("c1")["requires"]
              == ["capacity_fill_rate_pct>=50", "n_trades>=30"])

        rc = loop.main(["register", "--id", "c2", "--arena", "index_structures",
                        "--engine", "real_backtester", "--era", "modern",
                        "--require", "capacity_fill_rate_pct>=50",
                        "--claim", "x", "--kill", "y"])
        check(f"a field the option engine cannot report is refused (rc={rc})", rc == 2)
        check("...and nothing was registered", registry.get("c2") is None)

        rc = loop.main(["register", "--id", "c3", "--arena", "cross_sectional",
                        "--engine", "cross_sectional", "--era", "modern",
                        "--require", "not a requirement",
                        "--claim", "x", "--kill", "y"])
        check(f"a malformed threshold is refused (rc={rc})", rc == 2)

        rc = loop.main(["register", "--id", "c4", "--arena", "cross_sectional",
                        "--engine", "cross_sectional", "--era", "modern",
                        "--claim", "no thresholds is still fine",
                        "--kill", "the charter checks alone"])
        check("declaring none is still allowed — the charter checks always apply",
              rc == 0 and registry.get("c4")["requires"] == [])
    finally:
        registry.KILL_LOG_PATH, registry.SURVIVORS_DIR, registry.RESULTS_DIR = saved
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    test_parsing()
    test_field_validation()
    test_metric_fields_match_the_screen()
    test_evaluation()
    test_screen_enforces_them()
    test_requirements_are_fingerprinted()
    test_cli()
    print(f"\n{'=' * 50}\nRESULT: {PASS} passed, {FAIL} failed")
    sys.exit(1 if FAIL else 0)
