# Pre-registration — `pa-levels-modern`

**Written 2026-08-18. No price-action code exists at the time of writing. No
backtest of this family has been run. Nothing in this document was informed by a
result.**

Authorised by **Amendment F** (`RESEARCH_CHARTER.md`), which reopens
`intraday_index` once under its own pre-written reopening condition 2.

> **This document is the binding artifact.** The eventual
> `python -m research.loop register` call must reproduce the claim and kill
> criterion below **verbatim**. If the engine cannot implement a rule exactly as
> specified here, the rule is not silently adjusted — this document is amended
> with a dated note *before* the run, or the hypothesis is abandoned.

---

## 1. Provenance of the idea

Stated by the operator on 2026-08-18, after a discretionary trade that made
₹1,370.71 net. **That trade is n=1 and is not evidence** — it is the reason the
idea is being tested, not a result supporting it (§6.4).

The method as described: identify horizontal levels touched 2–3 times across
multiple timeframes; require approach structure (LH-LL into support, HH-HL into
resistance); require a candlestick reversal signal at the level on the 5-min;
wait for a retracement rather than entering on first touch; take the trade when
≥2 indications agree. Gap edges and round numbers also act as levels. PCR and
VWAP give supporting context.

## 2. Why this is admissible at all

`intraday_index` closed on measured absence of a tradeable edge. Reopening
condition 2 requires a signal family **genuinely unlike** the tested one. The
tested family, quoted from `kill_log.json`:

> *multi-lag momentum, VWAP deviation, opening-range position, realised-vol
> state, range position, acceleration and time-of-day*

Horizontal levels, swing structure, candlestick patterns and gap edges are absent
from that list. **VWAP is on it** — which is why VWAP enters here only as one
confluence input among several, never as the signal.

---

## 3. THE CLAIM (registered before running)

> On NIFTY 1-minute index bars, entries taken at a **pre-identified horizontal
> level** — confirmed by **≥2 independent confluence signals** and entered only
> on a **retest** rather than first touch — produce a **gross edge of ≥ 8.0 index
> points per round trip**, with the **same sign in every calendar year** having
> ≥60 trading days of data.
>
> Direction is not predicted: the rule is symmetric (long at support, short at
> resistance) and both must be tested together, so a sign flip between them is a
> failure rather than a finding.

### 3.1 Level construction — fixed, not searched

| element | rule |
|---|---|
| timeframes | 5-min, 15-min, 1-hour, daily |
| tolerance | **0.26 × median true range of that timeframe** |
| lookback | **60 sessions** |
| min touches | **2**, each reversing away by ≥1 × TR of that timeframe |
| gap edges | prior-bar high → next-bar low (and inverse) count as levels |
| round numbers | multiples of 100 within tolerance count as levels |

Tolerance provenance: the operator stated "50 points on the longer timeframe" on
2026-08-18 **before any result existed**. NIFTY's median daily true range over the
archive window is 195.3 points; 50 / 195.3 = **0.26**. One constant propagates to
every timeframe (5-min → 4.2 pts, 15-min → 7.5, 1-hour → 15.5, daily → 50.0), so
there is no per-timeframe grid to search.

Lookback provenance: **measured**, not chosen. 300 gaps over 1,972 sessions
(2016-01-01 .. 2023-12-29): median time to first touch is 1–3 sessions; ~95% are
touched within 60 sessions; extending to 120 buys ~1% more. 8–12% are never
touched within 400 sessions.

### 3.2 Confluence signals — entry requires ≥2

1. level touched ≥2 times within tolerance
2. approach structure aligned (LH-LL into support / HH-HL into resistance)
3. candlestick reversal at the level on the 5-min: hammer, inverted hammer, doji,
   bullish harami, bearish harami, engulfing (standard OHLC definitions)
4. level coincides with an unfilled gap edge
5. level coincides with a round number
6. price on the favourable side of session VWAP
7. **PCR** at a same-day extreme (outside its trailing 20-session 10th/90th pct)

Threshold provenance: operator stated "if there is 2-3 indications... I take the
trade" before any result. Fixed at **≥2**.

### 3.3 Retest rule — no entry on first touch

Operator: *"price never reverses immediately, there is a retracement back to that
reversal point consisting of 5-10 candles, then the actual reversal."*

Encoded: touch within tolerance → move away by ≥1 × TR(5-min) → return within
tolerance **within 10 five-minute bars** → entry on the next bar.

### 3.4 Exit — fixed in advance

Whichever comes first: the next opposing level within tolerance; adverse
excursion of 1 × TR(5-min) beyond the level; or session close. **No overnight
holds** — that is condition 3's territory and a different arena.

### 3.5 Measurement

- Window **2022-08-16 .. 2026-08-14**, matching Arena 5's screens so results are
  directly comparable.
