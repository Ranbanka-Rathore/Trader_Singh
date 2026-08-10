"""Margin required to hold an index-option structure — the gap found 2026-08-10.

`RealBacktester` sized positions by max loss alone and modelled no margin at all.
For a vertical spread that is close to harmless, because broker margin on a
defined-risk spread is roughly its max loss and max loss WAS modelled. It is not
harmless for anything else: with no margin model, an unhedged short is treated as
costing nothing to hold, so the engine would size a naked or ratioed structure at
a lot count no broker would permit. That is the Section 6.1 failure in a
different costume — a backtest modelling a position that could not have been
opened.

The fix is not a number, it is a refusal. `margin_per_lot` covers the three
structures the engine can actually emit and **raises on anything else**, so
adding a structure without deciding how it is margined fails loudly at the point
of sizing instead of silently assuming zero.

WHERE THE NUMBERS COME FROM, stated plainly because it matters:

  * The hedged cases (`vertical`, `iron_condor`) are ARITHMETIC, not estimates.
    A defined-risk spread cannot lose more than its width less its credit, only
    one side of a condor can be breached at expiry, and Indian brokers margin
    these at approximately that figure.
  * `naked_frac` is an ESTIMATE and is not measured. The bhavcopy archive carries
    no margin data, so there is nothing here to derive it from. 0.12 is anchored
    to the 0.15 that `research/engines/xsection.py` already uses for SPAN+exposure
    on stock futures, adjusted down because index vol is lower. It is a
    parameter, it is documented as an estimate, and it should not be quoted as if
    it were measured.

WHAT THIS DOES AND DOES NOT CHANGE. Checked after writing it: **none of the three
structures the engine currently emits carries unhedged exposure.** A vertical and
a condor are strike-hedged within one expiry, and the calendar it builds is short
near / long far AT THE SAME STRIKE, so the long leg covers assignment on the
short. Margin therefore equals max loss in all three cases, `l_margin` is slack
against `l_risk` by more than an order of magnitude, and every result already in
the kill log is unchanged.

That is the honest description of the gap: it was **latent, not active**. It
would have bitten the moment a ratio or a naked leg was added — precisely the
structures Section 8 points arena 1 at — and it would have bitten silently,
because zero margin is indistinguishable from cheap margin in a P&L.

The first draft defaulted a calendar's short leg to naked margin on the grounds
that NSE's spread benefit lapses at the near expiry. `tests/test_real_backtester.py`
rejected it: at Rs 5L the structure became untradeable, which is a modelling
artefact rather than a market fact, since the far long is what makes the near
short safe. Recorded because the conservative direction is not automatically the
correct one, and here it was not.
"""
from dataclasses import dataclass
from typing import Tuple

STRUCTURES = ("vertical", "iron_condor", "calendar")


class MarginError(Exception):
    """A structure whose margin treatment has not been decided. Never defaulted."""


@dataclass(frozen=True)
class MarginModel:
    """SPAN + exposure for an index option structure, per lot.

    `naked_frac` is a share of NOTIONAL (spot x lot), which is how SPAN+exposure
    is quoted. See the module docstring: it is an estimate, not a measurement.
    """
    naked_frac: float = 0.12
    # The engine's calendar is short near / long far at the SAME STRIKE, so the
    # long leg covers assignment on the short and the economic risk is the net
    # debit. Set True for the pessimistic view — NSE's spread benefit does lapse
    # at the near expiry — but be aware that it makes the structure untradeable
    # below roughly Rs 10L, which is a modelling artefact and not a market fact.
    calendar_short_is_naked: bool = False
    name: str = "nse_index"


DEFAULT = MarginModel()


def margin_per_lot(structure: str, *, lot: int, spot: float,
                   width: float = 0.0, credit: float = 0.0,
                   call_width: float = 0.0,
                   model: MarginModel = DEFAULT) -> Tuple[float, str]:
    """(margin in rupees per lot, how it was derived).

    The second element is returned so a sizing record can say WHY a lot count
    came out as it did. A margin figure with no stated basis is the kind of
    number that later gets argued with instead of checked.
    """
    if structure not in STRUCTURES:
        raise MarginError(
            f"no margin treatment defined for structure '{structure}'. "
            f"Known: {', '.join(STRUCTURES)}. Add one deliberately — defaulting "
            f"an unknown structure to zero margin is how a backtest ends up "
            f"sizing a position no broker would allow.")
    if lot <= 0:
        raise MarginError("lot size must be positive; an unknown lot silently "
                          "rescales every margin figure")
    if spot <= 0:
        raise MarginError("spot must be positive to compute notional margin")

    notional = float(spot) * int(lot)
    naked = model.naked_frac * notional

    if structure == "vertical":
        # Defined risk: the loss is capped at the width less the credit, and
        # brokers margin it at about that. Arithmetic, not an estimate.
        m = max((float(width) - float(credit)) * lot, 1.0)
        return min(m, naked), ("spread_max_loss" if m <= naked else "naked_cap")

    if structure == "iron_condor":
        # Only one side can be breached at expiry, so the requirement is the
        # worse side's max loss rather than the sum of both.
        worse = max(float(width), float(call_width))
        m = max((worse - float(credit)) * lot, 1.0)
        return min(m, naked), ("condor_worse_side" if m <= naked else "naked_cap")

    # calendar
    if model.calendar_short_is_naked:
        return naked, "calendar_short_treated_as_naked"
    m = max(abs(float(credit)) * lot, 1.0)      # net debit paid
    return m, "calendar_net_debit"


def lots_within_budget(margin: float, equity: float, max_frac: float) -> int:
    """How many lots the margin budget allows. Floors at zero, never negative."""
    if margin <= 0 or equity <= 0 or max_frac <= 0:
        return 0
    return int((max_frac * equity) // margin)
