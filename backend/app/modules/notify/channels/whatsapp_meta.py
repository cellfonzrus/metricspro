"""WhatsApp delivery via the Meta Cloud API (Graph) — plain HTTPS, no SDK.

`send_document` delivers the ACTUAL report file wherever Meta permits, falling back to a link ONLY when
it can't. OWNER DIRECTIVE 2026-07-17: "the PDF should be sent as is without logging in" — so the fallback
link is a no-login DIRECT-DOWNLOAD url (built by the router), never a login page. Selection ladder
(pure `plan_delivery` / `classify_send_result` decide the order + how each result is read):

  1. If a doc-header template is configured (WHATSAPP_TEMPLATE_DOC_HEADER=true) → send the approved
     template WITH a `document` header component (attaches the real file; deliverable OUTSIDE the 24h
     customer-service window — the only way to attach a file business-initiated). On the "no title
     component" header error (#132018) the template lacks a real header → fall through.
  2. Free-form `type:"document"` message (NO template) → attaches the real file, but Meta delivers it
     only INSIDE the 24h window (recipient messaged us in the last 24h). Outside → a re-engagement/window
     error (#131047 etc.) → fall through.
  3. Body-only approved template whose BODY text variable carries the caller-supplied link (now the
     no-login download url). Always deliverable business-initiated → the guaranteed fallback.

Meta 24h-window rule (why the ladder exists): business-initiated messages OUTSIDE the 24h window must be
an APPROVED template; free-form text/document is rejected there. So a template is ALWAYS in the ladder,
and the file only attaches via (1) a doc-header template or (2) an in-window free-form document.

Needs env (see app/core/config.py):
  WHATSAPP_ACCESS_TOKEN, WHATSAPP_PHONE_NUMBER_ID, WHATSAPP_TEMPLATE_NAME,
  WHATSAPP_TEMPLATE_LANG (default en), WHATSAPP_GRAPH_VERSION (default v21.0),
  WHATSAPP_TEMPLATE_DOC_HEADER (default false — set true only when the approved template has a real
  document header; see the owner setup steps in docs/handoffs/platform-core.md).
"""
import httpx

from app.core.config import settings


def is_configured() -> bool:
    return bool(
        settings.WHATSAPP_ACCESS_TOKEN
        and settings.WHATSAPP_PHONE_NUMBER_ID
        and settings.WHATSAPP_TEMPLATE_NAME
    )


def _to_number(raw) -> str:
    """The Meta Cloud API `to` must be digits-only with a country code. Strip +/spaces/dashes/parens;
    prepend the US country code '1' to a bare 10-digit number (how reps are commonly stored). This is
    the transport-layer defense-in-depth: callers should already pass a normalized '+<cc>...' number
    (core.auth_security.normalize_phone), and stripping the '+' here is BYTE-COMPATIBLE with that (and
    with the pre-existing notify send format). Without this an unprefixed 5162330422 hits Meta #131030
    'not in allowed list'."""
    digits = "".join(c for c in str(raw or "") if c.isdigit())
    if len(digits) == 10:
        digits = "1" + digits
    return digits


def _base() -> str:
    ver = settings.WHATSAPP_GRAPH_VERSION or "v21.0"
    return f"https://graph.facebook.com/{ver}/{settings.WHATSAPP_PHONE_NUMBER_ID}"


def _headers() -> dict:
    return {"Authorization": f"Bearer {settings.WHATSAPP_ACCESS_TOKEN}"}


async def upload_media(cx: httpx.AsyncClient, data: bytes, mime: str, filename: str) -> str:
    files = {"file": (filename, data, mime)}
    form = {"messaging_product": "whatsapp", "type": mime}
    r = await cx.post(f"{_base()}/media", headers=_headers(), data=form, files=files)
    if r.status_code >= 300:
        raise RuntimeError(f"WhatsApp media upload {r.status_code}: {r.text[:300]}")
    return r.json().get("id", "")


def _template_msg(to: str, filename: str, media_id: str, body_text: str, with_doc_header: bool) -> dict:
    to = _to_number(to)
    components = []
    if with_doc_header and media_id:
        components.append({"type": "header", "parameters": [
            {"type": "document", "document": {"id": media_id, "filename": filename}}]})
    components.append({"type": "body", "parameters": [
        {"type": "text", "text": (body_text or "")[:1024]}]})
    return {
        "messaging_product": "whatsapp",
        "to": to,
        "type": "template",
        "template": {
            "name": settings.WHATSAPP_TEMPLATE_NAME,
            "language": {"code": settings.WHATSAPP_TEMPLATE_LANG or "en"},
            "components": components,
        },
    }


def _is_header_error(text: str) -> bool:
    t = (text or "").lower()
    return "132018" in t or "title component" in t or "does not contain" in t


