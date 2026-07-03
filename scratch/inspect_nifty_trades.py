import pandas as pd
from run_full_backtest import CustomRunner

runner_no_ml = CustomRunner(ticker="NIFTY", use_ml_filter=False)
df_no_ml = runner_no_ml.load_data()
print("--- NIFTY Without ML ---")
trades_no_ml = []
# Let's inspect the trades
# We will temporarily mock the print_report to just capture trades
runner_no_ml.print_report = lambda trades: trades_no_ml.extend(trades)
runner_no_ml.run_and_get_stats()

trades_ml = []
runner_ml = CustomRunner(ticker="NIFTY", use_ml_filter=True)
runner_ml.print_report = lambda trades: trades_ml.extend(trades)
runner_ml.run_and_get_stats()

print("\n=== All Trades Without ML ===")
df_no_ml_tr = pd.DataFrame(trades_no_ml)
print(df_no_ml_tr.to_string())

print("\n=== All Trades With ML ===")
df_ml_tr = pd.DataFrame(trades_ml)
print(df_ml_tr.to_string())
