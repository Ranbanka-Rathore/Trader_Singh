"""Tests for the EOD liquidity gate — the backtest twin of the live book guard.

Run with:  PYTHONUTF8=1 python tests/test_liquidity_gate.py
"""
import datetime
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from backtest.liquidity_gate import LiquidityGate, gate_by_name
from backtest.real_backtester import Config, RealBacktester

PASS, FAIL = 0, 0


def check(name, cond):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  ✅ {name}")
    else:
        FAIL += 1
        print(f"  ❌ {name}")


def _row(close=100.0, traded=True, volume=500.0, txns=60.0, oi=5000.0):
    return {"close": close, "traded": traded, "volume": volume,
            "txns": txns, "oi": oi, "lot": 75}


def test_leg_gate():
    print("\n[1] leg_ok: settlement-only rows are refused, real prints pass")
    g = LiquidityGate(LiquidityGate.TRADED)
    check("a real print passes", g.leg_ok(_row())[0] is True)

    # THE case this exists for: ClsPric == 0, so bhavcopy substituted SttlmPric.
    # The price is non-zero and looks perfectly usable — that is the trap.
    ok, why = g.leg_ok(_row(close=87.5, traded=False))
    check(f"settlement-only row refused despite non-zero price ({why})",
          ok is False and why == "settle_only")

    check("absent row refused", g.leg_ok(None)[0] is False)
    check("zero price refused", g.leg_ok(_row(close=0.0))[0] is False)

    # fail-safe: a provider that predates the `traded` flag must not silently
    # disable the gate by omitting it
    stale = {"close": 100.0, "oi": 5000.0, "volume": 500.0}
    ok2, why2 = g.leg_ok(stale)
    check(f"row without a `traded` flag is refused, not assumed good ({why2})",
          ok2 is False and why2 == "traded_unknown")


def test_thresholds():
    print("\n[2] thresholds separate 'printed once' from 'was a market'")
    loose, strict = LiquidityGate(LiquidityGate.TRADED), LiquidityGate(LiquidityGate.STRICT)
    thin = _row(volume=1.0, txns=1.0, oi=0.0)
    check("a single print passes the honest minimum", loose.leg_ok(thin)[0] is True)
    ok, why = strict.leg_ok(thin)
    check(f"...but not the strict gate ({why})", ok is False)

    desk = LiquidityGate(LiquidityGate.DESK)
    check("desk gate refuses a mid-liquidity strike",
          desk.leg_ok(_row(volume=10.0, txns=10.0, oi=100.0))[0] is False)
    check("desk gate passes a liquid strike",
          desk.leg_ok(_row(volume=5000.0, txns=900.0, oi=90000.0))[0] is True)

    check("gate 'off' passes even a settlement-only row",
          LiquidityGate(LiquidityGate.OFF).leg_ok(_row(traded=False))[0] is True)


def test_spread_gate():
    print("\n[3] spread_ok: one dead leg kills the structure")
    g = LiquidityGate(LiquidityGate.TRADED)
    check("both legs live -> fillable", g.spread_ok([_row(), _row()])[0] is True)
    ok, why = g.spread_ok([_row(), _row(traded=False)])
    check(f"long leg dead -> whole spread refused ({why})",
          ok is False and "leg1" in why)
    ok2, why2 = g.spread_ok([_row(traded=False), _row()])
    check(f"short leg dead -> refused, reason names leg0 ({why2})",
          ok2 is False and "leg0" in why2)


def test_accounting():
    print("\n[4] the gate reports what it refused and why")
    g = LiquidityGate(LiquidityGate.TRADED)
    for _ in range(7):
        g.leg_ok(_row())
    for _ in range(3):
        g.leg_ok(_row(traded=False))
    check(f"counts checked ({g.checked})", g.checked == 10)
    check(f"counts fillable ({g.passed})", g.passed == 7)
    check(f"pass rate ({g.pass_rate:.0f}%)", abs(g.pass_rate - 70.0) < 1e-6)
    check("refusals bucketed by reason", g.rejections.get("settle_only") == 3)
    g.reset()
    check("reset clears counters", g.checked == 0 and not g.rejections)


