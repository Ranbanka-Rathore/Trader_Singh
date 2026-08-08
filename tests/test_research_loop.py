"""Tests for the research loop — the charter's rules, made mechanical.

These matter more than most tests here. The loop's whole job is to refuse things,
and a guard that silently stops guarding looks exactly like a guard that has
nothing to refuse. Every rule below is one the charter states in prose.

Run with:  PYTHONUTF8=1 python tests/test_research_loop.py
"""
import datetime
import json
import os
import shutil
import sys
import tempfile

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from research import charter, registry, screen
from research.stats import annualised_sharpe, daily_series, pearson, tstat

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
    """(did_raise, message) for a call expected to be refused."""
    try:
        fn(*a, **kw)
        return False, ""
    except Exception as exc:
        return True, str(exc)


# ── isolation ────────────────────────────────────────────────────────────────
_TMP = None
_SAVED = {}


def isolate():
    """Point the registry at a throwaway directory.

    Without this a test run would write to the real kill log, and a hypothesis
    invented by a test would be indistinguishable from one the operator
    registered — which is the one kind of corruption this package must not have.
    """
    global _TMP
    _TMP = tempfile.mkdtemp(prefix="research_loop_test_")
    for attr in ("KILL_LOG_PATH", "SURVIVORS_DIR", "RESULTS_DIR"):
        _SAVED[attr] = getattr(registry, attr)
    registry.KILL_LOG_PATH = os.path.join(_TMP, "kill_log.json")
    registry.SURVIVORS_DIR = os.path.join(_TMP, "survivors")
    registry.RESULTS_DIR = os.path.join(_TMP, "results")


def restore():
    for attr, val in _SAVED.items():
        setattr(registry, attr, val)
    if _TMP and os.path.isdir(_TMP):
        shutil.rmtree(_TMP, ignore_errors=True)


_SEQ = [0]


def _reg(hid="h1", **kw):
    """Register a throwaway hypothesis.

    Each one gets a distinct `delta_target` unless the caller supplies a config,
    because two registrations with identical configs are refused once either is
    closed — that guard is the subject of its own test, and it should not fire
    by accident in every other one. Values stay inside Config.delta_band so a
    default fixture is still runnable.
    """
    kw.setdefault("arena", "index_structures")
    kw.setdefault("claim", "a claim that can be wrong")
    kw.setdefault("kill_criterion", "expectancy <= 0 under the strict gate")
    kw.setdefault("window", ["2023-01-01", "2024-12-31"])
    if "config" not in kw:
        _SEQ[0] += 1
        kw["config"] = {"delta_target": round(0.150 + 0.001 * _SEQ[0], 3)}
    return registry.register(hid=hid, **kw)


# ── charter ──────────────────────────────────────────────────────────────────
def test_noise_threshold():
    print("\n[1] Section 4 noise threshold matches the charter's own table")
    # the table in Section 4: N=5 -> 1.79, N=10 -> 2.15, N=20 -> 2.45, N=50 -> 2.80
    for n, want in ((5, 1.79), (10, 2.15), (20, 2.45), (50, 2.80)):
        got = charter.noise_threshold(n)
        check(f"N={n} -> {got:.2f} (charter says {want})", abs(got - want) < 0.01)
    check("N=1 does not divide by zero or return 0",
          charter.noise_threshold(1) > 0)
    check("threshold rises with N",
          charter.noise_threshold(50) > charter.noise_threshold(5))


def test_eras():
    print("\n[2] Liquidity eras (Amendment B3)")
    check("2016-06-01 is early", charter.era_of(datetime.date(2016, 6, 1)) == "early")
    check("2019-12-31 is early", charter.era_of(datetime.date(2019, 12, 31)) == "early")
    check("2020-01-01 is ramp", charter.era_of(datetime.date(2020, 1, 1)) == "ramp")
    check("2022-12-31 is ramp", charter.era_of(datetime.date(2022, 12, 31)) == "ramp")
    check("2023-01-01 is modern", charter.era_of(datetime.date(2023, 1, 1)) == "modern")
    check("2026-08-08 is modern", charter.era_of(datetime.date(2026, 8, 8)) == "modern")
    check("2015 predates the archive", charter.era_of(datetime.date(2015, 1, 1)) is None)

    span = charter.eras_spanned(datetime.date(2019, 6, 1), datetime.date(2023, 6, 1))
    check(f"2019-06 -> 2023-06 spans all three {span}",
          span == ["early", "ramp", "modern"])
    check("a window inside one era spans one",
          charter.eras_spanned(datetime.date(2024, 1, 1),
                               datetime.date(2025, 1, 1)) == ["modern"])
    s, e = charter.era_window("modern", cap=datetime.date(2026, 8, 8))
    check(f"era_window clips to the cap ({s} -> {e})",
          s == datetime.date(2023, 1, 1) and e == datetime.date(2026, 8, 8))
    check("unknown era raises", raises(charter.era_window, "nope")[0])
    check("default era is the current regime", charter.DEFAULT_ERA == "modern")


