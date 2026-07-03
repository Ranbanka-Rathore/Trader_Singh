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
    resolve_option_contract(ticker, strike, opt_type, expiry=None) -> dict | None
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
_COL_EXCH_ID = 0      # SEM_EXM_EXCH_ID      (NSE/BSE/MCX)
_COL_SECURITY_ID = 2  # SEM_SMST_SECURITY_ID
_COL_INSTRUMENT = 3   # SEM_INSTRUMENT_NAME  (FUTIDX/FUTSTK/OPTIDX/OPTSTK...)
_COL_TRADING_SYMBOL = 5   # SEM_TRADING_SYMBOL   ("NIFTY-Aug2026-FUT")
_COL_LOT_UNITS = 6   # SEM_LOT_UNITS
_COL_EXPIRY_DATE = 8   # SEM_EXPIRY_DATE      ("2026-07-07 14:30:00")
_COL_STRIKE = 9       # SEM_STRIKE_PRICE     ("29300.00000")
_COL_OPTION_TYPE = 10  # SEM_OPTION_TYPE     (CE/PE)

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
# {underlying: {expiry_date: {(strike_key, 'CE'|'PE'): (security_id, lot, trading_symbol, exchange)}}}
_options: Dict[str, Dict[date, Dict[tuple, tuple]]] = {}
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
                    if not sym:
                        continue
                    try:
                        lot = int(float(row[_COL_LOT_UNITS]))
                    except (ValueError, TypeError):
                        lot = 0
                    # lot size can also be sourced from option rows as a backup
                    if lot > 0 and sym not in _lot_sizes:
                        _lot_sizes[sym] = lot

                    raw = (row[_COL_EXPIRY_DATE] or "").strip()
                    d = None
                    if raw:
                        try:
                            d = datetime.strptime(raw[:10], "%Y-%m-%d").date()
                            expiry_sets.setdefault(sym, set()).add(d)
                        except ValueError:
                            pass

                    # Full contract index for order routing:
                    # (strike, CE/PE) -> security id + lot + symbol + exchange
                    opt_type = (row[_COL_OPTION_TYPE] or "").strip().upper()
                    if d is not None and opt_type in ("CE", "PE") and len(row) > _COL_STRIKE:
                        try:
                            strike_key = f"{float(row[_COL_STRIKE]):.2f}"
                        except (ValueError, TypeError):
                            continue
                        _options.setdefault(sym, {}).setdefault(d, {})[(strike_key, opt_type)] = (
                            row[_COL_SECURITY_ID].strip(),
                            lot,
                            row[_COL_TRADING_SYMBOL].strip(),
                            row[_COL_EXCH_ID].strip().upper(),
                        )

        for sym, dates in expiry_sets.items():
            _expiries[sym] = sorted(dates)

        n_contracts = sum(len(k) for by_exp in _options.values() for k in by_exp.values())
        logger.info(
            f"✅ Scrip master loaded: {len(_lot_sizes)} lot sizes, "
            f"{len(_expiries)} symbols with expiries, {n_contracts} option contracts "
            f"(from {os.path.basename(path)})"
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


def resolve_option_contract(
    ticker: str,
    strike: float,
    opt_type: str,
    expiry: Optional[date] = None,
) -> Optional[Dict]:
    """Resolve an option leg to its tradeable contract.

    opt_type accepts 'CE'/'PE'/'ce'/'pe'/'c'/'p'/'call'/'put'.
    expiry=None uses the nearest expiry on/after today for the underlying.
    Returns {security_id, trading_symbol, lot_size, expiry, exchange_segment}
    (exchange_segment is the Dhan API value: NSE_FNO / BSE_FNO), or None if the
    exact contract does not exist in the scrip master — callers must treat None
    as "do not trade this leg", never guess an id.
    """
    _load()
    sym = _normalise(ticker)
    by_expiry = _options.get(sym)
    if not by_expiry:
        return None

    if expiry is None:
        expiry = get_nearest_expiry(sym)
    if expiry is None or expiry not in by_expiry:
        return None

    ot = str(opt_type).strip().upper()
    ot = {"C": "CE", "CALL": "CE", "P": "PE", "PUT": "PE"}.get(ot, ot)
    if ot not in ("CE", "PE"):
        return None

    try:
        strike_key = f"{float(strike):.2f}"
    except (ValueError, TypeError):
        return None

    hit = by_expiry[expiry].get((strike_key, ot))
    if hit is None:
        return None
    security_id, lot, trading_symbol, exchange = hit
    return {
        "security_id": security_id,
        "trading_symbol": trading_symbol,
        "lot_size": lot,
        "expiry": expiry,
        "exchange_segment": "BSE_FNO" if exchange == "BSE" else "NSE_FNO",
    }


def get_option_expiries(ticker: str) -> List[date]:
    """All known option expiries for a ticker, sorted ascending."""
    _load()
    return list(_expiries.get(_normalise(ticker), []))
