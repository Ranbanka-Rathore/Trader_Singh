"""
Phase 4 — honest EOD backtest v2 on real NSE bhavcopy premiums.

v1 (Phase 3) replicated the live system as-is and showed it was breakeven
after friction. v2 implements the overhaul plan (claudefable Phase-4 answer):

  STRUCTURE   delta-targeted short strike (|delta| ~ 0.15-0.20 from BS IV
              inversion of real closes), hedge 3-4 intervals further out
              (150-200 pts on NIFTY), enter 5-8 DTE, credit floor
              max(Rs 8/share, 2x est. friction/share)
  GATES       regime_filters.entry_allowed: event blackout, VRP (IV-RV>2pts,
              IV rank>0.3), GEX sign, symmetric PCR + EMA20 confirmation,
              ER counter-trend block  — all fixed a priori, never optimized
  SIZING      lots = min(L_risk, L_vol, L_kelly):
                L_risk  = floor(1.5% equity / max_loss_per_lot)
                L_vol   = floor(0.4% equity / (dnet * S * sigma_d * lot))
                L_kelly = floor(0.25 * f* * equity / max_loss_per_lot),
                          f* from last 50 closed trades; f*<=0 -> 1-lot probe
  EXITS       TP at +0.5-0.6 x credit on EOD mark; SL strike-touch (EOD close
              through the short strike) or mark stop at -1.5 x credit;
              TIME STOP at T-1 before expiry (never hold expiry-day gamma)

EOD granularity is still a stated limitation: intraday touches are invisible;
the live system checks the same rules every 5 minutes, so live behavior is
strictly tighter than this simulation.

Run:
  PYTHONUTF8=1 python -m backtest.real_backtester --start 2024-08-01 --end 2026-07-03
"""
import argparse
import datetime
import math
import os
import sys
from dataclasses import dataclass, asdict, field, replace
from typing import Any, Callable, Dict, List, Optional, Tuple

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from backend.app.core import bs_math, friction_model, regime_filters as rf
from backtest import bhavcopy

SETTLEMENT_STT_RATE = 0.00125


@dataclass
class Config:
    underlying: str = "NIFTY"
    slippage_per_leg: float = 0.75
    strike_interval: int = 50
    # structure
    delta_target: float = 0.18        # grid: {0.15, 0.20}
    delta_band: Tuple[float, float] = (0.10, 0.28)
    width_intervals: int = 4          # 200 pts on NIFTY
    min_days_to_expiry: int = 4       # -> 5-8 DTE weekly entries
    credit_floor_abs: float = 8.0     # Rs/share
    credit_floor_friction_mult: float = 2.0
    # exits
    tp_ratio: float = 0.5             # grid: {0.5, 0.6}
    sl_mode: str = "strike_touch"     # grid: {"strike_touch", "mark"}
    sl_mark_mult: float = 1.5
    time_stop_days: int = 1           # exit at T-1 before expiry
    # gates
    use_gates: bool = True
    # GEX off by default in backtest: the naive OI-sign convention (dealers
    # long calls / short puts) directly contradicts the PCR reading this
    # system trades (heavy put OI = written puts = support). The live worker
    # can gate on its own dealer-calibrated GEX feed instead.
    use_gex: bool = False
    # sizing
    equity0: float = 500_000.0
    risk_frac: float = 0.015
    # Indian F&O has no sub-lot sizing: if 1 lot exceeds risk_frac but stays
    # under this absolute per-trade cap, trade the single lot; above it, skip.
    risk_frac_hard_cap: float = 0.03
    sigma_target: float = 0.004
    kelly_frac: float = 0.25
    kelly_lookback: int = 50
    kelly_min_trades: int = 20
    kelly_probe_lots: int = 1         # f*<=0 -> probe size, keeps estimator alive
    max_lots: int = 20
    max_open: int = 1


@dataclass
class Position:
    entry_date: datetime.date
    expiry: datetime.date
    strategy: str
    opt_type: str
    sell_strike: float
    buy_strike: float
    entry_credit: float
    lots: int
    quantity: int
    lot_size: int
    entry_friction: float
    entry_spot: float
    pcr: float
    short_delta: float
    sizing: Dict[str, Any] = field(default_factory=dict)