def test_gate_materiality():
    print("\n[3] Section 6.6 / Amendment B1 — does the fill rule carry the result?")
    # the ladder: +Rs 1,93,464 over 82 trades off, -Rs 12,703 over 59 strict
    ladder_off = {"expectancy": 2359.0, "profit_factor": 3.47}
    ladder_strict = {"expectancy": -215.0, "profit_factor": 0.78}
    material, why = charter.gate_materiality(ladder_off, ladder_strict)
    check(f"the ladder is material ({why[0] if why else '-'})", material)
    check("...because expectancy flips sign",
          any("sign flips" in w for w in why))

    # fewer trades at the same per-trade edge is the gate working, not an artefact
    ok_off = {"expectancy": 900.0, "profit_factor": 1.40}
    ok_strict = {"expectancy": 860.0, "profit_factor": 1.38}
    material, why = charter.gate_materiality(ok_off, ok_strict)
    check(f"same edge, fewer trades -> not material ({why})", not material)

    drift_off = {"expectancy": 1000.0, "profit_factor": 1.40}
    drift_strict = {"expectancy": 400.0, "profit_factor": 1.30}
    material, why = charter.gate_materiality(drift_off, drift_strict)
    check("a 60% expectancy drop is material", material)

    pf_off = {"expectancy": 500.0, "profit_factor": 1.60}
    pf_strict = {"expectancy": 480.0, "profit_factor": 1.10}
    material, why = charter.gate_materiality(pf_off, pf_strict)
    check("PF crossing the 1.25 promotion bar is material",
          material and any("crosses" in w for w in why))


def test_portfolio_math():
    print("\n[4] Amendment A2 — portfolio Sharpe and its ceiling")
    # A2's table: individual 1.0 at rho 0.10 needs N=6 for portfolio 2.0
    s = charter.portfolio_sharpe(1.0, 6, 0.10)
    check(f"S=1.0, N=6, rho=0.10 -> {s:.2f} (A2 says ~2.0)", abs(s - 2.0) < 0.05)
    s9 = charter.portfolio_sharpe(1.0, 9, 0.15)
    check(f"S=1.0, N=9, rho=0.15 -> {s9:.2f} (A2 says ~2.0)", abs(s9 - 2.0) < 0.06)
    ceil = charter.sharpe_ceiling(0.5, 0.10)
    check(f"S=0.5 at rho=0.10 ceilings at {ceil:.2f} — under 2.0 at any N",
          abs(ceil - 1.58) < 0.02 and ceil < 2.0)
    check("stacking Sharpe-0.5 forever never reaches the target",
          charter.portfolio_sharpe(0.5, 10_000, 0.10) < charter.WORTH_IT_PORTFOLIO_SHARPE)
    check("individual bar is 0.8", charter.MIN_OOS_SHARPE == 0.8)
    check("OOS trade minimum is 100", charter.MIN_OOS_TRADES == 100)
    check("drawdown budget is Rs 1,00,000", charter.DRAWDOWN_BUDGET_RS == 100_000.0)


