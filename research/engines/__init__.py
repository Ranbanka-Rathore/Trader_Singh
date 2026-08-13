"""Engines — the things a hypothesis can actually be run on.

Phase 2's loop was written against one engine, the option-spread backtester. The
survey needs three more arenas, and they do not share a Config, a universe, or
even an instrument. What they DO share is the contract below, which is all the
loop ever needed:

    build(hypothesis, gate)  -> an engine-specific config object
    with_params(cfg, **kw)   -> the same config with grid parameters applied
    grid()                   -> the in-sample parameter grid for walk-forward
    run(cfg, dates)          -> a result dict

The result dict is the interface that matters, because the screen, the
walk-forward and the promotion evidence all read it:

    trades: [{entry_date, exit_date, net_pnl, friction, ...}]   ISO date strings
    summary: {n_trades, win_rate, total_net_pnl, total_friction,
              expectancy_per_trade, profit_factor, sharpe_annualized,
              max_drawdown, ...}
    skip_reasons: {slug: count}
    liquidity_gate: {preset, pass_rate_pct, ...}

Every engine builds its summary through `summarise()` here, so a profit factor
means the same thing in every arena. An arena that computed its own Sharpe
slightly differently would make the portfolio correlation work in Amendment A
quietly meaningless.
"""
import math
from typing import Any, Dict, List, Optional

_REGISTRY: Dict[str, Any] = {}


def register(engine) -> Any:
    _REGISTRY[engine.name] = engine
    return engine


def get(name: str):
    """Look up an engine, refusing an unknown one by name rather than vaguely."""
    if name not in _REGISTRY:
        _load_builtins()
    if name not in _REGISTRY:
        raise KeyError(
            f"unknown engine '{name}'; available: {sorted(_REGISTRY)}. "
            f"A new arena needs an engine before it can have a hypothesis.")
    return _REGISTRY[name]


def available() -> List[str]:
    _load_builtins()
    return sorted(_REGISTRY)


def _load_builtins() -> None:
    from research.engines import eventvol, options, trend, xsection  # noqa: F401


def coerce_field(cls, name: str, raw: Any) -> Any:
    """Coerce one config override to the type a dataclass declares for it.

    JSON has no tuples and a command line has no types, so `allow_short` arrives
    as the string "false" — which is truthy, and would silently do nothing while
    appearing to. An unknown field is refused by name rather than ignored.
    """
    import dataclasses
    spec = {f.name: f for f in dataclasses.fields(cls)}
    if name not in spec:
        raise KeyError(f"'{name}' is not a {cls.__name__} field. Known: "
                       f"{', '.join(sorted(spec))}")
    declared = str(spec[name].type)
    if "Tuple" in declared or isinstance(getattr(cls(), name, None), tuple):
        parts = (raw.split(",") if isinstance(raw, str) else list(raw))
        parts = [str(p).strip() for p in parts if str(p).strip()]
        if "str" in declared:
            return tuple(p.upper() for p in parts)
        cast = int if "int" in declared else float
        return tuple(cast(p) for p in parts)
    if "bool" in declared:
        if isinstance(raw, bool):
            return raw
        low = str(raw).strip().lower()
        if low not in ("true", "false", "1", "0", "yes", "no"):
            raise KeyError(f"{name}: expected a boolean, got '{raw}'")
        return low in ("true", "1", "yes")
    if "int" in declared:
        return int(raw)
    if "float" in declared:
        return float(raw)
    return raw


def summarise(trades: List[Dict[str, Any]], equity0: float,
              extra: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """The one place a result summary is computed, for every arena.

    Sharpe is annualised from the daily P&L series with non-trading days included
    as zeros. Leaving them out inflates it by deleting the flat days that are
    part of the return stream — the same trap `research.stats` guards.
    """
    pnls = [float(t["net_pnl"]) for t in trades]
    n = len(pnls)
    wins = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p <= 0]
    gw, gl = sum(wins), -sum(losses)

    daily: Dict[str, float] = {}
    for t in trades:
        k = str(t["exit_date"])[:10]
        daily[k] = daily.get(k, 0.0) + float(t["net_pnl"])

    sharpe = 0.0
    if daily and equity0 > 0:
        keys = sorted(daily)
        # zero-fill across the span so flat days count
        import datetime as _dt
        try:
            first = _dt.date.fromisoformat(keys[0])
            last = _dt.date.fromisoformat(keys[-1])
            span = [(first + _dt.timedelta(days=i)).isoformat()
                    for i in range((last - first).days + 1)]
            span = [k for k in span if _dt.date.fromisoformat(k).weekday() < 5]
        except ValueError:
            span = keys
        series = [daily.get(k, 0.0) / equity0 for k in span]
        if len(series) > 1:
            m = sum(series) / len(series)
            var = sum((x - m) ** 2 for x in series) / (len(series) - 1)
            sd = math.sqrt(var)
            if sd > 0:
                sharpe = (m / sd) * math.sqrt(252)

    cum = peak = max_dd = 0.0
    for p in pnls:
        cum += p
        peak = max(peak, cum)
        max_dd = max(max_dd, peak - cum)

    out = {
        "n_trades": n,
        "win_rate": round(len(wins) / n, 3) if n else 0.0,
        "expectancy_per_trade": round(sum(pnls) / n, 2) if n else 0.0,
        "total_net_pnl": round(sum(pnls), 2),
        "total_friction": round(sum(float(t.get("friction", 0.0)) for t in trades), 2),
        "return_pct": round(100 * sum(pnls) / equity0, 2) if equity0 else 0.0,
        "profit_factor": round(gw / gl, 2) if gl > 0 else (float("inf") if gw > 0 else 0.0),
        "sharpe_annualized": round(sharpe, 2),
        "max_drawdown": round(max_dd, 2),
        "max_drawdown_pct": round(100 * max_dd / equity0, 2) if equity0 else 0.0,
        "avg_days_held": (round(sum(float(t.get("days_held", 0)) for t in trades) / n, 1)
                          if n else 0.0),
    }
    # Engine-specific numbers are kept BOTH flat (so engine code reads them
    # naturally) and collected under one key, so the screen can carry them into
    # its report without having to know what any particular arena measures.
    #
    # This exists because a kill criterion once named a capacity threshold that
    # no report contained: the number had to be recomputed by hand to check the
    # verdict. A criterion nobody can see is a criterion that quietly stops
    # being applied.
    out.update(extra or {})
    out["engine_extras"] = dict(extra or {})
    return out
