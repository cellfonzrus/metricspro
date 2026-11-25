"""SEND RECORD — the ONE home for "we handed this message to a provider, and here is what became of it".

THE DEFECT THIS REMOVES (owner 2026-11-25, index §NOTIFY-02). Reported as "onboarding email not
reaching employees". Measured read-only on production first: the two NY Logistics invites minted at
20:26:57 and 20:26:59 UTC that day were stamped `invited`, their audit rows said
`sent_to: <address>`, and **`notify.send_log` held no row for either of them** — nor for any HR
letter, CRM mail, helpdesk reply, access code or receipt ever sent. Thirty days of `send_log` held
report sends and nothing else. The HR invite path put the provider's own refusal into an HTTP
response field (`email_note`) and threw it away, so "did the employee get it" was unanswerable from
inside the app, and a refused send, a bounced address and a delivered mail were indistinguishable.

Stated as a CLASS, because the instance is not the defect: **an outbound message is recorded by the
transport that sent it, never by the caller that asked.** `notify.send_log` was already the house's
record of a send — one row per attempt, with the channel, the address, the provider's message id and
the provider's error — and mig 714 already added the three columns that carry what the provider said
AFTERWARDS. Only `/notify/send*` dereferenced any of it. Thirty-one other call sites each decided
for themselves, and every one of them decided to record nothing. That is §19.18's pattern exactly:
the registry existed and the callers were never wired to it.

So the row is now written INSIDE the transport (`channels/email_resend.send_email`), where no caller
can forget it, and `purpose` is a REQUIRED keyword argument of that function — a send that cannot
say what it is cannot be made. `harness_email_send_record.py` fails the build if a second writer of
this table appears, if a caller reaches the provider's HTTP API directly, or if the transport stops
dereferencing this module.

WHY THE EMAIL SIBLING WAS MISSING FOR SIXTEEN MONTHS. The identical defect was found and fixed for
WhatsApp on 2026-07-18 (mig 714: Meta accepts a message, returns an id, then silently drops it — so
acceptance was recorded and delivery was not). Email never got the same treatment: there was no
provider webhook, so `delivery_status` was NULL on every email row ever written. The delivery
arithmetic that fix produced lived in `remediation/router` as three private functions; it is factored
out HERE and dereferenced from both webhooks rather than copied for the second one.

WHAT IS HERE
  attempt_row(...)              PURE. One `notify.send_log` row from a send attempt.
  insert_rows(rows)             Write them. Degrades when an optional column's migration is un-run.
  record_attempt(...)           Build + write one row. What the transport calls.
  valid_status(channel, s)      PURE. An ALLOWLIST per channel — an unruled status records nothing.
  merge_status(cur, incoming)   PURE. Monotonic latest status; any failure state always wins.
  flatten_meta_errors(errors)   PURE. Meta's `errors[]` -> one short human string.
  delivery_from_resend(event)   PURE. A Resend webhook event -> (message_id, status, error).
  svix_signed_payload(...)      PURE. The exact bytes Resend/Svix signs.
  svix_signature_ok(...)        PURE. Constant-time check of `svix-signature`, fail-CLOSED.
  stamp_delivery(...)           Write the provider's later verdict onto the matching row(s).

RULE TWO: no carrier, tenant, store or product name appears here. Everything above `insert_rows` is
pure stdlib so `harness_email_send_record.py` proves it with no database.
Registered in docs/SYSTEM_DATA_FLOW_INDEX.md §NOTIFY-02.
"""
import base64
import hashlib
import hmac
import time
from datetime import datetime, timezone

SCHEMA = "notify"
TABLE = "send_log"

#: Columns a row carries ONLY once their migration has been run: `delivery_route` (mig 723) and
#: `report_label` (mig 1057). PostgREST rejects the WHOLE batch when any one is missing, so
#: `insert_rows` drops them one at a time and retries. An un-run migration must cost a slightly
#: poorer history row, never the entire send history (the rule `notify/router._insert_log`
#: established and this module inherits).
OPTIONAL_COLUMNS = ("delivery_route", "report_label")

#: What a provider can tell us AFTER it accepted a message. Shared by both channels so there is one
#: arithmetic, not one per webhook. `failed`/`bounced`/`complained` all rank 4: a failure state is
#: terminal and always wins, however late it arrives.
DELIVERY_RANK = {
    "sent": 1,
    "delayed": 1,
    "delivered": 2,
    "read": 3,
    "failed": 4,
    "bounced": 4,
    "complained": 4,
}

