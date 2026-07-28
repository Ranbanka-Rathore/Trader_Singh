"""Restate the two ledger rows whose P&L was fabricated by wrong-expiry marks.

Context (see commit "Fix mark-to-market pricing positions off the wrong expiry"):
mark_position_pnl looked legs up by (strike, opt_type) with no expiry check, so
an aged position was marked against whatever expiry the chain publisher had
rolled to. Two routed exits fired at 09:15:0x off stale previous-session LTPs
from the WRONG expiry and booked P&L that never existed:

  trade 44  Aug-04 legs priced off Aug-11 quotes  -> booked -Rs9,616.93
  trade 48  Aug-25 legs priced off Sep-01 quotes  -> booked +Rs4,576.87
            (on a spread whose maximum possible profit is Rs1,745)

This script restates realized_pnl on `trades` and the matching `signal_audit`
row (which feeds ML retraining) using NSE bhavcopy settlement prices for the
contracts actually held, and recomputes friction from the true exit fills — the
booked friction was inflated too, since STT is charged on the sell premium.

Entry fills are NOT touched: they were real Dhan quotes and verify against the
bhavcopy closes. Only the exit side was fabricated.

The original values, the fabricated exit fills, and the full derivation are
preserved in learning_context['pnl_correction'] so this is auditable and
reversible. Both exits are also flagged with exit_should_have_fired=false:
correctly marked, neither trade's exit rule was met.

Usage:
    python fix_fabricated_pnl.py            # dry run, prints the restatement
    python fix_fabricated_pnl.py --apply    # write to the database
"""
import datetime
import json
import os
import sys
import zipfile

import pandas as pd
import psycopg2
from dotenv import load_dotenv
from psycopg2.extras import RealDictCursor

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
load_dotenv()

from backend.app.core import friction_model  # noqa: E402

BHAVCOPY_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                            "data", "bhavcopy")

# One entry per fabricated exit. `reference_date` is the last NSE session at or
# before the exit — spot was flat across both boundaries (23,996 -> 23,988 for
# trade 48; 23,882 -> 23,963 for trade 44), so the settlement close is the
# fairest available restatement of what the spread was worth when it was closed.
CORRECTIONS = [
    {
        "trade_id": 44,
        "expiry": "2026-08-04",
        "reference_date": "20260708",
        "exit_legs": [  # side is the CLOSING action
            {"side": "BUY", "strike": 23800.0, "opt_type": "pe"},
            {"side": "SELL", "strike": 23600.0, "opt_type": "pe"},
        ],
        # backstop mark stop is -1.5 x credit
        "exit_rule": lambda credit, pnl_ps: pnl_ps <= -1.5 * credit,
        "exit_rule_desc": "backstop stop at -1.5 x credit",
    },
    {
        "trade_id": 48,
        "expiry": "2026-08-25",
        "reference_date": "20260727",
        "exit_legs": [
            {"side": "BUY", "strike": 24950.0, "opt_type": "ce"},
            {"side": "SELL", "strike": 25150.0, "opt_type": "ce"},
        ],
        # take profit at +0.5 x credit
        "exit_rule": lambda credit, pnl_ps: pnl_ps >= 0.5 * credit,
        "exit_rule_desc": "take profit at +0.50 x credit",
    },
]


def bhavcopy_price(ymd, expiry, strike, opt_type):
    """Settlement-grade close for one contract from the cached NSE bhavcopy."""
    path = os.path.join(BHAVCOPY_DIR, f"BhavCopy_NSE_FO_{ymd}.csv.zip")
    with zipfile.ZipFile(path) as z:
        with z.open(z.namelist()[0]) as f:
            df = pd.read_csv(f)
    row = df[(df.TckrSymb == "NIFTY") & (df.FinInstrmTp == "IDO") &
             (df.XpryDt == expiry) & (df.StrkPric == strike) &
             (df.OptnTp == opt_type.upper())]
    if row.empty:
        raise SystemExit(f"no bhavcopy row for {expiry} {strike:.0f}{opt_type.upper()} on {ymd}")
    r = row.iloc[0]
    # ClsPric is 0 (or a stale print) on untraded strikes; SttlmPric is authoritative
    return float(r.ClsPric) if float(r.ClsPric) > 0 else float(r.SttlmPric)


def connect():
    return psycopg2.connect(
        dbname=os.getenv("DB_NAME"), user=os.getenv("DB_USER"),
        password=os.getenv("DB_PASSWORD"), host=os.getenv("DB_HOST"),
        port=os.getenv("DB_PORT"), connect_timeout=10)


