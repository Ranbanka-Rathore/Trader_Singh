import os
import datetime
import pandas as pd
import numpy as np
from sqlalchemy import create_engine

# Force UTF-8 stdout
import sys
if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8')

# Database connection
db_user = os.getenv("DB_USER", "trader")
db_pass = os.getenv("DB_PASSWORD", "institutional_grade_password")
db_port = os.getenv("DB_PORT", "5432")
db_name = os.getenv("DB_NAME", "agentic_trader")
db_host = os.getenv("DB_HOST", "localhost")

db_url = f"postgresql://{db_user}:{db_pass}@{db_host}:{db_port}/{db_name}"
engine = create_engine(db_url)

print("📡 Fetching candles for NIFTY...")
query = """
    SELECT timestamp, open, high, low, close, volume FROM candles
    WHERE ticker = 'NIFTY' AND timeframe = '5m'
    ORDER BY timestamp ASC
"""
df = pd.read_sql(query, engine)
if df.empty:
    print("❌ No data found.")
    sys.exit()

df['timestamp'] = pd.to_datetime(df['timestamp'])
df['IST_Time'] = df['timestamp'].dt.tz_convert('Asia/Kolkata')
df['Date'] = df['IST_Time'].dt.date
df['Time'] = df['IST_Time'].dt.strftime('%H:%M')

# Calculate indicators
df['Typical_Price'] = (df['high'] + df['low'] + df['close']) / 3
df['VP'] = df['Typical_Price'] * df['volume']
df['cum_vp'] = df.groupby('Date', observed=True)['VP'].cumsum()
df['cum_vol'] = df.groupby('Date', observed=True)['volume'].cumsum()
df['cum_vol'] = df['cum_vol'].replace(0, 1)
df['VWAP'] = df['cum_vp'] / df['cum_vol']

df['std_20'] = df['close'].rolling(window=20).std()
df['std_20'] = df['std_20'].bfill()
df['Upper_Band'] = df['VWAP'] + (2.0 * df['std_20'])
df['Lower_Band'] = df['VWAP'] - (2.0 * df['std_20'])

# Session High/Low (shifted to exclude current candle)
df['session_high'] = df.groupby('Date', observed=True)['high'].transform(lambda x: x.cummax().shift(1))
df['session_low'] = df.groupby('Date', observed=True)['low'].transform(lambda x: x.cummin().shift(1))

# Filter for today (June 5, 2026)
today_date = datetime.date(2026, 6, 5)
df_today = df[df['Date'] == today_date].copy()

if df_today.empty:
    print(f"❌ No candles found for today: {today_date}")
    sys.exit()

print(f"✅ Loaded {len(df_today)} candles for today ({today_date}).")
print("\n" + "="*120)
print(f"{'Time':<7} | {'Close':<9} | {'VWAP':<9} | {'Lower Band':<10} | {'Upper Band':<10} | {'Session L':<9} | {'Session H':<9} | {'Low':<9} | {'High':<9} | {'Status':<25}")
print("="*120)

for idx in range(len(df_today)):
    row = df_today.iloc[idx]
    
    # Calculate sweep and deviation flags
    is_bullish_sweep = False
    is_bearish_sweep = False
    is_lower_dev = False
    is_upper_dev = False
    
    # Needs historical session_low/high to be non-null
    if not pd.isna(row['session_low']):
        is_bullish_sweep = (row['session_low'] * 0.9985 <= row['low'] < row['session_low']) and row['close'] > row['session_low']
    if not pd.isna(row['session_high']):
        is_bearish_sweep = (row['session_high'] < row['high'] <= row['session_high'] * 1.0015) and row['close'] < row['session_high']
        
    # Standard deviation band crosses
    # For simplicity, check if current price crosses the band
    if row['close'] <= row['Lower_Band']:
        is_lower_dev = True
    if row['close'] >= row['Upper_Band']:
        is_upper_dev = True
        
    pa_status = "CHOP_ZONE"
    if is_bullish_sweep:
        pa_status = "LIQUIDITY_SWEEP_LONG"
    elif is_bearish_sweep:
        pa_status = "LIQUIDITY_SWEEP_SHORT"
    elif is_upper_dev:
        pa_status = "VWAP_DEVIATION_SHORT"
    elif is_lower_dev:
        pa_status = "VWAP_DEVIATION_LONG"
    elif row['close'] <= row['session_low']:
        pa_status = "SESSION_LOW_BREAKDOWN"
    elif row['close'] >= row['session_high']:
        pa_status = "SESSION_HIGH_BREAKOUT"

    print(f"{row['Time']:<7} | {row['close']:<9.2f} | {row['VWAP']:<9.2f} | {row['Lower_Band']:<10.2f} | {row['Upper_Band']:<10.2f} | {row['session_low']:<9.2f} | {row['session_high']:<9.2f} | {row['low']:<9.2f} | {row['high']:<9.2f} | {pa_status:<25}")

print("="*120 + "\n")
