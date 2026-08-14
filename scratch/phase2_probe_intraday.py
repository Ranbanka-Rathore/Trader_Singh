"""
Phase 2, Step 1 — probe what intraday history Dhan will actually serve.

RESUME.md §5 makes this the first thing to run and says nothing else should be
built before it is answered. Four questions, in ascending order of how much they
decide:

  1. NIFTY spot/index      — how deep does the 1-min archive go?
  2. NIFTY front future    — same, per-contract rather than per-index
  3. a LIVE weekly option  — does a per-contract option archive exist at all?
  4. an EXPIRED option     — is it still retrievable after expiry?

(4) is the one that decides everything. If expired contracts are retrievable,
options research can start on history immediately. If they are not, options
research must be built FORWARD from ticks we record ourselves, while index and
futures research can proceed on history.

This script only reads. It places no orders and writes nothing to the DB; it
prints a table and drops the raw answers in scratch/phase2_probe_results.json so
the numbers can be pasted into RESUME.md without being retyped from a screen.

Usage:  ./venv/Scripts/python.exe scratch/phase2_probe_intraday.py
"""
import json
import os
import sys
import time
from datetime import date, datetime, timedelta

import pandas as pd
from dhanhq import dhanhq
from dotenv import load_dotenv

sys.path.insert(0, os.getcwd())
load_dotenv()

from backend.app.core import scrip_master  # noqa: E402

OUT_PATH = os.path.join("scratch", "phase2_probe_results.json")

# Dhan rate-limits historical calls; the archive-depth question does not need
# many samples, it needs well-spaced ones.
SLEEP_BETWEEN_CALLS = 1.2

# How far back to look, in days. Each probe asks for a 5-day window ENDING at
# that age, so a hit means "the archive reaches at least this far back".
DEPTH_LADDER_DAYS = [3, 30, 90, 180, 365, 730, 1095, 1825]


def _client():
    cid = os.getenv("DHAN_CLIENT_ID")
    tok = os.getenv("DHAN_ACCESS_TOKEN")
    if not cid or not tok:
        sys.exit("DHAN_CLIENT_ID / DHAN_ACCESS_TOKEN missing from .env")
    return dhanhq(cid, tok)


def _normalise_bars(res):
    """Dhan returns either a dict-of-arrays or a list-of-dicts depending on
    version and endpoint. Return (count, first_ts, last_ts) for either, or
    (0, None, None) when the payload is empty."""
    if not res:
        return 0, None, None

    if isinstance(res, dict):
        if "open" not in res or not res.get("open"):
            return 0, None, None
        ts = res.get("timestamp") or res.get("start_Time") or []
        count = len(res["open"])
    elif isinstance(res, list):
        count = len(res)
        if count == 0:
            return 0, None, None
        ts = [r.get("timestamp") or r.get("start_Time") for r in res]
    else:
        return 0, None, None

    if not ts:
        return count, None, None

    def _fmt(v):
        if v is None:
            return None
        if isinstance(v, (int, float)):
            # Dhan epochs are seconds unless they are obviously milliseconds.
            unit = "ms" if v > 1e11 else "s"
            # Dhan's epoch is IST-based; render in IST so bars line up with
            # the trading session rather than appearing 5h30 early.
            return str(pd.to_datetime(v, unit=unit) + timedelta(hours=5, minutes=30))
        return str(v)

    return count, _fmt(ts[0]), _fmt(ts[-1])


def probe(dhan, label, security_id, segment, instrument_type, days_ago, window=5):
    """One archive-depth sample: a `window`-day range ending `days_ago` days back."""
    to_d = date.today() - timedelta(days=days_ago)
    from_d = to_d - timedelta(days=window)
    rec = {
        "instrument": label,
        "security_id": str(security_id),
        "segment": segment,
        "instrument_type": instrument_type,
        "days_ago": days_ago,
        "from_date": from_d.isoformat(),
        "to_date": to_d.isoformat(),
    }
    try:
        data = dhan.intraday_minute_data(
            security_id=str(security_id),
            exchange_segment=segment,
            instrument_type=instrument_type,
            from_date=from_d.isoformat(),
            to_date=to_d.isoformat(),
        )
        status = data.get("status")
        rec["status"] = status
        if status == "success":
            count, first_ts, last_ts = _normalise_bars(data.get("data"))
            rec.update(bars=count, first_ts=first_ts, last_ts=last_ts)
        else:
            # Keep the broker's own words — "no data" and "bad security id" are
            # different answers and must not be collapsed into one failure.
            rec.update(bars=0, remarks=str(data.get("remarks"))[:300])
    except Exception as e:  # noqa: BLE001 - probe must survive any single failure
        rec.update(status="EXCEPTION", bars=0, remarks=f"{type(e).__name__}: {e}"[:300])

    time.sleep(SLEEP_BETWEEN_CALLS)
    return rec


