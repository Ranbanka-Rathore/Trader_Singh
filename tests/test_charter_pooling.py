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




def test_power_derivations():
    """Section 4's bar and A5's floor are one constraint (added 2026-08-10)."""
    print("\ndetectable Sharpe, config budget, and A5's sample")
    import math

    # sqrt(2 ln N) / sqrt(Y), checked against hand arithmetic.
    check("detectable Sharpe is the noise bar over sqrt(years)",
          abs(charter.detectable_sharpe(11, 3.60)
              - charter.noise_threshold(11) / math.sqrt(3.60)) < 1e-9)
    check("more configs make the smallest visible Sharpe larger",
          charter.detectable_sharpe(20, 3.60) > charter.detectable_sharpe(1, 3.60))
    check("a longer window makes it smaller",
          charter.detectable_sharpe(11, 10.6) < charter.detectable_sharpe(11, 3.60))
    check("a zero-length window can detect nothing",
          charter.detectable_sharpe(1, 0) == float("inf"))

    # The measured consequence: on the modern era, 3 configs is the ceiling.
    check("modern era supports at most 3 configs at A5's floor",
          charter.max_configs_for_detectability(3.60) == 3)
    check("the full archive supports far more",
          charter.max_configs_for_detectability(10.6) >= 20)
    check("the returned budget really does keep A5's floor visible",
          charter.detectable_sharpe(charter.max_configs_for_detectability(3.60),
                                    3.60) <= charter.MIN_OOS_SHARPE)
    check("  and one more config would not",
          charter.detectable_sharpe(charter.max_configs_for_detectability(3.60) + 1,
                                    3.60) > charter.MIN_OOS_SHARPE)
    check("an impossibly short window supports zero configs",
          charter.max_configs_for_detectability(0.01) == 0)

    # A5's sample supply, from the anchored walk-forward's geometry.
    need = charter.trades_needed_for_a5(3.60)
    check("modern era needs ~116 trades for 100 OOS", 110 <= need <= 122)
    check("a longer window needs fewer per year",
          charter.trades_needed_for_a5(10.6) / 10.6 < need / 3.60)
    check("a window shorter than the training warm-up is unsatisfiable",
          charter.trades_needed_for_a5(0.4) == float("inf"))

if __name__ == "__main__":
    test_the_real_case_that_prompted_d5()
    test_a_genuinely_stable_estimate_passes()
    test_fails_closed()
    test_the_thresholds_match_the_document()
    test_power_derivations()
    print(f"\n{'=' * 50}\nRESULT: {PASS} passed, {FAIL} failed")
    sys.exit(1 if FAIL else 0)
