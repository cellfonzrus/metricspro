"""Helpdesk Auto-Remediation Agent — Phase 1 MVP.

Flow: an issue is PROPOSED → the agent (Claude, reusing the helpdesk AI key) classifies it data-vs-code,
and for a DATA issue picks a WHITELISTED playbook + params + a one-line fix, computes a dry-run PREVIEW,
and stores an awaiting-approval request with a signed magic-link. The assignee gets an email (best-effort
WhatsApp) with Approve/Reject. On APPROVE the one bounded playbook executes and the result is recorded +
returned. CODE-class issues are escalated, never auto-fixed. Everything is audited in remediation_request.
"""
import json
import secrets
from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException

from app.core.database import get_supabase
from app.core.config import settings
from . import playbooks as pb

router = APIRouter(prefix="/remediation", tags=["remediation"])
ORG_ID = "00000000-0000-0000-0000-000000000001"
_ISSUE_STATUSES = ("proposed", "awaiting_approval", "approved", "rejected", "executed",
                   "failed", "escalated", "expired")


def sb():
    return get_supabase()


def _now():
    return datetime.now(timezone.utc).isoformat()


def _catalog(client, org_id, only_enabled=False):
    rows = (client.schema("commcalc").table("remediation_playbook").select("*")
            .eq("org_id", org_id).order("risk_level").execute().data) or []
    for r in rows:
        r["implemented"] = pb.is_implemented(r.get("key"))
    if only_enabled:
        rows = [r for r in rows if r.get("enabled") and r.get("implemented")]
    return rows


# ── catalog (SAP config) ─────────────────────────────────────────────────────────────────────────
@router.get("/playbooks")
def list_playbooks(org_id: str = ORG_ID):
    return {"playbooks": _catalog(sb(), org_id)}


@router.post("/playbooks")
def upsert_playbook(body: dict, org_id: str = ORG_ID):
    key = (body.get("key") or "").strip()
    if not key:
        raise HTTPException(400, "key required")
    row = {"org_id": org_id, "key": key,
           "name": (body.get("name") or key).strip(),
           "description": body.get("description"),
           "risk_level": (body.get("risk_level") or "low"),
           "enabled": bool(body.get("enabled", True)),
           "requires_approval": bool(body.get("requires_approval", True)),
           "params_schema": body.get("params_schema") or {}}
    r = (sb().schema("commcalc").table("remediation_playbook")
         .upsert(row, on_conflict="org_id,key").execute())
    return {"playbook": (r.data or [row])[0]}


# ── AI diagnosis (reuses the helpdesk Anthropic key) ───────────────────────────────────────────────
_DIAGNOSE_SYSTEM = (
    "You are the MetricsPro auto-remediation triage agent. You are given an operational ISSUE and a "
    "CATALOG of whitelisted remediation playbooks. Decide:\n"
    "1. issue_class: 'data' if a config/data fix from the catalog resolves it, or 'code' if it needs a "
    "source-code change/deploy (then it must be escalated to a developer — never auto-fixed).\n"
    "2. If 'data', pick the single best playbook_key FROM THE CATALOG ONLY, and extract its params from "
    "the issue text. If none fits, set playbook_key to null.\n"
    "3. proposed_action: one concise sentence describing the fix for a human approver.\n"
    "4. diagnosis: 1-2 sentences on the root cause.\n"
    "Reply with ONLY a JSON object: {\"issue_class\":\"data|code\",\"playbook_key\":str|null,"
    "\"params\":{},\"proposed_action\":str,\"diagnosis\":str,\"confidence\":0..1}. No prose, no markdown."
)


def _ai_diagnose(catalog, issue):
    """Returns a dict (issue_class/playbook_key/params/proposed_action/diagnosis) or None if AI is off."""
    if not settings.ANTHROPIC_API_KEY:
        return None
    cat = [{"key": c["key"], "name": c["name"], "description": c.get("description"),
            "params_schema": c.get("params_schema")} for c in catalog]
    user = f"CATALOG:\n{json.dumps(cat)}\n\nISSUE:\n{issue[:3000]}"
    try:
        from anthropic import Anthropic
        cli = Anthropic(api_key=settings.ANTHROPIC_API_KEY)
        resp = cli.messages.create(model=settings.ACCOUNT_ENGINE_MODEL, max_tokens=700,
                                   system=_DIAGNOSE_SYSTEM, messages=[{"role": "user", "content": user}])
        text = "".join(getattr(b, "text", "") for b in resp.content
                       if getattr(b, "type", None) == "text").strip()
        # tolerate stray fences/prose around the JSON
        s, e = text.find("{"), text.rfind("}")
        if s >= 0 and e > s:
            return json.loads(text[s:e + 1])
    except Exception:
        return None
    return None


