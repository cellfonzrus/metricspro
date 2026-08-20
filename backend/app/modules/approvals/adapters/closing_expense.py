"""Approvals adapter — CLOSING EXPENSE LINE (owner directive 2026-08-19). MONEY-CRITICAL.

Registers the `closing_expense` type with the unified approvals engine. A closing-expense decision
approves or rejects ONE categorized commcalc.closing_expense line; APPROVING an 'expense'-kind line
pushes that category's updated P&L total for the period (a real accounting effect).

The effect lives in ONE shared function — closing.router._apply_expense_line_decision — called
identically whether the decision comes from the legacy /closing/expense/{id}/decide board (which also
calls engine.sync_source_decision) or the unified Approvals inbox (this on_decide). The money/account
routing/P&L logic is NEVER duplicated here. See docs/APPROVALS_AND_CHAT_PLAN.md.

Decision mapping (faithful to the module's own vocabulary):
    approve → closing_expense.status = 'approved'  (books the expense onto the P&L)
    deny    → closing_expense.status = 'rejected'
"""
from app.modules.approvals import engine


@engine.register_type("closing_expense", label="Store expense")
def _on_decide(request, decision, actor, note):
    """Apply an approvals-inbox decision to the underlying closing_expense line via the module's shared
    effect. Idempotent: if the line is already decided (e.g. on the legacy closing-management board), do
    nothing — the engine still stamps the approval_request so the inbox reflects the final state."""
    from app.modules.closing import router as C
    org_id = request.get("org_id")
    expense_id = request.get("source_id")
    if not expense_id:
        return
    client = C.sb()
    rows = (client.schema("commcalc").table("closing_expense").select("*")
            .eq("org_id", org_id).eq("id", expense_id).limit(1).execute().data) or []
    if not rows or rows[0].get("status") != "pending":
        return
    status = "approved" if (decision or "").lower() == "approve" else "rejected"
    C._apply_expense_line_decision(client, org_id, rows[0], status, actor)
