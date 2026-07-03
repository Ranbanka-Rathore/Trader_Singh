import asyncio
import os
import sys
import pandas as pd
import numpy as np
import datetime
from decimal import Decimal
from sqlalchemy import create_engine

# Force UTF-8 stdout mapping for Windows emojis
sys.stdout.reconfigure(encoding='utf-8')

def get_db_engine():
    db_user = os.getenv("DB_USER", "trader")
    db_pass = os.getenv("DB_PASSWORD", "institutional_grade_password")
    db_port = os.getenv("DB_PORT", "5432")
    db_name = os.getenv("DB_NAME", "agentic_trader")
    
    def _get_wsl_ip() -> str:
        import subprocess
        try:
            result = subprocess.run(
                ["wsl", "-d", "Ubuntu", "hostname", "-I"],
                capture_output=True, text=True, timeout=5
            )
            if result.returncode == 0:
                ip = result.stdout.strip().split()[0]
                if ip and ip != "127.0.0.1":
                    return ip
        except Exception:
            pass
        return None

    def _resolve_db_host() -> str:
        import socket
        try:
            s = socket.create_connection(("127.0.0.1", int(db_port)), timeout=1)
            s.close()
            return "127.0.0.1"
        except (ConnectionRefusedError, OSError):
            pass
        wsl_ip = _get_wsl_ip()
        return wsl_ip if wsl_ip else "127.0.0.1"

    db_host = _resolve_db_host()
    db_url = f"postgresql://{db_user}:{db_pass}@{db_host}:{db_port}/{db_name}"
    return create_engine(db_url, connect_args={'connect_timeout': 3})

def load_data():
    engine = get_db_engine()
    query = """
        SELECT c.timestamp, c.open, c.high, c.low, c.close, c.volume, m.pcr, m.total_gex
        FROM candles c
        LEFT JOIN LATERAL (
            SELECT pcr, total_gex
            FROM market_indicators
            WHERE ticker = c.ticker 
              AND timeframe = 1 
              AND timestamp <= c.timestamp
            ORDER BY timestamp DESC
            LIMIT 1
        ) m ON TRUE
        WHERE c.ticker = 'NIFTY' AND c.timeframe = '5m'
        ORDER BY c.timestamp ASC
    """
    df = pd.read_sql(query, engine)
    df['timestamp'] = pd.to_datetime(df['timestamp'])
    
    # Ensure float types
    for col in ['open', 'high', 'low', 'close', 'pcr', 'total_gex']:
        if col in df.columns:
            df[col] = df[col].astype(float)
    df['volume'] = df['volume'].astype(int)
    return df

def calculate_indicators(df):
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
    return df

