"""Notify / subscribe API — /api/v1/notify/*

Sends any registered report (and the flags list) by email (Resend) and WhatsApp
(Meta Cloud API), on-demand or on a recurring schedule. Files are generated
server-side (report_registry + render) so on-demand and scheduled output match the
browser export. Scheduling is driven by Supabase pg_cron hitting POST /notify/run-due.
"""
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, HTTPException, Header

from app.core.database import get_supabase
from app.core.config import settings
from . import report_registry, render
from .channels import email_resend, whatsapp_meta

router = APIRouter(prefix="/notify", tags=["Notify"])
ORG_ID = "00000000-0000-0000-0000-000000000001"


def sb():
    # notify.* tables live in the notify schema (migration 010).
    return get_supabase().schema("notify")


# ── recipients ────────────────────────────────────────────────────────────────
@router.get("/recipients")
async def list_recipients(org_id: str = ORG_ID):
    """Saved notify recipients + active employees (with contact info) for the picker."""
    saved = sb().table("recipients").select("*").eq("org_id", org_id).order("name").execute().data or []
    emps = get_supabase().schema("storeops").table("employees") \
        .select("name,email,phone,home_store").eq("org_id", org_id).eq("is_active", True).order("name").execute().data or []
    employees = [{"name": e.get("name"), "email": e.get("email"), "phone": e.get("phone"),
                  "store": e.get("home_store")} for e in emps if e.get("email") or e.get("phone")]
    return {"saved": saved, "employees": employees}


@router.post("/recipients")
async def create_recipient(body: dict, org_id: str = ORG_ID):
    row = {"org_id": org_id, "name": body.get("name"), "email": body.get("email"),
           "phone": body.get("phone"), "employee_id": body.get("employee_id")}
    r = sb().table("recipients").insert(row).execute()
    return r.data[0] if r.data else row


@router.put("/recipients/{rid}")
async def update_recipient(rid: str, body: dict, org_id: str = ORG_ID):
    allowed = {k: v for k, v in body.items() if k in ("name", "email", "phone", "employee_id")}
    r = sb().table("recipients").update(allowed).eq("org_id", org_id).eq("id", rid).execute()
    return r.data[0] if r.data else {}


@router.delete("/recipients/{rid}")
async def delete_recipient(rid: str, org_id: str = ORG_ID):
    sb().table("recipients").delete().eq("org_id", org_id).eq("id", rid).execute()
    return {"deleted": rid}


# ── reports + health ─────────────────────────────────────────────────────────
@router.get("/reports")
async def list_reports():
    return {"reports": report_registry.list_reports()}


@router.get("/health")
async def health():
    return {"email_configured": email_resend.is_configured(),
            "whatsapp_configured": whatsapp_meta.is_configured(),
            "from_email": settings.NOTIFY_FROM_EMAIL or None}


# ── unified report → designated recipient routing (Theme 4) ───────────────────
@router.get("/report-config")
async def get_report_config(org_id: str = ORG_ID):
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
async def put_report_config(report_key: str, body: dict, org_id: str = ORG_ID):
    """Set the designated recipients + channels for one report."""
    if report_key not in report_registry.REPORTS:
        raise HTTPException(400, f"unknown report_key '{report_key}'")
    row = {"org_id": org_id, "report_key": report_key,
           "recipient_ids": body.get("recipient_ids") or [],
           "ad_hoc_emails": body.get("ad_hoc_emails") or [],
           "ad_hoc_phones": body.get("ad_hoc_phones") or [],
           "channels": body.get("channels") or ["email"],
           "formats": body.get("formats") or ["xlsx", "pdf"],
           "is_active": body.get("is_active", True),
           "updated_at": datetime.now(timezone.utc).isoformat()}
    try:
        sb().table("report_config").upsert(row, on_conflict="org_id,report_key").execute()
    except Exception as e:
        raise HTTPException(500, f"save failed — run migration 044 first: {e}")
    return {"ok": True, "report_key": report_key}


