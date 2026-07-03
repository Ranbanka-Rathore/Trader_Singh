import asyncio
import logging
import os
import selectors
from sqlalchemy import text
from backend.app.db.database import engine as db_engine

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("DBReset")

async def main():
    print("\n" + "="*50)
    print("   TRADER SINGH - DATABASE PURGE (STALE DATA)")
    print("="*50)
    
    eng = db_engine()
    async with eng.begin() as conn:
        print("Clearing signal_audit table...")
        await conn.execute(text("TRUNCATE TABLE signal_audit RESTART IDENTITY CASCADE"))
        
        print("Clearing trades table...")
        await conn.execute(text("TRUNCATE TABLE trades RESTART IDENTITY CASCADE"))
        
        print("Clearing open_positions table...")
        await conn.execute(text("TRUNCATE TABLE open_positions RESTART IDENTITY CASCADE"))
        
        print("Clearing candle data (1m/5m)...")
        # Optional: Uncomment if you want to clear candles too, but we just backfilled them
        # await conn.execute(text("TRUNCATE TABLE candle RESTART IDENTITY CASCADE"))

    print("\nDATABASE PURGE COMPLETE.")
    print("="*50 + "\n")

if __name__ == "__main__":
    if os.name == 'nt':
        selector = selectors.SelectSelector()
        loop = asyncio.SelectorEventLoop(selector)
        asyncio.set_event_loop(loop)
        loop.run_until_complete(main())
    else:
        asyncio.run(main())
