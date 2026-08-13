# Research Charter — Phase 0

**Locked 2026-08-07. This document is written BEFORE the search begins and is not
edited afterwards.**

Amendments are appended to the Amendment Log at the bottom, dated, with a reason.
Any amendment made *after seeing a result it would change* invalidates that
result. If that happens, say so out loud and discard the result.

The purpose of locking this is narrow and specific: on 2026-08-07 a DTE sweep
produced a band showing +Rs 16,723 and PF 1.57. It looked like a discovery. It
was noise — out-of-sample it returned **-Rs 16,185**, a near-perfect sign flip.
The only reason it was called correctly is that the noise threshold was committed
to before the numbers were seen. Everything below exists to make that repeatable.

---

## 1. What this project is for

Generate returns that beat the alternative on a risk-adjusted basis, net of all
costs, at a size actually reachable with the available capital.

**The alternative is a ~7% fixed deposit / index fund.** It requires zero hours
per week and carries no execution risk. It is the benchmark, and it wins by
default. Nothing here is worth running unless it clearly beats it.

- Capital: **Rs 15,00,000**
- Time budget: **15+ hrs/week**
- Capital at risk during the search: **zero — paper only** until the promotion
  gate in Section 5 is passed

## 2. Success criteria (pre-registered)

A strategy is a candidate only if it clears **all** of these:

| criterion | threshold | why |
|---|---|---|
| CAGR | **>= 15%** net of friction | must clearly beat a 7% FD, not marginally |
| Max drawdown | **<= 15%** of equity | must be sit-through-able without intervening |
| Sharpe (annualised) | **>= 1.0** OOS | below this the return is not distinguishable from risk-taking |
| Expectancy | **>= Rs 2,250/trade** at 100 trades/yr, scaled by actual frequency | see Section 3 |
| Capacity | works at Rs 15L with **real lot sizes** | no fractional-lot fantasies |
| Fill realism | passes under **`liquidity_gate=strict`** | see Section 6 |

Rs 15L at 15% = **Rs 2.25L/year**. That is the target. It is deliberately
modest in market terms — small enough that no institution is competing for it,
which is the whole structural bet (Section 8).

## 3. The detection rule — what is worth hunting at all

Required expectancy scales with trade frequency:

| trades/year | required expectancy/trade |
|---|---|
| 33 | Rs 6,800 |
| 100 | Rs 2,250 |
| 250 | Rs 900 |

Observed per-trade standard deviation on this instrument class is ~Rs 2,800.
Detecting a Rs 2,250 effect against that variance needs **~12-30 trades**.

**RULE: only hunt effects large enough to be both economically meaningful and
statistically detectable within ~30 trades.**

Corollary, and it is a hard one: **a strategy that needs 500 trades to prove
itself has an edge too small to move Rs 15L past an FD. Discard it without
testing it.** This prunes most of the search space and keeps every screen cheap.

## 4. Multiple-comparisons discipline

Every hypothesis is registered in the kill log (Section 7) **before** it is run,
with its prediction and its kill criterion.

For any sweep of `N` configurations, the significance bar is the expected maximum
|t| across `N` pure-noise configs, approximately `sqrt(2 * ln N)`:

| N configs | required \|t\| |
|---|---|
| 5 | 1.79 |
| 10 | 2.15 |
| 20 | 2.45 |
| 50 | 2.80 |

A result below this threshold is **not a finding**, regardless of how good its
P&L looks. `backtest/sweep_dte.py` already computes and prints this.

Clearing the threshold makes a result **worth testing out-of-sample**. It does
not make it true.

## 5. Promotion gate — what it takes to reach live

No strategy gets a live code path until it passes **all four** walk-forward
acceptance criteria (already implemented in `backtest/walkforward.py`):

- `wfe_ge_0.5` — walk-forward efficiency >= 0.5
- `oos_pf_ge_1.25` — out-of-sample profit factor >= 1.25
- `folds_60pct_profitable` — >= 60% of active test folds profitable
- `oos_sharpe_gt_hurdle` — OOS Sharpe above the deflated-Sharpe hurdle

