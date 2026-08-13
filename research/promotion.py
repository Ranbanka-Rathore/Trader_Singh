"""The promotion gate — what a strategy must have earned before it trades money.

Section 5 of the charter lists four walk-forward acceptance criteria. They were
implemented, they were correct, and the ladder failed all four while running.
Nothing was bound to them: passing was something a human was supposed to check.
This module is the binding.

THE LADDER OF STAGES
--------------------
    research   nothing has been proven. PAPER only.
    paper      cleared the Section 5 walk-forward gate. Still PAPER only, now
               accumulating the pre-committed sample Section 5 requires.
    live       the paper sample cleared too. Real orders are permitted.

A strategy is at `research` unless a promotion record says otherwise, so the
default for anything new — or anything the store has never heard of — is that it
cannot touch money.

WHAT THIS DOES AND DOES NOT BLOCK
---------------------------------
It blocks ENTRIES in LIVE mode. It never blocks an EXIT or an UNWIND, in any
mode, for any reason. Refusing to close a position does not protect the account;
it traps risk in it, and a gate that can strand an open short is more dangerous
than the thing it was guarding against. The same reasoning keeps a demoted
strategy's services running rather than refusing to boot: they still have
positions to manage.

PAPER entries are never blocked either. Paper trading IS the search (Section 1:
"capital at risk during the search: zero — paper only"), and a gate that stopped
paper trades would stop the evidence that promotes a strategy from ever existing.

FAIL CLOSED
-----------
If the store is missing, corrupt, or unreadable, LIVE entries are refused. This
sits in the order path and is deliberately stdlib-only for that reason: reading
it must not be able to fail for an interesting reason.
"""
import datetime
import json
import os
from typing import Any, Dict, List, Optional, Tuple

RESEARCH_DIR = os.path.dirname(os.path.abspath(__file__))
PROMOTIONS_PATH = os.path.join(RESEARCH_DIR, "promotions.json")

RESEARCH, PAPER, LIVE = "research", "paper", "live"
STAGES = (RESEARCH, PAPER, LIVE)

# A promotion is evidence, and evidence has a date. Without an expiry the first
# strategy that ever passed would be licensed forever on one backtest, and the
# market it was measured in has already changed character twice in this archive.
DEFAULT_REVIEW_DAYS = 180


class PromotionError(Exception):
    """A promotion was requested that the charter does not permit."""


# ── store ────────────────────────────────────────────────────────────────────
def load() -> Dict[str, Any]:
    if not os.path.exists(PROMOTIONS_PATH):
        return {"version": 1, "promotions": []}
    with open(PROMOTIONS_PATH, "r", encoding="utf-8") as f:
        blob = json.load(f)
    blob.setdefault("promotions", [])
    return blob


def save(blob: Dict[str, Any]) -> None:
    tmp = PROMOTIONS_PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(blob, f, indent=1, default=str)
    os.replace(tmp, PROMOTIONS_PATH)


def record(structure: str) -> Optional[Dict[str, Any]]:
    for p in load()["promotions"]:
        if p["structure"] == structure:
            return p
    return None


def all_records() -> List[Dict[str, Any]]:
    return load()["promotions"]


def active_structure() -> str:
    """Which structure the live system is currently configured to trade.

    Mirrors the switch the services actually read, so the gate is asked about
    the thing that is running rather than the thing someone meant to run.
    """
    try:
        from trading_mode import ladder_enabled
        return "ladder" if ladder_enabled() else "sniper"
    except Exception:
        return "sniper"


def stage(structure: str) -> str:
    """Current stage of a structure. Unknown or revoked means `research`."""
    try:
        p = record(structure)
    except (OSError, ValueError):
        return RESEARCH
    if not p or p.get("revoked"):
        return RESEARCH
    st = p.get("stage", RESEARCH)
    return st if st in STAGES else RESEARCH


# ── the gate ─────────────────────────────────────────────────────────────────
def may_enter(strategy_type: str, mode: Optional[str] = None,
              structure: Optional[str] = None,
              today: Optional[datetime.date] = None) -> Tuple[bool, str]:
    """(allowed, reason) for opening a NEW position.

    Never call this for an exit. `reason` is a short slug intended to land in an
    order-audit row and a log line, so it has to say which of the several ways
    to be unpromoted applied.
    """
    if mode is None:
        try:
            from trading_mode import mode as trading_mode
            mode = trading_mode()
        except Exception:
            mode = "LIVE"        # fail closed: unknown mode is treated as live
    if str(mode).upper() != "LIVE":
        return True, "paper_mode"

    structure = structure or active_structure()
    ok, why = eligible(structure, today)
    if not ok:
        return False, why

    covers = [str(c).upper() for c in ((record(structure) or {}).get("covers") or [])]
    st = str(strategy_type or "").upper()
    if covers and st not in covers:
        # A promotion is evidence about the structure that was tested, not a
        # licence for every structure the engine can emit. An iron condor riding
        # a credit-spread promotion is untested size on untested risk.
        return False, f"uncovered_{st or 'unknown'}"

    return True, "promoted"


