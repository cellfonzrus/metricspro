"""EXPECTED vs EARNED for the multi-month (sale-triggered) installment — migration 258.

OWNER DIRECTIVE 2026-08-01 (verbatim): "as a modification to the commision payout for the second third
and upto 6 months, let the system calculate the the expected commission as a separate column but not
use that to pay out, if the company gets paid the employee commission auto fills from there, there
should be an option to move the expected commisison to the earned column is the system malfnctions or
the report is not updated on time, this will done as an edit function gated per permission."

Three things, and only the third is new behaviour:

  ① EXPECTED — "calculate the expected commission as a separate column but not use that to pay out".
     The engine ALREADY computes this number: it is the installment amount BEFORE the paid gate is
     applied. Today it is thrown away when the gate is unmet (`amount if gate_met else 0.0`). This
     module surfaces it as `expected_amount` on every ledger row and on the read surfaces. It is NEVER
     summed into `by_rep`, `totals.amount`, `rep_commissions` or any payout. Nothing about what pays
     changes to make this column exist.

  ② EARNED AUTO-FILL — "if the company gets paid the employee commission auto fills from there".
     That IS the existing paid gate: when the carrier/master-agent statement proves the dealer was
     paid, `gate_met` flips and the amount pays. This module does NOT fork or re-implement that gate.
     It makes the relationship legible: earned = expected, once the gate is met.

  ③ MANUAL PROMOTE — "an option to move the expected commisison to the earned column if the system
     malfunctions or the report is not updated on time … an edit function gated per permission".
     THE ONLY NEW MONEY PATH. An authorised person promotes ONE chain-month; the engine then pays it
     even though the gate is unmet, with full provenance on the ledger row.

──────────────────────────────────────────────────────────────────────────────────────────────────
FOUR RULES THE PROMOTE OBEYS, ALL PROVEN IN THE HARNESS
──────────────────────────────────────────────────────────────────────────────────────────────────
1. IT SURVIVES RECOMPUTE. Promotes live in their own org-scoped table (`commcalc.installment_promote`)
   keyed by (org, pay_period, trans_id, mdn, month_index) and are re-applied inside
   `compute_sale_installments`. `_persist`'s delete-then-insert rewrites the LEDGER, never the promote
   table — the same separation that keeps a manually-assigned chargeback alive across a recalc
   (`router._run_calculation` deletes `chargeback_items` with `.neq('source','chargeback_review')`).

2. IT IS NEVER PAID AT A STALE NUMBER. The approver approves a specific dollar, so the row stores
   `expected_at_promote`. If a later recompute produces a DIFFERENT expected, the default posture is
   `hold_and_warn`: the month does NOT pay and a loud `promote_expected_changed` warning names the old
   and new figures and asks for re-approval. A tenant may configure `pay_current_and_warn` instead —
   which pays the CURRENT number (never the stored one) and still shouts. There is no configuration in
   which the stale number is paid.

3. IT NEVER RESURRECTS A MONTH THAT DOES NOT EXIST. A chain whose category is paid as a one-time FLAT
   amount (mig 256) has no months 2..N — they are suppressed before the gate is ever consulted, so a
   promote cannot reach them. Same for a category that does not qualify (mig 245) and for a month
   outside the configured expected window. Every such promote is reported as UNAPPLIED with the reason,
   never silently ignored.

4. IT IS NEVER SILENT, IN EITHER DIRECTION. Applied promotes, unapplied promotes, redundant promotes
   (the gate has since been met on its own) and stale promotes all appear in `expected_guard` and in
   `warnings`, with per-rep dollars.

MONTHS 2..6 IS CONFIG, NOT A CONSTANT. The owner's words give the DEFAULT (`from_month=2`,
`to_month=6`); a tenant can change the window. Same three-layer ladder style as the rest of this
module, degrading to the code default with migration 258 unapplied.

PURE + DB-FREE: every function takes its config/rows as arguments. The DB orchestration lives in
router.py and the re-application lives in sale_installment_engine.py.
"""

TABLE = "installment_promote"

