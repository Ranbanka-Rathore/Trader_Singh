import asyncio
import logging
import os
import threading
from dhanhq import DhanFeed
from database_manager import db_manager, MarketIndicator

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger("DhanWebSocket")

class DhanWebSocketManager:
    def __init__(self, client_id, access_token):
        self.client_id = client_id
        self.access_token = access_token
        self.feed = None
        self.instruments = []
        self.is_running = False
        self._thread = None
        self._loop = None
        
        # Data storage for latest ticks (memory cache)
        self.latest_ticks = {}

    def add_instrument(self, exchange_segment, security_id):
        """Add an instrument to the subscription list.
        Exchange segments:
        0: IDX_I (Indices)
        1: NSE_EQ (Equity)
        2: NSE_FNO (F&O)
        """
        # (exchange_segment, security_id, request_type)
        # request_type: 15 (Ticker), 17 (Quote), 21 (Full)
        self.instruments.append((exchange_segment, str(security_id), 21))

    def start(self):
        """Starts the WebSocket in a background thread."""
        if self.is_running:
            return
        
        self.is_running = True
        self._thread = threading.Thread(target=self._run_loop, daemon=True)
        self._thread.start()
        logger.info("WebSocket Manager Thread started.")

    def _run_loop(self):
        """Internal method to run the asyncio loop."""
        self._loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._loop)
        
        self.feed = DhanFeed(
            self.client_id, 
            self.access_token, 
            self.instruments, 
            version='v2'
        )
        
        # Override the process_data or use a custom loop to handle ticks
        # The dhanhq library doesn't seem to have a clean callback for v2 yet in the source
        # but let's try to implement a receiving loop
        
        self._loop.run_until_complete(self._connect_and_listen())

    async def _connect_and_listen(self):
        """Asynchronous connection and message processing."""
        try:
            await self.feed.connect()
            logger.info("Connected to Dhan WebSocket.")
            
            while self.is_running:
                data = await self.feed.get_instrument_data()
                if data:
                    self._handle_tick(data)
                    
        except Exception as e:
            logger.error(f"WebSocket Connection Error: {e}")
            self.is_running = False

    def _handle_tick(self, data):
        """Processes incoming data packets."""
        try:
            sec_id = data.get("security_id")
            if not sec_id:
                return

            self.latest_ticks[str(sec_id)] = data
            
            # If it's Full Data or OI Data, we might want to update the DB or state
            if data.get("type") in ["Full Data", "OI Data", "Quote Data"]:
                # Log or update internal state
                pass

        except Exception as e:
            logger.error(f"Error handling tick: {e}")

    def get_latest_price(self, security_id):
        """Thread-safe way to get the latest price from the cache."""
        tick = self.latest_ticks.get(str(security_id))
        if tick:
            return float(tick.get("LTP", 0))
        return None

    def get_latest_oi(self, security_id):
        """Thread-safe way to get the latest OI from the cache."""
        tick = self.latest_ticks.get(str(security_id))
        if tick:
            return tick.get("OI", 0)
        return None

    def stop(self):
        """Stops the WebSocket connection."""
        self.is_running = False
        if self._loop:
            self._loop.stop()
        logger.info("WebSocket Manager stopped.")

# Example Usage
if __name__ == "__main__":
    from dotenv import load_dotenv
    load_dotenv()
    
    cid = os.getenv("DHAN_CLIENT_ID")
    token = os.getenv("DHAN_ACCESS_TOKEN")
    
    ws_manager = DhanWebSocketManager(cid, token)
    # NIFTY 50 Index (Exchange Segment 0, Security ID 13)
    ws_manager.add_instrument(0, "13") 
    
    ws_manager.start()
    
    try:
        import time
        for _ in range(10):
            price = ws_manager.get_latest_price("13")
            oi = ws_manager.get_latest_oi("13")
            print(f"NIFTY 50 -> Price: {price}, OI: {oi}")
            time.sleep(2)
    except KeyboardInterrupt:
        ws_manager.stop()
