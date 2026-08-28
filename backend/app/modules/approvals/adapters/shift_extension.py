"""Approvals adapter — SHIFT EXTENSION (owner directive 2026-08-19).

Registers the `shift_extension` type with the unified approvals engine. A shift-extension decision is a
pure status flip on the storeops.shift_extension row (the forced-clockout sweep later honours
status='approved'), so the effect is simply stamping that row — done identically whether the decision
comes from the legacy /shift-extensions board (which calls engine.sync_source_decision) or the unified
Approvals inbox (this on_decide). See docs/APPROVALS_AND_CHAT_PLAN.md.
"""
from datetime import datetime, timezone

from app.modules.approvals import engine


@engine.register_type("shift_extension", label="Shift extension")
def _on_decide(request, decision, actor, note):
    from app.modules.storeops import router as S
    org_id = request.get("org_id")
    ext_id = request.get("source_id")
    if not ext_id:
        return
    rows = (S.sb().table("shift_extension").select("status")
            .eq("org_id", org_id).eq("id", ext_id).limit(1).execute().data) or []
    if not rows or rows[0].get("status") != "pending":
        return   # already decided on the legacy board; the engine still stamps the approval_request
    S.sb().table("shift_extension").update({
        "status": "approved" if (decision or "").lower() == "approve" else "denied",
        "decided_by": actor, "decided_by_name": actor,
        "decided_at": datetime.now(timezone.utc).isoformat(), "decision_note": note,
    }).eq("org_id", org_id).eq("id", ext_id).execute()
