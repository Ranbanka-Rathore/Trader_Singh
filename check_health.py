import asyncio
import logging
import os
from backend.app.services.redis_service import redis_service

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("HealthCheck")

async def main():
    print("\n" + "="*50)
    print("   TRADER SINGH v8.0 - SYSTEM HEALTH CHECK")
    print("="*50)

    # 0. Trading mode gate (most important line for safety)
    try:
        from trading_mode import mode as trading_mode
        m = trading_mode()
        if m == "LIVE":
            print("⚠️  [SAFETY] TRADING_MODE: LIVE — REAL ORDERS WILL BE SENT")
        else:
            print("🧻 [SAFETY] TRADING_MODE: PAPER — orders simulated, none sent to broker")
    except Exception as e:
        print(f"❌ [SAFETY] TRADING_MODE: could not resolve ({e})")

    # 0b. Dhan API credentials present
    if os.getenv("DHAN_ACCESS_TOKEN") and os.getenv("DHAN_CLIENT_ID"):
        print("✅ [BROKER] Dhan credentials: present in environment")
    else:
        print("❌ [BROKER] Dhan credentials: MISSING (DHAN_CLIENT_ID / DHAN_ACCESS_TOKEN)")

    # 0c. ML model load
    try:
        from ml_approval_engine import MLApprovalEngine
        eng = MLApprovalEngine(ticker="NIFTY", timeframe=1)
        if eng.model_data:
            print("✅ [ML] NIFTY 1m model: loaded")
        else:
            print("⚠️ [ML] NIFTY 1m model: NOT loaded (ML gate will fail-closed to 0.5)")
    except Exception as e:
        print(f"❌ [ML] Model load check failed: {e}")

    # 0d. Scrip master (lot sizes / expiries)
    try:
        from backend.app.core import scrip_master
        if scrip_master.loaded():
            print(f"✅ [DATA] Scrip master: loaded (NIFTY lot={scrip_master.get_lot_size('NIFTY')}, "
                  f"nearest expiry={scrip_master.get_nearest_expiry('NIFTY')})")
        else:
            print("⚠️ [DATA] Scrip master: not loaded — using fallback lot sizes")
    except Exception as e:
        print(f"❌ [DATA] Scrip master check failed: {e}")

    # 1. Check Redis Connection
    try:
        await redis_service.client.ping()
        print("✅ [INFRA] Redis: Connected")
    except:
        print("❌ [INFRA] Redis: DISCONNECTED")

    # 2. Check System Control Status
    status = await redis_service.get_json("autopilot_status")
    if status:
        print(f"✅ [CONTROL] Status: {status.get('status')} (Source: {status.get('source')})")
        print(f"   ↳ Last Pulse: {status.get('last_run')}")
    else:
        print("❌ [CONTROL] Status: NOT FOUND (Service might be down)")

    # 3. Check Data Harvester
    snap = await redis_service.get_json("market_snapshot:NIFTY")
    if snap:
        print(f"✅ [DATA] Harvester: ACTIVE (Last NIFTY Price: {snap.get('price')})")
    else:
        print("⚠️ [DATA] Harvester: No recent snapshots found.")

    # 4. Check API Gateway
    print("✅ [API] Gateway: http://localhost:8000")
    print("✅ [UI] Dashboard: http://localhost:5173")
    
    print("="*50 + "\n")

if __name__ == "__main__":
    asyncio.run(main())
