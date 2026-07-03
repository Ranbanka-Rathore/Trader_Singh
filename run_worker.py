"""
Trader Singh v7.0 — Worker Startup Script
Guarantees SelectorEventLoop is active for psycopg3/Windows compatibility.
Run with: python run_worker.py
"""
import os
import sys
import selectors
import asyncio

# ─── Force UTF-8 for print/logging ────────────────────────────────────────
if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
if hasattr(sys.stderr, 'reconfigure'):
    sys.stderr.reconfigure(encoding='utf-8', errors='replace')

def make_selector_loop():
    """Factory for SelectorEventLoop — passed to asyncio.run() as loop_factory."""
    return asyncio.SelectorEventLoop(selectors.SelectSelector())

async def main():
    """Async entry point — runs inside a guaranteed SelectorEventLoop."""
    from backend.app.services.worker import AutopilotWorker
    print("[WORKER] Initializing Autopilot AI Worker...")
    worker = AutopilotWorker()
    await worker.run()

if __name__ == "__main__":
    print("[WORKER] Trader Singh Autopilot Worker starting...")
    if os.name == 'nt':
        # Python 3.14+ uses loop_factory for clean SelectorEventLoop setup
        asyncio.run(main(), loop_factory=make_selector_loop)
    else:
        asyncio.run(main())