def eligible(structure: str,
             today: Optional[datetime.date] = None) -> Tuple[bool, str]:
    """(allowed, reason) for the structure itself, ignoring strategy coverage.

    Split out from `may_enter` so the boot banner can ask "is this structure
    licensed at all" without naming a strategy type — asking with an empty one
    would trip the coverage check and report a promoted structure as refused.
    """
    today = today or datetime.date.today()
    try:
        p = record(structure)
    except (OSError, ValueError) as exc:
        return False, f"promotion_store_unreadable_{type(exc).__name__}"

    if not p:
        return False, f"unpromoted_{structure}"
    if p.get("revoked"):
        return False, f"revoked_{structure}"
    if p.get("stage") != LIVE:
        return False, f"stage_{p.get('stage', 'unknown')}_{structure}"

    review_by = p.get("review_by")
    if review_by:
        try:
            if datetime.date.fromisoformat(str(review_by)[:10]) < today:
                return False, f"promotion_expired_{review_by}"
        except ValueError:
            return False, "promotion_review_date_unparseable"

    return True, "promoted"


def gate_banner(mode: Optional[str] = None) -> str:
    """One line for the boot banner, so every service states its own eligibility."""
    structure = active_structure()
    st = stage(structure)
    if mode is None:
        try:
            from trading_mode import mode as trading_mode
            mode = trading_mode()
        except Exception:
            mode = "?"
    if str(mode).upper() != "LIVE":
        return (f"  promotion: '{structure}' at stage '{st}' — PAPER mode, "
                f"entries allowed and counted toward the Section 5 sample")
    ok, why = eligible(structure)
    if st == LIVE and ok:
        p = record(structure) or {}
        return (f"  promotion: '{structure}' is LIVE-eligible "
                f"(hypothesis {p.get('hypothesis_id')}, review by {p.get('review_by')})")
    return (f"  promotion: '{structure}' is at stage '{st}' — LIVE ENTRIES WILL BE "
            f"REFUSED ({why}). Exits and unwinds still run.")


# ── transitions ──────────────────────────────────────────────────────────────
def _touch(p: Dict[str, Any], to: str, reason: str) -> None:
    p.setdefault("history", []).append({
        "at": datetime.datetime.now().isoformat(timespec="seconds"),
        "from": p.get("stage", RESEARCH), "to": to, "reason": reason})
    p["stage"] = to


def promote_to_paper(structure: str, hypothesis_id: str, covers: List[str],
                     evidence: Dict[str, Any],
                     review_days: int = DEFAULT_REVIEW_DAYS) -> Dict[str, Any]:
    """Record that a structure cleared the Section 5 walk-forward gate.

    Only `research.loop` calls this, and only for a hypothesis whose recorded
    verdict is `survived`. There is deliberately no way to assert a promotion
    from evidence that was not produced by the loop.
    """
    if not covers:
        raise PromotionError(
            "a promotion must name the strategy types it covers; an open-ended "
            "one licenses structures that were never tested")
    blob = load()
    now = datetime.datetime.now()
    existing = next((p for p in blob["promotions"] if p["structure"] == structure), None)
    entry = existing or {"structure": structure, "history": []}
    entry.update({
        "hypothesis_id": hypothesis_id,
        "covers": [str(c).upper() for c in covers],
        "evidence": evidence,
        "promoted_at": now.isoformat(timespec="seconds"),
        "review_by": (now.date() + datetime.timedelta(days=review_days)).isoformat(),
        "revoked": None,
    })
    entry.setdefault("paper", None)
    _touch(entry, PAPER, f"walk-forward survived ({hypothesis_id})")
    if existing is None:
        blob["promotions"].append(entry)
    save(blob)
    return entry


def promote_to_live(structure: str, paper: Dict[str, Any]) -> Dict[str, Any]:
    """Record that the paper sample cleared too. This is the last gate."""
    blob = load()
    entry = next((p for p in blob["promotions"] if p["structure"] == structure), None)
    if entry is None:
        raise PromotionError(
            f"'{structure}' has no promotion record — it has not passed the "
            f"walk-forward gate, so there is nothing to promote from.")
    if entry.get("revoked"):
        raise PromotionError(f"'{structure}' is revoked; re-earn the paper stage first.")
    if entry.get("stage") != PAPER:
        raise PromotionError(
            f"'{structure}' is at stage '{entry.get('stage')}'. Live promotion "
            f"runs paper -> live only; there is no path that skips paper.")
    entry["paper"] = paper
    _touch(entry, LIVE, f"paper sample cleared ({paper.get('n_trades')} trades)")
    save(blob)
    return entry


def revoke(structure: str, reason: str) -> Dict[str, Any]:
    """Stop a structure trading live.

    Amendment A3's shutdown rule: a breach of the modelled p99 drawdown stops the
    system, not because the loss is unbearable but because the model that
    produced the p99 has been falsified. Revoking is cheap and reversible by
    re-earning the stage; leaving a falsified model live is neither.
    """
    blob = load()
    entry = next((p for p in blob["promotions"] if p["structure"] == structure), None)
    if entry is None:
        raise PromotionError(f"'{structure}' has no promotion record to revoke.")
    entry["revoked"] = {"at": datetime.datetime.now().isoformat(timespec="seconds"),
                        "reason": reason}
    _touch(entry, RESEARCH, f"revoked: {reason}")
    save(blob)
    return entry
