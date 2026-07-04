"""
Regime filters — the gates that decide WHETHER the system is allowed to sell
premium today, and WHICH side. Shared verbatim by the backtester and the live
worker so simulation and production cannot drift.

All functions are pure: they take price/chain data as arguments and return
decisions. Callers supply data (backtester: bhavcopy chains; live: bhavcopy
cache + live Dhan chain).

The five gates (rationale in claudefable.md Phase-4 overhaul plan):
  1. VRP gate      — sell premium only when implied vol is rich vs realized
                     (IV - RV > 2 vol pts) AND IV rank > 0.3
  2. Side gate     — symmetric PCR with trend confirmation:
                     PCR >= 1.25 AND spot > EMA20 -> BULL_PUT
                     PCR <= 0.75 AND spot < EMA20 -> BEAR_CALL, else no trade
  3. ER gate       — Kaufman Efficiency Ratio > 0.4 blocks counter-trend side
                     (redundant with EMA confirmation by design: two cheap,
                     different lenses on the same failure mode)
  4. Event gate    — no new entries within the blackout window of scheduled
                     macro events (FOMC/RBI/budget) or monthly expiry +-1 day
  5. GEX sign gate — negative dealer gamma -> moves accelerate -> stand down

EVENT DATES CAVEAT: the calendars below are best-effort schedules compiled
2026-07; RBI MPC dates especially shift. Verify/refresh each quarter.
"""
import datetime
import math
from typing import Any, Dict, List, Optional, Tuple

from backend.app.core import bs_math

# ── tunables (fixed a priori — NOT part of the optimization grid) ───────────
VRP_MIN_POINTS = 0.02          # IV - RV must exceed 2 vol points (0.02)
IV_RANK_MIN = 0.30
ER_TREND_THRESHOLD = 0.40
ER_LOOKBACK = 10
RV_LOOKBACK = 10
EMA_PERIOD = 20
PCR_BULL = 1.25
PCR_BEAR = 0.75
EVENT_BLACKOUT_DAYS = 2        # block entries within N calendar days before event
IV_RANK_MIN_HISTORY = 60       # need this many IV observations before rank gate binds

# ── scheduled macro events (decision dates; verify quarterly) ────────────────
FOMC_DATES = [
    # 2024
    "2024-01-31", "2024-03-20", "2024-05-01", "2024-06-12",
    "2024-07-31", "2024-09-18", "2024-11-07", "2024-12-18",
    # 2025
    "2025-01-29", "2025-03-19", "2025-05-07", "2025-06-18",
    "2025-07-30", "2025-09-17", "2025-10-29", "2025-12-10",
    # 2026
    "2026-01-28", "2026-03-18", "2026-04-29", "2026-06-17",
    "2026-07-29", "2026-09-16", "2026-10-28", "2026-12-09",
]
RBI_MPC_DATES = [
    # 2024
    "2024-02-08", "2024-04-05", "2024-06-07", "2024-08-08",
    "2024-10-09", "2024-12-06",
    # 2025
    "2025-02-07", "2025-04-09", "2025-06-06", "2025-08-06",
    "2025-10-01", "2025-12-05",
    # 2026 (provisional)
    "2026-02-06", "2026-04-08", "2026-06-05", "2026-08-06",
    "2026-10-01", "2026-12-04",
]
OTHER_EVENT_DATES = [
    "2024-02-01",  # interim budget
    "2024-06-04",  # general election results
    "2024-07-23",  # full budget
    "2025-02-01", "2026-02-01",  # union budgets
]

_ALL_EVENTS = sorted(
    datetime.date.fromisoformat(s)
    for s in FOMC_DATES + RBI_MPC_DATES + OTHER_EVENT_DATES
)