Plus:
- survives **2x slippage** cost stress with PF still > 1.0
- survives **jackknife** (dropping the best 3 trades does not flip the verdict)
- run under `liquidity_gate=strict`

Then, and only then: **paper trade with a pre-committed sample size** (minimum 30
trades) before any real capital. The paper result must independently clear
Section 2.

*The ladder failed all four of these criteria. They existed and nothing was bound
to them. That is the specific hole this section closes.*

## 6. What does NOT count as evidence

Each of these produced a false positive in this project's actual history. They
are disqualifying, not merely discouraged:

1. **Fills on contracts that did not trade.** Settlement-price fills manufactured
   +Rs 1,93,464 of ladder profit that became -Rs 12,703 under a real fill rule.
   Any result without `liquidity_gate=strict` is void.
2. **Marks from a book that is not two-sided.** Marking off stale LTP fabricated
   a take-profit and a stop-loss in the live ledger and forced the purge of 15 of
   17 rows on 2026-07-30.
3. **In-sample tuning presented as a result.** If parameters were selected on the
   same data that produced the number, it is a hypothesis, not evidence.
4. **n < 20 trades.** The weekly structure showed PF 29.04 on 12 trades with
   **zero losses observed** — an unsampled tail, not an edge. One max-width loss
   (~Rs 12,975) would erase the entire 2.5-year profit.
5. **Profit factor > 3 on n < 50.** Implausible for any spread structure. Treat
   as a bug in the fill model until proven otherwise. Real credit-spread PFs run
   1.1-1.5.
6. **Any backtest whose P&L moves materially between `liquidity_gate=off` and
   `strict`.** It is measuring the fill model, not the market.
7. **A single good configuration surrounded by bad ones.** A real effect is a
   broad plateau of adjacent parameters behaving alike.

## 7. Kill rules

**Per hypothesis.** Registered before running, with an explicit falsifiable
prediction and kill criterion. Dead hypotheses are written to the kill log with
their numbers so nothing is ever silently retested. **A hypothesis that fails is
closed, not tuned.** Re-testing a variant requires a new registration and counts
against the multiple-comparisons budget in Section 4.

**Per arena.** If an arena (Section 8) yields nothing clearing Section 4 after
its allotted screens, the arena is closed. No extensions.

**Project level — the important one.**

> If the survey completes with nothing clearing the promotion gate in Section 5,
> the project stops and the Rs 15,00,000 goes into an index fund.

Written now, while unattached to any idea. The failure mode being guarded against
is not a bad strategy — it is another two and a half years of near-misses that
never quite die.

**Review date: 2027-01-07** (~5 months). At that point the survey is either
complete or the project is reassessed against this document as written.

## 8. Where to search, and the structural bet

Direction: **survey cheaply and broadly, let the data choose.** No arena gets
deep investment before the screens are in.

Arenas, each with pre-registered screens:
1. **Index-option structures other than vanilla credit spreads** — calendars,
   ratios, term-structure, event vol
2. **Cross-sectional equities** — stock F&O, relative value across names
3. **Directional / trend on liquid futures** — edge lives in sizing and risk
   management rather than structure
4. **Event-driven volatility** — earnings, RBI policy, budget

**The bet, stated plainly so it can be judged later:** Rs 2.25L/year is beneath
the notice of anyone equipped to compete it away. Capacity-constrained edges are
the one category where a Rs 15L participant genuinely out-positions institutions.
The ladder failed in the *most crowded* arena available to Indian retail —
vanilla credit spreads at standard deltas on the most liquid index in the
country, paying market makers the spread for the privilege.

**If this charter's premise is wrong, it is wrong here.** If all four arenas come
back empty, the honest conclusion is that no accessible edge exists at this size
and skill level, and Section 7 applies.

## 9. Honest priors, recorded now

- Most retail quant research finds nothing. This is expected to fail.
- Any single arena probably comes up empty.
- The plan is built so that finding nothing costs ~5 months and produces a clear
  answer, rather than an indefinite search.
- Discovering there is no edge **is a successful outcome of this charter.** It is
  the second-best result available, and far better than the alternative of
  learning it slowly with real money.

