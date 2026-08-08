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

## Amendment Log

*(append-only; date + reason + what changed. An amendment made after seeing a
result it would change invalidates that result.)*

- 2026-08-07 — charter locked, no amendments.
- 2026-08-07 — **Amendment A** added. Reason: target variable was wrong (CAGR
  chosen, when it is a consequence of Sharpe under a fixed drawdown budget);
  operator supplied real drawdown tolerance of Rs 1,00,000. Supersedes Sections 2
  and 3; Sections 5-7 unchanged. **Made before any search was run — no result
  informed it.**
