"""Unified Approvals Engine — the ONE lifecycle every module's approval/intimation request flows
through. See docs/APPROVALS_AND_CHAT_PLAN.md.

The engine owns the request LIFECYCLE (create → pending → approved/denied/cancelled/expired + audit +
notification); each module owns the EFFECT of a decision, contributed as a registered handler:

    from app.modules.approvals import engine

    @engine.register_type("timeclock_permission", label="Time-clock permission")
    def _decide_timeclock(request, decision, actor, note):
        ...perform the module's real effect (stamp hours, extend clock-out, ...)...

Storage is storeops.approval_requests / approval_events (migration 867), service-role-only behind
FastAPI. Creation is idempotent per (type, source_table, source_id) so a module can safely call
create_request every time without spawning duplicates.
"""
from __future__ import annotations

from datetime import datetime, timezone


def _sb():
    from app.core.database import get_supabase
    return get_supabase().schema("storeops")


def _now_iso():
    return datetime.now(timezone.utc).isoformat()


# ── Type registry ────────────────────────────────────────────────────────────────────────────────
class _TypeSpec:
    __slots__ = ("type", "label", "on_decide", "approver_predicate", "renderer")

    def __init__(self, type, label, on_decide, approver_predicate, renderer):
        self.type = type
        self.label = label
        self.on_decide = on_decide
        self.approver_predicate = approver_predicate
        self.renderer = renderer


_TYPES: dict[str, _TypeSpec] = {}


def register_type(type_key, *, label=None, approver_predicate=None, renderer=None):
    """Register an approval type. The decorated function becomes `on_decide(request, decision, actor,
    note)` — it performs the module's real effect and may raise to abort the decision. Idempotent:
    re-registering a type REPLACES it (same posture as core.import_health.register_provider)."""
    def deco(fn):
        _TYPES[type_key] = _TypeSpec(type_key, label or type_key, fn, approver_predicate, renderer)
        return fn
    return deco


def registered_types():
    return {k: v.label for k, v in _TYPES.items()}


# ── Notification (reuses the storeops email + manager-resolution helpers) ─────────────────────────
def _notify_approvers(org_id, request):
    """Fire-and-forget email to whoever can approve `request` — its explicit assignee, else the store's
    DM + managers above, else the tenant admins. Reuses the storeops helpers so approvals share ONE
    notification path with the time-clock permissions. NEVER raises."""
    import threading

    def _run():
        try:
            from app.modules.storeops import router as S
            recips = set()
            if request.get("assignee_email"):
                recips.add(str(request["assignee_email"]).strip())
            store_code = request.get("store_code")
            if store_code:
                try:
                    roster = S._managers_above_dm(org_id, store_code)
                    for grp in ("dm", "above"):
                        for m in (roster.get(grp) or []):
                            if m.get("email"):
                                recips.add(str(m["email"]).strip())
                except Exception:
                    pass
            if not recips:
                try:
                    recips.update(S._admin_fallback_emails(org_id))
                except Exception:
                    pass
            recips = {e for e in recips if e}
            if not recips:
                return
            biz = S._tenant_display_name(org_id)
            title = request.get("title") or "A request"
            lines = [title, ""]
            if request.get("summary"):
                lines += [request["summary"], ""]
            lines += [f"Approve or deny it in {biz}: Approvals.",
                      f"(Request #{request.get('request_no') or ''}.)"]
            S._send_plain_email(emails=sorted(recips),
                                subject=f"Approval needed: {title}", body="\n".join(lines))
        except Exception:
            pass

    try:
        threading.Thread(target=_run, daemon=True, name="approval-notify").start()
    except Exception:
        pass


# ── Lifecycle ─────────────────────────────────────────────────────────────────────────────────────
_LIVE = ("pending", "approved", "denied")


def create_request(org_id, *, type, title, source_table=None, source_id=None, summary=None,
                   payload=None, requested_by=None, requested_by_name=None, store_code=None,
                   market=None, assignee_kind=None, assignee_employee_id=None, assignee_email=None,
                   priority="normal", due_at=None, notify=True):
    """Create (idempotently) a pending approval request and notify its approvers. If a LIVE request
    already exists for the same (type, source_table, source_id), it is returned unchanged (no dup, no
    second notification). NEVER raises — an approvals miss must not fail the caller's own work."""
    try:
        if source_table and source_id:
            existing = (_sb().table("approval_requests").select("*")
                        .eq("org_id", org_id).eq("type", type)
                        .eq("source_table", source_table).eq("source_id", str(source_id))
                        .in_("status", list(_LIVE)).limit(1).execute().data) or []
            if existing:
                return existing[0]
        row = {"org_id": org_id, "type": type, "title": title, "summary": summary,
               "source_table": source_table, "source_id": (str(source_id) if source_id else None),
               "payload": payload or {}, "requested_by": requested_by,
               "requested_by_name": requested_by_name, "store_code": store_code, "market": market,
               "assignee_kind": assignee_kind, "assignee_employee_id": assignee_employee_id,
               "assignee_email": assignee_email, "priority": priority, "status": "pending"}
        if due_at is not None:
            row["due_at"] = due_at.isoformat() if hasattr(due_at, "isoformat") else due_at
        saved = (_sb().table("approval_requests").insert(row).execute().data or [row])[0]
        try:
            _sb().table("approval_events").insert({
                "org_id": org_id, "request_id": saved.get("id"),
                "actor": requested_by_name or requested_by or "system",
                "event_type": "created", "detail": {"title": title}}).execute()
        except Exception:
            pass
        if notify:
            _notify_approvers(org_id, saved)
        return saved
    except Exception as e:
        return {"id": None, "type": type, "status": "pending", "error": str(e)}


