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
