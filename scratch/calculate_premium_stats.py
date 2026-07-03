import sys
sys.path.append('c:/Users/ST/Desktop/Agentic_Trader')
import pandas as pd
from scratch.test_strategy_premium import PremiumManagedRunner

summary = []
for ticker in ["NIFTY", "BANKNIFTY"]:
    for use_ml in [False, True]:
        runner = PremiumManagedRunner(ticker=ticker, use_ml_filter=use_ml)
        df_candles = runner.load_data()
        if not df_candles.empty:
            # Run the backtest and get list of trades
            import io
            old_stdout = sys.stdout
            sys.stdout = io.StringIO()
            try:
                trades = runner.run_and_get_stats()
            finally:
                sys.stdout = old_stdout
                
            if not trades:
                summary.append({
                    "Ticker": f"{ticker} (with ML)" if use_ml else ticker,
                    "Total_Trades": 0,
                    "Win_Rate": "0.00%",
                    "Net_Points": 0.0,
                    "Net_PnL": 0.0,
                    "Profit_Factor": 0.0
                })
                continue
                
            # Aggregate stats
            total_trades = len(trades)
            wins = sum(1 for t in trades if t['win'] == 1)
            win_rate = (wins / total_trades) * 100
            total_pnl = sum(t['pnl_pts'] for t in trades)
            lot_size = runner.lot_sizes.get(ticker, 1)
            net_pnl_val = total_pnl * lot_size
            
            gross_profits = sum(t['pnl_pts'] for t in trades if t['pnl_pts'] > 0)
            gross_losses = abs(sum(t['pnl_pts'] for t in trades if t['pnl_pts'] < 0))
            profit_factor = (gross_profits / gross_losses) if gross_losses > 0 else float('inf')
            
            summary.append({
                "Ticker": f"{ticker} (with ML)" if use_ml else ticker,
                "Total_Trades": total_trades,
                "Win_Rate": f"{win_rate:.2f}%",
                "Net_Points": round(total_pnl, 2),
                "Net_PnL": round(net_pnl_val, 2),
                "Profit_Factor": round(profit_factor, 2)
            })

df = pd.DataFrame(summary)
print("\n=============================================================")
print("📊 PREMIUM-MANAGED STRATEGY PERFORMANCE SUMMARY")
print("=============================================================")
print(df.to_string(index=False))
print("=============================================================")
