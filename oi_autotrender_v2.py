import os
import time
import json
import pandas as pd
from datetime import datetime, timedelta, time as dttime
from dotenv import load_dotenv
import logging
from collections import deque
from database_manager import db_manager, MarketIndicator, OptionChainData
from websocket_manager import DhanWebSocketManager
from institutional_edge import InstitutionalEdgeEngine

# Setup logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger("DeltaAggregator")

try:
    from dhan_integration import DhanBroker
except ImportError:
    logger.error("❌ DhanBroker class not found. Ensure dhan_integration.py is correctly wired.")
    exit()

print("="*80)
print("👁️ SMC-STYLE AUTOTRENDER V2: INSTITUTIONAL EDGE ENGINE (GEX/VOC)")
print("="*80)

load_dotenv(override=True)
dhan_id = os.getenv("DHAN_CLIENT_ID")
dhan_token = os.getenv("DHAN_ACCESS_TOKEN")

if not dhan_id or not dhan_token:
    logger.error("❌ DHAN_CLIENT_ID or DHAN_ACCESS_TOKEN missing in .env file.")
    exit()

try:
    broker = DhanBroker(dhan_id, dhan_token)
    logger.info("✅ DhanBroker successfully initialized in Delta Engine.")
    
    ws_manager = DhanWebSocketManager(dhan_id, dhan_token)
    ws_manager.add_instrument(0, "13") # NIFTY 50
    ws_manager.start()
    logger.info("✅ WebSocket Manager started for NIFTY 50.")
    
    db_manager.connect()
    edge_engine = InstitutionalEdgeEngine()
except Exception as e:
    logger.error(f"❌ CRITICAL: Failed to initialize components: {e}")
    exit()

TARGET_INDEX = "NIFTY"
SEC_ID = "13"
OPT_TYPE = "OPTIDX"
STRIKE_RADIUS = 10
SCAN_INTERVAL_SEC = 60
TIMEFRAMES = [3, 5, 15]

