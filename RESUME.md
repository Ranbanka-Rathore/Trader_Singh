# RESUME HERE — Phase 2, after the first intraday screen

**Written 2026-08-14 (session ran ~12:50–17:00 IST, market open then closed).**
Supersedes the 2026-08-13 handoff, which is preserved at commit `40a8dc4`.

---

## 0. TL;DR of what changed today

| | |
|---|---|
| Data probe | **Answered.** Expired option history is NOT retrievable. Index is 5+ yrs. |
| Capital | **Rs 50k–1L, staged** — added once the system is developed. Rs 10,111 funded now. |
| Charter | **Amendment E** written, and bound in `research/charter.py` + 10 tests. |
| Archive | **3.98M 1-min bars** on disk, 577 contracts. Aug-18 expiry secured. |
| Arena 5 | **Screen 1 KILLED** — index IS predictable; the edge is smaller than the spread. |
| Registry | **10 registered, 10 killed.** |
| Tests | **856 checks, 0 failures.** |

**The single most important number found today:**

```
predictable move, best cell (h=60)   2.995 index points
round-trip spread at 0.75/leg        3.000 index points   (at ATM delta 0.5)
                                     ratio 0.998
```

The intraday edge is real, sign-stable across four years — and almost exactly
equal to the cost of crossing the book. **Caveat that matters: the 0.75/leg is
the project's assumed default, not a measured NIFTY spread.** The near-exact
equality is therefore partly an artifact of an assumed number, which is what
makes §4 below the obvious next step rather than a nice-to-have.

---

## 1. What was answered, and what it cost to answer

### Step 1 — the data probe (RESUME §5 of the old handoff)

| series | what Dhan serves |
|---|---|
| NIFTY index 1-min | **5+ years** (probed to 2021) |
| NIFTY futures 1-min | **~75 days, rolling purge** |
| live option 1-min | from the contract's **first trade** to now |
| **expired option 1-min** | **nothing, permanently** |

The expired probe was built to be unarguable: the four security ids came from
this project's own `order_audit` — known contracts, known trading days — and a
live control on the identical call returned 1,540 bars. All four returned 0, at
both 10 and 38 days past expiry.

**Consequence:** the plan's "persist live ticks" was the wrong mechanism. A live
contract keeps its whole history until expiry, so the right mechanism is a
**daily archival job**, and it is *retroactive* — it recovered history the old
plan had written off as already lost.

Also learned: Dhan caps a single call at **90 days** (DH-905), but within that
limit one call returns a contract's entire life, which is what makes archiving
the whole live board affordable.

### Step 2 — the archive (`backtest/intraday_archive.py`)

3,980,747 bars across 577 contracts in 11 minutes. `--report` gives the quality
report; 595 contracts returned `empty` and are recorded as such rather than
skipped (they never traded — consistent with the known NIFTY liquidity picture).

> **OPERATIONAL, AND THE ONLY THING WITH A DEADLINE:**
> **run `python -m backtest.intraday_archive --board --band 15 --expiries 4`
> before each Tuesday expiry.** The next is **2026-08-18**. Contracts purge at
> expiry and cannot be recovered. Ideally run it daily; it is 11 minutes.

### Step 3 — Amendment E

Written, and *bound in code* rather than left in prose: `DRAWDOWN_BUDGET_RS`
(a Rs 1,00,000 figure that was 6.67% of a capital base the operator never chose)
is now `DRAWDOWN_BUDGET_PCT = 0.15` with `drawdown_budget_rs(equity)` deriving
it; the old constant is `None` so stale readers fail loudly. Three call sites
converted. Three intraday arenas added to `ARENAS`. Ten tests bind all of it.

E's measured findings, which constrain everything downstream:
- **E5:** friction takes **49–62%** of the gross edge a 15% CAGR needs, and
  barely improves across Rs 50k–1L, because flat brokerage does not scale with
  the net target. **Frequency is the lever, not capital.**
- **E3:** at this capital the project **cannot be justified by returns**
  (Rs 11,250/yr vs a Rs 5,250 FD, for 780 hours). It is justified only as a
  validated system tested where being wrong is cheap.

### Step 4 — Arena 5, screen 1 (`intraday-ic-modern`)

Registered at `340f4f1` **before** it ran. 370,626 bars, 992 trading days,
2022-08-16 → 2026-08-14. Four signals × four horizons = 16 cells.

- **(a) significance: 8 of 16 cells PASSED** after correcting |t| for the
  overlap of forward windows (without that correction every cell passes on
  sample size alone — the correction is doing real work).
- **(b) sign consistency: all 8 PASSED.** `vwap_dev` and `or_pos` at h=60 both
  reach IC ≈ 0.053 with the same sign in every year 2022–2026.
- **(c) economics: 0 of 8 passed.** Best cell grosses Rs 97/lot against Rs 147
  of round-trip cost. **At zero spread it still only nets Rs 48**, and every
  cell at h ≤ 30 loses money even then.

**Intraday NIFTY is not a random walk. The predictability is simply smaller than
the spread.** That is a *measured absence*, the same shape as Arenas 1 and 4 —
not an absence of evidence. A larger sample would measure the same too-small
effect more precisely.

**Two errors caught in-flight, both recorded in the kill log** because they are
the kind that get reported as discoveries:

