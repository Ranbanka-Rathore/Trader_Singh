import logging
from decimal import Decimal
from typing import Dict, Any, Optional
from backend.app.db.models import OpenPosition
from backend.app.services.options_desk_service import options_desk_service

logger = logging.getLogger("OptionsFirefighter")

class OptionsFirefighter:
    def __init__(self, max_adjustments_per_trade: int = 1):
        self.max_adjustments = max_adjustments_per_trade

    def evaluate_adjustment_need(self, position: OpenPosition, current_price: float) -> bool:
        """
        Determines if an open position is challenged and requires firefighting.
        Limit: Maximum of 1 adjustment per trade to avoid over-adjusting in runaway trends.
        """
        adj_count = position.adjustment_count or 0
        if adj_count >= self.max_adjustments:
            return False

        strategy = position.strategy_type
        bias = position.bias
        entry_spot = float(position.entry_spot_price or position.spot_price or 0.0)
        short_strike = float(position.leg_1_sell or 0.0)

        if not entry_spot or not short_strike:
            return False

        if strategy in ["BULL_PUT_SPREAD", "BEAR_CALL_SPREAD", "CREDIT_SPREAD"]:
            # Midpoint breach: spot drifts 50% toward the short strike
            midpoint = (entry_spot + short_strike) / 2.0
            
            # For Bull Put (bias BULLISH), short strike is below entry. Breach is when price falls below midpoint.
            if bias == "BULLISH" and current_price <= midpoint:
                logger.warning(f"🚨 [Firefighter] Bull Put Spread challenged on {position.ticker}. Spot: {current_price} <= Midpoint: {midpoint:.2f} (Short Strike: {short_strike})")
                return True
            # For Bear Call (bias BEARISH), short strike is above entry. Breach is when price rises above midpoint.
            elif bias == "BEARISH" and current_price >= midpoint:
                logger.warning(f"🚨 [Firefighter] Bear Call Spread challenged on {position.ticker}. Spot: {current_price} >= Midpoint: {midpoint:.2f} (Short Strike: {short_strike})")
                return True

        elif strategy == "CALENDAR_SPREAD":
            # Calendar Spread: triggers if spot price drifts more than 0.8% from the strike
            pct_drift = abs(current_price - short_strike) / short_strike
            if pct_drift > 0.008:
                logger.warning(f"🚨 [Firefighter] Calendar Spread challenged on {position.ticker}. Spot: {current_price} drifted {pct_drift*100:.2f}% from Strike: {short_strike} (Limit: 0.8%)")
                return True

        elif strategy == "COVERED_CALL":
            # Covered Call: triggers if spot price falls more than 1.0% below entry price
            pct_drift = (entry_spot - current_price) / entry_spot
            if pct_drift > 0.010:
                logger.warning(f"🚨 [Firefighter] Covered Call challenged on {position.ticker}. Spot: {current_price} is {pct_drift*100:.2f}% below Entry: {entry_spot} (Limit: 1.0%)")
                return True

        elif strategy == "CASH_SECURED_PUT":
            # Cash Secured Put: triggers if spot price falls more than 1.0% below short strike
            pct_drift = (short_strike - current_price) / short_strike
            if pct_drift > 0.010:
                logger.warning(f"🚨 [Firefighter] Cash Secured Put challenged on {position.ticker}. Spot: {current_price} is {pct_drift*100:.2f}% below Strike: {short_strike} (Limit: 1.0%)")
                return True

        elif strategy == "DIAGONAL_SPREAD":
            # Diagonal Spread: triggers if spot price drifts more than 1.0% from the short strike
            pct_drift = abs(current_price - short_strike) / short_strike
            if pct_drift > 0.010:
                logger.warning(f"🚨 [Firefighter] Diagonal Spread challenged on {position.ticker}. Spot: {current_price} drifted {pct_drift*100:.2f}% from Strike: {short_strike} (Limit: 1.0%)")
                return True

        elif strategy == "DELTA_NEUTRAL":
            # Delta Neutral Straddle: triggers if spot price drifts more than 1.2% from the center strike
            pct_drift = abs(current_price - short_strike) / short_strike
            if pct_drift > 0.012:
                logger.warning(f"🚨 [Firefighter] Delta Neutral challenged on {position.ticker}. Spot: {current_price} drifted {pct_drift*100:.2f}% from Straddle Strike: {short_strike} (Limit: 1.2%)")
                return True

        elif strategy in ["DEBIT_BULL_SPREAD", "DEBIT_BEAR_SPREAD", "DEBIT_SPREAD"]:
            # Debit Spreads: triggers if spot price moves against trade bias by more than 1.0%
            if bias == "BULLISH":
                pct_drift = (entry_spot - current_price) / entry_spot
            else:
                pct_drift = (current_price - entry_spot) / entry_spot
            if pct_drift > 0.010:
                logger.warning(f"🚨 [Firefighter] Debit Spread challenged on {position.ticker}. Spot: {current_price} moved against Bias {bias} by {pct_drift*100:.2f}% from Entry: {entry_spot} (Limit: 1.0%)")
                return True

        return False

    def build_adjustment_trade(self, position: OpenPosition, current_price: float) -> Optional[Dict[str, Any]]:
        """
        Builds the options adjustment legs for the challenged position.
        """
        strategy = position.strategy_type
        bias = position.bias
        ticker = position.ticker

        interval = options_desk_service._get_strike_interval(ticker)
        
        if strategy in ["BULL_PUT_SPREAD", "BEAR_CALL_SPREAD", "CREDIT_SPREAD"]:
            # 1. Opposing Side Credit Spread (convert to Iron Condor)
            if bias == "BULLISH":
                # Challenged Bull Put Spread -> Sell a Bear Call Spread to collect extra premium
                logger.info(f"🛡️ [Firefighter] Constructing Bear Call Spread adjustment for {ticker}...")
                short_call = options_desk_service._round_to_strike(current_price + interval, interval)
                long_call = short_call + interval
                
                net_credit = Decimal(str(interval * 0.15))
                
                return {
                    "ticker": ticker,
                    "strategy_type": "BEAR_CALL_SPREAD",
                    "bias": "BEARISH",
                    "leg_1_sell": Decimal(str(short_call)),
                    "leg_2_buy": Decimal(str(long_call)),
                    "net_credit_per_share": net_credit,
                    "max_risk_per_share": Decimal(str(interval - float(net_credit))),
                    "lots_sized": position.lots_sized or 1,
                    "is_adjustment": True
                }
            else:
                # Challenged Bear Call Spread -> Sell a Bull Put Spread to collect extra premium
                logger.info(f"🛡️ [Firefighter] Constructing Bull Put Spread adjustment for {ticker}...")
                short_put = options_desk_service._round_to_strike(current_price - interval, interval)
                long_put = short_put - interval
                
                net_credit = Decimal(str(interval * 0.15))
                
                return {
                    "ticker": ticker,
                    "strategy_type": "BULL_PUT_SPREAD",
                    "bias": "BULLISH",
                    "leg_1_sell": Decimal(str(short_put)),
                    "leg_2_buy": Decimal(str(long_put)),
                    "net_credit_per_share": net_credit,
                    "max_risk_per_share": Decimal(str(interval - float(net_credit))),
                    "lots_sized": position.lots_sized or 1,
                    "is_adjustment": True
                }

        elif strategy == "CALENDAR_SPREAD":
            # 2. Calendar Roll: Buy back current short weekly and sell next week ATM
            logger.info(f"🛡️ [Firefighter] Constructing next-week Calendar Roll for {ticker}...")
            new_strike = options_desk_service._round_to_strike(current_price, interval)
            
            original_debit = float(position.net_credit_per_share or 0.0)
            roll_credit = Decimal(str(original_debit * 0.50)) if original_debit > 0 else Decimal("48.00")
            
            return {
                "ticker": ticker,
                "strategy_type": "CALENDAR_ROLL",
                "bias": bias,
                "leg_1_sell": Decimal(str(new_strike)),  # new weekly short strike
                "leg_2_buy": position.leg_2_buy,          # keep original monthly long leg
                "net_credit_per_share": roll_credit,
                "max_risk_per_share": position.max_risk_per_share,
                "lots_sized": position.lots_sized or 1,
                "is_adjustment": True
            }

        elif strategy == "COVERED_CALL":
            # 3. Covered Call Roll-Down: Roll short call down closer to spot
            logger.info(f"🛡️ [Firefighter] Constructing Covered Call roll-down for {ticker}...")
            new_strike = options_desk_service._round_to_strike(current_price, interval)
            
            original_credit = float(position.net_credit_per_share or 0.0)
            roll_credit = Decimal(str(original_credit * 0.40)) if original_credit > 0 else Decimal("50.00")
            
            return {
                "ticker": ticker,
                "strategy_type": "COVERED_CALL_ROLL",
                "bias": "BULLISH",
                "leg_1_sell": Decimal(str(new_strike)),
                "net_credit_per_share": roll_credit,
                "max_risk_per_share": position.max_risk_per_share,
                "lots_sized": position.lots_sized or 1,
                "is_adjustment": True
            }

        elif strategy == "CASH_SECURED_PUT":
            # 4. CSP Roll-Down-and-Out: Roll short put lower to next week's expiry
            logger.info(f"🛡️ [Firefighter] Constructing Cash Secured Put roll-down-and-out for {ticker}...")
            new_strike = options_desk_service._round_to_strike(current_price - interval, interval)
            
            original_credit = float(position.net_credit_per_share or 0.0)
            roll_credit = Decimal(str(original_credit * 0.30)) if original_credit > 0 else Decimal("40.00")
            
            return {
                "ticker": ticker,
                "strategy_type": "CSP_ROLL",
                "bias": "BULLISH",
                "leg_1_sell": Decimal(str(new_strike)),
                "net_credit_per_share": roll_credit,
                "max_risk_per_share": position.max_risk_per_share,
                "lots_sized": position.lots_sized or 1,
                "is_adjustment": True
            }

        elif strategy == "DIAGONAL_SPREAD":
            # 5. Diagonal Spread: Roll short weekly leg to ATM
            logger.info(f"🛡️ [Firefighter] Constructing Diagonal Spread short-leg roll for {ticker}...")
            new_strike = options_desk_service._round_to_strike(current_price, interval)
            
            original_debit = float(position.net_credit_per_share or 0.0)
            roll_credit = Decimal(str(original_debit * 0.40)) if original_debit > 0 else Decimal("45.00")
            
            return {
                "ticker": ticker,
                "strategy_type": "DIAGONAL_ROLL",
                "bias": bias,
                "leg_1_sell": Decimal(str(new_strike)),
                "leg_2_buy": position.leg_2_buy,
                "net_credit_per_share": roll_credit,
                "max_risk_per_share": position.max_risk_per_share,
                "lots_sized": position.lots_sized or 1,
                "is_adjustment": True
            }

        elif strategy == "DELTA_NEUTRAL":
            # 6. Delta Neutral Untested-Leg Roll: Roll profitable leg closer to spot
            logger.info(f"🛡️ [Firefighter] Constructing Delta Neutral untested-leg roll for {ticker}...")
            new_strike = options_desk_service._round_to_strike(current_price, interval)
            
            original_credit = float(position.net_credit_per_share or 0.0)
            roll_credit = Decimal(str(original_credit * 0.35)) if original_credit > 0 else Decimal("60.00")
            
            return {
                "ticker": ticker,
                "strategy_type": "STRADDLE_ROLL",
                "bias": "NEUTRAL",
                "leg_1_sell": Decimal(str(new_strike)),
                "net_credit_per_share": roll_credit,
                "max_risk_per_share": position.max_risk_per_share,
                "lots_sized": position.lots_sized or 1,
                "is_adjustment": True
            }

        elif strategy in ["DEBIT_BULL_SPREAD", "DEBIT_BEAR_SPREAD", "DEBIT_SPREAD"]:
            # 7. Debit Spread Short-Leg Compression: Tighten short strike closer to long strike
            logger.info(f"🛡️ [Firefighter] Constructing Debit Spread short-leg compression adjustment for {ticker}...")
            new_strike = options_desk_service._round_to_strike((current_price + float(position.leg_1_sell)) / 2.0, interval)
            
            original_debit = float(position.net_credit_per_share or 0.0)
            roll_credit = Decimal(str(original_debit * 0.25)) if original_debit > 0 else Decimal("30.00")
            
            return {
                "ticker": ticker,
                "strategy_type": "DEBIT_ROLL",
                "bias": bias,
                "leg_1_sell": Decimal(str(new_strike)),
                "net_credit_per_share": roll_credit,
                "max_risk_per_share": position.max_risk_per_share,
                "lots_sized": position.lots_sized or 1,
                "is_adjustment": True
            }

        return None

firefighter_service = OptionsFirefighter()