class MultiTimeframeAggregator:
    def __init__(self):
        self.buffers = {tf: deque(maxlen=tf + 1) for tf in TIMEFRAMES}
        self.vwap_data = {"cumulative_tp": 0, "cumulative_vol": 0, "anchor_date": None}

    def fetch_market_data(self):
        try:
            _, df_live = broker.get_clean_option_chain(SEC_ID, OPT_TYPE) 
            spot_price = ws_manager.get_latest_price(SEC_ID)
            if spot_price == 0 or spot_price is None:
                spot_price, _ = broker.get_clean_option_chain(SEC_ID, OPT_TYPE) 
                if spot_price == 0 or spot_price is None:
                    print(f"   ⚠️ Could not fetch spot price for {SEC_ID}. Using 0.0 fallback.")
                    spot_price = 0.0
            return spot_price, df_live, None
        except Exception as e:
            logger.error(f"Error fetching market data: {e}")
            return None, None, None

    def calculate_atm_strike(self, spot_price, step=50):
        return round(spot_price / step) * step

    def update_vwap(self, current_price, timestamp):
        current_date = timestamp.date()
        if self.vwap_data["anchor_date"] != current_date:
            self.vwap_data = {"cumulative_tp": 0, "cumulative_vol": 0, "anchor_date": current_date}
        self.vwap_data["cumulative_tp"] += current_price
        self.vwap_data["cumulative_vol"] += 1
        return self.vwap_data["cumulative_tp"] / self.vwap_data["cumulative_vol"]

    def aggregate_for_timeframes(self, current_chain, current_price, current_vwap, current_timestamp):
        try:
            atm_strike = self.calculate_atm_strike(current_price)
            lower_bound = atm_strike - (STRIKE_RADIUS * 50)
            upper_bound = atm_strike + (STRIKE_RADIUS * 50)
            
            # Robust Column Standardization
            cols = {c.upper(): c for c in current_chain.columns}
            
            call_oi_col = next((cols[c] for c in cols if ('CE' in c or 'CALL' in c) and ('OI' in c or 'COI' in c)), None)
            put_oi_col = next((cols[c] for c in cols if ('PE' in c or 'PUT' in c) and ('OI' in c or 'COI' in c)), None)
            strike_col = next((cols[c] for c in cols if 'STRIKE' in c), None)

            if not all([call_oi_col, put_oi_col, strike_col]):
                logger.error(f"❌ Critical Columns Missing. Found: {current_chain.columns.tolist()}")
                return

            current_chain = current_chain.rename(columns={
                call_oi_col: 'Call_OI', 
                put_oi_col: 'Put_OI', 
                strike_col: 'Strike'
            })
            
            stripped_chain = current_chain[['Strike', 'Call_OI', 'Put_OI']].copy()
            stripped_chain['Strike'] = pd.to_numeric(stripped_chain['Strike'])
            stripped_chain['Call_OI'] = pd.to_numeric(stripped_chain['Call_OI'])
            stripped_chain['Put_OI'] = pd.to_numeric(stripped_chain['Put_OI'])
            
            self._save_raw_snapshot(stripped_chain, current_price, current_timestamp)

            # --- INSTITUTIONAL EDGE: GEX & POC ---
            today = datetime.now().date()
            days_until_thursday = (3 - today.weekday()) % 7
            if days_until_thursday == 0: days_until_thursday = 7
            expiry = today + timedelta(days=days_until_thursday)
            
            gex_matrix = edge_engine.calculate_gex_matrix(current_price, stripped_chain, expiry)
            total_gex = gex_matrix['Total_GEX'].sum() if not gex_matrix.empty else 0.0

            # 1. POC (Point of Control)
            poc_data = edge_engine.calculate_volume_profile(pd.DataFrame(list(self.buffers[15])) if self.buffers[15] else pd.DataFrame())
            current_poc = poc_data.get('poc', current_price)

            for tf in TIMEFRAMES:
                self.buffers[tf].append({"timestamp": current_timestamp, "price": current_price, "chain": stripped_chain})
                if len(self.buffers[tf]) < tf + 1: continue
                if current_timestamp.minute % tf != 0: continue
                
                old_snapshot = self.buffers[tf][0]
                merged = stripped_chain.merge(old_snapshot["chain"], on='Strike', suffixes=('', '_prev'))
                call_chg = (merged['Call_OI'] - merged['Call_OI_prev']).sum()
                put_chg = (merged['Put_OI'] - merged['Put_OI_prev']).sum()
                
                oi_diff = put_chg - call_chg
                pcr = round(put_chg / (call_chg if call_chg != 0 else 1), 2)

                db_manager.save_market_indicator({
                    "timestamp": current_timestamp,
                    "ticker": "NIFTY",
                    "timeframe": tf,
                    "call_oi": int(call_chg),
                    "put_oi": int(put_chg),
                    "oi_diff": int(oi_diff),
                    "pcr": pcr,
                    "vwap": round(current_vwap, 2),
                    "price": round(current_price, 2),
                    "total_gex": round(float(total_gex), 2),
                    "poc": round(float(current_poc), 2)
                })
                logger.info(f"✅ Persisted {tf}-Min Institutional Data. GEX: {total_gex:,.0f}")
        except Exception as e:
            logger.error(f"Error in aggregation: {e}")

    def _save_raw_snapshot(self, chain, spot_price, timestamp):
        atm = self.calculate_atm_strike(spot_price)
        sliced = chain[(chain['Strike'] >= atm-250) & (chain['Strike'] <= atm+250)]
        records = [{
            "timestamp": timestamp, 
            "ticker": "NIFTY", 
            "strike": float(r['Strike']), 
            "call_coi": float(r['Call_OI']), 
            "put_coi": float(r['Put_OI'])
        } for _, r in sliced.iterrows()]
        if records: db_manager.save_option_chain_data(records)

    def run(self):
        while True:
            spot, chain, _ = self.fetch_market_data()
            if chain is not None and not chain.empty and spot:
                vwap = self.update_vwap(spot, datetime.now())
                self.aggregate_for_timeframes(chain, spot, vwap, datetime.now())
            time.sleep(SCAN_INTERVAL_SEC)

if __name__ == "__main__":
    MultiTimeframeAggregator().run()
