import datetime as dt, sys, collections
sys.path.insert(0, r"D:\Projects\Agentic_Trader")
from backtest import futures
for gate in ("strict", "strict_legacy"):
    dates = futures.trading_dates(dt.date(2023,1,1), dt.date(2026,8,10))
    p = futures.build_panel(dates, kind="stock", gate=gate)
    by_year = collections.Counter()
    for ser in p.series.values():
        for b in ser.bars:
            by_year[b.date.year] += 1
    print(f"gate={gate:14s} pass={p.pass_rate:5.2f}%  fillable bars by year: "
          f"{dict(sorted(by_year.items()))}")
