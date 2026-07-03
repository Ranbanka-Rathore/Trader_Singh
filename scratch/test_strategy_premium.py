import os
import sys
import pandas as pd
import numpy as np
from scratch.test_strategy_opt import OptimizedStrategyRunner

class PremiumManagedRunner(OptimizedStrategyRunner):
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
                net_credit = strike_width * 0.25 # Collect 25% of strike width (OTM spread)
                
                # Dynamic spread value estimation using delta approximation
                # Spread delta is typically around 0.15. If spot moves against us, spread value rises.
                if trade_bias == "BULLISH":
                    highest_seen = max(highest_seen, row['high'])
                    # If spot falls, we lose premium
                    price_change = current_price - entry_price
                    current_spread_value = net_credit - (price_change * 0.15)
                    
                    is_tp = current_spread_value <= net_credit * 0.20 # Captured 80% of premium
                    is_sl = current_spread_value >= net_credit * 2.00 # Spread doubled (stop out at 2x credit)
                    
                else: # BEARISH
                    lowest_seen = min(lowest_seen, row['low'])
                    # If spot rises, we lose premium
                    price_change = entry_price - current_price
                    current_spread_value = net_credit - (price_change * 0.15)
                    
                    is_tp = current_spread_value <= net_credit * 0.20
                    is_sl = current_spread_value >= net_credit * 2.00

                # Check Exits
                exit_triggered = False
                exit_reason = ""
                realized_pnl_pts = 0.0
                
                if is_eod:
                    exit_triggered = True
                    exit_reason = "⏰ EOD SQUARE OFF (3:15 PM)"
                    # EOD close is estimated at current spread value
                    realized_pnl_pts = net_credit - current_spread_value
                elif is_tp:
                    exit_triggered = True
                    exit_reason = "🎯 TAKE PROFIT (80% Premium)"
                    realized_pnl_pts = net_credit * 0.80
                elif is_sl:
                    exit_triggered = True
                    exit_reason = "🛑 PREMIUM STOP LOSS (Double Premium)"
                    # We bought back at 2x, so loss is exactly 1x net credit
                    realized_pnl_pts = -net_credit
                
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
                    if ml_score < 0.60: # Threshold of 0.60
                        direction = None
                        
                if direction:
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

        if conn:
            conn.close()

        print(f"\n=== {self.ticker} {"(WITH ML)" if self.use_ml_filter else "(NO ML)"} TRADES TAKEN ===")
        for t in trades_logged:
            print(f"Time: {t['entry_time']} -> {t['exit_time']} | Bias: {t['bias']} | Entry: {t['entry_price']} | Exit: {t['exit_price']} | PnL: {t['pnl_pts']} | Reason: {t['exit_reason']}")
        print("===================================\n")
        return trades_logged

if __name__ == "__main__":
    summary_list = []
    
    # 1. Run NIFTY without ML
    runner = PremiumManagedRunner(ticker="NIFTY", use_ml_filter=False)
    stats = runner.run_and_get_stats()
    if stats:
        summary_list.append(stats)
        
    # 2. Run NIFTY with ML
    runner_ml = PremiumManagedRunner(ticker="NIFTY", use_ml_filter=True)
    stats_ml = runner_ml.run_and_get_stats()
    if stats_ml:
        summary_list.append(stats_ml)
        
    # 3. Run BANKNIFTY without ML
    runner_bn = PremiumManagedRunner(ticker="BANKNIFTY", use_ml_filter=False)
    stats_bn = runner_bn.run_and_get_stats()
    if stats_bn:
        summary_list.append(stats_bn)
        
    # 4. Run BANKNIFTY with ML
    runner_bn_ml = PremiumManagedRunner(ticker="BANKNIFTY", use_ml_filter=True)
    stats_bn_ml = runner_bn_ml.run_and_get_stats()
    if stats_bn_ml:
        summary_list.append(stats_bn_ml)
        
    if summary_list:
        df_summary = pd.DataFrame(summary_list)
        print("\n=============================================================")
        print("📊 PREMIUM-MANAGED STRATEGY PERFORMANCE COMPARISON")
        print("=============================================================")
        print(df_summary.to_string(index=False))
        print("=============================================================")
