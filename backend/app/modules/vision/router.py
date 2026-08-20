"""Vision API — /api/v1/vision/*  (live Nest camera feeds, customer heat maps, behavior coaching).

OWNER DIRECTIVE 2026-08-19 (sanjot@): "pull the camera feed from the Google home server in live mode
and give analytics for the employee behavior use their voice transcript, use the heat map based on
the customers in and out of the store."

Tables: core.vision_* (migration 900). See that migration's header for why `core` and not a `vision`
schema, and for what this module deliberately does NOT store.

SHAPE (identical doctrine to crm/referral):
  • Every DECISION is a pure function in a sibling file — config.py (the gates), geometry.py (did a
    person cross the door line), heatmap.py (traffic + occupancy roll-up), behavior.py (rubric
    scoring), ingest.py (what the edge analyzer may send), google_sdm.py (the Google request shapes).
    This file is I/O and HTTP only, which is what lets the whole module be proven offline by the four
    harness_vision_*.py scripts instead of "verified" by watching a camera.
  • org_id is a QUERY PARAM on every operator endpoint (AGENT_CONTRACT §2); the tenant middleware
    rewrites it from the caller's JWT. Every read filters it and every insert stamps it.
  • Every table read is wrapped: migration 900 not run degrades to an empty list / a named 400.
  • THE EDGE ROUTES ARE DIFFERENT. /vision/edge/* carries no JWT — an analyzer box in a stockroom is
    not a person — and self-authenticates with a per-agent HMAC signature over the body. They are on
    the tenant-middleware public-prefix allowlist for exactly that reason, and every one of them
    resolves its own org from the agent record, never from the request.

NO VIDEO AND NO AUDIO EVER TRANSITS THIS PROCESS. A WebRTC live view is brokered here (the browser's
SDP offer goes to Google, Google's answer comes back) and the media flows browser↔Google directly.
The analyzer holds its own stream at the edge and posts only derived numbers.
"""
import json
import secrets
from datetime import date, datetime, timedelta, timezone
from typing import Any

from fastapi import APIRouter, Header, HTTPException, Request

from app.core.crypto import encrypt, decrypt
from app.core.database import get_supabase
from app.core.schemas import LaxModel
from app.modules.vision import behavior as B
from app.modules.vision import config as C
from app.modules.vision import google_sdm as G
from app.modules.vision import heatmap as H
from app.modules.vision import ingest as I
from app.modules.vision import retention as R

router = APIRouter(prefix="/vision", tags=["Vision"])

ORG_ID = "00000000-0000-0000-0000-000000000001"   # house org; middleware rewrites the query param


def sb():
    """Vision tables live in core.* (migration 900) — the schema PostgREST already serves."""
    return get_supabase().schema("core")


def _now():
    return datetime.now(timezone.utc)


def _iso(dt):
    return dt.isoformat() if hasattr(dt, "isoformat") else dt


# ══════════════════════════════════════════════════════════════════════════════════════════════
# Request bodies (Item 15 Pydantic rollout — lax so a legacy caller never 422s)
# ══════════════════════════════════════════════════════════════════════════════════════════════
class ConfigIn(LaxModel):
    enabled: Any = None
    live_view_enabled: Any = None
    traffic_enabled: Any = None
    heatmap_enabled: Any = None
    audio_analytics_enabled: Any = None
    behavior_scoring_enabled: Any = None
    audio_consent_mode: Any = None
    presence_retention_days: Any = None
    visit_retention_days: Any = None
    transcript_retention_days: Any = None
    heat_retention_days: Any = None
    score_retention_days: Any = None
    grid_cols: Any = None
    grid_rows: Any = None
    min_visit_seconds: Any = None
    max_visit_seconds: Any = None
    stream_max_minutes: Any = None


class GoogleLinkIn(LaxModel):
    project_id: Any = None
    client_id: Any = None
    client_secret: Any = None
    code: Any = None
    redirect_uri: Any = None


class StructuresIn(LaxModel):
    structures: Any = None      # [{structure_id, structure_name, enabled, default_store_code}]


class CameraPatchIn(LaxModel):
    label: Any = None
    store_code: Any = None
    stream_protocol: Any = None
    analytics_enabled: Any = None
    audio_enabled: Any = None
    is_entrance: Any = None
    enabled: Any = None


class ZonesIn(LaxModel):
    zones: Any = None


class StreamIn(LaxModel):
    offer_sdp: Any = None
    purpose: Any = None


class AgentIn(LaxModel):
    label: Any = None
    store_code: Any = None


class ConsentIn(LaxModel):
    employee_id: Any = None
    status: Any = None
    scope: Any = None
    source: Any = None
    document_url: Any = None
    note: Any = None


class RulesIn(LaxModel):
    rules: Any = None


class PurgeIn(LaxModel):
    confirm: Any = None


# ══════════════════════════════════════════════════════════════════════════════════════════════
# Caller identity, permissions, scope (mirrors referral/crm — Vision adds no new vocabulary)
# ══════════════════════════════════════════════════════════════════════════════════════════════
def _caller(authorization: str, x_active_org: str = ""):
    """{org_id, role, super_admin, perms, id, employee_id, store_code, market} or None."""
    try:
        from app.modules.core.router import _uid_from_token, _resolve_caller
        uid = _uid_from_token(authorization)
        if not uid:
            return None
        c = _resolve_caller(get_supabase(), uid, (x_active_org or "").strip() or None)
        if not c:
            return None
        try:
            from app.core.tenant_middleware import caller_app_user
            u = caller_app_user(uid, "id,org_id,employee_id,store_code,market,full_name,email") or {}
        except Exception:
            u = {}
        return {**c, "id": u.get("id"), "employee_id": u.get("employee_id"),
                "store_code": u.get("store_code"), "market": u.get("market"),
                "full_name": u.get("full_name"), "email": u.get("email")}
    except Exception:
        return None


def _require_caller(authorization, x_active_org):
    caller = _caller(authorization, x_active_org)
    if not caller:
        raise HTTPException(401, "not authenticated")
    return caller


def _keyset(authorization: str, org_id: str):
    """None = unrestricted; else the UPPER store keyset the caller may see. The same helper
    closing/pos/crm/referral already use — Vision introduces no second scoping vocabulary."""
    try:
        from app.modules.storeops.router import scope_keyset
        return scope_keyset(authorization, org_id)
    except Exception:
        return None


def _in_keyset(keyset, *vals) -> bool:
    if keyset is None:
        return True
    return any(str(v or "").strip().upper() in keyset for v in vals)


def _is_manager(caller) -> bool:
    return bool(caller and (caller.get("super_admin")
                            or (caller.get("perms") or {}).get("scope") in ("all", "market")))


def _can_edit_settings(caller) -> bool:
    """Who may change the vision program setup — the master switch, the Google link, camera
    assignment, zones, retention. Company-wide scope or an explicit `settings.vision` grant. A caller
    we could not resolve is DENIED, never defaulted open."""
    if not caller:
        return False
    if caller.get("super_admin"):
        return True
    perms = caller.get("perms") or {}
    s = perms.get("settings") or {}
    if "vision" in s:
        return bool(s["vision"])
    return (perms.get("scope") == "all") or ((caller.get("role") or "").lower() == "admin")


def _require_settings(caller):
    if not _can_edit_settings(caller):
        raise HTTPException(403, "Changing the camera-analytics setup is permission-restricted — you "
                                 "need the 'vision' settings permission or a company-wide role.")


def _require_manager(caller, what="this report"):
    """Behavior scores are about a named person, so they are manager-only by default. An employee
    reaches their OWN numbers through /vision/behavior/mine, which needs no manager role — a person
    is always entitled to see what is being recorded about them."""
    if not _is_manager(caller) and (caller.get("role") or "").lower() != "admin":
        raise HTTPException(403, f"Viewing {what} needs a manager role.")


def _cfg(org_id: str) -> dict:
    return C.resolve_config(get_supabase(), org_id)


def _require_module(cfg, feature=None):
    """Fail with an explanation an operator can act on, not a bare 403. The three "off" reasons look
    identical from the outside and have completely different fixes, so they are named."""
    if not cfg.get("available"):
        raise HTTPException(400, "Camera analytics is not installed on this database yet "
                                 "(migration 900 has not been run).")
    if not cfg.get("enabled"):
        raise HTTPException(403, "Camera analytics is turned off for this company. An administrator "
                                 "enables it in Vision → Settings.")
    if feature and not C.feature_enabled(cfg, feature):
        if feature in ("audio_analytics", "behavior_scoring") and cfg.get("audio_kill_switch"):
            raise HTTPException(403, "Voice transcript analytics is disabled for this deployment "
                                     "(VISION_AUDIO_ENABLED is not set on the server).")
        raise HTTPException(403, f"The '{feature.replace('_', ' ')}' feature is turned off for this "
                                 "company.")


def _audit(org_id, actor, action, target=None, detail=None):
    try:
        sb().table("vision_audit").insert({
            "org_id": org_id, "actor": actor, "action": action, "target": target,
            "detail": detail or {}}).execute()
    except Exception:
        pass       # an audit write must never fail the action it is auditing


def _rows(table, org_id, cols="*", **eq):
    """Wrapped select — a missing migration degrades to []."""
    try:
        q = sb().table(table).select(cols).eq("org_id", org_id)
        for k, v in eq.items():
            if v is not None:
                q = q.eq(k, v)
        return q.execute().data or []
    except Exception:
        return []


# ══════════════════════════════════════════════════════════════════════════════════════════════
# Configuration + status
# ══════════════════════════════════════════════════════════════════════════════════════════════
@router.get("/config")
def get_config(org_id: str = ORG_ID, authorization: str = Header(default=""),
               x_active_org: str = Header(default="")):
    caller = _require_caller(authorization, x_active_org)
    cfg = _cfg(org_id)
    return {**cfg, "can_edit": _can_edit_settings(caller),
            "audio_consent_modes": ["required", "off"]}


