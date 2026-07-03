import os
import sys
import asyncio
import datetime
import pandas as pd
import numpy as np

# Add parent dir to path
sys.path.append(os.getcwd())

from backend.app.core.quant_engine import QuantEngine
from backend.app.services.data_service import data_service
from backend.app.db.database import engine as db_engine
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import sessionmaker

if os.name == 'nt':
    import warnings
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", DeprecationWarning)
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

async def main():
    print("📡 Initializing QuantEngine...")
    engine = QuantEngine()
    
    ticker = "NIFTY"
    clean_ticker = "NIFTY"
    
    # 1. Fetch HTF Memory
    print(f"📡 Fetching HTF Memory for {ticker}...")
    await engine._fetch_htf_memory(ticker)
    mem = engine._htf_memory.get(ticker)
    
    # 2. Fetch latest candles
    print(f"📡 Fetching latest candles for {clean_ticker}...")
    df = await data_service.get_latest_candles(clean_ticker, timeframe="5m", limit=300)
    if df.empty:
        print("❌ No candles found!")
        return
        
    if df.index.tz is None:
        df.index = df.index.tz_localize('UTC')
    df['IST_Time'] = df.index.tz_convert('Asia/Kolkata')
    df['Date'] = df['IST_Time'].dt.date
    
    today_ist = datetime.date(2026, 6, 5)
    df_today = df[df['Date'] == today_ist].copy()
    
    print(f"✅ Loaded {len(df_today)} candles for today ({today_ist}). Analyzing step-by-step...")
    print("="*130)
    print(f"{'Time':<7} | {'Price':<8} | {'VWAP':<8} | {'LowerB':<8} | {'UpperB':<8} | {'PA Status':<25} | {'VolSurg':<7} | {'Direction':<9} | {'Reason/Status':<30}")
    print("="*130)
    
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
    
    for idx in range(len(df_today)):
        # Slice up to current candle of today
        end_idx = df.index.get_loc(df_today.index[idx])
        df_slice = df.iloc[:end_idx + 1].copy()
        
        # Calculate indicators on the slice
        df_slice['Typical_Price'] = (df_slice['High'] + df_slice['Low'] + df_slice['Close']) / 3
        df_slice['Volume_Proxy'] = 1
        df_slice['VP_Proxy'] = df_slice['Typical_Price'] * df_slice['Volume_Proxy']
        df_slice['cum_vp'] = df_slice.groupby('Date', observed=True)['VP_Proxy'].cumsum()
        df_slice['cum_vol'] = df_slice.groupby('Date', observed=True)['Volume_Proxy'].cumsum()
        df_slice['VWAP'] = df_slice['cum_vp'] / df_slice['cum_vol']
        
        current_vwap = float(df_slice['VWAP'].iloc[-1])
        current_price = float(df_slice['Close'].iloc[-1])
        current_time = df_slice['IST_Time'].iloc[-1]
        
        # Session boundaries
        df_today_slice = df_slice[df_slice['Date'] == today_ist].copy()
        df_today_prev = df_today_slice.iloc[:-1] if len(df_today_slice) > 1 else pd.DataFrame()
        if not df_today_prev.empty:
            session_high = float(df_today_prev['High'].max())
            session_low = float(df_today_prev['Low'].min())
        else:
            if mem:
                session_high = mem['pdh']
                session_low = mem['pdl']
            else:
                session_high = float(df_slice['High'].iloc[-1])
                session_low = float(df_slice['Low'].iloc[-1])
                
        # Bollinger Bands around VWAP
        df_slice['std_20'] = df_slice['Close'].rolling(window=20).std()
        std_val = float(df_slice['std_20'].iloc[-1]) if not pd.isna(df_slice['std_20'].iloc[-1]) else 0.0
        upper_band = current_vwap + (2.0 * std_val)
        lower_band = current_vwap - (2.0 * std_val)
        
        # Sweeps
        is_bullish_sweep = False
        is_bearish_sweep = False
        if mem:
            target_low = min(mem['pdl'], session_low)
            is_bullish_sweep = (target_low * 0.9985 <= df_slice['Low'].iloc[-1] < target_low) and current_price > target_low
            target_high = max(mem['pdh'], session_high)
            is_bearish_sweep = (target_high < df_slice['High'].iloc[-1] <= target_high * 1.0015) and current_price < target_high
        else:
            is_bullish_sweep = (session_low * 0.9985 <= df_slice['Low'].iloc[-1] < session_low) and current_price > session_low
            is_bearish_sweep = (session_high < df_slice['High'].iloc[-1] <= session_high * 1.0015) and current_price < session_high
            
        pa_status = "CHOP_ZONE"
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
            
        # Volatility / Volume Surge
        df_slice['body'] = abs(df_slice['Close'] - df_slice['Open'])
        avg_body = df_slice['body'].iloc[-11:-1].mean()
        avg_vol = df_slice['Volume'].iloc[-11:-1].mean()
        vol_surge = float(df_slice['Volume'].iloc[-1] / avg_vol) if avg_vol > 0 else 1.0
        
        # Crosses
        if pa_status == "CHOP_ZONE" and len(df_slice) >= 2:
            if current_price > current_vwap and df_slice['Close'].iloc[-2] <= df_slice['VWAP'].iloc[-2] and (True or vol_surge >= 1.5):
                pa_status = "VWAP_BULL_CROSS"
            elif current_price < current_vwap and df_slice['Close'].iloc[-2] >= df_slice['VWAP'].iloc[-2] and (True or vol_surge >= 1.5):
                pa_status = "VWAP_BEAR_CROSS"
                
        direction = None
        status_reason = "No signal"
        
        if pa_status in bullish_triggers:
            if True or vol_surge >= 1.0:
                direction = "BULLISH"
                status_reason = "Signal Active"
            else:
                status_reason = f"Blocked by Vol Surge ({vol_surge:.2f} < 1.0)"
        elif pa_status in bearish_triggers:
            if True or vol_surge >= 1.0:
                direction = "BEARISH"
                status_reason = "Signal Active"
            else:
                status_reason = f"Blocked by Vol Surge ({vol_surge:.2f} < 1.0)"
                
        time_str = current_time.strftime('%H:%M')
        print(f"{time_str:<7} | {current_price:<8.2f} | {current_vwap:<8.2f} | {lower_band:<8.2f} | {upper_band:<8.2f} | {pa_status:<25} | {vol_surge:<7.2f} | {str(direction):<9} | {status_reason:<30}")
        
    print("="*130)

if __name__ == "__main__":
    asyncio.run(main())