---

## Amendment A — portfolio targets replace single-strategy targets

**2026-08-07, same day, BEFORE any search was run or any result seen.** Legitimate
for exactly that reason: nothing below was chosen to accommodate a number we
liked. Supersedes Sections 2 and 3 where they conflict; Sections 5, 6 and 7 stand
unchanged.

**Reason.** Section 2 set a CAGR target. That was the wrong variable. Drawdown
tolerance caps volatility, and `return ≈ Sharpe × volatility`, so with a fixed
drawdown budget the return is not something to choose — it is what falls out of
Sharpe. The operator's stated tolerance is **Rs 1,00,000 = 6.67% of Rs 15L**,
being the loss that would actually cause them to switch the system off.

### A1. Target

At a Rs 1L drawdown budget:

| portfolio Sharpe | ~CAGR | ~per year |
|---|---|---|
| 1.0 | 3.6% | Rs 54,000 |
| **1.4** | **7%** | **Rs 1.05L — parity with the FD** |
| 2.0 | 14% | Rs 2.1L |
| 2.5 | 21% | Rs 3.2L |
| 3.0 | 31% | Rs 4.6L |

- **Minimum viable outcome: portfolio OOS Sharpe >= 1.4.** Below this the project
  loses to a deposit requiring zero hours, and Section 7 applies.
- **Worth the time: >= 2.0.** **Target: 3.0.**
- Max drawdown budget: **Rs 1,00,000 (6.67%)**, absolute, not a percentage.

### A2. What the research must produce

Portfolio Sharpe for N equal-weight strategies with average pairwise correlation
`rho` is `S * sqrt(N / [1 + (N-1)*rho])`, with a hard ceiling of **`S / sqrt(rho)`**
regardless of N.

| individual OOS Sharpe | avg correlation | N needed for portfolio 2.0 |
|---|---|---|
| 1.0 | 0.10 | 6 |
| 1.0 | 0.15 | 9 |
| 1.0 | 0.20 | 16 |
| 0.8 | 0.10 | 15 |
| 0.8 | 0.15 | 85 — impractical |
| 0.5 | 0.10 | impossible (ceiling 1.58) |

Therefore:
- **Individual strategy bar: OOS Sharpe >= 0.8** (prefer >= 1.0). Sharpe-0.5
  strategies are not admissible at any count — the correlation ceiling makes them
  unable to reach the target however many are stacked.
- **Average pairwise correlation <= 0.15**, measured on OOS daily returns.
- **Target portfolio: 6-15 strategies.**
- Correlation is checked **before** P&L. A new candidate that is highly correlated
  with an existing one adds nothing and is rejected regardless of its own numbers.
- The per-strategy CAGR/expectancy thresholds in Section 2 no longer apply
  individually; they apply to the **combined portfolio**.

### A3. Drawdown, confidence and the shutdown rule

The operator's tolerance is **absolute-anchored and confidence-conditional** —
~20% of Rs 1L feels survivable, 20% of Rs 10L does not. Left unmanaged this
guarantees the system gets switched off at the worst moment as the account grows.

- Every promoted strategy and the combined portfolio **publish a bootstrap
  max-drawdown distribution (p50/p95/p99) before going live** (already implemented
  as `mc_bootstrap_dd` in `backtest/walkforward.py`). The operator signs off on
  the **p99 as a rupee figure** in advance.
- **Pre-committed shutdown rule: breach of the p99 modelled drawdown stops the
  system.** Not from fear — the model is falsified. Agreeing this in advance is
  what makes an ordinary drawdown survivable, because sitting through it becomes
  following a rule rather than a test of nerve.
- **Staged escalation.** Start at Rs 20-30k of risk. Increase only on realized
  milestones (e.g. 50 live trades with drawdowns inside the modelled
  distribution). This builds the confidence that is currently missing instead of
  assuming it.
- **Ring-fence profit periodically** so the at-risk pool stays inside gut
  tolerance as the total grows. This slows compounding — an accepted cost, on the
  grounds that a system run at 60% of optimal size beats an optimally-sized one
  that gets turned off in month four.