def run_backtest_strategy(df, strategy_mode, target_date, lot_size=25):
    in_trade = False
    trade_bias = None
    entry_price = 0.0
    entry_time = None
    highest_seen = 0.0
    lowest_seen = 0.0
    
    net_premium = 0.0 # Stored positive
    delta = 0.15
    if strategy_mode == "DEBIT_SPREAD":
        delta = 0.30
    elif strategy_mode == "NAKED_OPTIONS":
        delta = 0.50
    elif strategy_mode == "IRON_CONDOR":
        delta = 0.05
    elif strategy_mode == "LONG_STRANGLE":
        delta = 0.20
    elif strategy_mode == "CALENDAR_SPREAD":
        delta = 0.05
    elif strategy_mode == "RATIO_SPREAD":
        delta = 0.15
    elif strategy_mode == "LONG_STRADDLE":
        delta = 0.10
    elif strategy_mode == "IRON_BUTTERFLY":
        delta = 0.08
    elif strategy_mode == "DIAGONAL_SPREAD":
        delta = 0.20
    elif strategy_mode == "DELTA_NEUTRAL":
        delta = 0.02
        
    trades_logged = []
    
    # Find index range of target date
    target_indices = df[df['Date'] == target_date].index
    if len(target_indices) == 0:
        return []
        
    start_idx = target_indices[0]
    end_idx = target_indices[-1]
    
    for i in range(start_idx, end_idx + 1):
        row = df.iloc[i]
        prev_row = df.iloc[i-1]
        current_time = row['IST_Time']
        current_price = row['close']
        
        is_eod = (current_time.hour == 15 and current_time.minute >= 15) or (current_time.hour > 15)
        
        if in_trade:
            # Update extremes
            highest_seen = max(highest_seen, row['high'])
            lowest_seen = min(lowest_seen, row['low'])
            
            # Hours elapsed since entry
            hours_elapsed = (current_time - entry_time).total_seconds() / 3600.0
            
            realized_pnl_pts = 0.0
            exit_triggered = False
            exit_reason = ""
            
            if strategy_mode == "IRON_CONDOR":
                price_change = abs(current_price - entry_price)
                decay = net_premium * 0.08 * hours_elapsed
                current_value = net_premium + (price_change * delta) - decay
                
                is_tp = current_value <= net_premium * 0.40 # 60% decay captured
                is_sl = current_value >= net_premium * 2.0  # 100% loss
                
                if is_eod:
                    exit_triggered = True
                    exit_reason = "EOD"
                    realized_pnl_pts = net_premium - current_value
                elif is_tp:
                    exit_triggered = True
                    exit_reason = "TP (60% Decay)"
                    realized_pnl_pts = net_premium * 0.60
                elif is_sl:
                    exit_triggered = True
                    exit_reason = "SL (100%)"
                    realized_pnl_pts = -net_premium * 1.0
                    
            elif strategy_mode == "IRON_BUTTERFLY":
                price_change = abs(current_price - entry_price)
                decay = net_premium * 0.10 * hours_elapsed
                current_value = net_premium + (price_change * delta) - decay
                
                is_tp = current_value <= net_premium * 0.50 # 50% decay captured
                is_sl = current_value >= net_premium * 1.80  # 80% loss
                
                if is_eod:
                    exit_triggered = True
                    exit_reason = "EOD"
                    realized_pnl_pts = net_premium - current_value
                elif is_tp:
                    exit_triggered = True
                    exit_reason = "TP (50% Decay)"
                    realized_pnl_pts = net_premium * 0.50
                elif is_sl:
                    exit_triggered = True
                    exit_reason = "SL (80%)"
                    realized_pnl_pts = -net_premium * 0.80
                    
            elif strategy_mode == "DELTA_NEUTRAL":
                price_change = abs(current_price - entry_price)
                decay = net_premium * 0.10 * hours_elapsed
                current_value = net_premium + (price_change * delta) - decay
                
                is_tp = current_value <= net_premium * 0.50 # 50% decay captured
                is_sl = current_value >= net_premium * 1.80  # 80% loss
                
                if is_eod:
                    exit_triggered = True
                    exit_reason = "EOD"
                    realized_pnl_pts = net_premium - current_value
                elif is_tp:
                    exit_triggered = True
                    exit_reason = "TP (50% Decay)"
                    realized_pnl_pts = net_premium * 0.50
                elif is_sl:
                    exit_triggered = True
                    exit_reason = "SL (80%)"
                    realized_pnl_pts = -net_premium * 0.80
                    
            elif strategy_mode == "LONG_STRANGLE":
                price_change = abs(current_price - entry_price)
                decay = net_premium * 0.05 * hours_elapsed
                current_value = net_premium + (price_change * delta) - decay
                
                is_tp = current_value >= net_premium * 1.50 # 50% gain
                is_sl = current_value <= net_premium * 0.60 # 40% loss
                
                if is_eod:
                    exit_triggered = True
                    exit_reason = "EOD"
                    realized_pnl_pts = current_value - net_premium
                elif is_tp:
                    exit_triggered = True
                    exit_reason = "TP (50% Gain)"
                    realized_pnl_pts = net_premium * 0.50
                elif is_sl:
                    exit_triggered = True
                    exit_reason = "SL (40%)"
                    realized_pnl_pts = -net_premium * 0.40
                    
            elif strategy_mode == "LONG_STRADDLE":
                price_change = abs(current_price - entry_price)
                decay = net_premium * 0.06 * hours_elapsed
                current_value = net_premium + (price_change * delta) - decay
                
                is_tp = current_value >= net_premium * 1.40 # 40% gain
                is_sl = current_value <= net_premium * 0.70 # 30% loss
                
                if is_eod:
                    exit_triggered = True
                    exit_reason = "EOD"
                    realized_pnl_pts = current_value - net_premium
                elif is_tp:
                    exit_triggered = True
                    exit_reason = "TP (40% Gain)"
                    realized_pnl_pts = net_premium * 0.40
                elif is_sl:
                    exit_triggered = True
                    exit_reason = "SL (30%)"
                    realized_pnl_pts = -net_premium * 0.30
                    
            elif strategy_mode == "CALENDAR_SPREAD":
                price_change = abs(current_price - entry_price)
                appreciation = net_premium * 0.08 * hours_elapsed
                loss_from_delta = price_change * delta
                current_value = net_premium + appreciation - loss_from_delta
                
                is_tp = current_value >= net_premium * 1.35 # 35% gain
                is_sl = current_value <= net_premium * 0.75 # 25% loss
                
                if is_eod:
                    exit_triggered = True
                    exit_reason = "EOD"
                    realized_pnl_pts = current_value - net_premium
                elif is_tp:
                    exit_triggered = True
                    exit_reason = "TP (35% Gain)"
                    realized_pnl_pts = net_premium * 0.35
                elif is_sl:
                    exit_triggered = True
                    exit_reason = "SL (25%)"
                    realized_pnl_pts = -net_premium * 0.25
                    
            elif strategy_mode == "DIAGONAL_SPREAD":
                price_change = abs(current_price - entry_price)
                appreciation = net_premium * 0.06 * hours_elapsed
                loss_from_delta = price_change * delta
                current_value = net_premium + appreciation - loss_from_delta
                
                is_tp = current_value >= net_premium * 1.40 # 40% gain
                is_sl = current_value <= net_premium * 0.75 # 25% loss
                
                if is_eod:
                    exit_triggered = True
                    exit_reason = "EOD"
                    realized_pnl_pts = current_value - net_premium
                elif is_tp:
                    exit_triggered = True
                    exit_reason = "TP (40% Gain)"
                    realized_pnl_pts = net_premium * 0.40
                elif is_sl:
                    exit_triggered = True
                    exit_reason = "SL (25%)"
                    realized_pnl_pts = -net_premium * 0.25
                    
            elif strategy_mode == "RATIO_SPREAD":
                price_change = (current_price - entry_price) if trade_bias == "BULLISH" else (entry_price - current_price)
                if price_change <= 50.0:
                    current_value = net_premium - (price_change * delta)
                else:
                    current_value = net_premium - (50.0 * delta) + ((price_change - 50.0) * 0.35)
                    
                is_tp = current_value <= net_premium * 0.20 # 80% decay captured
                is_sl = current_value >= net_premium * 2.50 # 150% premium increase
                
                if is_eod:
                    exit_triggered = True
                    exit_reason = "EOD"
                    realized_pnl_pts = net_premium - current_value
                elif is_tp:
                    exit_triggered = True
                    exit_reason = "TP (80% Decay)"
                    realized_pnl_pts = net_premium * 0.80
                elif is_sl:
                    exit_triggered = True
                    exit_reason = "SL (150%)"
                    realized_pnl_pts = -net_premium * 1.50
                    
            elif strategy_mode == "CREDIT_SPREAD":
                price_change = (current_price - entry_price) if trade_bias == "BULLISH" else (entry_price - current_price)
                current_value = net_premium - (price_change * delta)
                
                # Trailing Breakeven Lock trigger: 30% favorable move
                max_fav_change = (highest_seen - entry_price) if trade_bias == "BULLISH" else (entry_price - lowest_seen)
                has_reached_be_lock = (max_fav_change * delta) >= (net_premium * 0.30)
                
                is_tp = current_value <= net_premium * 0.20 # 80% decay captured
                is_sl = current_value >= (net_premium if has_reached_be_lock else net_premium * 2.0) # 100% stop loss
                
                if is_eod:
                    exit_triggered = True
                    exit_reason = "EOD"
                    realized_pnl_pts = net_premium - current_value
                elif is_tp:
                    exit_triggered = True
                    exit_reason = "TP (80% Decay)"
                    realized_pnl_pts = net_premium * 0.80
                elif is_sl:
                    exit_triggered = True
                    if has_reached_be_lock:
                        exit_reason = "BE Trail"
                        realized_pnl_pts = 0.0
                    else:
                        exit_reason = "SL (100%)"
                        realized_pnl_pts = -net_premium * 1.0
                        
            else: # DEBIT_SPREAD or NAKED_OPTIONS
                price_change = (current_price - entry_price) if trade_bias == "BULLISH" else (entry_price - current_price)
                current_value = net_premium + (price_change * delta)
                
                # Trailing Breakeven Lock trigger: 30% favorable move
                max_fav_change = (highest_seen - entry_price) if trade_bias == "BULLISH" else (entry_price - lowest_seen)
                has_reached_be_lock = (max_fav_change * delta) >= (net_premium * 0.30)
                
                is_tp = current_value >= net_premium * 1.50 # 50% appreciation
                is_sl = current_value <= (net_premium if has_reached_be_lock else net_premium * 0.70) # 30% loss
                
                if is_eod:
                    exit_triggered = True
                    exit_reason = "EOD"
                    realized_pnl_pts = current_value - net_premium
                elif is_tp:
                    exit_triggered = True
                    exit_reason = "TP (50% Gain)"
                    realized_pnl_pts = net_premium * 0.50
                elif is_sl:
                    exit_triggered = True
                    if has_reached_be_lock:
                        exit_reason = "BE Trail"
                        realized_pnl_pts = 0.0
                    else:
                        exit_reason = "SL (30%)"
                        realized_pnl_pts = -net_premium * 0.30
            
            if exit_triggered:
                # Estimate broker charges & taxes per trade (flat options brokerage + STT & turnover charges)
                if strategy_mode in ["NAKED_OPTIONS"]:
                    charges = 60.0
                elif strategy_mode in ["IRON_CONDOR", "IRON_BUTTERFLY"]:
                    charges = 240.0
                elif strategy_mode in ["RATIO_SPREAD"]:
                    charges = 180.0
                elif strategy_mode == "DELTA_NEUTRAL":
                    charges = 350.0  # Higher due to dynamic hedging rebalances
                else: # 2-leg spreads / strangles / straddles
                    charges = 120.0
                    
                # Capital Deployment (Margin or Premium Paid)
                if strategy_mode == "CREDIT_SPREAD":
                    capital = 35000.0
                elif strategy_mode == "IRON_CONDOR":
                    capital = 50000.0
                elif strategy_mode == "RATIO_SPREAD":
                    capital = 120000.0
                elif strategy_mode == "IRON_BUTTERFLY":
                    capital = 50000.0
                elif strategy_mode == "DELTA_NEUTRAL":
                    capital = 180000.0
                else: # Debit strategies: capital is premium paid
                    capital = net_premium * lot_size

                trades_logged.append({
                    "entry_time": entry_time,
                    "exit_time": current_time,
                    "bias": trade_bias,
                    "entry_price": entry_price,
                    "exit_price": current_price,
                    "exit_reason": exit_reason,
                    "pnl_pts": realized_pnl_pts,
                    "pnl_rupees": realized_pnl_pts * lot_size,
                    "win": 1 if realized_pnl_pts >= 0 else 0,
                    "charges": charges,
                    "capital": capital
                })
                in_trade = False
            continue
            
        # Skip entries outside normal market hours (09:15 AM to 03:00 PM IST)
        if current_time.time() < datetime.time(9, 15) or current_time.time() >= datetime.time(15, 0):
            continue
            
        # Entry logic
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
            in_trade = True
            trade_bias = direction
            entry_price = current_price
            entry_time = current_time
            highest_seen = row['high']
            lowest_seen = row['low']
            
            # Setup premiums based on strategy mode
            if strategy_mode == "CREDIT_SPREAD":
                net_premium = 50.0 * 0.15 # strike width (50) * 0.15 = 7.5
            elif strategy_mode == "DEBIT_SPREAD":
                net_premium = 50.0 * 0.40 # strike width (50) * 0.40 = 20.0
            elif strategy_mode == "NAKED_OPTIONS":
                net_premium = entry_price * 0.008 # 0.8% of spot
            elif strategy_mode == "IRON_CONDOR":
                net_premium = 50.0 * 0.30 # strike width (50) * 0.30 = 15.0
            elif strategy_mode == "LONG_STRANGLE":
                net_premium = entry_price * 0.01
            elif strategy_mode == "CALENDAR_SPREAD":
                net_premium = entry_price * 0.004
            elif strategy_mode == "RATIO_SPREAD":
                net_premium = 50.0 * 0.05
            elif strategy_mode == "LONG_STRADDLE":
                net_premium = entry_price * 0.018
            elif strategy_mode == "IRON_BUTTERFLY":
                net_premium = 50.0 * 0.45
            elif strategy_mode == "DIAGONAL_SPREAD":
                net_premium = entry_price * 0.008
            elif strategy_mode == "DELTA_NEUTRAL":
                net_premium = entry_price * 0.08
                
    return trades_logged

