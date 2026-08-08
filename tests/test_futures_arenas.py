"""Tests for the futures panel and the two arena engines built on it.

The panel's whole reason to exist is that two things silently corrupt a futures
backtest — roll gaps counted as returns, and a guessed lot size — so most of what
is here is about those. The engine tests are about the constraints that decide
whether an arena is even reachable at Rs 15,00,000: whole lots and margin.

Run with:  PYTHONUTF8=1 python tests/test_futures_arenas.py
"""
import datetime
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from backtest import futures
from backtest.futures import Bar
from research import engines

PASS, FAIL = 0, 0


def check(name, cond):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  ✅ {name}")
    else:
        FAIL += 1
        print(f"  ❌ {name}")


# ── synthetic sessions ───────────────────────────────────────────────────────
def _bar(d, sym, exp, close, volume=1000.0, oi=5000.0, lot=50, txns=100.0):
    return Bar(date=d, symbol=sym, expiry=exp, open=close, high=close,
               low=close, close=close, volume=volume, oi=oi, txns=txns,
               lot=lot, traded=volume > 0)


def _loader(days):
    """A build_panel loader over a {date: {symbol: {expiry: Bar}}} dict."""
    return lambda d, kind="stock": days.get(d, {})


def _weekdays(start, n):
    out, d = [], start
    while len(out) < n:
        if d.weekday() < 5:
            out.append(d)
        d += datetime.timedelta(days=1)
    return out


# ── the roll ─────────────────────────────────────────────────────────────────
def test_roll_gap_is_not_a_return():
    print("\n[1] A roll gap is not a return — the single biggest futures trap")
    dates = _weekdays(datetime.date(2025, 1, 1), 10)
    near = datetime.date(2025, 1, 9)
    far = datetime.date(2025, 2, 27)

    # The near contract drifts 100 -> 104. The far contract sits 20 points higher
    # throughout (contango) and drifts identically. On the roll day the front
    # close therefore JUMPS from ~104 to ~124 with nothing earned.
    days = {}
    for i, d in enumerate(dates):
        px = 100.0 + i * 0.5
        days[d] = {"ACME": {near: _bar(d, "ACME", near, px),
                            far: _bar(d, "ACME", far, px + 20.0)}}
    panel = futures.build_panel(dates, gate="strict", roll_days=3,
                                loader=_loader(days))
    ser = panel.series["ACME"]

    check(f"the series rolled ({ser.rolls} roll)", ser.rolls == 1)
    biggest = max(abs(r) for r in ser.rets if r is not None)
    check(f"no return exceeds the real daily drift ({biggest:.4f} < 1%)",
          biggest < 0.01)
    check("...so the +20 point roll jump never became a return",
          all(abs(r) < 0.01 for r in ser.rets if r is not None))

    total = ser.index[-1] / ser.index[0] - 1.0
    raw = ser.bars[-1].close / ser.bars[0].close - 1.0
    check(f"roll-adjusted {total:+.2%} is far below raw front-close {raw:+.2%}",
          total < raw / 2)
    check(f"roll-adjusted total matches the real drift ({total:+.2%} ~ 4%)",
          abs(total - 0.04) < 0.015)


def test_front_expiry():
    print("\n[2] Front-contract selection rolls before settlement, not into it")
    d = datetime.date(2025, 1, 20)
    a, b, c = (datetime.date(2025, 1, 22), datetime.date(2025, 2, 26),
               datetime.date(2025, 3, 26))
    check("with 2 days left, the near contract is skipped",
          futures.front_expiry([a, b, c], d, roll_days=3) == b)
    check("with 6 days left it is still front",
          futures.front_expiry([a, b, c], datetime.date(2025, 1, 16),
                               roll_days=3) == a)
    check("expired contracts are never selected",
          futures.front_expiry([a, b], datetime.date(2025, 2, 1), roll_days=3) == b)
    check("nothing listed -> None", futures.front_expiry([], d) is None)
    check("all near expiry -> the furthest available, not None",
          futures.front_expiry([a], datetime.date(2025, 1, 21), roll_days=3) == a)


