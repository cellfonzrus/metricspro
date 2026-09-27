"""Setup-documents endpoints (owner 2026-09-27, index §39). The logic lives in commcalc/setup_documents.py; this file is
only HTTP: who may call, what they may change.

  Tenant (the setup wizard) — gated like Import Health (view: admin-ish; edit: the 'import_health' settings area),
  because the rows it writes ARE the tenant's core.import_feed rows:
    GET  /commcalc/setup-documents                 the tenant's required documents + where to get them + the gate
    GET  /commcalc/setup-documents/gate            cheap: is this tenant's admin still walked to the wizard?
    PUT  /commcalc/setup-documents/{key}           {cadence: daily|weekly|monthly} and/or {skip: true|false}

  Platform super admin (Super Admin Toolbox → Carrier Documents) — THE list, on the house rows of the registry:
    GET  /commcalc/report-kinds/house              every house kind + its setup facts + its automation track record
    PUT  /commcalc/report-kinds/house/{key}        edit one
    POST /commcalc/report-kinds/house              add one
    POST /commcalc/setup-documents/reopen          walk one tenant's admins through the wizard again
"""
import re
from typing import Any, Optional

from fastapi import APIRouter, Header, HTTPException

from app.core.database import get_supabase
from app.core.schemas import LaxModel
from app.modules.commcalc import report_kinds as RK
from app.modules.commcalc import setup_documents as SD
from app.modules.core import import_health as IH

router = APIRouter(prefix="/commcalc", tags=["setup-documents"])
ORG_ID = RK.HOUSE_ORG

# What a super admin may change on a house kind. Identity (key) and the ingest wiring that code depends on
# (landing / layout / signature fields) are not editable here — they belong to the kind's own migration.
HOUSE_EDITABLE = ("label", "what_in_it", "source_hint", "applies_to_carrier", "applies_to_pos", "applies_to_vertical",
                  "is_active", "sort_order") + RK.SETUP_COLS
_ARRAY_COLS = ("applies_to_carrier", "applies_to_pos", "applies_to_vertical", "upload_types")


def sb():
    return get_supabase()


def _require_super_admin(authorization, active_org):
    from app.modules.core.router import _require_super_admin as _gate
    return _gate(authorization, active_org)


class PutDocumentIn(LaxModel):
    cadence: Optional[str] = None
    skip: Optional[bool] = None


class HouseKindIn(LaxModel):
    key: Optional[str] = None
    label: Optional[str] = None
    what_in_it: Optional[str] = None
    source_hint: Optional[str] = None
    landing: Optional[str] = None
    upload_types: Any = None
    applies_to_carrier: Any = None
    applies_to_pos: Any = None
    applies_to_vertical: Any = None
    is_active: Optional[bool] = None
    sort_order: Optional[int] = None
    required: Optional[bool] = None
    default_cadence: Optional[str] = None
    download_url: Optional[str] = None
    download_steps: Optional[str] = None
    evidence_table: Optional[str] = None
    automation_min_runs: Optional[int] = None
    automation_window_days: Optional[int] = None


class ReopenIn(LaxModel):
    org_id: Optional[str] = None


def clean_house_patch(body: dict) -> dict:
    """PURE validation of a super admin's edit (proven by harness_setup_documents.py §E)."""
    out = {}
    for k in HOUSE_EDITABLE + ("upload_types", "landing"):
        if k not in body:
            continue
        v = body[k]
        if k in _ARRAY_COLS:
            vals = v if isinstance(v, list) else [x for x in re.split(r"[,\s]+", str(v or "")) if x]
            out[k] = [RK.code(x) if k in ("applies_to_carrier", "applies_to_pos") else str(x).strip().lower()
                      for x in vals if str(x).strip()]
        elif k == "default_cadence":
            if v not in SD.CADENCES:
                raise ValueError("default_cadence must be one of " + ", ".join(SD.CADENCES))
            out[k] = v
        elif k == "landing":
            if v not in RK.LANDINGS:
                raise ValueError("landing must be one of " + ", ".join(RK.LANDINGS))
            out[k] = v
        elif k == "automation_min_runs":
            n = int(v)
            if not 1 <= n <= 1000:
                raise ValueError("automation_min_runs must be 1–1000")
            out[k] = n
        elif k == "automation_window_days":
            n = int(v)
            if not 1 <= n <= 365:
                raise ValueError("automation_window_days must be 1–365")
            out[k] = n
        elif k == "download_url":
            u = (v or "").strip()
            if u and not re.match(r"^https?://", u, re.I):
                raise ValueError("download_url must start with http:// or https://")
            out[k] = u or None
        elif k == "evidence_table":
            t = (v or "").strip()
            if t and not re.match(r"^[a-z_][a-z0-9_]{0,62}$", t):
                raise ValueError("evidence_table must be a plain table name in the commcalc schema")
            out[k] = t or None
        elif k in ("required", "is_active"):
            out[k] = bool(v)
        elif k == "sort_order":
            out[k] = int(v)
        else:
            out[k] = (str(v).strip() or None) if v is not None else None
    if "label" in out and not out["label"]:
        raise ValueError("label cannot be empty")
    return out


# ── Tenant ───────────────────────────────────────────────────────────────────────────────────────────────────────────
@router.get("/setup-documents")
def get_setup_documents(org_id: str = ORG_ID, authorization: str = Header(default=""),
                        x_active_org: str = Header(default="")):
    client, _caller, org = IH._gate(authorization, x_active_org, org_id)
    return SD.payload(client, org, persist=True)