def _is_window_error(text: str) -> bool:
    """A free-form (non-template) message was rejected because we're OUTSIDE the 24h customer-service
    window. Meta phrasings/codes: #131047 're-engagement message', legacy #470 '24 hours have passed',
    #131026/#131051 undeliverable. Detected loosely — a false positive only makes us fall back to the
    (always-deliverable) link template, which is safe."""
    t = (text or "").lower()
    return any(k in t for k in (
        "131047", "131026", "131051", "re-engagement", "reengagement",
        "24 hours", "24-hour", "customer service window", "outside the allowed", "(#470)", "470"))


def plan_delivery(doc_header_configured: bool, media_ok: bool) -> list:
    """PURE. The ordered list of send attempts for one file. Attaching the real file needs an uploaded
    media id; without one we can only send the link template. With media: the doc-header template first
    (outside-window capable) when configured, then a free-form document (inside-window), then the link
    template as the guaranteed business-initiated fallback."""
    if not media_ok:
        return ["template_link"]
    steps = []
    if doc_header_configured:
        steps.append("template_doc")
    steps.append("freeform_doc")
    steps.append("template_link")
    return steps


def classify_send_result(status_code: int, text: str) -> str:
    """PURE. Read one Meta send response: 'ok' (2xx) | 'header_error' (template has no doc header,
    #132018 — abandon the doc-header attempt) | 'window_error' (free-form blocked outside the 24h
    window) | 'error' (any other failure). On anything but 'ok' the ladder advances to the next step."""
    if 200 <= int(status_code) < 300:
        return "ok"
    if _is_header_error(text):
        return "header_error"
    if _is_window_error(text):
        return "window_error"
    return "error"


def _msg_id(r) -> str:
    try:
        return (r.json().get("messages") or [{}])[0].get("id", "")
    except Exception:
        return ""


async def _send_freeform_document(cx, to: str, media_id: str, filename: str, caption: str):
    """Free-form `type:document` message (no template). Deliverable only inside the 24h window; Meta
    returns a re-engagement/window error otherwise. Returns the raw httpx.Response (caller classifies)."""
    msg = {"messaging_product": "whatsapp", "to": _to_number(to), "type": "document",
           "document": {"id": media_id, "filename": filename, "caption": (caption or "")[:1024]}}
    return await cx.post(f"{_base()}/messages", headers=_headers(), json=msg)


def approval_configured() -> bool:
    return bool(is_configured() and settings.WHATSAPP_APPROVAL_TEMPLATE)


def _clean_var(s: str, n: int = 280) -> str:
    """Template body variables reject newlines/tabs and long runs of spaces (Meta #132000). Flatten."""
    t = " ".join(str(s or "").split())
    return t[:n] or "—"


async def send_approval(to: str, req_id: str, token: str, issue: str, fix: str, preview: str) -> str:
    """Send the auto-remediation approval template: body {{1}}=issue {{2}}=fix {{3}}=preview and two
    QUICK-REPLY buttons whose per-send payloads carry the decision + request id + token. When the
    recipient taps a button, Meta posts that payload to our webhook, which runs the decision. Returns
    the message id. Requires the 'remediation_approval' template to be APPROVED in WhatsApp Manager."""
    if not approval_configured():
        raise RuntimeError("WhatsApp approval not configured (WHATSAPP_APPROVAL_TEMPLATE + base creds)")
    to = _to_number(to)
    msg = {
        "messaging_product": "whatsapp", "to": to, "type": "template",
        "template": {
            "name": settings.WHATSAPP_APPROVAL_TEMPLATE,
            "language": {"code": settings.WHATSAPP_APPROVAL_LANG or "en"},
            "components": [
                {"type": "body", "parameters": [
                    {"type": "text", "text": _clean_var(issue)},
                    {"type": "text", "text": _clean_var(fix)},
                    {"type": "text", "text": _clean_var(preview)}]},
                {"type": "button", "sub_type": "quick_reply", "index": "0",
                 "parameters": [{"type": "payload", "payload": f"approve|{req_id}|{token}"}]},
                {"type": "button", "sub_type": "quick_reply", "index": "1",
                 "parameters": [{"type": "payload", "payload": f"reject|{req_id}|{token}"}]},
            ],
        },
    }
    async with httpx.AsyncClient(timeout=30) as cx:
        r = await cx.post(f"{_base()}/messages", headers=_headers(), json=msg)
    if r.status_code >= 300:
        raise RuntimeError(f"WhatsApp approval send {r.status_code}: {r.text[:300]}")
    try:
        return (r.json().get("messages") or [{}])[0].get("id", "")
    except Exception:
        return ""


async def send_text(to: str, body: str) -> str:
    """Plain text message. Only deliverable inside the 24h customer-service window (i.e. right after
    the recipient messaged/tapped us) — used to confirm a decision back in-thread. Best-effort."""
    if not is_configured():
        return ""
    payload = {"messaging_product": "whatsapp", "to": _to_number(to), "type": "text",
               "text": {"body": (body or "")[:1000]}}
    async with httpx.AsyncClient(timeout=30) as cx:
        r = await cx.post(f"{_base()}/messages", headers=_headers(), json=payload)
    if r.status_code >= 300:
        raise RuntimeError(f"WhatsApp text {r.status_code}: {r.text[:200]}")
    try:
        return (r.json().get("messages") or [{}])[0].get("id", "")
    except Exception:
        return ""


