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
                "chg_oi": 0.0, "volume": 100.0, "txns": 50.0, "lot": lot,
                # fully-quoted fixture chain: every strike printed a real trade,
                # so the liquidity gate is a no-op here and these tests keep
                # measuring strategy logic rather than fill plausibility
                "traded": True,
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


def test_config_refuses_silent_noops():
    print("\n[11] Config refuses the settings that would run something else")
    from backtest.real_backtester import ConfigError

    def refuses(name, **kw):
        try:
            Config(**kw)
        except ConfigError:
            check(name, True)
            return
        check(name, False)

    # A butterfly is unreachable through classify_entry — it has no branch for
    # one — so this pairing would silently run vanilla credit spreads.
    refuses("butterfly without unconditional entry is refused",
            enable_iron_butterfly=True)
    # Unconditional entry enters ONE structure; zero or two is ambiguous.
    refuses("unconditional with no structure is refused",
            entry_unconditional=True, use_gates=False)
    refuses("unconditional with two structures is refused",
            entry_unconditional=True, use_gates=False,
            enable_iron_butterfly=True, enable_calendar=True)
    # The report must not be able to claim a regime filter that was bypassed.
    refuses("unconditional + use_gates is refused",
            entry_unconditional=True, use_gates=True,
            enable_iron_butterfly=True, sl_mode="mark")
    # THE important one: both shorts at ATM makes the touch test a tautology,
    # so the structure stops out on its own entry bar, every cycle.
    refuses("ATM butterfly + strike_touch stop is refused",
            enable_iron_butterfly=True, entry_unconditional=True,
            use_gates=False, sl_mode="strike_touch")
    refuses("empty width_fallbacks is refused", width_fallbacks=())
    refuses("zero-width wing is refused", width_fallbacks=(0,))

    # And the valid combination builds.
    ok = Config(enable_iron_butterfly=True, entry_unconditional=True,
                use_gates=False, sl_mode="mark", width_fallbacks=(6,))
    check("valid butterfly config builds", ok.width_fallbacks == (6,))

    # width_intervals is gone, not merely unused: an override naming it must
    # fail loudly at registration rather than run a different width in silence.
    from research.screen import coerce, ScreenError
    try:
        coerce("width_intervals", "6")
        check("width_intervals rejected by coerce", False)
    except ScreenError:
        check("width_intervals rejected by coerce", True)
    check("width_fallbacks accepts a single width", coerce("width_fallbacks", "6") == (6,))


def fly_cfg(**kw):
    # sl_mode="none" is deliberate, not a shortcut: at a measured ~222 credit
    # inside a 300-wide wing the mark stop at 1.5x credit asks for a 333/share
    # loss the structure cannot produce (max loss 78). See test [15].
    base = dict(enable_iron_butterfly=True, entry_unconditional=True,
                use_gates=False, sl_mode="none", width_fallbacks=(6,),
                equity0=1_500_000.0)
    base.update(kw)
    return Config(**base)


