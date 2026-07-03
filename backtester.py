import pandas as pd
import numpy as np
# yfinance removed

def run_vwap_backtest(ticker="HDFCBANK.NS"):
    print(f"📊 BACKTESTER: [DISABLED]")
    print("   ⚠️ yfinance has been removed. Backtesting requires a new data source integration.")
    return
            print("Failed to download data.")
            return

        # 1. Calculate Daily VWAP (Properly resetting each day)
        df['Date'] = df.index.date
        df['Typical_Price'] = (df['High'] + df['Low'] + df['Close']) / 3
        df['Cum_Vol_Price'] = df.groupby('Date').apply(lambda x: (x['Typical_Price'] * x['Volume']).cumsum()).reset_index(level=0, drop=True)
        df['Cum_Vol'] = df.groupby('Date').apply(lambda x: x['Volume'].cumsum()).reset_index(level=0, drop=True)
        df['VWAP'] = df['Cum_Vol_Price'] / df['Cum_Vol']
        
        # 2. Backtesting Variables
        in_trade = False
        entry_price = 0.0
        
        winning_trades = 0
        losing_trades = 0
        total_pnl_percent = 0.0
        
        trade_log = []

        print("Running historical simulation...\n")
        
        # 3. The Execution Loop
        for i in range(1, len(df)):
            current_time = df.index[i]
            current_price = float(df['Close'].iloc[i])
            prev_price = float(df['Close'].iloc[i-1])
            current_vwap = float(df['VWAP'].iloc[i])
            prev_vwap = float(df['VWAP'].iloc[i-1])
            
            # Flatten positions at 3:15 PM (15:15)
            if in_trade and current_time.hour == 15 and current_time.minute >= 15:
                pnl = (current_price - entry_price) / entry_price * 100
                total_pnl_percent += pnl
                if pnl > 0: winning_trades += 1
                else: losing_trades += 1
                trade_log.append(f"[{current_time}] ⏰ 3:15 PM FLATTEN | Exit: {current_price:.2f} | PnL: {pnl:.2f}%")
                in_trade = False
                continue

            # Check for Exits if already in a trade
            if in_trade:
                pnl = (current_price - entry_price) / entry_price * 100
                
                # FIXED: Aligned math to match Live Paper Broker logic (1.5% TP / 1.0% SL)
                if pnl >= 1.5:  
                    total_pnl_percent += pnl
                    winning_trades += 1
                    trade_log.append(f"[{current_time}] 🎯 TARGET HIT | Exit: {current_price:.2f} | PnL: {pnl:.2f}%")
                    in_trade = False
                elif pnl <= -1.0: 
                    total_pnl_percent += pnl
                    losing_trades += 1
                    trade_log.append(f"[{current_time}] 🛑 STOP LOSS | Exit: {current_price:.2f} | PnL: {pnl:.2f}%")
                    in_trade = False
                continue

            # Check for Entries (VWAP Crossover + Volume Surge)
            if not in_trade and prev_price < prev_vwap and current_price > current_vwap:
                
                # Calculate the average volume of the last 10 candles (50 minutes)
                avg_vol = df['Volume'].iloc[i-10:i].mean()
                current_vol = float(df['Volume'].iloc[i])
                
                # THE FILTER: Only buy if current volume is 1.5x higher than the recent average
                if current_vol > (avg_vol * 1.5):
                    
                    # Filter out the volatile first 15 minutes of the market open
                    if current_time.hour == 9 and current_time.minute < 30:
                        continue
                        
                    in_trade = True
                    entry_price = current_price
                    trade_log.append(f"[{current_time}] 🚨 BUY ENTRY | Price: {entry_price:.2f} (Vol Surge: {current_vol/avg_vol:.1f}x)")

        # 4. Print Results
        total_trades = winning_trades + losing_trades
        win_rate = (winning_trades / total_trades * 100) if total_trades > 0 else 0
        
        print("="*40)
        print("📈 BACKTEST RESULTS (Last 60 Days)")
        print("="*40)
        print(f"Total Trades Taken : {total_trades}")
        print(f"Winning Trades     : {winning_trades}")
        print(f"Losing Trades      : {losing_trades}")
        print(f"System Win Rate    : {win_rate:.1f}%")
        print(f"Total Net Return   : {total_pnl_percent:.2f}%")
        print("="*40)

    except Exception as e:
        print(f"Backtest failed: {e}")

if __name__ == "__main__":
    run_vwap_backtest("RELIANCE.NS")