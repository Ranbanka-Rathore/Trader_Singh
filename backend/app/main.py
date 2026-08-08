from fastapi import FastAPI, Depends, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from contextlib import asynccontextmanager
import asyncio
import selectors
import os
import warnings

# --- WINDOWS ASYNC COMPATIBILITY FIX ---
# psycopg3 requires SelectorEventLoop on Windows. Suppress deprecation warning
# since this is required until psycopg adds ProactorEventLoop support.
if os.name == 'nt':
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", DeprecationWarning)
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func
from typing import List, Dict, Any
from backend.app.db.database import get_session, init_db, dispose_engine
from backend.app.db.models import SignalAudit, OpenPosition, Trade, MarketIndicator
from backend.app.core.risk_shield import RiskShield
from backend.app.core.quant_engine import QuantEngine
from backend.app.services.redis_service import redis_service
from ai_market_analyst import AIMarketAnalyst
import uvicorn
import json
import os
from datetime import datetime
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

from backend.app.core.logging_setup import setup_logging

logger = setup_logging("CoreAPI", "api.log")


@asynccontextmanager
async def lifespan(app_instance: FastAPI):
    """Modern FastAPI lifespan handler."""
    logger.info("[STARTUP] Initializing database...")
    try:
        await init_db()
        logger.info("[STARTUP] Database ready.")
    except Exception as e:
        logger.error(f"[STARTUP] Database init failed: {e}")
    yield
    await dispose_engine()
    logger.info("[SHUTDOWN] Database pool closed.")


app = FastAPI(title="TRADER SINGH | CORE API v7.0", version="7.0.0", lifespan=lifespan)

# CORS Middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

shield = RiskShield()
engine = QuantEngine()

# Resilient AI Initialization
try:
    analyst = AIMarketAnalyst()
except Exception as e:
    logger.warning(f"[AI] Analyst failed to init (missing API key?): {e}")
    analyst = None

@app.get("/api/v1/ai/audit")
async def generate_forensic_audit():
    """Triggers the Deep Forensic AI Audit."""
    logger.info("🕵️ Received request for Forensic Deep Audit...")
    if analyst is None:
        logger.error("❌ Forensic Audit failed: AI Analyst not initialized (check GROQ_API_KEY).")
        raise HTTPException(
            status_code=503, 
            detail="AI Analyst module not initialized. Ensure GROQ_API_KEY is set in .env."
        )
    
    try:
        report = await analyst.generate_learning_report()
        logger.info("✅ Forensic Audit report generated successfully.")
        return {"status": "SUCCESS", "report": report}
    except Exception as e:
        logger.error(f"❌ Forensic Audit Error: {e}")
        raise HTTPException(status_code=500, detail=f"AI Engine Error: {str(e)}")

@app.get("/api/v1/autopilot/status")
async def get_autopilot_status():
    """Returns the current status of the background worker."""
    try:
        status = await asyncio.wait_for(redis_service.get_json("autopilot_status"), timeout=3.0)
        if not status:
            return {"status": "OFFLINE", "last_run": None}
        return status
    except (asyncio.TimeoutError, Exception):
        return {"status": "OFFLINE", "last_run": None, "error": "Redis unavailable"}

@app.post("/api/v1/autopilot/start")
async def start_autopilot():
    """Sends start command to the background worker."""
    logger.info("[API] Received START command request")
    try:
        await asyncio.wait_for(
            redis_service.publish("autopilot_commands", {"command": "START"}), timeout=3.0
        )
    except (asyncio.TimeoutError, Exception) as e:
        return {"status": "ERROR", "message": f"Redis unavailable: {e}"}
    logger.info("[API] Published START command to Redis")
    return {"status": "COMMAND_SENT", "command": "START"}

@app.post("/api/v1/autopilot/stop")
async def stop_autopilot():
    """Sends stop command to the background worker."""
    logger.info("[API] Received STOP command request")
    try:
        await asyncio.wait_for(
            redis_service.publish("autopilot_commands", {"command": "STOP"}), timeout=3.0
        )
    except (asyncio.TimeoutError, Exception) as e:
        return {"status": "ERROR", "message": f"Redis unavailable: {e}"}
    logger.info("[API] Published STOP command to Redis")
    return {"status": "COMMAND_SENT", "command": "STOP"}