def analyze_trades(trades, strategy_name):
    if not trades:
        return {
            "Strategy": strategy_name,
            "Total Trades": 0,
            "Win Rate": "0.00%",
            "Capital Req": "₹0.00",
            "Est Charges": "₹0.00",
            "Net PnL (1 Lot)": "₹0.00",
            "ROI %": "0.00%",
            "Max Drawdown": "₹0.00",
            "Profit Factor": 0.0
        }
        
    df = pd.DataFrame(trades)
    total_trades = len(df)
    wins = df['win'].sum()
    win_rate = (wins / total_trades) * 100
    
    # Capital required is the maximum margin or premium deployed
    capital_req = df['capital'].max()
    
    total_charges = df['charges'].sum()
    gross_pnl = df['pnl_rupees'].sum()
    net_pnl = gross_pnl - total_charges
    
    roi = (net_pnl / capital_req) * 100 if capital_req > 0 else 0.0
    
    # Calculate Max Drawdown (on net pnl after charges)
    df['net_pnl_trade'] = df['pnl_rupees'] - df['charges']
    cum_pnl = df['net_pnl_trade'].cumsum().values
    running_max = np.maximum.accumulate(cum_pnl)
    drawdowns = running_max - cum_pnl
    max_dd = np.max(drawdowns) if len(drawdowns) > 0 else 0.0
    
    # Profit Factor
    gross_profit = df[df['net_pnl_trade'] > 0]['net_pnl_trade'].sum()
    gross_loss = abs(df[df['net_pnl_trade'] < 0]['net_pnl_trade'].sum())
    profit_factor = (gross_profit / gross_loss) if gross_loss > 0 else float('inf')
    
    return {
        "Strategy": strategy_name,
        "Total Trades": total_trades,
        "Win Rate": f"{win_rate:.2f}%",
        "Capital Req": f"₹{capital_req:,.2f}",
        "Est Charges": f"₹{total_charges:,.2f}",
        "Net PnL (1 Lot)": f"₹{net_pnl:,.2f}",
        "ROI %": f"{roi:.2f}%",
        "Max Drawdown": f"₹{max_dd:,.2f}",
        "Profit Factor": round(profit_factor, 2)
    }

