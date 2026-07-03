import asyncio
import json
import logging
import os
from datetime import datetime
from typing import List, Dict, Any, Tuple
from dhanhq import DhanFeed
from backend.app.services.redis_service import redis_service
from backend.app.services.data_service import data_service

logger = logging.getLogger("MarketDataService")

class MarketDataService:
    def __init__(self):
        self.client_id = os.getenv("DHAN_CLIENT_ID")
        self.access_token = os.getenv("DHAN_ACCESS_TOKEN")
        self.feed = None
        self.is_running = False
        self.task = None
        
        # Request modes for v2: 15 (Ticker), 17 (Quote), 21 (Full)
        self.instruments = [
            (0, "13", 17), # NIFTY 50 - Quote
        ]
        
        self.ticker_map = {
            "13": "NIFTY", # Standardized for Charting
        }

    def add_instrument(self, segment: int, security_id: str, ticker: str, request_type: int = 21):
        if (segment, security_id, request_type) not in self.instruments:
            self.instruments.append((segment, security_id, request_type))
            self.ticker_map[security_id] = ticker

    async def start(self):
        """Starts the WebSocket feed as an async task."""
        if self.is_running:
            return
        
        self.is_running = True
        self.task = asyncio.create_task(self._run_feed())
        logger.info("Market Data Service Started.")

    async def _run_feed(self):
        while self.is_running:
            try:
                logger.info(f"Connecting to Dhan Feed v2 with {len(self.instruments)} instruments...")
                self.feed = DhanFeed(
                    self.client_id,
                    self.access_token,
                    self.instruments,
                    version='v2'
                )
                
                await self.feed.connect()
                logger.info("Connected to Dhan WebSocket Feed.")
                
                while self.is_running:
                    # DhanFeed.get_instrument_data() is an async call that waits for a message
                    data = await self.feed.get_instrument_data()
                    if data:
                        await self._process_tick(data)
                    else:
                        # Small sleep if no data returned (unlikely for recv() but safe)
                        await asyncio.sleep(0.001)
                        
            except Exception as e:
                logger.error(f"WebSocket Feed Error: {e}")
                if self.is_running:
                    logger.info("Reconnecting in 5 seconds...")
                    await asyncio.sleep(5)

    async def _process_tick(self, data: Dict[str, Any]):
        """Publishes the tick to Redis and updates snapshots."""
        try:
            sec_id = str(data.get("security_id"))
            ticker = self.ticker_map.get(sec_id, sec_id)
            
            # LTP is returned as a string formatted to 2 decimal places in process_full/process_ticker
            price = float(data.get("LTP", 0))
            
            payload = {
                "type": "TICK",
                "ticker": ticker,
                "price": price,
                "oi": data.get("OI", 0),
                "timestamp": datetime.now().isoformat(),
                "source": "DHAN_LIVE" # Add source tag to differentiate
            }
            
            # 1. Publish to Redis channel for live WebSocket broadcast
            await redis_service.publish("market_updates", payload)
            logger.info(f"LIVE TICK: {ticker} @ {price}")
            
            # 2. Update the latest snapshot in Redis (merge to preserve PCR and Gamma levels)
            existing = await redis_service.get_json(f"market_snapshot:{ticker}")
            if existing and isinstance(existing, dict):
                existing.update({
                    "price": price,
                    "oi": data.get("OI", 0),
                    "timestamp": payload["timestamp"],
                    "source": "DHAN_LIVE"
                })
                await redis_service.set_json(f"market_snapshot:{ticker}", existing)
            else:
                await redis_service.set_json(f"market_snapshot:{ticker}", payload)

            # 3. Ingest tick into historical buffer (Redis List)
            await data_service.ingest_tick(ticker, price)
            
        except Exception as e:
            logger.error(f"Error processing tick: {e}")

    async def stop(self):
        self.is_running = False
        if self.task:
            self.task.cancel()
        if self.feed:
            try:
                # Disconnect is async in marketfeed.py
                await self.feed.disconnect()
            except:
                pass
        logger.info("🛑 Market Data Service Stopped.")

# Singleton instance
market_data_service = MarketDataService()
