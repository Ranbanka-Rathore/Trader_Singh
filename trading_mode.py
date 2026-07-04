"""
Central trading-mode gate.

This is the single source of truth for whether the system is allowed to send
REAL orders to the broker. It is deliberately a small, dependency-light module
at the project root so every process (worker/OMS, hedger, RL-OMS, backtests,
health check) imports the exact same value.

    TRADING_MODE = "PAPER"  -> place_order is intercepted, nothing leaves to Dhan
    TRADING_MODE = "LIVE"   -> real orders are sent

Default is PAPER. Going live is an explicit, deliberate act: set TRADING_MODE=LIVE
in the environment / .env. Anything unrecognised falls back to PAPER (fail-safe).
"""
import os

try:
    from dotenv import load_dotenv
    load_dotenv()
except Exception:
    pass


def _resolve_mode() -> str:
    raw = (os.getenv("TRADING_MODE", "PAPER") or "PAPER").strip().upper()
    return "LIVE" if raw == "LIVE" else "PAPER"


TRADING_MODE = _resolve_mode()


def is_live() -> bool:
    """True only when real orders are permitted to leave for the broker.

    Re-reads the environment each call so a mode change (or a test monkeypatch
    of os.environ) is picked up without a restart.
    """
    return _resolve_mode() == "LIVE"


def mode() -> str:
    return _resolve_mode()


# ── Phase 6: strategy structure switch ───────────────────────────────────────
# LADDER_MODE=true switches the system from the gated 5-8 DTE sniper (hard
# regime gates, ~1 trade/month, validated at Rs 5L) to the validated income
# ladder (weekly tranches at 30-45 DTE, managed at 21 DTE, IVR-scaled sizing,
# max 6 concurrent, validated at Rs 15L / walk-forward e1bbdc4). Default OFF —
# flipping structures is a deliberate act, like going LIVE.
LADDER_DTE_MIN = 30
LADDER_DTE_MAX = 45
LADDER_MAX_OPEN = 6
LADDER_PORTFOLIO_MAX_LOSS_FRAC = 0.10
LADDER_IVR_SIZE_BASE = 0.5


def ladder_enabled() -> bool:
    """True when the income-ladder structure is active (env LADDER_MODE=true).
    Re-read each call, same as is_live()."""
    return (os.getenv("LADDER_MODE", "") or "").strip().lower() == "true"


def ladder_manage_dte() -> int:
    """Management exit DTE for ladder positions (default 21 — never hold the
    gamma half of an option's life)."""
    try:
        return int(os.getenv("LADDER_MANAGE_DTE", "21"))
    except ValueError:
        return 21


def banner() -> str:
    m = _resolve_mode()
    if m == "LIVE":
        return (
            "\n" + "!" * 60 +
            "\n  ⚠️  TRADING_MODE = LIVE — REAL ORDERS WILL BE SENT TO DHAN  ⚠️\n" +
            "!" * 60 + "\n"
        )
    return (
        "\n" + "=" * 60 +
        "\n  🧻 TRADING_MODE = PAPER — orders are simulated, none sent to broker\n" +
        "=" * 60 + "\n"
    )
