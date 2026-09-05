"""Helpdesk Auto-Remediation Agent — Phase 1 MVP.

Flow: an issue is PROPOSED → the agent (Claude, reusing the helpdesk AI key) classifies it data-vs-code,
and for a DATA issue picks a WHITELISTED playbook + params + a one-line fix, computes a dry-run PREVIEW,
and stores an awaiting-approval request with a signed magic-link. The assignee gets an email (best-effort
WhatsApp) with Approve/Reject. On APPROVE the one bounded playbook executes and the result is recorded +
returned. CODE-class issues are escalated, never auto-fixed. Everything is audited in remediation_request.
"""
import asyncio
import hashlib
import hmac
import json
import os
import secrets
from datetime import datetime, timezone

from typing import Any

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import PlainTextResponse

from app.core.database import get_supabase
from app.core.config import settings
from app.core.schemas import LaxModel
from app.modules.notify import whatsapp_window
from . import playbooks as pb

router = APIRouter(prefix="/remediation", tags=["remediation"])
ORG_ID = "00000000-0000-0000-0000-000000000001"


class UpsertPlaybookIn(LaxModel):
    key: Any = None
    name: Any = None
    description: Any = None
    risk_level: Any = None
    enabled: Any = True
    requires_approval: Any = True
    params_schema: Any = None


class ProposeIn(LaxModel):
    issue: Any = None
    playbook_key: Any = None
    params: Any = None
    diagnosis: Any = None
    proposed_action: Any = None
    assignee: Any = None
    requested_by: Any = None
    source: Any = None


class DecideIn(LaxModel):
    decision: Any = None
    token: Any = None
    decided_by: Any = None
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
def upsert_playbook(body: UpsertPlaybookIn, org_id: str = ORG_ID):
    key = (body.key or "").strip()
    if not key:
        raise HTTPException(400, "key required")
    row = {"org_id": org_id, "key": key,
           "name": (body.name or key).strip(),
           "description": body.description,
           "risk_level": (body.risk_level or "low"),
           "enabled": bool(body.enabled),
           "requires_approval": bool(body.requires_approval),
           "params_schema": body.params_schema or {}}
    r = (sb().schema("commcalc").table("remediation_playbook")
         .upsert(row, on_conflict="org_id,key").execute())
    return {"playbook": (r.data or [row])[0]}


# ── AI diagnosis (reuses the helpdesk Anthropic key) ───────────────────────────────────────────────
# Event-loop safety limits for the ONE outbound AI call on this path (same SEV-1 class as the helpdesk
# /ai-assist freeze, 2026-07-30). The Anthropic SDK defaults to a 600s timeout with 2 automatic retries;
# these defaults cap a single /propose diagnosis at ~60s worst case (timeout x (1 + retries)). Env-tunable
# so the operator can widen/narrow without a code deploy; a garbage env value falls back to the default
# rather than breaking module import.
try:
    REMEDIATION_AI_TIMEOUT_S = max(1.0, float(os.getenv("REMEDIATION_AI_TIMEOUT_S") or 30))
except Exception:
    REMEDIATION_AI_TIMEOUT_S = 30.0
try:
    REMEDIATION_AI_MAX_RETRIES = max(0, int(os.getenv("REMEDIATION_AI_MAX_RETRIES") or 1))
except Exception:
    REMEDIATION_AI_MAX_RETRIES = 1

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


