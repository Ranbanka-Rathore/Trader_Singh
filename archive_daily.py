"""
Daily intraday archive run — the standing data obligation of Amendment E10.

WHY THIS EXISTS
---------------
Dhan deletes option intraday history at expiry (verified 2026-08-14: four expired
contracts from this project's own order_audit returned 0 bars against a live
control returning 1,540). History not captured before an expiry cannot be bought
back at any price. Every Tuesday, ~460 NIFTY contracts take their entire intraday
record with them.

Amendment E10 fixes what the accumulating sample must reach before any hypothesis
may be registered against it: **250 distinct trading days, 3 sign-consistent
quarters, and 2 shock days**. This script accumulates toward that and reports
progress against it — day counts and shock counts only, never P&L. E10.4: the
next look at this data is the one that follows a registration.

TOKEN
-----
The Dhan access token lives exactly **24 hours**. Unattended scheduling therefore
cannot outlive a single day without the token being refreshed in .env. This
script checks the token FIRST and exits non-zero with the remaining lifetime, so
a scheduled run fails loudly and visibly rather than silently archiving nothing.

USAGE
-----
    python archive_daily.py              # index + futures + option board
    python archive_daily.py --coverage   # report progress only, no API calls

Scheduling on Windows (Task Scheduler), after refreshing the token:
    Program:   D:\\Projects\\Agentic_Trader\\venv\\Scripts\\python.exe
    Arguments: archive_daily.py
    Start in:  D:\\Projects\\Agentic_Trader
Run it AFTER 15:30 IST so the session is complete, on trading days only.
"""
import argparse
import base64
import datetime
import glob
import json
import os
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# Amendment E10.2 — fixed in advance, not renegotiable.
BAR_DAYS = 250
BAR_QUARTERS = 3
BAR_SHOCKS = 2
SHOCK_VARIANCE = 152.5e-6      # p99 of 989 index sessions, 2022-08..2026-08

ARCHIVE = os.path.join("data", "intraday", "NIFTY")
LOGDIR = os.path.join("logs", "archive")


def token_status():
    """(ok, message, hours_left). Reads the JWT expiry without calling the API."""
    path = ".env"
    if not os.path.exists(path):
        return False, ".env not found", 0.0
    tok = None
    for line in open(path, encoding="utf-8", errors="replace"):
        if line.startswith("DHAN_ACCESS_TOKEN="):
            tok = line.split("=", 1)[1].strip()
    if not tok:
        return False, "DHAN_ACCESS_TOKEN missing from .env", 0.0
    try:
        p = tok.split(".")[1]
        p += "=" * (-len(p) % 4)
        exp = json.loads(base64.urlsafe_b64decode(p))["exp"]
    except Exception as e:  # noqa: BLE001
        return False, f"could not parse token ({type(e).__name__})", 0.0
    left = (exp - datetime.datetime.now().timestamp()) / 3600.0
    if left <= 0:
        return False, (f"TOKEN EXPIRED {abs(left):.1f}h ago "
                       f"(at {datetime.datetime.fromtimestamp(exp):%Y-%m-%d %H:%M}). "
                       f"Refresh DHAN_ACCESS_TOKEN in .env — nothing was archived."), left
    return True, f"token valid for {left:.1f}h", left


COVERAGE_CACHE = os.path.join("data", "intraday", "coverage_cache.json")


def _option_days():
    """Exact set of distinct option trading days on disk, cached incrementally.

    The naive version re-read every option parquet on each call. At 1,200 files
    that already took minutes, and the archive grows by ~460 contracts a week —
    a coverage check that gets slower every day is one that stops being run.
    Cache each file's day list against its (size, mtime) and re-read only what
    changed. Still EXACT, which matters because this count gates E10.2.
    """
    import pandas as pd

    cache = {}
    if os.path.exists(COVERAGE_CACHE):
        try:
            with open(COVERAGE_CACHE, encoding="utf-8") as fh:
                cache = json.load(fh)
        except Exception:  # noqa: BLE001
            cache = {}

    days, fresh, reread = set(), {}, 0
    for f in glob.glob(os.path.join(ARCHIVE, "opt", "*", "*.parquet")):
        try:
            st = os.stat(f)
        except OSError:
            continue
        key = f"{os.path.getsize(f)}:{int(st.st_mtime)}"
        hit = cache.get(f)
        if hit and hit.get("key") == key:
            fresh[f] = hit
            days |= set(hit["days"])
            continue
        try:
            d = sorted({str(x) for x in
                        pd.read_parquet(f, columns=["ts"])["ts"].dt.date.unique()})
        except Exception:  # noqa: BLE001
            continue
        fresh[f] = {"key": key, "days": d}
        days |= set(d)
        reread += 1

    try:
        os.makedirs(os.path.dirname(COVERAGE_CACHE), exist_ok=True)
        with open(COVERAGE_CACHE, "w", encoding="utf-8") as fh:
            json.dump(fresh, fh)
    except Exception:  # noqa: BLE001
        pass
    return {datetime.date.fromisoformat(d) for d in days}, reread


