import asyncio
import os
import sys
import selectors

# --- WINDOWS ASYNC COMPATIBILITY FIX ---
if os.name == 'nt':
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

from dotenv import load_dotenv
from sqlalchemy.ext.asyncio import create_async_engine
from sqlmodel import SQLModel

load_dotenv()

async def test_db():
    print("Testing Async SQLModel Connection...")
    DB_USER = os.getenv("DB_USER", "trader")
    DB_PASSWORD = os.getenv("DB_PASSWORD", "institutional_grade_password")
    DB_PORT = os.getenv("DB_PORT", "5432")
    DB_NAME = os.getenv("DB_NAME", "agentic_trader")

    DB_HOST = os.getenv("DB_HOST", "172.26.128.109")

    DATABASE_URL = f"postgresql+psycopg://{DB_USER}:{DB_PASSWORD}@{DB_HOST}:{DB_PORT}/{DB_NAME}"
    print(f"URL: postgresql+psycopg://{DB_USER}:***@{DB_HOST}:{DB_PORT}/{DB_NAME}")
    
    try:
        engine = create_async_engine(DATABASE_URL, echo=False)
        async with engine.begin() as conn:
            # Check table existence
            await conn.run_sync(SQLModel.metadata.create_all)
        print("PostgreSQL Connected and Tables Verified successfully!")
        
        # Test ML Engine
        print("\nTesting ML Approval Engine...")
        from ml_approval_engine import MLApprovalEngine
        engine = MLApprovalEngine(ticker="NIFTY", timeframe=1)
        score = engine.get_approval_score()
        print(f"ML Engine initialized and scored successfully: {score}")
        
        return True
    except Exception as e:
        print(f"Verification failed: {e}")
        return False

if __name__ == "__main__":
    if os.name == 'nt':
        selector = selectors.SelectSelector()
        loop = asyncio.SelectorEventLoop(selector)
        asyncio.set_event_loop(loop)
    else:
        loop = asyncio.get_event_loop()
        
    success = loop.run_until_complete(test_db())
    sys.exit(0 if success else 1)
