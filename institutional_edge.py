import pandas as pd
import numpy as np
import datetime
from risk_shield import RiskShield

class InstitutionalEdgeEngine:
    def __init__(self):
        self.shield = RiskShield()
        self.market_multiplier = 50 # Nifty Multiplier

    def calculate_gex_matrix(self, spot_price, df_chain, expiry_date):
        """
        Calculates Gamma Exposure (GEX) across the option chain.
        This shows where Market Makers are forced to hedge.
        """
        try:
            if df_chain.empty:
                return pd.DataFrame()

            # Pre-filter for liquidity and distance (ATM +/- 20 strikes)
            df_chain['Distance'] = abs(df_chain['Strike'] - spot_price)
            df_chain = df_chain.sort_values('Distance').head(40).copy()
            
            # Assume constant IV for GEX calculation if not provided (approx 15%)
            iv = 0.15
            
            gex_data = []
            for _, row in df_chain.iterrows():
                strike = float(row['Strike'])
                
                # Calculate Gamma for this strike
                gamma = self.shield.calculate_gamma(spot_price, strike, expiry_date, iv)
                
                # Strike GEX (Nominal Value)
                # Formula: OI * Gamma * Spot * Multiplier * 0.01
                call_oi = float(row.get('Call_OI', row.get('Call_COI', 0)))
                put_oi = float(row.get('Put_OI', row.get('Put_COI', 0)))
                
                # Net GEX for the strike
                # Calls usually create Positive GEX (Stabilizing)
                # Puts create Negative GEX (Destabilizing)
                call_gex = (call_oi * gamma * spot_price * self.market_multiplier * 0.01)
                put_gex = -(put_oi * gamma * spot_price * self.market_multiplier * 0.01)
                
                gex_data.append({
                    "Strike": strike,
                    "Call_GEX": call_gex,
                    "Put_GEX": put_gex,
                    "Total_GEX": call_gex + put_gex
                })
                
            return pd.DataFrame(gex_data).sort_values('Strike')
            
        except Exception as e:
            print(f"⚠️ GEX Error: {e}")
            return pd.DataFrame()

    def get_volatility_regime(self, total_gex):
        """
        Categorizes market regime based on GEX.
        - High GEX (> 1Bn): Low Volatility (MMs suppressing moves)
        - Moderate GEX (0 to 1Bn): Normal Regime
        - Negative GEX (< 0): Toxic Volatility (MMs accelerating moves)
        """
        if total_gex < 0:
            return "TOXIC_SHORT_GAMMA"
        elif total_gex > 1000000000: # 1 Billion GEX (Approx for Nifty)
            return "STABLE_LONG_GAMMA"
        else:
            return "TRANSITION_ZONE"

    def calculate_vanna_charm(self, spot, strike, expiry_date, volatility, put_call='c'):
        """
        Calculates second-order Greeks for advanced institutional hedging.
        - Vanna: Sensitivity of Delta to Volatility (dDelta/dVol)
        - Charm: Sensitivity of Delta to Time (dDelta/dTime)
        """
        days_to_expiry = (expiry_date - datetime.date.today()).days
        if days_to_expiry <= 0: days_to_expiry = 0.1
        
        # We use a small bump to calculate finite difference sensitivities
        vol_pct = volatility * 100
        vol_bump = 0.01
        
        c1 = mibian.BS([spot, strike, self.shield.rf, days_to_expiry], volatility=vol_pct)
        c2 = mibian.BS([spot, strike, self.shield.rf, days_to_expiry], volatility=vol_pct + vol_bump)
        
        # Vanna Calculation
        d1 = c1.callDelta if put_call == 'c' else c1.putDelta
        d2 = c2.callDelta if put_call == 'c' else c2.putDelta
        vanna = (d2 - d1) / (vol_bump / 100) # Sensitivity per 1% vol move
        
        # Charm Calculation (Delta decay over 1 day)
        c_next_day = mibian.BS([spot, strike, self.shield.rf, max(0.01, days_to_expiry - 1)], volatility=vol_pct)
        d_next_day = c_next_day.callDelta if put_call == 'c' else c_next_day.putDelta
        charm = (d_next_day - d1) # Change in delta over 24 hours
        
        return {
            "vanna": round(vanna, 4),
            "charm": round(charm, 4)
        }
    def identify_gamma_levels(self, df_gex):
        """
        Identifies critical institutional levels:
        1. Gamma Flip Strike (Where Total GEX crosses 0)
        2. High GEX Wall (Highest Positive GEX strike)
        3. Low GEX Floor (Highest Negative GEX strike)
        """
        if df_gex.empty:
            return {}

        # 1. Gamma Flip (Simple search for sign change)
        flip_strike = 0
        df_gex['Prev_Total'] = df_gex['Total_GEX'].shift(1)
        flips = df_gex[(df_gex['Total_GEX'] * df_gex['Prev_Total'] < 0)]
        if not flips.empty:
            flip_strike = flips.iloc[0]['Strike']

        # 2. Gamma Walls
        call_wall = df_gex.loc[df_gex['Call_GEX'].idxmax()]['Strike']
        put_wall = df_gex.loc[df_gex['Put_GEX'].idxmin()]['Strike'] # Min because Put GEX is negative

        return {
            "gamma_flip": flip_strike,
            "call_wall": call_wall,
            "put_wall": put_wall
        }
    def calculate_volume_profile(self, df_intraday):
        """
        Identifies the Point of Control (POC) and Value Area.
        High Volume Nodes (HVN) act as Institutional Magnets.
        """
        try:
            if df_intraday.empty:
                return {"poc": 0, "hvn": []}
            
            # Group price into bins (5 INR steps for Nifty)
            bin_size = 5
            df_intraday['Price_Bin'] = (df_intraday['price'] / bin_size).round() * bin_size
            
            profile = df_intraday.groupby('Price_Bin')['Volume'].sum() if 'Volume' in df_intraday else df_intraday.groupby('Price_Bin').size()
            
            poc = profile.idxmax()
            
            # Identify other High Volume Nodes (top 3)
            hvn = profile.sort_values(ascending=False).head(3).index.tolist()
            
            return {
                "poc": float(poc),
                "hvn": [float(x) for x in hvn]
            }
        except Exception as e:
            print(f"⚠️ VOC Error: {e}")
            return {"poc": 0, "hvn": []}

if __name__ == "__main__":
    # Test
    engine = InstitutionalEdgeEngine()
    print("Institutional Engine Initialized.")
