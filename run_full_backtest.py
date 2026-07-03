import os
import sys
import pandas as pd
from backtester_v3 import PropDeskBacktester

# Force UTF-8 stdout mapping for Windows emojis
sys.stdout.reconfigure(encoding='utf-8')

tickers = ["NIFTY", "BANKNIFTY", "RELIANCE", "HDFCBANK"]
results = []

print("=============================================================")
print("🌐 RUNNING INTEGRATED PROP-DESK STRATEGY BACKTESTS")
print("=============================================================")

# Let's write a customized runner that extracts the stats directly
class CustomRunner(PropDeskBacktester):
    def __init__(self, ticker, timeframe="5m", use_ml_filter=False):
        super().__init__(ticker=ticker, timeframe=timeframe)
        self.use_ml_filter = use_ml_filter
        if self.use_ml_filter:
            from ml_approval_engine import MLApprovalEngine
            from sqlalchemy import create_engine
            self.ml_engine = MLApprovalEngine(ticker=ticker, timeframe=1)
            self.db_engine = create_engine(self.db_url)

    def run_and_get_stats(self):
        df = self.load_data()
        if df.empty or len(df) < 100:
            return None
            
        conn = None
        if self.use_ml_filter:
            conn = self.db_engine.connect()
            
        # Calculate daily groups for VWAP and Session High/Low
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
        
        # Session High/Low (running extremes, shifted by 1 to exclude current candle)
        df['session_high'] = df.groupby('Date', observed=True)['high'].transform(lambda x: x.cummax().shift(1))
        df['session_low'] = df.groupby('Date', observed=True)['low'].transform(lambda x: x.cummin().shift(1))
        
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
                strike_width = abs(leg_1_sell - leg_2_buy)
                is_index = self.ticker in ["NIFTY", "BANKNIFTY", "FINNIFTY", "SENSEX", "NSEI", "NSEBANK"]
                credit_ratio = 0.15 if is_index else 0.20
                net_credit = strike_width * credit_ratio
                
                has_reached_be_lock = False
                if trade_bias == "BULLISH":
                    highest_seen = max(highest_seen, row['high'])
                    lowest_seen = min(lowest_seen, row['low'])
                    price_change = current_price - entry_price
                    current_spread_value = net_credit - (price_change * 0.15)
                    
                    has_reached_be_lock = (highest_seen - entry_price) * 0.15 >= net_credit * 0.30
                    is_tp = current_spread_value <= net_credit * 0.20
                    is_sl = current_spread_value >= (net_credit if has_reached_be_lock else net_credit * 1.30)
                else: # BEARISH
                    lowest_seen = min(lowest_seen, row['low'])
                    highest_seen = max(highest_seen, row['high'])
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
            # Sweeps & Deviations checks
            # Enforce 0.15% limit on sweeps (low/high deviations)
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
            
            # --- V8 Safety Filters Alignment ---
            if direction:
                # 1. Market Open Stabilization
                is_stabilizing = (current_time.hour == 9 and 15 <= current_time.minute <= 30)
                pcr_val = float(row['pcr']) if ('pcr' in row and not pd.isna(row['pcr'])) else 1.0
                
                if is_stabilizing and pcr_val == 1.0:
                    direction = None
                    
                # 2. PCR Extreme Range Skew Check (Capital Guard)
                if direction and 'pcr' in row and not pd.isna(row['pcr']) and pcr_val != 1.0:
                    if pcr_val < 0.70 or pcr_val > 1.80:
                        direction = None
                        
                # 3. GEX Volatility Check
                gex_val = float(row['total_gex']) if ('total_gex' in row and not pd.isna(row['total_gex'])) else 0.0
                gex_min_threshold = -200000000.0
                if direction and 'total_gex' in row and not pd.isna(row['total_gex']) and gex_val < gex_min_threshold:
                    direction = None

            if direction:
                if self.use_ml_filter:
                    naive_time = current_time.replace(tzinfo=None)
                    raw_prob = self.ml_engine.get_approval_score_sync(conn, as_of_timestamp=naive_time)
                    ml_score = raw_prob if direction == "BULLISH" else 1.0 - raw_prob
                    ml_cutoff = 0.65
                    decision = "APPROVED" if ml_score >= ml_cutoff else "BLOCKED"
                    print(f"   [ML Guard] {self.ticker} | Time: {current_time} | Bias: {direction} | ML Confidence: {ml_score:.4f} | Decision: {decision}")
                    if ml_score < ml_cutoff:
                        direction = None
                        
                if direction:
                    if self.ticker in ["BANKNIFTY", "SENSEX"]:
                        interval = 100
                    elif self.ticker in ["NIFTY", "FINNIFTY"]:
                        interval = 50
                    else:
                        interval = 10
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

        if not trades_logged:
            return {
                "Ticker": f"{self.ticker} (with ML)" if self.use_ml_filter else self.ticker,
                "Total_Trades": 0,
                "Win_Rate": "0.00%",
                "Net_Points": 0.0,
                "Net_PnL": 0.0,
                "Profit_Factor": 0.0
            }
            
        df_tr = pd.DataFrame(trades_logged)
        wins = df_tr['win'].sum()
        win_rate = (wins / len(df_tr)) * 100
        total_pnl = df_tr['pnl_pts'].sum()
        lot_size = self.lot_sizes.get(self.ticker, 1)
        net_pnl_val = total_pnl * lot_size
        
        gross_profits = df_tr[df_tr['pnl_pts'] > 0]['pnl_pts'].sum()
        gross_losses = abs(df_tr[df_tr['pnl_pts'] < 0]['pnl_pts'].sum())
        profit_factor = (gross_profits / gross_losses) if gross_losses > 0 else float('inf')
        
        # Print individual report
        self.print_report(trades_logged)
        
        return {
            "Ticker": f"{self.ticker} (with ML)" if self.use_ml_filter else self.ticker,
            "Total_Trades": len(df_tr),
            "Win_Rate": f"{win_rate:.2f}%",
            "Net_Points": round(total_pnl, 2),
            "Net_PnL": round(net_pnl_val, 2),
            "Profit_Factor": round(profit_factor, 2)
        }

