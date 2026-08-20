"""Approvals adapter — AUTOMATED REMEDIATION (owner directive 2026-08-19).

Registers the `remediation` type with the unified approvals engine. A remediation decision approves or
rejects an awaiting_approval commcalc.remediation_request; APPROVING runs the one bounded playbook
(status → executed, or → failed on error), rejecting stops it (→ rejected). The effect already lives in
ONE shared function — remediation.router._apply_decision — used by the web magic-link decision endpoint
AND the WhatsApp webhook; this on_decide calls that SAME function, so an inbox decision behaves and
audits identically. See docs/APPROVALS_AND_CHAT_PLAN.md.

These are org-level / ops-admin requests (no store scope), so the unified inbox surfaces them to
unrestricted (admin) approvers only — the engine's default store-scope guard already enforces that.

Decision mapping: approve → run the playbook (approve); deny → reject.
"""
from app.modules.approvals import engine


@engine.register_type("remediation", label="Automated remediation")
def _on_decide(request, decision, actor, note):
    """Apply an approvals-inbox decision to the underlying remediation_request via the module's shared
    effect. Idempotent: only acts while the request is still awaiting_approval (already-decided → no-op;
    the engine still stamps the approval_request)."""
    from app.modules.remediation import router as RM
    org_id = request.get("org_id")
    rid = request.get("source_id")
    if not rid:
        return
    client = RM.sb()
    rows = (client.schema("commcalc").table("remediation_request").select("*")
            .eq("org_id", org_id).eq("id", rid).limit(1).execute().data) or []
    if not rows or rows[0].get("status") != "awaiting_approval":
        return
    RM._apply_decision(client, org_id, rows[0],
                       "approve" if (decision or "").lower() == "approve" else "reject",
                       actor or "approver")
