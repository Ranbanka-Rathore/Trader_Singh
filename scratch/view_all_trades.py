import sys
sys.path.append('c:/Users/ST/Desktop/Agentic_Trader')
import pandas as pd
from scratch.test_strategy_premium import PremiumManagedRunner

runner = PremiumManagedRunner(ticker="NIFTY", use_ml_filter=True)
# Directly access the logic and run it
df = runner.load_data()
conn = runner.db_engine.connect()

df['IST_Time'] = df['timestamp'].dt.tz_convert('Asia/Kolkata') if df['timestamp'].dt.tz is not None else df['timestamp']
df['Date'] = df['IST_Time'].dt.date
df['HourMinute'] = df['IST_Time'].dt.time

df['Typical_Price'] = (df['high'] + df['low'] + df['close']) / 3
df['VP'] = df['Typical_Price'] * df['volume']
df['cum_vp'] = df.groupby('Date', observed=True)['VP'].cumsum()
df['cum_vol'] = df.groupby('Date', observed=True)['volume'].cumsum()
df['cum_vol'] = df['cum_vol'].replace(0, 1)
df['VWAP'] = df['cum_vp'] / df['cum_vol']

df['std_20'] = df['close'].rolling(window=20).std().bfill()
df['Upper_Band'] = df['VWAP'] + (2.0 * df['std_20'])
df['Lower_Band'] = df['VWAP'] - (2.0 * df['std_20'])
df['session_high'] = df.groupby('Date', observed=True)['high'].cummax()
df['session_low'] = df.groupby('Date', observed=True)['low'].cummin()

in_trade = False
trade_bias = None
entry_price = 0.0
entry_time = None
highest_seen = 0.0
lowest_seen = 0.0
leg_1_sell = 0.0
leg_2_buy = 0.0
trades_logged = []

for i in range(50, len(df)):
    row = df.iloc[i]
    prev_row = df.iloc[i-1]
    current_time = row['IST_Time']
    current_price = row['close']
    is_eod = (current_time.hour == 15 and current_time.minute >= 15) or (current_time.hour > 15)
    
    if in_trade:
        strike_width = abs(leg_1_sell - leg_2_buy)
        net_credit = strike_width * 0.25
        
        if trade_bias == "BULLISH":
            highest_seen = max(highest_seen, row['high'])
            price_change = current_price - entry_price
            current_spread_value = net_credit - (price_change * 0.15)
            is_tp = current_spread_value <= net_credit * 0.20
            is_sl = current_spread_value >= net_credit * 2.00
        else:
            lowest_seen = min(lowest_seen, row['low'])
            price_change = entry_price - current_price
            current_spread_value = net_credit - (price_change * 0.15)
            is_tp = current_spread_value <= net_credit * 0.20
            is_sl = current_spread_value >= net_credit * 2.00

        exit_triggered = False
        exit_reason = ""
        realized_pnl_pts = 0.0
        
        if is_eod:
            exit_triggered = True
            exit_reason = "⏰ EOD SQUARE OFF (3:15 PM)"
            realized_pnl_pts = net_credit - current_spread_value
        elif is_tp:
            exit_triggered = True
            exit_reason = "🎯 TAKE PROFIT"
            realized_pnl_pts = net_credit * 0.80
        elif is_sl:
            exit_triggered = True
            exit_reason = "🛑 PREMIUM STOP LOSS"
            realized_pnl_pts = -net_credit
        
        if exit_triggered:
            trades_logged.append({
                "entry_time": entry_time,
                "exit_time": current_time,
                "bias": trade_bias,
                "entry": entry_price,
                "exit": current_price,
                "reason": exit_reason,
                "pnl": round(realized_pnl_pts, 2),
                "win": 1 if realized_pnl_pts >= 0 else 0
            })
            in_trade = False
        continue

    if is_eod or (current_time.hour == 15):
        continue
        
    pa_status = "CHOP_ZONE"
    is_bullish_sweep = row['low'] < row['session_low'] and current_price > row['session_low']
    is_bearish_sweep = row['high'] > row['session_high'] and current_price < row['session_high']
    
    if is_bullish_sweep:
        pa_status = "LIQUIDITY_SWEEP_LONG"
    elif is_bearish_sweep:
        pa_status = "LIQUIDITY_SWEEP_SHORT"
    elif current_price >= row['Upper_Band'] and prev_row['close'] < prev_row['Upper_Band']:
        pa_status = "VWAP_DEVIATION_SHORT"
    elif current_price <= row['Lower_Band'] and prev_row['close'] > prev_row['Lower_Band']:
        pa_status = "VWAP_DEVIATION_LONG"
    elif current_price > row['VWAP'] and prev_row['close'] <= prev_row['VWAP']:
        pa_status = "VWAP_BULL_CROSS"
    elif current_price < row['VWAP'] and prev_row['close'] >= prev_row['VWAP']:
        pa_status = "VWAP_BEAR_CROSS"

    direction = None
    bullish_triggers = ["VWAP_BULL_CROSS", "LIQUIDITY_SWEEP_LONG", "VWAP_DEVIATION_LONG"]
    bearish_triggers = ["VWAP_BEAR_CROSS", "LIQUIDITY_SWEEP_SHORT", "VWAP_DEVIATION_SHORT"]
    
    if pa_status in bullish_triggers: direction = "BULLISH"
    elif pa_status in bearish_triggers: direction = "BEARISH"
    
    if direction:
        naive_time = current_time.replace(tzinfo=None)
        raw_prob = runner.ml_engine.get_approval_score_sync(conn, as_of_timestamp=naive_time)
        ml_score = raw_prob if direction == "BULLISH" else 1.0 - raw_prob
        
        if ml_score >= 0.60:
            interval = 50
            if direction == "BULLISH":
                leg_1_sell = round((current_price - interval) / interval) * interval
                leg_2_buy = leg_1_sell - interval
            else:
                leg_1_sell = round((current_price + interval) / interval) * interval
                leg_2_buy = leg_1_sell + interval
            
            in_trade = True
            trade_bias = direction
            entry_price = current_price
            entry_time = current_time
            highest_seen = row['high']
            lowest_seen = row['low']

conn.close()

print("\n--- ALL NIFTY WITH ML TRADES (EXACT DETAIL) ---")
for idx, t in enumerate(trades_logged):
    print(f"#{idx+1} Entry: {t['entry_time'].strftime('%Y-%m-%d %H:%M')} | Bias: {t['bias']} | Price: {t['entry']} -> {t['exit']} | PnL: {t['pnl']} | Reason: {t['reason']}")
print("Total net return:", sum(t['pnl'] for t in trades_logged))
