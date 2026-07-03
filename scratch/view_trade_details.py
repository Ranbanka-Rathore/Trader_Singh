import os
import sys
import asyncio
from sqlalchemy import select

# Add project root to python path
sys.path.append(os.getcwd())

if os.name == 'nt':
    import warnings
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", DeprecationWarning)
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

from backend.app.db.database import sessionmaker, engine
from backend.app.db.models import Trade

async def main():
    from sqlalchemy.orm import sessionmaker as sa_sessionmaker
    from sqlalchemy.ext.asyncio import AsyncSession
    
    session_factory = sa_sessionmaker(engine(), class_=AsyncSession, expire_on_commit=False)
    async with session_factory() as session:
        res = await session.execute(select(Trade).where(Trade.id == 1))
        t = res.scalar()
        if t:
            print("--- Trade Details ---")
            for k, v in t.__dict__.items():
                if not k.startswith('_'):
                    print(f"{k}: {v}")
        else:
            print("Trade not found!")

if __name__ == "__main__":
    asyncio.run(main())
