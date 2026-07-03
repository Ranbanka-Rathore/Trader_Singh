"""
Phase 1 — real option pricing.

Turns the per-strike premium map published by the option-chain parser
(`option_premiums:{ticker}` in Redis) into:
  * per-leg marks (bid/ask/mid/iv/greeks),
  * paper fills (bid for sells, ask for buys, +/- slippage) — real premiums,
    fake fills, exactly as Fable's Phase 1 item 9 specifies,
  * a spread's real net entry premium and per-leg entry record, and
  * mark-to-market P&L of an open position from live leg marks.

Everything degrades gracefully: if the premium map is stale/missing, or the
strategy needs multiple expiries (calendar/diagonal) which a single-expiry chain
can't price, the caller is told `pricing_source == "HEURISTIC_FALLBACK"` so the
old synthetic math is used and the UI can flag it.
"""
import logging
import time
from typing import Any, Dict, List, Optional, Tuple

from backend.app.services.redis_service import redis_service

logger = logging.getLogger("OptionsPricing")

# NSE option tick is ₹0.05; 2 ticks of slippage per Fable's spec.
TICK_SIZE = 0.05
SLIPPAGE_TICKS = 2
SLIPPAGE = TICK_SIZE * SLIPPAGE_TICKS

# Premium map older than this (seconds) is considered stale -> heuristic fallback.
MAX_PREMIUM_AGE_SEC = 120

SOURCE_LIVE = "DHAN_LIVE"
SOURCE_FALLBACK = "HEURISTIC_FALLBACK"

# Strategies that a single nearest-expiry chain cannot price (need >1 expiry).
_MULTI_EXPIRY = {"CALENDAR_SPREAD", "DIAGONAL_SPREAD"}


def _normalise(ticker: str) -> str:
    t = (ticker or "").replace("^", "").replace(".NS", "").replace(".BO", "").strip().upper()
    return {"NSEI": "NIFTY", "NSEBANK": "BANKNIFTY", "BSESN": "SENSEX"}.get(t, t)


def leg_specs(strategy_type: str, leg_1_sell: float, leg_2_buy: float) -> Optional[List[Dict[str, Any]]]:
    """Return the option legs for a strategy as [{strike, opt_type, side}], or
    None if the strategy can't be priced from a single-expiry chain.

    opt_type: 'ce' | 'pe' | 'fut'. side: 'BUY' | 'SELL'. leg_1_sell is the SOLD
    strike, leg_2_buy the BOUGHT strike, matching options_desk_service output.
    """
    st = (strategy_type or "").upper()
    if st in _MULTI_EXPIRY:
        return None

    if st == "BULL_PUT_SPREAD":
        return [
            {"strike": leg_1_sell, "opt_type": "pe", "side": "SELL"},
            {"strike": leg_2_buy, "opt_type": "pe", "side": "BUY"},
        ]
    if st == "BEAR_CALL_SPREAD":
        return [
            {"strike": leg_1_sell, "opt_type": "ce", "side": "SELL"},
            {"strike": leg_2_buy, "opt_type": "ce", "side": "BUY"},
        ]
    if st in ("DEBIT_BULL_SPREAD",):
        return [
            {"strike": leg_2_buy, "opt_type": "ce", "side": "BUY"},
            {"strike": leg_1_sell, "opt_type": "ce", "side": "SELL"},
        ]
    if st in ("DEBIT_BEAR_SPREAD",):
        return [
            {"strike": leg_2_buy, "opt_type": "pe", "side": "BUY"},
            {"strike": leg_1_sell, "opt_type": "pe", "side": "SELL"},
        ]
    if st == "DELTA_NEUTRAL":
        # Short straddle: sell ATM call + sell ATM put (same strike).
        return [
            {"strike": leg_1_sell, "opt_type": "ce", "side": "SELL"},
            {"strike": leg_2_buy or leg_1_sell, "opt_type": "pe", "side": "SELL"},
        ]
    if st == "CASH_SECURED_PUT":
        return [{"strike": leg_1_sell, "opt_type": "pe", "side": "SELL"}]
    if st == "COVERED_CALL":
        # Short OTM call + long future (future priced off spot, opt_type 'fut').
        return [
            {"strike": leg_1_sell, "opt_type": "ce", "side": "SELL"},
            {"strike": 0.0, "opt_type": "fut", "side": "BUY"},
        ]
    # Unknown / generic DEBIT_SPREAD -> let caller fall back.
    return None


def sign_of(side: str) -> int:
    """+1 for a long (BUY) leg, -1 for a short (SELL) leg."""
    return 1 if str(side).upper() == "BUY" else -1


def paper_fill_price(leg: Dict[str, float], side: str) -> float:
    """Simulated fill using real quotes: sells hit the bid, buys lift the ask,
    each worsened by SLIPPAGE. Falls back to mid/ltp if a book side is empty."""
    bid = float(leg.get("bid") or 0.0)
    ask = float(leg.get("ask") or 0.0)
    mid = float(leg.get("mid") or 0.0)
    ltp = float(leg.get("ltp") or 0.0)
    ref = mid or ltp

    if str(side).upper() == "SELL":
        base = bid if bid > 0 else ref
        return round(max(base - SLIPPAGE, 0.05), 2)
    else:  # BUY
        base = ask if ask > 0 else ref
        return round(base + SLIPPAGE, 2)


