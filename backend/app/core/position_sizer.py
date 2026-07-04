"""
Phase 4 — position sizing: risk is the unit of account, not lots.

lots = min(L_risk, L_vol, L_kelly), identical math to the validated
backtester (backtest/real_backtester.py) so live sizing is exactly what was
tested:

  L_risk  = floor(RISK_FRAC x equity / max_loss_per_lot)
            (min-lot exception if 1 lot still fits under HARD_CAP)
  L_vol   = floor(SIGMA_TARGET x equity / (dnet x spot x sigma_daily x lot))
            skipped when no realized-vol estimate is available
  L_kelly = floor(KELLY_FRAC x f* x equity / max_loss_per_lot)
            f* = p - (1-p)/b over the last KELLY_LOOKBACK closed trades;
            f* <= 0 -> KELLY_PROBE_LOTS (keeps the estimator alive);
            skipped below KELLY_MIN_TRADES

Equity comes from the TRADING_EQUITY env var (the account's actual capital);
returns 0 lots -> the trade must NOT be taken.
"""
import math
import os
from typing import Any, Dict, List, Optional, Tuple

RISK_FRAC = 0.015
HARD_CAP = 0.03
SIGMA_TARGET = 0.004
KELLY_FRAC = 0.25
KELLY_LOOKBACK = 50
KELLY_MIN_TRADES = 20
KELLY_PROBE_LOTS = 1
MAX_LOTS = 20


def account_equity() -> float:
    try:
        return float(os.getenv("TRADING_EQUITY", "500000"))
    except ValueError:
        return 500_000.0


def kelly_fraction(recent_pnls: List[float]) -> Optional[float]:
    """f* = p - q/b from recent realized P&Ls. None if sample too small."""
    recent = recent_pnls[-KELLY_LOOKBACK:]
    if len(recent) < KELLY_MIN_TRADES:
        return None
    wins = [p for p in recent if p > 0]
    losses = [-p for p in recent if p <= 0]
    if not losses:
        return 1.0
    if not wins:
        return 0.0
    p = len(wins) / len(recent)
    b = (sum(wins) / len(wins)) / (sum(losses) / len(losses))
    return p - (1 - p) / b


def size_lots(
    *,
    equity: Optional[float] = None,
    width: float,
    credit: float,
    lot_size: int,
    spot: Optional[float] = None,
    dnet: float = 0.10,
    realized_vol_ann: Optional[float] = None,
    recent_pnls: Optional[List[float]] = None,
) -> Tuple[int, Dict[str, Any]]:
    """Lots for a defined-risk credit spread. Returns (lots, detail).

    width/credit per share; lot_size from the scrip master; dnet = net spread
    delta per share (|d_short| - |d_long|).
    """
    eq = equity if equity is not None else account_equity()
    if width <= 0 or lot_size <= 0 or eq <= 0:
        return 0, {"reason": "bad_inputs"}

    max_loss_per_lot = max((width - max(credit, 0.0)) * lot_size, 1.0)
    l_risk = int(RISK_FRAC * eq / max_loss_per_lot)
    if l_risk < 1 and max_loss_per_lot <= HARD_CAP * eq:
        l_risk = 1

    candidates = [l_risk]
    l_vol = None
    if realized_vol_ann and realized_vol_ann > 0 and spot and spot > 0:
        sigma_d = realized_vol_ann / math.sqrt(252)
        lot_daily_vol = max(dnet, 0.02) * spot * sigma_d * lot_size
        l_vol = int(SIGMA_TARGET * eq / max(lot_daily_vol, 1.0))
        candidates.append(l_vol)

    l_kelly, f_star = None, None
    if recent_pnls is not None:
        f_star = kelly_fraction(recent_pnls)
        if f_star is not None:
            l_kelly = (KELLY_PROBE_LOTS if f_star <= 0
                       else int(KELLY_FRAC * f_star * eq / max_loss_per_lot))
            candidates.append(l_kelly)

    lots = max(0, min(min(candidates), MAX_LOTS))
    return lots, {
        "equity": eq, "max_loss_per_lot": round(max_loss_per_lot, 2),
        "l_risk": l_risk, "l_vol": l_vol, "l_kelly": l_kelly,
        "f_star": round(f_star, 4) if f_star is not None else None,
    }
