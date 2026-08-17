"""Notify / subscribe API — /api/v1/notify/*

Sends any registered report (and the flags list) by email (Resend) and WhatsApp
(Meta Cloud API), on-demand or on a recurring schedule. Files are generated
server-side (report_registry + render) so on-demand and scheduled output match the
browser export. Scheduling is driven by Supabase pg_cron hitting POST /notify/run-due.
"""
import base64
import urllib.parse
from datetime import datetime, timedelta, timezone
from typing import Any

from fastapi import APIRouter, HTTPException, Header, Response
from starlette.concurrency import run_in_threadpool

from app.core.database import get_supabase
from app.core.config import settings
from app.core.schemas import LaxModel
from app.core.run_secret import verify_notify_secret
from app.modules.core.run_for_tenant import run_for_tenant_async, TenantNotRunnable
from app.modules.core import auth_security as _sec
from . import report_registry, render, download_token, whatsapp_window
from .channels import email_resend, whatsapp_meta

router = APIRouter(prefix="/notify", tags=["Notify"])
ORG_ID = "00000000-0000-0000-0000-000000000001"


# ── Request bodies (Item 15 Pydantic rollout — lax so legacy callers never break) ──────────────
class CreateRecipientIn(LaxModel):
    name: Any = None
    email: Any = None
    phone: Any = None
    employee_id: Any = None


class UpdateRecipientIn(LaxModel):
    name: Any = None
    email: Any = None
    phone: Any = None
    employee_id: Any = None


class PutReportConfigIn(LaxModel):
    recipient_ids: Any = None
    ad_hoc_emails: Any = None
    ad_hoc_phones: Any = None
    channels: Any = None
    formats: Any = None
    is_active: Any = True


class SendToDesignatedIn(LaxModel):
    report_key: Any = None
    filters: Any = None
    message: Any = None


class SendNowIn(LaxModel):
    report_key: Any = None
    filters: Any = None
    channels: Any = None
    formats: Any = None
    emails: Any = None
    phones: Any = None
    recipient_ids: Any = None
    message: Any = None


class PutNotifySettingsIn(LaxModel):
    download_link_expiry_days: Any = None


class SendEmailPlainIn(LaxModel):
    to: Any = None
    subject: Any = None
    html: Any = None
    text: Any = None


class SubscriptionIn(LaxModel):
    report_key: Any = None
    filters: Any = None
    channels: Any = None
    formats: Any = None
    recipient_ids: Any = None
    ad_hoc_emails: Any = None
    ad_hoc_phones: Any = None
    frequency: Any = None
    day_of_week: Any = None
    day_of_month: Any = None
    hour: Any = None
    timezone: Any = None
    is_active: Any = None
    name: Any = None
    created_by: Any = None


def sb():
    # notify.* tables live in the notify schema (migration 010).
    return get_supabase().schema("notify")


def _tenant_default_cc(org_id) -> str:
    """The tenant's default phone country code (twofa_policy.default_cc, additive JSON key; '+1'
    fallback). Best-effort — un-run/absent config degrades to '+1'. Never raises."""
    try:
        rows = (get_supabase().schema("storeops").table("tenants").select("twofa_policy")
                .eq("org_id", org_id).limit(1).execute().data) or []
        raw = (rows[0].get("twofa_policy") or {}).get("default_cc") if rows else None
        return _sec.normalize_cc(raw)
    except Exception:
        return _sec.DEFAULT_COUNTRY_CODE


# ── no-login download artifacts (owner directive: "send the PDF as is without logging in") ───────────
# Cap on the raw (pre-base64) file we persist in-row. Report files are small; a runaway export must never
# be base64'd into a giant TEXT row. Over-cap → skip storage, fall back to the live-report link (no crash).
MAX_ARTIFACT_BYTES = 8 * 1024 * 1024  # 8 MB


def _download_expiry_days(org_id) -> int:
    """Tenant-configurable expiry (days) for no-login download links —
    storeops.tenants.notify_policy.download_link_expiry_days. Default 7, clamped 1..90. Best-effort
    (un-run mig 713 / absent column → 7). Never raises."""
    try:
        rows = (get_supabase().schema("storeops").table("tenants").select("notify_policy")
                .eq("org_id", org_id).limit(1).execute().data) or []
        raw = (rows[0].get("notify_policy") or {}).get("download_link_expiry_days") if rows else None
        return max(1, min(int(raw), 90))
    except Exception:
        return 7


def _store_artifact(org_id, filename, mime, data: bytes, report_key=None, created_by=None):
    """Persist a sent file as a notify.send_artifact and return its no-login signed DOWNLOAD URL (an
    absolute backend url the recipient taps → the file streams with NO login). Returns None if storage
    is unavailable (un-run mig 713 → the caller falls back to the live-report link; never crashes a send).
    Single-file scope: the token references ONLY this row — no other org data is reachable from it."""
    try:
        if not data:
            return None
        if len(data) > MAX_ARTIFACT_BYTES:
            return None  # (m1) too large for in-row storage → fall back to the live-report link
        days = _download_expiry_days(org_id)
        expires = (datetime.now(timezone.utc) + timedelta(days=days)).isoformat()
        row = {"org_id": org_id, "filename": filename or "report",
               "mime": mime or "application/octet-stream",
               "content_b64": base64.b64encode(data).decode(), "size_bytes": len(data),
               "report_key": report_key, "created_by": created_by, "expires_at": expires}
        # NOTE (B1): if the backend ever ran on the anon key, this insert is DENIED by RLS and raises a
        # PostgREST APIError (a plain Exception subclass) → caught below → returns None → link fallback.
        res = sb().table("send_artifact").insert(row).execute()
        aid = (res.data or [{}])[0].get("id")
        if not aid:
            return None
        tok = download_token.sign(aid)
        if not tok:  # (M2) no download secret configured → fail closed to the live-report link
            return None
        return settings.API_PUBLIC_URL.rstrip("/") + "/api/v1/notify/dl/" + tok
    except Exception:
        return None


