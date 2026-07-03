from typing import List, Dict, Any

class OptionsDeskService:
    def _get_strike_interval(self, ticker: str) -> int:
        """Returns the correct NSE strike interval for the asset."""
        clean_ticker = ticker.replace("^", "").replace(".NS", "").replace(".BO", "")
        if clean_ticker in ["NSEBANK", "BANKNIFTY", "BSESN", "SENSEX"]:
            return 100
        elif clean_ticker in ["NSEI", "NIFTY", "FINNIFTY"]:
            return 50
        else:
            return 10 # Default fallback for equities (Reliance, HDFCBANK, etc.)

    def _round_to_strike(self, price: float, interval: int) -> float:
        """Rounds a raw price to the nearest valid exchange strike."""
        return round(price / interval) * interval

    def build_bull_put_spread(self, asset_data: Dict[str, Any]) -> Dict[str, Any]:
        """Constructs a Theta-positive OTM Bull Put Spread."""
        current_price = float(asset_data.get('spot_price', 0.0))
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
            "strategy_type": "BULL_PUT_SPREAD",
            "spot_price": current_price,
            "leg_1_sell": sell_strike,
            "leg_2_buy": buy_strike,
            "net_credit_per_share": net_credit,
            "max_risk_per_share": max_risk,
            "risk_reward_ratio": f"1:{round(max_risk/net_credit, 1) if net_credit > 0 else 0.0}",
            "win_probability": asset_data.get('win_probability', 85.0),
            "vol_surge_multiplier": asset_data.get('vol_surge', 1.0),
            "coi_pcr": asset_data.get('coi_pcr', 1.0),
            "bias": "BULLISH",
            "learning_context": asset_data.get("learning_context", {"PA_Status": asset_data.get('pa_status', 'UNKNOWN')}),
            "ml_score": asset_data.get('ml_score', 0.5),
            "lots_sized": asset_data.get('recommended_lots', 1)
        }

    def build_bear_call_spread(self, asset_data: Dict[str, Any]) -> Dict[str, Any]:
        """Constructs a Theta-positive OTM Bear Call Spread."""
        current_price = float(asset_data.get('spot_price', 0.0))
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
            "strategy_type": "BEAR_CALL_SPREAD",
            "spot_price": current_price,
            "leg_1_sell": sell_strike,
            "leg_2_buy": buy_strike,
            "net_credit_per_share": net_credit,
            "max_risk_per_share": max_risk,
            "risk_reward_ratio": f"1:{round(max_risk/net_credit, 1) if net_credit > 0 else 0.0}",
            "win_probability": asset_data.get('win_probability', 85.0),
            "vol_surge_multiplier": asset_data.get('vol_surge', 1.0),
            "coi_pcr": asset_data.get('coi_pcr', 1.0),
            "bias": "BEARISH",
            "learning_context": asset_data.get("learning_context", {"PA_Status": asset_data.get('pa_status', 'UNKNOWN')}),
            "ml_score": asset_data.get('ml_score', 0.5),
            "lots_sized": asset_data.get('recommended_lots', 1)
        }

    def build_debit_spread(self, asset_data: Dict[str, Any]) -> Dict[str, Any]:
        """Constructs an ATM Debit Spread."""
        current_price = float(asset_data.get('spot_price', 0.0))
        ticker = asset_data.get('ticker', 'UNKNOWN')
        bias = asset_data.get('bias', 'BULLISH')
        interval = self._get_strike_interval(ticker)
        
        # ATM buy strike
        buy_strike = self._round_to_strike(current_price, interval)
        
        if bias == 'BEARISH':
            # Buy ATM Put, Sell OTM Put 1-strike below
            sell_strike = buy_strike - interval
            strategy_type = "DEBIT_BEAR_SPREAD"
        else:
            # Buy ATM Call, Sell OTM Call 1-strike above
            sell_strike = buy_strike + interval
            strategy_type = "DEBIT_BULL_SPREAD"
            
        strike_width = abs(sell_strike - buy_strike)
        net_debit = round(strike_width * 0.40, 2)
        max_risk = net_debit
        reward = round(strike_width - net_debit, 2)
        
        return {
            "ticker": ticker,
            "strategy_type": strategy_type,
            "spot_price": current_price,
            "leg_1_sell": sell_strike,
            "leg_2_buy": buy_strike,
            "net_credit_per_share": net_debit, # Store debit in same field for schema compatibility
            "max_risk_per_share": max_risk,
            "risk_reward_ratio": f"1:{round(reward/net_debit, 1) if net_debit > 0 else 0.0}",
            "win_probability": asset_data.get('win_probability', 55.0),
            "vol_surge_multiplier": asset_data.get('vol_surge', 1.0),
            "coi_pcr": asset_data.get('coi_pcr', 1.0),
            "bias": bias,
            "learning_context": asset_data.get("learning_context", {"PA_Status": asset_data.get('pa_status', 'UNKNOWN')}),
            "ml_score": asset_data.get('ml_score', 0.5),
            "lots_sized": asset_data.get('recommended_lots', 1)
        }

    def build_calendar_spread(self, asset_data: Dict[str, Any]) -> Dict[str, Any]:
        """Constructs an ATM Calendar Spread."""
        current_price = float(asset_data.get('spot_price', 0.0))
        ticker = asset_data.get('ticker', 'UNKNOWN')
        interval = self._get_strike_interval(ticker)
        bias = asset_data.get('bias', 'BULLISH')
        
        # ATM strike for both legs
        atm_strike = self._round_to_strike(current_price, interval)
        
        net_debit = round(current_price * 0.004, 2) # Cost representing premium difference
        max_risk = net_debit
        
        learning_context = asset_data.get("learning_context", {"PA_Status": asset_data.get('pa_status', 'UNKNOWN')}).copy()
        learning_context.update({
            "short_expiry_strike": atm_strike,
            "long_expiry_strike": atm_strike,
            "original_strategy": "CALENDAR_SPREAD"
        })
        
        return {
            "ticker": ticker,
            "strategy_type": "CALENDAR_SPREAD",
            "spot_price": current_price,
            "leg_1_sell": atm_strike, # Short Weekly
            "leg_2_buy": atm_strike,  # Long Monthly
            "net_credit_per_share": net_debit,
            "max_risk_per_share": max_risk,
            "risk_reward_ratio": "1:1.5",
            "win_probability": asset_data.get('win_probability', 60.0),
            "vol_surge_multiplier": asset_data.get('vol_surge', 1.0),
            "coi_pcr": asset_data.get('coi_pcr', 1.0),
            "bias": bias,
            "learning_context": learning_context,
            "ml_score": asset_data.get('ml_score', 0.5),
            "lots_sized": asset_data.get('recommended_lots', 1)
        }

    def build_diagonal_spread(self, asset_data: Dict[str, Any]) -> Dict[str, Any]:
        """Constructs an ATM/OTM Diagonal Spread."""
        current_price = float(asset_data.get('spot_price', 0.0))
        ticker = asset_data.get('ticker', 'UNKNOWN')
        interval = self._get_strike_interval(ticker)
        bias = asset_data.get('bias', 'BULLISH')
        
        atm_strike = self._round_to_strike(current_price, interval)
        if bias == 'BEARISH':
            # Sell Weekly Put OTM, Buy Monthly Put ATM
            weekly_strike = atm_strike - interval
            strategy_type = "DIAGONAL_SPREAD"
        else:
            # Sell Weekly Call OTM, Buy Monthly Call ATM
            weekly_strike = atm_strike + interval
            strategy_type = "DIAGONAL_SPREAD"
            
        net_debit = round(current_price * 0.008, 2)
        max_risk = net_debit
        
        learning_context = asset_data.get("learning_context", {"PA_Status": asset_data.get('pa_status', 'UNKNOWN')}).copy()
        learning_context.update({
            "short_expiry_strike": weekly_strike,
            "long_expiry_strike": atm_strike,
            "diagonal_type": "BEARISH_PUT" if bias == 'BEARISH' else "BULLISH_CALL",
            "original_strategy": "DIAGONAL_SPREAD"
        })
        
        return {
            "ticker": ticker,
            "strategy_type": strategy_type,
            "spot_price": current_price,
            "leg_1_sell": weekly_strike, # Short Weekly
            "leg_2_buy": atm_strike,     # Long Monthly
            "net_credit_per_share": net_debit,
            "max_risk_per_share": max_risk,
            "risk_reward_ratio": "1:1.5",
            "win_probability": asset_data.get('win_probability', 62.0),
            "vol_surge_multiplier": asset_data.get('vol_surge', 1.0),
            "coi_pcr": asset_data.get('coi_pcr', 1.0),
            "bias": bias,
            "learning_context": learning_context,
            "ml_score": asset_data.get('ml_score', 0.5),
            "lots_sized": asset_data.get('recommended_lots', 1)
        }

    def build_delta_neutral(self, asset_data: Dict[str, Any]) -> Dict[str, Any]:
        """Constructs a Delta-neutral Short Straddle (simulated dynamic neutralization)."""
        current_price = float(asset_data.get('spot_price', 0.0))
        ticker = asset_data.get('ticker', 'UNKNOWN')
        interval = self._get_strike_interval(ticker)
        
        atm_strike = self._round_to_strike(current_price, interval)
        net_credit = round(current_price * 0.08, 2) # ~8.0% of spot
        max_risk = round(net_credit * 1.5, 2)
        
        learning_context = asset_data.get("learning_context", {"PA_Status": asset_data.get('pa_status', 'UNKNOWN')}).copy()
        learning_context.update({
            "short_call_strike": atm_strike,
            "short_put_strike": atm_strike,
            "original_strategy": "DELTA_NEUTRAL"
        })
        
        return {
            "ticker": ticker,
            "strategy_type": "DELTA_NEUTRAL",
            "spot_price": current_price,
            "leg_1_sell": atm_strike, # Call Sell
            "leg_2_buy": atm_strike,  # Put Sell
            "net_credit_per_share": net_credit,
            "max_risk_per_share": max_risk,
            "risk_reward_ratio": "1:1.5",
            "win_probability": asset_data.get('win_probability', 75.0),
            "vol_surge_multiplier": asset_data.get('vol_surge', 1.0),
            "coi_pcr": asset_data.get('coi_pcr', 1.0),
            "bias": "NEUTRAL",
            "learning_context": learning_context,
            "ml_score": asset_data.get('ml_score', 0.5),
            "lots_sized": asset_data.get('recommended_lots', 1)
        }

    def build_covered_call(self, asset_data: Dict[str, Any]) -> Dict[str, Any]:
        """Constructs a Covered Call (Long Future + Short OTM Call)."""
        current_price = float(asset_data.get('spot_price', 0.0))
        ticker = asset_data.get('ticker', 'UNKNOWN')
        interval = self._get_strike_interval(ticker)
        
        # OTM Call Sell: strike is spot + 100
        call_sell = self._round_to_strike(current_price + (interval * 2), interval)
        
        # Premium collected: ~1.2% of spot
        net_credit = round(current_price * 0.012, 2)
        max_risk = round(current_price, 2) # Future price
        
        learning_context = asset_data.get("learning_context", {"PA_Status": asset_data.get('pa_status', 'UNKNOWN')}).copy()
        learning_context.update({
            "long_future_price": current_price,
            "short_call_strike": call_sell,
            "original_strategy": "COVERED_CALL"
        })
        
        return {
            "ticker": ticker,
            "strategy_type": "COVERED_CALL",
            "spot_price": current_price,
            "leg_1_sell": call_sell, # Short Call
            "leg_2_buy": 0.0,        # Future position
            "net_credit_per_share": net_credit,
            "max_risk_per_share": max_risk,
            "risk_reward_ratio": "1:1.5",
            "win_probability": asset_data.get('win_probability', 70.0),
            "vol_surge_multiplier": asset_data.get('vol_surge', 1.0),
            "coi_pcr": asset_data.get('coi_pcr', 1.0),
            "bias": "BULLISH",
            "learning_context": learning_context,
            "ml_score": asset_data.get('ml_score', 0.5),
            "lots_sized": asset_data.get('recommended_lots', 1)
        }

    def build_cash_secured_put(self, asset_data: Dict[str, Any]) -> Dict[str, Any]:
        """Constructs a Cash Secured Put (Short OTM Put)."""
        current_price = float(asset_data.get('spot_price', 0.0))
        ticker = asset_data.get('ticker', 'UNKNOWN')
        interval = self._get_strike_interval(ticker)
        
        # OTM Put Sell: strike is spot - 100
        put_sell = self._round_to_strike(current_price - (interval * 2), interval)
        
        # Premium collected: ~1.2% of spot
        net_credit = round(current_price * 0.012, 2)
        max_risk = put_sell # Cash collateral strike
        
        learning_context = asset_data.get("learning_context", {"PA_Status": asset_data.get('pa_status', 'UNKNOWN')}).copy()
        learning_context.update({
            "short_put_strike": put_sell,
            "original_strategy": "CASH_SECURED_PUT"
        })
        
        return {
            "ticker": ticker,
            "strategy_type": "CASH_SECURED_PUT",
            "spot_price": current_price,
            "leg_1_sell": put_sell, # Short Put
            "leg_2_buy": 0.0,
            "net_credit_per_share": net_credit,
            "max_risk_per_share": max_risk,
            "risk_reward_ratio": "1:1.5",
            "win_probability": asset_data.get('win_probability', 85.0),
            "vol_surge_multiplier": asset_data.get('vol_surge', 1.0),
            "coi_pcr": asset_data.get('coi_pcr', 1.0),
            "bias": "BULLISH",
            "learning_context": learning_context,
            "ml_score": asset_data.get('ml_score', 0.5),
            "lots_sized": asset_data.get('recommended_lots', 1)
        }

    def process_approved_assets(self, passed_assets: List[Dict[str, Any]], strategy_mode: str = "CREDIT_SPREAD") -> List[Dict[str, Any]]:
        """The Router: Reads the Quant Engine's bias and builds the correct structure."""
        structured_trades = []
        for asset in passed_assets:
            if strategy_mode == "DEBIT_SPREAD":
                trade = self.build_debit_spread(asset)
            elif strategy_mode == "CALENDAR_SPREAD":
                trade = self.build_calendar_spread(asset)
            elif strategy_mode == "DIAGONAL_SPREAD":
                trade = self.build_diagonal_spread(asset)
            elif strategy_mode == "DELTA_NEUTRAL":
                trade = self.build_delta_neutral(asset)
            elif strategy_mode == "COVERED_CALL":
                trade = self.build_covered_call(asset)
            elif strategy_mode == "CASH_SECURED_PUT":
                trade = self.build_cash_secured_put(asset)
            else: # CREDIT_SPREAD or default
                if asset.get('bias') == 'BEARISH':
                    trade = self.build_bear_call_spread(asset)
                else:
                    trade = self.build_bull_put_spread(asset)
            
            # Enforce minimum lot size of 5 for Credit and Debit spreads to dilute friction charges
            if strategy_mode in ["CREDIT_SPREAD", "DEBIT_SPREAD"]:
                trade["lots_sized"] = max(5, trade.get("lots_sized", 1))

            structured_trades.append(trade)
        return structured_trades

options_desk_service = OptionsDeskService()
