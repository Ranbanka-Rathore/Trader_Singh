import os
import subprocess
import socket
from sqlalchemy import create_engine, text
from dotenv import load_dotenv

load_dotenv()

db_user = os.getenv("DB_USER", "trader")
db_pass = os.getenv("DB_PASSWORD", "institutional_grade_password")
db_port = os.getenv("DB_PORT", "5432")
db_name = os.getenv("DB_NAME", "agentic_trader")

def get_wsl_ip():
    try:
        r = subprocess.run(["wsl", "-d", "Ubuntu", "hostname", "-I"], capture_output=True, text=True, timeout=5)
        if r.returncode == 0:
            return r.stdout.strip().split()[0]
    except Exception:
        pass
    return None

def resolve_host():
    try:
        s = socket.create_connection(("127.0.0.1", int(db_port)), timeout=1)
        s.close()
        return "127.0.0.1"
    except Exception:
        pass
    wsl = get_wsl_ip()
    return wsl if wsl else "127.0.0.1"

host = resolve_host()
url = f"postgresql://{db_user}:{db_pass}@{host}:{db_port}/{db_name}"
engine = create_engine(url)

print("=== Market Indicators Count ===")
with engine.connect() as conn:
    rows = conn.execute(text("SELECT ticker, timeframe, count(*), min(timestamp), max(timestamp) FROM market_indicators GROUP BY ticker, timeframe")).fetchall()
    for r in rows:
        print(r)
