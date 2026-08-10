# The survey — four arenas, what is built, and what is already spent

Companion to `RESEARCH_CHARTER.md` Section 8. This file records the state of each
arena's engine, what has already been looked at, and the hypotheses drafted but
**not yet registered**. Nothing here has been written to the kill log.

---

## DISCLOSURE FIRST: what has already been seen

**Read this before registering anything.** Building the arena engines required
running them on real data, and those runs produced knowledge. Under Section 4 a
hypothesis must be registered *before* its result exists, so results already seen
cannot later be presented as discoveries. They are recorded here so that cannot
happen by accident.

Runs performed on 2026-08-08 for engine validation, all on the **default
configuration**, all under `gate=strict`, none registered:

| arena | window | result |
|---|---|---|
| futures trend (index) | 2023-01-01 → 2026-08-06 | 79 trades, −Rs 5,14,508, PF 0.48, Sharpe −1.12 |
| futures trend, walk-forward | same | 37 OOS trades, −Rs 6,09,908, PF 0.049, Sharpe −2.39; fails 7 of the acceptance criteria |
| cross-sectional (12-1 momentum) | 2022-01-01 → 2026-08-06 | 294 trades, −Rs 4,30,672, PF 0.93, Sharpe −0.26, capacity fill 68% |
| event vol (earnings short straddle) | 2024-01-01 → 2026-08-08 | 32 trades from 251 events, −Rs 30,115, PF 0.65, capacity fill 12.7% |

These were validation runs, not screens, and they were not cherry-picked — both
arenas lost badly on the first configuration tried, which is why there is no
temptation to re-present them. But the knowledge is spent either way:

- **The default configuration of each futures arena is no longer a live
  hypothesis.** Registering "Donchian breakout on index futures has positive
  expectancy" now would be registering a prediction whose answer is known.
- A registration in these arenas must therefore either test something genuinely
  not yet looked at, or be registered **as a confirmatory kill** — writing the
  known result into the log so the arena's history is complete and the
  multiple-comparisons budget is charged for it.

The cross-sectional run above also used a leverage default that was afterwards
judged infeasible (4x gross, producing a drawdown larger than the account) and
lowered to 2x. That change was made on risk grounds, before any registration, and
is disclosed here rather than left to look like tuning.

**A gate bug found on 2026-08-08 affects the three hypotheses already closed.**
`txns` is NaN — not zero — for every row before 2024, because the legacy NSE
schema has no trade-count column, and `float('nan') < 5.0` is False in Python. So
the `strict` gate's numeric floors were never enforced on pre-2024 data; they
silently passed. `cal-cheapvol-modern`, `xsect-mom-modern` and
`trend-donchian-modern` all ran 2023-01-01 → 2026-08-08, so their 2023 portion
was screened by a looser gate than the report claimed. The fix makes the gate
STRICTER, so all three kills stand a fortiori — a stricter fill rule cannot
rescue a strategy that already failed. The numbers in those reports are still
what was run, and are kept as such.

---

## Arena 1 — index-option structures beyond vanilla credit spreads

**Engine:** `research/engines/options.py` — an adapter over the existing
`RealBacktester`, so this arena runs the same code that produced the ladder
verdict rather than a reimplementation that might disagree with it.

**Already dead, do not re-sweep:**
- the income ladder — killed 2026-08-07, edge disappears under a real fill rule
- the 35–45 DTE band — walk-forward failed all four criteria, WFE −0.571
- the iron condor — rejected by walk-forward 2026-07-04: 18 OOS trades, net
  −4,936, double the friction of a directional spread

**Not yet tested:** the calendar spread (`enable_calendar`) has never been
validated — its cheap-vol regime never fired in a single test month, so it has
0 OOS trades rather than a bad result. That is the one genuinely open question
left in this arena, and it is a narrow one.

**Honest prior:** this is the most crowded arena available to Indian retail, and
the thing that has already failed here failed in its most liquid corner. Section
8's structural bet says the edge is unlikely to be here.

