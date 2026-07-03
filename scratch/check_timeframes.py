import sys
sys.path.append('.')
from scratch.backtest_today import get_db_engine
import pandas as pd

engine = get_db_engine()
try:
    print(pd.read_sql("select timeframe, count(1) from market_indicators group by timeframe", engine))
except Exception as e:
    print("Error:", e)
