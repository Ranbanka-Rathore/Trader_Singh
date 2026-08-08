"""Earnings calendar from NSE board-meeting intimations — arena 4's missing data.

WHY THIS EXISTS
---------------
Arena 4 (event-driven volatility) was the one arena with no engine, and not
because it was hard: the only event dates in this project were 47 macro dates
hand-compiled in `regime_filters.py`, covering 2024-2026, carrying their own
warning that "RBI MPC dates especially shift". Screening a strategy on 47
unverified dates inside a single liquidity era would have produced exactly the
kind of false positive Section 6 rejects.

NSE publishes every listed company's board-meeting intimation, and a meeting
called to approve financial results IS the earnings date. The archive reaches
back past 2016 and runs to thousands of events a year, which makes the
single-stock half of this arena statistically tractable in a way the macro half
never was.

THE LOOKAHEAD TRAP, AND WHY `announced_at` IS KEPT
---------------------------------------------------
Each intimation carries two timestamps: the date of the meeting (`bm_date`) and
the moment the company told the exchange it was happening (`bm_timestamp`). A
backtest that positions ahead of earnings may only use events it could have known
about, and companies typically give a week or two of notice — sometimes a day.
Filtering on the meeting date alone would let a strategy enter a trade before
anyone knew there was an event to trade, which is a subtle, invisible, and
completely fatal lookahead. Both dates are stored; `events_known_by()` is the
only sanctioned way to ask what was visible on a given day.

Meetings also get rescheduled. Where a symbol has several intimations for the
same meeting date, the EARLIEST announcement is kept, because that is when the
date first became knowable.
"""
import datetime
import json
import os
import time
from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Optional, Set

import requests

DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                        "data", "events")

API_TMPL = ("https://www.nseindia.com/api/corporate-board-meetings"
            "?index=equities&from_date={f}&to_date={t}")

HEADERS = {
    "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                   "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"),
    "Referer": "https://www.nseindia.com/",
    "Accept": "*/*",
    "Accept-Language": "en-US,en;q=0.9",
}

# A board meeting is an earnings event when it was called to approve results.
#
# THREE VOCABULARY ERAS, and matching the wrong one loses years in silence —
# exactly the trap the bhavcopy loader hit twice:
#
#   2016-2017  bm_purpose is "Results", "Results/Dividend", "Results/Others"
#   2018-2024  bm_purpose is "Financial Results", "Financial Results/Dividend",
#              "Financial results/..." — capitalisation varies too
#   2025-      bm_purpose is very often the generic "Board Meeting Intimation"
#              (18,580 rows in 2025-26) with the real subject moved into
#              bm_desc: "X has informed the Exchange about Board Meeting to be
#              held on ... to consider and approve the financial results ..."
#
# Matching "financial result" against bm_purpose alone found ZERO events before
# 2018 and dropped ~18,600 rows in 2025-26. Matching the substring "result"
# across purpose AND description gives 7k-18k events every year, rising with the
# number of listings, which is the real trend.
EARNINGS_MARKER = "result"


def _is_earnings(row: Dict[str, Any]) -> bool:
    blob = (str(row.get("bm_purpose") or "") + " "
            + str(row.get("bm_desc") or "")).lower()
    return EARNINGS_MARKER in blob

FIRST_YEAR = 2016


@dataclass(frozen=True)
class Event:
    symbol: str
    date: datetime.date            # when the meeting happens — the event
    announced_at: datetime.date    # when the market first learned of it
    purpose: str

    @property
    def notice_days(self) -> int:
        return (self.date - self.announced_at).days


def _ddmmyyyy(d: datetime.date) -> str:
    return d.strftime("%d-%m-%Y")


def year_path(year: int) -> str:
    return os.path.join(DATA_DIR, f"board_meetings_{year}.json")


def _parse_date(raw: Any) -> Optional[datetime.date]:
    """NSE prints '31-Jan-2024'; timestamps add a clock. Both land here."""
    s = str(raw or "").strip()
    if not s:
        return None
    s = s.split(" ")[0]
    for fmt in ("%d-%b-%Y", "%d-%m-%Y", "%Y-%m-%d"):
        try:
            return datetime.datetime.strptime(s, fmt).date()
        except ValueError:
            continue
    return None


def download_year(year: int, force: bool = False, timeout: int = 60) -> Optional[str]:
    """Fetch one calendar year of intimations into the cache. Returns the path."""
    os.makedirs(DATA_DIR, exist_ok=True)
    path = year_path(year)
    if not force and os.path.exists(path) and os.path.getsize(path) > 0:
        return path

    url = API_TMPL.format(f=_ddmmyyyy(datetime.date(year, 1, 1)),
                          t=_ddmmyyyy(datetime.date(year, 12, 31)))
    resp = requests.get(url, headers=HEADERS, timeout=timeout)
    resp.raise_for_status()
    try:
        rows = resp.json()
    except ValueError:
        raise RuntimeError(
            f"board meetings for {year} did not return JSON "
            f"({len(resp.content)} bytes starting {resp.content[:60]!r}) — "
            f"NSE serves an HTML error page with status 200 when unhappy")
    if not isinstance(rows, list):
        raise RuntimeError(f"board meetings for {year}: expected a list, got "
                           f"{type(rows).__name__}")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(rows, f)
    return path


