import os
import asyncio
import datetime
import random
import pandas as pd
from typing import List, Dict, Any, Optional
from decimal import Decimal
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from backend.app.db.models import OpenPosition, Trade
from backend.app.core.risk_shield import RiskShield
from backend.app.core.rl_oms import rl_oms
from backend.app.services.database_service import database_service
from backend.app.services.redis_service import redis_service

class ExecutionService:
    def __init__(self):
        self.risk_shield = RiskShield()

    def _load_brain_config(self) -> Dict[str, Any]:
        import json
        if os.path.exists("brain_config.json"):
            try:
                with open("brain_config.json", 'r') as f:
                    return json.load(f)
            except Exception:
                pass
        return {}

    async def execute_trade(self, session: AsyncSession, trade_data: Dict[str, Any]) -> bool:
        """
        Logs a new trade into the Open Positions database.
        Supports Institutional Execution Algos: 'MARKET', 'ICEBERG', 'TWAP', 'RL_HUNT'
        """
        try:
            # V8 Upgrade: Default to RL_HUNT for larger positions
            total_lots = int(trade_data.get("lots_sized", 1))
            execution_algo = trade_data.get("execution_algo", "RL_HUNT" if total_lots >= 3 else "MARKET")
            ticker = trade_data.get("ticker", "UNK")
            spot_price = float(trade_data.get("spot_price", 0.0))
            
            print(f"🚀 EXECUTION START: {ticker} | Algo: {execution_algo} | Lots: {total_lots}")

            if execution_algo == "RL_HUNT":
                side = "BUY" if trade_data.get("bias") == "BULLISH" else "SELL"
                final_fill_price = await rl_oms.execute_limit_hunt(ticker, side, total_lots, spot_price)
            elif execution_algo == "ICEBERG":
                final_fill_price = await self._execute_iceberg(ticker, total_lots, spot_price)
            elif execution_algo == "TWAP":
                duration_mins = int(trade_data.get("algo_duration", 5))
                final_fill_price = await self._execute_twap(ticker, total_lots, duration_mins)
            else:
                final_fill_price = spot_price

            # --- 🛡️ RISK SHIELD: CALCULATE INITIAL GREEKS ---
            greeks = {"net_delta": 0.0, "net_gamma": 0.0, "net_theta": 0.0, "net_vega": 0.0}
            try:
                iv = 0.15
                today = datetime.date.today()
                # Use the real nearest expiry from the scrip master (NIFTY weeklies
                # are Tuesday in 2026, BANKNIFTY is monthly-only). Fall back to a
                # next-Tuesday estimate only if the scrip master has no data.
                from backend.app.core import scrip_master
                expiry = scrip_master.get_nearest_expiry(ticker, today)
                if expiry is None:
                    days_until_tue = (1 - today.weekday()) % 7
                    if days_until_tue == 0:
                        days_until_tue = 7
                    expiry = today + datetime.timedelta(days=days_until_tue)

                g = self.risk_shield.calculate_spread_greeks(
                    spot=final_fill_price,
                    leg_1_strike=float(trade_data.get("leg_1_sell", 0)),
                    leg_2_strike=float(trade_data.get("leg_2_buy", 0)),
                    expiry_date=expiry,
                    volatility=iv,
                    strategy_type=trade_data.get("strategy_type", "BULL_PUT_SPREAD")
                )
                greeks.update(g)
            except Exception as re:
                print(f"   ⚠️ Risk Shield Warning: Could not calc initial Greeks: {re}")

            IST = datetime.timezone(datetime.timedelta(hours=5, minutes=30))
            now = datetime.datetime.now(IST).replace(tzinfo=None)
            
            formatted_data = {
                "ticker": ticker,
                "strategy_type": trade_data.get("strategy_type"),
                "spot_price": Decimal(str(round(final_fill_price, 2))),
                "leg_1_sell": Decimal(str(trade_data.get("leg_1_sell", 0))),
                "leg_2_buy": Decimal(str(trade_data.get("leg_2_buy", 0))),
                "net_credit_per_share": Decimal(str(trade_data.get("net_credit_per_share", 0))),
                "max_risk_per_share": Decimal(str(trade_data.get("max_risk_per_share", 0))),
                "risk_reward_ratio": str(trade_data.get("risk_reward_ratio", "1:1")),
                "win_probability": Decimal(str(trade_data.get("win_probability", 85.0))),
                "vol_surge_multiplier": Decimal(str(trade_data.get("vol_surge_multiplier", 1.0))),
                "coi_pcr": Decimal(str(trade_data.get("coi_pcr", 1.0))),
                "bias": str(trade_data.get("bias", "BULLISH")),
                "execution_time": now.time(),
                "mode": trade_data.get("mode", "Paper"),
                "lots_sized": total_lots,
                "entry_date": now,
                "entry_spot_price": Decimal(str(round(final_fill_price, 2))),
                "highest_seen": Decimal(str(round(final_fill_price, 2))),
                "lowest_seen": Decimal(str(round(final_fill_price, 2))),
                "dynamic_sl": Decimal(str(trade_data.get("leg_1_sell", 0))),
                "net_delta": Decimal(str(greeks.get("net_delta", 0.0))),
                "net_gamma": Decimal(str(greeks.get("net_gamma", 0.0))),
                "net_theta": Decimal(str(greeks.get("net_theta", 0.0))),
                "net_vega": Decimal(str(greeks.get("net_vega", 0.0))),
                "learning_context": trade_data.get("learning_context", {})
            }
            
            await database_service.add_open_position(session, formatted_data)
            print(f"✅ TRADE LOCKED: {ticker} at ₹{final_fill_price:.2f}")
            return True
        except Exception as e:
            print(f"   ⚠️ Error executing trade: {e}")
            return False

    async def _execute_iceberg(self, ticker: str, total_lots: int, start_price: float) -> float:
        visible_size = max(1, total_lots // 4)
        remaining = total_lots
        fill_prices = []
        
        print(f"   ↳ Iceberg Active: Visible size {visible_size} lots.")
        
        while remaining > 0:
            chunk = min(visible_size, remaining)
            await asyncio.sleep(random.uniform(0.1, 0.3))
            slippage = start_price * random.uniform(0.0001, 0.0005)
            fill_prices.append(start_price + slippage)
            remaining -= chunk
            
        return sum(fill_prices) / len(fill_prices)

    async def _execute_twap(self, ticker: str, total_lots: int, duration_mins: int) -> float:
        slices = 5
        interval = (duration_mins * 60) / slices / 100 
        remaining = total_lots
        fill_prices = []
        
        print(f"   ↳ TWAP Active: {total_lots} lots over {duration_mins} mins.")
        
        # Get start price from latest tick snapshot
        snap = await redis_service.get_json(f"market_snapshot:{ticker}")
        start_price = float(snap["price"]) if snap and "price" in snap else 100.0

        for i in range(slices):
            chunk = total_lots // slices if i < slices - 1 else remaining
            snap = await redis_service.get_json(f"market_snapshot:{ticker}")
            current_price = float(snap["price"]) if snap and "price" in snap else start_price
            fill_prices.append(current_price)
            remaining -= chunk
            if remaining > 0:
                await asyncio.sleep(interval)
                
        return sum(fill_prices) / len(fill_prices)

    def _calculate_strategy_pnl(self, pos, current_price: float, now_time) -> float:
        """
        Calculates the actual strategy-specific realized P&L based on options equations.
        """
        try:
            entry_price = float(pos.entry_spot_price)
            net_credit = float(pos.adjusted_net_credit if pos.adjusted_net_credit is not None else pos.net_credit_per_share)
            premium_paid = float(pos.net_credit_per_share) # Alias for debit
            lots = int(pos.lots_sized)
            lot_size = self.risk_shield.get_lot_size(pos.ticker)
            total_multiplier = lots * lot_size
            
            hours_elapsed = 0.0
            if pos.entry_date:
                time_diff = now_time - pos.entry_date
                hours_elapsed = time_diff.total_seconds() / 3600.0
                
            spot_change = current_price - entry_price
            
            # Determine delta
            if pos.strategy_type in ["DEBIT_BULL_SPREAD", "DEBIT_BEAR_SPREAD", "DEBIT_SPREAD"]:
                delta = 0.30
                if pos.bias == "BULLISH":
                    current_spread_value = premium_paid + (spot_change * delta)
                else:
                    current_spread_value = premium_paid - (spot_change * delta)
                return (current_spread_value - premium_paid) * total_multiplier
                
            elif pos.strategy_type == "CALENDAR_SPREAD":
                delta = 0.05
                appreciation = premium_paid * 0.08 * hours_elapsed
                loss_from_delta = abs(spot_change) * delta * lot_size * lots
                return (appreciation - loss_from_delta)
                
            elif pos.strategy_type == "DIAGONAL_SPREAD":
                delta = 0.20
                appreciation = premium_paid * 0.05 * hours_elapsed
                loss_from_delta = abs(spot_change) * delta * lot_size * lots
                return (appreciation - loss_from_delta)
                
            elif pos.strategy_type == "DELTA_NEUTRAL":
                delta = 0.02
                appreciation = net_credit * 0.04 * hours_elapsed
                loss_from_delta = abs(spot_change) * delta * lot_size * lots
                return (appreciation - loss_from_delta)
                
            elif pos.strategy_type == "COVERED_CALL":
                delta = 0.30
                realized_pnl_pts = spot_change - ( (net_credit + spot_change * delta - net_credit * 0.05 * hours_elapsed) - net_credit )
                return realized_pnl_pts * total_multiplier
                
            elif pos.strategy_type == "CASH_SECURED_PUT":
                realized_pnl_pts = (spot_change * 0.30) + net_credit * 0.05 * hours_elapsed
                return realized_pnl_pts * total_multiplier
                
            else: # Default Credit Spread (OTM)
                delta = 0.15
                if pos.bias == "BULLISH":
                    current_spread_value = net_credit - (spot_change * delta)
                else:
                    current_spread_value = net_credit + (spot_change * delta)
                return (net_credit - current_spread_value) * total_multiplier
                
        except Exception as e:
            print(f"Error in _calculate_strategy_pnl: {e}")
            return 0.0

    async def square_off_all_positions(self, session: AsyncSession, reason: str = "EMERGENCY_SQUARE_OFF") -> int:
        open_positions = await database_service.get_open_positions(session)
        if not open_positions:
            return 0

        print(f"🚨 EMERGENCY: Squaring off all {len(open_positions)} positions. Reason: {reason}")
        count = 0
        IST = datetime.timezone(datetime.timedelta(hours=5, minutes=30))
        now = datetime.datetime.now(IST).replace(tzinfo=None)
        
        for pos in open_positions:
            try:
                snap = await redis_service.get_json(f"market_snapshot:{pos.ticker}")
                if snap and "price" in snap:
                    current_price = float(snap["price"])
                else:
                    print(f"⚠️ Warning: No live price for {pos.ticker} during emergency exit. Using last seen price: ₹{pos.highest_seen}")
                    current_price = float(pos.highest_seen or 0.0)
                
                if current_price == 0.0:
                    print(f"   ❌ Critical: No price data available for {pos.ticker}. Skipping square off.")
                    continue
                
                # Calculate actual dynamic P&L for emergency exit
                pnl = self._calculate_strategy_pnl(pos, current_price, now)
                
                exit_data = {
                    "exit_date": now,
                    "exit_price": Decimal(str(round(current_price, 2))),
                    "exit_reason": reason,
                    "realized_pnl": Decimal(str(round(pnl, 2)))
                }
                await database_service.close_position(session, pos.id, exit_data)
                count += 1
            except Exception as e:
                print(f"   ⚠️ Failed to emergency close {pos.ticker}: {e}")

        return count

    async def evaluate_open_positions(self, session: AsyncSession) -> List[Dict[str, Any]]:
        open_positions = await database_service.get_open_positions(session)
        if not open_positions:
            return []

        print(f"\n🔍 EXECUTION SERVICE: Evaluating {len(open_positions)} Open Positions...")
        
        # Calculate VaR
        portfolio_var = self.risk_shield.calculate_portfolio_var(open_positions)
        print(f"🛡️ PORTFOLIO RISK: 1-Day VaR (95%): ₹{portfolio_var:,.2f}")

        closed_trades_report = []
        IST = datetime.timezone(datetime.timedelta(hours=5, minutes=30))
        now = datetime.datetime.now(IST).replace(tzinfo=None)
        is_eod = (now.hour == 15 and now.minute >= 15) or (now.hour > 15)

        for pos in open_positions:
            ticker = pos.ticker
            # Normalize ticker for Redis lookup (Handle DB vs Live Feed naming mismatch)
            lookup_ticker = ticker
            if ticker == "NSEBANK": lookup_ticker = "BANKNIFTY"
            elif ticker == "NSEI": lookup_ticker = "NIFTY"
            
            try:
                snap = await redis_service.get_json(f"market_snapshot:{lookup_ticker}")
                if snap and "price" in snap:
                    current_price = float(snap["price"])
                else:
                    if is_eod:
                        print(f"⚠️ Warning: No live price for {lookup_ticker} during EOD check. Attempting DB candle close fallback...")
                        from backend.app.db.models import Candle
                        q_last_candle = select(Candle.close).where(
                            (Candle.ticker == pos.ticker) & (Candle.timeframe == "5m")
                        ).order_by(Candle.timestamp.desc()).limit(1)
                        res_lc = await session.execute(q_last_candle)
                        db_close = res_lc.scalar()
                        if db_close is not None:
                            current_price = float(db_close)
                            print(f"   ✅ DB Fallback Price Found: ₹{current_price}")
                        else:
                            current_price = float(pos.highest_seen or pos.entry_spot_price or 0.0)
                            print(f"   ⚠️ No DB candle found. Using fallback: ₹{current_price}")
                    else:
                        print(f"⚠️ Warning: No live price for {lookup_ticker} (DB Ref: {ticker}). Skipping evaluation.")
                        continue

                # --- OPTIONS FIREFIGHTER (ADJUSTMENT ENGINE) ---
                firefighting_enabled = await redis_service.get_json("firefighting_enabled")
                if firefighting_enabled:
                    from backend.app.services.firefighter_service import firefighter_service
                    # adj_data must be defined before the check below: if no adjustment
                    # is needed it stays None, otherwise a NameError would be raised and
                    # caught by the outer except — silently skipping this position's
                    # SL/TP evaluation for the cycle (stops would stop working).
                    adj_data = None
                    if firefighter_service.evaluate_adjustment_need(pos, current_price):
                        adj_data = firefighter_service.build_adjustment_trade(pos, current_price)
                    if adj_data:
                        print(f"🚨 [Firefighter] ADJUSTING position {pos.id} ({pos.ticker}) | Bias: {pos.bias} | Spot: {current_price}")
                        original_credit = pos.original_net_credit or pos.net_credit_per_share or Decimal("0.0")
                        adj_credit = adj_data["net_credit_per_share"]
                        
                        pos.is_adjusted = True
                        pos.adjustment_count = (pos.adjustment_count or 0) + 1
                        pos.original_net_credit = original_credit
                        pos.adjusted_net_credit = original_credit + adj_credit
                        
                        context = pos.learning_context or {}
                        
                        if pos.strategy_type == "CALENDAR_SPREAD":
                            # Calendar Roll
                            old_strike = float(pos.leg_1_sell)
                            new_strike = float(adj_data["leg_1_sell"])
                            pos.leg_1_sell = adj_data["leg_1_sell"]
                            pos.entry_spot_price = adj_data["leg_1_sell"] # Reset profit center
                            
                            rolls = context.setdefault("rolls", [])
                            rolls.append({
                                "date": now.isoformat(),
                                "old_strike": old_strike,
                                "new_strike": new_strike,
                                "credit_collected": float(adj_credit)
                            })
                        else:
                            # Credit Spread -> Convert to Iron Condor
                            context["iron_condor_adjustment"] = {
                                "date": now.isoformat(),
                                "opposing_strategy": adj_data["strategy_type"],
                                "leg_1_sell": float(adj_data["leg_1_sell"]),
                                "leg_2_buy": float(adj_data["leg_2_buy"]),
                                "credit_collected": float(adj_credit)
                            }
                            
                        pos.learning_context = context
                        session.add(pos)
                        await session.commit()
                        print(f"🛡️ [Firefighter] Adjustment successfully executed and recorded for position {pos.id}!")
                        # Skip evaluating exits in the same cycle to allow the adjustment to settle
                        continue

                # Technical EMA Stop: Calculate 26-EMA on daily
                exit_triggered = False
                exit_reason = ""
                realized_pnl = 0.0

                bias = pos.bias
                short_strike = float(pos.leg_1_sell)
                entry_price = float(pos.entry_spot_price)
                net_credit = float(pos.adjusted_net_credit if pos.adjusted_net_credit is not None else pos.net_credit_per_share)
                lots = int(pos.lots_sized)
                lot_size = self.risk_shield.get_lot_size(ticker)
                total_multiplier = lots * lot_size

                highest_seen = float(pos.highest_seen or current_price)
                lowest_seen = float(pos.lowest_seen or current_price)
                dynamic_sl = float(pos.dynamic_sl or (short_strike if short_strike > 0 else entry_price))

                is_credit = pos.strategy_type in ["BULL_PUT_SPREAD", "BEAR_CALL_SPREAD", "DELTA_NEUTRAL", "COVERED_CALL", "CASH_SECURED_PUT"]
                
                # Determine delta
                if pos.strategy_type in ["DEBIT_BULL_SPREAD", "DEBIT_BEAR_SPREAD"]:
                    delta = 0.30
                elif pos.strategy_type == "CALENDAR_SPREAD":
                    delta = 0.05
                elif pos.strategy_type == "DIAGONAL_SPREAD":
                    delta = 0.20
                elif pos.strategy_type == "DELTA_NEUTRAL":
                    delta = 0.02
                elif pos.strategy_type in ["COVERED_CALL", "CASH_SECURED_PUT"]:
                    delta = 0.30
                else: # Default Credit Spread
                    delta = 0.15

                brain = self._load_brain_config()
                clean_ticker = ticker.replace("^", "").replace(".NS", "").replace(".BO", "")
                ticker_config = brain.get(ticker, brain.get(clean_ticker, {}))
                sl_ratio = float(ticker_config.get("stop_loss_pct", 1.0))
                be_lock_ratio = float(ticker_config.get("breakeven_lock_pct", 0.30))

                has_reached_be_lock = False
                
                # Calculate hours elapsed since trade entry (for theta decay simulation)
                hours_elapsed = 0.0
                if pos.entry_date:
                    time_diff = now - pos.entry_date
                    hours_elapsed = time_diff.total_seconds() / 3600.0

                # Realized PnL logic
                if is_credit:
                    # Short Option math (Credit)
                    if pos.strategy_type == "DELTA_NEUTRAL":
                        highest_seen = max(highest_seen, current_price)
                        pos.highest_seen = Decimal(str(round(highest_seen, 2)))
                        lowest_seen = min(lowest_seen, current_price)
                        pos.lowest_seen = Decimal(str(round(lowest_seen, 2)))
                        
                        price_change = abs(current_price - entry_price)
                        decay = net_credit * 0.10 * hours_elapsed
                        current_spread_value = net_credit + (price_change * delta) - decay
                        
                        is_tp = current_spread_value <= net_credit * 0.50 # 50% decay captured
                        is_sl = current_spread_value >= net_credit * 1.80  # 80% loss
                        realized_pnl_per_share = net_credit - current_spread_value
                        
                        spot_sl_est = entry_price + (net_credit * 0.80 / delta)
                        pos.dynamic_sl = Decimal(str(round(spot_sl_est, 2)))
                    elif pos.strategy_type == "COVERED_CALL":
                        highest_seen = max(highest_seen, current_price)
                        pos.highest_seen = Decimal(str(round(highest_seen, 2)))
                        lowest_seen = min(lowest_seen, current_price)
                        pos.lowest_seen = Decimal(str(round(lowest_seen, 2)))
                        
                        spot_change = current_price - entry_price
                        decay = net_credit * 0.05 * hours_elapsed
                        current_call_value = net_credit + (spot_change * delta) - decay
                        
                        realized_pnl_per_share = spot_change - (current_call_value - net_credit)
                        max_profit_pts = 100.0 + net_credit
                        is_tp = realized_pnl_per_share >= max_profit_pts * 0.95
                        is_sl = realized_pnl_per_share <= -200.0
                        
                        if is_tp:
                            realized_pnl_per_share = max_profit_pts
                        elif is_sl:
                            realized_pnl_per_share = -200.0
                            
                        spot_sl_est = entry_price - 200.0
                        pos.dynamic_sl = Decimal(str(round(spot_sl_est, 2)))
                    elif pos.strategy_type == "CASH_SECURED_PUT":
                        highest_seen = max(highest_seen, current_price)
                        pos.highest_seen = Decimal(str(round(highest_seen, 2)))
                        lowest_seen = min(lowest_seen, current_price)
                        pos.lowest_seen = Decimal(str(round(lowest_seen, 2)))
                        
                        spot_change = current_price - entry_price
                        decay = net_credit * 0.05 * hours_elapsed
                        current_put_value = net_credit - (spot_change * delta) - decay
                        
                        realized_pnl_per_share = net_credit - current_put_value
                        is_tp = current_put_value <= net_credit * 0.20
                        is_sl = current_put_value >= net_credit * 2.0
                        
                        if is_tp:
                            realized_pnl_per_share = net_credit * 0.80
                        elif is_sl:
                            realized_pnl_per_share = -net_credit * 1.0
                            
                        spot_sl_est = entry_price - (net_credit * 1.0 / delta)
                        pos.dynamic_sl = Decimal(str(round(spot_sl_est, 2)))
                    else: # Standard Credit Spreads (BULL_PUT_SPREAD or BEAR_CALL_SPREAD)
                        if bias == "BULLISH":
                            highest_seen = max(highest_seen, current_price)
                            pos.highest_seen = Decimal(str(round(highest_seen, 2)))
                            lowest_seen = min(lowest_seen, current_price)
                            pos.lowest_seen = Decimal(str(round(lowest_seen, 2)))
                            
                            price_change = current_price - entry_price
                            current_spread_value = net_credit - (price_change * delta)
                            
                            has_reached_be_lock = (highest_seen - entry_price) * delta >= net_credit * be_lock_ratio
                            
                            if has_reached_be_lock:
                                spot_sl_est = entry_price
                                is_sl = current_spread_value >= net_credit
                            else:
                                spot_sl_est = entry_price - (net_credit * sl_ratio / delta)
                                is_sl = current_spread_value >= net_credit * (1.0 + sl_ratio)
                                
                            pos.dynamic_sl = Decimal(str(round(spot_sl_est, 2)))
                            is_tp = current_spread_value <= net_credit * 0.20
                            realized_pnl_per_share = net_credit - current_spread_value
                        else: # BEARISH
                            lowest_seen = min(lowest_seen, current_price)
                            pos.lowest_seen = Decimal(str(round(lowest_seen, 2)))
                            highest_seen = max(highest_seen, current_price)
                            pos.highest_seen = Decimal(str(round(highest_seen, 2)))
                            
                            price_change = entry_price - current_price
                            current_spread_value = net_credit - (price_change * delta)
                            
                            has_reached_be_lock = (entry_price - lowest_seen) * delta >= net_credit * be_lock_ratio
                            
                            if has_reached_be_lock:
                                spot_sl_est = entry_price
                                is_sl = current_spread_value >= net_credit
                            else:
                                spot_sl_est = entry_price + (net_credit * sl_ratio / delta)
                                is_sl = current_spread_value >= net_credit * (1.0 + sl_ratio)
                                
                            pos.dynamic_sl = Decimal(str(round(spot_sl_est, 2)))
                            is_tp = current_spread_value <= net_credit * 0.20
                            realized_pnl_per_share = net_credit - current_spread_value
                else:
                    # Long Option math (Debit / Calendar / Diagonal)
                    premium_paid = net_credit
                    if pos.strategy_type == "CALENDAR_SPREAD":
                        # Neutral Debit: profits on consolidation (weekly decays faster), loses on violent moves
                        highest_seen = max(highest_seen, current_price)
                        pos.highest_seen = Decimal(str(round(highest_seen, 2)))
                        lowest_seen = min(lowest_seen, current_price)
                        pos.lowest_seen = Decimal(str(round(lowest_seen, 2)))
                        
                        price_change = abs(current_price - entry_price)
                        appreciation = premium_paid * 0.08 * hours_elapsed
                        loss_from_delta = price_change * delta
                        current_option_value = premium_paid + appreciation - loss_from_delta
                        
                        is_tp = current_option_value >= premium_paid * 1.35 # 35% gain
                        is_sl = current_option_value <= premium_paid * 0.75 # 25% loss
                        realized_pnl_per_share = current_option_value - premium_paid
                        
                        spot_sl_est = entry_price + (premium_paid * 0.25 / delta)
                        pos.dynamic_sl = Decimal(str(round(spot_sl_est, 2)))
                    elif pos.strategy_type == "DIAGONAL_SPREAD":
                        highest_seen = max(highest_seen, current_price)
                        pos.highest_seen = Decimal(str(round(highest_seen, 2)))
                        lowest_seen = min(lowest_seen, current_price)
                        pos.lowest_seen = Decimal(str(round(lowest_seen, 2)))
                        
                        price_change = abs(current_price - entry_price)
                        appreciation = premium_paid * 0.06 * hours_elapsed
                        loss_from_delta = price_change * delta
                        current_option_value = premium_paid + appreciation - loss_from_delta
                        
                        is_tp = current_option_value >= premium_paid * 1.40 # 40% gain
                        is_sl = current_option_value <= premium_paid * 0.75 # 25% loss
                        realized_pnl_per_share = current_option_value - premium_paid
                        
                        spot_sl_est = entry_price + (premium_paid * 0.25 / delta)
                        pos.dynamic_sl = Decimal(str(round(spot_sl_est, 2)))
                    else: # Standard Debit Spreads
                        if bias == "BULLISH":
                            highest_seen = max(highest_seen, current_price)
                            pos.highest_seen = Decimal(str(round(highest_seen, 2)))
                            lowest_seen = min(lowest_seen, current_price)
                            pos.lowest_seen = Decimal(str(round(lowest_seen, 2)))
                            
                            price_change = current_price - entry_price
                            current_option_value = premium_paid + (price_change * delta)
                            
                            has_reached_be_lock = (highest_seen - entry_price) * delta >= premium_paid * be_lock_ratio
                            
                            if has_reached_be_lock:
                                spot_sl_est = entry_price
                                is_sl = current_option_value <= premium_paid
                            else:
                                spot_sl_est = entry_price - (premium_paid * 0.30 / delta)
                                is_sl = current_option_value <= premium_paid * 0.70
                                
                            pos.dynamic_sl = Decimal(str(round(spot_sl_est, 2)))
                            is_tp = current_option_value >= premium_paid * 1.50
                            realized_pnl_per_share = current_option_value - premium_paid
                        else: # BEARISH
                            lowest_seen = min(lowest_seen, current_price)
                            pos.lowest_seen = Decimal(str(round(lowest_seen, 2)))
                            highest_seen = max(highest_seen, current_price)
                            pos.highest_seen = Decimal(str(round(highest_seen, 2)))
                            
                            price_change = entry_price - current_price
                            current_option_value = premium_paid + (price_change * delta)
                            
                            has_reached_be_lock = (entry_price - lowest_seen) * delta >= premium_paid * be_lock_ratio
                            
                            if has_reached_be_lock:
                                spot_sl_est = entry_price
                                is_sl = current_option_value <= premium_paid
                            else:
                                spot_sl_est = entry_price + (premium_paid * 0.30 / delta)
                                is_sl = current_option_value <= premium_paid * 0.70
                                
                            pos.dynamic_sl = Decimal(str(round(spot_sl_est, 2)))
                            is_tp = current_option_value >= premium_paid * 1.50
                            realized_pnl_per_share = current_option_value - premium_paid

                # Exit logic
                if is_eod:
                    exit_triggered = True
                    exit_reason = "⏰ EOD SQUARE OFF (Time Stop at 3:15 PM)"
                    realized_pnl = realized_pnl_per_share * total_multiplier
                elif is_tp:
                    exit_triggered = True
                    exit_reason = f"🎯 TAKE PROFIT ({pos.strategy_type})"
                    # P&L calculations
                    if pos.strategy_type == "DELTA_NEUTRAL":
                        realized_pnl = net_credit * 0.50 * total_multiplier
                    elif pos.strategy_type == "CALENDAR_SPREAD":
                        realized_pnl = premium_paid * 0.35 * total_multiplier
                    elif pos.strategy_type == "DIAGONAL_SPREAD":
                        realized_pnl = premium_paid * 0.40 * total_multiplier
                    else:
                        realized_pnl = (net_credit * 0.80 if is_credit else premium_paid * 0.50) * total_multiplier
                elif is_sl:
                    exit_triggered = True
                    if has_reached_be_lock:
                        exit_reason = "🛡️ TRAILING STOP (Breakeven)"
                        realized_pnl = 0.0
                    else:
                        exit_reason = f"🛑 STOP LOSS ({pos.strategy_type})"
                        if pos.strategy_type == "DELTA_NEUTRAL":
                            realized_pnl = -net_credit * 0.80 * total_multiplier
                        elif pos.strategy_type == "CALENDAR_SPREAD":
                            realized_pnl = -premium_paid * 0.25 * total_multiplier
                        elif pos.strategy_type == "DIAGONAL_SPREAD":
                            realized_pnl = -premium_paid * 0.25 * total_multiplier
                        else:
                            realized_pnl = (-net_credit * sl_ratio if is_credit else -premium_paid * 0.30) * total_multiplier


                if exit_triggered:
                    exit_pnl_total = realized_pnl
                    exit_data = {
                        "exit_date": now,
                        "exit_price": Decimal(str(round(current_price, 2))),
                        "exit_reason": exit_reason,
                        "realized_pnl": Decimal(str(round(exit_pnl_total, 2)))
                    }
                    closed_trade = await database_service.close_position(session, pos.id, exit_data)
                    if closed_trade:
                        closed_trades_report.append(closed_trade.model_dump())
                        print(f"   -> EXIT TRIGGERED: {ticker} | Reason: {exit_reason} | PnL: ₹{exit_pnl_total:.2f}")
                else:
                    session.add(pos)
                    await session.commit()
                    print(f"   -> HOLD: {ticker} (Current: ₹{current_price:.2f} | Trailing SL: ₹{dynamic_sl:.2f})")

            except Exception as e:
                print(f"   -> Error evaluating {ticker}: {e}")

        return closed_trades_report

execution_service = ExecutionService()
