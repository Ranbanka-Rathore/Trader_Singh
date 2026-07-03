import os
import pandas as pd
import datetime
import json
from dotenv import load_dotenv
from dhan_integration import DhanBroker
from database_manager import db_manager, MarketIndicator, OpenPosition
from ml_approval_engine import MLApprovalEngine

def get_latest_autotrender_signal(ticker="NIFTY", timeframe=3):
    """Reads the database to get the live institutional signal."""
    try:
        # Query the latest market indicator for the given ticker and timeframe
        latest_data = (MarketIndicator
                       .select()
                       .where((MarketIndicator.ticker == ticker) & (MarketIndicator.timeframe == timeframe))
                       .order_by(MarketIndicator.timestamp.desc())
                       .get())
        
        pcr = float(latest_data.pcr)
        price = float(latest_data.price)
        vwap = float(latest_data.vwap)
        
        # Recreate the exact logic from your dashboard
        pcr_signal = "BUY" if pcr >= 1.15 else "SELL" if pcr <= 0.85 else "WAIT"
        vwap_signal = "BUY" if price > vwap else "SELL" if price < vwap else "WAIT"
        
        return {
            "pcr_signal": pcr_signal, 
            "vwap_signal": vwap_signal, 
            "pcr_value": pcr
        }
    except Exception as e:
        # Fallback if no data is found in DB
        return {"pcr_signal": "WAIT", "vwap_signal": "WAIT", "pcr_value": 1.0}

# Load the secure environment variables
load_dotenv(override=True)

class QuantEngine:
    def __init__(self):
        self.dhan_client_id = os.getenv("DHAN_CLIENT_ID")
        self.dhan_access_token = os.getenv("DHAN_ACCESS_TOKEN")
        self.broker = DhanBroker(self.dhan_client_id, self.dhan_access_token)
        # Ensure database is connected
        db_manager.connect()
        
        # Cache for ML engines
        self.ml_engines = {}

    def get_ml_engine(self, ticker, timeframe=1):
        key = f"{ticker}_{timeframe}"
        if key not in self.ml_engines:
            self.ml_engines[key] = MLApprovalEngine(ticker=ticker, timeframe=timeframe)
        return self.ml_engines[key]

    def analyze_universe(self, universe):
        print("\n" + "="*60)
        print("⚡ QUANT ENGINE: [DISABLED] - REFACTOR TO BACKEND/APP/CORE REQUIRED")
        print("="*60)
        return []
                
        return passed_assets

    def _calculate_coi_pcr(self, current_spot_price, df_chain):
        """Smart Indexing: Dynamically finds ATM and slices +/- 10 strikes."""
        df_chain['Distance'] = abs(df_chain['Strike'] - current_spot_price)
        atm_idx = df_chain['Distance'].idxmin()
        
        start_idx = max(0, atm_idx - 10)
        end_idx = min(len(df_chain), atm_idx + 11)
        active_strikes_df = df_chain.iloc[start_idx:end_idx]
        
        total_put_coi = active_strikes_df['Put_COI'].sum()
        total_call_coi = active_strikes_df['Call_COI'].sum()
        
        if total_call_coi > 0:
            live_pcr = total_put_coi / total_call_coi
        elif total_call_coi < 0 and total_put_coi > 0:
            live_pcr = 9.99 
        else:
            live_pcr = 1.0
            
        if live_pcr >= 1.25: bias = "BULLISH"
        elif live_pcr <= 1.00: bias = "BEARISH"
        else: bias = "NEUTRAL"
        
        return {"coi_pcr": round(live_pcr, 2), "bias": bias}