@router.put("/config")
def put_config(body: ConfigIn, org_id: str = ORG_ID, authorization: str = Header(default=""),
               x_active_org: str = Header(default="")):
    """Update the tenant's vision configuration.

    Two of these are not ordinary settings and are treated accordingly:
      * turning `enabled` ON is recorded with who and when (the columns exist for that purpose);
      * setting `audio_consent_mode` to 'off' is an operator asserting they hold their own recorded
        releases, so it is written to vision_audit with the actor's name. It does NOT retroactively
        legitimise anything — a 'declined' or 'withdrawn' employee is still never recorded (see
        config.consent_ok), because consent_mode is about people with NO record, not about overriding
        someone who said no.
    """
    caller = _require_caller(authorization, x_active_org)
    _require_settings(caller)
    cfg = _cfg(org_id)
    if not cfg.get("available"):
        raise HTTPException(400, "Camera analytics is not installed on this database yet "
                                 "(migration 900 has not been run).")

    patch, actor = {}, (caller.get("email") or caller.get("full_name") or "unknown")
    for k in ("enabled", "live_view_enabled", "traffic_enabled", "heatmap_enabled",
              "audio_analytics_enabled", "behavior_scoring_enabled"):
        v = getattr(body, k, None)
        if v is not None:
            patch[k] = bool(v)
    for k in ("presence_retention_days", "visit_retention_days", "transcript_retention_days",
              "heat_retention_days", "score_retention_days", "grid_cols", "grid_rows",
              "min_visit_seconds", "max_visit_seconds", "stream_max_minutes"):
        v = getattr(body, k, None)
        if v is not None:
            try:
                patch[k] = max(0, int(v))
            except (TypeError, ValueError):
                raise HTTPException(400, f"{k} must be a whole number.")
    mode = getattr(body, "audio_consent_mode", None)
    if mode is not None:
        mode = str(mode).strip().lower()
        if mode not in ("required", "off"):
            raise HTTPException(400, "audio_consent_mode must be 'required' or 'off'.")
        patch["audio_consent_mode"] = mode

    if not patch:
        return {**cfg, "saved": False}

    if patch.get("enabled") and not cfg.get("enabled"):
        patch["enabled_at"] = _iso(_now())
        patch["enabled_by"] = actor
    patch["updated_at"] = _iso(_now())

    try:
        sb().table("vision_config").upsert({"org_id": org_id, **patch},
                                           on_conflict="org_id").execute()
    except Exception as e:
        raise HTTPException(400, f"Could not save the vision configuration: {str(e)[:200]}")

    changed = {k: v for k, v in patch.items() if k not in ("updated_at",)}
    _audit(org_id, actor, "config_update", "vision_config", changed)
    if patch.get("audio_consent_mode") == "off":
        _audit(org_id, actor, "audio_consent_mode_off", "vision_config",
               {"note": "Operator asserts they hold their own recorded releases."})
    return {**_cfg(org_id), "saved": True}


@router.get("/status")
def status(org_id: str = ORG_ID, authorization: str = Header(default=""),
           x_active_org: str = Header(default="")):
    """The one call the Vision settings page opens with: every gate, and what is on the other side of
    it. Built so an operator can see WHY nothing is being recorded without reading five pages."""
    caller = _require_caller(authorization, x_active_org)
    cfg = _cfg(org_id)
    cams = _rows("vision_camera", org_id)
    agents = _rows("vision_edge_agent", org_id)
    cred = (_rows("vision_credential", org_id, provider="google_sdm") or [None])[0]
    consents = _rows("vision_consent", org_id, scope="audio")
    return {
        "config": cfg,
        "can_edit": _can_edit_settings(caller),
        "google": {
            "linked": bool(cred and cred.get("refresh_token_enc")),
            "status": (cred or {}).get("status") or "needs_setup",
            "project_id": (cred or {}).get("project_id"),
            "google_account": (cred or {}).get("google_account"),
            "last_ok_at": (cred or {}).get("last_ok_at"),
            "last_error": (cred or {}).get("last_error"),
        },
        "homes": {
            "claimed": len(_rows("vision_structure", org_id)),
        },
        "cameras": {
            "total": len(cams),
            "enabled": sum(1 for c in cams if c.get("enabled")),
            "unassigned": sum(1 for c in cams if not (c.get("store_code") or "").strip()),
            "entrances": sum(1 for c in cams if c.get("is_entrance")),
            "audio_on": sum(1 for c in cams if c.get("audio_enabled")),
        },
        "edge_agents": {
            "total": len(agents),
            "online": sum(1 for a in agents if _fresh(a.get("last_seen_at"), minutes=10)),
            "last_ingest_at": max([a.get("last_ingest_at") or "" for a in agents] or [""]) or None,
        },
        "consent": {
            "signed": sum(1 for c in consents if c.get("status") == C.CONSENT_SIGNED),
            "declined": sum(1 for c in consents if c.get("status") == C.CONSENT_DECLINED),
            "withdrawn": sum(1 for c in consents if c.get("status") == C.CONSENT_WITHDRAWN),
            "pending": sum(1 for c in consents if c.get("status") == C.CONSENT_PENDING),
        },
    }


def _fresh(iso, minutes=10) -> bool:
    try:
        return datetime.fromisoformat(str(iso).replace("Z", "+00:00")) > _now() - timedelta(minutes=minutes)
    except (ValueError, TypeError):
        return False


# ══════════════════════════════════════════════════════════════════════════════════════════════
# Google Device Access link
# ══════════════════════════════════════════════════════════════════════════════════════════════
@router.get("/google/auth-url")
def google_auth_url(redirect_uri: str = "", org_id: str = ORG_ID,
                    authorization: str = Header(default=""), x_active_org: str = Header(default="")):
    """The consent URL the operator visits once to link the Google account that owns the cameras.
    Requires the project_id + client_id to already be saved (POST /google/link without a code)."""
    _require_settings(_require_caller(authorization, x_active_org))
    cred = (_rows("vision_credential", org_id, provider="google_sdm") or [None])[0]
    if not cred or not cred.get("project_id") or not cred.get("client_id"):
        raise HTTPException(400, "Save the Device Access project id and OAuth client id first.")
    if not redirect_uri:
        raise HTTPException(400, "redirect_uri is required and must match the OAuth client exactly.")
    return {"url": G.authorization_url(cred["project_id"], cred["client_id"], redirect_uri),
            "scope": G.SDM_SCOPE}


@router.post("/google/link")
def google_link(body: GoogleLinkIn, org_id: str = ORG_ID, authorization: str = Header(default=""),
                x_active_org: str = Header(default="")):
    """Two-step by design. Called WITHOUT `code` it saves the project/client details so the auth URL
    can be built; called WITH `code` it exchanges the authorization code for the refresh token.
    The client secret and the refresh token are stored through app.core.crypto — never in the clear,
    and there is no endpoint anywhere in this module that reads them back out."""
    caller = _require_caller(authorization, x_active_org)
    _require_settings(caller)
    actor = caller.get("email") or "unknown"
    existing = (_rows("vision_credential", org_id, provider="google_sdm") or [None])[0] or {}

    row = {"org_id": org_id, "provider": "google_sdm", "updated_at": _iso(_now())}
    for k in ("project_id", "client_id"):
        v = getattr(body, k, None)
        if v is not None:
            row[k] = str(v).strip()
    # Reject a Cloud project id HERE rather than letting it save and 404 every device call later,
    # from a URL the operator never sees. The message names which of the two ids was pasted.
    if row.get("project_id"):
        problem = G.project_id_problem(row["project_id"])
        if problem:
            raise HTTPException(400, problem)
    if getattr(body, "client_secret", None):
        row["client_secret_enc"] = encrypt(str(body.client_secret).strip())

    code = getattr(body, "code", None)
    if code:
        client_id = row.get("client_id") or existing.get("client_id")
        secret = (decrypt(row.get("client_secret_enc") or existing.get("client_secret_enc") or "")
                  or "")
        redirect_uri = str(getattr(body, "redirect_uri", "") or "").strip()
        if not (client_id and secret and redirect_uri):
            raise HTTPException(400, "client_id, client_secret and redirect_uri are all required to "
                                     "complete the Google link.")
        try:
            tok = G.exchange_code(client_id, secret, str(code).strip(), redirect_uri)
        except G.SdmError as e:
            raise HTTPException(400, str(e))
        row["refresh_token_enc"] = encrypt(tok["refresh_token"])
        row["scopes"] = tok.get("scope") or G.SDM_SCOPE
        row["status"] = "ok"
        row["last_ok_at"] = _iso(_now())
        row["last_error"] = None
    elif not existing.get("refresh_token_enc"):
        row["status"] = "needs_setup"

    try:
        sb().table("vision_credential").upsert(row, on_conflict="org_id,provider").execute()
    except Exception as e:
        raise HTTPException(400, f"Could not save the Google credential: {str(e)[:200]}")
    _audit(org_id, actor, "google_link" if code else "google_config", "vision_credential",
           {"project_id": row.get("project_id") or existing.get("project_id")})
    cred = (_rows("vision_credential", org_id, provider="google_sdm") or [None])[0] or {}
    return {"linked": bool(cred.get("refresh_token_enc")), "status": cred.get("status"),
            "project_id": cred.get("project_id")}


@router.delete("/google/link")
def google_unlink(org_id: str = ORG_ID, authorization: str = Header(default=""),
                  x_active_org: str = Header(default="")):
    """Drop the stored grant. Cameras and their history are KEPT — unlinking is how an operator stops
    live access without throwing away the traffic history they have been reporting on."""
    caller = _require_caller(authorization, x_active_org)
    _require_settings(caller)
    try:
        sb().table("vision_credential").update(
            {"refresh_token_enc": None, "client_secret_enc": None, "status": "revoked",
             "updated_at": _iso(_now())}).eq("org_id", org_id).eq("provider", "google_sdm").execute()
    except Exception as e:
        raise HTTPException(400, f"Could not unlink: {str(e)[:200]}")
    _audit(org_id, caller.get("email"), "google_unlink", "vision_credential")
    return {"linked": False, "status": "revoked"}


def _sdm_client(org_id: str) -> G.SdmClient:
    cred = (_rows("vision_credential", org_id, provider="google_sdm") or [None])[0]
    if not cred or not cred.get("refresh_token_enc"):
        raise HTTPException(428, "This company's Google account is not linked yet. "
                                 "Vision → Settings → Connect Google.")
    return G.SdmClient({
        "project_id": cred.get("project_id"),
        "client_id": cred.get("client_id"),
        "client_secret": decrypt(cred.get("client_secret_enc") or ""),
        "refresh_token": decrypt(cred.get("refresh_token_enc") or ""),
    })


def _sdm_fail(org_id, e: G.SdmError):
    """Record the failure on the credential row so the settings page can show it, then translate it.
    A 401/403 from Google is a re-authorization, not a retry, and saying so saves a support call."""
    try:
        sb().table("vision_credential").update(
            {"status": "error", "last_error": str(e)[:500], "last_error_at": _iso(_now())}
        ).eq("org_id", org_id).eq("provider", "google_sdm").execute()
    except Exception:
        pass
    if e.status in (401, 403):
        return HTTPException(428, "Google rejected this company's authorization — it was revoked or "
                                  "expired. Reconnect Google in Vision → Settings.")
    return HTTPException(502, f"Google returned an error: {str(e)[:200]}")


