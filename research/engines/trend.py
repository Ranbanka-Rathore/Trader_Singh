"""Arena 3 — directional trend on liquid futures.

The charter's note on this arena is that the edge, if any, "lives in sizing and
risk management rather than structure". So the structure here is deliberately the
plainest thing that could work — a Donchian channel breakout — and the care goes
into the parts that actually decide the outcome: roll-safe returns, volatility
sizing, whole lots, and real friction.

WHERE THE SIGNAL LIVES AND WHERE THE MONEY LIVES
------------------------------------------------
Signals are computed on the synthetic compounded index from `backtest.futures`,
which is roll-safe and therefore the only series in which a "20-day high" means
anything. P&L is computed on actual contract closes with actual lot sizes. Mixing
those up either way is how a futures backtest books the roll gap as alpha.

Risk is expressed in return space too: the stop is a multiple of realised
volatility, so a Rs 1,600 stock and a 24,000-point index are sized on the same
scale. Position size comes out in WHOLE LOTS, and a trade whose minimum lot
exceeds the per-trade risk cap is skipped and counted, never fractionally sized.
At Rs 15L that constraint bites, and it is supposed to show.

TWO LIMITATIONS, STATED RATHER THAN HIDDEN
-------------------------------------------
1. NO INTEREST ON IDLE MARGIN. Futures carry is IN these returns — a rolled long
   earns the spot move minus financing, measured at ~0.6% per roll on NIFTY,
   about 7%/yr. What is NOT modelled is the interest the ~85% of equity not
   posted as margin would earn, which offsets it. So long P&L is understated and
   short P&L overstated, by roughly the carry rate times time-in-position. At
   typical holding periods of a week or two that is a few hundred rupees a trade
   — worth knowing, not worth a verdict.
2. STOPS ARE EOD, SO THEY OVERSHOOT. The stop is checked against the close and
   filled at the close, because that is the only price this archive can honestly
   claim. A gap through the level therefore books the full gap, and realised loss
   per trade routinely exceeds the intended risk. This is the pessimistic
   direction, and it is the truthful one for a daily-bar system.
"""
import datetime
import math
import os
import statistics as st
import sys
from dataclasses import dataclass, field, replace
from typing import Any, Dict, List, Optional, Tuple

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from backend.app.core import friction_model
from backtest import futures
from research import engines

# The liquid index futures. Stock futures are arena 2's universe; a trend system
# run over 200 correlated single stocks is a cross-sectional bet wearing a trend
# costume, and its results would not be independent of arena 2's.
DEFAULT_UNIVERSE = ("NIFTY", "BANKNIFTY", "FINNIFTY")


@dataclass
class TrendConfig:
    universe: Tuple[str, ...] = DEFAULT_UNIVERSE
    kind: str = "index"
    equity0: float = 1_500_000.0
    gate: str = "strict"
    roll_days: int = futures.DEFAULT_ROLL_DAYS
    # signal
    entry_lookback: int = 20          # breakout window (grid)
    exit_lookback: int = 10           # opposite-extreme exit window (grid)
    vol_lookback: int = 20            # realised-vol window for the stop
    stop_vol_mult: float = 2.5        # stop distance = mult x daily vol (grid)
    allow_short: bool = True
    # risk
    risk_frac: float = 0.0075         # fraction of equity risked to the stop
    risk_frac_hard_cap: float = 0.02  # a single lot may risk up to this, else skip
    max_open: int = 3
    max_lots: int = 20
    # costs
    slippage_bps: float = 2.0         # per side, on the contract price