# ── recipients ────────────────────────────────────────────────────────────────
@router.get("/recipients")
def list_recipients(org_id: str = ORG_ID):
    """Saved notify recipients + active employees (with contact info) for the picker."""
    saved = sb().table("recipients").select("*").eq("org_id", org_id).order("name").execute().data or []
    emps = get_supabase().schema("storeops").table("employees") \
        .select("name,email,phone,home_store").eq("org_id", org_id).eq("is_active", True).order("name").execute().data or []
    employees = [{"name": e.get("name"), "email": e.get("email"), "phone": e.get("phone"),
                  "store": e.get("home_store")} for e in emps if e.get("email") or e.get("phone")]
    return {"saved": saved, "employees": employees}


def _norm_save_phone(org_id, raw):
    """Normalize a phone for STORAGE (canonical '+<cc>...'). Empty → None (email-only recipient is fine).
    Un-normalizable → raise 400 with a clear reason (never silently store garbage)."""
    if not (raw or "").strip():
        return None
    v, err = _sec.normalize_phone(raw, _tenant_default_cc(org_id))
    if err or not v:
        raise HTTPException(400, err or "Enter a valid phone number.")
    return v


@router.post("/recipients")
def create_recipient(body: CreateRecipientIn, org_id: str = ORG_ID):
    row = {"org_id": org_id, "name": body.name, "email": body.email,
           "phone": _norm_save_phone(org_id, body.phone), "employee_id": body.employee_id}
    r = sb().table("recipients").insert(row).execute()
    return r.data[0] if r.data else row


@router.put("/recipients/{rid}")
def update_recipient(rid: str, body: UpdateRecipientIn, org_id: str = ORG_ID):
    allowed = {k: getattr(body, k) for k in ("name", "email", "phone", "employee_id")
               if k in body.model_fields_set}
    if "phone" in allowed:
        allowed["phone"] = _norm_save_phone(org_id, allowed.get("phone"))
    r = sb().table("recipients").update(allowed).eq("org_id", org_id).eq("id", rid).execute()
    return r.data[0] if r.data else {}


@router.delete("/recipients/{rid}")
def delete_recipient(rid: str, org_id: str = ORG_ID):
    sb().table("recipients").delete().eq("org_id", org_id).eq("id", rid).execute()
    return {"deleted": rid}


# ── reports + health ─────────────────────────────────────────────────────────
@router.get("/reports")
def list_reports():
    return {"reports": report_registry.list_reports()}


@router.get("/health")
def health():
    """Cheap, NETWORK-FREE configuration truth for the notify surfaces.

    The extra whatsapp_* keys answer the questions that made the 2026-08-05 silent-failure incident hard
    to see from inside the app: is the delivery-status webhook actually wired (verify token + app secret),
    is window tracking live (mig 723), and which template/ladder are we on. NO secrets are returned — only
    booleans and the non-secret template name/graph version. The live "which account am I sending as"
    probe is the separate, super-admin-gated GET /notify/whatsapp-account (it calls Meta)."""
    try:
        window_tracking = whatsapp_window.tracking_available()
    except Exception:
        window_tracking = False
    return {"email_configured": email_resend.is_configured(),
            "whatsapp_configured": whatsapp_meta.is_configured(),
            "from_email": settings.NOTIFY_FROM_EMAIL or None,
            # ── WhatsApp deliverability diagnostics (no network, no secrets) ──
            "whatsapp_template": settings.WHATSAPP_TEMPLATE_NAME or None,
            "whatsapp_template_lang": settings.WHATSAPP_TEMPLATE_LANG or None,
            "whatsapp_graph_version": settings.WHATSAPP_GRAPH_VERSION or None,
            "whatsapp_doc_header": bool(settings.WHATSAPP_TEMPLATE_DOC_HEADER),
            "whatsapp_verify_token_set": bool(settings.WHATSAPP_VERIFY_TOKEN),
            "whatsapp_app_secret_set": bool(settings.WHATSAPP_APP_SECRET),
            "whatsapp_webhook_ready": bool(settings.WHATSAPP_VERIFY_TOKEN and settings.WHATSAPP_APP_SECRET),
            "whatsapp_window_tracking": bool(window_tracking),
            "whatsapp_window_hours": whatsapp_window.window_hours(),
            "whatsapp_freeform_when_unknown": bool(
                getattr(settings, "WHATSAPP_FREEFORM_WHEN_UNKNOWN", False)),
            "whatsapp_webhook_url": (settings.API_PUBLIC_URL or "").rstrip("/")
                                    + "/api/v1/remediation/whatsapp-webhook"}


