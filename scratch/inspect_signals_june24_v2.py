import asyncio
import sys
import os
import warnings
from datetime import datetime, date

if os.name == 'nt':
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", DeprecationWarning)
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

sys.stdout.reconfigure(encoding='utf-8')

from sqlalchemy import select
from backend.app.db.database import get_session, init_db
from backend.app.db.models import SignalAudit

async def diagnose():
    await init_db()
    async for session in get_session():
        target_date = date(2026, 6, 24)
        result = await session.execute(
            select(SignalAudit)
            .order_by(SignalAudit.timestamp.asc())
        )
        signals = result.scalars().all()
        target_signals = [s for s in signals if s.timestamp.date() == target_date]
        print(f"Signals generated on 2026-06-24: {len(target_signals)}")
        for sig in target_signals:
            print(f"Time: {sig.timestamp.time().isoformat()}")
            print(f" Ticker: {sig.ticker}")
            print(f" Bias: {sig.pa_status}")
            print(f" PCR: {sig.pcr}")
            print(f" ML Score: {sig.ml_score}")
            print(f" Verdict: {sig.committee_verdict}")
            print(f" Reasoning: {str(sig.committee_reasoning)[:150]}...")
            print("---")
        break

if __name__ == "__main__":
    asyncio.run(diagnose())
