import asyncio
import datetime
import os
import sys
sys.path.append(os.getcwd())
from sqlalchemy import select
from backend.app.db.database import engine, sessionmaker
from backend.app.db.models import MarketIndicator
from sqlalchemy.ext.asyncio import AsyncSession
import warnings

warnings.simplefilter('ignore')
asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

async def main():
    session_factory = sessionmaker(engine(), class_=AsyncSession)
    async with session_factory() as session:
        # Fetch indicators for today
        res = await session.execute(
            select(MarketIndicator)
            .where(MarketIndicator.timestamp >= datetime.datetime(2026, 6, 23))
            .order_by(MarketIndicator.timestamp.desc())
        )
        indicators = res.scalars().all()
        print(f"--- Market Indicators Today (Count: {len(indicators)}) ---")
        for i in indicators[:30]:
            print(f"Time: {i.timestamp} | Price: {i.price} | PCR: {i.pcr} | GEX: {i.total_gex}")
            
if __name__ == "__main__":
    asyncio.run(main())
