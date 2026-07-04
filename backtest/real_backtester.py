"""
Phase 3 — honest EOD backtest on real NSE bhavcopy premiums.

Purpose: replace the synthetic simulator's verdict with one derived from
settlement-grade prices plus real frictions. EOD granularity is a *limitation*
(no intraday TP/SL), stated openly, not hidden.

Mirrors the live system's decision chain:
  * bias from real chain OI PCR on the trade expiry:
        PCR >= 1.25 -> BULLISH -> bull put spread
        PCR <= 1.00 -> BEARISH -> bear call spread
        else        -> no trade (mixed regime)
    (mirrors regime_classifier's coi_pcr thresholds)
  * strikes mirror options_desk_service: sell ~1% OTM rounded to the strike
    interval, buy exactly 1 interval further OTM (50-wide on NIFTY)
  * entry at the day's close with per-leg slippage (sell receives close-slip,
    buy pays close+slip)
  * exits mirror execution_service._real_exit_decision for credit spreads:
    TP at +0.80 x credit, SL at -1.00 x credit, evaluated on subsequent EOD
    marks; if neither hits, hold to expiry and settle at intrinsic
  * frictions from backend.app.core.friction_model on every executed leg;
    expiry settlement is cash-settled (no exit order) — modelled as STT 0.125%
    on intrinsic for LONG in-the-money legs only.

Run:
  PYTHONUTF8=1 python -m backtest.real_backtester --start 2026-05-25 --end 2026-07-03
"""
import argparse
import datetime
import math
from dataclasses import dataclass, field, asdict
from typing import Any, Callable, Dict, List, Optional

import os
import sys
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from backend.app.core import friction_model
from backtest import bhavcopy

SETTLEMENT_STT_RATE = 0.00125  # 0.125% on intrinsic value of exercised long options


@dataclass
class Config:
    underlying: str = "NIFTY"
    lots: int = 5                     # system enforces min 5 lots on spreads
    slippage_per_leg: float = 0.75    # Rs/share worse than close on each executed leg
    otm_pct: float = 0.01             # sell strike ~1% OTM
    strike_interval: int = 50         # NIFTY
    width_intervals: int = 1          # buy 1 interval further OTM
    pcr_bull: float = 1.25            # PCR >= this -> bull put
    pcr_bear: float = 1.00            # PCR <= this -> bear call
    tp_ratio: float = 0.80            # take profit at +0.80 x credit
    sl_ratio: float = 1.00            # stop loss at -1.00 x credit
    min_days_to_expiry: int = 1       # never enter on expiry day itself
    min_leg_close: float = 0.5        # skip illiquid legs (Rs)
    max_open: int = 1                 # one NIFTY position at a time


@dataclass
class Position:
    entry_date: datetime.date
    expiry: datetime.date
    strategy: str                     # BULL_PUT_SPREAD | BEAR_CALL_SPREAD
    opt_type: str                     # 'PE' | 'CE'
    sell_strike: float
    buy_strike: float
    entry_credit: float               # per share, after slippage
    quantity: int                     # lots x lot_size
    lot_size: int
    entry_friction: float
    entry_spot: float
    pcr: float


@dataclass
class ClosedTrade:
    entry_date: str
    exit_date: str
    strategy: str
    sell_strike: float
    buy_strike: float
    expiry: str
    entry_credit: float
    exit_cost: float                  # per share paid to close (0-cost if expired worthless)
    exit_reason: str
    quantity: int
    gross_pnl: float                  # Rs, before friction
    friction: float                   # Rs, entry+exit
    net_pnl: float                    # Rs
    days_held: int
    entry_spot: float
    exit_spot: float
    pcr_at_entry: float


def chain_pcr(chain: Dict[str, Any], expiry: datetime.date) -> float:
    """OI put-call ratio over one expiry from a bhavcopy chain."""
    put_oi = sum(v["oi"] for (e, s, t), v in chain["options"].items()
                 if e == expiry and t == "PE")
    call_oi = sum(v["oi"] for (e, s, t), v in chain["options"].items()
                  if e == expiry and t == "CE")
    return (put_oi / call_oi) if call_oi > 0 else 1.0


def _leg_close(chain: Dict[str, Any], expiry: datetime.date, strike: float,
               opt_type: str) -> Optional[float]:
    row = chain["options"].get((expiry, strike, opt_type))
    if not row:
        return None
    c = float(row.get("close") or 0.0)
    return c if c > 0 else None


