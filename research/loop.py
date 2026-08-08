"""The research loop — one command from hypothesis to verdict.

    python -m research.loop register --id ... --arena ... --claim ... --kill ...
    python -m research.loop run <id> [<id> ...]
    python -m research.loop list
    python -m research.loop show <id>
    python -m research.loop throughput

and, once something survives, the Section 5 promotion ladder:

    python -m research.loop promote <id> --structure ladder --covers BULL_PUT_SPREAD
    python -m research.loop check-paper --structure ladder
    python -m research.loop promote-live --structure ladder
    python -m research.loop promotions | revoke --structure ... --reason ...

STAGES
------
  1. SCREEN         one pass over the window under gate off/strict, judged
                    against Section 4's noise bar and Section 6's evidence rules.
                    Can only kill or advance — never promote.
  2. WALK-FORWARD   anchored IS/OOS folds with parameters frozen per fold, plus
                    the Monte Carlo suite. Only reached by an advancing screen.
  3. VERDICT        killed or survived, written to the kill log with its numbers
                    so nothing is ever silently retested.

Surviving means "candidate for paper trading", never "deploy". A survived verdict
is the only thing that can open the promotion ladder in research/promotion.py,
and reaching `live` still needs a pre-committed paper sample on top of it. Until
then the order router refuses every live entry for that structure.

Run several hypotheses in one invocation when you can: parsing the bhavcopy
archive dominates the cost (~100ms per session file, ~1s for every backtest
after that), and the chain cache is shared across the whole process. Screening
one hypothesis over a three-year window takes about a minute; ten of them takes
about two.
"""
import argparse
import datetime
import json
import os
import sys
from dataclasses import replace
from typing import Any, Dict, List, Optional

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from backtest import walkforward as wf
from research import charter, paper_gate, promotion, registry, screen
from research.stats import daily_series, pearson

# Data sources are the engine's business, not the loop's: the option arena reads
# chains (through walkforward's process-wide cache, so the screen and every fold
# parse each session once between them) while the futures arenas read the same
# bhavcopy as a price panel. Passing one of those to the other would be silently
# wrong, so the loop passes nothing and each engine uses its own.