# ══════════════════════════════════════════════════════════════════════════════════════════════
# Cameras + zones
# ══════════════════════════════════════════════════════════════════════════════════════════════
@router.get("/structures")
def list_structures(org_id: str = ORG_ID, authorization: str = Header(default=""),
                    x_active_org: str = Header(default="")):
    """Every home on the linked Google account, and which of them this company has claimed.

    A Device Access grant is per Google ACCOUNT, and one account routinely owns several homes. This
    is the screen that answers "which of these four homes is THIS company's" — and until it is
    answered, camera sync imports nothing, by design."""
    caller = _require_caller(authorization, x_active_org)
    _require_settings(caller)
    cfg = _cfg(org_id)
    _require_module(cfg)

    assigned = {str(r.get("structure_id")): r for r in _rows("vision_structure", org_id)}
    try:
        homes = _sdm_client(org_id).list_structures()
    except G.SdmError as e:
        raise _sdm_fail(org_id, e)

    # Homes claimed by ANOTHER company on this platform are reported as taken rather than offered.
    # Two tenants sharing one Google account is legitimate (a franchisee and their own house); two
    # tenants claiming the SAME home is not, and finding out at sync time would be far too late.
    taken = {}
    try:
        for r in (sb().table("vision_structure").select("structure_id,org_id")
                  .neq("org_id", org_id).execute().data) or []:
            taken[str(r.get("structure_id"))] = True
    except Exception:
        pass

    out = []
    for h in homes:
        sid = h["structure_id"]
        row = assigned.get(sid)
        out.append({
            "structure_id": sid,
            "structure_name": h["structure_name"],
            "assigned": bool(row),
            "enabled": bool(row.get("enabled")) if row else False,
            "default_store_code": (row or {}).get("default_store_code"),
            "claimed_by_another_company": sid in taken and not row,
        })
    out.sort(key=lambda h: (not h["assigned"], (h["structure_name"] or "").lower()))
    return {"structures": out,
            "unassigned_count": sum(1 for h in out if not h["assigned"])}


@router.put("/structures")
def put_structures(body: StructuresIn, org_id: str = ORG_ID,
                   authorization: str = Header(default=""), x_active_org: str = Header(default="")):
    """Claim (or release) homes for this company.

    Whole-set replace: what you send IS this company's home list. A home dropped from the list is
    released — its cameras stop syncing, but the cameras themselves and their history are KEPT, so
    releasing a home by accident is recoverable rather than destructive.

    A home already claimed by a DIFFERENT company is refused outright. That check is the whole point
    of the feature; letting it through would recreate the leak this closes."""
    caller = _require_caller(authorization, x_active_org)
    _require_settings(caller)
    _require_module(_cfg(org_id))
    items = getattr(body, "structures", None)
    if not isinstance(items, list):
        raise HTTPException(400, "structures must be a list.")

    try:
        taken = {str(r.get("structure_id")) for r in
                 ((sb().table("vision_structure").select("structure_id,org_id")
                   .neq("org_id", org_id).execute().data) or [])}
    except Exception:
        taken = set()

    rows, actor = [], (caller.get("email") or "unknown")
    for i, it in enumerate(items):
        if not isinstance(it, dict) or not str(it.get("structure_id") or "").strip():
            raise HTTPException(400, f"Home {i + 1} is missing a structure_id.")
        sid = str(it["structure_id"]).strip()
        if sid in taken:
            raise HTTPException(409, f"The home '{it.get('structure_name') or sid}' is already "
                                     "connected to another company on this platform. Release it "
                                     "there first.")
        rows.append({"org_id": org_id, "structure_id": sid,
                     "structure_name": (it.get("structure_name") or sid)[:160],
                     "enabled": bool(it.get("enabled", True)),
                     "default_store_code": (str(it.get("default_store_code") or "").strip() or None),
                     "assigned_by": actor, "assigned_at": _iso(_now()),
                     "updated_at": _iso(_now())})

    keep = {r["structure_id"] for r in rows}
    try:
        for existing in _rows("vision_structure", org_id):
            if str(existing.get("structure_id")) not in keep:
                sb().table("vision_structure").delete().eq("id", existing["id"]).execute()
        if rows:
            sb().table("vision_structure").upsert(rows, on_conflict="org_id,structure_id").execute()
    except Exception as e:
        raise HTTPException(400, f"Could not save the home assignments: {str(e)[:200]}")
    _audit(org_id, actor, "structures_assigned", None,
           {"homes": [{"id": r["structure_id"], "name": r["structure_name"],
                       "enabled": r["enabled"]} for r in rows]})
    return {"structures": _rows("vision_structure", org_id)}


@router.post("/cameras/sync")
def sync_cameras(org_id: str = ORG_ID, authorization: str = Header(default=""),
                 x_active_org: str = Header(default="")):
    """Pull the device list from Google and reconcile it into core.vision_camera.

    ADDITIVE ONLY. A device that disappears from Google is marked offline, never deleted — a camera
    unplugged for a week must not take its store's traffic history with it, and the operator's store
    assignment / zone drawings must survive the outage. New devices arrive DISABLED for analytics
    until an operator assigns them to a store, which is what stops a camera someone adds at home from
    quietly joining a store's numbers."""
    caller = _require_caller(authorization, x_active_org)
    _require_settings(caller)
    cfg = _cfg(org_id)
    _require_module(cfg)
    client = _sdm_client(org_id)
    try:
        devices = client.list_devices()
    except G.SdmError as e:
        raise _sdm_fail(org_id, e)

    # FAIL CLOSED ON UNCLAIMED HOMES. One Google account can own several homes; only the ones this
    # company has explicitly claimed may contribute cameras. A home nobody has assigned — including
    # a fifth home added in the Google Home app tomorrow — imports nothing and is REPORTED, so the
    # operator sees "3 cameras skipped: 1 home not connected" instead of silently missing a store.
    homes = {str(r.get("structure_id")): r for r in _rows("vision_structure", org_id)
             if r.get("enabled")}
    try:
        home_names = {h["structure_id"]: h["structure_name"] for h in client.list_structures()}
    except G.SdmError:
        home_names = {}

    known = {c["device_name"]: c for c in _rows("vision_camera", org_id)}
    added = updated = 0
    skipped_homes = {}
    for d in devices:
        sid = d.get("structure_id") or ""
        if sid not in homes:
            label = home_names.get(sid) or (sid or "unknown home")
            skipped_homes[label] = skipped_homes.get(label, 0) + 1
            continue
        row = {
            "org_id": org_id, "device_name": d["device_name"], "device_type": d["device_type"],
            "display_name": d["display_name"], "room": d.get("room"),
            "structure_id": sid, "structure_name": home_names.get(sid) or homes[sid].get("structure_name"),
            "stream_protocol": d["stream_protocol"], "supports_audio": d["supports_audio"],
            "status": "online", "last_seen_at": _iso(_now()), "updated_at": _iso(_now()),
        }
        if d["device_name"] not in known:
            # A brand-new camera contributes nothing until a human places it — except that a home
            # carrying a default store code pre-fills it, which is the difference between an
            # operator assigning four cameras and assigning forty.
            row.update({"enabled": True, "analytics_enabled": False, "audio_enabled": False,
                        "is_entrance": False,
                        "store_code": homes[sid].get("default_store_code")})
            added += 1
        else:
            updated += 1
        try:
            sb().table("vision_camera").upsert(row, on_conflict="org_id,device_name").execute()
        except Exception:
            pass

    seen = {d["device_name"] for d in devices if (d.get("structure_id") or "") in homes}
    for name, cam in known.items():
        if name not in seen and cam.get("status") != "offline":
            try:
                sb().table("vision_camera").update({"status": "offline", "updated_at": _iso(_now())}
                                                   ).eq("id", cam["id"]).execute()
            except Exception:
                pass
    try:
        sb().table("vision_credential").update(
            {"status": "ok", "last_ok_at": _iso(_now()), "last_error": None}
        ).eq("org_id", org_id).eq("provider", "google_sdm").execute()
    except Exception:
        pass
    _audit(org_id, caller.get("email"), "camera_sync", None,
           {"found": len(devices), "added": added, "updated": updated,
            "skipped_unclaimed_homes": skipped_homes})
    return {"found": len(devices), "added": added, "updated": updated,
            "offline": len(known) - len(seen & set(known)),
            "skipped": sum(skipped_homes.values()),
            "skipped_homes": skipped_homes,
            "cameras": _visible_cameras(org_id, authorization)}


def _visible_cameras(org_id, authorization):
    keyset = _keyset(authorization, org_id)
    return [c for c in _rows("vision_camera", org_id)
            if _in_keyset(keyset, c.get("store_code")) or not (c.get("store_code") or "").strip()]


@router.get("/cameras")
def list_cameras(org_id: str = ORG_ID, authorization: str = Header(default=""),
                 x_active_org: str = Header(default="")):
    """The tenant's cameras, filtered to the caller's reporting span. A camera with no store assigned
    is visible to anyone who can see the page — it is by definition not yet anyone's store's camera,
    and hiding it would leave it un-assignable."""
    _require_caller(authorization, x_active_org)
    cams = sorted(_visible_cameras(org_id, authorization),
                  key=lambda c: ((c.get("store_code") or "~"), (c.get("label") or
                                                                c.get("display_name") or "")))
    return {"cameras": cams, "config": _cfg(org_id)}


@router.patch("/cameras/{camera_id}")
def patch_camera(camera_id: str, body: CameraPatchIn, org_id: str = ORG_ID,
                 authorization: str = Header(default=""), x_active_org: str = Header(default="")):
    """Assign a camera to a store and set its per-camera switches.

    Turning `audio_enabled` on is recorded in the audit log with the actor: it is the switch that
    starts a store recording its own staff, and "who turned this on and when" must have an answer."""
    caller = _require_caller(authorization, x_active_org)
    _require_settings(caller)
    cam = _camera(org_id, camera_id)
    patch = {"updated_at": _iso(_now())}
    for k in ("label", "store_code"):
        v = getattr(body, k, None)
        if v is not None:
            patch[k] = str(v).strip() or None
    for k in ("analytics_enabled", "audio_enabled", "is_entrance", "enabled"):
        v = getattr(body, k, None)
        if v is not None:
            patch[k] = bool(v)
    proto = getattr(body, "stream_protocol", None)
    if proto is not None:
        proto = str(proto).strip().lower()
        if proto not in ("webrtc", "rtsp"):
            raise HTTPException(400, "stream_protocol must be 'webrtc' or 'rtsp'.")
        patch["stream_protocol"] = proto
    if patch.get("audio_enabled") and not cam.get("supports_audio"):
        raise HTTPException(400, "This device does not report a microphone, so audio analytics "
                                 "cannot be enabled for it.")
    try:
        sb().table("vision_camera").update(patch).eq("org_id", org_id).eq("id", camera_id).execute()
    except Exception as e:
        raise HTTPException(400, f"Could not update the camera: {str(e)[:200]}")
    if patch.get("audio_enabled") and not cam.get("audio_enabled"):
        _audit(org_id, caller.get("email"), "camera_audio_on", cam.get("device_name"),
               {"store_code": patch.get("store_code") or cam.get("store_code")})
    return {"camera": _camera(org_id, camera_id)}


