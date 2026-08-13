# RESUME HERE — Phase 2 (intraday), opening on corrected premises

**Written 2026-08-13. Next session: 2026-08-14, during live market hours, with a
fresh Dhan token.**

---

## 1. Why we are continuing after Section 7 was invoked

Phase 1's survey completed and reached its named failure branch: 9 hypotheses,
9 killed, 0 survivors, all four arenas closed. That verdict stands and is
recorded in `RESEARCH_CHARTER.md`.

**We are not ignoring it. A founding premise turned out to be wrong.**

The charter's capital figure — Rs 15,00,000 — was never chosen by the operator.
It entered the repo on 2026-07-06 as `TRADING_EQUITY=1500000`, traced back to a
capital-sizing *recommendation* an earlier assistant session made for the ladder
("Rs 5L = proof-of-life, Rs 15-20L = ladder income"), and was written into the
charter on 2026-08-08 as an axiom. Everything downstream came from it: the 15%
CAGR target, the Rs 2.25L/year goal, the Rs 1,00,000 drawdown budget ("6.67% of
Rs 15L"), and the capacity criterion.

Section 7 says that at the review point "the project is reassessed against this
document as written". Reassessing a document whose first premise was a mistake is
exactly that, not an evasion of it.

> **ACTION REQUIRED: the charter needs a formal Amendment E recording the capital
> correction and the new targets. Do not start research before it is written.**
> Silently proceeding on new premises is how the last set of premises went
> unexamined for five days.

---

## 2. What Phase 1 established that STILL BINDS — do not re-run these

Two of the four closures are size-independent and remain true at any capital:

- **Weekly NIFTY short volatility loses BEFORE friction.** 377 trades across an
  ATM butterfly and a 0.18-delta condor. The +2.38 vol-point premium is payment
  for a left tail, not a mispricing. More capital loses more.
- **Single-stock earnings straddles are priced approximately fair.** Mid-price
  premium **-Rs 174/trade** across 246 events. Both directions lose at *every*
  slippage level including zero, because a 4-leg round trip costs an order of
  magnitude more than the premium.

Two closures were partly artifacts of the wrong capital figure:

- **Cross-sectional** required IC 0.054 only because Rs 15L + F&O lot sizes
  forced a 2+2-name book. At 5+5 names it is 0.044; at zero cost 0.035. Best
  measured candidate was 0.0359 across 12 signals. Closed on search budget, not
  on signal space.
- **Trend** closed because detection threshold ≈ profitability threshold.

**At under Rs 5L none of the above is reachable anyway** — Phase 5 already found
the honest minimum for NIFTY credit spreads is ~Rs 4.4L, and 4-leg structures
need margin this account does not have. The old arenas are closed *and*
unaffordable. Phase 2 is a different space, not a retry.

---

## 3. The three answers that define Phase 2

| | |
|---|---|
| **Capital** | **Under Rs 5L** — exact figure STILL NEEDED, see §6 |
| **Data** | Paid intraday history — but check Dhan first, see §5 |
| **Target** | *"remain profitable on a monthly basis, whatever that may be"* |

### What the monthly target actually requires — know this before setting a bar

| profitable months | annual Sharpe needed |
|---|---|
| 9 of 12 (75%) | **2.34** |
| 10 of 12 (83%) | **3.35** |
| 11 of 12 (92%) | 4.79 |
| 12 of 12 | 10.7 — not achievable by anything |

The old charter's Sharpe-1.0 bar produces a profitable month only **61%** of the
time. Literally every month is not a target any strategy can hold.

**Read the answer as a preference, not a number:** low variance and short
drawdowns matter more than peak return. That is a real design constraint and it
points at many small trades with tight risk, not few large ones. **Proposed bar
for Amendment E: annual Sharpe >= 2.3 (~75% of months profitable).** Demanding,
but not absurd intraday, where sample size finally works for us rather than
against.

---

## 4. Why intraday, and the number that justifies it

Every Phase 1 hypothesis used **end-of-day bhavcopy**. Intraday is not merely
untested — there is no intraday history stored anywhere. `data/` holds only EOD
bhavcopy and the events calendar, there are no candle or tick tables in the
schema, and the harvester publishes ticks to Redis with a 90-second TTL and then
discards them. The live system is built entirely around intraday and has zero
intraday research behind it.

**The cost arithmetic, from the project's own friction model** (`friction_model`,
NIFTY lot = 65 from the scrip master):

| trade | friction vs the edge it chases |
|---|---|
| 4-leg earnings straddle | Rs 3,994/trade against a Rs 174 edge — **23x** |
| weekly condor | negative *before* friction |
| **1-lot intraday long option** | **Rs 66 round trip = ~1.0 index point** |

A 20-point premium move on 1 lot is Rs 1,300 gross, so friction is ~**5%** of it.
The EOD structures died harvesting a tiny edge (2 vol points) with an expensive
vehicle (4 legs). A 1-2 leg intraday trade chases 10-30% of the instrument's
price with ~1 point of cost. Roughly a 100x better edge-to-cost ratio, and long
options need **no margin**, so under Rs 5L is workable rather than marginal.

