"""
Black-Scholes utilities for EOD analytics: pricing, implied vol (bisection),
delta and gamma. Pure math, no dependencies beyond stdlib.

Used by the backtester for delta-targeted strike selection and by
regime_filters for ATM implied vol and naive GEX. European exercise on a
cash-settled index is the textbook case, so BS is appropriate here.

Conventions: t in years (ACT/365), r = risk-free (default 6.5% — Indian
short rate ballpark), q = 0 (index options on price index).
"""
import math

DEFAULT_R = 0.065


def _norm_cdf(x: float) -> float:
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def _norm_pdf(x: float) -> float:
    return math.exp(-0.5 * x * x) / math.sqrt(2.0 * math.pi)


def _d1(s: float, k: float, t: float, sigma: float, r: float) -> float:
    return (math.log(s / k) + (r + 0.5 * sigma * sigma) * t) / (sigma * math.sqrt(t))


def price(s: float, k: float, t: float, sigma: float, opt_type: str,
          r: float = DEFAULT_R) -> float:
    """BS price of a European CE/PE."""
    if t <= 0 or sigma <= 0:
        # expiry: intrinsic
        return max(s - k, 0.0) if opt_type.upper() == "CE" else max(k - s, 0.0)
    d1 = _d1(s, k, t, sigma, r)
    d2 = d1 - sigma * math.sqrt(t)
    if opt_type.upper() == "CE":
        return s * _norm_cdf(d1) - k * math.exp(-r * t) * _norm_cdf(d2)
    return k * math.exp(-r * t) * _norm_cdf(-d2) - s * _norm_cdf(-d1)


def implied_vol(target_price: float, s: float, k: float, t: float, opt_type: str,
                r: float = DEFAULT_R, lo: float = 0.01, hi: float = 3.0,
                tol: float = 1e-4, max_iter: int = 80) -> float:
    """Implied vol via bisection. Returns 0.0 if no solution in [lo, hi]
    (price below intrinsic, or absurd)."""
    if target_price <= 0 or t <= 0 or s <= 0:
        return 0.0
    intrinsic = max(s - k, 0.0) if opt_type.upper() == "CE" else max(k - s, 0.0)
    if target_price <= intrinsic * 1.0001:
        return 0.0
    p_lo = price(s, k, t, lo, opt_type, r)
    p_hi = price(s, k, t, hi, opt_type, r)
    if not (p_lo <= target_price <= p_hi):
        return 0.0
    for _ in range(max_iter):
        mid = 0.5 * (lo + hi)
        p_mid = price(s, k, t, mid, opt_type, r)
        if abs(p_mid - target_price) < tol:
            return mid
        if p_mid < target_price:
            lo = mid
        else:
            hi = mid
    return 0.5 * (lo + hi)


def delta(s: float, k: float, t: float, sigma: float, opt_type: str,
          r: float = DEFAULT_R) -> float:
    """BS delta (CE in [0,1], PE in [-1,0])."""
    if t <= 0 or sigma <= 0:
        if opt_type.upper() == "CE":
            return 1.0 if s > k else 0.0
        return -1.0 if s < k else 0.0
    d1 = _d1(s, k, t, sigma, r)
    if opt_type.upper() == "CE":
        return _norm_cdf(d1)
    return _norm_cdf(d1) - 1.0


def gamma(s: float, k: float, t: float, sigma: float, r: float = DEFAULT_R) -> float:
    """BS gamma (same for CE/PE)."""
    if t <= 0 or sigma <= 0:
        return 0.0
    d1 = _d1(s, k, t, sigma, r)
    return _norm_pdf(d1) / (s * sigma * math.sqrt(t))
