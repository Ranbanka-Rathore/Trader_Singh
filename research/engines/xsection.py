"""Arena 2 — cross-sectional equities via stock futures.

Rank the F&O stock universe on a signal, go long the strongest and short the
weakest, rebalance monthly. The classic version of this is momentum, and the
classic version is also where the arena's real question lives: not "does the
cross-section have structure" — it does, everywhere — but "does any of it survive
whole lots and margin at Rs 15,00,000".

WHY THE CAPACITY CONSTRAINT IS THE POINT
-----------------------------------------
A stock-future lot is a fixed notional: RELIANCE at Rs 1,400 with a lot of 500 is
Rs 7L of exposure, needing roughly Rs 1L of margin. A decile long/short over a
220-name universe wants ~44 positions. At Rs 15L with a 60% margin budget there
is room for about eight. So the portfolio that the signal describes cannot be
held, and what actually gets traded is a handful of names — a different, much
noisier strategy with most of the diversification removed.

This engine models that instead of assuming it away: positions are taken from the
extremes inward until the margin budget is exhausted, and the shortfall is
counted. If the arena dies on capacity rather than on signal, that is a finding
worth having cheaply, and it is one no fractional-lot backtest could ever show.

NO SURVIVORSHIP BIAS: the universe is whatever the bhavcopy listed that day, so
names that left F&O are present in the eras when they traded and absent after.
"""
import datetime
import os
import statistics as st
import sys
from dataclasses import dataclass, replace
from typing import Any, Dict, List, Optional, Tuple

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from backend.app.core import friction_model
from backtest import futures
from research import engines


@dataclass
class XSectionConfig:
    kind: str = "stock"
    equity0: float = 1_500_000.0
    gate: str = "strict"
    roll_days: int = futures.DEFAULT_ROLL_DAYS
    # signal: return from t-`mom_lookback` to t-`mom_skip`, the standard
    # 12-1 construction that drops the most recent month to avoid short-term
    # reversal contaminating a momentum measurement
    mom_lookback: int = 252           # grid
    mom_skip: int = 21                # grid
    n_per_side: int = 5               # names wanted per side before capacity
    allow_short: bool = True
    rebalance_days: int = 21          # grid: CALENDAR days, so ~3 weeks
    # universe screen
    min_adv_rs: float = 50_000_000.0  # Rs 5 crore average daily traded value
    adv_lookback: int = 21
    # capacity — the constraint that decides this arena
    margin_frac: float = 0.15         # SPAN+exposure as a share of notional
    # Gross notional is max_gross_margin_frac / margin_frac times equity, so this
    # IS the leverage dial: 0.30 gives 2x gross, i.e. roughly 1x long and 1x
    # short — a conventional market-neutral book. It is set on risk grounds, not
    # fitted: at 0.60 (4x gross) the same strategy draws down more than the whole
    # account, which is outside the feasible set whatever its P&L looks like.
    # Anything above this belongs in a registered hypothesis, declared up front.
    max_gross_margin_frac: float = 0.30
    max_lots_per_name: int = 5
    slippage_bps: float = 5.0         # single stocks are wider than the index


@dataclass
class _Held:
    symbol: str
    direction: int
    entry_date: datetime.date
    entry_price: float
    lots: int
    lot_size: int
    entry_friction: float


