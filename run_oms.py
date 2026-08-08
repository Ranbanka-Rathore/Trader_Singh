import asyncio
import os
import selectors
from datetime import datetime
from backend.app.core.logging_setup import setup_logging
from backend.app.services.execution_service import execution_service
from backend.app.services.redis_service import redis_service
from backend.app.db.database import engine as db_engine
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import sessionmaker

# The OMS closes positions and books P&L, so its cycle errors are the ones most
# worth keeping. Until now they went only to a console window and died with it.
logger = setup_logging("OMS", "oms.log")

# --- WINDOWS ASYNC COMPATIBILITY FIX ---
if os.name == 'nt':
    import warnings
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", DeprecationWarning)
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

async def main():
    logger.info("Starting Institutional Execution Service (OMS)...")
    
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

            async with async_session() as session:
                # Evaluate Open Positions & Dynamic Trailing Stop Levels
                closed_trades = await execution_service.evaluate_open_positions(session)
                if closed_trades:
                    logger.info(f"Closed {len(closed_trades)} open positions.")
                    await redis_service.publish("market_updates", {
                        "type": "TRADES_CLOSED",
                        "timestamp": datetime.now().isoformat(),
                        "data": closed_trades
                    })
            
            await asyncio.sleep(5) # Fast evaluation every 5s
        except Exception as e:
            logger.error(f"Error in OMS cycle: {e}", exc_info=True)
            await asyncio.sleep(5)

if __name__ == "__main__":
    if os.name == 'nt':
        # Policy set above
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        loop.run_until_complete(main())
    else:
        asyncio.run(main())