@router.get("/whatsapp-account")
async def whatsapp_account(org_id: str = ORG_ID, authorization: str = Header(default=""),
                           x_active_org: str = Header(default="")):
    """DIAGNOSTIC — "which WhatsApp account am I actually sending as?" Calls the Meta Graph API for the
    configured phone number id and reports display_phone_number / verified_name / quality_rating / etc.

    SUPER-ADMIN ONLY: the Meta WABA is PLATFORM infrastructure shared by every tenant (one app, one
    number), so this is not tenant data and must not be readable by a tenant admin. No token is ever
    returned or logged (Graph error bodies are redacted). Never 500s on a Meta outage — it reports
    {"ok": false, "error": ...} so the page can render the failure."""
    from app.modules.core.router import _uid_from_token, _resolve_caller
    uid = _uid_from_token(authorization)
    if not uid:
        raise HTTPException(401, "not authenticated")
    caller = await run_in_threadpool(_resolve_caller, get_supabase(), uid, x_active_org)
    if not caller or not caller.get("super_admin"):
        raise HTTPException(403, "super-admin only — the WhatsApp Business account is platform-wide")
    return await whatsapp_meta.account_info()


# ── unified report → designated recipient routing (Theme 4) ───────────────────
@router.get("/report-config")
def get_report_config(org_id: str = ORG_ID):
    """EVERY sendable report merged with its saved designated-recipient config, so one page can show
    the full routing table (report → who it's sent to). Reports with no config come back with empty
    recipients + sensible defaults."""
    saved = {}
    try:
        for r in (sb().table("report_config").select("*").eq("org_id", org_id).execute().data or []):
            saved[r["report_key"]] = r
    except Exception:
        pass  # table may not exist yet (migration 044)
    out = []
    for r in report_registry.list_reports():
        c = saved.get(r["key"]) or {}
        out.append({"report_key": r["key"], "label": r["label"],
                    "recipient_ids": c.get("recipient_ids") or [],
                    "ad_hoc_emails": c.get("ad_hoc_emails") or [],
                    "ad_hoc_phones": c.get("ad_hoc_phones") or [],
                    "channels": c.get("channels") or ["email"],
                    "formats": c.get("formats") or ["xlsx", "pdf"],
                    "is_active": c.get("is_active", True),
                    "configured": r["key"] in saved})
    return {"reports": out}


@router.put("/report-config/{report_key}")
def put_report_config(report_key: str, body: PutReportConfigIn, org_id: str = ORG_ID):
    """Set the designated recipients + channels for one report."""
    if report_key not in report_registry.REPORTS:
        raise HTTPException(400, f"unknown report_key '{report_key}'")
    # Country-code-normalize ad-hoc phones on save (canonical '+<cc>...'); keep an un-normalizable
    # entry verbatim so nothing is silently lost (the send-time normalize gives it a second pass).
    _cc = _tenant_default_cc(org_id)
    _phones = []
    for p in (body.ad_hoc_phones or []):
        v, _e = _sec.normalize_phone(p, _cc)
        _phones.append(v or p)
    row = {"org_id": org_id, "report_key": report_key,
           "recipient_ids": body.recipient_ids or [],
           "ad_hoc_emails": body.ad_hoc_emails or [],
           "ad_hoc_phones": _phones,
           "channels": body.channels or ["email"],
           "formats": body.formats or ["xlsx", "pdf"],
           "is_active": body.is_active,
           "updated_at": datetime.now(timezone.utc).isoformat()}
    try:
        sb().table("report_config").upsert(row, on_conflict="org_id,report_key").execute()
    except Exception as e:
        raise HTTPException(500, f"save failed — run migration 044 first: {e}")
    return {"ok": True, "report_key": report_key}


@router.post("/send-to-designated")
async def send_to_designated(body: SendToDesignatedIn, org_id: str = ORG_ID, authorization: str = Header(default="")):
    """Send a report to its CONFIGURED designated recipients (report_config). The single entry point
    every module uses for 'send this to the designated person' — envelope-mismatch alerts, daily
    targets, cash pickup, etc. Body: report_key, filters?. No recipients in the body — they come from
    the routing config."""
    report_key = body.report_key
    if report_key not in report_registry.REPORTS:
        raise HTTPException(400, f"unknown report_key '{report_key}'")
    cfg = (sb().table("report_config").select("*").eq("org_id", org_id)
           .eq("report_key", report_key).limit(1).execute().data or [])
    if not cfg or cfg[0].get("is_active") is False:
        return {"sent": 0, "failed": 0, "skipped": "no active designated recipients for this report"}
    cfg = cfg[0]
    emails, phones = _resolve_targets(sb(), org_id, {
        "recipient_ids": cfg.get("recipient_ids") or [],
        "emails": cfg.get("ad_hoc_emails") or [],
        "phones": cfg.get("ad_hoc_phones") or []})
    if not emails and not phones:
        return {"sent": 0, "failed": 0, "skipped": "designated recipients have no contact info"}
    channels = cfg.get("channels") or (["email"] if emails else []) + (["whatsapp"] if phones else [])
    try:
        # NOTE: other modules call this function IN-PROCESS (commcalc sales-recon) without the
        # header, which binds FastAPI's Header sentinel; build_payload normalizes a non-str to ""
        # (= no caller ⇒ the handler's own org-wide path), so that path cannot crash.
        return await _dispatch(org_id, report_key, body.filters or {}, channels,
                               cfg.get("formats"), emails, phones, body.message,
                               triggered_by="designated", authorization=authorization)
    except (KeyError, ValueError) as e:
        raise HTTPException(400, str(e))


