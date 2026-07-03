import json
import redis

r = redis.Redis(host='172.26.128.109', port=6379, db=0)

for ticker in ["NIFTY", "BANKNIFTY"]:
    print(f"\n================= {ticker} REDIS KEYS =================")
    
    # 1. Market Snapshot
    snap = r.get(f"market_snapshot:{ticker}")
    if snap:
        print(f" market_snapshot:{ticker} -> {json.loads(snap.decode('utf-8'))}")
    else:
        print(f" market_snapshot:{ticker} -> NOT FOUND")
        
    # 2. Structural Levels
    levels = r.get(f"structural_levels:{ticker}")
    if levels:
        print(f" structural_levels:{ticker} -> {json.loads(levels.decode('utf-8'))}")
    else:
        print(f" structural_levels:{ticker} -> NOT FOUND")
        
print("=======================================================\n")