### A4. What this changes about the hunt

Not "find a high-return strategy". **Find many weakly-correlated modest ones.**

- Breadth across instruments and horizons matters more than depth in any one —
  raising the priority of Phase 1 (data width and depth).
- Requires higher trade frequency than the ladder: modest per-trade effects need
  more observations both to detect and to compound.
- A new **portfolio-construction layer** the previous system never had:
  fractional-Kelly or risk-parity sizing, live correlation monitoring,
  portfolio-level drawdown control, and leverage applied only to the *combination*
  — never to a single strategy.
- **Named failure branch:** if the survey finds real edges but they are all
  mutually correlated, portfolio Sharpe stalls near 1.0, which at this drawdown
  budget is a ~3.6% strategy. That is a genuine finding and Section 7 applies.
  Recorded now so it cannot later be argued away.

### A5. Note on Section 3

Section 3's "detectable in ~30 trades" rule was derived from needing the whole
Rs 2.25L from a single strategy. Under Amendment A each strategy carries a
fraction of the load, so smaller per-trade effects are admissible — but they then
require **more** observations to establish. The rule becomes: a candidate must
reach **OOS Sharpe >= 0.8 on >= 100 OOS trades**, or be discarded. The spirit is
unchanged: do not chase effects too small to matter or too small to see.

---

## Amendment B — operationalising Sections 4, 6 and 8 for the research loop

**2026-08-08, BEFORE any hypothesis was registered or run.** Legitimate for
exactly that reason: no result exists yet that any of these numbers could have
been chosen to accommodate. Changes no threshold — it fixes the meaning of rules
that were written qualitatively, so that `research/` can enforce them.

**Reason.** Sections 6.6 and 6.7 say "moves materially" and "a broad plateau".
Those cannot be checked by a program, and deciding what they mean *after* seeing
a screen result is precisely the failure Section 4 exists to prevent. So they are
fixed now, in advance, in `research/charter.py`.

### B1. "P&L moves materially between gate off and strict" (Section 6.6)

Judged on **per-trade edge, not total P&L**. A strict gate removes trades that
could not have happened, so totals *should* fall — condemning a strategy for that
would be condemning it for the gate working. What indicts a backtest is the
per-trade edge changing, because that means the fill model produced the number.

Material if **any** of:
- the sign of expectancy per trade flips between `off` and `strict`;
- expectancy per trade moves by more than **50%** in relative terms;
- profit factor crosses **1.25** (the Section 5 promotion bar) in either direction.

*The ladder fails on the first clause: +Rs 2,359/trade under `off`, negative under
`strict`.*

### B2. "A broad plateau of adjacent parameters" (Section 6.7)

Applies only to a hypothesis that registers a sweep. The best cell on the swept
axis must have **at least 1 immediately adjacent cell with positive expectancy**.
Deliberately generous: it only has to catch the isolated spike, which is the
pattern that produced the 35–45 DTE false positive.

### B3. Liquidity eras (reporting requirement)

The share of NIFTY option legs that actually trade rises ~6× across the archive
(measured 2026-08-08, three sessions sampled per year): 2016 10.2%, 2019 9.8%,
2020 14.8%, 2021 29.0%, 2022 38.6%, 2023 51.7%, 2026 63.5%. A result pooled
across that break is an average over three different markets.

Eras, drawn where the slope changes:

| era | window | legs tradeable |
|---|---|---|
| `early` | 2016-01-01 – 2019-12-31 | ~10% |
| `ramp` | 2020-01-01 – 2022-12-31 | 15–39% |
| `modern` | 2023-01-01 – | 52–64% |

**Every result is reported per era.** `modern` is the default test window: it is
the only regime resembling the market a strategy would actually be traded in.

### B4. The multiple-comparisons budget compounds across retries

Section 7 says re-testing a variant "counts against the multiple-comparisons
budget in Section 4". Made mechanical: a hypothesis registered with
`--supersedes` inherits the sum of its ancestors' declared configuration counts,
so the `sqrt(2 ln N)` bar rises with every retry of a dead idea. The fourth
variant is the fourth draw from the same urn and is priced as one.