---

## Arena 2 — cross-sectional equities

**Engine:** `research/engines/xsection.py` on `backtest/futures.py`.
Rank the F&O stock universe on momentum, long the strongest and short the
weakest, rebalance on a fixed calendar, hold whole lots against a margin budget.

**What makes this arena's question different from the textbook one:** not "does
the cross-section have structure" — it does, everywhere — but "does any of it
survive whole lots and margin at Rs 15,00,000". A decile long/short over a
220-name universe wants ~44 positions. At 2x gross there is room for about six.
The engine takes names from the extremes inward until the margin budget is
exhausted and **counts the shortfall**, so if the arena dies on capacity rather
than on signal, that shows up as a number instead of as a mystery.

No survivorship bias: the universe is whatever the bhavcopy listed that day.

---

## Arena 3 — directional trend on liquid futures

**Engine:** `research/engines/trend.py` on `backtest/futures.py`.
Donchian channel breakout on the roll-adjusted series, volatility-scaled stops,
whole-lot sizing against a per-trade risk cap.

The charter's note on this arena is that the edge would live "in sizing and risk
management rather than structure", so the structure is deliberately plain and the
care went into roll-safe returns, whole lots, and real friction.

**Two limitations, stated in the engine's own docstring rather than buried:** no
interest is credited on the ~85% of equity not posted as margin (so long P&L is
understated and short P&L overstated by roughly the carry rate); and stops are
checked and filled at the close, so a gap through the level books the whole gap.

---

## Arena 4 — event-driven volatility — **OPEN (earnings half)**

**Engine:** `research/engines/eventvol.py` on `backtest/events.py`.
Sell the ATM straddle a few days before an earnings meeting, buy it back the day
after, on the nearest expiry that survives the event. Wings are bought by default
so the maximum loss is computable — a naked short straddle over an earnings gap
has unbounded risk and is inadmissible against a Rs 1,00,000 budget.

**The data problem is solved.** NSE publishes every listed company's
board-meeting intimation, and a meeting called to approve financial results IS
the earnings date. `backtest/events.py` harvests it:

- **80,185 earnings events, 2,745 symbols, 2016-2026**, 6,000-9,000 a year
- quality report passes with zero hard and zero soft faults, no empty quarters
- median advance notice 7-11 days in every year (p05 of 3-7 days)

That replaces the 47 hand-compiled macro dates that blocked this arena. It also
covers the half that matters: single-stock earnings is where the events are
numerous enough to be statistically tractable.

**Three vocabulary eras, and matching one loses years in silence** — the same
trap the bhavcopy loader hit twice:

| era | `bm_purpose` |
|---|---|
| 2016-2017 | `Results`, `Results/Dividend`, `Results/Others` |
| 2018-2024 | `Financial Results/...`, capitalisation varies |
| 2025- | often the generic `Board Meeting Intimation`, real subject in `bm_desc` |

Matching `"financial result"` against `bm_purpose` alone found **zero** events
before 2018 and dropped **18,600** rows in 2025-26. The quality report's
empty-quarter check is what caught it.

**The lookahead guard is the whole game here.** An earnings strategy that may
consult the calendar as of today is trivially profitable and worthless. Every
intimation carries both the meeting date and `bm_timestamp` — when the company
told the exchange — and `events_known_by()` is the only sanctioned way to ask
what was visible on a given day. The engine also enforces `min_notice_days`.

### The macro half — attempted 2026-08-08, STILL BLOCKED

`backtest/macro_events.py` now exists and does the useful part: harvesting,
per-source provenance, declared coverage, and a cadence quality report. What it
does not do is produce a calendar good enough to research on, and it says so
rather than shipping one.

| source | provenance | coverage | usable? |
|---|---|---|---|
| `fomc` | harvested from federalreserve.gov | 2016-01 – 2026-12 | **no** — fails its own cadence check |
| `rbi` | manual, from `regime_filters` | 2024-02 – 2026-12 | only inside that window |
| `budget` | manual, from `regime_filters` | 2024-02 – 2026-02 | only inside that window |

