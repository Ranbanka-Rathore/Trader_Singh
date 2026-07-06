---
phase: 7
name: ladder-live-entry-path
goal: >
  Give the income ladder its own cadence-driven live entry path that runs through
  regime_service.evaluate_ladder ONLY, so LADDER_MODE actually trades instead of
  being silently blocked by the sniper's directional scan + directional ML guard.
created: 2026-07-06
status: ready
autonomous: false          # touches live order-creation path — human verifies paper fills
trading_mode_required: PAPER
files_modified:
  - backend/app/services/ladder_entry.py   # new — shared source-of-truth
  - run_quant.py                           # live: source ladder candidate
  - run_risk_committee.py                  # live: ladder gate + advisory committee + execute
  - backend/app/services/worker.py         # monolith: delegate to shared module
  - tests/test_ladder_live_entry.py        # new
depends_on:
  - Phase 6 (commit b03016a — ladder wired behind LADDER_MODE, never fired live)
requirements:
  - LAD-1  Ladder entries must NOT depend on analyze_universe's directional trigger/ML guard
  - LAD-2  Ladder entries gated ONLY by evaluate_ladder + validated hard gates (risk audit, event blackout, credit floor, cadence, max-open, portfolio max-loss)
  - LAD-3  Sniper path (LADDER_MODE off) must be byte-for-byte unchanged
  - LAD-4  A NIFTY tranche books in paper within one session under normal conditions
---

# Phase 7 — Ladder Live Entry Path

## ⚠ CORRECTION (2026-07-06 ~12:00 IST) — re-targeted to the LIVE services

The first cut of this plan (commit 1bccec4) targeted `worker.py::AutopilotWorker`,
assuming it was the live entry path. **It is not.** `start_v8.bat` launches a
microservice split and never launches `run_worker.py`, so `worker.py` is dead code
for this deployment. The live entry path is:

- **`run_quant.py`** — loops `analyze_universe(['NIFTY'])`, publishes survivors to
  Redis `quant_signals`.
- **`run_risk_committee.py`** — subscribes `quant_signals` → options desk → committee
  → `execute_trade`.
- **`run_oms.py`** — exits only.

None of these referenced `evaluate_ladder`/`ladder_enabled`/`regime_service` before
this phase. The ladder logic is now in a **shared module** (`ladder_entry.py`) used by
both the live services and `worker.py`, so the two can never drift again — which is
what hid the ladder from the live deployment through Phase 6.

## Root cause (found live 2026-07-06 ~09:30 IST paper session)

`worker.py::run_cycle` sources entry candidates from
`self.engine.analyze_universe(self.universe)` (`worker.py:211`) **for both** the
sniper and the ladder. `analyze_universe` is the sniper's directional pipeline:

```
directional PA trigger → OBI(±0.15) → PCR band[0.7,1.8] → GEX vol(-200M)
  → directional ML win-prob guard(cutoff 0.65) → PCR-stability/trend → cooldown
```

Only survivors reach `options_desk` → the `if ladder_enabled():` block
(`worker.py:225`) → `evaluate_ladder`. So the ladder's validated gate sits
**downstream of a directional gauntlet it was never designed to clear.** On
2026-07-06 the live tape blocked in sequence: PCR 1.90 > 1.80, then GEX −275M <
−200M, then ML score 0.015 < 0.65. An income ladder must not be gated by a
**directional** win-probability model — that is the bug. This is why `LADDER_MODE`
has never actually traded live.

## Design decision

Branch the candidate **source** on `ladder_enabled()`, not just the gate:

- **LADDER_MODE on:** build the NIFTY ladder candidate **directly from
  `market_snapshot:NIFTY`** (published every cycle by `data_service`, already
  carries `price`, `coi_pcr`, `bias`) → feed one synthetic `asset_data` dict to
  `options_desk_service.build_bull_put_spread` → run the **existing** ladder block
  (`worker.py:225-347`) which already overrides side/bias/size from
  `evaluate_ladder`. Skip `analyze_universe` entirely.
