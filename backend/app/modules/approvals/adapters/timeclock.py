"""Approvals adapter — TIME-CLOCK PERMISSION (the pilot; owner directive 2026-08-19).

Registers the `timeclock_permission` type with the unified approvals engine so a decision made in the
central Approvals inbox performs the SAME effect as the legacy /timeclock/permissions board — both call
storeops.router._apply_timeclock_permission_decision, the one shared effect function. See
docs/APPROVALS_AND_CHAT_PLAN.md.
"""
from app.modules.approvals import engine


@engine.register_type("timeclock_permission", label="Time-clock permission")
def _on_decide(request, decision, actor, note):
    """Apply an approvals-inbox decision to the underlying time-clock permission + punch. Loads the perm
    by the request's source_id and delegates to the module's shared effect. Idempotent: if the perm is
    already decided (e.g. via the legacy board), it does nothing here — the engine still stamps the
    approval_request so the inbox reflects the final state."""
    from app.modules.storeops import router as S
    org_id = request.get("org_id")
    perm_id = request.get("source_id")
    if not perm_id:
        return
    rows = (S.sb().table("timeclock_permission").select("*")
            .eq("org_id", org_id).eq("id", perm_id).limit(1).execute().data) or []
    if not rows or rows[0].get("status") != "pending":
        return
    new_status = "approved" if (decision or "").lower() == "approve" else "denied"
    S._apply_timeclock_permission_decision(org_id, rows[0], new_status, {"email": actor}, note=note)
