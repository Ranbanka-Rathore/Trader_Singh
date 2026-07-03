import re

with open("c:/Users/ST/Desktop/Agentic_Trader/frontend/src/App.tsx", "r", encoding="utf-8") as f:
    lines = f.readlines()

for idx, line in enumerate(lines):
    if "dev_mode" in line or "Autopilot" in line or "sidebar" in line.lower() or "active_nodes" in line:
        print(f"Line {idx+1}: {line.strip()}")