- Edge quoted in **INDEX POINTS**, never rupees (Amendment E9).
- Gross, before costs — the 8.0 bar is a *gross* bar, with cost as the separate
  reference (3.67 pts all-in on the cheapest affordable contract, measured live
  2026-08-18).
- |t| corrected for overlapping forward windows (the ~√h inflation that made all
  16 cells of screen 1 look significant).
- Any per-session statistic must be `expanding()`, never a session-wide
  aggregate. **This is the `rvol_ratio` lookahead trap and it is the single most
  likely way this screen produces a false positive.**

---

---

## 3A. AMENDMENT — 2026-08-18, written BEFORE the screen was run

§3.2.3 said "standard OHLC definitions" for the candlestick set. Implementing
them showed that phrase is not a specification: every pattern needs numeric
thresholds, and each one is a free parameter this document failed to pin. Per the
rule at the top of this file, they are fixed here **before any run**, not chosen
afterwards.

| element | value |
|---|---|
| doji | body ≤ **0.10** × bar range |
| hammer / inverted hammer | body ≤ **0.35** × range, long shadow ≥ **2.0** × body, opposite shadow ≤ **0.30** × range |
| harami | this bar's body wholly inside the prior body, opposite colour |
| engulfing | this bar's body wholly contains the prior body, opposite colour |
| swing detection | zigzag with a **1.0 × true range** reversal threshold |

These are conventional textbook values, chosen without reference to any result —
no version of this screen has been run. They are recorded so that if a later
reader wonders whether 0.35 was tuned, the commit history answers it.

**A second, more substantive correction.** The first draft of `price_action.py`
assigned each candlestick shape a direction, putting `hammer` in the bullish set
and `inverted_hammer` in the bearish set. That is wrong, and wrong in a way that
would have quietly degraded the screen: **shape alone does not carry direction.**
A hammer at a low is bullish; the identical shape at a high is a hanging man and
is bearish. An inverted hammer at a low is bullish; the same shape at a high is a
shooting star.

Corrected encoding: `doji`, `hammer` and `inverted_hammer` are **direction-
neutral** reversal signals, and their direction comes from which kind of level
they occur at — which the symmetric long-at-support / short-at-resistance rule
already supplies. Only the two-bar patterns (harami, engulfing) carry a colour,
because they are defined against the prior bar's direction.

This **removes** a free choice rather than adding one: context now supplies
direction, exactly as a chart reader would read it.

---

## 4. THE KILL CRITERION (registered before running)

**KILLED unless ALL THREE hold together:**

- **(a)** gross edge **≥ 8.0 index points** per round trip, pooled over the window
- **(b)** the **same sign in every calendar year** with ≥60 trading days
- **(c)** |t| on the per-trade edge clears the Section 4 bar for the number of
  configurations actually run, after overlap correction

Clearing (a) but failing (b) is a sign-unstable estimate and is **not** a finding
(Amendment D5). Clearing (a) and (b) but failing (c) is recorded as real but
unproven, and is **not** carried forward.

Per Amendment B5 this screen **cannot promote anything**. Clearing the bar earns
a walk-forward test under Section 5, nothing more.

### What failure means

`intraday_index` returns to **closed**, and **condition 2 is spent** (Amendment
F4). A second price-action variant does not reopen it. The hypothesis is closed,
not tuned (Section 7).

---

## 5. Configuration budget

Pre-declared, to fix the Section 4 threshold before results exist:

| axis | values | n |
|---|---|---|
| confluence threshold | 2, 3 | 2 |
| direction | long-at-support, short-at-resistance | tested jointly, not separately |

**N = 2 configurations.** Section 4 bar at N=2 is |t| ≥ ~1.18; the binding
constraint is (a) and (b), not (c).

Everything else is pinned by §3. **Any additional axis explored later charges the
compounding budget and must be registered separately.**

## 6. Explicitly excluded from this registration

Head-and-shoulders, cup-and-handle, rising/falling wedges, double top/bottom.
Each admits many valid encodings, and each encoding is a free choice — precisely
the degrees of freedom that let `intraday-ceiling-modern` reach IC 0.0539 from
26 fitted features when one unfitted signal reached 0.0537.

They may be registered **only if this core survives**.

## 7. Pre-committed expectation

Recorded so the result cannot be re-read afterwards:

**The most likely outcome is a kill on (a).** The best edge Phase 2 measured at
any horizon, under any conditioning, was 5.39 index points; 8.0 is above
everything found so far. The specific reason to test anyway is that the family is
genuinely untested, and `intraday-edgesize-modern` showed regime conditioning
*does* scale edge with distribution width (high-vol mean OOS gross +2.21 vs low
−0.83) — level-based entries are a different conditioning of the same kind.

If it clears 8.0, the first thing to suspect is lookahead in the level
construction: a level built from bars the trade could not have seen is the same
error as `rvol_ratio`, wearing different clothes. **Levels must be constructed
only from bars strictly prior to the entry bar**, and that must be asserted in
code, not assumed.