class XSectionEngine:
    name = "cross_sectional"
    arena = "cross_sectional"

    GRID = [{"mom_lookback": lb, "rebalance_days": rb, "n_per_side": n}
            for lb in (126, 252) for rb in (21, 42) for n in (3, 5)]

    def coerce(self, name: str, raw: Any) -> Any:
        return engines.coerce_field(XSectionConfig, name, raw)

    def build(self, hypothesis: Dict[str, Any],
              gate: Optional[str] = None) -> XSectionConfig:
        cfg = XSectionConfig(equity0=float(hypothesis.get("equity", 1_500_000.0)),
                             gate=gate or hypothesis.get("gate", "strict"))
        overrides = {k: self.coerce(k, v)
                     for k, v in (hypothesis.get("config") or {}).items()}
        return replace(cfg, **overrides) if overrides else cfg

    def with_params(self, cfg: XSectionConfig, **params) -> XSectionConfig:
        return replace(cfg, **params)

    def stress(self, cfg: XSectionConfig, mult: float) -> XSectionConfig:
        """Config with execution costs multiplied, for the Section 5 cost stress."""
        return replace(cfg, slippage_bps=cfg.slippage_bps * mult)

    def grid(self) -> List[Dict[str, Any]]:
        return list(self.GRID)

    def warmup_days(self, cfg: XSectionConfig) -> int:
        """Calendar days needed before a 12-1 momentum rank exists at all.

        This is over a year, and it is the reason warmup is asked of the engine:
        run with the option arena's 45 days, every early fold would rank an empty
        universe and the arena would look like it simply does not trade.
        """
        return int((cfg.mom_lookback + cfg.adv_lookback) * 1.5) + 30

    # ── the run ──────────────────────────────────────────────────────────────
    def run(self, cfg: XSectionConfig, dates: List[datetime.date],
            provider=None) -> Dict[str, Any]:
        panel = futures.build_panel(dates, kind=cfg.kind, gate=cfg.gate,
                                    roll_days=cfg.roll_days, loader=provider)
        # index each symbol's bars by date for O(1) lookup on rebalance days
        pos_of: Dict[str, Dict[datetime.date, int]] = {
            s: {b.date: i for i, b in enumerate(ser.bars)}
            for s, ser in panel.series.items()}

        trades: List[Dict[str, Any]] = []
        skips: Dict[str, int] = {}
        held: Dict[str, _Held] = {}
        equity = cfg.equity0
        rebalances = 0
        wanted_total = taken_total = 0

        def skip(why: str, n: int = 1):
            skips[why] = skips.get(why, 0) + n

        all_dates = sorted({b.date for ser in panel.series.values() for b in ser.bars})
        last_rebalance: Optional[datetime.date] = None

        for d in all_dates:
            if last_rebalance is not None and (d - last_rebalance).days < cfg.rebalance_days:
                continue
            # ── close everything held; this is a full rebalance, not an overlay
            for sym, h in list(held.items()):
                i = pos_of.get(sym, {}).get(d)
                if i is None:
                    skip("exit_bar_missing")
                    continue
                trades.append(self._close(h, panel.series[sym].bars[i], d, cfg))
                equity += trades[-1]["net_pnl"]
                del held[sym]

            ranked = self._rank(panel, pos_of, d, cfg)
            if len(ranked) < 2 * cfg.n_per_side:
                skip("universe_too_thin")
                last_rebalance = d
                continue
            rebalances += 1
            last_rebalance = d

            longs = ranked[-cfg.n_per_side:][::-1]
            shorts = ranked[:cfg.n_per_side] if cfg.allow_short else []
            wanted = len(longs) + len(shorts)
            wanted_total += wanted

            # Take from the extremes inward until margin runs out. The strongest
            # long and the weakest short are the highest-conviction names, so
            # capacity should bite the middle of the book, not the edges.
            budget = cfg.max_gross_margin_frac * equity
            used = 0.0
            queue: List[Tuple[str, int, float]] = []
            for rank_i in range(cfg.n_per_side):
                if rank_i < len(longs):
                    queue.append((longs[rank_i][0], 1, longs[rank_i][1]))
                if rank_i < len(shorts):
                    queue.append((shorts[rank_i][0], -1, shorts[rank_i][1]))

            for sym, direction, _score in queue:
                i = pos_of.get(sym, {}).get(d)
                if i is None:
                    skip("entry_bar_missing")
                    continue
                bar = panel.series[sym].bars[i]
                margin_per_lot = cfg.margin_frac * bar.close * bar.lot
                if margin_per_lot <= 0:
                    skip("degenerate_margin")
                    continue
                # equal-notional target, then floored to whole lots
                target_margin = budget / max(wanted, 1)
                lots = int(target_margin // margin_per_lot)
                if lots < 1:
                    lots = 1        # a name is either in the book or it is not
                lots = min(lots, cfg.max_lots_per_name)
                if used + lots * margin_per_lot > budget:
                    skip("margin_budget_exhausted")
                    continue
                used += lots * margin_per_lot

                fill = bar.close * (1 + cfg.slippage_bps / 10_000.0 * direction)
                qty = lots * bar.lot
                fr = friction_model.leg_friction(
                    side="BUY" if direction > 0 else "SELL",
                    price=fill, quantity=qty, instrument="future")["total"]
                held[sym] = _Held(symbol=sym, direction=direction, entry_date=d,
                                  entry_price=fill, lots=lots, lot_size=bar.lot,
                                  entry_friction=fr)
                taken_total += 1

        for sym in held:
            skip("open_at_end")

        fill_rate = (100.0 * taken_total / wanted_total) if wanted_total else 0.0
        return {
            "config": {k: v for k, v in cfg.__dict__.items()},
            "trades": trades,
            "summary": engines.summarise(trades, cfg.equity0, {
                "rebalances": rebalances,
                "positions_wanted": wanted_total,
                "positions_taken": taken_total,
                "capacity_fill_rate_pct": round(fill_rate, 1),
                "symbols_traded": len({t["symbol"] for t in trades}),
                # panel health belongs in the report too: a run whose universe
                # silently shrank because lots went missing looks exactly like a
                # strategy that found nothing to trade
                "panel_symbols": len(panel.series),
                "panel_missing_lot": panel.missing_lot,
            }),
            "skip_reasons": dict(sorted(skips.items(), key=lambda kv: -kv[1])),
            "liquidity_gate": {
                "preset": panel.gate_name,
                "legs_checked": panel.checked,
                "legs_fillable": panel.fillable,
                "pass_rate_pct": round(panel.pass_rate, 2),
                "refusals": panel.refusals,
            },
            "panel": {"symbols": len(panel.series), "rolls": panel.rolls,
                      "missing_lot": panel.missing_lot},
        }

    def _rank(self, panel, pos_of, d: datetime.date,
              cfg: XSectionConfig) -> List[Tuple[str, float]]:
        """(symbol, momentum) ascending, for names passing the liquidity screen."""
        out: List[Tuple[str, float]] = []
        for sym, ser in panel.series.items():
            i = pos_of.get(sym, {}).get(d)
            if i is None or i < cfg.mom_lookback:
                continue
            idx = ser.index
            past = idx[i - cfg.mom_lookback]
            recent = idx[i - cfg.mom_skip]
            if past <= 0:
                continue
            # average daily traded value over the screen window, in rupees
            window = ser.bars[max(0, i - cfg.adv_lookback):i + 1]
            adv = st.mean([b.close * b.volume for b in window]) if window else 0.0
            if adv < cfg.min_adv_rs:
                continue
            out.append((sym, recent / past - 1.0))
        out.sort(key=lambda t: t[1])
        return out

    def _close(self, h: _Held, bar, d: datetime.date,
               cfg: XSectionConfig) -> Dict[str, Any]:
        fill = bar.close * (1 - cfg.slippage_bps / 10_000.0 * h.direction)
        qty = h.lots * h.lot_size
        exit_fr = friction_model.leg_friction(
            side="SELL" if h.direction > 0 else "BUY",
            price=fill, quantity=qty, instrument="future")["total"]
        gross = (fill - h.entry_price) * h.direction * qty
        friction = h.entry_friction + exit_fr
        return {
            "symbol": h.symbol,
            "entry_date": h.entry_date.isoformat(),
            "exit_date": d.isoformat(),
            "direction": "LONG" if h.direction > 0 else "SHORT",
            "strategy": f"XSECT_{'LONG' if h.direction > 0 else 'SHORT'}",
            "entry_price": round(h.entry_price, 2),
            "exit_price": round(fill, 2),
            "lots": h.lots, "quantity": qty,
            "exit_reason": "rebalance",
            "gross_pnl": round(gross, 2),
            "friction": round(friction, 2),
            "net_pnl": round(gross - friction, 2),
            "days_held": (d - h.entry_date).days,
        }


engines.register(XSectionEngine())
