"""
Phase 7 — ladder live entry-path tests.

Proves the income ladder sources its entry candidate on CADENCE (straight from the
market snapshot) and does NOT run through the sniper's directional scan
(analyze_universe) when LADDER_MODE is on — the fix for LADDER_MODE never firing
live. Also proves the sniper path is untouched when LADDER_MODE is off, and that the
cadence candidate feeds the options desk + evaluate_ladder without error.

Pure logic; Redis is monkeypatched, the worker's heavy __init__ is bypassed with a
fake self. Cadence / max-open / DB guards live in run_cycle's ladder block (unchanged
Phase-6 validated code) and are exercised by the live paper verification (Task 4),
not here.

Run with:  PYTHONUTF8=1 python tests/test_ladder_live_entry.py
"""
import asyncio
import datetime
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from backend.app.services.worker import AutopilotWorker
from backend.app.services.redis_service import redis_service
from backend.app.services.options_desk_service import options_desk_service
from backend.app.services.regime_service import regime_service

PASS, FAIL = 0, 0


def check(name, cond):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  ✅ {name}")
    else:
        FAIL += 1
        print(f"  ❌ {name}")


class _FakeEngine:
    """Sniper scan spy — records whether analyze_universe was called."""
    def __init__(self):
        self.called = False

    async def analyze_universe(self, universe):
        self.called = True
        return [{"ticker": "NIFTY", "_sniper_sentinel": True, "bias": "BULLISH"}]


class _FakeSelf:
    """Minimal stand-in so we can call the unbound method without the real
    (DB/engine-heavy) AutopilotWorker.__init__."""
    def __init__(self):
        self.engine = _FakeEngine()
        self.universe = ["NIFTY"]


def _install_snapshot(snap):
    async def fake_get_json(key):
        return snap if key == "market_snapshot:NIFTY" else None
    redis_service.get_json = fake_get_json


def _source(fake_self):
    return asyncio.run(AutopilotWorker._source_entry_candidates(fake_self))


def test_ladder_on_uses_snapshot_not_scan():
    print("\n[1] LADDER on → cadence candidate from snapshot, sniper scan bypassed")
    os.environ["LADDER_MODE"] = "true"
    _install_snapshot({"ticker": "NIFTY", "price": 24350.5, "coi_pcr": 1.42, "bias": "BULLISH"})
    fs = _FakeSelf()
    out = _source(fs)
    check("returns exactly one candidate", isinstance(out, list) and len(out) == 1)
    c = out[0] if out else {}
    check("candidate ticker NIFTY", c.get("ticker") == "NIFTY")
    check("spot pulled from snapshot", abs(float(c.get("spot_price", 0)) - 24350.5) < 1e-6)
    check("pcr pulled from snapshot coi_pcr", abs(float(c.get("coi_pcr", 0)) - 1.42) < 1e-6)
    check("tagged LADDER_CADENCE", c.get("pa_status") == "LADDER_CADENCE")
    check("directional sniper scan NOT called", fs.engine.called is False)


def test_ladder_off_uses_sniper_scan():
    print("\n[2] LADDER off → sniper analyze_universe drives entries (unchanged)")
    os.environ["LADDER_MODE"] = ""
    _install_snapshot({"ticker": "NIFTY", "price": 24350.5, "coi_pcr": 1.42})
    fs = _FakeSelf()
    out = _source(fs)
    check("sniper scan WAS called", fs.engine.called is True)
    check("returns the scan output (sentinel)", bool(out) and out[0].get("_sniper_sentinel") is True)


def test_ladder_on_no_snapshot_skips():
    print("\n[3] LADDER on + no/empty snapshot → skip cleanly, no exception")
    os.environ["LADDER_MODE"] = "true"
    _install_snapshot(None)
    check("missing snapshot → []", _source(_FakeSelf()) == [])
    _install_snapshot({"ticker": "NIFTY", "price": 0, "coi_pcr": 1.0})
    check("zero/blank spot → []", _source(_FakeSelf()) == [])


def test_candidate_feeds_desk_and_ladder_gate():
    print("\n[4] Cadence candidate → options desk → evaluate_ladder (no directional gate)")
    os.environ["LADDER_MODE"] = "true"
    _install_snapshot({"ticker": "NIFTY", "price": 24000.0, "coi_pcr": 1.40})
    cand = _source(_FakeSelf())
    spreads = options_desk_service.process_approved_assets(cand, strategy_mode="CREDIT_SPREAD")
    check("desk builds one spread from candidate", len(spreads) == 1)
    sp = spreads[0]
    check("spread is a credit spread", sp.get("strategy_type") == "BULL_PUT_SPREAD")
    check("spot carried through to spread", abs(float(sp.get("spot_price", 0)) - 24000.0) < 1e-6)

    # inject regime state (mirror test_phase4_wiring) so no bhavcopy load happens
    n = 30
    regime_service._closes = [24000.0 / (1.0005 ** (n - i)) for i in range(n)]
    regime_service._iv_hist = [0.13] * 80
    regime_service._built_for = datetime.date.today()
    regime_service._underlying = "NIFTY"
    side, mult, reason = regime_service.evaluate_ladder(
        ticker="NIFTY",
        pcr=float(sp.get("coi_pcr", 1.0)),
        spot=float(sp.get("spot_price", 0)),
        live_iv=0.13,
    )
    check(f"ladder gate returns a tradeable side ({side})", side in ("BULL_PUT_SPREAD", "BEAR_CALL_SPREAD"))
    check(f"ladder gate returns a size mult ({mult})", isinstance(mult, float) and mult > 0)


