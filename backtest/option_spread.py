"""
Measure the REAL NIFTY option bid-ask spread.

WHY THIS EXISTS
---------------
Arena 5 screen 1 was killed on economics, and the number that killed it —
`slippage_per_leg = 0.75` — is a DEFAULT in backtest/real_backtester.py, not
something this project has ever observed. The sensitivity sweep put breakeven at
~0.36/leg, so that single unmeasured constant decides whether the arena reopens
or whether most of Phase 2 closes. Measuring it is therefore worth more than any
further screen.

Dhan's `quote_data` returns the full 5-level depth book, and returns it after
hours too (as the closing snapshot). So the spread is directly observable; it
never needed to be assumed.

WHAT "PER LEG" MEANS HERE
-------------------------
`real_backtester` models a fill as `close +/- slippage_per_leg`, i.e. the cost of
one leg relative to mid. That is the HALF-spread, not the full spread. A book of
125.35 / 125.85 is 0.50 wide and costs **0.25 per leg** to cross from mid. Every
figure this module calls `per_leg` is the half-spread, so it is directly
comparable to the config constant it exists to check.

THE TRAP THIS MODULE IS BUILT AROUND
------------------------------------
Spread must be measured against the premium you would actually trade, and delta
must match that same contract. Arena 5's first economics took the friction of a
Rs 19 option and the delta of an ATM one (0.5) — but a Rs 19 NIFTY option is far
OTM with delta nearer 0.1. Mixing them overstates edge per rupee of cost. So this
module reports spread jointly with premium AND measured delta, per contract, and
refuses to average across moneyness buckets that do not share a delta.

A single snapshot is not a distribution. `snapshot()` records one; run it
repeatedly through a session to build the intraday picture, which is what the
`--watch` mode does.

Usage:
  python -m backtest.option_spread --snapshot            # one sweep, now
  python -m backtest.option_spread --watch --minutes 360 # sample all session
  python -m backtest.option_spread --report
"""
import argparse
import datetime
import json
import math
import os
import sys
import time
from typing import Any, Dict, List, Optional

import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from backend.app.core import scrip_master  # noqa: E402
from backend.app.core.bs_math import delta as bs_delta, implied_vol  # noqa: E402

OUT_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                       "data", "spread")
SNAP_PATH = os.path.join(OUT_DIR, "snapshots.parquet")

BATCH = 100          # quote_data accepts far more; 100 keeps payloads small
# Dhan's Market Quote API is rate-limited to ~1 request/second. At 0.4s every
# batch after the first came back with an empty error object and no data — a
# silent throttle rather than an explicit 429, which is exactly the kind of
# failure that quietly halves a dataset. 1.2s leaves headroom.
SLEEP_BETWEEN = 1.2
NSE_TICK = 0.05      # NIFTY option tick size — the floor on any spread


def _client():
    from dotenv import load_dotenv
    load_dotenv()
    from dhanhq import dhanhq
    cid, tok = os.getenv("DHAN_CLIENT_ID"), os.getenv("DHAN_ACCESS_TOKEN")
    if not cid or not tok:
        raise RuntimeError("DHAN_CLIENT_ID / DHAN_ACCESS_TOKEN missing from .env")
    return dhanhq(cid, tok)


def _spot(dhan) -> Optional[float]:
    try:
        q = dhan.ohlc_data(securities={"IDX_I": [13]})
        if q.get("status") == "success":
            return float(q["data"]["data"]["IDX_I"]["13"]["last_price"])
    except Exception:  # noqa: BLE001
        pass
    return None


def targets(underlying: str, band_pct: float, n_expiries: int,
            spot: float) -> List[Dict[str, Any]]:
    """Contracts to quote: within `band_pct` of spot, nearest `n_expiries`."""
    scrip_master._load()
    board = scrip_master._options.get(underlying, {})
    today = datetime.date.today()
    expiries = sorted(e for e in board if e >= today)[:n_expiries]
    lo, hi = spot * (1 - band_pct / 100), spot * (1 + band_pct / 100)

    out = []
    for exp in expiries:
        for (strike_key, opt_type), (sid, lot, sym, exch) in board[exp].items():
            try:
                strike = float(strike_key)
            except ValueError:
                continue
            if lo <= strike <= hi:
                out.append({"security_id": int(sid), "trading_symbol": sym,
                            "expiry": exp, "strike": strike, "opt_type": opt_type,
                            "lot": lot,
                            "segment": "BSE_FNO" if exch == "BSE" else "NSE_FNO"})
    return out


