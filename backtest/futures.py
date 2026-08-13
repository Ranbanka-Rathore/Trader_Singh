"""Daily futures panel from the F&O bhavcopy — the data layer under arenas 2 and 3.

The same archive that carries option chains carries every index and stock future:
5 index futures and 156-220 stock futures per session across the whole 2016-2026
window. Cross-sectional equities and futures trend both need the same thing from
it — a per-symbol daily price series that can be traded — so they share this
rather than each growing their own.

THE TWO THINGS THAT MAKE A FUTURES SERIES WRONG
-----------------------------------------------
1. ROLL GAPS ARE NOT RETURNS. The front contract changes every month, and the
   new contract trades at a different price. The gap between yesterday's front
   close and today's front close therefore contains a jump nobody could earn.
   Every return here is computed from two closes OF THE SAME CONTRACT, and the
   continuous series is compounded from those returns rather than pasted
   together from prices. A backtest built on raw front-month closes books a free
   profit or loss every month, and on a 10-year window that is most of the P&L.

2. LOT SIZE IS PER EXPIRY. Same trap as the option chain: NSE revises lots for
   newly listed expiries while open ones keep the old value, so a single session
   legitimately carries two lots for one symbol. Lots come from the pooled table
   in `bhavcopy.lot_table()`, falling back to the file's own NewBrdLotQty.

Liquidity is judged by the same `LiquidityGate` the option backtest uses, on the
same evidence: a contract that printed no volume is not one you filled.
"""
import datetime
import os
import sys
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, Iterable, List, Optional, Tuple

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from backtest import bhavcopy
from backtest.liquidity_gate import LiquidityGate, gate_by_name

# Index futures carry their own character (and their own lots); stock futures are
# the cross-sectional universe. Kept separate because a survey that pools NIFTY
# with 200 single stocks is measuring neither.
INDEX_TYPES = ("IDF",)
STOCK_TYPES = ("STF",)

# Roll this many calendar days before expiry. NSE futures stay liquid to the end,
# but holding into the last two sessions means exiting into settlement rather
# than into a market.
DEFAULT_ROLL_DAYS = 3


@dataclass
class Bar:
    """One contract, one session."""
    date: datetime.date
    symbol: str
    expiry: datetime.date
    open: float
    high: float
    low: float
    close: float
    volume: float
    oi: float
    txns: float
    lot: int
    traded: bool

    def as_gate_row(self) -> Dict[str, Any]:
        """Shape the LiquidityGate expects, so options and futures are judged alike."""
        return {"close": self.close, "traded": self.traded, "volume": self.volume,
                "txns": self.txns, "oi": self.oi}


_DAY_CACHE: Dict[Tuple[datetime.date, str], Dict[str, Dict[datetime.date, Bar]]] = {}


def load_day(d: datetime.date, kind: str = "stock") -> Dict[str, Dict[datetime.date, Bar]]:
    """{symbol: {expiry: Bar}} for one session. Every listed expiry, not just front.

    All expiries are kept because a correct return needs the prior close of the
    contract that is front TODAY, which on a roll day is not the contract that
    was front yesterday.
    """
    key = (d, kind)
    if key in _DAY_CACHE:
        return _DAY_CACHE[key]

    out: Dict[str, Dict[datetime.date, Bar]] = {}
    df = bhavcopy._read_df(d)
    if df is None:
        _DAY_CACHE[key] = out
        return out

    wanted = INDEX_TYPES if kind == "index" else STOCK_TYPES
    futs = df[df["FinInstrmTp"].isin(wanted)]
    table = bhavcopy.lot_table()

    for row in futs.itertuples(index=False):
        try:
            expiry = datetime.date.fromisoformat(str(row.XpryDt)[:10])
        except (ValueError, TypeError):
            continue
        symbol = str(row.TckrSymb)
        close = float(row.ClsPric or 0.0)
        settle = float(row.SttlmPric or 0.0)
        volume = float(row.TtlTradgVol or 0.0)

        lot = table.get(bhavcopy._lot_key(symbol, expiry), 0)
        if not lot:
            v = bhavcopy._opt_float(getattr(row, "NewBrdLotQty", None))
            lot = int(v) if v and v > 0 else 0

        out.setdefault(symbol, {})[expiry] = Bar(
            date=d, symbol=symbol, expiry=expiry,
            open=float(row.OpnPric or 0.0), high=float(row.HghPric or 0.0),
            low=float(row.LwPric or 0.0),
            close=close if close > 0 else settle,
            volume=volume, oi=float(row.OpnIntrst or 0.0),
            txns=float(getattr(row, "TtlNbOfTxsExctd", 0) or 0.0),
            lot=lot,
            # Same definition as the option chain, for the same reason: the
            # legacy era prints a settlement close for contracts nobody dealt in.
            traded=volume > 0,
        )
    _DAY_CACHE[key] = out
    return out


def front_expiry(expiries: Iterable[datetime.date], on: datetime.date,
                 roll_days: int = DEFAULT_ROLL_DAYS) -> Optional[datetime.date]:
    """Nearest expiry still more than `roll_days` away, else the nearest at all."""
    dated = sorted(e for e in expiries if e >= on)
    if not dated:
        return None
    for e in dated:
        if (e - on).days > roll_days:
            return e
    return dated[-1]


