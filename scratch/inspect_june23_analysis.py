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

print("📡 Fetching NIFTY 5m candles for June 23, 2026...")
query_candles = """
    SELECT timestamp, open, high, low, close, volume FROM candles
    WHERE ticker = 'NIFTY' AND timeframe = '5m' AND timestamp::date = '2026-06-23'
    ORDER BY timestamp ASC
"""
df_candles = pd.read_sql(query_candles, engine)
if df_candles.empty:
    print("❌ No candles found for today.")
else:
    df_candles['timestamp'] = pd.to_datetime(df_candles['timestamp'])
    df_candles['IST_Time'] = df_candles['timestamp'].dt.tz_convert('Asia/Kolkata')
    df_candles['Time'] = df_candles['IST_Time'].dt.strftime('%H:%M')
    
    # Calculate VWAP
    df_candles['Typical_Price'] = (df_candles['high'] + df_candles['low'] + df_candles['close']) / 3
    df_candles['VP'] = df_candles['Typical_Price'] * df_candles['volume']
    df_candles['cum_vp'] = df_candles['VP'].cumsum()
    df_candles['cum_vol'] = df_candles['volume'].cumsum().replace(0, 1)
    df_candles['VWAP'] = df_candles['cum_vp'] / df_candles['cum_vol']
    
    print(f"Loaded {len(df_candles)} candles.")
    print("\n--- Detailed Candle List ---")
    for idx, row in df_candles.iterrows():
         print(f"Time: {row['Time']} | Open: {row['open']:.2f} | High: {row['high']:.2f} | Low: {row['low']:.2f} | Close: {row['close']:.2f} | VWAP: {row['VWAP']:.2f}")

print("\n📡 Fetching Signal Audits for June 23, 2026...")
query_signals = """
    SELECT timestamp, ticker, pa_status, pcr, gex_mn, ml_score, committee_verdict, committee_reasoning
    FROM signal_audit
    WHERE timestamp::date = '2026-06-23'
    ORDER BY timestamp ASC
"""
df_signals = pd.read_sql(query_signals, engine)
if df_signals.empty:
    print("❌ No signal audits found for today.")
else:
    df_signals['timestamp'] = pd.to_datetime(df_signals['timestamp'])
    df_signals['IST_Time'] = df_signals['timestamp'].dt.tz_convert('Asia/Kolkata') if df_signals['timestamp'].dt.tz is not None else df_signals['timestamp']
    df_signals['Time'] = df_signals['IST_Time'].dt.strftime('%H:%M:%S')
    
    print(f"Loaded {len(df_signals)} signal audits.")
    for idx, row in df_signals.iterrows():
         print(f"\nTime: {row['Time']} | Ticker: {row['ticker']} | PA: {row['pa_status']} | PCR: {row['pcr']:.4f} | GEX: {row['gex_mn']:.0f} | ML: {row['ml_score']:.4f} | Verdict: {row['committee_verdict']}")
         print(f"Reasoning: {row['committee_reasoning']}")