# ── stats ────────────────────────────────────────────────────────────────────
def test_stats():
    print("\n[5] Statistics helpers")
    m, t = tstat([100.0] * 10)
    check("a constant sample has no t (zero variance)", t is None and m == 100.0)
    m, t = tstat([])
    check("empty sample is (0, None)", m == 0.0 and t is None)
    m, t = tstat([1.0])
    check("n=1 has no t", t is None)
    m, t = tstat([10.0, -5.0, 7.0, 3.0, -2.0])
    check(f"t computed for a real sample (mean {m:.1f}, t {t:.2f})",
          t is not None and abs(m - 2.6) < 1e-9)

    a = {"2024-01-01": 100.0, "2024-01-02": -50.0, "2024-01-03": 20.0}
    check("identical series correlate at 1.0", abs(pearson(a, a) - 1.0) < 1e-9)
    anti = {k: -v for k, v in a.items()}
    check("mirrored series correlate at -1.0", abs(pearson(a, anti) + 1.0) < 1e-9)
    b = {"2024-02-01": 10.0, "2024-02-02": -5.0, "2024-02-03": 8.0}
    r = pearson(a, b)
    check(f"non-overlapping series are near zero, not undefined ({r:.3f})",
          r is not None and abs(r) < 0.6)
    check("too few days -> None", pearson({"2024-01-01": 1.0}, {"2024-01-01": 1.0}) is None)
    check("a flat series -> None",
          pearson(a, {k: 0.0 for k in a}) is None)

    trades = [{"exit_date": "2024-01-02", "net_pnl": 100.0},
              {"exit_date": "2024-01-02", "net_pnl": 50.0},
              {"exit_date": "2024-01-05", "net_pnl": -30.0}]
    ds = daily_series(trades)
    check("same-day trades aggregate", ds["2024-01-02"] == 150.0)

    # Zero-filling is the difference between a real Sharpe and a flattering one:
    # measuring only on days that traded deletes the flat days that are part of
    # the return stream, and the same trades then look far better than they are.
    daily = {"2024-01-02": 1000.0, "2024-01-08": -400.0, "2024-01-15": 800.0}
    dense = annualised_sharpe(daily, 1_000_000.0)
    sparse = annualised_sharpe(daily, 1_000_000.0,
                               all_days=[f"2024-01-{i:02d}" for i in range(1, 21)])
    check(f"zero-filled Sharpe ({sparse:.2f}) is lower than trade-days-only "
          f"({dense:.2f})", 0.0 < sparse < dense)


# ── registry ─────────────────────────────────────────────────────────────────
def test_registration_rules():
    print("\n[6] Registration refuses what the charter forbids")
    h = _reg("baseline")
    check("a well-formed hypothesis registers", h["status"] == "registered")
    check("it gets a fingerprint", h["fingerprint"].startswith("sha256:"))

    ok, msg = raises(_reg, "baseline")
    check(f"duplicate id refused ({msg[:38]}...)", ok)
    ok, msg = raises(_reg, "bad-arena", arena="crypto_moonshots")
    check("unknown arena refused (Section 8 lists them)", ok and "arena" in msg)
    ok, msg = raises(_reg, "no-claim", claim="   ")
    check("empty claim refused", ok)
    ok, msg = raises(_reg, "no-kill", kill_criterion="")
    check(f"missing kill criterion refused ({msg[:40]}...)", ok)
    ok, msg = raises(_reg, "gate-off", gate="off")
    check("gate 'off' refused at registration (Section 6.1)",
          ok and "6.1" in msg)


def test_closed_means_closed():
    print("\n[7] Section 7 — a hypothesis that fails is closed, not tuned")
    twin_cfg = {"delta_target": 0.19}
    _reg("dead-idea", config=twin_cfg)
    registry.add_event("dead-idea", "screen", "kill", {"why": "test"}, status="killed")

    ok, msg = raises(registry.open_for_running, "dead-idea")
    check(f"a killed hypothesis cannot be re-run ({msg[:44]}...)", ok)
    check("...and the refusal names the supersedes route", "supersedes" in msg)

    ok, msg = raises(registry.open_for_running, "never-registered")
    check("an unregistered id cannot be run at all", ok)
    check("...and says so plainly", "never registered" in msg)

    # the same config under a new name, without acknowledging the retry
    ok, msg = raises(_reg, "dead-idea-v2", config=twin_cfg)
    check(f"a config-identical rename is refused ({msg[:40]}...)", ok)
    check("...and points at the original", "dead-idea" in msg)

    v2 = _reg("dead-idea-v2", config=twin_cfg, supersedes="dead-idea")
    check("the same config IS allowed when declared as a retry",
          v2["supersedes"] == "dead-idea")

    _reg("still-open", config={"delta_target": 0.11})
    ok, msg = raises(_reg, "premature", config={"delta_target": 0.12},
                     supersedes="still-open")
    check("cannot supersede a hypothesis that is still open", ok)


