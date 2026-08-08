import asyncio
import json
import os
import selectors
from datetime import datetime, time as dt_time, timedelta, timezone
from backend.app.services.redis_service import redis_service

# IST is UTC+5:30
IST = timezone(timedelta(hours=5, minutes=30))

from backend.app.core.logging_setup import setup_logging

# Own file, not the shared trader_singh.log: rotation needs a single writer, and
# this service's 5s heartbeat was drowning the risk committee's decision log.
logger = setup_logging("SystemControl", "system_control.log")

# --- WINDOWS ASYNC COMPATIBILITY FIX ---
if os.name == 'nt':
    import warnings
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", DeprecationWarning)
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

async def main():
    logger.info("Starting V8 System Control (Autopilot Manager) - IST Aware...")
    
    # Initialize status if not present
    try:
        current_status = await redis_service.get_json("autopilot_status")
        if not current_status:
            logger.info("Initializing autopilot_status in Redis...")
            await redis_service.set_json("autopilot_status", {
                "status": "PAUSED",
                "last_run": datetime.now(IST).isoformat(),
                "source": "IDLE"
            })
    except Exception as e:
        logger.error(f"Failed to connect to Redis during init: {e}")

    # Listen for commands in background
    async def listen_commands():
        logger.info("Subscribing to autopilot_commands channel...")
        try:
            pubsub = await redis_service.subscribe("autopilot_commands")
            async for message in pubsub.listen():
                if message["type"] == "message":
                    data = json.loads(message["data"])
                    command = data.get("command")
                    logger.info(f"RECEIVED COMMAND FROM REDIS: {command}")
                    if command == "START":
                        await redis_service.client.set("autopilot_mode", "START")
                    elif command == "STOP":
                        await redis_service.client.set("autopilot_mode", "STOP")
        except Exception as e:
            logger.error(f"Error in command listener: {e}")

    asyncio.create_task(listen_commands())

    has_run_classifier_today = False
    has_run_reclassifier_today = False
    last_date = None

    while True:
        try:
            # 1. Determine Mode
            mode_raw = await redis_service.client.get("autopilot_mode")
            mode = mode_raw.decode() if mode_raw and isinstance(mode_raw, bytes) else (mode_raw or "STOP")
            
            dev_mode_val = await redis_service.get_json("dev_mode")
            dev_mode = bool(dev_mode_val) if dev_mode_val is not None else False
            
            # 2. Market Hour Check (IST)
            now_ist = datetime.now(IST)
            current_date = now_ist.date()
            is_weekday = now_ist.weekday() < 5

            # Reset daily flags on new day
            if last_date != current_date:
                last_date = current_date
                has_run_classifier_today = False
                has_run_reclassifier_today = False

            # 3. Dynamic Strategy Regime Classification (runs at 09:10 AM on weekdays, or immediately in dev_mode once)
            should_run_classifier = False
            
            # Fetch selection mode (AUTO vs MANUAL)
            selection_mode_raw = await redis_service.client.get("strategy_selection_mode")
            selection_mode = selection_mode_raw.decode() if selection_mode_raw and isinstance(selection_mode_raw, bytes) else (selection_mode_raw or "MANUAL")
            
            if selection_mode == "AUTO":
                if dev_mode:
                    if now_ist.time() >= dt_time(9, 10) and not has_run_classifier_today:
                        should_run_classifier = True
                else:
                    if is_weekday and now_ist.time() >= dt_time(9, 10) and now_ist.time() < dt_time(15, 30):
                        if not has_run_classifier_today:
                            should_run_classifier = True

            if should_run_classifier:
                try:
                    from backend.app.db.database import get_session
                    from backend.app.services.regime_classifier import regime_classifier
                    async for session in get_session():
                        strategy = await regime_classifier.get_market_regime(session)
                        await redis_service.set("execution_strategy_mode", strategy)
                        logger.info(f"🔮 [SystemControl] Dynamic strategy classified for today: {strategy}")
                        break
                    has_run_classifier_today = True
                except Exception as ce:
                    logger.error(f"Failed to run regime classifier: {ce}")
            
            # 3.5. Mid-Market Dynamic Strategy Pivot (runs at 10:00 AM IST)
            should_run_reclassifier = False
            if selection_mode == "AUTO":
                if dev_mode:
                    if now_ist.time() >= dt_time(10, 0) and not has_run_reclassifier_today:
                        should_run_reclassifier = True
                else:
                    if is_weekday and now_ist.time() >= dt_time(10, 0) and now_ist.time() < dt_time(15, 30):
                        if not has_run_reclassifier_today:
                            should_run_reclassifier = True

            if should_run_reclassifier:
                try:
                    from backend.app.db.database import get_session
                    from backend.app.db.models import Candle
                    from sqlalchemy import select
                    
                    async for session in get_session():
                        # Fetch 09:15 - 09:30 AM candles for today to find Opening Range
                        today_start = datetime.combine(current_date, dt_time(9, 15))
                        today_range_end = datetime.combine(current_date, dt_time(9, 30))
                        
                        q = select(Candle.high, Candle.low).where(
                            (Candle.ticker == "NIFTY") & 
                            (Candle.timeframe == "5m") & 
                            (Candle.timestamp >= today_start) & 
                            (Candle.timestamp <= today_range_end)
                        )
                        res = await session.execute(q)
                        rows = res.all()
                        
                        # Get current strategy
                        current_strat = await redis_service.get("execution_strategy_mode")
                        
                        if rows and current_strat:
                            highs = [float(r.high) for r in rows]
                            lows = [float(r.low) for r in rows]
                            range_high = max(highs)
                            range_low = min(lows)
                            
                            # Get latest price at 10:00 AM
                            q_latest = select(Candle.close).where(
                                (Candle.ticker == "NIFTY") & (Candle.timeframe == "5m")
                            ).order_by(Candle.timestamp.desc()).limit(1)
                            res_latest = await session.execute(q_latest)
                            latest_price = res_latest.scalar()
                            
                            if latest_price:
                                latest_price = float(latest_price)
                                new_strat = None
                                reason = ""
                                
                                # Check Low Margin Mode constraint
                                low_margin_val = await redis_service.client.get("low_margin_only")
                                low_margin_only = low_margin_val.decode() == "true" if low_margin_val else False

                                # Rule 1: Covered Call / Debit Spread Fade Pivot -> Cash Secured Put / Credit Spread
                                if (current_strat == "COVERED_CALL" or current_strat == "DEBIT_SPREAD") and latest_price < range_low:
                                    new_strat = "CREDIT_SPREAD" if low_margin_only else "CASH_SECURED_PUT"
                                    reason = f"NIFTY spot {latest_price} breached Opening Range Low {range_low}"
                                    
                                # Rule 2: Bearish Credit Spread Breakout Pivot -> Calendar Spread
                                elif current_strat == "CREDIT_SPREAD" and latest_price > range_high:
                                    new_strat = "CALENDAR_SPREAD"
                                    reason = f"NIFTY spot {latest_price} breached Opening Range High {range_high}"
                                
                                if new_strat:
                                    await redis_service.set("execution_strategy_mode", new_strat)
                                    logger.warning(f"🔄 [SystemControl] MID-MARKET STRATEGY PIVOT: {current_strat} -> {new_strat} (Reason: {reason})")
                                    await redis_service.publish("market_updates", {
                                        "type": "STRATEGY_PIVOT",
                                        "timestamp": datetime.now().isoformat(),
                                        "data": {
                                            "from_strategy": current_strat,
                                            "to_strategy": new_strat,
                                            "reason": reason
                                        }
                                    })
                        break
                    has_run_reclassifier_today = True
                except Exception as rce:
                    logger.error(f"Failed to run mid-market re-classifier: {rce}")
            
            market_open = dt_time(9, 15)
            market_close = dt_time(15, 30)
            current_time = now_ist.time()
            
            is_market_open = (market_open <= current_time <= market_close)
            
            status_text = "PAUSED"
            source = "IDLE"
            
            if mode == "START":
                if dev_mode or (is_weekday and is_market_open):
                    status_text = "ACTIVE"
                    source = "LIVE" if not dev_mode else "SIMULATED"
                else:
                    status_text = "WAITING_FOR_MARKET"
                    source = "IDLE"
            
            # Verbose logging every 10 cycles or when status changes
            logger.info(f"System Pulse | Mode: {mode} | Dev: {dev_mode} | MarketOpen: {is_market_open} | Status -> {status_text}")
            
            # 3. Update Global Status
            await redis_service.set_json("autopilot_status", {
                "status": status_text,
                "last_run": now_ist.isoformat(),
                "source": source
            }, expire=30)
            
            await asyncio.sleep(5)
        except Exception as e:
            logger.error(f"Error in System Control loop: {e}")
            await asyncio.sleep(5)

if __name__ == "__main__":
    if os.name == 'nt':
        selector = selectors.SelectSelector()
        loop = asyncio.SelectorEventLoop(selector)
        asyncio.set_event_loop(loop)
        loop.run_until_complete(main())
    else:
        asyncio.run(main())
