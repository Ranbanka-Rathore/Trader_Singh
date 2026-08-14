"""RESEARCH_CHARTER.md expressed as numbers a program can enforce.

The charter is the contract; this module is the single place its thresholds are
written down for machines. Nothing else in `research/` may hard-code one. If a
number here disagrees with the document, the document wins and this is a bug.

Several charter rules are stated qualitatively ("moves materially", "a broad
plateau"). Turning those into numbers is a judgement, and making it after seeing
a result would be exactly the sin Section 4 exists to prevent — so every such
choice was fixed in Amendment B, dated and appended to the charter BEFORE any
hypothesis was registered. Where this module interprets rather than transcribes,
the docstring says so.
"""
import datetime
import math
import os
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

CHARTER_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "RESEARCH_CHARTER.md")

# Section 7. Past this date the survey is complete or the project is reassessed
# against the charter as written. The loop prints the remaining budget on every
# run so it cannot quietly slide.
PROJECT_STOP_DATE = datetime.date(2027, 1, 7)

# Section 8. A hypothesis must belong to one of these; a new arena is a charter
# amendment, not a command-line argument.
ARENAS: Dict[str, str] = {
    "index_structures": "Index-option structures other than vanilla credit spreads",
    "cross_sectional": "Cross-sectional equities — stock F&O, relative value",
    "futures_trend": "Directional / trend on liquid futures",
    "event_vol": "Event-driven volatility — earnings, RBI policy, budget",
    # Amendment E7 (2026-08-14). All four arenas above are CLOSED; Phase 2 opens
    # intraday, which no Phase 1 hypothesis touched — every one used EOD
    # bhavcopy. These are added by amendment, which is what the registry's
    # "a new arena is a charter amendment" rule requires.
    "intraday_index": "Intraday NIFTY index structure at 5-60 min horizons",
    "intraday_option": "Expiry-day and short-DTE option behaviour (forward-tested, E6)",
    "intraday_session": "Session structure — opening range, close, time-of-day",
}


# ── liquidity eras ───────────────────────────────────────────────────────────
@dataclass(frozen=True)
class Era:
    name: str
    start: datetime.date
    end: datetime.date
    tradeable_pct: str      # measured share of NIFTY option legs that traded
    note: str


# NSE's NIFTY option book changed character over the archive: the share of legs
# that actually trade rises ~6x while the listed strike universe halves. Measured
# 2026-08-08 by sampling three sessions a year across 2016-2026:
#
#   2016 10.2%   2017 10.2%   2018 10.9%   2019  9.8%   <- flat
#   2020 14.8%   2021 29.0%   2022 38.6%                <- ramp
#   2023 51.7%   2024 52.2%   2025 57.0%   2026 63.5%   <- plateau
#
# Pooling a STRATEGY RESULT across these is meaningless: the same strategy is
# being asked to trade three different markets. Boundaries are drawn where the
# slope changes, not on round years by coincidence.
#
# AMENDMENT D (2026-08-10) scopes this to the evidence above, which is entirely
# about the NIFTY OPTION book. Measured on single-stock futures the same property
# is 100.00% in every year 2016-2026 — there is no ramp there to partition, and
# rho_bar, breadth, vol and dispersion are continuous across both boundaries. So:
#
#   - strategy results are still reported per era, `modern` still the default
#     (D1) — a backtest reads lots, fills, margin and capacity, which do differ;
#   - signal-PROPERTY estimation (IC, correlation, dispersion — quantities with
#     no lots, fills or sizing in them) may pool eras, but only for an instrument
#     whose era-defining property has been measured and found flat (D2).
#
# Stock futures qualify. The option book demonstrably does not. Index futures
# have not been checked, so they do not qualify either. Amendment D lowers no
# bar and reopens no closed hypothesis (D3).
ERAS: Dict[str, Era] = {
    "early": Era("early", datetime.date(2016, 1, 1), datetime.date(2019, 12, 31),
                 "~10%", "thin book, ~3,000 listed legs/day, weeklies barely traded"),
    "ramp": Era("ramp", datetime.date(2020, 1, 1), datetime.date(2022, 12, 31),
                "15-39%", "retail options boom; liquidity climbing every year"),
    "modern": Era("modern", datetime.date(2023, 1, 1), datetime.date(2030, 12, 31),
                  "52-64%", "current regime — the only one resembling what you would trade today"),
}
DEFAULT_ERA = "modern"


