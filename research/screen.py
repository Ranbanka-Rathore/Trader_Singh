"""Stage 1 — the cheap screen that kills most hypotheses before they cost anything.

A screen is a single pass over the registered window, run under more than one
liquidity gate, judged against Section 4's noise threshold and Section 6's list
of things that do not count as evidence. It can only ever KILL or ADVANCE. It can
never conclude that something works: that requires the out-of-sample stage, and
wiring it so a screen cannot promote is the point of splitting them.

The gate A/B is run automatically rather than left to the operator's memory.
Section 6.6 voids any result whose per-trade edge depends on the fill rule, and
that is the single check that would have killed the ladder two years earlier.
"""
import datetime
import os
import sys
from dataclasses import fields, replace
from typing import Any, Callable, Dict, List, Optional, Tuple

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from backtest import bhavcopy
from backtest.real_backtester import Config, RealBacktester
from research import charter
from research.stats import tstat

# Gates every screen runs regardless of what was registered: "off" is the
# counterfactual that exposes a fill-model artefact, "strict" is the honest end
# of the range. The registered gate is added if it is neither.
AB_GATES = ("off", "strict")


class ScreenError(Exception):
    pass


# ── config assembly ──────────────────────────────────────────────────────────
def coerce(name: str, raw: Any) -> Any:
    """Coerce a registered override to the type Config declares for it.

    JSON has no tuples and the CLI has no types, so `delta_band` would arrive as
    a list and `use_gates` as the string "false" — which is truthy, and would
    silently disable nothing while appearing to. Fail loudly on an unknown key.
    """
    spec = {f.name: f for f in fields(Config)}
    if name not in spec:
        raise ScreenError(
            f"'{name}' is not a Config field. Known fields: "
            f"{', '.join(sorted(spec))}")
    declared = spec[name].type
    text = str(declared)
    if isinstance(raw, str):
        low = raw.strip().lower()
        if "bool" in text:
            if low not in ("true", "false", "1", "0", "yes", "no"):
                raise ScreenError(f"{name}: expected a boolean, got '{raw}'")
            return low in ("true", "1", "yes")
        if "int" in text and "Tuple" not in text:
            return int(raw)
        if "float" in text and "Tuple" not in text:
            return float(raw)
        if "Tuple" in text:
            parts = [p for p in raw.replace("(", "").replace(")", "").split(",") if p.strip()]
            cast = int if "int" in text else float
            return tuple(cast(p) for p in parts)
        return raw
    if "Tuple" in text and isinstance(raw, (list, tuple)):
        cast = int if "int" in text else float
        return tuple(cast(p) for p in raw)
    if "bool" in text:
        return bool(raw)
    if "int" in text and "Tuple" not in text:
        return int(raw)
    if "float" in text and "Tuple" not in text:
        return float(raw)
    return raw


def config_from(h: Dict[str, Any], gate: Optional[str] = None) -> Config:
    """Build a backtest Config from a registered hypothesis."""
    overrides = {k: coerce(k, v) for k, v in (h.get("config") or {}).items()}
    return Config(underlying=h.get("underlying", "NIFTY"),
                  equity0=float(h.get("equity", 1_500_000.0)),
                  liquidity_gate=gate or h.get("gate", "strict"),
                  **overrides)


def trading_dates(start: datetime.date, end: datetime.date) -> List[datetime.date]:
    """Weekdays with a cached bhavcopy, so a hole in the archive is visible."""
    out, d = [], start
    while d <= end:
        if d.weekday() < 5 and os.path.exists(bhavcopy.zip_path(d)):
            out.append(d)
        d += datetime.timedelta(days=1)
    return out


# ── metrics ──────────────────────────────────────────────────────────────────
def metrics(res: Dict[str, Any]) -> Dict[str, Any]:
    """Flatten one backtest result into the numbers the charter judges."""
    s = res["summary"]
    pnls = [t["net_pnl"] for t in res["trades"]]
    mean, t = tstat(pnls)
    pf = s["profit_factor"]
    return {
        "n_trades": s["n_trades"],
        "net_pnl": s["total_net_pnl"],
        "expectancy": round(mean, 2),
        "profit_factor": (999.0 if pf == float("inf") else pf),
        "win_rate": s["win_rate"],
        "sharpe": s["sharpe_annualized"],
        "max_drawdown": s["max_drawdown"],
        "t": round(t, 3) if t is not None else None,
        "fill_pass_rate_pct": res["liquidity_gate"]["pass_rate_pct"],
        "top_skip": next(iter(res["skip_reasons"]), "-"),
        # Whatever the arena measures about itself — capacity fill, rebalance
        # count, panel health. Carried through so a kill criterion phrased in
        # those terms can be checked against the report instead of recomputed.
        "extras": dict(s.get("engine_extras") or {}),
    }