def test_budget_compounds():
    print("\n[8] Amendment B4 — retrying a dead idea raises its own bar")
    _reg("gen1", n_configs=4, config={"delta_target": 0.15})
    registry.add_event("gen1", "screen", "kill", {}, status="killed")
    _reg("gen2", n_configs=4, config={"delta_target": 0.16}, supersedes="gen1")
    registry.add_event("gen2", "screen", "kill", {}, status="killed")
    g3 = _reg("gen3", n_configs=4, config={"delta_target": 0.17}, supersedes="gen2")

    check("ancestry walks the whole chain",
          registry.ancestry(g3) == ["gen2", "gen1"])
    eff = registry.effective_configs(g3)
    check(f"third attempt is priced as 12 configs, not 4 (got {eff})", eff == 12)
    bar_first = charter.noise_threshold(4)
    bar_third = charter.noise_threshold(eff)
    check(f"so the t it must clear rose {bar_first:.2f} -> {bar_third:.2f}",
          bar_third > bar_first)

    lone = _reg("standalone", n_configs=4, config={"delta_target": 0.99})
    check("an unrelated hypothesis is unaffected",
          registry.effective_configs(lone) == 4)


def test_fingerprint_detects_drift():
    print("\n[9] A registration that no longer describes the run is refused")
    _reg("drifter", config={"delta_target": 0.15})
    log = registry.load()
    for h in log["hypotheses"]:
        if h["id"] == "drifter":
            h["config"]["delta_target"] = 0.30   # edited after the fact
    registry.save(log)

    ok, msg = raises(registry.open_for_running, "drifter")
    check(f"changed config is caught ({msg[:44]}...)", ok)
    check("...and the message explains why it matters", "in-sample" in msg)


def test_survivors_and_throughput():
    print("\n[10] Survivors and the throughput metric")
    _reg("winner")
    registry.record_survivor("winner", {"2024-01-02": 500.0, "2024-01-03": -200.0},
                             {"sharpe": 1.1})
    registry.add_event("winner", "walk_forward", "survived", {}, status="survived")
    surv = registry.survivors()
    check("a survivor's daily returns are stored for correlation",
          "winner" in surv and surv["winner"]["2024-01-02"] == 500.0)

    tp = registry.throughput(today=datetime.date.today())
    check(f"throughput counts closed hypotheses ({tp['closed']} of {tp['registered']})",
          tp["closed"] >= 1 and tp["registered"] >= tp["closed"])
    check("per_week is reported", "per_week" in tp)
    check(f"days to the Section 7 review are tracked ({tp['days_to_stop']})",
          isinstance(tp["days_to_stop"], int))


# ── screen ───────────────────────────────────────────────────────────────────
def test_coercion():
    print("\n[11] Config overrides are coerced, not trusted")
    check("'false' becomes False, not a truthy string",
          screen.coerce("use_gates", "false") is False)
    check("'true' becomes True", screen.coerce("ladder_mode", "true") is True)
    check("ints are ints", screen.coerce("dte_max", "45") == 45)
    check("floats are floats", screen.coerce("delta_target", "0.18") == 0.18)
    check("tuples parse from a list", screen.coerce("delta_band", [0.1, 0.3]) == (0.1, 0.3))
    check("tuples parse from a string",
          screen.coerce("width_fallbacks", "4,2") == (4, 2))
    ok, msg = raises(screen.coerce, "not_a_field", "1")
    check(f"an unknown Config field is refused ({msg[:34]}...)", ok)
    ok, _ = raises(screen.coerce, "use_gates", "maybe")
    check("a non-boolean for a bool field is refused", ok)


def test_config_from():
    print("\n[12] Hypothesis -> backtest Config")
    h = {"underlying": "NIFTY", "equity": 1_500_000.0, "gate": "strict",
         "config": {"ladder_mode": "true", "dte_max": "30"}}
    cfg = screen.config_from(h)
    check("gate is applied", cfg.liquidity_gate == "strict")
    check("equity is applied", cfg.equity0 == 1_500_000.0)
    check("overrides are coerced through", cfg.ladder_mode is True and cfg.dte_max == 30)
    cfg_off = screen.config_from(h, gate="off")
    check("the A/B can override the gate", cfg_off.liquidity_gate == "off")


