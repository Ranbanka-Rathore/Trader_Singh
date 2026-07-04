"""
Database engine with lazy initialization.
The engine is created inside an async context to ensure it uses the correct
SelectorEventLoop that uvicorn/FastAPI sets up, not the module-level loop.
"""
import os
import asyncio
import socket
import subprocess
import logging
from typing import AsyncGenerator
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, AsyncEngine
from sqlalchemy.orm import sessionmaker
from sqlmodel import SQLModel
from dotenv import load_dotenv

load_dotenv()
logger = logging.getLogger("Database")

# ─── Connection parameters ─────────────────────────────────────────────────
DB_USER = os.getenv("DB_USER", "trader")
DB_PASSWORD = os.getenv("DB_PASSWORD", "institutional_grade_password")
DB_PORT = os.getenv("DB_PORT", "5432")
DB_NAME = os.getenv("DB_NAME", "agentic_trader")


def _get_wsl_ip() -> str | None:
    """Auto-discover WSL2 virtual NIC IP (changes on every WSL restart)."""
    try:
        result = subprocess.run(
            ["wsl", "-d", "Ubuntu", "hostname", "-I"],
            capture_output=True, text=True, timeout=5
        )
        if result.returncode == 0:
            parts = result.stdout.strip().split()
            if parts and parts[0] != "127.0.0.1":
                return parts[0]
    except Exception:
        pass
    return None


def _resolve_db_host() -> str:
    """
    Resolve the DB host.
    On Windows/WSL2, auto-discovery is more reliable than static IPs.
    """
    # Check if localhost:5432 responds first (covers native Windows PG or port forwarding)
    port = int(DB_PORT)
    try:
        s = socket.create_connection(("127.0.0.1", port), timeout=0.5)
        s.close()
        return "127.0.0.1"
    except (ConnectionRefusedError, OSError):
        pass

    # Fall back to WSL2 IP auto-detection
    wsl_ip = _get_wsl_ip()
    if wsl_ip:
        return wsl_ip

    # Last resort: env var
    env_host = os.getenv("DB_HOST", "127.0.0.1")
    return env_host


DB_HOST = _resolve_db_host()
DATABASE_URL = f"postgresql+psycopg://{DB_USER}:{DB_PASSWORD}@{DB_HOST}:{DB_PORT}/{DB_NAME}"
logger.info(f"[DB] URL: postgresql://{DB_USER}@{DB_HOST}:{DB_PORT}/{DB_NAME}")

# ─── Lazy engine & session factory ────────────────────────────────────────
# These are None at module import time and initialized inside an async context.
# This prevents the ProactorEventLoop binding issue on Python 3.14 Windows.
_engine: AsyncEngine | None = None
_AsyncSessionLocal = None


def _get_engine() -> AsyncEngine:
    """Lazily create the SQLAlchemy async engine on first call."""
    global _engine
    if _engine is None:
        _engine = create_async_engine(
            DATABASE_URL,
            echo=False,
            pool_size=20,
            max_overflow=40,
            pool_pre_ping=True,
            pool_recycle=300,
            pool_timeout=30,
            connect_args={"connect_timeout": 10},
        )
        logger.info("[DB] Engine created with pool_size=20, max_overflow=40.")
    return _engine


def _get_session_factory():
    """Lazily create the session factory on first call."""
    global _AsyncSessionLocal
    if _AsyncSessionLocal is None:
        _AsyncSessionLocal = sessionmaker(
            _get_engine(), class_=AsyncSession, expire_on_commit=False
        )
    return _AsyncSessionLocal


# Keep this alias for backward compatibility
def engine():
    return _get_engine()


async def init_db() -> bool:
    """Create all tables, retrying for WSL startup delays."""
    eng = _get_engine()
    for i in range(10):
        try:
            async with eng.begin() as conn:
                await conn.run_sync(SQLModel.metadata.create_all)
                # Alter table open_positions to add adjustment columns if they don't exist
                from sqlalchemy import text
                await conn.execute(text("ALTER TABLE open_positions ADD COLUMN IF NOT EXISTS is_adjusted BOOLEAN DEFAULT FALSE;"))
                await conn.execute(text("ALTER TABLE open_positions ADD COLUMN IF NOT EXISTS adjustment_count INTEGER DEFAULT 0;"))
                await conn.execute(text("ALTER TABLE open_positions ADD COLUMN IF NOT EXISTS original_net_credit NUMERIC(15, 2);"))
                await conn.execute(text("ALTER TABLE open_positions ADD COLUMN IF NOT EXISTS adjusted_net_credit NUMERIC(15, 2);"))
                # Phase 3: signal -> trade -> outcome attribution columns
                await conn.execute(text("ALTER TABLE signal_audit ADD COLUMN IF NOT EXISTS position_id INTEGER;"))
                await conn.execute(text("ALTER TABLE signal_audit ADD COLUMN IF NOT EXISTS realized_pnl NUMERIC(15, 2);"))
            logger.info(f"[DB] Connected & tables verified. (host={DB_HOST})")
            return True
        except Exception as e:
            short = str(e).split("\n")[0][:120]
            logger.warning(f"[DB] Attempt {i+1}/10 failed: {short}")
            await asyncio.sleep(3)  # 3s between retries (not 5s)

    logger.error("[DB] CRITICAL: Could not connect after 30 seconds.")
    return False


async def get_session() -> AsyncGenerator[AsyncSession, None]:
    """FastAPI dependency: yields a DB session per request."""
    factory = _get_session_factory()
    async with factory() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


async def dispose_engine():
    """Call on shutdown to cleanly close all pool connections."""
    global _engine
    if _engine:
        await _engine.dispose()
        _engine = None
        logger.info("[DB] Engine disposed.")
