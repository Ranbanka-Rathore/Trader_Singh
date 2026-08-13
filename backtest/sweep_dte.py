"""Sweep the ladder's entry-DTE band under the strict liquidity gate.

WHAT THIS IS FOR
----------------
The ladder enters at 30-45 DTE and manages out at 21. Live books on 2026-08-07
showed NIFTY is only reliably quotable inside ~3 weeks, and the gated backtest
showed the ladder's apparent edge (PF 3.47) collapses to noise (PF 0.78,
t = -0.60) once legs must have real volume. So: is there ANY DTE band where this
structure both trades and survives a plausible fill rule?

HOW TO READ THE OUTPUT — PLEASE READ THIS
-----------------------------------------
This is a grid search on a single sample. With ~20 configurations, the best-
looking row is expected to look good by chance even if every band is worthless:
at n=20 the highest t-statistic among pure-noise configs runs ~2.2-2.5. So a
t above 2 on the winning row means approximately nothing on its own.

The surface is therefore printed in DTE order, NOT sorted by profit, to make it
harder to read as a ranking. What matters is the SHAPE: a real effect looks like
a broad plateau of adjacent bands behaving similarly. A single spiky band
surrounded by bad ones is a fluke, no matter how good its numbers.

Anything that looks promising must then be confirmed out-of-sample:

    python -m backtest.walkforward --start ... --end ...   (anchored IS/OOS folds,
                                                            deflated Sharpe hurdle,
                                                            MC bootstrap)

Usage:
    python -m backtest.sweep_dte --start 2024-01-01 --end 2026-08-06
    python -m backtest.sweep_dte --gate strict --span 15
"""
import argparse
import datetime
import json
import math
import os
import statistics as st
import sys
from dataclasses import replace
from typing import Any, Dict, List

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from backtest import bhavcopy
from backtest.real_backtester import Config, RealBacktester

# one parse per session file, shared across every config in the sweep
_CHAIN_CACHE: Dict[Any, Any] = {}


def cached_load_chain(d: datetime.date, underlying: str = "NIFTY"):
    key = (d, underlying)
    if key not in _CHAIN_CACHE:
        _CHAIN_CACHE[key] = bhavcopy.load_chain(d, underlying)
    return _CHAIN_CACHE[key]


def trading_dates(start: datetime.date, end: datetime.date) -> List[datetime.date]:
    dates, d = [], start
    while d <= end:
        if d.weekday() < 5 and os.path.exists(bhavcopy.zip_path(d)):
            dates.append(d)
        d += datetime.timedelta(days=1)
    return dates


def _tstat(pnls: List[float]):
    """(mean, t) for per-trade P&L; t is None when it cannot be computed."""
    if len(pnls) < 2:
        return (pnls[0] if pnls else 0.0), None
    m, s = st.mean(pnls), st.stdev(pnls)
    if s <= 0:
        return m, None
    return m, m / (s / math.sqrt(len(pnls)))


def run_one(dates, base: Config, dte_min: int, dte_max: int,
            manage: int, gate: str) -> Dict[str, Any]:
    cfg = replace(base, ladder_mode=True, liquidity_gate=gate,
                  min_days_to_expiry=dte_min, dte_max=dte_max,
                  time_stop_days=manage)
    bt = RealBacktester(cfg, chain_provider=cached_load_chain)
    res = bt.run(dates)
    s = res["summary"]
    pnls = [t["net_pnl"] for t in res["trades"]]
    mean, t = _tstat(pnls)
    return {
        "dte_min": dte_min, "dte_max": dte_max, "manage": manage,
        "n_trades": s["n_trades"], "net_pnl": s["total_net_pnl"],
        "pf": s["profit_factor"], "win_rate": s["win_rate"],
        "expectancy": mean, "t": t,
        "max_dd": s["max_drawdown"], "sharpe": s["sharpe_annualized"],
        "pass_rate": res["liquidity_gate"]["pass_rate_pct"],
        "top_skip": next(iter(res["skip_reasons"]), "-"),
    }