def test_iron_butterfly():
    print("\n[12] Iron butterfly — ATM shorts, wings out, unconditional entry")
    d1, d2 = D(2026, 7, 6), D(2026, 7, 7)
    ch1 = mk_chain(d1, 24000.0, iv=0.13, pcr=1.00)
    closes, ivh = choppy_seeds()

    bt = RealBacktester(fly_cfg(), provider_from({d1: ch1}))
    bt._closes, bt._iv_hist, bt._equity, bt._closed = closes, ivh, 1_500_000.0, []
    pos = bt._try_enter(d1, ch1)

    check("butterfly entered", pos is not None and pos.strategy == "IRON_BUTTERFLY")
    # The blocker this was written to clear: _pick_short_strike starts at i=1 and
    # can never return ATM, so a butterfly built through it is impossible.
    check("BOTH shorts at ATM", pos.sell_strike == 24000.0 and pos.call_sell == 24000.0)
    check("wings 6 intervals (300pts) out",
          pos.buy_strike == 23700.0 and pos.call_buy == 24300.0)
    check(f"credit {pos.entry_credit} >= floor 12", pos.entry_credit >= 12.0)
    # ATM shorts collect more than the condor's OTM shorts at the same width.
    # Compared at the research equity of Rs 15L: at 5L a 300-pt wing cannot be
    # sized at all for the condor (max loss/lot 20,250 > the 3% hard cap of
    # 15,000), so the comparison would silently be against no trade.
    ic = RealBacktester(Config(enable_iron_condor=True, width_fallbacks=(6,),
                               equity0=1_500_000.0),
                        provider_from({d1: ch1}))
    ic._closes, ic._iv_hist, ic._equity, ic._closed = closes, ivh, 1_500_000.0, []
    ic_pos = ic._try_enter(d1, ch1)
    check("ATM butterfly out-earns the OTM condor at equal width",
          ic_pos is not None and pos.entry_credit > ic_pos.entry_credit)
    # Max loss is the wing less the credit — one side only, as for a condor.
    check("max loss = wing - credit",
          approx(pos.sizing["max_loss_per_lot"] / pos.lot_size,
                 300.0 - pos.entry_credit, tol=0.5))
    check("margin basis is the butterfly's own",
          pos.sizing.get("margin_basis") in ("fly_worse_side", "naked_cap"))


def test_unconditional_entry_lifts_the_duty_cycle():
    print("\n[13] Unconditional entry bypasses classify_entry but not risk gates")
    # A regime the gates refuse outright: rich-vol branch blocked by a trending
    # PCR with no confirmation is the common case, so use the condor, which
    # classify_entry only emits on middle PCR + low ER.
    d1 = D(2026, 7, 6)
    ch1 = mk_chain(d1, 24000.0, iv=0.13, pcr=1.90)   # far from middle PCR
    closes, ivh = choppy_seeds()

    gated = RealBacktester(Config(enable_iron_condor=True),
                           provider_from({d1: ch1}))
    gated._closes, gated._iv_hist, gated._equity, gated._closed = closes, ivh, 500_000.0, []
    gated_pos = gated._try_enter(d1, ch1)

    uncond = RealBacktester(
        Config(enable_iron_condor=True, entry_unconditional=True, use_gates=False),
        provider_from({d1: ch1}))
    uncond._closes, uncond._iv_hist, uncond._equity, uncond._closed = closes, ivh, 500_000.0, []
    uncond_pos = uncond._try_enter(d1, ch1)

    check("gated run declines this regime", gated_pos is None
          or gated_pos.strategy != "IRON_CONDOR")
    check("unconditional run enters anyway",
          uncond_pos is not None and uncond_pos.strategy == "IRON_CONDOR")

    # use_gates=False is NOT the same thing, and that confusion is the trap:
    # its fallback emits only BULL_PUT/BEAR_CALL, so a condor becomes
    # unavailable rather than unconditional.
    off = RealBacktester(Config(enable_iron_condor=True, use_gates=False),
                         provider_from({d1: ch1}))
    off._closes, off._iv_hist, off._equity, off._closed = closes, ivh, 500_000.0, []
    off_pos = off._try_enter(d1, ch1)
    check("use_gates=False makes the condor UNAVAILABLE, not unconditional",
          off_pos is not None and off_pos.strategy != "IRON_CONDOR")

    # Risk filters survive: warmup and the event blackout are not regime opinions.
    cold = RealBacktester(
        Config(enable_iron_condor=True, entry_unconditional=True, use_gates=False),
        provider_from({d1: ch1}))
    cold._closes, cold._iv_hist, cold._equity, cold._closed = [24000.0] * 3, ivh, 500_000.0, []
    check("warmup still refuses a thin closes series",
          cold._try_enter(d1, ch1) is None and "warmup" in cold.skip_reasons)