# ── price-series statistics ──────────────────────────────────────────────────
def realized_vol(closes: List[float], lookback: int = RV_LOOKBACK) -> float:
    """Annualized close-to-close realized vol over the last `lookback` returns."""
    if len(closes) < lookback + 1:
        return 0.0
    tail = closes[-(lookback + 1):]
    rets = [math.log(tail[i] / tail[i - 1]) for i in range(1, len(tail))
            if tail[i - 1] > 0 and tail[i] > 0]
    if not rets:
        return 0.0
    mean = sum(rets) / len(rets)
    var = sum((r - mean) ** 2 for r in rets) / max(len(rets) - 1, 1)
    return math.sqrt(var * 252.0)


def efficiency_ratio(closes: List[float], lookback: int = ER_LOOKBACK) -> float:
    """Kaufman ER: |net move| / sum(|daily moves|) in [0, 1]. High = trending."""
    if len(closes) < lookback + 1:
        return 0.0
    tail = closes[-(lookback + 1):]
    net = abs(tail[-1] - tail[0])
    path = sum(abs(tail[i] - tail[i - 1]) for i in range(1, len(tail)))
    return (net / path) if path > 0 else 0.0


def ema(closes: List[float], period: int = EMA_PERIOD) -> float:
    """Standard EMA over the full series (last value)."""
    if not closes:
        return 0.0
    k = 2.0 / (period + 1)
    e = closes[0]
    for c in closes[1:]:
        e = c * k + e * (1 - k)
    return e


# ── implied vol from an EOD chain ────────────────────────────────────────────
def atm_straddle_iv(spot: float, expiry: datetime.date, on_date: datetime.date,
                    option_close: Any, strike_interval: int = 50) -> float:
    """ATM implied vol as the average of ATM CE and PE implied vols.

    option_close(strike, 'CE'|'PE') -> close price or None. Returns 0.0 if
    neither side inverts (illiquid/absent chain).
    """
    t = max((expiry - on_date).days, 0) / 365.0
    if t <= 0 or spot <= 0:
        return 0.0
    atm = round(spot / strike_interval) * strike_interval
    ivs = []
    for typ in ("CE", "PE"):
        p = option_close(float(atm), typ)
        if p and p > 0:
            iv = bs_math.implied_vol(p, spot, float(atm), t, typ)
            if iv > 0:
                ivs.append(iv)
    return sum(ivs) / len(ivs) if ivs else 0.0


def iv_rank(iv_history: List[float], current_iv: float) -> float:
    """Rank of current IV within history (rolling window handled by caller).
    Returns 0.5 while history is too thin to be meaningful."""
    hist = [v for v in iv_history if v > 0]
    if len(hist) < IV_RANK_MIN_HISTORY:
        return 0.5
    lo, hi = min(hist), max(hist)
    if hi <= lo:
        return 0.5
    return (current_iv - lo) / (hi - lo)


# ── gates ────────────────────────────────────────────────────────────────────
def vrp_gate(iv: float, rv: float, ivr: float) -> Tuple[bool, str]:
    """License to sell premium: IV rich vs realized AND not at the vol floor."""
    if iv <= 0:
        return False, "no_iv"
    if iv - rv < VRP_MIN_POINTS:
        return False, f"vrp_thin_iv{iv:.3f}_rv{rv:.3f}"
    if ivr < IV_RANK_MIN:
        return False, f"iv_rank_low_{ivr:.2f}"
    return True, "ok"


def choose_side(pcr: float, spot: float, ema20: float) -> Optional[str]:
    """Symmetric PCR with EMA trend confirmation.
    Returns 'BULL_PUT_SPREAD' | 'BEAR_CALL_SPREAD' | None."""
    if pcr >= PCR_BULL and spot > ema20 > 0:
        return "BULL_PUT_SPREAD"
    if 0 < pcr <= PCR_BEAR and 0 < spot < ema20:
        return "BEAR_CALL_SPREAD"
    return None