def _camera(org_id, camera_id) -> dict:
    rows = _rows("vision_camera", org_id, id=camera_id)
    if not rows:
        raise HTTPException(404, "Camera not found.")
    return rows[0]


@router.get("/cameras/{camera_id}/zones")
def get_zones(camera_id: str, org_id: str = ORG_ID, authorization: str = Header(default=""),
              x_active_org: str = Header(default="")):
    _require_caller(authorization, x_active_org)
    _camera(org_id, camera_id)
    return {"zones": sorted(_rows("vision_zone", org_id, camera_id=camera_id),
                            key=lambda z: (z.get("sort_order") or 100, z.get("name") or ""))}


@router.put("/cameras/{camera_id}/zones")
def put_zones(camera_id: str, body: ZonesIn, org_id: str = ORG_ID,
              authorization: str = Header(default=""), x_active_org: str = Header(default="")):
    """Replace this camera's zone set. Whole-set replace rather than per-zone edits because the UI is
    a drawing surface — an operator drags three shapes and saves, and a partial-update API would turn
    that into a diff nobody asked for.

    Geometry is validated HERE, not at read time: a line needs two distinct points and a polygon needs
    three, and a malformed shape saved now becomes a silently-uncounted doorway later."""
    caller = _require_caller(authorization, x_active_org)
    _require_settings(caller)
    _camera(org_id, camera_id)
    zones = getattr(body, "zones", None) or []
    if not isinstance(zones, list):
        raise HTTPException(400, "zones must be a list.")

    from app.modules.vision import geometry as GEO
    clean = []
    for i, z in enumerate(zones):
        if not isinstance(z, dict):
            raise HTTPException(400, f"Zone {i + 1} is malformed.")
        kind = (z.get("kind") or "polygon").strip().lower()
        if kind not in ("line", "polygon", "exclude"):
            raise HTTPException(400, f"Zone {i + 1}: kind must be line, polygon or exclude.")
        geom = z.get("geometry") or {}
        if kind == "line":
            if not GEO.line_points(geom):
                raise HTTPException(400, f"Zone {i + 1}: a counting line needs two distinct points.")
        elif not GEO.polygon_points(geom):
            raise HTTPException(400, f"Zone {i + 1}: a zone needs at least three points.")
        clean.append({
            "org_id": org_id, "camera_id": camera_id, "kind": kind,
            "name": (z.get("name") or f"Zone {i + 1}")[:120],
            "zone_key": (z.get("zone_key") or z.get("name") or f"zone_{i + 1}"
                         ).strip().lower().replace(" ", "_")[:60],
            "geometry": geom,
            "inward": (z.get("inward") or "left").strip().lower(),
            "is_active": bool(z.get("is_active", True)),
            "sort_order": int(z.get("sort_order") or (i + 1) * 10),
            "updated_at": _iso(_now()),
        })
    try:
        sb().table("vision_zone").delete().eq("org_id", org_id).eq("camera_id", camera_id).execute()
        if clean:
            sb().table("vision_zone").insert(clean).execute()
    except Exception as e:
        raise HTTPException(400, f"Could not save the zones: {str(e)[:200]}")
    _audit(org_id, caller.get("email"), "zones_saved", camera_id, {"count": len(clean)})
    return {"zones": _rows("vision_zone", org_id, camera_id=camera_id)}


# ══════════════════════════════════════════════════════════════════════════════════════════════
# Live stream — the "pull the feed in live mode" path
# ══════════════════════════════════════════════════════════════════════════════════════════════
@router.post("/cameras/{camera_id}/stream")
def open_stream(camera_id: str, body: StreamIn, org_id: str = ORG_ID,
                authorization: str = Header(default=""), x_active_org: str = Header(default="")):
    """Broker a live stream grant for one camera.

    WebRTC (every modern Nest camera): the browser creates an RTCPeerConnection, hands us its SDP
    OFFER, and gets Google's SDP ANSWER back. The media then flows browser↔Google directly — this
    backend never sees a video frame, which is both the only architecture that scales and the one
    that keeps store video out of a shared API process.

    RTSP (older wired cameras): returns a tokenized URL. That URL is a bearer credential for the
    camera and is treated as one — it is returned once to the caller, stored only in encrypted form,
    and is never included in any list endpoint.

    Every issue is written to core.vision_stream_session first, so "who watched this camera" is
    answerable even if the viewer's browser then fails to connect."""
    caller = _require_caller(authorization, x_active_org)
    cfg = _cfg(org_id)
    _require_module(cfg, "live_view")
    cam = _camera(org_id, camera_id)
    if not cam.get("enabled"):
        raise HTTPException(403, "This camera is disabled.")
    keyset = _keyset(authorization, org_id)
    if not _in_keyset(keyset, cam.get("store_code")):
        raise HTTPException(403, "This camera belongs to a store outside your access.")

    protocol = (cam.get("stream_protocol") or "webrtc").strip().lower()
    offer = getattr(body, "offer_sdp", None)
    if protocol == "webrtc" and not offer:
        raise HTTPException(400, "This camera streams over WebRTC — send the browser's SDP offer as "
                                 "offer_sdp.")
    client = _sdm_client(org_id)
    try:
        res = client.generate_stream(cam["device_name"], protocol, str(offer) if offer else None)
    except G.SdmError as e:
        raise _sdm_fail(org_id, e)

    token = res.get("media_session_id") or res.get("stream_extension_token")
    expires = G.parse_expiry(res.get("expires_at"))
    session = {
        "org_id": org_id, "camera_id": camera_id, "device_name": cam["device_name"],
        "store_code": cam.get("store_code"), "protocol": protocol,
        "viewer_email": caller.get("email"), "viewer_role": caller.get("role"),
        "purpose": (getattr(body, "purpose", None) or "live_view"),
        "extension_token_enc": encrypt(token or ""),
        "issued_at": _iso(_now()), "expires_at": _iso(expires),
    }
    try:
        row = sb().table("vision_stream_session").insert(session).execute().data or []
        session_id = row[0]["id"] if row else None
    except Exception as e:
        raise HTTPException(400, f"Could not record the stream session: {str(e)[:200]}")

    _audit(org_id, caller.get("email"), "stream_issued", cam.get("device_name"),
           {"store_code": cam.get("store_code"), "protocol": protocol})
    out = {"session_id": session_id, "protocol": protocol, "expires_at": _iso(expires),
           "extend_after_seconds": max(30, int((expires - _now()).total_seconds()) - 60),
           "max_minutes": cfg.get("stream_max_minutes")}
    if protocol == "webrtc":
        out["answer_sdp"] = res.get("answer_sdp")
    else:
        out["rtsp_url"] = res.get("rtsp_url")
    return out


@router.post("/stream/{session_id}/extend")
def extend_stream(session_id: str, org_id: str = ORG_ID, authorization: str = Header(default=""),
                  x_active_org: str = Header(default="")):
    """Push a live grant's expiry out. The client calls this ~60s before `expires_at`.

    The `stream_max_minutes` ceiling is enforced here rather than client-side: a viewer who walks away
    with the tab open would otherwise hold a store's camera open indefinitely, and the person on the
    other end of that camera is an employee who has a reasonable interest in that not happening."""
    caller = _require_caller(authorization, x_active_org)
    cfg = _cfg(org_id)
    _require_module(cfg, "live_view")
    rows = _rows("vision_stream_session", org_id, id=session_id)
    if not rows:
        raise HTTPException(404, "Stream session not found.")
    s = rows[0]
    if s.get("stopped_at"):
        raise HTTPException(409, "That stream was already stopped.")
    if (s.get("viewer_email") or "") != (caller.get("email") or "") and not caller.get("super_admin"):
        raise HTTPException(403, "That stream belongs to another viewer.")

    issued = G.parse_expiry(s.get("issued_at"))
    if _now() - issued > timedelta(minutes=int(cfg.get("stream_max_minutes") or 30)):
        _stop(org_id, s, caller.get("email"), reason="max_duration")
        raise HTTPException(409, "This live view reached the company's maximum session length. "
                                 "Start it again if you still need it.")

    cam = _camera(org_id, s["camera_id"]) if s.get("camera_id") else {}
    client = _sdm_client(org_id)
    try:
        res = client.extend_stream(s.get("device_name") or cam.get("device_name"),
                                   s.get("protocol"), decrypt(s.get("extension_token_enc") or ""))
    except G.SdmError as e:
        raise _sdm_fail(org_id, e)

    expires = G.parse_expiry(res.get("expires_at"))
    # RTSP hands back a NEW extension token every time; storing it is what makes the second extension
    # work. WebRTC keeps the same mediaSessionId, so this is a no-op there.
    new_token = res.get("stream_extension_token") or res.get("media_session_id")
    patch = {"expires_at": _iso(expires), "extended_count": int(s.get("extended_count") or 0) + 1}
    if new_token:
        patch["extension_token_enc"] = encrypt(new_token)
    try:
        sb().table("vision_stream_session").update(patch).eq("id", session_id).execute()
    except Exception:
        pass
    out = {"session_id": session_id, "expires_at": _iso(expires),
           "extend_after_seconds": max(30, int((expires - _now()).total_seconds()) - 60)}
    if res.get("rtsp_url"):
        out["rtsp_url"] = res["rtsp_url"]
    return out


@router.post("/stream/{session_id}/stop")
def stop_stream(session_id: str, org_id: str = ORG_ID, authorization: str = Header(default=""),
                x_active_org: str = Header(default="")):
    """Hand the grant back when the viewer closes the tab. Never fails: if Google has already expired
    the session there is nothing to release, and telling the caller their close button errored would
    be noise."""
    caller = _require_caller(authorization, x_active_org)
    rows = _rows("vision_stream_session", org_id, id=session_id)
    if not rows:
        raise HTTPException(404, "Stream session not found.")
    # Same ownership rule as /extend: your own session, or a manager's override. Stopping someone
    # else's live view is a small thing, but "any signed-in user can cut any camera" is the kind of
    # small thing that turns into an incident report.
    if (rows[0].get("viewer_email") or "") != (caller.get("email") or "") and not _is_manager(caller):
        raise HTTPException(403, "That stream belongs to another viewer.")
    _stop(org_id, rows[0], caller.get("email"))
    return {"stopped": True}


