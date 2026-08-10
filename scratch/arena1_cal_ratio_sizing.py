"""Do a calendar and a ratio size like the vertical did? Two different answers.

The 96.9% capacity fill measured for weekly NIFTY used a VERTICAL as its sizing
probe -- which is the one structure Section 8 excludes from arena 1. So the
number established liquidity and lot availability but not that an admissible
structure sizes the same way. This checks the two that are admissible.

They are not the same question:

  * a CALENDAR exists in the engine (`real_backtester.py:555`), so it can be
    measured against the real archive, exactly as the vertical was;
  * a RATIO does not exist, and `backtest/margin.py` refuses it by construction.
    So "does it size the same way" cannot be measured -- it can only be reasoned
    about, and the reasoning turns out to matter more than a number would.

Calendar construction is the engine's own: ATM CE, sell the near expiry, buy the
next expiry within 35 days, debit = far_buy - near_sell, max loss = debit x lot.
Sizing is the engine's own too.

NOT A HYPOTHESIS. No claim registered, no budget spent, no P&L.
"""
import collections
import datetime as dt
import sys

sys.path.insert(0, r"D:\Projects\Agentic_Trader")

from backtest import bhavcopy, futures, margin
from backtest.liquidity_gate import LiquidityGate, gate_by_name
from research import charter

UNDERLYING = "NIFTY"
GATE = "strict_legacy"
EQUITY = 1_500_000.0
RISK_FRAC = 0.015
RISK_HARD_CAP = 0.03
MAX_MARGIN_FRAC = 0.30
CAL_MAX_FAR_DAYS = 35        # cfg: far expiry within 35 days
CAL_MIN_DEBIT = 8.0          # cfg.cal_min_debit, from the engine's test
ENTRY_DTE = (2, 3, 4, 5)


def gated(chain, expiry, strike, side, gate):
    o = chain["options"].get((expiry, float(strike), side))
    if not o:
        return None
    ok, _ = gate.leg_ok({"close": o.get("close"), "traded": o.get("traded"),
                         "volume": o.get("volume"), "txns": o.get("txns"),
                         "oi": o.get("oi")})
    return float(o["close"]) if ok else None


def size_calendar(entry, near, gate):
    chain = bhavcopy.load_chain(entry, UNDERLYING)
    if not chain or not chain.get("spot"):
        return "no_chain", {}
    spot = chain["spot"]
    far = next((e for e in chain["expiries"]
                if e > near and (e - near).days <= CAL_MAX_FAR_DAYS), None)
    if far is None:
        return "cal_no_far_expiry", {}
    step = bhavcopy.infer_strike_interval(chain, near, spot) or 50
    atm = float(round(spot / step) * step)
    near_c = gated(chain, near, atm, "CE", gate)
    far_c = gated(chain, far, atm, "CE", gate)
    if near_c is None or far_c is None:
        return "cal_leg_missing", {}
    debit = round(far_c - near_c, 2)
    if debit < CAL_MIN_DEBIT:
        return "cal_debit_below_floor", {"debit": debit}
    lot = (chain.get("lot_by_expiry") or {}).get(near) or chain.get("lot") or 0
    if not lot:
        return "unknown_lot", {}

    max_loss = max(debit * lot, 1.0)
    l_risk = int(RISK_FRAC * EQUITY / max_loss)
    if l_risk < 1 and max_loss <= RISK_HARD_CAP * EQUITY:
        l_risk = 1
    m, basis = margin.margin_per_lot("calendar", lot=lot, spot=spot,
                                     credit=debit)
    l_margin = margin.lots_within_budget(m, EQUITY, MAX_MARGIN_FRAC)
    lots = min(l_risk, l_margin)
    if lots < 1:
        return "sizing_zero", {"l_risk": l_risk, "l_margin": l_margin,
                               "max_loss": max_loss}
    return "sized", {"lots": lots, "l_risk": l_risk, "l_margin": l_margin,
                     "max_loss": max_loss, "margin": m, "basis": basis,
                     "debit": debit, "lot": lot}