### B5. Screens cannot promote

Stage 1 may return only *kill* or *advance*. Nothing reaches a positive verdict
without the Section 5 walk-forward. This is structural, not procedural: there is
no code path from a screen to "survived".

---

## Amendment C — operationalising the Section 5 promotion gate

**2026-08-08, BEFORE anything has been promoted.** Nothing is at stage `paper` or
`live`, so no number below could have been chosen to let a particular strategy
through. Adds no new permission — it makes the existing gate enforceable.

**Reason.** Section 5 says a strategy must "paper trade with a pre-committed
sample size (minimum 30 trades) before any real capital" and that "the paper
result must independently clear" the targets. What *clearing* means at n=30 was
never defined, and defining it after seeing a paper result would be the Section 4
failure again.

### C1. The stage ladder

`research` → `paper` → `live`. A structure is at `research` unless a promotion
record says otherwise, and **`research` and `paper` both mean live entries are
refused**. Only `live` permits real orders. The transitions:

- **research → paper** requires a hypothesis in the kill log with status
  `survived` — the Section 5 walk-forward criteria, the cost and jackknife
  stress, and Amendment A5's OOS bar, all already evaluated by the loop. There is
  no other door, and no way to assert a promotion from evidence the loop did not
  produce.
- **paper → live** requires the sample in C2.

### C2. What the paper sample must clear

Counting only trades entered **after** the paper promotion was recorded — a
sample that includes trades already in the ledger is selected evidence, not a
pre-committed one.

1. **≥ 30 closed trades** (Section 5, unchanged).
2. **Every counted trade live-priced.** A single non-live-priced row fails the
   whole sample rather than being quietly excluded: it means the entry pricing
   guard has a hole, and a ledger that can contain fiction cannot authorise
   money. This is the 2026-07-30 purge condition.
3. **Positive expectancy.**
4. **Not falsifying the model.** At n=30 with per-trade sd ~Rs 2,800 the standard
   error is ~Rs 511, so the sample cannot *prove* an edge and is not asked to.
   It is asked not to contradict one: realised expectancy more than **2 standard
   errors below** the modelled OOS expectancy fails.
5. **Realised max drawdown within the modelled p99** (Amendment A3).

### C3. The drawdown shutdown is automatic

A3 pre-committed that "breach of the p99 modelled drawdown stops the system".
Made mechanical: the same check run against a `live` structure **revokes its
promotion on breach**, without asking. The entire value of that rule is that it
was agreed before it hurt, and a rule that needs a decision at the moment it
binds is not a rule. Revocation stops entries only — open positions still exit
normally, because trapping risk in a position is not a safety measure.

### C4. A promotion expires

Default **180 days**, recorded on the record as `review_by`. Past it, live entries
are refused until it is re-earned. Without an expiry the first strategy to pass
would be licensed indefinitely on a single backtest, in a market whose liquidity
regime has already changed character twice inside this archive (B3).

### C5. A promotion covers named strategy types only

The record lists the strategy types its evidence covers, and an entry outside
that list is refused. Evidence about credit spreads is not a licence for iron
condors the engine happens to be able to emit.

---

## Amendment D — B3's eras are scoped to the evidence that drew them

**2026-08-10.** Made with arena 3's IC table already seen, which is disclosed
rather than hidden — see "what this may not be used for" below. It **loosens** a
constraint, so it is the dangerous kind of amendment and is written to be as
narrow as the evidence supports.

**Reason.** B3 partitions the archive into three liquidity eras and requires every
result to be reported per era. Its entire evidence is the share of NIFTY
**option** legs that actually trade, which rises ~6× from 2016 to 2026. Nothing
in B3 measured any other instrument. Applying its boundaries to single-stock
futures was an extension by analogy, and the analogy is false: measured on stock
futures under `strict_legacy`, the era-defining property is **100.00% in every
year from 2016 to 2026**. There is no liquidity ramp in that instrument to
partition.

The properties that matter for a cross-sectional book are likewise continuous
across the 2022→2023 boundary (`scratch/arena3_era_break.py`):

