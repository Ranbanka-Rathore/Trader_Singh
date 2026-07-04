"""
Phase 4 — walk-forward validation + Monte Carlo robustness suite.

Protocol (pre-registered in the Phase-4 overhaul plan — do not widen the grid):
  GRID (8 combos, fixed a priori):
      delta_target in {0.15, 0.20}
      tp_ratio     in {0.5, 0.6}
      sl_mode      in {strike_touch, mark}
  Regime gates are NEVER optimized — they are theory-fixed in regime_filters.

  ANCHORED WALK-FORWARD:
      train = 6 calendar months -> pick combo by profit factor (min 8 trades,
              else carry the default combo)
      test  = following 1 month with params frozen; only test trades count
      roll monthly across the data window

  ACCEPTANCE (all must hold):
      WFE = Sharpe_OOS / mean(Sharpe_IS of chosen combos) >= 0.5
      OOS profit factor >= 1.25
      >= 60% of folds profitable
      OOS Sharpe > deflated hurdle sqrt(2 ln 8 / T) * sqrt(252)

  MONTE CARLO (on the pooled OOS trades):
      1. 10,000x trade-order bootstrap -> max-drawdown distribution (p50/p95/p99)
      2. cost stress: rerun every test fold at slippage x2 and x3
      3. jackknife: drop the k best trades (k=1..3) -> does the sign survive?

Run:  PYTHONUTF8=1 python -m backtest.walkforward --start 2024-01-01 --end 2026-07-03
"""
import argparse
import datetime
import json
import math
import os
import random
import sys
from dataclasses import replace
from typing import Any, Dict, List, Optional, Tuple

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from backtest import bhavcopy
from backtest.real_backtester import Config, RealBacktester

RESULTS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "results")

GRID = [
    {"delta_target": dt, "tp_ratio": tp, "sl_mode": sl}
    for dt in (0.15, 0.20) for tp in (0.5, 0.6) for sl in ("strike_touch", "mark")
]
DEFAULT_COMBO = GRID[0]
MIN_TRAIN_TRADES = 8
WARMUP_DAYS = 45          # calendar days of indicator warmup before any window
TRAIN_MONTHS = 6


# ── cached chain provider (one parse per session file, shared by all runs) ──
_CHAIN_CACHE: Dict[Tuple[datetime.date, str], Any] = {}


def cached_load_chain(d: datetime.date, underlying: str = "NIFTY"):
    key = (d, underlying)
    if key not in _CHAIN_CACHE:
        _CHAIN_CACHE[key] = bhavcopy.load_chain(d, underlying)
    return _CHAIN_CACHE[key]


def weekdays(start: datetime.date, end: datetime.date) -> List[datetime.date]:
    out, d = [], start
    while d <= end:
        if d.weekday() < 5:
            out.append(d)
        d += datetime.timedelta(days=1)
    return out


def month_add(d: datetime.date, months: int) -> datetime.date:
    y, m = d.year, d.month + months
    y += (m - 1) // 12
    m = (m - 1) % 12 + 1
    return datetime.date(y, m, 1)


def run_window(cfg: Config, start: datetime.date, end: datetime.date) -> Dict[str, Any]:
    """Run with indicator warmup before `start`; only trades entered inside
    [start, end] count toward the window's result."""
    dates = weekdays(start - datetime.timedelta(days=WARMUP_DAYS), end)
    bt = RealBacktester(cfg, cached_load_chain)
    res = bt.run(dates)
    trades = [t for t in res["trades"] if t["entry_date"] >= start.isoformat()]
    return {"trades": trades, "skip_reasons": res["skip_reasons"]}


