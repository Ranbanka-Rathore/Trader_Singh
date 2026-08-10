"""Is the 52/year weekly-NIFTY supply real once Rs 15L has to size the trade?

The supply measurement (`arena1_weekly_supply.py`) found a near-ATM pair openable
on ~52 NIFTY expiries a year, against the 32.2 Amendment A5 needs. It was
explicitly an UPPER BOUND: a quoted leg is not a filled position in the size
wanted. Arena 2 measured 35% capacity fill and arena 4 measured 6.6%, and
anything like that applied to 52 erases the headroom. This measures the gap.

"Capacity fill" here means what it means everywhere else in this project: of the
trades the structure wanted to open, on what fraction could at least ONE whole
lot actually be sized? Indian F&O has no sub-lot sizing, so a structure whose one
lot exceeds the risk budget is not a small position -- it is no position.

The sizing rule is `RealBacktester`'s own, not a new one invented here
(`real_backtester.py:260`):

    max_loss_per_lot = (width - credit) * lot
    L_risk           = floor(risk_frac * equity / max_loss_per_lot)     risk_frac 0.015
    hard cap         = one lot allowed if max_loss_per_lot <= 0.03 * equity

L_vol and L_kelly are omitted: both only ever REDUCE the count, and Kelly needs a
trade history this measurement does not have. So the fill rate below is again an
upper bound, deliberately.

NOT A HYPOTHESIS. No claim registered, no budget spent. Structure-level supply
and sizing only -- no P&L, no entry rule, no verdict on whether any of this makes
money.

**The options engine models no margin at all** -- `grep margin` finds nothing in
`real_backtester.py` or `engines/options.py`. For a defined-risk vertical that is
close to harmless, since broker margin on a debit/credit spread is roughly its
max loss, which IS modelled. It would not be harmless for a naked or ratioed
structure, and that is worth knowing before arena 1 is registered with one.
"""
import collections
import datetime as dt
import sys

sys.path.insert(0, r"D:\Projects\Agentic_Trader")

from backtest import bhavcopy, futures
from backtest.liquidity_gate import LiquidityGate, gate_by_name
from research import charter

UNDERLYING = "NIFTY"
GATE = "strict_legacy"
EQUITY = 1_500_000.0
RISK_FRAC = 0.015          # real_backtester.py:106
RISK_HARD_CAP = 0.03       # real_backtester.py:109
WIDTH_INTERVALS = (4, 2)   # cfg.width_fallbacks — wide first, then narrow
SHORT_STEPS = 2            # short strike this many intervals OTM
ENTRY_DTE = (2, 3, 4, 5)


def leg(chain, expiry, strike, side, gate):
    o = chain["options"].get((expiry, strike, side))
    if not o:
        return None
    ok, _ = gate.leg_ok({"close": o.get("close"), "traded": o.get("traded"),
                         "volume": o.get("volume"), "txns": o.get("txns"),
                         "oi": o.get("oi")})
    return o if ok else None


def size_one(entry, expiry, gate):
    """Return (outcome, detail) for a two-leg vertical on this expiry."""
    chain = bhavcopy.load_chain(entry, UNDERLYING)
    if not chain or not chain.get("spot"):
        return "no_chain", {}
    spot = chain["spot"]
    strikes = sorted({k[1] for k in chain["options"] if k[0] == expiry})
    if len(strikes) < 6:
        return "thin_strikes", {}
    step = bhavcopy.infer_strike_interval(chain, expiry, spot) or 50
    lot = (chain.get("lot_by_expiry") or {}).get(expiry) or chain.get("lot") or 0
    if not lot:
        return "unknown_lot", {}
    atm = min(strikes, key=lambda s: abs(s - spot))

    # Try the wide spread first, then the narrow one, exactly as the engine does.
    for wi in WIDTH_INTERVALS:
        for side, sign in (("CE", +1), ("PE", -1)):
            short_k = atm + sign * SHORT_STEPS * step
            long_k = short_k + sign * wi * step
            s_leg = leg(chain, expiry, short_k, side, gate)
            l_leg = leg(chain, expiry, long_k, side, gate)
            if not s_leg or not l_leg:
                continue
            credit = float(s_leg["close"]) - float(l_leg["close"])
            width = wi * step
            max_loss = max((width - credit) * lot, 1.0)
            l_risk = int(RISK_FRAC * EQUITY / max_loss)
            if l_risk >= 1:
                return "sized", {"lots": l_risk, "lot": lot, "width": width,
                                 "max_loss": max_loss, "credit": credit}
            if max_loss <= RISK_HARD_CAP * EQUITY:
                return "sized_hardcap", {"lots": 1, "lot": lot, "width": width,
                                         "max_loss": max_loss, "credit": credit}
            return "one_lot_too_big", {"lot": lot, "width": width,
                                       "max_loss": max_loss, "credit": credit}
    return "legs_not_tradeable", {}