| | early | ramp | modern |
|---|---|---|---|
| gate pass rate | 100% | 100% | 100% |
| ρ̄ | 0.183–0.314 | 0.271–0.418 | 0.205–0.341 |
| N_eff at 8 held | 2.50–3.51 | 2.04–2.76 | 2.36–3.29 |
| median annualised vol | 32–36% | 33–48% | 25–32% |
| 21-day cross-sectional dispersion | ~10.3% | ~10.4% | ~8.6% |

Ranges overlap throughout with no trend. 2020's ρ̄ 0.418 and vol 47.7% are COVID —
an event inside an era, not a boundary between eras. The one metric that moves
materially is lot size (1500 → 625), and it declines smoothly across the whole
archive rather than breaking at any era edge.

### D1. What B3 continues to require, unchanged

**Every strategy result is still reported per era, and `modern` is still the
default test window.** A backtest reads lots, fills, margin and capacity, all of
which differ across a decade, and pooling those is exactly the average-over-three-
markets B3 names. Nothing about screens, walk-forwards, verdicts or promotions
changes.

### D2. What may now be pooled

**Signal-property estimation may pool eras** — information coefficient, pairwise
correlation, cross-sectional dispersion, and other unconditional properties of
the instrument set. These quantities involve no lots, no fills, no sizing and no
capital, so the objection that makes pooling wrong for a backtest does not apply
to them.

Permitted only where **the era-defining property has been measured on that
instrument and found flat**, with the measurement recorded before the pooled
estimate is used. Stock futures qualify on the table above. No other instrument
qualifies until someone measures it: the option book demonstrably does not, and
index futures have not been checked.

### D3. What this may not be used for

- **It does not reopen a closed hypothesis.** Section 7 stands: `tsmom-stock-modern`
  and the rest are closed, and a pooled re-run of a dead configuration is the
  tuning Section 7 forbids.
- **It does not lower any bar.** A5's 0.8/1.0, the drawdown budget and the
  `sqrt(2 ln N)` noise threshold are untouched. Pooling buys *observations*, not
  a cheaper test — and the noise bar is computed from the config count, so more
  data cannot flatter a sweep.
- **The eleven ICs already measured on `modern` are spent knowledge** (disclosed
  in `research/ARENAS.md`). Re-estimating them pooled does not make them fresh
  predictions, and any of them registered later is still a confirmatory
  registration priced at eleven looks.

### D4. Why this was safe to make after seeing a result

The result that prompted it is a **power** calculation, not an edge: the smallest
IC detectable on `modern` alone (0.0551) is the same size as the IC required to
be worth trading (~0.04–0.05), so the arena could not answer its own question.
Amendment D changes which observations may be counted, not what counts as
passing. It cannot turn a losing signal into a winning one, and the table it
rests on contains no strategy result — deliberately, since choosing a window by
first looking at what each window does to a signal's IC is how a result gets
manufactured.

---

### D5. A pooled estimate must earn the pooling — added 2026-08-10, same day

**D2 as first written was wrong, and this narrows it.** The pooled IC re-run it
authorised (`research/ARENAS.md` T2c) showed **nine of eleven signals flipping
sign across eras**, with the entire pooled number carried by `early`: momentum
ICs of 0.05–0.07 in 2016–2019 collapsing to ~0.00 in both ramp and modern. The
pooled figure described no market that has ever existed.

The measurement D2 rested on was correct and still stands — for stock futures the
era-defining property is 100.00% in every year, and ρ̄, breadth, vol and dispersion
are continuous. The **inference** was wrong:

> **Flat instrument properties do not imply flat signal properties.** The same
> instrument, with the same liquidity, breadth and correlation structure, can
> stop being predictable. Structure and predictability are different things, and
> D2 assumed the first governed the second.

So D2's permission is now conditional. **A pooled estimate may be relied on only
if both of these hold; otherwise the per-era estimates are reported and the
pooled number is discarded.** Fails closed: an era with too few observations to
estimate makes the pooled figure unverifiable, and an unverifiable condition is
not a satisfied one.