def by_era(trades: List[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    """Split a trade list by the liquidity era it was entered in.

    Never report a pooled number across an era boundary without this beside it.
    The share of NIFTY option legs that trade rises from ~10% to ~63% across the
    archive, so a pooled result is an average over three different markets.
    """
    buckets: Dict[str, List[Dict[str, Any]]] = {}
    for t in trades:
        era = charter.era_of(datetime.date.fromisoformat(t["entry_date"][:10]))
        buckets.setdefault(era or "unknown", []).append(t)
    out = {}
    for era, ts in buckets.items():
        pnls = [x["net_pnl"] for x in ts]
        mean, tt = tstat(pnls)
        wins = [p for p in pnls if p > 0]
        losses = [p for p in pnls if p <= 0]
        gl = -sum(losses)
        out[era] = {
            "n_trades": len(ts),
            "net_pnl": round(sum(pnls), 2),
            "expectancy": round(mean, 2),
            "win_rate": round(len(wins) / len(ts), 3) if ts else 0.0,
            "profit_factor": round(sum(wins) / gl, 2) if gl > 0 else (999.0 if wins else 0.0),
            "t": round(tt, 3) if tt is not None else None,
        }
    order = [e for e in ("early", "ramp", "modern", "unknown") if e in out]
    return {e: out[e] for e in order}


# ── the screen ───────────────────────────────────────────────────────────────
def engine_for(h: Dict[str, Any]):
    """The engine a hypothesis names, refusing an unknown one by name."""
    from research import engines
    try:
        return engines.get(h.get("engine") or "real_backtester")
    except KeyError as exc:
        raise ScreenError(str(exc))


def run_gates(h: Dict[str, Any], dates: List[datetime.date],
              provider: Optional[Callable] = None) -> Dict[str, Dict[str, Any]]:
    """Run the hypothesis under each gate; returns {gate: {metrics, trades}}."""
    eng = engine_for(h)
    gates = list(dict.fromkeys(list(AB_GATES) + [h.get("gate", "strict")]))
    out = {}
    for g in gates:
        res = eng.run(eng.build(h, gate=g), dates, provider)
        out[g] = {"metrics": metrics(res), "trades": res["trades"],
                  "skip_reasons": res["skip_reasons"],
                  "gate_detail": res["liquidity_gate"]}
    return out


def run_sweep(h: Dict[str, Any], dates: List[datetime.date],
              provider: Optional[Callable] = None) -> List[Dict[str, Any]]:
    """Run a one-axis sweep declared at registration, in declared order.

    Order is preserved, not sorted by P&L: Section 6.7 is about the SHAPE of the
    surface, and a ranked table invites reading the top row as the answer.
    """
    eng = engine_for(h)
    sweep = h["sweep"]
    axis, values = sweep["axis"], sweep["values"]
    rows = []
    for v in values:
        cfg = eng.with_params(eng.build(h), **{axis: v})
        res = eng.run(cfg, dates, provider)
        m = metrics(res)
        m[axis] = v
        rows.append(m)
    return rows


def plateau_check(rows: List[Dict[str, Any]], axis: str) -> Tuple[bool, str]:
    """Section 6.7 — is the best cell part of a shape, or an isolated spike?"""
    usable = [r for r in rows if r["t"] is not None
              and r["n_trades"] >= charter.MIN_TRADES_FOR_EVIDENCE]
    if not usable:
        return False, "no cell has enough trades to read"
    best = max(usable, key=lambda r: r["t"])
    i = rows.index(best)
    neighbours = [rows[j] for j in (i - 1, i + 1) if 0 <= j < len(rows)]
    agreeing = [n for n in neighbours if n["expectancy"] > 0]
    ok = len(agreeing) >= charter.MIN_AGREEING_NEIGHBOURS
    label = f"{axis}={best[axis]}"
    if ok:
        return True, (f"best cell {label} has {len(agreeing)}/{len(neighbours)} "
                      f"adjacent cells also positive")
    return False, (f"best cell {label} is an isolated spike — "
                   f"0/{len(neighbours)} adjacent cells positive")


def screen(h: Dict[str, Any], dates: List[datetime.date],
           provider: Optional[Callable] = None) -> Dict[str, Any]:
    """Stage 1. Returns a report whose `verdict` is 'kill' or 'advance'.

    `provider` is an optional data-source override, and it is engine-specific —
    a chain loader for the option arena, a futures day-loader for the others.
    Left None, each engine uses its own default, which is what production runs
    do; tests inject synthetic data through it.
    """
    from research import registry

    gate = h.get("gate", "strict")
    runs = run_gates(h, dates, provider)
    primary = runs[gate]["metrics"]
    n_cfg = registry.effective_configs(h)
    threshold = charter.noise_threshold(n_cfg)

    checks: List[Dict[str, Any]] = []

    def check(name: str, passed: bool, detail: str):
        checks.append({"check": name, "passed": bool(passed), "detail": detail})

    # 6.4 — an unsampled tail is not an edge. Zero trades is also an answer:
    # the structure does not fit this market, which is what killed the ladder.
    check("has_trades", primary["n_trades"] > 0,
          f"{primary['n_trades']} trades under gate '{gate}'"
          + ("" if primary["n_trades"] else
             " — the structure never fills here; that is a finding, not a bug"))
    check("enough_trades", primary["n_trades"] >= charter.MIN_TRADES_FOR_EVIDENCE,
          f"{primary['n_trades']} trades vs minimum "
          f"{charter.MIN_TRADES_FOR_EVIDENCE} (charter 6.4)")

    # 6.5 — PF above 3 on a small sample is a fill-model bug until proven otherwise.
    implausible = (primary["profit_factor"] > charter.IMPLAUSIBLE_PF
                   and primary["n_trades"] < charter.IMPLAUSIBLE_PF_BELOW_N)
    check("plausible_profit_factor", not implausible,
          f"PF {primary['profit_factor']} on {primary['n_trades']} trades"
          + (f" — above {charter.IMPLAUSIBLE_PF} below n={charter.IMPLAUSIBLE_PF_BELOW_N}"
             " is not a credit-spread number (charter 6.5)" if implausible else ""))

    # 6.1 / 6.6 — does the fill rule carry the result?
    material, reasons = charter.gate_materiality(runs["off"]["metrics"],
                                                 runs["strict"]["metrics"])
    check("fill_model_stable", not material,
          "; ".join(reasons) if material
          else (f"expectancy {runs['off']['metrics']['expectancy']:+,.0f} (off) -> "
                f"{runs['strict']['metrics']['expectancy']:+,.0f} (strict) — "
                f"the edge does not depend on the fill rule"))

    # Section 4 — the multiple-comparisons bar, raised by every retry.
    t = primary["t"]
    check("clears_noise_threshold", t is not None and t >= threshold,
          f"t = {t if t is not None else 'n/a'} vs threshold {threshold:.2f} "
          f"for {n_cfg} configuration(s)"
          + (f" (includes {n_cfg - int(h.get('n_configs', 1))} carried from "
             f"superseded attempts)" if n_cfg > int(h.get("n_configs", 1)) else ""))

    sweep_rows = None
    if h.get("sweep"):
        sweep_rows = run_sweep(h, dates, provider)
        ok, detail = plateau_check(sweep_rows, h["sweep"]["axis"])
        check("plateau_not_spike", ok, detail + " (charter 6.7)")

    eras = by_era(runs[gate]["trades"])
    spanned = charter.eras_spanned(
        datetime.date.fromisoformat(h["window"][0]),
        datetime.date.fromisoformat(h["window"][1]))

    verdict = "advance" if all(c["passed"] for c in checks) else "kill"
    return {
        "stage": "screen",
        "gate": gate,
        "engine": h.get("engine") or "real_backtester",
        "window": h["window"],
        "trading_days": len(dates),
        "effective_configs": n_cfg,
        "noise_threshold": round(threshold, 3),
        "gates": {g: r["metrics"] for g, r in runs.items()},
        "by_era": eras,
        "eras_spanned": spanned,
        "sweep": sweep_rows,
        "checks": checks,
        "verdict": verdict,
        "failed": [c["check"] for c in checks if not c["passed"]],
        "top_skip_reasons": dict(list(runs[gate]["skip_reasons"].items())[:5]),
    }