@dataclass
class ClosedTrade:
    entry_date: str
    exit_date: str
    strategy: str
    sell_strike: float
    buy_strike: float
    expiry: str
    entry_credit: float
    exit_cost: float
    exit_reason: str
    lots: int
    quantity: int
    gross_pnl: float
    friction: float
    net_pnl: float
    days_held: int
    entry_spot: float
    exit_spot: float
    pcr_at_entry: float
    short_delta: float


def chain_pcr(chain: Dict[str, Any], expiry: datetime.date) -> float:
    put_oi = sum(v["oi"] for (e, s, t), v in chain["options"].items()
                 if e == expiry and t == "PE")
    call_oi = sum(v["oi"] for (e, s, t), v in chain["options"].items()
                  if e == expiry and t == "CE")
    return (put_oi / call_oi) if call_oi > 0 else 1.0


def _leg_close(chain, expiry, strike, opt_type) -> Optional[float]:
    row = chain["options"].get((expiry, float(strike), opt_type))
    if not row:
        return None
    c = float(row.get("close") or 0.0)
    return c if c > 0 else None


class RealBacktester:
    def __init__(self, cfg: Config = None,
                 chain_provider: Callable = bhavcopy.load_chain):
        self.cfg = cfg or Config()
        self.load_chain = chain_provider
        self._closes: List[float] = []
        self._iv_hist: List[float] = []
        self._equity: float = self.cfg.equity0
        self._closed: List[ClosedTrade] = []
        self.skip_reasons: Dict[str, int] = {}

    # ── strike selection ───────────────────────────────────────────────────
    def _pick_short_strike(self, chain, expiry, spot, opt_type,
                           on_date) -> Optional[Tuple[float, float]]:
        """Walk OTM from ATM; return (strike, delta) with |delta| nearest the
        target inside the band. Uses BS IV inverted from each strike's close."""
        cfg = self.cfg
        t = max((expiry - on_date).days, 1) / 365.0
        step = cfg.strike_interval if opt_type == "CE" else -cfg.strike_interval
        atm = round(spot / cfg.strike_interval) * cfg.strike_interval
        best: Optional[Tuple[float, float]] = None
        best_err = 1e9
        for i in range(1, 40):
            strike = float(atm + i * step)
            close = _leg_close(chain, expiry, strike, opt_type)
            if close is None:
                continue
            iv = bs_math.implied_vol(close, spot, strike, t, opt_type)
            if iv <= 0:
                continue
            d = abs(bs_math.delta(spot, strike, t, iv, opt_type))
            if d < cfg.delta_band[0] - 0.02:
                break  # walked past the band — deltas only shrink from here
            if cfg.delta_band[0] <= d <= cfg.delta_band[1]:
                err = abs(d - cfg.delta_target)
                if err < best_err:
                    best, best_err = (strike, d), err
        return best

    # ── sizing ─────────────────────────────────────────────────────────────
    def _kelly_fraction(self) -> Optional[float]:
        """f* = p - q/b from the last `kelly_lookback` closed trades.
        None while the sample is too small to estimate."""
        cfg = self.cfg
        recent = self._closed[-cfg.kelly_lookback:]
        if len(recent) < cfg.kelly_min_trades:
            return None
        wins = [t.net_pnl for t in recent if t.net_pnl > 0]
        losses = [-t.net_pnl for t in recent if t.net_pnl <= 0]
        if not losses:
            return 1.0
        if not wins:
            return 0.0
        p = len(wins) / len(recent)
        b = (sum(wins) / len(wins)) / (sum(losses) / len(losses))
        return p - (1 - p) / b

    def _size_lots(self, *, width: float, credit: float, lot: int,
                   spot: float, dnet: float) -> Tuple[int, Dict[str, Any]]:
        cfg = self.cfg
        max_loss_per_lot = max((width - credit) * lot, 1.0)
        l_risk = int(cfg.risk_frac * self._equity / max_loss_per_lot)
        if l_risk < 1 and max_loss_per_lot <= cfg.risk_frac_hard_cap * self._equity:
            l_risk = 1  # min-lot exception under the absolute cap

        rv = rf.realized_vol(self._closes)
        sigma_d = (rv / math.sqrt(252)) if rv > 0 else 0.01 / math.sqrt(252)
        lot_daily_vol = max(dnet, 0.02) * spot * sigma_d * lot
        l_vol = int(cfg.sigma_target * self._equity / max(lot_daily_vol, 1.0))

        f_star = self._kelly_fraction()
        if f_star is None:
            l_kelly = None
        elif f_star <= 0:
            l_kelly = cfg.kelly_probe_lots
        else:
            l_kelly = int(cfg.kelly_frac * f_star * self._equity / max_loss_per_lot)

        candidates = [l_risk, l_vol] + ([l_kelly] if l_kelly is not None else [])
        lots = max(0, min(min(candidates), cfg.max_lots))
        return lots, {"l_risk": l_risk, "l_vol": l_vol, "l_kelly": l_kelly,
                      "f_star": round(f_star, 4) if f_star is not None else None,
                      "max_loss_per_lot": round(max_loss_per_lot, 2)}

    # ── entry ──────────────────────────────────────────────────────────────
    def _skip(self, reason: str):
        key = reason.split("_iv")[0].split("_0")[0][:40]
        self.skip_reasons[key] = self.skip_reasons.get(key, 0) + 1

    def _try_enter(self, d: datetime.date, chain) -> Optional[Position]:
        cfg = self.cfg
        spot = float(chain["spot"] or 0)
        if spot <= 0:
            return None

        expiry = bhavcopy.nearest_expiry(chain, d, min_days=cfg.min_days_to_expiry)
        if expiry is None:
            self._skip("no_expiry")
            return None

        pcr = chain_pcr(chain, expiry)
        iv_now = self._iv_hist[-1] if self._iv_hist else 0.0

        if cfg.use_gates:
            gex = 0
            if cfg.use_gex:
                gex = rf.naive_gex_sign(spot, expiry, d, chain["options"])
            side, reason = rf.entry_allowed(
                side_pcr=pcr, spot=spot, closes=self._closes, iv=iv_now,
                iv_hist=self._iv_hist[:-1], on_date=d, gex_sign=gex)
            if side is None:
                self._skip(reason)
                return None
        else:
            side = ("BULL_PUT_SPREAD" if pcr >= rf.PCR_BULL
                    else "BEAR_CALL_SPREAD" if pcr <= rf.PCR_BEAR else None)
            if side is None:
                self._skip("no_side")
                return None

        opt_type = "PE" if side == "BULL_PUT_SPREAD" else "CE"
        picked = self._pick_short_strike(chain, expiry, spot, opt_type, d)
        if picked is None:
            self._skip("no_strike_in_delta_band")
            return None
        sell_strike, short_delta = picked
        off = cfg.width_intervals * cfg.strike_interval
        buy_strike = sell_strike - off if opt_type == "PE" else sell_strike + off

        sell_close = _leg_close(chain, expiry, sell_strike, opt_type)
        buy_close = _leg_close(chain, expiry, buy_strike, opt_type)
        if sell_close is None or buy_close is None:
            self._skip("hedge_leg_missing")
            return None

        sell_fill = max(sell_close - cfg.slippage_per_leg, 0.05)
        buy_fill = buy_close + cfg.slippage_per_leg
        credit = round(sell_fill - buy_fill, 2)

        row = chain["options"].get((expiry, sell_strike, opt_type)) or {}
        lot = int(row.get("lot") or 0) or 75

        # credit floor: absolute AND friction multiple (per share, round trip)
        fr_probe = friction_model.round_trip_friction(
            [{"side": "SELL", "opt_type": opt_type.lower(), "price": sell_fill, "quantity": lot},
             {"side": "BUY", "opt_type": opt_type.lower(), "price": buy_fill, "quantity": lot}],
            [{"side": "BUY", "opt_type": opt_type.lower(), "price": sell_fill, "quantity": lot},
             {"side": "SELL", "opt_type": opt_type.lower(), "price": buy_fill, "quantity": lot}],
        )["total"] / lot
        floor = max(cfg.credit_floor_abs, cfg.credit_floor_friction_mult * fr_probe)
        if credit < floor:
            self._skip("credit_below_floor")
            return None

        # net spread delta per share for vol sizing
        t = max((expiry - d).days, 1) / 365.0
        iv_s = bs_math.implied_vol(sell_close, spot, sell_strike, t, opt_type)
        iv_b = bs_math.implied_vol(buy_close, spot, buy_strike, t, opt_type)
        d_short = abs(bs_math.delta(spot, sell_strike, t, iv_s or 0.13, opt_type))
        d_long = abs(bs_math.delta(spot, buy_strike, t, iv_b or 0.13, opt_type))
        dnet = max(d_short - d_long, 0.01)

        width = abs(sell_strike - buy_strike)
        lots, sizing = self._size_lots(width=width, credit=credit, lot=lot,
                                       spot=spot, dnet=dnet)
        if lots < 1:
            self._skip("sizing_zero")
            return None

        quantity = lots * lot
        entry_friction = friction_model.basket_friction([
            {"side": "SELL", "opt_type": opt_type.lower(), "price": sell_fill, "quantity": quantity},
            {"side": "BUY", "opt_type": opt_type.lower(), "price": buy_fill, "quantity": quantity},
        ])["total"]

        return Position(
            entry_date=d, expiry=expiry, strategy=side, opt_type=opt_type,
            sell_strike=sell_strike, buy_strike=buy_strike, entry_credit=credit,
            lots=lots, quantity=quantity, lot_size=lot,
            entry_friction=entry_friction, entry_spot=spot,
            pcr=round(pcr, 3), short_delta=round(short_delta, 3), sizing=sizing,
        )

    # ── exit ───────────────────────────────────────────────────────────────
    def _intrinsic(self, pos: Position, spot: float) -> float:
        if pos.opt_type == "PE":
            return max(pos.sell_strike - spot, 0.0) - max(pos.buy_strike - spot, 0.0)
        return max(spot - pos.sell_strike, 0.0) - max(spot - pos.buy_strike, 0.0)

    def _mark_exit(self, chain, pos) -> Optional[Tuple[float, float, float]]:
        """(close_cost, buy_back_price, sell_out_price) at today's marks."""
        sell_close = _leg_close(chain, pos.expiry, pos.sell_strike, pos.opt_type)
        buy_close = _leg_close(chain, pos.expiry, pos.buy_strike, pos.opt_type)
        if sell_close is None:
            return None
        buy_close = buy_close or 0.05
        buy_back = sell_close + self.cfg.slippage_per_leg
        sell_out = max(buy_close - self.cfg.slippage_per_leg, 0.05)
        return buy_back - sell_out, buy_back, sell_out

    def _try_exit(self, d: datetime.date, chain, pos: Position) -> Optional[ClosedTrade]:
        cfg = self.cfg
        spot = float(chain["spot"] or pos.entry_spot)

        if d >= pos.expiry:
            # should be rare (time stop fires at T-1); settle at intrinsic
            exit_cost = self._intrinsic(pos, spot)
            long_itm = (max(pos.buy_strike - spot, 0.0) if pos.opt_type == "PE"
                        else max(spot - pos.buy_strike, 0.0))
            xf = SETTLEMENT_STT_RATE * long_itm * pos.quantity
            return self._close(pos, d, spot, exit_cost, xf, "EXPIRY_SETTLEMENT")

        marks = self._mark_exit(chain, pos)
        days_left = (pos.expiry - d).days

        # time stop at T-1 (calendar): never hold expiry-day gamma
        time_stop = days_left <= cfg.time_stop_days

        if marks is None:
            if time_stop:
                # no mark to exit on — settle next iteration
                return None
            return None
        close_cost, buy_back, sell_out = marks
        pnl_ps = pos.entry_credit - close_cost

        reason = None
        if pnl_ps >= cfg.tp_ratio * pos.entry_credit:
            reason = "TAKE_PROFIT"
        elif cfg.sl_mode == "strike_touch":
            breached = (spot <= pos.sell_strike if pos.opt_type == "PE"
                        else spot >= pos.sell_strike)
            if breached:
                reason = "STOP_STRIKE_TOUCH"
        elif cfg.sl_mode == "mark" and pnl_ps <= -cfg.sl_mark_mult * pos.entry_credit:
            reason = "STOP_LOSS_MARK"
        if reason is None and time_stop:
            reason = "TIME_STOP_T1"
        if reason is None:
            return None

        xf = friction_model.basket_friction([
            {"side": "BUY", "opt_type": pos.opt_type.lower(), "price": buy_back, "quantity": pos.quantity},
            {"side": "SELL", "opt_type": pos.opt_type.lower(), "price": sell_out, "quantity": pos.quantity},
        ])["total"]
        return self._close(pos, d, spot, close_cost, xf, reason)

    def _close(self, pos, d, spot, exit_cost, exit_friction, reason) -> ClosedTrade:
        gross = (pos.entry_credit - exit_cost) * pos.quantity
        friction = round(pos.entry_friction + exit_friction, 2)
        return ClosedTrade(
            entry_date=pos.entry_date.isoformat(), exit_date=d.isoformat(),
            strategy=pos.strategy, sell_strike=pos.sell_strike,
            buy_strike=pos.buy_strike, expiry=pos.expiry.isoformat(),
            entry_credit=pos.entry_credit, exit_cost=round(exit_cost, 2),
            exit_reason=reason, lots=pos.lots, quantity=pos.quantity,
            gross_pnl=round(gross, 2), friction=friction,
            net_pnl=round(gross - friction, 2),
            days_held=(d - pos.entry_date).days,
            entry_spot=round(pos.entry_spot, 2), exit_spot=round(spot, 2),
            pcr_at_entry=pos.pcr, short_delta=pos.short_delta,
        )

    # ── main loop ──────────────────────────────────────────────────────────
    def run(self, dates: List[datetime.date],
            seed_closes: Optional[List[float]] = None,
            seed_iv_hist: Optional[List[float]] = None) -> Dict[str, Any]:
        cfg = self.cfg
        self._closes = list(seed_closes or [])
        self._iv_hist = list(seed_iv_hist or [])
        self._equity = cfg.equity0
        self._closed = []
        self.skip_reasons = {}
        open_pos: List[Position] = []
        daily_pnl: Dict[datetime.date, float] = {}
        last_chain = None

        for d in sorted(dates):
            chain = self.load_chain(d, cfg.underlying)
            if chain is None:
                continue
            last_chain = chain
            daily_pnl.setdefault(d, 0.0)
            spot = float(chain["spot"] or 0)

            # exits first
            still = []
            for pos in open_pos:
                if pos.entry_date >= d:
                    still.append(pos)
                    continue
                closed = self._try_exit(d, chain, pos)
                if closed:
                    self._closed.append(closed)
                    self._equity += closed.net_pnl
                    daily_pnl[d] += closed.net_pnl
                else:
                    still.append(pos)
            open_pos = still

            # entry at close
            if len(open_pos) < cfg.max_open:
                pos = self._try_enter(d, chain)
                if pos:
                    open_pos.append(pos)

            # update state AFTER decisions (no lookahead)
            if spot > 0:
                self._closes.append(spot)
                self._closes = self._closes[-300:]
                exp_iv = bhavcopy.nearest_expiry(chain, d, min_days=cfg.min_days_to_expiry)
                if exp_iv:
                    iv = rf.atm_straddle_iv(
                        spot, exp_iv, d,
                        lambda k, ty, _c=chain, _e=exp_iv: _leg_close(_c, _e, k, ty),
                        cfg.strike_interval)
                    self._iv_hist.append(iv)
                    self._iv_hist = self._iv_hist[-252:]

        # window-end mark for anything still open
        for pos in open_pos:
            if last_chain is None:
                continue
            spot = float(last_chain["spot"] or pos.entry_spot)
            marks = self._mark_exit(last_chain, pos)
            cost = marks[0] if marks else self._intrinsic(pos, spot)
            closed = self._close(pos, last_chain["date"], spot, cost, 0.0, "WINDOW_END_MARK")
            self._closed.append(closed)
            self._equity += closed.net_pnl
            daily_pnl[last_chain["date"]] = daily_pnl.get(last_chain["date"], 0.0) + closed.net_pnl

        return self._metrics(self._closed, daily_pnl)

    # ── metrics ────────────────────────────────────────────────────────────
    def _metrics(self, trades: List[ClosedTrade],
                 daily_pnl: Dict[datetime.date, float]) -> Dict[str, Any]:
        cfg = self.cfg
        n = len(trades)
        pnls = [t.net_pnl for t in trades]
        wins = [p for p in pnls if p > 0]
        losses = [p for p in pnls if p <= 0]

        equity_curve, eq = [], cfg.equity0
        for d in sorted(daily_pnl):
            eq += daily_pnl[d]
            equity_curve.append((d.isoformat(), round(eq, 2)))

        peak, max_dd = cfg.equity0, 0.0
        for _, v in equity_curve:
            peak = max(peak, v)
            max_dd = max(max_dd, peak - v)

        max_consec, cur = 0, 0
        for t in trades:
            cur = cur + 1 if t.net_pnl <= 0 else 0
            max_consec = max(max_consec, cur)

        # daily returns on equity for Sharpe
        rets, prev = [], cfg.equity0
        for _, v in equity_curve:
            if prev > 0:
                rets.append((v - prev) / prev)
            prev = v
        sharpe = 0.0
        if len(rets) > 1:
            mean = sum(rets) / len(rets)
            var = sum((x - mean) ** 2 for x in rets) / (len(rets) - 1)
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

        gw, gl = sum(wins), -sum(losses)
        return {
            "config": asdict(cfg),
            "trades": [asdict(t) for t in trades],
            "equity_curve": equity_curve,
            "skip_reasons": dict(sorted(self.skip_reasons.items(),
                                        key=lambda kv: -kv[1])),
            "summary": {
                "n_trades": n,
                "win_rate": round(len(wins) / n, 3) if n else 0.0,
                "expectancy_per_trade": round(sum(pnls) / n, 2) if n else 0.0,
                "total_net_pnl": round(sum(pnls), 2),
                "total_gross_pnl": round(sum(t.gross_pnl for t in trades), 2),
                "total_friction": round(sum(t.friction for t in trades), 2),
                "return_pct": round(100 * sum(pnls) / cfg.equity0, 2),
                "profit_factor": round(gw / gl, 2) if gl > 0 else (float("inf") if gw > 0 else 0.0),
                "sharpe_annualized": round(sharpe, 2),
                "max_drawdown": round(max_dd, 2),
                "max_drawdown_pct": round(100 * max_dd / cfg.equity0, 2),
                "max_consecutive_losses": max_consec,
                "avg_days_held": round(sum(t.days_held for t in trades) / n, 1) if n else 0.0,
                "final_equity": round(self._equity, 2),
                "per_strategy": per_strategy,
                "exit_reasons": per_reason,
            },
        }


