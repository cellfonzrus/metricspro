"""Store Visit API Router — /api/v1/storevisit/*  (DM store-visit + inspection checklist).

Photos go to the Supabase Storage bucket `store-visits` (created on first upload); the DB stores
only the storage PATH, served to the UI as a short-lived signed URL. Tables live in storeops.*
(migration 027). A DM = the Market Manager role (scope: market) and acts on stores in their market.
"""
from typing import Any

from fastapi import APIRouter, HTTPException, UploadFile, File, Form, Header, BackgroundTasks
from app.core.database import get_supabase
# The ONE dialect module for a vendor order API. WHICH dialect a vendor speaks is a config value
# read through supply/order_transport, never a name decided here (RULE TWO).
from app.modules.supply import shopify_draft_order as _shopify
from app.core.schemas import LaxModel
from datetime import datetime, timezone
import uuid

router = APIRouter(prefix="/storevisit", tags=["Store Visits"])


# ── Request bodies (Item 15 Pydantic rollout — lax so legacy callers never break) ──────────────
class PutStorevisitConfigIn(LaxModel):
    accessory_order_url: Any = None
    accessory_order_label: Any = None


class ChecklistItemIn(LaxModel):
    item_key: Any = None
    label: Any = None
    category: Any = None
    input_type: Any = None
    sort_order: Any = None
    is_active: Any = True


class UpdateChecklistItemIn(LaxModel):
    label: Any = None
    category: Any = None
    input_type: Any = None
    sort_order: Any = None
    is_active: Any = None


class CreateVisitIn(LaxModel):
    store_code: Any = None
    store_address: Any = None
    market: Any = None
    dm_email: Any = None
    dm_name: Any = None
    check_in_at: Any = None
    check_in_lat: Any = None
    check_in_lng: Any = None
    check_in_accuracy: Any = None
    scheduled_rep: Any = None
    actual_rep: Any = None
    rep_discrepancy_reason: Any = None


class UpdateVisitIn(LaxModel):
    store_code: Any = None
    store_address: Any = None
    market: Any = None
    dm_email: Any = None
    dm_name: Any = None
    check_out_at: Any = None
    scheduled_rep: Any = None
    actual_rep: Any = None
    rep_discrepancy_reason: Any = None
    extra_notes: Any = None
    check_in_lat: Any = None
    check_in_lng: Any = None
    check_in_accuracy: Any = None
    responses: Any = None
    accessories: Any = None


class SaveActionItemsIn(LaxModel):
    items: Any = None


class SaveActionPlanIn(LaxModel):
    plan: Any = None


class SignoffIn(LaxModel):
    who: Any = None
    name: Any = None
    signed: Any = True

ORG_ID = "00000000-0000-0000-0000-000000000001"
BUCKET = "store-visits"
# Default accessory-reorder link (mig 503, 2026-07-16 luxelink-parity audit): this was a bare constant
# used for EVERY tenant, including non-Boost carriers that have no relationship with vAccessorize — a
# hard-coded-vendor violation. Kept as the DEFAULT (so an un-configured tenant, i.e. the house org today,
# is byte-for-byte unchanged); a tenant overrides it via GET/PUT /storevisit/config ->
# storeops.store_visit_config (accessory_order_url/accessory_order_label).
VACCESSORIZE_URL = "https://www.vaccessorize.com"
_DEFAULT_ACCESSORY_ORDER_LABEL = "Order on vAccessorize.com"


def sb():
    # store-visit + checklist tables live in the storeops.* schema (migration 027)
    return get_supabase().schema("storeops")


def _accessory_order_config(client, org_id: str) -> dict:
    """{url, label} for the store-visit 'order accessories' link — per-tenant override (mig 503) with
    the historical vAccessorize default so an un-configured tenant (the house org today) is unchanged.
    Missing table (migration not run) degrades to the same default, never a 500."""
    url, label = VACCESSORIZE_URL, _DEFAULT_ACCESSORY_ORDER_LABEL
    try:
        rows = (client.table("store_visit_config").select("*")
                .eq("org_id", org_id).limit(1).execute().data) or []
        if rows:
            url = (rows[0].get("accessory_order_url") or "").strip() or url
            label = (rows[0].get("accessory_order_label") or "").strip() or label
    except Exception:
        pass
    return {"url": url, "label": label}


@router.get("/config")
def get_storevisit_config(org_id: str = ORG_ID):
    """Tenant-editable store-visit settings — currently just the accessory-reorder link (mig 503)."""
    cfg = _accessory_order_config(sb(), org_id)
    return {"accessory_order_url": cfg["url"], "accessory_order_label": cfg["label"],
            "is_default": cfg["url"] == VACCESSORIZE_URL and cfg["label"] == _DEFAULT_ACCESSORY_ORDER_LABEL}


@router.put("/config")
def put_storevisit_config(body: PutStorevisitConfigIn, org_id: str = ORG_ID, authorization: str = Header(default="")):
    """Set (or clear, via blank strings -> back to the default) the tenant's accessory-reorder link."""
    if not _can_edit_visit_setting(_caller_perms(authorization)):
        raise HTTPException(403, "Editing store-visit settings is permission-restricted.")
    url = (body.accessory_order_url or "").strip()
    label = (body.accessory_order_label or "").strip()
    row = {"org_id": org_id, "accessory_order_url": url or None, "accessory_order_label": label or None,
           "updated_at": _now()}
    try:
        sb().table("store_visit_config").upsert(row, on_conflict="org_id").execute()
    except Exception:
        raise HTTPException(400, "run migration 503 first (storeops.store_visit_config)")
    return get_storevisit_config(org_id)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


# ── Per-setting edit permission (2026-07-26 settings audit) ──────────────────────────────────
# The checklist TEMPLATE (checklist_items) and the accessory-order link (store_visit_config) are
# admin-configured settings, not the operational DM visit flow — /storeops/visits/settings (the
# template editor) is already nav-restricted to company-wide scope, and /closing/store-visit-config
# likewise, but neither had a matching SERVER-side check: a market-scoped caller who knew the
# endpoint could still write either. Mirrors closing/router.py's _caller_perms/_can_edit_closing_
# setting (kept local/self-contained here rather than a cross-module import, matching this module's
# existing zero-cross-import style). Uses the SAME 'closing' settings-area key core's SETTING_AREAS
# registry already exposes as "Daily Closing / Tender Fields" — store-visit config is part of that
# same retail-ops domain and there is no separate key for it.
def _caller_perms(authorization: str) -> dict:
    """`__resolved` marks "we found a real, logged-in caller" (mirrors closing/router.py's fix of the
    same name, 2026-07-26 settings audit) — every early-return/exception yields `{}` (no `__resolved`
    key), which `_can_edit_visit_setting` below treats as an explicit DENY rather than defaulting to
    company-wide-allowed."""
    try:
        from app.modules.core.router import _uid_from_token
        uid = _uid_from_token(authorization)
        if not uid:
            return {}
        c = sb()   # already schema("storeops")
        from app.core.tenant_middleware import caller_app_user
        u = caller_app_user(uid, "org_id,role,super_admin")
        if not u:
            return {}
        perms = {}
        if u.get("role"):
            rr = (c.table("roles").select("permissions")
                  .eq("org_id", u.get("org_id") or ORG_ID).eq("name", u["role"]).limit(1).execute().data) or []
            if rr:
                perms = dict(rr[0].get("permissions") or {})
        perms["__super_admin"] = bool(u.get("super_admin"))
        perms["__resolved"] = True
        return perms
    except Exception:
        return {}


