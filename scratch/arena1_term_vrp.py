"""Which tenor's variance risk premium is richer on weekly NIFTY — near or far?

This decides whether the reverse calendar (W2) is worth three code changes.

A reverse calendar is long the near leg and short the far leg. For a delta-hedged
option the vol-related P&L runs with (realised - implied), so:

    long near  ->  P&L ~ +(RV_near - IV_near)  =  -VRP_near
    short far  ->  P&L ~ -(RV_far  - IV_far)   =  +VRP_far

    net, per unit of vega  ~  VRP_far - VRP_near

So the structure wins only if **the FAR leg is more overpriced than the near
one**. The standard finding in equity index options is the opposite — the
shortest tenor is the richest — which is exactly the objection raised against W2.
The far leg does carry more vega, so the honest test is whether that weighting
recovers the gap. Both are computed below.

MEASURED IN VARIANCE, NOT VOL. Realised vol over a 5-day near leg is estimated
from a handful of returns, and sqrt of an unbiased variance estimator is biased
LOW by Jensen's inequality — a bias that would land almost entirely on the near
leg and manufacture exactly the answer being tested for. Variance is unbiased, so
VRP is computed as IV^2 - RV^2 and converted to vol points only for reading.

Entry is 5-8 days before the near expiry, which is what `min_days_to_expiry=4`
already makes the engine do.

NOT A HYPOTHESIS. No claim registered, no config budget spent, no P&L. It spends
knowledge of arena 1's term structure, which is disclosed in ARENAS.md.
"""
import collections
import datetime as dt
import math
import statistics as st
import sys

sys.path.insert(0, r"D:\Projects\Agentic_Trader")

from backend.app.core import bs_math
from backtest import bhavcopy, futures
from backtest.liquidity_gate import LiquidityGate, gate_by_name
from research import charter

UNDERLYING = "NIFTY"
GATE = "strict_legacy"
ENTRY_DTE = (7, 6, 8, 5)        # engine enters at 5-8 DTE; prefer ~7
FAR_MAX_DAYS = 35
MIN_RV_DAYS = 3


def vega(s, k, t, sigma):
    """dPrice/dSigma. Units cancel in the ratio, so per-1.0-of-sigma is fine."""
    if t <= 0 or sigma <= 0:
        return 0.0
    d1 = bs_math._d1(s, k, t, sigma, bs_math.DEFAULT_R)
    return s * bs_math._norm_pdf(d1) * math.sqrt(t)


def realised_var(rets):
    """Annualised realised VARIANCE. Unbiased; no sqrt taken here on purpose."""
    if len(rets) < MIN_RV_DAYS:
        return None
    m = sum(rets) / len(rets)
    v = sum((r - m) ** 2 for r in rets) / (len(rets) - 1)
    return v * 252.0


def gated_close(chain, expiry, strike, side, gate):
    o = chain["options"].get((expiry, float(strike), side))
    if not o:
        return None
    ok, _ = gate.leg_ok({"close": o.get("close"), "traded": o.get("traded"),
                         "volume": o.get("volume"), "txns": o.get("txns"),
                         "oi": o.get("oi")})
    return float(o["close"]) if ok else None


