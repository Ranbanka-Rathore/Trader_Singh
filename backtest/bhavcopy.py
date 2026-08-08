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
  TtlNbOfTxsExctd   number of trades executed  <-- liquidity, see liquidity_gate
  NewBrdLotQty      market lot

NOTE ON `close` VS `traded`: bhavcopy carries NO bid/ask, so the live book test
(`spread_is_tradeable`) cannot be reproduced here. What it does carry is whether
a contract traded at all: ClsPric == 0 means no trade near the close, and the
settlement price substituted in its place is an exchange-computed number, not a
price anyone dealt at. `close` keeps that substitution because OI/PCR analytics
need a value for every strike; `traded` records whether the price was real. Fills
must be gated on `traded`, never on `close` alone — see backtest/liquidity_gate.py.
"""
import csv
import datetime
import io
import os
import time
import zipfile
from collections import Counter
from typing import Any, Dict, List, Optional

import pandas as pd
import requests

DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                        "data", "bhavcopy")

URL_TMPL = ("https://nsearchives.nseindia.com/content/fo/"
            "BhavCopy_NSE_FO_0_0_0_{ymd}_F_0000.csv.zip")

# The UDiFF feed above only reaches back to 2024-01. Everything earlier is served
# by the legacy archive in a completely different schema (verified 2026-08-07:
# UDiFF 404s for every probe from 2016 through 2023-02, legacy returns a valid
# zip for all of them). `load_chain` normalises the two into one shape.
LEGACY_URL_TMPL = ("https://nsearchives.nseindia.com/content/historical/"
                   "DERIVATIVES/{Y}/{MON}/fo{D}{MON}{Y}bhav.csv.zip")
# First UDiFF date; at or after this we use the modern feed, before it the legacy.
UDIFF_FROM = datetime.date(2024, 1, 1)

HEADERS = {
    "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                   "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"),
    "Referer": "https://www.nseindia.com/",
    "Accept": "*/*",
    "Accept-Language": "en-US,en;q=0.9",
}

_USECOLS = ["TckrSymb", "FinInstrmTp", "XpryDt", "StrkPric", "OptnTp",
            "OpnPric", "HghPric", "LwPric",
            "ClsPric", "SttlmPric", "UndrlygPric", "OpnIntrst",
            "ChngInOpnIntrst", "TtlTradgVol", "TtlNbOfTxsExctd",
            "TtlTrfVal", "NewBrdLotQty"]


def _ymd(d: datetime.date) -> str:
    return d.strftime("%Y%m%d")


INDEX_CLOSE_URL = ("https://nsearchives.nseindia.com/content/indices/"
                   "ind_close_all_{dmy}.csv")

# Underlying symbol -> the name it carries in NSE's daily index close file. Used
# only for legacy years, where the F&O bhavcopy has no UndrlygPric column.
INDEX_NAMES = {
    "NIFTY": "nifty 50",
    "BANKNIFTY": "nifty bank",
    "FINNIFTY": "nifty financial services",
    "MIDCPNIFTY": "nifty midcap select",
}


def is_udiff(d: datetime.date) -> bool:
    """True when this date is served by the modern UDiFF feed."""
    return d >= UDIFF_FROM


def _opt_float(v) -> Optional[float]:
    """float(v), or None for absent/NaN — never a silent 0.

    Legacy years genuinely lack some columns. Returning None keeps 'we do not
    know' distinct from 'it was zero', so a consumer cannot reject a contract for
    a field its era never carried.
    """
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return None if f != f else f  # NaN


def _index_close_path(d: datetime.date) -> str:
    return os.path.join(DATA_DIR, f"ind_close_{_ymd(d)}.csv")


def download_index_close(d: datetime.date, timeout: int = 30) -> Optional[str]:
    """Cache NSE's daily all-index close file. None on a holiday/404."""
    os.makedirs(DATA_DIR, exist_ok=True)
    cp = _index_close_path(d)
    if os.path.exists(cp):
        return cp if os.path.getsize(cp) > 0 else None
    if os.path.exists(_holiday_marker(d)):
        return None
    url = INDEX_CLOSE_URL.format(dmy=d.strftime("%d%m%Y"))
    resp = requests.get(url, headers=HEADERS, timeout=timeout)
    if resp.status_code == 404:
        return None
    resp.raise_for_status()
    with open(cp, "wb") as f:
        f.write(resp.content)
    return cp


