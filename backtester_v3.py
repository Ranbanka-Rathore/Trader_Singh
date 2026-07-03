import os
import datetime
import pandas as pd
import numpy as np
from sqlalchemy import create_engine
from dotenv import load_dotenv

load_dotenv()

class PropDeskBacktester:
    def __init__(self, ticker="NIFTY", timeframe="5m", strategy_mode="CREDIT_SPREAD"):
        self.ticker = ticker
        self.timeframe = timeframe
        self.strategy_mode = strategy_mode
        
        # Load DB credentials
        db_user = os.getenv("DB_USER", "trader")
        db_pass = os.getenv("DB_PASSWORD", "institutional_grade_password")
        db_port = os.getenv("DB_PORT", "5432")
        db_name = os.getenv("DB_NAME", "agentic_trader")
        
        # Dynamic WSL IP host resolution
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
            env_host = os.getenv("DB_HOST", "")
            import socket
            try:
                s = socket.create_connection(("127.0.0.1", int(db_port)), timeout=1)
                s.close()
                return "127.0.0.1"
            except (ConnectionRefusedError, OSError):
                pass
            if env_host and env_host not in ["127.0.0.1", "localhost"]:
                try:
                    s = socket.create_connection((env_host, int(db_port)), timeout=1)
                    s.close()
                    return env_host
                except (ConnectionRefusedError, OSError):
                    pass
            wsl_ip = _get_wsl_ip()
            return wsl_ip if wsl_ip else "127.0.0.1"

        db_host = _resolve_db_host()
        self.db_url = f"postgresql://{db_user}:{db_pass}@{db_host}:{db_port}/{db_name}"
        self.lot_sizes = {"NIFTY": 25, "BANKNIFTY": 15, "RELIANCE": 250, "HDFCBANK": 550}

    def load_data(self) -> pd.DataFrame:
        """Fetches candles from PostgreSQL database with boot retries."""
        print(f"📡 Loading historical {self.timeframe} candles for {self.ticker} from DB...")
        import time
        for attempt in range(10):
            try:
                engine = create_engine(self.db_url, connect_args={'connect_timeout': 3})
                query = f"""
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
                    WHERE c.ticker = '{self.ticker}' AND c.timeframe = '{self.timeframe}'
                    ORDER BY c.timestamp ASC
                """
                df = pd.read_sql(query, engine)
                if df.empty:
                    print(f"❌ No candles found in DB for {self.ticker} ({self.timeframe}).")
                    return pd.DataFrame()
                
                # Sort and set index
                df['timestamp'] = pd.to_datetime(df['timestamp'])
                df = df.sort_values(by="timestamp").reset_index(drop=True)
                
                # Ensure float types
                for col in ['open', 'high', 'low', 'close', 'pcr', 'total_gex']:
                    if col in df.columns:
                        df[col] = df[col].astype(float)
                df['volume'] = df['volume'].astype(int)
                
                print(f"   ↳ Loaded {len(df)} candles.")
                return df
            except Exception as e:
                err_str = str(e)
                if "starting up" in err_str or "OperationalError" in err_str or "connection" in err_str or "SSL" in err_str:
                    print(f"   ⚠️ DB is starting up or not ready. Retrying in 2 seconds... (Attempt {attempt+1}/10)")
                    time.sleep(2)
                else:
                    print(f"❌ DB Query Error: {e}")
                    return pd.DataFrame()
        return pd.DataFrame()

    def run_simulation(self, df: pd.DataFrame):
        if df.empty or len(df) < 100:
            print("⚠️ Insufficient data to run backtest. Need at least 100 candles.")
            return

        print("🚀 Running Backtest Simulation...")
        
        # Calculate daily groups for VWAP and Session High/Low
        df['IST_Time'] = df['timestamp'].dt.tz_convert('Asia/Kolkata') if df['timestamp'].dt.tz is not None else df['timestamp']
        df['Date'] = df['IST_Time'].dt.date
        df['HourMinute'] = df['IST_Time'].dt.time
        
        # 1. Pre-calculate indicators on full df to avoid window loop bottlenecks
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
        trade_bias = None # "BULLISH" or "BEARISH"
        entry_price = 0.0
        entry_time = None
        highest_seen = 0.0
        lowest_seen = 0.0
        dynamic_sl = 0.0
        leg_1_sell = 0.0
        leg_2_buy = 0.0
        premium_paid = 0.0  # For Calendar Spreads
        
        trades_logged = []
        
        # 2. Main Replay Loop
        for i in range(50, len(df)):
            row = df.iloc[i]
            prev_row = df.iloc[i-1]
            current_time = row['IST_Time']
            current_price = row['close']
            
            # EOD check (flatten at 3:15 PM)
            is_eod = (current_time.hour == 15 and current_time.minute >= 15) or (current_time.hour > 15)
            
            if in_trade:
                exit_triggered = False
                exit_reason = ""
                realized_pnl_pts = 0.0
                
                if self.strategy_mode == "CALENDAR_SPREAD":
                    # Option Pricing model for Calendar Spread
                    highest_seen = max(highest_seen, row['high'])
                    lowest_seen = min(lowest_seen, row['low'])
                    hours_elapsed = (current_time - entry_time).total_seconds() / 3600.0
                    price_change = abs(current_price - entry_price)
                    appreciation = premium_paid * 0.08 * hours_elapsed
                    loss_from_delta = price_change * 0.05  # delta = 0.05
                    current_value = premium_paid + appreciation - loss_from_delta
                    
                    is_tp = current_value >= premium_paid * 1.35
                    is_sl = current_value <= premium_paid * 0.75
                    
                    if is_eod:
                        exit_triggered = True
                        exit_reason = "⏰ EOD SQUARE OFF (3:15 PM)"
                        realized_pnl_pts = current_value - premium_paid
                    elif is_tp:
                        exit_triggered = True
                        exit_reason = "🎯 TAKE PROFIT (+35%)"
                        realized_pnl_pts = premium_paid * 0.35
                    elif is_sl:
                        exit_triggered = True
                        exit_reason = "🛑 STOP LOSS (-25%)"
                        realized_pnl_pts = -premium_paid * 0.25
                else:
                    # CREDIT_SPREAD
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

            # Skip entries outside normal market hours (09:15 AM to 03:00 PM IST)
            if current_time.time() < datetime.time(9, 15) or current_time.time() >= datetime.time(15, 0):
                continue
                
            # Signal scanning: Standardized rules from QuantEngine
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
 
            # Map triggers to Direction
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
                if self.strategy_mode == "CALENDAR_SPREAD":
                    in_trade = True
                    trade_bias = direction
                    entry_price = current_price
                    entry_time = current_time
                    highest_seen = row['high']
                    lowest_seen = row['low']
                    premium_paid = entry_price * 0.004
                else:
                    if self.ticker in ["BANKNIFTY", "SENSEX"]:
                        interval = 100
                    elif self.ticker in ["NIFTY", "FINNIFTY"]:
                        interval = 50
                    else:
                        interval = 10
                    if direction == "BULLISH":
                        leg_1_sell = round((current_price * 0.99) / interval) * interval
                        leg_2_buy = leg_1_sell - interval
                        dynamic_sl = leg_1_sell
                    else:
                        leg_1_sell = round((current_price * 1.01) / interval) * interval
                        leg_2_buy = leg_1_sell + interval
                        dynamic_sl = leg_1_sell
                    
                    # Enter Virtual Trade
                    in_trade = True
                    trade_bias = direction
                    entry_price = current_price
                    entry_time = current_time
                    highest_seen = row['high']
                    lowest_seen = row['low']

        # 3. Aggregate results
        self.print_report(trades_logged)

    def print_report(self, trades: list):
        if not trades:
            print("\n❌ Backtest completed but zero trades were executed. Adjust filters.")
            return

        df_tr = pd.DataFrame(trades)
        total_trades = len(df_tr)
        wins = df_tr['win'].sum()
        losses = total_trades - wins
        win_rate = (wins / total_trades) * 100
        
        # Calculate returns in Index points
        total_pnl = df_tr['pnl_pts'].sum()
        
        # Profit Factor: Gross profits / Gross losses
        gross_profits = df_tr[df_tr['pnl_pts'] > 0]['pnl_pts'].sum()
        gross_losses = abs(df_tr[df_tr['pnl_pts'] < 0]['pnl_pts'].sum())
        profit_factor = (gross_profits / gross_losses) if gross_losses > 0 else float('inf')
        
        print("\n" + "="*60)
        print(f"📈 REAL-WORLD PROP-DESK BACKTEST REPORT: {self.ticker}")
        print("="*60)
        print(f"Time Range           | {df_tr['entry_time'].min().strftime('%Y-%m-%d')} to {df_tr['exit_time'].max().strftime('%Y-%m-%d')}")
        print(f"Total Trades Taken   | {total_trades}")
        print(f"Winning Trades       | {wins}")
        print(f"Losing Trades        | {losses}")
        print(f"System Win Rate      | {win_rate:.2f}%")
        print(f"Profit Factor        | {profit_factor:.2f}")
        print(f"Total Net Return     | {total_pnl:.2f} Index Points")
        print(f"Lot-Sized Net Return | ₹{total_pnl * self.lot_sizes.get(self.ticker, 1):,.2f}")
        print("="*60)
        print("\n📄 Summary of Recent Trades:")
        print(df_tr.tail(10).to_string(index=False))
        print("="*60)

if __name__ == "__main__":
    backtester = PropDeskBacktester(ticker="NIFTY")
    df_candles = backtester.load_data()
    if not df_candles.empty:
        backtester.run_simulation(df_candles)