def _stop(org_id, session, actor, reason=None):
    try:
        client = _sdm_client(org_id)
        client.stop_stream(session.get("device_name"), session.get("protocol"),
                           decrypt(session.get("extension_token_enc") or ""))
    except Exception:
        pass
    try:
        sb().table("vision_stream_session").update(
            {"stopped_at": _iso(_now()), "extension_token_enc": None}).eq("id", session["id"]).execute()
    except Exception:
        pass
    _audit(org_id, actor, "stream_stopped", session.get("device_name"), {"reason": reason})


@router.get("/stream-sessions")
def list_stream_sessions(org_id: str = ORG_ID, limit: int = 100,
                         authorization: str = Header(default=""),
                         x_active_org: str = Header(default="")):
    """The live-view audit trail: who watched which camera, when, for how long. Manager-gated, and
    deliberately easy to reach — a store's staff being able to ask "who has been watching us" and get
    a real answer is what separates this from surveillance."""
    caller = _require_caller(authorization, x_active_org)
    _require_manager(caller, "the camera viewing log")
    try:
        rows = (sb().table("vision_stream_session")
                .select("id,camera_id,device_name,store_code,protocol,viewer_email,viewer_role,"
                        "purpose,issued_at,expires_at,extended_count,stopped_at")
                .eq("org_id", org_id).order("issued_at", desc=True)
                .limit(max(1, min(500, limit))).execute().data) or []
    except Exception:
        rows = []
    keyset = _keyset(authorization, org_id)
    return {"sessions": [r for r in rows if _in_keyset(keyset, r.get("store_code"))]}


# ══════════════════════════════════════════════════════════════════════════════════════════════
# Edge analyzer registration
# ══════════════════════════════════════════════════════════════════════════════════════════════
@router.get("/edge-agents")
def list_agents(org_id: str = ORG_ID, authorization: str = Header(default=""),
                x_active_org: str = Header(default="")):
    _require_settings(_require_caller(authorization, x_active_org))
    rows = _rows("vision_edge_agent", org_id,
                 cols="id,agent_key,label,store_code,enabled,version,last_seen_at,last_ingest_at,"
                      "events_received,rotated_at,created_at")
    return {"agents": [{**a, "online": _fresh(a.get("last_seen_at"), minutes=10)} for a in rows]}


@router.post("/edge-agents")
def create_agent(body: AgentIn, org_id: str = ORG_ID, authorization: str = Header(default=""),
                 x_active_org: str = Header(default="")):
    """Register an analyzer node and mint its signing secret.

    The secret is returned EXACTLY ONCE, here. It is stored encrypted and there is no read-back
    endpoint anywhere — a lost secret is rotated, not recovered. That is the same posture the rest of
    the platform takes with credentials, and it is the one that makes an accidental screenshot of an
    admin page a non-event."""
    caller = _require_caller(authorization, x_active_org)
    _require_settings(caller)
    _require_module(_cfg(org_id))
    agent_key = "va_" + secrets.token_hex(8)
    secret = secrets.token_urlsafe(32)
    row = {"org_id": org_id, "agent_key": agent_key,
           "label": (getattr(body, "label", None) or "Store analyzer")[:120],
           "store_code": (str(getattr(body, "store_code", "") or "").strip() or None),
           "secret_enc": encrypt(secret), "enabled": True}
    try:
        sb().table("vision_edge_agent").insert(row).execute()
    except Exception as e:
        raise HTTPException(400, f"Could not register the analyzer: {str(e)[:200]}")
    _audit(org_id, caller.get("email"), "agent_registered", agent_key,
           {"store_code": row["store_code"]})
    return {"agent_key": agent_key, "secret": secret, "store_code": row["store_code"],
            "note": "Copy this secret now — it is stored encrypted and cannot be shown again."}


@router.post("/edge-agents/{agent_id}/rotate")
def rotate_agent(agent_id: str, org_id: str = ORG_ID, authorization: str = Header(default=""),
                 x_active_org: str = Header(default="")):
    """Issue a new secret for an existing analyzer. The old one stops working immediately — that is
    the point of a rotation, and an analyzer that keeps posting on the old secret is exactly the
    signal an operator wants after a suspected compromise."""
    caller = _require_caller(authorization, x_active_org)
    _require_settings(caller)
    rows = _rows("vision_edge_agent", org_id, id=agent_id)
    if not rows:
        raise HTTPException(404, "Analyzer not found.")
    secret = secrets.token_urlsafe(32)
    try:
        sb().table("vision_edge_agent").update(
            {"secret_enc": encrypt(secret), "rotated_at": _iso(_now())}).eq("id", agent_id).execute()
    except Exception as e:
        raise HTTPException(400, f"Could not rotate: {str(e)[:200]}")
    _audit(org_id, caller.get("email"), "agent_rotated", rows[0].get("agent_key"))
    return {"agent_key": rows[0].get("agent_key"), "secret": secret,
            "note": "The previous secret stopped working. Update the analyzer now."}


@router.delete("/edge-agents/{agent_id}")
def delete_agent(agent_id: str, org_id: str = ORG_ID, authorization: str = Header(default=""),
                 x_active_org: str = Header(default="")):
    caller = _require_caller(authorization, x_active_org)
    _require_settings(caller)
    try:
        sb().table("vision_edge_agent").delete().eq("org_id", org_id).eq("id", agent_id).execute()
    except Exception as e:
        raise HTTPException(400, f"Could not remove the analyzer: {str(e)[:200]}")
    _audit(org_id, caller.get("email"), "agent_removed", agent_id)
    return {"removed": True}


# ══════════════════════════════════════════════════════════════════════════════════════════════
# THE EDGE SURFACE — no JWT. HMAC over the raw body. Resolves its own org from the agent record.
#
# These three routes sit under /api/v1/vision/edge, which is on the tenant-middleware public-prefix
# allowlist for the same reason /core/fix-pipeline is: the caller is a machine with no login, and the
# JWT requirement would fire before the handler could check the signature it DOES carry. Allowlisting
# also skips the org_id rewrite, so every one of them derives org_id from the authenticated agent and
# ignores anything the request says about which tenant it is.
# ══════════════════════════════════════════════════════════════════════════════════════════════
async def _authenticate_agent(request: Request):
    """(agent_row, org_id, raw_body). Raises 401 on anything unproven.

    Failure modes are deliberately indistinguishable to the caller — unknown agent, disabled agent,
    bad signature and stale timestamp all return the same 401 — so a probe with a guessed agent_key
    learns nothing about whether that key exists."""
    raw = await request.body()
    agent_key = (request.headers.get("x-vision-agent") or "").strip()
    signature = (request.headers.get("x-vision-signature") or "").strip()
    timestamp = (request.headers.get("x-vision-timestamp") or "").strip()
    deny = HTTPException(401, "Analyzer authentication failed.")
    if not agent_key:
        raise deny
    try:
        rows = (sb().table("vision_edge_agent").select("*").eq("agent_key", agent_key)
                .limit(1).execute().data) or []
    except Exception:
        raise HTTPException(503, "Analyzer registry unavailable.")
    if not rows or not rows[0].get("enabled"):
        raise deny
    agent = rows[0]
    ok, _reason = I.verify(decrypt(agent.get("secret_enc") or ""), timestamp, raw, signature)
    if not ok:
        raise deny
    return agent, agent.get("org_id"), raw


@router.post("/edge/heartbeat")
async def edge_heartbeat(request: Request):
    """The analyzer says it is alive and reports its version. Also the cheapest way for an installer
    to confirm the secret is right before they debug anything else."""
    agent, org_id, raw = await _authenticate_agent(request)
    try:
        body = json.loads(raw or b"{}")
    except Exception:
        body = {}
    try:
        sb().table("vision_edge_agent").update(
            {"last_seen_at": _iso(_now()), "version": str(body.get("version") or "")[:40]}
        ).eq("id", agent["id"]).execute()
    except Exception:
        pass
    cfg = _cfg(org_id)
    return {"ok": True, "enabled": cfg.get("enabled"), "agent_key": agent.get("agent_key"),
            "store_code": agent.get("store_code")}


@router.get("/edge/config")
async def edge_config(request: Request):
    """Everything the analyzer needs to do its job, and nothing else.

    Cameras it may analyze (its own store only), their zones, the heat grid, and — critically — the
    THREE boolean answers that decide what it is allowed to compute: traffic, heatmap, audio. The
    analyzer re-fetches this on a short cycle, so an operator flipping audio off in the UI stops the
    microphone at the edge within a minute rather than merely stopping the storage here. Consent is
    resolved server-side and returned as a plain allowlist of employee ids, so the analyzer never
    holds a consent state it could get wrong."""
    agent, org_id, _raw = await _authenticate_agent(request)
    cfg = _cfg(org_id)
    cams = _rows("vision_camera", org_id)
    if agent.get("store_code"):
        cams = [c for c in cams if (c.get("store_code") or "") == agent["store_code"]]
    cams = [c for c in cams if c.get("enabled")]
    zones = _rows("vision_zone", org_id)
    zones_by_cam = {}
    for z in zones:
        zones_by_cam.setdefault(z.get("camera_id"), []).append(z)

    consented, on_duty = [], []
    if C.feature_enabled(cfg, "audio_analytics"):
        for row in _rows("vision_consent", org_id, scope="audio"):
            allowed, _r = C.consent_ok(cfg, row)
            if allowed:
                consented.append(str(row.get("employee_id")))
        on_duty = _on_duty(org_id, agent.get("store_code"), set(consented))

    return {
        "org_id": org_id,
        "store_code": agent.get("store_code"),
        "features": {
            "traffic": C.feature_enabled(cfg, "traffic"),
            "heatmap": C.feature_enabled(cfg, "heatmap"),
            "audio_analytics": C.feature_enabled(cfg, "audio_analytics"),
        },
        "grid": {"cols": cfg.get("grid_cols"), "rows": cfg.get("grid_rows")},
        "cameras": [{
            "device_name": c.get("device_name"),
            "camera_id": c.get("id"),
            "store_code": c.get("store_code"),
            # The STORE's own IANA zone, resolved server-side through the platform's existing ladder
            # (stores.timezone -> tenant default -> house default). Sent per camera rather than
            # configured on the analyzer, because one analyzer can legitimately serve stores in
            # DIFFERENT zones — see _tz_name(). Getting this wrong does not fail loudly; it files the
            # evening rush under the wrong business date, which nobody notices until a report is
            # compared against the POS.
            "timezone": _tz_name(org_id, c.get("store_code")),
            "stream_protocol": c.get("stream_protocol"),
            "analytics": bool(c.get("analytics_enabled")),
            "is_entrance": bool(c.get("is_entrance")),
            "audio": bool(c.get("audio_enabled") and c.get("supports_audio")
                          and C.feature_enabled(cfg, "audio_analytics")),
            "zones": [{"kind": z.get("kind"), "zone_key": z.get("zone_key"), "name": z.get("name"),
                       "geometry": z.get("geometry"), "inward": z.get("inward"),
                       "is_active": z.get("is_active"), "sort_order": z.get("sort_order")}
                      for z in zones_by_cam.get(c.get("id"), [])],
        } for c in cams],
        "consented_employee_ids": consented,
        # WHO A TRANSCRIPT BELONGS TO. The analyzer cannot answer this from the audio: telling
        # employees apart by voice would need enrolled voiceprints, which are biometric data this
        # module deliberately does not collect (migration 900 header). So attribution comes from the
        # thing the platform already knows for certain — who is CLOCKED IN at this store right now.
        # `attribution` is 'unambiguous' when exactly ONE consented employee is on duty, in which
        # case the analyzer stamps that employee_id on the segments. Any other count is 'ambiguous'
        # and the analyzer must send nothing: an unattributed segment is refused at ingest anyway
        # (reject reason `no_employee`), and guessing between two people on duty would put words in
        # someone's coaching record that they did not say.
        "on_duty": on_duty,
        "attribution": "unambiguous" if len(on_duty) == 1 else "ambiguous",
        "poll_seconds": 60,
    }


