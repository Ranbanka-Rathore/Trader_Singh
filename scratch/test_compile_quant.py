import os
import sys

# Add parent dir to path
sys.path.append(os.getcwd())

print("📡 Testing compile and import of QuantEngine...")
try:
    from backend.app.core.quant_engine import QuantEngine
    engine = QuantEngine()
    print("✅ QuantEngine imported and instantiated successfully!")
except Exception as e:
    print(f"❌ QuantEngine compilation failed: {e}")
    sys.exit(1)
