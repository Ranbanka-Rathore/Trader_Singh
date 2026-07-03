import unittest
from decimal import Decimal

# Test class for verifying margin and breakeven lock logic
class TestMarginAndLock(unittest.TestCase):
    def test_spread_construction_and_margin(self):
        # 1. Mock options desk spread construction
        current_price = 24106.60
        ticker = "NIFTY"
        interval = 50  # Nifty strike interval
        
        # Bearish Bear Call Spread construction
        # Sell strike: spot * 1.01 rounded to nearest 50
        raw_sell = current_price * 1.01
        sell_strike = round(raw_sell / interval) * interval
        # Buy strike: sell_strike + interval (exactly 1-strike interval wide)
        buy_strike = sell_strike + interval
        
        # Verify sell strike is OTM and buy strike is 1-strike above
        self.assertEqual(sell_strike, 24350)
        self.assertEqual(buy_strike, 24400)
        
        # Calculate strike width and net credit (15% of width for index)
        strike_width = abs(buy_strike - sell_strike)
        self.assertEqual(strike_width, 50)
        
        net_credit = round(strike_width * 0.15, 2)
        max_risk = round(strike_width - net_credit, 2)
        
        self.assertEqual(net_credit, 7.50)
        self.assertEqual(max_risk, 42.50)

        # 2. Mock margin calculations from RiskShield
        lots = 2
        # Nifty margin is 35k per lot
        margin_per_lot = 35000
        est_margin_deployed = lots * margin_per_lot
        self.assertEqual(est_margin_deployed, 70000)

    def test_breakeven_lock_activation_bearish(self):
        # Mock position details
        entry_price = 24106.60
        net_credit = 7.50  # 15% of 50 strike width
        
        # Simulate price moving in our favor to lowest seen 24074.05
        current_price = 24074.05
        lowest_seen = min(entry_price, current_price)
        highest_seen = max(entry_price, current_price)
        
        # 30% premium decay check (using delta approximation of 0.15)
        # Entry price - lowest_seen is the favorable move
        favorable_move = entry_price - lowest_seen
        decay_est = favorable_move * 0.15
        decay_threshold = net_credit * 0.30
        
        has_reached_be_lock = decay_est >= decay_threshold
        
        # Favorable move: 24106.60 - 24074.05 = 32.55 points
        # Decay est: 32.55 * 0.15 = 4.8825 points
        # Decay threshold: 7.50 * 0.30 = 2.25 points
        self.assertAlmostEqual(favorable_move, 32.55, places=2)
        self.assertAlmostEqual(decay_est, 4.8825, places=4)
        self.assertEqual(decay_threshold, 2.25)
        self.assertTrue(has_reached_be_lock)

        # Update dynamic SL representation
        if has_reached_be_lock:
            spot_sl_est = entry_price
        else:
            spot_sl_est = entry_price + (net_credit * 0.30 / 0.15)
            
        self.assertEqual(spot_sl_est, entry_price)

        # Now simulate price retracing (climbing) back to 24108.40
        retrace_price = 24108.40
        price_change = entry_price - retrace_price
        current_spread_value = net_credit - (price_change * 0.15)
        
        # Retrace price change is -1.8 points
        # Current spread value is 7.50 - (-1.8 * 0.15) = 7.50 + 0.27 = 7.77 points
        self.assertAlmostEqual(price_change, -1.80, places=2)
        self.assertAlmostEqual(current_spread_value, 7.77, places=4)

        # Since has_reached_be_lock is True, check if SL is triggered (spread value >= net_credit)
        is_sl = current_spread_value >= net_credit
        self.assertTrue(is_sl)

        # Check realized pnl
        if is_sl:
            if has_reached_be_lock:
                exit_reason = "🛡️ TRAILING STOP (Breakeven)"
                realized_pnl = 0.0
            else:
                exit_reason = "🛑 PREMIUM STOP LOSS (Tight 30% SL)"
                realized_pnl = -net_credit * 0.30
                
        self.assertEqual(exit_reason, "🛡️ TRAILING STOP (Breakeven)")
        self.assertEqual(realized_pnl, 0.0)

if __name__ == "__main__":
    unittest.main()