# ── dispatch core (shared by on-demand send + scheduled run-due) ──────────────
def _resolve_targets(client_notify, org_id, body) -> tuple[list, list]:
    """Return (emails, phones) from explicit lists + saved recipient_ids.

    Phones are country-code-normalized to canonical E.164 '+<cc>...' using the tenant's default_cc.
    This is also the SEND-TIME rescue for pre-existing bare-10-digit stored rows (e.g. '2125550123'):
    they normalize to '+12125550123' here even without a data backfill. The WhatsApp transport layer
    (whatsapp_meta._to_number) strips the '+' to digits for Meta — byte-compatible with the prior send
    format — so this changes storage/dedup canonicalization, not what Meta receives for existing rows."""
    cc = _tenant_default_cc(org_id)
    emails = list(body.get("emails") or [])
    phones = list(body.get("phones") or [])
    rids = body.get("recipient_ids") or []
    if rids:
        rows = client_notify.table("recipients").select("email,phone") \
            .eq("org_id", org_id).in_("id", rids).execute().data or []
        for r in rows:
            if r.get("email"):
                emails.append(r["email"])
            if r.get("phone"):
                phones.append(r["phone"])
    # normalize phones (country code) then dedup, preserve order; drop un-normalizable entries.
    norm_phones = []
    for p in phones:
        v, _err = _sec.normalize_phone(p, cc)
        if v:
            norm_phones.append(v)
    return (list(dict.fromkeys([e for e in emails if e])),
            list(dict.fromkeys(norm_phones)))


def _email_html(payload, link, message) -> str:
    title = payload.get("title") or "Report"
    sub = payload.get("subtitle") or ""
    msg = f"<p>{message}</p>" if message else ""
    return (
        f"<div style='font-family:Arial,sans-serif;max-width:560px'>"
        f"<h2 style='color:#1E3A5F;margin:0 0 4px'>{title}</h2>"
        f"<p style='color:#666;margin:0 0 12px'>{sub}</p>"
        f"{msg}"
        f"<p><a href='{link}' style='background:#1E3A5F;color:#fff;padding:9px 16px;"
        f"border-radius:6px;text-decoration:none;display:inline-block'>View live report</a></p>"
        f"<p style='color:#999;font-size:12px;margin-top:16px'>"
        f"The report is attached. Sent automatically by MetricsPro.</p></div>"
    )


def _insert_log(log_rows) -> None:
    """Write send_log rows. Never lets a logging failure abort a real send, and DEGRADES GRACEFULLY when
    migration 723 has not been run: PostgREST rejects the whole batch if `delivery_route` doesn't exist,
    so we strip that one key and retry once. Without the retry an un-run migration would silently lose the
    ENTIRE send history — exactly the class of failure this package exists to remove."""
    if not log_rows:
        return
    try:
        sb().table("send_log").insert(log_rows).execute()
        return
    except Exception:
        pass
    if not any("delivery_route" in r for r in log_rows):
        return
    try:
        sb().table("send_log").insert(
            [{k: v for k, v in r.items() if k != "delivery_route"} for r in log_rows]).execute()
    except Exception:
        pass


async def _dispatch(org_id, report_key, filters, channels, formats, emails, phones, message,
                    subscription_id=None, triggered_by="manual", authorization="", tz=""):
    """Build the report once, deliver to every target, log each attempt.

    `authorization` is the SENDING caller's header (on-demand) and `tz` the schedule's timezone
    (scheduled). Both are consumed only by builders that opted in — see report_registry's docstring;
    they never touch the delivery payload, and the token is never written to send_log."""
    channels = channels or ["email"]
    formats = [f for f in (formats or ["xlsx", "pdf"]) if f in ("xlsx", "pdf")] or ["xlsx"]

    payload = await report_registry.build_payload(report_key, org_id, filters or {},
                                                  authorization=authorization, tz=tz)
    link = settings.APP_PUBLIC_URL.rstrip("/") + (payload.get("live_path") or "/")

    # Render each requested format once: (bytes, filename, mime)
    files = [render.render(payload, fmt) for fmt in formats]
    email_attachments = [(fn, data, mime) for (data, fn, mime) in files]

    title = payload.get("title") or report_key
    subject = title if not payload.get("subtitle") else f"{title} — {payload['subtitle']}"
    log_rows = []
    sent = failed = 0

    def _log(channel, target, status, err="", mid="", route=""):
        nonlocal sent, failed
        sent += status == "sent"
        failed += status == "failed"
        row = {
            "org_id": org_id, "subscription_id": subscription_id, "report_key": report_key,
            "channel": channel, "target": target, "status": status,
            "provider_message_id": mid or None, "error": (err or None),
            "filters": filters or {}, "triggered_by": triggered_by,
        }
        if route:
            row["delivery_route"] = route   # stripped + retried if mig 723 is un-run (see _insert_log)
        log_rows.append(row)

    if "email" in channels:
        html = _email_html(payload, link, message)
        for addr in emails:
            try:
                mid = await email_resend.send_email(addr, subject, html, email_attachments)
                _log("email", addr, "sent", mid=mid)
            except Exception as e:
                _log("email", addr, "failed", err=str(e))

    if "whatsapp" in channels:
        # Persist each file once as a no-login downloadable artifact; its signed direct-download url rides
        # in the WhatsApp body and IS the deliverable when Meta only permits a link (business-initiated,
        # outside the 24h window, no doc-header template). Best-effort — no artifact → the live-report link.
        dls = [_store_artifact(org_id, fn, mime, data, report_key) for (data, fn, mime) in files]
        for ph in phones:
            for (data, fn, mime), dl in zip(files, dls):
                body_text = f"{title} — {dl or link}"
                try:
                    res = await whatsapp_meta.send_document_detailed(ph, data, mime, fn, body_text)
                    _log("whatsapp", ph, "sent", mid=res.get("message_id"),
                         route=res.get("route") or "")
                except Exception as e:
                    _log("whatsapp", ph, "failed", err=str(e))

    _insert_log(log_rows)

    return {"sent": sent, "failed": failed, "targets": {"emails": emails, "phones": phones},
            "formats": formats, "channels": channels}


