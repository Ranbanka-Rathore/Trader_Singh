import os
import sys
import datetime
import pandas as pd
import numpy as np
import asyncio
import selectors

if os.name == 'nt':
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

# Add parent dir to path
sys.path.append(os.getcwd())
from backend.app.core.quant_engine import QuantEngine
from backend.app.services.data_service import data_service
from backend.app.db.database import engine as db_engine
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import sessionmaker

# Force UTF-8 stdout
if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8')

async def main():
    print("📡 Fetching today's candles for NIFTY...")
    df = await data_service.get_latest_candles("NIFTY", timeframe="5m", limit=100)
    
    if df.empty:
        print("❌ No data fetched.")
        return
        
    if df.index.tz is None:
        df.index = df.index.tz_localize('UTC')
    df['IST_Time'] = df.index.tz_convert('Asia/Kolkata')
    df['Date'] = df['IST_Time'].dt.date
    
    # Filter for today (June 5, 2026)
    today_ist = datetime.date(2026, 6, 5)
    df_today = df[df['Date'] == today_ist].copy()
    
    if df_today.empty:
        print("❌ No candles found for today in fetched data.")
        return
        
    print(f"Loaded {len(df_today)} candles for NIFTY today.")
    
    # We will step through each candle of today and evaluate how QuantEngine sees it
    # We need to simulate that we are at index `i` of today's candles
    for i in range(1, len(df_today) + 1):
        # Slice the df up to index `i` of today's candles in the full df
        end_idx = df.index.get_loc(df_today.index[i-1])
        df_slice = df.iloc[:end_idx + 1].copy()
        
        # Calculate VWAP
        df_slice['Typical_Price'] = (df_slice['High'] + df_slice['Low'] + df_slice['Close']) / 3
        df_slice['Volume_Proxy'] = 1
        df_slice['VP'] = df_slice['Typical_Price'] * df_slice['Volume_Proxy']
        df_slice['cum_vp'] = df_slice.groupby('Date', observed=True)['VP'].cumsum()
        df_slice['cum_vol'] = df_slice.groupby('Date', observed=True)['Volume_Proxy'].cumsum()
        df_slice['VWAP'] = df_slice['cum_vp'] / df_slice['cum_vol']
        
        current_vwap = float(df_slice['VWAP'].iloc[-1])
        current_price = float(df_slice['Close'].iloc[-1])
        
        df_today_slice = df_slice[df_slice['Date'] == today_ist].copy()
        df_today_prev = df_today_slice.iloc[:-1] if len(df_today_slice) > 1 else pd.DataFrame()
        
        # Fallback values
        session_high = float(df_slice['High'].iloc[-1])
        session_low = float(df_slice['Low'].iloc[-1])
        
        if not df_today_prev.empty:
            session_high = float(df_today_prev['High'].max())
            session_low = float(df_today_prev['Low'].min())
            
        # Bollinger Bands around VWAP
        df_slice['std_20'] = df_slice['Close'].rolling(window=20).std()
        std_val = float(df_slice['std_20'].iloc[-1]) if not pd.isna(df_slice['std_20'].iloc[-1]) else 0.0
        upper_band = current_vwap + (2.0 * std_val)
        lower_band = current_vwap - (2.0 * std_val)
        
        pa_status = "CHOP_ZONE"
        is_bullish_sweep = (session_low * 0.9985 <= df_slice['Low'].iloc[-1] < session_low) and current_price > session_low
        is_bearish_sweep = (session_high < df_slice['High'].iloc[-1] <= session_high * 1.0015) and current_price < session_high
        
        if is_bullish_sweep:
            pa_status = "LIQUIDITY_SWEEP_LONG"
        elif is_bearish_sweep:
            pa_status = "LIQUIDITY_SWEEP_SHORT"
        elif current_price >= upper_band and df_slice['Close'].iloc[-2] < upper_band:
            pa_status = "VWAP_DEVIATION_SHORT"
        elif current_price <= lower_band and df_slice['Close'].iloc[-2] > lower_band:
            pa_status = "VWAP_DEVIATION_LONG"
        elif current_price <= session_low:
            pa_status = "SESSION_LOW_BREAKDOWN"
        elif current_price >= session_high:
            pa_status = "SESSION_HIGH_BREAKOUT"
            
        direction = None
        bullish_triggers = [
            "VWAP_BULL_CROSS", "SESSION_HIGH_BREAKOUT", "SMC_DEMAND_BOUNCE", 
            "HTF_SUPPORT_BOUNCE", "MTF_DOUBLE_BOTTOM", "VWAP_PULLBACK_LONG",
            "LIQUIDITY_SWEEP_LONG", "VWAP_DEVIATION_LONG"
        ]
        bearish_triggers = [
            "VWAP_BEAR_CROSS", "SESSION_LOW_BREAKDOWN", "SMC_SUPPLY_REJECTION", 
            "HTF_RESISTANCE_REJECTION", "MTF_DOUBLE_TOP", "VWAP_RALLY_SHORT",
            "LIQUIDITY_SWEEP_SHORT", "VWAP_DEVIATION_SHORT"
        ]
        
        if pa_status in bullish_triggers: direction = "BULLISH"
        elif pa_status in bearish_triggers: direction = "BEARISH"
        
        time_str = df_today_slice.index[-1].tz_convert('Asia/Kolkata').strftime('%H:%M')
        if direction or pa_status != "CHOP_ZONE":
            print(f"Time: {time_str} | Close: {current_price:.2f} | VWAP: {current_vwap:.2f} | session_low: {session_low:.2f} | low: {df_slice['Low'].iloc[-1]:.2f} | PA Status: {pa_status} | Direction: {direction}")

if __name__ == "__main__":
    asyncio.run(main())
