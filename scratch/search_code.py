import os

search_terms = ["market_snapshot:", "run_autotrender_cycle", "set_json", "publish"]
dirs = [".", "backend/app", "backend/app/services", "backend/app/core"]

for term in search_terms:
    print(f"=== SEARCHING FOR: {term} ===")
    for root, d_names, f_names in os.walk("."):
        if "venv" in root or ".git" in root or "__pycache__" in root:
            continue
        for f in f_names:
            if f.endswith(".py"):
                path = os.path.join(root, f)
                try:
                    with open(path, "r", encoding="utf-8") as file:
                        for line_num, line in enumerate(file, 1):
                            if term in line:
                                print(f"{path}:{line_num}: {line.strip()}")
                except Exception:
                    pass
    print("\n")