class _FakeResult:
    def __init__(self, val):
        self._val = val

    def scalar(self):
        return self._val


class _FakeSession:
    """Returns the supplied count for each .execute() in order (n_open)."""
    def __init__(self, counts):
        self._counts = list(counts)
        self._i = 0

    async def execute(self, *a, **k):
        v = self._counts[self._i] if self._i < len(self._counts) else 0
        self._i += 1
        return _FakeResult(v)


def _install_redis():
    async def fake_get_json(key):
        return None   # no option chain -> refine skips; no snapshot needed here
    async def fake_set_json(key, val, **k):
        return True
    redis_service.get_json = fake_get_json
    redis_service.set_json = fake_set_json


def _inject_regime_state():
    n = 30
    regime_service._closes = [24000.0 / (1.0005 ** (n - i)) for i in range(n)]
    regime_service._iv_hist = [0.13] * 80
    regime_service._built_for = datetime.date.today()
    regime_service._underlying = "NIFTY"


def _built_spread():
    cand = [{"ticker": "NIFTY", "spot_price": 24000.0, "coi_pcr": 1.40,
             "ml_score": 0.5, "pa_status": "LADDER_CADENCE",
             "learning_context": {"PA_Status": "LADDER_CADENCE"}, "recommended_lots": 1}]
    return options_desk_service.process_approved_assets(cand, strategy_mode="CREDIT_SPREAD")[0]


def test_shared_source_module():
    print("\n[5] ladder_entry.source_ladder_candidate (shared live source)")
    from backend.app.services.ladder_entry import source_ladder_candidate
    _install_snapshot({"ticker": "NIFTY", "price": 24111.0, "coi_pcr": 1.33})
    out = asyncio.run(source_ladder_candidate())
    check("builds one candidate from snapshot", len(out) == 1 and out[0]["ticker"] == "NIFTY")
    check("pcr from snapshot", abs(out[0]["coi_pcr"] - 1.33) < 1e-6)
    _install_snapshot(None)
    check("no snapshot -> []", asyncio.run(source_ladder_candidate()) == [])


def test_apply_ladder_gate():
    print("\n[6] ladder_entry.apply_ladder_gate (shared live gate)")
    from backend.app.services import ladder_entry
    _inject_regime_state()

    # pass: no open positions
    _install_redis()
    sp = _built_spread()
    ok, reason = asyncio.run(ladder_entry.apply_ladder_gate(_FakeSession([0]), sp))
    check(f"clean gate -> proceed ({reason})", ok is True)
    check("side assigned by evaluate_ladder", sp.get("strategy_type") in ("BULL_PUT_SPREAD", "BEAR_CALL_SPREAD"))
    check("IVR size mult attached", "_ivr_size_mult" in sp)

    # single-position guard: a position is already open
    _install_redis()
    ok2, r2 = asyncio.run(ladder_entry.apply_ladder_gate(_FakeSession([1]), _built_spread()))
    check(f"open-position guard blocks ({r2})", ok2 is False and "already open" in r2)


def test_select_ladder_expiry():
    print("\n[7] trading_mode.select_ladder_expiry (30-45 DTE with gap fallback)")
    from trading_mode import select_ladder_expiry
    today = datetime.date(2026, 7, 6)

    # real NIFTY list on 2026-07-06 — NOTE the 30-45 DTE gap (29 then 50)
    real = ["2026-07-07", "2026-07-14", "2026-07-21", "2026-07-28", "2026-08-04",
            "2026-08-25", "2026-09-29", "2026-12-29"]
    exp, dte, why = select_ladder_expiry(real, today=today)
    check(f"gap -> closest holdable 2026-08-04/29DTE not 1DTE ({exp} {dte} {why})",
          exp == "2026-08-04" and dte == 29 and why == "closest_holdable")
    check("never the instantly-managed near weekly", exp != "2026-07-07")

    # when a real 30-45 expiry exists, prefer it
    win = ["2026-07-07", "2026-08-11", "2026-08-25"]   # 08-11 = 36 DTE
    exp2, dte2, why2 = select_ladder_expiry(win, today=today)
    check(f"in-window preferred ({exp2} {dte2} {why2})",
          exp2 == "2026-08-11" and dte2 == 36 and why2 == "in_window")

    # only near weeklies (all <= manage 21) -> last-resort nearest
    near = ["2026-07-07", "2026-07-14", "2026-07-21"]
    exp3, dte3, why3 = select_ladder_expiry(near, today=today)
    check(f"all <=manageDTE -> fallback_nearest ({exp3} {why3})", why3 == "fallback_nearest")

    check("empty list -> no_expiries", select_ladder_expiry([], today=today)[2] == "no_expiries")


if __name__ == "__main__":
    try:
        test_ladder_on_uses_snapshot_not_scan()
        test_ladder_off_uses_sniper_scan()
        test_ladder_on_no_snapshot_skips()
        test_candidate_feeds_desk_and_ladder_gate()
        test_shared_source_module()
        test_apply_ladder_gate()
        test_select_ladder_expiry()
    finally:
        os.environ["LADDER_MODE"] = ""
    print(f"\n{'=' * 50}\nRESULT: {PASS} passed, {FAIL} failed")
    sys.exit(1 if FAIL else 0)