def _tz_name(org_id, store_code) -> str:
    """IANA zone name for a store, via storeops' canonical resolver. Falls back to UTC only if that
    whole path is unavailable — and the analyzer then falls back to its own --tz-offset, so a store
    is never left silently filing events under an arbitrary zone."""
    try:
        from app.modules.storeops.router import _biz_tz_for_store
        return str(getattr(_biz_tz_for_store(org_id, store_code), "key", "") or "UTC")
    except Exception:
        return "UTC"


def _on_duty(org_id, store_code, consented_ids):
    """The employees currently clocked in at this store who have consented to audio.

    Two id vocabularies meet here: storeops.timelog.employee_id is the human-readable TEXT staff code,
    while vision_consent.employee_id is the employees table's UUID. Joining them through
    storeops.employees is the whole reason this is a function and not an inline query — getting it
    wrong would silently attribute every segment to nobody (and reject the lot)."""
    if not store_code:
        return []
    try:
        open_punches = (get_supabase().schema("storeops").table("timelog")
                        .select("employee_id,employee_name").eq("org_id", org_id)
                        .eq("store_code", store_code).is_("clock_out", "null")
                        .limit(50).execute().data) or []
    except Exception:
        return []
    codes = [p.get("employee_id") for p in open_punches if p.get("employee_id")]
    if not codes:
        return []
    try:
        emps = (get_supabase().schema("storeops").table("employees")
                .select("id,employee_id,full_name").eq("org_id", org_id)
                .in_("employee_id", codes).limit(50).execute().data) or []
    except Exception:
        return []
    return [{"employee_id": str(e["id"]), "name": e.get("full_name")}
            for e in emps if str(e.get("id")) in consented_ids]


@router.post("/edge/stream")
async def edge_stream(request: Request):
    """The analyzer asks for a live stream grant for one of ITS cameras.

    Same brokering as the operator-facing /cameras/{id}/stream, with three differences that matter:
    the caller is authenticated by HMAC rather than a JWT; the camera must belong to the agent's own
    store AND have analytics enabled (a camera an operator excluded from analytics is not one the
    analyzer may open); and the session is recorded with purpose='analyzer' so the viewing log tells
    a human watching a feed apart from a machine counting on it.

    Body: {"device_name": "...", "offer_sdp": "..."} — the offer is required for a WebRTC camera and
    ignored for an RTSP one. The response carries `extend_after_seconds`: Google's grant lapses in
    about five minutes and the analyzer must call /edge/stream/extend before it does.
    """
    agent, org_id, raw = await _authenticate_agent(request)
    try:
        body = json.loads(raw or b"{}")
    except Exception:
        raise HTTPException(400, "Body must be JSON.")
    cfg = _cfg(org_id)
    if not cfg.get("enabled"):
        raise HTTPException(403, "Camera analytics is turned off for this company.")

    device_name = (body.get("device_name") or "").strip()
    cams = {c["device_name"]: c for c in _rows("vision_camera", org_id)}
    cam = cams.get(device_name)
    if not cam or not cam.get("enabled"):
        raise HTTPException(404, "Unknown camera.")
    if agent.get("store_code") and (cam.get("store_code") or "") != agent["store_code"]:
        raise HTTPException(403, "That camera belongs to another store.")
    if not cam.get("analytics_enabled"):
        raise HTTPException(403, "Analytics is disabled for that camera.")

    protocol = (cam.get("stream_protocol") or "webrtc").strip().lower()
    offer = body.get("offer_sdp")
    if protocol == "webrtc" and not offer:
        raise HTTPException(400, "This camera streams over WebRTC — send an SDP offer as offer_sdp.")
    try:
        res = _sdm_client(org_id).generate_stream(device_name, protocol, offer)
    except G.SdmError as e:
        raise _sdm_fail(org_id, e)

    token = res.get("media_session_id") or res.get("stream_extension_token")
    expires = G.parse_expiry(res.get("expires_at"))
    session_id = None
    try:
        row = sb().table("vision_stream_session").insert({
            "org_id": org_id, "camera_id": cam.get("id"), "device_name": device_name,
            "store_code": cam.get("store_code"), "protocol": protocol,
            "viewer_email": agent.get("agent_key"), "viewer_role": "edge_analyzer",
            "purpose": "analyzer", "extension_token_enc": encrypt(token or ""),
            "issued_at": _iso(_now()), "expires_at": _iso(expires)}).execute().data or []
        session_id = row[0]["id"] if row else None
    except Exception:
        pass
    out = {"session_id": session_id, "protocol": protocol, "expires_at": _iso(expires),
           "extend_after_seconds": max(30, int((expires - _now()).total_seconds()) - 60)}
    if protocol == "webrtc":
        out["answer_sdp"] = res.get("answer_sdp")
    else:
        out["rtsp_url"] = res.get("rtsp_url")
    return out


@router.post("/edge/stream/extend")
async def edge_stream_extend(request: Request):
    """Push an analyzer's grant out before it lapses. Body: {"session_id": "..."}.

    Unlike the operator path there is NO max-session ceiling here: an analyzer holding a stream is
    the feature working as intended, and stopping it after 30 minutes would silently end a store's
    counting mid-afternoon. The operator's ceiling exists to stop a HUMAN leaving a tab open on a
    camera pointed at their staff — a different concern with a different answer."""
    agent, org_id, raw = await _authenticate_agent(request)
    try:
        body = json.loads(raw or b"{}")
    except Exception:
        raise HTTPException(400, "Body must be JSON.")
    rows = _rows("vision_stream_session", org_id, id=(body.get("session_id") or "").strip())
    if not rows or rows[0].get("viewer_email") != agent.get("agent_key"):
        raise HTTPException(404, "Stream session not found.")
    s = rows[0]
    try:
        res = _sdm_client(org_id).extend_stream(s.get("device_name"), s.get("protocol"),
                                                decrypt(s.get("extension_token_enc") or ""))
    except G.SdmError as e:
        raise _sdm_fail(org_id, e)
    expires = G.parse_expiry(res.get("expires_at"))
    new_token = res.get("stream_extension_token") or res.get("media_session_id")
    patch = {"expires_at": _iso(expires), "extended_count": int(s.get("extended_count") or 0) + 1}
    if new_token:
        patch["extension_token_enc"] = encrypt(new_token)
    try:
        sb().table("vision_stream_session").update(patch).eq("id", s["id"]).execute()
    except Exception:
        pass
    out = {"session_id": s["id"], "expires_at": _iso(expires),
           "extend_after_seconds": max(30, int((expires - _now()).total_seconds()) - 60)}
    if res.get("rtsp_url"):
        out["rtsp_url"] = res["rtsp_url"]
    return out


@router.post("/edge/ingest")
async def edge_ingest(request: Request):
    """The analyzer posts a batch of derived events. This is the only write path for camera-derived
    data in the whole platform.

    Every rejection is COUNTED AND RETURNED rather than silently dropped, and the same counts are
    stored on the agent row, because the most likely failure in production is not an attack — it is
    an operator who turned audio on before collecting consent, and who deserves to be told that in
    the settings page instead of discovering an empty report a week later."""
    agent, org_id, raw = await _authenticate_agent(request)
    try:
        payload = json.loads(raw or b"{}")
    except Exception:
        raise HTTPException(400, "Body must be JSON.")

    cfg = _cfg(org_id)
    if not cfg.get("enabled"):
        # Not an error — the operator turned the module off and the analyzer has not re-polled yet.
        return {"accepted": 0, "rejected": {"tenant_disabled": len(payload.get("events") or [])},
                "enabled": False}

    cams = {c["device_name"]: c for c in _rows("vision_camera", org_id)}
    consents = {str(r.get("employee_id")): r for r in _rows("vision_consent", org_id, scope="audio")}
    norm = I.normalize_batch(payload, cams, cfg, consents, org_id, agent)

    # Traffic is UPSERTED against the dedupe index, everything else is a plain insert. The asymmetry
    # is deliberate: the analyzer re-queues a batch whose POST failed, so the same door crossing can
    # legitimately arrive twice, and a double-counted entry is the one error a manager would notice
    # and stop trusting. A presence sample or a transcript segment carries no such natural key —
    # duplicating one nudges an aggregate imperceptibly, and inventing a key to dedupe on would cost
    # more than it saves.
    TRAFFIC_KEY = "org_id,store_code,camera_id,track_key,direction,occurred_at"
    written = {}
    for key, table in (("traffic", "vision_traffic_event"), ("presence", "vision_presence_sample"),
                       ("transcripts", "vision_transcript")):
        rows = norm.get(key) or []
        if not rows:
            written[key] = 0
            continue
        try:
            if key == "traffic":
                sb().table(table).upsert(rows, on_conflict=TRAFFIC_KEY,
                                         ignore_duplicates=True).execute()
            else:
                sb().table(table).insert(rows).execute()
            written[key] = len(rows)
        except Exception as e:
            written[key] = 0
            norm["rejected"][f"{key}_write_failed"] = str(e)[:120]

    try:
        sb().table("vision_edge_agent").update(
            {"last_seen_at": _iso(_now()), "last_ingest_at": _iso(_now()),
             "events_received": int(agent.get("events_received") or 0) + norm["accepted"]}
        ).eq("id", agent["id"]).execute()
    except Exception:
        pass
    return {"accepted": norm["accepted"], "written": written, "rejected": norm["rejected"],
            "enabled": True}


