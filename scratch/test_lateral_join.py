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
            SELECT c.timestamp, c.open, c.high, c.low, c.close, c.volume, m.pcr, m.total_gex, m.ind_time
            FROM candles c
            LEFT JOIN LATERAL (
                SELECT pcr, total_gex, timestamp as ind_time
                FROM market_indicators
                WHERE ticker = c.ticker 
                  AND timeframe = 1 
                  AND timestamp <= c.timestamp
                ORDER BY timestamp DESC
                LIMIT 1
            ) m ON TRUE
            WHERE c.ticker = 'NIFTY' 
              AND c.timeframe = '5m' 
              AND c.timestamp::date = '2026-06-25'
            ORDER BY c.timestamp ASC
        """
        df = pd.read_sql(query, engine)
        break
    except Exception as e:
        err_str = str(e)
        if "starting up" in err_str or "OperationalError" in err_str or "connection" in err_str or "SSL" in err_str:
            print(f"⚠️ DB is starting up. Retrying in 2 seconds... (Attempt {attempt+1}/10)")
            time.sleep(2)
        else:
            raise e

if df.empty:
    print("❌ No data found.")
    sys.exit()

df['timestamp'] = pd.to_datetime(df['timestamp'])
df['IST_Time'] = df['timestamp'].dt.tz_convert('Asia/Kolkata') if df['timestamp'].dt.tz is not None else df['timestamp'].dt.tz_localize('UTC').dt.tz_convert('Asia/Kolkata')
df['Time'] = df['IST_Time'].dt.strftime('%H:%M')

print("Time | Open | Close | PCR | Total GEX | Ind Time")
print("-" * 85)
for i, row in df.iterrows():
    if ("09:35" <= row['Time'] <= "10:10") or ("14:40" <= row['Time'] <= "15:05"):
        ind_time_val = pd.to_datetime(row['ind_time'])
        ind_time_str = (ind_time_val.tz_convert('Asia/Kolkata').strftime('%H:%M:%S') if ind_time_val.tz is not None else ind_time_val.tz_localize('UTC').tz_convert('Asia/Kolkata').strftime('%H:%M:%S')) if not pd.isna(row['ind_time']) else "None"
        print(f"{row['Time']} | {row['open']:.2f} | {row['close']:.2f} | {row['pcr']} | {row['total_gex']} | {ind_time_str}")