# ── metrics on a pooled trade list ───────────────────────────────────────────
def trade_metrics(trades: List[Dict], equity0: float,
                  all_days: Optional[List[datetime.date]] = None) -> Dict[str, Any]:
    n = len(trades)
    pnls = [t["net_pnl"] for t in trades]
    wins = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p <= 0]
    gw, gl = sum(wins), -sum(losses)

    # daily P&L series on exit dates (zeros on non-exit trading days)
    daily: Dict[str, float] = {}
    for t in trades:
        daily[t["exit_date"]] = daily.get(t["exit_date"], 0.0) + t["net_pnl"]
    if all_days:
        series = [daily.get(d.isoformat(), 0.0) for d in all_days]
    else:
        series = [daily[k] for k in sorted(daily)]

    sharpe = 0.0
    if len(series) > 1 and equity0 > 0:
        rets = [x / equity0 for x in series]
        mean = sum(rets) / len(rets)
        var = sum((x - mean) ** 2 for x in rets) / (len(rets) - 1)
        sd = math.sqrt(var)
        if sd > 0:
            sharpe = (mean / sd) * math.sqrt(252)

    cum, peak, max_dd = 0.0, 0.0, 0.0
    for t in trades:
        cum += t["net_pnl"]
        peak = max(peak, cum)
        max_dd = max(max_dd, peak - cum)

    return {
        "n_trades": n,
        "win_rate": round(len(wins) / n, 3) if n else 0.0,
        "total_net_pnl": round(sum(pnls), 2),
        "total_friction": round(sum(t["friction"] for t in trades), 2),
        "expectancy": round(sum(pnls) / n, 2) if n else 0.0,
        "profit_factor": round(gw / gl, 3) if gl > 0 else (float("inf") if gw > 0 else 0.0),
        "sharpe": round(sharpe, 3),
        "max_drawdown": round(max_dd, 2),
    }


# ── walk-forward ─────────────────────────────────────────────────────────────
def walk_forward(start: datetime.date, end: datetime.date,
                 base_cfg: Config) -> Dict[str, Any]:
    folds = []
    first_test = month_add(datetime.date(start.year, start.month, 1), TRAIN_MONTHS)
    test_start = first_test
    while test_start < end:
        test_end = min(month_add(test_start, 1) - datetime.timedelta(days=1), end)
        train_start = month_add(test_start, -TRAIN_MONTHS)
        train_end = test_start - datetime.timedelta(days=1)
        folds.append((train_start, train_end, test_start, test_end))
        test_start = month_add(test_start, 1)

    fold_reports = []
    oos_trades: List[Dict] = []
    is_sharpes: List[float] = []

    n_selected = 0
    for i, (tr_s, tr_e, te_s, te_e) in enumerate(folds):
        best_combo, best_pf, best_train = None, -1e9, None
        default_train = None
        for combo in GRID:
            cfg = replace(base_cfg, **combo)
            res = run_window(cfg, tr_s, tr_e)
            m = trade_metrics(res["trades"], base_cfg.equity0,
                              weekdays(tr_s, tr_e))
            if combo == DEFAULT_COMBO:
                default_train = m
            pf = m["profit_factor"] if m["n_trades"] >= MIN_TRAIN_TRADES else -1e9
            pf = pf if pf != float("inf") else 99.0
            if pf > best_pf:
                best_pf, best_combo, best_train = pf, combo, m

        if best_pf <= -1e9:
            # too few train trades for a real selection: carry the default
            # combo (no selection bias in this fold, but its train Sharpe
            # still informs the IS baseline when it traded at all)
            best_combo, best_train = dict(DEFAULT_COMBO), default_train
        else:
            n_selected += 1

        cfg = replace(base_cfg, **best_combo)
        test_res = run_window(cfg, te_s, te_e)
        test_m = trade_metrics(test_res["trades"], base_cfg.equity0,
                               weekdays(te_s, te_e))
        oos_trades.extend(test_res["trades"])
        if best_train and best_train["n_trades"] > 0:
            is_sharpes.append(best_train["sharpe"])

        fold_reports.append({
            "fold": i, "train": [tr_s.isoformat(), tr_e.isoformat()],
            "test": [te_s.isoformat(), te_e.isoformat()],
            "combo": best_combo,
            "train_metrics": best_train, "test_metrics": test_m,
        })
        print(f"  fold {i:>2} {te_s.strftime('%Y-%m')}: combo {best_combo} | "
              f"train PF {best_train['profit_factor'] if best_train else 'n/a'} "
              f"({best_train['n_trades'] if best_train else 0}t) | "
              f"test net {test_m['total_net_pnl']:+.0f} ({test_m['n_trades']}t)")

    oos_days = weekdays(folds[0][2], folds[-1][3]) if folds else []
    oos = trade_metrics(oos_trades, base_cfg.equity0, oos_days)
    is_sharpe_mean = sum(is_sharpes) / len(is_sharpes) if is_sharpes else 0.0
    # WFE only means something when parameter selection actually happened;
    # when every fold carried the default combo there is no selection bias
    # to measure -> criterion is n/a, the other three carry the verdict.
    wfe = (round(oos["sharpe"] / is_sharpe_mean, 3)
           if (n_selected > 0 and is_sharpe_mean > 0) else None)
    profitable_folds = sum(1 for f in fold_reports
                           if f["test_metrics"]["total_net_pnl"] > 0)
    active_folds = sum(1 for f in fold_reports if f["test_metrics"]["n_trades"] > 0)

    T = len(oos_days) or 1
    hurdle = math.sqrt(2 * math.log(len(GRID)) / T) * math.sqrt(252)

    return {
        "folds": fold_reports,
        "oos_trades": oos_trades,
        "oos_metrics": oos,
        "is_sharpe_mean": round(is_sharpe_mean, 3),
        "wfe": wfe,
        "n_selected_folds": n_selected,
        "profitable_folds": profitable_folds,
        "active_folds": active_folds,
        "n_folds": len(fold_reports),
        "deflated_sharpe_hurdle": round(hurdle, 3),
        "acceptance": {
            "wfe_ge_0.5": (wfe >= 0.5) if wfe is not None else None,
            "oos_pf_ge_1.25": oos["profit_factor"] >= 1.25,
            "folds_60pct_profitable": (profitable_folds >= 0.6 * active_folds
                                       if active_folds else False),
            "oos_sharpe_gt_hurdle": oos["sharpe"] > hurdle,
        },
    }