async def _ai_diagnose(catalog, issue):
    """Returns a dict (issue_class/playbook_key/params/proposed_action/diagnosis) or None if AI is off.

    ASYNC ON PURPOSE — see the comment on the client below. Do NOT make this a plain `def` again."""
    if not settings.ANTHROPIC_API_KEY:
        return None
    cat = [{"key": c["key"], "name": c["name"], "description": c.get("description"),
            "params_schema": c.get("params_schema")} for c in catalog]
    user = f"CATALOG:\n{json.dumps(cat)}\n\nISSUE:\n{issue[:3000]}"
    try:
        # SEV-1 class (2026-07-30, helpdesk /ai-assist) — this call MUST be the ASYNC client and MUST
        # be awaited. The synchronous client blocks the FastAPI event loop for the entire HTTP call, and
        # the SDK defaults to a 600s timeout with 2 automatic retries, so one stalled /propose would
        # freeze EVERY endpoint (including /health) for up to ~30 minutes. Awaiting the async client
        # hands the loop back while the model thinks; the explicit timeout + single retry cap the worst
        # case for THIS request at ~60s. Do NOT reintroduce `Anthropic(` here.
        from anthropic import AsyncAnthropic
        cli = AsyncAnthropic(api_key=settings.ANTHROPIC_API_KEY,
                             timeout=REMEDIATION_AI_TIMEOUT_S, max_retries=REMEDIATION_AI_MAX_RETRIES)
        resp = await cli.messages.create(model=settings.ACCOUNT_ENGINE_MODEL, max_tokens=700,
                                         system=_DIAGNOSE_SYSTEM,
                                         messages=[{"role": "user", "content": user}])
        from app.modules.billing import ai_meter as _ai_meter
        _ai_meter.record("remediation_diagnose", settings.ACCOUNT_ENGINE_MODEL, resp)  # usage metering only (mig 972/973) — no auth implication
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
    wa = (contact.get("whatsapp") or "").strip()
    channels = []
    wa_status = None
    # WhatsApp interactive approval (Phase 2): the template's two quick-reply buttons carry per-send
    # payloads → a tap posts to /whatsapp-webhook → the decision runs, all inside WhatsApp.
    if not wa:
        wa_status = "no WhatsApp number on the assignee"
    else:
        try:
            from app.modules.notify.channels import whatsapp_meta
            if whatsapp_meta.approval_configured():
                await whatsapp_meta.send_approval(
                    wa, req["id"], req.get("approval_token") or "",
                    req.get("issue") or "", req.get("proposed_action") or req.get("title") or "",
                    req.get("preview") or "")
                channels.append("whatsapp"); wa_status = "sent (approval template)"
            elif whatsapp_meta.is_configured():
                # Base WhatsApp works but the interactive approval template isn't set — send a plain-text
                # approval with the link (delivers inside the 24h customer-initiated window; Meta rejects
                # business-initiated free-form text otherwise). Set WHATSAPP_APPROVAL_TEMPLATE for buttons.
                try:
                    await whatsapp_meta.send_text(
                        wa, f"MetricsPro — approve a fix: {(req.get('issue') or req.get('title') or '')[:200]}. "
                            f"Review & approve: {approval_url}")
                    channels.append("whatsapp-text")
                    wa_status = "sent as plain text — set WHATSAPP_APPROVAL_TEMPLATE (approved in WhatsApp Manager) for inline buttons"
                except Exception as e:
                    wa_status = f"approval template not configured; text fallback failed ({str(e)[:120]})"
            else:
                wa_status = "WhatsApp base creds missing (WHATSAPP_ACCESS_TOKEN / PHONE_NUMBER_ID / TEMPLATE_NAME)"
        except Exception as e:
            wa_status = f"WhatsApp send error: {str(e)[:140]}"
    subject = f"[MetricsPro] Approve a fix: {req.get('title') or req.get('playbook_key')}"
    html = (f"<p>An automated fix is awaiting your approval.</p>"
            f"<p><b>Issue:</b> {(req.get('issue') or '')[:400]}</p>"
            f"<p><b>Proposed fix:</b> {req.get('proposed_action') or ''}</p>"
            f"<p><b>Preview (dry-run):</b> {req.get('preview') or ''}</p>"
            f"<p><a href=\"{approval_url}\" style=\"background:#2563eb;color:#fff;padding:10px 18px;"
            f"border-radius:8px;text-decoration:none\">Review &amp; Approve / Reject</a></p>"
            f"<p style=\"color:#888;font-size:12px\">Only a whitelisted, bounded action runs — and only "
            f"after you approve.</p>")
    email_status = None
    if not email:
        email_status = "no email on the assignee"
    else:
        try:
            from app.modules.notify.channels import email_resend
            if email_resend.is_configured():
                await email_resend.send_email(email, subject, html)
                channels.append("email"); email_status = "sent"
            else:
                email_status = "email (Resend) not configured"
        except Exception as e:
            email_status = f"email send error: {str(e)[:140]}"
    return {"channels": channels, "whatsapp": wa_status, "email": email_status}


