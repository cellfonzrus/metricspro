"""SUPER-ADMIN TENANT GATES — the API the gating dashboard reads and writes. I/O ONLY.

OWNER (2026-11-10), verbatim: *"why is vidapay connector showing in boost ? we need to get these
things under control before we roll out for outside customers — have an agent asses and create a
dashboard for the super admin whereby these things can be gated out for the respective tenants right
from the get go"*.

TWO LAYERS, deliberately separated (the control-box precedent):
  · `core/feature_gate.py` — PURE. The posture vocabulary, the truth table, the grid row and its
    fold. Proven DB-free by `backend/harness_feature_gate.py`.
  · this file — I/O only. It GATHERS what the three existing mechanisms already hold and hands it to
    that pure layer. It decides nothing and spells no vendor, module or tenant name.

WHAT IT GATHERS — each from the mechanism that already owns it, never a second derivation:
  · modules      → `core/entitlements.load_module_catalog` (core.module_catalog, mig 700) for the
                   list, and `effective_modules` for `by_rule`.
  · connectors   → `commcalc/connector_registry` (mig 1014) rows + `report_kinds.tenant_declaration`
                   for `by_rule` — the registry's own answer for that tenant.
  · report kinds → `commcalc/report_kinds` (mig 1010), the same way.
  · grants       → `feature_gate.load_grants` (the `cap` rows, mig 068) — the one grant reader.
  · posture      → `feature_gate.load_posture` (mig 1119) — the one posture reader.

AUTHORITY. Every endpoint here is `core.router._require_super_admin` — THE one gate — and NOT a
second one. These endpoints are cross-tenant by nature (that is the whole point of the dashboard), so
they take an explicit `org` and never the middleware-rewritten `org_id`: a tenant admin must not be
able to reach another tenant's gates by switching the active org. A write is REFUSED unless the
caller is a super admin, and the target org must be a registered tenant.

MOUNTED on core's router (prefix `/tenant-gates` → `/api/v1/core/tenant-gates/*`), like the operator
console, so `main.py` needs no change and there is no import cycle (core.router is imported lazily).

DEGRADES: with mig 1119 un-run every posture reads `inherit`, so the board shows every tenant exactly
as the platform behaves today and a posture write returns the reason it could not be stored.
NOT MONEY-TOUCHING.
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Header, HTTPException

from app.core.database import get_supabase
from app.modules.core import feature_gate as FG

router = APIRouter(prefix="/tenant-gates", tags=["tenant-gates"])


def _sb():
    return get_supabase()


def _gate(authorization: str, active_org: str = ""):
    """THE one gate, dereferenced. No second authority is defined in this file."""
    from app.modules.core.router import _require_super_admin
    return _require_super_admin(authorization, active_org)


def _tenants(client):
    """Every registered tenant with its posture — name included so the board can be read by a human."""
    try:
        rows = (client.schema("storeops").table("tenants")
                .select("org_id,name,is_active,created_at," + FG.COLUMN).execute().data) or []
    except Exception:
        try:
            rows = (client.schema("storeops").table("tenants")
                    .select("org_id,name,is_active,created_at").execute().data) or []
        except Exception:
            rows = []
    out = []
    for r in rows:
        if not r.get("org_id"):
            continue
        out.append({"org_id": str(r["org_id"]), "name": r.get("name") or str(r["org_id"])[:8],
                    "is_active": r.get("is_active") is not False,
                    "created_at": r.get("created_at"),
                    "posture": FG.posture(r.get(FG.COLUMN))})
    out.sort(key=lambda t: (t["name"] or "").lower())
    return out


def _require_tenant(client, org):
    org = str(org or "").strip()
    if not org:
        raise HTTPException(400, "org is required")
    if not any(t["org_id"] == org for t in _tenants(client)):
        raise HTTPException(404, "not a registered tenant")
    return org


# ── the three kinds, each asking the mechanism that owns it ───────────────────────────────────────
def _module_rows(client, org, posture, grants):
    from app.modules.core import entitlements as EN
    catalog = EN.load_module_catalog(client)
    try:
        by_rule = EN.effective_modules_by_rule(client, org)
    except Exception:
        by_rule = set(catalog.keys())
    return [FG.row(FG.KIND_MODULE, k, v, grant=grants.get(k), posture=posture,
                   by_rule=(k in by_rule)) for k, v in catalog.items()]


def _registry_rows(client, org, posture, grants, kind):
    """Connector / report-kind rows, each with the REGISTRY's own `by_rule` for this tenant."""
    from app.modules.commcalc import report_kinds as RK
    if kind == FG.KIND_CONNECTOR:
        from app.modules.commcalc import connector_registry as REG
        rows, ready = REG.load_registry(client, org)
    else:
        rows, ready = RK.load_registry(client, org)
    try:
        decl = RK.tenant_declaration(client, org)
    except Exception:
        decl = {"pos": [], "carriers": [], "vertical": None}
    out = []
    for r in rows:
        if not r.get("is_active", True):
            continue
        out.append(FG.row(kind, r["key"], r.get("label"), grant=grants.get(r["key"]),
                          posture=posture, by_rule=RK.applies(r, decl),
                          extra={"applies_to_pos": r.get("applies_to_pos") or [],
                                 "applies_to_carrier": r.get("applies_to_carrier") or [],
                                 "registry_ready": ready}))
    return out


