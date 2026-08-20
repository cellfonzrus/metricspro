"""Approvals adapter — CROSS-TENANT INGEST GUARD (owner directive 2026-08-19). INTIMATION-ONLY.

The ingest guard quarantines rows whose store string doesn't resolve to one of this tenant's stores
(a likely cross-tenant mis-file). Ruling on a flag is binary (allow / reject), BUT 'allow' requires the
reviewer to PICK which of our stores the foreign string maps to (creating a store alias) and then
RELEASES the withheld rows into the ledger — a store-code decision the generic inbox cannot supply and a
cross-tenant data write that must remain a deliberate human action on the guard board. So this type is
INTIMATION-ONLY: registered only to give the unified inbox a label and to HARD-BLOCK any inbox decision
(approver_predicate → False, on_decide raises). The guard board owns the decision and MIRRORS each flag's
state into the inbox via engine.create_request / engine.sync_source_decision (see
ingest_store_guard._intimate_quarantine + router.decide_ingest_guard_item). See
docs/APPROVALS_AND_CHAT_PLAN.md.
"""
from app.modules.approvals import engine


def _no_inbox_decide(ctx, request):
    """A quarantined store flag is ruled on ONLY on the ingest guard board, never from the inbox."""
    return False


@engine.register_type("ingest_guard", label="Cross-tenant ingest guard",
                      approver_predicate=_no_inbox_decide)
def _on_decide(request, decision, actor, note):
    raise RuntimeError("ingest_guard is decided on the ingest guard board (allow picks the store + "
                       "releases rows; reject parks them), not from the unified inbox")