def _can_edit_visit_setting(perms: dict) -> bool:
    """An explicit settings.closing grant/deny on the caller's role wins; else company-wide scope or
    super-admin only. A blank/failed perms lookup (no auth header, invalid token, no matching user —
    `__resolved` absent) is DENIED, never silently allowed."""
    if perms.get("__super_admin"):
        return True
    if not perms.get("__resolved"):
        return False
    s = perms.get("settings") or {}
    if "closing" in s:
        return bool(s["closing"])
    return (perms.get("scope") or "all") == "all"


# ── Storage helpers ──────────────────────────────────────────────────────────────────────
def _ensure_bucket():
    """Create the private `store-visits` bucket if it doesn't exist yet (idempotent)."""
    client = get_supabase()
    try:
        client.storage.get_bucket(BUCKET)
    except Exception:
        try:
            client.storage.create_bucket(BUCKET)   # private by default
        except Exception:
            pass   # already exists or created concurrently — uploads will still work
    return client


def _signed_url(path: str | None):
    if not path:
        return None
    try:
        res = get_supabase().storage.from_(BUCKET).create_signed_url(path, 3600)
        if isinstance(res, dict):
            return res.get("signedURL") or res.get("signedUrl") or res.get("signed_url")
        return res
    except Exception:
        return None


# ── Checklist template (management-configurable) ─────────────────────────────────────────
@router.get("/checklist-items")
def list_checklist_items(include_inactive: bool = False, org_id: str = ORG_ID):
    q = sb().table("checklist_items").select("*").eq("org_id", org_id)
    if not include_inactive:
        q = q.eq("is_active", True)
    return q.order("sort_order").execute().data or []


@router.post("/checklist-items")
def create_checklist_item(item: ChecklistItemIn, org_id: str = ORG_ID, authorization: str = Header(default="")):
    if not _can_edit_visit_setting(_caller_perms(authorization)):
        raise HTTPException(403, "Editing the visit checklist template is permission-restricted.")
    label = (item.label or "").strip()
    if not label:
        raise HTTPException(400, "label required")
    body = {
        "org_id": org_id,
        "item_key": (item.item_key or f"custom_{uuid.uuid4().hex[:8]}"),
        "label": label,
        "category": item.category or "general",
        "input_type": item.input_type or "check",
        "sort_order": int(item.sort_order or 100),
        "is_active": bool(item.is_active),
    }
    r = sb().table("checklist_items").insert(body).execute()
    return r.data[0] if r.data else body


@router.patch("/checklist-items/{item_id}")
def update_checklist_item(item_id: str, updates: UpdateChecklistItemIn, org_id: str = ORG_ID, authorization: str = Header(default="")):
    """2026-07-26 settings audit: `org_id` was entirely MISSING from this endpoint's signature — the
    multi-tenant middleware rewrites the query param, but with nothing here to catch it, the UPDATE
    below matched by `id` alone, org-unscoped (a caller in one tenant could edit ANOTHER tenant's
    checklist item by id). Fixed the same way every other write in this module already scopes."""
    if not _can_edit_visit_setting(_caller_perms(authorization)):
        raise HTTPException(403, "Editing the visit checklist template is permission-restricted.")
    allowed = ("label", "category", "input_type", "sort_order", "is_active")
    body = {k: getattr(updates, k) for k in allowed if k in updates.model_fields_set}
    body["updated_at"] = _now()
    r = sb().table("checklist_items").update(body).eq("id", item_id).eq("org_id", org_id).execute()
    return r.data[0] if r.data else body


@router.delete("/checklist-items/{item_id}")
def delete_checklist_item(item_id: str, org_id: str = ORG_ID, authorization: str = Header(default="")):
    """2026-07-26 settings audit: same missing-org_id gap as update_checklist_item above, fixed the
    same way."""
    if not _can_edit_visit_setting(_caller_perms(authorization)):
        raise HTTPException(403, "Editing the visit checklist template is permission-restricted.")
    # Soft-delete (deactivate) so historical visits keep their item snapshots.
    sb().table("checklist_items").update({"is_active": False, "updated_at": _now()}).eq("id", item_id).eq("org_id", org_id).execute()
    return {"deactivated": item_id}


# ── Stores in a market + scheduled rep ───────────────────────────────────────────────────
@router.get("/stores")
def stores_in_market(market: str = None, org_id: str = ORG_ID):
    rows = sb().table("stores").select("store_code,address,market").eq("org_id", org_id).order("address").execute().data or []
    # Blank storeops market inherits from THE canonical union map, and the filter compares
    # case-insensitively (core.scope.market_by_code; 2026-09-03 "1115 Liberty Ave"/LI class fix —
    # was storeops-only + exact-case). Set markets never overwritten.
    try:
        from app.core import scope as _cscope
        _mkt = _cscope.market_by_code(sb(), org_id)
        for s in rows:
            if not (s.get("market") or "").strip():
                s["market"] = _mkt.get(str(s.get("store_code") or "").strip().upper(), s.get("market"))
    except Exception as e:
        print(f"WARN storevisit stores_in_market canonical overlay failed: {e}")
    if market:
        rows = [s for s in rows if (s.get("market") or "").strip().lower() == market.strip().lower()]
    return rows


@router.get("/scheduled-rep")
def scheduled_rep(store_code: str, date: str, org_id: str = ORG_ID):
    """Reps scheduled at a store on a given date (from storeops.shifts)."""
    rows = (sb().table("shifts")
            .select("employee_name,start_time,end_time,scheduled_hours")
            .eq("org_id", org_id).eq("is_deleted", False).eq("store_code", store_code).eq("shift_date", date)
            .order("start_time").execute().data or [])
    names = [r.get("employee_name") for r in rows if r.get("employee_name")]
    return {"store_code": store_code, "date": date, "reps": names, "shifts": rows}