def download_range(start_year: int = FIRST_YEAR,
                   end_year: Optional[int] = None,
                   pause_sec: float = 2.0) -> List[int]:
    """Fetch every year in range, pausing between fresh hits."""
    end_year = end_year or datetime.date.today().year
    done = []
    for y in range(start_year, end_year + 1):
        fresh = not os.path.exists(year_path(y))
        download_year(y)
        done.append(y)
        if fresh:
            time.sleep(pause_sec)
    return done


def _load_year(year: int) -> List[Dict[str, Any]]:
    path = year_path(year)
    if not os.path.exists(path):
        return []
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def load_events(start: datetime.date, end: datetime.date,
                symbols: Optional[Iterable[str]] = None,
                earnings_only: bool = True) -> List[Event]:
    """Every known event with a meeting date in [start, end].

    Deduplicated on (symbol, meeting date), keeping the EARLIEST announcement —
    a rescheduled meeting is re-intimated, and the first notice is when the date
    became knowable.
    """
    want: Optional[Set[str]] = ({str(s).upper() for s in symbols}
                                if symbols is not None else None)
    best: Dict[Any, Event] = {}
    for year in range(start.year, end.year + 1):
        for row in _load_year(year):
            purpose = str(row.get("bm_purpose") or "")
            if earnings_only and not _is_earnings(row):
                continue
            symbol = str(row.get("bm_symbol") or "").strip().upper()
            if not symbol or (want is not None and symbol not in want):
                continue
            when = _parse_date(row.get("bm_date"))
            told = _parse_date(row.get("bm_timestamp")) or when
            if when is None or not (start <= when <= end):
                continue
            # An intimation dated after its own meeting is a correction filed
            # late; treat the meeting date as the announcement so it can never
            # look like advance notice the market did not have.
            if told > when:
                told = when
            key = (symbol, when)
            prev = best.get(key)
            if prev is None or told < prev.announced_at:
                best[key] = Event(symbol=symbol, date=when, announced_at=told,
                                  purpose=purpose)
    return sorted(best.values(), key=lambda e: (e.date, e.symbol))


def events_known_by(events: Iterable[Event], as_of: datetime.date,
                    horizon_days: int = 30) -> List[Event]:
    """Events a trader could see on `as_of`: announced by then, not yet happened.

    The ONLY sanctioned way to ask what was visible. Using a meeting date without
    this filter lets a backtest position ahead of an event nobody had heard of.
    """
    return [e for e in events
            if e.announced_at <= as_of < e.date
            and (e.date - as_of).days <= horizon_days]


def by_symbol(events: Iterable[Event]) -> Dict[str, List[Event]]:
    out: Dict[str, List[Event]] = {}
    for e in events:
        out.setdefault(e.symbol, []).append(e)
    for v in out.values():
        v.sort(key=lambda e: e.date)
    return out


# ── quality ──────────────────────────────────────────────────────────────────
def quality_report(start: datetime.date, end: datetime.date) -> Dict[str, Any]:
    """Checks that would catch a silently broken calendar.

    A calendar is not like a price series: a missing quarter looks exactly like a
    quarter in which nothing happened, and a strategy would simply not trade
    then rather than erroring. So coverage is asserted per quarter, and the
    notice-period distribution is reported because a calendar whose announcements
    all landed on the meeting date would mean `bm_timestamp` had stopped being
    populated — which would silently disable the lookahead guard.
    """
    events = load_events(start, end)
    per_quarter: Dict[str, int] = {}
    per_year: Dict[int, int] = {}
    for e in events:
        per_quarter[f"{e.date.year}Q{(e.date.month - 1) // 3 + 1}"] = \
            per_quarter.get(f"{e.date.year}Q{(e.date.month - 1) // 3 + 1}", 0) + 1
        per_year[e.date.year] = per_year.get(e.date.year, 0) + 1

    notice = [e.notice_days for e in events]
    same_day = sum(1 for n in notice if n <= 0)
    quarters = []
    y, q = start.year, (start.month - 1) // 3 + 1
    while (y, q) <= (end.year, (end.month - 1) // 3 + 1):
        quarters.append(f"{y}Q{q}")
        q += 1
        if q > 4:
            q, y = 1, y + 1
    empty = [k for k in quarters if per_quarter.get(k, 0) == 0]

    hard: List[str] = []
    soft: List[str] = []
    if empty:
        hard.append(f"{len(empty)} quarter(s) with no earnings events at all: "
                    f"{empty[:6]} — the calendar has a hole")
    if events and same_day / len(events) > 0.5:
        hard.append(f"{same_day}/{len(events)} events carry no advance notice; "
                    f"bm_timestamp is probably not being read, which would "
                    f"disable the lookahead guard")
    thin = [k for k, v in per_quarter.items() if 0 < v < 100]
    if thin:
        soft.append(f"{len(thin)} quarter(s) under 100 events: {sorted(thin)[:6]}")

    notice_sorted = sorted(notice)
    def pct(p):
        return notice_sorted[min(int(p * len(notice_sorted)), len(notice_sorted) - 1)] \
            if notice_sorted else 0

    return {
        "window": [start.isoformat(), end.isoformat()],
        "n_events": len(events),
        "n_symbols": len({e.symbol for e in events}),
        "per_year": dict(sorted(per_year.items())),
        "empty_quarters": empty,
        "notice_days": {"p05": pct(0.05), "p50": pct(0.50), "p95": pct(0.95),
                        "same_day_or_late": same_day},
        "hard_faults": hard,
        "soft_faults": soft,
        "ok": not hard,
    }
