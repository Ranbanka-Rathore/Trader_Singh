"""
Phase 2 — 1-minute intraday archive for NIFTY index, futures and options.

WHY THIS EXISTS, AND WHY IT IS URGENT
-------------------------------------
Probed 2026-08-14 (scratch/phase2_probe_*.py), against Dhan's intraday endpoint:

  index (secid 13)   5+ years of 1-min bars, always available
  futures            ~75 days, rolling purge, regardless of contract life
  live option        available from the contract's FIRST TRADE to now
  EXPIRED option     NOTHING. Zero bars, permanently.

The last line is the whole point. Four expired contracts taken from this
project's own `order_audit` — known-good security ids, for contracts the system
actually placed orders against — returned 0 bars at both 10 and 38 days past
expiry, while a live control on the identical call returned 1540. Option
intraday history is not archived by the broker; it is deleted at expiry.

So there is no such thing as "we'll pull the option data when we need it". Every
Tuesday, ~460 NIFTY contracts take their entire intraday history with them. This
module's job is to get it onto disk first.

The one piece of good news the probe also found: a single call with a wide
`from_date` returns a contract's *complete* history, not a page of it. One call
per contract is enough, which is what makes archiving the whole live board
affordable (~1,200 contracts in ~20 minutes).

DISCIPLINE (mirrors backtest/bhavcopy.py, per RESUME.md Step 2)
---------------------------------------------------------------
  * cache to disk; never re-fetch a contract already captured past its expiry
  * explicit schema, written once, below
  * an empty range is RECORDED as empty with a reason, never silently returned
    as success — `status` in the manifest distinguishes ok / empty / error
  * a quality report (`report()`) that flags what is suspect rather than
    averaging it away

STORAGE
-------
  data/intraday/NIFTY/index.parquet                    index, one file, appended
  data/intraday/NIFTY/fut/<trading_symbol>.parquet     one file per contract
  data/intraday/NIFTY/opt/<expiry>/<symbol>.parquet    one file per contract
  data/intraday/manifest.json                          what we have, and its quality

SCHEMA (every parquet)
----------------------
  ts          datetime64[ns]  bar START, IST (Dhan serves a UTC epoch; +5:30)
  open/high/low/close  float64
  volume      int64           0 for the index, which has no volume

Usage:
  python -m backtest.intraday_archive --index
  python -m backtest.intraday_archive --board --band 15 --expiries 4
  python -m backtest.intraday_archive --report
"""
import argparse
import datetime
import json
import os
import sys
import time
from typing import Dict, List, Optional, Tuple

import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

ARCHIVE_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "intraday")
MANIFEST_PATH = os.path.join(ARCHIVE_DIR, "manifest.json")

# Dhan's documented data-API ceiling is 5 req/s. Stay well under it: the archive
# is not latency-sensitive and a 429 mid-run costs more than the sleep does.
SLEEP_BETWEEN_CALLS = 0.35
MAX_RETRIES = 3

# Dhan rejects any range wider than 90 days outright (DH-905, "Data for Intraday
# Charts can be fetched for 90 days at a time"), so 89 is the widest safe ask.
# Within that limit one call returns a contract's whole life rather than a page
# of it — verified: a 90-day ask and a 45-day ask both returned the same 7,496
# bars, starting at the contract's first trade. That is what makes one call per
# contract sufficient for F&O, whose retention (~75 days) fits inside the window.
# The index reaches back years and is therefore fetched in paged 89-day windows.
CHUNK_DAYS = 89
LOOKBACK_DAYS = 89

# NSE session, used only to FLAG suspect bars in the quality report — never to
# silently drop them. The 2021 index probe returned bars as late as 18:42, which
# is exactly the sort of thing that must surface rather than be cleaned away.
SESSION_START = datetime.time(9, 15)
SESSION_END = datetime.time(15, 30)

_IST_OFFSET = datetime.timedelta(hours=5, minutes=30)


# --------------------------------------------------------------------------
# client
# --------------------------------------------------------------------------
def _client():
    from dotenv import load_dotenv
    load_dotenv()
    from dhanhq import dhanhq
    cid, tok = os.getenv("DHAN_CLIENT_ID"), os.getenv("DHAN_ACCESS_TOKEN")
    if not cid or not tok:
        raise RuntimeError("DHAN_CLIENT_ID / DHAN_ACCESS_TOKEN missing from .env")
    return dhanhq(cid, tok)


