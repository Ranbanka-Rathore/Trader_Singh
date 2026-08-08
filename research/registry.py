"""The kill log — every hypothesis this project has ever registered.

Section 7 of the charter: hypotheses are registered BEFORE they are run, with a
falsifiable prediction and a kill criterion, and a hypothesis that fails is
closed rather than tuned. This module is what makes that mechanical:

  * a hypothesis cannot be run unless it was registered first;
  * a closed hypothesis cannot be re-run at all;
  * re-registering a variant of a dead idea must declare `--supersedes`, and the
    dead ancestor's configuration budget is added to the new one's, so the
    multiple-comparisons bar in Section 4 rises every time an idea is retried.

That last rule is the whole point. Tuning a dead strategy is not free, and the
loop should make the price visible instead of leaving it to good intentions.

The fingerprint guards against drift, not against a determined operator: it is
computed from the stored fields, so anyone editing the log by hand could edit it
too. It exists to catch the honest mistake — changing a parameter and forgetting
that the registration no longer describes what is being run.
"""
import datetime
import hashlib
import json
import os
from typing import Any, Dict, List, Optional

from research import charter

RESEARCH_DIR = os.path.dirname(os.path.abspath(__file__))
KILL_LOG_PATH = os.path.join(RESEARCH_DIR, "kill_log.json")
SURVIVORS_DIR = os.path.join(RESEARCH_DIR, "survivors")
RESULTS_DIR = os.path.join(RESEARCH_DIR, "results")

# Terminal states. Section 7: "A hypothesis that fails is closed, not tuned."
CLOSED = ("killed", "survived")

LOG_VERSION = 1


class RegistryError(Exception):
    """A charter rule was violated. Never caught to keep going."""


# ── persistence ──────────────────────────────────────────────────────────────
def load() -> Dict[str, Any]:
    if not os.path.exists(KILL_LOG_PATH):
        return {"version": LOG_VERSION, "hypotheses": []}
    with open(KILL_LOG_PATH, "r", encoding="utf-8") as f:
        log = json.load(f)
    log.setdefault("version", LOG_VERSION)
    log.setdefault("hypotheses", [])
    return log


def save(log: Dict[str, Any]) -> None:
    os.makedirs(RESEARCH_DIR, exist_ok=True)
    tmp = KILL_LOG_PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(log, f, indent=1, default=str)
    os.replace(tmp, KILL_LOG_PATH)


def all_hypotheses() -> List[Dict[str, Any]]:
    return load()["hypotheses"]


def get(hid: str) -> Optional[Dict[str, Any]]:
    for h in all_hypotheses():
        if h["id"] == hid:
            return h
    return None


def require(hid: str) -> Dict[str, Any]:
    h = get(hid)
    if h is None:
        raise RegistryError(
            f"'{hid}' was never registered. The charter does not allow running a "
            f"hypothesis that was not written down first — register it, then run it.")
    return h


# ── fingerprinting ───────────────────────────────────────────────────────────
def fingerprint(h: Dict[str, Any]) -> str:
    """Hash of everything that decides what a run actually does."""
    payload = {
        "engine": h.get("engine"),
        "underlying": h.get("underlying"),
        "window": h.get("window"),
        "gate": h.get("gate"),
        "equity": h.get("equity"),
        "config": h.get("config") or {},
        "sweep": h.get("sweep"),
        "n_configs": h.get("n_configs"),
        # Requirements are part of what the hypothesis claims, so loosening a
        # threshold after seeing the screen changes the fingerprint and the run
        # is refused. That is the entire point of declaring them up front.
        "requires": list(h.get("requires") or []),
    }
    blob = json.dumps(payload, sort_keys=True, default=str)
    return "sha256:" + hashlib.sha256(blob.encode("utf-8")).hexdigest()[:32]


def verify_unchanged(h: Dict[str, Any]) -> None:
    actual = fingerprint(h)
    if h.get("fingerprint") and h["fingerprint"] != actual:
        raise RegistryError(
            f"'{h['id']}' no longer matches its registration.\n"
            f"  registered: {h['fingerprint']}\n"
            f"  now:        {actual}\n"
            f"Something changed after the prediction was written down, which "
            f"makes any result from it in-sample. Register a new hypothesis "
            f"with --supersedes {h['id']} instead.")


