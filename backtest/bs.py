"""Black-Scholes utilities — canonical implementation lives in
backend/app/core/bs_math.py so the live worker can use it without importing
the backtest package. This module re-exports it for backtest-side callers."""
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from backend.app.core.bs_math import (  # noqa: F401
    DEFAULT_R, price, implied_vol, delta, gamma,
)
