"""
Phase 3 — transaction friction model (shared by live execution and backtests).

Computes the real cost of trading a leg: brokerage, STT, exchange transaction
charges, SEBI turnover fee, IPFT, stamp duty and GST. These charges routinely
sum to 2-3x the flat assumptions used before Phase 3, and for multi-leg option
spreads they materially change whether a strategy has positive expectancy.

Rate card (Indian F&O, NSE, discount broker — e.g. Dhan):

  OPTIONS (turnover = premium x quantity):
    brokerage        flat Rs 20 per executed leg-order
    STT              0.1%      SELL side only (on premium)
    exchange txn     0.03503%  both sides (NSE options)
    SEBI fee         0.0001%   both sides
    IPFT             0.0005%   both sides
    stamp duty       0.003%    BUY side only
    GST              18% on (brokerage + exchange txn + SEBI + IPFT)

  FUTURES (turnover = price x quantity, i.e. notional):
    brokerage        flat Rs 20 per executed leg-order
    STT              0.02%     SELL side only
    exchange txn     0.00173%  both sides
    SEBI fee         0.0001%   both sides
    IPFT             0.0001%   both sides
    stamp duty       0.002%    BUY side only
    GST              18% on (brokerage + exchange txn + SEBI + IPFT)

NOTE: rates change with the Union Budget / SEBI circulars. These are the
FY 2025-26 published rates — verify against the broker's contract note for the
current FY before trusting backtest results to the rupee.
"""
from typing import Any, Dict, Iterable, List, Optional

BROKERAGE_PER_ORDER = 20.0
GST_RATE = 0.18

# side-dependent rates as fractions of turnover
_RATES = {
    "option": {
        "stt_sell": 0.001,       # 0.1% on sell premium
        "exchange_txn": 0.0003503,
        "sebi": 0.000001,        # 0.0001%
        "ipft": 0.000005,        # 0.0005%
        "stamp_buy": 0.00003,    # 0.003% on buy
    },
    "future": {
        "stt_sell": 0.0002,      # 0.02% on sell notional
        "exchange_txn": 0.0000173,
        "sebi": 0.000001,
        "ipft": 0.000001,        # 0.0001%
        "stamp_buy": 0.00002,    # 0.002% on buy
    },
}

_BREAKDOWN_KEYS = ("brokerage", "stt", "exchange_txn", "sebi", "ipft", "stamp_duty", "gst")


def _zero_breakdown() -> Dict[str, float]:
    d = {k: 0.0 for k in _BREAKDOWN_KEYS}
    d["total"] = 0.0
    return d


def leg_friction(
    *,
    side: str,
    price: float,
    quantity: int,
    instrument: str = "option",
    brokerage: float = BROKERAGE_PER_ORDER,
) -> Dict[str, float]:
    """Friction (Rs) for one executed leg-order.

    price is per share (option premium or futures price), quantity is absolute
    (lots x lot_size). Returns the full charge breakdown plus 'total'.
    """
    is_buy = str(side).upper() == "BUY"
    inst = "future" if str(instrument).lower() in ("fut", "future", "futures") else "option"
    r = _RATES[inst]
    turnover = max(float(price), 0.0) * max(int(quantity), 0)

    if turnover <= 0:
        # No fill value -> no ad-valorem charges; brokerage only applies to a
        # real executed order, which a zero-turnover leg is not.
        return _zero_breakdown()

    stt = 0.0 if is_buy else turnover * r["stt_sell"]
    exchange_txn = turnover * r["exchange_txn"]
    sebi = turnover * r["sebi"]
    ipft = turnover * r["ipft"]
    stamp = turnover * r["stamp_buy"] if is_buy else 0.0
    gst = GST_RATE * (brokerage + exchange_txn + sebi + ipft)

    out = {
        "brokerage": round(brokerage, 4),
        "stt": round(stt, 4),
        "exchange_txn": round(exchange_txn, 4),
        "sebi": round(sebi, 4),
        "ipft": round(ipft, 4),
        "stamp_duty": round(stamp, 4),
        "gst": round(gst, 4),
    }
    out["total"] = round(sum(out.values()), 2)
    return out


def basket_friction(
    legs: Iterable[Dict[str, Any]],
    *,
    default_quantity: Optional[int] = None,
) -> Dict[str, float]:
    """Aggregate friction (Rs) over a basket of executed legs.

    Accepts legs in either shape used across the system:
      * order_router basket legs: {side, opt_type, quantity, entry_fill}
      * pricing-service entry legs: {side, opt_type, entry_fill} (no quantity —
        pass default_quantity = lots x lot_size)
      * backtester legs: {side, opt_type, price, quantity}
    """
    total = _zero_breakdown()
    for leg in legs or []:
        qty = leg.get("quantity", default_quantity)
        if qty is None:
            raise ValueError("leg has no 'quantity' and no default_quantity given")
        price = leg.get("entry_fill", leg.get("price", 0.0)) or 0.0
        instrument = "future" if str(leg.get("opt_type", "")).lower() == "fut" else "option"
        f = leg_friction(side=str(leg.get("side", "")), price=float(price),
                         quantity=int(qty), instrument=instrument)
        for k in _BREAKDOWN_KEYS:
            total[k] += f[k]
    total["total"] = round(sum(total[k] for k in _BREAKDOWN_KEYS), 2)
    for k in _BREAKDOWN_KEYS:
        total[k] = round(total[k], 4)
    return total


def round_trip_friction(
    entry_legs: Iterable[Dict[str, Any]],
    exit_legs: Iterable[Dict[str, Any]],
    *,
    default_quantity: Optional[int] = None,
) -> Dict[str, Any]:
    """Entry + exit friction for a position. Returns
    {entry: breakdown, exit: breakdown, total: Rs}."""
    entry = basket_friction(entry_legs, default_quantity=default_quantity)
    exit_ = basket_friction(exit_legs, default_quantity=default_quantity)
    return {
        "entry": entry,
        "exit": exit_,
        "total": round(entry["total"] + exit_["total"], 2),
    }