# ── the multiple-comparisons budget ──────────────────────────────────────────
def ancestry(h: Dict[str, Any]) -> List[str]:
    """Chain of dead hypotheses this one is a retry of, oldest last."""
    chain, seen = [], {h["id"]}
    cur = h.get("supersedes")
    while cur and cur not in seen:
        seen.add(cur)
        chain.append(cur)
        parent = get(cur)
        cur = parent.get("supersedes") if parent else None
    return chain


def effective_configs(h: Dict[str, Any]) -> int:
    """Configurations tested across this hypothesis and everything it retries.

    Section 7: "Re-testing a variant requires a new registration and counts
    against the multiple-comparisons budget in Section 4." So the noise threshold
    a retry must clear is set by the whole search, not by the latest attempt —
    the fourth variant of a dead idea is the 4th draw from the same urn, and
    pretending otherwise is how a sweep gets laundered into a discovery.
    """
    total = int(h.get("n_configs", 1))
    for anc in ancestry(h):
        parent = get(anc)
        if parent:
            total += int(parent.get("n_configs", 1))
    return total


# ── registration ─────────────────────────────────────────────────────────────
def register(hid: str, arena: str, claim: str, kill_criterion: str,
             window: List[str], gate: str = "strict", n_configs: int = 1,
             config: Optional[Dict[str, Any]] = None,
             sweep: Optional[Dict[str, Any]] = None,
             underlying: str = "NIFTY", equity: float = 1_500_000.0,
             engine: str = "real_backtester", era: Optional[str] = None,
             supersedes: Optional[str] = None,
             requires: Optional[List[str]] = None,
             note: str = "") -> Dict[str, Any]:
    """Write a hypothesis to the kill log. Refuses anything the charter forbids."""
    log = load()
    if any(h["id"] == hid for h in log["hypotheses"]):
        raise RegistryError(f"'{hid}' is already registered; ids are permanent.")
    if arena not in charter.ARENAS:
        raise RegistryError(
            f"unknown arena '{arena}'; charter Section 8 lists "
            f"{sorted(charter.ARENAS)}. A new arena is an amendment, not a flag.")
    if not claim.strip() or not kill_criterion.strip():
        raise RegistryError(
            "a hypothesis needs both a falsifiable claim and the result that "
            "would close it. Without the kill criterion there is nothing "
            "stopping a bad result from becoming a reason to keep looking.")
    if gate == "off":
        raise RegistryError(
            "gate 'off' fills contracts that never traded — charter Section 6.1 "
            "makes any such result void. Register 'traded' or 'strict'.")

    h = {
        "id": hid,
        "arena": arena,
        "claim": claim.strip(),
        "kill_criterion": kill_criterion.strip(),
        "engine": engine,
        "underlying": underlying,
        "equity": equity,
        "era": era,
        "window": window,
        "gate": gate,
        "n_configs": int(n_configs),
        "config": config or {},
        "sweep": sweep,
        "requires": [str(r) for r in (requires or [])],
        "supersedes": supersedes,
        "note": note,
        "registered_at": datetime.datetime.now().isoformat(timespec="seconds"),
        "status": "registered",
        "events": [],
    }

    if supersedes:
        parent = get(supersedes)
        if parent is None:
            raise RegistryError(f"--supersedes '{supersedes}' is not registered.")
        if parent["status"] not in CLOSED:
            raise RegistryError(
                f"'{supersedes}' is still open ({parent['status']}); finish it "
                f"before registering a variant of it.")
    else:
        # An unacknowledged retry of a dead idea is the failure mode Section 7
        # names: the same configuration comes back under a new name and its
        # history — and its share of the multiple-comparisons budget — is lost.
        fp = fingerprint(h)
        twin = next((x for x in log["hypotheses"]
                     if x.get("fingerprint") == fp and x["status"] in CLOSED), None)
        if twin:
            raise RegistryError(
                f"this is configuration-identical to '{twin['id']}', which is "
                f"already {twin['status']}. Re-run it as a variant with "
                f"--supersedes {twin['id']} so it counts against the "
                f"multiple-comparisons budget, or change what it tests.")

    h["fingerprint"] = fingerprint(h)
    log["hypotheses"].append(h)
    save(log)
    return h