def _rows_for(client, org, posture):
    rows = []
    for kind in FG.KINDS:
        grants = FG.load_grants(client, org, kind)
        if kind == FG.KIND_MODULE:
            rows += _module_rows(client, org, posture, grants)
        else:
            try:
                rows += _registry_rows(client, org, posture, grants, kind)
            except Exception:
                pass
    return rows


# ── endpoints ─────────────────────────────────────────────────────────────────────────────────────
@router.get("")
def list_tenant_gates(org: str = "", authorization: str = Header(default=""),
                      x_active_org: str = Header(default="")):
    """The board. Without `org`: every tenant, its posture and how much is on. With `org`: that
    tenant's full grid — every module, connector and report kind, its verdict, the basis for it and
    where its grant lives."""
    _gate(authorization, x_active_org)
    client = _sb()
    tenants = _tenants(client)
    body: dict[str, Any] = {
        "postures": list(FG.POSTURES), "posture_default_new_tenant": FG.POSTURE_GATED,
        "kinds": list(FG.KINDS), "grant_homes": FG.GRANT_HOME, "migration": FG.MIGRATION,
        "bases": list(FG.BASES), "tenants": tenants,
    }
    if not org:
        # The per-tenant fold, so the board can be read without opening every tenant.
        folds = []
        for t in tenants:
            try:
                folds.append({**t, "summary": FG.summary(_rows_for(client, t["org_id"], t["posture"]))})
            except Exception:
                folds.append({**t, "summary": None})
        body["tenants"] = folds
        return body
    org = _require_tenant(client, org)
    posture = FG.load_posture(client, org)
    rows = _rows_for(client, org, posture)
    body["org"] = org
    body["posture"] = posture
    body["rows"] = rows
    body["summary"] = FG.summary(rows)
    return body


@router.put("/posture")
def set_posture(body: dict, authorization: str = Header(default=""),
                x_active_org: str = Header(default="")):
    """Put ONE tenant on a posture. `gated` = this tenant is offered only what has been granted."""
    _gate(authorization, x_active_org)
    client = _sb()
    org = _require_tenant(client, (body or {}).get("org"))
    want = str((body or {}).get("posture") or "").strip().lower()
    try:
        FG.save_posture(client, org, want)
    except ValueError as e:
        raise HTTPException(400, str(e))
    except Exception as e:
        # The column may not exist yet (mig 1119 un-run). Say so; never pretend it was stored.
        raise HTTPException(409, f"could not store the posture — is {FG.MIGRATION} applied? ({e})")
    after = FG.load_posture(client, org)
    if after != want:
        raise HTTPException(409, f"the posture read back as {after!r} — {FG.MIGRATION} may not be applied")
    return {"org": org, "posture": after}


@router.put("/grant")
def set_grant(body: dict, authorization: str = Header(default=""),
              x_active_org: str = Header(default="")):
    """Grant / gate off / CLEAR one thing for one tenant. `grant: null` clears the decision, which
    returns the thing to the tenant's posture and its own declaration rather than leaving a row that
    says the opposite of what was meant."""
    _gate(authorization, x_active_org)
    client = _sb()
    b = body or {}
    org = _require_tenant(client, b.get("org"))
    kind = str(b.get("kind") or "").strip()
    if kind not in FG.KINDS:
        raise HTTPException(400, f"kind must be one of {FG.KINDS}")
    key = str(b.get("key") or "").strip()
    if not key:
        raise HTTPException(400, "key is required")
    grant = b.get("grant", None)
    if grant is not None and FG.grant_state(grant) is None:
        raise HTTPException(400, "grant must be true, false or null")
    try:
        out = FG.save_grant(client, org, kind, key, None if grant is None else FG.grant_state(grant))
    except ValueError as e:
        raise HTTPException(400, str(e))
    except Exception as e:
        raise HTTPException(409, f"could not store the decision ({e})")
    posture = FG.load_posture(client, org)
    grants = FG.load_grants(client, org, kind)
    return {"org": org, "posture": posture, **out, "stored_grant": grants.get(key)}