@app.get("/api/v1/autopilot/dev_mode")
async def get_dev_mode():
    """Returns the current status of Developer Mode."""
    try:
        is_dev = await asyncio.wait_for(redis_service.get_json("dev_mode"), timeout=3.0)
        return {"dev_mode": is_dev or False}
    except (asyncio.TimeoutError, Exception):
        return {"dev_mode": False, "error": "Redis unavailable"}

@app.post("/api/v1/autopilot/dev_mode")
async def toggle_dev_mode(status: bool):
    """Sets the Developer Mode status."""
    try:
        await asyncio.wait_for(redis_service.set_json("dev_mode", status), timeout=3.0)
        return {"status": "SUCCESS", "dev_mode": status}
    except (asyncio.TimeoutError, Exception) as e:
        return {"status": "ERROR", "message": f"Redis unavailable: {e}"}

@app.get("/api/v1/autopilot/firefighting")
async def get_firefighting_status():
    """Returns the current status of Options Firefighting."""
    try:
        is_enabled = await asyncio.wait_for(redis_service.get_json("firefighting_enabled"), timeout=3.0)
        return {"firefighting": is_enabled or False}
    except (asyncio.TimeoutError, Exception):
        return {"firefighting": False, "error": "Redis unavailable"}

@app.post("/api/v1/autopilot/firefighting")
async def toggle_firefighting(status: bool):
    """Enables or disables Options Firefighting."""
    try:
        await asyncio.wait_for(redis_service.set_json("firefighting_enabled", status), timeout=3.0)
        return {"status": "SUCCESS", "firefighting": status}
    except (asyncio.TimeoutError, Exception) as e:
        return {"status": "ERROR", "message": f"Redis unavailable: {e}"}

@app.get("/api/v1/autopilot/low-margin")
async def get_low_margin_status():
    """Returns the current status of Low Margin Mode."""
    try:
        val = await redis_service.client.get("low_margin_only")
        is_enabled = val.decode() == "true" if val else False
        return {"low_margin": is_enabled}
    except Exception:
        return {"low_margin": False, "error": "Redis unavailable"}

@app.post("/api/v1/autopilot/low-margin")
async def toggle_low_margin(status: bool):
    """Enables or disables Low Margin Mode."""
    try:
        val_str = "true" if status else "false"
        await redis_service.client.set("low_margin_only", val_str)
        return {"status": "SUCCESS", "low_margin": status}
    except Exception as e:
        return {"status": "ERROR", "message": f"Redis unavailable: {e}"}

@app.get("/api/v1/autopilot/strategy-selection")
async def get_strategy_selection_mode():
    """Returns the current strategy selection mode (AUTO vs MANUAL)."""
    try:
        mode = await asyncio.wait_for(redis_service.client.get("strategy_selection_mode"), timeout=3.0)
        decoded = mode.decode() if mode and isinstance(mode, bytes) else (mode or "MANUAL")
        return {"mode": decoded}
    except (asyncio.TimeoutError, Exception):
        return {"mode": "MANUAL", "error": "Redis unavailable"}

@app.post("/api/v1/autopilot/strategy-selection")
async def set_strategy_selection_mode(mode: str):
    """Sets the strategy selection mode (AUTO or MANUAL)."""
    if mode not in ["AUTO", "MANUAL"]:
        raise HTTPException(status_code=400, detail="Invalid strategy selection mode")
    try:
        await asyncio.wait_for(redis_service.client.set("strategy_selection_mode", mode), timeout=3.0)
        return {"status": "SUCCESS", "mode": mode}
    except (asyncio.TimeoutError, Exception) as e:
        return {"status": "ERROR", "message": f"Redis unavailable: {e}"}

@app.get("/api/v1/autopilot/strategy-mode")
async def get_strategy_mode():
    """Returns the current execution strategy mode."""
    try:
        mode = await asyncio.wait_for(redis_service.get("execution_strategy_mode"), timeout=3.0)
        return {"strategy_mode": mode or "CREDIT_SPREAD"}
    except (asyncio.TimeoutError, Exception):
        return {"strategy_mode": "CREDIT_SPREAD", "error": "Redis unavailable"}

@app.post("/api/v1/autopilot/strategy-mode")
async def set_strategy_mode(mode: str):
    """Sets the execution strategy mode."""
    if mode not in ["CREDIT_SPREAD", "DEBIT_SPREAD", "CALENDAR_SPREAD", "DIAGONAL_SPREAD", "DELTA_NEUTRAL", "COVERED_CALL", "CASH_SECURED_PUT"]:
        raise HTTPException(status_code=400, detail="Invalid strategy mode")
    try:
        await asyncio.wait_for(redis_service.set("execution_strategy_mode", mode), timeout=3.0)
        return {"status": "SUCCESS", "strategy_mode": mode}
    except (asyncio.TimeoutError, Exception) as e:
        return {"status": "ERROR", "message": f"Redis unavailable: {e}"}


