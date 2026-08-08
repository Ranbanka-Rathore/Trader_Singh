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

## Arena 4 — event-driven volatility — **BLOCKED, not built**

No engine. Not because it is hard, but because the data to do it honestly does
not exist in this project, and building it on what *is* here would manufacture
exactly the kind of false positive Section 6 exists to reject.

**What exists:** `backend/app/core/regime_filters.py` carries 47 macro event
dates — FOMC, RBI MPC, budget — spanning **2024-01-31 to 2026-12-09**, with the
module's own caveat that they are "best-effort schedules compiled 2026-07" and
that "RBI MPC dates especially shift".

**Why that is not enough:**
1. **2.5 years of coverage** against a 10.5-year archive, all inside the modern
   liquidity era, so results could not be reported per era as Amendment B3
   requires.
2. **47 events** total. Section 3's detection rule needs an effect visible in
   ~30 observations; a calendar this thin gives at most one usable sample and no
   out-of-sample fold.
3. **Hand-compiled and unverified.** A shifted RBI date silently mislabels the
   event window, and the resulting "edge" would be a data-entry artefact. This is
   the same class of error as the settlement-price fills.
4. **No earnings dates at all**, so the single-stock half of the arena — the part
   with enough events to be statistically tractable — cannot be touched.

**To unblock it, in order:**
- Harvest NSE corporate announcements (board-meeting and results dates per
  symbol) back to 2016. Same shape of job as `backtest/bhavcopy.py`: a dated
  archive on disk, cached, with a quality report.
- Harvest RBI MPC dates from the RBI press-release archive, and budget dates,
  back to 2016 — both are published and datable, unlike the current hand list.
- Run `backtest/data_quality.py`-style checks over the result: no missing
  quarters, every date attributable to a source document.
- Only then write the engine.

Estimated effort is comparable to Phase 1's archive work. Until it is done, this
arena stays closed, and the honest position is that the survey covers three
arenas, not four.

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

### Arenas still worth a first look

Nothing above touches the **early** or **ramp** eras. Amendment B3 requires
per-era reporting, and all three engines run on the full 2016–2026 archive, so
the same hypotheses can be registered per era. Do that only with the era declared
up front — running `modern` and then reaching for `early` because `modern` failed
is a second draw from the same urn and must be registered with `--supersedes`.