@router.post("/send-file")
async def send_file(body: dict, org_id: str = ORG_ID):
    """Deliver an ALREADY-RENDERED report file (built in the browser by <ReportShell> / lib/export)
    to reps by email + WhatsApp — the universal path so EVERY report can be sent without registering
    it server-side in report_registry. Body: {title?, message?, channels[], recipient_ids[], emails[],
    phones[], files:[{filename, mime, content_b64}]}. Reuses the same recipient resolution + delivery
    helpers + send_log as /send."""
    import base64
    files_in = body.get("files") or []
    if not files_in:
        raise HTTPException(400, "no files (provide files:[{filename, mime, content_b64}])")
    files = []
    for f in files_in:
        try:
            data = base64.b64decode(f.get("content_b64") or "")
        except Exception:
            raise HTTPException(400, f"bad base64 for {f.get('filename')}")
        if not data:
            continue
        files.append((data, (f.get("filename") or "report"), (f.get("mime") or "application/octet-stream")))
    if not files:
        raise HTTPException(400, "all files were empty")

    emails, phones = _resolve_targets(sb(), org_id, body)
    if not emails and not phones:
        raise HTTPException(400, "no recipients (provide emails, phones, or recipient_ids)")
    channels = body.get("channels") or ((["email"] if emails else []) + (["whatsapp"] if phones else []))
    title = (body.get("title") or "Report").strip()
    message = body.get("message") or ""
    link = settings.APP_PUBLIC_URL.rstrip("/") + "/"
    sent = failed = 0
    log_rows = []

    def _log(channel, target, status, err="", mid="", route=""):
        nonlocal sent, failed
        sent += status == "sent"
        failed += status == "failed"
        row = {"org_id": org_id, "report_key": "(client-export)", "channel": channel,
               "target": target, "status": status, "provider_message_id": mid or None,
               "error": (err or None), "triggered_by": "manual"}
        if route:
            row["delivery_route"] = route   # stripped + retried if mig 723 is un-run (see _insert_log)
        log_rows.append(row)

    if "email" in channels and emails:
        attachments = [(fn, data, mime) for (data, fn, mime) in files]
        html = f"<p>{(message or 'Please find the attached report.')}</p><p style='color:#64748b;font-size:12px'>{title}</p>"
        for addr in emails:
            try:
                mid = await email_resend.send_email(addr, title, html, attachments)
                _log("email", addr, "sent", mid=mid)
            except Exception as e:
                _log("email", addr, "failed", err=str(e))
    if "whatsapp" in channels and phones:
        # Same no-login artifact path as _dispatch: the tapped link downloads the exact file, no login.
        dls = [_store_artifact(org_id, fn, mime, data, "(client-export)") for (data, fn, mime) in files]
        for ph in phones:
            for (data, fn, mime), dl in zip(files, dls):
                try:
                    res = await whatsapp_meta.send_document_detailed(
                        ph, data, mime, fn, f"{title} — {dl or link}")
                    _log("whatsapp", ph, "sent", mid=res.get("message_id"),
                         route=res.get("route") or "")
                except Exception as e:
                    _log("whatsapp", ph, "failed", err=str(e))
    _insert_log(log_rows)
    return {"sent": sent, "failed": failed, "targets": {"emails": emails, "phones": phones}, "channels": channels}


def _content_disposition(filename) -> str:
    """(m2) A Content-Disposition value safe for ANY filename. CR/LF & control chars are stripped
    (header-injection guard) and non-latin-1 names (CJK/emoji/quotes) can never crash the ASGI header
    encoder (which encodes headers as latin-1 → a 500 on a VALID token otherwise). Serves an ASCII-only
    `filename=` fallback for legacy clients PLUS an RFC 5987 `filename*` (UTF-8, percent-encoded) carrying
    the true name for modern clients."""
    raw = "".join(c for c in (filename or "report") if ord(c) >= 32 and c != "\x7f")
    raw = raw.strip() or "report"
    ascii_name = "".join(c if (32 <= ord(c) < 127 and c not in '"\\') else "_" for c in raw) or "report"
    star = urllib.parse.quote(raw, safe="")
    return f"attachment; filename=\"{ascii_name}\"; filename*=UTF-8''{star}"