def index_close(d: datetime.date, underlying: str) -> Optional[float]:
    """Closing level of the index underlying `underlying` on `d`, or None.

    This is the authoritative spot for legacy years. The alternative — using the
    near futures close — carries basis error that varies with time to expiry and
    would contaminate every moneyness and delta calculation downstream.
    """
    name = INDEX_NAMES.get(underlying.upper())
    if not name:
        return None
    try:
        cp = download_index_close(d)
        if not cp:
            return None
        with open(cp, "r", encoding="utf-8", errors="ignore") as f:
            rdr = csv.DictReader(f)
            for row in rdr:
                if (row.get("Index Name") or "").strip().lower() == name:
                    return _opt_float(row.get("Closing Index Value"))
    except Exception:
        return None
    return None


CM_URL_TMPL = ("https://nsearchives.nseindia.com/content/cm/"
               "BhavCopy_NSE_CM_0_0_0_{ymd}_F_0000.csv.zip")
LEGACY_CM_URL_TMPL = ("https://nsearchives.nseindia.com/content/historical/"
                      "EQUITIES/{Y}/{MON}/cm{D}{MON}{Y}bhav.csv.zip")

# Small LRU of parsed cash closes. Research walks dates in order, so a handful of
# entries is enough to avoid re-parsing without holding the decade in memory.
_CM_CACHE: Dict[datetime.date, Dict[str, float]] = {}
_CM_CACHE_MAX = 8


def _cm_zip_path(d: datetime.date) -> str:
    return os.path.join(DATA_DIR, f"BhavCopy_NSE_CM_{_ymd(d)}.csv.zip")


def download_cm_bhavcopy(d: datetime.date, timeout: int = 30) -> Optional[str]:
    """Cache the cash-market bhavcopy for `d`. None on a holiday/404."""
    os.makedirs(DATA_DIR, exist_ok=True)
    zp = _cm_zip_path(d)
    if os.path.exists(zp):
        return zp if os.path.getsize(zp) > 0 else None
    url = (CM_URL_TMPL.format(ymd=_ymd(d)) if is_udiff(d)
           else LEGACY_CM_URL_TMPL.format(Y=d.strftime("%Y"),
                                          MON=d.strftime("%b").upper(),
                                          D=d.strftime("%d")))
    resp = requests.get(url, headers=HEADERS, timeout=timeout)
    if resp.status_code == 404:
        return None
    resp.raise_for_status()
    if resp.content[:2] != b"PK":
        return None
    with open(zp, "wb") as f:
        f.write(resp.content)
    return zp


def _cm_closes(d: datetime.date) -> Dict[str, float]:
    """{symbol: close} for the EQ series on `d`, from the cash bhavcopy."""
    if d in _CM_CACHE:
        return _CM_CACHE[d]
    out: Dict[str, float] = {}
    try:
        zp = download_cm_bhavcopy(d)
        if zp:
            with zipfile.ZipFile(zp) as z:
                name = next(n for n in z.namelist() if n.lower().endswith(".csv"))
                with z.open(name) as f:
                    for row in csv.DictReader(io.TextIOWrapper(f, encoding="utf-8")):
                        row = {(k or "").strip(): v for k, v in row.items()}
                        sym = row.get("SYMBOL") or row.get("TckrSymb")
                        ser = (row.get("SERIES") or row.get("SctySrs") or "").strip()
                        if not sym or ser != "EQ":
                            continue
                        c = _opt_float(row.get("CLOSE") or row.get("ClsPric"))
                        if c and c > 0:
                            out[sym.strip().upper()] = c
    except Exception:
        pass
    if len(_CM_CACHE) >= _CM_CACHE_MAX:
        _CM_CACHE.pop(next(iter(_CM_CACHE)))
    _CM_CACHE[d] = out
    return out


def cash_close(d: datetime.date, symbol: str) -> Optional[float]:
    """Cash-market close for a stock underlying, or None.

    The authoritative spot for stock options in legacy years, where the F&O file
    carries no UndrlygPric. The alternative is the near-futures close, whose
    basis varies with time to expiry, dividends and borrow — error that lands
    directly in every moneyness and delta. Validated on 2024-02-06, where the
    cash close for RELIANCE (2855.60) matches the F&O UndrlygPric exactly.
    """
    return _cm_closes(d).get(symbol.strip().upper())


LOT_TABLE_PATH = os.path.join(DATA_DIR, "lot_table.json")
_LOT_TABLE: Optional[Dict[str, int]] = None


def _lot_key(symbol: str, expiry: datetime.date) -> str:
    return f"{symbol.upper()}|{expiry.isoformat()}"


