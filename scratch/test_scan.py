import asyncio
import sys
sys.path.append('c:/Users/ST/Desktop/Agentic_Trader')
from backend.app.core.quant_engine import QuantEngine
from backend.app.db.database import engine as db_engine
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import sessionmaker

async def main():
    print("Initializing QuantEngine...")
    qe = QuantEngine()
    universe = ["NIFTY", "BANKNIFTY", "RELIANCE"]
    
    print("\nRunning live scan cycle...")
    passed = await qe.analyze_universe(universe)
    print(f"Scan finished. Passed assets: {passed}")
    
    print("\nVerifying memory levels in QuantEngine:")
    for ticker in universe:
        mem = qe._htf_memory.get(ticker)
        if mem:
            print(f"--- {ticker} ---")
            print(f"  PDH: {mem.get('pdh')}")
            print(f"  PDL: {mem.get('pdl')}")
            print(f"  Live Session POC: {mem.get('session_poc')}")
            print(f"  5-day HTF POC: {mem.get('poc')}")
        else:
            print(f"No memory found for {ticker}")

if __name__ == "__main__":
    import os
    import warnings
    if os.name == 'nt':
        import selectors
        selector = selectors.SelectSelector()
        loop = asyncio.SelectorEventLoop(selector)
        asyncio.set_event_loop(loop)
    else:
        loop = asyncio.get_event_loop()
        
    loop.run_until_complete(main())
