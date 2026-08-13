"""Small statistics helpers shared by the screen and the verdict.

Deliberately dependency-free and explicit: these numbers decide whether an idea
lives, and a silent library default (ddof, NaN handling, pairwise vs listwise
alignment) is exactly the kind of thing that would go unnoticed for months.
"""
import math
from typing import Dict, Iterable, List, Optional, Tuple


def tstat(values: List[float]) -> Tuple[float, Optional[float]]:
    """(mean, t) for a sample. t is None when it cannot be computed.

    One-sample t against zero, ddof=1. Matches sweep_dte._tstat so a screen and
    a sweep never disagree about the same trade list.
    """
    if not values:
        return 0.0, None
    if len(values) < 2:
        return values[0], None
    m = sum(values) / len(values)
    var = sum((v - m) ** 2 for v in values) / (len(values) - 1)
    sd = math.sqrt(var)
    if sd <= 0:
        return m, None
    return m, m / (sd / math.sqrt(len(values)))


def daily_series(trades: Iterable[Dict], key: str = "exit_date") -> Dict[str, float]:
    """Realised P&L per calendar date, keyed by ISO date string."""
    out: Dict[str, float] = {}
    for t in trades:
        out[t[key]] = out.get(t[key], 0.0) + float(t["net_pnl"])
    return out


def pearson(a: Dict[str, float], b: Dict[str, float]) -> Optional[float]:
    """Correlation of two daily P&L series over the union of their dates.

    Union, not intersection: two strategies that never trade on the same day are
    uncorrelated, and intersecting would either divide by zero or — worse —
    correlate the handful of days they happen to overlap on and call that the
    answer. Missing days are genuine zeros, because a strategy not trading
    earns nothing that day.

    Returns None when fewer than 3 shared days exist or a series is flat.
    """
    dates = sorted(set(a) | set(b))
    if len(dates) < 3:
        return None
    xs = [a.get(d, 0.0) for d in dates]
    ys = [b.get(d, 0.0) for d in dates]
    n = len(dates)
    mx, my = sum(xs) / n, sum(ys) / n
    sxy = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    sxx = sum((x - mx) ** 2 for x in xs)
    syy = sum((y - my) ** 2 for y in ys)
    if sxx <= 0 or syy <= 0:
        return None
    return sxy / math.sqrt(sxx * syy)


def annualised_sharpe(daily: Dict[str, float], equity0: float,
                      all_days: Optional[List[str]] = None) -> float:
    """Sharpe from a daily P&L map, zero-filled across `all_days` when given.

    Zero-filling matters: measuring only on days that traded inflates Sharpe by
    deleting the flat days that are part of the strategy's real return stream.
    """
    if equity0 <= 0:
        return 0.0
    keys = all_days if all_days is not None else sorted(daily)
    series = [daily.get(k, 0.0) / equity0 for k in keys]
    if len(series) < 2:
        return 0.0
    m = sum(series) / len(series)
    var = sum((x - m) ** 2 for x in series) / (len(series) - 1)
    sd = math.sqrt(var)
    return (m / sd) * math.sqrt(252) if sd > 0 else 0.0