summary_list = []

# 1. Run NIFTY without ML
runner_nifty = CustomRunner(ticker="NIFTY", use_ml_filter=False)
stats_nifty = runner_nifty.run_and_get_stats()
if stats_nifty:
    summary_list.append(stats_nifty)

# 2. Run NIFTY with ML
runner_nifty_ml = CustomRunner(ticker="NIFTY", use_ml_filter=True)
stats_nifty_ml = runner_nifty_ml.run_and_get_stats()
if stats_nifty_ml:
    summary_list.append(stats_nifty_ml)

# 3. Run BANKNIFTY without ML
runner_bn = CustomRunner(ticker="BANKNIFTY", use_ml_filter=False)
stats_bn = runner_bn.run_and_get_stats()
if stats_bn:
    summary_list.append(stats_bn)

# 4. Run BANKNIFTY with ML
runner_bn_ml = CustomRunner(ticker="BANKNIFTY", use_ml_filter=True)
stats_bn_ml = runner_bn_ml.run_and_get_stats()
if stats_bn_ml:
    summary_list.append(stats_bn_ml)

# 5. Run Stocks
for ticker in ["RELIANCE", "HDFCBANK"]:
    runner = CustomRunner(ticker=ticker, use_ml_filter=False)
    stats = runner.run_and_get_stats()
    if stats:
        summary_list.append(stats)

if summary_list:
    df_summary = pd.DataFrame(summary_list)
    print("\n=============================================================")
    print("📊 MASTER PERFORMANCE SUMMARY ACROSS ALL TICKERS")
    print("=============================================================")
    print(df_summary.to_string(index=False))
    print("=============================================================")
else:
    print("\n❌ No backtest statistics were generated. Check database data.")