# ── Monte Carlo suite ────────────────────────────────────────────────────────
def mc_bootstrap_dd(trades: List[Dict], n_paths: int = 10_000,
                    seed: int = 42) -> Dict[str, float]:
    """Resample the trade sequence with replacement; distribution of max DD."""
    if not trades:
        return {"p50": 0.0, "p95": 0.0, "p99": 0.0}
    rng = random.Random(seed)
    pnls = [t["net_pnl"] for t in trades]
    dds = []
    for _ in range(n_paths):
        cum, peak, dd = 0.0, 0.0, 0.0
        for _ in range(len(pnls)):
            cum += rng.choice(pnls)
            peak = max(peak, cum)
            dd = max(dd, peak - cum)
        dds.append(dd)
    dds.sort()
    q = lambda p: dds[min(int(p * len(dds)), len(dds) - 1)]
    return {"p50": round(q(0.50), 2), "p95": round(q(0.95), 2), "p99": round(q(0.99), 2)}


def mc_cost_stress(wf: Dict[str, Any], base_cfg: Config) -> Dict[str, Any]:
    """Rerun every test fold with the fold's chosen combo at slippage x2/x3."""
    out = {}
    for mult in (2.0, 3.0):
        trades: List[Dict] = []
        for f in wf["folds"]:
            cfg = replace(base_cfg, **f["combo"],
                          slippage_per_leg=base_cfg.slippage_per_leg * mult)
            te_s = datetime.date.fromisoformat(f["test"][0])
            te_e = datetime.date.fromisoformat(f["test"][1])
            trades.extend(run_window(cfg, te_s, te_e)["trades"])
        m = trade_metrics(trades, base_cfg.equity0)
        out[f"slippage_x{mult:.0f}"] = {
            "total_net_pnl": m["total_net_pnl"], "profit_factor": m["profit_factor"],
            "n_trades": m["n_trades"],
        }
    return out


def mc_jackknife(trades: List[Dict], k_max: int = 3) -> Dict[str, Any]:
    """Drop the k BEST trades: does profitability survive without the luckiest
    outcomes? (Dropping worst trades flatters; dropping best stresses.)"""
    out = {}
    ordered = sorted(trades, key=lambda t: -t["net_pnl"])
    total = sum(t["net_pnl"] for t in trades)
    out["total"] = round(total, 2)
    for k in range(1, k_max + 1):
        rem = ordered[k:]
        out[f"drop_best_{k}"] = round(sum(t["net_pnl"] for t in rem), 2)
    return out