# ── PUBLIC no-login download (owner directive: "the PDF should be sent as is without logging in") ─────
# This route is on the tenant-middleware PUBLIC allowlist (core/tenant_middleware.py `/api/v1/notify/dl`,
# segment-boundary). The TOKEN is the only auth: an HMAC capability over exactly ONE artifact id. Any
# failure (bad/forged token, unknown id, expired, empty) returns an IDENTICAL 404 with no detail, so a
# probe learns nothing (anti-enumeration). No org_id is taken from the request; nothing but the one file
# is reachable from the token.
@router.get("/dl/{token}")
def download_artifact(token: str):
    aid = download_token.verify(token)
    if not aid:
        raise HTTPException(404, "Not found")
    try:
        rows = (get_supabase().schema("notify").table("send_artifact").select("*")
                .eq("id", aid).limit(1).execute().data) or []
    except Exception:
        raise HTTPException(404, "Not found")   # un-run mig 713 / query error → uniform 404
    if not rows:
        raise HTTPException(404, "Not found")
    art = rows[0]
    # Expiry — uniform 404, never distinguished from a missing/forged token.
    try:
        expired = _sec.otp_is_expired(_sec.now_ts(), art.get("expires_at"))
    except Exception:
        expired = True
    if expired:
        raise HTTPException(404, "Not found")
    try:
        data = base64.b64decode(art.get("content_b64") or "")
    except Exception:
        data = b""
    if not data:
        raise HTTPException(404, "Not found")
    # Best-effort download audit (never blocks the download).
    now_iso = datetime.now(timezone.utc).isoformat()
    try:
        get_supabase().schema("notify").table("send_artifact").update(
            {"download_count": int(art.get("download_count") or 0) + 1,
             "last_downloaded_at": now_iso}).eq("id", aid).execute()
    except Exception:
        pass
    try:
        sb().table("send_log").insert({
            "org_id": art.get("org_id"), "report_key": art.get("report_key") or "(download)",
            "channel": "download", "target": (art.get("filename") or aid)[:120],
            "status": "sent", "triggered_by": "download"}).execute()
    except Exception:
        pass
    return Response(content=data, media_type=(art.get("mime") or "application/octet-stream"),
                    headers={"Content-Disposition": _content_disposition(art.get("filename")),
                             "Cache-Control": "no-store"})


# ── tenant notify settings (RULE TWO — download-link expiry, default 7 days) ──────────────────────────
@router.get("/settings")
def get_settings(org_id: str = ORG_ID):
    return {"download_link_expiry_days": _download_expiry_days(org_id)}


@router.put("/settings")
def put_settings(body: PutNotifySettingsIn, org_id: str = ORG_ID,
                       authorization: str = Header(default=""), x_active_org: str = Header(default="")):
    # M1 (per-setting edit-permissions doctrine): gate on the 'notify_policy' settings area, resolving the
    # caller from the auth header (same shape as core's put_tenant_settings). GET stays open to the org.
    from app.modules.core.router import _uid_from_token, _resolve_caller, _can_edit_setting
    uid = _uid_from_token(authorization)
    if not uid:
        raise HTTPException(401, "not authenticated")
    caller = _resolve_caller(get_supabase(), uid, x_active_org)
    if not caller:
        raise HTTPException(403, "no tenant for this login")
    if not _can_edit_setting(caller, "notify_policy"):
        raise HTTPException(403, "you don't have permission to edit notify settings")
    try:
        days = max(1, min(int(body.download_link_expiry_days), 90))
    except Exception:
        raise HTTPException(400, "download_link_expiry_days must be a number between 1 and 90")
    try:
        st = get_supabase().schema("storeops").table("tenants")
        rows = st.select("notify_policy").eq("org_id", org_id).limit(1).execute().data or []
    except Exception as e:
        raise HTTPException(500, f"save failed — run migration 713 first: {e}")
    if not rows:  # (m3) no tenant record → don't silently no-op the update
        raise HTTPException(404, "no tenant record for this org — complete tenant setup first")
    try:
        # (m3) re-read-merge: only touch our key so a concurrent write to another notify_policy key isn't clobbered.
        pol = dict(rows[0].get("notify_policy") or {})
        pol["download_link_expiry_days"] = days
        st.update({"notify_policy": pol}).eq("org_id", org_id).execute()
    except Exception as e:
        raise HTTPException(500, f"save failed — run migration 713 first: {e}")
    return {"ok": True, "download_link_expiry_days": days}


@router.post("/send")
async def send_now(body: SendNowIn, org_id: str = ORG_ID, authorization: str = Header(default="")):
    """On-demand send. Body: report_key, filters, channels[], formats[],
    emails[], phones[], recipient_ids[], message."""
    report_key = body.report_key
    if report_key not in report_registry.REPORTS:
        raise HTTPException(400, f"unknown report_key '{report_key}'")
    emails, phones = _resolve_targets(sb(), org_id, {"emails": body.emails, "phones": body.phones,
                                                     "recipient_ids": body.recipient_ids})
    if not emails and not phones:
        raise HTTPException(400, "no recipients (provide emails, phones, or recipient_ids)")
    channels = body.channels or (["email"] if emails else []) + (["whatsapp"] if phones else [])
    try:
        # The caller's header rides along so a caller-scoped report (flags / commissions / gp /
        # action_plan) exports exactly what that caller may see (AGENT_CONTRACT §3c) instead of
        # 500-ing on the FastAPI Header sentinel.
        return await _dispatch(org_id, report_key, body.filters or {}, channels,
                               body.formats, emails, phones, body.message,
                               triggered_by="manual", authorization=authorization)
    except (KeyError, ValueError) as e:
        raise HTTPException(400, str(e))


