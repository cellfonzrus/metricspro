"""THE COMPANY NOTIFICATION INBOX — its read surface (owner 2026-11-24, index §CORE-49).

Mounted ONTO `core.router` (same pattern as import_health / fix_pipeline), so its paths resolve to
/api/v1/core/notifications*. No new gate, no new org clamp and no second read of the table:

  · the GATE is `import_health.can_view_attention`, via that module's own `_gate` — the same gate
    that already guards the admin-attention popup these items replace. A tenant that has granted
    /admin/import-health to a role has already decided who hears about broken feeds, and this
    surface must not quietly widen that decision.
  · the ORG is clamped by the same `_gate`: a non-super-admin reads their OWN company whatever
    `org_id` the query asks for. Cross-tenant leakage lives in tenant-scoped reads (CLAUDE.md,
    owner 2026-10-30), so the clamp is pinned by a test that ASKS for another company's inbox.
  · the ROWS come from `core.notification_inbox` through `notification_inbox.list_items`, which is
    the one home for that read.

RULE TWO: no carrier, tenant, store or product name appears here.
"""
from fastapi import APIRouter, Header, HTTPException
from pydantic import BaseModel

from app.core.database import get_supabase
from app.modules.core import notification_inbox as NI
from app.modules.core.import_health import ORG_ID, _gate

router = APIRouter(prefix="/notifications", tags=["Core / Notifications"])


def sb():
    return get_supabase()


class ReadBody(BaseModel):
    ids: list[str] | None = None
    all: bool = False


@router.get("")
def list_notifications(org_id: str = ORG_ID, include_resolved: int = 0, scope: str = "",
                       authorization: str = Header(default=""),
                       x_active_org: str = Header(default="")):
    """This company's notification inbox: one item per standing finding, newest sighting first.

    `read_ok` is the silent-zero guard (§23e): a caller can tell "this company has nothing to
    report" from "the read did not happen", so a surface never renders a confident 0 it did not earn.
    """
    _client, _caller, org = _gate(authorization, x_active_org, org_id)   # 401/403 before any read
    items = NI.list_items(sb(), org, include_resolved=bool(include_resolved), scope=scope or None)
    return {"org_id": org, "items": items, "unread": NI.unread_count(items),
            "open": sum(1 for i in items if not i.get("resolved_at")), "read_ok": True,
            "severities": list(NI.SEVERITIES)}


@router.post("/read")
def mark_notifications_read(body: ReadBody, org_id: str = ORG_ID,
                            authorization: str = Header(default=""),
                            x_active_org: str = Header(default="")):
    """Mark items read. Org-scoped at the write, so an id from another company matches nothing."""
    _client, _caller, org = _gate(authorization, x_active_org, org_id)
    if not body.all and not (body.ids or []):
        raise HTTPException(400, "pass ids, or all=true")
    return {"org_id": org, **NI.mark_read(sb(), org, ids=body.ids, all_items=body.all)}


@router.get("/routes")
def list_routes(org_id: str = ORG_ID, authorization: str = Header(default=""),
                x_active_org: str = Header(default="")):
    """Which alert scopes this company carries to the inbox, to email/WhatsApp, or to both — the
    DECLARATION behind the change, so a tenant can see why an email stopped arriving."""
    _client, _caller, org = _gate(authorization, x_active_org, org_id)
    rows = NI.routes(sb(), org_id=org)      # this company + the house defaults, nothing else
    scopes = sorted({(r.get("scope") or "").strip() for r in rows if (r.get("scope") or "").strip()})
    return {"org_id": org, "routes": [{"scope": s, "route": NI.route_for(rows, s, org)} for s in scopes],
            "default": NI.DEFAULT_ROUTE, "known": list(NI.ROUTES)}