def era_of(d: datetime.date) -> Optional[str]:
    for name, e in ERAS.items():
        if e.start <= d <= e.end:
            return name
    return None


def eras_spanned(start: datetime.date, end: datetime.date) -> List[str]:
    """Era names touched by [start, end], in chronological order."""
    return [n for n, e in sorted(ERAS.items(), key=lambda kv: kv[1].start)
            if e.start <= end and e.end >= start]


def era_window(name: str, cap: Optional[datetime.date] = None
               ) -> Tuple[datetime.date, datetime.date]:
    """(start, end) for an era, clipped to `cap` (normally today)."""
    if name not in ERAS:
        raise ValueError(f"unknown era '{name}'; choose from {sorted(ERAS)}")
    e = ERAS[name]
    end = min(e.end, cap) if cap else e.end
    return e.start, end


# ── Amendment D5: a pooled estimate must earn the pooling ────────────────────
# D2 permitted pooling eras for signal-property estimation on an instrument whose
# era-defining property is flat. Stock futures qualify. The pooled IC re-run that
# permission authorised then flipped sign across eras for nine of eleven signals,
# with the whole pooled number carried by `early` — a figure describing no market
# that has ever existed. D2's measurement was right; its inference was not. Flat
# INSTRUMENT properties do not imply flat SIGNAL properties.
#
# So the permission is conditional, and the two conditions are here rather than in
# prose because a criterion written only in prose is one that quietly stops being
# applied — the same lesson `requirements.py` exists for.
POOLED_DOMINANCE_LIMIT = 0.50   # D5.2; Amendment B's materiality figure reused


def pooled_estimate_admissible(per_era: Dict[str, Tuple[float, int]]
                               ) -> Tuple[bool, List[str]]:
    """(admissible, reasons) — may a pooled estimate be relied on? Amendment D5.

    `per_era` maps era name -> (estimate, n_observations). The pooled estimate is
    the n-weighted mean, which is what pooling the underlying observations gives.

    Fails closed throughout. Fewer than two eras cannot be cross-checked; an era
    with no observations makes the pooled figure unverifiable; and an estimate
    indistinguishable from zero has no sign to be consistent with. In each case
    the answer is "not admissible", because an unverifiable condition is not a
    satisfied one.
    """
    reasons: List[str] = []
    clean = {e: (float(v), int(n)) for e, (v, n) in (per_era or {}).items() if int(n) > 0}
    if len(clean) < 2:
        return False, [f"only {len(clean)} era(s) with observations; a pooled "
                       f"estimate cannot be cross-checked against itself"]

    total_n = sum(n for _, n in clean.values())
    pooled = sum(v * n for v, n in clean.values()) / total_n
    if abs(pooled) < 1e-12:
        return False, ["pooled estimate is zero; nothing to rely on"]

    # D5.1 — sign consistency.
    off = {e: v for e, (v, _) in clean.items()
           if (v > 0) != (pooled > 0) or v == 0.0}
    if off:
        detail = ", ".join(f"{e} {v:+.4f}" for e, v in sorted(off.items()))
        reasons.append(f"D5.1 sign flip: pooled {pooled:+.4f} but {detail}")

    # D5.2 — no single era may carry the estimate (leave-one-out jackknife).
    for e in sorted(clean):
        rest = {k: val for k, val in clean.items() if k != e}
        rest_n = sum(n for _, n in rest.values())
        if rest_n <= 0:
            reasons.append(f"D5.2 unverifiable: dropping {e} leaves no observations")
            continue
        without = sum(v * n for v, n in rest.values()) / rest_n
        drift = abs(without - pooled) / abs(pooled)
        if drift > POOLED_DOMINANCE_LIMIT:
            reasons.append(
                f"D5.2 {e} carries it: dropping {e} moves the estimate "
                f"{drift:.0%} ({pooled:+.4f} -> {without:+.4f})")

    return (not reasons), reasons


