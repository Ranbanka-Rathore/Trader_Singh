import sys
sys.path.append('c:/Users/ST/Desktop/Agentic_Trader')
import pandas as pd
from scratch.test_strategy_premium import PremiumManagedRunner

# We temporarily override print inside PremiumManagedRunner to do nothing
PremiumManagedRunner.load_data = lambda self: super(PremiumManagedRunner, self).load_data()

summary = []
for ticker in ["NIFTY", "BANKNIFTY"]:
    for use_ml in [False, True]:
        runner = PremiumManagedRunner(ticker=ticker, use_ml_filter=use_ml)
        
        # Actually let's just run it and capture the dictionary returned
        df_candles = runner.load_data()
        if not df_candles.empty:
            # We run the original method but redirect stdout to devnull
            import io
            old_stdout = sys.stdout
            sys.stdout = io.StringIO()
            try:
                stats = runner.run_and_get_stats()
            finally:
                sys.stdout = old_stdout
            if stats:
                summary.append(stats)

df = pd.DataFrame(summary)
print("\n=============================================================")
print("📊 PREMIUM-MANAGED STRATEGY PERFORMANCE SUMMARY")
print("=============================================================")
print(df.to_string(index=False))
print("=============================================================")