**FOMC is 8-for-11 years and the quality report hard-faults.** 2017, 2018 and
2023 come back two meetings short, and those two are always the ones straddling a
month boundary — "January 31–February 1", "October 31–November 1". The string
"January" appears *twice* in the whole of `fomchistorical2017.htm`, so the
meeting is not in the document to parse: a source problem, not a regex problem.
2019 separately returns nine against a known eight, so the parser over-matches
too. The right response to 8-for-11 is not to tune the parser until the counts
match a number already known — that is fitting to a target. The check stays
failing.

**RBI cannot be harvested at all.** Its press-release archive is ASP.NET
postback-driven: query parameters are ignored and the year dropdown is server
state, so there is no stable URL. The alternatives were a postback-simulating
scraper that breaks silently, or typing ~75 meeting dates from memory — the
second being exactly the unverified input Section 6 rejects, where being one day
wrong on a policy date manufactures an edge out of a typo.

**What was gained anyway:** `require_coverage()` means an under-covered source
now raises at load time instead of returning an empty list. That matters more
than it sounds — a short calendar produces no trades, and no trades is
indistinguishable from a strategy that does not work, which is how 47 hand-typed
dates sat in the codebase looking like a feature.

**To finish it**, in order: a source for RBI MPC dates that is not the website —
the RBI Bulletin PDFs or an official data release — then the same for Union
Budget dates, then a second FOMC source to cross-check the three short years
against. Until then, a macro hypothesis is registrable only inside 2024-02 to
2026-02, which is roughly 40 events across all three sources in one liquidity
era: enough to look, not enough to conclude.

---

## Drafted hypotheses — NOT REGISTERED

To register, run the command. To change one, change it before running: after the
screen has been seen, it cannot be edited without invalidating the result.

**Put every numeric part of a kill criterion in `--require`.** A threshold that
lives only in the prose of `--kill` is one nothing checks: `xsect-mom-modern` was
registered with "capacity fill rate below 50%" in its text, and confirming the
verdict meant re-running the engine by hand afterwards. `--require` thresholds go
into the fingerprint, are validated at registration against what the engine
actually reports, and are enforced by the screen. Fields are the standard screen
metrics plus whatever the engine measures about itself — `python -m research.loop
register --help` and a deliberately wrong `--require` will list them.

### A1 — the one genuinely open option structure

```
python -m research.loop register --id cal-cheapvol-modern \
  --arena index_structures --engine real_backtester --era modern --gate strict \
  --configs 1 \
  --set enable_calendar=true --set ladder_mode=false \
  --claim "Calendar spreads entered in the cheap-vol regime have positive per-trade expectancy in the modern era under a real fill rule." \
  --kill "Fewer than 20 trades, or screen t below the Section 4 bar, or any Section 6 evidence check fails."
```

*Note the likely outcome is "fewer than 20 trades" — the regime rarely fires.
That is a legitimate kill: a structure that cannot accumulate a sample cannot be
traded, and finding that out costs one screen.*

### A2 — cross-sectional, and its real question

```
python -m research.loop register --id xsect-mom-modern \
  --arena cross_sectional --engine cross_sectional --era modern --gate strict \
  --configs 1 \
  --require capacity_fill_rate_pct>=50 \
  --require max_drawdown<=100000 \
  --claim "12-1 cross-sectional momentum on F&O stock futures has positive per-trade expectancy at Rs 15L after whole-lot and margin constraints." \
  --kill "Screen t below the Section 4 bar, any Section 6 check fails, capacity fill rate below 50% — a portfolio that cannot be held is not a strategy — or a drawdown past the Rs 1,00,000 budget."
```

*The version actually registered on 2026-08-08 predates `--require` and carried
the capacity threshold in prose only. It was killed on t regardless, and the
capacity figure was confirmed by hand afterwards at 35.0%.*

### A3 — trend, as a confirmatory kill