# ── propose ────────────────────────────────────────────────────────────────────────────────────────
@router.post("/propose")
async def propose(body: ProposeIn, org_id: str = ORG_ID):
    """Propose a remediation. Body: {issue, playbook_key?, params?, assignee?:{name,email,whatsapp},
    requested_by?, source?}. If playbook_key is given → manual mode (no AI). Otherwise the agent
    diagnoses + picks a playbook. Creates an awaiting-approval request + magic-link, notifies the
    assignee, and returns the request + approval_url. Code-class issues are escalated, not executed."""
    client = sb()
    issue = (body.issue or "").strip()
    if not issue and not body.playbook_key:
        raise HTTPException(400, "issue (or an explicit playbook_key) is required")
    catalog = _catalog(client, org_id, only_enabled=True)

    playbook_key = (body.playbook_key or "").strip() or None
    params = body.params or {}
    diagnosis = body.diagnosis or ""
    proposed_action = body.proposed_action or ""
    issue_class = "data"

    if not playbook_key:  # let the agent decide
        ai = await _ai_diagnose(catalog, issue)
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
               "status": "escalated", "source": body.source or "manual",
               "requested_by": body.requested_by, "title": (issue[:80] or playbook_key or "issue")}
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
           "assignee_contact": body.assignee or {}, "source": body.source or "manual",
           "requested_by": body.requested_by}
    r = client.schema("commcalc").table("remediation_request").insert(row).execute()
    req = (r.data or [row])[0]
    approval_url = _approval_url(req["id"], token)
    delivery = await _send_approval(req, approval_url)
    # Intimation into the UNIFIED approvals inbox (org-level / ops-admin scope). notify=False — this
    # module already emailed/WhatsApp'd the assignee above, so the engine must not send a second message.
    try:
        from app.modules.approvals import engine as _approvals
        _approvals.create_request(
            org_id, type="remediation", source_table="remediation_request", source_id=req["id"],
            title=f"Automated fix: {req.get('title') or req.get('issue') or 'remediation'}",
            summary=(req.get("proposed_action") or req.get("issue") or None),
            payload={"playbook_key": req.get("playbook_key"), "preview": req.get("preview"),
                     "issue": req.get("issue")},
            requested_by=req.get("requested_by"),
            requested_by_name=(req.get("assignee_contact") or {}).get("name"),
            priority="high", notify=False)
    except Exception:
        pass
    req.pop("approval_token", None)
    return {"request": req, "approval_url": approval_url, "notified": delivery.get("channels", []),
            "delivery": delivery, "preview": preview}


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
def _apply_decision(client, org_id, req, decision, decided_by):
    """Apply approve/reject to an awaiting_approval request; execute the bounded playbook on approve.
    Idempotent: a non-pending request is returned unchanged with _already=True. Shared by the web
    decision endpoint AND the WhatsApp webhook, so both paths behave identically + audit the same."""
    if req.get("status") != "awaiting_approval":
        return {**req, "_already": True}
    rid = req["id"]
    if decision == "reject":
        upd = {"status": "rejected", "decided_at": _now(), "decided_by": decided_by}
        client.schema("commcalc").table("remediation_request").update(upd).eq("id", rid).execute()
        return {**req, **upd}
    try:
        result = pb.run_execute(req.get("playbook_key"), client, org_id, req.get("params") or {})
        upd = {"status": "executed", "decided_at": _now(), "decided_by": decided_by,
               "executed_at": _now(), "result": result, "error": None}
    except Exception as e:
        upd = {"status": "failed", "decided_at": _now(), "decided_by": decided_by, "error": str(e)[:500]}
    client.schema("commcalc").table("remediation_request").update(upd).eq("id", rid).execute()
    return {**req, **upd}


@router.post("/requests/{req_id}/decision")
def decide(req_id: str, body: DecideIn, org_id: str = ORG_ID):
    """Approve or reject a pending remediation. Body: {decision:'approve'|'reject', token, decided_by?}.
    The token must match the one minted at propose time. Approve → execute the one bounded playbook."""
    client = sb()
    decision = (body.decision or "").strip().lower()
    token = (body.token or "").strip()
    if decision not in ("approve", "reject"):
        raise HTTPException(400, "decision must be 'approve' or 'reject'")
    rows = (client.schema("commcalc").table("remediation_request").select("*")
            .eq("org_id", org_id).eq("id", req_id).limit(1).execute().data) or []
    if not rows:
        raise HTTPException(404, "not found")
    req = rows[0]
    if not req.get("approval_token") or token != req.get("approval_token"):
        raise HTTPException(403, "invalid or missing approval token")
    decided_by = body.decided_by or (req.get("assignee_contact") or {}).get("name") or "approver"
    out = _apply_decision(client, org_id, req, decision, decided_by)
    already = out.pop("_already", False)
    if not already:
        try:
            from app.modules.approvals import engine as _approvals
            _approvals.sync_source_decision(org_id, type="remediation",
                                            source_table="remediation_request", source_id=req_id,
                                            decision=("approve" if decision == "approve" else "deny"),
                                            actor=decided_by)
        except Exception:
            pass
    out.pop("approval_token", None)
    return {"request": out, "already": already}


