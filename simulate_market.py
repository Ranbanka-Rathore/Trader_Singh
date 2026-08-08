import asyncio
import json
import random
import time
from datetime import datetime
import redis.asyncio as redis
import os
import selectors
from dotenv import load_dotenv

load_dotenv()
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker
from sqlalchemy import text
from sqlmodel import SQLModel
from backend.app.db.models import SignalAudit, Trade, OpenPosition, MarketIndicator # Import models to register them with SQLModel

from backend.app.core.logging_setup import setup_logging

# Own file so simulated runs never interleave with a live service's log.
logger = setup_logging("MarketSimulator", "simulate_market.log")

DATABASE_URL = "postgresql+psycopg://trader:institutional_grade_password@localhost:5432/agentic_trader"

async def simulate():
    logger.info("🚀 Starting TRADER SINGH Market Simulator + Signal Generator...")
    
    # DB Connection Retry Loop
    r = None
    for i in range(5):
        try:
            # Detect host
            host = os.getenv("REDIS_HOST", "localhost")
            r = redis.Redis(host=host, port=6379, decode_responses=True)
            await r.ping()
            logger.info(f"   ✅ Redis Connected to {host} (Simulator)")
            break
        except Exception:
            logger.warning(f"   ⏳ Redis not ready (Attempt {i+1}/5)...")
            await asyncio.sleep(3)
    
    if not r:
        logger.error("❌ Could not connect to Redis. Simulator exiting.")
        return

    # DB Setup for signals
    db_host = os.getenv("DB_HOST", "localhost")
    db_url = DATABASE_URL.replace("localhost", db_host)
    engine = create_async_engine(db_url)
    async_session = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    tickers = {
        "NIFTY": 24150.0,
    }
    
    counter = 0
    while True:
        try:
            for ticker, base_price in tickers.items():
                # Random walk
                change = random.uniform(-2, 2)
                tickers[ticker] += change
                price = round(tickers[ticker], 2)
                
                timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                tick_data = {"t": timestamp, "p": price, "v": random.randint(100, 1000)}
                
                # 1. Push to Redis list (for initial load)
                await r.lpush(f"ticks:{ticker}", json.dumps(tick_data))
                await r.ltrim(f"ticks:{ticker}", 0, 999)
                
                # 2. Publish live tick (for WebSocket)
                payload = {
                    "type": "TICK",
                    "ticker": ticker,
                    "price": price,
                    "volume": tick_data["v"],
                    "timestamp": timestamp,
                    "source": "SIMULATED"
                }
                await r.publish("market_updates", json.dumps(payload))
                
                # 3. Update snapshot (merge with existing to preserve PCR and Gamma levels)
                existing = await r.get(f"market_snapshot:{ticker}")
                if existing:
                    try:
                        snap_data = json.loads(existing)
                        snap_data["price"] = price
                        snap_data["timestamp"] = timestamp
                        snap_data["source"] = "SIMULATED"
                        await r.set(f"market_snapshot:{ticker}", json.dumps(snap_data))
                    except Exception:
                        await r.set(f"market_snapshot:{ticker}", json.dumps(payload))
                else:
                    # Provide defaults so visual dashboard elements never disappear
                    payload.update({
                        "pcr": 1.0,
                        "coi_pcr": 1.0,
                        "bias": "NEUTRAL",
                        "gamma_flip": round(price * 0.995, 2),
                        "call_wall": round(price * 1.01, 2),
                        "put_wall": round(price * 0.99, 2)
                    })
                    await r.set(f"market_snapshot:{ticker}", json.dumps(payload))
        except Exception as e:
            logger.error(f"Simulation Error: {e}")

        counter += 1
        await asyncio.sleep(1)

if __name__ == "__main__":
    if os.name == 'nt':
        selector = selectors.SelectSelector()
        loop = asyncio.SelectorEventLoop(selector)
        asyncio.set_event_loop(loop)
    else:
        loop = asyncio.get_event_loop()
        
    loop.run_until_complete(simulate())