```
python -m research.loop register --id trend-donchian-modern \
  --arena futures_trend --engine futures_trend --era modern --gate strict \
  --configs 1 \
  --note "CONFIRMATORY: the default configuration was run on 2026-08-08 during engine validation and failed. See ARENAS.md disclosure." \
  --claim "Donchian breakout on liquid index futures has positive per-trade expectancy in the modern era." \
  --kill "Screen t below the Section 4 bar or any Section 6 evidence check fails."
```

*Registered knowing the answer, so that the arena's history is on the record and
the budget is charged. It must not be counted as a prediction that was tested.*

### A4 — earnings volatility

```
python -m research.loop register --id evol-earnings-modern \
  --arena event_vol --engine event_vol --era modern --gate strict \
  --configs 1 \
  --require capacity_fill_rate_pct>=25 \
  --require max_drawdown<=100000 \
  --claim "Selling the ATM straddle into single-stock earnings and covering the day after has positive per-trade expectancy in the modern era under a real fill rule." \
  --kill "Screen t below the Section 4 bar, any Section 6 check fails, fewer than a quarter of known events tradeable, or a drawdown past the Rs 1,00,000 budget."
```

*Engine validation on 2024-01-01 to 2026-08-08 with a 25-name universe produced
32 trades from 251 events (12.7% fill) at PF 0.65 — the constraint was capacity,
with 164 events dropped because one lot of a four-leg stock butterfly exceeds
the risk cap. As with arenas 2 and 3, that default configuration is now known
and cannot be re-registered as a discovery; see the disclosure at the top.*

**Gate choice matters for pre-2024 windows.** The legacy NSE schema has no trade
count, so `strict` cannot be evaluated there and refuses everything as
`txns_unknown`. Use `--gate strict_legacy` on an `early` or `ramp` window.

### Arenas still worth a first look

Nothing above touches the **early** or **ramp** eras. Amendment B3 requires
per-era reporting, and all three engines run on the full 2016–2026 archive, so
the same hypotheses can be registered per era. Do that only with the era declared
up front — running `modern` and then reaching for `early` because `modern` failed
is a second draw from the same urn and must be registered with `--supersedes`.

---

## SURVEY, 2026-08-09 — how many independent bets does 1-leg futures supply?

**Disclosure: measured before drafting anything below.** No signal, no
positions, no P&L — this is unconditional correlation and drift of the
instrument set, the same class of measurement as the liquidity-era survey. It
does not spend a hypothesis, and it is recorded here so it cannot later be
presented as one.

Run: `futures.build_panel` over 2016-01-01 → 2026-08-08, `gate=strict_legacy`.

**Reproduce with** `scratch/arena3_survey.py` and `scratch/arena3_stock_indep.py`
(Findings 1–4), `scratch/arena3_passive_bench.py` and
`scratch/arena3_gate_year.py` (Findings 5–6). These were originally written to a
session scratchpad and were nearly lost; a finding whose reproduction path is a
temporary directory is not reproducible, so they now live in the repo.

Note the survey's "modern" window was **2024-01-01** → 2026-08-08, which is not
`charter.era_window("modern")`'s 2023-01-01. Finding 5 explains why the two
coincide in practice under `--gate strict`, and why they must not be conflated
when a registration declares a threshold "over the identical window".

### Finding 1 — the liquidity gate does not bind on futures at all

| universe | window | bars checked | fillable | pass rate |
|---|---|---|---|---|
| index (3 syms) | 2016–2026 | 6,602 | 6,485 | **98.2%** |
| stock (277 syms) | modern | 129,626 | 129,625 | **100.0%** |
| stock (219 syms) | ramp | 169,837 | 169,834 | **100.0%** |

The 117 index refusals are 64 unknown-lot, 52 settle-only, 1 no-OI — housekeeping,
not liquidity. **The failure mode that killed three of the five closed
hypotheses does not exist on this instrument.** That is the entire case for the
"fewer legs" direction, and it is now a number rather than a hope.

