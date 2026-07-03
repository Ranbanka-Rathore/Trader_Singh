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

query = """
    SELECT timestamp, ticker, pa_status, pcr, ml_score, committee_verdict, committee_reasoning
    FROM signal_audits 
    WHERE timestamp::date = '2026-06-24' 
    ORDER BY timestamp ASC
"""
df = pd.read_sql(query, engine)
print(f"Total signals generated yesterday: {len(df)}")
if not df.empty:
    print(df.to_string(index=False))