**Honest prior:** intraday index direction is the most competed space in this
market — HFT and prop desks live there. But the structural bet in Section 8 gets
*stronger* at this size, not weaker: a Rs 5L participant trading 1-2 lots is
genuinely beneath anyone's notice. At Rs 15L that argument was strained; here it
is real.

---

## 5. THE PLAN — in order, starting tomorrow

### Step 1 — Probe what intraday data already exists (do FIRST, needs token)

`dhan_integration.py:617` already implements `get_historical_intraday()`, and the
SDK exposes `intraday_minute_data` and `historical_daily_data`. **The data may
already be free on the existing account.**

Probe, and record the answers here:

- [ ] NIFTY **spot/index** — how many days/years of 1-min bars come back?
- [ ] NIFTY **futures** (front month) — same
- [ ] A **live weekly option** — same
- [ ] An **EXPIRED weekly option** — *this is the one that decides everything.*
      Historical intraday for expired contracts is often not retrievable by
      security ID. If it is not, options research must be built FORWARD from
      recorded data while index/futures research can start on history immediately.

This single test decides whether Phase 2 is "start researching next week" or
"start recording and wait". Nothing else should be built before it is answered.

### Step 2 — Persist live ticks (do regardless of Step 1's outcome)

The harvester already receives ticks and drops them into 90-second Redis TTLs.
Write them to disk instead. **This is the only item with a deadline** — history
not captured today cannot be bought back if Dhan's archive turns out shallow. It
also gives a forward-test set and validates any vendor data against real fills.

Mirror `backtest/bhavcopy.py`'s discipline: cache to disk, quality report,
explicit schema, refuse to silently return an empty range.

### Step 3 — Write Amendment E to the charter

Capital, target Sharpe, drawdown as a percentage, and the Phase 2 arena
definitions. **Before any research runs.**

### Step 4 — Measure before building

The single most effective thing in Phase 1 was measuring the premium or the IC
*before* writing a strategy — it killed hypotheses in minutes that would
otherwise have cost weeks. The intraday equivalent:

> Before building anything, measure whether NIFTY 1-minute returns have any
> predictable structure at a horizon where ~1 index point of friction is
> affordable.

A few days of work that either opens the arena or closes it honestly.

---

## 6. NEEDED FROM THE OPERATOR

1. **Exact capital figure.** "Under Rs 5L" spans Rs 50k to Rs 4.9L, and the
   difference decides whether the book holds 1 position or 6.
2. **Fresh Dhan token** in `.env` (`DHAN_ACCESS_TOKEN`). The one currently there
   is from 2026-08-07 and will have expired.
3. Confirmation that live-market hours are available for the Step 2 work.

---

## 7. System state as left on 2026-08-13

| | |
|---|---|
| `main` | `06609a0`, pushed, clean tree, in sync with origin |
| research branch | `a203fba`, pushed |
| test suite | 847 checks, 0 failures, 20 files (`python tests/run_all.py`) |
| services | **all stopped** — python, node, WSL, ports 8000/8001/3000/5432/6379 |
| `TRADING_MODE` | `PAPER` |
| `LADDER_MODE` | `false` (the ladder is a fill artifact: PF 3.47 -> 0.78) |
| live entries | refused — `unpromoted_ladder`, zero promotions on record |
| logs | `worker.log` emptied (was 100% test artifacts); tests now write to `logs/test/` |
| registry | 9 registered, 9 killed, 4 of 4 arenas closed |

To restart the stack: `start_v8.bat`, then `python check_health.py`.
Postgres and Redis live in WSL at 172.26.128.109 — bring WSL up first.

---

## 8. Traps this project has already paid for — do not re-learn them

- **The engine's defaults are shaped around vertical credit spreads.** Six config
  knobs silently did nothing on other structures. Check every `--set` against the
  entry path that will actually read it. `Config.__post_init__` now refuses the
  known ones.
- **Never accept an aggregate fill/skip rate as evidence about the market.**
  Decompose by *kind* of refusal — self-imposed rule vs schema artefact vs real
  illiquidity. Arena 4's "capacity" problem was 45% a risk cap and only 12%
  actual illiquidity.
- **When a short structure loses, compute the MID-PRICE P&L before proposing the
  long side.** The spread is paid in both directions. This is what stopped a
  well-motivated but wrong "buy earnings vol instead" hypothesis.
- **A log file's recency is not evidence a service ran.** Check the logger name.
  `worker.log` looked like live activity and was 427/427 test fixtures.
- **The two-era schema break bites repeatedly.** `txns` is NaN before 2024 and
  `UndrlygPric` is empty for stock futures before 2024. Use `strict_legacy` on
  any window starting 2023-01-01, or `strict` silently deletes a third of it.
- **Pre-registration works.** The carry signal was declared, with direction and
  thresholds, in a commit made before the number existed — which is the only
  reason its negative result could not be re-read as "short carry works".