### Finding 2 — the index universe is one bet wearing three costumes

Daily roll-adjusted return correlation, full window:

| | BANKNIFTY | FINNIFTY | NIFTY |
|---|---|---|---|
| **BANKNIFTY** | 1.000 | 0.956 | 0.892 |
| **FINNIFTY** | 0.956 | 1.000 | 0.894 |
| **NIFTY** | 0.892 | 0.894 | 1.000 |

ρ̄ = 0.914 → **N_eff = 3 / (1 + 2ρ̄) = 1.06 independent bets.** FINNIFTY does not
exist before 2021-01-11, so the early era is two symbols at ρ 0.892 → N_eff 1.04.
It is 1.06 in every era.

Two consequences, and the second is a risk finding:

1. **`trend-donchian-modern`'s 79 trades were not 79 observations.** Breakouts on
   instruments correlated at 0.91 fire together, so with `max_open=3` over three
   symbols the concurrent positions are one position taken three times. Effective
   sample ≈ 79 × (1.06/3) ≈ **28** — at the Section 3 floor, not comfortably past
   it. The kill stands regardless: correlation *inflates* apparent significance,
   so a negative t is only more negative once deflated. But a *positive* t in this
   arena would have been overstated, which is what matters for anything registered
   next.
2. **`risk_frac=0.0075` is not the risk taken.** Three concurrent correlated
   positions risk ~2.25% of equity as one bet — Rs 33,750 against the Rs 1,00,000
   budget. Three such bets going wrong consecutively exhausts it. Any registration
   here should either set `max_open=1` or declare the concurrency-adjusted number.

### Finding 3 — stock futures buy independence, and capacity caps what you can buy

| universe | N names | ρ̄ | N_eff (all names) | N_eff at 6 held | at 8 held |
|---|---|---|---|---|---|
| stock, modern | 248 | 0.298 | 3.33 | 2.41 | 2.59 |
| stock, ramp | 203 | 0.307 | 3.22 | 2.36 | 2.54 |

Stock futures are ~3× as independent as the index set, but arena 2 already
established that Rs 15L holds **6–8 names**, so the reachable ceiling is
**~2.6 independent bets**, not 3.3. Capacity is the binding constraint for the
third time — but note the shape is different here: it caps the *diversification*
rather than refusing the trade.

What that ceiling costs, stated in the charter's own units. A book of k
equally-good bets reaches Sharpe ≈ s·√N_eff, so:

| universe | N_eff | standalone Sharpe needed **per bet** for a book Sharpe of 1.0 |
|---|---|---|
| index futures | 1.06 | **0.97** |
| stock futures @ 8 held | 2.59 | **0.62** |

A timing rule with per-bet Sharpe 0.97 on NIFTY is not a realistic thing to go
looking for. 0.62 per name across eight names is demanding but not absurd.
**This is the reason to draft on the stock universe rather than the index one**,
and it is arithmetic from measured correlations, not preference.

> **Correction, 2026-08-10 — read the two numbers above in the right units.**
> As first written this table said "standalone Sharpe needed for portfolio 1.0"
> and cited Section 2. Both halves were wrong, and the second one flattered the
> result.
>
> 1. **1.0 is not a portfolio target.** Amendment A supersedes Section 2
>    (`RESEARCH_CHARTER.md:199`) and sets portfolio minimum-viable at **1.4**,
>    worth-it 2.0, target 3.0. Portfolio Sharpe 1.0 is A4's explicitly *named
>    failure branch* — ~3.6% CAGR, Rs 54,000/yr, losing to the FD. 1.0 is the
>    **preferred individual** bar of A5 (floor 0.8), which is what these numbers
>    actually reach.
> 2. **The √N_eff conversion does not apply to the `sharpe` a screen reports.**
>    `engines/__init__.py:112` divides the whole book's daily P&L by `equity0`
>    and annualises — identical construction to the OOS Sharpe checked against
>    A5 at `walkforward.py:112` / `loop.py:141`. Diversification across the eight
>    names is *already inside* that number. So a `--require sharpe>=X` threshold
>    is in book units and must be compared to A5's 0.8/1.0 directly. The per-bet
>    column above explains *why* a book Sharpe of 0.8 is reachable on stocks and
>    not on the index; it is not itself a threshold anything can be set to.
>
> Net effect: clearing 0.62 per bet makes tsmom **one admissible strategy**, with
> 6–15 more still needed at ρ̄ ≤ 0.15 to reach the portfolio target. It does not
> mean the target is met.
>
> A third slip, in Finding 2 rather than here: concurrent risk across k positions
> scales as **k/√N_eff**, not k/N_eff. At `max_open=8` and ρ̄ 0.298 that is
> **4.97×** nominal per-position risk, not ~3×. At `risk_frac=0.0075` the 1σ
> concurrent figure is ~Rs 56,000 against the Rs 1,00,000 budget, not ~Rs 36,000.

