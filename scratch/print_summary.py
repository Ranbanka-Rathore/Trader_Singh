import sys
sys.path.append('c:/Users/ST/Desktop/Agentic_Trader')
import pandas as pd
from scratch.test_strategy_premium import PremiumManagedRunner

summary = []
for ticker in ["NIFTY", "BANKNIFTY"]:
    for use_ml in [False, True]:
        runner = PremiumManagedRunner(ticker=ticker, use_ml_filter=use_ml)
        stats = runner.run_and_get_stats()
        if stats:
            summary.append(stats)

df = pd.DataFrame(summary)
print("\n" + "="*80)
print("🚀 FINAL PROP-DESK PREMIUM-MANAGED BACKTEST RESULTS (WITH DIRECION-AWARE ML)")
print("="*80)
print(df.to_string(index=False))
print("="*80)