1. **A lookahead artifact.** A fifth signal, `rvol_ratio`, was initially the
   *strongest* result (IC 0.084, t 5.75, sign-consistent every year). Its
   normaliser was the session median of realised vol over the **whole session**,
   so the 10:00 value knew 15:00. With a past-only expanding median it fails at
   every horizon and leaves the screen entirely.
2. **A permissive economic test.** The first version of condition (c) compared
   gross against the CAGR target **without subtracting friction at all** and
   "advanced" three cells that lose money on every trade. It now prices
   statutory charges *and* the spread, and sweeps the spread 0 → 1.50/leg so the
   verdict rests on no single assumed number.

---

## 2. What still binds, and is not to be re-run

- Arena 1 (weekly short vol) and Arena 4 (earnings straddles): **closed,
  size-independent**, unaffected by the capital correction.
- Arenas 2 and 3: **closed**, and unaffordable at this capital regardless
  (~Rs 4.4L minimum for NIFTY credit spreads).
- Arena 5's **unconditional single-signal** idea: dead as of today.
- The traps list in §6 below. Every item on it was paid for once already.

---

## 3. Registry state

10 registered, 10 killed, 0 survivors. `python -m research.loop list`.

Arena `intraday_index` is **not closed** — Section 7 leaves closure an operator
decision and fixes no screen allotment. Screen 1 closes one specific idea.

---

## 4. THE NEXT STEP, and why it is this one

**Measure the actual NIFTY option bid-ask spread.**

The entire Arena 5 verdict turns on a cost number the project has never
measured. `0.75/leg` is a default in `real_backtester.Config`, not an
observation. The sensitivity table shows exactly how much rides on it:

| spread/leg | best cell, net Rs/trade |
|---|---|
| 0.00 | +47.82 |
| 0.10 | +34.82 |
| 0.25 | +15.32 |
| **~0.36** | **breakeven** |
| 0.50 | −17.18 |
| 0.75 | −49.68 |

If the real spread on tradeable NIFTY options is under ~0.36/leg, screen 1's
kill is wrong and must be revisited. If it is 0.75 or worse, the kill is right
and it also closes most of what Arenas 6 and 7 could propose — because they all
have to cross the same book.

**This is what the deferred live tick recorder is for**, and it has been promoted
from "nice for fill realism later" to the critical path. 1-min OHLC carries no
bid/ask; only a live feed does. The harvester already receives ticks and drops
them into 90-second Redis TTLs (`market_data_service._process_tick` →
`data_service.ingest_tick`); it currently subscribes **only NIFTY index, secid
13, quote mode**, so option contracts would need adding.

Do this before designing any further screen. It is one measurement that either
reopens the arena or closes most of Phase 2 honestly.

**Not a blocker (resolved 2026-08-14):** the Rs 10,111 broker balance is not a
gap. Capital is **staged** — the operator adds the Rs 50k–1L once the system is
developed. Funding follows validation, which is exactly what Amendment E3 says
the project is for, and is why E2's thresholds are percentages rather than rupee
figures. Nothing before the Section 5 gate needs the money present. It does mean
a promotion earned at nominal size is a promotion to trade at *that* size — not
a licence to relax a gate because the real money is not in yet.

---

## 5. System state as left on 2026-08-14

| | |
|---|---|
| `main` | `634e821`, clean tree, **not yet pushed** |
| tests | **856 checks, 0 failures**, 20 files (`python tests/run_all.py`) |
| services | **all stopped** except WSL Postgres, which this session started |
| `TRADING_MODE` | `PAPER` |
| live entries | refused — zero promotions on record |
| archive | `data/intraday/` — 3.98M bars; `manifest.json` is the index |
| Dhan token | expires **2026-08-15 12:51 IST** — refresh before next session |

Postgres/Redis are in WSL at 172.26.128.109; `wsl -d Ubuntu -u root service
postgresql start`. Windows→WSL TCP was flaky this session; `wsl -d Ubuntu -u
postgres psql -d agentic_trader -c "..."` works when the TCP path does not.

To restart the full stack: `start_v8.bat`, then `python check_health.py`.

---

## 6. Traps this project has already paid for — do not re-learn them

- **Lookahead hides in the normaliser, not the signal.** `rvol_ratio` used a
  whole-session median and became the best signal in the screen. Any statistic
  computed per-session must be `expanding()`, never a session-wide aggregate.
- **Overlapping forward windows inflate |t| by ~sqrt(h).** Uncorrected, all 16
  cells clear Section 4 on sample size alone.
- **friction_model carries NO bid/ask.** It prices brokerage and statutory
  charges only. On a cheap option the spread is the *larger* half of the cost.
- **The engine's defaults are shaped around vertical credit spreads.** Six
  config knobs silently did nothing on other structures.
- **Never accept an aggregate fill/skip rate as evidence.** Decompose by *kind*
  of refusal. Arena 4's "capacity" problem was 45% a risk cap, 12% illiquidity.
- **When a short structure loses, compute the MID-PRICE P&L before proposing the
  long side.** The spread is paid in both directions.
- **A log file's recency is not evidence a service ran.** Check the logger name.
- **The two-era bhavcopy schema break bites repeatedly.** Use `strict_legacy` on
  any window starting 2023-01-01.
- **Pre-registration works.** It is the only reason today's negative result
  cannot be re-read later as "intraday momentum works".
