"""Macro event calendar — the other half of arena 4, with its coverage enforced.

WHAT WAS WRONG WITH THE OLD ONE
--------------------------------
`regime_filters.py` carries 47 macro dates spanning 2024-2026, hand-typed, with
its own warning that "RBI MPC dates especially shift". Three problems: it covers
2.5 years of a 10.5-year archive, it is too thin for Section 3's detection rule,
and nothing could tell you which of those two facts was biting. A calendar that
is silently short does not error — the strategy simply does not trade, and that
looks exactly like a strategy with no signal.

WHAT THIS DOES INSTEAD
----------------------
Sources are separate, each carries its provenance, and each declares the window
it actually covers. `require_coverage()` refuses to serve a window a source
cannot support, so an under-covered calendar is a loud failure at registration
rather than a quiet absence of trades.

    fomc    HARVESTED from federalreserve.gov. NOT YET RESEARCH-READY — see below.
    rbi     MANUAL, 2024-2026 only, inherited from regime_filters.
    budget  MANUAL, 2024-2026 only, inherited from regime_filters.

STATUS: THE MACRO HALF IS STILL BLOCKED, AND THIS SAYS SO
---------------------------------------------------------
The FOMC harvester works and its output is auditable, but the Fed's pages are not
uniform enough to trust yet. `quality_report()` hard-faults on three of eleven
years, and the faults are real rather than cosmetic:

  * 2017, 2018 and 2023 come back two meetings short. Those are exactly the
    meetings that straddle a month boundary — "January 31-February 1",
    "October 31-November 1". The string "January" appears TWICE in the whole of
    fomchistorical2017.htm, so the meeting is not in the document to be parsed;
    it is not a regex problem, it is a source problem.
  * 2019 comes back with nine against a known eight, so the parser also
    over-matches somewhere.

The right response to a parser that is 8-for-11 is not to tune it until the
counts look right — that is fitting to a target, and the target here is a number
I already know. It is to leave the check failing and say the source is not ready.
So `quality_report()` fails, and a caller that wants to trade on it must look at
that report first. What IS delivered is the machinery: harvesting, provenance,
per-source coverage, and the cadence check that caught all of this.

RBI is worse and for a structural reason. Its press-release archive is ASP.NET
postback-driven — query parameters are ignored, the year dropdown is server
state — so there is no stable URL to harvest. The remaining options were to
simulate postbacks, giving a scraper that breaks silently, or to type ~75 meeting
dates from memory. The second is exactly the unverified input Section 6 rejects:
being wrong by one day on a policy date would manufacture an edge out of a typo.

RBI AND BUDGET ARE STILL NOT UNBLOCKED, and this module says so rather than
papering over it. RBI's press-release archive is ASP.NET postback-driven: query
parameters are ignored and the year dropdown is server-state, so there is no
stable URL to harvest. The remaining options were to simulate postbacks — a
scraper that breaks silently and would need re-verifying every time it did — or
to type ~75 meeting dates from memory. The second is exactly the unverified
input Section 6 rejects, and being wrong by one day on a policy date would
manufacture an edge out of a typo. So they stay marked `manual` with their real
coverage declared, and any hypothesis wanting them over a longer window is
refused until someone sources them properly.
"""
import datetime
import html
import json
import os
import re
import time
from typing import Any, Dict, Iterable, List, Optional, Tuple

import requests

from backtest.events import Event

DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                        "data", "events")

FOMC_CURRENT = "https://www.federalreserve.gov/monetarypolicy/fomccalendars.htm"
FOMC_HISTORICAL = "https://www.federalreserve.gov/monetarypolicy/fomchistorical{y}.htm"

HEADERS = {"User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                          "AppleWebKit/537.36 (KHTML, like Gecko) "
                          "Chrome/126.0.0.0 Safari/537.36"),
           "Accept": "*/*"}

MONTHS = {m: i for i, m in enumerate(
    ["January", "February", "March", "April", "May", "June", "July", "August",
     "September", "October", "November", "December"], start=1)}

