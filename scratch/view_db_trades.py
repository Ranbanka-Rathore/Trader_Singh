import sys
sys.path.append('c:/Users/ST/Desktop/Agentic_Trader')
from database_manager import db_manager, Trade, OpenPosition

db_manager.connect()

print("=====================================================")
print("📂 OPEN POSITIONS IN DATABASE")
print("=====================================================")
positions = list(OpenPosition.select().dicts())
if not positions:
    print("No open positions found.")
for p in positions:
    print(f"ID: {p['id']} | Ticker: {p['ticker']} | Bias: {p['bias']} | Lots: {p['lots_sized']} | Entry Time: {p['entry_date']} | Entry Spot: {p['entry_spot_price']} | Mode: {p['mode']}")

print("\n=====================================================")
print("📜 CLOSED TRADES IN DATABASE (TRADE LEDGER)")
print("=====================================================")
trades = list(Trade.select().order_by(Trade.entry_date.desc()).dicts())
if not trades:
    print("No closed trades found.")
for t in trades:
    print(f"ID: {t['id']} | Ticker: {t['ticker']} | Bias: {t['bias']} | Lots: {t['lots_sized']} | Entry: {t['entry_date']} | Exit: {t['exit_date']} | PnL: {t['realized_pnl']} | Reason: {t['exit_reason']} | Mode: {t['mode']}")
print("=====================================================\n")