def calendar_pass():
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

    print("=" * 76)
    print(f"CALENDAR — measured against the archive, {start} -> {end}")
    print("=" * 76)
    print(f"A5 needs {need:.1f} sized trades/year\n")

    per_year = collections.defaultdict(collections.Counter)
    lots_seen, margins, risks = [], [], []
    for x in expiries:
        entry = next((x - dt.timedelta(days=k) for k in ENTRY_DTE
                      if (x - dt.timedelta(days=k)) in sess), None)
        if entry is None:
            continue
        out, det = size_calendar(entry, x, gate)
        per_year[x.year][out] += 1
        if out == "sized":
            lots_seen.append(det["lots"])
            margins.append(det["margin"])
            risks.append(det["max_loss"])

    print(f"{'year':>6} {'near expiries':>14} {'SIZED':>7} {'per yr':>8} "
          f"{'debit<floor':>12} {'leg missing':>12} {'fill%':>7}")
    for y in sorted(per_year):
        c = per_year[y]
        tot = sum(c.values())
        ok = c["sized"]
        lo = max(start, dt.date(y, 1, 1))
        hi = min(end, dt.date(y, 12, 31))
        rate = ok / (max((hi - lo).days + 1, 1) / 365.25)
        print(f"{y:>6} {tot:>14} {ok:>7} {rate:>8.1f} "
              f"{c['cal_debit_below_floor']:>12} {c['cal_leg_missing']:>12} "
              f"{100.0*ok/max(tot,1):>6.1f}%")

    tot = sum(sum(c.values()) for c in per_year.values())
    ok = sum(c["sized"] for c in per_year.values())
    print(f"\noverall calendar capacity fill: {ok}/{tot} = "
          f"{100.0*ok/max(tot,1):.1f}%")
    if lots_seen:
        lots_seen.sort(); margins.sort(); risks.sort()
        print(f"lots: min {lots_seen[0]}  median {lots_seen[len(lots_seen)//2]}"
              f"  max {lots_seen[-1]}")
        print(f"margin/lot: median Rs {margins[len(margins)//2]:,.0f}   "
              f"max loss/lot: median Rs {risks[len(risks)//2]:,.0f}")
        print(f"  (identical by construction — a same-strike calendar's margin "
              f"IS its max loss)")


def ratio_pass():
    print("\n" + "=" * 76)
    print("RATIO — cannot be measured, and the reason is the finding")
    print("=" * 76)

    try:
        margin.margin_per_lot("ratio", lot=75, spot=25_000.0)
        print("  margin.py accepted 'ratio' — that would be a bug")
    except margin.MarginError as e:
        print(f"  margin.py refuses it, as designed:\n    {str(e)[:150]}...")

    print("""
  But the margin refusal is the SMALLER half of the problem.

  `_size_lots` computes every one of its constraints from `max_loss_per_lot`:

      max_loss_per_lot = max((width - credit) * lot, 1.0)
      l_risk           = floor(risk_frac * equity / max_loss_per_lot)
      l_kelly          = floor(kelly_frac * f_star * equity / max_loss_per_lot)

  A ratio spread -- long 1, short 2 -- has an uncovered short leg and therefore
  **no max loss at all**. `width` is not defined for it. So `l_risk` and
  `l_kelly` are not merely wrong for a ratio, they are undefined, and the
  hard-cap exception `max_loss <= risk_frac_hard_cap * equity` cannot fire
  either.

  A ratio is therefore not a config change to arena 1. It needs:
    1. a margin treatment (this is the easy part; the naked leg is
       naked_frac x spot x lot, and margin.py already has the arithmetic);
    2. a RISK treatment, which is a genuinely new sizing rule -- the current
       model assumes defined risk everywhere and has no concept of sizing
       against an unbounded tail.

  For scale, if the uncovered leg were margined naked at Rs 15L:""")

    spot, lot = 25_000.0, 75
    naked = margin.DEFAULT.naked_frac * spot * lot
    lots = margin.lots_within_budget(naked, EQUITY, MAX_MARGIN_FRAC)
    print(f"    naked margin per lot  Rs {naked:,.0f}")
    print(f"    lots within a {MAX_MARGIN_FRAC:.0%} budget  {lots}")
    print(f"    against a vertical's ~40 and a calendar's, below — so a ratio is")
    print(f"    MARGIN-bound where the others are RISK-bound. Different regime,")
    print(f"    not a different number.")


if __name__ == "__main__":
    calendar_pass()
    ratio_pass()
