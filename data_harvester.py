import os
import pandas as pd
# yfinance removed

print("="*60)
print("🚜 QUANTITATIVE DATA HARVESTER: [DISABLED]")
print("="*60)
print("   ⚠️ yfinance has been removed. Data harvesting requires a new data source integration.")
exit()
print("   ↳ Downloading Price Action data via Yahoo Finance...")
df = yf.download(TICKER_YF, period=f"{DAYS_TO_HARVEST}d", interval="5m", progress=False)

if df.empty:
    print("❌ Critical Error: Could not fetch price data.")
    exit()

# Flatten multi-index columns if they exist
if isinstance(df.columns, pd.MultiIndex):
    df.columns = df.columns.get_level_values(0)

# 2. Recreate the v4.0 Scanner Logic
print("   ↳ Scanning historical timeline for structural setups...")
df['Swing_High'] = df['High'].rolling(window=20).max().shift(1)
df['Swing_Low'] = df['Low'].rolling(window=20).min().shift(1)
df['EMA_50'] = df['Close'].ewm(span=50, adjust=False).mean()
df['Avg_Volume'] = df['Volume'].rolling(window=10).mean().shift(1)

df['Setup_Type'] = None
df['Vol_Multiplier'] = 0.0

# Process every 5-minute candle in history
for index, row in df.iterrows():
    if pd.isna(row['Swing_High']) or pd.isna(row['Avg_Volume']):
        continue
        
    margin = row['Close'] * 0.0015
    
    # Double Top (Bearish)
    if abs(row['High'] - row['Swing_High']) <= margin:
        if row['Close'] < row['EMA_50']:
            df.at[index, 'Setup_Type'] = 'DOUBLE_TOP_REVERSAL'
            df.at[index, 'Vol_Multiplier'] = row['Volume'] / row['Avg_Volume'] if row['Avg_Volume'] > 0 else 1.0

    # Double Bottom (Bullish)
    elif abs(row['Low'] - row['Swing_Low']) <= margin:
        if row['Close'] > row['EMA_50']:
            df.at[index, 'Setup_Type'] = 'DOUBLE_BOTTOM_REVERSAL'
            df.at[index, 'Vol_Multiplier'] = row['Volume'] / row['Avg_Volume'] if row['Avg_Volume'] > 0 else 1.0

# Extract valid setups
setups_df = df[df['Setup_Type'].notnull()].copy()
print(f"   ↳ Found {len(setups_df)} historical setups.")

# 3. Label the Data (Did the trade Win or Lose?)
print("   ↳ Labeling outcomes (1 = Win, 0 = Loss)...")
setups_df['Trade_Outcome'] = 0 

for index in setups_df.index:
    try:
        current_idx = df.index.get_loc(index)
        # Look 10 candles (50 minutes) into the future
        future_idx = min(current_idx + 10, len(df) - 1)
        future_price = df.iloc[future_idx]['Close']
        entry_price = setups_df.at[index, 'Close']
        setup_type = setups_df.at[index, 'Setup_Type']
        
        if setup_type == 'DOUBLE_TOP_REVERSAL' and future_price < entry_price:
            setups_df.at[index, 'Trade_Outcome'] = 1 
        elif setup_type == 'DOUBLE_BOTTOM_REVERSAL' and future_price > entry_price:
            setups_df.at[index, 'Trade_Outcome'] = 1 
            
    except Exception as e:
        pass

# 4. Save the Training Data
print("   ↳ Saving training dataset...")
training_features = setups_df[['Setup_Type', 'Vol_Multiplier', 'Trade_Outcome']]
training_features.to_csv("training_data.csv", index=False)

print(f"\n✅ Harvesting Complete! Saved to training_data.csv")
print(f"Total Trades Evaluated: {len(training_features)}")
if len(training_features) > 0:
    print(f"Base Win Rate: {training_features['Trade_Outcome'].mean() * 100:.2f}%")