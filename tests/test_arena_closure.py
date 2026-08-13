"""Tests for charter Section 7's per-arena kill rule.

Section 7 says a spent arena "is closed. No extensions." That sentence existed
from 2026-08-07 and nothing in the code consulted it — the same shape of hole as
the Section 5 promotion gate, which existed while the ladder went live having
failed all four of its criteria. These tests are the binding.

They also pin the two things a closure must carry. An arena closed because no
edge was demonstrated and one closed because the question cannot be resolved with
the data available are different claims, and a closure that does not say which
cannot be audited later.

Run with:  PYTHONUTF8=1 python tests/test_arena_closure.py
"""
import os
import shutil
import sys
import tempfile

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from research import charter, registry

PASS, FAIL = 0, 0


def check(name, cond):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  ✅ {name}")
    else:
        FAIL += 1
        print(f"  ❌ {name}")


def _sandbox():
    tmp = tempfile.mkdtemp()
    saved = (registry.KILL_LOG_PATH, registry.SURVIVORS_DIR, registry.RESULTS_DIR)
    registry.KILL_LOG_PATH = os.path.join(tmp, "kill_log.json")
    registry.SURVIVORS_DIR = os.path.join(tmp, "survivors")
    registry.RESULTS_DIR = os.path.join(tmp, "results")
    return tmp, saved


def _restore(tmp, saved):
    registry.KILL_LOG_PATH, registry.SURVIVORS_DIR, registry.RESULTS_DIR = saved
    shutil.rmtree(tmp, ignore_errors=True)


def _register(hid, arena="futures_trend"):
    return registry.register(
        hid=hid, arena=arena, claim="a claim", kill_criterion="a kill criterion",
        window=["2023-01-01", "2026-08-10"], gate="strict")


def test_closure_blocks_registration():
    print("\nSection 7: no extensions")
    tmp, saved = _sandbox()
    try:
        _register("h1")
        registry.add_event("h1", "SCREEN", "KILLED", {}, status="killed")
        registry.close_arena("futures_trend", grounds="spent",
                             reopen_requires="a materially longer dataset")
        check("arena_closure reports the closure",
              registry.arena_closure("futures_trend")["status"] == "closed")
        check("an open arena reports None",
              registry.arena_closure("event_vol") is None)

        try:
            _register("h2")
            check("registering into a closed arena is refused", False)
        except registry.RegistryError as e:
            check("registering into a closed arena is refused", True)
            check("  and the refusal quotes the grounds", "spent" in str(e))
            check("  and names what would reopen it",
                  "materially longer dataset" in str(e))

        # A different arena is untouched. Distinct window, or the
        # configuration-identical guard fires before the arena check is reached.
        registry.register(hid="h3", arena="event_vol", claim="c",
                          kill_criterion="k", engine="event_vol",
                          window=["2024-01-01", "2026-08-10"], gate="strict")
        check("other arenas keep accepting registrations",
              registry.get("h3") is not None)
    finally:
        _restore(tmp, saved)


def test_a_closure_must_say_what_it_means():
    print("\na closure with no grounds cannot be audited")
    tmp, saved = _sandbox()
    try:
        for grounds, reopen, label in (
                ("", "something", "empty grounds"),
                ("   ", "something", "whitespace grounds"),
                ("real grounds", "", "empty reopen condition")):
            try:
                registry.close_arena("futures_trend", grounds=grounds,
                                     reopen_requires=reopen)
                check(f"{label} is refused", False)
            except registry.RegistryError:
                check(f"{label} is refused", True)
    finally:
        _restore(tmp, saved)


def test_cannot_close_over_unresolved_hypotheses():
    print("\nan arena cannot reach a verdict its hypotheses have not")
    tmp, saved = _sandbox()
    try:
        _register("open-one")
        try:
            registry.close_arena("futures_trend", grounds="g", reopen_requires="r")
            check("closing over an unresolved hypothesis is refused", False)
        except registry.RegistryError as e:
            check("closing over an unresolved hypothesis is refused", True)
            check("  and the error names it", "open-one" in str(e))
        registry.add_event("open-one", "SCREEN", "KILLED", {}, status="killed")
        registry.close_arena("futures_trend", grounds="g", reopen_requires="r")
        check("once every hypothesis is closed, the arena can close",
              registry.arena_closure("futures_trend") is not None)
    finally:
        _restore(tmp, saved)


def test_closure_is_permanent_and_recorded():
    print("\nclosure is permanent and carries its record")
    tmp, saved = _sandbox()
    try:
        _register("h1")
        registry.add_event("h1", "SCREEN", "KILLED", {}, status="killed")
        rec = registry.close_arena(
            "futures_trend", grounds="resolvability, not absence of edge",
            reopen_requires="detectable IC well below 0.04",
            evidence=["ARENAS T2", "ARENAS T2b"])
        check("the record lists the hypotheses the arena registered",
              rec["hypotheses"] == ["h1"])
        check("the record keeps the evidence pointers",
              rec["evidence"] == ["ARENAS T2", "ARENAS T2b"])
        check("grounds are preserved verbatim",
              rec["grounds"] == "resolvability, not absence of edge")
        try:
            registry.close_arena("futures_trend", grounds="g2", reopen_requires="r2")
            check("closing twice is refused", False)
        except registry.RegistryError:
            check("closing twice is refused", True)
        check("the record survives a reload",
              registry.arena_closure("futures_trend")["grounds"]
              == "resolvability, not absence of edge")
    finally:
        _restore(tmp, saved)


def test_unknown_arena():
    print("\nan arena the charter does not list cannot be closed")
    tmp, saved = _sandbox()
    try:
        try:
            registry.close_arena("crypto_scalping", grounds="g", reopen_requires="r")
            check("unknown arena is refused", False)
        except registry.RegistryError as e:
            check("unknown arena is refused", True)
            check("  and the error points at Section 8", "Section 8" in str(e))
    finally:
        _restore(tmp, saved)


def test_existing_logs_load():
    print("\nlogs written before arena closure existed still load")
    tmp, saved = _sandbox()
    try:
        with open(registry.KILL_LOG_PATH, "w", encoding="utf-8") as f:
            f.write('{"version": 1, "hypotheses": []}')
        log = registry.load()
        check("a log with no 'arenas' key gains an empty one",
              log["arenas"] == {})
        check("and nothing reads as closed",
              all(registry.arena_closure(n) is None for n in charter.ARENAS))
    finally:
        _restore(tmp, saved)


if __name__ == "__main__":
    test_closure_blocks_registration()
    test_a_closure_must_say_what_it_means()
    test_cannot_close_over_unresolved_hypotheses()
    test_closure_is_permanent_and_recorded()
    test_unknown_arena()
    test_existing_logs_load()
    print(f"\n{'=' * 50}\nRESULT: {PASS} passed, {FAIL} failed")
    sys.exit(1 if FAIL else 0)