class RealBacktester:
    """EOD backtest over a date window. chain_provider is injectable for tests
    (signature: (date, underlying) -> chain dict or None)."""

    def __init__(self, cfg: Config = None,
                 chain_provider: Callable = bhavcopy.load_chain):
        self.cfg = cfg or Config()
        self.load_chain = chain_provider

    # ── entry ──────────────────────────────────────────────────────────────
    def _try_enter(self, d: datetime.date, chain: Dict[str, Any]) -> Optional[Position]:
        cfg = self.cfg
        spot = float(chain["spot"] or 0)
        if spot <= 0:
            return None

        expiry = bhavcopy.nearest_expiry(chain, d, min_days=cfg.min_days_to_expiry)
        if expiry is None:
            return None

        pcr = chain_pcr(chain, expiry)
        if pcr >= cfg.pcr_bull:
            strategy, opt_type = "BULL_PUT_SPREAD", "PE"
            raw_sell = spot * (1 - cfg.otm_pct)
            sell_strike = round(raw_sell / cfg.strike_interval) * cfg.strike_interval
            buy_strike = sell_strike - cfg.width_intervals * cfg.strike_interval
        elif pcr <= cfg.pcr_bear:
            strategy, opt_type = "BEAR_CALL_SPREAD", "CE"
            raw_sell = spot * (1 + cfg.otm_pct)
            sell_strike = round(raw_sell / cfg.strike_interval) * cfg.strike_interval
            buy_strike = sell_strike + cfg.width_intervals * cfg.strike_interval
        else:
            return None  # mixed regime — the system would not trade

        sell_close = _leg_close(chain, expiry, float(sell_strike), opt_type)
        buy_close = _leg_close(chain, expiry, float(buy_strike), opt_type)
        if sell_close is None or buy_close is None:
            return None
        if sell_close < cfg.min_leg_close:
            return None  # nothing to sell — dead strike

        # fills: sell receives less, buy pays more
        sell_fill = max(sell_close - cfg.slippage_per_leg, 0.05)
        buy_fill = buy_close + cfg.slippage_per_leg
        credit = round(sell_fill - buy_fill, 2)
        if credit <= 0:
            return None  # no premium left after slippage — not a trade

        lot = 0
        row = chain["options"].get((expiry, float(sell_strike), opt_type))
        if row:
            lot = int(row.get("lot") or 0)
        if lot <= 0:
            lot = 65  # NIFTY 2026 lot — only hit if bhavcopy omits it
        quantity = cfg.lots * lot

        entry_friction = friction_model.basket_friction([
            {"side": "SELL", "opt_type": opt_type.lower(), "price": sell_fill, "quantity": quantity},
            {"side": "BUY", "opt_type": opt_type.lower(), "price": buy_fill, "quantity": quantity},
        ])["total"]

        return Position(
            entry_date=d, expiry=expiry, strategy=strategy, opt_type=opt_type,
            sell_strike=float(sell_strike), buy_strike=float(buy_strike),
            entry_credit=credit, quantity=quantity, lot_size=lot,
            entry_friction=entry_friction, entry_spot=spot, pcr=round(pcr, 3),
        )

    # ── exit ───────────────────────────────────────────────────────────────
    def _intrinsic(self, pos: Position, spot: float) -> float:
        """Spread liability per share at settlement (what closing costs us)."""
        if pos.opt_type == "PE":
            short_itm = max(pos.sell_strike - spot, 0.0)
            long_itm = max(pos.buy_strike - spot, 0.0)
        else:
            short_itm = max(spot - pos.sell_strike, 0.0)
            long_itm = max(spot - pos.buy_strike, 0.0)
        return short_itm - long_itm

    def _try_exit(self, d: datetime.date, chain: Dict[str, Any],
                  pos: Position) -> Optional[ClosedTrade]:
        cfg = self.cfg
        spot = float(chain["spot"] or pos.entry_spot)

        if d >= pos.expiry:
            # Cash settlement at intrinsic. No exit orders; the only cost is
            # exercise STT on a long leg that finishes ITM.
            exit_cost = self._intrinsic(pos, spot)
            long_itm = (max(pos.buy_strike - spot, 0.0) if pos.opt_type == "PE"
                        else max(spot - pos.buy_strike, 0.0))
            exit_friction = SETTLEMENT_STT_RATE * long_itm * pos.quantity
            return self._close(pos, d, spot, exit_cost, exit_friction,
                               "EXPIRY_SETTLEMENT")

        sell_close = _leg_close(chain, pos.expiry, pos.sell_strike, pos.opt_type)
        buy_close = _leg_close(chain, pos.expiry, pos.buy_strike, pos.opt_type)
        if sell_close is None or buy_close is None:
            return None  # no mark today — hold

        # cost to close per share at today's close, with slippage against us
        # (buy back the short at ask-ish, sell the long at bid-ish)
        close_cost = (sell_close + cfg.slippage_per_leg) - max(buy_close - cfg.slippage_per_leg, 0.05)
        pnl_ps = pos.entry_credit - close_cost

        tp = cfg.tp_ratio * pos.entry_credit
        sl = -cfg.sl_ratio * pos.entry_credit
        if pnl_ps >= tp:
            reason = "TAKE_PROFIT"
        elif pnl_ps <= sl:
            reason = "STOP_LOSS"
        else:
            return None

        buy_back = sell_close + cfg.slippage_per_leg
        sell_out = max(buy_close - cfg.slippage_per_leg, 0.05)
        exit_friction = friction_model.basket_friction([
            {"side": "BUY", "opt_type": pos.opt_type.lower(), "price": buy_back, "quantity": pos.quantity},
            {"side": "SELL", "opt_type": pos.opt_type.lower(), "price": sell_out, "quantity": pos.quantity},
        ])["total"]
        return self._close(pos, d, spot, close_cost, exit_friction, reason)

    def _close(self, pos: Position, d: datetime.date, spot: float,
               exit_cost: float, exit_friction: float, reason: str) -> ClosedTrade:
        gross = (pos.entry_credit - exit_cost) * pos.quantity
        friction = round(pos.entry_friction + exit_friction, 2)
        return ClosedTrade(
            entry_date=pos.entry_date.isoformat(), exit_date=d.isoformat(),
            strategy=pos.strategy, sell_strike=pos.sell_strike,
            buy_strike=pos.buy_strike, expiry=pos.expiry.isoformat(),
            entry_credit=pos.entry_credit, exit_cost=round(exit_cost, 2),
            exit_reason=reason, quantity=pos.quantity,
            gross_pnl=round(gross, 2), friction=friction,
            net_pnl=round(gross - friction, 2),
            days_held=(d - pos.entry_date).days,
            entry_spot=round(pos.entry_spot, 2), exit_spot=round(spot, 2),
            pcr_at_entry=pos.pcr,
        )

    # ── main loop ──────────────────────────────────────────────────────────
    def run(self, dates: List[datetime.date]) -> Dict[str, Any]:
        trades: List[ClosedTrade] = []
        open_pos: List[Position] = []
        daily_pnl: Dict[datetime.date, float] = {}
        skipped_mixed = 0

        for d in sorted(dates):
            chain = self.load_chain(d, self.cfg.underlying)
            if chain is None:
                continue
            daily_pnl.setdefault(d, 0.0)

            # exits first (never same-day as entry: entries happen at close)
            still_open = []
            for pos in open_pos:
                if pos.entry_date >= d:
                    still_open.append(pos)
                    continue
                closed = self._try_exit(d, chain, pos)
                if closed:
                    trades.append(closed)
                    daily_pnl[d] += closed.net_pnl
                else:
                    still_open.append(pos)
            open_pos = still_open

            # entry at close
            if len(open_pos) < self.cfg.max_open:
                pos = self._try_enter(d, chain)
                if pos:
                    open_pos.append(pos)
                elif chain:
                    skipped_mixed += 1

        # force-settle anything still open at window end using its last chain
        for pos in open_pos:
            last = None
            for d in sorted(dates, reverse=True):
                last = self.load_chain(d, self.cfg.underlying)
                if last:
                    break
            if last:
                spot = float(last["spot"] or pos.entry_spot)
                sell_c = _leg_close(last, pos.expiry, pos.sell_strike, pos.opt_type) or 0.0
                buy_c = _leg_close(last, pos.expiry, pos.buy_strike, pos.opt_type) or 0.0
                cost = (sell_c + self.cfg.slippage_per_leg) - max(buy_c - self.cfg.slippage_per_leg, 0.0)
                closed = self._close(pos, last["date"], spot, cost, 0.0, "WINDOW_END_MARK")
                trades.append(closed)
                daily_pnl[last["date"]] = daily_pnl.get(last["date"], 0.0) + closed.net_pnl

        return self._metrics(trades, daily_pnl, skipped_mixed)

    # ── metrics ────────────────────────────────────────────────────────────
    def _metrics(self, trades: List[ClosedTrade],
                 daily_pnl: Dict[datetime.date, float],
                 skipped_mixed: int) -> Dict[str, Any]:
        n = len(trades)
        pnls = [t.net_pnl for t in trades]
        wins = [p for p in pnls if p > 0]
        losses = [p for p in pnls if p <= 0]

        equity, run_tot = [], 0.0
        for d in sorted(daily_pnl):
            run_tot += daily_pnl[d]
            equity.append((d.isoformat(), round(run_tot, 2)))

        peak, max_dd = 0.0, 0.0
        for _, v in equity:
            peak = max(peak, v)
            max_dd = max(max_dd, peak - v)

        max_consec, cur = 0, 0
        for t in trades:
            cur = cur + 1 if t.net_pnl <= 0 else 0
            max_consec = max(max_consec, cur)

        series = [daily_pnl[d] for d in sorted(daily_pnl)]
        sharpe = 0.0
        if len(series) > 1:
            mean = sum(series) / len(series)
            var = sum((x - mean) ** 2 for x in series) / (len(series) - 1)
            sd = math.sqrt(var)
            if sd > 0:
                sharpe = (mean / sd) * math.sqrt(252)

        per_strategy: Dict[str, Dict[str, float]] = {}
        for t in trades:
            s = per_strategy.setdefault(t.strategy, {"trades": 0, "net_pnl": 0.0, "wins": 0})
            s["trades"] += 1
            s["net_pnl"] = round(s["net_pnl"] + t.net_pnl, 2)
            s["wins"] += 1 if t.net_pnl > 0 else 0

        per_reason: Dict[str, int] = {}
        for t in trades:
            per_reason[t.exit_reason] = per_reason.get(t.exit_reason, 0) + 1

        gross_win = sum(wins)
        gross_loss = -sum(losses)
        return {
            "config": asdict(self.cfg),
            "trades": [asdict(t) for t in trades],
            "equity_curve": equity,
            "summary": {
                "n_trades": n,
                "skipped_mixed_regime_days": skipped_mixed,
                "win_rate": round(len(wins) / n, 3) if n else 0.0,
                "expectancy_per_trade": round(sum(pnls) / n, 2) if n else 0.0,
                "total_net_pnl": round(sum(pnls), 2),
                "total_gross_pnl": round(sum(t.gross_pnl for t in trades), 2),
                "total_friction": round(sum(t.friction for t in trades), 2),
                "profit_factor": round(gross_win / gross_loss, 2) if gross_loss > 0 else float("inf") if gross_win > 0 else 0.0,
                "sharpe_annualized": round(sharpe, 2),
                "max_drawdown": round(max_dd, 2),
                "max_consecutive_losses": max_consec,
                "avg_days_held": round(sum(t.days_held for t in trades) / n, 1) if n else 0.0,
                "per_strategy": per_strategy,
                "exit_reasons": per_reason,
            },
        }


