import os
import sys
import datetime
import asyncio
from sqlalchemy import select
from backend.app.db.database import init_db, get_session
from backend.app.db.models import Trade, OpenPosition

sys.stdout.reconfigure(encoding='utf-8')

async def main():
    import time
    for attempt in range(10):
        try:
            await init_db()
            break
        except Exception as e:
            print(f"⚠️ DB is starting up. Retrying... (Attempt {attempt+1}/10)")
            time.sleep(2)
            
    async for session in get_session():
        target_date = datetime.date.today()
        print(f"📡 Querying Trades and Open Positions for {target_date}...")
        
        # Query completed trades
        trade_query = select(Trade).order_by(Trade.entry_date.asc())
        res = await session.execute(trade_query)
        all_trades = res.scalars().all()
        target_trades = [t for t in all_trades if t.entry_date.date() == target_date]
        
        if not target_trades:
            print("❌ No completed trades found for today.")
        else:
            print(f"\n--- Completed Trades ({len(target_trades)}) ---")
            for t in target_trades:
                print(f"ID: {t.id} | Ticker: {t.ticker} | Strategy: {t.strategy_type} | Spot: {t.spot_price} | Entry: {t.entry_date} | Exit: {t.exit_date} | Reason: {t.exit_reason} | PnL: ₹{t.realized_pnl} | Mode: {t.mode}")
                
        # Query open positions
        open_query = select(OpenPosition).order_by(OpenPosition.entry_date.asc())
        res_open = await session.execute(open_query)
        all_open = res_open.scalars().all()
        target_open = [o for o in all_open if o.entry_date.date() == target_date]
        
        if not target_open:
            print("❌ No open positions found for today.")
        else:
            print(f"\n--- Open Positions ({len(target_open)}) ---")
            for o in target_open:
                print(f"ID: {o.id} | Ticker: {o.ticker} | Strategy: {o.strategy_type} | Spot: {o.spot_price} | Entry: {o.entry_date} | Mode: {o.mode}")
        break

if __name__ == "__main__":
    if os.name == 'nt':
        import warnings
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", DeprecationWarning)
            asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    asyncio.run(main())
