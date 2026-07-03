import pandas as pd
import json
import numpy as np

print("="*60)
print("🧠 QUANTITATIVE ML OPTIMIZER v1.0")
print("="*60)

# 1. Load the Harvested Data
try:
    df = pd.read_csv("training_data.csv")
    print(f"✅ Loaded {len(df)} historical trades from memory.")
except FileNotFoundError:
    print("❌ Critical Error: training_data.csv not found. Run the harvester first.")
    exit()

# Overall Baseline
base_win_rate = df['Trade_Outcome'].mean() * 100
print(f"📊 Baseline Win Rate (No Filters): {base_win_rate:.2f}%")

def optimize_thresholds(setup_name, data):
    print(f"\n🔍 Optimizing {setup_name}...")
    setup_data = data[data['Setup_Type'] == setup_name]
    
    if len(setup_data) == 0:
        return {"vol_surge_multiplier": 1.5, "win_rate": 0, "trades": 0}

    best_threshold = 1.0
    best_win_rate = 0.0
    viable_trades = 0
    
    # Target: We want at least a 75% win rate.
    TARGET_WIN_RATE = 75.0 
    
    # Sweep through Volume Multipliers from 1.0x to 4.0x in increments of 0.1
    for threshold in np.arange(1.0, 4.1, 0.1):
        # Filter trades where the volume surge was GREATER than the threshold
        filtered_trades = setup_data[setup_data['Vol_Multiplier'] >= threshold]
        
        total_filtered = len(filtered_trades)
        if total_filtered < 5: # Skip if the sample size is too small to trust
            continue
            
        win_rate = filtered_trades['Trade_Outcome'].mean() * 100
        
        # We want the lowest threshold that still achieves our target win rate, 
        # so we don't over-filter and miss good trades.
        if win_rate >= TARGET_WIN_RATE:
            best_threshold = round(threshold, 1)
            best_win_rate = round(win_rate, 2)
            viable_trades = total_filtered
            break # We found the optimal floor, stop searching!
            
        # Fallback: keep track of the absolute highest win rate found
        if win_rate > best_win_rate:
            best_win_rate = round(win_rate, 2)
            best_threshold = round(threshold, 1)
            viable_trades = total_filtered

    print(f"   ↳ Optimal Volume Surge: {best_threshold}x")
    print(f"   ↳ Upgraded Win Rate: {best_win_rate}% (Across {viable_trades} High-Conviction Trades)")
    
    return {
        "vol_surge_multiplier": best_threshold,
        "expected_win_rate": best_win_rate
    }

# 2. Run the Optimization
double_top_stats = optimize_thresholds("DOUBLE_TOP_REVERSAL", df)
double_bottom_stats = optimize_thresholds("DOUBLE_BOTTOM_REVERSAL", df)

# 3. Export to the Live Trading Engine
print("\n" + "="*60)
print("💾 EXPORTING SYNAPSES TO BRAIN CONFIG...")

brain_config = {
    "^NSEI": {
        "DOUBLE_TOP_REVERSAL": {
            "vol_surge_multiplier": double_top_stats["vol_surge_multiplier"],
            "expected_win_rate": double_top_stats["expected_win_rate"],
            "double_top_bottom_margin": 0.0015
        },
        "DOUBLE_BOTTOM_REVERSAL": {
            "vol_surge_multiplier": double_bottom_stats["vol_surge_multiplier"],
            "expected_win_rate": double_bottom_stats["expected_win_rate"],
            "double_top_bottom_margin": 0.0015
        }
    }
}

with open("brain_config.json", "w") as f:
    json.dump(brain_config, f, indent=4)

print("✅ Saved to brain_config.json.")
print("⚡ The v4.0 Quant Engine will now dynamically load these thresholds on its next scan!")