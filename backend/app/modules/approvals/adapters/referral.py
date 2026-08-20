"""Approvals adapter — REFERRAL COMMISSION (owner directive 2026-08-19). MONEY-CRITICAL (gated commission).

Registers the `referral` type with the unified approvals engine. A referral decision moves the referral
from commission_pending → approved (booking a USER-DEFINED-or-default commission payable on a resolved
date) or → rejected. The effect lives in ONE shared function — referral.router._apply_referral_decision —
called identically by the legacy /referrals/{id}/approve + /reject endpoints (which also call
engine.sync_source_decision) and this on_decide, so the money (amount + payout date) is never recomputed
anywhere else. See docs/APPROVALS_AND_CHAT_PLAN.md.

Segregation of duties: the referral module forbids the approver from being the rep who created the
referral (referral_core.approval_conflict). That control is preserved for the inbox path via an
approver_predicate, checked by the approvals router BEFORE the decision runs — the SAME gate the legacy
endpoint enforces. An inbox approve carries no amount override, so it books the referral's stored/tenant
default commission (equivalent to a legacy approve with an empty amount field).

Decision mapping: approve → 'approved'; deny → 'rejected'.
"""
from app.modules.approvals import engine


def _approver_predicate(ctx, request):
    """Whether the inbox caller may decide this referral: they must be able to approve referral payouts
    (referral._can_approve) AND must not be the rep who created it (referral_core.approval_conflict) —
    the SAME RBAC + segregation-of-duties the legacy /referrals/{id}/approve endpoint applies. Fail closed."""
    from app.modules.referral import router as R
    try:
        org_id = request.get("org_id")
        rid = request.get("source_id")
        if not rid:
            return False
        caller = R._caller(ctx.get("authorization", ""), org_id)
        if not R._can_approve(caller):
            return False
        r = R._get_referral_safe(org_id, rid)
        if not r:
            return False
        conflict = R.core.approval_conflict((caller or {}).get("employee_id"),
                                            (caller or {}).get("id"), r)
        return not conflict
    except Exception:
        return False


@engine.register_type("referral", label="Referral commission", approver_predicate=_approver_predicate)
def _on_decide(request, decision, actor, note):
    """Apply an approvals-inbox decision to the underlying referral via the module's shared effect.
    Idempotent: only acts while the referral is still commission_pending (already-decided → no-op; the
    engine still stamps the approval_request). The RBAC/SoD gate ran in the approver_predicate first."""
    from app.modules.referral import router as R
    org_id = request.get("org_id")
    rid = request.get("source_id")
    if not rid:
        return
    r = R._get_referral_safe(org_id, rid)
    if not r or r.get("status") != "commission_pending":
        return
    R._apply_referral_decision(
        org_id, r, "approve" if (decision or "").lower() == "approve" else "deny",
        caller={"email": actor}, note=note)