# ── Visits ───────────────────────────────────────────────────────────────────────────────
@router.get("/visits")
def list_visits(market: str = None, store_code: str = None, status: str = None,
                date_from: str = None, date_to: str = None, org_id: str = ORG_ID):
    q = sb().table("store_visits").select("*").eq("org_id", org_id)
    if market:      q = q.eq("market", market)
    if store_code:  q = q.eq("store_code", store_code)
    if status:      q = q.eq("status", status)
    if date_from:   q = q.gte("check_in_at", date_from)
    if date_to:     q = q.lte("check_in_at", date_to)
    return q.order("check_in_at", desc=True).limit(500).execute().data or []


@router.post("/visits")
def create_visit(payload: CreateVisitIn, org_id: str = ORG_ID):
    body = {
        "org_id": org_id,
        "store_code": payload.store_code,
        "store_address": payload.store_address,
        "market": payload.market,
        "dm_email": payload.dm_email,
        "dm_name": payload.dm_name,
        "check_in_at": payload.check_in_at or _now(),
        "check_in_lat": payload.check_in_lat,
        "check_in_lng": payload.check_in_lng,
        "check_in_accuracy": payload.check_in_accuracy,
        "scheduled_rep": payload.scheduled_rep,
        "actual_rep": payload.actual_rep,
        "rep_discrepancy_reason": payload.rep_discrepancy_reason,
        "status": "in_progress",
    }
    r = sb().table("store_visits").insert(body).execute()
    return r.data[0] if r.data else body


@router.get("/visits/{visit_id}")
def get_visit(visit_id: str, org_id: str = ORG_ID):
    v = sb().table("store_visits").select("*").eq("org_id", org_id).eq("id", visit_id).limit(1).execute().data
    if not v:
        raise HTTPException(404, "visit not found")
    visit = v[0]
    responses = sb().table("store_visit_responses").select("*").eq("org_id", org_id).eq("visit_id", visit_id).execute().data or []
    accessories = (sb().table("store_visit_accessories").select("*")
                   .eq("org_id", org_id).eq("visit_id", visit_id).order("created_at").execute().data or [])
    for resp in responses:
        resp["photo_url"] = _signed_url(resp.get("photo_path"))
    visit["clean_store_photo_url"] = _signed_url(visit.get("clean_store_photo_path"))
    visit["signed_checklist_url"] = _signed_url(visit.get("signed_checklist_path"))
    acc_cfg = _accessory_order_config(sb(), org_id)
    return {"visit": visit, "responses": responses, "accessories": accessories,
            "vaccessorize_url": acc_cfg["url"], "accessory_order_url": acc_cfg["url"],
            "accessory_order_label": acc_cfg["label"]}


@router.patch("/visits/{visit_id}")
def update_visit(visit_id: str, payload: UpdateVisitIn, org_id: str = ORG_ID):
    header = ("store_code", "store_address", "market", "dm_email", "dm_name", "check_out_at",
              "scheduled_rep", "actual_rep", "rep_discrepancy_reason", "extra_notes",
              "check_in_lat", "check_in_lng", "check_in_accuracy")
    updates = {k: getattr(payload, k) for k in header if k in payload.model_fields_set}
    if updates:
        updates["updated_at"] = _now()
        sb().table("store_visits").update(updates).eq("id", visit_id).eq("org_id", org_id).execute()

    # Checklist answers: full replace for this visit (delete-then-insert).
    if "responses" in payload.model_fields_set:
        sb().table("store_visit_responses").delete().eq("visit_id", visit_id).eq("org_id", org_id).execute()
        rows = [{
            "org_id": org_id, "visit_id": visit_id,
            "item_key": r.get("item_key"),
            "label_snapshot": r.get("label_snapshot") or r.get("label"),
            "category_snapshot": r.get("category_snapshot") or r.get("category"),
            "checked": r.get("checked"),
            "note": r.get("note"),
            "photo_path": r.get("photo_path"),
        } for r in (payload.responses or [])]
        if rows:
            sb().table("store_visit_responses").insert(rows).execute()

    # Accessories-to-order: full replace.
    if "accessories" in payload.model_fields_set:
        sb().table("store_visit_accessories").delete().eq("visit_id", visit_id).eq("org_id", org_id).execute()
        rows = []
        for a in (payload.accessories or []):
            name = (a.get("accessory_name") or "").strip()
            if not name:
                continue
            rows.append({
                "org_id": org_id, "visit_id": visit_id,
                "accessory_name": name,
                "qty": int(a.get("qty") or 1),
                "note": a.get("note"),
            })
        if rows:
            sb().table("store_visit_accessories").insert(rows).execute()

    return get_visit(visit_id, org_id)


@router.post("/visits/{visit_id}/submit")
def submit_visit(visit_id: str, background: BackgroundTasks = None, org_id: str = ORG_ID):
    """Complete the visit — and tell the managers NOW.

    OWNER, 2026-10-03: *"every sore visti as soon as it is uploaded should be emailed as soon as the
    visti is completed"*. So the alert fires on THIS event, not on the next tick of the hourly sweep.

    IT IS THE SAME ONE PATH, not a second send. `_run_store_visit_alerts` scoped to this visit does
    the whole job — the same recipients, the same digests, the same `alert_log` dedup, the same
    draft purchase order — so there is no second renderer and no second fan-out to drift. The hourly
    sweep stays as the SAFETY NET rather than the trigger: it catches a visit whose immediate send
    could not go out (a channel down, a tenant that switched the alert on afterwards), and the dedup
    key being (visit, item) means a visit already announced here is never announced twice.

    IN THE BACKGROUND, and that is deliberate. A visit is submitted from a phone, often on a store's
    wifi, and the rep must get their confirmation whether or not an email provider answers. A send
    that fails leaves no `alert_log` row, so the next sweep retries it — which is exactly what
    "an alert that reached nobody is not already alerted" already means everywhere else here."""
    sb().table("store_visits").update({
        "status": "submitted", "submitted_at": _now(), "updated_at": _now(),
    }).eq("id", visit_id).eq("org_id", org_id).execute()
    out = get_visit(visit_id, org_id)
    if background is not None:
        background.add_task(_alert_on_submit, org_id, visit_id)
    return out


