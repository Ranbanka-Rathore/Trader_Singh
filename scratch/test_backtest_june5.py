import os
import datetime
import pandas as pd
import numpy as np
from backtester_v3 import PropDeskBacktester

# Force UTF-8 stdout
import sys
if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8')

b = PropDeskBacktester(ticker="NIFTY")
df = b.load_data()

# Calculate indicators exactly like backtester
df['IST_Time'] = df['timestamp'].dt.tz_convert('Asia/Kolkata') if df['timestamp'].dt.tz is not None else df['timestamp']
df['Date'] = df['IST_Time'].dt.date

df['Typical_Price'] = (df['high'] + df['low'] + df['close']) / 3
df['VP'] = df['Typical_Price'] * df['volume']
df['cum_vp'] = df.groupby('Date', observed=True)['VP'].cumsum()
df['cum_vol'] = df.groupby('Date', observed=True)['volume'].cumsum()
df['cum_vol'] = df['cum_vol'].replace(0, 1)
df['VWAP'] = df['cum_vp'] / df['cum_vol']

df['std_20'] = df['close'].rolling(window=20).std()
df['std_20'] = df['std_20'].bfill()
df['Upper_Band'] = df['VWAP'] + (2.0 * df['std_20'])
df['Lower_Band'] = df['VWAP'] - (2.0 * df['std_20'])

df['session_high'] = df.groupby('Date', observed=True)['high'].transform(lambda x: x.cummax().shift(1))
df['session_low'] = df.groupby('Date', observed=True)['low'].transform(lambda x: x.cummin().shift(1))

today_date = datetime.date(2026, 6, 5)
df_today = df[df['Date'] == today_date]

print(f"Step-by-step backtest trace for {today_date}:")
in_trade = False
trade_bias = None

for i in range(len(df)):
    row = df.iloc[i]
    if row['Date'] != today_date:
        continue
        
    prev_row = df.iloc[i-1]
    current_time = row['IST_Time']
    current_price = row['close']
    
    is_eod = (current_time.hour == 15 and current_time.minute >= 15) or (current_time.hour > 15)
    
    # Check triggers if not in trade
    pa_status = "CHOP_ZONE"
    is_bullish_sweep = (row['session_low'] * 0.9985 <= row['low'] < row['session_low']) and current_price > row['session_low']
    is_bearish_sweep = (row['session_high'] < row['high'] <= row['session_high'] * 1.0015) and current_price < row['session_high']
    
    if is_bullish_sweep:
        pa_status = "LIQUIDITY_SWEEP_LONG"
    elif is_bearish_sweep:
        pa_status = "LIQUIDITY_SWEEP_SHORT"
    elif current_price >= row['Upper_Band'] and prev_row['close'] < prev_row['Upper_Band']:
        pa_status = "VWAP_DEVIATION_SHORT"
    elif current_price <= row['Lower_Band'] and prev_row['close'] > prev_row['Lower_Band']:
        pa_status = "VWAP_DEVIATION_LONG"
        
    direction = None
    bullish_triggers = ["VWAP_BULL_CROSS", "LIQUIDITY_SWEEP_LONG", "VWAP_DEVIATION_LONG", "SESSION_HIGH_BREAKOUT"]
    bearish_triggers = ["VWAP_BEAR_CROSS", "LIQUIDITY_SWEEP_SHORT", "VWAP_DEVIATION_SHORT", "SESSION_LOW_BREAKDOWN"]
    
    if pa_status in bullish_triggers: direction = "BULLISH"
    elif pa_status in bearish_triggers: direction = "BEARISH"
    
    if direction or pa_status != "CHOP_ZONE":
        print(f"Time: {current_time.strftime('%H:%M')} | Price: {current_price:.2f} | PA Status: {pa_status} | Direction: {direction} | low: {row['low']} | session_low: {row['session_low']}")
