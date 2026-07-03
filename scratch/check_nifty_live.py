import os
import sys
import asyncio
import datetime
import pandas as pd

# Add parent dir to path
sys.path.append(os.getcwd())

from backend.app.services.data_service import data_service

if os.name == 'nt':
    import warnings
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", DeprecationWarning)
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

async def main():
    print("📡 Fetching latest candles for NIFTY...")
    df = await data_service.get_latest_candles("NIFTY", timeframe="5m", limit=30)
    
    if df.empty:
        print("❌ No candles found in DB!")
        return
        
    if df.index.tz is None:
        df.index = df.index.tz_localize('UTC')
    df['IST_Time'] = df.index.tz_convert('Asia/Kolkata')
    df['Date'] = df['IST_Time'].dt.date
    
    today_ist = datetime.date(2026, 6, 22)
    df_today = df[df['Date'] == today_ist].copy()
    
    print(f"✅ Loaded {len(df_today)} candles for today ({today_ist})")
    
    # Calculate VWAP exactly like quant_engine
    df_today['Typical_Price'] = (df_today['High'] + df_today['Low'] + df_today['Close']) / 3
    df_today['Volume_Proxy'] = 1
    df_today['VP_Proxy'] = df_today['Typical_Price'] * df_today['Volume_Proxy']
    df_today['cum_vp'] = df_today['VP_Proxy'].cumsum()
    df_today['cum_vol'] = df_today['Volume_Proxy'].cumsum()
    df_today['VWAP'] = df_today['cum_vp'] / df_today['cum_vol']
    
    # Also calculate standard VWAP using the Volume column in DB if present
    df_today['cum_vp_real'] = (df_today['Typical_Price'] * df_today['Volume']).cumsum()
    df_today['cum_vol_real'] = df_today['Volume'].cumsum().replace(0, 1)
    df_today['VWAP_Real'] = df_today['cum_vp_real'] / df_today['cum_vol_real']
    
    print("\n" + "="*120)
    print(f"{'Time (IST)':<12} | {'Open':<8} | {'High':<8} | {'Low':<8} | {'Close':<8} | {'Volume':<6} | {'Our VWAP':<10} | {'Vol-W VWAP':<10}")
    print("="*120)
    for idx, row in df_today.iterrows():
        time_str = row['IST_Time'].strftime('%H:%M:%S')
        print(f"{time_str:<12} | {row['Open']:<8.2f} | {row['High']:<8.2f} | {row['Low']:<8.2f} | {row['Close']:<8.2f} | {int(row['Volume']):<6} | {row['VWAP']:<10.2f} | {row['VWAP_Real']:<10.2f}")
    print("="*120)

if __name__ == "__main__":
    asyncio.run(main())