# The owner's window, as the DEFAULT. Month 1 is deliberately outside it: month 1 is the activation
# month, which has its own gate (`m1_gate='activation_payment'`, mig 210) and its own meaning.
DEFAULT_CONFIG = {
    "enabled": True,
    "from_month": 2,
    "to_month": 6,
    # What to do when a recompute produces a different expected than the one that was approved.
    #   'hold_and_warn'        (default) — do NOT pay; shout; ask for re-approval.
    #   'pay_current_and_warn'           — pay the CURRENT number (never the stored one); shout.
    "on_expected_change": "hold_and_warn",
    # A promote is a MONEY WRITE, so an unidentifiable caller is refused by default — deliberately
    # STRICTER than the rest of this module (which degrades open when RBAC cannot resolve a caller),
    # because an audit row reading "promoted by: unknown" is not an audit row. A genuinely RBAC-off
    # deployment can set this true.
    "promote_allow_unidentified": False,
}

ON_CHANGE_MODES = ("hold_and_warn", "pay_current_and_warn")

# Statuses a promote row can carry. 'revoked' rows are kept (the audit trail is the point) and are
# never re-applied.
STATUSES = ("active", "revoked")

# Why a promote did not apply. Every one of these is REPORTED, never swallowed.
UNAPPLIED_REASONS = {
    "chain_not_found": "No installment chain for that transaction/month exists in this pay period.",
    "month_suppressed": "That month does not exist: the chain's category is paid as a ONE-TIME FLAT "
                        "amount, so it has no monthly installments.",
    "out_of_window": "That month is outside the configured expected-commission window.",
    "disabled": "Expected-commission promotes are switched off for this tenant.",
    "expected_changed": "The expected amount changed since this was approved, and the tenant's "
                        "posture is to HOLD rather than pay a different number.",
    "revoked": "This promote was revoked.",
}


def _f(v):
    from app.modules.commcalc.calculator import safe_float
    return safe_float(v)


def _int(v, default):
    try:
        return int(str(v).strip())
    except (TypeError, ValueError):
        return default


def normalize_config(stored):
    """A stored config -> the full dict, code defaults filling anything absent. PURE; never raises.

    The window is normalised so `from_month <= to_month`, both clamped to 1..12 — a reversed or absurd
    window silently paying nothing would be exactly the silent-zero class this module exists to end."""
    out = dict(DEFAULT_CONFIG)
    if isinstance(stored, dict):
        if "enabled" in stored:
            out["enabled"] = bool(stored.get("enabled"))
        out["from_month"] = _int(stored.get("from_month"), out["from_month"])
        out["to_month"] = _int(stored.get("to_month"), out["to_month"])
        mode = str(stored.get("on_expected_change") or "").strip().lower()
        if mode in ON_CHANGE_MODES:
            out["on_expected_change"] = mode
        if "promote_allow_unidentified" in stored:
            out["promote_allow_unidentified"] = bool(stored.get("promote_allow_unidentified"))
    lo = max(1, min(12, out["from_month"]))
    hi = max(1, min(12, out["to_month"]))
    out["from_month"], out["to_month"] = (lo, hi) if lo <= hi else (hi, lo)
    return out


def in_window(month_index, cfg):
    """Is this installment month inside the tenant's expected-commission window? PURE."""
    c = cfg or DEFAULT_CONFIG
    try:
        m = int(month_index)
    except (TypeError, ValueError):
        return False
    return c["from_month"] <= m <= c["to_month"]


def promote_key(pay_period, trans_id, mdn, month_index):
    """The stable identity of ONE promotable chain-month — the same shape as the ledger's own UNIQUE
    key (org_id, trans_id, mdn, month_index, pay_period), so a promote can never point at two rows or
    drift from the row it approved. PURE."""
    return (str(pay_period or "").strip(),
            str(trans_id or "").strip(),
            str(mdn or "").strip(),
            _int(month_index, 0))


def row_key(row):
    """promote_key for a ledger row OR a stored promote row (they carry the same four fields). PURE."""
    return promote_key(row.get("pay_period"), row.get("trans_id"), row.get("mdn"),
                       row.get("month_index"))


def build_index(promote_rows, pay_period=None):
    """{promote_key: row} for the ACTIVE promotes. Revoked rows are excluded from application but the
    caller still sees them in the audit list. When two rows collide (should be impossible — the table
    has a UNIQUE key) the most recently promoted wins, deterministically. PURE.

    `pay_period` matches through `_pvariants` at the CALLER (the DB read), not here; this function is
    period-agnostic and simply indexes what it is given."""
    idx = {}
    for r in (promote_rows or []):
        if str(r.get("status") or "active").strip().lower() != "active":
            continue
        k = row_key(r)
        prev = idx.get(k)
        if prev is None or str(r.get("promoted_at") or "") >= str(prev.get("promoted_at") or ""):
            idx[k] = r
    return idx