- **D5.1 — sign consistency.** Every per-era estimate must share the sign of the
  pooled estimate. A pooled mean taken across a sign change is an average of
  opposite effects and means nothing.
- **D5.2 — no single-era dominance.** Removing any one era must not change the
  pooled estimate by more than **50%** — the materiality figure Amendment B
  already uses, applied as a leave-one-out jackknife, which is the same stress
  the walk-forward already applies to folds. An estimate that survives only while
  one era is included is that era's result wearing a pooled label.

Both are mechanical and both are implemented as `charter.pooled_estimate_admissible`,
so this cannot quietly stop being applied the way a prose threshold does. Neither
rule requires *proof of heterogeneity* to block pooling: with three eras and wide
per-era standard errors, a formal heterogeneity test has almost no power, and
failing to reject homogeneity is not evidence of it. The burden sits on the
pooled estimate to justify itself, which is the same direction of proof the rest
of the charter uses.

**This tightens; it does not license anything new.** Nothing that failed under
D1–D4 passes under D5.

## VERDICT — Section 7's project-level rule, invoked 2026-08-13

**All four arenas are closed. Nothing cleared the Section 5 promotion gate.
9 hypotheses registered, 9 killed, 0 survivors of the 6–15 Amendment A1 needs.**

Section 7, as written before any search was run:

> If the survey completes with nothing clearing the promotion gate in Section 5,
> the project stops and the Rs 15,00,000 goes into an index fund.

That branch is now the live one. Recorded here rather than argued with.

### The four closures are not equally strong, and that matters

| arena | closed | hypotheses | grounds | strength |
|---|---|---|---|---|
| `futures_trend` | 2026-08-10 | 2 | detection threshold ≈ profitability threshold | **weak** — could not resolve |
| `index_structures` | 2026-08-13 | 3 | both structures negative **before friction** across 377 trades | **strong** — measured absence |
| `event_vol` | 2026-08-13 | 3 | mid-price premium −Rs 174/trade over 246 events ≈ zero | **strong** — measured absence |
| `cross_sectional` | 2026-08-13 | 1 | one pre-registered signal refuted; further search barred by its own power arithmetic | **medium** — budget exhausted |

**Two arenas produced a measured absence of edge, which is a real finding.**
Weekly NIFTY short volatility loses before costs, and the +2.38 vol point premium
it harvests is payment for a left tail rather than a mispricing. Single-stock
earnings straddles have a mid-price premium indistinguishable from zero, and lose
in *both* directions at *any* slippage assumption including none. Those two are
not "we looked and did not find" — they are "we measured the thing itself".

**Two arenas produced an absence of evidence, which is weaker and is not
disguised.** `futures_trend` closed because the smallest IC it could detect was
the same size as the smallest IC worth trading. `cross_sectional` closed because
its eleven scored candidates cleared nothing and the single pre-registered signal
it had budget for was refuted — but eleven signals is not the space of signals,
and the honest statement is that the *search budget* ran out, not the space.

### The honest caveats, recorded because the conclusion is expensive

1. **The survey took 5 days, not the ~5 months Section 7 budgeted.** That is not
   corner-cutting: the archive, engines, liquidity gate, friction and margin
   models were built in Phases 0–4, and the discipline of measuring the *premium
   or the IC first* killed hypotheses in minutes that would have taken a full
   backtest campaign each. But it does mean the calendar reserve is entirely
   unspent, and an operator who wants to reassess has 147 days in which to.
2. **Nine hypotheses is a small number.** Section 4's budget would have allowed
   more. The defence is that in two arenas the binding measurement was of the
   market rather than of a strategy, which is stronger evidence than more
   strategies would have been — and in the other two, more searching was
   demonstrably self-defeating.
3. **Five silent no-op configuration bugs were found during the survey**, four of
   them before the runs that would have been corrupted. It is reasonable to ask
   what a sixth would have changed. The mitigation is that every kill above rests
   on a gate A/B and on requirements fixed in a fingerprint before the run.
4. **This says nothing about edges requiring data this project does not have.**
   Fundamentals, borrow/short-interest, analyst revisions and index-flow are all
   untouched and none is derivable from bhavcopy.

