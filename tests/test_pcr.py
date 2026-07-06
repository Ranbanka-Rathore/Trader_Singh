"""
PCR data-quality test.

Live snapshot PCR was computed from change-in-OI (Put_COI/Call_COI) summed over the
whole chain, which produced garbage (observed 7.74, 14.81) and corrupted the ladder's
PCR-based side selection. It must be open-interest PCR (Put OI / Call OI), matching the
validated backtest's real_backtester.chain_pcr. This test locks that equivalence.

Run with:  PYTHONUTF8=1 python tests/test_pcr.py
"""
import datetime
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import pandas as pd
from backend.app.services.data_service import oi_pcr
from backtest.real_backtester import chain_pcr

PASS, FAIL = 0, 0


def check(name, cond):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  ✅ {name}")
    else:
        FAIL += 1
        print(f"  ❌ {name}")


def _df(rows):
    # rows: {strike: (put_oi, call_oi, put_coi, call_coi)}
    return pd.DataFrame([
        {"Strike": k, "Put_OI": p, "Call_OI": c, "Put_COI": pc, "Call_COI": cc}
        for k, (p, c, pc, cc) in rows.items()
    ])


def test_basic():
    print("\n[1] oi_pcr basics")
    df = _df({23800: (1200, 1000, 5, -3), 24000: (1500, 1100, -2, 1)})
    # put 2700 / call 2100 = 1.2857
    check("Put OI / Call OI", abs(oi_pcr(df) - (2700 / 2100)) < 1e-9)
    check("uses OI not COI (COI would give a different, garbage ratio)",
          abs(oi_pcr(df) - (3 / -2)) > 0.1)
    check("zero call OI -> neutral 1.0", oi_pcr(_df({24000: (500, 0, 0, 0)})) == 1.0)
    check("missing OI columns -> neutral 1.0", oi_pcr(pd.DataFrame([{"Strike": 1, "Put_COI": 9}])) == 1.0)


def test_matches_backtest():
    print("\n[2] live oi_pcr == validated backtest chain_pcr (no sim/live drift)")
    exp = datetime.date(2026, 8, 4)
    # strike: (put_oi, call_oi)
    book = {23600: (1800, 700), 23800: (1200, 900), 24000: (1500, 1100),
            24200: (800, 1300), 24400: (400, 1600)}
    df = _df({k: (p, c, 0, 0) for k, (p, c) in book.items()})
    chain = {"options": {}}
    for k, (p, c) in book.items():
        chain["options"][(exp, float(k), "PE")] = {"oi": p}
        chain["options"][(exp, float(k), "CE")] = {"oi": c}
    live = oi_pcr(df)
    bt = chain_pcr(chain, exp)
    check(f"live {live:.5f} == backtest {bt:.5f}", abs(live - bt) < 1e-9)
    # put 5700 / call 5600
    check("value is a sane PCR (~1.0), not garbage", 0.9 < live < 1.1)


if __name__ == "__main__":
    test_basic()
    test_matches_backtest()
    print(f"\n{'=' * 50}\nRESULT: {PASS} passed, {FAIL} failed")
    sys.exit(1 if FAIL else 0)
