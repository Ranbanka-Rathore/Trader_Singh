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

from backend.app.services.market_data_service import market_data_service
from backend.app.services.data_service import data_service
from backend.app.services.redis_service import redis_service

from backend.app.core.logging_setup import setup_logging

# This service logs a line per tick — roughly 55MB/hour, ~350MB over a session.
# 50MB x 8 caps it at 400MB while still retaining a full trading day.
logger = setup_logging(
    "DataHarvester", "harvester.log",
    max_bytes=50 * 1024 * 1024, backup_count=8,
)

async def main():
    logger.info("Starting Institutional Data Harvester...")

    # --- V8 UPGRADE: HISTORICAL BACKFILL ON STARTUP ---
    # This ensures QuantEngine has structural context (POC, PDH/L) immediately
    core_universe = ["NIFTY"]
    for ticker in core_universe:
        logger.info(f"Checking historical data for {ticker}...")
        await data_service.backfill_historical_data(ticker, days=5)

    # Start Dhan WebSocket Feed
    await market_data_service.start()
    while True:
        try:
            # 1. Run Ingestion (GEX/POC/PCR calculation)
            await data_service.run_autotrender_cycle()
            
            # Update status in Redis
            await redis_service.set_json("harvester_status", {
                "last_run": datetime.now().isoformat(),
                "status": "ACTIVE"
            }, expire=60)
            
            await asyncio.sleep(10) # Cycle every 10s
        except Exception as e:
            logger.error(f"Error in Harvester cycle: {e}")
            await asyncio.sleep(5)

if __name__ == "__main__":
    if os.name == 'nt':
        # Policy set above
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        loop.run_until_complete(main())
    else:
        asyncio.run(main())
