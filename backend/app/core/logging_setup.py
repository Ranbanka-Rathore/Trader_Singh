"""Shared logging setup for the v8 microservices.

Every service writes to its own file. That is deliberate, not an accident of
layout: RotatingFileHandler renames the active file on rollover, and on Windows
that rename fails — or silently strands the other writers on the old handle —
when a second process holds the same file open. One writer per file is what
makes rotation safe here. Pointing two services at one path reintroduces the
race that left an 85MB trader_singh.log.1 behind a 10MB rotation limit.

Handlers are attached to the root logger so third-party library output lands in
the service file too, matching how the services configured logging before.
"""

import logging
import os
import sys
from logging.handlers import RotatingFileHandler

LOG_DIR = "logs"
TEST_LOG_DIR = os.path.join("logs", "test")
LOG_DIR_ENV = "AGENTIC_TRADER_LOG_DIR"
LOG_FORMAT = "%(asctime)s - %(name)s - %(levelname)s - %(message)s"

# 10MB x 5 keeps a low-volume service's history for several sessions. Services
# that log per tick pass their own budget — see run_harvester.py.
DEFAULT_MAX_BYTES = 10 * 1024 * 1024
DEFAULT_BACKUP_COUNT = 5


def _running_under_test() -> bool:
    """True when the entry point is a test file rather than a service.

    Services import worker/quant/etc. at module scope, and those call
    `setup_logging` on import — so merely importing the code under test used to
    open the PRODUCTION log and write to it. On 2026-08-13 `logs/worker.log`
    carried lines reading `[Ladder] NIFTY BULL_PUT_SPREAD tranche` dated hours
    after every service had stopped: entirely synthetic test fixtures, sitting
    in the file an operator would read to find out what the live system did.

    That is worse than noise. This module's own docstring explains that two
    writers on one path breaks RotatingFileHandler on Windows, so a test run
    while a service is live is the exact rotation race it warns about.

    Detection is on the immediate parent directory of the entry script rather
    than anywhere in the path, so a checkout living under some unrelated
    `.../tests/...` directory does not silently divert production logs.
    """
    if os.getenv("PYTEST_CURRENT_TEST") or "pytest" in sys.modules:
        return True
    entry = sys.argv[0] if sys.argv else ""
    if not entry:
        return False
    parent = os.path.basename(os.path.dirname(os.path.abspath(entry)))
    return parent == "tests"


def log_dir() -> str:
    """Directory this process should log into.

    `AGENTIC_TRADER_LOG_DIR` wins if set, so a runner can be explicit instead of
    relying on detection; otherwise tests get `logs/test/` and services `logs/`.
    """
    override = (os.getenv(LOG_DIR_ENV) or "").strip()
    if override:
        return override
    return TEST_LOG_DIR if _running_under_test() else LOG_DIR


def setup_logging(
    logger_name,
    filename,
    level=logging.INFO,
    max_bytes=DEFAULT_MAX_BYTES,
    backup_count=DEFAULT_BACKUP_COUNT,
):
    """Attach a rotating file handler and a console handler to the root logger.

    Returns the named logger for the calling service. Safe to call twice — the
    file handler is only added once per path.
    """
    target_dir = log_dir()
    os.makedirs(target_dir, exist_ok=True)
    path = os.path.join(target_dir, filename)

    root_logger = logging.getLogger()
    root_logger.setLevel(level)
    formatter = logging.Formatter(LOG_FORMAT)

    already_attached = any(
        isinstance(h, RotatingFileHandler)
        and os.path.abspath(getattr(h, "baseFilename", "")) == os.path.abspath(path)
        for h in root_logger.handlers
    )
    if not already_attached:
        file_handler = RotatingFileHandler(
            path,
            maxBytes=max_bytes,
            backupCount=backup_count,
            encoding="utf-8",
        )
        file_handler.setFormatter(formatter)
        root_logger.addHandler(file_handler)

    # The services previously got their console output from basicConfig. Keep it,
    # but only once — basicConfig is a no-op after a handler exists on root.
    if not any(
        isinstance(h, logging.StreamHandler)
        and not isinstance(h, RotatingFileHandler)
        for h in root_logger.handlers
    ):
        stream_handler = logging.StreamHandler()
        stream_handler.setFormatter(formatter)
        root_logger.addHandler(stream_handler)

    return logging.getLogger(logger_name)
