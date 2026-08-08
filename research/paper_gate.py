"""The paper-sample gate — the last thing between a backtest and real money.

Section 5: after a strategy clears the walk-forward criteria it must "paper trade
with a pre-committed sample size (minimum 30 trades) before any real capital",
and the paper result must clear the targets on its own.

The sample counts only trades entered AFTER the paper promotion was recorded.
That is the whole point of a pre-committed sample: counting trades that already
existed would be selecting the evidence after seeing it, and there would be no
difference between this gate and the backtest it is supposed to check.

Split deliberately in two: `evaluate()` is a pure function over a list of trades
and can be tested exhaustively, while `load_paper_trades()` is the thin, boring
part that talks to Postgres.
"""
import datetime
import math
import os
import sys
from typing import Any, Dict, List, Optional

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from research import charter

# Section 5 states this one directly.
MIN_PAPER_TRADES = 30

# Amendment C. A 30-trade sample cannot support a significance test — per-trade
# sd on this instrument class is ~Rs 2,800, so the standard error at n=30 is
# ~Rs 511 and only effects of roughly Rs 1,000+ are visible. So the sample is not
# asked to PROVE the edge again; it is asked not to CONTRADICT the model that
# earned the promotion. Realised expectancy more than this many standard errors
# below the modelled figure falsifies the model rather than disappointing it.
MAX_ADVERSE_Z = 2.0


def _sd(values: List[float]) -> float:
    if len(values) < 2:
        return 0.0
    m = sum(values) / len(values)
    return math.sqrt(sum((v - m) ** 2 for v in values) / (len(values) - 1))


def _max_drawdown(pnls: List[float]) -> float:
    cum = peak = dd = 0.0
    for p in pnls:
        cum += p
        peak = max(peak, cum)
        dd = max(dd, peak - cum)
    return dd


def evaluate(trades: List[Dict[str, Any]], modelled: Dict[str, Any],
             min_trades: int = MIN_PAPER_TRADES) -> Dict[str, Any]:
    """Judge a paper sample. `trades` must already be filtered to the structure.

    Each trade: {exit_date, realized_pnl, live_priced, strategy_type}.
    `modelled`: {expectancy, dd_p99} taken from the promotion's stored evidence.
    """
    ordered = sorted(trades, key=lambda t: str(t.get("exit_date") or ""))
    synthetic = [t for t in ordered if not t.get("live_priced")]
    priced = [t for t in ordered if t.get("live_priced")]
    pnls = [float(t["realized_pnl"]) for t in priced]

    n = len(pnls)
    total = sum(pnls)
    expectancy = total / n if n else 0.0
    sd = _sd(pnls)
    se = sd / math.sqrt(n) if n > 1 and sd > 0 else 0.0
    dd = _max_drawdown(pnls)
    wins = [p for p in pnls if p > 0]
    gl = -sum(p for p in pnls if p <= 0)

    model_exp = float(modelled.get("expectancy") or 0.0)
    p99 = float(modelled.get("dd_p99") or 0.0)
    z = ((expectancy - model_exp) / se) if se > 0 else None

    checks: List[Dict[str, Any]] = []

    def check(name: str, passed: bool, detail: str):
        checks.append({"check": name, "passed": bool(passed), "detail": detail})

    # A synthetic row in a paper ledger means the live-pricing guard has a hole.
    # The 2026-07-30 purge removed 15 of 17 rows for exactly this, carrying
    # +Rs 7,531 of profit that was never real. A sample drawn from a ledger that
    # can contain fiction cannot be used to authorise money, whatever it says.
    check("ledger_is_live_priced", not synthetic,
          f"{len(synthetic)} non-live-priced row(s) in the window"
          + (f" (ids {[t.get('id') for t in synthetic][:5]}) — the entry pricing "
             f"guard leaked; fix that before reading this sample"
             if synthetic else " — every counted trade was priced off a real book"))

    check("sample_size", n >= min_trades,
          f"{n} closed live-priced trades vs the pre-committed minimum {min_trades}")

    check("positive_expectancy", expectancy > 0,
          f"Rs {expectancy:+,.0f}/trade over {n} trades (total Rs {total:+,.0f})")

    # Not "did it beat the model" — models are noisy at n=30 — but "did it come
    # in so far below the model that the model is wrong".
    if z is None:
        check("consistent_with_model", n < 2 or sd == 0,
              "not enough variation to compare against the model"
              if n >= 2 else "too few trades to compare")
    else:
        check("consistent_with_model", z > -MAX_ADVERSE_Z,
              f"realised Rs {expectancy:+,.0f}/trade vs modelled Rs {model_exp:+,.0f} "
              f"= {z:+.2f} SE (falsified below {-MAX_ADVERSE_Z:+.1f})")

    # Amendment A3's shutdown rule, applied before the money rather than after.
    if p99 > 0:
        check("drawdown_within_model", dd <= p99,
              f"realised max drawdown Rs {dd:,.0f} vs modelled p99 Rs {p99:,.0f}"
              + ("" if dd <= p99 else " — the drawdown model is falsified, not unlucky"))
    else:
        check("drawdown_within_model", False,
              "promotion carries no modelled p99 drawdown to check against")

    # A3 again: the operator signs off on a rupee figure, so surface it whether
    # or not it passes, scaled to the drawdown budget.
    budget = charter.DRAWDOWN_BUDGET_RS
    size_mult = (budget / p99) if p99 > 0 else None

    return {
        "n_trades": n,
        "n_synthetic": len(synthetic),
        "total_pnl": round(total, 2),
        "expectancy": round(expectancy, 2),
        "sd_per_trade": round(sd, 2),
        "standard_error": round(se, 2),
        "win_rate": round(len(wins) / n, 3) if n else 0.0,
        "profit_factor": round(sum(wins) / gl, 3) if gl > 0 else (999.0 if wins else 0.0),
        "max_drawdown": round(dd, 2),
        "modelled_expectancy": round(model_exp, 2),
        "modelled_dd_p99": round(p99, 2),
        "adverse_z": round(z, 3) if z is not None else None,
        "drawdown_budget": budget,
        "suggested_size_multiplier": (round(size_mult, 2)
                                      if size_mult is not None and size_mult < 1.0 else 1.0),
        "window": [str(ordered[0].get("exit_date")) if ordered else None,
                   str(ordered[-1].get("exit_date")) if ordered else None],
        "checks": checks,
        "verdict": "pass" if all(c["passed"] for c in checks) else "fail",
        "failed": [c["check"] for c in checks if not c["passed"]],
    }


