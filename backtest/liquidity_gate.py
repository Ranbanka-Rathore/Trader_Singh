"""EOD analogue of the live tradeable-book guard, for backtest fills.

WHY THIS EXISTS
---------------
Live, `options_pricing_service.spread_is_tradeable(bid, ask)` refuses to price a
leg unless the book is two-sided, uncrossed and tight. That guard was added after
marking off stale LTPs fabricated both a take-profit and a stop-loss in the paper
ledger, and on 2026-07-30 it forced the purge of 15 of 17 ledger rows as
"synthetic (non-live-priced)" — rows carrying +Rs 7,531 of profit that was never
real.

The backtester had the same hole. NSE bhavcopy carries NO bid/ask, so
`spread_is_tradeable` cannot be reproduced literally. What it carries instead is
whether a contract traded at all, and `bhavcopy.load_chain` was masking that by
substituting SttlmPric whenever ClsPric == 0. A settlement price is computed by
the exchange, not dealt by anyone — filling a backtest at one is exactly the
synthetic pricing that was purged from live.

WHAT THIS GATE ASSERTS
----------------------
Only that a fill is plausible, on evidence bhavcopy actually contains:

  traded    ClsPric > 0 — the contract printed a trade near the close. This is
            not a proxy; it is a direct statement that a market existed. Primary.
  volume    contracts dealt that day.
  txns      number of executed trades — the closest available stand-in for
            "someone was continuously quoting this".
  oi        open interest — a contract with no OI has no natural counterparty.

Calibrated against live NIFTY books on 2026-08-07, scoring each leg with the real
`spread_is_tradeable`: legs with OI>0 AND volume>0 were 89% tradeable, while legs
with neither were 19%. The separation is real but imperfect, and that live sample
was small (80 legs). Treat a gated backtest as an UPPER bound on realism, not a
guarantee — it can still fill on a strike that printed one lot at 09:16 and was
untouchable after. It cannot, however, fill on a strike that never traded, which
is what the ungated version was doing.

USAGE
-----
    gate = LiquidityGate(LiquidityGate.STRICT)
    ok, why = gate.leg_ok(chain["options"].get(key))
    ok, why = gate.spread_ok([row_short, row_long])   # every leg must pass
"""
import math
from dataclasses import dataclass
from typing import Any, Dict, Iterable, Optional, Tuple


def _num(v: Any) -> Optional[float]:
    """Float, or None when the value is absent or not a number.

    NaN has to be caught explicitly. It arrives from pandas wherever a column
    does not exist in that era's schema, and every comparison against it is
    False — so `nan < floor` reads as "meets the floor" and a missing column
    becomes a silently disabled check.
    """
    if v is None:
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return None if math.isnan(f) else f


@dataclass(frozen=True)
class GateConfig:
    """Thresholds for calling an EOD option row fillable.

    `require_traded` is the one that matters; the numeric floors mostly separate
    "printed once" from "was a market". Defaults are deliberately mild so the
    headline result is driven by the settlement-price hole, not by tuning.
    """
    require_traded: bool = True
    min_volume: float = 0.0
    min_txns: float = 0.0
    min_oi: float = 0.0
    name: str = "custom"


