"""Store Visit API Router — /api/v1/storevisit/*  (DM store-visit + inspection checklist).

Photos go to the Supabase Storage bucket `store-visits` (created on first upload); the DB stores
only the storage PATH, served to the UI as a short-lived signed URL. Tables live in storeops.*
(migration 027). A DM = the Market Manager role (scope: market) and acts on stores in their market.
"""
from fastapi import APIRouter, HTTPException, UploadFile, File, Form
from app.core.database import get_supabase
from datetime import datetime, timezone
import uuid

router = APIRouter(prefix="/storevisit", tags=["Store Visits"])

ORG_ID = "00000000-0000-0000-0000-000000000001"
BUCKET = "store-visits"
VACCESSORIZE_URL = "https://www.vaccessorize.com"


def sb():
    # store-visit + checklist tables live in the storeops.* schema (migration 027)
    return get_supabase().schema("storeops")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


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
def create_checklist_item(item: dict, org_id: str = ORG_ID):
    label = (item.get("label") or "").strip()
    if not label:
        raise HTTPException(400, "label required")
    body = {
        "org_id": org_id,
        "item_key": (item.get("item_key") or f"custom_{uuid.uuid4().hex[:8]}"),
        "label": label,
        "category": item.get("category") or "general",
        "input_type": item.get("input_type") or "check",
        "sort_order": int(item.get("sort_order") or 100),
        "is_active": bool(item.get("is_active", True)),
    }
    r = sb().table("checklist_items").insert(body).execute()
    return r.data[0] if r.data else body


@router.patch("/checklist-items/{item_id}")
def update_checklist_item(item_id: str, updates: dict):
    allowed = ("label", "category", "input_type", "sort_order", "is_active")
    body = {k: updates[k] for k in allowed if k in updates}
    body["updated_at"] = _now()
    r = sb().table("checklist_items").update(body).eq("id", item_id).execute()
    return r.data[0] if r.data else body


@router.delete("/checklist-items/{item_id}")
def delete_checklist_item(item_id: str):
    # Soft-delete (deactivate) so historical visits keep their item snapshots.
    sb().table("checklist_items").update({"is_active": False, "updated_at": _now()}).eq("id", item_id).execute()
    return {"deactivated": item_id}


# ── Stores in a market + scheduled rep ───────────────────────────────────────────────────
@router.get("/stores")
def stores_in_market(market: str = None, org_id: str = ORG_ID):
    rows = sb().table("stores").select("store_code,address,market").eq("org_id", org_id).order("address").execute().data or []
    if market:
        rows = [s for s in rows if (s.get("market") or "") == market]
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
def create_visit(payload: dict, org_id: str = ORG_ID):
    body = {
        "org_id": org_id,
        "store_code": payload.get("store_code"),
        "store_address": payload.get("store_address"),
        "market": payload.get("market"),
        "dm_email": payload.get("dm_email"),
        "dm_name": payload.get("dm_name"),
        "check_in_at": payload.get("check_in_at") or _now(),
        "check_in_lat": payload.get("check_in_lat"),
        "check_in_lng": payload.get("check_in_lng"),
        "check_in_accuracy": payload.get("check_in_accuracy"),
        "scheduled_rep": payload.get("scheduled_rep"),
        "actual_rep": payload.get("actual_rep"),
        "rep_discrepancy_reason": payload.get("rep_discrepancy_reason"),
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
    return {"visit": visit, "responses": responses, "accessories": accessories,
            "vaccessorize_url": VACCESSORIZE_URL}


@router.patch("/visits/{visit_id}")
def update_visit(visit_id: str, payload: dict, org_id: str = ORG_ID):
    header = ("store_code", "store_address", "market", "dm_email", "dm_name", "check_out_at",
              "scheduled_rep", "actual_rep", "rep_discrepancy_reason", "extra_notes",
              "check_in_lat", "check_in_lng", "check_in_accuracy")
    updates = {k: payload[k] for k in header if k in payload}
    if updates:
        updates["updated_at"] = _now()
        sb().table("store_visits").update(updates).eq("id", visit_id).eq("org_id", org_id).execute()

    # Checklist answers: full replace for this visit (delete-then-insert).
    if "responses" in payload:
        sb().table("store_visit_responses").delete().eq("visit_id", visit_id).eq("org_id", org_id).execute()
        rows = [{
            "org_id": org_id, "visit_id": visit_id,
            "item_key": r.get("item_key"),
            "label_snapshot": r.get("label_snapshot") or r.get("label"),
            "category_snapshot": r.get("category_snapshot") or r.get("category"),
            "checked": r.get("checked"),
            "note": r.get("note"),
            "photo_path": r.get("photo_path"),
        } for r in (payload["responses"] or [])]
        if rows:
            sb().table("store_visit_responses").insert(rows).execute()

    # Accessories-to-order: full replace.
    if "accessories" in payload:
        sb().table("store_visit_accessories").delete().eq("visit_id", visit_id).eq("org_id", org_id).execute()
        rows = []
        for a in (payload["accessories"] or []):
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
def submit_visit(visit_id: str, org_id: str = ORG_ID):
    sb().table("store_visits").update({
        "status": "submitted", "submitted_at": _now(), "updated_at": _now(),
    }).eq("id", visit_id).eq("org_id", org_id).execute()
    return get_visit(visit_id, org_id)


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
    if kind == "clean_store":
        sb().table("store_visits").update(
            {"clean_store_photo_path": path, "updated_at": _now()}).eq("id", visit_id).execute()
    elif kind == "signed_checklist":
        sb().table("store_visits").update(
            {"signed_checklist_path": path, "updated_at": _now()}).eq("id", visit_id).execute()
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
def save_action_items(visit_id: str, payload: dict, org_id: str = ORG_ID):
    """Full replace of the DM's discussion overlay for this visit (delete-then-insert)."""
    sb().table("visit_action_items").delete().eq("visit_id", visit_id).eq("org_id", org_id).execute()
    rows = []
    for it in (payload.get("items") or []):
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
def save_action_plan(visit_id: str, payload: dict, org_id: str = ORG_ID):
    """Full replace of the agreed rep action plan for this visit (delete-then-insert)."""
    sb().table("visit_action_plan").delete().eq("visit_id", visit_id).eq("org_id", org_id).execute()
    rows = []
    for p in (payload.get("plan") or []):
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
def signoff(visit_id: str, payload: dict, org_id: str = ORG_ID):
    """Record a rep or DM sign-off on the agreed action plan."""
    who = (payload.get("who") or "").lower()
    name = payload.get("name") or ""
    if who not in ("rep", "dm"):
        raise HTTPException(400, "who must be 'rep' or 'dm'")
    pre = "plan_rep" if who == "rep" else "plan_dm"
    signed = payload.get("signed", True)
    upd = {f"{pre}_signed": bool(signed), "updated_at": _now()}
    upd[f"{pre}_signed_by"] = name if signed else None
    upd[f"{pre}_signed_at"] = _now() if signed else None
    sb().table("store_visits").update(upd).eq("id", visit_id).eq("org_id", org_id).execute()
    return get_visit_action(visit_id, org_id)


@router.get("/health")
def health():
    return {"status": "ok", "module": "storevisit", "vaccessorize_url": VACCESSORIZE_URL}