def print_report(result: Dict[str, Any], show_trades: bool = True):
    s = result["summary"]
    cfg = result["config"]
    print("\n" + "=" * 70)
    print(f"HONEST EOD BACKTEST v2 — {cfg['underlying']} | delta {cfg['delta_target']} | "
          f"width {cfg['width_intervals']}x{cfg['strike_interval']} | tp {cfg['tp_ratio']} | "
          f"sl {cfg['sl_mode']} | gates {'ON' if cfg['use_gates'] else 'OFF'}")
    print("=" * 70)
    print(f"  trades: {s['n_trades']}  win rate: {s['win_rate']*100:.1f}%  "
          f"PF: {s['profit_factor']}  Sharpe: {s['sharpe_annualized']}")
    print(f"  net P&L: Rs {s['total_net_pnl']:,.2f} ({s['return_pct']:+.2f}% on equity)  "
          f"gross {s['total_gross_pnl']:,.2f} - friction {s['total_friction']:,.2f}")
    print(f"  expectancy: Rs {s['expectancy_per_trade']:,.2f}/trade  "
          f"max DD: Rs {s['max_drawdown']:,.2f} ({s['max_drawdown_pct']:.2f}%)  "
          f"consec losses: {s['max_consecutive_losses']}")
    print(f"  per strategy: {s['per_strategy']}")
    print(f"  exit reasons: {s['exit_reasons']}")
    print(f"  skips: {result['skip_reasons']}")
    if show_trades:
        print("-" * 70)
        for t in result["trades"]:
            print(f"  {t['entry_date']}->{t['exit_date']} {t['strategy']:<17} "
                  f"{t['sell_strike']:.0f}/{t['buy_strike']:.0f} d{t['short_delta']:.2f} "
                  f"x{t['lots']}L cr {t['entry_credit']:>6.2f} {t['exit_reason']:<18} "
                  f"net {t['net_pnl']:>10.2f}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", required=True)
    ap.add_argument("--end", required=True)
    ap.add_argument("--underlying", default="NIFTY")
    ap.add_argument("--no-gates", action="store_true")
    ap.add_argument("--no-download", action="store_true")
    args = ap.parse_args()

    start = datetime.date.fromisoformat(args.start)
    end = datetime.date.fromisoformat(args.end)
    if not args.no_download:
        bhavcopy.download_range(start, end)

    dates = []
    d = start
    while d <= end:
        if d.weekday() < 5:
            dates.append(d)
        d += datetime.timedelta(days=1)

    cfg = Config(underlying=args.underlying, use_gates=not args.no_gates)
    result = RealBacktester(cfg).run(dates)
    print_report(result)


if __name__ == "__main__":
    main()
