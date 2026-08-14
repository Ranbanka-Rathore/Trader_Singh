"""
Phase 2, Step 1, probe 4 — THE decisive one.

Is intraday history for an EXPIRED option contract still retrievable by
security id? RESUME.md §5: "If it is not, options research must be built FORWARD
from recorded data while index/futures research can start on history
immediately."

The scrip master no longer lists expired contracts, so the ids come from this
project's own `order_audit` table — contracts the system actually placed paper
orders against in July, which have since expired. That matters: these are known
-good ids for known contracts on known trading days, so an empty response cannot
be waved away as "probably a bad id".

Two controls make the answer readable rather than merely suggestive:

  * a LIVE contract is probed with the identical call. If the live one returns
    bars and the expired ones do not, the difference is expiry, not the request.
  * the expired set spans two ages (expired ~10 days ago and ~38 days ago), so
    if there is a retention window rather than a hard cutoff, it shows up as a
    split rather than as a uniform blank.

Read only. Places no orders, writes nothing to the DB.

Usage:  ./venv/Scripts/python.exe scratch/phase2_probe_expired.py
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

OUT_PATH = os.path.join("scratch", "phase2_probe_expired_results.json")
SLEEP = 1.2

# (security_id, trading_symbol, expiry, a date the system actually traded it)
# Sourced from order_audit; see the module docstring.
EXPIRED = [
    ("65677", "NIFTY-Aug2026-23600-PE", date(2026, 8, 4), date(2026, 7, 6)),
    ("65685", "NIFTY-Aug2026-23800-PE", date(2026, 8, 4), date(2026, 7, 6)),
    ("44623", "NIFTY-Jul2026-24050-PE", date(2026, 7, 7), date(2026, 7, 6)),
    ("44643", "NIFTY-Jul2026-24250-PE", date(2026, 7, 7), date(2026, 7, 6)),
]

# Control: still live (expires 2026-08-18), proven to return bars in probe 3.
LIVE_CONTROL = ("45104", "NIFTY-Aug2026-24350-CE", date(2026, 8, 18))


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
    if isinstance(res, list):
        return len(res)
    return 0


def ask(dhan, security_id, from_d, to_d, instrument_type="OPTIDX"):
    rec = {
        "security_id": security_id,
        "from_date": from_d.isoformat(),
        "to_date": to_d.isoformat(),
        "instrument_type": instrument_type,
    }
    try:
        data = dhan.intraday_minute_data(
            security_id=str(security_id),
            exchange_segment="NSE_FNO",
            instrument_type=instrument_type,
            from_date=from_d.isoformat(),
            to_date=to_d.isoformat(),
        )
        rec["status"] = data.get("status")
        rec["bars"] = _count(data.get("data"))
        if data.get("status") != "success" or rec["bars"] == 0:
            rec["remarks"] = str(data.get("remarks"))[:300]
    except Exception as e:  # noqa: BLE001
        rec.update(status="EXCEPTION", bars=0, remarks=f"{type(e).__name__}: {e}"[:300])
    time.sleep(SLEEP)
    return rec


def main():
    dhan = _client()
    print("=" * 78)
    print("PHASE 2 STEP 1, PROBE 4 — is EXPIRED option history retrievable?")
    print(f"run at {datetime.now():%Y-%m-%d %H:%M:%S} IST")
    print("=" * 78)

    results = {"run_at": datetime.now().isoformat(), "control": [], "expired": []}

    # ---- control: a live contract, identical call shape ----------------------
    sid, sym, exp = LIVE_CONTROL
    print(f"\nCONTROL (live, expires {exp}): {sym}")
    to_d = date.today() - timedelta(days=1)
    r = ask(dhan, sid, to_d - timedelta(days=5), to_d)
    r.update(symbol=sym, expiry=str(exp), kind="live_control")
    results["control"].append(r)
    print(f"  {r['from_date']}..{r['to_date']}  bars={r['bars']}  {r.get('remarks','')}")
    if not r["bars"]:
        print("  !! control returned nothing — the expired results below prove nothing.")

    # ---- the expired contracts ----------------------------------------------
    print("\nEXPIRED contracts (window ends 1 day BEFORE expiry, when each still traded):")
    for sid, sym, exp, traded_on in EXPIRED:
        # Ask for the contract's final week, which certainly had trading.
        to_d = exp - timedelta(days=1)
        from_d = to_d - timedelta(days=5)
        r = ask(dhan, sid, from_d, to_d)
        r.update(symbol=sym, expiry=str(exp), kind="expired",
                 days_since_expiry=(date.today() - exp).days)
        results["expired"].append(r)
        print(f"  {sym:<28} expired {exp} ({r['days_since_expiry']}d ago)  "
              f"{from_d}..{to_d}  bars={r['bars']}  {r.get('remarks','')}")

        # Second look for the ones the system definitely traded, in case the
        # final week was thin: ask around the actual order date instead.
        if r["bars"] == 0 and traded_on:
            r2 = ask(dhan, sid, traded_on - timedelta(days=2), traded_on + timedelta(days=2))
            r2.update(symbol=sym, expiry=str(exp), kind="expired_retry_on_traded_date",
                      days_since_expiry=(date.today() - exp).days)
            results["expired"].append(r2)
            print(f"    retry around known trade date {traded_on}: bars={r2['bars']}  "
                  f"{r2.get('remarks','')}")

    with open(OUT_PATH, "w", encoding="utf-8") as f:
        json.dump(results, f, indent=2)

    ctrl_ok = any(x["bars"] for x in results["control"])
    exp_ok = any(x["bars"] for x in results["expired"])
    print("\n" + "=" * 78)
    if ctrl_ok and exp_ok:
        print("VERDICT: expired option history IS retrievable.")
        print("  -> options research can start on history now.")
    elif ctrl_ok and not exp_ok:
        print("VERDICT: expired option history is NOT retrievable (live control worked).")
        print("  -> options research must be built FORWARD from ticks we record.")
        print("  -> index/futures research can still start on history immediately.")
    else:
        print("VERDICT: INCONCLUSIVE — the live control returned no bars either.")
        print("  -> fix the control before drawing any conclusion from the expired set.")
    print(f"raw -> {OUT_PATH}")


if __name__ == "__main__":
    main()