def add_event(hid: str, stage: str, verdict: str, detail: Dict[str, Any],
              status: Optional[str] = None) -> Dict[str, Any]:
    """Append a stage result and optionally move the hypothesis's status."""
    log = load()
    for h in log["hypotheses"]:
        if h["id"] != hid:
            continue
        h.setdefault("events", []).append({
            "at": datetime.datetime.now().isoformat(timespec="seconds"),
            "stage": stage,
            "verdict": verdict,
            "detail": detail,
        })
        if status:
            h["status"] = status
        save(log)
        return h
    raise RegistryError(f"'{hid}' is not registered.")


def open_for_running(hid: str) -> Dict[str, Any]:
    """Fetch a hypothesis, refusing anything the charter says is closed."""
    h = require(hid)
    if h["status"] in CLOSED:
        closed_at = h["events"][-1]["at"] if h.get("events") else "?"
        raise RegistryError(
            f"'{hid}' is {h['status']} (closed {closed_at}). Section 7: a "
            f"hypothesis that fails is closed, not tuned. If there is a genuinely "
            f"new question here, register it with --supersedes {hid}.")
    verify_unchanged(h)
    return h


# ── survivors, for the portfolio correlation check ───────────────────────────
def survivor_path(hid: str) -> str:
    return os.path.join(SURVIVORS_DIR, f"{hid}.json")


def record_survivor(hid: str, daily_returns: Dict[str, float],
                    metrics: Dict[str, Any]) -> str:
    """Store a survivor's OOS daily P&L so later candidates can be correlated."""
    os.makedirs(SURVIVORS_DIR, exist_ok=True)
    path = survivor_path(hid)
    with open(path, "w", encoding="utf-8") as f:
        json.dump({"id": hid, "metrics": metrics, "daily": daily_returns},
                  f, indent=1, default=str)
    return path


def survivors() -> Dict[str, Dict[str, float]]:
    """{hypothesis id: daily P&L map} for everything that has passed."""
    out: Dict[str, Dict[str, float]] = {}
    if not os.path.isdir(SURVIVORS_DIR):
        return out
    for fn in sorted(os.listdir(SURVIVORS_DIR)):
        if not fn.endswith(".json"):
            continue
        with open(os.path.join(SURVIVORS_DIR, fn), "r", encoding="utf-8") as f:
            blob = json.load(f)
        out[blob["id"]] = {k: float(v) for k, v in blob.get("daily", {}).items()}
    return out


# ── throughput, the metric that actually matters ─────────────────────────────
def throughput(today: Optional[datetime.date] = None) -> Dict[str, Any]:
    """Hypotheses closed per week since the first registration.

    Section 9 expects most of these to die. Closure rate is the measure of
    whether the loop is working; P&L is not, and will not be for months.
    """
    hs = all_hypotheses()
    today = today or datetime.date.today()
    if not hs:
        return {"registered": 0, "closed": 0, "weeks": 0.0, "per_week": 0.0,
                "days_to_stop": charter.days_to_stop(today)}
    first = min(datetime.datetime.fromisoformat(h["registered_at"]).date() for h in hs)
    weeks = max((today - first).days, 1) / 7.0
    closed = [h for h in hs if h["status"] in CLOSED]
    return {
        "registered": len(hs),
        "closed": len(closed),
        "killed": sum(1 for h in closed if h["status"] == "killed"),
        "survived": sum(1 for h in closed if h["status"] == "survived"),
        "open": len(hs) - len(closed),
        "first_registered": first.isoformat(),
        "weeks": round(weeks, 2),
        "per_week": round(len(closed) / weeks, 2),
        "days_to_stop": charter.days_to_stop(today),
    }