# ── stage 2: out of sample ───────────────────────────────────────────────────
def walk_forward_stage(h: Dict[str, Any]) -> Dict[str, Any]:
    """Anchored walk-forward + Monte Carlo, judged against Section 5 and Amendment A."""
    start = datetime.date.fromisoformat(h["window"][0])
    end = datetime.date.fromisoformat(h["window"][1])
    eng = screen.engine_for(h)
    base = eng.build(h)

    def runner(cfg, s, e):
        """One fold, with the warmup the engine's signals need before `s`.

        Warmup is asked of the engine, not fixed: a 20-day breakout needs weeks
        of history and a 12-1 momentum rank needs more than a year. Using the
        option engine's 45 days for all of them would silently give the
        cross-sectional arena no signal at all for its first several folds.

        Only trades ENTERED inside the window count, so the warmup can inform
        indicators without leaking result trades into an adjacent fold.
        """
        dates = screen.trading_dates(
            s - datetime.timedelta(days=eng.warmup_days(cfg)), e)
        res = eng.run(cfg, dates)
        return {"trades": [t for t in res["trades"]
                           if t["entry_date"] >= s.isoformat()],
                "skip_reasons": res.get("skip_reasons", {})}

    result = wf.walk_forward(start, end, base, runner=runner, grid=eng.grid(),
                             apply_params=eng.with_params)
    oos = result["oos_metrics"]
    boot = wf.mc_bootstrap_dd(result["oos_trades"])
    stress = wf.mc_cost_stress(result, base, runner=runner,
                               apply_params=eng.with_params, stress=eng.stress)
    jack = wf.mc_jackknife(result["oos_trades"])

    checks: List[Dict[str, Any]] = []

    def check(name: str, passed: Optional[bool], detail: str):
        checks.append({"check": name, "passed": passed, "detail": detail})

    # Amendment A2 puts correlation ahead of P&L: a candidate that moves with an
    # existing survivor adds nothing to the portfolio however good it looks alone.
    # It is listed and reported first for that reason. (It is necessarily
    # COMPUTED after the folds run — there is no return series before then — but
    # it vetoes independently of every P&L check below it.)
    cand_daily = daily_series(result["oos_trades"])
    corrs = {}
    for other_id, other_daily in registry.survivors().items():
        if other_id == h["id"]:
            continue
        r = pearson(cand_daily, other_daily)
        if r is not None:
            corrs[other_id] = round(r, 3)
    worst = max(corrs.values(), default=None)
    if not corrs:
        check("uncorrelated_with_survivors", True,
              "no prior survivors to correlate against — first admission is free")
    else:
        check("uncorrelated_with_survivors", worst <= charter.MAX_PAIRWISE_CORR,
              f"max pairwise correlation {worst} vs {charter.MAX_PAIRWISE_CORR} "
              f"({', '.join(f'{k}={v}' for k, v in sorted(corrs.items()))})")

    # Section 5 — the four acceptance criteria that already existed and that
    # nothing was ever bound to. This is the binding.
    for name, passed in result["acceptance"].items():
        check(f"wf_{name}", passed,
              "n/a — no parameter selection happened in any fold"
              if passed is None else str(passed))

    # Section 5 — cost and luck stress.
    pf_x2 = stress.get("slippage_x2", {}).get("profit_factor", 0.0)
    check("survives_2x_slippage", pf_x2 > 1.0,
          f"PF {pf_x2} at double slippage (needs > 1.0)")
    drop3 = jack.get("drop_best_3", 0.0)
    check("survives_jackknife", drop3 > 0,
          f"Rs {jack.get('total', 0):+,.0f} total -> Rs {drop3:+,.0f} without "
          f"the 3 best trades")

    # Amendment A5 — the individual bar that makes a portfolio reachable.
    check("oos_trades_ge_100", oos["n_trades"] >= charter.MIN_OOS_TRADES,
          f"{oos['n_trades']} OOS trades vs minimum {charter.MIN_OOS_TRADES}")
    check("oos_sharpe_ge_0.8", oos["sharpe"] >= charter.MIN_OOS_SHARPE,
          f"OOS Sharpe {oos['sharpe']} vs minimum {charter.MIN_OOS_SHARPE} "
          f"(prefer {charter.PREFERRED_OOS_SHARPE})")

    # Amendment A3 — advisory, not a kill. A p99 drawdown above budget is a
    # sizing instruction, not a verdict: halve the risk and it halves with it.
    p99 = boot.get("p99", 0.0)
    size_mult = (charter.DRAWDOWN_BUDGET_RS / p99) if p99 > 0 else None
    dd_note = (f"modelled p99 max drawdown Rs {p99:,.0f} vs budget "
               f"Rs {charter.DRAWDOWN_BUDGET_RS:,.0f}")
    if size_mult is not None and size_mult < 1.0:
        dd_note += f" — size at {size_mult:.2f}x to fit, and sign off on the rupee figure"

    hard = [c for c in checks if c["passed"] is not None]
    verdict = "survived" if all(c["passed"] for c in hard) else "killed"
    return {
        "stage": "walk_forward",
        "oos_metrics": oos,
        "wfe": result["wfe"],
        "deflated_sharpe_hurdle": result["deflated_sharpe_hurdle"],
        "profitable_folds": f"{result['profitable_folds']}/{result['active_folds']} active",
        "n_folds": result["n_folds"],
        "mc_bootstrap_dd": boot,
        "mc_cost_stress": stress,
        "mc_jackknife": jack,
        "correlations": corrs,
        "drawdown_note": dd_note,
        "suggested_size_multiplier": (round(size_mult, 2)
                                      if size_mult is not None and size_mult < 1.0 else 1.0),
        "checks": checks,
        "verdict": verdict,
        "failed": [c["check"] for c in checks if c["passed"] is False],
        "oos_daily": {k: round(v, 2) for k, v in cand_daily.items()},
        "folds": result["folds"],
    }


