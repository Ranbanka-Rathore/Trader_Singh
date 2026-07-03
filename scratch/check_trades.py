import os
import sys
import pandas as pd
from backtester_v3 import PropDeskBacktester

b = PropDeskBacktester(ticker="NIFTY")
df = b.load_data()

# We override print_report to inspect the trades list directly
def inspect_trades(trades):
    print(f"Total trades logged by backtester: {len(trades)}")
    for t in trades[-5:]:
        print(f"Entry: {t['entry_time']} | Exit: {t['exit_time']} | Bias: {t['bias']} | Entry Px: {t['entry_price']:.2f} | Exit Px: {t['exit_price']:.2f} | Reason: {t['exit_reason']} | PnL: {t['pnl_pts']:.2f}")

b.print_report = inspect_trades
b.run_simulation(df)
