import mibian
import numpy as np
import os
import logging
from datetime import datetime, date
from typing import List, Dict, Optional, Tuple, Any
from decimal import Decimal

logger = logging.getLogger("RiskShield")

class RiskShield:
    def __init__(self, risk_free_rate=0.07):
        self.rf = risk_free_rate * 100 # mibian expects percentage
        
        # --- INSTITUTIONAL HARD LIMITS ---
        self.max_daily_loss = 10000.00   # Absolute INR limit per day
        self.max_drawdown_pct = 0.05     # 5% Max Drawdown from Peak
        self.max_open_positions = 5      # Increased for portfolio diversification
        self.max_var_threshold = 25000   # Max Portfolio VaR allowed
        
        # --- AI AUDIT MANDATED FILTERS ---
        self.min_trade_spacing_mins = 10 # 10-minute cooldown between entries
        self.max_consecutive_losses = 3  # Pause after 3 losses
        self.gex_volatility_threshold = -1000 # Allow slight negative GEX but monitor
        
        # --- EXCHANGE LOT SIZES ---
        # Authoritative values come from the scrip master at runtime via
        # scrip_master.get_lot_size(). This dict is only a last-resort fallback
        # (values reflect the 2026 scrip master; do NOT trust indefinitely).
        self.LOT_SIZES = {
            "NIFTY": 65,
            "BANKNIFTY": 30,
            "FINNIFTY": 60,
            "MIDCPNIFTY": 120,
            "SENSEX": 20,
            "BANKEX": 30,
            "RELIANCE": 500,
            "HDFCBANK": 650
        }

    def get_lot_size(self, ticker: str) -> int:
        """Returns the lot size for a ticker, preferring the scrip master."""
        clean_ticker = ticker.replace("^", "").replace(".NS", "").replace(".BO", "")
        try:
            from backend.app.core import scrip_master
            size = scrip_master.get_lot_size(ticker)
            if size and size > 1:
                return size
        except Exception as e:
            logger.debug(f"Scrip-master lot lookup failed for {ticker}: {e}")
        # Fallback to the local table (handles NSEI/NSEBANK aliases too)
        alias = {"NSEI": "NIFTY", "NSEBANK": "BANKNIFTY"}.get(clean_ticker, clean_ticker)
        return self.LOT_SIZES.get(alias, 1)

    def check_kill_switches(
        self, 
        current_daily_pnl: float, 
        open_positions_count: int, 
        total_gex: float = 0.0, 
        last_trade_time: Optional[datetime] = None, 
        current_consecutive_losses: int = 0,
        portfolio_var: float = 0.0
    ) -> Tuple[bool, Optional[str]]:
        """
        The Final Gate. Implements AI-mandated Hard Filters.
        """
        # 1. Volatility Filter: GEX Check
        if total_gex < self.gex_volatility_threshold:
            return True, f"TOXIC_VOLATILITY: GEX is critically negative ({total_gex:,.0f})."

        # 2. Consecutive Loss Filter (Circuit Breaker)
        if current_consecutive_losses >= self.max_consecutive_losses:
            return True, f"CIRCUIT_BREAKER: {current_consecutive_losses} consecutive losses. Pausing."

        # 3. Time-Based Filter (Cooldown)
        if last_trade_time:
            time_diff = (datetime.now() - last_trade_time).total_seconds() / 60
            if time_diff < self.min_trade_spacing_mins:
                return True, f"ENTRY_COOLDOWN: Only {time_diff:.1f} mins since last trade."

        # 4. Daily Loss Limit
        if current_daily_pnl <= -self.max_daily_loss:
            return True, f"DAILY_LOSS_LIMIT_EXCEEDED: ₹{current_daily_pnl}"

        # 5. Max Open Positions
        if open_positions_count >= self.max_open_positions:
            return True, f"MAX_POSITIONS_REACHED: {open_positions_count}"
            
        # 6. Portfolio VaR Limit
        if portfolio_var >= self.max_var_threshold:
            return True, f"PORTFOLIO_VAR_LIMIT: VaR ₹{portfolio_var:,.2f} exceeds threshold."

        # 7. Emergency Kill Switch
        if os.path.exists("PANIC_BUTTON.txt"):
            return True, "MANUAL_PANIC_TRIGGERED"

        return False, None

    def calculate_greeks(self, spot: float, strike: float, expiry_date: date, volatility: float, put_call: str = 'c') -> Dict[str, float]:
        """Calculates Greeks for a single option leg."""
        days_to_expiry = (expiry_date - date.today()).days
        if days_to_expiry <= 0:
            days_to_expiry = 0.1
            
        # Mibian inputs: [Price, Strike, InterestRate, DaysToExpiry]
        try:
            c = mibian.BS([spot, strike, self.rf, days_to_expiry], volatility=volatility*100)
            
            if put_call.lower() == 'c':
                return {
                    "delta": c.callDelta,
                    "gamma": c.gamma,
                    "theta": c.callTheta,
                    "vega": c.vega
                }
            else:
                return {
                    "delta": c.putDelta,
                    "gamma": c.gamma,
                    "theta": c.putTheta,
                    "vega": c.vega
                }
        except Exception as e:
            logger.error(f"Greeks Calc Error: {e}")
            return {"delta": 0, "gamma": 0, "theta": 0, "vega": 0}

    def calculate_spread_greeks(self, spot: float, leg_1_strike: float, leg_2_strike: float, expiry_date: date, volatility: float, strategy_type: str) -> Dict[str, float]:
        """Calculates net Greeks for a Credit Spread, Debit Spread, or custom layouts."""
        strategy_upper = strategy_type.upper()
        
        # New Layouts Greeks Calculations
        if "DIAGONAL_SPREAD" in strategy_upper:
            import datetime
            put_call = 'p' if "PUT" in strategy_upper else 'c'
            weekly_g = self.calculate_greeks(spot, leg_1_strike, expiry_date, volatility, put_call)
            monthly_g = self.calculate_greeks(spot, leg_2_strike, expiry_date + datetime.timedelta(days=28), volatility, put_call)
            return {
                "net_delta": monthly_g['delta'] - weekly_g['delta'],
                "net_gamma": monthly_g['gamma'] - weekly_g['gamma'],
                "net_theta": monthly_g['theta'] - weekly_g['theta'],
                "net_vega": monthly_g['vega'] - weekly_g['vega']
            }
            
        if "DELTA_NEUTRAL" in strategy_upper:
            cg = self.calculate_greeks(spot, leg_1_strike, expiry_date, volatility, 'c') # Short Call
            pg = self.calculate_greeks(spot, leg_2_strike, expiry_date, volatility, 'p') # Short Put
            return {
                "net_delta": -cg['delta'] - pg['delta'],
                "net_gamma": -cg['gamma'] - pg['gamma'],
                "net_theta": -cg['theta'] - pg['theta'],
                "net_vega": -cg['vega'] - pg['vega']
            }

        if "COVERED_CALL" in strategy_upper:
            cg = self.calculate_greeks(spot, leg_1_strike, expiry_date, volatility, 'c') # Short Call
            return {
                "net_delta": 1.0 - cg['delta'],
                "net_gamma": -cg['gamma'],
                "net_theta": -cg['theta'],
                "net_vega": -cg['vega']
            }
            
        if "CASH_SECURED_PUT" in strategy_upper:
            pg = self.calculate_greeks(spot, leg_1_strike, expiry_date, volatility, 'p') # Short Put
            return {
                "net_delta": -pg['delta'], # Positive delta since putDelta is negative
                "net_gamma": -pg['gamma'],
                "net_theta": -pg['theta'],
                "net_vega": -pg['vega']
            }

        if "CALENDAR_SPREAD" in strategy_upper:
            # leg_2_strike = ATM strike. Short weekly, long monthly (+28 days)
            import datetime
            weekly_g = self.calculate_greeks(spot, leg_2_strike, expiry_date, volatility, 'c') # Short Weekly Call
            monthly_g = self.calculate_greeks(spot, leg_2_strike, expiry_date + datetime.timedelta(days=28), volatility, 'c') # Long Monthly Call
            return {
                "net_delta": monthly_g['delta'] - weekly_g['delta'],
                "net_gamma": monthly_g['gamma'] - weekly_g['gamma'],
                "net_theta": monthly_g['theta'] - weekly_g['theta'],
                "net_vega": monthly_g['vega'] - weekly_g['vega']
            }

        # Spreads (Debit or Credit)
        if "PUT" in strategy_upper or "LONG_PUT" in strategy_upper:
            g1 = self.calculate_greeks(spot, leg_1_strike, expiry_date, volatility, 'p') # Short Put or OTM leg
            g2 = self.calculate_greeks(spot, leg_2_strike, expiry_date, volatility, 'p') # Long Put or ATM leg
            return {
                "net_delta": g2['delta'] - g1['delta'],
                "net_gamma": g2['gamma'] - g1['gamma'],
                "net_theta": g2['theta'] - g1['theta'],
                "net_vega": g2['vega'] - g1['vega']
            }
        else: # CALL spreads
            g1 = self.calculate_greeks(spot, leg_1_strike, expiry_date, volatility, 'c')
            g2 = self.calculate_greeks(spot, leg_2_strike, expiry_date, volatility, 'c')
            return {
                "net_delta": g2['delta'] - g1['delta'],
                "net_gamma": g2['gamma'] - g1['gamma'],
                "net_theta": g2['theta'] - g1['theta'],
                "net_vega": g2['vega'] - g1['vega']
            }

    def calculate_portfolio_var(self, positions: List[Any], confidence_level=0.95, horizon_days=1) -> float:
        """
        Calculates Portfolio Value at Risk (VaR) using Delta-Normal approach.
        Accepts list of OpenPosition SQLModel objects or dictionaries.
        """
        if not positions:
            return 0.0

        total_portfolio_delta_value = 0
        market_daily_vol = 0.012 # Estimated daily volatility (1.2%)
        
        for pos in positions:
            # Handle both SQLModel objects and dicts
            if hasattr(pos, "ticker"):
                ticker = pos.ticker
                spot = float(pos.spot_price or 0)
                delta = float(pos.net_delta or 0)
                lots = int(pos.lots_sized or 1)
            else:
                ticker = pos.get('ticker', 'UNK')
                spot = float(pos.get('spot_price', 0))
                delta = float(pos.get('net_delta', 0))
                lots = int(pos.get('lots_sized', 1))
            
            multiplier = self.get_lot_size(ticker)
            
            # Delta Value = Delta * Spot * Lots * LotSize
            delta_value = delta * spot * lots * multiplier
            total_portfolio_delta_value += delta_value
            
        z_score = 1.645 if confidence_level == 0.95 else 2.326
        var_value = abs(total_portfolio_delta_value) * market_daily_vol * z_score * np.sqrt(horizon_days)
        
        return round(var_value, 2)

    def get_risk_report(self, positions: List[Any], daily_pnl: float) -> Dict[str, Any]:
        """Generates a comprehensive risk report for the dashboard."""
        var = self.calculate_portfolio_var(positions)
        
        total_exposure = 0
        net_delta = 0
        net_theta = 0
        net_gamma = 0
        net_vega = 0
        
        for pos in positions:
            if hasattr(pos, "ticker"):
                spot = float(pos.spot_price or 0)
                lots = int(pos.lots_sized or 1)
                delta = float(pos.net_delta or 0)
                theta = float(pos.net_theta or 0)
                gamma = float(pos.net_gamma or 0)
                vega = float(pos.net_vega or 0)
                ticker = pos.ticker
            else:
                spot = float(pos.get('spot_price', 0))
                lots = int(pos.get('lots_sized', 1))
                delta = float(pos.get('net_delta', 0))
                theta = float(pos.get('net_theta', 0))
                gamma = float(pos.get('net_gamma', 0))
                vega = float(pos.get('net_vega', 0))
                ticker = pos.get('ticker', 'UNK')
                
            mult = self.get_lot_size(ticker)
            total_exposure += spot * lots * mult
            net_delta += delta * lots * mult
            net_theta += theta * lots * mult
            net_gamma += gamma * lots * mult
            net_vega += vega * lots * mult
            
        # Estimate margin deployed:
        total_margin = 0
        for pos in positions:
            if hasattr(pos, "ticker"):
                ticker = pos.ticker
                lots = int(pos.lots_sized or 1)
                strategy = getattr(pos, "strategy_type", "")
            else:
                ticker = pos.get('ticker', 'UNK')
                lots = int(pos.get('lots_sized', 1))
                strategy = pos.get('strategy_type', '')
            
            clean_ticker = ticker.replace("^", "").replace(".NS", "").replace(".BO", "")
            if clean_ticker in ["NIFTY", "FINNIFTY", "SENSEX", "NSEI"]:
                if strategy == "COVERED_CALL":
                    total_margin += lots * 160000
                elif strategy == "CASH_SECURED_PUT":
                    total_margin += lots * 140000
                elif strategy == "DELTA_NEUTRAL":
                    total_margin += lots * 120000
                elif strategy == "DEBIT_SPREAD":
                    total_margin += lots * 15000
                else:
                    total_margin += lots * 35000
            elif clean_ticker in ["BANKNIFTY", "NSEBANK"]:
                if strategy == "COVERED_CALL":
                    total_margin += lots * 180000
                elif strategy == "CASH_SECURED_PUT":
                    total_margin += lots * 160000
                elif strategy == "DELTA_NEUTRAL":
                    total_margin += lots * 140000
                elif strategy == "DEBIT_SPREAD":
                    total_margin += lots * 18000
                else:
                    total_margin += lots * 40000
            else:
                total_margin += lots * 150000 # Equities
            
        return {
            "portfolio_var": var,
            "daily_pnl": daily_pnl,
            "total_exposure": round(total_exposure, 2),
            "est_margin_deployed": round(total_margin, 2),
            "net_delta": round(net_delta, 4),
            "net_theta": round(net_theta, 4),
            "net_gamma": round(net_gamma, 4),
            "net_vega": round(net_vega, 4),
            "positions_count": len(positions),
            "is_within_limits": bool(daily_pnl > -self.max_daily_loss and var < self.max_var_threshold),
            "timestamp": datetime.now().isoformat()
        }
