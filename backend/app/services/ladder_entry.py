"""
Shared income-ladder live entry logic (Phase 7).

Single source of truth for the ladder's signal-driven entry, used by BOTH:
  - the LIVE microservice path — run_quant.py sources the candidate,
    run_risk_committee.py applies the gate before executing; and
  - the monolithic worker.py (AutopilotWorker), which is not launched by
    start_v8.bat but is kept in sync via this module.

This module exists specifically to prevent the drift that hid the ladder from
the live deployment through Phase 6: the ladder was wired into worker.py while
the running services (run_quant + run_risk_committee) never received it. Keep the
ladder decision here — never re-implement it inline in a service.

The ladder is signal-driven and is gated ONLY by regime_service.evaluate_ladder
plus a single-position-at-a-time guard (no new entry while one is already open).
It must NOT inherit the sniper's directional gauntlet (analyze_universe's PA
trigger / OBI / PCR band / GEX / ML guard). See LADDER_LIVE_REWIRE_PLAN.md.
"""
import asyncio
import logging

from sqlalchemy import select, func

from backend.app.services.redis_service import redis_service
from backend.app.db.models import OpenPosition

logger = logging.getLogger("LadderEntry")


async def source_ladder_candidate():
    """Return ``[candidate]`` built from the live NIFTY snapshot, or ``[]`` to skip.

    Bypasses the sniper's directional scan entirely — the ladder trades on signal,
    not the sniper's gauntlet. The candidate carries only what the options desk +
    evaluate_ladder need.
    """
    snap = await redis_service.get_json("market_snapshot:NIFTY")
    spot = float((snap or {}).get("price", 0) or 0)
    if not snap or spot <= 0:
        logger.info("🪜 [Ladder] no NIFTY snapshot yet — skipping cycle")
        return []
    pcr = float(snap.get("coi_pcr", snap.get("pcr", 1.0)) or 1.0)
    logger.info(f"🪜 [Ladder] candidate built | spot {spot} | pcr {pcr}")
    return [{
        "ticker": "NIFTY",
        "spot_price": spot,
        "coi_pcr": pcr,
        "ml_score": 0.5,   # ladder does not use a directional ML gate
        "pa_status": "LADDER_CADENCE",
        "learning_context": {"PA_Status": "LADDER_CADENCE"},
        "recommended_lots": 1,   # evaluate_ladder IVR mult + position_sizer set final size
    }]


async def apply_ladder_gate(session, spread):
    """Decide whether ``spread`` may enter as a new ladder tranche.

    Signal-driven: gated only by "no position already open" (single position
    at a time), then regime_service.evaluate_ladder for side + IVR size mult.
    On a pass, mutates ``spread`` in place (strategy_type/bias/_ivr_size_mult +
    chain-refined strikes) and returns ``(True, reason)``. On a block returns
    ``(False, reason)``.
    """
    n_open = (await session.execute(
        select(func.count()).select_from(OpenPosition))).scalar() or 0
    if n_open >= 1:
        return False, f"{n_open} position(s) already open"

    ticker = spread.get("ticker", "NIFTY")
    try:
        from backend.app.services.regime_service import regime_service
        side, ivr_mult, reason = await asyncio.to_thread(
            regime_service.evaluate_ladder,
            ticker=ticker,
            pcr=float(spread.get("coi_pcr", 1.0) or 1.0),
            spot=float(spread.get("spot_price", 0) or 0),
            live_iv=spread.get("_chain_atm_iv"),
        )
    except Exception as e:
        logger.error(f"ladder gate error for {ticker}: {e} — refusing entry")
        return False, "ladder_gate_error"
    if side is None:
        return False, reason

    spread["strategy_type"] = side
    spread["bias"] = "BULLISH" if side == "BULL_PUT_SPREAD" else "BEARISH"
    spread["_ivr_size_mult"] = ivr_mult
    try:
        from backend.app.services.options_pricing_service import options_pricing_service
        refined = await options_pricing_service.refine_spread_with_chain(spread)
        if refined is not None and refined is not spread:
            spread.clear()
            spread.update(refined)
    except Exception as e:
        logger.warning(f"strike refinement failed for {ticker}: {e}")

    logger.info(f"🪜 [Ladder] {ticker} {side} tranche | {reason} | size mult {ivr_mult}x")
    return True, reason
