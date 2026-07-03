import os
import sys
import datetime
import pandas as pd
import numpy as np
from sqlalchemy import create_engine
from dotenv import load_dotenv

load_dotenv()
sys.stdout.reconfigure(encoding='utf-8')

class CalendarBacktester:
    def __init__(self, ticker="NIFTY", timeframe="5m"):
        self.ticker = ticker
        self.timeframe = timeframe
        self.lot_sizes = {"NIFTY": 25, "BANKNIFTY": 15, "RELIANCE": 250, "HDFCBANK": 550}
        self.db_url = self._get_db_url()

    def _get_db_url(self) -> str:
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
        return f"postgresql://{db_user}:{db_pass}@{db_host}:{db_port}/{db_name}"

    def load_data(self) -> pd.DataFrame:
        """Loads data prior to and including the target date to ensure indicators are primed."""
        print(f"📡 Loading NIFTY {self.timeframe} candles from database...")
        engine = create_engine(self.db_url)
        query = f"""
            SELECT timestamp, open, high, low, close, volume FROM candles
            WHERE ticker = '{self.ticker}' AND timeframe = '{self.timeframe}'
            ORDER BY timestamp ASC
        """
        df = pd.read_sql(query, engine)
        if df.empty:
            print("❌ No data loaded. Check candles table.")
            return pd.DataFrame()
            
        df['timestamp'] = pd.to_datetime(df['timestamp'])
        df = df.sort_values(by="timestamp").reset_index(drop=True)
        
        # Ensure float types
        for col in ['open', 'high', 'low', 'close']:
            df[col] = df[col].astype(float)
        df['volume'] = df['volume'].astype(int)
        
        df['IST_Time'] = df['timestamp'].dt.tz_convert('Asia/Kolkata') if df['timestamp'].dt.tz is not None else df['timestamp']
        df['Date'] = df['IST_Time'].dt.date
        df['HourMinute'] = df['IST_Time'].dt.time
        return df

    def calculate_indicators(self, df: pd.DataFrame) -> pd.DataFrame:
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

    def run_backtest(self, df: pd.DataFrame, target_date: datetime.date):
        print(f"🎬 Running NIFTY Calendar Spread Backtest for {target_date}...")
        
        lot_size = self.lot_sizes.get(self.ticker, 25)
        
        in_trade = False
        trade_bias = None
        entry_price = 0.0
        entry_time = None
        highest_seen = 0.0
        lowest_seen = 0.0
        net_premium = 0.0
        delta = 0.05 # low delta sensitivity for ATM Calendar Spreads
        
        trades_logged = []
        
        # Find index range of target date
        target_indices = df[df['Date'] == target_date].index
        if len(target_indices) == 0:
            print(f"❌ No candles found for target date: {target_date}")
            return
            
        start_idx = target_indices[0]
        end_idx = target_indices[-1]
        
        # We start the loop from start_idx, ensuring we have history before it
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
                
                # Premium model for Calendar Spreads:
                # Value increases by time decay of short front-week (appreciation)
                # Value decreases if spot price moves away from entry (delta loss)
                price_change = abs(current_price - entry_price)
                appreciation = net_premium * 0.08 * hours_elapsed
                loss_from_delta = price_change * delta
                current_value = net_premium + appreciation - loss_from_delta
                
                is_tp = current_value >= net_premium * 1.35  # +35% target
                is_sl = current_value <= net_premium * 0.75  # -25% stop loss
                
                exit_triggered = False
                exit_reason = ""
                realized_pnl_pts = 0.0
                
                if is_eod:
                    exit_triggered = True
                    exit_reason = "⏰ EOD SQUARE OFF (3:15 PM)"
                    realized_pnl_pts = current_value - net_premium
                elif is_tp:
                    exit_triggered = True
                    exit_reason = "🎯 TAKE PROFIT (+35%)"
                    realized_pnl_pts = net_premium * 0.35
                elif is_sl:
                    exit_triggered = True
                    exit_reason = "🛑 STOP LOSS (-25%)"
                    realized_pnl_pts = -net_premium * 0.25
                    
                if exit_triggered:
                    # Estimate broker charges (Calendar Spread has 4 legs total: 2 entry + 2 exit)
                    # Flat options brokerage: ₹20/order -> ₹40 entry, ₹40 exit = ₹80.
                    # Plus clearing, stamp duty, GST, STT = ~₹120 total.
                    charges = 120.00
                    capital = net_premium * lot_size
                    
                    trades_logged.append({
                        "entry_time": entry_time.strftime("%H:%M"),
                        "exit_time": current_time.strftime("%H:%M"),
                        "bias": trade_bias,
                        "entry_price": round(entry_price, 2),
                        "exit_price": round(current_price, 2),
                        "exit_reason": exit_reason,
                        "pnl_pts": round(realized_pnl_pts, 2),
                        "pnl_rupees": round(realized_pnl_pts * lot_size, 2),
                        "win": 1 if realized_pnl_pts >= 0 else 0,
                        "charges": charges,
                        "capital": round(capital, 2)
                    })
                    in_trade = False
                continue
                
            # Entry scans: Must be during normal trading hours (09:15 AM to 03:00 PM IST)
            if current_time.time() < datetime.time(9, 15) or current_time.time() >= datetime.time(15, 0):
                continue
                
            # Scan signals based on VWAP, sweeps, and breakouts
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
            
            if direction:
                in_trade = True
                trade_bias = direction
                entry_price = current_price
                entry_time = current_time
                highest_seen = row['high']
                lowest_seen = row['low']
                # Calendar spread premium is estimated at 0.4% of underlying spot price
                net_premium = entry_price * 0.004
                
        # Print results
        self.report_results(trades_logged, target_date)

    def report_results(self, trades: list, target_date: datetime.date):
        print("\n" + "="*70)
        print(f"📊 CALENDAR SPREAD SINGLE-DAY BACKTEST REPORT | DATE: {target_date}")
        print("="*70)
        
        if not trades:
            print("❌ Zero trades executed. The market remained in CHOP_ZONE or didn't hit triggers.")
            print("="*70)
            return
            
        df_tr = pd.DataFrame(trades)
        total_trades = len(df_tr)
        wins = df_tr['win'].sum()
        losses = total_trades - wins
        win_rate = (wins / total_trades) * 100
        
        total_pnl_pts = df_tr['pnl_pts'].sum()
        total_pnl_rupees = df_tr['pnl_rupees'].sum()
        total_charges = df_tr['charges'].sum()
        net_return = total_pnl_rupees - total_charges
        
        # Max capital deployed (since it's a debit strategy, capital is the premium paid)
        max_capital = df_tr['capital'].max()
        roi = (net_return / max_capital * 100) if max_capital > 0 else 0.0
        
        print(f"Total Trades Taken   : {total_trades}")
        print(f"Winning Trades       : {wins}")
        print(f"Losing Trades        : {losses}")
        print(f"System Win Rate      : {win_rate:.2f}%")
        print(f"Gross PnL (Points)   : {total_pnl_pts:+.2f} pts")
        print(f"Gross PnL (Rupees)   : ₹{total_pnl_rupees:+.2f}")
        print(f"Total Broker Charges : ₹{total_charges:.2f}")
        print(f"Net Profit / Loss    : ₹{net_return:+.2f}")
        print(f"Capital Deployed     : ₹{max_capital:,.2f} (1 Lot)")
        print(f"Single-Day ROI %     : {roi:+.2f}%")
        print("="*70)
        print("\n📄 Detailed Trades Log:")
        print(df_tr[['entry_time', 'exit_time', 'bias', 'entry_price', 'exit_price', 'exit_reason', 'pnl_pts', 'pnl_rupees', 'charges', 'capital']].to_string(index=False))
        print("="*70)

if __name__ == '__main__':
    ticker = "NIFTY"
    timeframe = "5m"
    target_date = datetime.date(2026, 6, 24) # 24th June 2026
    
    backtester = CalendarBacktester(ticker=ticker, timeframe=timeframe)
    df_data = backtester.load_data()
    if not df_data.empty:
        df_data = backtester.calculate_indicators(df_data)
        backtester.run_backtest(df_data, target_date)