def coverage():
    """Distinct archived option days, quarters and shock days. No P&L, per E10.4."""
    import numpy as np
    import pandas as pd

    days, reread = _option_days()
    if reread:
        print(f"  (coverage: re-read {reread} changed file(s))")

    shocks = []
    idx_path = os.path.join(ARCHIVE, "index.parquet")
    if os.path.exists(idx_path) and days:
        idx = pd.read_parquet(idx_path)
        t = idx["ts"].dt.time
        idx = idx[(t >= datetime.time(9, 15)) & (t <= datetime.time(15, 30))].copy()
        idx["date"] = idx["ts"].dt.date
        for d, g in idx[idx["date"].isin(days)].groupby("date"):
            r = g.sort_values("ts")["close"].astype(float).pct_change()
            if float(np.nansum(r ** 2)) >= SHOCK_VARIANCE:
                shocks.append(d)

    quarters = {(d.year, (d.month - 1) // 3 + 1) for d in days}
    return {"days": sorted(days), "n_days": len(days),
            "quarters": sorted(quarters), "n_quarters": len(quarters),
            "shocks": sorted(shocks), "n_shocks": len(shocks)}


def print_coverage(c):
    print("\n" + "=" * 66)
    print("ARCHIVE COVERAGE vs Amendment E10.2  (day counts only — E10.4)")
    print("=" * 66)

    def row(label, have, need):
        pct = min(100.0, 100.0 * have / need) if need else 100.0
        bar = "#" * int(pct / 4) + "." * (25 - int(pct / 4))
        print(f"  {label:<26} {have:>4} / {need:<4} [{bar}] {pct:5.1f}%")

    row("distinct trading days", c["n_days"], BAR_DAYS)
    row("sign-consistent quarters", c["n_quarters"], BAR_QUARTERS)
    row("shock days (var >= p99)", c["n_shocks"], BAR_SHOCKS)
    if c["days"]:
        print(f"\n  range {c['days'][0]} .. {c['days'][-1]}")
    if c["shocks"]:
        print(f"  shock days so far: {', '.join(str(d) for d in c['shocks'])}")
    else:
        print(f"  shock days so far: NONE — the tail is unsampled (Section 6.4)")

    met = (c["n_days"] >= BAR_DAYS and c["n_quarters"] >= BAR_QUARTERS
           and c["n_shocks"] >= BAR_SHOCKS)
    print()
    if met:
        print("  E10.2 BAR MET. A hypothesis may now be registered against this")
        print("  sample. It still must be registered BEFORE it is looked at.")
    else:
        short = []
        if c["n_days"] < BAR_DAYS:
            short.append(f"{BAR_DAYS - c['n_days']} more days")
        if c["n_quarters"] < BAR_QUARTERS:
            short.append(f"{BAR_QUARTERS - c['n_quarters']} more quarters")
        if c["n_shocks"] < BAR_SHOCKS:
            short.append(f"{BAR_SHOCKS - c['n_shocks']} more shock days")
        print(f"  BAR NOT MET — short by: {', '.join(short)}.")
        print("  No hypothesis may be registered against this sample yet (E10.2).")
    print("=" * 66)


def run(cmd):
    print(f"\n$ {' '.join(cmd[2:])}")
    r = subprocess.run([sys.executable] + cmd[1:], cwd=os.getcwd())
    return r.returncode


def main():
    ap = argparse.ArgumentParser(description="daily intraday archive (Amendment E10)")
    ap.add_argument("--coverage", action="store_true",
                    help="report coverage only; makes no API calls")
    ap.add_argument("--band", type=float, default=15.0)
    ap.add_argument("--expiries", type=int, default=4)
    a = ap.parse_args()

    if a.coverage:
        print_coverage(coverage())
        return 0

    ok, msg, left = token_status()
    print(f"[{datetime.datetime.now():%Y-%m-%d %H:%M:%S}] {msg}")
    if not ok:
        print("\nARCHIVE ABORTED. Nothing was captured.")
        print("Contracts expiring before the next successful run lose their")
        print("intraday history permanently — this is not a retryable failure.")
        return 2
    if left < 1.0:
        print("WARNING: under an hour of token life left; a long board run may "
              "fail partway. Refresh the token first if you can.")

    os.makedirs(LOGDIR, exist_ok=True)
    rc = 0
    rc |= run([sys.executable, "-m", "backtest.intraday_archive",
               "--index", "--futures", "--days", "365"])
    rc |= run([sys.executable, "-m", "backtest.intraday_archive",
               "--board", "--band", str(a.band), "--expiries", str(a.expiries)])

    print_coverage(coverage())
    if rc:
        print("\none or more archive steps returned non-zero — check the output above")
    return rc


if __name__ == "__main__":
    sys.exit(main())