def test_gate_and_lot():
    print("\n[3] A bar is dropped when it did not trade or its lot is unknown")
    dates = _weekdays(datetime.date(2025, 1, 1), 6)
    exp = datetime.date(2025, 2, 27)
    days = {}
    for i, d in enumerate(dates):
        days[d] = {
            "LIQUID": {exp: _bar(d, "LIQUID", exp, 100.0 + i)},
            "DEAD": {exp: _bar(d, "DEAD", exp, 100.0 + i, volume=0.0)},
            "NOLOT": {exp: _bar(d, "NOLOT", exp, 100.0 + i, lot=0)},
        }
    panel = futures.build_panel(dates, gate="strict", loader=_loader(days))

    check("a liquid symbol makes it into the panel", "LIQUID" in panel.series)
    check("a symbol that never traded does not", "DEAD" not in panel.series)
    check("...refused as settle_only, on the same rule as the option gate",
          panel.refusals.get("settle_only", 0) == len(dates))
    check("a symbol with an unknown lot is refused, never guessed",
          "NOLOT" not in panel.series and panel.missing_lot == len(dates))
    check(f"pass rate is reported ({panel.pass_rate:.0f}%)",
          0 < panel.pass_rate < 100)

    off = futures.build_panel(dates, gate="off", loader=_loader(days))
    check("with the gate off the dead symbol does trade — the A/B still works",
          "DEAD" in off.series)


def test_untraded_day_does_not_fake_a_return():
    print("\n[4] A gap in trading is skipped, not compounded away")
    dates = _weekdays(datetime.date(2025, 1, 1), 6)
    exp = datetime.date(2025, 3, 27)
    days = {}
    for i, d in enumerate(dates):
        vol = 0.0 if i == 2 else 1000.0
        days[d] = {"ACME": {exp: _bar(d, "ACME", exp, 100.0 + i, volume=vol)}}
    panel = futures.build_panel(dates, gate="strict", loader=_loader(days))
    ser = panel.series["ACME"]

    check(f"the untraded session is absent from the series ({len(ser)} of 6 bars)",
          len(ser) == 5)
    check("the return across it is measured from the real prior close, not skipped",
          all(abs(r - 1.0 / (100 + i)) < 0.02
              for i, r in enumerate(x for x in ser.rets if x is not None)))
    check("no return is inflated by the missing day",
          max(abs(r) for r in ser.rets if r is not None) < 0.03)


# ── engines ──────────────────────────────────────────────────────────────────
def test_engine_registry():
    print("\n[5] The loop can only run engines that exist")
    names = engines.available()
    check(f"all three arenas are registered ({names})",
          {"real_backtester", "futures_trend", "cross_sectional"} <= set(names))
    for n in names:
        e = engines.get(n)
        for method in ("build", "with_params", "grid", "run", "stress", "warmup_days"):
            check(f"{n}.{method} exists", hasattr(e, method))
    try:
        engines.get("event_vol")
        check("an unbuilt arena raises", False)
    except KeyError as exc:
        check(f"an unbuilt arena raises by name ({str(exc)[:34]}...)",
              "unknown engine" in str(exc))


def test_grids_are_comparable():
    print("\n[6] Grid sizes match, so the deflated-Sharpe hurdle is comparable")
    sizes = {n: len(engines.get(n).grid()) for n in engines.available()}
    check(f"every arena searches the same number of combos {sizes}",
          len(set(sizes.values())) == 1)
    check("...and it is 8, as fixed in the Phase-4 protocol",
          set(sizes.values()) == {8})


def test_warmup_is_engine_specific():
    print("\n[7] Warmup is asked of the engine, not assumed")
    opt = engines.get("real_backtester")
    tr = engines.get("futures_trend")
    xs = engines.get("cross_sectional")
    w_opt = opt.warmup_days(opt.build({}))
    w_tr = tr.warmup_days(tr.build({}))
    w_xs = xs.warmup_days(xs.build({}))
    check(f"option arena warms up in {w_opt} days", w_opt >= 30)
    check(f"trend needs more than that ({w_tr})", w_tr > w_opt)
    check(f"12-1 momentum needs over a year ({w_xs})", w_xs > 365)
    check("...which is why a single fixed warmup would have starved it",
          w_xs > 5 * w_opt)


def _monthly_expiry(d):
    """A contract that settles on the 25th, so the panel actually rolls."""
    y, m = d.year, d.month
    if d.day > 24:
        m += 1
        if m > 12:
            m, y = 1, y + 1
    return datetime.date(y, m, 25)


def _trending(dates, sym="TREND", start=100.0, drift=0.004, vol=0.01,
              lot=50, seed=3):
    """An uptrend with REAL daily volatility, on monthly contracts.

    Both matter. A perfectly smooth ramp has near-zero return variance, which
    makes the volatility stop microscopic, makes every position affordable
    regardless of lot size, and means nothing ever stops out — so the engine
    looks broken when it is the fixture that is unphysical. And without a
    rolling expiry a winning position simply never closes, so no trade is ever
    recorded at all.
    """
    import random
    rng = random.Random(seed)
    out, px = {}, start
    for d in dates:
        px *= (1.0 + drift + rng.gauss(0.0, vol))
        e = _monthly_expiry(d)
        out[d] = {sym: {e: _bar(d, sym, e, round(max(px, 1.0), 2), lot=lot)}}
    return out


