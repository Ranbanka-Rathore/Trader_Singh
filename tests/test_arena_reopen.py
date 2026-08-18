"""Tests for Amendment F's reopening mechanism.

Section 7 says a closed arena stays closed: "No extensions." Every closure this
project writes also names `reopen_requires` — the evidence that WOULD justify the
charter amendment Section 7 otherwise forbids. Until 2026-08-18 nothing could act
on that field, so an amendment could only be prose, and prose no code consults is
the same hole that let the ladder reach live with Section 5 already written.

What these tests pin is not that reopening WORKS — that part is easy and
dangerous. They pin that it cannot be done quietly:

  * the closure record survives in `arena_history`, so no kill is erased;
  * a reopening without a named amendment, condition and justification is
    refused, because a reopening that cannot be audited later is just a retry;
  * an arena that was never closed cannot be "reopened".

Run with:  PYTHONUTF8=1 python tests/test_arena_reopen.py
"""
import os
import shutil
import sys
import tempfile

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from research import registry

PASS, FAIL = 0, 0


def check(name, cond):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  PASS  {name}")
    else:
        FAIL += 1
        print(f"  FAIL  {name}")


def raises(fn, *a, **kw):
    try:
        fn(*a, **kw)
        return False
    except registry.RegistryError:
        return True
    except Exception:
        return False


GROUNDS = "measured absence of a tradeable edge; the edge is smaller than the cost"
REOPEN = "1. a measured all-in cost below ~3.0 index points; 2. an edge of a different order (>=8 pts) from a genuinely different signal family"


def main():
    tmp = tempfile.mkdtemp(prefix="reopen_test_")
    old = registry.KILL_LOG_PATH
    registry.KILL_LOG_PATH = os.path.join(tmp, "kill_log.json")
    try:
        A = "intraday_index"

        # --- reopening something never closed is refused ------------------
        check("cannot reopen an arena that was never closed",
              raises(registry.reopen_arena, A, "F", "2", "because"))

        registry.close_arena(A, GROUNDS, REOPEN)
        check("arena reads closed after close_arena",
              registry.arena_closure(A) is not None)
        check("registration into a closed arena is refused",
              raises(registry.register, "x-1", A, "claim", "kill",
                     ["2022-01-01", "2026-01-01"]))

        # --- the three mandatory fields -----------------------------------
        check("reopening with no amendment is refused",
              raises(registry.reopen_arena, A, "", "2", "because"))
        check("reopening with no condition is refused",
              raises(registry.reopen_arena, A, "F", "", "because"))
        check("reopening with no justification is refused",
              raises(registry.reopen_arena, A, "F", "2", "   "))
        check("still closed after the refused attempts",
              registry.arena_closure(A) is not None)

        # --- a legitimate reopening ---------------------------------------
        rec = registry.reopen_arena(
            A, amendment="F", condition="2",
            justification="levels/swing/candlestick/gaps appear nowhere in the "
                          "tested family (momentum, VWAP, OR, vol state, range, "
                          "acceleration, time-of-day)",
            spent_on="pa-levels-modern")
        check("arena reads open after reopen", registry.arena_closure(A) is None)
        check("registration now succeeds",
              registry.register("pa-levels-modern", A, "claim", "kill",
                                ["2022-08-16", "2026-08-14"], n_configs=2,
                                engine="pa_levels") is not None)

        # --- THE POINT: the kill is not erased ----------------------------
        hist = registry.arena_history(A)
        check("closure is preserved in arena_history", len(hist) == 1)
        check("original grounds survive verbatim",
              hist and hist[0]["grounds"] == GROUNDS)
        check("the reopen_requires it was closed under survives",
              hist and hist[0]["reopen_requires"] == REOPEN)
        check("the amendment is recorded",
              hist and hist[0]["reopened_by_amendment"] == "F")
        check("the condition invoked is recorded",
              hist and hist[0]["reopened_under_condition"] == "2")
        check("what the reopening was spent on is recorded",
              hist and hist[0]["reopen_spent_on"] == "pa-levels-modern")
        check("history is filtered by arena",
              registry.arena_history("event_vol") == [])
        check("unfiltered history returns everything",
              len(registry.arena_history()) == 1)

        # --- an unknown arena is still an amendment, not a flag -----------
        check("unknown arena refused",
              raises(registry.reopen_arena, "not_an_arena", "F", "2", "x"))

    finally:
        registry.KILL_LOG_PATH = old
        shutil.rmtree(tmp, ignore_errors=True)

    print(f"\n{'=' * 50}\nRESULT: {PASS} passed, {FAIL} failed")
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
