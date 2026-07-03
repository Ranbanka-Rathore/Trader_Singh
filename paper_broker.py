"""
[LEGACY - v7] DO NOT USE FOR THE v8 LIVE SYSTEM.

This root-level PaperBroker belongs to the deprecated v7 Streamlit stack (uses the
peewee `database_manager`, not the v8 SQLModel layer). The v8 execution path lives
in `backend/app/services/execution_service.py`. Kept only so legacy analysis
scripts still import.
"""
import os
import datetime
import warnings
import pandas as pd
import yfinance as yf  # was used un-imported -> NameError on any square-off / TWAP
import time
import random
import decimal
from database_manager import db_manager, OpenPosition, Trade
from risk_shield import RiskShield

warnings.warn(
    "root paper_broker.py is LEGACY (v7). Use backend.app.services.execution_service "
    "for the v8 system.",
    DeprecationWarning,
    stacklevel=2,
)

class PaperBroker:
    def __init__(self):
        # Ensure database is connected
        db_manager.connect()
        self.risk_shield = RiskShield()

    def execute_trade(self, trade_data):
        """
        Logs a new trade into the Open Positions database.
        Supports Institutional Execution Algos: 'MARKET', 'ICEBERG', 'TWAP'
        """
        try:
            execution_algo = trade_data.get("execution_algo", "MARKET")
            total_lots = trade_data.get("lots_sized", 1)
            ticker = trade_data.get("ticker", "UNK")
            
            print(f"🚀 EXECUTION START: {ticker} | Algo: {execution_algo} | Lots: {total_lots}")

            if execution_algo == "ICEBERG":
                final_fill_price = self._execute_iceberg(ticker, total_lots, trade_data.get("spot_price"))
            elif execution_algo == "TWAP":
                duration_mins = trade_data.get("algo_duration", 5)
                final_fill_price = self._execute_twap(ticker, total_lots, duration_mins)
            else:
                final_fill_price = trade_data.get("spot_price")

            # --- 🛡️ RISK SHIELD: CALCULATE INITIAL GREEKS ---
            try:
                # Approximate implied volatility (can be refined with real data)
                iv = 0.15 
                # Weekly expiry assumption (next Thursday)
                today = datetime.date.today()
                days_until_thursday = (3 - today.weekday()) % 7
                if days_until_thursday == 0: days_until_thursday = 7
                expiry = today + datetime.timedelta(days=days_until_thursday)
                
                greeks = self.risk_shield.calculate_spread_greeks(
                    spot=final_fill_price,
                    leg_1_strike=trade_data.get("leg_1_sell", 0),
                    leg_2_strike=trade_data.get("leg_2_buy", 0),
                    expiry_date=expiry,
                    volatility=iv,
                    strategy_type=trade_data.get("strategy_type", "BULL_PUT_SPREAD")
                )
                trade_data.update(greeks)
            except Exception as re:
                print(f"   ⚠️ Risk Shield Warning: Could not calc initial Greeks: {re}")

            # Add timestamp, entry price, and initialize tracking memory
            now = datetime.datetime.now()
            
            # --- 🛠️ NATIVE TYPE CONVERSION (Fixes 'schema np' error) ---
            final_fill_price = float(final_fill_price)
            
            formatted_data = {
                "ticker": ticker,
                "strategy_type": trade_data.get("strategy_type"),
                "spot_price": final_fill_price,
                "leg_1_sell": float(trade_data.get("leg_1_sell", 0)),
                "leg_2_buy": float(trade_data.get("leg_2_buy", 0)),
                "net_credit_per_share": float(trade_data.get("net_credit_per_share", 0)),
                "max_risk_per_share": float(trade_data.get("max_risk_per_share", 0)),
                "risk_reward_ratio": str(trade_data.get("risk_reward_ratio", "1:1")),
                "win_probability": float(trade_data.get("win_probability", 0)),
                "vol_surge_multiplier": float(trade_data.get("vol_surge_multiplier", 0)),
                "coi_pcr": float(trade_data.get("coi_pcr", 0)),
                "bias": str(trade_data.get("bias", "BULLISH")),
                "execution_time": now.time(),
                "mode": trade_data.get("mode", "Paper"),
                "lots_sized": int(trade_data.get("lots_sized", 1)),
                "entry_date": now,
                "entry_spot_price": final_fill_price,
                "highest_seen": final_fill_price,
                "lowest_seen": final_fill_price,
                "dynamic_sl": float(trade_data.get("dynamic_sl", trade_data.get("leg_1_sell", 0))),
                "net_delta": float(trade_data.get("net_delta", 0)),
                "net_gamma": float(trade_data.get("net_gamma", 0)),
                "net_theta": float(trade_data.get("net_theta", 0)),
                "net_vega": float(trade_data.get("net_vega", 0)),
                "learning_context": trade_data.get("learning_context", {})
            }
            
            db_manager.add_open_position(formatted_data)
            print(f"✅ TRADE LOCKED: {ticker} at ₹{final_fill_price:.2f}")
            return True
        except Exception as e:
            print(f"   ⚠️ Error executing trade in database: {e}")
            return False

    def _execute_iceberg(self, ticker, total_lots, start_price):
        """
        Splits a large order into smaller 'visible' chunks to hide size.
        Simulates slippage for each chunk.
        """
        visible_size = max(1, total_lots // 4) # Show 25% at a time
        remaining = total_lots
        fill_prices = []
        
        print(f"   🧊 Iceberg Active: Visible size {visible_size} lots.")
        
        while remaining > 0:
            chunk = min(visible_size, remaining)
            # Simulate a small delay and market impact slippage (0.01% to 0.05% per chunk)
            time.sleep(random.uniform(0.5, 1.5))
            slippage = start_price * random.uniform(0.0001, 0.0005)
            fill_prices.append(start_price + slippage)
            
            remaining -= chunk
            print(f"      ↳ Chunk filled: {chunk} lots. Remaining: {remaining}")
            
        return sum(fill_prices) / len(fill_prices)

    def _execute_twap(self, ticker, total_lots, duration_mins):
        """
        Time-Weighted Average Price execution.
        Executes slices of the order over a time window.
        """
        slices = 5
        interval = (duration_mins * 60) / slices
        remaining = total_lots
        fill_prices = []
        
        print(f"   ⏳ TWAP Active: {total_lots} lots over {duration_mins} mins.")
        
        for i in range(slices):
            chunk = total_lots // slices if i < slices - 1 else remaining
            
            # Real-world simulation: Fetch latest price for each slice
            df = yf.download(ticker, period="1d", interval="1m", progress=False)
            current_price = float(df['Close'].iloc[-1])
            fill_prices.append(current_price)
            
            remaining -= chunk
            print(f"      ↳ Slice {i+1}/{slices} filled at ₹{current_price:.2f}. Next slice in {interval}s")
            
            if remaining > 0:
                time.sleep(interval)
                
        return sum(fill_prices) / len(fill_prices)

    def square_off_all_positions(self, reason="EMERGENCY_SQUARE_OFF"):
        """Forcefully closes all open positions at current market price."""
        try:
            db_manager.connect()
            open_trades = OpenPosition.select()
        except Exception as e:
            print(f"❌ CRITICAL: Could not fetch open positions for emergency square off: {e}")
            return 0
            
        if not open_trades or len(open_trades) == 0:
            return 0

        print(f"🚨 EMERGENCY: Squaring off all {len(open_trades)} positions. Reason: {reason}")
        count = 0
        for trade_obj in open_trades:
            ticker = trade_obj.ticker
            try:
                df = yf.download(ticker, period="1d", interval="1m", progress=False)
                current_price = float(df['Close'].iloc[-1])
                
                exit_data = {
                    "exit_date": datetime.datetime.now(),
                    "exit_price": round(current_price, 2),
                    "exit_reason": reason,
                    "realized_pnl": 0.0 # Force neutral or conservative for emergency
                }
                db_manager.close_position(trade_obj.id, exit_data)
                count += 1
            except Exception as e:
                print(f"   ⚠️ Failed to emergency close {ticker}: {e}")
        
        return count

    def evaluate_open_positions(self):
        """Scans all open trades and automatically triggers exits using dynamic Trailing Stops."""
        open_trades = OpenPosition.select()
        
        if not open_trades:
            return []

        print(f"\n🔍 PAPER BROKER: Evaluating {len(open_trades)} Open Positions...")
        
        # --- 🛡️ RISK SHIELD: PORTFOLIO VaR ---
        try:
            positions_list = [t.__data__ for t in open_trades]
            portfolio_var = self.risk_shield.calculate_portfolio_var(positions_list)
            print(f"🛡️ PORTFOLIO RISK: 1-Day VaR (95%): ₹{portfolio_var:,.2f}")
        except Exception as ve:
            print(f"   ⚠️ Risk Shield Warning: Could not calc VaR: {ve}")

        closed_trades_report = []

        # --- THE MASTER OVERRIDE: EOD TIME CHECK ---
        now = datetime.datetime.now()
        is_eod = (now.hour == 15 and now.minute >= 15) or (now.hour > 15)

        for trade_obj in open_trades:
            # Use __data__ for raw dict access, but cast Decimal to float
            trade = {k: float(v) if isinstance(v, (decimal.Decimal, pd.Series)) else v for k, v in trade_obj.__data__.items()}
            # Convert specifically problematic Decimal fields
            for field in ["entry_spot_price", "net_credit_per_share", "highest_seen", "lowest_seen", "dynamic_sl", "leg_1_sell", "leg_2_buy"]:
                if field in trade and trade[field] is not None:
                    trade[field] = float(trade[field])
            
            ticker = trade["ticker"]
            try:
                # Fetch today's price and calculate the 26-EMA
                df = yf.download(ticker, period="1mo", interval="1d", progress=False)
                if isinstance(df.columns, pd.MultiIndex):
                    df.columns = df.columns.get_level_values(0)
                
                df['EMA_26'] = df['Close'].ewm(span=26, adjust=False).mean()
                
                current_price = float(df['Close'].iloc[-1])
                current_ema = float(df['EMA_26'].iloc[-1])
                
                exit_triggered = False
                exit_reason = ""
                realized_pnl = 0

                # Extract trade details safely
                bias = trade.get("bias", "BULLISH")
                short_strike = trade.get("leg_1_sell", 0.0)
                entry_price = trade.get("entry_spot_price", current_price)
                net_credit = trade.get("net_credit_per_share", 0.0)

                # Retrieve tracking memory
                highest_seen = trade.get("highest_seen", current_price)
                lowest_seen = trade.get("lowest_seen", current_price)
                dynamic_sl = trade.get("dynamic_sl", short_strike)

                # --- 📈 TRAILING STOP PROTOCOL ---
                if bias == "BULLISH":
                    # 1. Update High Water Mark
                    highest_seen = max(highest_seen, current_price)
                    trade_obj.highest_seen = highest_seen
                    lowest_seen = min(lowest_seen, current_price)
                    trade_obj.lowest_seen = lowest_seen
                    
                    # 2. Calculate Favorable Move (Profit Percentage)
                    profit_pct = (highest_seen - entry_price) / entry_price
                    
                    # 3. Ratchet the Trailing Stop Upwards
                    if profit_pct >= 0.010: # +1.0% in our favor
                        dynamic_sl = max(dynamic_sl, entry_price * 1.005) # Lock in profit
                    elif profit_pct >= 0.005: # +0.5% in our favor
                        dynamic_sl = max(dynamic_sl, entry_price) # Move to Breakeven
                        
                    trade_obj.dynamic_sl = dynamic_sl
                    
                    # 4. Trigger Logic
                    is_tp = current_price >= entry_price * 1.015
                    is_sl = current_price <= dynamic_sl
                    is_ema_stop = current_price < current_ema
                    is_profitable_now = current_price > entry_price

                else: # BEARISH
                    # 1. Update Low Water Mark
                    lowest_seen = min(lowest_seen, current_price)
                    trade_obj.lowest_seen = lowest_seen
                    highest_seen = max(highest_seen, current_price)
                    trade_obj.highest_seen = highest_seen
                    
                    # 2. Calculate Favorable Move (Profit Percentage)
                    profit_pct = (entry_price - lowest_seen) / entry_price
                    
                    # 3. Ratchet the Trailing Stop Downwards
                    if profit_pct >= 0.010: # +1.0% in our favor
                        new_sl = entry_price * 0.995
                        if dynamic_sl == short_strike or new_sl < dynamic_sl:
                            dynamic_sl = new_sl
                    elif profit_pct >= 0.005: # +0.5% in our favor
                        new_sl = entry_price
                        if dynamic_sl == short_strike or new_sl < dynamic_sl:
                            dynamic_sl = new_sl
                            
                    trade_obj.dynamic_sl = dynamic_sl
                    
                    # 4. Trigger Logic
                    is_tp = current_price <= entry_price * 0.985
                    is_sl = current_price >= dynamic_sl
                    is_ema_stop = current_price > current_ema
                    is_profitable_now = current_price < entry_price

                # RULE 0: EOD Time-Based Stop
                if is_eod:
                    exit_triggered = True
                    exit_reason = "⏰ EOD SQUARE OFF (Time Stop at 3:15 PM)"
                    realized_pnl = (net_credit * 0.50) if is_profitable_now else -(net_credit * 0.5)

                # RULE 1: Take Profit Hit
                elif is_tp:
                    exit_triggered = True
                    exit_reason = "🎯 TAKE PROFIT (75% Premium Captured)"
                    realized_pnl = net_credit * 0.75  
                    
                # RULE 2: Stop Loss (Hard or Trailing)
                elif is_sl:
                    exit_triggered = True
                    if dynamic_sl == short_strike:
                        exit_reason = "🛑 HARD STOP LOSS (100% Premium Lost)"
                        realized_pnl = -net_credit 
                    elif dynamic_sl == entry_price:
                        exit_reason = "🛡️ TRAILING STOP (Breakeven - 0% Premium Lost)"
                        realized_pnl = 0.0
                    else:
                        exit_reason = "📈 TRAILING STOP (Profit Locked In - 25% Premium Captured)"
                        realized_pnl = net_credit * 0.25
                    
                # RULE 3: Technical Stop (Macro Trend Broken)
                elif is_ema_stop:
                    exit_triggered = True
                    exit_reason = "📉 TECHNICAL STOP (Price crossed 26-EMA)"
                    realized_pnl = -(net_credit * 0.5)

                if exit_triggered:
                    exit_data = {
                        "exit_date": now,
                        "exit_price": round(current_price, 2),
                        "exit_reason": exit_reason,
                        "realized_pnl": round(realized_pnl, 2)
                    }
                    
                    db_manager.close_position(trade_obj.id, exit_data)
                    
                    # Prepare report entry matching the expected structure
                    report_entry = trade.copy()
                    report_entry.update(exit_data)
                    closed_trades_report.append(report_entry)
                    
                    print(f"   -> EXIT TRIGGERED: {ticker} | Reason: {exit_reason} | PnL: ₹{realized_pnl}")
                else:
                    trade_obj.save()
                    print(f"   -> HOLD: {ticker} (Current: ₹{round(current_price, 2)} | Trailing SL: ₹{round(dynamic_sl, 2)})")

            except Exception as e:
                print(f"   -> Error evaluating {ticker}: {e}")

        return closed_trades_report