### Finding 4 — roll-adjusted drift IS excess return, and the modern era has none

Because financing is embedded in the roll and the engine credits no interest on
idle margin (its stated limitation #1), these drifts already sit relative to the
risk-free rate. They are excess returns, and the right hurdle for them is 0%, not 7%.

| symbol | era | drift %/yr | vol %/yr | Sharpe |
|---|---|---|---|---|
| NIFTY | full 10.6y | +7.93 | 16.37 | 0.47 |
| NIFTY | modern | **−0.60** | 13.71 | **−0.04** |
| BANKNIFTY | full 10.6y | +9.63 | 21.46 | 0.43 |
| BANKNIFTY | modern | **+0.44** | 16.23 | **0.03** |
| FINNIFTY | modern | +3.59 | 16.38 | 0.22 |

Passive long index futures has returned essentially nothing over the risk-free
rate since 2024-01. `trend-donchian-modern` was screened over a window in which
its underlying had no risk premium to harvest. That does not reopen it — it is
closed — but it means a long-biased signal screened on the *full* window would
face a +7.9%/yr passive drift instead, and that changes what its t-statistic means.

### The benchmark gap this exposes

Section 1 names the benchmark as "a ~7% fixed deposit / **index fund**". The
screen does not implement the second half: its noise bar tests per-trade
expectancy against **zero**, not against buy-and-hold. For the five closed
hypotheses that never bit — they were short-vol or dollar-neutral, with no
structural beta to accidentally harvest. It bites immediately on any long-biased
futures signal, where holding beta in a rising market produces a positive t while
proving nothing.

**Fix, and it needs no new code:** any long-biased registration declares its
benchmark as a machine-checked `--require sharpe>=X`, where X is the passive
Sharpe over the identical window, taken from the table above. `sharpe` is already
a standard screen metric, so it goes into the fingerprint and is enforced at
verdict like any other threshold. A hypothesis that beats zero but loses to
holding the thing is then killed by the machinery instead of by whoever happens
to remember.

### Finding 5 — `--gate strict` silently deletes 2023 from the "modern" era

Measured 2026-08-10 (`scratch/arena3_gate_year.py`), stock panel, 2023-01-01 → today:

```
gate=strict         pass= 73.90%  fillable bars by year: {2024: 45493, 2025: 53607, 2026: 30522}
gate=strict_legacy  pass=100.00%  fillable bars by year: {2023: 45767, 2024: 45496, 2025: 53607, 2026: 30522}
```

`txns` is NaN before 2024, and the unevaluable-floor fix correctly refuses what it
cannot evaluate — so **every one of 2023's 45,767 bars is refused as
`txns_unknown`**. `charter.era_window("modern")` returns 2023-01-01, so a run
registered `--era modern --gate strict` is labelled with an era a third of which
is not in the sample. Nothing in the kill log says so.

Two consequences:

- **The `txns` floor earns nothing on this instrument.** Across 2024+ the two
  gates differ by **3 bars** out of ~130,000. `strict_legacy`'s floors — `traded`,
  `volume`, `oi` — are real liquidity tests; the `txns` floor here only decides
  which schema era survives. On stock futures `strict_legacy` is strictly better
  information, not a weaker gate.
- **The four closed `--era modern --gate strict` hypotheses ran on 2024+ only.**
  They are closed and stay closed — but a smaller sample makes a t-bar *harder*
  to clear, so this is a reason those kills might have been premature, not a
  reason to doubt them in the other direction. Recorded rather than reopened.

### Finding 6 — the passive stock benchmark, measured (what Finding 4's fix needs)

Finding 4 prescribed `--require sharpe>=X` at "the passive Sharpe over the
identical window" but only measured the *index*. Here is the stock number
(`scratch/arena3_passive_bench.py`, same `futures.build_panel` path as the survey).
248 names, 640 sessions, buy-and-hold, no signal:

| passive long book | Sharpe |
|---|---|
| equal-weight, all 248 names | **0.243** (+2.80%/yr, vol 18.07%) |
| random 8-name books | median **0.202**, p05 −0.345, p95 0.757 |
| NIFTY / BANKNIFTY, same window | −0.044 / +0.027 |

The number that matters is not the median but the **tail**, because a `--require`
bar is only worth declaring if a book with no signal fails it:

| bar | share of random 8-name buy-and-hold books that already clear it |
|---|---|
| `sharpe>=0.30` | **38.6%** |
| `sharpe>=0.50` | 19.5% |
| `sharpe>=0.62` | 10.9% |
| `sharpe>=0.80` | **4.0%** |
| `sharpe>=1.00` | 0.8% |

This kills the `sharpe>=0.30` in T1's original draft. It was justified against the
*index* passive Sharpe of ≈0.03, which is the wrong comparator for a stock book:
stock dispersion means one 8-name draw in three clears 0.30 on luck alone. The
single-name index figure understates the bar by an order of magnitude in
false-pass terms.

---

## Drafted, NOT REGISTERED — arena 3 redrawn on the stock universe

### T1 — time-series momentum on single-stock futures *(recommended first)*

The claim: judged against its **own** history rather than cross-sectionally,
a stock future's trailing return predicts its next return, enough to clear
the FD after whole lots, margin and friction.

Why this is not arena 2 again: `xsect-mom-modern` ranks names against **each
other** and is dollar-neutral by construction — it holds no market exposure and
died at t +0.13. A time-series rule judges each name against itself, so the book
carries net directional beta and its P&L is a different object. That distinction
is a claim, not an axiom, and it should be checked rather than asserted: the two
trade lists overlap in names, and if their P&L correlates above ~0.7 this is
arena 2 in a trend costume, exactly as `trend.py`'s docstring warns.

**The engine change is built** (2026-08-09). `TrendEngine` now takes
`signal=donchian|tsmom`, and `--set kind=stock --set universe=` reaches the stock
panel. What it does:

- `tsmom` enters on the sign of the return from `t-(mom_lookback+mom_skip)` to
  `t-mom_skip`, computed on the roll-safe compounded index, and exits on the stop,
  a **`signal_flip`** (its own trailing return stops pointing the way the position
  does), or `roll_out`. It has no channel exit.
- **`GRID` was not widened.** `walkforward.py` sets the hurdle at
  `sqrt(2 ln |grid| / T)`, so tsmom gets its **own** 8 combinations
  (`mom_lookback` 126/252 × `mom_skip` 0/21 × `stop_vol_mult` 2.5/4.0) and the bar
  `trend-donchian-modern` was judged against is untouched. An assertion in the
  engine fails the import if any signal's grid stops being 8 long.
- `donchian` remains the default and is byte-for-byte unchanged — a regression
  test asserts the implicit and explicit runs are the same run.
- An unrecognised signal name is refused at **registration**, not at run time, so
  a typo cannot put a fingerprint in the kill log describing a run that never
  happened.
- `warmup_days` is signal-aware: 439 calendar days at `mom_lookback=252` against
  60 for a 20-bar channel. Getting this wrong does not error — it silently starves
  the early folds and reports "does not trade".

**Four parameters settled 2026-08-10, before the run, on the evidence in Findings
5 and 6.** Recorded here so each is a decision with a reason rather than an
inherited default:

| parameter | chosen | why |
|---|---|---|
| `--gate` | `strict_legacy` | Finding 5 — `strict` deletes all of 2023 for want of a `txns` column, and buys 3 bars of filtering for a year of data |
| `--require sharpe` | `>=0.80` | Finding 6 — A5's individual floor; 4.0% passive false-pass, against 38.6% at the drafted 0.30 |
| `allow_short` | left at engine default `True` | tests the claim symmetrically — whether trailing return predicts in *both* directions, not only when the market rises |
| `max_open` / `risk_frac` | `8` / `0.0075` inherited | the Rs 1,00,000 budget is enforced by `--require max_drawdown<=100000` at screen and A3's `mc_bootstrap_dd` p99 at walk-forward, which bind on the realised path rather than on an analytic guess |

The Sharpe bar is anchored to **A5's individual floor of 0.8**, not to the passive
number. That is the deliberate choice: with `allow_short=True` the book is not
purely long, so passive-long is a floor rather than the right comparator — and
0.8 is the bar the charter already requires of any strategy admitted to the
portfolio. It happens to be the stricter of the two, at 4.0% passive false-pass.

```
python -m research.loop register --id tsmom-stock-modern \
  --arena futures_trend --engine futures_trend --era modern --gate strict_legacy \
  --configs 1 \
  --set kind=stock --set universe= \
  --set signal=tsmom --set max_open=8 \
  --require 'capacity_fill_rate_pct>=50' \
  --require 'max_drawdown<=100000' \
  --require 'sharpe>=0.80' \
  --claim "Time-series momentum on single-stock futures has positive per-trade expectancy at Rs 15L in the modern era, after whole lots, margin and a real fill rule, and reaches a book Sharpe of 0.8 — Amendment A5's individual admissibility floor." \
  --kill "Screen t below the Section 4 bar, any Section 6 check fails, capacity fill below 50%, a drawdown past Rs 1,00,000, or a book Sharpe below A5's individual floor of 0.8."
```

*What a pass means, stated before the number exists: tsmom clearing 0.8 makes it
**one admissible strategy**, not a solved portfolio. Amendment A still wants 6–15
of them at ρ̄ ≤ 0.15 to reach portfolio 1.4/2.0/3.0. See the correction under
Finding 3 — the older "0.62 clears Section 2" framing was a unit error and must
not be cited to argue a pass means more than this.*

*Quote the `--require` arguments in bash — an unquoted `>` is eaten by shell
redirection and silently writes a junk file.*

### T2 — the honest null: is arena 3 huntable at all?

Findings 2 and 3 together say the reachable ceiling in 1-leg futures is ~2.6
independent bets. In the corrected units that means a **per-bet** Sharpe of 0.50
to reach A5's individual floor of 0.8, or 0.62 to reach its preferred 1.0. If T1
comes back below that, the finding is not "tsmom does not work" but **"this arena
cannot supply an A5-admissible strategy at Rs 15L, whatever the signal"** — which
closes the arena rather than one hypothesis, and is worth more than another
variant. Register it only after T1, and only with the ceiling argument stated in
advance so it is a prediction rather than a consolation.

### Not drafted, and why

**A new signal on the index universe.** N_eff 1.06 means it needs a per-bet
Sharpe of 0.78 on essentially one instrument to reach A5's floor of 0.8, and 0.97
to reach its preferred 1.0 — the correction under Finding 3 does not rescue this
arena, because with no diversification to contribute the book Sharpe and the
per-bet Sharpe are very nearly the same number. Section 3's
corollary — discard effects too small to detect in ~30 trades — applies in the
other direction here: the effect required is so *large* that finding it would be
more surprising than not finding it. The index universe is where arena 3 should
stop, not where it should be redrawn.