def main():
    start, end = charter.era_window("modern", cap=dt.date.today())
    yrs = (end - start).days / 365.25
    need = charter.trades_needed_for_a5(yrs) / yrs
    sessions = futures.trading_dates(start, end)
    sess = set(sessions)
    gate = LiquidityGate(gate_by_name(GATE))

    seen = set()
    for d in sessions[::3]:
        df = bhavcopy.read_df_cached(d)
        if df is None:
            continue
        sym = df[(df["TckrSymb"] == UNDERLYING) & (df["FinInstrmTp"] == "IDO")]
        for v in sym["XpryDt"].unique():
            try:
                seen.add(dt.date.fromisoformat(str(v)[:10]))
            except ValueError:
                pass
    expiries = sorted(x for x in seen if start <= x <= end)

    print(f"{UNDERLYING} weekly capacity, modern era {start} -> {end}")
    print(f"equity Rs {EQUITY:,.0f}  risk_frac {RISK_FRAC}  "
          f"hard cap {RISK_HARD_CAP}  gate {GATE}")
    print(f"A5 needs {need:.1f} sized trades/year\n")

    per_year = collections.defaultdict(collections.Counter)
    lots_seen, lot_sizes, losses = [], collections.Counter(), []
    for x in expiries:
        entry = next((x - dt.timedelta(days=k) for k in ENTRY_DTE
                      if (x - dt.timedelta(days=k)) in sess), None)
        if entry is None:
            continue
        out, det = size_one(entry, x, gate)
        per_year[x.year][out] += 1
        if out.startswith("sized"):
            lots_seen.append(det["lots"])
            lot_sizes[det["lot"]] += 1
            losses.append(det["max_loss"])

    print(f"{'year':>6} {'expiries':>9} {'SIZED':>7} {'per yr':>8} "
          f"{'1lot too big':>13} {'legs fail':>10} {'other':>7} {'fill%':>7}")
    for y in sorted(per_year):
        c = per_year[y]
        tot = sum(c.values())
        ok = c["sized"] + c["sized_hardcap"]
        lo = max(start, dt.date(y, 1, 1))
        hi = min(end, dt.date(y, 12, 31))
        rate = ok / (max((hi - lo).days + 1, 1) / 365.25)
        other = tot - ok - c["one_lot_too_big"] - c["legs_not_tradeable"]
        print(f"{y:>6} {tot:>9} {ok:>7} {rate:>8.1f} {c['one_lot_too_big']:>13} "
              f"{c['legs_not_tradeable']:>10} {other:>7} "
              f"{100.0*ok/max(tot,1):>6.1f}%")

    tot = sum(sum(c.values()) for c in per_year.values())
    ok = sum(c["sized"] + c["sized_hardcap"] for c in per_year.values())
    print(f"\noverall capacity fill: {ok}/{tot} = {100.0*ok/max(tot,1):.1f}%")
    if lots_seen:
        lots_seen.sort()
        losses.sort()
        print(f"lots sized: min {lots_seen[0]}  median "
              f"{lots_seen[len(lots_seen)//2]}  max {lots_seen[-1]}")
        print(f"max loss per lot: min Rs {losses[0]:,.0f}  median "
              f"Rs {losses[len(losses)//2]:,.0f}  max Rs {losses[-1]:,.0f}")
        print(f"NIFTY lot sizes encountered: {dict(lot_sizes)}")
        print(f"\n  risk budget per trade at risk_frac: "
              f"Rs {RISK_FRAC*EQUITY:,.0f}; hard cap Rs {RISK_HARD_CAP*EQUITY:,.0f}")


if __name__ == "__main__":
    main()