- **LADDER_MODE off:** unchanged — call `analyze_universe`, run the sniper path.

Why this is minimal & safe:
- The ladder entry/exit/sizing block (`worker.py:225-347`) **already exists and is
  validated** — we only change what feeds it.
- All pre-entry safety runs *before* the source branch and is preserved for both
  paths: `evaluate_open_positions` (:158), `perform_risk_audit` kill-switch (:168),
  `delta_hedger` (:177), max-daily-trades (:185), the market-hours time-guard (:206).
- `evaluate_ladder` (`regime_service.py:140`) keeps the validated hard gates:
  warmup, event blackout, PCR+EMA side, IVR sizing. `execute_trade` keeps the
  credit floor + 10% portfolio max-loss cap.
- Side seed is irrelevant: `build_bull_put_spread` seeds `BULL_PUT_SPREAD`, but the
  ladder block reassigns `spread["strategy_type"]`/`bias` from `evaluate_ladder`'s
  returned `side` before pricing (`worker.py:266-267`).

---

## Task 1 — Branch candidate source on `ladder_enabled()` in `run_cycle`

<action>
In `backend/app/services/worker.py`, in `run_cycle`, replace the single entry
source (currently `worker.py:206-218`) so that when the market-hours time-guard
allows entries AND `ladder_enabled()` is true, the candidate is built directly
from the market snapshot instead of the directional scan.

Concretely, inside the `else:` branch that currently runs
`passed = await self.engine.analyze_universe(self.universe)`:

1. `from trading_mode import ladder_enabled`
2. If `ladder_enabled()`:
   - `snap = await redis_service.get_json("market_snapshot:NIFTY")`
   - If `snap` is falsy or `float(snap.get("price", 0)) <= 0`:
     `logger.info("🪜 [Ladder] no NIFTY snapshot yet — skipping cycle")` and set
     `passed = []`
   - Else build ONE candidate asset dict and set `passed = [asset]`:
     ```python
     spot = float(snap.get("price", 0) or 0)
     pcr = float(snap.get("coi_pcr", snap.get("pcr", 1.0)) or 1.0)
     asset = {
         "ticker": "NIFTY",
         "spot_price": spot,
         "coi_pcr": pcr,
         "ml_score": 0.5,   # ladder does not use a directional ML gate
         "pa_status": "LADDER_CADENCE",
         "learning_context": {"PA_Status": "LADDER_CADENCE"},
         "recommended_lots": 1,   # evaluate_ladder IVR mult + position_sizer set final size
     }
     passed = [asset]
     logger.info(f"🪜 [Ladder] cadence candidate built | spot {spot} | pcr {pcr}")
     ```
3. Else (`ladder_enabled()` false): unchanged —
   `passed = await self.engine.analyze_universe(self.universe)`

Do NOT touch the time-guard block (`worker.py:206-208`) — the ladder still only
enters 09:15–15:00 on weekdays (or dev-mode out-of-hours), which is correct.
Do NOT touch the `if passed:` block below (`worker.py:213+`) — the existing
`if ladder_enabled():` sub-branch (`worker.py:225-347`) consumes `structured_spreads`
unchanged.
</action>

<read_first>
- backend/app/services/worker.py            # run_cycle 150-360: source, time-guard, ladder block
- backend/app/services/data_service.py      # 355-370: snapshot keys published (coi_pcr, bias, price)
- backend/app/services/options_desk_service.py  # 18-55: asset_data fields build_bull_put_spread reads
- backend/app/services/regime_service.py    # 140-200: evaluate_ladder inputs/returns
- trading_mode.py                           # ladder_enabled(), LADDER_* constants
</read_first>

