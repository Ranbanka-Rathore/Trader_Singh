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
from backtest import margin as margin_mod
from backtest.liquidity_gate import LiquidityGate, gate_by_name

SETTLEMENT_STT_RATE = 0.00125

# Structures whose shorts sit at the same strike on both sides, or at different
# strikes on both sides: either way four legs, one expiry, and only one side can
# finish in the money. Every exit, mark and settlement path treats them alike, so
# they are named once here rather than compared against by string in six places.
FOUR_LEG = ("IRON_CONDOR", "IRON_BUTTERFLY")


class ConfigError(Exception):
    """A config that would run something other than what it declares."""


@dataclass
class Config:
    underlying: str = "NIFTY"
    slippage_per_leg: float = 0.75
    strike_interval: int = 50
    # stocks: infer the strike grid per day from the chain (grids change
    # after corporate actions) and ratio-adjust the closes series across
    # bonus/split jumps (>22% overnight = corporate action, not a move)
    auto_interval: bool = False
    # structure
    delta_target: float = 0.18        # grid: {0.15, 0.20}
    delta_band: Tuple[float, float] = (0.10, 0.28)
    # adaptive width: fall back to narrower spreads when 1 lot of the wide
    # structure exceeds the account's per-trade loss cap (small accounts).
    # SINGLE SOURCE OF TRUTH for every structure's width, in strike intervals.
    #
    # There used to be a `width_intervals: int = 4` field beside this one. It was
    # read in exactly one place — a line in print_report — and never by any entry
    # path, so `--set width_intervals=6` at registration was a silent no-op that
    # ran a 4-interval structure while the registration said 6. Removed 2026-08-13
    # rather than fixed, so that `screen.coerce` now rejects the name outright
    # ("not a Config field") instead of accepting an override nothing reads.
    # A one-element tuple means "this width or no trade" — which is what a width
    # chosen from a friction measurement requires.
    width_fallbacks: Tuple[int, ...] = (4, 2)
    min_days_to_expiry: int = 4       # -> 5-8 DTE weekly entries
    credit_floor_abs: float = 8.0     # Rs/share
    credit_floor_friction_mult: float = 2.0
    # iron condor — REJECTED by walk-forward 2026-07-04 (OOS: 18 trades,
    # 50% win, net -4,936, friction 2x directional). Kept for research only.
    enable_iron_condor: bool = False
    ic_credit_floor: float = 12.0     # 4 legs pay ~2x the friction of 2
    # iron butterfly — UNTESTED as of 2026-08-13. Both shorts AT ATM, wings
    # `width_fallbacks` intervals out on each side. Distinct from the condor:
    # the condor's shorts are delta-targeted OTM, the butterfly's are struck at
    # the money, which is a different risk shape (more premium, more gamma) and
    # not a retry of the same idea.
    enable_iron_butterfly: bool = False
    fly_credit_floor: float = 12.0    # 4 legs, same reasoning as the condor's
    # calendar spread — UNVALIDATED (0 OOS trades in the same WF run; its
    # cheap-vol regime never fired in a test month). Research only.
    enable_calendar: bool = False
    cal_min_debit: float = 8.0        # Rs/share
    cal_tp_ratio: float = 0.40        # +40% of debit
    cal_sl_ratio: float = 0.40        # -40% of debit
    # exits
    tp_ratio: float = 0.5             # grid: {0.5, 0.6}
    # "strike_touch" | "mark" | "none".
    # "none" means the structure's own defined risk IS the stop and the position
    # runs to the time stop. That is a real choice for a four-legged structure
    # whose max loss is capped by construction, and it is stated rather than
    # arrived at by setting a threshold that cannot be reached — see
    # `sl_mark_mult` below.
    sl_mode: str = "strike_touch"
    # Multiple of the CREDIT at which a mark stop fires. Calibrated for a
    # vertical, where credit is small against width (credit ~30 on a 200-wide
    # spread, so 1.5x credit = 45 against a 170 max loss — it binds).
    #
    # It does NOT transfer to an ATM butterfly, which inverts that ratio: a
    # measured 221.72 credit inside a 300-wide wing caps the loss at 78.28, while
    # 1.5 x credit asks for 332.58. The stop sits past the worst case the
    # structure can produce and therefore never fires. Any structure whose credit
    # exceeds 40% of its width has this problem at the default multiplier.
    # `_size_lots` records `mark_stop_binds` per position so it is visible in the
    # report instead of being discovered by reading the exit-reason histogram.
    sl_mark_mult: float = 1.5
    time_stop_days: int = 1           # exit at T-1 before expiry
    # ── LADDER MODE (income structure; all fixed a priori, none in grid) ──
    # weekly tranches at 30-45 DTE, managed at 21 DTE (set time_stop_days=21),
    # IVR-scaled sizing replaces the VRP hard gate, side = PCR+EMA when they
    # agree else EMA trend alone; event blackout + credit floor + risk caps
    # are the only hard filters kept.
    ladder_mode: bool = False
    dte_max: int = 45                 # entry expiry must be <= this many days out
    ivr_size_base: float = 0.5        # size mult = base + IV_rank  (0.5x..1.5x)
    portfolio_max_loss_frac: float = 0.10  # sum of open max-losses <= 10% equity
    # ── liquidity gate ────────────────────────────────────────────────────
    # Name of the backtest.liquidity_gate preset deciding whether a leg may be
    # filled at all. "off" reproduces the pre-2026-08-07 behaviour, which filled
    # on settlement prices for contracts that never traded — the backtest twin of
    # the synthetic ledger rows purged on 2026-07-30. Anything else requires a
    # real print. Default "traded": the honest minimum, no threshold tuning.
    liquidity_gate: str = "traded"
    # gates
    use_gates: bool = True
    # Unconditional entry (added 2026-08-13). Enters the single enabled structure
    # EVERY cycle, bypassing `regime_filters.classify_entry` entirely.
    #
    # Why this exists. Every arena-1 structure routed through classify_entry, and
    # each of its branches is a conjunction (the calendar needs ivr<0.30 AND
    # er<0.25; the condor needs vrp_gate AND gex>=0 AND middle PCR AND er<max).
    # The measured effect was a ~50% duty cycle: cal-cheapvol-modern reached 18.6
    # trades/year and the iron condor 18 OOS trades, against Amendment A5's
    # requirement of 32.3/year — while weekly supply is 52/year at 96.9% capacity
    # fill. The market offered the sample and the regime gate discarded it.
    #
    # `use_gates=False` does NOT do this. Its fallback emits only BULL_PUT/
    # BEAR_CALL by PCR, so switching gates off makes a four-legged structure
    # unavailable rather than unconditional. That is the trap this flag replaces.
    #
    # What it does NOT bypass: warmup and the event blackout. Those are risk
    # filters, not regime opinions — trading blind on a thin closes series, or
    # through a scheduled event, is not "unconditional entry", it is a different
    # and worse hypothesis. The ladder drew the same line.
    entry_unconditional: bool = False
    # GEX off by default in backtest: the naive OI-sign convention (dealers
    # long calls / short puts) directly contradicts the PCR reading this
    # system trades (heavy put OI = written puts = support). The live worker
    # can gate on its own dealer-calibrated GEX feed instead.
    use_gex: bool = False
    # sizing
    equity0: float = 500_000.0
    # Margin (added 2026-08-10). Before this the engine modelled none, so an
    # unhedged short cost nothing to hold and could be sized without limit.
    # `max_margin_frac` mirrors xsection.py's `max_gross_margin_frac`.
    margin_model: margin_mod.MarginModel = margin_mod.DEFAULT
    max_margin_frac: float = 0.30
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

    def __post_init__(self):
        """Refuse configurations that would run something other than they say.

        Every check here is a mistake this project has actually made or come one
        step from making. A config that silently does nothing is worse than one
        that crashes: it produces a result, and the result gets believed.
        """
        structures = [n for n, on in (
            ("enable_iron_butterfly", self.enable_iron_butterfly),
            ("enable_iron_condor", self.enable_iron_condor),
            ("enable_calendar", self.enable_calendar)) if on]

        if self.enable_iron_butterfly and not self.entry_unconditional:
            raise ConfigError(
                "enable_iron_butterfly=True with entry_unconditional=False does "
                "nothing: classify_entry has no butterfly branch, so the "
                "structure is unreachable and the run would silently be a "
                "vanilla credit-spread run. Set entry_unconditional=True.")

        if self.entry_unconditional:
            if len(structures) != 1:
                raise ConfigError(
                    "entry_unconditional=True enters ONE structure every cycle, "
                    f"but {len(structures)} are enabled: {structures or 'none'}. "
                    "A directional spread has no unconditional form — choosing "
                    "BULL_PUT vs BEAR_CALL is itself a regime read — so exactly "
                    "one of enable_iron_butterfly / enable_iron_condor / "
                    "enable_calendar must be set.")
            if self.use_gates:
                raise ConfigError(
                    "entry_unconditional=True and use_gates=True contradict each "
                    "other. Set use_gates=False so the report cannot claim a "
                    "regime filter was applied when it was bypassed.")

        if self.enable_iron_butterfly and self.sl_mode == "strike_touch":
            raise ConfigError(
                "an ATM iron butterfly cannot use sl_mode='strike_touch'. Both "
                "shorts sit AT the money, so the touch test "
                "(spot <= put_short or spot >= call_short) is a tautology and "
                "fires on the entry bar of every cycle — the structure would "
                "stop out the day it is opened, every time, and report a book "
                "of instant losses as a strategy result. Use sl_mode='mark'.")

        if self.sl_mode not in ("strike_touch", "mark", "none"):
            raise ConfigError(
                f"sl_mode='{self.sl_mode}' is not a stop rule. Known: "
                "strike_touch, mark, none. A typo here matches no branch in "
                "_try_exit and silently removes the stop loss.")

        if not self.width_fallbacks:
            raise ConfigError("width_fallbacks is empty: no structure has a width")
        if any(int(w) < 1 for w in self.width_fallbacks):
            raise ConfigError(
                f"width_fallbacks={self.width_fallbacks} contains a width below "
                "one strike interval; a zero-width wing is not a hedge")


