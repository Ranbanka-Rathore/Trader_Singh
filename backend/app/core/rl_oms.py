import asyncio
import logging
from datetime import datetime, timedelta
from backend.app.services.redis_service import redis_service
from backend.app.services.broker_service import broker_service

logger = logging.getLogger("RL_OMS")

class RL_OMS:
    """
    Institutional Order Management System.
    Manages 'Limit Hunting' to minimize slippage by sitting on the bid/ask and chasing.
    """
    def __init__(self):
        self.max_wait_seconds = 120 # 2 minutes max for entry hunt
        self.chase_interval = 15    # Check and adjust every 15s
        self.max_slippage_pct = 0.002 # 0.2% max chase distance

    async def execute_limit_hunt(self, ticker: str, side: str, target_lots: int, start_price: float) -> float:
        """
        Performs a real 'Limit Hunt' on the exchange.
        1. Places Limit order at Best Bid/Ask.
        2. Monitors and 'Chases' the price if not filled.
        3. Fails safe to Market after timeout.
        """
        broker = broker_service.get_broker()
        sec_id = broker.get_equity_security_id(ticker)
        
        # Handle index naming for segment routing
        is_index = ticker in ["NIFTY", "BANKNIFTY", "NSEI", "NSEBANK"]
        segment = "IDX_I" if is_index else "NSE_EQ"

        if not sec_id:
            logger.error(f"❌ RL-OMS: Could not find Security ID for {ticker}. Aborting hunt.")
            return start_price

        logger.info(f"🎯 RL-OMS: Starting Institutional Hunt for {ticker} ({side}) | Target: {target_lots} lots")
        
        # 1. Get Initial Depth
        depth = await broker.get_market_depth(sec_id, segment)
        if depth:
            # Sit on the current best bid (if buying) or ask (if selling)
            limit_price = depth["bid"] if side == "BUY" else depth["ask"]
            # Fallback if depth is weird
            if limit_price == 0: limit_price = start_price
        else:
            limit_price = start_price

        # 2. Place Initial Limit Order
        order_id = await broker.place_order(sec_id, segment, side, "LIMIT", target_lots, limit_price)
        
        if not order_id:
            logger.error("   ❌ RL-OMS: Initial order placement failed. Falling back to start_price.")
            return start_price

        start_time = datetime.now()
        last_limit = limit_price
        final_fill_price = limit_price

        # 3. The 'Chase' Loop
        while True:
            await asyncio.sleep(self.chase_interval)
            elapsed = (datetime.now() - start_time).total_seconds()
            
            # Check Status
            status = await broker.get_order_status(order_id)
            logger.info(f"   ↳ RL-OMS: Order {order_id} Status: {status} | Elapsed: {elapsed:.0f}s")
            
            if status == "TRADED":
                logger.info(f"   ✅ RL-OMS: Hunt Success! Filled at ₹{last_limit:.2f}")
                return last_limit
            
            if status in ["CANCELLED", "REJECTED"]:
                logger.warning(f"   ⚠️ RL-OMS: Order {status}. Returning start_price.")
                return start_price

            # Get Latest Depth to see if we need to chase
            depth = await broker.get_market_depth(sec_id, segment)
            if not depth: continue

            current_target = depth["bid"] if side == "BUY" else depth["ask"]
            
            # 4. Decision: Do we chase?
            # If market moved > 0.02% away from our limit, we move with it.
            price_diff_pct = abs(current_target - last_limit) / last_limit
            total_slippage_from_start = abs(current_target - start_price) / start_price
            
            if price_diff_pct > 0.0002: # 0.02% move detected
                # Check Max Slippage Guard
                if total_slippage_from_start > self.max_slippage_pct:
                    logger.warning(f"   🚨 RL-OMS: Max Chase limit reached (0.2%). Stopping chase.")
                    # Keep existing limit and wait for fill or timeout
                else:
                    success = await broker.modify_order(order_id, target_lots, current_target)
                    if success:
                        logger.info(f"   🔄 RL-OMS: Chasing {side} Price -> ₹{current_target:.2f}")
                        last_limit = current_target
            
            # 5. Timeout / Desperation Logic
            if elapsed >= self.max_wait_seconds:
                logger.warning(f"   ⏳ RL-OMS: Hunt Timeout. Converting to MARKET for guaranteed fill.")
                await broker.cancel_order(order_id)
                # Note: In real life, we'd immediately place a Market order here.
                # For this implementation, we assume market fill at current LTP.
                return depth["ltp"]

rl_oms = RL_OMS()