@router.get("/setup-documents/gate")
def get_setup_gate(org_id: str = ORG_ID, authorization: str = Header(default=""),
                   x_active_org: str = Header(default="")):
    """Cheap for every tenant that is done (one read); the full evaluation only while a tenant is still setting up."""
    client, _caller, org = IH._gate(authorization, x_active_org, org_id)
    done_at, _name = SD.tenant_setup_state(client, org)
    if done_at:          # done, or a database without the mig-1028 column (never gate)
        return {"active": False, "pending": [], "done": True, "wizard_path": SD.WIZARD_PATH}
    p = SD.payload(client, org, persist=True)
    return {**p["gate"], "wizard_path": SD.WIZARD_PATH}


@router.put("/setup-documents/{key}")
def put_setup_document(key: str, body: PutDocumentIn, org_id: str = ORG_ID, authorization: str = Header(default=""),
                       x_active_org: str = Header(default="")):
    client, _caller, org = IH._gate(authorization, x_active_org, org_id, edit=True)
    fields = body.model_fields_set
    if "cadence" not in fields and "skip" not in fields:
        raise HTTPException(400, "send a cadence and/or skip")
    try:
        upd = SD.put_document(client, org, key, cadence=body.cadence if "cadence" in fields else None,
                              skip=body.skip if "skip" in fields else None)
    except ValueError as e:
        raise HTTPException(400, str(e))
    except LookupError as e:
        raise HTTPException(404, str(e))
    return {"ok": True, "key": key, "saved": {k: v for k, v in upd.items() if k != "updated_at"}}


# ── Platform super admin ─────────────────────────────────────────────────────────────────────────────────────────────
@router.get("/report-kinds/house")
def get_house_kinds(authorization: str = Header(default=""), x_active_org: str = Header(default="")):
    _require_super_admin(authorization, x_active_org)
    client = sb()
    try:
        rows = (client.schema("commcalc").table(RK.TABLE).select("*").eq("org_id", RK.HOUSE_ORG)
                .order("sort_order").execute().data) or []
    except Exception as e:
        raise HTTPException(503, f"The report registry could not be read ({str(e)[:160]}).")
    runs = SD.load_automation_runs(client)
    out = []
    for r in rows:
        n = RK.normalise_row(r)
        n["_source"] = "house"
        out.append({**{k: n.get(k) for k in ("key", "label", "what_in_it", "source_hint", "landing", "upload_types",
                                             "applies_to_carrier", "applies_to_pos", "applies_to_vertical",
                                             "is_active", "sort_order")},
                    **SD.setup_fields(n), "automation": SD.automation_proof(n, runs),
                    "setup_ready": all(c in r for c in RK.SETUP_COLS)})
    return {"kinds": out, "cadences": list(SD.CADENCES), "landings": list(RK.LANDINGS),
            "setup_ready": bool(out) and all(k["setup_ready"] for k in out),
            "migration": "1028_setup_documents.sql"}


@router.put("/report-kinds/house/{key}")
def put_house_kind(key: str, body: HouseKindIn, authorization: str = Header(default=""),
                   x_active_org: str = Header(default="")):
    _require_super_admin(authorization, x_active_org)
    raw = {k: getattr(body, k) for k in body.model_fields_set if k != "key"}
    try:
        patch = clean_house_patch(raw)
    except (ValueError, TypeError) as e:
        raise HTTPException(400, str(e))
    if not patch:
        raise HTTPException(400, "nothing to update")
    patch["updated_at"] = RK.now_iso()
    try:
        res = (sb().schema("commcalc").table(RK.TABLE).update(patch)
               .eq("org_id", RK.HOUSE_ORG).eq("key", key).execute().data) or []
    except Exception as e:
        raise HTTPException(400, f"Could not save ({str(e)[:200]}). If a setup column is named, apply migration 1028.")
    if not res:
        raise HTTPException(404, f"no platform document called {key}")
    return {"ok": True, "key": key}


@router.post("/report-kinds/house")
def post_house_kind(body: HouseKindIn, authorization: str = Header(default=""), x_active_org: str = Header(default="")):
    _require_super_admin(authorization, x_active_org)
    key = re.sub(r"[^a-z0-9_]+", "_", (body.key or body.label or "").strip().lower()).strip("_")
    if not key:
        raise HTTPException(400, "a key or label is required")
    raw = {k: getattr(body, k) for k in body.model_fields_set if k != "key"}
    raw.setdefault("landing", "other")
    try:
        row = clean_house_patch(raw)
    except (ValueError, TypeError) as e:
        raise HTTPException(400, str(e))
    if not row.get("label"):
        raise HTTPException(400, "label is required")
    row.update({"org_id": RK.HOUSE_ORG, "key": key, "defined_by": "house"})
    try:
        sb().schema("commcalc").table(RK.TABLE).insert(row).execute()
    except Exception as e:
        raise HTTPException(400, f"Could not add ({str(e)[:200]}).")
    return {"ok": True, "key": key}


@router.post("/setup-documents/reopen")
def reopen_setup(body: ReopenIn, authorization: str = Header(default=""), x_active_org: str = Header(default="")):
    _require_super_admin(authorization, x_active_org)
    org = (body.org_id or "").strip()
    if not org:
        raise HTTPException(400, "org_id required")
    try:
        sb().schema("storeops").table("tenants").update({"documents_setup_done_at": None}).eq("org_id", org).execute()
    except Exception as e:
        raise HTTPException(400, f"Could not re-open ({str(e)[:160]}). Apply migration 1028.")
    return {"ok": True, "org_id": org}