# ── printing ─────────────────────────────────────────────────────────────────
def _mark(passed: Optional[bool]) -> str:
    return "  n/a" if passed is None else ("   ok" if passed else " FAIL")


def print_checks(checks: List[Dict[str, Any]]):
    for c in checks:
        print(f"   [{_mark(c['passed'])}] {c['check']:<32} {c['detail']}")


def print_screen(rep: Dict[str, Any]):
    print(f"\n  STAGE 1 — SCREEN  ({rep['trading_days']} trading days, "
          f"{rep['window'][0]} -> {rep['window'][1]})")
    hdr = (f"    {'gate':<8}{'trades':>8}{'net P&L':>14}{'exp/trade':>12}"
           f"{'PF':>8}{'t':>8}{'fill%':>8}")
    print(hdr)
    for g, m in rep["gates"].items():
        t = f"{m['t']:.2f}" if m["t"] is not None else "-"
        print(f"    {g:<8}{m['n_trades']:>8}{m['net_pnl']:>14,.0f}"
              f"{m['expectancy']:>12,.0f}{m['profit_factor']:>8.2f}{t:>8}"
              f"{m['fill_pass_rate_pct']:>8.1f}")

    primary = rep.get("gate") or next(iter(rep["gates"]), None)
    extras = (rep["gates"].get(primary, {}) or {}).get("extras") or {}
    if extras:
        print(f"\n    what the {rep.get('engine', 'engine')} measured about itself:")
        for k, v in extras.items():
            shown = f"{v:,}" if isinstance(v, (int, float)) else str(v)
            print(f"      {k:<28} {shown}")

    if len(rep["eras_spanned"]) > 1:
        print(f"\n    per era (window spans {', '.join(rep['eras_spanned'])} — "
              f"pooled numbers above average over different markets):")
        for era, m in rep["by_era"].items():
            t = f"{m['t']:.2f}" if m["t"] is not None else "-"
            share = charter.ERAS[era].tradeable_pct if era in charter.ERAS else "?"
            print(f"      {era:<8} ({share:>6} legs tradeable) "
                  f"{m['n_trades']:>4} trades  net {m['net_pnl']:>+12,.0f}  "
                  f"PF {m['profit_factor']:>5.2f}  t {t:>6}")

    if rep.get("sweep"):
        axis = rep["sweep"][0]
        keys = [k for k in axis if k not in
                ("n_trades", "net_pnl", "expectancy", "profit_factor", "win_rate",
                 "sharpe", "max_drawdown", "t", "fill_pass_rate_pct", "top_skip")]
        name = keys[0] if keys else "value"
        print(f"\n    sweep on {name} — in declared order, NOT ranked:")
        for r in rep["sweep"]:
            t = f"{r['t']:.2f}" if r["t"] is not None else "-"
            print(f"      {name}={str(r[name]):<8} {r['n_trades']:>4} trades  "
                  f"net {r['net_pnl']:>+12,.0f}  PF {r['profit_factor']:>5.2f}  t {t:>6}")

    print()
    print_checks(rep["checks"])
    if rep["top_skip_reasons"]:
        top = ", ".join(f"{k}={v}" for k, v in rep["top_skip_reasons"].items())
        print(f"\n    top skip reasons: {top}")


def print_wf(rep: Dict[str, Any]):
    m = rep["oos_metrics"]
    print(f"\n  STAGE 2 — WALK-FORWARD  ({rep['n_folds']} folds, "
          f"{rep['profitable_folds']} profitable)")
    print(f"    OOS: {m['n_trades']} trades  net Rs {m['total_net_pnl']:+,.0f}  "
          f"PF {m['profit_factor']}  Sharpe {m['sharpe']}  WFE {rep['wfe']}")
    print(f"    {rep['drawdown_note']}")
    print()
    print_checks(rep["checks"])


