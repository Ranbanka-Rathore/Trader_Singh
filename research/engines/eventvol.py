"""Arena 4 — event-driven volatility, on single-stock earnings.

THE CLAIM THIS ARENA EXISTS TO TEST
------------------------------------
Implied volatility rises into an earnings announcement and collapses the moment
the uncertainty resolves. If the premium paid for that uncertainty exceeds the
move that actually happens, selling it before the event and buying it back after
is an edge. If it does not, it is a way to be short a gap.

Structure: sell the at-the-money straddle a few days before the meeting, buy it
back the day after, on the nearest expiry that survives the event. Wings are
bought by default (`wing_pct` > 0), making it an iron butterfly with a computable
maximum loss — a naked short straddle over an earnings gap has unbounded risk and
is inadmissible against a Rs 1,00,000 drawdown budget. Set `wing_pct=0` to test
the naked version deliberately.

THE LOOKAHEAD GUARD IS THE WHOLE GAME HERE
-------------------------------------------
An earnings-timing strategy is trivially profitable if it may use the event
calendar as of today. It may not: `events.events_known_by()` filters to meetings
already intimated to the exchange on the entry date, and companies give a median
of 7-11 days' notice — sometimes one. Positioning ahead of an announcement nobody
had heard of would be invisible in the P&L and completely fatal, so the filter is
applied here and nowhere else, and the count of events dropped for insufficient
notice is reported.

WHAT WILL PROBABLY KILL IT
---------------------------
Stock options, not the signal. Near-ATM legs on liquid F&O names do print most
days, but a four-legged structure needs all four to be fillable at entry AND at
exit, and the friction on four stock-option legs is heavy. Before 2024 the NSE
schema carries no trade count at all, so `strict` cannot be evaluated there —
use `strict_legacy` on a pre-2024 window and read the two eras separately.
"""
import datetime
import os
import sys
from dataclasses import dataclass, replace
from typing import Any, Dict, List, Optional, Tuple

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from backend.app.core import friction_model
from backtest import bhavcopy, events as ev
from backtest.liquidity_gate import LiquidityGate, gate_by_name
from research import engines


@dataclass
class EventVolConfig:
    equity0: float = 1_500_000.0
    gate: str = "strict"
    # timing, in trading days relative to the meeting
    entry_days_before: int = 2        # grid
    exit_days_after: int = 1          # grid
    min_notice_days: int = 3          # the event must have been public this long
    # structure
    wing_pct: float = 0.10            # wings at +-10% of spot; 0 => naked straddle
    min_credit_frac: float = 0.004    # credit must beat friction: >=0.4% of spot
    # universe
    max_symbols: int = 60             # most-liquid F&O names by option turnover
    universe: Tuple[str, ...] = ()    # explicit override
    # risk
    risk_frac_hard_cap: float = 0.02  # one lot may risk this much of equity
    max_lots: int = 5
    max_concurrent: int = 4
    slippage_per_leg: float = 0.75


@dataclass
class _Open:
    symbol: str
    event_date: datetime.date
    entry_date: datetime.date
    expiry: datetime.date
    strike: float
    wing_lo: float
    wing_hi: float
    credit: float                     # per share, net of wings
    max_loss_per_share: float
    lots: int
    lot_size: int
    entry_friction: float


