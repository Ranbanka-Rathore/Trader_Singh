import pandas as pd
import sys

sys.stdout.reconfigure(encoding='utf-8')

df = pd.read_csv("api-scrip-master.csv", nrows=100)
print("Columns:", list(df.columns))

df_full = pd.read_csv("api-scrip-master.csv", low_memory=False)
print("Unique Instrument Names:", list(df_full['SEM_INSTRUMENT_NAME'].unique()))
print("Unique Exchange IDs:", list(df_full['SEM_EXM_EXCH_ID'].unique()))

futidx_rows = df_full[df_full['SEM_INSTRUMENT_NAME'] == 'FUTIDX']
print("FUTIDX rows count:", len(futidx_rows))
if not futidx_rows.empty:
    print(futidx_rows[['SEM_SMST_SECURITY_ID', 'SEM_EXM_EXCH_ID', 'SEM_SEGMENT', 'SEM_TRADING_SYMBOL', 'SEM_EXPIRY_DATE']].head(10))
