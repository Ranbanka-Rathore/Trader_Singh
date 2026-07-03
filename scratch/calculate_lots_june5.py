import os
import sys
import asyncio
import datetime
import pandas as pd
import numpy as np

# Add parent dir to path
sys.path.append(os.getcwd())

from ml_approval_engine import MLApprovalEngine
from backend.app.db.database import engine as db_engine
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import sessionmaker
from sqlalchemy import text

if os.name == 'nt':
    import warnings
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", DeprecationWarning)
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

async def main():
    print("📡 Connecting to DB...")
    async_session = sessionmaker(db_engine(), class_=AsyncSession, expire_on_commit=False)
    
    # Instantiate ML engine
    ticker = "NIFTY"
    engine = MLApprovalEngine(ticker=ticker, timeframe=1)
    
    if not engine.model_data:
        print("❌ No model loaded! Lot sizing would default to 1 lot.")
        return
        
    print(f"Model keys: {engine.model_data.keys() if isinstance(engine.model_data, dict) else 'Not a dict'}")

    async with async_session() as session:
        # Check what indicators we have around 09:15 AM on June 5
        query = text("""
            SELECT timestamp, price, vwap, pcr, total_gex FROM market_indicators
            WHERE ticker = 'NIFTY' AND timeframe = 1 
              AND timestamp >= '2026-06-05 09:00:00'
              AND timestamp <= '2026-06-05 09:30:00'
            ORDER BY timestamp ASC
        """)
        res = await session.execute(query)
        rows = res.fetchall()
        
        print("\n--- Indicators around 09:15 AM IST ---")
        if not rows:
            print("❌ No market indicators found in this range!")
        else:
            for r in rows:
                print(f"Time: {r[0]} | Price: {r[1]:.2f} | VWAP: {r[2]:.2f} | PCR: {r[3]:.2f} | GEX: {r[4]:.2f}")
                
        # Now let's calculate the ML score at 09:15 AM
        # Note: The database timestamp might be UTC or local. Let's see what timestamps exist in the DB.
        # Let's get the score using 09:15:00 as of timestamp
        as_of = datetime.datetime(2026, 6, 5, 9, 15, 0)
        
        # We need to see if the database timestamps are offset or local.
        # Let's run get_approval_score_async with as_of
        raw_prob = await engine.get_approval_score_async(session, as_of_timestamp=as_of)
        
        # Since it is a BEARISH trade, ml_score = 1.0 - raw_prob
        ml_score = 1.0 - raw_prob
        
        print(f"\nAt 09:15 AM IST on June 5:")
        print(f"Raw Prob (Bullish Probability): {raw_prob:.4f}")
        print(f"Bearish ML Score (1.0 - raw_prob): {ml_score:.4f}")
        
        # Sizing logic
        ml_cutoff = 0.65
        is_approved = ml_score >= ml_cutoff
        
        base_lots = 5 if ml_score > 0.90 else 3 if ml_score > 0.80 else 2 if ml_score > 0.70 else 1 if is_approved else 0
        
        print(f"ML Approved (Cutoff {ml_cutoff}): {is_approved}")
        print(f"Sized Lots: {base_lots} lots")

if __name__ == "__main__":
    asyncio.run(main())
