# Section 7 Closure — Trader Singh

**Drafted 2026-08-18. Review date as chartered: 2027-01-07.**
Invokes `RESEARCH_CHARTER.md` §7, project level.

> *"If the survey completes with nothing clearing the promotion gate in Section 5,
> the project stops and the Rs 15,00,000 goes into an index fund."*
> — written 2026-08-07, before any hypothesis was registered

---

## 1. Verdict

**The survey is complete. Nothing cleared the Section 5 promotion gate. Nothing
ever reached it.**

```
registered   14        promoted to live      0
killed       14        reached Section 5     0
survived      0        arenas closed      6 of 7
```

Section 7 fires as written. The research programme stops.

Two corrections to the literal text, both recorded rather than quietly applied:

- **There is no ₹15,00,000.** Amendment E (2026-08-14) corrected capital to
  **₹50k–1L, staged** — added only once a system is validated. The instruction to
  redirect ₹15L into an index fund is therefore moot. The substance — stop
  spending effort here — stands unchanged, and is if anything stronger: the
  capital that would have justified the search never existed.
- **This is drafted ahead of the review date**, because the survey completed
  early. §7 says that at 2027-01-07 "the survey is either complete or the project
  is reassessed." It is complete. Nothing between now and January changes that;
  see §5 below for the one arena that stays open and why it does not.

---

## 2. The record

### Hypotheses — 14 registered, 14 killed

| id | arena | verdict |
|---|---|---|
| `cal-cheapvol-modern` | index_structures | killed |
| `xsect-mom-modern` | cross_sectional | killed |
| `trend-donchian-modern` | futures_trend | killed |
| `evol-earnings-modern` | event_vol | killed |
| `evol-earnings-pre2024` | event_vol | killed |
| `tsmom-stock-modern` | futures_trend | killed |
| `fly-weekly-modern` | index_structures | killed |
| `ic-uncond-modern` | index_structures | killed |
| `evol-earnings-narrowwing` | event_vol | killed |
| `intraday-ic-modern` | intraday_index | killed |
| `intraday-ceiling-modern` | intraday_index | killed |
| `intraday-conditional-modern` | intraday_index | killed |
| `intraday-edgesize-modern` | intraday_session | killed |
| `intraday-varshare-modern` | intraday_option | killed |

Every one was registered **before** it ran. That is the only reason this document
can be trusted: none of these results can be re-read later as a discovery.

### Arenas — and the closures are not all the same strength

This distinction is load-bearing and §6.4 exists to enforce it.

| arena | closed | grounds |
|---|---|---|
| `index_structures` | 2026-08-13 | **measured absence** — question was resolvable |
| `event_vol` | 2026-08-13 | **measured absence** — question was resolvable |
| `intraday_index` | 2026-08-14 | **measured absence of a tradeable edge** |
| `intraday_session` | 2026-08-14 | **measured absence** from session/regime structure |
| `cross_sectional` | 2026-08-13 | *search budget exhausted* — not the question |
| `futures_trend` | 2026-08-10 | *resolvability and throughput* — not demonstrated absence |
| `intraday_option` | — | **OPEN**, gated by Amendment E10.2 |

**Four closures are measured absence. Two are absence of evidence.** Those two —
`cross_sectional` and `futures_trend` — are places where the project ran out of
budget or data, not places where it proved nothing is there. Anyone reading this
as "there is no edge in Indian markets" is overreading it by two arenas.

---

## 3. What was actually established

Four findings survive the programme. All are cost results, which is itself the
headline.

### 3.1 The binding constraint is the cost floor, and it is regulator-fixed

Measured twice by independent paths that agree to 2%:

| path | result |
|---|---|
| Dhan 5-level depth book (2026-08-14 after-hours, 2026-08-18 live) | 4.01 → **2.73** index pts all-in |
| bhavcopy premiums + `friction_model` (`backtest/cost_floor.py`) | **2.243** pts statutory, vs 2.20 measured |