# ── the boring half ──────────────────────────────────────────────────────────
def modelled_from(promotion_record: Dict[str, Any]) -> Dict[str, Any]:
    """The two model numbers the sample is judged against, from stored evidence."""
    ev = promotion_record.get("evidence") or {}
    return {
        "expectancy": float((ev.get("oos_metrics") or {}).get("expectancy") or 0.0),
        "dd_p99": float((ev.get("mc_bootstrap_dd") or {}).get("p99") or 0.0),
    }


async def load_trades(covers: List[str], since: datetime.datetime,
                      mode: str = "PAPER",
                      ticker: Optional[str] = None) -> List[Dict[str, Any]]:
    """Closed trades entered since `since`, for the covered strategy types.

    `mode` selects PAPER (the Section 5 sample) or LIVE (ongoing monitoring of a
    promoted strategy against its own drawdown model).

    `live_priced` is read from the trade's own learning_context, which is where
    execution_service stores the entry pricing source — the same field the
    2026-07-30 purge used to decide which rows were real.
    """
    from sqlalchemy import select
    from sqlalchemy.ext.asyncio import AsyncSession
    from sqlalchemy.orm import sessionmaker

    from backend.app.db.database import engine as db_engine
    from backend.app.db.models import Trade

    factory = sessionmaker(db_engine(), class_=AsyncSession, expire_on_commit=False)
    wanted = {str(c).upper() for c in covers}
    out: List[Dict[str, Any]] = []
    async with factory() as session:
        stmt = select(Trade).where(Trade.exit_date.isnot(None))
        rows = (await session.execute(stmt)).scalars().all()

    for r in rows:
        if str(r.mode or "").upper() != str(mode).upper():
            continue
        if wanted and str(r.strategy_type or "").upper() not in wanted:
            continue
        if ticker and str(r.ticker or "").upper() != ticker.upper():
            continue
        entered = r.entry_date
        if entered is not None and entered.tzinfo is not None:
            entered = entered.replace(tzinfo=None)
        if entered is not None and entered < since:
            continue
        ctx = r.learning_context or {}
        source = ((ctx.get("entry_pricing") or {}).get("pricing_source"))
        out.append({
            "id": r.id,
            "ticker": r.ticker,
            "strategy_type": r.strategy_type,
            "entry_date": entered,
            "exit_date": r.exit_date,
            "realized_pnl": float(r.realized_pnl or 0.0),
            "live_priced": source == "DHAN_LIVE",
            "pricing_source": source,
        })
    return sorted(out, key=lambda t: str(t.get("exit_date") or ""))