async def _alert_on_submit(org_id, visit_id):
    """The on-submit send, deferred. NEVER raises: a visit is already saved by the time this runs,
    and an alerting failure must not surface as a failed submit or a 500 in the background runner."""
    try:
        res = await _run_store_visit_alerts(org_id_filter=org_id, respect_enabled=True,
                                           dry_run=False, visit_id=visit_id)
        print(f"storevisit on-submit alert for {visit_id}: {res}")
    except Exception as e:
        print(f"WARN storevisit on-submit alert for {visit_id} failed: {e}")


# ── Photo upload (clean-store photo or a per-item photo) ──────────────────────────────────
@router.post("/visits/{visit_id}/photo")
async def upload_photo(visit_id: str, kind: str = Form("clean_store"),
                       file: UploadFile = File(...), org_id: str = ORG_ID):
    contents = await file.read()
    client = _ensure_bucket()
    ext = "jpg"
    if file.filename and "." in file.filename:
        ext = file.filename.rsplit(".", 1)[-1].lower()[:5]
    path = f"{org_id}/{visit_id}/{kind.replace(':', '_')}-{uuid.uuid4().hex}.{ext}"
    ctype = file.content_type or "image/jpeg"
    try:
        client.storage.from_(BUCKET).upload(path, contents, {"content-type": ctype, "upsert": "true"})
    except Exception as e:
        raise HTTPException(400, f"photo upload failed: {e}")

    # clean_store + signed_checklist live on the visit header; per-item / proof photos
    # (kind='item:<key>' / 'proof:<key>') are linked when the frontend saves their rows.
    # 2026-07-26 settings audit: both updates below were missing `.eq("org_id", org_id)` — the ONLY
    # write in this whole endpoint that skipped it (the storage path itself is org_id-namespaced, but
    # the DB pointer update was not org-scoped) — a visit_id from another tenant could have its
    # clean-store/signed-checklist photo pointer overwritten. Fixed to match every other write here.
    if kind == "clean_store":
        sb().table("store_visits").update(
            {"clean_store_photo_path": path, "updated_at": _now()}).eq("id", visit_id).eq("org_id", org_id).execute()
    elif kind == "signed_checklist":
        sb().table("store_visits").update(
            {"signed_checklist_path": path, "updated_at": _now()}).eq("id", visit_id).eq("org_id", org_id).execute()
    return {"path": path, "url": _signed_url(path), "kind": kind}


# ── Phase 2: action-item rollup overlay + rep action plan + sign-off ──────────────────────
@router.get("/visits/{visit_id}/action")
def get_visit_action(visit_id: str, org_id: str = ORG_ID):
    """The DM's saved overlay (which rolled-up action items were discussed + comments + proof),
    the agreed rep action plan, and the sign-off state. The live rolled-up action items come from
    the commcalc action-plan engine — the frontend fetches those and merges this overlay onto them."""
    v = sb().table("store_visits").select("*").eq("org_id", org_id).eq("id", visit_id).limit(1).execute().data
    if not v:
        raise HTTPException(404, "visit not found")
    visit = v[0]
    items = sb().table("visit_action_items").select("*").eq("org_id", org_id).eq("visit_id", visit_id).execute().data or []
    for it in items:
        it["proof_photo_url"] = _signed_url(it.get("proof_photo_path"))
    plan = (sb().table("visit_action_plan").select("*")
            .eq("org_id", org_id).eq("visit_id", visit_id).order("created_at").execute().data or [])
    signoff = {k: visit.get(k) for k in (
        "plan_rep_signed", "plan_rep_signed_by", "plan_rep_signed_at",
        "plan_dm_signed", "plan_dm_signed_by", "plan_dm_signed_at")}
    return {"items": items, "plan": plan, "signoff": signoff,
            "signed_checklist_url": _signed_url(visit.get("signed_checklist_path"))}


@router.put("/visits/{visit_id}/action-items")
def save_action_items(visit_id: str, payload: SaveActionItemsIn, org_id: str = ORG_ID):
    """Full replace of the DM's discussion overlay for this visit (delete-then-insert)."""
    sb().table("visit_action_items").delete().eq("visit_id", visit_id).eq("org_id", org_id).execute()
    rows = []
    for it in (payload.items or []):
        key = (it.get("item_key") or "").strip()
        if not key:
            continue
        rows.append({
            "org_id": org_id, "visit_id": visit_id, "item_key": key,
            "rep": it.get("rep"), "severity": it.get("severity"), "metric": it.get("metric"),
            "title": it.get("title"), "detail": it.get("detail"),
            "discussed": bool(it.get("discussed")), "comment": it.get("comment"),
            "proof_photo_path": it.get("proof_photo_path"),
        })
    if rows:
        sb().table("visit_action_items").insert(rows).execute()
    return get_visit_action(visit_id, org_id)


@router.put("/visits/{visit_id}/action-plan")
def save_action_plan(visit_id: str, payload: SaveActionPlanIn, org_id: str = ORG_ID):
    """Full replace of the agreed rep action plan for this visit (delete-then-insert)."""
    sb().table("visit_action_plan").delete().eq("visit_id", visit_id).eq("org_id", org_id).execute()
    rows = []
    for p in (payload.plan or []):
        desc = (p.get("description") or "").strip()
        if not desc:
            continue
        rows.append({
            "org_id": org_id, "visit_id": visit_id,
            "store_code": p.get("store_code"), "rep": p.get("rep"),
            "description": desc, "due_date": p.get("due_date") or None,
            "status": p.get("status") or "open",
        })
    if rows:
        sb().table("visit_action_plan").insert(rows).execute()
    return get_visit_action(visit_id, org_id)


@router.post("/visits/{visit_id}/signoff")
def signoff(visit_id: str, payload: SignoffIn, org_id: str = ORG_ID):
    """Record a rep or DM sign-off on the agreed action plan."""
    who = (payload.who or "").lower()
    name = payload.name or ""
    if who not in ("rep", "dm"):
        raise HTTPException(400, "who must be 'rep' or 'dm'")
    pre = "plan_rep" if who == "rep" else "plan_dm"
    signed = payload.signed
    upd = {f"{pre}_signed": bool(signed), "updated_at": _now()}
    upd[f"{pre}_signed_by"] = name if signed else None
    upd[f"{pre}_signed_at"] = _now() if signed else None
    sb().table("store_visits").update(upd).eq("id", visit_id).eq("org_id", org_id).execute()
    return get_visit_action(visit_id, org_id)


@router.get("/health")
def health(org_id: str = ORG_ID):
    return {"status": "ok", "module": "storevisit", "vaccessorize_url": _accessory_order_config(sb(), org_id)["url"]}


# ── Universal admin-attention contributions (2026-07-26 settings audit) ─────────────────────────────
# See storevisit/attention_providers.py. Import-time side effect only, guarded so a deployment
# missing core/import_health.py (mig 717 un-run, or just an older core) never breaks this module.
try:
    from . import attention_providers  # noqa: F401
