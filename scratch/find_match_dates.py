import os
import sys
import datetime
import time
import pandas as pd
from sqlalchemy import create_engine
from dotenv import load_dotenv

sys.stdout.reconfigure(encoding='utf-8')
load_dotenv()

def get_db_url():
    db_user = os.getenv("DB_USER", "trader")
    db_pass = os.getenv("DB_PASSWORD", "institutional_grade_password")
    db_port = os.getenv("DB_PORT", "5432")
    db_name = os.getenv("DB_NAME", "agentic_trader")
    
    def _get_wsl_ip():
        import subprocess
        try:
            result = subprocess.run(["wsl", "-d", "Ubuntu", "hostname", "-I"], capture_output=True, text=True, timeout=5)
            if result.returncode == 0:
                ip = result.stdout.strip().split()[0]
                if ip and ip != "127.0.0.1":
                    return ip
        except Exception:
            pass
        return None

    def _resolve_db_host():
        import socket
        try:
            s = socket.create_connection(("127.0.0.1", int(db_port)), timeout=1)
            s.close()
            return "127.0.0.1"
        except Exception:
            pass
        wsl_ip = _get_wsl_ip()
        return wsl_ip if wsl_ip else "127.0.0.1"

    db_host = _resolve_db_host()
    return f"postgresql://{db_user}:{db_pass}@{db_host}:{db_port}/{db_name}"

db_url = get_db_url()
df = pd.DataFrame()

for attempt in range(10):
    try:
        engine = create_engine(db_url)
        query = """
            SELECT DISTINCT c.timestamp::date as match_date 
            FROM candles c 
            JOIN market_indicators m 
              ON c.ticker = m.ticker 
              AND c.timestamp = m.timestamp 
              AND m.timeframe = 1
            ORDER BY match_date DESC 
            LIMIT 10
        """
        df = pd.read_sql(query, engine)
        break
    except Exception as e:
        err_str = str(e)
        if "starting up" in err_str or "OperationalError" in err_str or "connection" in err_str or "SSL" in err_str:
            print(f"⚠️ DB is starting up or has SSL error. Retrying in 2 seconds... (Attempt {attempt+1}/10)")
            time.sleep(2)
        else:
            raise e

if df.empty:
    print("❌ No matches found at all.")
else:
    print("Dates with exact matches:")
    print(df)