def otp_configured() -> bool:
    """True when a dedicated approved OTP/authentication template is set. Without it, OTP delivery
    falls back to a plain text message (24h-window only) and is best-effort/unconfirmed."""
    return bool(is_configured() and settings.WHATSAPP_OTP_TEMPLATE)


async def send_otp(to: str, code: str, purpose: str = "verification") -> str:
    """Deliver a one-time code to `to`. Prefers an approved AUTHENTICATION template (single body var =
    the code) when WHATSAPP_OTP_TEMPLATE is configured; otherwise falls back to a plain text message
    (Meta delivers text only inside the 24h service window → cold sends may silently not arrive). Marks
    UNCONFIRMED in the handoff. Returns the message id; raises RuntimeError on a hard send failure."""
    if not is_configured():
        raise RuntimeError("WhatsApp not configured (set WHATSAPP_ACCESS_TOKEN / PHONE_NUMBER_ID / TEMPLATE_NAME)")
    to = _to_number(to)
    if settings.WHATSAPP_OTP_TEMPLATE:
        msg = {
            "messaging_product": "whatsapp", "to": to, "type": "template",
            "template": {
                "name": settings.WHATSAPP_OTP_TEMPLATE,
                "language": {"code": settings.WHATSAPP_OTP_LANG or "en"},
                "components": [
                    {"type": "body", "parameters": [{"type": "text", "text": _clean_var(code, 32)}]},
                    # AUTHENTICATION templates require the code echoed as the button parameter too.
                    {"type": "button", "sub_type": "url", "index": "0",
                     "parameters": [{"type": "text", "text": _clean_var(code, 32)}]},
                ],
            },
        }
        async with httpx.AsyncClient(timeout=30) as cx:
            r = await cx.post(f"{_base()}/messages", headers=_headers(), json=msg)
            # If the template has no URL button, retry body-only (mirrors send_document's self-heal).
            if r.status_code >= 300:
                msg["template"]["components"] = [msg["template"]["components"][0]]
                r = await cx.post(f"{_base()}/messages", headers=_headers(), json=msg)
        if r.status_code >= 300:
            raise RuntimeError(f"WhatsApp OTP send {r.status_code}: {r.text[:300]}")
        try:
            return (r.json().get("messages") or [{}])[0].get("id", "")
        except Exception:
            return ""
    # Fallback: plain text (24h-window only). Best-effort; returns "" outside the window.
    return await send_text(to, f"Your MetricsPro {purpose} code is {code}. It expires shortly. "
                               f"Do not share it with anyone.")


async def send_document(to: str, data: bytes, mime: str, filename: str, body_text: str) -> str:
    """Deliver the ACTUAL report file on WhatsApp wherever Meta permits, else the caller-supplied link.

    Walks the `plan_delivery` ladder (doc-header template → in-window free-form document → link template),
    reading each Meta response with `classify_send_result` and advancing on anything but 'ok'. `body_text`
    is the template/caption text and, for the link fallback, MUST already contain the no-login download
    url (the router builds it). Returns the provider message id; raises only if EVERY planned attempt
    failed. See the module docstring for the Meta 24h-window rationale."""
    if not is_configured():
        raise RuntimeError("WhatsApp not configured (set WHATSAPP_ACCESS_TOKEN / PHONE_NUMBER_ID / TEMPLATE_NAME)")

    async with httpx.AsyncClient(timeout=120) as cx:
        # One media id serves both attach paths (doc-header template + free-form document); upload once.
        # Empty bytes = a text-only notification (some callers pass b"" just to fire a templated alert),
        # so skip the upload → link template only. A failed upload likewise drops the attach paths → the
        # link template still goes out.
        media_id = ""
        if data:
            try:
                media_id = await upload_media(cx, data, mime, filename)
            except Exception:
                media_id = ""

        last = None
        for step in plan_delivery(bool(settings.WHATSAPP_TEMPLATE_DOC_HEADER), bool(media_id)):
            if step == "template_doc":
                r = await cx.post(f"{_base()}/messages", headers=_headers(),
                                  json=_template_msg(to, filename, media_id, body_text, True))
            elif step == "freeform_doc":
                r = await _send_freeform_document(cx, to, media_id, filename, body_text)
            else:  # template_link — body-only approved template carrying the download link
                r = await cx.post(f"{_base()}/messages", headers=_headers(),
                                  json=_template_msg(to, filename, "", body_text, False))
            last = r
            if classify_send_result(r.status_code, r.text) == "ok":
                return _msg_id(r)
            # else: advance to the next planned attempt (header/window/other error)

    if last is not None and 200 <= last.status_code < 300:
        return _msg_id(last)
    raise RuntimeError(f"WhatsApp send {last.status_code if last is not None else '??'}: "
                       f"{last.text[:300] if last is not None else 'no send attempted'}")