<acceptance_criteria>
- `worker.py` contains `from trading_mode import ladder_enabled` inside run_cycle's entry-source block
- `worker.py` contains the string `market_snapshot:NIFTY` inside the ladder source branch
- `worker.py` contains `"pa_status": "LADDER_CADENCE"` (grep-verifiable candidate marker)
- The call `self.engine.analyze_universe(self.universe)` still exists but is now inside an `else` (sniper-only) branch — `grep -n analyze_universe backend/app/services/worker.py` shows exactly one call site
- `python -c "import ast,sys; ast.parse(open('backend/app/services/worker.py').read())"` exits 0
</acceptance_criteria>

---

## Task 2 — Confirm the ladder block consumes the direct candidate unchanged

<action>
No code change expected — verification task. Read `worker.py:220-347` and confirm:
- `options_desk_service.process_approved_assets([asset], strategy_mode)` returns one
  spread for the synthetic asset (strategy_mode defaults to CREDIT_SPREAD →
  build_bull_put_spread, which tolerates ml_score/pa_status defaults).
- The cadence guard (`ladder_last_entry_week`, `worker.py:231`), DB entries-this-week
  guard (:244), and max-open guard (:247) still fire against the synthetic candidate.
- `evaluate_ladder` is called with `pcr`/`spot` from the candidate and reassigns
  `spread["strategy_type"]`/`bias`/`_ivr_size_mult` (:266-268) before
  `refine_spread_with_chain` (:273) and `execute_trade`.

If `process_approved_assets` or the ladder block rejects the synthetic candidate
(e.g. a required key raises KeyError), add ONLY the missing key to the Task 1 asset
dict — do not alter the ladder block itself.
</action>

<read_first>
- backend/app/services/worker.py                 # 220-347 ladder block
- backend/app/services/options_desk_service.py   # 332-359 process_approved_assets
- backend/app/services/options_pricing_service.py # 177-210 refine_spread_with_chain
</read_first>

<acceptance_criteria>
- No new exceptions introduced: the ladder block references only keys present in the Task 1 asset dict (`ticker`, `spot_price`, `coi_pcr`, `ml_score`, `pa_status`, `learning_context`, `recommended_lots`)
- `worker.py:225` `if ladder_enabled():` block is unmodified except (if needed) added keys in Task 1
</acceptance_criteria>

---

## Task 3 — Unit test: ladder enters on cadence without a directional signal

<action>
Create `tests/test_ladder_live_entry.py` mirroring the existing suite style
(`tests/test_phase4_wiring.py`, run with `PYTHONUTF8=1`). Use a fake engine whose
`analyze_universe` raises or returns [] (to prove the ladder path does NOT call it),
a fake Redis returning a `market_snapshot:NIFTY` with `price`/`coi_pcr`, and a fake
execution path. Assert:

1. **LADDER_MODE on + no directional signal → tranche candidate reaches evaluate_ladder.**
   Monkeypatch `os.environ["LADDER_MODE"]="true"`, stub `market_snapshot:NIFTY`,
   spy on `regime_service.evaluate_ladder`; assert it is called with `ticker="NIFTY"`
   and the snapshot's spot/pcr, and that `engine.analyze_universe` is NOT called.
2. **LADDER_MODE off → sniper path.** `LADDER_MODE` unset; assert
   `engine.analyze_universe` IS called and `evaluate_ladder` is NOT.
3. **Cadence guard.** With `ladder_last_entry_week` == current ISO week, assert no
   entry is attempted (execute_trade spy not called).
4. **No-snapshot safety.** Snapshot missing/price 0 → cycle skips cleanly, no exception.

Follow the monkeypatch/fake patterns already in tests/ (do not hit live Redis/Dhan/PG).
</action>

<read_first>
- tests/test_phase4_wiring.py        # fake-service + monkeypatch patterns to mirror
- tests/test_options_pricing.py      # fake chain/redis patterns
- backend/app/services/worker.py     # run_cycle under test
</read_first>

<acceptance_criteria>
- `PYTHONUTF8=1 ./venv/Scripts/python.exe -m pytest tests/test_ladder_live_entry.py -q` exits 0
- Test file asserts `evaluate_ladder` called AND `analyze_universe` NOT called when LADDER_MODE=true
- Test file asserts the inverse when LADDER_MODE unset
- Existing suites still green: `PYTHONUTF8=1 ./venv/Scripts/python.exe -m pytest -q` (138+ assertions across 5 suites) exits 0
</acceptance_criteria>