except Exception as _e:
    print("storevisit.attention_providers registration skipped:", _e)


# ════════════════════════════════════════════════════════════════════════════════════════════════
# STORE VISIT FOLLOW-THROUGH ALERTS (owner ask 2026-10-03) — the to-do digest, the separate
# accessory notification, and the DRAFT purchase order.
#
# The decisions are all in `visit_alerts` (pure, DB-free, proven by harness_storevisit_alerts.py).
# Everything here is I/O: read the visit's own rows, resolve recipients through the ONE fan-out
# (`commcalc/manager_digest`), send on the channels the tenant configured, and record the dedup row
# in `storeops.alert_log` — the same four steps the ePay, zero-sales and follow-up sweeps take.
# NO second fan-out, no second dedup table, no second PO path.
# ════════════════════════════════════════════════════════════════════════════════════════════════
class PutVisitAlertConfigIn(LaxModel):
    store_visit_alert_enabled: Any = None
    store_visit_alert_channels: Any = None
    store_visit_alert_lookback_days: Any = None
    store_visit_alert_min_items: Any = None
    store_visit_accessory_alert_enabled: Any = None
    store_visit_accessory_channels: Any = None
    store_visit_accessory_vendor_id: Any = None
    store_visit_accessory_po_mode: Any = None


def _tenant_row(org_id):
    """The tenant's row, or {} — its own defensive read (the mig-313 posture), so a database that has
    not run the alert migration resolves to the house defaults (which are OFF) instead of 500ing."""
    try:
        rows = (sb().table("tenants").select("*").eq("org_id", org_id).limit(1).execute().data) or []
        return rows[0] if rows else {}
    except Exception:
        return {}


def _recipient_rows(org_id):
    """THE tenant's notification list — `storeops.alert_recipient` (mig 089), the same table and the
    same editor (the Cash & Closing Alerts page) every other alert scope uses. Never a second list."""
    try:
        return (sb().table("alert_recipient").select("*").eq("org_id", org_id)
                .execute().data) or []
    except Exception:
        return []


@router.get("/alerts/config")
def get_visit_alert_config(authorization: str = Header(default=""), org_id: str = ORG_ID):
    """The tenant's store-visit alert settings, the resolved effective config (house defaults where
    nothing is set), and the notification list as it stands — including whether the DM-and-above
    default is in force, which is what a tenant gets when it has configured no list at all."""
    from app.modules.storevisit import visit_alerts as _va
    from app.modules.commcalc import manager_digest as _md
    cfg = _va.resolve_config(_tenant_row(org_id))
    rows = _recipient_rows(org_id)

    def _list(scope):
        mine = [r for r in rows
                if str(r.get("scope") or "").strip().lower() in (scope, "all")]
        return {"scope": scope, "rows": mine,
                "dm_and_above_default": _md.use_hierarchy(mine),
                "named": _md.named_extras(mine, scope, cfg["channels"])}

    return {"config": {k: (list(v) if isinstance(v, tuple) else v) for k, v in cfg.items()},
            "kinds": [{"key": k, "label": lbl} for (k, lbl) in _va.KINDS],
            "todo": _list(_va.ALERT_SCOPE), "accessories": _list(_va.ACCESSORY_SCOPE),
            "po_modes": list(_va.PO_MODES)}


@router.put("/alerts/config")
def put_visit_alert_config(body: PutVisitAlertConfigIn, authorization: str = Header(default=""),
                           org_id: str = ORG_ID):
    """Set the tenant's store-visit alert settings. Same permission as every other store-visit
    setting. The recipient LIST itself is edited where it already lives (the alert-recipient
    endpoints) — this never becomes a second editor for it."""
    if not _can_edit_visit_setting(_caller_perms(authorization)):
        raise HTTPException(403, "Editing store-visit alert settings is permission-restricted.")
    row = {k: getattr(body, k) for k in body.model_fields_set}
    if not row:
        return get_visit_alert_config(authorization, org_id)
    for col in ("store_visit_alert_channels", "store_visit_accessory_channels"):
        if col in row:
            from app.modules.commcalc import manager_digest as _md
            row[col] = list(_md.normalize_channels(row[col]))
    row["org_id"] = org_id
    try:
        sb().table("tenants").upsert(row, on_conflict="org_id").execute()
    except Exception as e:
        raise HTTPException(400, f"run migration 1047 first (storeops.tenants store-visit alerts): {e}")
    return get_visit_alert_config(authorization, org_id)


def _visit_window(org_id, cfg, visit_id=None):
    """The submitted visits a sweep is answering for, with their child rows read in ONE batch each.
    Returns (visits, responses, action_items, plan, accessories) — every list already org-scoped."""
    from datetime import timedelta
    client = sb()
    if visit_id:
        visits = (client.table("store_visits").select("*")
                  .eq("org_id", org_id).eq("id", visit_id).limit(1).execute().data) or []
    else:
        since = (datetime.now(timezone.utc)
                 - timedelta(days=int(cfg["lookback_days"]))).date().isoformat()
        visits = (client.table("store_visits").select("*")
                  .eq("org_id", org_id).eq("status", "submitted")
                  .gte("submitted_at", since).order("submitted_at", desc=True)
                  .limit(500).execute().data) or []
    ids = [v.get("id") for v in visits if v.get("id")]
    if not ids:
        return visits, [], [], [], []

    def _kids(table):
        out = []
        for i in range(0, len(ids), 100):
            try:
                out.extend(client.table(table).select("*").eq("org_id", org_id)
                           .in_("visit_id", ids[i:i + 100]).execute().data or [])
            except Exception as e:
                print(f"WARN storevisit alerts could not read {table}: {e}")
        return out

    return (visits, _kids("store_visit_responses"), _kids("visit_action_items"),
            _kids("visit_action_plan"), _kids("store_visit_accessories"))


def _accessory_unit_costs(org_id, vendor_id, lines):
    """{merge key: unit cost} from whatever price the tenant actually has on file for this vendor.

    The catalog snapshot is NOT read here: `supply/store.latest_rows` is its one home and already
    knows to take only the newest run per vendor rather than mixing yesterday's prices into today's
    order. Defensive because that table arrives with migration 1021, which not every database has
    run — a missing table yields no prices, and `visit_alerts.po_draft` then NAMES every unpriced
    line instead of quietly pricing it at zero and calling the total complete."""
    if not vendor_id or not lines:
        return {}
    try:
        from app.modules.supply import store as _supply_store
        # The ROOT client: `supply/store.t()` applies the schema itself, so handing it an
        # already-schema'd client would ask for commcalc.commcalc and read nothing.
        rows, _seen = _supply_store.latest_rows(get_supabase(), org_id, [vendor_id])
    except Exception as e:
        print(f"WARN store-visit accessory prices unavailable: {e}")
        return {}
    by_name = {}
    for r in (rows or []):
        key = " ".join(str(r.get("name") or "").lower().split())
        val = r.get("price")
        if key and val is not None and key not in by_name:
            by_name[key] = val
    return {ln["key"]: by_name[ln["key"]] for ln in lines if ln.get("key") in by_name}


