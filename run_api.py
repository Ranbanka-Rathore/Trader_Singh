"""
Trader Singh v7.0 - Guaranteed Startup Entry Point

This is the ONLY correct way to start the API on Python 3.14 + Windows.

The problem: uvicorn's `loop="asyncio"` re-creates the event loop using Python's 
default which on Python 3.14+ Windows is ProactorEventLoop. psycopg3 and some
redis.asyncio operations don't support ProactorEventLoop.

The fix: Use asyncio.run() with an explicit loop_factory that creates SelectorEventLoop,
and pass loop="none" to uvicorn so it uses whatever loop asyncio.run() provides.
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

print("[STARTUP] Trader Singh v7.0 API starting...")
print(f"[STARTUP] Python {sys.version.split()[0]} on {sys.platform}")


def make_selector_loop():
    """Factory for SelectorEventLoop — passed to asyncio.run() as loop_factory."""
    return asyncio.SelectorEventLoop(selectors.SelectSelector())


async def main():
    """Async entry point — runs inside a guaranteed SelectorEventLoop."""
    import uvicorn
    config = uvicorn.Config(
        "backend.app.main:app",
        host="0.0.0.0",
        port=8000,
        loop="none",        # CRITICAL: tells uvicorn not to create its own loop
        log_level="info",
        reload=False,
    )
    server = uvicorn.Server(config)
    await server.serve()


if __name__ == "__main__":
    if os.name == 'nt':
        # Python 3.14 added asyncio.run(loop_factory=...) support
        asyncio.run(main(), loop_factory=make_selector_loop)
    else:
        asyncio.run(main())
