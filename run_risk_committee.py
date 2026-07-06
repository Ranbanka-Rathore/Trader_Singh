import asyncio
import json
import logging
import os
import selectors
import warnings
from datetime import datetime
from decimal import Decimal

# --- WINDOWS ASYNC COMPATIBILITY FIX ---
if os.name == 'nt':
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", DeprecationWarning)
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

from backend.app.core.risk_committee import risk_committee
from backend.app.services.redis_service import redis_service
from backend.app.services.options_desk_service import options_desk_service
from backend.app.services.execution_service import execution_service
from backend.app.services.database_service import database_service
from backend.app.db.database import engine as db_engine
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import sessionmaker

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("RiskCommittee")

# Configure shared file logging
log_formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
file_handler = logging.FileHandler("logs/trader_singh.log")
file_handler.setFormatter(log_formatter)
logging.getLogger().addHandler(file_handler)

from backend.app.services.database_service import database_service

async def main():
    print("🚀 [STARTUP] Institutional Risk Committee Service...")

    async_session = sessionmaker(
        db_engine(), class_=AsyncSession, expire_on_commit=False
    )
    
    print("   ↳ Subscribing to quant_signals...")
    pubsub = await redis_service.subscribe("quant_signals")
    print("   ↳ System READY. Awaiting signals...")

    async for message in pubsub.listen():
        if message["type"] == "message":
            try:
                # --- V8 GATE: Check Autopilot Status ---
                status = await redis_service.get_json("autopilot_status")
                if not status or status.get("status") != "ACTIVE":
                    logger.warning("Risk Committee received signal but System is NOT ACTIVE. Skipping...")
                    continue

                passed_assets = json.loads(message["data"])
                if not passed_assets:
                    continue

                logger.info(f"Received {len(passed_assets)} assets from Quant Engine. Processing...")

                # Fetch strategy mode from Redis
                strategy_mode = await redis_service.get("execution_strategy_mode") or "CREDIT_SPREAD"

                # 1. Build Options Spreads
                structured_spreads = options_desk_service.process_approved_assets(passed_assets, strategy_mode=strategy_mode)

                from trading_mode import ladder_enabled
                is_ladder = ladder_enabled()

                async with async_session() as session:
                    for spread in structured_spreads:
                        ticker = spread.get("ticker", "UNKNOWN")
                        bias = spread.get("bias", "NEUTRAL")

                        # --- 🪜 PHASE 7: income-ladder gate (validated e1bbdc4) ---
                        # cadence + max-open + evaluate_ladder side/IVR sizing + chain
                        # refine, all in the shared source-of-truth module. The ladder
                        # NEVER runs the sniper's directional gauntlet.
                        if is_ladder:
                            from backend.app.services.ladder_entry import apply_ladder_gate
                            proceed_gate, gate_reason = await apply_ladder_gate(session, spread)
                            if not proceed_gate:
                                logger.info(f"🪜 [Ladder] {ticker} blocked: {gate_reason}")
                                continue

                        # --- V8 UPGRADE: PARALLEL EXECUTION PIPELINE ---
                        import time
                        start_time = time.perf_counter()

                        # Evaluate trade using AI Committee
                        verdict, reasoning = await risk_committee.evaluate_trade(spread)

                        elapsed = time.perf_counter() - start_time
                        logger.info(f"⏱️ Committee Evaluation for {ticker} completed in {elapsed:.2f}s")

                        # 3. Log Audit
                        audit_record = {
                            "ticker": ticker,
                            "pa_status": spread["learning_context"].get("PA_Status", "UNKNOWN"),
                            "pcr": Decimal(str(spread["coi_pcr"])),
                            "gex_mn": Decimal("1450.00"),
                            "ml_score": Decimal(str(spread["ml_score"])),
                            "committee_verdict": verdict,
                            "committee_reasoning": reasoning
                        }
                        audit = await database_service.add_signal_audit(session, audit_record)

                        # For the ladder the committee is ADVISORY (the quantitative
                        # ladder gate already approved the entry); COMMITTEE_MODE=blocking
                        # restores its veto. The sniper path stays strictly gated on APPROVED.
                        committee_mode = os.getenv("COMMITTEE_MODE", "advisory").lower()
                        proceed = (verdict == "APPROVED") or (is_ladder and committee_mode != "blocking")
                        if proceed and verdict != "APPROVED":
                            logger.info(f"⚖️ Committee said {verdict} (advisory mode) — proceeding on the ladder gate")

                        if proceed:
                            logger.info(f"Trade APPROVED for {ticker}. Executing...")
                            if audit is not None and getattr(audit, "id", None) is not None:
                                spread.setdefault("learning_context", {})["signal_audit_id"] = audit.id
                            spread["execution_algo"] = "ICEBERG" if spread["lots_sized"] > 2 else "MARKET"
                            success = await execution_service.execute_trade(session, spread)
                            if success:
                                logger.info(f"Execution success for {ticker}")
                                if is_ladder:
                                    from backend.app.services.ladder_entry import mark_ladder_entered
                                    await mark_ladder_entered()
            
            except Exception as e:
                logger.error(f"Error in Risk Committee processing: {e}")

if __name__ == "__main__":
    if os.name == 'nt':
        # Policy set above
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        loop.run_until_complete(main())
    else:
        asyncio.run(main())
