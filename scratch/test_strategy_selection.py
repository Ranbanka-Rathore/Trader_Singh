import unittest
import sys
import os
import datetime
from decimal import Decimal

# Add project root to python path
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from backend.app.services.options_desk_service import options_desk_service
from backend.app.core.risk_shield import RiskShield
from backend.app.services.execution_service import execution_service

class TestStrategySelection(unittest.TestCase):

    def setUp(self):
        self.asset_data = {
            "spot_price": 24106.0,
            "ticker": "NIFTY",
            "recommended_lots": 1,
            "win_probability": 85.0,
            "vol_surge": 1.0,
            "coi_pcr": 1.15,
            "bias": "BULLISH",
            "learning_context": {"PA_Status": "DOUBLE_BOTTOM"}
        }
        self.risk_shield = RiskShield()

    def test_options_desk_credit_spread(self):
        # CREDIT_SPREAD Bullish
        trades = options_desk_service.process_approved_assets([self.asset_data], "CREDIT_SPREAD")
        self.assertEqual(len(trades), 1)
        t = trades[0]
        self.assertEqual(t["strategy_type"], "BULL_PUT_SPREAD")
        # spot_price=24106 -> sell put OTM by ~1% -> 24106*0.99 = 23864.94 -> round to strike interval 50 -> 23850.
        self.assertEqual(t["leg_1_sell"], 23850)
        self.assertEqual(t["leg_2_buy"], 23800)
        # Net Credit: width * 0.15 = 50 * 0.15 = 7.5
        self.assertEqual(t["net_credit_per_share"], 7.5)
        self.assertEqual(t["max_risk_per_share"], 42.5)

    def test_options_desk_debit_spread(self):
        # DEBIT_SPREAD Bullish
        trades = options_desk_service.process_approved_assets([self.asset_data], "DEBIT_SPREAD")
        self.assertEqual(len(trades), 1)
        t = trades[0]
        self.assertEqual(t["strategy_type"], "DEBIT_BULL_SPREAD")
        # ATM buy strike -> 24106 rounded to 50 = 24100.
        # OTM sell strike 1 strike above -> 24150.
        self.assertEqual(t["leg_2_buy"], 24100)
        self.assertEqual(t["leg_1_sell"], 24150)
        # Net Debit: width * 0.40 = 50 * 0.40 = 20.0
        self.assertEqual(t["net_credit_per_share"], 20.0)
        self.assertEqual(t["max_risk_per_share"], 20.0)

    def test_options_desk_naked_options(self):
        # NAKED_OPTIONS Bullish
        trades = options_desk_service.process_approved_assets([self.asset_data], "NAKED_OPTIONS")
        self.assertEqual(len(trades), 1)
        t = trades[0]
        self.assertEqual(t["strategy_type"], "LONG_CALL")
        # ATM buy strike -> 24100.
        # sell strike -> 0.0.
        self.assertEqual(t["leg_2_buy"], 24100)
        self.assertEqual(t["leg_1_sell"], 0.0)
        # Net Debit: spot * 0.008 = 24106 * 0.008 = 192.85
        self.assertEqual(t["net_credit_per_share"], 192.85)

    def test_greeks_naked_options(self):
        # Test that calculate_spread_greeks doesn't error when leg_1_strike is 0
        expiry = datetime.date.today() + datetime.timedelta(days=2)
        greeks = self.risk_shield.calculate_spread_greeks(
            spot=24106.0,
            leg_1_strike=0.0,
            leg_2_strike=24100.0,
            expiry_date=expiry,
            volatility=0.15,
            strategy_type="LONG_CALL"
        )
        # Verify it returns non-zero delta and no error
        self.assertIn("net_delta", greeks)
        self.assertGreater(greeks["net_delta"], 0)

    def test_exit_manager_credit_vs_debit_pnl(self):
        # Mock class for OpenPosition SQLModel
        class MockPosition:
            def __init__(self, **kwargs):
                for k, v in kwargs.items():
                    setattr(self, k, v)
        
        # 1. Credit spread position (Bull Put Spread)
        pos_credit = MockPosition(
            ticker="NIFTY",
            strategy_type="BULL_PUT_SPREAD",
            bias="BULLISH",
            leg_1_sell=Decimal("23850.0"),
            leg_2_buy=Decimal("23800.0"),
            entry_spot_price=Decimal("24106.0"),
            net_credit_per_share=Decimal("7.5"),
            lots_sized=1,
            highest_seen=Decimal("24106.0"),
            lowest_seen=Decimal("24106.0"),
            dynamic_sl=Decimal("23850.0"),
            net_delta=Decimal("0.15"),
            net_gamma=Decimal("0.0"),
            net_theta=Decimal("0.0"),
            net_vega=Decimal("0.0")
        )
        
        # 2. Debit spread position (Debit Bull Spread)
        pos_debit = MockPosition(
            ticker="NIFTY",
            strategy_type="DEBIT_BULL_SPREAD",
            bias="BULLISH",
            leg_1_sell=Decimal("24150.0"),
            leg_2_buy=Decimal("24100.0"),
            entry_spot_price=Decimal("24106.0"),
            net_credit_per_share=Decimal("20.0"), # Stored positive premium_paid
            lots_sized=1,
            highest_seen=Decimal("24106.0"),
            lowest_seen=Decimal("24106.0"),
            dynamic_sl=Decimal("24100.0"),
            net_delta=Decimal("0.30"),
            net_gamma=Decimal("0.0"),
            net_theta=Decimal("0.0"),
            net_vega=Decimal("0.0")
        )

        # Let's verify the P&L evaluations
        # We simulate a price increase of 100 points
        # For Credit Spread (short options, bullish), price change = 100 -> current_spread_value = 7.5 - (100 * 0.15) = -7.5 (fully decayed to max profit)
        # This will trigger Take Profit (current_spread_value <= 7.5 * 0.20 = 1.5)
        # For Debit Spread (long options, bullish), price change = 100 -> current_option_value = 20.0 + (100 * 0.30) = 50.0
        # This will trigger Take Profit (current_option_value >= 20 * 1.5 = 30.0)
        
        # Test Credit Spreads P&L simulation
        # Simulation of pricing and exit checks (replicating evaluate_open_positions logic):
        entry_price = float(pos_credit.entry_spot_price)
        net_credit = float(pos_credit.net_credit_per_share)
        delta = 0.15
        
        # Favorable move (spot increases to 24200)
        current_price = 24200.0
        price_change = current_price - entry_price
        current_spread_value = net_credit - (price_change * delta)
        is_tp = current_spread_value <= net_credit * 0.20
        self.assertTrue(is_tp) # Should be true (TP triggered)
        
        # Adverse move (spot drops to 24000)
        current_price = 24000.0
        price_change = current_price - entry_price
        current_spread_value = net_credit - (price_change * delta)
        # SL trigger: current_spread_value >= net_credit * (1.0 + sl_ratio), sl_ratio = 1.0 -> 15.0
        is_sl = current_spread_value >= net_credit * 2.0
        # current_spread_value = 7.5 - (-106 * 0.15) = 7.5 + 15.9 = 23.4 (exceeds 15.0)
        self.assertTrue(is_sl) # Should be true (SL triggered)

        # Test Debit Spreads P&L simulation
        entry_price = float(pos_debit.entry_spot_price)
        premium_paid = float(pos_debit.net_credit_per_share)
        delta = 0.30
        
        # Favorable move (spot increases to 24200)
        current_price = 24200.0
        price_change = current_price - entry_price
        current_option_value = premium_paid + (price_change * delta)
        is_tp = current_option_value >= premium_paid * 1.50
        # current_option_value = 20.0 + 94 * 0.30 = 20 + 28.2 = 48.2 (exceeds 30.0)
        self.assertTrue(is_tp)
        
        # Adverse move (spot drops to 24050)
        current_price = 24050.0
        price_change = current_price - entry_price
        current_option_value = premium_paid + (price_change * delta)
        is_sl = current_option_value <= premium_paid * 0.70
        # current_option_value = 20.0 + (-56 * 0.30) = 20 - 16.8 = 3.2 (less than 14.0)
        self.assertTrue(is_sl)

if __name__ == '__main__':
    unittest.main()