def print_report(result: Dict[str, Any]):
    s = result["summary"]
    cfg = result["config"]
    print("\n" + "=" * 64)
    print(f"HONEST EOD BACKTEST — {cfg['underlying']} | {cfg['lots']} lots | "
          f"slippage Rs {cfg['slippage_per_leg']}/leg")
    print("=" * 64)
    print(f"  trades: {s['n_trades']}   (mixed-regime days skipped: {s['skipped_mixed_regime_days']})")
    print(f"  win rate: {s['win_rate']*100:.1f}%   profit factor: {s['profit_factor']}")
    print(f"  net P&L: Rs {s['total_net_pnl']:,.2f}   (gross {s['total_gross_pnl']:,.2f} "
          f"- friction {s['total_friction']:,.2f})")
    print(f"  expectancy/trade: Rs {s['expectancy_per_trade']:,.2f}   avg hold: {s['avg_days_held']} days")
    print(f"  Sharpe (ann.): {s['sharpe_annualized']}   max DD: Rs {s['max_drawdown']:,.2f}   "
          f"max consec losses: {s['max_consecutive_losses']}")
    print(f"  per strategy: {s['per_strategy']}")
    print(f"  exit reasons: {s['exit_reasons']}")
    print("-" * 64)
    for t in result["trades"]:
        print(f"  {t['entry_date']} -> {t['exit_date']}  {t['strategy']:<17} "
              f"{t['sell_strike']:.0f}/{t['buy_strike']:.0f} exp {t['expiry']}  "
              f"credit {t['entry_credit']:>6.2f}  {t['exit_reason']:<18} "
              f"net Rs {t['net_pnl']:>10.2f} (friction {t['friction']:.2f})")


def main():
    ap = argparse.ArgumentParser(description="Honest EOD backtest on NSE bhavcopy")
    ap.add_argument("--start", required=True)
    ap.add_argument("--end", required=True)
    ap.add_argument("--underlying", default="NIFTY")
    ap.add_argument("--lots", type=int, default=5)
    ap.add_argument("--no-download", action="store_true")
    args = ap.parse_args()

    start = datetime.date.fromisoformat(args.start)
    end = datetime.date.fromisoformat(args.end)

    if not args.no_download:
        print(f"Downloading bhavcopies {start} -> {end} (cached files skipped)...")
        have = bhavcopy.download_range(start, end)
        print(f"  {len(have)} trading days with data")

    dates = []
    d = start
    while d <= end:
        if d.weekday() < 5:
            dates.append(d)
        d += datetime.timedelta(days=1)

    cfg = Config(underlying=args.underlying, lots=args.lots)
    bt = RealBacktester(cfg)
    result = bt.run(dates)
    print_report(result)
    return result


if __name__ == "__main__":
    main()