---

## Task 4 — Live paper verification (human-in-loop, market hours)

<action>
With `.env` = `TRADING_MODE=PAPER, LADDER_MODE=true, TRADING_EQUITY=1500000`,
restart `start_v8.bat` during market hours (09:15–15:00 IST) and watch
`logs/trader_singh.log` / the Execution OMS window for the ladder to enter WITHOUT
the sniper filters blocking. Confirm the full plumbing: `🪜 [Ladder] cadence
candidate built` → `🪜 [Ladder] NIFTY <side> tranche | <reason> | size mult` →
`Strikes refined` → `BASKET FILLED` → one row in the `order_audit` table → position
visible in War Room. Then trigger one panic square-off and confirm `friction_costs`
booked. Expect exactly ONE tranche this week (cadence guard blocks repeats).
</action>

<read_first>
- check_health.py            # confirm 🪜 LADDER ON / ₹15L / PAPER before restart
- logs/trader_singh.log      # worker + risk committee + ladder logs
- logs/quant.log             # confirm sniper filters are bypassed (not gating the ladder)
</read_first>

<acceptance_criteria>
- `logs/trader_singh.log` shows `🪜 [Ladder] ... tranche` followed by `BASKET FILLED` within one session
- One new `order_audit` row exists for the NIFTY tranche
- Position appears in War Room; panic square-off books a row with non-null friction_costs
- NO `SIGNAL BLOCKED ... PCR / GEX / ML Guard` line gates the ladder entry (those are sniper-only now)
- `brain_config.json` is git-clean throughout (no filter loosening required)
</acceptance_criteria>

---

## Verification (phase-level, goal-backward)

- [ ] LAD-1: `analyze_universe` has exactly one call site, inside the `else` (sniper) branch
- [ ] LAD-2: ladder entry path calls only `evaluate_ladder` + validated hard gates; no directional PCR/GEX/ML block reachable from it
- [ ] LAD-3: with `LADDER_MODE` unset, `git diff` of runtime behavior on the sniper path is nil (test 2 proves analyze_universe still drives it)
- [ ] LAD-4: a NIFTY tranche books in paper within one session (Task 4)
- [ ] Full test suite green including new `test_ladder_live_entry.py`

## must_haves

1. Ladder trades live on cadence without a directional trigger (the whole point).
2. Sniper path untouched when LADDER_MODE off.
3. No safety regression: risk-audit kill switch, event blackout, credit floor,
   10% portfolio max-loss cap, max-open, cadence guard all still apply to the ladder.
4. No `brain_config.json` filter loosening — the fix removes the *need* for it.

## Risks & rollback

- **Risk:** the ladder now enters on cadence regardless of directional edge — this is
  intended (validated at ₹15L, WF e1bbdc4) but means it WILL take a weekly tranche in
  regimes the sniper would skip. Mitigated by evaluate_ladder's event/warmup hard gates
  and the credit floor.
- **Risk:** `market_snapshot:NIFTY` staleness → building a candidate on a stale spot.
  Mitigated by the price>0 guard; consider adding a snapshot-age check (< 60s) if the
  harvester ever lags (follow-up, not blocking).
- **Rollback:** single-file behavioral change guarded by `ladder_enabled()`. Set
  `LADDER_MODE=` (unset) in `.env` and restart → reverts to the sniper path instantly,
  no code revert needed. Full revert = `git revert` the worker.py commit.

## Out of scope (deferred)

- Snapshot-age freshness gate (< 60s) — nice-to-have hardening.
- Multi-expiry / calendar ladder variants (Phase 5 UNVALIDATED).
- ML retrain on ladder SignalAudit outcomes (needs paper trades to accumulate first).
- Iron condor ladder (Phase 5 REJECTED by OOS).