# ── driving one hypothesis ───────────────────────────────────────────────────
def run_one(hid: str) -> str:
    h = registry.open_for_running(hid)
    print("\n" + "=" * 78)
    print(f"HYPOTHESIS {h['id']}  [{h['arena']}]  gate '{h['gate']}'  "
          f"engine '{h.get('engine', 'real_backtester')}'")
    print("=" * 78)
    print(f"  claim: {h['claim']}")
    print(f"  kills it: {h['kill_criterion']}")
    if h.get("supersedes"):
        chain = registry.ancestry(h)
        print(f"  retry of: {' <- '.join(chain)} "
              f"(budget now {registry.effective_configs(h)} configs)")

    start = datetime.date.fromisoformat(h["window"][0])
    end = datetime.date.fromisoformat(h["window"][1])
    dates = screen.trading_dates(start, end)
    if not dates:
        raise screen.ScreenError(
            f"no cached bhavcopy between {start} and {end}; "
            f"run backtest.bhavcopy download_range first")

    scr = screen.screen(h, dates)
    print_screen(scr)

    if scr["verdict"] == "kill":
        registry.add_event(hid, "screen", "kill",
                           {k: scr[k] for k in ("gates", "by_era", "checks",
                                                "failed", "noise_threshold",
                                                "effective_configs")},
                           status="killed")
        _write_report(hid, {"hypothesis": h, "screen": scr})
        print(f"\n  VERDICT: KILLED at screen — {', '.join(scr['failed'])}")
        print("  Closed. Section 7: a hypothesis that fails is closed, not tuned.")
        return "killed"

    print("\n  screen advances — testing out of sample "
          "(this is what makes it worth believing, not the numbers above)")
    wfr = walk_forward_stage(h)
    print_wf(wfr)

    slim = {k: v for k, v in wfr.items() if k not in ("oos_daily", "folds")}
    status = wfr["verdict"]
    registry.add_event(hid, "walk_forward", status, slim, status=status)
    _write_report(hid, {"hypothesis": h, "screen": scr, "walk_forward": wfr})

    if status == "survived":
        registry.record_survivor(hid, wfr["oos_daily"], wfr["oos_metrics"])
        n_surv = len(registry.survivors())
        lo, hi = charter.PORTFOLIO_N_RANGE
        print(f"\n  VERDICT: SURVIVED — candidate for PAPER trading, not live.")
        print(f"  Section 5 still requires a pre-committed 30-trade paper sample "
              f"clearing Amendment A on its own.")
        print(f"  Portfolio: {n_surv} survivor(s) of the {lo}-{hi} needed at "
              f"correlation <= {charter.MAX_PAIRWISE_CORR}.")
    else:
        print(f"\n  VERDICT: KILLED out of sample — {', '.join(wfr['failed'])}")
        print("  Closed. This is the expected outcome (charter Section 9).")
    return status


def _write_report(hid: str, payload: Dict[str, Any]) -> str:
    os.makedirs(registry.RESULTS_DIR, exist_ok=True)
    path = os.path.join(registry.RESULTS_DIR, f"{hid}.json")
    payload["generated"] = datetime.datetime.now().isoformat(timespec="seconds")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=1, default=str)
    return path