class LiquidityGate:
    # No gate at all — reproduces the ORIGINAL backtester, fills on settlement
    # prices for contracts that never traded. Kept to quantify what that was worth.
    OFF = GateConfig(require_traded=False, name="off")
    # The honest minimum: the contract must have actually traded.
    TRADED = GateConfig(require_traded=True, name="traded")
    # Traded, with open interest and more than a token print.
    STRICT = GateConfig(require_traded=True, min_volume=1.0, min_txns=5.0,
                        min_oi=1.0, name="strict")
    # What a real desk would want before working a spread in size.
    DESK = GateConfig(require_traded=True, min_volume=50.0, min_txns=25.0,
                      min_oi=500.0, name="desk")
    # The strongest gate that can be EVALUATED before 2024. The legacy NSE
    # schema has no trade-count column, so `txns` is NaN for every pre-UDiFF row
    # and any floor on it is unenforceable rather than merely unmet. Use this on
    # a legacy window; `strict` there will refuse everything as txns_unknown.
    STRICT_LEGACY = GateConfig(require_traded=True, min_volume=1.0, min_txns=0.0,
                               min_oi=1.0, name="strict_legacy")

    def __init__(self, cfg: GateConfig = None):
        self.cfg = cfg or self.TRADED
        self.rejections: Dict[str, int] = {}
        self.checked = 0
        self.passed = 0

    # ── single leg ─────────────────────────────────────────────────────────
    def leg_ok(self, row: Optional[Dict[str, Any]]) -> Tuple[bool, Optional[str]]:
        """(ok, reason). `reason` is None on success, a short slug on refusal."""
        self.checked += 1
        cfg = self.cfg
        if not row:
            return self._no("absent")

        price = _num(row.get("close"))
        if price is None or price <= 0:
            return self._no("no_price")

        # A row loaded from a source that predates the `traded` flag would
        # silently pass; treat a missing flag as "price is real" only when the
        # gate is off, so an un-upgraded provider cannot quietly disable this.
        traded = row.get("traded")
        if cfg.require_traded:
            if traded is None:
                return self._no("traded_unknown")
            if not traded:
                return self._no("settle_only")

        for field, floor, thin in (("volume", cfg.min_volume, "thin_volume"),
                                   ("txns", cfg.min_txns, "thin_txns"),
                                   ("oi", cfg.min_oi, "no_oi")):
            if floor <= 0:
                continue                      # nothing asked of this field
            v = _num(row.get(field))
            if v is None:
                # The field is absent or NaN. A threshold that CANNOT BE
                # EVALUATED must not pass: `float('nan') < 5.0` is False in
                # Python, so the original comparison silently waved through
                # every pre-2024 row, where the legacy schema leaves `txns`
                # NaN. Refused under its own reason so it reads as "this era
                # cannot answer the question" rather than as illiquidity.
                return self._no(f"{field}_unknown")
            if v < floor:
                return self._no(thin)

        self.passed += 1
        return True, None

    # ── whole structure ────────────────────────────────────────────────────
    def spread_ok(self, rows: Iterable[Optional[Dict[str, Any]]]
                  ) -> Tuple[bool, Optional[str]]:
        """A spread is fillable only if EVERY leg is.

        Mirrors `price_spread_entry`, which abandons the whole structure on the
        first untradeable leg rather than filling the rest — a spread with one
        unfillable leg is naked risk, not a spread.
        """
        for i, row in enumerate(rows):
            ok, why = self.leg_ok(row)
            if not ok:
                return False, f"leg{i}_{why}"
        return True, None

    # ── reporting ──────────────────────────────────────────────────────────
    def _no(self, reason: str) -> Tuple[bool, str]:
        self.rejections[reason] = self.rejections.get(reason, 0) + 1
        return False, reason

    @property
    def pass_rate(self) -> float:
        return (100.0 * self.passed / self.checked) if self.checked else 0.0

    def summary(self) -> str:
        if not self.checked:
            return f"gate[{self.cfg.name}]: nothing checked"
        top = sorted(self.rejections.items(), key=lambda kv: -kv[1])
        detail = ", ".join(f"{k}={v}" for k, v in top) or "none"
        return (f"gate[{self.cfg.name}]: {self.passed}/{self.checked} legs fillable "
                f"({self.pass_rate:.1f}%) | refusals: {detail}")

    def reset(self):
        self.rejections.clear()
        self.checked = self.passed = 0


def gate_by_name(name: str) -> GateConfig:
    """Look up a preset by name for CLI use."""
    presets = {c.name: c for c in (LiquidityGate.OFF, LiquidityGate.TRADED,
                                   LiquidityGate.STRICT, LiquidityGate.DESK,
                                   LiquidityGate.STRICT_LEGACY)}
    if name not in presets:
        raise ValueError(f"unknown gate '{name}'; choose from {sorted(presets)}")
    return presets[name]