def print_surface(rows: List[Dict[str, Any]], gate: str, min_trades: int):
    print("\n" + "=" * 100)
    print(f"DTE SWEEP under gate '{gate}' — printed in DTE order, NOT ranked")
    print("=" * 100)
    hdr = (f"{'entry band':>12} {'manage':>7} {'trades':>7} {'net P&L':>12} "
           f"{'PF':>6} {'win':>6} {'exp/trade':>10} {'t':>7} {'fill%':>6}  top skip")
    print(hdr)
    print("-" * 100)
    for r in rows:
        band = f"{r['dte_min']}-{r['dte_max']}"
        tt = f"{r['t']:.2f}" if r["t"] is not None else "-"
        thin = "" if r["n_trades"] >= min_trades else "  (thin)"
        print(f"{band:>12} {r['manage']:>7} {r['n_trades']:>7} "
              f"{r['net_pnl']:>12,.0f} {r['pf']:>6.2f} {r['win_rate']:>6.3f} "
              f"{r['expectancy']:>10,.0f} {tt:>7} {r['pass_rate']:>6.1f}  "
              f"{r['top_skip'][:22]}{thin}")


def verdict(rows: List[Dict[str, Any]], min_trades: int, n_cfgs: int):
    usable = [r for r in rows if r["n_trades"] >= min_trades and r["t"] is not None]
    print("\n" + "-" * 100)
    if not usable:
        print(f"No band produced >= {min_trades} trades. The structure does not "
              f"fit this underlying at any DTE tested.")
        return
    # expected max |t| among n_cfgs pure-noise configs, rough normal-order stat
    exp_max_t = math.sqrt(2 * math.log(max(n_cfgs, 2)))
    best = max(usable, key=lambda r: r["t"])
    pos = [r for r in usable if r["t"] > 0]
    print(f"bands with >= {min_trades} trades: {len(usable)} of {len(rows)}")
    print(f"bands with positive expectancy:    {len(pos)}")
    print(f"best t-statistic:                  {best['t']:.2f} at "
          f"{best['dte_min']}-{best['dte_max']} DTE, manage {best['manage']} "
          f"({best['n_trades']} trades)")
    print(f"noise threshold (max |t| expected across {n_cfgs} random configs): "
          f"~{exp_max_t:.2f}")
    if best["t"] < exp_max_t:
        print("\n  => The best band does NOT clear the multiple-comparisons "
              "threshold.\n     Nothing here is distinguishable from luck. Do not "
              "trade any of it.")
    else:
        print("\n  => The best band clears the noise threshold, which makes it "
              "WORTH TESTING,\n     not validated. Confirm out-of-sample before "
              "believing it:\n       python -m backtest.walkforward")
    print("\n  Look at the SHAPE above, not the winner: a real effect is a broad "
          "plateau of\n  adjacent bands behaving alike. An isolated good row "
          "between bad ones is a fluke.")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", default="2024-01-01")
    ap.add_argument("--end", default="2026-08-06")
    ap.add_argument("--gate", default="strict")
    ap.add_argument("--span", type=int, default=10,
                    help="width of each entry band (dte_max = dte_min + span)")
    ap.add_argument("--min-trades", type=int, default=20,
                    help="below this a band is too thin to read")
    ap.add_argument("--equity", type=float, default=1_500_000.0)
    ap.add_argument("--json", default="")
    a = ap.parse_args()

    dates = trading_dates(datetime.date.fromisoformat(a.start),
                          datetime.date.fromisoformat(a.end))
    if not dates:
        print("No cached bhavcopy in that range.")
        return 1
    print(f"{len(dates)} trading days, {dates[0]} -> {dates[-1]}  "
          f"| gate '{a.gate}' | band span {a.span}d")

    base = Config(equity0=a.equity)
    # entry floors from the front weekly out past the current ladder setting;
    # management DTE is kept at roughly half the entry floor (never hold into
    # the gamma half of the option's life), with a floor of 1 day
    plan = []
    for dte_min in (4, 7, 10, 14, 18, 21, 25, 30, 35):
        dte_max = dte_min + a.span
        for manage in sorted({max(1, dte_min // 3), max(1, dte_min // 2)}):
            plan.append((dte_min, dte_max, manage))

    print(f"running {len(plan)} configurations...\n")
    rows = []
    for i, (lo, hi, mg) in enumerate(plan, 1):
        r = run_one(dates, base, lo, hi, mg, a.gate)
        rows.append(r)
        print(f"  [{i:>2}/{len(plan)}] {lo}-{hi} DTE manage {mg:>2}: "
              f"{r['n_trades']:>3} trades, net {r['net_pnl']:>+11,.0f}, "
              f"PF {r['pf']:.2f}")

    print_surface(rows, a.gate, a.min_trades)
    verdict(rows, a.min_trades, len(plan))

    if a.json:
        with open(a.json, "w", encoding="utf-8") as f:
            json.dump(rows, f, indent=2, default=str)
        print(f"\nfull surface -> {a.json}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