# ── CLI ──────────────────────────────────────────────────────────────────────
def cmd_register(a) -> int:
    if a.era and (a.start or a.end):
        raise registry.RegistryError("give --era or --start/--end, not both")
    if a.era:
        s, e = charter.era_window(a.era, cap=datetime.date.today())
    else:
        if not (a.start and a.end):
            raise registry.RegistryError("need --era, or both --start and --end")
        s = datetime.date.fromisoformat(a.start)
        e = datetime.date.fromisoformat(a.end)

    # Overrides are validated against the ENGINE'S config, not the option
    # backtester's — `entry_lookback` is meaningless to one and `delta_target`
    # to the other, and a typo silently ignored is a hypothesis that does not
    # test what it says it tests.
    from research import engines
    try:
        eng = engines.get(a.engine)
    except KeyError as exc:
        raise registry.RegistryError(str(exc))
    if getattr(eng, "arena", None) and eng.arena != a.arena:
        # A trend engine filed under index_structures would make the arena kill
        # rules in Section 7 meaningless — closing an arena has to actually close
        # what was tested in it.
        raise registry.RegistryError(
            f"engine '{a.engine}' belongs to arena '{eng.arena}', but this was "
            f"registered under '{a.arena}'. Section 7 closes arenas, so the "
            f"label has to match what actually ran.")

    config = {}
    for kv in a.set or []:
        if "=" not in kv:
            raise registry.RegistryError(f"--set expects key=value, got '{kv}'")
        k, v = kv.split("=", 1)
        try:
            config[k.strip()] = eng.coerce(k.strip(), v)
        except (KeyError, ValueError, screen.ScreenError) as exc:
            raise registry.RegistryError(
                f"--set {kv}: {str(exc).strip(chr(39))}")

    sweep = None
    n_configs = a.configs
    if a.sweep:
        if "=" not in a.sweep:
            raise registry.RegistryError("--sweep expects axis=v1,v2,v3")
        axis, vals = a.sweep.split("=", 1)
        try:
            values = [eng.coerce(axis.strip(), v) for v in vals.split(",") if v.strip()]
        except (KeyError, ValueError, screen.ScreenError) as exc:
            raise registry.RegistryError(f"--sweep {a.sweep}: {exc}")
        sweep = {"axis": axis.strip(), "values": values}
        # The budget is the size of the search unless the operator declares a
        # larger one; understating it is how a sweep becomes a "finding".
        n_configs = max(n_configs or 0, len(values))

    h = registry.register(
        hid=a.id, arena=a.arena, claim=a.claim, kill_criterion=a.kill,
        window=[s.isoformat(), e.isoformat()], gate=a.gate,
        n_configs=n_configs or 1, config=config, sweep=sweep,
        underlying=a.underlying, equity=a.equity, era=a.era,
        engine=a.engine, supersedes=a.supersedes, note=a.note or "")
    print(f"registered '{h['id']}' [{h['arena']}] engine '{h['engine']}'  "
          f"{h['window'][0]} -> {h['window'][1]}")
    print(f"  gate '{h['gate']}'  budget {registry.effective_configs(h)} config(s) "
          f"-> noise threshold t >= "
          f"{charter.noise_threshold(registry.effective_configs(h)):.2f}")
    print(f"  claim: {h['claim']}")
    print(f"  kills it: {h['kill_criterion']}")
    if h["config"]:
        print(f"  config: {h['config']}")
    print(f"  fingerprint {h['fingerprint']}")
    print(f"\nrun it with:  python -m research.loop run {h['id']}")
    return 0


def cmd_run(a) -> int:
    statuses = []
    for hid in a.ids:
        try:
            statuses.append(run_one(hid))
        except (registry.RegistryError, screen.ScreenError) as exc:
            print(f"\n  REFUSED {hid}: {exc}")
            statuses.append("refused")
    if len(statuses) > 1:
        print("\n" + "=" * 78)
        print("BATCH: " + ", ".join(f"{i}={s}" for i, s in zip(a.ids, statuses)))
    tp = registry.throughput()
    print(f"\n  throughput: {tp['closed']}/{tp['registered']} closed in "
          f"{tp['weeks']} weeks = {tp['per_week']}/week   "
          f"| {tp['days_to_stop']} days to the Section 7 review")
    return 0 if all(s != "refused" for s in statuses) else 1


def cmd_list(a) -> int:
    hs = registry.all_hypotheses()
    if not hs:
        print("kill log is empty — nothing has been registered yet.")
        return 0
    print(f"{'id':<28}{'arena':<20}{'status':<11}{'window':<25}claim")
    print("-" * 120)
    for h in hs:
        win = f"{h['window'][0]}..{h['window'][1]}"
        claim = h["claim"][:44] + ("..." if len(h["claim"]) > 44 else "")
        print(f"{h['id']:<28}{h['arena']:<20}{h['status']:<11}{win:<25}{claim}")
    tp = registry.throughput()
    print(f"\n{tp['registered']} registered | {tp.get('killed', 0)} killed | "
          f"{tp.get('survived', 0)} survived | {tp['open']} open")
    return 0


def cmd_show(a) -> int:
    h = registry.require(a.id)
    print(json.dumps(h, indent=1, default=str))
    return 0