def _approval_url(req_id, token):
    base = (settings.APP_PUBLIC_URL or "").rstrip("/")
    return f"{base}/remediation/approve/{req_id}?token={token}"


async def _send_approval(req, approval_url):
    """Best-effort notify the assignee. Email is the reliable channel (HTML link); WhatsApp text is
    template-gated in this stack (Phase 2 inbound). Never raises — delivery failure ≠ propose failure."""
    contact = req.get("assignee_contact") or {}
    email = (contact.get("email") or "").strip()
    channels = []
    subject = f"[MetricsPro] Approve a fix: {req.get('title') or req.get('playbook_key')}"
    html = (f"<p>An automated fix is awaiting your approval.</p>"
            f"<p><b>Issue:</b> {(req.get('issue') or '')[:400]}</p>"
            f"<p><b>Proposed fix:</b> {req.get('proposed_action') or ''}</p>"
            f"<p><b>Preview (dry-run):</b> {req.get('preview') or ''}</p>"
            f"<p><a href=\"{approval_url}\" style=\"background:#2563eb;color:#fff;padding:10px 18px;"
            f"border-radius:8px;text-decoration:none\">Review &amp; Approve / Reject</a></p>"
            f"<p style=\"color:#888;font-size:12px\">Only a whitelisted, bounded action runs — and only "
            f"after you approve.</p>")
    if email:
        try:
            from app.modules.notify.channels import email_resend
            if email_resend.is_configured():
                await email_resend.send_email(email, subject, html)
                channels.append("email")
        except Exception:
            pass
    return channels


# ── propose ────────────────────────────────────────────────────────────────────────────────────────
@router.post("/propose")
async def propose(body: dict, org_id: str = ORG_ID):
    """Propose a remediation. Body: {issue, playbook_key?, params?, assignee?:{name,email,whatsapp},
    requested_by?, source?}. If playbook_key is given → manual mode (no AI). Otherwise the agent
    diagnoses + picks a playbook. Creates an awaiting-approval request + magic-link, notifies the
    assignee, and returns the request + approval_url. Code-class issues are escalated, not executed."""
    client = sb()
    issue = (body.get("issue") or "").strip()
    if not issue and not body.get("playbook_key"):
        raise HTTPException(400, "issue (or an explicit playbook_key) is required")
    catalog = _catalog(client, org_id, only_enabled=True)

    playbook_key = (body.get("playbook_key") or "").strip() or None
    params = body.get("params") or {}
    diagnosis = body.get("diagnosis") or ""
    proposed_action = body.get("proposed_action") or ""
    issue_class = "data"

    if not playbook_key:  # let the agent decide
        ai = _ai_diagnose(catalog, issue)
        if ai:
            issue_class = (ai.get("issue_class") or "data").lower()
            playbook_key = ai.get("playbook_key") or None
            params = ai.get("params") or {}
            proposed_action = ai.get("proposed_action") or proposed_action
            diagnosis = ai.get("diagnosis") or diagnosis

    # Code-class or no fitting playbook → ESCALATE (never auto-fix).
    if issue_class == "code" or not playbook_key or not pb.is_implemented(playbook_key):
        row = {"org_id": org_id, "issue": issue, "diagnosis": diagnosis or "",
               "proposed_action": proposed_action or "Needs a developer / no automatic playbook fits.",
               "issue_class": issue_class if issue_class == "code" else "data",
               "status": "escalated", "source": body.get("source") or "manual",
               "requested_by": body.get("requested_by"), "title": (issue[:80] or playbook_key or "issue")}
        r = client.schema("commcalc").table("remediation_request").insert(row).execute()
        out = (r.data or [row])[0]
        out["escalated"] = True
        return {"request": out, "escalated": True,
                "message": "No safe automatic fix — escalated for a person/developer to handle."}

    # Build the dry-run preview + create the awaiting-approval request with a magic-link token.
    preview = pb.run_preview(playbook_key, client, org_id, params)
    token = secrets.token_urlsafe(24)
    pbrow = next((c for c in catalog if c["key"] == playbook_key), {})
    row = {"org_id": org_id, "playbook_key": playbook_key,
           "title": pbrow.get("name") or playbook_key,
           "issue": issue, "diagnosis": diagnosis, "proposed_action": proposed_action or pbrow.get("name"),
           "params": params, "preview": preview.get("summary"), "issue_class": "data",
           "status": "awaiting_approval", "approval_token": token,
           "assignee_contact": body.get("assignee") or {}, "source": body.get("source") or "manual",
           "requested_by": body.get("requested_by")}
    r = client.schema("commcalc").table("remediation_request").insert(row).execute()
    req = (r.data or [row])[0]
    approval_url = _approval_url(req["id"], token)
    channels = await _send_approval(req, approval_url)
    req.pop("approval_token", None)
    return {"request": req, "approval_url": approval_url, "notified": channels,
            "preview": preview}


