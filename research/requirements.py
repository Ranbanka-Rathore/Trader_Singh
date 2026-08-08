"""Machine-checked kill criteria — thresholds declared at registration.

A kill criterion written only in prose is a criterion that quietly stops being
applied. `xsect-mom-modern` was registered with "capacity fill rate below 50%"
in its kill text, and checking whether that had been breached meant re-running
the engine by hand afterwards. It happened to agree. Nothing made it agree.

A requirement declared here is part of the registration, goes into the
fingerprint, and is evaluated by the screen alongside the charter's own Section 6
checks. It cannot be added, removed or loosened after a result has been seen
without the fingerprint refusing the run.

    --require capacity_fill_rate_pct>=50
    --require n_trades>=30
    --require max_drawdown<=100000

A requirement states what must HOLD. If it does not hold, the hypothesis is
killed. Naming a field the engine does not produce is refused at registration,
because a threshold on a misspelt field would silently never fire — which is
worse than not declaring one at all, since it looks like a check.

SCOPE: these are screened, not walk-forwarded. Stage 2 judges an out-of-sample
trade list and does not carry engine-specific measurements, so a requirement on
`capacity_fill_rate_pct` has nothing to bind to there. Requirements are for
killing cheaply at stage 1; Section 5's four criteria remain the promotion gate.
"""
import operator
import re
from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Optional, Set

# Scalar fields every screen produces, whatever the arena. Kept here rather than
# derived from a sample run so a requirement can be validated at registration,
# before anything has been executed.
METRIC_FIELDS = (
    "n_trades", "net_pnl", "expectancy", "profit_factor", "win_rate",
    "sharpe", "max_drawdown", "t", "fill_pass_rate_pct",
)

OPS = {
    ">=": operator.ge,
    "<=": operator.le,
    "==": operator.eq,
    "!=": operator.ne,
    ">": operator.gt,
    "<": operator.lt,
}
# longest first, so ">=" is not read as ">" followed by junk
_PATTERN = re.compile(r"^\s*([A-Za-z_][A-Za-z0-9_]*)\s*(>=|<=|==|!=|>|<)\s*(-?[0-9.]+)\s*$")


class RequirementError(Exception):
    """A requirement was malformed or named something that cannot exist."""


@dataclass(frozen=True)
class Requirement:
    field: str
    op: str
    value: float

    def __str__(self) -> str:
        return f"{self.field}{self.op}{self.value:g}"

    @property
    def check_name(self) -> str:
        slug = {">=": "ge", "<=": "le", "==": "eq", "!=": "ne",
                ">": "gt", "<": "lt"}[self.op]
        return f"require_{self.field}_{slug}_{self.value:g}"


def parse(text: str) -> Requirement:
    m = _PATTERN.match(str(text))
    if not m:
        raise RequirementError(
            f"cannot read requirement '{text}'. Expected FIELD OP NUMBER, "
            f"e.g. capacity_fill_rate_pct>=50. Operators: {', '.join(OPS)}")
    field, op, raw = m.groups()
    try:
        value = float(raw)
    except ValueError:
        raise RequirementError(f"'{raw}' in '{text}' is not a number")
    return Requirement(field=field, op=op, value=value)


def known_fields(engine=None) -> Set[str]:
    """Every field a requirement may name for this engine."""
    extras = set(getattr(engine, "EXTRA_FIELDS", ()) or ())
    return set(METRIC_FIELDS) | extras


def parse_all(texts: Iterable[str], engine=None) -> List[Requirement]:
    """Parse and validate against what the engine can actually produce."""
    known = known_fields(engine)
    out = []
    for t in texts or []:
        req = parse(t)
        if req.field not in known:
            raise RequirementError(
                f"'{req.field}' is not a field this engine reports, so a "
                f"threshold on it could never fire. Available: "
                f"{', '.join(sorted(known))}")
        out.append(req)
    return out


def evaluate(reqs: Iterable[Requirement], metrics: Dict[str, Any]
             ) -> List[Dict[str, Any]]:
    """Check dicts in the shape the screen already uses.

    Fails closed. A field that is absent, or present but None — `t` is None when
    a sample is too small to compute one — is a requirement that could not be
    verified, and an unverifiable requirement is not a satisfied one.
    """
    values = dict(metrics or {})
    values.update(dict(values.get("extras") or {}))
    out = []
    for req in reqs or []:
        if req.field not in values:
            out.append({"check": req.check_name, "passed": False,
                        "detail": f"{req.field} was not reported by this run, so "
                                  f"'{req}' could not be checked"})
            continue
        actual = values[req.field]
        if actual is None:
            out.append({"check": req.check_name, "passed": False,
                        "detail": f"{req.field} is undefined for this run, so "
                                  f"'{req}' could not be checked"})
            continue
        try:
            ok = OPS[req.op](float(actual), req.value)
        except (TypeError, ValueError):
            out.append({"check": req.check_name, "passed": False,
                        "detail": f"{req.field}={actual!r} is not numeric"})
            continue
        out.append({
            "check": req.check_name,
            "passed": bool(ok),
            "detail": f"{req.field} = {_fmt(float(actual))}, required {req.op}"
                      f"{_fmt(req.value)} (declared at registration)",
        })
    return out


def _fmt(v: float) -> str:
    """Readable numbers in a kill record — rupee amounts, not 1.39836e+06."""
    if abs(v) >= 1000:
        return f"{v:,.0f}"
    return f"{v:g}"
