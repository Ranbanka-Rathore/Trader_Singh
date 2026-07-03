import sys
sys.path.append('c:/Users/ST/Desktop/Agentic_Trader')
import pandas as pd
from scratch.test_strategy_opt import OptimizedStrategyRunner

class DirectPrinterRunner(OptimizedStrategyRunner):
    def run_and_get_stats(self):
        df = self.load_data()
        if df.empty or len(df) < 100:
            return None
            
        conn = self.db_engine.connect() if self.use_ml_filter else None
            
        df['IST_Time'] = df['timestamp'].dt.tz_convert('Asia/Kolkata') if df['timestamp'].dt.tz is not None else df['timestamp']
        df['Date'] = df['IST_Time'].dt.date
        df['HourMinute'] = df['IST_Time'].dt.time
        
        # VWAP calculation
        df['Typical_Price'] = (df['high'] + df['low'] + df['close']) / 3
        df['VP'] = df['Typical_Price'] * df['volume']
        df['cum_vp'] = df.groupby('Date', observed=True)['VP'].cumsum()
        df['cum_vol'] = df.groupby('Date', observed=True)['volume'].cumsum()
        df['cum_vol'] = df['cum_vol'].replace(0, 1) # Prevent ZeroDivision
        df['VWAP'] = df['cum_vp'] / df['cum_vol']
        
        # Standard deviation bands around VWAP
        df['std_20'] = df['close'].rolling(window=20).std()
        df['std_20'] = df['std_20'].bfill()
        df['Upper_Band'] = df['VWAP'] + (2.0 * df['std_20'])
        df['Lower_Band'] = df['VWAP'] - (2.0 * df['std_20'])
        
        # Session High/Low (running extremes)
        df['session_high'] = df.groupby('Date', observed=True)['high'].cummax()
        df['session_low'] = df.groupby('Date', observed=True)['low'].cummin()
        
        # Double top/bottom markers (local extremes)
        df['Swing_High'] = df['high'].rolling(window=20).max().shift(1)
        df['Swing_Low'] = df['low'].rolling(window=20).min().shift(1)

        # Simulation parameters
        in_trade = False
        trade_bias = None
        entry_price = 0.0
        entry_time = None
        highest_seen = 0.0
        lowest_seen = 0.0
        dynamic_sl = 0.0
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
                if trade_bias == "BULLISH":
                    highest_seen = max(highest_seen, row['high'])
                    profit_pct = (highest_seen - entry_price) / entry_price
                    if profit_pct >= 0.005:
                        dynamic_sl = max(dynamic_sl, entry_price)
                        
                    is_tp = current_price >= entry_price * 1.008
                    is_sl = current_price <= dynamic_sl
                    is_profitable_now = current_price > entry_price
                    
                else: # BEARISH
                    lowest_seen = min(lowest_seen, row['low'])
                    profit_pct = (entry_price - lowest_seen) / entry_price
                    if profit_pct >= 0.005:
                        dynamic_sl = min(dynamic_sl, entry_price)
                        
                    is_tp = current_price <= entry_price * 0.992
                    is_sl = current_price >= dynamic_sl
                    is_profitable_now = current_price < entry_price

                # Check Exits
                exit_triggered = False
                exit_reason = ""
                realized_pnl_pts = 0.0
                
                strike_width = abs(leg_1_sell - leg_2_buy)
                net_credit = strike_width * 0.25
                max_loss = strike_width - net_credit
                
                time_elapsed = current_time - entry_time
                
                if is_eod:
                    exit_triggered = True
                    exit_reason = "⏰ EOD SQUARE OFF (3:15 PM)"
                    realized_pnl_pts = (net_credit * 0.50) if is_profitable_now else -(net_credit * 0.50)
                elif is_tp:
                    exit_triggered = True
                    exit_reason = "🎯 TAKE PROFIT"
                    realized_pnl_pts = net_credit * 0.80
                elif is_sl:
                    exit_triggered = True
                    if dynamic_sl == leg_1_sell:
                        exit_reason = "🛑 HARD STOP LOSS"
                        realized_pnl_pts = -max_loss
                    else:
                        exit_reason = "🛡️ TRAILING STOP (Breakeven)"
                        realized_pnl_pts = 0.0
                
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
                if self.ticker in ["BANKNIFTY", "SENSEX"]:
                    interval = 100
                elif self.ticker in ["NIFTY", "FINNIFTY"]:
                    interval = 50
                else:
                    interval = 10
                    
                if self.use_ml_filter:
                    naive_time = current_time.replace(tzinfo=None)
                    raw_prob = self.ml_engine.get_approval_score_sync(conn, as_of_timestamp=naive_time)
                    ml_score = raw_prob if direction == "BULLISH" else 1.0 - raw_prob
                    if ml_score < 0.55:
                        direction = None
                        
                if direction:
                    if direction == "BULLISH":
                        leg_1_sell = round((current_price - interval) / interval) * interval
                        leg_2_buy = leg_1_sell - interval
                    else:
                        leg_1_sell = round((current_price + interval) / interval) * interval
                        leg_2_buy = leg_1_sell + interval
                    dynamic_sl = leg_1_sell
                    
                    in_trade = True
                    trade_bias = direction
                    entry_price = current_price
                    entry_time = current_time
                    highest_seen = row['high']
                    lowest_seen = row['low']

        if conn:
            conn.close()

        print("\n=== ALL TRADES TAKEN ===")
        for t in trades_logged:
            print(f"Time: {t['entry_time']} -> {t['exit_time']} | Bias: {t['bias']} | Entry: {t['entry_price']} | Exit: {t['exit_price']} | PnL: {t['pnl_pts']} | Reason: {t['exit_reason']}")
        print("========================\n")
        return trades_logged

runner = DirectPrinterRunner(ticker="NIFTY", use_ml_filter=True)
runner.run_and_get_stats()