def _accessory_po_for_visits(org_id, cfg, visits, accessory_rows, who=None, dry_run=True):
    """One DRAFT purchase order per visit that asked for accessories, raised against the tenant's
    configured vendor through the ONE PO path (`supply/store.create_order`, mig-301 tables).

    IDEMPOTENT by `purchase_order.store_visit_id`: a visit gets one draft, however many times the
    sweep runs. NOTHING IS ORDERED — `status` stays 'draft'. Raising the draft is all this does;
    pushing it to the vendor is `_push_accessory_pos`, a separate step under `po_mode`
    'draft_push', which creates a DRAFT on the vendor's side and still places nothing. A
    tenant with `po_mode` 'off' (the default, and what an unconfigured vendor resolves to) gets
    none at all, and the reason rides in the result."""
    from app.modules.storevisit import visit_alerts as _va
    if cfg["po_mode"] not in _va.PO_DRAFT_MODES:
        return {"created": [], "skipped": "po_mode is off",
                "reason": cfg.get("po_mode_reason")}
    vmap = {v.get("id"): v for v in visits}
    by_visit = {}
    for r in (accessory_rows or []):
        by_visit.setdefault(r.get("visit_id"), []).append(r)
    if not by_visit:
        return {"created": [], "skipped": "no accessories were asked for"}
    root = get_supabase()
    vendor_id = cfg["accessory_vendor_id"]
    vendor_name = None
    try:
        # The vendor roster has ONE reader (`supply/store.vendor_by_id`); this does not become a
        # second one. The root client, because that module applies the schema itself.
        from app.modules.supply import store as _supply_store
        vendor_name = (_supply_store.vendor_by_id(root, org_id, vendor_id) or {}).get("name")
    except Exception as e:
        print(f"WARN store-visit accessory vendor unreadable: {e}")
    if not vendor_name:
        return {"created": [], "skipped": "the configured accessory vendor no longer exists"}
    have = set()
    try:
        rows = (root.schema("commcalc").table("purchase_order").select("store_visit_id")
                .eq("org_id", org_id).eq("source", _va.PO_SOURCE)
                .in_("store_visit_id", [v for v in by_visit if v]).execute().data) or []
        have = {r.get("store_visit_id") for r in rows}
    except Exception as e:
        # Unreadable is not "none exist": raising a second draft for every visit because the
        # idempotency read failed would be worse than raising none. Refuse the batch and say why.
        return {"created": [], "skipped": f"could not check for existing drafts: {str(e)[:120]}"}
    created, planned = [], []
    for vid, rows in sorted(by_visit.items(), key=lambda kv: str(kv[0])):
        if not vid or vid in have:
            continue
        v = vmap.get(vid) or {}
        lines = _va.accessory_lines(rows, vmap)
        if not lines:
            continue
        draft = _va.po_draft(lines, vendor_id, vendor_name,
                             unit_costs=_accessory_unit_costs(org_id, vendor_id, lines),
                             ship_to_store=v.get("store_code"), market=v.get("market"))
        planned.append({"visit_id": vid, "store_code": v.get("store_code"),
                        "lines": len(draft["lines"]), "total": draft["total"],
                        "total_is_floor": draft["total_is_floor"], "unpriced": draft["unpriced"]})
        if dry_run:
            continue
        try:
            from app.modules.supply import store as _supply_store
            res = _supply_store.create_order(
                root, org_id, draft, None, who=who,
                source=_va.PO_SOURCE,
                extra={"store_visit_id": vid, "ship_to_store": v.get("store_code"),
                       "market": v.get("market"),
                       "notes": "Raised from store visit {0} on {1}.".format(
                           vid, (v.get("submitted_at") or "")[:10])})
            res.update({"visit_id": vid, "vendor_name": vendor_name,
                        "total_is_floor": draft["total_is_floor"],
                        "unpriced": draft["unpriced"]})
            created.append(res)
        except Exception as e:
            print(f"WARN store-visit accessory PO for visit {vid} failed: {e}")
            planned[-1]["error"] = str(e)[:200]
    return {"created": created, "planned": planned, "vendor_name": vendor_name,
            "status": "draft", "sent_to_vendor": False}