#: Which of those words each channel's provider is ALLOWED to say. Per channel on purpose: Meta
#: never says "bounced", and accepting a word a provider does not use would let a spoofed payload
#: write a verdict that channel cannot reach. An ALLOWLIST — an unruled status records NOTHING,
#: rather than a confident wrong verdict (the `component_is_evidence` rule).
CHANNEL_STATUSES = {
    "whatsapp": ("sent", "delivered", "read", "failed"),
    "email": ("sent", "delivered", "delayed", "bounced", "complained", "failed"),
}

#: Resend's own event names -> the house word for what happened. `email.opened` / `email.clicked`
#: are ENGAGEMENT, not delivery, and are deliberately absent: whether somebody opened a mail is not
#: whether we delivered it, and recording an open as a delivery state would overwrite a bounce.
RESEND_EVENTS = {
    "email.sent": "sent",
    "email.delivered": "delivered",
    "email.delivery_delayed": "delayed",
    "email.bounced": "bounced",
    "email.complained": "complained",
    # `email.failed` is the provider REFUSING to send a mail it already answered 200 to. CONFIRMED
    # LIVE on 2026-11-25: the Resend dashboard showed the reported onboarding invite as
    # **suppressed** — the address was on the account's suppression list (it had hard-bounced or
    # been marked as spam before), so the send was dropped at the provider while our code received
    # `200 OK` with a real message id and reported success. This is the silent-drop class in its
    # purest form, and the reason an acceptance may never be recorded as a delivery.
    "email.failed": "failed",
}

#: How far out of step a webhook's own timestamp may be before we refuse it (Svix's own default).
#: A replay of a captured, correctly-signed payload is otherwise valid forever.
SIGNATURE_MAX_SKEW_SECONDS = 300


def _s(v):
    return str(v or "").strip()


def _now_iso():
    return datetime.now(timezone.utc).isoformat()


# ── the attempt row ──────────────────────────────────────────────────────────────────────────────
def attempt_row(*, org_id, purpose, channel, target, status, provider_message_id="", error="",
                label="", subscription_id=None, filters=None, triggered_by="", route=""):
    """PURE. One `notify.send_log` row describing ONE attempt to carry ONE message to ONE address.

    `purpose` is what was sent — the report key for a report, otherwise a stable name for the kind
    of message (`hr_onboarding_invite`, `core_access_code`). It lands in the NOT NULL `report_key`
    column because that column has always meant "what was sent"; it was only ever filled by report
    senders because they were the only senders writing rows at all.

    `status` is the TRANSPORT's verdict and only ever `sent` (the provider accepted it) or `failed`
    (it did not). What the provider says later goes in the `delivery_*` columns via `stamp_delivery`,
    never here — that distinction is the whole point of mig 714.
    """
    st = _s(status).lower()
    if st not in ("sent", "failed"):
        raise ValueError(f"attempt status must be 'sent' or 'failed', got {status!r}")
    if not _s(purpose):
        raise ValueError("attempt_row needs a purpose — a send that cannot say what it is "
                         "is the defect this module removes")
    if not _s(channel):
        raise ValueError("attempt_row needs a channel")
    row = {
        "org_id": org_id,
        "subscription_id": subscription_id,
        "report_key": _s(purpose),
        "channel": _s(channel).lower(),
        "target": _s(target),
        "status": st,
        "provider_message_id": _s(provider_message_id) or None,
        "error": (_s(error)[:1000] or None),
        "filters": filters if filters is not None else {},
        "triggered_by": _s(triggered_by) or None,
    }
    if _s(label):
        row["report_label"] = _s(label)
    if _s(route):
        row["delivery_route"] = _s(route)
    return row


def _table(client=None):
    if client is None:
        from app.core.database import get_supabase
        client = get_supabase()
    return client.schema(SCHEMA).table(TABLE)


def insert_rows(rows, client=None) -> bool:
    """Write attempt rows. NEVER lets a logging failure abort a real send, and degrades gracefully
    when an optional column's migration has not been run: strip one, retry, repeat. True when a
    write landed.
    """
    rows = [dict(r) for r in (rows or []) if r]
    if not rows:
        return False
    drop = []
    for _ in range(len(OPTIONAL_COLUMNS) + 1):
        payload = [{k: v for k, v in r.items() if k not in drop} for r in rows]
        try:
            _table(client).insert(payload).execute()
            return True
        except Exception:
            pass
        nxt = [c for c in OPTIONAL_COLUMNS if c not in drop and any(c in r for r in rows)]
        if not nxt:
            return False
        drop.append(nxt[0])
    return False


def record_attempt(*, org_id, purpose, channel, target, status, client=None, **kw) -> bool:
    """Record ONE attempt. What a transport calls, so no caller of the transport can forget.

    Never raises: a send that happened must not be undone by a history row that could not be
    written, and a send that failed must not have its own error replaced by a logging error.
    """
    try:
        row = attempt_row(org_id=org_id, purpose=purpose, channel=channel, target=target,
                          status=status, **kw)
    except Exception:
        return False
    return insert_rows([row], client=client)