def er_gate(side: str, closes: List[float]) -> Tuple[bool, str]:
    """In a strong trend (ER > 0.4), block the counter-trend side."""
    er = efficiency_ratio(closes)
    if er <= ER_TREND_THRESHOLD or len(closes) < ER_LOOKBACK + 1:
        return True, "ok"
    trending_up = closes[-1] > closes[-(ER_LOOKBACK + 1)]
    if trending_up and side == "BEAR_CALL_SPREAD":
        return False, f"er_{er:.2f}_uptrend_blocks_bear_call"
    if not trending_up and side == "BULL_PUT_SPREAD":
        return False, f"er_{er:.2f}_downtrend_blocks_bull_put"
    return True, "ok"


def monthly_expiry(year: int, month: int, weekday: int = 1) -> datetime.date:
    """Last <weekday> of the month (default Tuesday=1 — NIFTY monthly, 2026 regime)."""
    d = datetime.date(year, month, 28) + datetime.timedelta(days=4)
    d = d.replace(day=1) - datetime.timedelta(days=1)  # last day of month
    while d.weekday() != weekday:
        d -= datetime.timedelta(days=1)
    return d


def event_gate(d: datetime.date) -> Tuple[bool, str]:
    """No new entries within EVENT_BLACKOUT_DAYS before a macro event (through
    the event day) or within +-1 day of monthly expiry."""
    for ev in _ALL_EVENTS:
        if 0 <= (ev - d).days <= EVENT_BLACKOUT_DAYS:
            return False, f"event_blackout_{ev.isoformat()}"
    me = monthly_expiry(d.year, d.month)
    if abs((me - d).days) <= 1:
        return False, f"monthly_expiry_{me.isoformat()}"
    return True, "ok"


def naive_gex_sign(spot: float, expiry: datetime.date, on_date: datetime.date,
                   chain_rows: Dict[Any, Dict[str, float]],
                   band_pct: float = 0.05) -> int:
    """Sign of naive dealer gamma exposure from an EOD chain.

    Convention (standard naive GEX): dealers long calls (+gamma), short puts
    (-gamma): GEX = sum[ gamma_k * OI_call - gamma_k * OI_put ] over strikes
    within +-band_pct of spot. Returns +1, -1, or 0 (no data).
    chain_rows: {(expiry, strike, 'CE'|'PE'): {close, oi, ...}}.
    """
    t = max((expiry - on_date).days, 0) / 365.0
    if t <= 0 or spot <= 0:
        return 0
    lo, hi = spot * (1 - band_pct), spot * (1 + band_pct)
    total = 0.0
    seen = False
    for (e, strike, typ), row in chain_rows.items():
        if e != expiry or not (lo <= strike <= hi):
            continue
        close = float(row.get("close") or 0)
        oi = float(row.get("oi") or 0)
        if close <= 0 or oi <= 0:
            continue
        iv = bs_math.implied_vol(close, spot, strike, t, typ)
        if iv <= 0:
            continue
        g = bs_math.gamma(spot, strike, t, iv)
        total += g * oi if typ == "CE" else -g * oi
        seen = True
    if not seen:
        return 0
    return 1 if total >= 0 else -1


def entry_allowed(*, side_pcr: float, spot: float, closes: List[float],
                  iv: float, iv_hist: List[float], on_date: datetime.date,
                  gex_sign: int = 0) -> Tuple[Optional[str], str]:
    """Run all gates in order. Returns (side or None, reason).

    gex_sign: pass 0 if unavailable (gate skipped) — the backtester computes
    it from the chain, the live worker from its GEX feed.
    """
    # warmup: EMA/ER/RV are garbage on a thin series — refuse to trade blind
    if len(closes) < max(EMA_PERIOD, RV_LOOKBACK + 1):
        return None, "warmup"

    ok, why = event_gate(on_date)
    if not ok:
        return None, why

    rv = realized_vol(closes)
    ivr = iv_rank(iv_hist, iv)
    ok, why = vrp_gate(iv, rv, ivr)
    if not ok:
        return None, why

    if gex_sign < 0:
        return None, "negative_gex"

    side = choose_side(side_pcr, spot, ema(closes))
    if side is None:
        return None, f"no_side_pcr_{side_pcr:.2f}"

    ok, why = er_gate(side, closes)
    if not ok:
        return None, why

    return side, "ok"
