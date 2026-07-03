import os
import datetime
import time
import pandas as pd
from dhanhq import dhanhq
from dotenv import load_dotenv
from database_manager import db_manager, MarketIndicator

# Load environment variables
load_dotenv()

class DeepDataHarvester:
    def __init__(self):
        self.client_id = os.getenv("DHAN_CLIENT_ID")
        self.access_token = os.getenv("DHAN_ACCESS_TOKEN")
        self.dhan = dhanhq(self.client_id, self.access_token)
        db_manager.connect()

    def fetch_historical_intraday(self, symbol, security_id, exchange_segment, from_date, to_date):
        """
        Fetches 1-minute historical data from Dhan and saves to MarketIndicator table.
        Format: YYYY-MM-DD
        """
        print(f"🚜 Harvesting Deep Data for {symbol} ({from_date} to {to_date})...")
        
        try:
            # Dhan API call for historical intraday data
            data = self.dhan.intraday_minute_data(
                security_id=security_id,
                exchange_segment=exchange_segment,
                instrument_type='INDEX',
                from_date=from_date,
                to_date=to_date
            )
            
            if data.get('status') == 'success':
                df = pd.DataFrame(data['data'])
                if df.empty:
                    print(f"⚠️ No data returned for {symbol} between {from_date} and {to_date}")
                    return 0
                
                records = []
                for _, row in df.iterrows():
                    # Format in v2.0.2: timestamp can be 'YYYY-MM-DD HH:MM:SS' or milliseconds epoch
                    raw_ts = row['start_Time'] if 'start_Time' in row else row['timestamp']
                    
                    if isinstance(raw_ts, (int, float)):
                        # If it's a large number, it's likely milliseconds
                        unit = 'ms' if raw_ts > 1e11 else 's'
                        ts = pd.to_datetime(raw_ts, unit=unit)
                    else:
                        ts = pd.to_datetime(raw_ts)

                    records.append({
                        "timestamp": ts,
                        "ticker": symbol,
                        "timeframe": 1,
                        "price": float(row['close']),
                        "vwap": float((row['high'] + row['low'] + row['close']) / 3)
                    })
                
                # Bulk insert into DB
                batch_size = 1000
                with db_manager.db.atomic():
                    for i in range(0, len(records), batch_size):
                        MarketIndicator.insert_many(records[i:i+batch_size]).execute()
                
                print(f"✅ Saved {len(records)} candles.")
                return len(records)
            else:
                print(f"❌ Dhan API Error: {data.get('remarks')}")
                return 0

        except Exception as e:
            print(f"❌ Deep Harvesting Failed for {from_date}: {e}")
            return 0

    def harvest_5_years(self, symbol, security_id, exchange_segment):
        """
        Dhan historical data limits:
        - Intraday (1m): Last 90 days
        To get 5 years, we usually need daily data or a different API.
        HOWEVER, for 1m data, Dhan only provides last 90 days.
        For true 5-year 1m data, one typically needs a paid provider like TrueData or GlobalDataFeeds.
        BUT, we will harvest whatever is available in 90-day windows.
        """
        end_date = datetime.date.today()
        # Dhan's limit for 1-minute data is actually 90 days.
        start_date = end_date - datetime.timedelta(days=90) 
        
        print(f"🚀 Starting Multi-Year Harvest for {symbol} (Target: Max Available)")
        
        current_to = end_date
        total_records = 0
        
        # We'll try to go back as much as Dhan allows in 90-day chunks
        # Note: If Dhan only gives 90 days total for 1m, this loop will stop after first iteration
        for _ in range(20): # Try up to 5 years (approx 20 * 90 days)
            current_from = current_to - datetime.timedelta(days=90)
            
            count = self.fetch_historical_intraday(
                symbol, security_id, exchange_segment,
                current_from.strftime("%Y-%m-%d"),
                current_to.strftime("%Y-%m-%d")
            )
            
            total_records += count
            if count == 0:
                print(f"🏁 No more data available for {symbol} before {current_from}")
                break
                
            current_to = current_from - datetime.timedelta(days=1)
            time.sleep(1) # Rate limit respect

        print(f"📊 Total Harvested for {symbol}: {total_records} records.")

if __name__ == "__main__":
    harvester = DeepDataHarvester()
    
    # NIFTY 50 (Security ID 13)
    harvester.harvest_5_years("NIFTY", "13", "IDX_I")
    
    # BANKNIFTY (Security ID 25)
    harvester.harvest_5_years("BANKNIFTY", "25", "IDX_I")
