with open("c:/Users/ST/Desktop/Agentic_Trader/dhan_integration.py", "r", encoding="utf-8") as f:
    lines = f.readlines()

for idx, line in enumerate(lines):
    if "def get_historical_intraday" in line or "historical" in line:
        print(f"Line {idx+1}: {line.strip()}")