def test_butterfly_exits_and_settlement():
    print("\n[14] Butterfly economics: crush pays, adverse move is capped")
    d1, d2 = D(2026, 7, 6), D(2026, 7, 7)
    closes, ivh = choppy_seeds()
    ch1 = mk_chain(d1, 24000.0, iv=0.13, pcr=1.00)

    # vol crush with spot pinned at ATM -> the butterfly's best case
    ch2 = mk_chain(d2, 24000.0, iv=0.07, pcr=1.0)
    bt = RealBacktester(fly_cfg(), provider_from({d1: ch1, d2: ch2}))
    res = bt.run([d1, d2], seed_closes=closes, seed_iv_hist=ivh)
    fl = [t for t in res["trades"] if t["strategy"] == "IRON_BUTTERFLY"]
    check("vol crush pinned at ATM is profitable", fl and fl[0]["net_pnl"] > 0)

    # a move out to the wing is the butterfly's bad case
    ch2b = mk_chain(d2, 24300.0, iv=0.15, pcr=1.0)
    bt2 = RealBacktester(fly_cfg(), provider_from({d1: ch1, d2: ch2b}))
    res2 = bt2.run([d1, d2], seed_closes=closes, seed_iv_hist=ivh)
    fl2 = [t for t in res2["trades"] if t["strategy"] == "IRON_BUTTERFLY"]
    check("adverse move loses money", fl2 and fl2[0]["net_pnl"] < 0)
    check("adverse loss stays inside the wing",
          fl2 and abs(fl2[0]["gross_pnl"]) <= 300.0 * fl2[0]["quantity"])

    # settlement: spot pinned exactly at ATM = both shorts expire worthless =
    # the whole credit is kept, which is the structure's maximum outcome.
    bt3 = RealBacktester(fly_cfg(), provider_from({}))
    bt3._closes, bt3._iv_hist, bt3._equity, bt3._closed = closes, ivh, 1_500_000.0, []
    p = bt3._try_enter(d1, ch1)
    check("settles to zero liability when pinned at ATM",
          approx(bt3._intrinsic(p, 24000.0), 0.0))
    # a full move to the wing costs exactly the wing width, one side only
    check("settles to the wing width on a full adverse move",
          approx(bt3._intrinsic(p, 24300.0), 300.0)
          and approx(bt3._intrinsic(p, 23700.0), 300.0))
    check("loss is capped past the wing",
          approx(bt3._intrinsic(p, 25000.0), 300.0))


def test_unreachable_mark_stop_is_reported():
    print("\n[15] A stop loss that cannot fire is reported, not silent")
    d1, d2 = D(2026, 7, 6), D(2026, 7, 7)
    closes, ivh = choppy_seeds()
    ch1 = mk_chain(d1, 24000.0, iv=0.13, pcr=1.00)
    ch2 = mk_chain(d2, 24300.0, iv=0.15, pcr=1.0)

    # The default sl_mark_mult=1.5 was calibrated on verticals, where credit is
    # small against width. An ATM butterfly inverts that: credit ~222 inside a
    # 300 wing caps the loss at 78, while the stop asks for 333.
    cfg = fly_cfg(sl_mode="mark")
    bt = RealBacktester(cfg, provider_from({d1: ch1}))
    bt._closes, bt._iv_hist, bt._equity, bt._closed = closes, ivh, 1_500_000.0, []
    pos = bt._try_enter(d1, ch1)
    max_loss_ps = pos.sizing["max_loss_per_lot"] / pos.lot_size
    trigger = cfg.sl_mark_mult * pos.entry_credit
    check(f"stop trigger {trigger:.0f}/sh exceeds max loss {max_loss_ps:.0f}/sh",
          trigger > max_loss_ps)
    check("and the position says so", pos.sizing["mark_stop_binds"] is False)

    bt2 = RealBacktester(cfg, provider_from({d1: ch1, d2: ch2}))
    res = bt2.run([d1, d2], seed_closes=closes, seed_iv_hist=ivh)
    check("the run reports it in engine_extras",
          res["summary"]["engine_extras"]["positions_with_unreachable_mark_stop"] >= 1)

    # sl_mode="none" is the honest expression of the same outcome: the wing IS
    # the stop. It reports None rather than False — nothing was claimed.
    bt3 = RealBacktester(fly_cfg(), provider_from({d1: ch1}))
    bt3._closes, bt3._iv_hist, bt3._equity, bt3._closed = closes, ivh, 1_500_000.0, []
    check("sl_mode='none' claims no mark stop at all",
          bt3._try_enter(d1, ch1).sizing["mark_stop_binds"] is None)

    # On a vertical, where it was calibrated, the same stop binds normally.
    bt4 = RealBacktester(Config(sl_mode="mark", equity0=1_500_000.0),
                         provider_from({d1: ch1}))
    bt4._closes, bt4._iv_hist, bt4._equity, bt4._closed = seeds(24000.0)[0], ivh, 1_500_000.0, []
    v = bt4._try_enter(d1, mk_chain(d1, 24000.0, iv=0.13, pcr=1.8))
    check("the same stop still binds on a vertical",
          v is not None and v.sizing["mark_stop_binds"] is True)