The irreducible part — statutory charges, immune to execution skill — is
**2.07–2.20 index points**, and it is the *larger* half of the cost. Perfect
limit fills do not touch it.

And it cannot be escaped by changing instrument. Across all four NSE index
contracts, cost per unit of daily sigma is **0.71%–0.79%** — a 1.11× spread —
because SEBI standardises index lot notional to ~₹16–18 lakh, and
`cost = cost_Rs / (delta × sigma × notional)`. Raw index points span 3.75× and
look like a large free choice; normalised, the choice does not exist.

### 3.2 Intraday NIFTY is predictable — and that is not enough

The strongest positive finding in the whole programme, and it still lost:

```
Spearman IC              0.053   sign-stable EVERY year 2022-2026
best predictable move    2.995 index points  (h=60)
cheapest all-in cost     2.73-4.01 index points
irreducible floor        2.07-2.20 index points  = 73% of the edge
```

NIFTY intraday is demonstrably not a random walk. The predictability is real,
survives correction for overlapping forward windows, and is sign-consistent
across four calendar years. **It is simply smaller than the cost of harvesting
it.** A larger sample would measure the same too-small effect more precisely.

`intraday-edgesize-modern` asked the decisive version directly — does *any*
intraday edge reach 8 index points out of sample? Best result anywhere: **7.70
against a 7.71 floor**, net −0.01, and that 7.70 rested on 41 trades in a single
year (2024 was −5.2). Eleven of eighteen cells had *negative* OOS gross before
costs: with nothing left to learn, fitting actively hurt.

### 3.3 The affordable contract and the efficient contract are different contracts

| bucket | cost (index pts) | lot cost | % of account | −40% stop |
|---|---|---|---|---|
| 20–40 | 5.40 | ₹1,879 | 16.4% | 6.5% |
| 40–80 | 3.67 | ₹3,629 | 31.6% | 12.6% |
| **80–160** | **2.73** | **₹7,508** | **65.4%** | **26.1%** |

Cost per index point is minimised at ATM; ATM is what this capital cannot hold.
Against Amendment E2's 1–2% per-trade risk budget, every contract that is cheap
enough to trade is too expensive to own. One lot is indivisible, so this is not
fixable by sizing down.

### 3.4 Frequency, not capital, was always the lever

Amendment E5: friction consumes **49–62%** of the gross edge a 15% CAGR requires,
and barely improves from ₹50k to ₹1L, because flat brokerage does not scale with
a net target. More capital would not have rescued any of these strategies.

Amendment E3 had already conceded the returns case: **₹11,250/yr against a ₹5,250
FD, for 780 hours of work.** The project was justified only as a validated system
tested where being wrong is cheap. It did that job, and the answer was no.

---

## 4. Why this negative result is credible

A negative result is only worth as much as the errors it caught on the way. Three
were caught in flight and are recorded in the kill log:

- **A lookahead artifact.** `rvol_ratio` was initially the *strongest* signal in
  the programme (IC 0.084, t 5.75, sign-consistent every year). Its normaliser
  used the whole-session median of realised vol, so the 10:00 value knew 15:00.
  With a past-only expanding median it fails at every horizon. **Lookahead hid in
  the normaliser, not the signal.**
- **Overlapping forward windows inflate |t| by ~√h.** Uncorrected, all 16 cells
  of screen 1 cleared §4 on sample size alone.
- **A permissive economic test.** The first version of condition (c) compared
  gross edge against the CAGR target *without subtracting friction at all*, and
  advanced three cells that lose money on every trade.

Each of these, left in, would have produced a "discovery."

---

## 5. The one arena left open, and why it does not change the verdict

`intraday_option` is not closed. It is **gated** by Amendment E10.2, which
requires 250 distinct trading days, 3 sign-consistent quarters, and 2 shock days
before any hypothesis may be registered against the accumulating sample. Current
state: **58 / 2 / 0**.

E10.2 was fixed *before* the data arrived, precisely because the early sample
already looks encouraging (+₹778/lot full-session short straddle, t=8.64) and a
bar set later is a bar set where the numbers look best. Amendment E10.1 records
that this t is inflated ~2.2× — 280 observations came from 56 independent days.