# ── what the provider says afterwards ────────────────────────────────────────────────────────────
def valid_status(channel, status) -> bool:
    """PURE. May this channel's provider say this word? See CHANNEL_STATUSES on why it is per
    channel and why an unruled word records nothing."""
    return _s(status).lower() in CHANNEL_STATUSES.get(_s(channel).lower(), ())


def merge_status(current, incoming):
    """PURE. The latest delivery status, monotonic by rank with any failure state ALWAYS winning.
    Never regresses (a late `delivered` after `read` keeps `read`); an unknown or empty incoming
    keeps the current value. Returns the winning status, lower-cased.

    Carried over verbatim from `remediation/router._merge_delivery_status` (the 2026-07-18 WhatsApp
    silent-drop fix), which now dereferences this instead of holding the second copy.
    """
    inc = _s(incoming).lower()
    inc_r = DELIVERY_RANK.get(inc, 0)
    if inc_r == 0:
        return current
    cur_r = DELIVERY_RANK.get(_s(current).lower(), 0)
    return inc if inc_r >= cur_r else current


def flatten_meta_errors(errors):
    """PURE. Flatten Meta's status `errors[]` array into one short human string. Best-effort; '' when
    there are none. Each error: {code, title, message, error_data:{details}}."""
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


def delivery_from_resend(event):
    """PURE. One Resend webhook event -> (provider_message_id, status, error), or None when the
    event says nothing about delivery.

    Resend's shape: {"type": "email.bounced", "data": {"email_id": "...", "bounce": {...}, ...}}.
    An event type outside RESEND_EVENTS returns None — including `email.opened`, which is somebody
    reading a mail, not us delivering one.
    """
    if not isinstance(event, dict):
        return None
    status = RESEND_EVENTS.get(_s(event.get("type")).lower())
    if not status:
        return None
    data = event.get("data")
    data = data if isinstance(data, dict) else {}
    mid = _s(data.get("email_id") or data.get("id"))
    if not mid:
        return None
    return (mid, status, _resend_error(status, data))


def _resend_error(status, data):
    """PURE. The reason a mail did not arrive, as the provider gave it. '' for a healthy status."""
    if status == "bounced":
        b = data.get("bounce")
        b = b if isinstance(b, dict) else {}
        seg = " — ".join(p for p in (_s(b.get("type")), _s(b.get("subType")),
                                     _s(b.get("message"))) if p)
        return (seg or "bounced")[:500]
    if status == "failed":
        f = data.get("failed")
        f = f if isinstance(f, dict) else {}
        return (_s(f.get("reason"))
                or "the mail provider refused to send it (commonly: the address is on the "
                   "account's suppression list after an earlier bounce or spam complaint)")[:500]
    if status == "complained":
        return "the recipient marked this mail as spam"
    if status == "delayed":
        return _s((data.get("delivery_delayed") or {}).get("message")
                  if isinstance(data.get("delivery_delayed"), dict) else "")[:500]
    return ""


# ── the webhook's only authentication ────────────────────────────────────────────────────────────
def svix_signed_payload(msg_id, timestamp, body: bytes) -> bytes:
    """PURE. The exact bytes Resend (via Svix) signs: `{id}.{timestamp}.{raw body}`. The RAW body —
    a re-serialised one has different whitespace and will not verify."""
    return _s(msg_id).encode() + b"." + _s(timestamp).encode() + b"." + (body or b"")


def svix_signature_ok(secret, msg_id, timestamp, signature_header, body: bytes,
                      *, now=None, max_skew=SIGNATURE_MAX_SKEW_SECONDS) -> bool:
    """PURE. Is this POST really from the provider? Constant-time, and fail-CLOSED: an unset secret
    rejects every payload.

    The endpoint is on the PUBLIC middleware allowlist (a provider carries no JWT of ours), so this
    signature is its ONLY authentication, and an unauthenticated write path into a tenant's send
    history is worse than no delivery statuses at all — the posture `svix-signature` inherits from
    WHATSAPP_APP_SECRET's 2026-08-05 hardening.

    `secret` is Svix's `whsec_<base64>`; the signing key is the base64 BODY, decoded.
    `signature_header` is a space-separated list of `v1,<base64 sig>` — several while a secret is
    being rotated, so ANY of them matching is a pass.
    """
    sec = _s(secret)
    if not sec or not _s(msg_id) or not _s(timestamp) or not _s(signature_header):
        return False
    try:
        ts = int(_s(timestamp))
    except (TypeError, ValueError):
        return False
    if max_skew and abs((time.time() if now is None else now) - ts) > max_skew:
        return False
    try:
        key = base64.b64decode(sec.split("_", 1)[1] if sec.startswith("whsec_") else sec)
    except Exception:
        return False
    if not key:
        return False
    expected = base64.b64encode(
        hmac.new(key, svix_signed_payload(msg_id, timestamp, body), hashlib.sha256).digest()
    ).decode()
    for part in _s(signature_header).split():
        version, _, sig = part.partition(",")
        if version.strip() == "v1" and hmac.compare_digest(sig.strip(), expected):
            return True
    return False