@dataclass
class Position:
    entry_date: datetime.date
    expiry: datetime.date
    strategy: str
    opt_type: str
    sell_strike: float
    buy_strike: float
    entry_credit: float               # credit/share; calendars: DEBIT paid/share
    lots: int
    quantity: int
    lot_size: int
    entry_friction: float
    entry_spot: float
    pcr: float
    short_delta: float
    sizing: Dict[str, Any] = field(default_factory=dict)
    # iron condor call side (put side lives in sell/buy_strike)
    call_sell: float = 0.0
    call_buy: float = 0.0
    # calendar: expiry of the LONG far leg (near short leg is `expiry`)
    far_expiry: Optional[datetime.date] = None


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
        self._cur_interval: float = float(self.cfg.strike_interval)
        self._last_entry_week: Optional[Tuple[int, int]] = None  # ladder cadence
        self._open_max_loss: float = 0.0                         # portfolio cap
        # positions opened whose declared mark stop could not be reached
        self._unreachable_stops: int = 0
        self.skip_reasons: Dict[str, int] = {}
        self.gate = LiquidityGate(gate_by_name(self.cfg.liquidity_gate))

    # ── liquidity ─────────────────────────────────────────────────────────────
    def _gated_close(self, chain, expiry, strike, opt_type) -> Optional[float]:
        """`_leg_close`, but None when the leg is not plausibly fillable.

        Every price the backtester acts on goes through here, so a contract that
        never traded can neither be entered nor exited — the same refusal the
        live pricing guard makes, on the evidence EOD data can supply.
        """
        row = chain["options"].get((expiry, float(strike), opt_type))
        ok, _why = self.gate.leg_ok(row)
        if not ok:
            return None
        return _leg_close(chain, expiry, strike, opt_type)

    # ── strike selection ───────────────────────────────────────────────────
    def _pick_short_strike(self, chain, expiry, spot, opt_type,
                           on_date) -> Optional[Tuple[float, float]]:
        """Walk OTM from ATM; return (strike, delta) with |delta| nearest the
        target inside the band. Uses BS IV inverted from each strike's close."""
        cfg = self.cfg
        t = max((expiry - on_date).days, 1) / 365.0
        iv_step = self._cur_interval
        step = iv_step if opt_type == "CE" else -iv_step
        atm = round(spot / iv_step) * iv_step
        best: Optional[Tuple[float, float]] = None
        best_err = 1e9
        for i in range(1, 40):
            strike = float(atm + i * step)
            # gated: a strike we could not fill is not a candidate, so selection
            # walks past dead strikes instead of picking one and failing later
            close = self._gated_close(chain, expiry, strike, opt_type)
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
                   spot: float, dnet: float, structure: str = "vertical",
                   call_width: float = 0.0) -> Tuple[int, Dict[str, Any]]:
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

        # Margin. Before 2026-08-10 this constraint did not exist, which meant an
        # unhedged short was treated as free to hold. For a vertical it is
        # slack by a wide margin — max loss is ~Rs 8,800 against a Rs 4.5L budget,
        # so l_margin lands near 50 while l_risk lands near 2 — and every result
        # already in the kill log is therefore unchanged. It binds where it
        # should: on a calendar's naked-margined short leg.
        margin, basis = margin_mod.margin_per_lot(
            structure, lot=lot, spot=spot, width=width, credit=credit,
            call_width=call_width, model=cfg.margin_model)
        l_margin = margin_mod.lots_within_budget(
            margin, self._equity, cfg.max_margin_frac)

        candidates = [l_risk, l_vol, l_margin] + ([l_kelly] if l_kelly is not None else [])
        lots = max(0, min(min(candidates), cfg.max_lots))

        # Can the declared stop actually fire? A mark stop triggers at
        # sl_mark_mult x credit of loss, but the structure cannot lose more than
        # (width - credit). Where the second is smaller the stop is unreachable
        # and the config describes a risk control that does not exist. Recorded
        # per position rather than assumed, because it depends on the credit
        # received and so cannot be settled at config time.
        max_loss_ps = max_loss_per_lot / max(lot, 1)
        binds = (None if cfg.sl_mode != "mark"
                 else bool(cfg.sl_mark_mult * credit <= max_loss_ps))

        return lots, {"l_risk": l_risk, "l_vol": l_vol, "l_kelly": l_kelly,
                      "l_margin": l_margin,
                      "margin_per_lot": round(margin, 2),
                      "margin_basis": basis,
                      "f_star": round(f_star, 4) if f_star is not None else None,
                      "max_loss_per_lot": round(max_loss_per_lot, 2),
                      "mark_stop_binds": binds}

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

        self._cur_interval = cfg.strike_interval
        if cfg.auto_interval:
            inferred = bhavcopy.infer_strike_interval(chain, expiry, spot)
            if inferred:
                self._cur_interval = inferred

        pcr = chain_pcr(chain, expiry)
        iv_now = self._iv_hist[-1] if self._iv_hist else 0.0

        if cfg.ladder_mode:
            wk = (d.isocalendar()[0], d.isocalendar()[1])
            if self._last_entry_week == wk:
                return None  # one tranche per ISO week — cadence, not a skip
            if len(self._closes) < max(rf.EMA_PERIOD, rf.RV_LOOKBACK + 1):
                self._skip("warmup")
                return None
            ok, why = rf.event_gate(d)
            if not ok:
                self._skip(why)
                return None
            if (expiry - d).days > cfg.dte_max:
                self._skip("no_expiry_in_dte_window")
                return None
            ema20 = rf.ema(self._closes)
            side = rf.choose_side(pcr, spot, ema20)
            if side is None:
                # soft tilt: trend side when PCR has no confirmed opinion
                side = ("BULL_PUT_SPREAD" if (ema20 > 0 and spot > ema20)
                        else "BEAR_CALL_SPREAD")
            ivr = rf.iv_rank(self._iv_hist[:-1], iv_now)
            mult = cfg.ivr_size_base + max(min(ivr, 1.0), 0.0)
            pos = self._enter_directional(d, chain, expiry, spot, side, pcr,
                                          size_mult=mult)
            if pos is not None:
                new_ml = pos.sizing.get("max_loss_total", 0.0)
                if (self._open_max_loss + new_ml
                        > cfg.portfolio_max_loss_frac * self._equity):
                    self._skip("portfolio_risk_cap")
                    return None
                self._last_entry_week = wk
            return pos

        if cfg.entry_unconditional:
            # The two filters kept are risk filters, not regime opinions. Trading
            # on a closes series too thin to compute RV/EMA is not a hypothesis
            # about volatility, it is a hypothesis about garbage; and entering
            # through a scheduled event is a different bet from the one being
            # registered. Everything classify_entry would have said about IV
            # rank, VRP, PCR, GEX and efficiency ratio is bypassed.
            if len(self._closes) < max(rf.EMA_PERIOD, rf.RV_LOOKBACK + 1):
                self._skip("warmup")
                return None
            ok, why = rf.event_gate(d)
            if not ok:
                self._skip(why)
                return None
            strategy = ("IRON_BUTTERFLY" if cfg.enable_iron_butterfly
                        else "IRON_CONDOR" if cfg.enable_iron_condor
                        else "CALENDAR_SPREAD")
        elif cfg.use_gates:
            gex = 0
            if cfg.use_gex:
                gex = rf.naive_gex_sign(spot, expiry, d, chain["options"])
            strategy, reason = rf.classify_entry(
                side_pcr=pcr, spot=spot, closes=self._closes, iv=iv_now,
                iv_hist=self._iv_hist[:-1], on_date=d, gex_sign=gex,
                allow_ic=cfg.enable_iron_condor,
                allow_calendar=cfg.enable_calendar)
            if strategy is None:
                self._skip(reason)
                return None
        else:
            strategy = ("BULL_PUT_SPREAD" if pcr >= rf.PCR_BULL
                        else "BEAR_CALL_SPREAD" if pcr <= rf.PCR_BEAR else None)
            if strategy is None:
                self._skip("no_side")
                return None

        if strategy == "IRON_BUTTERFLY":
            return self._enter_fly(d, chain, expiry, spot, pcr)
        if strategy == "IRON_CONDOR":
            return self._enter_ic(d, chain, expiry, spot, pcr)
        if strategy == "CALENDAR_SPREAD":
            return self._enter_calendar(d, chain, expiry, spot, pcr)
        return self._enter_directional(d, chain, expiry, spot, strategy, pcr)

    def _leg_fills(self, chain, expiry, strike, opt_type):
        """(sell_fill, buy_fill) at today's close with slippage, or None.

        None also means "the liquidity gate refused this leg" — callers already
        treat None as unfillable, so refusal propagates as a skipped entry.
        """
        close = self._gated_close(chain, expiry, strike, opt_type)
        if close is None:
            return None
        return (max(close - self.cfg.slippage_per_leg, 0.05),
                close + self.cfg.slippage_per_leg)

    def _enter_directional(self, d, chain, expiry, spot, side, pcr,
                           size_mult: float = 1.0) -> Optional[Position]:
        cfg = self.cfg
        opt_type = "PE" if side == "BULL_PUT_SPREAD" else "CE"
        picked = self._pick_short_strike(chain, expiry, spot, opt_type, d)
        if picked is None:
            self._skip("no_strike_in_delta_band")
            return None
        sell_strike, short_delta = picked

        row = chain["options"].get((expiry, sell_strike, opt_type)) or {}
        lot = int(row.get("lot") or 0) or 75

        # adaptive width: widest affordable structure wins; a small account
        # falls back to a narrower hedge instead of not trading at all
        last_reason = "sizing_zero"
        for w_int in cfg.width_fallbacks:
            off = w_int * self._cur_interval
            buy_strike = sell_strike - off if opt_type == "PE" else sell_strike + off
            sf = self._leg_fills(chain, expiry, sell_strike, opt_type)
            bf = self._leg_fills(chain, expiry, buy_strike, opt_type)
            if sf is None or bf is None:
                last_reason = "hedge_leg_missing"
                continue
            sell_fill, _ = sf
            _, buy_fill = bf
            credit = round(sell_fill - buy_fill, 2)

            fr_probe = friction_model.round_trip_friction(
                [{"side": "SELL", "opt_type": opt_type.lower(), "price": sell_fill, "quantity": lot},
                 {"side": "BUY", "opt_type": opt_type.lower(), "price": buy_fill, "quantity": lot}],
                [{"side": "BUY", "opt_type": opt_type.lower(), "price": sell_fill, "quantity": lot},
                 {"side": "SELL", "opt_type": opt_type.lower(), "price": buy_fill, "quantity": lot}],
            )["total"] / lot
            floor = max(cfg.credit_floor_abs, cfg.credit_floor_friction_mult * fr_probe)
            if credit < floor:
                last_reason = "credit_below_floor"
                continue

            t = max((expiry - d).days, 1) / 365.0
            sc = _leg_close(chain, expiry, sell_strike, opt_type)
            bc = _leg_close(chain, expiry, buy_strike, opt_type)
            iv_s = bs_math.implied_vol(sc, spot, sell_strike, t, opt_type)
            iv_b = bs_math.implied_vol(bc, spot, buy_strike, t, opt_type)
            d_short = abs(bs_math.delta(spot, sell_strike, t, iv_s or 0.13, opt_type))
            d_long = abs(bs_math.delta(spot, buy_strike, t, iv_b or 0.13, opt_type))
            dnet = max(d_short - d_long, 0.01)

            width = abs(sell_strike - buy_strike)
            lots, sizing = self._size_lots(width=width, credit=credit, lot=lot,
                                           spot=spot, dnet=dnet)
            if lots < 1:
                last_reason = "sizing_zero"
                continue
            if size_mult != 1.0:
                # IVR soft sizing: scale the base, never past the hard cap
                hard_lots = max(int(cfg.risk_frac_hard_cap * self._equity
                                    / max(sizing["max_loss_per_lot"], 1.0)), 1)
                lots = min(max(int(round(lots * size_mult)), 1),
                           hard_lots, cfg.max_lots)
            sizing["size_mult"] = round(size_mult, 3)
            sizing["max_loss_total"] = round(sizing["max_loss_per_lot"] * lots, 2)

            quantity = lots * lot
            entry_friction = friction_model.basket_friction([
                {"side": "SELL", "opt_type": opt_type.lower(), "price": sell_fill, "quantity": quantity},
                {"side": "BUY", "opt_type": opt_type.lower(), "price": buy_fill, "quantity": quantity},
            ])["total"]
            sizing["width_intervals"] = w_int

            return Position(
                entry_date=d, expiry=expiry, strategy=side, opt_type=opt_type,
                sell_strike=sell_strike, buy_strike=buy_strike, entry_credit=credit,
                lots=lots, quantity=quantity, lot_size=lot,
                entry_friction=entry_friction, entry_spot=spot,
                pcr=round(pcr, 3), short_delta=round(short_delta, 3), sizing=sizing,
            )
        self._skip(last_reason)
        return None

    def _enter_ic(self, d, chain, expiry, spot, pcr) -> Optional[Position]:
        """Iron condor: delta-target shorts on BOTH sides, hedges width out.
        Defined risk: only one side can lose -> max loss = width - total credit."""
        cfg = self.cfg
        p_pick = self._pick_short_strike(chain, expiry, spot, "PE", d)
        c_pick = self._pick_short_strike(chain, expiry, spot, "CE", d)
        if p_pick is None or c_pick is None:
            self._skip("ic_no_strike_in_band")
            return None
        pe_s, pe_d = p_pick
        ce_s, ce_d = c_pick

        row = chain["options"].get((expiry, pe_s, "PE")) or {}
        lot = int(row.get("lot") or 0) or 75

        last_reason = "sizing_zero"
        for w_int in cfg.width_fallbacks:
            off = w_int * self._cur_interval
            pe_b, ce_b = pe_s - off, ce_s + off
            legs = []
            fills = {}
            ok = True
            for strike, typ, side in ((pe_s, "PE", "SELL"), (pe_b, "PE", "BUY"),
                                      (ce_s, "CE", "SELL"), (ce_b, "CE", "BUY")):
                f = self._leg_fills(chain, expiry, strike, typ)
                if f is None:
                    ok = False
                    break
                fill = f[0] if side == "SELL" else f[1]
                fills[(strike, typ)] = fill
                legs.append({"side": side, "opt_type": typ.lower(),
                             "price": fill, "quantity": lot})
            if not ok:
                last_reason = "ic_leg_missing"
                continue

            credit = round(fills[(pe_s, "PE")] - fills[(pe_b, "PE")]
                           + fills[(ce_s, "CE")] - fills[(ce_b, "CE")], 2)
            if credit < cfg.ic_credit_floor:
                last_reason = "ic_credit_below_floor"
                continue

            width = off  # per side; only one side can be ITM at expiry
            lots, sizing = self._size_lots(width=width, credit=credit, lot=lot,
                                           spot=spot, dnet=0.05,
                                           structure="iron_condor",
                                           call_width=off)
            if lots < 1:
                last_reason = "sizing_zero"
                continue

            quantity = lots * lot
            entry_friction = friction_model.basket_friction(
                [{**l, "quantity": quantity} for l in legs])["total"]
            sizing["width_intervals"] = w_int

            return Position(
                entry_date=d, expiry=expiry, strategy="IRON_CONDOR", opt_type="PE",
                sell_strike=pe_s, buy_strike=pe_b, call_sell=ce_s, call_buy=ce_b,
                entry_credit=credit, lots=lots, quantity=quantity, lot_size=lot,
                entry_friction=entry_friction, entry_spot=spot,
                pcr=round(pcr, 3), short_delta=round(max(pe_d, ce_d), 3), sizing=sizing,
            )
        self._skip(last_reason)
        return None

    def _enter_fly(self, d, chain, expiry, spot, pcr) -> Optional[Position]:
        """Iron butterfly: BOTH shorts at ATM, wings `width_fallbacks` out.

        Structurally an iron condor whose two short strikes have collapsed onto
        the same strike, so max loss is still (wing width - credit) and only one
        side can finish in the money. The difference that matters is economic:
        ATM shorts collect the most premium and carry the most gamma, which is
        why the friction work found it the best-earning four-legged structure
        and why nothing here says it is a good one.

        It does NOT go through `_pick_short_strike`. That function walks outward
        from ATM starting at `i=1`, so the nearest strike it can return is one
        interval OTM — no delta target reaches the money. The butterfly does not
        want a delta target at all: its strike is defined by the spot, exactly as
        the calendar's is.
        """
        cfg = self.cfg
        atm = float(round(spot / self._cur_interval) * self._cur_interval)

        row = chain["options"].get((expiry, atm, "PE")) or {}
        lot = int(row.get("lot") or 0) or 75

        last_reason = "sizing_zero"
        for w_int in cfg.width_fallbacks:
            off = w_int * self._cur_interval
            pe_b, ce_b = atm - off, atm + off
            legs = []
            fills = {}
            ok = True
            for strike, typ, side in ((atm, "PE", "SELL"), (pe_b, "PE", "BUY"),
                                      (atm, "CE", "SELL"), (ce_b, "CE", "BUY")):
                f = self._leg_fills(chain, expiry, strike, typ)
                if f is None:
                    ok = False
                    break
                fill = f[0] if side == "SELL" else f[1]
                fills[(strike, typ)] = fill
                legs.append({"side": side, "opt_type": typ.lower(),
                             "price": fill, "quantity": lot})
            if not ok:
                last_reason = "fly_leg_missing"
                continue

            credit = round(fills[(atm, "PE")] - fills[(pe_b, "PE")]
                           + fills[(atm, "CE")] - fills[(ce_b, "CE")], 2)
            if credit < cfg.fly_credit_floor:
                last_reason = "fly_credit_below_floor"
                continue

            # Both shorts are ATM, so net delta is ~0 by construction and the
            # 0.05 the condor uses as a nominal dnet is if anything generous
            # here. Kept identical so the two structures are sized by the same
            # rule and any difference in result is the structure, not the sizer.
            lots, sizing = self._size_lots(width=off, credit=credit, lot=lot,
                                           spot=spot, dnet=0.05,
                                           structure="iron_butterfly",
                                           call_width=off)
            if lots < 1:
                last_reason = "sizing_zero"
                continue

            quantity = lots * lot
            entry_friction = friction_model.basket_friction(
                [{**l, "quantity": quantity} for l in legs])["total"]
            sizing["width_intervals"] = w_int

            return Position(
                entry_date=d, expiry=expiry, strategy="IRON_BUTTERFLY", opt_type="PE",
                sell_strike=atm, buy_strike=pe_b, call_sell=atm, call_buy=ce_b,
                entry_credit=credit, lots=lots, quantity=quantity, lot_size=lot,
                entry_friction=entry_friction, entry_spot=spot,
                pcr=round(pcr, 3), short_delta=0.5, sizing=sizing,
            )
        self._skip(last_reason)
        return None

    def _enter_calendar(self, d, chain, expiry, spot, pcr) -> Optional[Position]:
        """ATM CE calendar: SELL near expiry, BUY the next expiry out.
        Debit-defined risk (max loss = debit paid). Long vol / long term
        structure — the structure for the days the VRP gate refuses to sell."""
        cfg = self.cfg
        far = next((e for e in chain["expiries"]
                    if e > expiry and (e - expiry).days <= 35), None)
        if far is None:
            self._skip("cal_no_far_expiry")
            return None
        atm = float(round(spot / self._cur_interval) * self._cur_interval)

        near_f = self._leg_fills(chain, expiry, atm, "CE")
        far_f = self._leg_fills(chain, far, atm, "CE")
        if near_f is None or far_f is None:
            self._skip("cal_leg_missing")
            return None
        near_sell = near_f[0]     # we SELL the near leg (receive)
        far_buy = far_f[1]        # we BUY the far leg (pay)
        debit = round(far_buy - near_sell, 2)
        if debit < cfg.cal_min_debit:
            self._skip("cal_debit_below_floor")
            return None

        row = chain["options"].get((expiry, atm, "CE")) or {}
        lot = int(row.get("lot") or 0) or 75

        # max loss = debit paid, and margin is the same figure: the far long sits
        # at the SAME strike as the near short, so it covers assignment and the
        # economic risk is the debit. See backtest/margin.py for why the first
        # draft's naked treatment was wrong.
        lots, sizing = self._size_lots(width=debit, credit=0.0, lot=lot,
                                       spot=spot, dnet=0.05,
                                       structure="calendar")
        if lots < 1:
            self._skip("sizing_zero")
            return None
        quantity = lots * lot
        entry_friction = friction_model.basket_friction([
            {"side": "SELL", "opt_type": "ce", "price": near_sell, "quantity": quantity},
            {"side": "BUY", "opt_type": "ce", "price": far_buy, "quantity": quantity},
        ])["total"]

        return Position(
            entry_date=d, expiry=expiry, strategy="CALENDAR_SPREAD", opt_type="CE",
            sell_strike=atm, buy_strike=atm, far_expiry=far,
            entry_credit=debit,  # NOTE: debit for calendars
            lots=lots, quantity=quantity, lot_size=lot,
            entry_friction=entry_friction, entry_spot=spot,
            pcr=round(pcr, 3), short_delta=0.5, sizing=sizing,
        )

    # ── exit ───────────────────────────────────────────────────────────────
    def _intrinsic(self, pos: Position, spot: float) -> float:
        """Settlement liability per share. Four-legged: put side + call side
        (only one can be ITM). Calendar handled separately (far leg is not
        expiring). A butterfly is the condor case with both shorts at ATM, so
        the same arithmetic settles it."""
        put_side = max(pos.sell_strike - spot, 0.0) - max(pos.buy_strike - spot, 0.0)
        call_side = max(spot - pos.sell_strike, 0.0) - max(spot - pos.buy_strike, 0.0)
        if pos.strategy in FOUR_LEG:
            put_leg = max(pos.sell_strike - spot, 0.0) - max(pos.buy_strike - spot, 0.0)
            call_leg = max(spot - pos.call_sell, 0.0) - max(spot - pos.call_buy, 0.0)
            return put_leg + call_leg
        return put_side if pos.opt_type == "PE" else call_side

    def _side_mark(self, chain, expiry, sell_k, buy_k, opt_type):
        """(cost_to_close, buyback, sellout) for one short spread side.

        The SHORT leg must be gated: buying it back is the trade that actually
        has to happen, and pretending it can be closed at a settlement price is
        how a paper stop-loss gets fabricated. The long leg falls back to 0.05
        (abandoned worthless) as before — failing to sell a long is a cost we
        already assume, not a fill we invent.
        """
        sell_close = self._gated_close(chain, expiry, sell_k, opt_type)
        buy_close = self._gated_close(chain, expiry, buy_k, opt_type)
        if sell_close is None:
            return None
        buy_close = buy_close or 0.05
        buy_back = sell_close + self.cfg.slippage_per_leg
        sell_out = max(buy_close - self.cfg.slippage_per_leg, 0.05)
        return buy_back - sell_out, buy_back, sell_out

    def _mark_exit(self, chain, pos) -> Optional[Dict[str, Any]]:
        """{'value': per-share exit value, 'legs': friction legs} at today's
        marks. value semantics: credit structures -> cost to close (lower is
        better); calendars -> proceeds received on close (higher is better)."""
        q = pos.quantity
        if pos.strategy == "CALENDAR_SPREAD":
            near_close = _leg_close(chain, pos.expiry, pos.sell_strike, "CE")
            far_close = _leg_close(chain, pos.far_expiry, pos.buy_strike, "CE")
            if near_close is None or far_close is None:
                return None
            near_buyback = near_close + self.cfg.slippage_per_leg
            far_sellout = max(far_close - self.cfg.slippage_per_leg, 0.05)
            return {"value": far_sellout - near_buyback,
                    "legs": [{"side": "BUY", "opt_type": "ce", "price": near_buyback, "quantity": q},
                             {"side": "SELL", "opt_type": "ce", "price": far_sellout, "quantity": q}]}
        if pos.strategy in FOUR_LEG:
            p = self._side_mark(chain, pos.expiry, pos.sell_strike, pos.buy_strike, "PE")
            c = self._side_mark(chain, pos.expiry, pos.call_sell, pos.call_buy, "CE")
            if p is None or c is None:
                return None
            return {"value": p[0] + c[0],
                    "legs": [{"side": "BUY", "opt_type": "pe", "price": p[1], "quantity": q},
                             {"side": "SELL", "opt_type": "pe", "price": p[2], "quantity": q},
                             {"side": "BUY", "opt_type": "ce", "price": c[1], "quantity": q},
                             {"side": "SELL", "opt_type": "ce", "price": c[2], "quantity": q}]}
        m = self._side_mark(chain, pos.expiry, pos.sell_strike, pos.buy_strike, pos.opt_type)
        if m is None:
            return None
        return {"value": m[0],
                "legs": [{"side": "BUY", "opt_type": pos.opt_type.lower(), "price": m[1], "quantity": q},
                         {"side": "SELL", "opt_type": pos.opt_type.lower(), "price": m[2], "quantity": q}]}

    def _pnl_ps(self, pos: Position, mark_value: float) -> float:
        """P&L per share from a mark. Credit: credit - cost_to_close.
        Calendar: proceeds - debit."""
        if pos.strategy == "CALENDAR_SPREAD":
            return mark_value - pos.entry_credit
        return pos.entry_credit - mark_value

    def _try_exit(self, d: datetime.date, chain, pos: Position) -> Optional[ClosedTrade]:
        cfg = self.cfg
        spot = float(chain["spot"] or pos.entry_spot)

        if d >= pos.expiry:
            # missed the T-1 stop (holiday/missing marks) — settle
            if pos.strategy == "CALENDAR_SPREAD":
                # near short settles at intrinsic; far long is SOLD at mark
                near_intrinsic = max(spot - pos.sell_strike, 0.0)
                far_close = _leg_close(chain, pos.far_expiry, pos.buy_strike, "CE")
                far_out = max((far_close or 0.05) - cfg.slippage_per_leg, 0.05)
                proceeds = far_out - near_intrinsic
                xf = friction_model.basket_friction([
                    {"side": "SELL", "opt_type": "ce", "price": far_out,
                     "quantity": pos.quantity}])["total"]
                return self._close(pos, d, spot, proceeds, xf, "EXPIRY_SETTLEMENT")
            exit_cost = self._intrinsic(pos, spot)
            if pos.strategy in FOUR_LEG:
                long_itm = (max(pos.buy_strike - spot, 0.0)
                            + max(spot - pos.call_buy, 0.0))
            else:
                long_itm = (max(pos.buy_strike - spot, 0.0) if pos.opt_type == "PE"
                            else max(spot - pos.buy_strike, 0.0))
            xf = SETTLEMENT_STT_RATE * long_itm * pos.quantity
            return self._close(pos, d, spot, exit_cost, xf, "EXPIRY_SETTLEMENT")

        marks = self._mark_exit(chain, pos)
        days_left = (pos.expiry - d).days
        time_stop = days_left <= cfg.time_stop_days
        if marks is None:
            return None  # no mark today; settlement path catches expiry

        pnl_ps = self._pnl_ps(pos, marks["value"])

        if pos.strategy == "CALENDAR_SPREAD":
            reason = None
            if pnl_ps >= cfg.cal_tp_ratio * pos.entry_credit:
                reason = "TAKE_PROFIT"
            elif pnl_ps <= -cfg.cal_sl_ratio * pos.entry_credit:
                reason = "STOP_LOSS_MARK"
            elif time_stop:
                reason = "TIME_STOP_T1"
            if reason is None:
                return None
        else:
            reason = None
            if pnl_ps >= cfg.tp_ratio * pos.entry_credit:
                reason = "TAKE_PROFIT"
            elif cfg.sl_mode == "strike_touch":
                if pos.strategy == "IRON_BUTTERFLY":
                    # Unreachable: Config.__post_init__ refuses this pairing. Kept
                    # as a raise rather than a fall-through because the degenerate
                    # answer here is not an error, it is `True` — both shorts are
                    # at ATM, so the touch test is satisfied on the entry bar and
                    # the run would look like a strategy that always stops out.
                    raise ConfigError(
                        "strike_touch on an ATM butterfly is a tautology; "
                        "sl_mode='mark' is the only defined stop for it")
                if pos.strategy == "IRON_CONDOR":
                    breached = spot <= pos.sell_strike or spot >= pos.call_sell
                else:
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

        xf = friction_model.basket_friction(marks["legs"])["total"]
        return self._close(pos, d, spot, marks["value"], xf, reason)

    def _close(self, pos, d, spot, exit_cost, exit_friction, reason) -> ClosedTrade:
        # exit_cost: cost-to-close for credit structures; PROCEEDS for calendars
        if pos.strategy == "CALENDAR_SPREAD":
            gross = (exit_cost - pos.entry_credit) * pos.quantity
        else:
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
        self._last_entry_week = None
        self._open_max_loss = 0.0
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
                    self._open_max_loss -= pos.sizing.get("max_loss_total", 0.0)
                else:
                    still.append(pos)
            open_pos = still

            # entry at close
            if len(open_pos) < cfg.max_open:
                pos = self._try_enter(d, chain)
                if pos:
                    open_pos.append(pos)
                    self._open_max_loss += pos.sizing.get("max_loss_total", 0.0)
                    if pos.sizing.get("mark_stop_binds") is False:
                        self._unreachable_stops += 1

            # update state AFTER decisions (no lookahead)
            if spot > 0:
                if self._closes and abs(spot / self._closes[-1] - 1.0) > 0.22:
                    # corporate action (bonus/split), not a market move:
                    # ratio-adjust history so RV/EMA/ER stay meaningful
                    f = spot / self._closes[-1]
                    self._closes = [c * f for c in self._closes]
                self._closes.append(spot)
                self._closes = self._closes[-300:]
                exp_iv = bhavcopy.nearest_expiry(chain, d, min_days=cfg.min_days_to_expiry)
                if exp_iv:
                    iv = rf.atm_straddle_iv(
                        spot, exp_iv, d,
                        lambda k, ty, _c=chain, _e=exp_iv: _leg_close(_c, _e, k, ty),
                        self._cur_interval)
                    self._iv_hist.append(iv)
                    self._iv_hist = self._iv_hist[-252:]

        # window-end mark for anything still open
        for pos in open_pos:
            if last_chain is None:
                continue
            spot = float(last_chain["spot"] or pos.entry_spot)
            marks = self._mark_exit(last_chain, pos)
            if marks is not None:
                value = marks["value"]
            elif pos.strategy == "CALENDAR_SPREAD":
                value = pos.entry_credit  # no mark: assume flat (debit back)
            else:
                value = self._intrinsic(pos, spot)
            closed = self._close(pos, last_chain["date"], spot, value, 0.0, "WINDOW_END_MARK")
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
            "liquidity_gate": {
                "preset": self.gate.cfg.name,
                "legs_checked": self.gate.checked,
                "legs_fillable": self.gate.passed,
                "pass_rate_pct": round(self.gate.pass_rate, 2),
                "refusals": dict(sorted(self.gate.rejections.items(),
                                        key=lambda kv: -kv[1])),
            },
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
                # Surfaced to the screen report via schema.metrics()["extras"],
                # so a run whose stop loss could never fire says so on its own
                # face rather than being inferred from a missing exit reason.
                "engine_extras": {
                    "positions_with_unreachable_mark_stop": self._unreachable_stops,
                },
            },
        }


def print_report(result: Dict[str, Any], show_trades: bool = True):
    s = result["summary"]
    cfg = result["config"]
    print("\n" + "=" * 70)
    widths = "/".join(str(w) for w in cfg["width_fallbacks"])
    print(f"HONEST EOD BACKTEST v2 — {cfg['underlying']} | delta {cfg['delta_target']} | "
          f"width {widths}x{cfg['strike_interval']} | tp {cfg['tp_ratio']} | "
          f"sl {cfg['sl_mode']} | "
          f"gates {'OFF' if cfg['entry_unconditional'] else 'ON' if cfg['use_gates'] else 'FALLBACK'}")
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
