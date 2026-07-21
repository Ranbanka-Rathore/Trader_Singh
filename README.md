# Trader Singh

An algorithmic options-selling (credit spread) trading system for Indian index/stock
options (NIFTY, BANKNIFTY, and select stocks), built as a set of Python microservices
around a shared regime-filter engine, with a React dashboard and [Dhan](https://dhan.co)
as the broker.

The system defaults to **paper trading**. Going live is a deliberate, explicit
configuration change — see [Trading mode](#trading-mode--safety).

> **Status**: research/validation framework paired with a live-capable deployment.
> Strategies are only wired live after passing walk-forward + Monte Carlo validation
> on real NSE settlement data — see [Validation](#validation--current-status) below.

## How it works

A shared, pure-function regime engine (`backend/app/core/regime_filters.py`) decides
whether and how to sell premium on a given day. It is used identically by the live
system and the backtester, by design, to avoid live/backtest drift. Five gates gate
entry:

1. **VRP** — only sell premium when implied vol is rich vs realized vol.
2. **Side/PCR** — Put-Call Ratio + EMA20 trend confirms directional bias.
3. **Efficiency Ratio** — blocks counter-trend entries in choppy conditions.
4. **Event blackout** — stands down around FOMC/RBI MPC/budget and monthly expiry.
5. **GEX sign** — stands down when dealer gamma exposure is negative (accelerating moves).

Two structures currently exist:

- **Sniper** — gated 5–8 DTE directional credit spreads, low trade frequency (~1/month).
- **Income ladder** (`LADDER_MODE=true`) — weekly 30–45 DTE tranches, managed at 21 DTE,
  IVR-scaled sizing, up to 6 concurrent positions, signal-driven entry gated only by
  "no position already open."

Trade lifecycle: quant scan for setups → ML win-probability filter (XGBoost, `models/`)
→ AI risk committee (multi-agent LLM audit) → order execution → ongoing position
management (delta hedging, trailing stops, PCR/GEX monitoring).

## Architecture

```
backend/app/
  core/       pure logic — Black-Scholes, regime filters, position sizing, risk shield
  services/   broker I/O, execution, order routing, ladder entry, market data, workers
  db/         SQLModel models + Postgres access
  main.py     FastAPI gateway — REST + WebSocket

frontend/     React 19 + TypeScript + Vite dashboard ("Glass Cockpit")
backtest/     real bhavcopy backtester + walk-forward/Monte Carlo validation harness
tests/        pytest suite
```

Each backend responsibility (`api`, `harvester`, `quant`, `committee`, `oms`, `worker`)
runs as its own process, communicating over Redis, with Postgres for persistence. See
`DEPRECATION.md` for a map of legacy root-level scripts superseded by `backend/app/`.

## Running it

### Docker Compose

```bash
cp .env.example .env   # fill in credentials, see below
docker compose up
```

Spins up Postgres, Redis, the API (`:8000`), and each backend microservice, plus the
frontend dev server (`:5173`).

### Native (Windows + WSL)

`start_v8.bat` starts Postgres/Redis in WSL, launches each backend microservice as a
native Python process from `venv/`, starts the frontend dev server, and runs
`check_health.py` to report system status.

### Environment variables

| Variable | Purpose | Default |
|---|---|---|
| `TRADING_MODE` | `PAPER` or `LIVE` | `PAPER` (fails safe on anything unrecognized) |
| `LADDER_MODE` | `true`/`false` — switch strategy from sniper to income ladder | off |
| `TRADING_EQUITY` | Capital base used for position sizing | — |
| `DHAN_CLIENT_ID` / `DHAN_ACCESS_TOKEN` | Dhan broker API credentials | — |
| `DB_HOST` / `DB_PORT` / `DB_USER` / `DB_PASSWORD` / `DB_NAME` | Postgres connection | — |
| `REDIS_HOST` / `REDIS_PORT` | Redis connection | — |
| `GROQ_API_KEY` | Powers the AI risk committee / forensic audit | optional, degrades gracefully if absent |

None of these are committed to the repo — `.env` is gitignored.

## Trading mode & safety

`trading_mode.py` is the single gate between paper and live trading. It reads
`TRADING_MODE` with a safe fallback to `PAPER` for any unrecognized value — there is no
accidental path to live order placement. `check_health.py` reports current mode and
credential presence before you start the system.

## Validation & current status

Strategies are validated on real NSE F&O bhavcopy settlement data (`backtest/bhavcopy.py`)
via an anchored walk-forward harness (`backtest/walkforward.py`): 6-month train / 1-month
test, rolling monthly, over a pre-registered parameter grid, with acceptance criteria on
walk-forward efficiency, out-of-sample profit factor, fold win rate, and deflated Sharpe,
plus bootstrap drawdown and slippage-stress checks. Regime gates are theory-fixed and
never part of the optimization grid.

- **Sniper**: validated at ₹5L equity.
- **Income ladder**: validated at ₹15L equity — 99 OOS trades, profit factor 3.56, passed
  all walk-forward criteria — and is the most recently live-wired strategy.
- Multi-strategy expansion and single-stock-options variants were tried and **rejected**
  by out-of-sample testing rather than shipped by default.

See `MANUAL.md` for the fuller architecture writeup, and `roadmap.md` /
`LADDER_LIVE_REWIRE_PLAN.md` for historical design context (these predate the current
phase and are not live status documents).

## Testing

```bash
pytest tests/
```

## Disclaimer

This is a research project for algorithmic trading in Indian derivatives markets. It is
not financial advice. Options trading carries substantial risk of loss. Nothing here is
an offer or recommendation to trade any instrument. Use at your own risk, and never run
`LIVE` mode against real capital without independently verifying the execution and risk
logic yourself.

## License

No license is currently specified — all rights reserved by default. Open an issue if
you'd like to discuss licensing terms for reuse or contribution.