# ── WhatsApp interactive approval (Phase 2) ────────────────────────────────────────────────────────
def _text_decision(text):
    t = (text or "").strip().lower()
    if t in ("yes", "y", "approve", "approved", "ok", "okay", "confirm", "1", "✅"):
        return "approve"
    if t in ("no", "n", "reject", "rejected", "deny", "cancel", "2", "❌"):
        return "reject"
    return None


def _digits(s):
    return "".join(c for c in str(s or "") if c.isdigit())

def _orgs_for_sender(client, digits):
    """Distinct org_ids whose notify registry (notify.recipients) has this sender's phone number.
    Used to attribute a free-text WhatsApp approval to the sender's OWN tenant(s), so a number shared
    across tenants can't approve another tenant's remediation. Best-effort → empty set on any error."""
    if not digits:
        return set()
    orgs = set()
    try:
        rows = (client.schema("notify").table("recipients")
                .select("org_id, phone").ilike("phone", f"%{digits[-7:]}%")
                .limit(200).execute().data) or []
        for r in rows:
            if _digits(r.get("phone")) == digits:
                orgs.add(r.get("org_id") or ORG_ID)
    except Exception:
        pass
    return orgs


# ── WhatsApp delivery-status ingestion (owner incident 2026-07-18: silent drops made VISIBLE) ────────
# Meta delivers message STATUS events on the SAME webhook subscription:
#   entry[].changes[].value.statuses[] = [{id:<wamid>, status:sent|delivered|read|failed, timestamp,
#                                          errors?:[{code,title,message,error_data}]}]
# We record the latest status onto the matching notify.send_log row(s) (provider_message_id == wamid),
# so a message Meta ACCEPTED ('sent') but then dropped never reaching 'delivered' is visible in the log.
_DELIVERY_RANK = {"sent": 1, "delivered": 2, "read": 3, "failed": 4}


def _merge_delivery_status(current, incoming):
    """PURE. The latest delivery status, monotonic by rank sent<delivered<read with failed ALWAYS winning.
    Never regresses (a later 'delivered' after 'read' keeps 'read'); an unknown/empty incoming keeps the
    current value. Returns the winning status string (lowercased) or the unchanged current."""
    inc = (incoming or "").strip().lower()
    inc_r = _DELIVERY_RANK.get(inc, 0)
    if inc_r == 0:
        return current
    cur_r = _DELIVERY_RANK.get((current or "").strip().lower(), 0)
    return inc if inc_r >= cur_r else current


def _flatten_delivery_errors(errors):
    """PURE. Flatten a Meta status `errors[]` array into one short human string for send_log.delivery_error.
    Each error: {code, title, message, error_data:{details}}. Best-effort; '' when there are none."""
    if not errors:
        return ""
    parts = []
    for e in errors:
        if not isinstance(e, dict):
            parts.append(str(e)[:200])
            continue
        code = e.get("code")
        title = e.get("title") or e.get("message") or ""
        ed = e.get("error_data")
        detail = ed.get("details") if isinstance(ed, dict) else ""
        seg = (f"[{code}] {title}" if code is not None else str(title)).strip()
        if detail:
            seg = f"{seg} — {detail}".strip(" —")
        if seg:
            parts.append(seg)
    return " | ".join(parts)[:500]


