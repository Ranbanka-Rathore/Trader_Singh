"""Run one backtest config under several liquidity gates and diff the results.

THE QUESTION THIS ANSWERS
-------------------------
The ladder was blessed by a walk-forward reporting OOS profit factor 7.08 over 29
trades. PF 7.08 is not a number credit spreads produce (real ones run ~1.1-1.5),
and on 2026-07-30 fifteen of seventeen LIVE ledger rows were purged as synthetic
because they had been priced off books that did not exist. If the backtester was
filling the same way — and it was, substituting settlement prices for contracts
that never traded — then the 7.08 measures the substitution, not an edge.

This runner holds the strategy fixed and varies ONLY whether a fill has to be
plausible, so the drop from "off" to "traded" is a direct read on how much of the
reported performance was manufactured by the fill model.

    python -m backtest.run_liquidity_ab --start 2024-01-01 --end 2025-12-31
    python -m backtest.run_liquidity_ab --ladder --gates off,traded,strict

Read the output as: how much survives? A strategy whose edge evaporates when it
can only trade contracts that traded did not have an edge.
"""
import argparse
import datetime
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dataclasses import replace

from backtest import bhavcopy
from backtest.real_backtester import Config, RealBacktester


def _trading_dates(start: datetime.date, end: datetime.date):
    """Dates with a cached bhavcopy, so a missing archive is visible up front."""
    dates, d = [], start
    while d <= end:
        if d.weekday() < 5 and os.path.exists(bhavcopy.zip_path(d)):
            dates.append(d)
        d += datetime.timedelta(days=1)
    return dates


def _fmt(v, width=12, dp=2):
    if v is None:
        return "-".rjust(width)
    if isinstance(v, float):
        return f"{v:,.{dp}f}".rjust(width)
    return str(v).rjust(width)


def run(dates, base: Config, gates):
    out = {}
    for g in gates:
        cfg = replace(base, liquidity_gate=g)
        bt = RealBacktester(cfg)
        res = bt.run(dates)
        out[g] = res
        s, lg = res["summary"], res["liquidity_gate"]
        print(f"  [{g:7s}] {s['n_trades']:4d} trades | "
              f"net {s['total_net_pnl']:>12,.2f} | PF {s['profit_factor']:>6.2f} | "
              f"legs fillable {lg['pass_rate_pct']:5.1f}%")
    return out


def report(results, gates, equity0):
    print("\n" + "=" * 78)
    print("LIQUIDITY-GATED BACKTEST — what survives a plausible fill rule")
    print("=" * 78)
    rows = [("metric", *gates)]
    def _g(g, k, d=0.0):
        return results[g]["summary"].get(k, d)
    for label, key, dp in (("trades", "n_trades", 0),
                           ("net P&L", "total_net_pnl", 2),
                           ("profit factor", "profit_factor", 2),
                           ("win rate", "win_rate", 3),
                           ("expectancy/trade", "expectancy_per_trade", 2),
                           ("friction paid", "total_friction", 2),
                           ("return %", "return_pct", 2),
                           ("max drawdown", "max_drawdown", 2),
                           ("sharpe", "sharpe_annualized", 2)):
        rows.append((label, *[_g(g, key) for g in gates]))

    w = 14
    print(f"{'metric':<18}" + "".join(str(g).rjust(w) for g in gates))
    print("-" * (18 + w * len(gates)))
    for label, *vals in rows[1:]:
        dp = 0 if label == "trades" else (3 if label == "win rate" else 2)
        print(f"{label:<18}" + "".join(_fmt(v, w, dp) for v in vals))

    print("\nfill plausibility")
    print("-" * (18 + w * len(gates)))
    for label, key in (("legs checked", "legs_checked"),
                       ("legs fillable", "legs_fillable"),
                       ("pass rate %", "pass_rate_pct")):
        print(f"{label:<18}" + "".join(
            _fmt(results[g]["liquidity_gate"][key], w,
                 1 if "%" in label else 0) for g in gates))

    # the headline: how much of the ungated result was fill-model artefact
    if "off" in results and len(gates) > 1:
        base_pnl = _g("off", "total_net_pnl")
        base_pf = _g("off", "profit_factor")
        print("\nverdict vs ungated ('off')")
        print("-" * 78)
        for g in gates:
            if g == "off":
                continue
            pnl, pf = _g(g, "total_net_pnl"), _g(g, "profit_factor")
            keep = (100.0 * pnl / base_pnl) if base_pnl else float("nan")
            print(f"  {g:8s}: net P&L {pnl:>12,.2f} "
                  f"({keep:6.1f}% of ungated)  PF {pf:.2f} vs {base_pf:.2f}")
        print("\n  A large drop means the reported edge was produced by filling on")
        print("  contracts that never traded — the backtest twin of the synthetic")
        print("  ledger rows purged on 2026-07-30, not a tradeable edge.")

    # refusal breakdown for the strictest gate actually run
    strict = [g for g in gates if g != "off"]
    if strict:
        g = strict[-1]
        ref = results[g]["liquidity_gate"]["refusals"]
        if ref:
            print(f"\nwhy legs were refused under '{g}'")
            print("-" * 78)
            for k, v in list(ref.items())[:8]:
                print(f"  {k:<24} {v:>8,}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", default="2024-01-01")
    ap.add_argument("--end", default="2025-12-31")
    ap.add_argument("--gates", default="off,traded,strict",
                    help="comma-separated liquidity_gate presets")
    ap.add_argument("--ladder", action="store_true",
                    help="ladder structure (30-45 DTE, managed at 21)")
    ap.add_argument("--equity", type=float, default=1_500_000.0)
    ap.add_argument("--json", default="", help="write full results here")
    a = ap.parse_args()

    start = datetime.date.fromisoformat(a.start)
    end = datetime.date.fromisoformat(a.end)
    gates = [g.strip() for g in a.gates.split(",") if g.strip()]

    dates = _trading_dates(start, end)
    if not dates:
        print(f"No cached bhavcopy between {start} and {end}.\n"
              f"Fetch it first:  python -m backtest.bhavcopy "
              f"--start {start} --end {end}")
        return 1
    print(f"{len(dates)} trading days cached, {dates[0]} -> {dates[-1]}")

    base = Config(equity0=a.equity)
    if a.ladder:
        base = replace(base, ladder_mode=True, dte_max=45, time_stop_days=21,
                       min_days_to_expiry=30)
    print(f"structure: {'LADDER 30-45 DTE' if a.ladder else 'weekly 5-8 DTE'}, "
          f"equity Rs {a.equity:,.0f}\n")

    results = run(dates, base, gates)
    report(results, gates, a.equity)

    if a.json:
        with open(a.json, "w", encoding="utf-8") as f:
            json.dump({g: {k: v for k, v in r.items() if k != "trades"}
                       for g, r in results.items()}, f, indent=2, default=str)
        print(f"\nfull results -> {a.json}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