def test_preset_lookup():
    print("\n[5] preset lookup")
    check("known preset resolves", gate_by_name("strict").name == "strict")
    try:
        gate_by_name("nope")
        check("unknown preset raises", False)
    except ValueError:
        check("unknown preset raises", True)


def _chain(date, expiry, spot=24000.0, dead_strikes=(), lot=75):
    """BS-priced chain where `dead_strikes` never traded (settlement only)."""
    from backend.app.core import bs_math as bs
    t = max((expiry - date).days, 0) / 365.0
    options = {}
    lo, hi = int(spot * 0.92 // 50 * 50), int(spot * 1.08 // 50 * 50)
    for k in range(lo, hi + 50, 50):
        for typ in ("CE", "PE"):
            p = bs.price(spot, float(k), t, 0.13, typ) if t > 0 else (
                max(spot - k, 0.0) if typ == "CE" else max(k - spot, 0.0))
            live = float(k) not in dead_strikes
            options[(expiry, float(k), typ)] = {
                "close": round(max(p, 0.05), 2), "traded": live,
                "oi": 1000.0 if live else 0.0, "chg_oi": 0.0,
                "volume": 100.0 if live else 0.0, "txns": 50.0 if live else 0.0,
                "lot": lot,
            }
    options[(expiry, float(lo), "PE")]["oi"] = 1.4 * 10_000_000
    options[(expiry, float(hi), "CE")]["oi"] = 10_000_000
    return {"date": date, "underlying": "NIFTY", "spot": spot,
            "expiries": [expiry], "options": options, "futures": {}}


def _seeded(cfg, chains):
    """Backtester with warmup closes/IV seeded, mirroring test_real_backtester."""
    bt = RealBacktester(cfg, lambda d, underlying="NIFTY": chains.get(d))
    n, spot = 30, 24000.0
    bt._closes = [spot / (1.0005 ** (n - i)) for i in range(n)]
    bt._iv_hist = [0.13] * 80
    bt._equity, bt._closed = cfg.equity0, []
    return bt


def test_backtester_refuses_dead_strikes():
    print("\n[6] RealBacktester will not fill a strike that never traded")
    d = datetime.date(2026, 7, 6)
    expiry = datetime.date(2026, 7, 30)
    # kill every strike below spot: the bull-put short leg has nowhere to go
    dead = {float(k) for k in range(int(24000 * 0.92 // 50 * 50), 24000, 50)}
    ch_live = _chain(d, expiry)
    ch_dead = _chain(d, expiry, dead_strikes=dead)

    cfg = Config(equity0=1_500_000.0, liquidity_gate="traded")
    bt_live = _seeded(cfg, {d: ch_live})
    pos_live = bt_live._try_enter(d, ch_live)
    check(f"fully-quoted chain enters ({pos_live and pos_live.strategy})",
          pos_live is not None)

    bt_dead = _seeded(cfg, {d: ch_dead})
    pos_dead = bt_dead._try_enter(d, ch_dead)
    check("dead put wing -> no entry", pos_dead is None)
    check(f"gate checked legs ({bt_dead.gate.checked})", bt_dead.gate.checked > 0)
    check(f"refusals recorded ({dict(bt_dead.gate.rejections)})",
          bt_dead.gate.rejections.get("settle_only", 0) > 0)

    # the same dead chain with the gate off fills happily — this is exactly the
    # behaviour that manufactured profit before
    cfg_off = Config(equity0=1_500_000.0, liquidity_gate="off")
    bt_off = _seeded(cfg_off, {d: ch_dead})
    pos_off = bt_off._try_enter(d, ch_dead)
    check(f"gate 'off' fills the dead chain anyway "
          f"({pos_off and pos_off.strategy})", pos_off is not None)
    check("...at a strike that never traded",
          pos_off is not None and float(pos_off.sell_strike) in dead)


if __name__ == "__main__":
    test_leg_gate()
    test_thresholds()
    test_spread_gate()
    test_accounting()
    test_preset_lookup()
    test_backtester_refuses_dead_strikes()
    print(f"\n{'=' * 50}\nRESULT: {PASS} passed, {FAIL} failed")
    sys.exit(1 if FAIL else 0)
