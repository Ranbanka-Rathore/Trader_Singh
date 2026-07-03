class OptionsDesk:
    def __init__(self):
        pass

    def _get_strike_interval(self, ticker):
        """Returns the correct NSE strike interval for the asset."""
        if ticker in ["^NSEBANK", "BANKNIFTY", "^BSESN", "SENSEX"]:
            return 100
        elif ticker in ["^NSEI", "NIFTY"]:
            return 50
        else:
            return 10 # Default fallback for heavy liquid stocks (Reliance, TCS, etc.)

    def _round_to_strike(self, price, interval):
        """Rounds a raw price to the nearest valid exchange strike."""
        return round(price / interval) * interval

    def build_bull_put_spread(self, asset_data):
        current_price = asset_data.get('spot_price', asset_data.get('current_price', 0.0))
        ticker = asset_data.get('ticker', 'UNKNOWN')
        interval = self._get_strike_interval(ticker)
        
        # Bullish: Sell Put slightly below (~1% OTM)
        raw_sell = current_price * 0.99
        sell_strike = self._round_to_strike(raw_sell, interval)
        
        # Narrow Spread: Buy Put exactly 1 strike interval below
        buy_strike = sell_strike - interval
        
        strike_width = abs(sell_strike - buy_strike)
        clean_ticker = ticker.replace("^", "").replace(".NS", "").replace(".BO", "")
        is_index = clean_ticker in ["NIFTY", "BANKNIFTY", "FINNIFTY", "SENSEX", "NSEI", "NSEBANK"]
        credit_ratio = 0.15 if is_index else 0.20
        
        net_credit = round(strike_width * credit_ratio, 2)
        max_risk = round(strike_width - net_credit, 2)
        
        return {
            "ticker": ticker,
            "strategy_type": "Bull Put Spread",
            "spot_price": current_price,
            "leg_1_sell": sell_strike,
            "leg_2_buy": buy_strike,
            "net_credit_per_share": net_credit,
            "max_risk_per_share": max_risk,
            "risk_reward_ratio": f"1:{round(max_risk/net_credit, 1) if net_credit > 0 else 0}",
            "win_probability": asset_data.get('win_probability', 85.0),
            "learning_context": asset_data.get("learning_context", {"RSI": 50, "MACD_State": "UNKNOWN", "Dist_from_VWAP": 0, "IB_Status": "UNKNOWN", "PA_Status": "UNKNOWN"})
        }

    def build_bear_call_spread(self, asset_data):
        current_price = asset_data.get('spot_price', asset_data.get('current_price', 0.0))
        ticker = asset_data.get('ticker', 'UNKNOWN')
        interval = self._get_strike_interval(ticker)
        
        # Bearish: Sell Call slightly above (~1% OTM)
        raw_sell = current_price * 1.01
        sell_strike = self._round_to_strike(raw_sell, interval)
        
        # Narrow Spread: Buy Call exactly 1 strike interval above
        buy_strike = sell_strike + interval
        
        strike_width = abs(buy_strike - sell_strike)
        clean_ticker = ticker.replace("^", "").replace(".NS", "").replace(".BO", "")
        is_index = clean_ticker in ["NIFTY", "BANKNIFTY", "FINNIFTY", "SENSEX", "NSEI", "NSEBANK"]
        credit_ratio = 0.15 if is_index else 0.20
        
        net_credit = round(strike_width * credit_ratio, 2)
        max_risk = round(strike_width - net_credit, 2)
        
        return {
            "ticker": ticker,
            "strategy_type": "Bear Call Spread",
            "spot_price": current_price,
            "leg_1_sell": sell_strike,
            "leg_2_buy": buy_strike,
            "net_credit_per_share": net_credit,
            "max_risk_per_share": max_risk,
            "risk_reward_ratio": f"1:{round(max_risk/net_credit, 1) if net_credit > 0 else 0}",
            "win_probability": asset_data.get('win_probability', 85.0),
            "learning_context": asset_data.get("learning_context", {"RSI": 50, "MACD_State": "UNKNOWN", "Dist_from_VWAP": 0, "IB_Status": "UNKNOWN", "PA_Status": "UNKNOWN"})
        }

    def process_approved_assets(self, passed_assets):
        """The Router: Reads the Quant Engine's bias and builds the correct structure."""
        structured_trades = []
        for asset in passed_assets:
            if asset.get('bias') == 'BEARISH':
                trade = self.build_bear_call_spread(asset)
            else:
                trade = self.build_bull_put_spread(asset)
                
            structured_trades.append(trade)
            
        return structured_trades