def cmd_promote(a) -> int:
    """research -> paper. The only door, and it needs a survived verdict."""
    h = registry.require(a.hypothesis)
    if h["status"] != "survived":
        raise promotion.PromotionError(
            f"'{a.hypothesis}' is {h['status']}, not survived. Section 5 gives no "
            f"path to paper trading that skips the walk-forward gate — the ladder "
            f"failed all four criteria and traded anyway, which is what this "
            f"refusal exists to prevent.")
    ev = next((e for e in reversed(h.get("events", []))
               if e["stage"] == "walk_forward"), None)
    if ev is None:
        raise promotion.PromotionError(
            f"'{a.hypothesis}' is marked survived but carries no walk-forward "
            f"evidence. Re-run it rather than promoting on a status field.")

    covers = [c.strip().upper() for c in a.covers.split(",") if c.strip()]
    entry = promotion.promote_to_paper(a.structure, a.hypothesis, covers,
                                       ev["detail"], review_days=a.review_days)
    m = (ev["detail"].get("oos_metrics") or {})
    print(f"'{a.structure}' -> stage PAPER on hypothesis {a.hypothesis}")
    print(f"  evidence: {m.get('n_trades')} OOS trades, PF {m.get('profit_factor')}, "
          f"Sharpe {m.get('sharpe')}, expectancy Rs {m.get('expectancy'):,}")
    print(f"  covers: {', '.join(covers)}")
    print(f"  review by: {entry['review_by']}")
    print(f"\nLIVE entries remain REFUSED. Section 5 now requires "
          f"{paper_gate.MIN_PAPER_TRADES} paper trades entered from here on;")
    print(f"check progress with:  python -m research.loop check-paper "
          f"--structure {a.structure}")
    return 0


def _sample_for(structure: str, mode: str):
    """(record, report) for a structure's post-promotion trade sample."""
    import asyncio

    # Same Windows fix the services carry: psycopg cannot drive a
    # ProactorEventLoop, which is what asyncio.run picks by default here.
    if os.name == "nt":
        import warnings
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", DeprecationWarning)
            asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

    rec = promotion.record(structure)
    if rec is None:
        raise promotion.PromotionError(
            f"'{structure}' has no promotion record — nothing to sample against.")
    since = datetime.datetime.fromisoformat(rec["promoted_at"])
    try:
        trades = asyncio.run(paper_gate.load_trades(rec.get("covers") or [], since,
                                                    mode=mode))
    except Exception as exc:
        # A ledger that cannot be read is not a sample that passed. Say so as a
        # refusal rather than a stack trace: this command is part of the path to
        # real money, and "it errored" must never be mistaken for "it cleared".
        raise promotion.PromotionError(
            f"cannot read the {mode} ledger, so the sample cannot be judged: "
            f"{type(exc).__name__}: {str(exc).splitlines()[0][:160]}\n"
            f"Nothing was promoted or revoked. Fix the database and re-run.")
    return rec, paper_gate.evaluate(trades, paper_gate.modelled_from(rec))


def cmd_check_paper(a) -> int:
    """Judge the sample; on a live strategy, enforce A3's shutdown rule."""
    rec, rep = _sample_for(a.structure, mode=a.mode)
    print(f"\n  {a.mode} SAMPLE for '{a.structure}' since {rec['promoted_at'][:10]} "
          f"(stage '{rec.get('stage')}')")
    print(f"    {rep['n_trades']} trades  net Rs {rep['total_pnl']:+,.0f}  "
          f"expectancy Rs {rep['expectancy']:+,.0f}  PF {rep['profit_factor']}  "
          f"max DD Rs {rep['max_drawdown']:,.0f}")
    print()
    print_checks(rep["checks"])

    breached = any(c["check"] == "drawdown_within_model" and not c["passed"]
                   for c in rep["checks"])
    if breached and rec.get("stage") == promotion.LIVE and not a.dry_run:
        # Amendment A3, pre-committed: a breach of the modelled p99 stops the
        # system. Enforcing it here rather than asking is the point — the whole
        # value of the rule is that it was agreed before it hurt.
        promotion.revoke(a.structure,
                         f"realised drawdown Rs {rep['max_drawdown']:,.0f} breached "
                         f"modelled p99 Rs {rep['modelled_dd_p99']:,.0f}")
        print(f"\n  REVOKED '{a.structure}': drawdown model falsified. LIVE entries "
              f"stop now; open positions are unaffected and still manageable.")
        return 1

    if rep["verdict"] == "pass":
        print(f"\n  Sample PASSES. Promote with:  python -m research.loop "
              f"promote-live --structure {a.structure}")
    else:
        print(f"\n  Sample not yet clear: {', '.join(rep['failed'])}")
    return 0


