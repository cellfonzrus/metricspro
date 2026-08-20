"""Unified Approvals inbox API. The single surface every module's approval/intimation request surfaces
on. See docs/APPROVALS_AND_CHAT_PLAN.md and engine.py.

Reads are member-scoped to the caller's store span (the same scope_keyset every storeops read applies);
decisions require a manager who is an eligible approver for the request's scope AND passes the type's
own approver_predicate. Identity is resolved from the caller's token — never trusted from the body.
"""
from fastapi import APIRouter, Depends, Header, HTTPException

from app.modules.approvals import engine

ORG_ID = "00000000-0000-0000-0000-000000000001"


def _require_member(authorization: str = Header(default=""), org_id: str = ORG_ID):
    # Router-wide gate: every approvals endpoint requires a signed-in member of the org. Reuses the
    # storeops membership check (super-admins pass on any org, same as elsewhere).
    from app.modules.storeops.router import _require_member as sm
    sm(authorization, org_id)


router = APIRouter(prefix="/approvals", tags=["Approvals"], dependencies=[Depends(_require_member)])


def _caller(authorization: str, org_id: str):
    """The signed-in manager's {org_id, email, role, employee_id}. 401/403 if not a manager."""
    from app.modules.storeops.router import _require_manager
    return _require_manager(authorization, org_id)


def _may_decide(authorization: str, org_id: str, request: dict) -> bool:
    """Whether the caller may decide `request`: in their store span (or unrestricted/admin), AND the
    type's approver_predicate (if any) allows it."""
    from app.modules.storeops.router import scope_keyset, in_keyset
    ks = scope_keyset(authorization, org_id)
    store = request.get("store_code")
    if ks is not None:
        # store-scoped caller: the request's store must be in their span. An org-level (store-less)
        # request is admin-only, so a scoped caller cannot decide it.
        if store is None or not in_keyset(ks, store):
            return False
    spec = engine._TYPES.get(request.get("type"))
    if spec and spec.approver_predicate:
        try:
            ctx = {"authorization": authorization, "org_id": org_id}
            if not spec.approver_predicate(ctx, request):
                return False
        except Exception:
            return False
    return True


@router.get("")
def list_approvals(status: str = "pending", type: str = "", store_code: str = "",
                   authorization: str = Header(default=""), org_id: str = ORG_ID):
    """The caller's approval inbox, scoped to their store span. ?status=pending|approved|denied|... ,
    ?type=, ?store_code=. Newest first."""
    rows = engine.list_inbox(org_id, authorization=authorization,
                             status=(status if status != "all" else ""), type=type, store_code=store_code)
    return {"approvals": rows, "types": engine.registered_types()}


@router.get("/summary")
def approvals_summary(authorization: str = Header(default=""), org_id: str = ORG_ID):
    """Pending counts (overall + by type), scoped to the caller — powers the nav badge."""
    return engine.summary(org_id, authorization=authorization)


@router.get("/{request_id}")
def approval_detail(request_id: str, authorization: str = Header(default=""), org_id: str = ORG_ID):
    req = engine.get_request(org_id, request_id)
    if not req:
        raise HTTPException(404, "not found")
    from app.modules.storeops.router import scope_keyset, in_keyset
    ks = scope_keyset(authorization, org_id)
    if ks is not None and (req.get("store_code") is None or not in_keyset(ks, req.get("store_code"))):
        raise HTTPException(403, "outside your scope")
    events = (engine._sb().table("approval_events").select("*")
              .eq("org_id", org_id).eq("request_id", request_id).order("created_at").execute().data) or []
    return {"approval": req, "events": events}


@router.post("/{request_id}/decision")
def decide_approval(request_id: str, body: dict, authorization: str = Header(default=""), org_id: str = ORG_ID):
    """Approve or deny a request IN-APP. Body: {decision:'approve'|'deny', note?}. The tick performs
    the module's real effect (via the registered handler) and is recorded with who + when."""
    mgr = _caller(authorization, org_id)
    org_id = mgr.get("org_id") or org_id
    req = engine.get_request(org_id, request_id)
    if not req:
        raise HTTPException(404, "unknown request")
    if not _may_decide(authorization, org_id, req):
        raise HTTPException(403, "you are not an eligible approver for this request")
    decision = str(body.get("decision") or "").strip().lower()
    try:
        out = engine.decide(org_id, request_id, decision=decision,
                            actor=mgr.get("email"), actor_name=mgr.get("email"),
                            note=(body.get("note") or None))
    except ValueError as e:
        msg = str(e)
        raise HTTPException(409 if "already" in msg else 400, msg)
    except Exception as e:
        # A module handler blew up — the request stays pending; surface it rather than half-applying.
        raise HTTPException(400, f"could not apply the decision: {e}")
    return {"ok": True, "status": out.get("status"), "decided_by": mgr.get("email")}


class _CreateBody:
    pass


@router.post("")
def create_approval(body: dict, authorization: str = Header(default=""), org_id: str = ORG_ID):
    """Create a generic/manual approval request (e.g. a free-form request, or one raised from chat).
    Module-specific requests are normally created server-side via engine.create_request, not here.
    Requires a manager (the requester is stamped from the token)."""
    mgr = _caller(authorization, org_id)
    org_id = mgr.get("org_id") or org_id
    title = (body.get("title") or "").strip()
    if not title:
        raise HTTPException(400, "title is required")
    req = engine.create_request(
        org_id, type=(body.get("type") or "manual"), title=title,
        summary=body.get("summary"), payload=body.get("payload") or {},
        requested_by=mgr.get("employee_id"), requested_by_name=mgr.get("email"),
        store_code=body.get("store_code"), market=body.get("market"),
        assignee_email=body.get("assignee_email"), assignee_kind=body.get("assignee_kind"),
        priority=(body.get("priority") or "normal"))
    if not req.get("id"):
        raise HTTPException(400, f"could not create the request: {req.get('error')}")
    return {"approval": req}


# Register the built-in approval-type adapters for their side effect (each calls engine.register_type at
# import). Imported here — the module main.py already mounts — so no shared file needs editing per type.
from app.modules.approvals.adapters import timeclock as _adapter_timeclock  # noqa: E402,F401
from app.modules.approvals.adapters import shift_extension as _adapter_shift_extension  # noqa: E402,F401
from app.modules.approvals.adapters import budget_override as _adapter_budget_override  # noqa: E402,F401
from app.modules.approvals.adapters import closing_expense as _adapter_closing_expense  # noqa: E402,F401
from app.modules.approvals.adapters import referral as _adapter_referral  # noqa: E402,F401
from app.modules.approvals.adapters import remediation as _adapter_remediation  # noqa: E402,F401
from app.modules.approvals.adapters import payroll_hours as _adapter_payroll_hours  # noqa: E402,F401
from app.modules.approvals.adapters import management_incentive as _adapter_mgmt_incentive  # noqa: E402,F401