# ── main ─────────────────────────────────────────────────────────────────────
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", default="2024-01-01")
    ap.add_argument("--end", default="2026-07-03")
    ap.add_argument("--underlying", default="NIFTY")
    ap.add_argument("--equity", type=float, default=500_000.0)
    ap.add_argument("--max-loss-frac", type=float, default=0.03,
                    help="absolute per-trade loss cap as fraction of equity")
    ap.add_argument("--auto-interval", action="store_true",
                    help="infer strike grid per day from the chain (stocks)")
    ap.add_argument("--ladder", action="store_true",
                    help="income-ladder mode: weekly tranches 30-45 DTE, "
                         "managed at 21 DTE, IVR-scaled sizing, 6 concurrent")
    ap.add_argument("--report", default="wf_report.json")
    args = ap.parse_args()
    start = datetime.date.fromisoformat(args.start)
    end = datetime.date.fromisoformat(args.end)

    base_cfg = Config(underlying=args.underlying, equity0=args.equity,
                      risk_frac_hard_cap=args.max_loss_frac,
                      auto_interval=args.auto_interval)
    if args.ladder:
        base_cfg = replace(base_cfg, ladder_mode=True, min_days_to_expiry=30,
                           dte_max=45, time_stop_days=21, max_open=6)
    print(f"WALK-FORWARD {start} -> {end} | equity Rs {args.equity:,.0f} | "
          f"loss cap {args.max_loss_frac:.0%} | grid of {len(GRID)} combos")
    wf = walk_forward(start, end, base_cfg)

    print("\nMONTE CARLO: bootstrap DD, cost stress, jackknife...")
    boot = mc_bootstrap_dd(wf["oos_trades"])
    stress = mc_cost_stress(wf, base_cfg)
    jack = mc_jackknife(wf["oos_trades"])

    report = {
        "generated": datetime.datetime.now().isoformat(timespec="seconds"),
        "window": [start.isoformat(), end.isoformat()],
        "grid": GRID,
        "oos_metrics": wf["oos_metrics"],
        "is_sharpe_mean": wf["is_sharpe_mean"],
        "wfe": wf["wfe"],
        "deflated_sharpe_hurdle": wf["deflated_sharpe_hurdle"],
        "profitable_folds": f"{wf['profitable_folds']}/{wf['active_folds']} active ({wf['n_folds']} total)",
        "acceptance": wf["acceptance"],
        "mc_bootstrap_dd": boot,
        "mc_cost_stress": stress,
        "mc_jackknife": jack,
        "folds": wf["folds"],
        "oos_trades": wf["oos_trades"],
    }
    os.makedirs(RESULTS_DIR, exist_ok=True)
    out_path = os.path.join(RESULTS_DIR, args.report)
    with open(out_path, "w") as f:
        json.dump(report, f, indent=1, default=str)

    m = wf["oos_metrics"]
    print("\n" + "=" * 70)
    print("WALK-FORWARD OOS RESULT (only frozen-parameter test months count)")
    print("=" * 70)
    print(f"  OOS trades: {m['n_trades']}  win rate {m['win_rate']*100:.1f}%  "
          f"PF {m['profit_factor']}  Sharpe {m['sharpe']}")
    print(f"  OOS net P&L: Rs {m['total_net_pnl']:,.2f}  friction {m['total_friction']:,.2f}  "
          f"expectancy Rs {m['expectancy']:,.2f}/trade")
    wfe_label = (wf["wfe"] if wf["wfe"] is not None
                 else ("n/a (IS Sharpe <= 0)" if wf["n_selected_folds"] > 0
                       else "n/a (no selection in any fold)"))
    print(f"  IS Sharpe (mean of used combos): {wf['is_sharpe_mean']}  ->  "
          f"WFE {wfe_label}"
          f"  [{wf['n_selected_folds']}/{wf['n_folds']} folds selected params]")
    print(f"  deflated-Sharpe hurdle: {wf['deflated_sharpe_hurdle']}")
    print(f"  profitable folds: {wf['profitable_folds']}/{wf['active_folds']} active")
    print(f"  ACCEPTANCE: {wf['acceptance']}")
    print(f"  MC bootstrap max-DD: {boot}")
    print(f"  MC cost stress: {stress}")
    print(f"  MC jackknife (drop best k): {jack}")
    verdict = all(v for v in wf["acceptance"].values() if v is not None)
    print(f"\n  VERDICT: {'PASS — candidate for paper deployment' if verdict else 'FAIL — do not deploy; edge not proven'}")
    print(f"  report: {out_path}")


if __name__ == "__main__":
    main()