# ══════════════════════════════════════════════════════════════════════════════════════════════
# Analytics — traffic, heat map, behavior
# ══════════════════════════════════════════════════════════════════════════════════════════════
def _date_range(date_from, date_to):
    today = date.today()
    try:
        b = date.fromisoformat(date_to) if date_to else today
    except ValueError:
        b = today
    try:
        a = date.fromisoformat(date_from) if date_from else b
    except ValueError:
        a = b
    if a > b:
        a, b = b, a
    if (b - a).days > 92:
        a = b - timedelta(days=92)     # a wider window is a report export, not a dashboard call
    return a.isoformat(), b.isoformat()


def _scoped_store(caller, authorization, org_id, store_code):
    store = (store_code or caller.get("store_code") or "").strip()
    if not store:
        raise HTTPException(400, "store_code is required.")
    if not _in_keyset(_keyset(authorization, org_id), store):
        raise HTTPException(403, "That store is outside your access.")
    return store


@router.get("/traffic")
def traffic(store_code: str = "", date_from: str = "", date_to: str = "", org_id: str = ORG_ID,
            authorization: str = Header(default=""), x_active_org: str = Header(default="")):
    """Customers in and out: totals, the hourly curve, dwell time, and the visits behind them.

    The pairing runs here in Python rather than in SQL because the rules that matter (an entry with no
    exit is still a visit; a 4-second track is a passerby; an 8-hour one is staff) are exactly the
    rules a tenant will want to tune, and they are provable offline where they live."""
    caller = _require_caller(authorization, x_active_org)
    cfg = _cfg(org_id)
    _require_module(cfg, "traffic")
    store = _scoped_store(caller, authorization, org_id, store_code)
    a, b = _date_range(date_from, date_to)
    try:
        events = (sb().table("vision_traffic_event")
                  .select("occurred_at,local_date,local_hour,direction,track_key,camera_id,store_code")
                  .eq("org_id", org_id).eq("store_code", store)
                  .gte("local_date", a).lte("local_date", b)
                  .order("occurred_at").limit(50000).execute().data) or []
    except Exception:
        events = []

    paired = H.pair_visits(events, int(cfg.get("min_visit_seconds") or 20),
                           int(cfg.get("max_visit_seconds") or 5400))
    summary = H.traffic_summary(events, paired["visits"])
    by_day = {}
    for v in paired["visits"]:
        d = str(v.get("local_date") or "")[:10]
        bucket = by_day.setdefault(d, {"local_date": d, "customers": 0, "passersby": 0, "dwell": []})
        if v.get("classification") == "customer":
            bucket["customers"] += 1
            if v.get("dwell_seconds") is not None:
                bucket["dwell"].append(v["dwell_seconds"])
        elif v.get("classification") == "passerby":
            bucket["passersby"] += 1
    days = [{"local_date": d, "customers": x["customers"], "passersby": x["passersby"],
             "avg_dwell_seconds": round(sum(x["dwell"]) / len(x["dwell"])) if x["dwell"] else None}
            for d, x in sorted(by_day.items())]

    return {"store_code": store, "date_from": a, "date_to": b, "summary": summary, "days": days,
            "filtered": {"short": paired["filtered_short"], "long": paired["filtered_long"],
                         "unpaired_exits": paired["unpaired_exits"]},
            "config": {"min_visit_seconds": cfg.get("min_visit_seconds"),
                       "max_visit_seconds": cfg.get("max_visit_seconds")}}


@router.get("/heatmap")
def heatmap(store_code: str = "", date_from: str = "", date_to: str = "", hours: str = "",
            camera_id: str = "", org_id: str = ORG_ID, authorization: str = Header(default=""),
            x_active_org: str = Header(default="")):
    """The occupancy heat map: person-seconds per grid cell, plus the hot cells and the dead zones.

    `hours` is a comma list (e.g. "17,18,19") so a manager can ask "where do people stand during the
    evening rush", which is a different map from the all-day one and the one that actually changes
    where a display gets placed."""
    caller = _require_caller(authorization, x_active_org)
    cfg = _cfg(org_id)
    _require_module(cfg, "heatmap")
    store = _scoped_store(caller, authorization, org_id, store_code)
    a, b = _date_range(date_from, date_to)
    want_hours = [int(h) for h in hours.split(",") if h.strip().isdigit()] if hours else None

    try:
        q = (sb().table("vision_heat_cell")
             .select("cell_x,cell_y,local_hour,local_date,occupancy,grid_cols,grid_rows,camera_id")
             .eq("org_id", org_id).eq("store_code", store)
             .gte("local_date", a).lte("local_date", b))
        if camera_id:
            q = q.eq("camera_id", camera_id)
        cells = q.limit(100000).execute().data or []
    except Exception:
        cells = []

    # The grid the DATA was recorded on wins over the current config: an operator who changed the
    # resolution last week must still be able to read last month's map, and re-binning old cells into
    # a new grid would invent precision that was never captured.
    cols = int((cells[0].get("grid_cols") if cells else None) or cfg.get("grid_cols") or 24)
    rows = int((cells[0].get("grid_rows") if cells else None) or cfg.get("grid_rows") or 16)
    payload = H.heat_matrix(cells, cols, rows, want_hours)
    return {"store_code": store, "date_from": a, "date_to": b,
            "hours": want_hours, **payload, "dead_zones": H.dead_zones(payload),
            "cameras": [{"id": c.get("id"), "label": c.get("label") or c.get("display_name")}
                        for c in _rows("vision_camera", org_id, store_code=store)]}


def _rules(org_id):
    return B.rules_or_defaults(_rows("vision_behavior_rule", org_id))


@router.get("/rules")
def get_rules(org_id: str = ORG_ID, authorization: str = Header(default=""),
              x_active_org: str = Header(default="")):
    caller = _require_caller(authorization, x_active_org)
    rows = _rows("vision_behavior_rule", org_id)
    return {"rules": sorted(_rules(org_id), key=lambda r: (r.get("sort_order") or 100)),
            "seeded": bool(rows), "can_edit": _can_edit_settings(caller)}


@router.put("/rules")
def put_rules(body: RulesIn, org_id: str = ORG_ID, authorization: str = Header(default=""),
              x_active_org: str = Header(default="")):
    """Replace the tenant's coaching rubric. RULE TWO: which behaviors count and what phrases evidence
    them belong to the operator, not to this file — a store selling home internet needs a different
    checklist than one selling tablets and neither should need a deploy."""
    caller = _require_caller(authorization, x_active_org)
    _require_settings(caller)
    rules = getattr(body, "rules", None) or []
    if not isinstance(rules, list) or not rules:
        raise HTTPException(400, "rules must be a non-empty list.")
    clean = []
    for i, r in enumerate(rules):
        if not isinstance(r, dict) or not (r.get("rule_key") or "").strip():
            raise HTTPException(400, f"Rule {i + 1} needs a rule_key.")
        polarity = (r.get("polarity") or "positive").strip().lower()
        if polarity not in ("positive", "negative"):
            raise HTTPException(400, f"Rule {i + 1}: polarity must be positive or negative.")
        phrases = [str(p).strip() for p in (r.get("phrases") or []) if str(p).strip()]
        if not phrases:
            raise HTTPException(400, f"Rule {i + 1} ({r['rule_key']}) needs at least one phrase.")
        clean.append({
            "org_id": org_id, "rule_key": str(r["rule_key"]).strip().lower()[:60],
            "label": (r.get("label") or r["rule_key"])[:160],
            "category": (r.get("category") or "sales").strip().lower()[:40],
            "phrases": phrases, "weight": float(r.get("weight") or 10), "polarity": polarity,
            "window_s": int(r["window_s"]) if str(r.get("window_s") or "").strip().isdigit() else None,
            "is_active": bool(r.get("is_active", True)),
            "sort_order": int(r.get("sort_order") or (i + 1) * 10),
            "updated_at": _iso(_now()),
        })
    try:
        sb().table("vision_behavior_rule").delete().eq("org_id", org_id).execute()
        sb().table("vision_behavior_rule").insert(clean).execute()
    except Exception as e:
        raise HTTPException(400, f"Could not save the rubric: {str(e)[:200]}")
    _audit(org_id, caller.get("email"), "rules_saved", None, {"count": len(clean)})
    return {"rules": clean}


def _score_day(org_id, store, day, cfg):
    """Score every employee who spoke at `store` on `day`, and upsert the rows. Returns the rows.

    Segments are grouped by employee and handed to behavior.score_interactions, which does the actual
    thinking; this function is the I/O around it."""
    try:
        segs = (sb().table("vision_transcript")
                .select("employee_id,text,duration_s,visit_id,signals,started_at")
                .eq("org_id", org_id).eq("store_code", store).eq("local_date", day)
                .limit(20000).execute().data) or []
    except Exception:
        segs = []
    rules = _rules(org_id)
    by_emp = {}
    for s in segs:
        emp = s.get("employee_id")
        if not emp:
            continue
        by_emp.setdefault(str(emp), []).append({
            "text": s.get("text") or "", "duration_s": s.get("duration_s") or 0,
            "visit_id": s.get("visit_id"),
            "elapsed_s": (s.get("signals") or {}).get("elapsed_s"),
        })

    out = []
    for emp, items in by_emp.items():
        scored = B.score_interactions(items, rules)
        row = {"org_id": org_id, "store_code": store, "employee_id": emp, "local_date": day,
               "segments": scored["segments"], "talk_seconds": scored["talk_seconds"],
               "interactions": scored["interactions"], "greeted": scored["greeted"],
               "missed_greetings": scored["missed_greetings"], "score": scored["score"],
               "rule_hits": scored["rule_hits"], "coaching": scored["coaching"],
               "source": scored["source"], "computed_at": _iso(_now())}
        try:
            sb().table("vision_behavior_score").upsert(
                row, on_conflict="org_id,store_code,employee_id,local_date").execute()
        except Exception:
            pass
        out.append({**row, "coverage": scored["coverage"]})
    return out