def stamp_delivery(provider_message_id, status, *, channel, error="", client=None) -> int:
    """Write the provider's later verdict onto the matching attempt row(s). Returns how many rows
    were changed.

    A GLOBAL lookup on `provider_message_id`, with NO org filter: a provider's message id is
    globally unique, and an org filter could only ever MISS the row the event belongs to (the rule
    the Meta handler established).

    IDEMPOTENT REPLAY: a provider retries a webhook until it sees a 2xx and re-sends the same
    events, so a merge that changes nothing and carries no new error skips the write entirely.

    Never raises, and degrades to a no-op on an un-run migration: the webhook MUST answer 2xx or the
    provider disables the subscription.
    """
    mid = _s(provider_message_id)
    st = _s(status).lower()
    if not mid or not valid_status(channel, st):
        return 0
    try:
        log = _table(client)
        rows = (log.select("id,delivery_status").eq("provider_message_id", mid)
                .execute().data) or []
    except Exception:
        return 0
    changed = 0
    err = _s(error)
    for r in rows:
        cur = r.get("delivery_status")
        merged = merge_status(cur, st)
        if merged == cur and not err:
            continue
        upd = {"delivery_status": merged, "delivery_updated_at": _now_iso()}
        if err:
            upd["delivery_error"] = err[:500]
        try:
            log.update(upd).eq("id", r.get("id")).execute()
            changed += 1
        except Exception:
            return changed
    return changed


def last_attempts(org_id, purpose, targets, *, client=None, limit=500):
    """The most recent attempt per address, for one org and one purpose. What a surface asks when it
    wants to show "did this person actually get it" beside the person.

    Returns {lower-cased address: row}. Never raises: an unreadable history is an empty answer, so a
    surface says "no send recorded" rather than inventing one.
    """
    want = [_s(t).lower() for t in (targets or []) if _s(t)]
    if not want or not _s(purpose):
        return {}
    try:
        rows = (_table(client)
                .select("target,status,error,provider_message_id,delivery_status,"
                        "delivery_error,delivery_updated_at,created_at")
                .eq("org_id", org_id).eq("report_key", _s(purpose))
                .in_("target", want).order("created_at", desc=True)
                .limit(limit).execute().data) or []
    except Exception:
        return {}
    out = {}
    for r in rows:                       # newest first, so the first sighting wins
        out.setdefault(_s(r.get("target")).lower(), r)
    return out


def delivery_verdict(row):
    """PURE. What ONE surface shows for one person: (state, plain sentence).

    The states are deliberately few and never flattering. `unknown` is what a missing row means —
    it is NOT "sent", because "we have no record" is exactly the fact that made this defect
    invisible for sixteen months.
    """
    if not row:
        return ("unknown", "No send recorded — nobody can say whether this reached them.")
    if _s(row.get("status")).lower() == "failed":
        return ("failed", "We could not send it: " + (_s(row.get("error")) or "the provider refused it."))
    d = _s(row.get("delivery_status")).lower()
    if d == "bounced":
        return ("bounced", "The address rejected it: "
                           + (_s(row.get("delivery_error")) or "it bounced."))
    if d == "failed":
        # A SEPARATE STATE from a bounce on purpose. A bounce is the receiving server saying no; this
        # is OUR provider declining to send at all, and the remedy is ours (correct the address, or
        # take it off the suppression list) rather than the recipient's. Conflating them would hand a
        # human the wrong fix, which is the thing the send_outcome allowlist exists to avoid.
        return ("refused", "The mail provider did not send it: "
                           + (_s(row.get("delivery_error"))
                              or "it was suppressed, usually after an earlier bounce or spam "
                                 "complaint from that address."))
    if d == "complained":
        return ("complained", "It arrived and was marked as spam by the recipient.")
    if d == "delayed":
        return ("delayed", "The receiving mail server is delaying it; it has not arrived yet.")
    if d == "delivered":
        return ("delivered", "Delivered to their mailbox.")
    return ("accepted", "Handed to the mail provider and accepted. No delivery confirmation yet — "
                        "if they cannot find it, have them check their spam folder.")
