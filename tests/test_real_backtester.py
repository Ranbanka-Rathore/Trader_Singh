"""
Backtester v2 math tests — canned BS-generated chains, no network, no infra.

Chains are priced with Black-Scholes at a known IV so delta-targeted strike
selection, gates, sizing, and exit logic are exactly verifiable.
Run with:  PYTHONUTF8=1 python tests/test_real_backtester.py
"""
import datetime
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from backend.app.core import bs_math as bs, regime_filters as rf
from backtest.real_backtester import RealBacktester, Config, chain_pcr

D = datetime.date
EXPIRY = D(2026, 7, 14)  # Tuesday, 7-13 days from test entry dates

PASS, FAIL = 0, 0


def check(name, cond):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  ✅ {name}")
    else:
        FAIL += 1
        print(f"  ❌ {name}")


def approx(a, b, tol=0.01):
    return abs(a - b) <= tol


def mk_chain(date, spot, iv=0.13, pcr=1.5, expiry=EXPIRY, lot=75):
    """Full BS-priced chain: strikes every 50 within +-8% of spot."""
    t = max((expiry - date).days, 0) / 365.0
    options = {}
    lo = int((spot * 0.92) // 50 * 50)
    hi = int((spot * 1.08) // 50 * 50)
    for k in range(lo, hi + 50, 50):
        for typ in ("CE", "PE"):
            p = bs.price(spot, float(k), t, iv, typ) if t > 0 else (
                max(spot - k, 0.0) if typ == "CE" else max(k - spot, 0.0))
            options[(expiry, float(k), typ)] = {
                "close": round(max(p, 0.05), 2), "oi": 1000.0,
                "chg_oi": 0.0, "volume": 100.0, "lot": lot,
            }
    # OI carriers pin the PCR exactly
    options[(expiry, float(lo), "PE")]["oi"] = pcr * 10_000_000
    options[(expiry, float(hi), "CE")]["oi"] = 10_000_000
    return {"date": date, "underlying": "NIFTY", "spot": spot,
            "expiries": [expiry], "options": options, "futures": {}}


def provider_from(chains):
    return lambda d, underlying="NIFTY": chains.get(d)


def seeds(spot=24000.0, n=30, drift=1.0005, iv=0.13):
    """Uptrending warmup closes + flat IV history."""
    closes = [spot / (drift ** (n - i)) for i in range(n)]
    return closes, [iv] * 80


def bt_with_state(cfg=None, chains=None, spot=24000.0, iv=0.13):
    bt = RealBacktester(cfg or Config(), provider_from(chains or {}))
    closes, ivh = seeds(spot, iv=iv)
    bt._closes, bt._iv_hist, bt._equity = closes, ivh, bt.cfg.equity0
    bt._closed = []
    return bt


def test_delta_strike_selection():
    print("\n[1] Delta-targeted strike selection")
    d = D(2026, 7, 6)
    ch = mk_chain(d, 24000.0, iv=0.13, pcr=1.5)
    bt = bt_with_state(chains={d: ch})
    pos = bt._try_enter(d, ch)
    check("bull put entered", pos is not None and pos.strategy == "BULL_PUT_SPREAD")
    t = (EXPIRY - d).days / 365.0
    dsel = abs(bs.delta(24000.0, pos.sell_strike, t, 0.13, "PE"))
    check(f"short delta {dsel:.3f} in band [0.10,0.28]", 0.10 <= dsel <= 0.28)
    # verify it is the closest-to-target strike among all valid candidates
    best_err = min(
        abs(abs(bs.delta(24000.0, float(k), t, 0.13, "PE")) - bt.cfg.delta_target)
        for k in range(23000, 24000, 50)
        if 0.10 <= abs(bs.delta(24000.0, float(k), t, 0.13, "PE")) <= 0.28)
    check("closest to target", approx(abs(dsel - bt.cfg.delta_target), best_err, 0.02))
    check("width 200 (4x50)", pos.buy_strike == pos.sell_strike - 200)
    check(f"credit {pos.entry_credit} >= floor 8", pos.entry_credit >= 8.0)


def test_gates():
    print("\n[2] Regime gates")
    d = D(2026, 7, 6)
    ch = mk_chain(d, 24000.0, iv=0.13, pcr=1.5)
    bt = bt_with_state(chains={d: ch})

    # warmup: no closes -> blocked
    bt2 = RealBacktester(Config(), provider_from({d: ch}))
    bt2._closes, bt2._iv_hist, bt2._closed = [], [], []
    bt2._equity = bt2.cfg.equity0
    check("warmup blocks", bt2._try_enter(d, ch) is None)

    # VRP thin: chain IV 13% but IV history says current IV (last elem) is low
    bt3 = bt_with_state(chains={d: ch})
    closes_vol = [24000 * (1 + (0.02 if i % 2 else -0.02)) ** i for i in range(30)]
    bt3._closes = closes_vol[-30:]  # violent RV
    check("VRP thin blocks", bt3._try_enter(d, ch) is None)

    # event blackout (2026-07-28 monthly expiry: use a date next to FOMC 2026-07-29)
    ev = D(2026, 7, 27)
    ch_ev = mk_chain(ev, 24000.0, iv=0.13, pcr=1.5, expiry=D(2026, 8, 4))
    bt4 = bt_with_state(chains={ev: ch_ev})
    check("event blackout blocks", bt4._try_enter(ev, ch_ev) is None)

    # mixed PCR: no side
    ch_mix = mk_chain(d, 24000.0, iv=0.13, pcr=1.05)
    bt5 = bt_with_state(chains={d: ch_mix})
    check("PCR 1.05 no side", bt5._try_enter(d, ch_mix) is None)

    # bearish PCR but uptrending closes -> EMA confirmation refuses bear call
    ch_bear = mk_chain(d, 24000.0, iv=0.13, pcr=0.60)
    bt6 = bt_with_state(chains={d: ch_bear})  # seeds are uptrend
    check("bear call refused in uptrend", bt6._try_enter(d, ch_bear) is None)

    # bearish PCR + downtrend -> bear call allowed
    bt7 = bt_with_state(chains={d: ch_bear})
    closes_dn, _ = seeds(24000.0, drift=1.0005)
    bt7._closes = list(reversed(closes_dn))  # falling series ending low
    bt7._closes = [c * 24000.0 / bt7._closes[-1] for c in bt7._closes]
    pos = bt7._try_enter(d, ch_bear)
    check("bear call entered in downtrend", pos is not None and pos.strategy == "BEAR_CALL_SPREAD")


def test_sizing():
    print("\n[3] Sizing = min(L_risk, L_vol, L_kelly)")
    d = D(2026, 7, 6)
    ch = mk_chain(d, 24000.0, iv=0.13, pcr=1.5)
    bt = bt_with_state(chains={d: ch})
    pos = bt._try_enter(d, ch)
    s = pos.sizing
    # L_risk = floor(1.5% x 500k / max_loss_per_lot), min-lot exception under 3% cap
    expected_lrisk = int(0.015 * 500_000 / s["max_loss_per_lot"])
    if expected_lrisk < 1 and s["max_loss_per_lot"] <= 0.03 * 500_000:
        expected_lrisk = 1
    check(f"L_risk {s['l_risk']} == {expected_lrisk}", s["l_risk"] == expected_lrisk)
    check("kelly None (no history)", s["l_kelly"] is None)
    check("lots = min(risk, vol) capped", pos.lots == min(s["l_risk"], s["l_vol"], bt.cfg.max_lots))

    # 20 straight losses -> f* = 0 -> probe size 1 lot
    from backtest.real_backtester import ClosedTrade
    bt._closed = [ClosedTrade(
        entry_date="2026-01-01", exit_date="2026-01-02", strategy="BULL_PUT_SPREAD",
        sell_strike=1, buy_strike=1, expiry="2026-01-06", entry_credit=1,
        exit_cost=2, exit_reason="SL", lots=1, quantity=75, gross_pnl=-1000,
        friction=100, net_pnl=-1100, days_held=1, entry_spot=1, exit_spot=1,
        pcr_at_entry=1, short_delta=0.2) for _ in range(20)]
    pos2 = bt._try_enter(d, ch)
    check("all-loss history -> probe 1 lot", pos2 is not None and pos2.lots == 1)
    check("f* <= 0 recorded", pos2.sizing["f_star"] <= 0)


def test_take_profit_and_strike_touch():
    print("\n[4] TP and strike-touch stop")
    d1, d2 = D(2026, 7, 6), D(2026, 7, 7)
    ch1 = mk_chain(d1, 24000.0, iv=0.13, pcr=1.5)
    # rally + vol crush -> spread collapses -> TP
    ch2 = mk_chain(d2, 24400.0, iv=0.09, pcr=1.05)
    bt = bt_with_state(chains={d1: ch1, d2: ch2})
    res = bt.run([d1, d2], seed_closes=bt._closes, seed_iv_hist=bt._iv_hist)
    check("1 trade", len(res["trades"]) == 1)
    check("TAKE_PROFIT", res["trades"][0]["exit_reason"] == "TAKE_PROFIT")

    # crash through the short strike -> STOP_STRIKE_TOUCH
    ch1b = mk_chain(d1, 24000.0, iv=0.13, pcr=1.5)
    bt2 = bt_with_state(chains={})
    pos = bt2._try_enter(d1, ch1b)
    crash_spot = pos.sell_strike - 50
    ch2b = mk_chain(d2, crash_spot, iv=0.20, pcr=1.5)
    bt3 = bt_with_state(chains={d1: ch1b, d2: ch2b})
    res2 = bt3.run([d1, d2], seed_closes=bt3._closes, seed_iv_hist=bt3._iv_hist)
    check("STOP_STRIKE_TOUCH", res2["trades"][0]["exit_reason"] == "STOP_STRIKE_TOUCH")
    check("loss is negative", res2["trades"][0]["net_pnl"] < 0)


def test_time_stop():
    print("\n[5] Time stop at T-1")
    d1 = D(2026, 7, 6)
    d2 = EXPIRY - datetime.timedelta(days=1)  # T-1
    ch1 = mk_chain(d1, 24000.0, iv=0.13, pcr=1.5)
    ch2 = mk_chain(d2, 23900.0, iv=0.30, pcr=1.10)  # vol up: no TP, no touch
    bt = bt_with_state(chains={d1: ch1, d2: ch2})
    res = bt.run([d1, d2], seed_closes=bt._closes, seed_iv_hist=bt._iv_hist)
    t = res["trades"][0]
    check("TIME_STOP_T1", t["exit_reason"] == "TIME_STOP_T1")
    check("never held to expiry", t["exit_date"] < t["expiry"])


def test_friction_and_metrics():
    print("\n[6] Friction integration + metrics shape")
    d1, d2 = D(2026, 7, 6), D(2026, 7, 7)
    ch1 = mk_chain(d1, 24000.0, iv=0.13, pcr=1.5)
    ch2 = mk_chain(d2, 24400.0, iv=0.09, pcr=1.05)
    bt = bt_with_state(chains={d1: ch1, d2: ch2})
    res = bt.run([d1, d2], seed_closes=bt._closes, seed_iv_hist=bt._iv_hist)
    t = res["trades"][0]
    check("friction > 0", t["friction"] > 0)
    check("net = gross - friction", approx(t["net_pnl"], t["gross_pnl"] - t["friction"], 0.02))
    s = res["summary"]
    for key in ("n_trades", "win_rate", "expectancy_per_trade", "total_net_pnl",
                "return_pct", "profit_factor", "sharpe_annualized", "max_drawdown",
                "max_drawdown_pct", "final_equity", "per_strategy", "exit_reasons"):
        check(f"summary has {key}", key in s)
    check("equity moved", s["final_equity"] != bt.cfg.equity0)
    check("skip_reasons tracked", isinstance(res["skip_reasons"], dict))


def choppy_seeds(spot=24000.0, n=30, amp=0.001, iv=0.13):
    """Range-bound warmup closes (ER ~ 0) + flat IV history."""
    closes = [spot * (1 + (amp if i % 2 else -amp)) for i in range(n - 1)] + [spot]
    return closes, [iv] * 80


def mk_chain_multi(date, spot, expiries_iv, pcr=1.0, lot=75):
    """Chain with several expiries: expiries_iv = {expiry: iv}."""
    base = None
    for exp, iv in sorted(expiries_iv.items()):
        ch = mk_chain(date, spot, iv=iv, pcr=pcr, expiry=exp, lot=lot)
        if base is None:
            base = ch
        else:
            base["options"].update(ch["options"])
            base["expiries"] = sorted(set(base["expiries"] + [exp]))
    return base


def test_iron_condor():
    print("\n[7] Iron condor in middle-PCR range regime (research flag ON)")
    d1, d2 = D(2026, 7, 6), D(2026, 7, 7)
    ic_cfg = Config(enable_iron_condor=True)
    ch1 = mk_chain(d1, 24000.0, iv=0.13, pcr=1.00)
    bt = RealBacktester(ic_cfg, provider_from({d1: ch1}))
    closes, ivh = choppy_seeds()
    bt._closes, bt._iv_hist, bt._equity, bt._closed = closes, ivh, 500_000.0, []
    pos = bt._try_enter(d1, ch1)
    # default config must NOT trade IC (rejected by walk-forward)
    bt_off = RealBacktester(Config(), provider_from({d1: ch1}))
    bt_off._closes, bt_off._iv_hist, bt_off._equity, bt_off._closed = closes, ivh, 500_000.0, []
    check("IC disabled by default", bt_off._try_enter(d1, ch1) is None)
    check("IC entered", pos is not None and pos.strategy == "IRON_CONDOR")
    check("put short below spot", pos.sell_strike < 24000.0)
    check("call short above spot", pos.call_sell > 24000.0)
    check("hedges 200 out both sides",
          pos.buy_strike == pos.sell_strike - 200 and pos.call_buy == pos.call_sell + 200)
    check(f"total credit {pos.entry_credit} >= 12", pos.entry_credit >= 12.0)

    # vol crush next day -> both sides collapse -> TP
    ch2 = mk_chain(d2, 24000.0, iv=0.07, pcr=1.0)
    bt2 = RealBacktester(ic_cfg, provider_from({d1: ch1, d2: ch2}))
    res = bt2.run([d1, d2], seed_closes=closes, seed_iv_hist=ivh)
    ic = [t for t in res["trades"] if t["strategy"] == "IRON_CONDOR"]
    check("IC TP on vol crush", ic and ic[0]["exit_reason"] == "TAKE_PROFIT")
    check("4-leg friction > 2-leg",
          ic and ic[0]["friction"] > 100)  # 8 leg-orders round trip

    # rally through the call short -> strike touch
    ch2b = mk_chain(d2, float(pos.call_sell) + 60, iv=0.15, pcr=1.0)
    bt3 = RealBacktester(ic_cfg, provider_from({d1: ch1, d2: ch2b}))
    res2 = bt3.run([d1, d2], seed_closes=closes, seed_iv_hist=ivh)
    ic2 = [t for t in res2["trades"] if t["strategy"] == "IRON_CONDOR"]
    check("IC call-side strike touch", ic2 and ic2[0]["exit_reason"] == "STOP_STRIKE_TOUCH")


def test_calendar():
    print("\n[8] Calendar spread in cheap-vol regime")
    d1, d2 = D(2026, 7, 6), D(2026, 7, 7)
    near, far = EXPIRY, D(2026, 7, 21)
    ch1 = mk_chain_multi(d1, 24000.0, {near: 0.13, far: 0.13}, pcr=1.0)
    closes, _ = choppy_seeds()
    # IV history says current vol is CHEAP: rank of 0.11 within [0.10, 0.25]
    ivh = [0.10 + 0.15 * (i % 10) / 9 for i in range(79)] + [0.11]
    cal_cfg = Config(enable_calendar=True)
    bt = RealBacktester(cal_cfg, provider_from({d1: ch1}))
    bt._closes, bt._iv_hist, bt._equity, bt._closed = closes, ivh, 500_000.0, []
    pos = bt._try_enter(d1, ch1)
    check("calendar entered", pos is not None and pos.strategy == "CALENDAR_SPREAD")
    check("ATM strike", pos.sell_strike == 24000.0 and pos.buy_strike == 24000.0)
    check("near/far expiries", pos.expiry == near and pos.far_expiry == far)
    check(f"debit {pos.entry_credit} >= 8", pos.entry_credit >= 8.0)
    check("max loss = debit", approx(pos.sizing["max_loss_per_lot"],
                                     pos.entry_credit * pos.lot_size, 1.0))

    # vol expansion -> far leg gains more (vega) -> TP
    ch2 = mk_chain_multi(d2, 24000.0, {near: 0.20, far: 0.20}, pcr=1.0)
    bt2 = RealBacktester(cal_cfg, provider_from({d1: ch1, d2: ch2}))
    res = bt2.run([d1, d2], seed_closes=closes, seed_iv_hist=ivh)
    cal = [t for t in res["trades"] if t["strategy"] == "CALENDAR_SPREAD"]
    check("calendar TP on vol expansion", cal and cal[0]["exit_reason"] == "TAKE_PROFIT")
    check("calendar profit positive", cal and cal[0]["net_pnl"] > 0)

    # vol collapse -> debit shrinks -> SL
    ch2b = mk_chain_multi(d2, 24000.0, {near: 0.10, far: 0.085}, pcr=1.0)
    bt3 = RealBacktester(cal_cfg, provider_from({d1: ch1, d2: ch2b}))
    res2 = bt3.run([d1, d2], seed_closes=closes, seed_iv_hist=ivh)
    cal2 = [t for t in res2["trades"] if t["strategy"] == "CALENDAR_SPREAD"]
    check("calendar SL on vol collapse", cal2 and cal2[0]["exit_reason"] == "STOP_LOSS_MARK")


def test_adaptive_width_small_account():
    print("\n[9] Adaptive width for a Rs 50k account")
    d = D(2026, 7, 6)
    ch = mk_chain(d, 24000.0, iv=0.13, pcr=1.5)
    cfg = Config(equity0=50_000.0, risk_frac_hard_cap=0.15)
    bt = RealBacktester(cfg, provider_from({d: ch}))
    closes, ivh = seeds()
    bt._closes, bt._iv_hist, bt._equity, bt._closed = closes, ivh, 50_000.0, []
    pos = bt._try_enter(d, ch)
    check("trade still possible at 50k", pos is not None)
    check("fell back to width 2 (100pt)", pos.sizing["width_intervals"] == 2
          and pos.buy_strike == pos.sell_strike - 100)
    check("max loss under 15% cap",
          pos.sizing["max_loss_per_lot"] <= 0.15 * 50_000)
    check("1 lot", pos.lots == 1)

    # default 3% cap at 50k -> nothing affordable -> no trade
    cfg2 = Config(equity0=50_000.0)
    bt2 = RealBacktester(cfg2, provider_from({d: ch}))
    bt2._closes, bt2._iv_hist, bt2._equity, bt2._closed = closes, ivh, 50_000.0, []
    check("3% cap refuses 50k account", bt2._try_enter(d, ch) is None)


def ladder_cfg():
    return Config(ladder_mode=True, min_days_to_expiry=30, dte_max=45,
                  time_stop_days=21, max_open=6, equity0=1_500_000)


def test_ladder():
    print("\n[10] Income ladder mode")
    FAR = D(2026, 8, 11)  # ~35 DTE from test dates
    d1, d2 = D(2026, 7, 6), D(2026, 7, 8)   # same ISO week
    d3 = D(2026, 7, 13)                      # next week
    ch1 = mk_chain(d1, 24000.0, iv=0.13, pcr=1.5, expiry=FAR)
    ch2 = mk_chain(d2, 24010.0, iv=0.13, pcr=1.5, expiry=FAR)
    FAR2 = D(2026, 8, 18)  # 36 DTE from d3 (FAR is only 29 out by then)
    ch3 = mk_chain(d3, 24020.0, iv=0.13, pcr=1.5, expiry=FAR2)
    closes, ivh = seeds()

    # weekly cadence: only ONE tranche per ISO week
    bt = RealBacktester(ladder_cfg(), provider_from({d1: ch1, d2: ch2, d3: ch3}))
    bt._closes, bt._iv_hist, bt._equity, bt._closed = closes, ivh, 1_500_000.0, []
    p1 = bt._try_enter(d1, ch1)
    check("tranche 1 entered (30-45 DTE)", p1 is not None
          and 30 <= (p1.expiry - d1).days <= 45)
    bt._last_entry_week = (d1.isocalendar()[0], d1.isocalendar()[1])
    check("same week blocked", bt._try_enter(d2, ch2) is None)
    p3 = bt._try_enter(d3, ch3)
    check("next week allowed", p3 is not None)

    # IVR sizing: flat history -> rank 0.5 -> mult 1.0; rich IV -> bigger
    check("mult recorded", "size_mult" in p1.sizing)
    check("flat IVR -> mult 1.0", abs(p1.sizing["size_mult"] - 1.0) < 0.01)
    bt2 = RealBacktester(ladder_cfg(), provider_from({d1: ch1}))
    ivh_rich = [0.10 + 0.10 * (i % 10) / 9 for i in range(79)] + [0.20]  # rank ~1
    bt2._closes, bt2._iv_hist, bt2._equity, bt2._closed = closes, ivh_rich, 1_500_000.0, []
    p_rich = bt2._try_enter(d1, ch1)
    check("rich IVR -> mult ~1.5", p_rich is not None
          and p_rich.sizing["size_mult"] >= 1.4)
    check("rich sizes >= flat", p_rich.lots >= p1.lots)

    # EMA fallback side: middle PCR + uptrend seeds -> bull put (no hard skip)
    ch_mid = mk_chain(d1, 24000.0, iv=0.13, pcr=1.0, expiry=FAR)
    bt3 = RealBacktester(ladder_cfg(), provider_from({d1: ch_mid}))
    bt3._closes, bt3._iv_hist, bt3._equity, bt3._closed = closes, ivh, 1_500_000.0, []
    p_mid = bt3._try_enter(d1, ch_mid)
    check("middle PCR still trades (EMA tilt)", p_mid is not None
          and p_mid.strategy == "BULL_PUT_SPREAD")

    # portfolio cap: inflate open risk -> new tranche refused
    bt4 = RealBacktester(ladder_cfg(), provider_from({d1: ch1}))
    bt4._closes, bt4._iv_hist, bt4._equity, bt4._closed = closes, ivh, 1_500_000.0, []
    bt4._open_max_loss = 0.099 * 1_500_000
    check("portfolio cap blocks", bt4._try_enter(d1, ch1) is None
          and "portfolio_risk_cap" in bt4.skip_reasons)

    # 21-DTE management exit: day at DTE 21, drifting market (no TP/SL)
    d_mgmt = FAR - datetime.timedelta(days=21)
    ch_m = mk_chain(d_mgmt, 24050.0, iv=0.15, pcr=1.10, expiry=FAR)
    bt5 = RealBacktester(ladder_cfg(), provider_from({d1: ch1, d_mgmt: ch_m}))
    res = bt5.run([d1, d_mgmt], seed_closes=closes, seed_iv_hist=ivh)
    check("managed at 21 DTE", len(res["trades"]) == 1
          and res["trades"][0]["exit_reason"] == "TIME_STOP_T1")


if __name__ == "__main__":
    test_delta_strike_selection()
    test_gates()
    test_sizing()
    test_take_profit_and_strike_touch()
    test_time_stop()
    test_friction_and_metrics()
    test_iron_condor()
    test_calendar()
    test_adaptive_width_small_account()
    test_ladder()
    print(f"\n{'='*50}\nRESULT: {PASS} passed, {FAIL} failed")
    sys.exit(1 if FAIL else 0)