class ConnectionManager:
    def __init__(self):
        self.active_connections: List[WebSocket] = []

    async def connect(self, websocket: WebSocket):
        await websocket.accept()
        self.active_connections.append(websocket)

    def disconnect(self, websocket: WebSocket):
        self.active_connections.remove(websocket)

    async def broadcast(self, message: dict):
        for connection in self.active_connections:
            await connection.send_json(message)

manager = ConnectionManager()



@app.get("/")
async def root():
    return {
        "status": "ONLINE", 
        "system": "AGENTIC_TRADER_v7", 
        "timestamp": datetime.now().isoformat(),
        "nodes": ["ALGO_ENGINE", "RISK_SHIELD", "AI_COMMITTEE"]
    }

@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    await manager.connect(websocket)
    
    # Create a subscriber to Redis market_updates
    pubsub = await redis_service.subscribe("market_updates")
    
    async def listen_to_redis():
        try:
            async for message in pubsub.listen():
                if message["type"] == "message":
                    data = json.loads(message["data"])
                    await websocket.send_json(data)
        except Exception as e:
            print(f"WebSocket Redis Listener Error: {e}")

    # Run listener as a background task
    listener_task = asyncio.create_task(listen_to_redis())
    
    try:
        while True:
            # Non-blocking check for incoming messages (Heartbeats)
            try:
                # Wait for message for 60 seconds (twice the heartbeat interval)
                data = await asyncio.wait_for(websocket.receive_text(), timeout=60.0)
                # Echo heartbeats back to client for validation
                await websocket.send_json({"type": "PONG", "timestamp": datetime.now().isoformat()})
            except asyncio.TimeoutError:
                # Send a server-side ping to verify connection
                await websocket.send_json({"type": "PING"})
                continue
                
    except WebSocketDisconnect:
        manager.disconnect(websocket)
        listener_task.cancel()
    except Exception as e:
        print(f"WebSocket Error: {e}")
        manager.disconnect(websocket)
        listener_task.cancel()

@app.get("/api/v1/scan")
async def run_market_scan(session: AsyncSession = Depends(get_session)):
    """Triggers a manual market scan on the core universe."""
    universe = ["NIFTY"]
    passed = await engine.analyze_universe(universe)
    return {"status": "SUCCESS", "passed_assets": passed}

@app.get("/api/v1/positions", response_model=List[Dict[str, Any]])
async def get_active_positions(session: AsyncSession = Depends(get_session)):
    """Returns all currently open trades, each annotated with live unrealized P&L."""
    from backend.app.services.options_pricing_service import options_pricing_service
    from backend.app.services.execution_service import execution_service

    result = await session.execute(select(OpenPosition))
    positions = result.scalars().all()

    enriched = []
    for pos in positions:
        data = pos.model_dump(mode="json")
        snap = await redis_service.get_json(f"market_snapshot:{pos.ticker}")
        current_price = float(snap["price"]) if snap and "price" in snap else float(pos.spot_price or 0)

        mark = await options_pricing_service.mark_position_pnl(pos, current_spot=current_price)
        if mark:
            lot_size = execution_service.risk_shield.get_lot_size(pos.ticker)
            qty = int(pos.lots_sized or 1) * lot_size
            data["unrealized_pnl"] = round(mark["pnl_per_share"] * qty, 2)
            data["unrealized_pnl_source"] = "LIVE"
        else:
            import datetime as _dt
            ist_now = _dt.datetime.now(_dt.timezone(_dt.timedelta(hours=5, minutes=30))).replace(tzinfo=None)
            data["unrealized_pnl"] = round(
                execution_service._calculate_strategy_pnl(pos, current_price, ist_now), 2
            )
            data["unrealized_pnl_source"] = "HEURISTIC_FALLBACK"
        enriched.append(data)

    return enriched

@app.get("/api/v1/trades", response_model=List[Trade])
async def get_historical_trades(limit: int = 50, session: AsyncSession = Depends(get_session)):
    """Returns historical trade ledger."""
    result = await session.execute(
        select(Trade).order_by(Trade.entry_date.desc()).limit(limit)
    )
    return result.scalars().all()