def _record_delivery_statuses(statuses):
    """Best-effort: write Meta delivery-status events onto notify.send_log. GLOBAL wamid lookup
    (`provider_message_id == id`) — wamids are globally unique, so NO org filter (an org filter could miss
    the row). Writes ONLY the three delivery columns; the status is monotonic (never regresses; failed
    wins). GRACEFUL pre-mig-714: any missing-column / query error drops the whole batch to a silent no-op
    (the webhook MUST always 200 — Meta disables a subscription that repeatedly errors). Never raises."""
    try:
        log = get_supabase().schema("notify").table("send_log")
    except Exception:
        return
    now_iso = _now()
    for st in statuses or []:
        if not isinstance(st, dict):
            continue
        wamid = st.get("id")
        status = (st.get("status") or "").strip().lower()
        if not wamid or status not in _DELIVERY_RANK:
            continue
        try:
            rows = (log.select("id, delivery_status")
                    .eq("provider_message_id", wamid).execute().data) or []
        except Exception:
            return  # missing column (un-run mig 714) / query error → no-op for the whole batch
        if not rows:
            continue  # unknown wamid → nothing to update (no crash)
        err = _flatten_delivery_errors(st.get("errors")) if status == "failed" else ""
        for r in rows:
            cur = r.get("delivery_status")
            merged = _merge_delivery_status(cur, status)
            # IDEMPOTENT REPLAY (Meta retries a webhook until it sees a 2xx, and re-sends the same
            # status events): when the merge changes nothing and there is no new error to record, skip
            # the write entirely. Replaying a batch is then a pure no-op on the row.
            if merged == cur and not err:
                continue
            upd = {"delivery_status": merged, "delivery_updated_at": now_iso}
            if err:
                upd["delivery_error"] = err
            try:
                log.update(upd).eq("id", r.get("id")).execute()
            except Exception:
                return  # graceful: a missing column on write → no-op


def _valid_signature(sig_header, body):
    """Validate Meta's `X-Hub-Signature-256` (HMAC-SHA256 of the RAW body under the app secret), in
    CONSTANT TIME. This POST is on the PUBLIC middleware allowlist — Meta carries no JWT — so the
    signature is its ONLY authentication.

    2026-08-05 hardening: with NO app secret configured this used to return True, i.e. the endpoint
    accepted anonymous payloads. That is a real hole: a spoofed inbound can drive the free-text YES/NO
    remediation approval path (which matches on the sender's digits) and can write fake delivery
    statuses onto notify.send_log. It now fails CLOSED — an unset WHATSAPP_APP_SECRET rejects every POST.
    Break-glass: WHATSAPP_WEBHOOK_REQUIRE_SIGNATURE=0 restores the old verify-only-if-set behaviour with
    one Railway env change (no code rollback), for the window between deploying and setting the secret."""
    if not settings.WHATSAPP_APP_SECRET:
        return not bool(getattr(settings, "WHATSAPP_WEBHOOK_REQUIRE_SIGNATURE", True))
    if not (sig_header or "").startswith("sha256="):
        return False
    expected = hmac.new(settings.WHATSAPP_APP_SECRET.encode(), body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, sig_header.split("=", 1)[1])


@router.get("/whatsapp-webhook")
def whatsapp_verify(request: Request):
    """Meta webhook verification handshake — echoes hub.challenge when hub.verify_token matches
    WHATSAPP_VERIFY_TOKEN. Set the same value on the callback URL in the Meta App dashboard."""
    q = request.query_params
    if q.get("hub.mode") == "subscribe" and q.get("hub.verify_token") and \
            q.get("hub.verify_token") == settings.WHATSAPP_VERIFY_TOKEN:
        return PlainTextResponse(q.get("hub.challenge") or "")
    raise HTTPException(403, "verification failed")