# ── Section 4: multiple comparisons ──────────────────────────────────────────
def noise_threshold(n_configs: int) -> float:
    """Expected max |t| across `n_configs` pure-noise configurations.

    sqrt(2 ln N) — the standard order-statistic approximation, already used by
    sweep_dte.py. This is the bar a screen result must clear to be worth an
    out-of-sample test. It is NOT a significance test and clearing it does not
    make a result true.
    """
    return math.sqrt(2 * math.log(max(int(n_configs), 2)))


# ── power: Section 4's bar and Amendment A5's floor are one constraint ───────
# For a strategy with annualised Sharpe S observed over Y years, the t-statistic
# of its mean is approximately S * sqrt(Y), independent of trade frequency --
# trading more often buys more trades but each carries proportionally less.
# Checked against all six closed hypotheses on 2026-08-10: predicted/actual
# ratios spanned 0.83-1.32, mean 1.01. Good to about +/-30%, which is enough to
# reason with and not enough to cite to two decimal places.
#
# The consequence is not obvious and is worth stating plainly: Section 4's
# multiple-comparisons bar and Amendment A5's Sharpe floor are the SAME
# constraint seen twice. Widening a sweep raises the bar, which raises the
# smallest Sharpe the window can see, which can push A5's own floor out of
# reach. On the modern era that happens at four configurations.
def detectable_sharpe(n_configs: int, years: float) -> float:
    """Smallest annualised Sharpe distinguishable from noise. sqrt(2 ln N)/sqrt(Y)."""
    if years <= 0:
        return float("inf")
    return noise_threshold(n_configs) / math.sqrt(years)


def max_configs_for_detectability(years: float,
                                  sharpe: Optional[float] = None) -> int:
    """Largest config budget that still leaves `sharpe` (default A5's floor) visible.

    Returns 0 when even a single configuration cannot see it -- which is not a
    quibble about budgets but a statement that the window is too short to answer
    the question at all.
    """
    target = MIN_OOS_SHARPE if sharpe is None else sharpe
    if years <= 0:
        return 0
    ceiling = target * math.sqrt(years)
    best = 0
    for n in range(1, 1000):
        if noise_threshold(n) <= ceiling:
            best = n
        else:
            break
    return best


def trades_needed_for_a5(years: float, train_months: int = 6) -> float:
    """Total trades a window must produce to yield A5's >= 100 OOS trades.

    `walk_forward` is anchored with a `train_months` warm-up and one-month test
    folds, so every month after the first is out-of-sample. A hypothesis whose
    configuration cannot supply this cannot satisfy A5 whatever its edge, and
    that is checkable before registration rather than after a walk-forward.
    """
    months = years * 12.0
    oos_months = months - train_months
    if oos_months <= 0:
        return float("inf")
    return MIN_OOS_TRADES * months / oos_months


# ── Section 6: what does not count as evidence ───────────────────────────────
# 6.4 — n < 20 trades is an unsampled tail, not an edge.
MIN_TRADES_FOR_EVIDENCE = 20

# 6.5 — profit factor > 3 on n < 50 is implausible for a spread structure; real
# credit-spread PFs run 1.1-1.5. Treat as a fill-model bug until proven otherwise.
IMPLAUSIBLE_PF = 3.0
IMPLAUSIBLE_PF_BELOW_N = 50

# 6.6 — "P&L moves materially between gate=off and gate=strict".
#
# INTERPRETATION (Amendment B). Total P&L is the wrong quantity to compare: a
# strict gate removes trades that could not have happened, so totals SHOULD fall,
# and a strategy would be condemned for the gate working. What indicts the
# backtest is the per-trade edge changing — that means the fill model, not the
# market, produced the number. So materiality is judged on expectancy and PF:
MATERIAL_EXPECTANCY_DRIFT = 0.50   # >50% relative change in expectancy/trade
PROMOTION_PF = 1.25                # a PF crossing this bar changes the verdict

# 6.7 — "a single good configuration surrounded by bad ones". Applies only to a
# registered sweep: the winning cell's immediate neighbours on the swept axis
# must not all be negative. One adjacent cell agreeing is the minimum shape a
# real effect can have; this is deliberately generous, since it only has to
# catch the isolated spike.
MIN_AGREEING_NEIGHBOURS = 1