def cmd_promote_live(a) -> int:
    """paper -> live. Real money from here."""
    rec, rep = _sample_for(a.structure, mode="PAPER")
    print_checks(rep["checks"])
    if rep["verdict"] != "pass":
        raise promotion.PromotionError(
            f"the paper sample does not clear: {', '.join(rep['failed'])}. "
            f"Section 5 requires it independently of the backtest.")
    entry = promotion.promote_to_live(a.structure, rep)
    mult = rep["suggested_size_multiplier"]
    print(f"\n  '{a.structure}' -> stage LIVE. Real orders are now permitted for "
          f"{', '.join(entry['covers'])}.")
    print(f"  Modelled p99 drawdown Rs {rep['modelled_dd_p99']:,.0f} against a "
          f"Rs {charter.DRAWDOWN_BUDGET_RS:,.0f} budget"
          + (f" — start at {mult:.2f}x size." if mult < 1.0 else "."))
    print(f"  Amendment A3: start at Rs 20-30k of risk and escalate on realised "
          f"milestones, not on confidence.")
    print(f"  Re-run check-paper --mode LIVE regularly; a p99 breach revokes this.")
    return 0


def cmd_promotions(a) -> int:
    recs = promotion.all_records()
    if not recs:
        print("nothing promoted. Every structure is at stage 'research' — "
              "LIVE entries are refused for all of them.")
        return 0
    print(f"{'structure':<14}{'stage':<10}{'hypothesis':<26}{'review by':<13}covers")
    print("-" * 96)
    for p in recs:
        flag = " (REVOKED)" if p.get("revoked") else ""
        print(f"{p['structure']:<14}{p.get('stage', '?') + flag:<10}"
              f"{str(p.get('hypothesis_id')):<26}{str(p.get('review_by')):<13}"
              f"{', '.join(p.get('covers') or [])}")
    print(f"\nactive structure right now: '{promotion.active_structure()}' "
          f"at stage '{promotion.stage(promotion.active_structure())}'")
    return 0


def cmd_revoke(a) -> int:
    promotion.revoke(a.structure, a.reason)
    print(f"'{a.structure}' revoked: {a.reason}")
    print("LIVE entries stop immediately. Open positions still exit normally.")
    return 0


