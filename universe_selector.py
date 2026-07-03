class UniverseSelector:
    def __init__(self):
        # The "Institutional Tier" - Heavyweight Indices + Top Liquid F&O Stocks
        self.liquid_universe = [
            # --- THE MAJOR INDICES (Highest Priority) ---
            "^NSEI",     # Nifty 50
            "^NSEBANK",  # BankNifty
            "^BSESN",    # Sensex
            
            # --- THE HEAVYWEIGHT STOCKS ---
            "HDFCBANK.NS", "RELIANCE.NS", "ICICIBANK.NS", "INFY.NS", 
            "TCS.NS", "ITC.NS", "SBIN.NS", "BHARTIARTL.NS", 
            "AXISBANK.NS", "KOTAKBANK.NS", "LT.NS", "TATAMOTORS.NS", 
            "MARUTI.NS", "SUNPHARMA.NS", "BAJFINANCE.NS", "ASIANPAINT.NS",
            "HINDUNILVR.NS", "TITAN.NS", "M&M.NS", "ULTRACEMCO.NS"
        ]

    def get_todays_universe(self):
        """
        Returns the high-liquidity universe ready for daily or intraday scanning.
        Prioritizes the major indices for maximum order block reliability.
        """
        print("🌐 INSTITUTIONAL LIQUIDITY UNIVERSE LOADED (Index-First Routing).")
        return {
            "target_index_ticker": "^NSEI", # Primary Benchmark
            "backup_stocks": self.liquid_universe,
            "day_of_week": "Any" # Prevents any KeyError in the terminal
        }