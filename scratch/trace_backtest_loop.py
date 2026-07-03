import os
import sys
import datetime
import pandas as pd
import numpy as np

# Add parent dir to path
sys.path.append(os.getcwd())
from backtester_v3 import PropDeskBacktester

b = PropDeskBacktester(ticker="NIFTY")
df = b.load_data()

# Overwrite run_simulation to add deep print tracing for today (June 5, 2026)
def run_simulation_trace(df):
    # Calculate daily groups for VWAP and Session High/Low
    df['IST_Time'] = df['timestamp'].dt.tz_convert('Asia/Kolkata') if df['timestamp'].dt.tz is not None else df['timestamp']
    df['Date'] = df['IST_Time'].dt.date
    df['HourMinute'] = df['IST_Time'].dt.time
    
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
    
    df['Swing_High'] = df['high'].rolling(window=20).max().shift(1)
    df['Swing_Low'] = df['low'].rolling(window=20).min().shift(1)

    in_trade = False
    trade_bias = None
    entry_price = 0.0
    entry_time = None
    highest_seen = 0.0
    lowest_seen = 0.0
    leg_1_sell = 0.0
    leg_2_buy = 0.0
    
    trades_logged = []
    today_date = datetime.date(2026, 6, 5)
    
    for i in range(50, len(df)):
        row = df.iloc[i]
        prev_row = df.iloc[i-1]
        current_time = row['IST_Time']
        current_price = row['close']
        
        is_today = current_time.date() == today_date
        is_eod = (current_time.hour == 15 and current_time.minute >= 15) or (current_time.hour > 15)
        
        if in_trade:
            strike_width = abs(leg_1_sell - leg_2_buy)
            net_credit = strike_width * 0.25
            
            has_reached_be_lock = False
            if trade_bias == "BULLISH":
                highest_seen = max(highest_seen, row['high'])
                price_change = current_price - entry_price
                current_spread_value = net_credit - (price_change * 0.15)
                has_reached_be_lock = (highest_seen - entry_price) * 0.15 >= net_credit * 0.30
                is_tp = current_spread_value <= net_credit * 0.20
                is_sl = current_spread_value >= (net_credit if has_reached_be_lock else net_credit * 1.30)
            else:
                lowest_seen = min(lowest_seen, row['low'])
                price_change = entry_price - current_price
                current_spread_value = net_credit - (price_change * 0.15)
                has_reached_be_lock = (entry_price - lowest_seen) * 0.15 >= net_credit * 0.30
                is_tp = current_spread_value <= net_credit * 0.20
                is_sl = current_spread_value >= (net_credit if has_reached_be_lock else net_credit * 1.30)

            exit_triggered = False
            exit_reason = ""
            realized_pnl_pts = 0.0
            
            if is_eod:
                exit_triggered = True
                exit_reason = "⏰ EOD SQUARE OFF (3:15 PM)"
                realized_pnl_pts = net_credit - current_spread_value
            elif is_tp:
                exit_triggered = True
                exit_reason = "🎯 TAKE PROFIT (80% Premium)"
                realized_pnl_pts = net_credit * 0.80
            elif is_sl:
                exit_triggered = True
                if has_reached_be_lock:
                    exit_reason = "🛡️ TRAILING STOP (Breakeven)"
                    realized_pnl_pts = 0.0
                else:
                    exit_reason = "🛑 PREMIUM STOP LOSS (Tight 30% SL)"
                    realized_pnl_pts = -net_credit * 0.30
            
            if is_today:
                print(f"[TRADE STATUS] {current_time.strftime('%H:%M')} | Price: {current_price:.2f} | Spread: {current_spread_value:.2f} | is_sl: {is_sl} | is_tp: {is_tp} | has_reached_be_lock: {has_reached_be_lock} | exit_triggered: {exit_triggered}")
                
            if exit_triggered:
                trades_logged.append({
                    "entry_time": entry_time,
                    "exit_time": current_time,
                    "bias": trade_bias,
                    "entry_price": entry_price,
                    "exit_price": current_price,
                    "exit_reason": exit_reason,
                    "pnl_pts": round(realized_pnl_pts, 2),
                    "win": 1 if realized_pnl_pts >= 0 else 0
                })
                in_trade = False
                if is_today:
                    print(f"❌ EXIT LOGGED: {current_time.strftime('%H:%M')} | Reason: {exit_reason} | PnL: {realized_pnl_pts:.2f}")
            continue

        if is_eod or (current_time.hour == 15):
            continue
            
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
        elif current_price <= row['session_low']:
            pa_status = "SESSION_LOW_BREAKDOWN"
        elif current_price >= row['session_high']:
            pa_status = "SESSION_HIGH_BREAKOUT"
        elif current_price > row['VWAP'] and prev_row['close'] <= prev_row['VWAP']:
            pa_status = "VWAP_BULL_CROSS"
        elif current_price < row['VWAP'] and prev_row['close'] >= prev_row['VWAP']:
            pa_status = "VWAP_BEAR_CROSS"

        direction = None
        bullish_triggers = ["VWAP_BULL_CROSS", "LIQUIDITY_SWEEP_LONG", "VWAP_DEVIATION_LONG", "SESSION_HIGH_BREAKOUT"]
        bearish_triggers = ["VWAP_BEAR_CROSS", "LIQUIDITY_SWEEP_SHORT", "VWAP_DEVIATION_SHORT", "SESSION_LOW_BREAKDOWN"]
        
        if pa_status in bullish_triggers: direction = "BULLISH"
        elif pa_status in bearish_triggers: direction = "BEARISH"
        
        if is_today and (direction or pa_status != "CHOP_ZONE"):
            print(f"[SCAN] {current_time.strftime('%H:%M')} | Price: {current_price:.2f} | PA Status: {pa_status} | Direction: {direction} | low: {row['low']} | session_low: {row['session_low']}")
            
        if direction:
            interval = 50
            if direction == "BULLISH":
                leg_1_sell = round((current_price * 0.99) / interval) * interval
                leg_2_buy = round((current_price * 0.98) / interval) * interval
                if leg_2_buy >= leg_1_sell: leg_2_buy = leg_1_sell - interval
            else:
                leg_1_sell = round((current_price * 1.01) / interval) * interval
                leg_2_buy = round((current_price * 1.02) / interval) * interval
                if leg_2_buy <= leg_1_sell: leg_2_buy = leg_1_sell + interval
            
            in_trade = True
            trade_bias = direction
            entry_price = current_price
            entry_time = current_time
            highest_seen = row['high']
            lowest_seen = row['low']
            if is_today:
                print(f"🚀 ENTRY EXECUTED: {current_time.strftime('%H:%M')} | Bias: {direction} | Entry Price: {entry_price:.2f} | Sell Strike: {leg_1_sell} | Buy Strike: {leg_2_buy}")

b.run_simulation = run_simulation_trace
b.run_simulation(df)