def main():
    start, end = charter.era_window("modern", cap=dt.date.today())
    sessions = futures.trading_dates(start, end)
    sess_idx = {d: i for i, d in enumerate(sessions)}
    gate = LiquidityGate(gate_by_name(GATE))

    # Realised vol source: NIFTY front futures, roll-adjusted. Same series
    # Finding 4 used; the basis noise is small against a 13-16% vol.
    panel = futures.build_panel(sessions, kind="index", gate=GATE)
    ser = panel.series.get(UNDERLYING)
    rets_by_date = {b.date: r for b, r in zip(ser.bars, ser.rets) if r is not None}

    def rv_between(a, b):
        rs = [rets_by_date[d] for d in sessions
              if a < d <= b and d in rets_by_date]
        return realised_var(rs)

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

    rows = []
    skips = collections.Counter()
    for near in expiries:
        entry = next((near - dt.timedelta(days=k) for k in ENTRY_DTE
                      if (near - dt.timedelta(days=k)) in sess_idx), None)
        if entry is None:
            skips["no_entry_session"] += 1
            continue
        chain = bhavcopy.load_chain(entry, UNDERLYING)
        if not chain or not chain.get("spot"):
            skips["no_chain"] += 1
            continue
        spot = float(chain["spot"])
        far = next((e for e in chain["expiries"]
                    if e > near and (e - near).days <= FAR_MAX_DAYS), None)
        if far is None:
            skips["no_far"] += 1
            continue
        step = bhavcopy.infer_strike_interval(chain, near, spot) or 50
        atm = float(round(spot / step) * step)
        c_near = gated_close(chain, near, atm, "CE", gate)
        c_far = gated_close(chain, far, atm, "CE", gate)
        if c_near is None or c_far is None:
            skips["leg_not_tradeable"] += 1
            continue

        t_near = (near - entry).days / 365.0
        t_far = (far - entry).days / 365.0
        iv_n = bs_math.implied_vol(c_near, spot, atm, t_near, "CE")
        iv_f = bs_math.implied_vol(c_far, spot, atm, t_far, "CE")
        if not iv_n or not iv_f or iv_n <= 0 or iv_f <= 0:
            skips["iv_solver_failed"] += 1
            continue
        rv_n = rv_between(entry, near)
        rv_f = rv_between(entry, far)
        if rv_n is None or rv_f is None:
            skips["insufficient_rv"] += 1
            continue

        vrp_n = iv_n ** 2 - rv_n          # variance premium, annualised
        vrp_f = iv_f ** 2 - rv_f
        rows.append({
            "near": near, "iv_n": iv_n, "iv_f": iv_f,
            "rv_n": math.sqrt(max(rv_n, 0)), "rv_f": math.sqrt(max(rv_f, 0)),
            "vrp_n": vrp_n, "vrp_f": vrp_f,
            "vega_n": vega(spot, atm, t_near, iv_n),
            "vega_f": vega(spot, atm, t_far, iv_f),
        })

    print(f"NIFTY weekly term-structure VRP, {start} -> {end}")
    print(f"{len(rows)} cycles measured; skips {dict(skips)}\n")
    if not rows:
        return

    def mean(k):
        return sum(r[k] for r in rows) / len(rows)

    def tstat(vals):
        m = sum(vals) / len(vals)
        s = st.stdev(vals) if len(vals) > 1 else 0.0
        return (m / (s / math.sqrt(len(vals)))) if s > 0 else 0.0

    print("--- implied vs realised, by tenor (vol points) ---")
    print(f"{'tenor':>6} {'mean IV':>9} {'mean RV':>9} {'IV-RV':>8}")
    print(f"{'near':>6} {mean('iv_n')*100:8.2f}% {mean('rv_n')*100:8.2f}% "
          f"{(mean('iv_n')-mean('rv_n'))*100:7.2f}")
    print(f"{'far':>6} {mean('iv_f')*100:8.2f}% {mean('rv_f')*100:8.2f}% "
          f"{(mean('iv_f')-mean('rv_f'))*100:7.2f}")

    print("\n--- variance risk premium (annualised variance units) ---")
    vn = [r["vrp_n"] for r in rows]
    vf = [r["vrp_f"] for r in rows]
    print(f"  near  mean {sum(vn)/len(vn):+.5f}   t {tstat(vn):+.2f}")
    print(f"  far   mean {sum(vf)/len(vf):+.5f}   t {tstat(vf):+.2f}")

    diff = [r["vrp_f"] - r["vrp_n"] for r in rows]
    md = sum(diff) / len(diff)
    print(f"\n--- the question: is FAR richer than NEAR? ---")
    print(f"  mean (VRP_far - VRP_near) = {md:+.5f}   t {tstat(diff):+.2f}")
    print(f"  positive favours the reverse calendar; negative kills it.")
    print(f"  cycles where far is richer: "
          f"{100.0*sum(1 for d in diff if d > 0)/len(diff):.1f}%")

    print(f"\n--- does the vega weighting recover it? ---")
    vw = [r["vega_f"] * r["vrp_f"] - r["vega_n"] * r["vrp_n"] for r in rows]
    mv = sum(vw) / len(vw)
    ratio = mean("vega_f") / mean("vega_n") if mean("vega_n") else float("nan")
    print(f"  mean vega ratio far/near = {ratio:.2f}x")
    print(f"  vega-weighted net = {mv:+.2f}   t {tstat(vw):+.2f}")
    print(f"  positive favours the reverse calendar; negative kills it.")
    print(f"  cycles positive: {100.0*sum(1 for v in vw if v > 0)/len(vw):.1f}%")


if __name__ == "__main__":
    main()