def _parse(rec: Dict[str, Any], meta: Dict[str, Any], spot: float,
           at: datetime.datetime) -> Optional[Dict[str, Any]]:
    depth = rec.get("depth") or {}
    buys = [b for b in (depth.get("buy") or []) if float(b.get("price") or 0) > 0]
    sells = [s for s in (depth.get("sell") or []) if float(s.get("price") or 0) > 0]

    bid = max((float(b["price"]) for b in buys), default=0.0)
    ask = min((float(s["price"]) for s in sells), default=0.0)
    # A one-sided book is not a tradeable book. Charter Section 6.2 forced the
    # 2026-07-30 ledger purge over exactly this; it is recorded, not discarded,
    # so the share of untradeable contracts stays visible.
    two_sided = bid > 0 and ask > 0 and ask >= bid

    mid = (bid + ask) / 2 if two_sided else None
    spread = (ask - bid) if two_sided else None

    t_years = max((meta["expiry"] - at.date()).days, 0) / 365.0
    iv = dlt = None
    if mid and t_years > 0:
        iv = implied_vol(mid, spot, meta["strike"], t_years, meta["opt_type"])
        if iv and iv > 0:
            dlt = bs_delta(spot, meta["strike"], t_years, iv, meta["opt_type"])

    return {
        "at": at,
        "security_id": meta["security_id"],
        "trading_symbol": meta["trading_symbol"],
        "expiry": meta["expiry"],
        "dte": (meta["expiry"] - at.date()).days,
        "strike": meta["strike"],
        "opt_type": meta["opt_type"],
        "lot": meta["lot"],
        "spot": spot,
        "moneyness": meta["strike"] / spot if spot else None,
        "bid": bid or None,
        "ask": ask or None,
        "mid": mid,
        "two_sided": two_sided,
        "spread": spread,
        "per_leg": (spread / 2) if spread is not None else None,
        "spread_pct_mid": (100 * spread / mid) if (spread is not None and mid) else None,
        "bid_qty": sum(int(b.get("quantity") or 0) for b in buys[:1]),
        "ask_qty": sum(int(s.get("quantity") or 0) for s in sells[:1]),
        "last_price": float(rec.get("last_price") or 0) or None,
        "volume": int(rec.get("volume") or 0),
        "oi": int(rec.get("oi") or 0),
        "iv": iv,
        "delta": dlt,
        "last_trade_time": rec.get("last_trade_time"),
    }


def snapshot(dhan, underlying: str = "NIFTY", band_pct: float = 8.0,
             n_expiries: int = 2, verbose: bool = True) -> pd.DataFrame:
    """One sweep of the board's depth book."""
    spot = _spot(dhan)
    if not spot:
        raise RuntimeError("could not read spot")
    tg = targets(underlying, band_pct, n_expiries, spot)
    by_id = {t["security_id"]: t for t in tg}
    at = datetime.datetime.now()
    if verbose:
        print(f"  spot {spot:,.2f}  quoting {len(tg)} contracts "
              f"across {len({t['expiry'] for t in tg})} expiries")

    rows = []
    ids = sorted(by_id)
    for i in range(0, len(ids), BATCH):
        chunk = ids[i:i + BATCH]
        payload = None
        for attempt in range(3):
            try:
                r = dhan.quote_data(securities={"NSE_FNO": chunk})
            except Exception as e:  # noqa: BLE001
                print(f"    batch {i}: {type(e).__name__}: {e}")
                time.sleep(1.5 * (attempt + 1))
                continue
            if r.get("status") == "success":
                payload = (r.get("data") or {}).get("data", {}).get("NSE_FNO", {}) or {}
                break
            # A throttled quote call returns status!=success with an all-None
            # error object, so retry rather than treating it as "no data".
            time.sleep(1.5 * (attempt + 1))
        if payload is None:
            print(f"    batch {i}: no data after 3 attempts (throttled?)")
            continue
        for sid, rec in payload.items():
            meta = by_id.get(int(sid))
            if meta:
                row = _parse(rec, meta, spot, at)
                if row:
                    rows.append(row)
        time.sleep(SLEEP_BETWEEN)

    df = pd.DataFrame(rows)
    if verbose and not df.empty:
        live = df[df["two_sided"]]
        print(f"  got {len(df)} quotes, {len(live)} two-sided "
              f"({100*len(live)/len(df):.0f}%)")
    return df


def _append(df: pd.DataFrame) -> int:
    os.makedirs(OUT_DIR, exist_ok=True)
    if os.path.exists(SNAP_PATH):
        try:
            df = pd.concat([pd.read_parquet(SNAP_PATH), df], ignore_index=True)
        except Exception:
            pass
    df.to_parquet(SNAP_PATH, index=False)
    return len(df)


