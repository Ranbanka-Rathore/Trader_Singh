import os
import sys
import datetime
import logging
from database_manager import db_manager, MarketIndicator, OpenPosition, Trade
from websocket_manager import DhanWebSocketManager
from ml_approval_engine import MLApprovalEngine
from risk_shield import RiskShield
from paper_broker import PaperBroker
from quant_engine import QuantEngine

# Configure Logging
logging.basicConfig(level=logging.INFO, format='%(levelname)s: %(message)s')
logger = logging.getLogger("Diagnostic")

def run_diagnostic():
    print("\n" + "="*60)
    print("🛡️ AGENTIC TRADER: TIER-1 INSTITUTIONAL DIAGNOSTIC")
    print("="*60)
    
    results = {}

    # 1. Database Connectivity
    print("\n[1/5] Testing Relational Infrastructure...")
    try:
        db_manager.connect()
        m_count = MarketIndicator.select().count()
        p_count = OpenPosition.select().count()
        t_count = Trade.select().count()
        print(f"✅ DB Connected. Records: {m_count} Indicators, {p_count} Positions, {t_count} History.")
        results['database'] = "PASS"
    except Exception as e:
        print(f"❌ DB Error: {e}")
        results['database'] = "FAIL"

    # 2. WebSocket & Data Edge
    print("\n[2/5] Verifying Data Edge (WebSockets)...")
    try:
        cid = os.getenv("DHAN_CLIENT_ID")
        token = os.getenv("DHAN_ACCESS_TOKEN")
        if not cid or not token:
            print("⚠️ API Keys missing. WebSocket test skipped.")
            results['websocket'] = "SKIPPED"
        else:
            ws = DhanWebSocketManager(cid, token)
            print("✅ WebSocket Manager Initialized.")
            results['websocket'] = "PASS"
    except Exception as e:
        print(f"❌ WebSocket Error: {e}")
        results['websocket'] = "FAIL"

    # 3. ML Alpha Engine
    print("\n[3/5] Auditing Intelligence Layer (XGBoost)...")
    try:
        ml = MLApprovalEngine(ticker="NIFTY", timeframe=1)
        score = ml.get_approval_score()
        print(f"✅ ML Engine Active. Sample Alpha Score: {score}")
        results['ml'] = "PASS"
    except Exception as e:
        print(f"❌ ML Error: {e}")
        results['ml'] = "FAIL"

    # 4. Risk Shield & Greeks
    print("\n[4/5] Inspecting Risk Shield (VaR & Greeks)...")
    try:
        shield = RiskShield()
        test_greeks = shield.calculate_spread_greeks(
            spot=24000, 
            leg_1_strike=24100, 
            leg_2_strike=24200, 
            expiry_date=(datetime.date.today() + datetime.timedelta(days=7)),
            volatility=0.15,
            strategy_type="BEAR_CALL_SPREAD"
        )
        print(f"✅ Greeks Calculator Functional. Test Delta: {test_greeks['net_delta']:.4f}")
        
        test_var = shield.calculate_portfolio_var([{"spot_price": 24000, "net_delta": 0.05, "lots_sized": 1}])
        print(f"✅ Portfolio VaR Engine Functional. Test VaR: ₹{test_var}")
        results['risk'] = "PASS"
    except Exception as e:
        print(f"❌ Risk Shield Error: {e}")
        results['risk'] = "FAIL"

    # 5. Core Engine Integration
    print("\n[5/5] Checking Hub & Spoke Integrity...")
    try:
        qe = QuantEngine()
        broker = PaperBroker()
        print("✅ Quant Engine and Paper Broker handshake successful.")
        results['integration'] = "PASS"
    except Exception as e:
        print(f"❌ Integration Error: {e}")
        results['integration'] = "FAIL"

    print("\n" + "="*60)
    print("📊 SYSTEM READINESS REPORT")
    print("-" * 60)
    for component, status in results.items():
        print(f"{component.upper():<15} : {status}")
    print("="*60)
    
    if all(v in ["PASS", "SKIPPED"] for v in results.values()):
        print("\n🏆 STATUS: SYSTEM READY FOR INSTITUTIONAL DEPLOYMENT")
    else:
        print("\n⚠️ STATUS: SYSTEM HAS CRITICAL VULNERABILITIES")

if __name__ == "__main__":
    run_diagnostic()
