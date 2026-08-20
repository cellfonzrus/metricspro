"""Approvals adapter — MANAGEMENT INCENTIVE PAYOUT (owner directive 2026-08-19). MONEY — INTIMATION-ONLY.

A management-incentive payout is a multi-state commission ledger (draft -> approved -> paid) whose
decision endpoint takes THREE actions — approve / deny / pay — where 'deny' reopens the row to draft
(not a terminal rejection) and 'pay' disburses commission money. That is not a single binary
approve/deny, and a wrong mapping releases real commission. So this type is INTIMATION-ONLY: registered
only to give the unified inbox a label and to HARD-BLOCK any inbox decision (approver_predicate → False,
on_decide raises). The incentive board owns the decision and MIRRORS the payout's state into the inbox
via engine.create_request / engine.sync_source_decision (see commcalc.router._intimate_mi_payout +
mi_payout_decision). See docs/APPROVALS_AND_CHAT_PLAN.md.
"""
from app.modules.approvals import engine


def _no_inbox_decide(ctx, request):
    """A management-incentive payout is decided ONLY on the incentive board, never from the inbox."""
    return False


@engine.register_type("management_incentive", label="Management incentive payout",
                      approver_predicate=_no_inbox_decide)
def _on_decide(request, decision, actor, note):
    raise RuntimeError("management_incentive is decided on the incentive board "
                       "(approve/deny/pay), not from the unified inbox")