**The bar will most likely never be met:**

| | days | P(≥2 shock days) |
|---|---|---|
| run to 2027-01-07 | 158 | **27%** |
| run to the 250-day bar (≈ May 2027) | 250 | **58%** |
| run 300 more days | 358 | 81% |

Reaching 250 days requires **192 more trading sessions**, landing mid-2027 — and
even then the shock-day criterion is a coin flip. Shock days occur ~1-in-99 and
cannot be scheduled. **This is not a waiting problem that waiting solves.**

And meeting the bar would buy *permission to test one hypothesis*, not a
promotion — in an arena where Arena 1 already killed short volatility on
size-independent grounds, with a sample containing **zero shock days**, i.e. one
that has not yet sampled the tail that Arena 1 identified as the thing being paid
for.

**Extending the project to resolve this arena is not justified.** Closing it as
*unresolved* is the honest disposition.

---

## 6. What is explicitly NOT claimed

- **Not** that Indian markets are efficient. Two arenas closed on exhausted
  budget rather than demonstrated absence.
- **Not** that no retail edge exists at any capital. Everything here is measured
  at ₹50k–1L, where flat brokerage dominates. §3.4 is a statement about *this*
  account size.
- **Not** that the cost floor is permanent. It is a function of Indian F&O
  taxation. STT is the larger half; a change there changes the conclusion, and
  that is written into every arena's reopening conditions.
- **Not** a claim about discretionary trading. This programme tested systematic,
  pre-registered hypotheses only. It has **zero promotions and refuses live
  entries**, and therefore validates no discretionary approach whatsoever.

---

## 7. What continues

**The archive, and only the archive.**

`AgenticTrader-IntradayArchive` — weekdays 15:45 IST, ~11 minutes, one manual
token refresh a day. Current holding: **4.18M 1-minute bars, 1,188 contracts,
58 distinct option days.**

It continues for one reason: **Dhan deletes expired option intraday history
permanently, and it cannot be bought back at any price.** Verified 2026-08-14 —
four expired contracts from this project's own `order_audit` returned zero bars
against a live control returning 1,540. Every session archived is irreplaceable,
and the asset appreciates precisely because the source destroys it.

If any reopening condition is ever met — a change in F&O taxation, an instrument
with materially better exposure-per-rupee, or an edge of a genuinely different
order — the data will exist to test it immediately. Nothing else about this
project has that property.

---

## 8. What would reopen this

Per arena, from `research/kill_log.json`. Reopening requires a **charter
amendment**, and the conditions were written at closure, before any could be
observed.

For `intraday_index`, condition 1 is a measured all-in cost **below ~3.0 index
points for an instrument this account can actually hold**.

**This was tested on 2026-08-18 and failed.** The live measurement produced 2.73
points — but only in a bucket costing 65% of the account, and every affordable
bucket costs ≥3.67. The mechanism the condition anticipated (a taxation change,
or a better instrument) did not occur: statutory charges were unchanged and it
was the same 65-unit NIFTY lot. The number moved because an after-hours book was
replaced by a live one — **the same instrument measured better, not a cheaper
instrument.**

That test is the template. A reopening needs the *whole* condition, not the
threshold.

---

## 9. Disposition

1. **Research programme: STOPPED.** No further hypotheses registered.
2. **`intraday_option`: closed as UNRESOLVED**, on the E10.3 collision — the
   question is legitimate and the data to answer it will not arrive in time.
   This is absence of evidence and is labelled as such.
3. **Archive: CONTINUES**, unchanged, as a data asset rather than a research
   activity.
4. **Capital: not deployed.** No promotion was earned, so Section 5 was never
   satisfied and no live code path was opened.

The failure mode §7 was written to guard against was *"another two and a half
years of near-misses that never quite die."* This programme ran roughly five
months, registered fourteen falsifiable hypotheses, killed fourteen, and stopped.

That is the section working.
