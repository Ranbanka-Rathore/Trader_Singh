"""
Phase 3 backtester math tests — canned chains, no network, no infra.

Exercises: PCR bias gating, strike selection, slippage-adjusted entry credit,
TP/SL exits on EOD marks, expiry settlement at intrinsic (worthless + max-loss),
and friction integration.
Run with:  PYTHONUTF8=1 python tests/test_real_backtester.py
"""
import datetime
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from backend.app.core import friction_model
from backtest.real_backtester import RealBacktester, Config, chain_pcr

D = datetime.date
EXPIRY = D(2026, 7, 7)  # Tuesday

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


def mk_chain(date, spot, legs, pcr=1.5, expiry=EXPIRY):
    """legs: {(strike, 'CE'|'PE'): close}. OI arranged to hit the target PCR."""
    options = {}
    for (strike, typ), close in legs.items():
        options[(expiry, float(strike), typ)] = {
            "close": close, "oi": 0.0, "chg_oi": 0.0, "volume": 1000.0, "lot": 65,
        }
    # two OI carrier rows so chain_pcr(chain, expiry) == pcr exactly
    options.setdefault((expiry, 1.0, "PE"), {"close": 0.05, "oi": 0, "chg_oi": 0, "volume": 0, "lot": 65})
    options.setdefault((expiry, 2.0, "CE"), {"close": 0.05, "oi": 0, "chg_oi": 0, "volume": 0, "lot": 65})
    options[(expiry, 1.0, "PE")]["oi"] = pcr * 1_000_000
    options[(expiry, 2.0, "CE")]["oi"] = 1_000_000
    return {"date": date, "underlying": "NIFTY", "spot": spot,
            "expiries": [expiry], "options": options, "futures": {}}


def provider_from(chains):
    def load(d, underlying="NIFTY"):
        return chains.get(d)
    return load


def cfg1():  # 1 lot to keep the arithmetic small
    return Config(lots=1)


def test_pcr_and_strikes():
    print("\n[1] PCR gating + strike selection")
    ch = mk_chain(D(2026, 7, 1), 25000.0,
                  {(24750, "PE"): 100.0, (24700, "PE"): 60.0}, pcr=1.5)
    check("chain_pcr = 1.5", approx(chain_pcr(ch, EXPIRY), 1.5, 0.001))

    bt = RealBacktester(cfg1(), provider_from({D(2026, 7, 1): ch}))
    pos = bt._try_enter(D(2026, 7, 1), ch)
    check("bull put entered", pos is not None and pos.strategy == "BULL_PUT_SPREAD")
    check("sell strike 24750 (1% OTM)", pos.sell_strike == 24750.0)
    check("buy strike 24700 (1 interval)", pos.buy_strike == 24700.0)
    # credit = (100 - 0.75) - (60 + 0.75) = 38.50
    check("credit 38.50 after slippage", approx(pos.entry_credit, 38.50))
    check("qty 65 (1 lot)", pos.quantity == 65)

    # mixed regime -> no trade
    ch_mixed = mk_chain(D(2026, 7, 1), 25000.0,
                        {(24750, "PE"): 100.0, (24700, "PE"): 60.0}, pcr=1.10)
    check("PCR 1.10 skips", bt._try_enter(D(2026, 7, 1), ch_mixed) is None)

    # bearish -> bear call at 1% above
    ch_bear = mk_chain(D(2026, 7, 1), 25000.0,
                       {(25250, "CE"): 90.0, (25300, "CE"): 55.0}, pcr=0.80)
    pos_b = bt._try_enter(D(2026, 7, 1), ch_bear)
    check("bear call entered", pos_b is not None and pos_b.strategy == "BEAR_CALL_SPREAD")
    check("sell 25250 / buy 25300", pos_b.sell_strike == 25250.0 and pos_b.buy_strike == 25300.0)


def test_take_profit():
    print("\n[2] Take profit on EOD mark")
    d1, d2 = D(2026, 7, 1), D(2026, 7, 2)
    chains = {
        d1: mk_chain(d1, 25000.0, {(24750, "PE"): 100.0, (24700, "PE"): 60.0}, pcr=1.5),
        # spread collapses: close_cost = (5+0.75) - (1-0.75->0.25... min 0.05) = 5.75-0.25 = 5.50
        d2: mk_chain(d2, 25200.0, {(24750, "PE"): 5.0, (24700, "PE"): 1.0}, pcr=1.10),
    }
    bt = RealBacktester(cfg1(), provider_from(chains))
    res = bt.run([d1, d2])
    trades = res["trades"]
    check("1 trade closed", len(trades) == 1)
    t = trades[0]
    check("TAKE_PROFIT", t["exit_reason"] == "TAKE_PROFIT")
    check("exit cost 5.50", approx(t["exit_cost"], 5.50))
    # gross = (38.50 - 5.50) * 65 = 2145
    check("gross 2145", approx(t["gross_pnl"], 2145.0, 0.5))
    ef = friction_model.basket_friction([
        {"side": "SELL", "opt_type": "pe", "price": 99.25, "quantity": 65},
        {"side": "BUY", "opt_type": "pe", "price": 60.75, "quantity": 65}])["total"]
    xf = friction_model.basket_friction([
        {"side": "BUY", "opt_type": "pe", "price": 5.75, "quantity": 65},
        {"side": "SELL", "opt_type": "pe", "price": 0.25, "quantity": 65}])["total"]
    check("friction = entry+exit baskets", approx(t["friction"], round(ef + xf, 2), 0.02))
    check("net = gross - friction", approx(t["net_pnl"], t["gross_pnl"] - t["friction"], 0.01))