@router.post("/send-to-designated")
async def send_to_designated(body: dict, org_id: str = ORG_ID):
    """Send a report to its CONFIGURED designated recipients (report_config). The single entry point
    every module uses for 'send this to the designated person' — envelope-mismatch alerts, daily
    targets, cash pickup, etc. Body: report_key, filters?. No recipients in the body — they come from
    the routing config."""
    report_key = body.get("report_key")
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
        return await _dispatch(org_id, report_key, body.get("filters") or {}, channels,
                               cfg.get("formats"), emails, phones, body.get("message"),
                               triggered_by="designated")
    except (KeyError, ValueError) as e:
        raise HTTPException(400, str(e))


# ── dispatch core (shared by on-demand send + scheduled run-due) ──────────────
def _normalize_phone(raw) -> str:
    """WhatsApp Cloud API needs digits-only with a country code. Strip +/space/
    dashes/parens; prepend US country code 1 to bare 10-digit numbers (the common
    way reps are stored). Already-prefixed or international numbers pass through.
    Without this, an unprefixed 5162330422 hits (#131030) 'not in allowed list'."""
    digits = "".join(ch for ch in str(raw or "") if ch.isdigit())
    if len(digits) == 10:
        digits = "1" + digits
    return digits


def _resolve_targets(client_notify, org_id, body) -> tuple[list, list]:
    """Return (emails, phones) from explicit lists + saved recipient_ids."""
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
    # normalize phones (country code) then dedup, preserve order
    phones = [_normalize_phone(p) for p in phones]
    return list(dict.fromkeys([e for e in emails if e])), list(dict.fromkeys([p for p in phones if p]))


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


async def _dispatch(org_id, report_key, filters, channels, formats, emails, phones, message,
                    subscription_id=None, triggered_by="manual"):
    """Build the report once, deliver to every target, log each attempt."""
    channels = channels or ["email"]
    formats = [f for f in (formats or ["xlsx", "pdf"]) if f in ("xlsx", "pdf")] or ["xlsx"]

    payload = await report_registry.build_payload(report_key, org_id, filters or {})
    link = settings.APP_PUBLIC_URL.rstrip("/") + (payload.get("live_path") or "/")

    # Render each requested format once: (bytes, filename, mime)
    files = [render.render(payload, fmt) for fmt in formats]
    email_attachments = [(fn, data, mime) for (data, fn, mime) in files]

    title = payload.get("title") or report_key
    subject = title if not payload.get("subtitle") else f"{title} — {payload['subtitle']}"
    log_rows = []
    sent = failed = 0

    def _log(channel, target, status, err="", mid=""):
        nonlocal sent, failed
        sent += status == "sent"
        failed += status == "failed"
        log_rows.append({
            "org_id": org_id, "subscription_id": subscription_id, "report_key": report_key,
            "channel": channel, "target": target, "status": status,
            "provider_message_id": mid or None, "error": (err or None),
            "filters": filters or {}, "triggered_by": triggered_by,
        })

    if "email" in channels:
        html = _email_html(payload, link, message)
        for addr in emails:
            try:
                mid = await email_resend.send_email(addr, subject, html, email_attachments)
                _log("email", addr, "sent", mid=mid)
            except Exception as e:
                _log("email", addr, "failed", err=str(e))

    if "whatsapp" in channels:
        body_text = f"{title} — {link}"
        for ph in phones:
            for (data, fn, mime) in files:
                try:
                    mid = await whatsapp_meta.send_document(ph, data, mime, fn, body_text)
                    _log("whatsapp", ph, "sent", mid=mid)
                except Exception as e:
                    _log("whatsapp", ph, "failed", err=str(e))

    if log_rows:
        try:
            sb().table("send_log").insert(log_rows).execute()
        except Exception:
            pass  # never let logging failure abort a real send

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

    def _log(channel, target, status, err="", mid=""):
        nonlocal sent, failed
        sent += status == "sent"
        failed += status == "failed"
        log_rows.append({"org_id": org_id, "report_key": "(client-export)", "channel": channel,
                         "target": target, "status": status, "provider_message_id": mid or None,
                         "error": (err or None), "triggered_by": "manual"})

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
        for ph in phones:
            for (data, fn, mime) in files:
                try:
                    mid = await whatsapp_meta.send_document(ph, data, mime, fn, f"{title} — {link}")
                    _log("whatsapp", ph, "sent", mid=mid)
                except Exception as e:
                    _log("whatsapp", ph, "failed", err=str(e))
    if log_rows:
        try:
            sb().table("send_log").insert(log_rows).execute()
        except Exception:
            pass
    return {"sent": sent, "failed": failed, "targets": {"emails": emails, "phones": phones}, "channels": channels}


