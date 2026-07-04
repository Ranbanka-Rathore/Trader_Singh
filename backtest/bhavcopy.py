"""
Phase 3 — NSE F&O bhavcopy (UDiFF) download, cache and parse.

The bhavcopy is NSE's official end-of-day file with settlement-grade closes for
every F&O contract. It is the only honest EOD data source for a backtest:
unlike the synthetic simulator, these are prices at which the market actually
settled.

URL pattern (confirmed live 2026-07-03):
  https://nsearchives.nseindia.com/content/fo/BhavCopy_NSE_FO_0_0_0_YYYYMMDD_F_0000.csv.zip
Requires a browser User-Agent and a Referer of https://www.nseindia.com/ or the
archive host returns 403. A 404 means holiday/no file for that date.

UDiFF columns used:
  TckrSymb          underlying symbol (NIFTY, BANKNIFTY, ...)
  FinInstrmTp       IDO=index option, STO=stock option, IDF=index fut, STF=stock fut
  XpryDt            expiry date (YYYY-MM-DD)
  StrkPric          strike
  OptnTp            CE / PE
  ClsPric           close price (0 if no trade near close)
  SttlmPric         settlement price (authoritative when ClsPric == 0)
  UndrlygPric       underlying spot at close  <-- spot source, no separate feed
  OpnIntrst         open interest
  ChngInOpnIntrst   change in OI
  TtlTradgVol       traded volume (contracts)
  NewBrdLotQty      market lot
"""
import datetime
import io
import os
import time
import zipfile
from typing import Any, Dict, List, Optional

import pandas as pd
import requests

DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                        "data", "bhavcopy")

URL_TMPL = ("https://nsearchives.nseindia.com/content/fo/"
            "BhavCopy_NSE_FO_0_0_0_{ymd}_F_0000.csv.zip")

HEADERS = {
    "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                   "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"),
    "Referer": "https://www.nseindia.com/",
    "Accept": "*/*",
    "Accept-Language": "en-US,en;q=0.9",
}

_USECOLS = ["TckrSymb", "FinInstrmTp", "XpryDt", "StrkPric", "OptnTp",
            "ClsPric", "SttlmPric", "UndrlygPric", "OpnIntrst",
            "ChngInOpnIntrst", "TtlTradgVol", "NewBrdLotQty"]


def _ymd(d: datetime.date) -> str:
    return d.strftime("%Y%m%d")


def zip_path(d: datetime.date) -> str:
    return os.path.join(DATA_DIR, f"BhavCopy_NSE_FO_{_ymd(d)}.csv.zip")


def _holiday_marker(d: datetime.date) -> str:
    return os.path.join(DATA_DIR, f"{_ymd(d)}.holiday")


def download(d: datetime.date, force: bool = False, timeout: int = 30) -> Optional[str]:
    """Download (or return cached) bhavcopy zip for a date.

    Returns the local zip path, or None for holidays/weekends (a 404 is cached
    as a .holiday marker so we never re-hit NSE for the same non-trading day).
    """
    os.makedirs(DATA_DIR, exist_ok=True)
    if d.weekday() >= 5:  # Sat/Sun — no session, don't even hit the archive
        return None

    zp = zip_path(d)
    if not force:
        if os.path.exists(zp) and os.path.getsize(zp) > 0:
            return zp
        if os.path.exists(_holiday_marker(d)):
            return None

    url = URL_TMPL.format(ymd=_ymd(d))
    resp = requests.get(url, headers=HEADERS, timeout=timeout)
    if resp.status_code == 404:
        with open(_holiday_marker(d), "w") as f:
            f.write("404 — trading holiday or file not published\n")
        return None
    resp.raise_for_status()

    # Sanity: must be a real zip (the host serves HTML error pages with 200
    # when it is unhappy about headers).
    if not resp.content[:2] == b"PK":
        raise RuntimeError(f"Bhavcopy for {d} is not a zip "
                           f"(got {len(resp.content)} bytes starting {resp.content[:20]!r})")

    with open(zp, "wb") as f:
        f.write(resp.content)
    return zp


