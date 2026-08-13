"""Run every test file and report one total, so "N checks pass" is checkable.

Every commit message in this project ends with a check count. Until now that
number was assembled by hand, which is exactly the kind of claim this project
treats as worthless everywhere else: unverifiable, and wrong the moment a file
stops being counted. On 2026-08-13 a hand count produced 782 against an actual
794, and the file that had silently dropped out was one that had been failing
for over a month.

Two report formats exist in tests/ and both are read:
  "RESULT: <p> passed, <f> failed"   — most files
  "ALL <p>/<n> ... PASSED"           — order router, options pricing

A file that exits non-zero, or prints neither, is reported as UNCOUNTED rather
than skipped silently. An uncounted file is a failure of this runner, not a
pass.

Run: python tests/run_all.py
"""
import glob
import os
import re
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def run_one(path):
    """(passed, failed, status) for one test file."""
    # AGENTIC_TRADER_LOG_DIR is set explicitly rather than left to detection:
    # importing worker/quant configures logging at module scope, and a test run
    # must never land in the production logs an operator reads.
    env = dict(os.environ, PYTHONIOENCODING="utf-8", PYTHONUTF8="1",
               AGENTIC_TRADER_LOG_DIR=os.path.join("logs", "test"))
    r = subprocess.run([sys.executable, path], capture_output=True, text=True,
                       encoding="utf-8", errors="replace", cwd=ROOT, env=env)
    out = (r.stdout or "") + (r.returncode and (r.stderr or "") or "")

    m = re.findall(r"RESULT:\s*(\d+)\s*passed,\s*(\d+)\s*failed", out)
    if m:
        p, f = map(int, m[-1])
        return p, f, ("ok" if r.returncode == 0 and not f else "failed")

    m = re.search(r"ALL\s+(\d+)/(\d+)", out)
    if m and r.returncode == 0:
        return int(m.group(1)), 0, "ok"

    # No parseable report. Never counted as zero-and-fine.
    return 0, 0, f"UNCOUNTED (exit {r.returncode})"


def main():
    files = sorted(glob.glob(os.path.join(ROOT, "tests", "test_*.py")))
    tp = tf = 0
    bad = []
    for path in files:
        p, f, status = run_one(path)
        tp += p
        tf += f
        name = os.path.basename(path)
        if status != "ok":
            bad.append((name, status))
            print(f"  {name:<34} {p:>4} passed, {f:>3} failed   <-- {status}")
        else:
            print(f"  {name:<34} {p:>4} passed, {f:>3} failed")

    print(f"\nTOTAL: {tp} checks pass, {tf} failures, across {len(files)} files")
    if bad:
        print("\nNOT COUNTED — fix these before quoting a total:")
        for name, status in bad:
            print(f"  {name}: {status}")
        return 1
    return 0 if tf == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