def cmd_throughput(a) -> int:
    tp = registry.throughput()
    if not tp["registered"]:
        print("nothing registered yet.")
        return 0
    print(f"  registered      {tp['registered']}")
    print(f"  killed          {tp.get('killed', 0)}")
    print(f"  survived        {tp.get('survived', 0)}")
    print(f"  open            {tp['open']}")
    print(f"  since           {tp['first_registered']}  ({tp['weeks']} weeks)")
    print(f"  CLOSED PER WEEK {tp['per_week']}   <- the metric for this phase")
    print(f"  days to Section 7 review ({charter.PROJECT_STOP_DATE}): "
          f"{tp['days_to_stop']}")
    surv = registry.survivors()
    lo, hi = charter.PORTFOLIO_N_RANGE
    print(f"  portfolio       {len(surv)} survivor(s) of {lo}-{hi} needed")
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="research.loop", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)

    r = sub.add_parser("register", help="write a hypothesis to the kill log")
    r.add_argument("--id", required=True, help="permanent kebab-case slug")
    r.add_argument("--arena", required=True, choices=sorted(charter.ARENAS))
    r.add_argument("--claim", required=True,
                   help="the falsifiable prediction, in one sentence")
    r.add_argument("--kill", required=True,
                   help="the result that closes this hypothesis")
    r.add_argument("--era", choices=sorted(charter.ERAS), default=None,
                   help=f"liquidity era to test in (default window; "
                        f"'{charter.DEFAULT_ERA}' is the current regime)")
    r.add_argument("--start", default=None)
    r.add_argument("--end", default=None)
    r.add_argument("--engine", default="real_backtester",
                   help="arena engine to run this on; 'python -m research.loop "
                        "list' shows what is registered. An arena with no engine "
                        "cannot have a hypothesis (see research/ARENAS.md)")
    r.add_argument("--gate", default="strict", choices=("traded", "strict", "desk"))
    r.add_argument("--configs", type=int, default=1,
                   help="how many configurations this hypothesis tries; sets the "
                        "Section 4 noise bar. Understating it is cheating.")
    r.add_argument("--set", action="append", metavar="FIELD=VALUE",
                   help="Config override, repeatable")
    r.add_argument("--sweep", default=None, metavar="AXIS=V1,V2,V3",
                   help="one-axis sweep; enables the Section 6.7 plateau check")
    r.add_argument("--underlying", default="NIFTY")
    r.add_argument("--equity", type=float, default=1_500_000.0)
    r.add_argument("--supersedes", default=None,
                   help="id of the closed hypothesis this one retries")
    r.add_argument("--note", default="")
    r.set_defaults(fn=cmd_register)

    x = sub.add_parser("run", help="screen, walk forward, and record a verdict")
    x.add_argument("ids", nargs="+")
    x.set_defaults(fn=cmd_run)

    sub.add_parser("list", help="every hypothesis and its status").set_defaults(fn=cmd_list)

    s = sub.add_parser("show", help="full record for one hypothesis")
    s.add_argument("id")
    s.set_defaults(fn=cmd_show)

    sub.add_parser("throughput", help="hypotheses closed per week").set_defaults(fn=cmd_throughput)

    # ── the promotion gate (charter Section 5) ───────────────────────────────
    p = sub.add_parser("promote", help="research -> paper, from a survived hypothesis")
    p.add_argument("hypothesis")
    p.add_argument("--structure", required=True,
                   help="live structure this licenses, e.g. 'ladder' or 'sniper'")
    p.add_argument("--covers", required=True,
                   help="comma-separated strategy types the evidence covers, "
                        "e.g. BULL_PUT_SPREAD,BEAR_CALL_SPREAD")
    p.add_argument("--review-days", type=int, default=promotion.DEFAULT_REVIEW_DAYS,
                   help="how long the promotion stands before it must be re-earned")
    p.set_defaults(fn=cmd_promote)

    c = sub.add_parser("check-paper",
                       help="judge the post-promotion sample; enforces the A3 "
                            "drawdown shutdown on a live strategy")
    c.add_argument("--structure", required=True)
    c.add_argument("--mode", default="PAPER", choices=("PAPER", "LIVE"))
    c.add_argument("--dry-run", action="store_true",
                   help="report only; do not revoke on a drawdown breach")
    c.set_defaults(fn=cmd_check_paper)

    pl = sub.add_parser("promote-live", help="paper -> live. Real money from here.")
    pl.add_argument("--structure", required=True)
    pl.set_defaults(fn=cmd_promote_live)

    sub.add_parser("promotions", help="what is licensed to trade, and at what stage"
                   ).set_defaults(fn=cmd_promotions)

    rv = sub.add_parser("revoke", help="stop a structure trading live")
    rv.add_argument("--structure", required=True)
    rv.add_argument("--reason", required=True)
    rv.set_defaults(fn=cmd_revoke)

    a = ap.parse_args(argv)
    try:
        return a.fn(a)
    except (registry.RegistryError, screen.ScreenError,
            promotion.PromotionError) as exc:
        print(f"REFUSED: {exc}")
        return 2


if __name__ == "__main__":
    sys.exit(main())
