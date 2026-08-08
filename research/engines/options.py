"""Arena 1 — index-option structures, on the existing spread backtester.

This is an adapter, not a new engine. `RealBacktester` already runs credit
spreads, iron condors and calendars under the liquidity gate; what it lacked was
a uniform surface the loop could drive alongside the futures arenas. Everything
here delegates, so arena 1 keeps running the exact code that produced the ladder
verdict rather than a reimplementation that might disagree with it.

The structures beyond vanilla credit spreads are already in `Config` and already
carry their own history, recorded in the comments there: the iron condor was
REJECTED by walk-forward on 2026-07-04 (18 OOS trades, net -4,936, double the
friction of a directional spread), and the calendar has never been validated at
all — its cheap-vol regime never fired in a test month. Both are reachable here
as `enable_iron_condor` / `enable_calendar`, and both start from that record
rather than from zero.
"""
import datetime
import os
import sys
from typing import Any, Dict, List, Optional

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from dataclasses import replace

from backtest import walkforward as wf
from backtest.real_backtester import Config, RealBacktester
from research import engines


class OptionsEngine:
    name = "real_backtester"
    arena = "index_structures"

    # This engine reports no scalar extras — its per-strategy and exit-reason
    # breakdowns are nested tables, not thresholds. Requirements on it are
    # limited to the standard metrics.
    EXTRA_FIELDS = frozenset()

    # The grid fixed a priori in the Phase-4 overhaul plan. Not widened here:
    # widening it would raise the deflated-Sharpe hurdle for every historical
    # result computed against it, and make this arena's numbers incomparable
    # with the ones already in the kill log.
    GRID = wf.GRID

    def coerce(self, name: str, raw: Any) -> Any:
        from research.screen import coerce
        return coerce(name, raw)

    def build(self, hypothesis: Dict[str, Any], gate: Optional[str] = None) -> Config:
        # Imported here rather than at module scope: screen.py dispatches through
        # the engine registry, so a top-level import would close a cycle.
        from research.screen import config_from
        return config_from(hypothesis, gate=gate)

    def with_params(self, cfg: Config, **params) -> Config:
        return replace(cfg, **params)

    def stress(self, cfg: Config, mult: float) -> Config:
        """Config with execution costs multiplied, for the Section 5 cost stress."""
        return replace(cfg, slippage_per_leg=cfg.slippage_per_leg * mult)

    def grid(self) -> List[Dict[str, Any]]:
        return list(self.GRID)

    def warmup_days(self, cfg: Config) -> int:
        """Calendar days of history the regime filters need before a fold."""
        return wf.WARMUP_DAYS

    def run(self, cfg: Config, dates: List[datetime.date],
            provider=None) -> Dict[str, Any]:
        # walkforward owns the process-wide chain cache; reusing it means the
        # screen and every fold parse each session file once between them.
        bt = RealBacktester(cfg, provider or wf.cached_load_chain)
        return bt.run(dates)


engines.register(OptionsEngine())