def report(path: str = SNAP_PATH) -> None:
    if not os.path.exists(path):
        print("no snapshots yet — run --snapshot")
        return
    df = pd.read_parquet(path)
    print("=" * 78)
    print("NIFTY OPTION SPREAD — measured")
    print("=" * 78)
    print(f"snapshots      {df['at'].nunique()}  "
          f"({df['at'].min()} .. {df['at'].max()})")
    print(f"quotes         {len(df):,}")

    live = df[df["two_sided"] & df["mid"].notna() & (df["mid"] > 0)].copy()
    print(f"two-sided      {len(live):,} ({100*len(live)/max(len(df),1):.0f}%)")
    print("   ^ a one-sided book is not tradeable at any price; excluded below")
    if live.empty:
        return

    # Only contracts that actually traded can be crossed at the quoted book.
    traded = live[live["volume"] > 0]
    print(f"and traded     {len(traded):,} "
          f"({100*len(traded)/max(len(live),1):.0f}% of two-sided)")

    print(f"\nThe constant this exists to check: real_backtester "
          f"slippage_per_leg = 0.75")
    for name, d in (("all two-sided", live), ("traded only", traded)):
        if d.empty:
            continue
        q = d["per_leg"].quantile([0.25, 0.5, 0.75, 0.9]).round(3)
        print(f"  {name:<14} median per_leg Rs {q[0.5]:.3f}   "
              f"p25 {q[0.25]:.3f}  p75 {q[0.75]:.3f}  p90 {q[0.9]:.3f}")

    # Premium buckets. Spread must be read against the premium actually traded,
    # and delta reported alongside so the two are never mixed across buckets.
    print("\nBY PREMIUM — with the delta that goes with it (never average across):")
    bins = [0, 5, 10, 20, 40, 80, 160, 1e9]
    labels = ["<5", "5-10", "10-20", "20-40", "40-80", "80-160", ">160"]
    traded = traded.copy()
    traded["bucket"] = pd.cut(traded["mid"], bins=bins, labels=labels, right=False)
    print(f"{'premium':>8} {'n':>6} {'mid':>8} {'per_leg':>9} {'as % mid':>9} "
          f"{'|delta|':>8} {'per_leg/pt':>11}")
    print("-" * 70)
    for lab in labels:
        b = traded[traded["bucket"] == lab]
        if b.empty:
            continue
        med_leg = b["per_leg"].median()
        med_delta = b["delta"].abs().median()
        # Cost per index point of exposure: the only cross-bucket comparison
        # that is meaningful, since delta differs by bucket.
        per_pt = med_leg / med_delta if med_delta and med_delta > 0 else float("nan")
        print(f"{lab:>8} {len(b):>6} {b['mid'].median():>8.2f} {med_leg:>9.3f} "
              f"{b['spread_pct_mid'].median():>8.2f}% {med_delta:>8.3f} "
              f"{per_pt:>11.3f}")
    print("\n  per_leg/pt = rupees of half-spread per unit of delta = the cost, in")
    print("  index points, of crossing the book once. This is the number Arena 5")
    print("  needs, because its edge is measured in index points.")

    # ---- the number Arena 5 actually has to clear --------------------------
    #
    # Spread is only half the cost. Statutory charges are near-flat per lot, and
    # a lot of a LOW-delta option buys very little index exposure — so fixed
    # costs per index point explode as delta falls. Cheap options look cheap per
    # rupee and are ruinous per point. This table is the honest comparison.
    from backend.app.core.friction_model import round_trip_friction

    print("\nTOTAL ROUND-TRIP COST IN INDEX POINTS (spread + statutory):")
    print(f"{'premium':>8} {'mid':>8} {'|delta|':>8} {'expo':>7} {'spread Rs':>10} "
          f"{'stat Rs':>8} {'total Rs':>9} {'COST pts':>9}")
    print("-" * 74)
    best = None
    for lab in labels:
        b = traded[traded["bucket"] == lab]
        if b.empty:
            continue
        mid = float(b["mid"].median())
        leg = float(b["per_leg"].median())
        dl = float(b["delta"].abs().median())
        lot = int(b["lot"].median())
        if not dl or dl <= 0:
            continue
        exposure = dl * lot                      # index-units controlled per lot
        spread_rs = 2 * leg * lot                # crossed on entry and on exit
        stat_rs = round_trip_friction(
            [{"side": "BUY", "price": mid, "quantity": lot, "instrument": "option"}],
            [{"side": "SELL", "price": mid, "quantity": lot, "instrument": "option"}],
        )["total"]
        total_rs = spread_rs + stat_rs
        cost_pts = total_rs / exposure
        if best is None or cost_pts < best[1]:
            best = (lab, cost_pts, mid, dl)
        print(f"{lab:>8} {mid:>8.2f} {dl:>8.3f} {exposure:>7.1f} {spread_rs:>10.2f} "
              f"{stat_rs:>8.2f} {total_rs:>9.2f} {cost_pts:>9.2f}")

    if best:
        lab, cost_pts, mid, dl = best
        print(f"\n  CHEAPEST BUCKET: premium {lab} (mid Rs {mid:.2f}, delta {dl:.3f})")
        print(f"  -> {cost_pts:.2f} index points, round trip, all-in.")
        edge = 2.995   # Arena 5 best cell, vwap_dev/or_pos at h=60
        print(f"\n  Arena 5's best measured edge was {edge:.3f} index points (h=60).")
        if cost_pts < edge:
            print(f"  Cost {cost_pts:.2f} < edge {edge:.3f}: the edge SURVIVES this "
                  f"cost by {edge - cost_pts:.2f} pts ({edge/cost_pts:.2f}x).")
            print("  >> Screen 1 was killed on an ASSUMED cost. That assumption is")
            print("     not supported by this measurement and the kill must be")
            print("     re-examined. See the caveats below before acting on it.")
        else:
            print(f"  Cost {cost_pts:.2f} >= edge {edge:.3f}: the kill STANDS, and now")
            print("  stands on a measured cost rather than an assumed one.")

        # The irreducible bound. Statutory charges do not move however good the
        # execution is, so this is the cost of a strategy that NEVER crosses the
        # spread — perfect limit fills, no slippage, which nothing achieves.
        print("\n  IRREDUCIBLE FLOOR — statutory charges only, zero spread:")
        floor_best = None
        for lab in labels:
            b = traded[traded["bucket"] == lab]
            if b.empty:
                continue
            mid = float(b["mid"].median())
            dl = float(b["delta"].abs().median())
            lot = int(b["lot"].median())
            if not dl or dl <= 0:
                continue
            stat_rs = round_trip_friction(
                [{"side": "BUY", "price": mid, "quantity": lot, "instrument": "option"}],
                [{"side": "SELL", "price": mid, "quantity": lot, "instrument": "option"}],
            )["total"]
            fl = stat_rs / (dl * lot)
            if floor_best is None or fl < floor_best[1]:
                floor_best = (lab, fl, mid)
        if floor_best:
            lab, fl, mid = floor_best
            print(f"    cheapest at premium {lab} (Rs {mid:.2f}): "
                  f"{fl:.2f} index points round trip")
            print(f"    Even with PERFECT limit fills and zero slippage, the edge of")
            print(f"    {edge:.3f} pts leaves {edge - fl:+.2f} pts. That floor cannot be")
            print("    executed away; it is the exchange's and the government's cut.")

    print("\nBY DTE:")
    for dte, b in traded.groupby("dte"):
        if len(b) < 5:
            continue
        print(f"  {dte:>3}d  n={len(b):>4}  median per_leg Rs {b['per_leg'].median():.3f}"
              f"  ({b['spread_pct_mid'].median():.2f}% of mid)")