@router.post("/behavior/recompute")
def recompute_behavior(store_code: str = "", date_from: str = "", date_to: str = "",
                       org_id: str = ORG_ID, authorization: str = Header(default=""),
                       x_active_org: str = Header(default="")):
    """Re-score a store's days from the stored transcripts. Idempotent (the score table upserts on
    its natural key), so an operator who edits the rubric re-runs this and the old numbers are
    replaced rather than duplicated."""
    caller = _require_caller(authorization, x_active_org)
    cfg = _cfg(org_id)
    _require_module(cfg, "behavior_scoring")
    _require_manager(caller, "behavior scores")
    store = _scoped_store(caller, authorization, org_id, store_code)
    a, b = _date_range(date_from, date_to)
    days, cur = [], date.fromisoformat(a)
    while cur <= date.fromisoformat(b):
        days.append(cur.isoformat())
        cur += timedelta(days=1)
    total = sum(len(_score_day(org_id, store, d, cfg)) for d in days)
    _audit(org_id, caller.get("email"), "behavior_recompute", store,
           {"date_from": a, "date_to": b, "rows": total})
    return {"store_code": store, "date_from": a, "date_to": b, "rows": total}


@router.get("/behavior")
def behavior(store_code: str = "", date_from: str = "", date_to: str = "", org_id: str = ORG_ID,
             authorization: str = Header(default=""), x_active_org: str = Header(default="")):
    """The coaching board: one row per employee over the window, with their weakest rubric areas.

    Manager-gated. The response carries an explicit `disclaimer` and the UI prints it, because a
    number derived from what someone said at work is a coaching prompt and nothing else — it is not
    a performance rating and the migration gives it no path into any payout table."""
    caller = _require_caller(authorization, x_active_org)
    cfg = _cfg(org_id)
    _require_module(cfg, "behavior_scoring")
    _require_manager(caller, "behavior scores")
    store = _scoped_store(caller, authorization, org_id, store_code)
    a, b = _date_range(date_from, date_to)
    try:
        rows = (sb().table("vision_behavior_score").select("*")
                .eq("org_id", org_id).eq("store_code", store)
                .gte("local_date", a).lte("local_date", b).limit(5000).execute().data) or []
    except Exception:
        rows = []
    return {"store_code": store, "date_from": a, "date_to": b,
            **_roll_up_scores(rows, org_id),
            "disclaimer": ("These numbers describe what was said during recorded interactions. They "
                           "are a coaching aid, not a performance rating, and they are never used in "
                           "any pay calculation.")}


@router.get("/behavior/mine")
def my_behavior(date_from: str = "", date_to: str = "", org_id: str = ORG_ID,
                authorization: str = Header(default=""), x_active_org: str = Header(default="")):
    """An employee's OWN scores. No manager role required, and deliberately so: the person being
    recorded is entitled to see everything that was derived from that recording, without asking."""
    caller = _require_caller(authorization, x_active_org)
    emp = caller.get("employee_id")
    if not emp:
        raise HTTPException(404, "Your login is not linked to an employee record.")
    a, b = _date_range(date_from, date_to)
    try:
        rows = (sb().table("vision_behavior_score").select("*")
                .eq("org_id", org_id).eq("employee_id", str(emp))
                .gte("local_date", a).lte("local_date", b).limit(1000).execute().data) or []
    except Exception:
        rows = []
    consent = (_rows("vision_consent", org_id, employee_id=str(emp), scope="audio") or [None])[0]
    return {"date_from": a, "date_to": b, **_roll_up_scores(rows, org_id),
            "consent": {"status": (consent or {}).get("status") or "pending",
                        "signed_at": (consent or {}).get("signed_at")}}


def _roll_up_scores(rows, org_id):
    """Per-employee aggregate over the window plus a per-day series, with names resolved once."""
    by_emp = {}
    for r in rows:
        e = by_emp.setdefault(str(r.get("employee_id")), {
            "employee_id": str(r.get("employee_id")), "days": 0, "segments": 0, "interactions": 0,
            "talk_seconds": 0.0, "greeted": 0, "missed_greetings": 0, "score_sum": 0.0,
            "rule_hits": {}, "coaching": [], "series": []})
        e["days"] += 1
        e["segments"] += int(r.get("segments") or 0)
        e["interactions"] += int(r.get("interactions") or 0)
        e["talk_seconds"] += float(r.get("talk_seconds") or 0)
        e["greeted"] += int(r.get("greeted") or 0)
        e["missed_greetings"] += int(r.get("missed_greetings") or 0)
        e["score_sum"] += float(r.get("score") or 0)
        for k, v in (r.get("rule_hits") or {}).items():
            e["rule_hits"][k] = e["rule_hits"].get(k, 0) + int(v or 0)
        e["series"].append({"local_date": r.get("local_date"), "score": r.get("score"),
                            "interactions": r.get("interactions")})
        e["coaching"] = r.get("coaching") or e["coaching"]      # the most recent day's advice

    names = _employee_names(org_id, list(by_emp))
    out = []
    for emp, e in by_emp.items():
        e["score"] = round(e["score_sum"] / e["days"], 1) if e["days"] else 0
        e["greet_rate"] = (round(e["greeted"] / e["interactions"], 3) if e["interactions"] else None)
        e["name"] = names.get(emp) or "—"
        e["series"].sort(key=lambda s: str(s.get("local_date")))
        e.pop("score_sum", None)
        out.append(e)
    out.sort(key=lambda e: -e["score"])
    return {"employees": out,
            "totals": {"employees": len(out),
                       "interactions": sum(e["interactions"] for e in out),
                       "avg_score": round(sum(e["score"] for e in out) / len(out), 1) if out else 0}}


def _employee_names(org_id, employee_ids):
    if not employee_ids:
        return {}
    try:
        rows = (get_supabase().schema("storeops").table("employees")
                .select("id,full_name").eq("org_id", org_id)
                .in_("id", employee_ids).execute().data) or []
        return {str(r["id"]): r.get("full_name") for r in rows}
    except Exception:
        return {}


# ══════════════════════════════════════════════════════════════════════════════════════════════
# Consent
# ══════════════════════════════════════════════════════════════════════════════════════════════
@router.get("/consent")
def list_consent(org_id: str = ORG_ID, authorization: str = Header(default=""),
                 x_active_org: str = Header(default="")):
    """The consent register — who has signed, declined, withdrawn, or never been asked.

    This is the page an operator has to be able to produce on demand, so it lists every employee, not
    only the ones with a row: "never asked" is the answer that matters most and it does not exist as
    a stored value."""
    caller = _require_caller(authorization, x_active_org)
    _require_manager(caller, "the consent register")
    rows = {str(r.get("employee_id")): r for r in _rows("vision_consent", org_id, scope="audio")}
    try:
        emps = (get_supabase().schema("storeops").table("employees")
                .select("id,full_name,store_code,status").eq("org_id", org_id)
                .limit(5000).execute().data) or []
    except Exception:
        emps = []
    keyset = _keyset(authorization, org_id)
    out = []
    for e in emps:
        if not _in_keyset(keyset, e.get("store_code")):
            continue
        r = rows.get(str(e.get("id"))) or {}
        out.append({"employee_id": str(e.get("id")), "name": e.get("full_name"),
                    "store_code": e.get("store_code"), "employment_status": e.get("status"),
                    "status": r.get("status") or "not_asked", "signed_at": r.get("signed_at"),
                    "withdrawn_at": r.get("withdrawn_at"), "source": r.get("source")})
    out.sort(key=lambda r: (r["status"] != "not_asked", r.get("name") or ""))
    return {"consent": out, "config": {"audio_consent_mode": _cfg(org_id).get("audio_consent_mode")}}


@router.post("/consent")
def record_consent(body: ConsentIn, org_id: str = ORG_ID, authorization: str = Header(default=""),
                   x_active_org: str = Header(default="")):
    """HR records a signed release, or an employee's withdrawal.

    A WITHDRAWAL is accepted from anyone about themselves and from a manager about anyone — consent is
    revocable by definition, and putting an approval step in front of a withdrawal would make it not
    a withdrawal. Recording a SIGNATURE needs the settings permission and a source, because a signed
    release is a legal artifact and "someone clicked a box" is not one."""
    caller = _require_caller(authorization, x_active_org)
    emp = str(getattr(body, "employee_id", "") or "").strip() or str(caller.get("employee_id") or "")
    if not emp:
        raise HTTPException(400, "employee_id is required.")
    status = (str(getattr(body, "status", "") or "").strip().lower())
    if status not in (C.CONSENT_SIGNED, C.CONSENT_DECLINED, C.CONSENT_WITHDRAWN):
        raise HTTPException(400, "status must be signed, declined or withdrawn.")

    is_self = emp == str(caller.get("employee_id") or "")
    if status == C.CONSENT_SIGNED and not is_self:
        _require_settings(caller)
        if not (getattr(body, "document_url", None) or getattr(body, "note", None)):
            raise HTTPException(400, "Recording someone else's signature needs the release document "
                                     "or a note saying where it is filed.")
    elif not is_self and not _is_manager(caller):
        raise HTTPException(403, "You can only change your own consent.")

    row = {"org_id": org_id, "employee_id": emp, "scope": "audio", "status": status,
           "source": ("self_service" if is_self else "hr_recorded"),
           "recorded_by": caller.get("email"),
           "document_url": getattr(body, "document_url", None),
           "note": getattr(body, "note", None), "updated_at": _iso(_now())}
    if status == C.CONSENT_SIGNED:
        row["signed_at"] = _iso(_now())
        row["withdrawn_at"] = None
    if status == C.CONSENT_WITHDRAWN:
        row["withdrawn_at"] = _iso(_now())
    try:
        sb().table("vision_consent").upsert(row, on_conflict="org_id,employee_id,scope").execute()
    except Exception as e:
        raise HTTPException(400, f"Could not record consent: {str(e)[:200]}")
    _audit(org_id, caller.get("email"), f"consent_{status}", emp, {"self": is_self})
    return {"employee_id": emp, "status": status}


# ══════════════════════════════════════════════════════════════════════════════════════════════
# Retention
# ══════════════════════════════════════════════════════════════════════════════════════════════
@router.get("/retention/plan")
def retention_plan(org_id: str = ORG_ID, authorization: str = Header(default=""),
                   x_active_org: str = Header(default="")):
    _require_settings(_require_caller(authorization, x_active_org))
    return R.plan(get_supabase(), org_id)


@router.post("/retention/purge")
def retention_purge(body: PurgeIn, org_id: str = ORG_ID, authorization: str = Header(default=""),
                    x_active_org: str = Header(default="")):
    """Delete everything past its retention window. `confirm: true` is required — a dry run is the
    default and the endpoint says exactly what it would remove before it removes anything."""
    caller = _require_caller(authorization, x_active_org)
    _require_settings(caller)
    dry = not bool(getattr(body, "confirm", False))
    return R.purge(get_supabase(), org_id, dry_run=dry, actor=caller.get("email"))
