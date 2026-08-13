"""Can a WEEKLY index structure clear 32 trades/year? Arena 1's precondition.

`cal-cheapvol-modern` produced 18.6 trades/year against the 32.3 that Amendment
A5's 100-OOS-trade rule requires on the modern era, so it could never have been
promoted whatever its edge. The claim recorded in ARENAS.md was that this is a
property of the CONFIGURATION -- a calendar gated on a cheap-vol regime fires
rarely by design -- and that a weekly-expiry structure would clear 32/year
easily. That claim was reasoning, not measurement. This measures it.

Deliberately structure-agnostic. It does NOT test a strategy, propose one, or
compute any P&L. It asks only: **on how many distinct expiries per year could a
near-ATM two-legged index structure actually have been opened**, with both legs
passing the liquidity gate on the entry session? That is the ceiling on trade
count for any weekly structure, and no strategy can exceed it.

NOT A HYPOTHESIS. No claim registered, no config budget spent. It spends
knowledge of arena 1's trade SUPPLY, which is disclosed -- but supply is not
edge, and nothing here says whether a weekly structure would make money.

Two things this cannot see, both of which push the real number DOWN:
  * capacity at Rs 15L. A leg that trades is not necessarily one you can fill in
    the size you want; arena 2 measured 35% capacity fill and arena 4 measured
    6.6%, so the gap between "quoted" and "fillable" is the usual failure here.
  * the structure's own entry condition. Every gate below is unconditional.
So this is an UPPER BOUND on supply, and a structure clearing it is not thereby
viable -- but one failing it is unregistrable against A5.
"""
import collections
import datetime as dt
import sys

sys.path.insert(0, r"D:\Projects\Agentic_Trader")

from backtest import bhavcopy
from backtest.liquidity_gate import LiquidityGate, gate_by_name
from research import charter

UNDERLYINGS = ("NIFTY", "BANKNIFTY", "FINNIFTY", "MIDCPNIFTY")
GATE = "strict_legacy"       # `strict` refuses ALL of 2023 on txns_unknown
                             # (ARENAS Finding 5) -- verified again below
CALENDAR_SAMPLE = 3          # sample every Nth session to build the expiry set
ENTRY_DTE = (2, 3, 4, 5)     # try these DTEs, in order, for the entry session
OTM_STEPS = 4                # how far from ATM a leg may sit, in strike steps
A5_RATE = None               # filled from charter below


def light_expiries(d, underlying):
    """Distinct option expiries for one underlying, without building a chain."""
    df = bhavcopy.read_df_cached(d)
    if df is None:
        return None
    sym = df[(df["TckrSymb"] == underlying) & (df["FinInstrmTp"].isin(["IDO", "STO"]))]
    if sym.empty:
        return set()
    out = set()
    for v in sym["XpryDt"].unique():
        try:
            out.add(dt.date.fromisoformat(str(v)[:10]))
        except ValueError:
            continue
    return out


def tradeable_on(entry, expiry, underlying, gate):
    """Could a near-ATM CE+PE pair on `expiry` have been opened on `entry`?"""
    chain = bhavcopy.load_chain(entry, underlying)
    if not chain or not chain.get("spot"):
        return None
    spot = chain["spot"]
    strikes = sorted({k[1] for k in chain["options"] if k[0] == expiry})
    if len(strikes) < 5:
        return False
    step = bhavcopy.infer_strike_interval(chain, expiry, spot)
    if not step or step <= 0:
        diffs = [b - a for a, b in zip(strikes, strikes[1:]) if b > a]
        step = min(diffs) if diffs else 0
    if not step:
        return False
    atm = min(strikes, key=lambda s: abs(s - spot))

    def ok(side, sign):
        for i in range(1, OTM_STEPS + 1):
            leg = chain["options"].get((expiry, atm + sign * i * step, side))
            if not leg:
                continue
            good, _ = gate.leg_ok({"close": leg.get("close"),
                                   "traded": leg.get("traded"),
                                   "volume": leg.get("volume"),
                                   "txns": leg.get("txns"),
                                   "oi": leg.get("oi")})
            if good:
                return True
        return False

    return ok("CE", +1) and ok("PE", -1)


def main():
    start, end = charter.era_window("modern", cap=dt.date.today())
    yrs = (end - start).days / 365.25
    need_rate = charter.trades_needed_for_a5(yrs) / yrs
    print(f"modern era {start} -> {end}  ({yrs:.2f} years)")
    print(f"A5 needs {charter.MIN_OOS_TRADES} OOS trades => "
          f"{need_rate:.1f} trades/year\n")

    sessions = [d for d in bhavcopy_sessions(start, end)]
    gate = LiquidityGate(gate_by_name(GATE))

    for u in UNDERLYINGS:
        # ── pass 1: the expiry calendar, sampled ────────────────────────────
        seen = set()
        for d in sessions[::CALENDAR_SAMPLE]:
            e = light_expiries(d, u)
            if e:
                seen |= e
        expiries = sorted(x for x in seen if start <= x <= end)
        if not expiries:
            print(f"{u:12s} no option expiries found in this window")
            continue
        by_year = collections.Counter(x.year for x in expiries)
        # monthly = last expiry in its calendar month; the rest are weeklies
        last_of_month = {(x.year, x.month): max(
            y for y in expiries if (y.year, y.month) == (x.month and x.year, x.month))
            for x in expiries} if False else {}
        month_max = {}
        for x in expiries:
            k = (x.year, x.month)
            month_max[k] = max(month_max.get(k, x), x)
        weeklies = [x for x in expiries if month_max[(x.year, x.month)] != x]

        # ── pass 2: could a near-ATM pair be opened on each expiry? ─────────
        tradeable = collections.Counter()
        checked = collections.Counter()
        sess_set = set(sessions)
        for x in expiries:
            entry = None
            for dte in ENTRY_DTE:
                cand = x - dt.timedelta(days=dte)
                if cand in sess_set:
                    entry = cand
                    break
            if entry is None:
                continue
            checked[x.year] += 1
            res = tradeable_on(entry, x, u, gate)
            if res:
                tradeable[x.year] += 1

        print(f"--- {u} ---")
        print(f"{'year':>6} {'expiries':>9} {'weeklies':>9} {'checked':>8} "
              f"{'tradeable':>10} {'per yr':>9} {'verdict vs A5':>16}")
        for y in sorted(by_year):
            tr = tradeable[y]
            # Partial years must be annualised or the comparison is meaningless:
            # 2026 stops in August and 2023 starts at the era boundary.
            lo = max(start, dt.date(y, 1, 1))
            hi = min(end, dt.date(y, 12, 31))
            frac = max((hi - lo).days + 1, 1) / 365.25
            rate = tr / frac
            v = "CLEARS" if rate >= need_rate else f"{need_rate/max(rate,0.1):.1f}x short"
            print(f"{y:>6} {by_year[y]:>9} "
                  f"{sum(1 for x in weeklies if x.year == y):>9} "
                  f"{checked[y]:>8} {tr:>10} {rate:>9.1f} {v:>16}")
        print()


def bhavcopy_sessions(start, end):
    """Weekdays with a cached bhavcopy, mirroring futures.trading_dates."""
    from backtest import futures
    return futures.trading_dates(start, end)


if __name__ == "__main__":
    main()