class OptionsPricingService:
    async def get_premium_chain(self, ticker: str) -> Optional[Dict[str, Any]]:
        """Fetch the fresh per-strike premium map for a ticker, or None if
        missing/stale."""
        key = f"option_premiums:{_normalise(ticker)}"
        try:
            payload = await redis_service.get_json(key)
        except Exception as e:
            logger.debug(f"premium chain read failed for {ticker}: {e}")
            return None
        if not payload or "strikes" not in payload:
            return None
        ts = float(payload.get("timestamp", 0) or 0)
        if ts and (time.time() - ts) > MAX_PREMIUM_AGE_SEC:
            logger.debug(f"premium chain for {ticker} is stale ({time.time()-ts:.0f}s)")
            return None
        return payload

    def _leg_from_chain(self, chain: Dict[str, Any], strike: float, opt_type: str) -> Optional[Dict[str, float]]:
        """Look up a single leg's premium dict from a premium chain, matching the
        nearest available strike key."""
        strikes = chain.get("strikes", {})
        key = f"{float(strike):.2f}"
        node = strikes.get(key)
        if node is None:
            # Tolerant match for float-formatting differences only (not a far strike).
            try:
                available = {float(k): k for k in strikes.keys()}
                nearest = min(available, key=lambda s: abs(s - float(strike)))
                if abs(nearest - float(strike)) <= 0.01:
                    node = strikes.get(available[nearest])
            except (ValueError, TypeError):
                return None
        if not node:
            return None
        return node.get(opt_type)

    async def price_spread_entry(self, spread: Dict[str, Any]) -> Dict[str, Any]:
        """Compute a spread's real net entry premium (per share) and per-leg entry
        records using paper fills. Returns a dict with `pricing_source`.

        On fallback the caller keeps the incoming synthetic `net_credit_per_share`.
        """
        ticker = spread.get("ticker", "")
        strategy = spread.get("strategy_type", "")
        leg_1_sell = float(spread.get("leg_1_sell", 0) or 0)
        leg_2_buy = float(spread.get("leg_2_buy", 0) or 0)
        spot = float(spread.get("spot_price", 0) or 0)

        specs = leg_specs(strategy, leg_1_sell, leg_2_buy)
        if specs is None:
            return {"pricing_source": SOURCE_FALLBACK, "reason": "unsupported_or_multi_expiry"}

        chain = await self.get_premium_chain(ticker)
        if chain is None:
            return {"pricing_source": SOURCE_FALLBACK, "reason": "no_premium_chain"}

        legs: List[Dict[str, Any]] = []
        net = 0.0  # + = we pay (debit), - = we receive (credit)
        for spec in specs:
            side = spec["side"]
            sgn = sign_of(side)
            if spec["opt_type"] == "fut":
                fill = round(spot, 2)
                leg_rec = {"opt_type": "fut", "strike": 0.0, "side": side,
                           "entry_fill": fill, "iv": 0.0, "delta": 1.0,
                           "gamma": 0.0, "theta": 0.0, "vega": 0.0}
                # future doesn't contribute option premium to net credit/debit
                legs.append(leg_rec)
                continue

            leg = self._leg_from_chain(chain, spec["strike"], spec["opt_type"])
            if not leg or (float(leg.get("bid") or 0) <= 0 and float(leg.get("ltp") or 0) <= 0):
                return {"pricing_source": SOURCE_FALLBACK, "reason": f"no_quote_{spec['opt_type']}_{spec['strike']}"}
            fill = paper_fill_price(leg, side)
            net += sgn * fill
            legs.append({
                "opt_type": spec["opt_type"], "strike": spec["strike"], "side": side,
                "entry_fill": fill, "iv": leg.get("iv", 0.0), "delta": leg.get("delta", 0.0),
                "gamma": leg.get("gamma", 0.0), "theta": leg.get("theta", 0.0), "vega": leg.get("vega", 0.0),
            })

        # net_credit_per_share is expressed as a positive credit for credit strategies.
        net_credit = round(-net, 2)  # net<0 (received) -> positive credit
        return {
            "pricing_source": SOURCE_LIVE,
            "net_premium_per_share": round(net, 2),      # signed: + debit / - credit
            "net_credit_per_share": net_credit,          # positive for credit spreads
            "legs": legs,
            "expiry": chain.get("expiry"),
            "priced_at": time.time(),
        }

    async def mark_position_pnl(self, pos, current_spot: Optional[float] = None) -> Optional[Dict[str, Any]]:
        """Mark-to-market P&L per share for an open position from live leg marks.

        Reads the entry legs stored at execution time in
        learning_context['entry_pricing']['legs']. Returns None if real pricing
        isn't available (caller uses the heuristic).
        P&L/share = Σ sign * (current_mark - entry_fill).
        """
        ctx = getattr(pos, "learning_context", None) or {}
        entry = ctx.get("entry_pricing") or {}
        legs = entry.get("legs")
        if not legs or entry.get("pricing_source") != SOURCE_LIVE:
            return None

        chain = await self.get_premium_chain(pos.ticker)
        if chain is None:
            return None
        spot = current_spot if current_spot is not None else float(chain.get("spot", 0) or 0)

        pnl_per_share = 0.0
        marked_legs = []
        for leg in legs:
            sgn = sign_of(leg["side"])
            entry_fill = float(leg["entry_fill"])
            if leg["opt_type"] == "fut":
                current_mark = round(spot, 2)
            else:
                q = self._leg_from_chain(chain, leg["strike"], leg["opt_type"])
                if not q:
                    return None
                current_mark = float(q.get("mid") or q.get("ltp") or 0.0)
                if current_mark <= 0:
                    return None
            pnl_per_share += sgn * (current_mark - entry_fill)
            marked_legs.append({**leg, "current_mark": current_mark})

        return {
            "pricing_source": SOURCE_LIVE,
            "pnl_per_share": round(pnl_per_share, 2),
            "legs": marked_legs,
            "marked_at": time.time(),
        }


options_pricing_service = OptionsPricingService()
