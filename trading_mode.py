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

# Below this near-spot priceable share an expiry is treated as untradeable by
# entry selection. Kept in sync with DhanBroker.MIN_CHAIN_QUALITY_PCT.
LADDER_MIN_CHAIN_QUALITY = 80.0


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


def select_ladder_expiry(exp_list, today=None, quality=None):
    """Pick the ladder entry expiry from a broker expiry list.

    Selection is liquidity-aware, because a date the ladder likes is worthless
    if its book cannot be priced. `quality` maps expiry -> measured near-spot
    priceable percent (written by the harvester); an expiry scoring below
    LADDER_MIN_CHAIN_QUALITY is skipped, unmeasured expiries are tried so they
    get scored, and a totally unusable list degrades to the best available
    rather than halting the ladder.

    Measurement drives this, NOT a calendar heuristic. Monthly-vs-weekly was
    tested on live NIFTY books (2026-08-07) and does not predict priceability at
    ladder horizons — time to expiry does. The 08-25 monthly at 18 DTE quoted
    100% of near-spot strikes while the 09-29 monthly at 53 DTE quoted 52%,
    WORSE than the 09-08 weekly at 32 DTE (60%). Preferring monthlies would have
    picked the worse book.

    Order of preference, applied to non-rejected expiries:
      1. the [LADDER_DTE_MIN, LADDER_DTE_MAX] window, best measured quality first
      2. the closest holdable expiry (DTE > management DTE) when the window is
         empty — never the near weekly, which the {manage}-DTE rule would
         force-close instantly (friction churn)
      3. the nearest expiry, when nothing is holdable

    Returns (expiry, dte, reason). reason ∈ {in_window, closest_holdable,
    fallback_nearest, no_expiries}, suffixed "_degraded" when every candidate
    scored unpriceable and one had to be used anyway. expiry is None only when
    no date parses.
    """
    import datetime as _dt
    today = today or _dt.date.today()
    manage_dte = ladder_manage_dte()
    scores = {str(k)[:10]: v for k, v in (quality or {}).items()}
    dated = []
    for e in (exp_list or []):
        try:
            d = (_dt.date.fromisoformat(str(e)[:10]) - today).days
        except (ValueError, TypeError):
            continue
        dated.append((d, e))
    if not dated:
        return None, None, "no_expiries"

    def _rejected(e):
        """True only for an expiry MEASURED below the quality floor."""
        pct = scores.get(str(e)[:10])
        return pct is not None and float(pct) < LADDER_MIN_CHAIN_QUALITY

    def _rank(d, e):
        """Sort key: best measured book first, then nearest DTE.

        Unmeasured expiries sort just behind a perfect score so they are tried
        ahead of anything known to be mediocre, which is how they get measured.
        """
        pct = scores.get(str(e)[:10])
        return (-(float(pct) if pct is not None else 99.9), d)

    def _dist(d):
        if d < LADDER_DTE_MIN:
            return LADDER_DTE_MIN - d
        if d > LADDER_DTE_MAX:
            return d - LADDER_DTE_MAX
        return 0

    def _choose(cands):
        """Best expiry from `cands`, or (None, None, None) if it is empty."""
        in_window = [(d, e) for d, e in cands if LADDER_DTE_MIN <= d <= LADDER_DTE_MAX]
        if in_window:
            d, e = min(in_window, key=lambda t: _rank(*t))
            return e, d, "in_window"

        holdable = [(d, e) for d, e in cands if d > manage_dte]
        if holdable:
            # closest to the window; tie-break toward the longer hold
            d, e = min(holdable, key=lambda t: (_dist(t[0]), -t[0]))
            return e, d, "closest_holdable"

        if cands:
            # everything is <= manage_dte — keep the nearest (will be managed out
            # quickly, but there is no holdable alternative)
            d, e = min(cands, key=lambda t: t[0])
            return e, d, "fallback_nearest"
        return None, None, None

    usable = [(d, e) for d, e in dated if not _rejected(e)]
    exp, dte, why = _choose(usable)
    if why is not None:
        return exp, dte, why

    # every expiry measured unpriceable: surface it in the reason and hand back
    # the least-bad book rather than silently refusing every trade downstream
    exp, dte, why = _choose(dated)
    return exp, dte, f"{why}_degraded"


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