# Meetings are announced on the closing day; a two-day meeting prices on day two.
#
# TWO PAGE FORMATS. The per-year historical pages write "January 26-27 Meeting";
# the live calendar writes "January 27-28 Statement: PDF | HTML". Requiring the
# word "Meeting" parsed the historical years and silently returned 2 meetings a
# year for every recent one — which the cadence check in quality_report caught.
# So the range itself is the anchor and the trailing word is optional.
_MONTHS_RE = ("January|February|March|April|May|June|July|August|September|"
              "October|November|December")
_RANGE_RE = re.compile(
    rf"({_MONTHS_RE})\s+(\d{{1,2}})\s*(?:-|–|/)\s*(\d{{1,2}})", re.IGNORECASE)
# Two meetings a year straddle a month boundary and the Fed writes them out in
# full: "January 31-February 1", "October 31-November 1". The same-month pattern
# cannot match those, which is precisely the two-per-year shortfall the cadence
# check flagged for 2017, 2018 and 2023.
_CROSS_MONTH_RE = re.compile(
    rf"({_MONTHS_RE})\s+\d{{1,2}}\s*(?:-|–|/)\s*({_MONTHS_RE})\s+(\d{{1,2}})",
    re.IGNORECASE)
# A single-day meeting must still say so, or "(Released February 18, 2026)" and
# every other stray date on the page would be read as a policy event.
_ONE_DAY_RE = re.compile(
    rf"({_MONTHS_RE})\s+(\d{{1,2}})\s*\*?\s*(?:Meeting|\(unscheduled\))",
    re.IGNORECASE)
# The live calendar holds every year in one document under these headers.
_YEAR_HDR_RE = re.compile(r"(\d{4})\s+FOMC\s+Meetings", re.IGNORECASE)

FOMC_PER_YEAR = 8          # the Fed's standing cadence; fewer means a parse miss


class CoverageError(Exception):
    """A source was asked for a window it does not actually cover."""


# ── FOMC: harvested ──────────────────────────────────────────────────────────
def fomc_path(year: int) -> str:
    return os.path.join(DATA_DIR, f"fomc_{year}.json")


def _strip(t: str) -> str:
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", t)))


def parse_fomc(text: str, year: int) -> List[datetime.date]:
    """Meeting-conclusion dates from one Fed calendar page.

    A two-day meeting spanning a month boundary ("January 31-February 1") is
    handled by taking the SECOND month's date when the day numbers go backwards.
    """
    flat = _year_section(_strip(text), year)
    out: List[datetime.date] = []
    seen = set()

    def add(y: int, mi: int, day: int) -> None:
        try:
            d = datetime.date(y, mi, day)
        except ValueError:
            return
        if d not in seen:
            seen.add(d)
            out.append(d)

    for m in _CROSS_MONTH_RE.finditer(flat):
        m1, m2, day = MONTHS[m.group(1).title()], MONTHS[m.group(2).title()], int(m.group(3))
        add(year + 1 if m2 < m1 else year, m2, day)
    for m in _RANGE_RE.finditer(flat):
        month, d1, d2 = m.group(1).title(), int(m.group(2)), int(m.group(3))
        mi, y = MONTHS[month], year
        if d2 < d1:                    # rolled into the next month
            mi = mi + 1 if mi < 12 else 1
            y = year if mi != 1 else year + 1
        add(y, mi, d2)
    for m in _ONE_DAY_RE.finditer(flat):
        add(year, MONTHS[m.group(1).title()], int(m.group(2)))
    return sorted(d for d in out if d.year == year)


def _year_section(flat: str, year: int) -> str:
    """The slice of a multi-year calendar belonging to `year`.

    The live page carries 2021 through 2027 in one document, so without this a
    parse for 2024 would happily collect 2026's meetings too.
    """
    heads = [(m.start(), int(m.group(1))) for m in _YEAR_HDR_RE.finditer(flat)]
    for i, (pos, y) in enumerate(heads):
        if y == year:
            end = heads[i + 1][0] if i + 1 < len(heads) else len(flat)
            return flat[pos:end]
    return flat                        # per-year historical page: whole document