def test_era_split():
    print("\n[13] Results are split by era, never pooled silently")
    trades = ([{"entry_date": "2017-05-02", "net_pnl": 1000.0}] * 3
              + [{"entry_date": "2021-05-04", "net_pnl": -400.0}] * 2
              + [{"entry_date": "2024-05-06", "net_pnl": 250.0}] * 4)
    out = screen.by_era(trades)
    check("three eras present", list(out) == ["early", "ramp", "modern"])
    check("early bucket correct", out["early"]["n_trades"] == 3
          and out["early"]["net_pnl"] == 3000.0)
    check("ramp bucket correct", out["ramp"]["n_trades"] == 2
          and out["ramp"]["net_pnl"] == -800.0)
    check("modern bucket correct", out["modern"]["n_trades"] == 4)
    check("chronological order, not insertion order",
          list(out) == sorted(out, key=lambda e: charter.ERAS[e].start))
    check("an out-of-archive date lands in 'unknown'",
          "unknown" in screen.by_era([{"entry_date": "2014-01-01", "net_pnl": 1.0}]))


def test_plateau():
    print("\n[14] Section 6.7 / B2 — an isolated spike is not a finding")
    def row(v, exp, t, n=40):
        return {"min_days_to_expiry": v, "expectancy": exp, "t": t, "n_trades": n}

    spike = [row(10, -300, -1.2), row(20, 900, 2.9), row(30, -250, -0.9)]
    ok, why = screen.plateau_check(spike, "min_days_to_expiry")
    check(f"a spike between two bad cells fails ({why[:46]}...)", not ok)

    plateau = [row(10, 400, 1.4), row(20, 900, 2.9), row(30, 350, 1.1)]
    ok, why = screen.plateau_check(plateau, "min_days_to_expiry")
    check(f"a plateau passes ({why[:46]}...)", ok)

    edge = [row(10, 900, 2.9), row(20, 500, 1.6), row(30, -200, -0.8)]
    ok, _ = screen.plateau_check(edge, "min_days_to_expiry")
    check("a winner at the edge of the axis judges its one neighbour", ok)

    thin = [row(10, 900, 2.9, n=3), row(20, 800, 2.5, n=4)]
    ok, why = screen.plateau_check(thin, "min_days_to_expiry")
    check(f"cells too thin to read cannot pass ({why[:40]}...)", not ok)