### What Section 9 said about this outcome, before it happened

> Discovering there is no edge **is a successful outcome of this charter.** It is
> the second-best result available, and far better than the alternative of
> learning it slowly with real money.

The counterfactual is concrete rather than rhetorical. The ladder that preceded
this charter showed PF 3.47 and t +3.15 on settlement-price fills; under a real
fill rule it was PF 0.78 and t −0.60. That strategy was wired live and would have
traded Rs 15,00,000. The charter's cost was five days and four closed arenas.

**Status: the survey is complete and its named failure branch is reached. The
decision to deploy capital to an index fund, to amend this charter, or to
reassess at the 2027-01-07 review is the operator's and is not recorded here.**

## Amendment Log

*(append-only; date + reason + what changed. An amendment made after seeing a
result it would change invalidates that result.)*

- 2026-08-07 — charter locked, no amendments.
- 2026-08-07 — **Amendment A** added. Reason: target variable was wrong (CAGR
  chosen, when it is a consequence of Sharpe under a fixed drawdown budget);
  operator supplied real drawdown tolerance of Rs 1,00,000. Supersedes Sections 2
  and 3; Sections 5-7 unchanged. **Made before any search was run — no result
  informed it.**
- 2026-08-08 — **Amendment B** added. Reason: Sections 6.6 and 6.7 were written
  qualitatively and cannot be enforced by a program; fixing their meaning after
  seeing a screen result would be the exact failure Section 4 guards against.
  Defines materiality, the plateau rule, the liquidity eras, the compounding
  multiple-comparisons budget, and that screens cannot promote. Changes no
  threshold. **Made before any hypothesis was registered — no result informed
  it.**
- 2026-08-08 — **Amendment C** added. Reason: Section 5's promotion gate existed
  and nothing was bound to it, which is how the ladder reached live having failed
  all four of its criteria. Defines the research/paper/live ladder, what a
  30-trade paper sample must clear, the automatic drawdown revocation, promotion
  expiry, and strategy-type coverage. Adds no new permission. **Made before
  anything had been promoted — no result informed it.**
- 2026-08-10 — **Amendment D** added. Reason: B3's three liquidity eras rest
  entirely on NIFTY *option* leg tradeability, and applying them to single-stock
  futures was an extension by analogy that measurement refutes — the same property
  is 100.00% for stock futures in every year 2016–2026, with ρ̄, breadth, vol and
  dispersion all continuous across the era boundaries. Scopes B3 to its evidence:
  strategy results stay per-era with `modern` the default (D1), while
  signal-property estimation (IC, correlation, dispersion — quantities involving
  no lots, fills or sizing) may pool eras where that instrument's era-defining
  property has been measured flat (D2). Lowers no bar and reopens no hypothesis
  (D3). **This one WAS made after seeing a result, which is disclosed rather than
  hidden:** the prompting result is a power calculation — detectable IC 0.0551 on
  `modern` against a ~0.04–0.05 requirement, so arena 3 could not answer its own
  question — and it changes which observations may be counted, not what counts as
  passing (D4). The table it rests on contains no strategy result.
- 2026-08-10 — **Amendment D5** added, same day, **narrowing D2**. Reason: the
  pooled re-run D2 authorised flipped sign across eras for nine of eleven signals,
  with the whole pooled figure carried by `early` — momentum ICs of 0.05–0.07 in
  2016–2019 collapsing to ~0.00 in ramp and modern. The pooled number described
  no market that has ever existed, which is the very thing B3 forbids. D2's
  measurement was correct and stands; its **inference** was wrong — flat
  *instrument* properties do not imply flat *signal* properties. A pooled estimate
  is now admissible only if every per-era estimate shares its sign (D5.1) and no
  single era's removal moves it by more than 50% (D5.2, reusing Amendment B's
  materiality figure). Implemented as `charter.pooled_estimate_admissible` with
  16 tests, so it cannot quietly stop being applied. **Tightens only — nothing
  that failed under D1–D4 passes under D5.** Applied to the run that prompted it,
  it admits **0 of 11** estimates.
