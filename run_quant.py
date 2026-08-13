import asyncio
import os
import selectors
import warnings
from datetime import datetime

# --- WINDOWS ASYNC COMPATIBILITY FIX ---
if os.name == 'nt':
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", DeprecationWarning)
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

from backend.app.core.quant_engine import QuantEngine
from backend.app.services.redis_service import redis_service
from backend.app.db.database import engine as db_engine
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import sessionmaker

from backend.app.core.logging_setup import setup_logging

logger = setup_logging("QuantEngine", "quant.log")

async def main():
    print("🚀 [STARTUP] Institutional Quant Engine...")
    
    print("   ↳ Initializing QuantEngine instance...")
    engine = QuantEngine()
    print("   ↳ QuantEngine initialized successfully.")
    
    universe = ["NIFTY"]
    
    async_session = sessionmaker(
        db_engine(), class_=AsyncSession, expire_on_commit=False
    )
    
    while True:
        try:
            # --- V8 GATE: Check Autopilot Status ---
            status = await redis_service.get_json("autopilot_status")
            if not status or status.get("status") != "ACTIVE":
                await asyncio.sleep(5)
                continue

            # 🪜 PHASE 7: in LADDER_MODE the income ladder runs on cadence, not on
            # the sniper's directional scan. Source the NIFTY candidate straight from
            # the live snapshot so it is gated ONLY by evaluate_ladder + validated hard
            # gates downstream (in run_risk_committee), never by analyze_universe's
            # directional gauntlet. See backend/app/services/ladder_entry.py.
            from trading_mode import ladder_enabled
            if ladder_enabled():
                from backend.app.services.ladder_entry import source_ladder_candidate
                passed = await source_ladder_candidate()
            else:
                # Surgical Session Management: analyze_universe now manages its own sessions
                passed = await engine.analyze_universe(universe)

            if passed:
                logger.info(f"Found {len(passed)} assets passing scan. Publishing to Redis...")
                await redis_service.publish("quant_signals", passed)
            
            await asyncio.sleep(10) # Scan every 10s (V8 Speed Optimization)
        except Exception as e:
            logger.error(f"Error in Quant Engine cycle: {e}")
            await asyncio.sleep(10)

if __name__ == "__main__":
    if os.name == 'nt':
        # Policy is already set above, but we still use SelectorEventLoop for safety
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        loop.run_until_complete(main())
    else:
        asyncio.run(main())