@router.post("/send-email")
async def send_email_plain(body: SendEmailPlainIn, org_id: str = ORG_ID):
    """Send a PLAIN email (not a registered report) via Resend — for handoff / action-item notes.
    Body: {to: str|[str], subject, html?|text?}. Returns per-recipient message id or error."""
    to = body.to or []
    if isinstance(to, str):
        to = [to]
    to = [t for t in to if t]
    if not to:
        raise HTTPException(400, "no recipient (provide `to`)")
    subject = (body.subject or "MetricsPro").strip()
    html = body.html
    if not html:
        text = body.text or ""
        import html as _h
        html = "<pre style='font-family:system-ui,-apple-system,sans-serif;white-space:pre-wrap'>" + _h.escape(text) + "</pre>"
    sent = []
    for addr in to:
        try:
            mid = await email_resend.send_email(addr, subject, html, [])
            sent.append({"to": addr, "id": mid})
        except Exception as e:
            sent.append({"to": addr, "error": str(e)})
    return {"sent": sent, "email_configured": email_resend.is_configured()}


# ── subscriptions (scheduled) ────────────────────────────────────────────────
def _compute_next_run(frequency, day_of_week, day_of_month, hour, tzname) -> str:
    """Next occurrence (UTC ISO) after now, in the subscription's timezone.
    day_of_week: 0=Mon..6=Sun (Python weekday)."""
    from zoneinfo import ZoneInfo
    from dateutil.relativedelta import relativedelta
    try:
        tz = ZoneInfo(tzname or "America/New_York")
    except Exception:
        tz = ZoneInfo("America/New_York")
    now = datetime.now(tz)
    hour = int(hour if hour is not None else 8)

    if frequency == "daily":
        nxt = now.replace(hour=hour, minute=0, second=0, microsecond=0)
        if nxt <= now:
            nxt += timedelta(days=1)
    elif frequency == "weekly":
        target = int(day_of_week if day_of_week is not None else 0)
        nxt = now.replace(hour=hour, minute=0, second=0, microsecond=0)
        delta = (target - nxt.weekday()) % 7
        nxt += timedelta(days=delta)
        if nxt <= now:
            nxt += timedelta(days=7)
    elif frequency == "monthly":
        dom = int(day_of_month if day_of_month is not None else 1)
        base = now.replace(hour=hour, minute=0, second=0, microsecond=0, day=1)

        def at_day(b):
            # clamp to month length
            last = (b + relativedelta(months=1, days=-1)).day
            return b.replace(day=min(dom, last))
        nxt = at_day(base)
        if nxt <= now:
            nxt = at_day(base + relativedelta(months=1))
    else:
        raise HTTPException(400, f"unknown frequency '{frequency}'")

    return nxt.astimezone(timezone.utc).isoformat()


def _sub_with_next(body, org_id):
    row = {k: getattr(body, k) for k in (
        "report_key", "filters", "channels", "formats", "recipient_ids",
        "ad_hoc_emails", "ad_hoc_phones", "frequency", "day_of_week", "day_of_month",
        "hour", "timezone", "is_active", "name", "created_by")}
    row["org_id"] = org_id
    if row.get("is_active") is None:
        row["is_active"] = True
    row["next_run_at"] = _compute_next_run(
        row.get("frequency"), row.get("day_of_week"), row.get("day_of_month"),
        row.get("hour"), row.get("timezone"))
    return {k: v for k, v in row.items() if v is not None}


@router.get("/subscriptions")
def list_subscriptions(org_id: str = ORG_ID):
    return sb().table("subscriptions").select("*").eq("org_id", org_id) \
        .order("created_at", desc=True).execute().data or []


@router.post("/subscriptions")
def create_subscription(body: SubscriptionIn, org_id: str = ORG_ID):
    if body.report_key not in report_registry.REPORTS:
        raise HTTPException(400, f"unknown report_key '{body.report_key}'")
    r = sb().table("subscriptions").insert(_sub_with_next(body, org_id)).execute()
    return r.data[0] if r.data else {}


@router.put("/subscriptions/{sid}")
def update_subscription(sid: str, body: SubscriptionIn, org_id: str = ORG_ID):
    r = sb().table("subscriptions").update(_sub_with_next(body, org_id)) \
        .eq("org_id", org_id).eq("id", sid).execute()
    return r.data[0] if r.data else {}


@router.delete("/subscriptions/{sid}")
def delete_subscription(sid: str, org_id: str = ORG_ID):
    sb().table("subscriptions").delete().eq("org_id", org_id).eq("id", sid).execute()
    return {"deleted": sid}


@router.get("/send-log")
def send_log(org_id: str = ORG_ID, limit: int = 200):
    return sb().table("send_log").select("*").eq("org_id", org_id) \
        .order("created_at", desc=True).limit(min(max(limit, 1), 1000)).execute().data or []


