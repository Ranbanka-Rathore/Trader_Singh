"""Tests for the option margin model added 2026-08-10.

The gap this closes: `RealBacktester` sized by max loss and modelled no margin at
all, so an unhedged short was treated as free to hold and could be sized at a lot
count no broker would permit. That is Section 6.1's failure — a backtest modelling
a position that could not have been opened — reached by a different route.

Two things matter more than the numbers and both are tested here:

  * an undecided structure must RAISE, never default to zero margin;
  * the vertical path must be unchanged, because every result already in the
    kill log was produced by it and a silent shift would rewrite their meaning.

Run with:  PYTHONUTF8=1 python tests/test_margin.py
"""
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from backtest import margin

PASS, FAIL = 0, 0


def check(name, cond):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  ✅ {name}")
    else:
        FAIL += 1
        print(f"  ❌ {name}")


SPOT, LOT = 25_000.0, 75
EQUITY = 1_500_000.0


def test_unknown_structure_raises():
    print("\nan undecided structure is refused, not defaulted to free")
    for bad in ("ratio", "naked_short", "butterfly", "", "VERTICAL"):
        try:
            margin.margin_per_lot(bad, lot=LOT, spot=SPOT, width=200, credit=50)
            check(f"'{bad}' is refused", False)
        except margin.MarginError:
            check(f"'{bad}' is refused", True)

    try:
        margin.margin_per_lot("ratio", lot=LOT, spot=SPOT)
        check("the error explains why defaulting is wrong", False)
    except margin.MarginError as e:
        check("the error explains why defaulting is wrong",
              "no broker would allow" in str(e) or "zero margin" in str(e))


def test_unknowable_inputs_refuse():
    print("\nan unknown lot or spot cannot be silently worked around")
    for kw, label in (({"lot": 0}, "lot 0"), ({"lot": -5}, "negative lot"),
                      ({"spot": 0.0}, "spot 0")):
        args = {"lot": LOT, "spot": SPOT, "width": 200, "credit": 50}
        args.update(kw)
        try:
            margin.margin_per_lot("vertical", **args)
            check(f"{label} is refused", False)
        except margin.MarginError:
            check(f"{label} is refused", True)


def test_hedged_structures_are_arithmetic():
    print("\nhedged structures are max loss, which is arithmetic not estimate")
    m, basis = margin.margin_per_lot("vertical", lot=LOT, spot=SPOT,
                                     width=200, credit=50)
    check("vertical margin is (width - credit) x lot", abs(m - 150 * LOT) < 1e-6)
    check("  and says so", basis == "spread_max_loss")

    # Only one side of a condor can be breached, so it is the worse side.
    m, basis = margin.margin_per_lot("iron_condor", lot=LOT, spot=SPOT,
                                     width=200, call_width=300, credit=60)
    check("condor margin uses the WORSE side, not the sum",
          abs(m - (300 - 60) * LOT) < 1e-6)
    check("  and says so", basis == "condor_worse_side")

    # A spread can never require more than the naked equivalent.
    m, basis = margin.margin_per_lot("vertical", lot=LOT, spot=SPOT,
                                     width=100_000, credit=0)
    naked = margin.DEFAULT.naked_frac * SPOT * LOT
    check("an absurdly wide spread is capped at the naked requirement",
          abs(m - naked) < 1e-6 and basis == "naked_cap")


def test_calendar_is_strike_hedged():
    """The first draft margined this as naked, and test_real_backtester rejected it.

    The engine's calendar is short near / long far at the SAME strike, so the
    long leg covers assignment on the short and the economic risk is the net
    debit. Margining it as naked made the structure untradeable below ~Rs 10L,
    which is an artefact of the model rather than a fact about the market. The
    conservative direction is not automatically the correct one.
    """
    print("\na calendar is strike-hedged, so margin is its net debit")
    m, basis = margin.margin_per_lot("calendar", lot=LOT, spot=SPOT, credit=120)
    check("calendar margin is the net debit", abs(m - 120 * LOT) < 1e-6)
    check("  and the basis says so", basis == "calendar_net_debit")
    check("it is far below the naked requirement",
          m < margin.DEFAULT.naked_frac * SPOT * LOT)
    check("a Rs 5L account can still hold one",
          margin.lots_within_budget(m, 500_000.0, 0.30) >= 1)

    # The pessimistic view remains available, explicitly.
    strict = margin.MarginModel(calendar_short_is_naked=True)
    m2, basis2 = margin.margin_per_lot("calendar", lot=LOT, spot=SPOT,
                                       credit=120, model=strict)
    check("the naked view can be selected deliberately",
          abs(m2 - strict.naked_frac * SPOT * LOT) < 1e-6
          and basis2 == "calendar_short_treated_as_naked")
    check("  and it is what made the structure untradeable at Rs 5L",
          margin.lots_within_budget(m2, 500_000.0, 0.30) == 0)


def test_budget_arithmetic():
    print("\nlots within budget")
    check("a Rs 4.5L budget against Rs 8,800 of margin allows 51 lots",
          margin.lots_within_budget(8_800, EQUITY, 0.30) == 51)
    check("naked NIFTY margin allows only 2 lots at Rs 15L",
          margin.lots_within_budget(0.12 * SPOT * LOT, EQUITY, 0.30) == 2)
    for bad in ((0, EQUITY, 0.3), (100, 0, 0.3), (100, EQUITY, 0)):
        check(f"degenerate input {bad} floors at zero",
              margin.lots_within_budget(*bad) == 0)


def test_vertical_path_is_unchanged():
    print("\nthe vertical path must not have moved — prior results depend on it")
    # A typical closed-hypothesis vertical: 200pt width, Rs 50 credit, lot 75.
    # max loss Rs 11,250; risk_frac 1.5% of Rs 15L = Rs 22,500 -> l_risk 2.
    # Margin equals max loss, so l_margin = floor(450000/11250) = 40.
    m, _ = margin.margin_per_lot("vertical", lot=LOT, spot=SPOT,
                                 width=200, credit=50)
    l_margin = margin.lots_within_budget(m, EQUITY, 0.30)
    l_risk = int(0.015 * EQUITY / m)
    check("margin equals max loss for a vertical", abs(m - 11_250) < 1e-6)
    check("l_margin (40) is far slacker than l_risk (2), so min() is unchanged",
          l_margin == 40 and l_risk == 2 and l_margin > l_risk)


def test_engine_wiring():
    print("\nthe engine actually consults it")
    from backtest import real_backtester as rb
    cfg = rb.Config()
    check("config carries a margin model", isinstance(cfg.margin_model,
                                                      margin.MarginModel))
    check("and a budget fraction matching xsection's precedent",
          cfg.max_margin_frac == 0.30)
    src = open(os.path.join(os.path.dirname(__file__), "..", "backtest",
                            "real_backtester.py"), encoding="utf-8").read()
    check("_size_lots includes l_margin among its candidates",
          "l_margin" in src and "candidates = [l_risk, l_vol, l_margin]" in src)
    check("the calendar call site declares its structure",
          'structure="calendar"' in src)
    check("the condor call site declares its structure",
          'structure="iron_condor"' in src)


if __name__ == "__main__":
    test_unknown_structure_raises()
    test_unknowable_inputs_refuse()
    test_hedged_structures_are_arithmetic()
    test_calendar_is_strike_hedged()
    test_budget_arithmetic()
    test_vertical_path_is_unchanged()
    test_engine_wiring()
    print(f"\n{'=' * 50}\nRESULT: {PASS} passed, {FAIL} failed")
    sys.exit(1 if FAIL else 0)