def evaluate(promote, expected_now, cfg, tolerance=0.005):
    """Decide what ONE active promote does to ONE chain-month, given today's expected amount. PURE.

    Returns {apply: bool, amount: float|None, stale: bool, expected_at_promote, expected_now,
             reason: str|None, mode}.

    There is NO branch that returns the stored amount: `amount` is always today's expected. The stored
    figure exists only to detect that it MOVED.
    """
    cfg = cfg or DEFAULT_CONFIG
    was = promote.get("expected_at_promote")
    was_f = None if was is None else round(_f(was), 2)
    now_f = round(_f(expected_now), 2)
    stale = was_f is not None and abs(now_f - was_f) > tolerance
    out = {"apply": True, "amount": now_f, "stale": stale, "expected_at_promote": was_f,
           "expected_now": now_f, "reason": None, "mode": cfg["on_expected_change"]}
    if not stale:
        return out
    if cfg["on_expected_change"] == "pay_current_and_warn":
        out["reason"] = "expected_changed_paid_current"
        return out
    out["apply"] = False
    out["amount"] = None
    out["reason"] = "expected_changed"
    return out


def promote_row(org_id, ledger_row, reason, who, when, expected_amount=None):
    """An INSERT-ready promote row for ONE ledger row. org_id is stamped here so no caller can forget
    it (RULE ONE: config/audit rows carry the tenant). PURE."""
    exp = ledger_row.get("expected_amount") if expected_amount is None else expected_amount
    return {
        "org_id": org_id,
        "pay_period": str(ledger_row.get("pay_period") or "").strip(),
        "sale_period": str(ledger_row.get("sale_period") or "").strip() or None,
        "trans_id": str(ledger_row.get("trans_id") or "").strip(),
        "mdn": str(ledger_row.get("mdn") or "").strip(),
        "serial_1": str(ledger_row.get("serial_1") or "").strip() or None,
        "month_index": _int(ledger_row.get("month_index"), 0),
        "schedule_id": ledger_row.get("schedule_id"),
        "plan_id": ledger_row.get("plan_id"),
        "epay_salesperson": str(ledger_row.get("epay_salesperson") or "").strip() or None,
        "store": str(ledger_row.get("store") or "").strip() or None,
        "expected_at_promote": round(_f(exp), 2),
        "reason": str(reason or "").strip(),
        "status": "active",
        "promoted_by": who,
        "promoted_at": when,
    }


def summarize(applied, unapplied, stale, redundant):
    """The `expected_guard` block. Kept OUT of `totals` so that dict stays byte-identical for every
    existing consumer/harness. PURE."""
    return {
        "promotes_applied": len(applied),
        "promoted_amount": round(sum(_f(a.get("amount")) for a in applied), 2),
        "promotes_unapplied": len(unapplied),
        "promotes_stale": len(stale),
        "promotes_redundant": len(redundant),
        "applied": applied,
        "unapplied": unapplied,
        "stale": stale,
        "redundant": redundant,
    }


# ── loaders (the only I/O; each degrades to the code default when 258 is unapplied) ────────────────
def load_config(client, org_id):
    """The tenant's expected-commission config. Missing column/table -> code defaults. Never raises."""
    stored = None
    try:
        rows = (client.schema("commcalc").table("commission_org_config")
                .select("expected_commission_config")
                .eq("org_id", org_id).limit(1).execute().data) or []
        if rows:
            stored = rows[0].get("expected_commission_config")
    except Exception:
        stored = None
    out = normalize_config(stored)
    out["_stored"] = isinstance(stored, dict) and bool(stored)
    return out


def load_promotes(client, org_id, pay_period, period_variants=None):
    """ACTIVE + revoked promote rows for ONE pay period, ORG-SCOPED. Missing table (258 unapplied) ->
    []. Never raises. `period_variants` lets the caller pass `_pvariants(period)` so both period
    spellings match — the recurring bug class in this codebase."""
    try:
        q = (client.schema("commcalc").table(TABLE).select("*").eq("org_id", org_id))
        if period_variants:
            q = q.in_("pay_period", list(period_variants))
        elif pay_period:
            q = q.eq("pay_period", pay_period)
        return (q.limit(100000).execute().data) or []
    except Exception:
        return []
