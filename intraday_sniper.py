import pandas as pd
import time
import datetime
# yfinance removed
from universe_selector import UniverseSelector

def run_sniper_bot():
    print("="*60)
    print("🦅 INTRADAY SNIPER: [DISABLED] - NEEDS DATA SOURCE REFACTOR")
    print("="*60)
    print("   ⚠️ yfinance has been removed. Integration with Dhan live feed required.")
    return
    watch_list = universe.get('backup_stocks', ["RELIANCE.NS", "HDFCBANK.NS"])[:5]
    print(f"👀 Watching: {', '.join(watch_list)}")
    
    while True:
        now = datetime.datetime.now()
        
        # Hard stop at 3:15 PM (Flatten all day-trade positions)
        if now.hour >= 15 and now.minute >= 15:
            print("\n⏰ 3:15 PM HIT: FLATTENING ALL INTRADAY POSITIONS. SHUTTING DOWN.")
            break
            
        print(f"\n[{now.strftime('%H:%M:%S')}] Scanning 5m Candles...")
        
        for ticker in watch_list:
            try:
                # Rapid data fetch
                df = yf.download(ticker, period="1d", interval="5m", progress=False)
                if isinstance(df.columns, pd.MultiIndex):
                    df.columns = df.columns.get_level_values(0)
                    
                if df.empty: continue
                
                is_index = ticker in ["^NSEI", "^NSEBANK", "^BSESN"]
                
                # Fast VWAP calculation for the current day
                df['Typical_Price'] = (df['High'] + df['Low'] + df['Close']) / 3
                
                # 🛡️ THE FIX: Protect against Division by Zero for Indices 🛡️
                if is_index or df['Volume'].sum() == 0:
                    # Fallback for indices: Time-Weighted Average Price (TWAP)
                    dynamic_wap = df['Typical_Price'].expanding().mean()
                else:
                    # Standard Stock VWAP
                    dynamic_wap = (df['Typical_Price'] * df['Volume']).cumsum() / df['Volume'].cumsum()
                
                current_price = float(df['Close'].iloc[-1])
                current_wap = float(dynamic_wap.iloc[-1])
                
                # SNIPER TRIGGER: If price violently crosses above VWAP/TWAP
                if current_price > current_wap * 1.001: 
                    print(f"🚨 TRIGGER [BUY]: {ticker} Crossed Above VWAP! (Price: ₹{current_price:.2f} | VWAP: ₹{current_wap:.2f})")
                    # In live fire, your Dhan API execution code goes EXACTLY here.
                else:
                    print(f"   ⏳ {ticker}: No Setup. (Price: ₹{current_price:.2f} | VWAP: ₹{current_wap:.2f})")
                    
            except Exception as e:
                print(f"   ⚠️ Error scanning {ticker}: {e}")
                
        # Wait 60 seconds before pulling the next 5m candle update
        time.sleep(60)

if __name__ == "__main__":
    run_sniper_bot()