def lot_table() -> Dict[str, int]:
    """Cached {symbol|expiry: lot}, built by `build_lot_table`. {} if absent."""
    global _LOT_TABLE
    if _LOT_TABLE is None:
        try:
            import json
            with open(LOT_TABLE_PATH, "r", encoding="utf-8") as f:
                _LOT_TABLE = json.load(f)
        except Exception:
            _LOT_TABLE = {}
    return _LOT_TABLE


def build_lot_table(start: datetime.date, end: datetime.date,
                    progress: bool = True) -> Dict[str, int]:
    """Derive the legacy-era lot for every (symbol, expiry) and cache it.

    A single day's estimate is not accurate enough. Turnover is struck on the
    day's traded prices, so even with an OHLC-average anchor the estimate carries
    that session's drift: RELIANCE on 2016-02-16 comes out at 501.6, which rounds
    to a wrong 502. The bias changes sign with the market's direction, so pooling
    every day on which a given expiry was listed cancels it — the same contract
    aggregated over February 2016 gives 500.38, which rounds correctly.

    Aggregating by (symbol, expiry) rather than by day is the right granularity
    twice over: the lot IS a per-expiry property, and each expiry appears in
    dozens of daily files, which is what supplies the samples.

    Rounding is plain, never snapped to a 'nice' number. Real NSE lots include
    309, 367, 456, 477, 1355, 4462 and 71475 — NSE sizes contracts to a target
    rupee value, so tidying the result would corrupt genuine values.

    MEASURED ACCURACY (2026-08-07, 560 contracts over 2024-02-01..2024-03-15,
    scored against UDiFF's authoritative NewBrdLotQty and restricted to contracts
    whose lot was not revised mid-window):

        within 1% of truth   100.0%      median relative error  0.000%
        within 0.5%           99.6%      worst case             0.70%
        exact, lot <= 1000    79.3%      exact, lot > 1000      15.4%

    All four index underlyings come out EXACT (NIFTY 50, BANKNIFTY 15,
    FINNIFTY 40, MIDCPNIFTY 75), which is what matters most since they carry the
    liquidity. The low exact-match rate on large lots is a rounding artefact, not
    a derivation failure: at a lot of 80,000 a 0.07% error is 56 units. The
    residual is bounded at 1% and sits an order of magnitude below the friction
    and slippage uncertainty already in the model, so it is accepted rather than
    engineered away. Do NOT read the exact-match percentage as an error rate.
    """
    import json
    ests: Dict[str, List[float]] = {}
    d, n = start, 0
    while d <= end:
        if d.weekday() < 5 and os.path.exists(zip_path(d)):
            try:
                df = _read_df(d)
            except Exception:
                df = None
            if df is not None:
                fut = df[df["FinInstrmTp"].isin(["IDF", "STF"])]
                for row in fut.itertuples(index=False):
                    c = _opt_float(getattr(row, "TtlTradgVol", None)) or 0.0
                    v = _opt_float(getattr(row, "TtlTrfVal", None)) or 0.0
                    cl = _opt_float(getattr(row, "ClsPric", None)) or 0.0
                    o = _opt_float(getattr(row, "OpnPric", None)) or 0.0
                    h = _opt_float(getattr(row, "HghPric", None)) or 0.0
                    lo = _opt_float(getattr(row, "LwPric", None)) or 0.0
                    price = ((o + h + lo + cl) / 4.0
                             if o > 0 and h > 0 and lo > 0 else cl)
                    if c < 100 or v <= 0 or price <= 1:
                        continue
                    try:
                        exp = datetime.date.fromisoformat(str(row.XpryDt)[:10])
                    except (ValueError, TypeError):
                        continue
                    ests.setdefault(_lot_key(str(row.TckrSymb), exp), []).append(
                        v / (c * price))
                n += 1
                if progress and n % 200 == 0:
                    print(f"  ... {n} days scanned, {len(ests)} contracts")
        d += datetime.timedelta(days=1)

    table: Dict[str, int] = {}
    for k, vals in ests.items():
        vals.sort()
        lot = int(round(vals[len(vals) // 2]))
        if lot > 0:
            table[k] = lot
    os.makedirs(DATA_DIR, exist_ok=True)
    with open(LOT_TABLE_PATH, "w", encoding="utf-8") as f:
        json.dump(table, f)
    global _LOT_TABLE
    _LOT_TABLE = table
    if progress:
        print(f"lot table: {len(table)} (symbol, expiry) entries "
              f"from {n} days -> {LOT_TABLE_PATH}")
    return table


def _derive_lot(df, ticker: str):
    """(modal_lot, {expiry: lot}) for `ticker`, derived from its FUTURES rows.

    Legacy files carry no NewBrdLotQty. Futures turnover is exactly
    `contracts * lot * price`, so `value / (contracts * price)` recovers the lot.

    The price anchor is the OHLC average, NOT the close. Turnover is struck on
    the day's traded prices, so on a trending day the close sits well off the
    VWAP and the estimate is biased by that day's move: on 2016-02-16 NIFTY fell
    1.6% and the close-anchored estimate came out at 75.68/75.75/75.73, which
    rounds to a silently wrong 76. The OHLC average gives 74.91/75.00/74.94 on
    the same rows. A wrong lot rescales every P&L in the backtest, so this is
    worth the extra columns.

    Options cannot be used at all: their turnover is struck on the underlying's
    VWAP rather than the premium, scattering the estimate across 73-78.

    Deriving per date means lot-size revisions are picked up automatically rather
    than hardcoded from memory and silently wrong for a stretch of years.
    """
    try:
        fut = df[(df["TckrSymb"] == ticker)
                 & (df["FinInstrmTp"].isin(["IDF", "STF"]))]
        ests, per_expiry = [], {}
        for row in fut.itertuples(index=False):
            c = _opt_float(getattr(row, "TtlTradgVol", None)) or 0.0
            v = _opt_float(getattr(row, "TtlTrfVal", None)) or 0.0
            cl = _opt_float(getattr(row, "ClsPric", None)) or 0.0
            o = _opt_float(getattr(row, "OpnPric", None)) or 0.0
            h = _opt_float(getattr(row, "HghPric", None)) or 0.0
            lo = _opt_float(getattr(row, "LwPric", None)) or 0.0
            price = (o + h + lo + cl) / 4.0 if o > 0 and h > 0 and lo > 0 else cl
            if c >= 100 and v > 0 and price > 1:
                est = v / (c * price)
                ests.append(est)
                try:
                    per_expiry[datetime.date.fromisoformat(
                        str(row.XpryDt)[:10])] = int(round(est))
                except (ValueError, TypeError):
                    pass
        if not ests:
            return None, {}
        ests.sort()
        # median, robust to one odd row
        return int(round(ests[len(ests) // 2])), per_expiry
    except Exception:
        return None, {}


def _source_url(d: datetime.date) -> str:
    if is_udiff(d):
        return URL_TMPL.format(ymd=_ymd(d))
    return LEGACY_URL_TMPL.format(Y=d.strftime("%Y"),
                                  MON=d.strftime("%b").upper(),
                                  D=d.strftime("%d"))


def zip_path(d: datetime.date) -> str:
    """Local cache path. One naming scheme for both eras — the era is implied by
    the date, so callers never have to care which archive it came from."""
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

    url = _source_url(d)
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


# Legacy -> UDiFF column and instrument-type mapping. Fields the legacy archive
# simply does not carry (trade count, underlying price, market lot) are filled
# with a sentinel and handled explicitly downstream — never silently defaulted.
_LEGACY_COLS = {
    "SYMBOL": "TckrSymb", "EXPIRY_DT": "XpryDt", "STRIKE_PR": "StrkPric",
    "OPTION_TYP": "OptnTp", "CLOSE": "ClsPric", "SETTLE_PR": "SttlmPric",
    "CONTRACTS": "TtlTradgVol", "OPEN_INT": "OpnIntrst",
    "CHG_IN_OI": "ChngInOpnIntrst", "VAL_INLAKH": "TtlTrfVal",
    "OPEN": "OpnPric", "HIGH": "HghPric", "LOW": "LwPric",
}
_LEGACY_INSTR = {"OPTIDX": "IDO", "OPTSTK": "STO", "FUTIDX": "IDF", "FUTSTK": "STF"}
_LEGACY_USECOLS = list(_LEGACY_COLS) + ["INSTRUMENT"]


def _read_df(d: datetime.date) -> Optional[pd.DataFrame]:
    """Read a cached bhavcopy into UDiFF-shaped columns, whichever era it is from."""
    zp = zip_path(d)
    if not (os.path.exists(zp) and os.path.getsize(zp) > 0):
        return None
    with zipfile.ZipFile(zp) as z:
        name = next(n for n in z.namelist() if n.lower().endswith(".csv"))
        with z.open(name) as f:
            cols = _USECOLS if is_udiff(d) else _LEGACY_USECOLS
            df = pd.read_csv(io.TextIOWrapper(f, encoding="utf-8"),
                             usecols=lambda c: c.strip() in cols, low_memory=False)
    df.columns = [c.strip() for c in df.columns]
    if is_udiff(d):
        return df

    df = df.rename(columns=_LEGACY_COLS)
    df["FinInstrmTp"] = df["INSTRUMENT"].map(_LEGACY_INSTR)
    # UNITS: legacy VAL_INLAKH is in lakhs, UDiFF TtlTrfVal is in rupees. Both
    # land in the same normalised column, so convert here — at the boundary —
    # rather than making every consumer branch on era. Verified 2026-08-07:
    # NIFTY futures turnover / (contracts * close) gives 49.89 on 2024-02-06
    # (true lot 50) treating UDiFF as rupees, and 75.16 on 2020-02-11 (true 75)
    # treating legacy as lakhs. Getting this wrong scales derived lots by 1e5.
    if "TtlTrfVal" in df.columns:
        df["TtlTrfVal"] = pd.to_numeric(df["TtlTrfVal"], errors="coerce") * 1e5
    # 'DD-Mon-YYYY' -> ISO, so expiry parsing is era-independent
    df["XpryDt"] = pd.to_datetime(df["XpryDt"], format="%d-%b-%Y",
                                  errors="coerce").dt.strftime("%Y-%m-%d")
    # Absent in the legacy archive. NaN (not 0) so a consumer that forgets to
    # handle it produces an obvious failure rather than a quiet wrong answer.
    for missing in ("TtlNbOfTxsExctd", "UndrlygPric", "NewBrdLotQty"):
        df[missing] = float("nan")
    return df


def load_chain(d: datetime.date, underlying: str = "NIFTY") -> Optional[Dict[str, Any]]:
    """Parse the cached bhavcopy into an option chain for one underlying.

    Returns {date, underlying, spot, spot_source, lot, era, expiries: [date...],
             options: {(expiry, strike, 'CE'|'PE'):
                       {close, traded, oi, chg_oi, volume, txns, lot}},
             futures: {expiry: {close, oi}}}
    or None if no file is cached for the date. `close` is ClsPric with
    SttlmPric substituted when the close is 0 (illiquid strike) — see `traded`
    before using it as a fill price.

    `spot_source` and `era` are recorded rather than inferred so the quality
    report can audit where every number came from.
    """
    df = _read_df(d)
    if df is None:
        return None

    sym = df[df["TckrSymb"] == underlying]
    opts = sym[sym["FinInstrmTp"].isin(["IDO", "STO"])]
    futs = sym[sym["FinInstrmTp"].isin(["IDF", "STF"])]
    if opts.empty:
        return None

    # Lot: from the file when the era carries it, otherwise derived from futures
    # turnover. Never defaulted to a guess — a wrong lot silently rescales every
    # P&L in the backtest.
    #
    # It is resolved PER EXPIRY, not per day. NSE revises the lot for newly
    # listed expiries while contracts already open keep the old one, so a single
    # session legitimately carries two lots: on 2025-01-15 the 2025-01-30 expiry
    # was 25 while every other expiry was 75. Taking any one row's value made the
    # chain-level lot flap 50->25->75->25->75 across 2024-25 and would have
    # mis-scaled every trade in the odd expiry by 3x.
    derived_lot, derived_by_expiry = _derive_lot(df, underlying)
    lot_by_expiry: Dict[datetime.date, int] = dict(derived_by_expiry)
    # The pooled table beats this day's estimate wherever it has an entry —
    # see build_lot_table for why a single session is not accurate enough.
    _tbl = lot_table()
    if _tbl:
        for raw in opts["XpryDt"].dropna().unique():
            try:
                exp = datetime.date.fromisoformat(str(raw)[:10])
            except (ValueError, TypeError):
                continue
            v = _tbl.get(_lot_key(underlying, exp))
            if v:
                lot_by_expiry[exp] = v
    for row in opts.itertuples(index=False):
        v = _opt_float(getattr(row, "NewBrdLotQty", None))
        if v and v > 0:
            try:
                lot_by_expiry[datetime.date.fromisoformat(str(row.XpryDt)[:10])] = int(v)
            except (ValueError, TypeError):
                pass
    chain_lot = (Counter(lot_by_expiry.values()).most_common(1)[0][0]
                 if lot_by_expiry else derived_lot)

    options: Dict[tuple, Dict[str, float]] = {}
    spot = 0.0
    for row in opts.itertuples(index=False):
        expiry = datetime.date.fromisoformat(str(row.XpryDt)[:10])
        strike = float(row.StrkPric)
        opt_type = str(row.OptnTp).upper()
        close = float(row.ClsPric or 0.0)
        settle = float(row.SttlmPric or 0.0)
        volume = float(row.TtlTradgVol or 0.0)
        options[(expiry, strike, opt_type)] = {
            "close": close if close > 0 else settle,
            # `traded` is the honest half of `close`: False means the price above
            # is a settlement figure for a contract nobody dealt in that day.
            #
            # It is defined on VOLUME, not on a non-zero close, because the two
            # eras disagree about what a close means. The legacy archive prints a
            # close for contracts that never traded (2020-02-11 NIFTY 10150 CE:
            # CLOSE 1876.5, OPEN/HIGH/LOW 0, CONTRACTS 0), so `close > 0` would
            # wave through exactly the fills this flag exists to stop. Volume is
            # unambiguous in both formats and is what the liquidity gate measured
            # as actually predictive: requiring close>0 passed 99.2% of ladder
            # legs and changed nothing, requiring volume dropped it to 51.5% and
            # flipped the strategy's profit factor from 3.47 to 0.78.
            "traded": volume > 0,
            "oi": float(row.OpnIntrst or 0.0),
            "chg_oi": float(row.ChngInOpnIntrst or 0.0),
            "volume": volume,
            # NaN in legacy years — the gate treats an absent trade count as
            # "unknown", never as zero, so a legacy chain cannot be rejected for
            # a field its era does not carry.
            "txns": _opt_float(getattr(row, "TtlNbOfTxsExctd", None)),
            # this expiry's own lot, not the day's modal one
            "lot": lot_by_expiry.get(expiry, chain_lot),
        }
        up = _opt_float(getattr(row, "UndrlygPric", None)) or 0.0
        if up > 0:
            spot = up

    futures: Dict[datetime.date, Dict[str, float]] = {}
    for row in futs.itertuples(index=False):
        expiry = datetime.date.fromisoformat(str(row.XpryDt)[:10])
        close = float(row.ClsPric or 0.0)
        settle = float(row.SttlmPric or 0.0)
        futures[expiry] = {"close": close if close > 0 else settle,
                           "oi": float(row.OpnIntrst or 0.0)}

    # Spot. UDiFF carries it per row; legacy does not, so fall back to NSE's
    # daily index close — the actual index, not a futures proxy whose basis
    # varies with time to expiry and would distort every moneyness and delta.
    spot_source = "bhavcopy_underlying"
    if spot <= 0:
        idx = index_close(d, underlying)
        cash = None if idx else cash_close(d, underlying)
        if idx and idx > 0:
            spot, spot_source = idx, "index_close_archive"
        elif cash and cash > 0:
            spot, spot_source = cash, "cash_bhavcopy"
        elif futures:
            # stock underlyings in legacy years have neither; nearest futures is
            # a documented PROXY carrying basis error, flagged so the quality
            # report can count how much of the sample depends on it
            near = min(futures)
            spot, spot_source = float(futures[near]["close"] or 0.0), "futures_proxy"
        else:
            spot_source = "unavailable"

    return {
        "date": d,
        "underlying": underlying,
        "spot": spot,
        "spot_source": spot_source,
        "lot": chain_lot,              # modal lot, for reporting only
        "lot_by_expiry": lot_by_expiry,  # authoritative; legs carry their own
        "era": "udiff" if is_udiff(d) else "legacy",
        "expiries": sorted({k[0] for k in options}),
        "options": options,
        "futures": futures,
    }


def infer_strike_interval(chain: Dict[str, Any], expiry: datetime.date,
                          spot: float) -> Optional[float]:
    """Strike step near the money for one expiry. Stock strike grids change
    after corporate actions (RELIANCE 20->10, HDFCBANK 10->5), so this must
    be inferred per day, never hardcoded."""
    strikes = sorted({k[1] for k in chain["options"]
                      if k[0] == expiry and abs(k[1] - spot) <= spot * 0.06})
    if len(strikes) < 3:
        return None
    diffs = [round(strikes[i + 1] - strikes[i], 2)
             for i in range(len(strikes) - 1)]
    diffs = [x for x in diffs if x > 0]
    if not diffs:
        return None
    return min(diffs)  # smallest positive step = the grid near ATM


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