# --------------------------------------------------------------------------
# fetch + parse
# --------------------------------------------------------------------------
def _to_frame(payload) -> pd.DataFrame:
    """Normalise Dhan's two payload shapes into the archive schema.

    Returns an EMPTY frame with the right columns when there is no data, so
    callers never have to guess whether they got a frame or None.
    """
    cols = ["ts", "open", "high", "low", "close", "volume"]
    if not payload:
        return pd.DataFrame(columns=cols)

    if isinstance(payload, dict):
        if not payload.get("open"):
            return pd.DataFrame(columns=cols)
        raw = pd.DataFrame({
            "ts": payload.get("timestamp") or payload.get("start_Time") or [],
            "open": payload.get("open", []),
            "high": payload.get("high", []),
            "low": payload.get("low", []),
            "close": payload.get("close", []),
            "volume": payload.get("volume", [0] * len(payload["open"])),
        })
    elif isinstance(payload, list):
        if not payload:
            return pd.DataFrame(columns=cols)
        raw = pd.DataFrame(payload)
        raw = raw.rename(columns={"start_Time": "ts", "timestamp": "ts"})
        for c in cols:
            if c not in raw.columns:
                raw[c] = 0
        raw = raw[cols]
    else:
        return pd.DataFrame(columns=cols)

    if raw.empty:
        return pd.DataFrame(columns=cols)

    ts = raw["ts"]
    if pd.api.types.is_numeric_dtype(ts):
        # Dhan serves a true UTC epoch; the archive stores IST wall-clock so a
        # bar's time reads directly against the trading session.
        unit = "ms" if float(ts.iloc[0]) > 1e11 else "s"
        raw["ts"] = pd.to_datetime(ts, unit=unit, utc=True).dt.tz_localize(None) + _IST_OFFSET
    else:
        raw["ts"] = pd.to_datetime(ts, errors="coerce")

    raw = raw.dropna(subset=["ts"])
    for c in ("open", "high", "low", "close"):
        raw[c] = pd.to_numeric(raw[c], errors="coerce")
    raw["volume"] = pd.to_numeric(raw.get("volume", 0), errors="coerce").fillna(0).astype("int64")
    raw = raw.dropna(subset=["open", "high", "low", "close"])
    return raw.sort_values("ts").drop_duplicates(subset="ts").reset_index(drop=True)


def fetch(dhan, security_id: str, segment: str, instrument_type: str,
          days: int = LOOKBACK_DAYS, end: Optional[datetime.date] = None
          ) -> Tuple[pd.DataFrame, str, str]:
    """Fetch one window (<= 90 days) of a contract's 1-min history.

    Returns (frame, status, note). status is 'ok' | 'empty' | 'error' — an empty
    response is a first-class outcome with its own record, not a silent success.
    """
    if days > CHUNK_DAYS:
        raise ValueError(f"window {days}d exceeds Dhan's 90-day ceiling; use fetch_paged")
    to_d = end or datetime.date.today()
    from_d = to_d - datetime.timedelta(days=days)

    last_err = ""
    for attempt in range(MAX_RETRIES):
        try:
            resp = dhan.intraday_minute_data(
                security_id=str(security_id),
                exchange_segment=segment,
                instrument_type=instrument_type,
                from_date=from_d.isoformat(),
                to_date=to_d.isoformat(),
            )
            time.sleep(SLEEP_BETWEEN_CALLS)
            if resp.get("status") != "success":
                note = str(resp.get("remarks"))[:200]
                # Rate limiting is worth retrying; a bad id is not.
                if "rate" in note.lower() or "limit" in note.lower():
                    last_err = note
                    time.sleep(2 ** attempt)
                    continue
                return pd.DataFrame(), "error", note
            df = _to_frame(resp.get("data"))
            if df.empty:
                return df, "empty", "API returned success with no bars"
            return df, "ok", ""
        except Exception as e:  # noqa: BLE001
            last_err = f"{type(e).__name__}: {e}"[:200]
            time.sleep(1.0 + 2 ** attempt)
    return pd.DataFrame(), "error", last_err or "exhausted retries"