def build(cur, spec):
    """Compute the restatement for one trade. Returns a plan dict."""
    cur.execute("""SELECT id, strategy_type, net_credit_per_share, lots_sized,
                          exit_reason, realized_pnl, learning_context
                   FROM trades WHERE id = %s""", (spec["trade_id"],))
    t = cur.fetchone()
    if t is None:
        raise SystemExit(f"trade {spec['trade_id']} not found")

    lc = dict(t["learning_context"] or {})
    if "pnl_correction" in lc:
        return {"trade": t, "already_corrected": True}

    entry = lc.get("entry_pricing") or {}
    entry_legs = entry.get("legs") or []
    if not entry_legs:
        raise SystemExit(f"trade {t['id']} has no entry legs to reprice")

    qty = int(entry_legs[0].get("quantity") or 0)
    credit = float(t["net_credit_per_share"])

    # True exit marks for the contracts actually held
    exit_legs, cost_to_close = [], 0.0
    for leg in spec["exit_legs"]:
        px = bhavcopy_price(spec["reference_date"], spec["expiry"],
                            leg["strike"], leg["opt_type"])
        exit_legs.append({**leg, "entry_fill": px, "quantity": qty})
        # BUY closes a short (we pay), SELL closes a long (we receive)
        cost_to_close += px if leg["side"] == "BUY" else -px

    pnl_ps = credit - cost_to_close
    gross = round(pnl_ps * qty, 2)
    friction = friction_model.round_trip_friction(entry_legs, exit_legs,
                                                  default_quantity=qty)
    corrected = round(gross - friction["total"], 2)

    fabricated_fills = [{"side": l.get("side"), "strike": l.get("strike"),
                         "opt_type": l.get("opt_type")}
                        for l in entry_legs]

    lc["pnl_correction"] = {
        "corrected_at": datetime.datetime.now().isoformat(timespec="seconds"),
        "reason": ("exit was marked against the wrong expiry — the chain publisher "
                   "had rolled off the expiry this position holds, and the empty "
                   "09:15 book made mid fall back to a stale previous-session LTP"),
        "original_realized_pnl": float(t["realized_pnl"]),
        "original_friction_total": float((lc.get("friction_costs") or {}).get("total") or 0),
        "original_exit_reason": t["exit_reason"],
        "corrected_realized_pnl": corrected,
        "corrected_gross_pnl": gross,
        "corrected_pnl_per_share": round(pnl_ps, 2),
        "corrected_cost_to_close_per_share": round(cost_to_close, 2),
        "corrected_friction": friction,
        "held_expiry": spec["expiry"],
        "reference": (f"NSE F&O bhavcopy {spec['reference_date']}, expiry "
                      f"{spec['expiry']}, settlement close"),
        "reference_marks": {f"{l['strike']:.0f}{l['opt_type'].upper()}": l["entry_fill"]
                            for l in exit_legs},
        "exit_should_have_fired": bool(spec["exit_rule"](credit, pnl_ps)),
        "exit_rule": spec["exit_rule_desc"],
        "note": ("entry fills are unchanged — they were real Dhan quotes and agree "
                 "with the bhavcopy closes; only the exit side was fabricated"),
        "fabricated_legs": fabricated_fills,
    }
    lc["friction_costs"] = friction

    tag = " [P&L RESTATED: wrong-expiry marks]"
    new_reason = t["exit_reason"] if tag in (t["exit_reason"] or "") else (t["exit_reason"] or "") + tag

    return {
        "trade": t, "already_corrected": False, "qty": qty, "credit": credit,
        "cost_to_close": cost_to_close, "pnl_ps": pnl_ps, "gross": gross,
        "friction": friction, "corrected": corrected, "learning_context": lc,
        "exit_reason": new_reason, "exit_legs": exit_legs,
        "signal_audit_id": lc.get("signal_audit_id"),
        "should_have_fired": lc["pnl_correction"]["exit_should_have_fired"],
        "exit_rule_desc": spec["exit_rule_desc"],
    }


def main():
    apply = "--apply" in sys.argv
    conn = connect()
    cur = conn.cursor(cursor_factory=RealDictCursor)

    plans = [build(cur, s) for s in CORRECTIONS]
    delta_total = 0.0

    for p in plans:
        t = p["trade"]
        if p["already_corrected"]:
            print(f"=== trade {t['id']}: already restated, skipping ===\n")
            continue
        old = float(t["realized_pnl"])
        delta = p["corrected"] - old
        delta_total += delta
        print(f"=== trade {t['id']} {t['strategy_type']} ({p['qty']} qty) ===")
        print(f"  held expiry        : {p['exit_legs'][0].get('opt_type')} legs, "
              f"reference {CORRECTIONS[plans.index(p)]['reference_date']}")
        for k, v in p["learning_context"]["pnl_correction"]["reference_marks"].items():
            print(f"    {k:<12} {v}")
        print(f"  credit/share       : {p['credit']:.2f}")
        print(f"  cost to close/share: {p['cost_to_close']:.2f}")
        print(f"  P&L/share          : {p['pnl_ps']:+.2f}")
        print(f"  gross P&L          : Rs {p['gross']:+,.2f}")
        print(f"  friction (recalc)  : Rs {p['friction']['total']:,.2f}  "
              f"(was Rs {p['learning_context']['pnl_correction']['original_friction_total']:,.2f})")
        print(f"  RESTATED realized  : Rs {p['corrected']:+,.2f}")
        print(f"  was booked         : Rs {old:+,.2f}   (delta {delta:+,.2f})")
        print(f"  exit rule          : {p['exit_rule_desc']}")
        print(f"  should have fired? : {p['should_have_fired']}")
        print(f"  signal_audit row   : {p['signal_audit_id']}")
        print()

    print(f"NET LEDGER CHANGE: Rs {delta_total:+,.2f}")

    if not apply:
        print("\nDRY RUN — nothing written. Re-run with --apply to commit.")
        conn.close()
        return

    for p in plans:
        if p["already_corrected"]:
            continue
        cur.execute("""UPDATE trades
                       SET realized_pnl = %s, exit_reason = %s, learning_context = %s
                       WHERE id = %s""",
                    (p["corrected"], p["exit_reason"],
                     json.dumps(p["learning_context"]), p["trade"]["id"]))
        if p["signal_audit_id"]:
            cur.execute("UPDATE signal_audit SET realized_pnl = %s WHERE id = %s",
                        (p["corrected"], p["signal_audit_id"]))
    conn.commit()
    print("\nAPPLIED — trades + signal_audit restated.")
    conn.close()


if __name__ == "__main__":
    main()