def _log_schedule_config_error(org_id, sub, err) -> None:
    """Record a mis-configured schedule as a CONFIG problem against that subscription.

    Best-effort in every direction (AGENT_CONTRACT §5: a missing migration must never break the
    caller): one core.failure_log row, org-scoped, category 'report_config' — deliberately NOT the
    'sweep_error' category run_for_tenant writes, because nothing crashed and re-running will not
    help; a human has to fix the schedule's filters."""
    try:
        name = sub.get("name") or sub.get("report_key") or "?"
        get_supabase().schema("core").table("failure_log").insert({
            "org_id": org_id,
            "category": "report_config",
            "severity": "warning",
            "source": f"notify.subscription/{sub.get('report_key') or '?'}"[:200],
            "message": f"Scheduled report '{name}' was skipped: {err}"[:1000],
            "detail": {"subscription_id": sub.get("id"), "report_key": sub.get("report_key"),
                       "filters": sub.get("filters") or {}, "error": str(err)},
            "remediation": ("This scheduled report's saved filters can't produce a report, so nothing "
                            "was sent (nothing crashed — re-running will not help). Open /notify → "
                            "Schedules, fix the filter named in the message, and the next run goes out. "
                            "Leaving a date filter BLANK is usually right for a recurring schedule: it "
                            "resolves to the current period every time."),
        }).execute()
    except Exception:
        pass


# ── scheduler entrypoint (called by Supabase pg_cron via pg_net) ──────────────
@router.post("/run-due")
async def run_due(x_notify_secret: str = Header(default="")):
    """Fire every subscription whose next_run_at has passed. Secret-guarded."""
    if not verify_notify_secret(x_notify_secret):
        raise HTTPException(403, "forbidden")
    now_iso = datetime.now(timezone.utc).isoformat()
    due = sb().table("subscriptions").select("*") \
        .eq("is_active", True).lte("next_run_at", now_iso).execute().data or []

    ran = []
    for s in due:
        org_id = s.get("org_id") or ORG_ID
        body = {"recipient_ids": s.get("recipient_ids") or [],
                "emails": s.get("ad_hoc_emails") or [],
                "phones": s.get("ad_hoc_phones") or []}
        result = {"error": None}

        # A schedule whose SAVED FILTERS can't build a report is a configuration problem, not a
        # crash. Validate BEFORE the tenant guard (cheap + pure) so it is reported against the
        # subscription instead of landing in core.failure_log as a sweep_error the operator would
        # chase as a bug — and so one bad schedule never opens a failed job_run. next_run_at is
        # still advanced below, so the sweep does not hot-loop on it.
        try:
            report_registry.validate_filters(s.get("report_key"), s.get("filters") or {})
            bad_config = None
        except report_registry.ReportConfigError as e:
            _log_schedule_config_error(org_id, s, e)
            bad_config = {"error": str(e), "config_error": True, "sent": 0, "failed": 0}

        # Each subscription runs under the central tenant guard: it asserts the subscription's tenant
        # exists + is active (a deactivated tenant is skipped, not sent to) and records a core.job_run
        # audit row + a core.failure_log entry on dispatch failure. money_scope="none" — notify sends a
        # report, it writes no money. See core.run_for_tenant.
        async def _job(ctx, _s=s, _body=body):
            emails, phones = _resolve_targets(sb(), ctx.org_id, _body)
            # No caller on a scheduled run ⇒ authorization stays "" (the report's own org-wide
            # path, which is what an admin-configured subscription means). `tz` lets a relative
            # date filter resolve on the schedule's own business day.
            return await _dispatch(
                ctx.org_id, _s["report_key"], _s.get("filters") or {}, _s.get("channels"),
                _s.get("formats"), emails, phones, None,
                subscription_id=_s.get("id"), triggered_by="schedule",
                tz=_s.get("timezone") or "")
        if bad_config is not None:
            result = bad_config          # nothing ran; the schedule needs a human, not a retry
        else:
            try:
                result = await run_for_tenant_async(org_id, "notify.subscription", _job)
            except TenantNotRunnable as e:
                result = {"error": str(e), "sent": 0, "failed": 0, "skipped": True}
            except Exception as e:
                result = {"error": str(e), "sent": 0, "failed": 0}

        # Advance the schedule either way, so a mis-configured one does not hot-loop the sweep.
        nxt = _compute_next_run(s.get("frequency"), s.get("day_of_week"),
                                s.get("day_of_month"), s.get("hour"), s.get("timezone"))
        sb().table("subscriptions").update(
            {"last_run_at": now_iso, "next_run_at": nxt}) \
            .eq("org_id", org_id).eq("id", s["id"]).execute()   # org-scoped write (RULE ONE)
        ran.append({"id": s.get("id"), "report_key": s.get("report_key"), **result})

    return {"ran": len(ran), "results": ran}


# ── Admin-attention providers (owner 2026-07-26) ──────────────────────────────────────────────────
# Imported for the @register_provider side effect ONLY: notify's delivery-wiring gaps (unconfigured
# channel, schedule with no recipients, sweep not firing, last send failed) surface in the login
# attention popup. No routes, no gates, no core edits — the registry exists for exactly this.
from . import attention as _attention   # noqa: E402,F401