async def main():
    # 1. Load historical NIFTY candles
    df = load_data()
    if df.empty:
        print("❌ Loaded data is empty. Exiting.")
        return
        
    # 2. Calculate indicators
    df = calculate_indicators(df)
    
    # 3. Filter for target date
    target_date = datetime.date(2026, 6, 25) # Today
    print(f"\n📅 Running NIFTY backtests specifically for today's session: {target_date}...")
    
    lot_size = 25
    
    strategies = [
        ("CREDIT_SPREAD", "Credit Spreads (Theta Sell)"),
        ("DEBIT_SPREAD", "Debit Spreads (Spread Buy)"),
        ("NAKED_OPTIONS", "Naked Options (ATM Long Buy)"),
        ("IRON_CONDOR", "Iron Condor (Delta Neutral Sell)"),
        ("LONG_STRANGLE", "Long Strangle (OTM Buy Wings)"),
        ("CALENDAR_SPREAD", "Calendar Spread (Weekly/Monthly)"),
        ("RATIO_SPREAD", "Ratio Spread (OTM Credit Ratio)"),
        ("LONG_STRADDLE", "Long Straddle (ATM Long Buy)"),
        ("IRON_BUTTERFLY", "Iron Butterfly (ATM Credit Fly)"),
        ("DIAGONAL_SPREAD", "Diagonal Spread (Weekly/Monthly)"),
        ("DELTA_NEUTRAL", "Delta Neutral (ATM Short Straddle)")
    ]
    
    results = []
    
    for mode, name in strategies:
        print(f"\nSimulating {name}...")
        trades = run_backtest_strategy(df, mode, target_date, lot_size)
        if trades:
            print(f"   ↳ Executed {len(trades)} trades today:")
            for t in trades:
                print(f"      - Entry: {t['entry_time'].strftime('%H:%M')} | Exit: {t['exit_time'].strftime('%H:%M')} | Bias: {t['bias']} | Exit Reason: {t['exit_reason']} | PnL: {t['pnl_pts']:.2f} pts (₹{t['pnl_rupees']:.2f})")
        else:
            print("   ↳ No trades taken today.")
            
        results.append(analyze_trades(trades, name))
        
    df_results = pd.DataFrame(results)
    print("\n==========================================================================")
    print("📊 BACKTEST PERFORMANCE SUMMARY COMPARISON FOR NIFTY TODAY (1 Lot)")
    print("==========================================================================")
    print(df_results.to_string(index=False))
    print("==========================================================================")

if __name__ == '__main__':
    asyncio.run(main())
