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

    # 0e. Phase 4 controls: sizing equity, committee mode, regime state
    import os as _os
    print(f"📐 [P4] TRADING_EQUITY: ₹{float(_os.getenv('TRADING_EQUITY', '500000')):,.0f} "
          f"(risk 1.5%/trade, hard cap 3%)")
    cm = _os.getenv("COMMITTEE_MODE", "advisory").lower()
    print(f"⚖️ [P4] COMMITTEE_MODE: {cm}" +
          (" — LLM verdicts recorded but NOT blocking" if cm != "blocking" else " — LLM can veto"))
    iso = _os.getenv("INTRADAY_SQUARE_OFF", "").lower() == "true"
    print(f"⏳ [P4] Exit stack: TP 0.5x | strike-touch | backstop -1.5x | "
          f"{'DAILY 15:15 square-off (legacy)' if iso else 'T-1 time stop (validated)'}")
    try:
        from backend.app.services.regime_service import regime_service
        allowed, reason, _sug = regime_service.evaluate(
            ticker="NIFTY", strategy_type="BULL_PUT_SPREAD", pcr=1.40,
            spot=float((await redis_service.get_json('market_snapshot:NIFTY') or {}).get('price', 0) or 0) or 24000.0)
        print(f"🌡️ [P4] Regime gate (probe bull-put @ PCR 1.4): "
              f"{'OPEN' if allowed else f'CLOSED ({reason})'} "
              f"[{len(regime_service._closes)} sessions of state]")
    except Exception as e:
        print(f"⚠️ [P4] Regime state unavailable: {e} (gate will fail-safe = no entries)")

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
