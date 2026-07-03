"""
[LEGACY - v7] DO NOT USE FOR THE v8 LIVE SYSTEM.

This root-level RiskShield belongs to the deprecated v7 Streamlit stack and has
DIFFERENT, INCOMPATIBLE risk limits and API from the authoritative v8 shield at
`backend/app/core/risk_shield.py` (e.g. max_daily_loss 5000 vs 10000,
max_open_positions 1 vs 5, GEX threshold 0 vs -1000, no VaR/lot-size lookup).

Two divergent sources of truth for risk limits is how accidents happen. The v8
microservices (start_v8.bat) import ONLY the backend version. This file is kept
solely so legacy analysis scripts still import; new work must use the backend one.
"""
import warnings
import mibian
import numpy as np
import pandas as pd
import os
from datetime import datetime, date

warnings.warn(
    "root risk_shield.py is LEGACY (v7). Use backend.app.core.risk_shield for the "
    "v8 system — its limits and API differ.",
    DeprecationWarning,
    stacklevel=2,
)


class RiskShield:
    def __init__(self, risk_free_rate=0.07):
        self.rf = risk_free_rate * 100 # mibian expects percentage
        
        # --- INSTITUTIONAL HARD LIMITS ---
        self.max_daily_loss = 5000.00   # Absolute INR limit per day
        self.max_drawdown_pct = 0.05    # 5% Max Drawdown from Peak
        self.max_open_positions = 1     # STRICT: Reduced to 1 per asset to stop over-trading
        self.max_var_threshold = 15000  # Max Portfolio VaR allowed
        
        # --- AI AUDIT MANDATED FILTERS ---
        self.min_trade_spacing_mins = 15 # 15-minute cooldown between entries
        self.max_consecutive_losses = 3  # Pause after 3 losses
        self.gex_volatility_threshold = 0 # Cannot trade if GEX is negative

    def check_kill_switches(self, current_daily_pnl, open_positions_count, total_gex=0.0, last_trade_time=None, current_consecutive_losses=0):
        """
        The Final Gate. Implements AI-mandated Hard Filters.
        """
        # 1. Volatility Filter: GEX Check
        if total_gex < self.gex_volatility_threshold:
            return True, f"TOXIC_VOLATILITY: GEX is negative ({total_gex:,.0f}). Market Makers forced to sell."

        # 2. Consecutive Loss Filter (Circuit Breaker)
        if current_consecutive_losses >= self.max_consecutive_losses:
            return True, f"CIRCUIT_BREAKER: {current_consecutive_losses} consecutive losses. Pausing for strategy reassessment."

        # 3. Time-Based Filter (Cooldown)
        if last_trade_time:
            time_diff = (datetime.now() - last_trade_time).total_seconds() / 60
            if time_diff < self.min_trade_spacing_mins:
                return True, f"ENTRY_COOLDOWN: Only {time_diff:.1f} mins since last trade. Wait for {self.min_trade_spacing_mins} mins."

        # 4. Daily Loss Limit
        if current_daily_pnl <= -self.max_daily_loss:
            return True, f"DAILY_LOSS_LIMIT_EXCEEDED: ₹{current_daily_pnl}"

        # 5. Max Open Positions
        if open_positions_count >= self.max_open_positions:
            return True, f"MAX_POSITIONS_REACHED: {open_positions_count}"

        # 6. Emergency Kill Switch
        if os.path.exists("PANIC_BUTTON.txt"):
            return True, "MANUAL_PANIC_TRIGGERED"

        return False, None

    def calculate_gamma(self, spot, strike, expiry_date, volatility):
        """Calculates the raw Gamma of an option leg."""
        if isinstance(expiry_date, str):
            expiry_date = datetime.strptime(expiry_date, "%Y-%m-%d").date()
        
        days_to_expiry = (expiry_date - date.today()).days
        if days_to_expiry <= 0: days_to_expiry = 0.1
            
        c = mibian.BS([spot, strike, self.rf, days_to_expiry], volatility=volatility*100)
        return c.gamma

    def calculate_greeks(self, spot, strike, expiry_date, volatility, put_call='c'):
        """Calculates Greeks for a single option leg."""
        # Calculate days to expiry
        if isinstance(expiry_date, str):
            expiry_date = datetime.strptime(expiry_date, "%Y-%m-%d").date()
        
        days_to_expiry = (expiry_date - date.today()).days
        if days_to_expiry <= 0:
            days_to_expiry = 0.1 # Minimal time for Greeks near expiry
            
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

    def calculate_spread_greeks(self, spot, leg_1_strike, leg_2_strike, expiry_date, volatility, strategy_type):
        """Calculates net Greeks for a Credit Spread."""
        # Bull Put Spread: Sell High Strike Put (Leg 1), Buy Low Strike Put (Leg 2)
        # Bear Call Spread: Sell Low Strike Call (Leg 1), Buy High Strike Call (Leg 2)
        
        if "PUT" in strategy_type.upper():
            g1 = self.calculate_greeks(spot, leg_1_strike, expiry_date, volatility, 'p')
            g2 = self.calculate_greeks(spot, leg_2_strike, expiry_date, volatility, 'p')
            # Net = Long - Short (In Credit Spread, Leg 1 is Short, Leg 2 is Long)
            return {
                "net_delta": g2['delta'] - g1['delta'],
                "net_gamma": g2['gamma'] - g1['gamma'],
                "net_theta": g2['theta'] - g1['theta'],
                "net_vega": g2['vega'] - g1['vega']
            }
        else: # CALL spread
            g1 = self.calculate_greeks(spot, leg_1_strike, expiry_date, volatility, 'c')
            g2 = self.calculate_greeks(spot, leg_2_strike, expiry_date, volatility, 'c')
            return {
                "net_delta": g2['delta'] - g1['delta'],
                "net_gamma": g2['gamma'] - g1['gamma'],
                "net_theta": g2['theta'] - g1['theta'],
                "net_vega": g2['vega'] - g1['vega']
            }

    def calculate_portfolio_var(self, positions, confidence_level=0.95, horizon_days=1):
        """
        Calculates Portfolio Value at Risk (VaR) using Delta-Normal approach.
        Simple version for institutional simulation.
        """
        if not positions:
            return 0.0

        total_portfolio_delta_value = 0
        portfolio_variance = 0
        
        # Assumption: We use NIFTY's daily volatility as a proxy for market risk
        # In a real institutional setup, we'd use a covariance matrix of all assets.
        market_daily_vol = 0.012 # 1.2% daily vol estimate for Nifty
        
        for pos in positions:
            # We estimate the 'Value' of the delta position
            # Delta * Spot * Lots * Multiplier
            spot = float(pos.get('spot_price', 0))
            delta = float(pos.get('net_delta', 0))
            lots = int(pos.get('lots_sized', 1))
            multiplier = 65 # Nifty Multiplier
            
            delta_value = delta * spot * lots * multiplier
            total_portfolio_delta_value += delta_value
            
        # VaR = Portfolio Delta Value * Market Vol * Z-Score
        z_score = 1.645 if confidence_level == 0.95 else 2.326 # 99%
        
        var_value = abs(total_portfolio_delta_value) * market_daily_vol * z_score * np.sqrt(horizon_days)
        
        return round(var_value, 2)

if __name__ == "__main__":
    shield = RiskShield()
    # Test Greek Calc
    res = shield.calculate_spread_greeks(
        spot=24000, 
        leg_1_strike=24100, 
        leg_2_strike=24200, 
        expiry_date="2026-05-14", 
        volatility=0.15, 
        strategy_type="BEAR_CALL_SPREAD"
    )
    print(f"Spread Greeks: {res}")