async def _handle_inbound(client, frm, payload, text):
    """Resolve an inbound WhatsApp reply (a quick-reply button payload 'decision|id|token', or a
    YES/NO text) to a pending request, apply the decision, and confirm back in-thread."""
    decision, req = None, None
    if payload and "|" in payload:
        parts = payload.split("|")
        if len(parts) == 3 and parts[0] in ("approve", "reject"):
            decision = parts[0]
            rows = (client.schema("commcalc").table("remediation_request").select("*")
                    .eq("id", parts[1]).limit(1).execute().data) or []
            if rows and rows[0].get("approval_token") and rows[0]["approval_token"] == parts[2]:
                req = rows[0]
    if req is None and text:  # free-text YES/NO fallback → pending for THIS SENDER'S OWN tenant(s)
        d = _text_decision(text)
        digits = _digits(frm)
        if d and digits:
            # A phone number can belong to more than one tenant. Scanning awaiting_approval requests
            # across ALL orgs and matching only by sender digits (the old behavior) let a shared
            # number approve ANOTHER tenant's remediation. Resolve which org(s) this sender is
            # registered to — from the notify module's per-org recipient registry (notify.recipients)
            # AND from the awaiting requests' own approver contact (assignee_contact.whatsapp) — then:
            #   • exactly one org  → act on that org's matching request (single-tenant case unchanged);
            #   • two or more orgs → AMBIGUOUS: refuse the free-text decision and ask the approver to
            #     use the tokenized button/link (the token path above binds a decision to one request);
            #   • zero orgs        → nothing registered → no-op.
            rows = (client.schema("commcalc").table("remediation_request").select("*")
                    .eq("status", "awaiting_approval").order("created_at", desc=True)
                    .limit(50).execute().data) or []
            matches = [r for r in rows
                       if _digits((r.get("assignee_contact") or {}).get("whatsapp")) == digits]
            owner_orgs = _orgs_for_sender(client, digits) | {
                (r.get("org_id") or ORG_ID) for r in matches}
            if len(owner_orgs) >= 2:
                try:
                    from app.modules.notify.channels import whatsapp_meta
                    await whatsapp_meta.send_text(frm,
                        "This number is linked to more than one account, so I can't tell which "
                        "approval you mean. Please tap the Approve/Reject button in the message "
                        "(or open the approval link) instead of replying.")
                except Exception:
                    pass
                return
            if len(owner_orgs) == 1:
                only = next(iter(owner_orgs))
                for r in matches:
                    if (r.get("org_id") or ORG_ID) == only:
                        decision = d
                        req = r
                        break
    if req is None or decision is None:
        return
    out = _apply_decision(client, req.get("org_id") or ORG_ID, req, decision,
                          decided_by=(req.get("assignee_contact") or {}).get("name") or frm)
    try:
        from app.modules.notify.channels import whatsapp_meta
        if out.get("_already"):
            msg = f"Already {out.get('status')}."
        elif out.get("status") == "executed":
            msg = f"✅ Approved & done: {(out.get('result') or {}).get('summary', '')}"
        elif out.get("status") == "failed":
            msg = f"⚠️ Approved, but the action failed: {out.get('error', '')}"
        elif out.get("status") == "rejected":
            msg = "❌ Rejected — nothing was changed."
        else:
            msg = f"Recorded: {out.get('status')}."
        await whatsapp_meta.send_text(frm, msg)
    except Exception:
        pass


@router.post("/whatsapp-webhook")
async def whatsapp_inbound(request: Request):
    """Receive WhatsApp inbound events. A quick-reply button tap (or a YES/NO text) drives the same
    _apply_decision path as the web page. Always 200s fast (Meta retries on non-2xx)."""
    body = await request.body()
    if not _valid_signature(request.headers.get("X-Hub-Signature-256", ""), body):
        raise HTTPException(403, "bad signature")
    try:
        data = json.loads(body or b"{}")
    except Exception:
        return {"ok": True}
    client = sb()
    for entry in data.get("entry", []) or []:
        for ch in entry.get("changes", []) or []:
            for m in (ch.get("value", {}) or {}).get("messages", []) or []:
                frm = m.get("from")
                # WINDOW EVIDENCE (owner incident 2026-08-05): ANY inbound message — whatever its type,
                # whether or not we can act on it — opens/refreshes Meta's 24h customer-service window
                # for that handset. Recording it is the ONLY positive evidence that a free-form
                # `type:document` send will actually be delivered, so notify's ladder can attach the real
                # file instead of falling back to the link template. Best-effort + thread-hopped: it must
                # never raise, never 500 the webhook, and never block the event loop.
                if frm:
                    try:
                        await asyncio.to_thread(whatsapp_window.record_inbound, frm)
                    except Exception:
                        pass
                payload, text = None, None
                mtype = m.get("type")
                if mtype == "button":                       # template quick-reply
                    payload = (m.get("button") or {}).get("payload")
                elif mtype == "interactive":                # interactive button reply
                    payload = ((m.get("interactive") or {}).get("button_reply") or {}).get("id")
                elif mtype == "text":
                    text = (m.get("text") or {}).get("body")
                if frm and (payload or text):
                    await _handle_inbound(client, frm, payload, text)
            # Delivery-STATUS events (sent/delivered/read/failed) ride the same subscription → record them
            # onto send_log so a silently-dropped send is visible. Fully guarded → never 500s the webhook.
            try:
                # Sync Supabase calls — hop a thread so a slow DB can never freeze the event loop
                # (the sync-in-async class that caused the 2026-07-30 whole-backend freeze).
                await asyncio.to_thread(
                    _record_delivery_statuses, (ch.get("value", {}) or {}).get("statuses", []) or [])
            except Exception:
                pass
    return {"ok": True}
