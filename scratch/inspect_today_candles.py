import os
import sys
import pandas as pd
from sqlalchemy import create_engine
from dotenv import load_dotenv

# Force UTF-8 stdout
if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8')

# Load the env variables
load_dotenv()

# Add project root to path for imports
sys.path.append(os.getcwd())
from backend.app.db.database import DB_HOST, DB_PORT, DB_USER, DB_PASSWORD, DB_NAME

db_url = f"postgresql://{DB_USER}:{DB_PASSWORD}@{DB_HOST}:{DB_PORT}/{DB_NAME}"
engine = create_engine(db_url)

print("📡 Fetching NIFTY 5m candles for June 22, 2026...")
query = """
    SELECT timestamp, open, high, low, close, volume FROM candles
    WHERE ticker = 'NIFTY' AND timeframe = '5m' AND timestamp::date = '2026-06-22'
    ORDER BY timestamp ASC
"""
df = pd.read_sql(query, engine)
if df.empty:
    print("❌ No data found.")
    sys.exit()

df['timestamp'] = pd.to_datetime(df['timestamp'])
df['IST_Time'] = df['timestamp'].dt.tz_convert('Asia/Kolkata')
df['Time'] = df['IST_Time'].dt.strftime('%H:%M')

print(f"Loaded {len(df)} candles.")
print(f"Session Start Spot: {df['open'].iloc[0]:.2f}")
print(f"Session End Spot: {df['close'].iloc[-1]:.2f}")
print(f"Session High: {df['high'].max():.2f}")
print(f"Session Low: {df['low'].min():.2f}")

# Calculate typical price and VWAP
df['Typical_Price'] = (df['high'] + df['low'] + df['close']) / 3
df['VP'] = df['Typical_Price'] * df['volume']
df['cum_vp'] = df['VP'].cumsum()
df['cum_vol'] = df['volume'].cumsum().replace(0, 1)
df['VWAP'] = df['cum_vp'] / df['cum_vol']

print("\n--- Detailed Candle List ---")
for idx, row in df.iterrows():
    print(f"Time: {row['Time']} | Close: {row['close']:.2f} | High: {row['high']:.2f} | Low: {row['low']:.2f} | VWAP: {row['VWAP']:.2f}")
