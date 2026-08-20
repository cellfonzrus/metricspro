"""Approvals adapter — PAYROLL HOURS (owner directive 2026-08-19). MONEY-CRITICAL — INTIMATION-ONLY.

payroll_hours is deliberately NOT inbox-actionable. The payroll-hours board (storeops/payroll_approval.py)
is a TWO-STAGE, multi-actor workflow — a DM reviews and may correct hours, then HR releases them for
payment — operating per-employee-per-period in batches, with send-back/reset and correction/adjustment
side effects. That cannot be faithfully reduced to a single inbox approve/deny, and a wrong mapping on a
payroll path releases real money. So this type is registered ONLY to give the unified inbox a label and
to HARD-BLOCK any decision from the inbox:

  • approver_predicate always returns False → the approvals router refuses the decision (403) before it
    ever reaches the engine; and
  • on_decide raises → even if a decision were forced through, the request stays pending and nothing is
    applied (never a silent divergence between the inbox and the payroll board).

The board itself owns the decision and MIRRORS its state into the inbox via engine.create_request /
engine.sync_source_decision (see payroll_approval._intimate_payroll_decision): a DM approval opens the
HR-release request, an HR approval/send-back closes it approved/denied. See docs/APPROVALS_AND_CHAT_PLAN.md.
"""
from app.modules.approvals import engine


def _no_inbox_decide(ctx, request):
    """payroll_hours is decided ONLY on its own two-stage board, never from the unified inbox."""
    return False


@engine.register_type("payroll_hours", label="Payroll hours (HR release)",
                      approver_predicate=_no_inbox_decide)
def _on_decide(request, decision, actor, note):
    raise RuntimeError("payroll_hours is decided on the payroll approvals board (DM then HR), "
                       "not from the unified inbox")