def main() -> None:
    ap = argparse.ArgumentParser(description="measure the NIFTY option spread")
    ap.add_argument("--snapshot", action="store_true")
    ap.add_argument("--watch", action="store_true", help="sample repeatedly")
    ap.add_argument("--minutes", type=int, default=60)
    ap.add_argument("--every", type=int, default=120, help="seconds between sweeps")
    ap.add_argument("--band", type=float, default=8.0)
    ap.add_argument("--expiries", type=int, default=2)
    ap.add_argument("--report", action="store_true")
    a = ap.parse_args()

    if a.report:
        report()
        return
    if not (a.snapshot or a.watch):
        ap.error("choose --snapshot, --watch or --report")

    dhan = _client()
    if a.snapshot:
        print(f"snapshot at {datetime.datetime.now():%Y-%m-%d %H:%M:%S}")
        df = snapshot(dhan, band_pct=a.band, n_expiries=a.expiries)
        if not df.empty:
            print(f"  stored, {_append(df):,} rows total")
        report()
        return

    end = time.time() + a.minutes * 60
    n = 0
    while time.time() < end:
        print(f"\nsweep {n + 1} at {datetime.datetime.now():%H:%M:%S}")
        try:
            df = snapshot(dhan, band_pct=a.band, n_expiries=a.expiries)
            if not df.empty:
                _append(df)
                n += 1
        except Exception as e:  # noqa: BLE001
            print(f"  sweep failed: {type(e).__name__}: {e}")
        time.sleep(max(a.every, 5))
    print(f"\n{n} sweeps stored")
    report()


if __name__ == "__main__":
    main()