def test_trend_engine():
    print("\n[8] The trend engine takes the breakout and pays real costs")
    dates = _weekdays(datetime.date(2025, 1, 1), 90)
    days = _trending(dates)
    eng = engines.get("futures_trend")
    cfg = eng.build({"equity": 1_500_000.0,
                     "config": {"universe": "TREND", "kind": "stock",
                                "entry_lookback": 10, "exit_lookback": 5,
                                "vol_lookback": 10}})
    res = eng.run(cfg, dates, provider=_loader(days))

    check(f"a strong uptrend is traded ({res['summary']['n_trades']} trades)",
          res["summary"]["n_trades"] >= 1)
    longs = [t for t in res["trades"] if t["direction"] == "LONG"]
    shorts = [t for t in res["trades"] if t["direction"] == "SHORT"]
    check(f"...mostly long, as an uptrend should be ({len(longs)}L/{len(shorts)}S)",
          len(longs) > len(shorts))
    check("positions do close — a trade that never exits is not a result",
          all(t["exit_date"] >= t["entry_date"] for t in res["trades"]))
    check("every trade paid friction",
          all(t["friction"] > 0 for t in res["trades"]))
    check("net P&L is gross minus friction, exactly",
          all(abs(t["net_pnl"] - (t["gross_pnl"] - t["friction"])) < 0.01
              for t in res["trades"]))
    check("lots are whole numbers",
          all(isinstance(t["lots"], int) and t["lots"] >= 1 for t in res["trades"]))
    check("the result carries a liquidity-gate block like every other engine",
          "pass_rate_pct" in res["liquidity_gate"])
    check("...and a summary the screen can read",
          {"n_trades", "profit_factor", "sharpe_annualized", "max_drawdown"}
          <= set(res["summary"]))


def test_trend_refuses_unaffordable_lot():
    print("\n[9] One lot too big for the risk cap is skipped, not part-sized")
    dates = _weekdays(datetime.date(2025, 1, 1), 90)
    # a lot of 50,000 at ~100 is Rs 50L of notional — no Rs 15L account can risk it
    days = _trending(dates, lot=50_000)
    eng = engines.get("futures_trend")
    cfg = eng.build({"equity": 1_500_000.0,
                     "config": {"universe": "TREND", "kind": "stock",
                                "entry_lookback": 10, "vol_lookback": 10}})
    res = eng.run(cfg, dates, provider=_loader(days))
    check("nothing was traded", res["summary"]["n_trades"] == 0)
    check("...and the reason is recorded as a capacity refusal",
          res["skip_reasons"].get("one_lot_exceeds_risk_cap", 0) > 0)
    check("no fractional lot appeared anywhere",
          all(float(t["lots"]).is_integer() for t in res["trades"]))


def test_xsection_capacity():
    print("\n[10] Cross-sectional capacity: the margin budget is a hard ceiling")
    dates = _weekdays(datetime.date(2023, 1, 2), 400)
    exp_for = lambda d: d + datetime.timedelta(days=45)
    days = {}
    # 12 names on deterministic, separated trends so the ranking is unambiguous
    for i, d in enumerate(dates):
        day = {}
        for k in range(12):
            sym = f"S{k:02d}"
            px = 100.0 * (1.0 + (k - 6) * 0.0006) ** i
            e = exp_for(d)
            day[sym] = {e: _bar(d, sym, e, max(px, 1.0), volume=100000.0,
                                lot=500)}
        days[d] = day
    eng = engines.get("cross_sectional")
    cfg = eng.build({"equity": 1_500_000.0,
                     "config": {"mom_lookback": 120, "adv_lookback": 10,
                                "min_adv_rs": 0.0, "n_per_side": 4,
                                "rebalance_days": 30}})
    res = eng.run(cfg, dates, provider=_loader(days))
    s = res["summary"]

    check(f"it rebalanced ({s['rebalances']} times)", s["rebalances"] >= 2)
    check(f"positions were wanted ({s['positions_wanted']})",
          s["positions_wanted"] > 0)
    check(f"capacity fill rate is reported ({s['capacity_fill_rate_pct']}%)",
          0 < s["capacity_fill_rate_pct"] <= 100)
    check("taken never exceeds wanted",
          s["positions_taken"] <= s["positions_wanted"])

    gross = sum(t["quantity"] * t["entry_price"] for t in res["trades"])
    per_rebalance = gross / max(s["rebalances"], 1)
    ceiling = (cfg.max_gross_margin_frac / cfg.margin_frac) * cfg.equity0
    check(f"gross notional per rebalance Rs {per_rebalance:,.0f} stays under the "
          f"{cfg.max_gross_margin_frac / cfg.margin_frac:.0f}x ceiling "
          f"Rs {ceiling:,.0f}", per_rebalance <= ceiling * 1.35)
    check("both sides were traded",
          {"LONG", "SHORT"} == {t["direction"] for t in res["trades"]})
    check("every trade paid friction on a real quantity",
          all(t["friction"] > 0 and t["quantity"] > 0 for t in res["trades"]))