def fetch_paged(dhan, security_id: str, segment: str, instrument_type: str,
                days: int) -> Tuple[pd.DataFrame, str, str]:
    """Fetch an arbitrarily long history by walking backwards in 89-day windows.

    Stops early after two consecutive empty windows: the archive is contiguous
    back to its start, so a run of empties means the start has been passed, and
    continuing would only spend rate limit on dates that will never have data.
    """
    end = datetime.date.today()
    earliest = end - datetime.timedelta(days=days)
    frames, notes = [], []
    empties = 0
    while end > earliest:
        span = min(CHUNK_DAYS, (end - earliest).days)
        df, status, note = fetch(dhan, security_id, segment, instrument_type,
                                 days=span, end=end)
        if status == "error":
            notes.append(f"{end}: {note}")
            break
        if status == "empty":
            empties += 1
            if empties >= 2:
                break
        else:
            empties = 0
            frames.append(df)
        end -= datetime.timedelta(days=span + 1)

    if not frames:
        return pd.DataFrame(), ("error" if notes else "empty"), "; ".join(notes)[:200]
    out = (pd.concat(frames, ignore_index=True)
             .sort_values("ts").drop_duplicates(subset="ts").reset_index(drop=True))
    return out, "ok", "; ".join(notes)[:200]


# --------------------------------------------------------------------------
# storage
# --------------------------------------------------------------------------
def _write(df: pd.DataFrame, path: str) -> int:
    """Write/merge a frame to parquet. Returns total bars on disk afterwards.

    Merges rather than overwrites: a contract fetched twice on different days
    keeps the union, so a run that happens after a purge boundary cannot shrink
    what an earlier run already saved.
    """
    os.makedirs(os.path.dirname(path), exist_ok=True)
    if os.path.exists(path):
        try:
            old = pd.read_parquet(path)
            df = (pd.concat([old, df], ignore_index=True)
                    .sort_values("ts").drop_duplicates(subset="ts").reset_index(drop=True))
        except Exception:
            pass  # unreadable prior file: prefer fresh data over failing the run
    df.to_parquet(path, index=False)
    return len(df)