def gate_materiality(off: Dict, strict: Dict) -> Tuple[bool, List[str]]:
    """(material, reasons) comparing the same strategy under gate off vs strict.

    Material means: the fill model is carrying the result. See 6.6 above for why
    this is judged on per-trade edge rather than total P&L.
    """
    reasons: List[str] = []
    e_off, e_str = off.get("expectancy", 0.0), strict.get("expectancy", 0.0)
    pf_off, pf_str = off.get("profit_factor", 0.0), strict.get("profit_factor", 0.0)

    if (e_off > 0) != (e_str > 0):
        reasons.append(f"expectancy sign flips: {e_off:+,.0f} -> {e_str:+,.0f}")
    elif abs(e_off) > 1e-9:
        drift = abs(e_str - e_off) / abs(e_off)
        if drift > MATERIAL_EXPECTANCY_DRIFT:
            reasons.append(f"expectancy moves {drift:.0%}: {e_off:+,.0f} -> {e_str:+,.0f}")

    if (pf_off >= PROMOTION_PF) != (pf_str >= PROMOTION_PF):
        reasons.append(f"PF crosses {PROMOTION_PF}: {pf_off:.2f} -> {pf_str:.2f}")

    return bool(reasons), reasons


# ── Amendment A: portfolio targets ───────────────────────────────────────────
# A5 — a candidate must reach OOS Sharpe >= 0.8 on >= 100 OOS trades.
MIN_OOS_SHARPE = 0.8
PREFERRED_OOS_SHARPE = 1.0
MIN_OOS_TRADES = 100

# A2 — average pairwise correlation <= 0.15, measured on OOS daily returns, and
# checked BEFORE P&L: a candidate correlated with an existing survivor adds
# nothing to the portfolio however good its own numbers are.
MAX_PAIRWISE_CORR = 0.15
PORTFOLIO_N_RANGE = (6, 15)

# A1/A3 — absolute drawdown budget; the loss that would actually cause the
# operator to switch the system off.
#
# Amendment E (2026-08-14) SUPERSEDES the rupee figure. Rs 1,00,000 was 6.67% of
# a Rs 15,00,000 capital base that the operator never chose — it descended from
# an earlier session's sizing recommendation and became an axiom. Actual capital
# is Rs 50,000-1,00,000, so a Rs 1L drawdown budget is the entire account twice
# over. The budget is now a PERCENTAGE, which is the whole point of E: no rupee
# figure gets to be an axiom again.
DRAWDOWN_BUDGET_PCT = 0.15        # E2 — max drawdown <= 15% of equity
TRADING_CAPITAL_RANGE_RS = (50_000.0, 100_000.0)  # E0 — operator's stated range

# Kept only so that anything still importing it fails loudly rather than silently
# sizing against a dead premise.
DRAWDOWN_BUDGET_RS = None         # E: use DRAWDOWN_BUDGET_PCT * equity

MIN_PORTFOLIO_SHARPE = 1.4        # FD parity — below this the project stops
WORTH_IT_PORTFOLIO_SHARPE = 2.0
TARGET_PORTFOLIO_SHARPE = 3.0
# E2 — the operator's "profitable every month" reads as a variance preference,
# not a literal target (12/12 needs Sharpe 10.7). 75% of months needs 2.34.
PHASE2_TARGET_SHARPE = 2.3


def drawdown_budget_rs(equity: float) -> float:
    """E2's drawdown budget in rupees, for a given equity. Always derived."""
    return DRAWDOWN_BUDGET_PCT * float(equity)


def portfolio_sharpe(individual: float, n: int, rho: float) -> float:
    """S * sqrt(N / [1 + (N-1) rho]) — Amendment A2.

    Ceiling as N grows is S / sqrt(rho), which is why Sharpe-0.5 strategies are
    inadmissible at any count.
    """
    if n <= 0:
        return 0.0
    denom = 1.0 + (n - 1) * rho
    return individual * math.sqrt(n / denom) if denom > 0 else 0.0


def sharpe_ceiling(individual: float, rho: float) -> float:
    """The best portfolio Sharpe reachable by stacking this strategy forever."""
    return individual / math.sqrt(rho) if rho > 0 else float("inf")


def days_to_stop(today: Optional[datetime.date] = None) -> int:
    """Calendar days left before the Section 7 review date."""
    return (PROJECT_STOP_DATE - (today or datetime.date.today())).days