@app.get("/api/v1/signals", response_model=List[SignalAudit])
async def get_signals(limit: int = 50, session: AsyncSession = Depends(get_session)):
    """Fetches the latest Signal Intelligence Audit logs."""
    result = await session.execute(
        select(SignalAudit).order_by(SignalAudit.timestamp.desc()).limit(limit)
    )
    return result.scalars().all()

@app.get("/api/v1/stats")
async def get_portfolio_stats(session: AsyncSession = Depends(get_session)):
    """Calculates real-time P&L and Risk Metrics."""
    # Try to get cached report from Redis first (populated by worker)
    try:
        cached_stats = await asyncio.wait_for(
            redis_service.get_json("portfolio_stats"), timeout=3.0
        )
        if cached_stats:
            return cached_stats
    except (asyncio.TimeoutError, Exception):
        pass  # Redis unavailable — fall through to manual calculation

    # Fallback to manual DB calculation
    try:
        pos_result = await session.execute(select(OpenPosition))
        open_positions = pos_result.scalars().all()
        
        today_start = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
        pnl_result = await session.execute(
            select(func.sum(Trade.realized_pnl))
            .where(Trade.exit_date >= today_start)
        )
        daily_pnl = float(pnl_result.scalar() or 0.0)

        # Fetch Latest GEX (Proxy: NIFTY)
        gex_result = await session.execute(
            select(MarketIndicator.total_gex)
            .where(MarketIndicator.ticker == "NIFTY")
            .order_by(MarketIndicator.timestamp.desc())
            .limit(1)
        )
        total_gex = float(gex_result.scalar() or 0.0)
        
        report = shield.get_risk_report(open_positions, daily_pnl)
        report["total_gex"] = total_gex
        return report
    except Exception as e:
        logger.warning(f"[API] DB fallback for stats failed: {e}")
        return {
            "total_pnl": 0.0, "daily_pnl": 0.0, "total_gex": 0.0,
            "open_positions": 0, "max_drawdown": 0.0,
            "error": "Databases temporarily unavailable"
        }

@app.get("/api/v1/ticks/{ticker}")
async def get_recent_ticks(ticker: str):
    """Fetches the last 1000 ticks for a ticker from Redis."""
    try:
        ticks = await asyncio.wait_for(
            redis_service.client.lrange(f"ticks:{ticker}", 0, 999), timeout=3.0
        )
        decoded_ticks = [json.loads(t) for t in ticks]
        decoded_ticks.reverse()
        return decoded_ticks
    except (asyncio.TimeoutError, Exception):
        return []  # Return empty list when Redis is unavailable

@app.get("/api/v1/snapshot/{ticker}")
async def get_market_snapshot(ticker: str):
    """Fetches the latest market snapshot for a ticker from Redis."""
    try:
        snapshot = await asyncio.wait_for(
            redis_service.get_json(f"market_snapshot:{ticker}"), timeout=3.0
        )
        return snapshot or {}
    except (asyncio.TimeoutError, Exception):
        return {}

@app.get("/api/v1/levels/{ticker}")
async def get_structural_levels(ticker: str):
    """Fetches the latest structural levels for a ticker from Redis."""
    try:
        levels = await asyncio.wait_for(
            redis_service.get_json(f"structural_levels:{ticker}"), timeout=3.0
        )
        return levels or {}
    except (asyncio.TimeoutError, Exception):
        return {}

@app.post("/api/v1/backfill/{ticker}")
async def backfill_ticker_data(ticker: str, days: int = 30, interval: str = "5m"):
    """Triggers a historical data backfill for a ticker."""
    from backend.app.services.data_service import data_service
    success = await data_service.backfill_historical_data(ticker, days, interval)
    if success:
        return {"status": "SUCCESS", "message": f"Backfilled {days} days for {ticker}"}
    else:
        raise HTTPException(status_code=500, detail="Backfill failed")

if __name__ == "__main__":
    import asyncio
    import selectors
    import uvicorn
    
    # Force SelectorEventLoop for Windows compatibility with psycopg async
    if os.name == 'nt':
        selector = selectors.SelectSelector()
        loop = asyncio.SelectorEventLoop(selector)
        asyncio.set_event_loop(loop)
    else:
        loop = asyncio.get_event_loop()
        
    config = uvicorn.Config(app, host="0.0.0.0", port=8000, loop="asyncio")
    server = uvicorn.Server(config)
    loop.run_until_complete(server.serve())
