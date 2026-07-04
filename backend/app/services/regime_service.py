"""
Phase 4 — live regime gate service.

Feeds regime_filters.entry_allowed with the SAME data source the backtester
was validated on (the NSE bhavcopy cache in data/bhavcopy/), plus today's
live spot and chain IV. EOD data is exactly right for daily regime state —
and it works even when live infra is flaky.

State (daily closes + ATM IV history) is built once per day and kept
in-process. Yesterday's bhavcopy is fetched on demand (best-effort: a failed
download just means the series ends one day earlier).
"""
import datetime
import logging
from typing import Any, Dict, List, Optional, Tuple

from backend.app.core import regime_filters as rf

logger = logging.getLogger("RegimeService")

HISTORY_SESSIONS = 90          # enough for EMA20/ER/RV + a real IV-rank window
MIN_DAYS_TO_EXPIRY = 4         # IV tenor matches the traded 5-8 DTE structure

_GATED_STRATEGIES = {"BULL_PUT_SPREAD", "BEAR_CALL_SPREAD", "IRON_CONDOR"}


def _normalise(ticker: str) -> str:
    t = (ticker or "").replace("^", "").replace(".NS", "").replace(".BO", "").strip().upper()
    return {"NSEI": "NIFTY", "NSEBANK": "BANKNIFTY", "BSESN": "SENSEX"}.get(t, t)


class RegimeService:
    def __init__(self):
        self._built_for: Optional[datetime.date] = None
        self._underlying: Optional[str] = None
        self._closes: List[float] = []
        self._iv_hist: List[float] = []

    # ── state building ─────────────────────────────────────────────────────
    def _build_state(self, underlying: str, today: datetime.date):
        from backtest import bhavcopy  # lazy: pandas/requests only when needed

        closes: List[float] = []
        iv_hist: List[float] = []
        d = today - datetime.timedelta(days=1)
        tried = 0
        # walk back until we have HISTORY_SESSIONS sessions (or ran out of cache)
        while len(closes) < HISTORY_SESSIONS and tried < HISTORY_SESSIONS * 2:
            tried += 1
            if d.weekday() >= 5:
                d -= datetime.timedelta(days=1)
                continue
            try:
                bhavcopy.download(d)  # cached/holiday-marked after first hit
                chain = bhavcopy.load_chain(d, underlying)
            except Exception as e:
                logger.warning(f"regime state: {d} unavailable ({e})")
                chain = None
            if chain and chain.get("spot"):
                spot = float(chain["spot"])
                closes.append(spot)
                expiry = bhavcopy.nearest_expiry(chain, d, min_days=MIN_DAYS_TO_EXPIRY)
                iv = 0.0
                if expiry:
                    iv = rf.atm_straddle_iv(
                        spot, expiry, d,
                        lambda k, ty, _c=chain, _e=expiry: self._chain_close(_c, _e, k, ty))
                iv_hist.append(iv)
            d -= datetime.timedelta(days=1)

        # collected newest->oldest; series must be oldest->newest
        self._closes = list(reversed(closes))
        self._iv_hist = list(reversed(iv_hist))
        self._built_for = today
        self._underlying = underlying
        logger.info(f"🌡️ Regime state built: {len(self._closes)} sessions of {underlying} "
                    f"(RV10 {rf.realized_vol(self._closes):.3f}, "
                    f"ER {rf.efficiency_ratio(self._closes):.2f})")

    @staticmethod
    def _chain_close(chain, expiry, strike, opt_type) -> Optional[float]:
        row = chain["options"].get((expiry, float(strike), opt_type))
        if not row:
            return None
        c = float(row.get("close") or 0)
        return c if c > 0 else None

    def _ensure_state(self, underlying: str):
        today = datetime.date.today()
        if self._built_for != today or self._underlying != underlying:
            self._build_state(underlying, today)

    # ── the gate ───────────────────────────────────────────────────────────
    def evaluate(self, *, ticker: str, strategy_type: str, pcr: float,
                 spot: float, live_iv: Optional[float] = None,
                 gex_sign: int = 0) -> Tuple[bool, str, Optional[str]]:
        """(allowed, reason, suggested_strategy) for a candidate spread.

        Premium-selling structures are fully regime-gated; other structures
        pass through the event gate only. When the regime licenses a
        DIFFERENT structure than the candidate (e.g. middle PCR -> iron
        condor), it is returned as `suggested` so the worker can convert.
        Calendar is classified but NOT live-wired yet (multi-expiry chain
        fetch pending validation), so it comes back as a suggestion the
        worker logs and skips. live_iv as a fraction (0.13) — falls back to
        yesterday's bhavcopy ATM IV.
        """
        st = (strategy_type or "").upper()
        today = datetime.date.today()

        if st not in _GATED_STRATEGIES:
            ok, why = rf.event_gate(today)
            return (True, "ok", None) if ok else (False, why, None)

        try:
            self._ensure_state(_normalise(ticker))
        except Exception as e:
            # No regime state -> refuse to sell premium blind (fail-safe)
            logger.error(f"regime state unavailable: {e}")
            return False, "regime_state_unavailable", None

        iv = live_iv if (live_iv and live_iv > 0) else (
            self._iv_hist[-1] if self._iv_hist else 0.0)
        closes = self._closes + ([spot] if spot > 0 else [])

        # allow_ic/allow_calendar False: iron condor was REJECTED by the
        # 2026-07-04 walk-forward (negative OOS, 2x friction) and calendar is
        # UNVALIDATED (0 OOS trades). Flip only after a passing revalidation.
        suggested, reason = rf.classify_entry(
            side_pcr=pcr, spot=spot, closes=closes, iv=iv,
            iv_hist=self._iv_hist, on_date=today, gex_sign=gex_sign,
            allow_ic=False, allow_calendar=False)

        if suggested is None:
            return False, reason, None
        if suggested == st:
            return True, "ok", suggested
        return False, f"regime_suggests_{suggested}", suggested


regime_service = RegimeService()