@router.post("/send")
async def send_now(body: dict, org_id: str = ORG_ID):
    """On-demand send. Body: report_key, filters, channels[], formats[],
    emails[], phones[], recipient_ids[], message."""
    report_key = body.get("report_key")
    if report_key not in report_registry.REPORTS:
        raise HTTPException(400, f"unknown report_key '{report_key}'")
    emails, phones = _resolve_targets(sb(), org_id, body)
    if not emails and not phones:
        raise HTTPException(400, "no recipients (provide emails, phones, or recipient_ids)")
    channels = body.get("channels") or (["email"] if emails else []) + (["whatsapp"] if phones else [])
    try:
        return await _dispatch(org_id, report_key, body.get("filters") or {}, channels,
                               body.get("formats"), emails, phones, body.get("message"),
                               triggered_by="manual")
    except (KeyError, ValueError) as e:
        raise HTTPException(400, str(e))


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
    row = {k: body.get(k) for k in (
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
async def list_subscriptions(org_id: str = ORG_ID):
    return sb().table("subscriptions").select("*").eq("org_id", org_id) \
        .order("created_at", desc=True).execute().data or []


@router.post("/subscriptions")
async def create_subscription(body: dict, org_id: str = ORG_ID):
    if body.get("report_key") not in report_registry.REPORTS:
        raise HTTPException(400, f"unknown report_key '{body.get('report_key')}'")
    r = sb().table("subscriptions").insert(_sub_with_next(body, org_id)).execute()
    return r.data[0] if r.data else {}


@router.put("/subscriptions/{sid}")
async def update_subscription(sid: str, body: dict, org_id: str = ORG_ID):
    r = sb().table("subscriptions").update(_sub_with_next(body, org_id)) \
        .eq("org_id", org_id).eq("id", sid).execute()
    return r.data[0] if r.data else {}


@router.delete("/subscriptions/{sid}")
async def delete_subscription(sid: str, org_id: str = ORG_ID):
    sb().table("subscriptions").delete().eq("org_id", org_id).eq("id", sid).execute()
    return {"deleted": sid}


@router.get("/send-log")
async def send_log(org_id: str = ORG_ID, limit: int = 200):
    return sb().table("send_log").select("*").eq("org_id", org_id) \
        .order("created_at", desc=True).limit(min(max(limit, 1), 1000)).execute().data or []


# ── scheduler entrypoint (called by Supabase pg_cron via pg_net) ──────────────
@router.post("/run-due")
async def run_due(x_notify_secret: str = Header(default="")):
    """Fire every subscription whose next_run_at has passed. Secret-guarded."""
    if not settings.NOTIFY_RUN_SECRET or x_notify_secret != settings.NOTIFY_RUN_SECRET:
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
        emails, phones = _resolve_targets(sb(), org_id, body)
        result = {"error": None}
        try:
            result = await _dispatch(
                org_id, s["report_key"], s.get("filters") or {}, s.get("channels"),
                s.get("formats"), emails, phones, None,
                subscription_id=s.get("id"), triggered_by="schedule")
        except Exception as e:
            result = {"error": str(e), "sent": 0, "failed": 0}
        nxt = _compute_next_run(s.get("frequency"), s.get("day_of_week"),
                                s.get("day_of_month"), s.get("hour"), s.get("timezone"))
        sb().table("subscriptions").update(
            {"last_run_at": now_iso, "next_run_at": nxt}).eq("id", s["id"]).execute()
        ran.append({"id": s.get("id"), "report_key": s.get("report_key"), **result})

    return {"ran": len(ran), "results": ran}
