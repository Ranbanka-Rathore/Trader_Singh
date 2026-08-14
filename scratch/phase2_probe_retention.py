"""
Phase 2, Step 1, follow-up — where exactly is the F&O retention edge?

Probe 4 established that once a contract expires its intraday history is gone.
That leaves a question which decides how Step 2 should be built at all:

  For a contract that is STILL LIVE, how far back does Dhan serve 1-min bars?

  * If it is deep (weeks), then options history can be captured by a DAILY
    ARCHIVAL JOB that pulls each live contract's bars before it expires — and
    every currently-live contract can be backfilled today, retroactively.
  * If it is shallow (days), nothing can be backfilled and a live TICK RECORDER
    is the only way to accumulate option history.

The difference is days of work and, more importantly, whether the history we can
never buy back starts accruing today or started accruing weeks ago.

Bisects on the front-month future (listed months ago, so contract age does not
confound the answer) and cross-checks on a live weekly option.

Read only.

Usage:  ./venv/Scripts/python.exe scratch/phase2_probe_retention.py
"""
import json
import os
import sys
import time
from datetime import date, datetime, timedelta

from dhanhq import dhanhq
from dotenv import load_dotenv

sys.path.insert(0, os.getcwd())
load_dotenv()

OUT_PATH = os.path.join("scratch", "phase2_probe_retention_results.json")
SLEEP = 1.2

# Front-month future: expires 2026-08-25, listed long ago. Probe 2 showed bars at
# 30d and nothing at 90d, so the edge is somewhere between.
FUTURE = ("58072", "NIFTY-Aug2026-FUT", "FUTIDX")
# Live weekly option, expires 2026-08-18. Cross-check only: a weekly contract is
# young, so an empty result here may mean "not yet listed" rather than "purged".
OPTION = ("45104", "NIFTY-Aug2026-24350-CE", "OPTIDX")

LADDER = [30, 40, 50, 60, 70, 80, 90, 120]


def _client():
    cid, tok = os.getenv("DHAN_CLIENT_ID"), os.getenv("DHAN_ACCESS_TOKEN")
    if not cid or not tok:
        sys.exit("DHAN_CLIENT_ID / DHAN_ACCESS_TOKEN missing from .env")
    return dhanhq(cid, tok)


def _count(res):
    if not res:
        return 0
    if isinstance(res, dict):
        return len(res.get("open") or [])
    return len(res) if isinstance(res, list) else 0


def ask(dhan, sid, itype, days_ago, window=4):
    to_d = date.today() - timedelta(days=days_ago)
    from_d = to_d - timedelta(days=window)
    rec = {"security_id": sid, "instrument_type": itype, "days_ago": days_ago,
           "from_date": from_d.isoformat(), "to_date": to_d.isoformat()}
    try:
        data = dhan.intraday_minute_data(
            security_id=str(sid), exchange_segment="NSE_FNO",
            instrument_type=itype,
            from_date=from_d.isoformat(), to_date=to_d.isoformat(),
        )
        rec["status"] = data.get("status")
        rec["bars"] = _count(data.get("data"))
        if rec["bars"] == 0:
            rec["remarks"] = str(data.get("remarks"))[:200]
    except Exception as e:  # noqa: BLE001
        rec.update(status="EXCEPTION", bars=0, remarks=f"{type(e).__name__}: {e}"[:200])
    time.sleep(SLEEP)
    return rec


def run(dhan, label, sid, itype, results):
    print(f"\n--- {label} (secid={sid}, {itype}) ---")
    deepest = None
    for d in LADDER:
        r = ask(dhan, sid, itype, d)
        r["instrument"] = label
        results.append(r)
        mark = f"{r['bars']:>6} bars" if r["bars"] else "  EMPTY"
        print(f"  {d:>4}d ago  {r['from_date']}..{r['to_date']}  {mark}")
        if r["bars"]:
            deepest = d
    return deepest


def main():
    dhan = _client()
    print("=" * 78)
    print("PHASE 2 — F&O intraday retention depth for LIVE contracts")
    print(f"run at {datetime.now():%Y-%m-%d %H:%M:%S} IST")
    print("=" * 78)

    results = []
    fut_depth = run(dhan, FUTURE[1], FUTURE[0], FUTURE[2], results)
    opt_depth = run(dhan, OPTION[1], OPTION[0], OPTION[2], results)

    with open(OUT_PATH, "w", encoding="utf-8") as f:
        json.dump({"run_at": datetime.now().isoformat(), "probes": results}, f, indent=2)

    print("\n" + "=" * 78)
    print(f"deepest hit — future: {fut_depth}d ago | option: {opt_depth}d ago")
    if fut_depth and fut_depth >= 30:
        print("A live contract retains weeks of 1-min history.")
        print("  -> Step 2 can be a DAILY ARCHIVAL JOB, and every live contract")
        print("     can be backfilled retroactively today.")
    else:
        print("A live contract retains only days of history.")
        print("  -> Step 2 must be a live TICK RECORDER; nothing can be backfilled.")
    print(f"raw -> {OUT_PATH}")


if __name__ == "__main__":
    main()