def _vol(rets: List[Optional[float]], i: int, n: int) -> Optional[float]:
    """Realised daily vol over the n returns ending at i, or None if too thin."""
    window = [r for r in rets[max(0, i - n + 1):i + 1] if r is not None]
    if len(window) < max(5, n // 2):
        return None
    try:
        s = st.stdev(window)
    except st.StatisticsError:
        return None
    return s if s > 0 else None


@dataclass
class _Open:
    symbol: str
    direction: int                    # +1 long, -1 short
    entry_date: datetime.date
    entry_price: float
    entry_index: float
    lots: int
    lot_size: int
    stop_ret: float
    entry_friction: float
    expiry: datetime.date
    bars_held: int = 0


class TrendEngine:
    name = "futures_trend"
    arena = "futures_trend"

    # Fields a registered requirement may name, beyond the standard metrics.
    EXTRA_FIELDS = frozenset({
        "symbols_traded", "open_at_end", "panel_symbols", "panel_missing_lot",
    })

    # Fixed a priori, and narrow on purpose: three axes at two levels is 8
    # combinations, the same size as the option engine's grid, which keeps the
    # deflated-Sharpe hurdle in walkforward.py comparable across arenas.
    GRID = [{"entry_lookback": e, "exit_lookback": x, "stop_vol_mult": s}
            for e in (20, 55) for x in (10, 20) for s in (2.5, 4.0)]

    def coerce(self, name: str, raw: Any) -> Any:
        return engines.coerce_field(TrendConfig, name, raw)

    def build(self, hypothesis: Dict[str, Any], gate: Optional[str] = None) -> TrendConfig:
        cfg = TrendConfig(equity0=float(hypothesis.get("equity", 1_500_000.0)),
                          gate=gate or hypothesis.get("gate", "strict"))
        overrides = {k: self.coerce(k, v)
                     for k, v in (hypothesis.get("config") or {}).items()}
        return replace(cfg, **overrides) if overrides else cfg

    def with_params(self, cfg: TrendConfig, **params) -> TrendConfig:
        return replace(cfg, **params)

    def stress(self, cfg: TrendConfig, mult: float) -> TrendConfig:
        """Config with execution costs multiplied, for the Section 5 cost stress."""
        return replace(cfg, slippage_bps=cfg.slippage_bps * mult)

    def grid(self) -> List[Dict[str, Any]]:
        return list(self.GRID)

    def warmup_days(self, cfg: TrendConfig) -> int:
        """Calendar days needed before the longest breakout window is defined.

        Trading days are ~69% of calendar days, so the lookback is scaled up
        rather than passed through — a 55-bar channel needs about 80 calendar
        days of archive, not 55.
        """
        bars = max(cfg.entry_lookback, cfg.exit_lookback, cfg.vol_lookback)
        return int(bars * 1.5) + 30

    # ── the run ──────────────────────────────────────────────────────────────
    def run(self, cfg: TrendConfig, dates: List[datetime.date],
            provider=None) -> Dict[str, Any]:
        panel = futures.build_panel(dates, kind=cfg.kind, gate=cfg.gate,
                                    roll_days=cfg.roll_days, symbols=cfg.universe,
                                    loader=provider)
        trades: List[Dict[str, Any]] = []
        skips: Dict[str, int] = {}
        equity = cfg.equity0
        open_pos: Dict[str, _Open] = {}

        def skip(why: str):
            skips[why] = skips.get(why, 0) + 1

        # Walk the calendar so `max_open` means what it says across symbols.
        by_date: Dict[datetime.date, List[Tuple[str, int]]] = {}
        for sym, ser in panel.series.items():
            for i, bar in enumerate(ser.bars):
                by_date.setdefault(bar.date, []).append((sym, i))

        for d in sorted(by_date):
            for sym, i in sorted(by_date[d]):
                ser = panel.series[sym]
                bar = ser.bars[i]
                idx = ser.index

                # ── manage an open position first ────────────────────────────
                pos = open_pos.get(sym)
                if pos is not None:
                    pos.bars_held += 1
                    reason = None
                    move = (bar.close / pos.entry_price - 1.0) * pos.direction
                    if move <= -pos.stop_ret:
                        reason = "stop"
                    elif i >= cfg.exit_lookback:
                        window = idx[max(0, i - cfg.exit_lookback):i]
                        if window:
                            if pos.direction > 0 and idx[i] <= min(window):
                                reason = "channel_exit"
                            elif pos.direction < 0 and idx[i] >= max(window):
                                reason = "channel_exit"
                    # Never hold a contract into its own settlement.
                    if reason is None and (bar.expiry - d).days <= cfg.roll_days:
                        reason = "roll_out"
                    if reason:
                        trades.append(self._close(pos, bar, d, reason, cfg))
                        equity += trades[-1]["net_pnl"]
                        del open_pos[sym]
                        continue

                if sym in open_pos:
                    continue
                if len(open_pos) >= cfg.max_open:
                    skip("max_open")
                    continue
                if i < max(cfg.entry_lookback, cfg.vol_lookback):
                    skip("warmup")
                    continue

                # ── entry signal on the roll-safe index ──────────────────────
                window = idx[max(0, i - cfg.entry_lookback):i]
                if not window:
                    skip("no_window")
                    continue
                direction = 0
                if idx[i] > max(window):
                    direction = 1
                elif cfg.allow_short and idx[i] < min(window):
                    direction = -1
                if direction == 0:
                    continue

                vol = _vol(ser.rets, i, cfg.vol_lookback)
                if vol is None:
                    skip("no_vol_estimate")
                    continue
                stop_ret = cfg.stop_vol_mult * vol
                if stop_ret <= 0:
                    skip("degenerate_stop")
                    continue

                # ── whole-lot sizing against the risk budget ─────────────────
                risk_per_lot = stop_ret * bar.close * bar.lot
                if risk_per_lot <= 0:
                    skip("degenerate_risk")
                    continue
                lots = int((cfg.risk_frac * equity) // risk_per_lot)
                if lots < 1:
                    # Indian F&O has no fractional lots. If one lot risks more
                    # than the hard cap the trade is simply not available at this
                    # account size — which is a capacity finding, not a bug.
                    if risk_per_lot <= cfg.risk_frac_hard_cap * equity:
                        lots = 1
                    else:
                        skip("one_lot_exceeds_risk_cap")
                        continue
                lots = min(lots, cfg.max_lots)

                fill = bar.close * (1 + cfg.slippage_bps / 10_000.0 * direction)
                qty = lots * bar.lot
                fr = friction_model.leg_friction(
                    side="BUY" if direction > 0 else "SELL",
                    price=fill, quantity=qty, instrument="future")["total"]
                open_pos[sym] = _Open(
                    symbol=sym, direction=direction, entry_date=d, entry_price=fill,
                    entry_index=idx[i], lots=lots, lot_size=bar.lot,
                    stop_ret=stop_ret, entry_friction=fr, expiry=bar.expiry)

        # Anything still open at the end never resolved; it is not a result.
        for sym, pos in open_pos.items():
            skip("open_at_end")

        return {
            "config": {k: v for k, v in cfg.__dict__.items()},
            "trades": trades,
            "summary": engines.summarise(trades, cfg.equity0, {
                "symbols_traded": len({t["symbol"] for t in trades}),
                "open_at_end": len(open_pos),
                # see xsection for why panel health is reported, not just used
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

    def _close(self, pos: _Open, bar, d: datetime.date, reason: str,
               cfg: TrendConfig) -> Dict[str, Any]:
        fill = bar.close * (1 - cfg.slippage_bps / 10_000.0 * pos.direction)
        qty = pos.lots * pos.lot_size
        exit_fr = friction_model.leg_friction(
            side="SELL" if pos.direction > 0 else "BUY",
            price=fill, quantity=qty, instrument="future")["total"]
        gross = (fill - pos.entry_price) * pos.direction * qty
        friction = pos.entry_friction + exit_fr
        return {
            "symbol": pos.symbol,
            "entry_date": pos.entry_date.isoformat(),
            "exit_date": d.isoformat(),
            "direction": "LONG" if pos.direction > 0 else "SHORT",
            "strategy": f"TREND_{'LONG' if pos.direction > 0 else 'SHORT'}",
            "entry_price": round(pos.entry_price, 2),
            "exit_price": round(fill, 2),
            "lots": pos.lots, "quantity": qty,
            "stop_ret": round(pos.stop_ret, 5),
            "exit_reason": reason,
            "gross_pnl": round(gross, 2),
            "friction": round(friction, 2),
            "net_pnl": round(gross - friction, 2),
            "days_held": (d - pos.entry_date).days,
        }


engines.register(TrendEngine())
