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

TWO SIGNALS, AND WHY THE GRID DOES NOT GROW
--------------------------------------------
`signal` selects the entry rule. `donchian` is the original breakout and is the
default, so every result already in the kill log reproduces byte-for-byte.
`tsmom` judges each symbol against its OWN trailing return instead of a channel,
which is a different bet: a breakout needs a new extreme, time-series momentum
only needs the last N months to have been positive.

Each signal carries its own grid of exactly EIGHT combinations. That number is
load-bearing and is not a coincidence: `walkforward.py` sets the deflated-Sharpe
hurdle at sqrt(2 ln |grid| / T), so a grid of a different size would silently
move the bar that `trend-donchian-modern` was already judged against and make
the two incomparable. A new signal therefore gets its own eight axes-combinations
rather than being bolted onto the existing eight.

WHAT tsmom IS FOR, AND WHAT IT IS NOT
--------------------------------------
It exists because the index universe cannot support a hypothesis: NIFTY,
BANKNIFTY and FINNIFTY correlate at 0.89-0.96 daily, so the three of them supply
1.06 independent bets and would need a standalone Sharpe near 0.97 to clear
Section 2. Single-stock futures correlate at ~0.30, giving ~2.6 independent bets
at the 6-8 names Rs 15L can hold, which drops the required standalone Sharpe to
~0.62. See the 2026-08-09 survey in ARENAS.md.

It is NOT arena 2 with a new name. `xsection` ranks names AGAINST EACH OTHER and
is dollar-neutral by construction, holding no market exposure. `tsmom` judges
each name against itself, so the book carries net directional beta. That is a
claim about the two P&L series, not an axiom, and it should be measured before
either is trusted as diversifying from the other.

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

# The entry rules this engine knows. An unrecognised name is refused at
# registration rather than silently falling back to the default — a hypothesis
# that registers "signal=tsmomentum" and quietly screens a Donchian breakout
# would put a fingerprint in the kill log that describes a run that never
# happened.
SIGNALS = ("donchian", "tsmom")


@dataclass
class TrendConfig:
    universe: Tuple[str, ...] = DEFAULT_UNIVERSE
    kind: str = "index"
    equity0: float = 1_500_000.0
    gate: str = "strict"
    roll_days: int = futures.DEFAULT_ROLL_DAYS
    # signal
    signal: str = "donchian"          # one of SIGNALS; default is the original
    entry_lookback: int = 20          # donchian: breakout window (grid)
    exit_lookback: int = 10           # donchian: opposite-extreme exit (grid)
    # tsmom: sign of the return from t-(mom_lookback+mom_skip) to t-mom_skip.
    # The skip drops the most recent month, the standard construction that keeps
    # short-term reversal out of a momentum measurement. Ignored by donchian.
    mom_lookback: int = 126           # tsmom: trailing window in bars (grid)
    mom_skip: int = 21                # tsmom: bars dropped at the near end (grid)
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

    # Per-signal grids. EVERY entry must be 8 combinations long: the hurdle is
    # sqrt(2 ln |grid| / T), so an unequal grid changes the bar rather than the
    # strategy, and results across signals stop being comparable. The assertion
    # below is not decoration — it is the thing that stops that happening by
    # accident when someone adds a third level to an axis.
    GRIDS = {
        "donchian": GRID,
        "tsmom": [{"mom_lookback": m, "mom_skip": k, "stop_vol_mult": s}
                  for m in (126, 252) for k in (0, 21) for s in (2.5, 4.0)],
    }
    assert set(GRIDS) == set(SIGNALS), "every signal needs a grid"
    assert all(len(g) == 8 for g in GRIDS.values()), \
        "grids must be 8 combos or the deflated-Sharpe hurdle shifts"

    def coerce(self, name: str, raw: Any) -> Any:
        value = engines.coerce_field(TrendConfig, name, raw)
        if name == "signal" and value not in SIGNALS:
            # Raised at REGISTRATION, which is the only moment it is cheap.
            raise KeyError(f"signal: expected one of {', '.join(SIGNALS)}, "
                           f"got '{value}'")
        return value

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

    def grid(self, cfg: Optional[TrendConfig] = None) -> List[Dict[str, Any]]:
        """The grid for the configured signal.

        Sweeping donchian's channel axes over a tsmom run would search eight
        parameters the strategy does not read, producing eight identical folds
        and an in-sample "best" chosen at random.
        """
        signal = cfg.signal if cfg is not None else "donchian"
        return list(self.GRIDS[signal])

    def _min_bars(self, cfg: TrendConfig) -> int:
        """Bars of history before this config's signal is defined at all."""
        if cfg.signal == "tsmom":
            return max(cfg.mom_lookback + cfg.mom_skip, cfg.vol_lookback)
        return max(cfg.entry_lookback, cfg.vol_lookback)

    def warmup_days(self, cfg: TrendConfig) -> int:
        """Calendar days needed before this config's signal is defined.

        Trading days are ~69% of calendar days, so the lookback is scaled up
        rather than passed through — a 55-bar channel needs about 80 calendar
        days of archive, not 55, and a 252-bar momentum with a 21-bar skip needs
        about 440, not 273. Getting this wrong does not error: it silently gives
        the first folds no signal and reports the result as "does not trade".
        """
        bars = max(self._min_bars(cfg), cfg.exit_lookback)
        return int(bars * 1.5) + 30

    def _direction(self, cfg: TrendConfig, ser, i: int) -> int:
        """+1 long, -1 short, 0 flat — the only place an entry rule is defined.

        Computed on `ser.index`, the roll-safe compounded series, never on the
        contract close: a 20-day high in raw front prices is an artefact of where
        the last roll landed.
        """
        idx = ser.index
        if cfg.signal == "donchian":
            window = idx[max(0, i - cfg.entry_lookback):i]
            if not window:
                return 0
            if idx[i] > max(window):
                return 1
            if cfg.allow_short and idx[i] < min(window):
                return -1
            return 0
        if cfg.signal == "tsmom":
            near = i - cfg.mom_skip
            far = near - cfg.mom_lookback
            if far < 0 or near < 0 or idx[far] <= 0:
                return 0
            r = idx[near] / idx[far] - 1.0
            if r > 0:
                return 1
            if r < 0 and cfg.allow_short:
                return -1
            return 0
        raise ValueError(f"unknown signal '{cfg.signal}'; expected one of "
                         f"{', '.join(SIGNALS)}")

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
                    elif cfg.signal == "donchian":
                        if i >= cfg.exit_lookback:
                            window = idx[max(0, i - cfg.exit_lookback):i]
                            if window:
                                if pos.direction > 0 and idx[i] <= min(window):
                                    reason = "channel_exit"
                                elif pos.direction < 0 and idx[i] >= max(window):
                                    reason = "channel_exit"
                    # tsmom has no channel to fall out of: it holds while its own
                    # trailing return still points the way the position does, and
                    # leaves the moment that stops being true — including when the
                    # sign goes flat, which with allow_short=False is the only exit
                    # the signal can produce.
                    elif self._direction(cfg, ser, i) != pos.direction:
                        reason = "signal_flip"
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
                if i < self._min_bars(cfg):
                    skip("warmup")
                    continue

                # ── entry signal on the roll-safe index ──────────────────────
                direction = self._direction(cfg, ser, i)
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
            # `strategy` keeps the TREND_ prefix regardless of signal so the
            # label in trend-donchian-modern's stored report still means the same
            # thing; which rule produced the trade goes in its own field.
            "strategy": f"TREND_{'LONG' if pos.direction > 0 else 'SHORT'}",
            "signal": cfg.signal,
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
