"""Approvals adapter — HOURS-BUDGET OVERRIDE (owner directive 2026-08-19).

Registers the `budget_override` type. Approving unlocks scheduling a store past its weekly hours budget;
the effect is a pure status flip on storeops.budget_override (schedulers read status='approved' later),
so on_decide stamps that row — identical to the legacy /budget-overrides board. See
docs/APPROVALS_AND_CHAT_PLAN.md.
"""
from datetime import datetime, timezone

from app.modules.approvals import engine


@engine.register_type("budget_override", label="Hours-budget override")
def _on_decide(request, decision, actor, note):
    from app.modules.storeops import router as S
    org_id = request.get("org_id")
    ov_id = request.get("source_id")
    if not ov_id:
        return
    rows = (S.sb().table("budget_override").select("status")
            .eq("org_id", org_id).eq("id", ov_id).limit(1).execute().data) or []
    if not rows or rows[0].get("status") != "pending":
        return
    S.sb().table("budget_override").update({
        "status": "approved" if (decision or "").lower() == "approve" else "denied",
        "decided_by": actor, "decided_by_name": actor,
        "decided_at": datetime.now(timezone.utc).isoformat(), "decision_note": note,
    }).eq("org_id", org_id).eq("id", ov_id).execute()
