import os
import sys
import asyncio
import warnings

# Add project root to python path
sys.path.append(os.getcwd())

if os.name == 'nt':
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", DeprecationWarning)
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

from backend.app.db.database import init_db, DATABASE_URL, DB_HOST

async def main():
    print(f"DATABASE_URL resolved to: {DATABASE_URL}")
    print(f"DB_HOST resolved to: {DB_HOST}")
    print("Testing connection and initializing tables...")
    success = await init_db()
    if success:
        print("✅ Connection successful and tables verified!")
    else:
        print("❌ Connection failed!")

if __name__ == "__main__":
    asyncio.run(main())