class EventVolEngine:
    name = "event_vol"
    arena = "event_vol"

    EXTRA_FIELDS = frozenset({
        "events_considered", "events_traded", "symbols_traded",
        "dropped_no_notice", "dropped_unfillable", "capacity_fill_rate_pct",
    })

    GRID = [{"entry_days_before": b, "exit_days_after": a, "wing_pct": w}
            for b in (2, 5) for a in (1, 2) for w in (0.10, 0.15)]

    def coerce(self, name: str, raw: Any) -> Any:
        return engines.coerce_field(EventVolConfig, name, raw)

    def build(self, hypothesis: Dict[str, Any],
              gate: Optional[str] = None) -> EventVolConfig:
        cfg = EventVolConfig(equity0=float(hypothesis.get("equity", 1_500_000.0)),
                             gate=gate or hypothesis.get("gate", "strict"))
        overrides = {k: self.coerce(k, v)
                     for k, v in (hypothesis.get("config") or {}).items()}
        return replace(cfg, **overrides) if overrides else cfg

    def with_params(self, cfg, **params):
        return replace(cfg, **params)

    def stress(self, cfg, mult: float):
        return replace(cfg, slippage_per_leg=cfg.slippage_per_leg * mult)

    def grid(self, cfg=None) -> List[Dict[str, Any]]:
        # `cfg` is accepted and ignored: only the trend engine's grid depends on
        # the config, but the caller cannot know that, so all engines take it.
        return list(self.GRID)

    def warmup_days(self, cfg) -> int:
        """No indicator history is needed — the signal is a date on a calendar."""
        return 10

    # ── universe ─────────────────────────────────────────────────────────────
    def _universe(self, dates: List[datetime.date],
                  cfg: EventVolConfig) -> List[str]:
        """Most-liquid option underlyings, measured not assumed.

        Sampled from a handful of sessions rather than all of them: this only has
        to separate names with an options market from names with a listing.
        """
        if cfg.universe:
            return [s.upper() for s in cfg.universe]
        turnover: Dict[str, float] = {}
        probes = dates[::max(len(dates) // 8, 1)][:8]
        for d in probes:
            df = bhavcopy.read_df_cached(d)
            if df is None:
                continue
            sto = df[df["FinInstrmTp"] == "STO"]
            for sym, grp in sto.groupby("TckrSymb"):
                turnover[str(sym)] = turnover.get(str(sym), 0.0) + float(
                    grp["TtlTradgVol"].fillna(0).astype(float).sum())
        ranked = sorted(turnover.items(), key=lambda kv: -kv[1])
        return [s for s, _ in ranked[:cfg.max_symbols]]

    # ── the run ──────────────────────────────────────────────────────────────
    def run(self, cfg: EventVolConfig, dates: List[datetime.date],
            provider=None) -> Dict[str, Any]:
        if not dates:
            return self._empty(cfg, "no_trading_days")
        load_chain = provider or bhavcopy.load_chain
        gate = LiquidityGate(gate_by_name(cfg.gate))
        idx = {d: i for i, d in enumerate(dates)}

        universe = self._universe(dates, cfg)
        calendar = ev.load_events(dates[0], dates[-1], symbols=universe)

        trades: List[Dict[str, Any]] = []
        skips: Dict[str, int] = {}
        equity = cfg.equity0
        considered = traded = no_notice = unfillable = 0

        def skip(why: str):
            nonlocal skips
            skips[why] = skips.get(why, 0) + 1

        # Group by entry date so `max_concurrent` means something across symbols.
        plan: Dict[datetime.date, List[ev.Event]] = {}
        for e in calendar:
            i = idx.get(e.date)
            if i is None:                       # meeting on a non-trading day
                nxt = [d for d in dates if d >= e.date]
                if not nxt:
                    continue
                i = idx[nxt[0]]
            ei = i - cfg.entry_days_before
            xi = i + cfg.exit_days_after
            if ei < 0 or xi >= len(dates):
                skip("window_outside_archive")
                continue
            plan.setdefault(dates[ei], []).append(e)

        for d in sorted(plan):
            open_today = 0
            for e in sorted(plan[d], key=lambda x: x.symbol):
                considered += 1
                # ── the lookahead guard ─────────────────────────────────────
                if e.announced_at > d or (e.date - d).days < 0:
                    no_notice += 1
                    skip("not_yet_announced")
                    continue
                if (d - e.announced_at).days < cfg.min_notice_days:
                    no_notice += 1
                    skip("notice_too_short")
                    continue
                if open_today >= cfg.max_concurrent:
                    skip("max_concurrent")
                    continue

                pos = self._try_enter(e, d, load_chain, gate, cfg, equity)
                if pos is None:
                    unfillable += 1
                    skip(self._last_skip or "no_structure")
                    continue

                xi = idx[d] + cfg.entry_days_before + cfg.exit_days_after
                exit_d = dates[min(xi, len(dates) - 1)]
                tr = self._close(pos, exit_d, load_chain, gate, cfg)
                if tr is None:
                    unfillable += 1
                    skip("exit_unfillable")
                    continue
                trades.append(tr)
                equity += tr["net_pnl"]
                traded += 1
                open_today += 1

        fill_rate = (100.0 * traded / considered) if considered else 0.0
        return {
            "config": {k: v for k, v in cfg.__dict__.items()},
            "trades": trades,
            "summary": engines.summarise(trades, cfg.equity0, {
                "events_considered": considered,
                "events_traded": traded,
                "dropped_no_notice": no_notice,
                "dropped_unfillable": unfillable,
                "capacity_fill_rate_pct": round(fill_rate, 1),
                "symbols_traded": len({t["symbol"] for t in trades}),
            }),
            "skip_reasons": dict(sorted(skips.items(), key=lambda kv: -kv[1])),
            "liquidity_gate": {
                "preset": gate.cfg.name,
                "legs_checked": gate.checked,
                "legs_fillable": gate.passed,
                "pass_rate_pct": round(gate.pass_rate, 2),
                "refusals": dict(sorted(gate.rejections.items(),
                                        key=lambda kv: -kv[1])),
            },
            "universe": len(universe),
            "calendar_events": len(calendar),
        }

    _last_skip: Optional[str] = None

    # ── structure ────────────────────────────────────────────────────────────
    def _legs(self, chain, expiry, strike, wing_lo, wing_hi, cfg):
        """(rows, prices) for the structure, or None if any leg is missing."""
        want = [(strike, "CE"), (strike, "PE")]
        if cfg.wing_pct > 0:
            want += [(wing_hi, "CE"), (wing_lo, "PE")]
        rows, prices = [], []
        for k, t in want:
            r = chain["options"].get((expiry, float(k), t))
            if not r:
                return None, None
            rows.append(r)
            prices.append(float(r.get("close") or 0.0))
        return rows, prices

    def _try_enter(self, e: ev.Event, d: datetime.date, load_chain,
                   gate: LiquidityGate, cfg: EventVolConfig,
                   equity: float) -> Optional[_Open]:
        self._last_skip = None
        chain = load_chain(d, e.symbol)
        if not chain or not chain.get("options"):
            self._last_skip = "no_chain"
            return None
        spot = float(chain.get("spot") or 0.0)
        if spot <= 0:
            self._last_skip = "no_spot"
            return None

        # The expiry must survive the event, or the option settles before the
        # news it was bought to cover.
        after = sorted({x for (x, _, _) in chain["options"] if x > e.date})
        if not after:
            self._last_skip = "no_expiry_past_event"
            return None
        expiry = after[0]

        strikes = sorted({k for (x, k, _) in chain["options"] if x == expiry})
        if not strikes:
            self._last_skip = "no_strikes"
            return None
        strike = min(strikes, key=lambda k: abs(k - spot))
        wing_hi = min((k for k in strikes if k >= strike * (1 + cfg.wing_pct)),
                      default=None) if cfg.wing_pct > 0 else strike
        wing_lo = max((k for k in strikes if k <= strike * (1 - cfg.wing_pct)),
                      default=None) if cfg.wing_pct > 0 else strike
        if cfg.wing_pct > 0 and (wing_hi is None or wing_lo is None):
            self._last_skip = "no_wings_listed"
            return None

        rows, prices = self._legs(chain, expiry, strike, wing_lo, wing_hi, cfg)
        if rows is None:
            self._last_skip = "leg_missing"
            return None
        ok, why = gate.spread_ok(rows)
        if not ok:
            self._last_skip = f"entry_{why}"
            return None

        slip = cfg.slippage_per_leg
        credit = (max(prices[0] - slip, 0.05) + max(prices[1] - slip, 0.05))
        if cfg.wing_pct > 0:
            credit -= (prices[2] + slip) + (prices[3] + slip)
        if credit <= cfg.min_credit_frac * spot:
            self._last_skip = "credit_too_thin"
            return None

        lot = int(rows[0].get("lot") or 0)
        if lot <= 0:
            self._last_skip = "unknown_lot"
            return None

        if cfg.wing_pct > 0:
            width = max(wing_hi - strike, strike - wing_lo)
            max_loss_ps = max(width - credit, 0.01)
        else:
            # A naked straddle has no defined maximum. Size it against a
            # deliberately pessimistic proxy — twice the credit collected as the
            # adverse move — and say so, rather than pretending it is bounded.
            max_loss_ps = 2.0 * credit
        risk_per_lot = max_loss_ps * lot
        if risk_per_lot > cfg.risk_frac_hard_cap * equity:
            self._last_skip = "one_lot_exceeds_risk_cap"
            return None
        lots = max(1, min(cfg.max_lots,
                          int((cfg.risk_frac_hard_cap * equity) // risk_per_lot)))

        qty = lots * lot
        legs = [{"side": "SELL", "price": max(prices[0] - slip, 0.05), "quantity": qty},
                {"side": "SELL", "price": max(prices[1] - slip, 0.05), "quantity": qty}]
        if cfg.wing_pct > 0:
            legs += [{"side": "BUY", "price": prices[2] + slip, "quantity": qty},
                     {"side": "BUY", "price": prices[3] + slip, "quantity": qty}]
        fr = friction_model.basket_friction(legs)["total"]

        return _Open(symbol=e.symbol, event_date=e.date, entry_date=d,
                     expiry=expiry, strike=strike, wing_lo=wing_lo or strike,
                     wing_hi=wing_hi or strike, credit=credit,
                     max_loss_per_share=max_loss_ps, lots=lots, lot_size=lot,
                     entry_friction=fr)

    def _close(self, pos: _Open, d: datetime.date, load_chain,
               gate: LiquidityGate, cfg: EventVolConfig) -> Optional[Dict[str, Any]]:
        chain = load_chain(d, pos.symbol)
        if not chain or not chain.get("options"):
            return None
        rows, prices = self._legs(chain, pos.expiry, pos.strike,
                                  pos.wing_lo, pos.wing_hi, cfg)
        if rows is None:
            return None
        # Only the SHORT legs must be fillable: buying them back is the trade
        # that has to happen. A long wing that cannot be sold is abandoned, which
        # is a cost already assumed, not a fill invented.
        ok, why = gate.spread_ok(rows[:2])
        if not ok:
            return None

        slip = cfg.slippage_per_leg
        cost = (prices[0] + slip) + (prices[1] + slip)
        if cfg.wing_pct > 0:
            cost -= max(prices[2] - slip, 0.0) + max(prices[3] - slip, 0.0)

        qty = pos.lots * pos.lot_size
        legs = [{"side": "BUY", "price": prices[0] + slip, "quantity": qty},
                {"side": "BUY", "price": prices[1] + slip, "quantity": qty}]
        if cfg.wing_pct > 0:
            legs += [{"side": "SELL", "price": max(prices[2] - slip, 0.0), "quantity": qty},
                     {"side": "SELL", "price": max(prices[3] - slip, 0.0), "quantity": qty}]
        exit_fr = friction_model.basket_friction(legs)["total"]

        gross = (pos.credit - cost) * qty
        friction = pos.entry_friction + exit_fr
        return {
            "symbol": pos.symbol,
            "entry_date": pos.entry_date.isoformat(),
            "exit_date": d.isoformat(),
            "event_date": pos.event_date.isoformat(),
            "strategy": "EARNINGS_SHORT_VOL",
            "expiry": pos.expiry.isoformat(),
            "strike": pos.strike,
            "entry_credit": round(pos.credit, 2),
            "exit_cost": round(cost, 2),
            "lots": pos.lots, "quantity": qty,
            "exit_reason": "post_event",
            "gross_pnl": round(gross, 2),
            "friction": round(friction, 2),
            "net_pnl": round(gross - friction, 2),
            "days_held": (d - pos.entry_date).days,
        }

    def _empty(self, cfg, why: str) -> Dict[str, Any]:
        return {"config": {k: v for k, v in cfg.__dict__.items()}, "trades": [],
                "summary": engines.summarise([], cfg.equity0, {
                    "events_considered": 0, "events_traded": 0,
                    "dropped_no_notice": 0, "dropped_unfillable": 0,
                    "capacity_fill_rate_pct": 0.0, "symbols_traded": 0}),
                "skip_reasons": {why: 1},
                "liquidity_gate": {"preset": cfg.gate, "legs_checked": 0,
                                   "legs_fillable": 0, "pass_rate_pct": 0.0,
                                   "refusals": {}}}


engines.register(EventVolEngine())