def download_fomc(year: int, force: bool = False, timeout: int = 45) -> List[str]:
    """Cache one year of FOMC meeting-conclusion dates. Returns ISO strings."""
    os.makedirs(DATA_DIR, exist_ok=True)
    path = fomc_path(year)
    if not force and os.path.exists(path) and os.path.getsize(path) > 0:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)["dates"]

    # The Fed keeps the last ~5 years on the live calendar and older years on
    # per-year historical pages. Try the historical page first; it is stable.
    dates: List[datetime.date] = []
    for url in (FOMC_HISTORICAL.format(y=year), FOMC_CURRENT):
        try:
            resp = requests.get(url, headers=HEADERS, timeout=timeout)
        except requests.RequestException:
            continue
        if resp.status_code != 200:
            continue
        got = parse_fomc(resp.text, year)
        if got:
            dates = got
            source = url
            break
    else:
        raise RuntimeError(f"no FOMC meeting dates found for {year}")

    payload = {"year": year, "source": source, "provenance": "harvested",
               "fetched": datetime.datetime.now().isoformat(timespec="seconds"),
               "dates": [d.isoformat() for d in dates]}
    with open(path, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=1)
    return payload["dates"]


def download_fomc_range(start_year: int = 2016, end_year: Optional[int] = None,
                        pause_sec: float = 1.5) -> Dict[int, int]:
    end_year = end_year or datetime.date.today().year
    out = {}
    for y in range(start_year, end_year + 1):
        fresh = not os.path.exists(fomc_path(y))
        out[y] = len(download_fomc(y))
        if fresh:
            time.sleep(pause_sec)
    return out


# ── manual sources, inherited with their real coverage declared ──────────────
def _as_date(v: Any) -> Optional[datetime.date]:
    """regime_filters stores these as ISO STRINGS, not date objects.

    Filtering on isinstance(date) silently returned an empty calendar for both
    manual sources — a source that looks fully absent rather than merely short,
    which is the same class of quiet failure this module exists to prevent.
    """
    if isinstance(v, datetime.datetime):
        return v.date()
    if isinstance(v, datetime.date):
        return v
    try:
        return datetime.date.fromisoformat(str(v)[:10])
    except (ValueError, TypeError):
        return None


def _manual(source: str) -> Tuple[List[datetime.date], str]:
    from backend.app.core import regime_filters as rf
    if source == "rbi":
        raw, where = getattr(rf, "RBI_MPC_DATES", []), "regime_filters.RBI_MPC_DATES"
    elif source == "budget":
        raw, where = getattr(rf, "OTHER_EVENT_DATES", []), "regime_filters.OTHER_EVENT_DATES"
    else:
        raise KeyError(source)
    return [d for d in (_as_date(x) for x in raw) if d is not None], where


SOURCES = ("fomc", "rbi", "budget")
PROVENANCE = {"fomc": "harvested", "rbi": "manual", "budget": "manual"}


def coverage(source: str) -> Optional[Tuple[datetime.date, datetime.date]]:
    """(first, last) date this source can actually speak to, or None if empty."""
    if source == "fomc":
        years = [y for y in range(2000, 2100) if os.path.exists(fomc_path(y))]
        if not years:
            return None
        alld: List[datetime.date] = []
        for y in years:
            with open(fomc_path(y), "r", encoding="utf-8") as f:
                alld += [datetime.date.fromisoformat(s) for s in json.load(f)["dates"]]
        return (min(alld), max(alld)) if alld else None
    dates, _ = _manual(source)
    return (min(dates), max(dates)) if dates else None