def depth_ladder(dhan, label, security_id, segment, instrument_type, ladder=None):
    print(f"\n--- {label}  (secid={security_id}, {segment}, {instrument_type}) ---")
    rows = []
    for d in ladder or DEPTH_LADDER_DAYS:
        r = probe(dhan, label, security_id, segment, instrument_type, d)
        rows.append(r)
        when = f"{r['from_date']}..{r['to_date']}"
        if r["status"] == "success" and r["bars"]:
            print(f"  {d:>5}d ago  {when}  {r['bars']:>6} bars   {r.get('first_ts')} -> {r.get('last_ts')}")
        else:
            print(f"  {d:>5}d ago  {when}  {'EMPTY':>6}        {r.get('remarks', r['status'])}")
    return rows


def pick_live_option(ref_spot):
    """Nearest-expiry ATM call from the scrip master."""
    expiries = [e for e in scrip_master.get_option_expiries("NIFTY") if e >= date.today()]
    if not expiries:
        return None
    expiry = expiries[0]
    strike = round(ref_spot / 50.0) * 50
    for offset in (0, 50, -50, 100, -100):
        c = scrip_master.resolve_option_contract("NIFTY", strike + offset, "CE", expiry=expiry)
        if c:
            return c
    return None


def main():
    dhan = _client()

    print("=" * 78)
    print("PHASE 2 STEP 1 — Dhan intraday archive probe")
    print(f"run at {datetime.now():%Y-%m-%d %H:%M:%S} IST")
    print("=" * 78)

    funds = dhan.get_fund_limits()
    if funds.get("status") != "success":
        sys.exit(f"Token rejected: {funds}")
    bal = funds.get("data", {})
    print(f"token OK — available balance INR {bal.get('availabelBalance')}")

    results = {"run_at": datetime.now().isoformat(), "probes": []}

    # ---- 1. NIFTY spot index -------------------------------------------------
    results["probes"] += depth_ladder(dhan, "NIFTY_INDEX", "13", "IDX_I", "INDEX")

    # ---- 2. NIFTY front-month future ----------------------------------------
    fut = None
    try:
        import csv
        with open("api-scrip-master.csv", encoding="utf-8", newline="") as f:
            rd = csv.reader(f)
            next(rd, None)
            best = None
            for row in rd:
                if len(row) <= 10 or row[3].strip().upper() != "FUTIDX":
                    continue
                if not row[5].strip().upper().startswith("NIFTY-"):
                    continue
                if row[5].split("-")[0].strip().upper() != "NIFTY":
                    continue
                try:
                    exp = datetime.strptime(row[8][:10], "%Y-%m-%d").date()
                except ValueError:
                    continue
                if exp >= date.today() and (best is None or exp < best[0]):
                    best = (exp, row[2].strip(), row[5].strip())
            fut = best
    except Exception as e:  # noqa: BLE001
        print(f"could not read futures from scrip master: {e}")

    if fut:
        print(f"\nfront future: {fut[2]} (expiry {fut[0]})")
        results["probes"] += depth_ladder(dhan, f"FUT {fut[2]}", fut[1], "NSE_FNO", "FUTIDX")
        results["front_future"] = {"symbol": fut[2], "security_id": fut[1], "expiry": str(fut[0])}
    else:
        print("\n!! no NIFTY future found in scrip master")

    # ---- 3. a LIVE weekly option --------------------------------------------
    spot = None
    idx_bars = [p for p in results["probes"] if p["instrument"] == "NIFTY_INDEX" and p.get("bars")]
    try:
        q = dhan.ohlc_data(securities={"IDX_I": [13]})
        if q.get("status") == "success":
            spot = float(q["data"]["data"]["IDX_I"]["13"]["last_price"])
    except Exception:  # noqa: BLE001
        pass
    if spot is None and idx_bars:
        spot = 25000.0  # only used to centre the strike search
    print(f"\nNIFTY spot for strike selection: {spot}")

    opt = pick_live_option(spot or 25000.0)
    if opt:
        print(f"live option: {opt['trading_symbol']} (expiry {opt['expiry']})")
        results["live_option"] = {k: str(v) for k, v in opt.items()}
        results["probes"] += depth_ladder(
            dhan, f"OPT-LIVE {opt['trading_symbol']}", opt["security_id"],
            opt["exchange_segment"], "OPTIDX",
            ladder=[3, 15, 30, 60, 90],  # a weekly contract cannot be older than this
        )
    else:
        print("!! could not resolve a live ATM option from the scrip master")

    with open(OUT_PATH, "w", encoding="utf-8") as f:
        json.dump(results, f, indent=2)
    print(f"\nraw results -> {OUT_PATH}")
    print("\nNOTE: probe 4 (EXPIRED contract) needs a security id of an already-")
    print("expired contract; the scrip master no longer lists them. Run")
    print("scratch/phase2_probe_expired.py once such an id is sourced.")


if __name__ == "__main__":
    main()
