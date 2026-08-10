"""Tests for Amendment D5 — a pooled estimate must earn the pooling.

The failure this exists to prevent is concrete and already happened once. D2
permitted pooling eras for signal-property estimation, and the pooled IC re-run
returned +0.0263 for 12-1 momentum. That number is an average of one era where
momentum worked (early, IC +0.0636) and two where it did not (ramp +0.0052,
modern +0.0137). It described no market that has ever existed, and nothing in
the system would have stopped it being cited.

Every test here is about the two conditions either firing on that shape of data,
or refusing when they cannot be evaluated.

Run with:  PYTHONUTF8=1 python tests/test_charter_pooling.py
"""
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from research import charter

PASS, FAIL = 0, 0


def check(name, cond):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  ✅ {name}")
    else:
        FAIL += 1
        print(f"  ❌ {name}")


def test_the_real_case_that_prompted_d5():
    print("\nthe measured data D5 was written for (ARENAS T2c)")

    # 12-1 momentum: all three eras positive, so D5.1 passes -- but `early`
    # carries the whole estimate, which is what D5.2 is for.
    ok, why = charter.pooled_estimate_admissible(
        {"early": (0.0636, 34), "ramp": (0.0052, 36), "modern": (0.0137, 41)})
    check("mom_252_21 is refused — early carries it (D5.2)", not ok)
    check("  and the reason names D5.2, not D5.1",
          any("D5.2" in r for r in why) and not any("D5.1" in r for r in why))

    # mom_63_21 flips sign in modern, so D5.1 catches it first.
    ok, why = charter.pooled_estimate_admissible(
        {"early": (0.0601, 34), "ramp": (0.0082, 36), "modern": (-0.0106, 41)})
    check("mom_63_21 is refused — sign flips in modern (D5.1)", not ok)
    check("  and the reason names D5.1", any("D5.1" in r for r in why))

    # lowvol_63 flips in two eras.
    ok, _ = charter.pooled_estimate_admissible(
        {"early": (0.0724, 34), "ramp": (-0.0067, 36), "modern": (-0.0236, 41)})
    check("lowvol_63 is refused", not ok)


def test_a_genuinely_stable_estimate_passes():
    print("\nD5 must not refuse everything, or it is just a ban on pooling")
    ok, why = charter.pooled_estimate_admissible(
        {"early": (0.050, 34), "ramp": (0.045, 36), "modern": (0.048, 41)})
    check("consistent estimates across three eras are admissible", ok)
    check("  with no reasons given", why == [])

    # Same sign, and no era's removal moves the pooled figure by half.
    ok, _ = charter.pooled_estimate_admissible(
        {"ramp": (0.030, 50), "modern": (0.042, 50)})
    check("two eras, same sign, moderate spread — admissible", ok)


def test_fails_closed():
    print("\nunverifiable is not satisfied")
    ok, why = charter.pooled_estimate_admissible({"modern": (0.05, 41)})
    check("a single era cannot be cross-checked — refused", not ok and why)

    ok, _ = charter.pooled_estimate_admissible({})
    check("no eras at all — refused", not ok)

    ok, _ = charter.pooled_estimate_admissible(
        {"early": (0.05, 0), "modern": (0.05, 41)})
    check("an era with zero observations is dropped, leaving one — refused", not ok)

    ok, why = charter.pooled_estimate_admissible(
        {"early": (0.05, 30), "modern": (-0.05, 30)})
    check("a pooled estimate of exactly zero — refused", not ok and why)

    ok, why = charter.pooled_estimate_admissible(
        {"early": (0.05, 30), "ramp": (0.0, 30), "modern": (0.04, 30)})
    check("an exactly-zero era has no sign to be consistent with — refused",
          not ok and any("D5.1" in r for r in why))


def test_the_thresholds_match_the_document():
    print("\nthresholds are the charter's, not invented here")
    check("D5.2 reuses Amendment B's 50% materiality figure",
          charter.POOLED_DOMINANCE_LIMIT == charter.MATERIAL_EXPECTANCY_DRIFT == 0.50)

    # Just inside and just outside the dominance limit, to prove the bar bites
    # where the document says it does rather than approximately near there.
    inside, _ = charter.pooled_estimate_admissible(
        {"a": (0.040, 50), "b": (0.060, 50)})       # dropping either moves 20%
    check("a 20% leave-one-out drift is admissible", inside)
    outside, _ = charter.pooled_estimate_admissible(
        {"a": (0.010, 50), "b": (0.090, 50)})       # dropping either moves 80%
    check("an 80% leave-one-out drift is refused", not outside)


if __name__ == "__main__":
    test_the_real_case_that_prompted_d5()
    test_a_genuinely_stable_estimate_passes()
    test_fails_closed()
    test_the_thresholds_match_the_document()
    print(f"\n{'=' * 50}\nRESULT: {PASS} passed, {FAIL} failed")
    sys.exit(1 if FAIL else 0)
