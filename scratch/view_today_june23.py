import asyncio
import datetime
import os
import sys
sys.path.append(os.getcwd())
sys.stdout.reconfigure(encoding='utf-8')
from sqlalchemy import select
from backend.app.db.database import engine, sessionmaker
from backend.app.db.models import Trade, OpenPosition
from sqlalchemy.ext.asyncio import AsyncSession
import warnings

warnings.simplefilter('ignore')
asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

async def main():
    session_factory = sessionmaker(engine(), class_=AsyncSession)
    async with session_factory() as session:
        # Completed trades
        res = await session.execute(select(Trade).where(Trade.entry_date >= datetime.datetime(2026, 6, 23)))
        trades = res.scalars().all()
        print(f"--- Completed Trades (Count: {len(trades)}) ---")
        for t in trades:
            print(f"Trade: Ticker={t.ticker}, Strategy={t.strategy_type}, Spot={t.spot_price}, "
                  f"Sell={t.leg_1_sell}, Buy={t.leg_2_buy}, Lots={t.lots_sized}, PnL={t.realized_pnl}, "
                  f"Reason={t.exit_reason}, Entry={t.entry_date}, Exit={t.exit_date}")
        
        # Open positions
        res_open = await session.execute(select(OpenPosition))
        open_pos = res_open.scalars().all()
        print(f"\n--- Open Positions (Count: {len(open_pos)}) ---")
        for o in open_pos:
            print(f"Open: Ticker={o.ticker}, Strategy={o.strategy_type}, Spot={o.spot_price}, "
                  f"Sell={o.leg_1_sell}, Buy={o.leg_2_buy}, Lots={o.lots_sized}, Entry={o.entry_date}")

if __name__ == "__main__":
    asyncio.run(main())