def test_screen_end_to_end():
    print("\n[15] A full screen over synthetic sessions")
    from backend.app.core import bs_math as bs

    def chain(d, expiry, spot, dead=()):
        t = max((expiry - d).days, 0) / 365.0
        opts = {}
        lo, hi = int(spot * 0.90 // 50 * 50), int(spot * 1.10 // 50 * 50)
        for k in range(lo, hi + 50, 50):
            for typ in ("CE", "PE"):
                p = bs.price(spot, float(k), t, 0.13, typ) if t > 0 else (
                    max(spot - k, 0.0) if typ == "CE" else max(k - spot, 0.0))
                live = float(k) not in dead
                opts[(expiry, float(k), typ)] = {
                    "close": round(max(p, 0.05), 2), "traded": live,
                    "oi": 5000.0 if live else 0.0, "chg_oi": 0.0,
                    "volume": 400.0 if live else 0.0,
                    "txns": 60.0 if live else 0.0, "lot": 75}
        return {"date": d, "underlying": "NIFTY", "spot": spot,
                "expiries": [expiry], "options": opts, "futures": {}}

    # every OTM put strike is settlement-only: nothing a gated run may sell
    start = datetime.date(2024, 1, 1)
    chains, d, spot = {}, start, 24000.0
    while d <= datetime.date(2024, 4, 30):
        if d.weekday() < 5:
            expiry = d + datetime.timedelta(days=28)
            dead = {float(k) for k in range(int(spot * 0.90 // 50 * 50), int(spot), 50)}
            chains[d] = chain(d, expiry, spot, dead=dead)
            spot *= 1.0008
        d += datetime.timedelta(days=1)
    dates = sorted(chains)
    provider = lambda dd, underlying="NIFTY": chains.get(dd)

    h = _reg("e2e-screen", window=["2024-01-01", "2024-04-30"], n_configs=1)
    rep = screen.screen(h, dates, provider)

    check(f"the screen returns a verdict ({rep['verdict']})",
          rep["verdict"] in ("kill", "advance"))
    check("both A/B gates ran alongside the registered one",
          {"off", "strict"} <= set(rep["gates"]))
    check("the noise threshold is recorded",
          rep["noise_threshold"] == round(charter.noise_threshold(1), 3))
    check("every charter check is reported with a verdict",
          all("passed" in c and "detail" in c for c in rep["checks"]))
    names = {c["check"] for c in rep["checks"]}
    check(f"the Section 6 checks are all present ({len(names)})",
          {"has_trades", "enough_trades", "plausible_profit_factor",
           "fill_model_stable", "clears_noise_threshold"} <= names)
    check("a dead put wing cannot pass the screen", rep["verdict"] == "kill")
    check("...and the failure is named",
          len(rep["failed"]) > 0 and rep["failed"] == [
              c["check"] for c in rep["checks"] if not c["passed"]])
    check("the ungated run is still reported for comparison",
          rep["gates"]["off"]["n_trades"] >= rep["gates"]["strict"]["n_trades"])
    check("era attribution is recorded", rep["eras_spanned"] == ["modern"])

    # a screen may never promote: the only positive outcome is 'advance'
    check("no screen verdict is ever 'survived'", rep["verdict"] != "survived")


def test_ab_compares_against_the_registered_gate():
    print("\n[16] The fill-model A/B compares 'off' with the gate that was registered")
    from tests.test_futures_arenas import _bar, _loader, _monthly_expiry, _weekdays

    dates = _weekdays(datetime.date(2023, 1, 2), 300)
    days = {}
    for i, d in enumerate(dates):
        e = _monthly_expiry(d)
        days[d] = {f"S{k:02d}": {e: _bar(d, f"S{k:02d}", e,
                                         100.0 * (1.0 + (k - 3) * 0.001) ** i,
                                         volume=50000.0, lot=200)}
                   for k in range(6)}
    cfg = {"mom_lookback": 60, "adv_lookback": 10, "min_adv_rs": 0.0,
           "n_per_side": 2, "rebalance_days": 30}

    for gate in ("traded", "strict"):
        h = _reg(f"ab-{gate}", arena="cross_sectional", engine="cross_sectional",
                 window=["2023-01-01", "2024-12-31"], gate=gate, config=dict(cfg))
        rep = screen.screen(h, dates, provider=_loader(days))
        check(f"gate '{gate}': the run set is off + the registered gate "
              f"({sorted(rep['gates'])})",
              set(rep["gates"]) == {"off", gate})
        detail = [c["detail"] for c in rep["checks"]
                  if c["check"] == "fill_model_stable"][0]
        check(f"...and materiality names it, not a hard-coded 'strict'",
              gate in detail or "flips" in detail or "moves" in detail)

    # The real reason this matters: a pre-2024 window cannot be judged against
    # 'strict', because the legacy schema has no trade count to evaluate.
    from backtest.liquidity_gate import LiquidityGate
    legacy_row = {"close": 10.0, "traded": True, "volume": 400.0,
                  "txns": float("nan"), "oi": 800.0}
    check("strict refuses a legacy row outright",
          not LiquidityGate(LiquidityGate.STRICT).leg_ok(legacy_row)[0])
    check("...so comparing 'off' against it would report every pre-2024 "
          "hypothesis as a fill artefact",
          LiquidityGate(LiquidityGate.STRICT_LEGACY).leg_ok(legacy_row)[0])


def test_report_printing():
    print("\n[17] The report renderer survives every shape it is handed")
    from research import loop
    import io
    import contextlib

    def render(rep):
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            loop.print_screen(rep)
        return buf.getvalue()

    def m(n=30, t=1.5):
        return {"n_trades": n, "net_pnl": 1000.0, "expectancy": 33.0,
                "profit_factor": 1.4, "win_rate": 0.6, "sharpe": 0.9,
                "max_drawdown": 500.0, "t": t, "fill_pass_rate_pct": 71.2,
                "top_skip": "vrp_thin"}

    base = {"trading_days": 400, "window": ["2023-01-01", "2024-12-31"],
            "gate": "strict", "engine": "real_backtester",
            "gates": {"off": m(), "strict": m()}, "by_era": {},
            "eras_spanned": ["modern"], "sweep": None,
            "checks": [{"check": "has_trades", "passed": True, "detail": "30 trades"}],
            "top_skip_reasons": {"vrp_thin": 12}, "noise_threshold": 1.18,
            "effective_configs": 1, "verdict": "advance", "failed": []}
    out = render(base)
    check("a plain screen renders", "STAGE 1" in out and "strict" in out)

    # a t of None must not blow up the %.2f formatting
    none_t = dict(base, gates={"off": m(t=None), "strict": m(t=None)})
    check("a missing t renders as '-'", "-" in render(none_t))

    # the sweep table has to find its own axis column among the metric columns
    swept = dict(base, sweep=[
        dict(m(t=0.5), min_days_to_expiry=4),
        dict(m(t=2.1), min_days_to_expiry=10),
        dict(m(t=0.3), min_days_to_expiry=20)])
    out = render(swept)
    check("a sweep table renders with its axis named",
          "min_days_to_expiry" in out and "NOT ranked" in out)
    check("...and prints every cell", out.count("min_days_to_expiry=") == 3)

    # a multi-era window must show the split, with each era's liquidity beside it
    multi = dict(base, eras_spanned=["ramp", "modern"],
                 by_era={"ramp": {"n_trades": 5, "net_pnl": -100.0,
                                  "expectancy": -20.0, "win_rate": 0.2,
                                  "profit_factor": 0.5, "t": -0.4},
                         "modern": {"n_trades": 25, "net_pnl": 1100.0,
                                    "expectancy": 44.0, "win_rate": 0.7,
                                    "profit_factor": 1.8, "t": 1.9}})
    out = render(multi)
    check("a multi-era window prints the per-era split",
          "per era" in out and "ramp" in out and "modern" in out)
    check("...and warns that the pooled row averages different markets",
          "average over different markets" in out)
    check("...quoting each era's measured liquidity",
          charter.ERAS["modern"].tradeable_pct in out)

    unknown = dict(base, eras_spanned=["early", "modern"],
                   by_era={"unknown": {"n_trades": 1, "net_pnl": 0.0,
                                       "expectancy": 0.0, "win_rate": 0.0,
                                       "profit_factor": 0.0, "t": None}})
    check("an 'unknown' era does not crash the renderer",
          "unknown" in render(unknown))


def test_cli_guards():
    print("\n[18] The command line refuses the same things the API does")
    from research import loop

    check("list works on an empty-ish log", loop.main(["list"]) == 0)
    check("throughput works", loop.main(["throughput"]) == 0)
    rc = loop.main(["run", "no-such-hypothesis"])
    check(f"running an unregistered id is a non-zero exit ({rc})", rc != 0)
    # The arena and the engine must agree — registering a futures claim against
    # the option backtester is refused, so the pairing is named explicitly here.
    rc = loop.main(["register", "--id", "cli-1", "--arena", "futures_trend",
                    "--engine", "futures_trend",
                    "--claim", "trend on NIFTY futures is positive-expectancy",
                    "--kill", "OOS expectancy <= 0", "--era", "modern"])
    check("register via CLI works", rc == 0)
    check("...and it landed in the log", registry.get("cli-1") is not None)
    rc = loop.main(["register", "--id", "cli-1", "--arena", "futures_trend",
                    "--engine", "futures_trend",
                    "--claim", "same id again", "--kill", "x", "--era", "modern"])
    check(f"a duplicate id exits non-zero ({rc})", rc == 2)

    rc = loop.main(["register", "--id", "cli-sweep", "--arena", "index_structures",
                    "--claim", "some DTE band survives", "--kill", "no band clears",
                    "--era", "modern", "--sweep", "min_days_to_expiry=4,10,20,30"])
    h = registry.get("cli-sweep")
    check("a sweep sets the config budget from its own size",
          rc == 0 and h["n_configs"] == 4)
    check("...which raises the noise bar accordingly",
          charter.noise_threshold(h["n_configs"]) > charter.noise_threshold(1))


if __name__ == "__main__":
    isolate()
    try:
        test_noise_threshold()
        test_eras()
        test_gate_materiality()
        test_portfolio_math()
        test_stats()
        test_registration_rules()
        test_closed_means_closed()
        test_budget_compounds()
        test_fingerprint_detects_drift()
        test_survivors_and_throughput()
        test_coercion()
        test_config_from()
        test_era_split()
        test_plateau()
        test_screen_end_to_end()
        test_ab_compares_against_the_registered_gate()
        test_report_printing()
        test_cli_guards()
    finally:
        restore()
    print(f"\n{'=' * 50}\nRESULT: {PASS} passed, {FAIL} failed")
    sys.exit(1 if FAIL else 0)
