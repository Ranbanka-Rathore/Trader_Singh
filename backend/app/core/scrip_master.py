"""
Authoritative contract metadata loaded from the Dhan scrip master
(api-scrip-master.csv), parsed once and cached at module level.

Why this exists: lot sizes and weekly-expiry weekdays for NSE/BSE derivatives
change frequently (NIFTY, BANKNIFTY, FINNIFTY, SENSEX and single stocks were all
revised across 2024-2026). Hardcoding them silently drifts stale and corrupts
every days-to-expiry / theta / margin / delta-hedge calculation. The scrip master
we already download is the source of truth — read from it.

Public API:
    get_lot_size(ticker) -> int
    get_nearest_expiry(ticker, ref_date=None) -> datetime.date | None
    loaded() -> bool          # did the CSV parse succeed?

Both lookups normalise the ticker (strips ^ / .NS / .BO, maps NSEI->NIFTY,
NSEBANK->BANKNIFTY) and fall back to sane defaults if the CSV is unavailable.
"""
import csv
import logging
import os
from datetime import date, datetime
from typing import Dict, List, Optional

logger = logging.getLogger("ScripMaster")

_CSV_NAME = "api-scrip-master.csv"

# CSV column indices (see header of api-scrip-master.csv)
_COL_INSTRUMENT = 3   # SEM_INSTRUMENT_NAME  (FUTIDX/FUTSTK/OPTIDX/OPTSTK...)
_COL_TRADING_SYMBOL = 5   # SEM_TRADING_SYMBOL   ("NIFTY-Aug2026-FUT")
_COL_LOT_UNITS = 6   # SEM_LOT_UNITS
_COL_EXPIRY_DATE = 8   # SEM_EXPIRY_DATE      ("2026-07-07 14:30:00")

_FUT_INSTRUMENTS = {"FUTIDX", "FUTSTK", "FUTCUR", "FUTCOM"}
_OPT_INSTRUMENTS = {"OPTIDX", "OPTSTK", "OPTCUR", "OPTFUT"}

# Fallback lot sizes if the CSV can't be read. Kept as a *secondary* defence
# only — the CSV is authoritative. Values below reflect the 2026 scrip master.
_FALLBACK_LOT_SIZES: Dict[str, int] = {
    "NIFTY": 65,
    "BANKNIFTY": 30,
    "FINNIFTY": 60,
    "MIDCPNIFTY": 120,
    "SENSEX": 20,
    "BANKEX": 30,
    "RELIANCE": 500,
    "HDFCBANK": 650,
}

# Fallback weekly-expiry weekday if the CSV has no option rows for a symbol.
# 0=Mon .. 6=Sun.  NIFTY weeklies are Tuesday in 2026; others are monthly.
_FALLBACK_EXPIRY_WEEKDAY: Dict[str, int] = {
    "NIFTY": 1,      # Tuesday
    "SENSEX": 1,     # Tuesday
}

_lot_sizes: Dict[str, int] = {}
_expiries: Dict[str, List[date]] = {}
_loaded = False


def _normalise(ticker: str) -> str:
    if not ticker:
        return ""
    t = ticker.replace("^", "").replace(".NS", "").replace(".BO", "").strip().upper()
    if t == "NSEI":
        return "NIFTY"
    if t == "NSEBANK":
        return "BANKNIFTY"
    return t


def _find_csv() -> Optional[str]:
    """Locate api-scrip-master.csv from cwd or relative to this file."""
    candidates = [
        _CSV_NAME,
        os.path.join(os.getcwd(), _CSV_NAME),
    ]
    # project root is three levels up from backend/app/core/
    here = os.path.dirname(os.path.abspath(__file__))
    root = os.path.abspath(os.path.join(here, "..", "..", ".."))
    candidates.append(os.path.join(root, _CSV_NAME))
    for path in candidates:
        if path and os.path.exists(path):
            return path
    return None


def _underlying(trading_symbol: str) -> str:
    # "NIFTY-Aug2026-FUT" / "BANKNIFTY-31Jul2026-25000-CE" -> first token
    return trading_symbol.split("-", 1)[0].strip().upper()


def _load() -> None:
    global _loaded
    if _loaded:
        return
    _loaded = True  # mark attempted regardless of outcome (avoid re-scanning on failure)

    path = _find_csv()
    if not path:
        logger.warning(
            f"⚠️ {_CSV_NAME} not found — using fallback lot sizes/expiries. "
            "Lot-size accuracy is NOT guaranteed."
        )
        return

    try:
        expiry_sets: Dict[str, set] = {}
        with open(path, "r", encoding="utf-8", newline="") as f:
            reader = csv.reader(f)
            next(reader, None)  # skip header
            for row in reader:
                if len(row) <= _COL_EXPIRY_DATE:
                    continue
                instrument = row[_COL_INSTRUMENT].strip().upper()

                if instrument in _FUT_INSTRUMENTS:
                    sym = _underlying(row[_COL_TRADING_SYMBOL])
                    if sym and sym not in _lot_sizes:
                        try:
                            lot = int(float(row[_COL_LOT_UNITS]))
                            if lot > 0:
                                _lot_sizes[sym] = lot
                        except (ValueError, TypeError):
                            pass

                elif instrument in _OPT_INSTRUMENTS:
                    sym = _underlying(row[_COL_TRADING_SYMBOL])
                    # lot size can also be sourced from option rows as a backup
                    if sym and sym not in _lot_sizes:
                        try:
                            lot = int(float(row[_COL_LOT_UNITS]))
                            if lot > 0:
                                _lot_sizes[sym] = lot
                        except (ValueError, TypeError):
                            pass
                    raw = (row[_COL_EXPIRY_DATE] or "").strip()
                    if sym and raw:
                        try:
                            d = datetime.strptime(raw[:10], "%Y-%m-%d").date()
                            expiry_sets.setdefault(sym, set()).add(d)
                        except ValueError:
                            pass

        for sym, dates in expiry_sets.items():
            _expiries[sym] = sorted(dates)

        logger.info(
            f"✅ Scrip master loaded: {len(_lot_sizes)} lot sizes, "
            f"{len(_expiries)} symbols with expiries (from {os.path.basename(path)})"
        )
    except Exception as e:
        logger.error(f"❌ Failed to parse {_CSV_NAME}: {e}. Using fallbacks.")


def loaded() -> bool:
    _load()
    return bool(_lot_sizes)


def get_lot_size(ticker: str) -> int:
    """Authoritative lot size for a ticker, with fallback. Returns 1 if unknown."""
    _load()
    sym = _normalise(ticker)
    if sym in _lot_sizes:
        return _lot_sizes[sym]
    if sym in _FALLBACK_LOT_SIZES:
        return _FALLBACK_LOT_SIZES[sym]
    return 1


def get_nearest_expiry(ticker: str, ref_date: Optional[date] = None) -> Optional[date]:
    """Nearest expiry on/after ref_date (default today) for the ticker.

    Falls back to the next occurrence of the configured weekly weekday, or None
    if nothing is known (caller should then use its own default)."""
    _load()
    if ref_date is None:
        ref_date = date.today()
    sym = _normalise(ticker)

    dates = _expiries.get(sym)
    if dates:
        for d in dates:
            if d >= ref_date:
                return d

    weekday = _FALLBACK_EXPIRY_WEEKDAY.get(sym)
    if weekday is not None:
        from datetime import timedelta
        days_ahead = (weekday - ref_date.weekday()) % 7
        if days_ahead == 0:
            days_ahead = 7  # roll to next week's expiry, not today
        return ref_date + timedelta(days=days_ahead)

    return None