@dataclass
class SymbolSeries:
    """One symbol's tradeable history: front-contract bars and honest returns."""
    symbol: str
    bars: List[Bar] = field(default_factory=list)
    # ret[i] is the same-contract return into bars[i]; None on the first bar and
    # whenever the prior close of today's contract is unavailable.
    rets: List[Optional[float]] = field(default_factory=list)
    # Compounded from rets — the series signals are computed on. It is a synthetic
    # index, NOT a price: never size a position off it.
    index: List[float] = field(default_factory=list)
    rolls: int = 0

    def __len__(self) -> int:
        return len(self.bars)


@dataclass
class Panel:
    """Every symbol's series over one window, plus what was refused and why."""
    dates: List[datetime.date]
    series: Dict[str, SymbolSeries]
    gate_name: str
    checked: int = 0
    fillable: int = 0
    refusals: Dict[str, int] = field(default_factory=dict)
    rolls: int = 0
    missing_lot: int = 0

    @property
    def pass_rate(self) -> float:
        return (100.0 * self.fillable / self.checked) if self.checked else 0.0

    def universe_on(self, d: datetime.date) -> List[str]:
        """Symbols with a tradeable bar on `d`."""
        return sorted(s for s, ser in self.series.items()
                      if any(b.date == d for b in ser.bars[-3:]) or
                      any(b.date == d for b in ser.bars))


def build_panel(dates: List[datetime.date], kind: str = "stock",
                gate: str = "strict", roll_days: int = DEFAULT_ROLL_DAYS,
                symbols: Optional[Iterable[str]] = None,
                loader: Optional[Callable] = None) -> Panel:
    """Assemble front-contract series for every symbol over `dates`.

    A bar is dropped when the liquidity gate refuses it, when its lot is unknown
    (an unknown lot silently rescales every P&L, so it is never guessed), or when
    no front contract exists. The refusal counts are kept so a thin universe is
    visible in the report rather than showing up as a strategy that "does not
    trade much".
    """
    load = loader or load_day
    g = LiquidityGate(gate_by_name(gate))
    panel = Panel(dates=list(dates), series={}, gate_name=gate)
    want = {str(s).upper() for s in symbols} if symbols else None
    # last accepted (expiry -> close) per symbol, for same-contract returns
    prev_closes: Dict[str, Dict[datetime.date, float]] = {}
    prev_expiry: Dict[str, datetime.date] = {}

    for d in dates:
        day = load(d, kind)
        for symbol, by_expiry in day.items():
            if want and symbol.upper() not in want:
                continue
            exp = front_expiry(by_expiry.keys(), d, roll_days)
            if exp is None:
                continue
            bar = by_expiry[exp]

            ok, why = g.leg_ok(bar.as_gate_row())
            panel.checked += 1
            if not ok:
                panel.refusals[why] = panel.refusals.get(why, 0) + 1
                # Remember the close anyway: an untradeable session still tells us
                # where the contract was, and the NEXT tradeable bar's return has
                # to be measured from somewhere real.
                prev_closes.setdefault(symbol, {})[exp] = bar.close
                continue
            if bar.lot <= 0:
                panel.missing_lot += 1
                panel.refusals["unknown_lot"] = panel.refusals.get("unknown_lot", 0) + 1
                continue
            panel.fillable += 1

            ser = panel.series.setdefault(symbol, SymbolSeries(symbol=symbol))
            # The return uses the previous close OF THIS CONTRACT. On a roll day
            # that is the new contract's own prior close, not the old front's —
            # which is what keeps the roll gap out of the P&L.
            prior = prev_closes.get(symbol, {}).get(exp)
            ret = ((bar.close / prior) - 1.0) if (prior and prior > 0) else None
            if prev_expiry.get(symbol) not in (None, exp):
                ser.rolls += 1
                panel.rolls += 1
            ser.bars.append(bar)
            ser.rets.append(ret)
            ser.index.append((ser.index[-1] * (1.0 + ret)) if (ser.index and ret is not None)
                             else (ser.index[-1] if ser.index else 100.0))
            prev_expiry[symbol] = exp
            prev_closes.setdefault(symbol, {})[exp] = bar.close

        # Every listed contract's close is worth remembering, not only the front's:
        # tomorrow's front is often a contract that was never front before, and its
        # first return has to come from its own prior close.
        for symbol, by_expiry in day.items():
            if want and symbol.upper() not in want:
                continue
            for exp, bar in by_expiry.items():
                prev_closes.setdefault(symbol, {})[exp] = bar.close

    panel.refusals = dict(sorted(panel.refusals.items(), key=lambda kv: -kv[1]))
    return panel


def trading_dates(start: datetime.date, end: datetime.date) -> List[datetime.date]:
    """Weekdays with a cached bhavcopy, so a hole in the archive is visible."""
    out, d = [], start
    while d <= end:
        if d.weekday() < 5 and os.path.exists(bhavcopy.zip_path(d)):
            out.append(d)
        d += datetime.timedelta(days=1)
    return out