def test_kelly_probe_floor_applies_to_small_positive_edge():
    print("\n[16] A barely-positive Kelly edge still sizes one lot")
    d1 = D(2026, 7, 6)
    ch1 = mk_chain(d1, 24000.0, iv=0.13, pcr=1.00)
    closes, ivh = choppy_seeds()
    bt = RealBacktester(Config(enable_iron_condor=True, entry_unconditional=True,
                               use_gates=False, sl_mode="none",
                               width_fallbacks=(4,), equity0=1_500_000.0),
                        provider_from({d1: ch1}))
    bt._closes, bt._iv_hist, bt._equity, bt._closed = closes, ivh, 1_500_000.0, []

    # A closed-trade history giving a SMALL positive f*: 21 trades, 11 wins of
    # 1,000 and 10 losses of 900 -> f* = 0.524 - 0.476/1.111 = +0.095... too big.
    # Tune to land just above zero: wins barely outnumber and barely outsize.
    class T:
        def __init__(self, p): self.net_pnl = p
    bt._closed = [T(1000.0)] * 11 + [T(-1050.0)] * 10
    f = bt._kelly_fraction()
    check(f"f* is small but positive ({f:.4f})", f is not None and 0 < f < 0.05)

    lots, sizing = bt._size_lots(width=200.0, credit=40.0, lot=75, spot=24000.0,
                                 dnet=0.05, structure="iron_condor", call_width=200.0)
    raw = int(0.25 * f * 1_500_000.0 / sizing["max_loss_per_lot"])
    check(f"raw Kelly lots would round to {raw}", raw == 0)
    check("but the probe floor keeps it at 1", sizing["l_kelly"] == 1)
    check("so the position is actually taken", lots >= 1)

    # A NEGATIVE f* was already floored; that behaviour is unchanged.
    bt._closed = [T(100.0)] * 5 + [T(-900.0)] * 16
    check("negative f* still gets the 1-lot probe",
          bt._size_lots(width=200.0, credit=40.0, lot=75, spot=24000.0, dnet=0.05,
                        structure="iron_condor", call_width=200.0)[1]["l_kelly"] == 1)

    # And the floor does not override the other constraints.
    poor = RealBacktester(Config(equity0=20_000.0), provider_from({d1: ch1}))
    poor._closes, poor._iv_hist, poor._equity = closes, ivh, 20_000.0
    poor._closed = [T(1000.0)] * 11 + [T(-1050.0)] * 10
    lots2, s2 = poor._size_lots(width=200.0, credit=40.0, lot=75, spot=24000.0,
                                dnet=0.05, structure="iron_condor", call_width=200.0)
    check("risk/margin still bind on an account too small", lots2 == 0)


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
    test_config_refuses_silent_noops()
    test_iron_butterfly()
    test_unconditional_entry_lifts_the_duty_cycle()
    test_butterfly_exits_and_settlement()
    test_unreachable_mark_stop_is_reported()
    test_kelly_probe_floor_applies_to_small_positive_edge()
    print(f"\n{'='*50}\nRESULT: {PASS} passed, {FAIL} failed")
    sys.exit(1 if FAIL else 0)