# ── list / detail ───────────────────────────────────────────────────────────────────────────────
@router.get("/requests")
def list_requests(status: str = "", org_id: str = ORG_ID):
    q = (sb().schema("commcalc").table("remediation_request").select("*")
         .eq("org_id", org_id).order("created_at", desc=True).limit(500))
    if status:
        q = q.eq("status", status)
    rows = q.execute().data or []
    for r in rows:
        r.pop("approval_token", None)   # never leak the token in listings
    return {"requests": rows}


@router.get("/requests/{req_id}")
def get_request(req_id: str, org_id: str = ORG_ID):
    rows = (sb().schema("commcalc").table("remediation_request").select("*")
            .eq("org_id", org_id).eq("id", req_id).limit(1).execute().data) or []
    if not rows:
        raise HTTPException(404, "not found")
    r = rows[0]
    r.pop("approval_token", None)
    return {"request": r}


# ── decision (the magic-link target) ───────────────────────────────────────────────────────────────
@router.post("/requests/{req_id}/decision")
def decide(req_id: str, body: dict, org_id: str = ORG_ID):
    """Approve or reject a pending remediation. Body: {decision:'approve'|'reject', token, decided_by?}.
    The token must match the one minted at propose time. Approve → execute the one bounded playbook."""
    client = sb()
    decision = (body.get("decision") or "").strip().lower()
    token = (body.get("token") or "").strip()
    if decision not in ("approve", "reject"):
        raise HTTPException(400, "decision must be 'approve' or 'reject'")
    rows = (client.schema("commcalc").table("remediation_request").select("*")
            .eq("org_id", org_id).eq("id", req_id).limit(1).execute().data) or []
    if not rows:
        raise HTTPException(404, "not found")
    req = rows[0]
    if not req.get("approval_token") or token != req.get("approval_token"):
        raise HTTPException(403, "invalid or missing approval token")
    if req.get("status") != "awaiting_approval":
        # idempotent: already decided → report the current state instead of re-running
        req.pop("approval_token", None)
        return {"request": req, "already": True}

    decided_by = body.get("decided_by") or (req.get("assignee_contact") or {}).get("name") or "approver"
    if decision == "reject":
        upd = {"status": "rejected", "decided_at": _now(), "decided_by": decided_by}
        client.schema("commcalc").table("remediation_request").update(upd).eq("id", req_id).execute()
        return {"request": {**req, **upd, "approval_token": None}}

    # approve → execute the bounded playbook
    try:
        result = pb.run_execute(req.get("playbook_key"), client, org_id, req.get("params") or {})
        upd = {"status": "executed", "decided_at": _now(), "decided_by": decided_by,
               "executed_at": _now(), "result": result, "error": None}
    except Exception as e:
        upd = {"status": "failed", "decided_at": _now(), "decided_by": decided_by, "error": str(e)[:500]}
    client.schema("commcalc").table("remediation_request").update(upd).eq("id", req_id).execute()
    out = {**req, **upd}
    out.pop("approval_token", None)
    return {"request": out}