def _manifest() -> Dict:
    if os.path.exists(MANIFEST_PATH):
        try:
            with open(MANIFEST_PATH, encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return {"contracts": {}, "runs": []}


def _save_manifest(m: Dict) -> None:
    os.makedirs(ARCHIVE_DIR, exist_ok=True)
    with open(MANIFEST_PATH, "w", encoding="utf-8") as f:
        json.dump(m, f, indent=2, default=str)


def _quality(df: pd.DataFrame) -> Dict:
    """Facts about a frame that a later reader would want flagged."""
    if df.empty:
        return {"bars": 0}
    t = df["ts"].dt.time
    outside = int(((t < SESSION_START) | (t > SESSION_END)).sum())
    days = df["ts"].dt.date.nunique()
    return {
        "bars": len(df),
        "first_ts": str(df["ts"].iloc[0]),
        "last_ts": str(df["ts"].iloc[-1]),
        "days": days,
        "bars_per_day": round(len(df) / days, 1) if days else 0,
        "bars_outside_session": outside,
        "zero_volume_bars": int((df["volume"] == 0).sum()),
    }


# --------------------------------------------------------------------------
# archive targets
# --------------------------------------------------------------------------
def archive_index(dhan, ticker: str = "NIFTY", security_id: str = "13",
                  days: int = 365) -> Dict:
    """The index archive is the deep one — 5+ years are available, so this pages."""
    path = os.path.join(ARCHIVE_DIR, ticker, "index.parquet")
    df, status, note = fetch_paged(dhan, security_id, "IDX_I", "INDEX", days=days)
    rec = {"kind": "index", "ticker": ticker, "security_id": security_id,
           "status": status, "note": note, "path": os.path.relpath(path, ARCHIVE_DIR),
           "fetched_at": datetime.datetime.now().isoformat(), **_quality(df)}
    if status == "ok":
        rec["bars_on_disk"] = _write(df, path)
    print(f"  index {ticker}: {status} {rec.get('bars', 0)} bars {note}")
    return rec


def _live_futures(underlying: str) -> List[Tuple[str, str, datetime.date]]:
    """(security_id, trading_symbol, expiry) for live futures on `underlying`."""
    import csv as _csv
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    path = os.path.join(root, "api-scrip-master.csv")
    out = []
    today = datetime.date.today()
    with open(path, encoding="utf-8", newline="") as f:
        rd = _csv.reader(f)
        next(rd, None)
        for row in rd:
            if len(row) <= 10 or row[3].strip().upper() != "FUTIDX":
                continue
            sym = row[5].strip()
            if sym.split("-")[0].strip().upper() != underlying:
                continue
            try:
                exp = datetime.datetime.strptime(row[8][:10], "%Y-%m-%d").date()
            except ValueError:
                continue
            if exp >= today:
                out.append((row[2].strip(), sym, exp))
    return sorted(out, key=lambda r: r[2])


def archive_futures(dhan, underlying: str = "NIFTY") -> List[Dict]:
    recs = []
    for sid, sym, exp in _live_futures(underlying):
        path = os.path.join(ARCHIVE_DIR, underlying, "fut", f"{sym}.parquet")
        df, status, note = fetch(dhan, sid, "NSE_FNO", "FUTIDX")
        rec = {"kind": "future", "ticker": underlying, "security_id": sid,
               "trading_symbol": sym, "expiry": str(exp), "status": status,
               "note": note, "path": os.path.relpath(path, ARCHIVE_DIR),
               "fetched_at": datetime.datetime.now().isoformat(), **_quality(df)}
        if status == "ok":
            rec["bars_on_disk"] = _write(df, path)
        recs.append(rec)
        print(f"  fut {sym}: {status} {rec.get('bars', 0)} bars {note}")
    return recs


def _spot(dhan, security_id: str = "13") -> Optional[float]:
    try:
        q = dhan.ohlc_data(securities={"IDX_I": [int(security_id)]})
        if q.get("status") == "success":
            return float(q["data"]["data"]["IDX_I"][str(security_id)]["last_price"])
    except Exception:  # noqa: BLE001
        pass
    return None


def archive_option_board(dhan, underlying: str = "NIFTY", band_pct: float = 15.0,
                         n_expiries: int = 4, spot: Optional[float] = None,
                         skip_captured: bool = True) -> List[Dict]:
    """Archive every option within `band_pct` of spot for the nearest expiries.

    `skip_captured` skips contracts already on disk whose expiry has passed —
    those can never gain new bars, and re-asking only burns the rate limit.
    """
    from backend.app.core import scrip_master

    scrip_master._load()
    board = scrip_master._options.get(underlying, {})
    today = datetime.date.today()
    expiries = sorted(e for e in board if e >= today)[:n_expiries]
    if spot is None:
        spot = _spot(dhan) or 24350.0

    lo, hi = spot * (1 - band_pct / 100.0), spot * (1 + band_pct / 100.0)
    print(f"\noption board: spot={spot:.1f} band=±{band_pct}% ({lo:.0f}..{hi:.0f}) "
          f"expiries={[str(e) for e in expiries]}")

    manifest = _manifest()
    recs: List[Dict] = []
    for exp in expiries:
        contracts = board[exp]
        targets = []
        for (strike_key, opt_type), (sid, lot, sym, exch) in contracts.items():
            try:
                strike = float(strike_key)
            except ValueError:
                continue
            if lo <= strike <= hi:
                targets.append((strike, opt_type, sid, sym, exch))
        targets.sort()
        print(f"\n  {exp}: {len(targets)} contracts in band")

        done = 0
        for strike, opt_type, sid, sym, exch in targets:
            path = os.path.join(ARCHIVE_DIR, underlying, "opt", str(exp), f"{sym}.parquet")
            prior = manifest["contracts"].get(sid)
            if (skip_captured and prior and prior.get("status") == "ok"
                    and os.path.exists(os.path.join(ARCHIVE_DIR, prior["path"]))
                    and exp < today):
                continue

            segment = "BSE_FNO" if exch == "BSE" else "NSE_FNO"
            df, status, note = fetch(dhan, sid, segment, "OPTIDX")
            rec = {"kind": "option", "ticker": underlying, "security_id": sid,
                   "trading_symbol": sym, "expiry": str(exp), "strike": strike,
                   "opt_type": opt_type, "status": status, "note": note,
                   "path": os.path.relpath(path, ARCHIVE_DIR),
                   "fetched_at": datetime.datetime.now().isoformat(), **_quality(df)}
            if status == "ok":
                rec["bars_on_disk"] = _write(df, path)
            recs.append(rec)
            manifest["contracts"][sid] = rec
            done += 1
            if done % 25 == 0:
                ok = sum(1 for r in recs if r["status"] == "ok")
                print(f"    {done}/{len(targets)}  ({ok} with data)")
                _save_manifest(manifest)  # checkpoint: a crash keeps the progress

        _save_manifest(manifest)
    return recs


# --------------------------------------------------------------------------
# reporting
# --------------------------------------------------------------------------
def report() -> None:
    m = _manifest()
    contracts = list(m.get("contracts", {}).values())
    if not contracts:
        print("archive is empty — nothing captured yet")
        return

    by_status: Dict[str, int] = {}
    for c in contracts:
        by_status[c.get("status", "?")] = by_status.get(c.get("status", "?"), 0) + 1

    total_bars = sum(c.get("bars", 0) for c in contracts)
    print("=" * 74)
    print("INTRADAY ARCHIVE — quality report")
    print("=" * 74)
    print(f"contracts recorded : {len(contracts)}")
    for s, n in sorted(by_status.items()):
        print(f"  {s:<8} {n}")
    print(f"total bars         : {total_bars:,}")

    ok = [c for c in contracts if c.get("status") == "ok"]
    if ok:
        odd_session = [c for c in ok if c.get("bars_outside_session", 0) > 0]
        thin = [c for c in ok if c.get("bars_per_day", 0) < 100]
        novol = [c for c in ok if c.get("kind") == "option"
                 and c.get("bars", 0) and c.get("zero_volume_bars", 0) == c.get("bars", 0)]
        print(f"\ncontracts with bars outside 09:15-15:30 : {len(odd_session)}")
        for c in odd_session[:5]:
            print(f"   {c.get('trading_symbol', c.get('kind'))}: "
                  f"{c['bars_outside_session']} bars, {c['first_ts']} .. {c['last_ts']}")
        print(f"contracts under 100 bars/day (thin)     : {len(thin)}")
        print(f"option contracts with ZERO volume       : {len(novol)}")
        print("   ^ these have printed bars but nothing traded; treat as untradeable,")
        print("     the same way `traded` gates fills in the bhavcopy path.")

    by_exp: Dict[str, List[Dict]] = {}
    for c in ok:
        if c.get("kind") == "option":
            by_exp.setdefault(c.get("expiry", "?"), []).append(c)
    if by_exp:
        print("\nper expiry:")
        for e in sorted(by_exp):
            cs = by_exp[e]
            print(f"  {e}: {len(cs):>4} contracts, {sum(x['bars'] for x in cs):>9,} bars")


def main() -> None:
    ap = argparse.ArgumentParser(description="1-min intraday archive")
    ap.add_argument("--index", action="store_true", help="archive the index series")
    ap.add_argument("--futures", action="store_true", help="archive live futures")
    ap.add_argument("--board", action="store_true", help="archive the option board")
    ap.add_argument("--all", action="store_true", help="index + futures + board")
    ap.add_argument("--report", action="store_true", help="quality report only")
    ap.add_argument("--band", type=float, default=15.0, help="strike band %% around spot")
    ap.add_argument("--expiries", type=int, default=4, help="how many expiries")
    ap.add_argument("--days", type=int, default=365,
                    help="index lookback days (paged in 89-day windows)")
    ap.add_argument("--ticker", default="NIFTY")
    args = ap.parse_args()

    if args.report:
        report()
        return

    if not any([args.index, args.futures, args.board, args.all]):
        ap.error("choose at least one of --index / --futures / --board / --all / --report")

    dhan = _client()
    manifest = _manifest()
    started = datetime.datetime.now()
    recs: List[Dict] = []

    if args.index or args.all:
        print("\n=== INDEX ===")
        r = archive_index(dhan, args.ticker, days=args.days)
        recs.append(r)
        manifest["contracts"][f"index:{args.ticker}"] = r

    if args.futures or args.all:
        print("\n=== FUTURES ===")
        for r in archive_futures(dhan, args.ticker):
            recs.append(r)
            manifest["contracts"][r["security_id"]] = r

    _save_manifest(manifest)

    if args.board or args.all:
        print("\n=== OPTION BOARD ===")
        recs += archive_option_board(dhan, args.ticker, band_pct=args.band,
                                     n_expiries=args.expiries)
        manifest = _manifest()

    manifest.setdefault("runs", []).append({
        "at": started.isoformat(),
        "finished": datetime.datetime.now().isoformat(),
        "args": vars(args),
        "contracts_touched": len(recs),
        "ok": sum(1 for r in recs if r.get("status") == "ok"),
        "empty": sum(1 for r in recs if r.get("status") == "empty"),
        "error": sum(1 for r in recs if r.get("status") == "error"),
    })
    _save_manifest(manifest)

    print(f"\ndone in {(datetime.datetime.now() - started).total_seconds() / 60:.1f} min")
    report()


if __name__ == "__main__":
    main()
