import os
import sys

# Add parent dir to path
sys.path.append(os.getcwd())

import asyncio
import datetime
from sqlalchemy import select
from backend.app.db.database import engine, sessionmaker
from sqlalchemy.ext.asyncio import AsyncSession
from backend.app.db.models import Trade, OpenPosition

if os.name == 'nt':
    import warnings
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", DeprecationWarning)
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

async def main():
    print("📡 Querying trade executions for today (June 22, 2026)...")
    async_session = sessionmaker(engine(), class_=AsyncSession, expire_on_commit=False)
    
    async with async_session() as session:
        # Query completed trades
        query = select(Trade).where(Trade.entry_date >= datetime.datetime(2026, 6, 22)).order_by(Trade.entry_date.asc())
        res = await session.execute(query)
        trades = res.scalars().all()
        
        if not trades:
            print("❌ No completed trades found for today.")
        else:
            print("\n--- Completed Trades Today ---")
            for t in trades:
                print(f"ID: {t.id} | Ticker: {t.ticker} | Strategy: {t.strategy_type} | Spot: {t.spot_price} | Strikes: ({t.leg_1_sell}, {t.leg_2_buy}) | Lots: {t.lots_sized} | Entry: {t.entry_date} | Exit: {t.exit_date} | Reason: {t.exit_reason} | PnL: {t.realized_pnl} | Mode: {t.mode}")
                
        # Query open positions
        query_open = select(OpenPosition)
        res_open = await session.execute(query_open)
        open_pos = res_open.scalars().all()
        
        if not open_pos:
            print("❌ No open positions found.")
        else:
            print("\n--- Active Open Positions ---")
            for o in open_pos:
                print(f"ID: {o.id} | Ticker: {o.ticker} | Strategy: {o.strategy_type} | Spot: {o.spot_price} | Strikes: ({o.leg_1_sell}, {o.leg_2_buy}) | Lots: {o.lots_sized} | Entry: {o.entry_date} | Mode: {o.mode}")

if __name__ == "__main__":
    asyncio.run(main())