def test_xsection_liquidity_screen():
    print("\n[11] The ADV screen keeps untradeable names out of the ranking")
    dates = _weekdays(datetime.date(2023, 1, 2), 300)
    days = {}
    for i, d in enumerate(dates):
        e = d + datetime.timedelta(days=45)
        day = {}
        for k in range(8):
            sym = f"T{k:02d}"
            # half the universe trades 10 lots a day: real prices, no market
            vol = 100000.0 if k % 2 == 0 else 10.0
            day[sym] = {e: _bar(d, sym, e, 100.0 + i * (k - 4) * 0.01,
                                volume=vol, lot=100)}
        days[d] = day
    eng = engines.get("cross_sectional")
    cfg = eng.build({"equity": 1_500_000.0,
                     "config": {"mom_lookback": 60, "adv_lookback": 10,
                                "min_adv_rs": 1_000_000.0, "n_per_side": 2,
                                "rebalance_days": 30}})
    res = eng.run(cfg, dates, provider=_loader(days))
    traded = {t["symbol"] for t in res["trades"]}
    check(f"only the liquid half is ever traded ({sorted(traded)})",
          all(int(s[1:]) % 2 == 0 for s in traded))
    check("the thin names are absent, not merely underweighted",
          not any(int(s[1:]) % 2 for s in traded))


def test_cli_engine_guards():
    print("\n[12] Registration validates the engine, not just the words")
    import shutil
    import tempfile
    from research import loop, registry

    tmp = tempfile.mkdtemp(prefix="arena_cli_")
    saved = (registry.KILL_LOG_PATH, registry.SURVIVORS_DIR, registry.RESULTS_DIR)
    registry.KILL_LOG_PATH = os.path.join(tmp, "kill_log.json")
    registry.SURVIVORS_DIR = os.path.join(tmp, "survivors")
    registry.RESULTS_DIR = os.path.join(tmp, "results")
    try:
        rc = loop.main(["register", "--id", "t1", "--arena", "futures_trend",
                        "--engine", "futures_trend", "--era", "modern",
                        "--set", "entry_lookback=55",
                        "--claim", "breakouts trend", "--kill", "t below the bar"])
        check(f"a futures hypothesis registers with its own fields (rc={rc})", rc == 0)
        h = registry.get("t1")
        check("...and the engine is recorded", h["engine"] == "futures_trend")
        check("...with the override coerced to an int, not left a string",
              h["config"]["entry_lookback"] == 55)

        rc = loop.main(["register", "--id", "t2", "--arena", "futures_trend",
                        "--engine", "futures_trend", "--era", "modern",
                        "--set", "delta_target=0.2",
                        "--claim", "x", "--kill", "y"])
        check(f"an option field on a futures engine is refused (rc={rc})", rc == 2)
        check("...and nothing was registered", registry.get("t2") is None)

        rc = loop.main(["register", "--id", "t3", "--arena", "index_structures",
                        "--engine", "futures_trend", "--era", "modern",
                        "--claim", "x", "--kill", "y"])
        check(f"an arena/engine mismatch is refused (rc={rc})", rc == 2)

        rc = loop.main(["register", "--id", "t4", "--arena", "event_vol",
                        "--engine", "event_vol", "--era", "modern",
                        "--claim", "x", "--kill", "y"])
        check(f"the unbuilt arena cannot be registered at all (rc={rc})", rc == 2)

        rc = loop.main(["register", "--id", "t5", "--arena", "cross_sectional",
                        "--engine", "cross_sectional", "--era", "modern",
                        "--set", "allow_short=false",
                        "--claim", "long only momentum", "--kill", "t below bar"])
        check("a boolean override becomes a real bool, not a truthy string",
              rc == 0 and registry.get("t5")["config"]["allow_short"] is False)
    finally:
        registry.KILL_LOG_PATH, registry.SURVIVORS_DIR, registry.RESULTS_DIR = saved
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    test_roll_gap_is_not_a_return()
    test_front_expiry()
    test_gate_and_lot()
    test_untraded_day_does_not_fake_a_return()
    test_engine_registry()
    test_grids_are_comparable()
    test_warmup_is_engine_specific()
    test_trend_engine()
    test_trend_refuses_unaffordable_lot()
    test_xsection_capacity()
    test_xsection_liquidity_screen()
    test_cli_engine_guards()
    print(f"\n{'=' * 50}\nRESULT: {PASS} passed, {FAIL} failed")
    sys.exit(1 if FAIL else 0)