async def _push_accessory_pos(org_id, cfg, created):
    """Push each freshly raised draft PO to the vendor over the route THAT VENDOR declares.

    The route is read once from `supply/order_transport.transport_for` — this function knows no
    vendor, store or dialect by name (RULE TWO), and decides nothing about whether sending is
    allowed: `order_transport.push_enabled` rules on that, and refuses any route that would PLACE
    an order rather than draft one.

    IDEMPOTENT by `purchase_order.external_ref` (mig 1053): a PO that already carries one is never
    pushed again, because the vendor API has no idempotency key of its own. A push that fails is
    RECORDED on the PO in `external_error`, not merely logged — a draft that never reached the
    vendor must be visible, not silently absent (§19.41).
    """
    from app.modules.supply import order_transport as _ot
    from app.modules.supply import store as _supply_store
    out = {"pushed": [], "skipped": None, "failed": []}
    if cfg.get("po_mode") != "draft_push" or not created:
        out["skipped"] = "po_mode does not ask for a push"
        return out
    root = get_supabase()
    vendor = None
    try:
        vendor = _supply_store.vendor_by_id(root, org_id, cfg["accessory_vendor_id"])
    except Exception as e:
        out["skipped"] = f"the vendor row is unreadable: {str(e)[:120]}"
        return out
    route = _ot.transport_for(vendor)
    ok, why = _ot.push_enabled(route, True)
    if not ok:
        out["skipped"] = why
        return out
    if route["kind"] != "api":
        # A portal route is a scripted browser walk and is driven by a person, not by this sweep.
        out["skipped"] = f"the {route['kind']} route is not one a sweep drives"
        return out

    # The token is ASKED FOR, never read from a column. Since 2026-01-01 a vendor API credential is
    # a client id and secret exchanged for a ~24h token, so a caller that read a stored string
    # would be correct only until that token expired. `api_credential` is the one home for the
    # question and mints on demand (index §51.2).
    from app.modules.supply import api_credential as _cred
    try:
        login = _supply_store.login_row_full(root, org_id, (vendor or {}).get("data_source_id"))
    except Exception as e:
        out["skipped"] = f"the vendor credential is unreadable: {str(e)[:120]}"
        return out
    got = await _cred.access_token(root, org_id, login, (route.get("config") or {}).get("host"))
    token = got["token"]
    if not token:
        out["skipped"] = got["error"] or "no usable credential for this vendor"
        return out
    if got["error"]:                      # minted but not cached — usable now, say so
        out["credential_note"] = got["error"]

    dialect = str((route.get("config") or {}).get("dialect") or "")
    if dialect != _shopify.DIALECT:
        out["skipped"] = f"no module speaks the dialect {dialect!r}"
        return out

    # WHO the order is for is read from the same one home the customer export reads
    # (`vendor_customer`, §51.5), keyed on the store the draft already ships to. A store the rule
    # cannot name gets a draft with no customer rather than a guessed one — the merchant assigns it.
    from app.modules.supply import vendor_customer as _vc
    cust_decl = route.get("customer") or {}
    stores_by_code = {}
    if cust_decl:
        try:
            stores_by_code = {str(st.get("store_code") or "").strip(): st
                              for st in _supply_store.load_store_roster(root, org_id)}
        except Exception as e:
            out["customer_note"] = f"the store roster is unreadable: {str(e)[:120]}"

    for rec in created:
        po_id = (rec or {}).get("id") or (rec or {}).get("po_id")
        if not po_id:
            continue
        try:
            row = (root.schema("commcalc").table("purchase_order")
                   .select("id,po_number,notes,external_ref,ship_to_store").eq("org_id", org_id)
                   .eq("id", po_id).limit(1).execute().data or [None])[0]
        except Exception as e:
            out["failed"].append({"po_id": po_id, "error": f"unreadable: {str(e)[:120]}"})
            continue
        if not row or str(row.get("external_ref") or "").strip():
            continue                     # already pushed; the vendor has it
        # The lines come from the PO we just wrote, not from the draft that produced it: what the
        # vendor is shown must be what our books say, or the two drift the moment either is edited.
        try:
            lines = (root.schema("commcalc").table("purchase_order_line")
                     .select("device_model,qty_ordered,unit_cost").eq("org_id", org_id)
                     .eq("po_id", po_id).order("line_no").execute().data) or []
        except Exception as e:
            out["failed"].append({"po_id": po_id, "error": f"lines unreadable: {str(e)[:120]}"})
            continue
        res = await _shopify.push_draft_order(
            route["config"], token, row,
            [{"name": ln.get("device_model"), "qty": ln.get("qty_ordered"),
              # A zero unit cost on an accessory line means "nobody could price it" — the draft PO
              # records it as a floor (§47.16). Passing None keeps the dialect's "(price to
              # confirm)" wording instead of asserting the item is free.
              "unit_cost": (ln.get("unit_cost") if float(ln.get("unit_cost") or 0) > 0 else None)}
             for ln in lines],
            customer=_vc.customer_for_store(
                cust_decl, stores_by_code.get(str(row.get("ship_to_store") or "").strip())))
        patch = ({"external_ref": res["external_ref"], "external_url": res["external_url"],
                  "external_pushed_at": _now(), "external_error": None}
                 if res["ok"] else {"external_error": res["error"][:500]})
        try:
            (root.schema("commcalc").table("purchase_order").update(patch)
             .eq("org_id", org_id).eq("id", po_id).execute())
        except Exception as e:
            out["failed"].append({"po_id": po_id, "error": f"unrecordable: {str(e)[:120]}"})
            continue
        (out["pushed"] if res["ok"] else out["failed"]).append(
            {"po_id": po_id, "po_number": row.get("po_number"),
             "external_ref": res["external_ref"], "external_url": res["external_url"],
             "error": res["error"] or None})
    return out