def test_stop_loss():
    print("\n[3] Stop loss on EOD mark")
    d1, d2 = D(2026, 7, 1), D(2026, 7, 2)
    chains = {
        d1: mk_chain(d1, 25000.0, {(24750, "PE"): 100.0, (24700, "PE"): 60.0}, pcr=1.5),
        # spread blows out: cost = (140+0.75) - (100-0.75) = 41.50 -> pnl -3.0? No:
        # credit 38.50, pnl = 38.50 - 41.50 = -3.00 -> not SL. Use bigger move:
        d2: mk_chain(d2, 24500.0, {(24750, "PE"): 260.0, (24700, "PE"): 180.0}, pcr=1.5),
    }
    # cost = 260.75 - 179.25 = 81.50 -> pnl = 38.50 - 81.50 = -43.00 <= -38.50 SL
    bt = RealBacktester(cfg1(), provider_from(chains))
    res = bt.run([d1, d2])
    t = res["trades"][0]
    check("STOP_LOSS", t["exit_reason"] == "STOP_LOSS")
    check("pnl/share -43.00", approx(t["gross_pnl"] / t["quantity"], -43.00, 0.01))


def test_expiry_worthless():
    print("\n[4] Expiry settlement — spread expires worthless")
    d1 = D(2026, 7, 6)
    chains = {
        d1: mk_chain(d1, 25000.0, {(24750, "PE"): 40.0, (24700, "PE"): 25.0}, pcr=1.5),
        EXPIRY: mk_chain(EXPIRY, 24900.0, {(24750, "PE"): 0.05, (24700, "PE"): 0.05}, pcr=1.10),
    }
    bt = RealBacktester(cfg1(), provider_from(chains))
    res = bt.run([d1, EXPIRY])
    t = res["trades"][0]
    check("EXPIRY_SETTLEMENT", t["exit_reason"] == "EXPIRY_SETTLEMENT")
    check("exit cost 0 (OTM)", t["exit_cost"] == 0.0)
    # credit = 39.25 - 25.75 = 13.50; gross = 13.50*65 = 877.50
    check("gross 877.50", approx(t["gross_pnl"], 877.50, 0.5))
    # settlement exit friction: no exit orders, long leg OTM -> entry friction only
    ef = friction_model.basket_friction([
        {"side": "SELL", "opt_type": "pe", "price": 39.25, "quantity": 65},
        {"side": "BUY", "opt_type": "pe", "price": 25.75, "quantity": 65}])["total"]
    check("friction = entry only", approx(t["friction"], ef, 0.02))


def test_expiry_max_loss():
    print("\n[5] Expiry settlement — deep ITM (max loss + exercise STT on long)")
    d1 = D(2026, 7, 6)
    chains = {
        d1: mk_chain(d1, 25000.0, {(24750, "PE"): 40.0, (24700, "PE"): 25.0}, pcr=1.5),
        EXPIRY: mk_chain(EXPIRY, 24600.0, {(24750, "PE"): 150.0, (24700, "PE"): 100.0}, pcr=1.5),
    }
    bt = RealBacktester(cfg1(), provider_from(chains))
    res = bt.run([d1, EXPIRY])
    t = res["trades"][0]
    check("EXPIRY_SETTLEMENT", t["exit_reason"] == "EXPIRY_SETTLEMENT")
    # intrinsic: short 24750P = 150, long 24700P = 100 -> cost 50/share
    check("exit cost 50 (width)", approx(t["exit_cost"], 50.0))
    # gross = (13.50 - 50) * 65 = -2372.50
    check("gross -2372.50", approx(t["gross_pnl"], -2372.50, 0.5))
    # exercise STT: 0.125% x long intrinsic 100 x 65 = 8.125 on top of entry friction
    ef = friction_model.basket_friction([
        {"side": "SELL", "opt_type": "pe", "price": 39.25, "quantity": 65},
        {"side": "BUY", "opt_type": "pe", "price": 25.75, "quantity": 65}])["total"]
    check("friction = entry + exercise STT", approx(t["friction"], round(ef + 8.125, 2), 0.03))


def test_metrics_shape():
    print("\n[6] Metrics summary shape")
    d1, d2 = D(2026, 7, 1), D(2026, 7, 2)
    chains = {
        d1: mk_chain(d1, 25000.0, {(24750, "PE"): 100.0, (24700, "PE"): 60.0}, pcr=1.5),
        d2: mk_chain(d2, 25200.0, {(24750, "PE"): 5.0, (24700, "PE"): 1.0}, pcr=1.10),
    }
    res = RealBacktester(cfg1(), provider_from(chains)).run([d1, d2])
    s = res["summary"]
    for key in ("n_trades", "win_rate", "expectancy_per_trade", "total_net_pnl",
                "total_friction", "profit_factor", "sharpe_annualized",
                "max_drawdown", "max_consecutive_losses", "per_strategy", "exit_reasons"):
        check(f"summary has {key}", key in s)
    check("win rate 1.0", s["win_rate"] == 1.0)
    check("equity curve present", len(res["equity_curve"]) >= 1)


if __name__ == "__main__":
    test_pcr_and_strikes()
    test_take_profit()
    test_stop_loss()
    test_expiry_worthless()
    test_expiry_max_loss()
    test_metrics_shape()
    print(f"\n{'='*50}\nRESULT: {PASS} passed, {FAIL} failed")
    sys.exit(1 if FAIL else 0)
