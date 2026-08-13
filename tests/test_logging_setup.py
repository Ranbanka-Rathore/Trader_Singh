"""Test logs must never land in the production log files.

Regression test for a real incident. On 2026-08-13 `logs/worker.log` — the file
an operator reads to find out what the live system did — contained lines saying
`[Ladder] NIFTY BULL_PUT_SPREAD tranche`, timestamped hours after every service
had been stopped by a power cut. They were synthetic test fixtures: importing
`worker` runs `setup_logging(...)` at module scope, so merely importing the code
under test opened the production log and wrote to it.

Two harms, and the second is the serious one:
  1. the production log becomes unusable as evidence
  2. `logging_setup`'s own docstring explains that two writers on one path breaks
     RotatingFileHandler on Windows — so running tests while a service is live is
     the exact rotation race that module was written to avoid

Run with:  PYTHONUTF8=1 python tests/test_logging_setup.py
"""
import logging
import os
import sys
from logging.handlers import RotatingFileHandler

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from backend.app.core import logging_setup as L

PASS, FAIL = 0, 0


def check(name, cond):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  ✅ {name}")
    else:
        FAIL += 1
        print(f"  ❌ {name}")


def test_detection():
    print("\n[1] Test entry points are detected as tests")
    # This file IS the entry point when run directly, and lives in tests/.
    check("this run is detected as a test", L._running_under_test() is True)
    check("and routes away from logs/", os.path.normpath(L.log_dir())
          != os.path.normpath(L.LOG_DIR))
    check("into the test log dir", os.path.normpath(L.log_dir())
          == os.path.normpath(L.TEST_LOG_DIR))


def test_env_override_wins():
    print("\n[2] An explicit AGENTIC_TRADER_LOG_DIR beats detection")
    prev = os.environ.get(L.LOG_DIR_ENV)
    try:
        os.environ[L.LOG_DIR_ENV] = os.path.join("logs", "explicit")
        check("override honoured", os.path.normpath(L.log_dir())
              == os.path.normpath(os.path.join("logs", "explicit")))
        os.environ[L.LOG_DIR_ENV] = "   "
        check("blank override ignored, falls back to detection",
              os.path.normpath(L.log_dir()) == os.path.normpath(L.TEST_LOG_DIR))
    finally:
        if prev is None:
            os.environ.pop(L.LOG_DIR_ENV, None)
        else:
            os.environ[L.LOG_DIR_ENV] = prev


def test_service_entry_points_still_use_production():
    print("\n[3] Service entry points are UNAFFECTED")
    # The fix must not divert real services. Simulate each service's argv[0],
    # which sits at the repo root, and confirm it still resolves to logs/.
    prev_argv, prev_env = sys.argv[:], os.environ.pop(L.LOG_DIR_ENV, None)
    prev_pytest = os.environ.pop("PYTEST_CURRENT_TEST", None)
    had_pytest = sys.modules.pop("pytest", None)
    try:
        for entry in ("run_quant.py", "run_oms.py", "run_risk_committee.py",
                      "run_harvester.py", "run_system_control.py"):
            sys.argv = [os.path.join(os.getcwd(), entry)]
            ok = os.path.normpath(L.log_dir()) == os.path.normpath(L.LOG_DIR)
            check(f"{entry} -> logs/", ok)
        # A checkout living under an unrelated .../tests/... path must not be
        # mistaken for a test run: only the IMMEDIATE parent counts.
        sys.argv = [os.path.join("C:", "tests", "checkout", "run_quant.py")]
        check("a repo nested under a 'tests' path is not misdetected",
              os.path.normpath(L.log_dir()) == os.path.normpath(L.LOG_DIR))
    finally:
        sys.argv = prev_argv
        if prev_env is not None:
            os.environ[L.LOG_DIR_ENV] = prev_env
        if prev_pytest is not None:
            os.environ["PYTEST_CURRENT_TEST"] = prev_pytest
        if had_pytest is not None:
            sys.modules["pytest"] = had_pytest


def test_handler_writes_where_it_says():
    print("\n[4] The handler actually opens the file it resolved")
    root = logging.getLogger()
    before = list(root.handlers)
    try:
        logger = L.setup_logging("LoggingSetupTest", "regression_probe.log")
        logger.info("probe")
        paths = [os.path.abspath(h.baseFilename) for h in root.handlers
                 if isinstance(h, RotatingFileHandler)]
        probe = [p for p in paths if p.endswith("regression_probe.log")]
        check("a rotating handler was attached", len(probe) == 1)
        if probe:
            parent = os.path.basename(os.path.dirname(probe[0]))
            check(f"it lives under logs/test (got '{parent}')", parent == "test")
            check("the file exists on disk", os.path.exists(probe[0]))
        # the production file must not have been opened by this run
        prod = [p for p in paths
                if os.path.basename(os.path.dirname(p)) == "logs"]
        check("no handler opened a production log path", not prod)
    finally:
        for h in list(root.handlers):
            if h not in before:
                h.close()
                root.removeHandler(h)


if __name__ == "__main__":
    test_detection()
    test_env_override_wins()
    test_service_entry_points_still_use_production()
    test_handler_writes_where_it_says()
    print(f"\n{'='*50}\nRESULT: {PASS} passed, {FAIL} failed")
    sys.exit(1 if FAIL else 0)
