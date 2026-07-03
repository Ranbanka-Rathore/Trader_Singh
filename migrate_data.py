import json
import os
from database_manager import db_manager, Trade, OpenPosition, MarketIndicator, OptionChainData
from datetime import datetime

def migrate_trades():
    if os.path.exists("paper_trades_db.json"):
        with open("paper_trades_db.json", "r") as f:
            data = json.load(f)
            
            # Migrate Open Positions
            for pos in data.get("open_positions", []):
                # Convert date strings to datetime objects if needed
                if "entry_date" in pos and isinstance(pos["entry_date"], str):
                    pos["entry_date"] = datetime.strptime(pos["entry_date"], "%Y-%m-%d %H:%M:%S")
                OpenPosition.create(**pos)
            
            # Migrate Trade History
            for trade in data.get("trade_history", []):
                if "entry_date" in trade and isinstance(trade["entry_date"], str):
                    trade["entry_date"] = datetime.strptime(trade["entry_date"], "%Y-%m-%d %H:%M:%S")
                if "exit_date" in trade and isinstance(trade["exit_date"], str):
                    trade["exit_date"] = datetime.strptime(trade["exit_date"], "%Y-%m-%d %H:%M:%S")
                Trade.create(**trade)
        print("Migrated trades successfully.")

def migrate_historical():
    files = ["historical_3min.json", "historical_5min.json", "historical_15min.json"]
    for file in files:
        if os.path.exists(file):
            with open(file, "r") as f:
                content = json.load(f)
                timeframe = content.get("timeframe", 5)
                ticker = "^NSEI" # Default for now
                
                indicators = []
                for entry in content.get("data", []):
                    # Time in JSON is "HH:MM", need to combine with date
                    # For migration, we'll use today's date or just the time
                    # Better to parse the 'last_update' date
                    last_update = datetime.strptime(content["last_update"], "%Y-%m-%d %H:%M:%S")
                    time_str = entry["Time"]
                    hour, minute = map(int, time_str.split(":"))
                    timestamp = last_update.replace(hour=hour, minute=minute, second=0)
                    
                    indicators.append({
                        "timestamp": timestamp,
                        "ticker": ticker,
                        "timeframe": timeframe,
                        "call_oi": entry.get("Call"),
                        "put_oi": entry.get("Put"),
                        "oi_diff": entry.get("Diff"),
                        "pcr": entry.get("PCR"),
                        "vwap": entry.get("VWAP"),
                        "price": entry.get("Price")
                    })
                
                if indicators:
                    with db_manager.db.atomic():
                        MarketIndicator.insert_many(indicators).execute()
            print(f"Migrated {file} successfully.")

def migrate_oi_memory():
    if os.path.exists("oi_memory_bank.json"):
        # This file might be large or have multiple snapshots if it was appended to,
        # but the current content seems to be a single snapshot.
        # Let's assume it's a single snapshot for now.
        with open("oi_memory_bank.json", "r") as f:
            content = json.load(f)
            timestamp_str = content.get("timestamp")
            # Convert HH:MM:SS to full timestamp using today
            now = datetime.now()
            hour, minute, second = map(int, timestamp_str.split(":"))
            timestamp = now.replace(hour=hour, minute=minute, second=second)
            
            ticker = "^NSEI"
            chain_data = []
            for entry in content.get("chain_data", []):
                chain_data.append({
                    "timestamp": timestamp,
                    "ticker": ticker,
                    "strike": entry.get("Strike"),
                    "call_coi": entry.get("Call_COI"),
                    "put_coi": entry.get("Put_COI"),
                    "call_oi_chg": entry.get("Call_OI_Chg"),
                    "put_oi_chg": entry.get("Put_OI_Chg")
                })
            
            if chain_data:
                with db_manager.db.atomic():
                    OptionChainData.insert_many(chain_data).execute()
        print("Migrated oi_memory_bank.json successfully.")

if __name__ == "__main__":
    try:
        db_manager.connect()
        db_manager.initialize_tables()
        migrate_trades()
        migrate_historical()
        migrate_oi_memory()
    except Exception as e:
        print(f"Error during migration: {e}")
    finally:
        db_manager.close()
