import os
import sys
import pandas as pd
from sqlalchemy import create_engine
from dotenv import load_dotenv

load_dotenv()
sys.stdout.reconfigure(encoding='utf-8')

# DB Connection Setup
db_user = os.getenv("DB_USER", "trader")
db_pass = os.getenv("DB_PASSWORD", "institutional_grade_password")
db_port = os.getenv("DB_PORT", "5432")
db_name = os.getenv("DB_NAME", "agentic_trader")
db_host = os.getenv("DB_HOST", "localhost")
db_url = f"postgresql://{db_user}:{db_pass}@{db_host}:{db_port}/{db_name}"

engine = create_engine(db_url)

print("=== 1m Candles (First 10) ===")
query_1m = """
    SELECT timestamp, open, close FROM candles 
    WHERE ticker = 'NIFTY' AND timeframe = '1m' AND timestamp::date = '2026-06-24' 
    ORDER BY timestamp ASC LIMIT 10
"""
df_1m = pd.read_sql(query_1m, engine)
print("Naive timestamp types:", df_1m['timestamp'].dtype)
print("Raw values:\n", df_1m)

print("\n=== 5m Candles (First 10) ===")
query_5m = """
    SELECT timestamp, open, close FROM candles 
    WHERE ticker = 'NIFTY' AND timeframe = '5m' AND timestamp::date = '2026-06-24' 
    ORDER BY timestamp ASC LIMIT 10
"""
df_5m = pd.read_sql(query_5m, engine)
print(df_5m)