def get_request(org_id, request_id):
    rows = (_sb().table("approval_requests").select("*")
            .eq("org_id", org_id).eq("id", request_id).limit(1).execute().data) or []
    return rows[0] if rows else None


def decide(org_id, request_id, *, decision, actor=None, actor_name=None, note=None):
    """Approve or deny a request: guard it is still pending, run the type's on_decide effect, stamp the
    status, and write an audit event. Raises ValueError on a bad/absent/already-decided request so the
    endpoint can map it to an HTTP error; a handler error aborts (status unchanged)."""
    decision = (decision or "").strip().lower()
    if decision not in ("approve", "deny"):
        raise ValueError("decision must be 'approve' or 'deny'")
    req = get_request(org_id, request_id)
    if not req:
        raise ValueError("unknown request")
    if req.get("status") != "pending":
        raise ValueError(f"already {req.get('status')}")
    spec = _TYPES.get(req.get("type"))
    new_status = "approved" if decision == "approve" else "denied"
    # Run the module effect FIRST — if it fails, the request stays pending (nothing half-applied).
    if spec and spec.on_decide:
        spec.on_decide(req, decision, actor, note)
    upd = {"status": new_status, "decision": decision, "decided_by": actor,
           "decided_by_name": actor_name or actor, "decided_at": _now_iso(), "decision_note": note,
           "updated_at": _now_iso()}
    (_sb().table("approval_requests").update(upd)
     .eq("org_id", org_id).eq("id", request_id).execute())
    try:
        _sb().table("approval_events").insert({
            "org_id": org_id, "request_id": request_id, "actor": actor_name or actor or "system",
            "event_type": new_status, "detail": {"note": note}}).execute()
    except Exception:
        pass
    return {**req, **upd}


def cancel_request(org_id, *, type, source_table, source_id, actor=None):
    """Withdraw the live request for a source record (e.g. the underlying record was voided). Best-effort."""
    try:
        rows = (_sb().table("approval_requests").select("id")
                .eq("org_id", org_id).eq("type", type).eq("source_table", source_table)
                .eq("source_id", str(source_id)).eq("status", "pending").execute().data) or []
        for r in rows:
            _sb().table("approval_requests").update(
                {"status": "cancelled", "updated_at": _now_iso()}).eq("org_id", org_id).eq("id", r["id"]).execute()
            _sb().table("approval_events").insert({
                "org_id": org_id, "request_id": r["id"], "actor": actor or "system",
                "event_type": "cancelled", "detail": {}}).execute()
    except Exception:
        pass


def sync_source_decision(org_id, *, type, source_table, source_id, decision, actor=None, actor_name=None,
                         note=None):
    """Reflect a decision that a module made through its OWN endpoint onto the linked approval_request,
    WITHOUT re-running the type's on_decide handler (the module already applied the effect). Keeps the
    unified inbox in sync when an approver acts on a legacy per-module surface. Best-effort; never raises."""
    try:
        rows = (_sb().table("approval_requests").select("id,status")
                .eq("org_id", org_id).eq("type", type).eq("source_table", source_table)
                .eq("source_id", str(source_id)).eq("status", "pending").execute().data) or []
        if not rows:
            return
        new_status = "approved" if (decision or "").lower() == "approve" else "denied"
        for r in rows:
            _sb().table("approval_requests").update(
                {"status": new_status, "decision": decision, "decided_by": actor,
                 "decided_by_name": actor_name or actor, "decided_at": _now_iso(),
                 "decision_note": note, "updated_at": _now_iso()}
            ).eq("org_id", org_id).eq("id", r["id"]).execute()
            _sb().table("approval_events").insert({
                "org_id": org_id, "request_id": r["id"], "actor": actor_name or actor or "system",
                "event_type": new_status, "detail": {"via": "module_endpoint", "note": note}}).execute()
    except Exception:
        pass


def list_inbox(org_id, *, authorization="", status="", type="", store_code="", limit=500):
    """The caller's approval inbox, narrowed to their store span (the same scope_keyset every storeops
    read applies). Newest first."""
    q = _sb().table("approval_requests").select("*").eq("org_id", org_id)
    if status:
        q = q.eq("status", status)
    if type:
        q = q.eq("type", type)
    if store_code:
        q = q.eq("store_code", store_code)
    rows = q.order("created_at", desc=True).limit(limit).execute().data or []
    ks = _scope_keyset(authorization, org_id)
    if ks is not None:
        rows = [r for r in rows if _in_span(ks, r.get("store_code"))]
    return rows


def summary(org_id, *, authorization=""):
    """Pending counts overall + by type, scoped to the caller — for the nav badge and dashboards."""
    rows = list_inbox(org_id, authorization=authorization, status="pending")
    by_type: dict = {}
    for r in rows:
        by_type[r.get("type")] = by_type.get(r.get("type"), 0) + 1
    return {"pending": len(rows), "by_type": by_type}


# ── scope helpers (reuse storeops' canonical machinery; fail closed) ──────────────────────────────
def _scope_keyset(authorization, org_id):
    try:
        from app.modules.storeops.router import scope_keyset
        return scope_keyset(authorization, org_id)
    except Exception:
        # Fail CLOSED: an empty keyset shows nothing rather than the whole org on a scope hiccup.
        return set()


def _in_span(keyset, store_code):
    if store_code is None:
        return False   # org-level-less rows are not shown to a store-scoped caller (writer-decides guard)
    try:
        from app.modules.storeops.router import in_keyset
        return in_keyset(keyset, store_code)
    except Exception:
        return False
