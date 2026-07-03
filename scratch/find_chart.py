path = "frontend/src/App.tsx"
with open(path, "r", encoding="utf-8") as f:
    for num, line in enumerate(f, 1):
        if "RealtimeChart" in line or "chart" in line.lower():
            if "RealtimeChart" in line or "chart" in line:
                print(f"{num}: {line.strip()}")