def require_coverage(source: str, start: datetime.date, end: datetime.date) -> None:
    """Refuse a window the source cannot support, loudly and by how much.

    This is the whole point of the module. A calendar that is short produces no
    trades, and no trades reads as "no signal" rather than "no data".
    """
    cov = coverage(source)
    if cov is None:
        raise CoverageError(
            f"macro source '{source}' has no dates at all"
            + (" — run macro_events.download_fomc_range()" if source == "fomc" else ""))
    lo, hi = cov
    if start < lo or end > hi:
        raise CoverageError(
            f"macro source '{source}' covers {lo}..{hi}, but {start}..{end} was "
            f"requested. It is {PROVENANCE[source]}; a window it cannot support "
            f"would simply produce no trades, which is indistinguishable from a "
            f"strategy that does not work. Narrow the window or source the dates.")


def load_macro(start: datetime.date, end: datetime.date, source: str = "fomc",
               notice_days: int = 21) -> List[Event]:
    """Macro events as `Event`s, so the engine treats them like any other.

    Macro dates are scheduled far ahead and published on a calendar, so the
    announcement date is taken as `notice_days` before the event rather than
    read from a filing. That is a modelling choice, not a measurement, and it is
    deliberately conservative: 21 days is shorter than the real notice for an
    FOMC or MPC date, so it can only ever make a strategy look worse.
    """
    if source not in SOURCES:
        raise KeyError(f"unknown macro source '{source}'; choose from {SOURCES}")
    require_coverage(source, start, end)

    if source == "fomc":
        dates: List[datetime.date] = []
        for y in range(start.year, end.year + 1):
            p = fomc_path(y)
            if not os.path.exists(p):
                continue
            with open(p, "r", encoding="utf-8") as f:
                dates += [datetime.date.fromisoformat(s) for s in json.load(f)["dates"]]
    else:
        dates, _ = _manual(source)

    label = source.upper()
    return sorted(
        (Event(symbol=label, date=d,
               announced_at=d - datetime.timedelta(days=notice_days),
               purpose=f"{label} scheduled macro event")
         for d in sorted(set(dates)) if start <= d <= end),
        key=lambda e: e.date)


def quality_report(start: datetime.date, end: datetime.date) -> Dict[str, Any]:
    """Per-source coverage and cadence. Hard-faults on a source that is short."""
    per_source: Dict[str, Any] = {}
    hard: List[str] = []
    soft: List[str] = []
    years = list(range(start.year, end.year + 1))

    for src in SOURCES:
        cov = coverage(src)
        entry: Dict[str, Any] = {"provenance": PROVENANCE[src],
                                 "coverage": [str(cov[0]), str(cov[1])] if cov else None}
        if cov is None:
            entry["n_events"] = 0
            hard.append(f"{src}: no dates at all")
            per_source[src] = entry
            continue
        lo, hi = cov
        window = (max(start, lo), min(end, hi))
        evs = load_macro(window[0], window[1], src) if window[0] <= window[1] else []
        entry["n_events"] = len(evs)
        by_year: Dict[int, int] = {}
        for e in evs:
            by_year[e.date.year] = by_year.get(e.date.year, 0) + 1
        entry["per_year"] = dict(sorted(by_year.items()))
        entry["covers_requested_window"] = (lo <= start and hi >= end)
        if not entry["covers_requested_window"]:
            soft.append(f"{src}: covers {lo}..{hi}, short of {start}..{end}")
        if src == "fomc":
            # Only UNDER the cadence is a fault, and only for years the window
            # covers end to end. More than eight is legitimate — 2020 held two
            # unscheduled emergency meetings — and a partial year is arithmetic,
            # not a parse failure.
            complete = [y for y in years
                        if lo <= datetime.date(y, 1, 1) and datetime.date(y, 12, 31) <= min(hi, end)]
            bad = [y for y in complete if by_year.get(y, 0) < FOMC_PER_YEAR]
            if bad:
                hard.append(f"fomc: {len(bad)} complete year(s) below the standing "
                            f"cadence of {FOMC_PER_YEAR}: "
                            f"{ {y: by_year.get(y, 0) for y in bad[:6]} }")
            entry["complete_years_checked"] = len(complete)
        per_source[src] = entry

    return {"window": [start.isoformat(), end.isoformat()],
            "sources": per_source, "hard_faults": hard, "soft_faults": soft,
            "ok": not hard}