async def _run_store_visit_alerts(org_id_filter=None, respect_enabled=True, dry_run=True,
                                  visit_id=None, who=None):
    """The store-visit follow-through sweep: the to-do digest to the DM and everyone above (plus the
    tenant's own notification list), the separate accessory notification, and the draft purchase
    order. Recipients, dedup and channels all come from `manager_digest`. NEVER raises."""
    from app.modules.storevisit import visit_alerts as _va
    from app.modules.commcalc import manager_digest as _md
    from app.modules.notify import digest_delivery as _delivery
    from app.modules.storeops.router import _managers_above_dm
    from app.modules.notify.channels import email_resend, whatsapp_meta
    from app.core.base_url import base_url
    root = get_supabase()
    so = root.schema("storeops")
    try:
        tenants = so.table("tenants").select("*").execute().data or []
    except Exception:
        tenants = []
    if org_id_filter:
        tenants = [t for t in tenants if str(t.get("org_id")) == str(org_id_filter)]
    channels_ok = {"email": email_resend.is_configured(),
                   "whatsapp": whatsapp_meta.is_configured()}
    try:
        link = (base_url() or "").rstrip("/") + "/storeops/visits"
    except Exception:
        link = None
    results = []
    for t in tenants:
        oid = t.get("org_id")
        if not oid:
            continue
        cfg = _va.resolve_config(t)
        if respect_enabled and not (cfg["enabled"] or cfg["accessory_enabled"]):
            continue
        try:
            visits, responses, action_items, plan_rows, acc_rows = _visit_window(oid, cfg, visit_id)
        except Exception as e:
            results.append({"org_id": oid, "error": str(e)[:200]})
            continue
        by_visit_resp, by_visit_item, by_visit_plan = {}, {}, {}
        for src, dest in ((responses, by_visit_resp), (action_items, by_visit_item),
                          (plan_rows, by_visit_plan)):
            for r in src:
                dest.setdefault(r.get("visit_id"), []).append(r)
        todos = []
        for v in visits:
            its = _va.todo_items(v, by_visit_resp.get(v.get("id")),
                                 by_visit_item.get(v.get("id")), by_visit_plan.get(v.get("id")))
            if len(its) >= cfg["min_items"]:
                todos.extend(its)
        summary = _va.summarize(todos)
        # Work recorded against no store cannot be followed up with any manager. It is COUNTED and
        # named in the footer, never dropped — the §15z rule applied to visit ownership.
        no_store = [it for it in todos if not it.get("store_code")]
        todos = [it for it in todos if it.get("store_code")]
        summary["totals"]["no_store"] = len(no_store)
        rows = _recipient_rows(oid)
        labels = _va.kind_labels()
        res = {"org_id": oid, "visits": len(visits), "todo_open": summary["totals"]["open"],
               "no_store": len(no_store), "totals": summary["totals"],
               "channels": list(cfg["channels"]), "enabled": cfg["enabled"],
               "accessory_enabled": cfg["accessory_enabled"], "po_mode": cfg["po_mode"],
               "email_configured": channels_ok["email"],
               "whatsapp_configured": channels_ok["whatsapp"],
               "dry_run": dry_run}
        if cfg.get("po_mode_reason"):
            res["po_mode_reason"] = cfg["po_mode_reason"]

        # ── 1. the to-do digest ──────────────────────────────────────────────────────────────
        if (cfg["enabled"] or not respect_enabled) and todos:
            scope_rows = [r for r in rows if str(r.get("scope") or "").strip().lower()
                          in (_va.ALERT_SCOPE, "all")]

            def _build(name, its, _s=summary, _l=labels):
                return _va.build_digest(name, its, totals=_s["totals"], labels=_l, link=link)

            stores = {i["store_code"] for i in todos if i.get("store_code")}
            fan = _md.plan_digests(
                todos, {s: _managers_above_dm(oid, s) for s in stores},
                # NOT the date: a store visit is an EVENT, so its to-do list is announced once and
                # re-announced only for work that is new. The visit id is in the key tail.
                "", scope=_va.ALERT_SCOPE, build=_build, key_parts=_va.key_parts,
                channels=cfg["channels"],
                extra_recipients=_md.named_extras(scope_rows, _va.ALERT_SCOPE, cfg["channels"]),
                use_tree=_md.use_hierarchy(scope_rows))
            s, k, planned = await _delivery.deliver_digests(
                so, oid, _va.ALERT_SCOPE, fan["digests"], build=_build,
                wa_filename="store-visit-todo.txt", channels_ok=channels_ok, dry_run=dry_run)
            res["todo"] = {"sent": s, "skipped": k, "recipients": planned}

        # ── 2. the DRAFT purchase order, before the accessory notification names it ──────────
        po = _accessory_po_for_visits(oid, cfg, visits, acc_rows, who=who, dry_run=dry_run)
        res["accessory_po"] = po
        # The push is a separate step on purpose: raising the draft here must not depend on the
        # vendor's system being up, and a dry run never speaks to anybody.
        if not dry_run:
            po["vendor_push"] = await _push_accessory_pos(oid, cfg, po.get("created") or [])

        # ── 3. the separate accessory notification ──────────────────────────────────────────
        acc_lines = _va.accessory_lines(acc_rows, {v.get("id"): v for v in visits})
        if (cfg["accessory_enabled"] or not respect_enabled) and acc_lines:
            scope_rows = [r for r in rows if str(r.get("scope") or "").strip().lower()
                          in (_va.ACCESSORY_SCOPE, "all")]
            first_po = (po.get("created") or [None])[0]

            def _build_acc(name, lns, _v=po.get("vendor_name"), _p=first_po):
                return _va.build_accessory_digest(name, lns, vendor_name=_v, link=link, po=_p)

            stores = {l["store_code"] for l in acc_lines if l.get("store_code")}
            fan = _md.plan_digests(
                acc_lines, {s: _managers_above_dm(oid, s) for s in stores}, "",
                scope=_va.ACCESSORY_SCOPE, build=_build_acc,
                key_parts=_va.accessory_key_parts, channels=cfg["accessory_channels"],
                extra_recipients=_md.named_extras(scope_rows, _va.ACCESSORY_SCOPE,
                                                  cfg["accessory_channels"]),
                use_tree=_md.use_hierarchy(scope_rows))
            s, k, planned = await _delivery.deliver_digests(
                so, oid, _va.ACCESSORY_SCOPE, fan["digests"], build=_build_acc,
                wa_filename="store-visit-accessories.txt", channels_ok=channels_ok, dry_run=dry_run)
            res["accessories"] = {"lines": len(acc_lines), "sent": s, "skipped": k,
                                  "recipients": planned}
        elif acc_lines:
            res["accessories"] = {"lines": len(acc_lines), "sent": 0, "skipped": 0,
                                  "note": "the accessory notification is switched off"}
        results.append(res)
    return {"ran": len(results), "dry_run": dry_run, "results": results}


@router.get("/visits/{visit_id}/todos")
def visit_todos(visit_id: str, org_id: str = ORG_ID):
    """READ-ONLY: what this visit left to be done, and what it asked to order. The same rows the
    digest is built from, so what a manager reads in the alert and what the board shows cannot
    disagree."""
    from app.modules.storevisit import visit_alerts as _va
    visits, responses, items, plan, acc = _visit_window(org_id, _va.HOUSE_CONFIG, visit_id)
    if not visits:
        raise HTTPException(404, "visit not found")
    v = visits[0]
    todos = _va.todo_items(v, responses, items, plan)
    return {"visit_id": visit_id, "store_code": v.get("store_code"), "status": v.get("status"),
            "items": todos, "summary": _va.summarize(todos),
            "labels": _va.kind_labels(),
            "accessories": _va.accessory_lines(acc, {v.get("id"): v})}


@router.post("/alerts/run-due")
async def visit_alerts_run_due(x_notify_secret: str = Header(default="")):
    """Secret-gated pg_cron entrypoint. Every switched-on tenant's recently submitted visits are
    checked; `alert_log` dedup means a given visit item is announced once, so the tick can be as
    frequent as the tenant wants without repeating itself."""
    from app.core.run_secret import verify_notify_secret
    if not verify_notify_secret(x_notify_secret):
        raise HTTPException(403, "forbidden")
    return await _run_store_visit_alerts(respect_enabled=True, dry_run=False)


@router.post("/alerts/run-now")
async def visit_alerts_run_now(send: bool = False, visit_id: str = None,
                               authorization: str = Header(default=""), org_id: str = ORG_ID):
    """Manager/admin manual trigger for THIS tenant, bypassing the enable gate. DEFAULTS TO A DRY
    RUN — it returns exactly who WOULD be messaged, on which channels, about which visits, and what
    the draft purchase order would contain, sending nothing and creating nothing. Dedup is always
    honoured, and no purchase order is ever transmitted to a supplier by any path here."""
    from app.modules.storeops.router import _require_manager
    mgr = _require_manager(authorization, org_id)
    org_id = mgr.get("org_id") or org_id
    return await _run_store_visit_alerts(org_id_filter=org_id, respect_enabled=False,
                                         dry_run=not send, visit_id=visit_id,
                                         who=mgr.get("email"))