def download_range(start: datetime.date, end: datetime.date,
                   pause_sec: float = 1.0) -> List[datetime.date]:
    """Download every trading day in [start, end]. Returns dates with data.
    Pauses between fresh hits so the NSE archive doesn't throttle us."""
    have: List[datetime.date] = []
    d = start
    while d <= end:
        cached = os.path.exists(zip_path(d)) or os.path.exists(_holiday_marker(d))
        try:
            p = download(d)
        except Exception as e:
            print(f"  ⚠️ {d}: download failed: {e}")
            p = None
        if p:
            have.append(d)
        if not cached and d.weekday() < 5:
            time.sleep(pause_sec)
        d += datetime.timedelta(days=1)
    return have


def _read_df(d: datetime.date) -> Optional[pd.DataFrame]:
    zp = zip_path(d)
    if not (os.path.exists(zp) and os.path.getsize(zp) > 0):
        return None
    with zipfile.ZipFile(zp) as z:
        name = next(n for n in z.namelist() if n.lower().endswith(".csv"))
        with z.open(name) as f:
            return pd.read_csv(io.TextIOWrapper(f, encoding="utf-8"),
                               usecols=lambda c: c in _USECOLS, low_memory=False)


def load_chain(d: datetime.date, underlying: str = "NIFTY") -> Optional[Dict[str, Any]]:
    """Parse the cached bhavcopy into an option chain for one underlying.

    Returns {date, underlying, spot, expiries: [date...],
             options: {(expiry, strike, 'CE'|'PE'): {close, oi, chg_oi, volume, lot}},
             futures: {expiry: {close, oi}}}
    or None if no file is cached for the date. `close` is ClsPric with
    SttlmPric substituted when the close is 0 (illiquid strike).
    """
    df = _read_df(d)
    if df is None:
        return None

    sym = df[df["TckrSymb"] == underlying]
    opts = sym[sym["FinInstrmTp"].isin(["IDO", "STO"])]
    futs = sym[sym["FinInstrmTp"].isin(["IDF", "STF"])]
    if opts.empty:
        return None

    options: Dict[tuple, Dict[str, float]] = {}
    spot = 0.0
    for row in opts.itertuples(index=False):
        expiry = datetime.date.fromisoformat(str(row.XpryDt)[:10])
        strike = float(row.StrkPric)
        opt_type = str(row.OptnTp).upper()
        close = float(row.ClsPric or 0.0)
        settle = float(row.SttlmPric or 0.0)
        options[(expiry, strike, opt_type)] = {
            "close": close if close > 0 else settle,
            "oi": float(row.OpnIntrst or 0.0),
            "chg_oi": float(row.ChngInOpnIntrst or 0.0),
            "volume": float(row.TtlTradgVol or 0.0),
            "lot": int(row.NewBrdLotQty or 0),
        }
        up = float(row.UndrlygPric or 0.0)
        if up > 0:
            spot = up

    futures: Dict[datetime.date, Dict[str, float]] = {}
    for row in futs.itertuples(index=False):
        expiry = datetime.date.fromisoformat(str(row.XpryDt)[:10])
        close = float(row.ClsPric or 0.0)
        settle = float(row.SttlmPric or 0.0)
        futures[expiry] = {"close": close if close > 0 else settle,
                           "oi": float(row.OpnIntrst or 0.0)}

    return {
        "date": d,
        "underlying": underlying,
        "spot": spot,
        "expiries": sorted({k[0] for k in options}),
        "options": options,
        "futures": futures,
    }


def nearest_expiry(chain: Dict[str, Any], on_or_after: datetime.date,
                   min_days: int = 0) -> Optional[datetime.date]:
    """First expiry >= on_or_after + min_days from the chain's expiry list."""
    floor = on_or_after + datetime.timedelta(days=min_days)
    for e in chain["expiries"]:
        if e >= floor:
            return e
    return None


if __name__ == "__main__":
    # Smoke: fetch + parse the most recent session
    d = datetime.date(2026, 7, 3)
    p = download(d)
    print(f"zip: {p}")
    ch = load_chain(d, "NIFTY")
    if ch:
        print(f"NIFTY spot {ch['spot']} | {len(ch['options'])} option rows | "
              f"expiries {ch['expiries'][:4]}")
