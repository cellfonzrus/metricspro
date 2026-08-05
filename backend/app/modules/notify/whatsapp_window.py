"""WhatsApp 24h customer-service WINDOW tracking (Meta Cloud API).

WHY THIS EXISTS (owner incident 2026-08-05)
-------------------------------------------
Meta only delivers FREE-FORM (non-template) messages inside the 24-hour customer-service window — i.e.
within 24h of the recipient's last INBOUND message to our WhatsApp Business number. Outside that window a
business-initiated message MUST be an approved TEMPLATE.

The trap: the Graph API does not reliably reject the out-of-window free-form send. It frequently returns
**200 with a real wamid**, and Meta drops the message asynchronously. `send_log.status` therefore reads
'sent' while nothing ever reaches the handset (luxelink → +1516…0422, 2026-08-05 03:01/03:02Z: real
`wamid.HBgL…` values logged, zero deliveries, zero conversations in Meta Insights for 30 days).

So "the API accepted it" is NOT evidence of an open window. This module supplies the only evidence that
is: an inbound message we actually received on the Meta webhook.

ACCOUNT-WIDE BY DESIGN (documented deviation from AGENT_CONTRACT §2's "every new table carries org_id")
------------------------------------------------------------------------------------------------------
One Meta WABA phone number serves EVERY tenant. The 24h window is a property of
(our sender phone_number_id, the recipient handset) at META — it is not tenant state, and Meta applies it
identically no matter which tenant's report triggered the send. Adding an org_id would FRAGMENT one real
window into N per-tenant copies and make us believe a genuinely-open window is closed (→ we would fall back
to a link when the file could have been attached). The table therefore stores no tenant data at all: our
own sender id, the recipient's digits, and a timestamp. Nothing here is readable per-tenant and nothing
here is used for authorization — it only ever decides "attach the file" vs "send the approved template",
and BOTH outcomes are delivered to the same caller-supplied recipient.

DEGRADES TO SAFE
----------------
Every read fails CLOSED: a missing table (mig 723 not run), a DB hiccup, an unparsable timestamp — all
return `False` = "window not proven open" = take the approved-template path, which is ALWAYS deliverable
business-initiated. So this module can never make a send fail; at worst the recipient gets the download
link instead of the attachment.
"""
from datetime import datetime, timedelta, timezone

from app.core.config import settings

TABLE = "whatsapp_window"
SCHEMA = "notify"


def digits(raw) -> str:
    """Digits-only key, matching `whatsapp_meta._to_number`: strip +/spaces/dashes/parens and prepend the
    US country code to a bare 10-digit number, so a number recorded from an inbound `from` (Meta always
    sends full E.164 digits) matches one we later send TO (often stored as 10 digits)."""
    d = "".join(c for c in str(raw or "") if c.isdigit())
    if len(d) == 10:
        d = "1" + d
    return d


def window_hours() -> float:
    """Configured window length in hours, clamped to (0, 24]. Meta's real window is 24h; the default is a
    little under so a send racing the boundary falls back to the (always-deliverable) template."""
    try:
        h = float(settings.WHATSAPP_WINDOW_HOURS)
    except Exception:
        h = 23.0
    if h <= 0:
        return 0.0
    return min(h, 24.0)


def parse_ts(value):
    """PURE. Parse a stored timestamp into an aware UTC datetime, or None. Accepts ISO-8601 with 'Z',
    with an offset, or naive (assumed UTC — Supabase timestamptz comes back with an offset)."""
    if value is None:
        return None
    if isinstance(value, datetime):
        dt = value
    else:
        s = str(value).strip()
        if not s:
            return None
        if s.endswith("Z") or s.endswith("z"):
            s = s[:-1] + "+00:00"
        try:
            dt = datetime.fromisoformat(s)
        except Exception:
            # Postgres can render 6+ fractional digits; trim to microseconds and retry once.
            try:
                head, _, tail = s.partition(".")
                frac = "".join(c for c in tail if c.isdigit())[:6]
                rest = tail[len(frac):] if tail[len(frac):] else ""
                dt = datetime.fromisoformat(f"{head}.{frac or '0'}{rest}")
            except Exception:
                return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def window_open_at(last_inbound, now=None, hours=None) -> bool:
    """PURE. True when `last_inbound` is within `hours` of `now`. Unknown/unparsable/future-dated inputs
    are NOT evidence → False (fail closed). A far-future timestamp (clock skew or a spoofed value) is
    rejected rather than trusted."""
    ts = parse_ts(last_inbound)
    if ts is None:
        return False
    h = window_hours() if hours is None else float(hours)
    if h <= 0:
        return False
    ref = now if isinstance(now, datetime) else datetime.now(timezone.utc)
    if ref.tzinfo is None:
        ref = ref.replace(tzinfo=timezone.utc)
    ref = ref.astimezone(timezone.utc)
    if ts > ref + timedelta(minutes=5):     # clock skew tolerance; beyond it, distrust the row
        return False
    return (ref - ts) <= timedelta(hours=h)


def _table():
    from app.core.database import get_supabase
    return get_supabase().schema(SCHEMA).table(TABLE)


def sender_id() -> str:
    """Our own Meta phone number id (the WABA sender). Part of the key so a phone-number migration
    doesn't inherit a stale window."""
    return str(settings.WHATSAPP_PHONE_NUMBER_ID or "")


def record_inbound(wa_id, at=None, phone_number_id=None) -> bool:
    """Record that `wa_id` messaged us (opens/refreshes the 24h window). Best-effort and SYNCHRONOUS —
    callers in async handlers must hop a thread. Never raises; returns True only on a confirmed write.
    Idempotent: repeated events for the same (sender, wa_id) just move `last_inbound_at` forward."""
    d = digits(wa_id)
    if not d:
        return False
    when = at if isinstance(at, datetime) else datetime.now(timezone.utc)
    if when.tzinfo is None:
        when = when.replace(tzinfo=timezone.utc)
    row = {"phone_number_id": phone_number_id or sender_id() or "unknown",
           "wa_id": d,
           "last_inbound_at": when.astimezone(timezone.utc).isoformat(),
           "updated_at": datetime.now(timezone.utc).isoformat()}
    try:
        # Both key columns are NOT NULL PK members, so ON CONFLICT can never hit the nullable-column trap.
        _table().upsert(row, on_conflict="phone_number_id,wa_id").execute()
        return True
    except Exception:
        return False  # mig 723 not run / DB hiccup → window simply stays unproven


def last_inbound_at(wa_id, phone_number_id=None):
    """Latest recorded inbound timestamp for `wa_id`, or None (unknown / table missing). SYNCHRONOUS."""
    d = digits(wa_id)
    if not d:
        return None
    try:
        q = _table().select("last_inbound_at").eq("wa_id", d)
        pid = phone_number_id or sender_id()
        if pid:
            q = q.eq("phone_number_id", pid)
        rows = q.order("last_inbound_at", desc=True).limit(1).execute().data or []
    except Exception:
        return None
    return (rows[0].get("last_inbound_at") if rows else None)


def is_window_open(wa_id, now=None, phone_number_id=None) -> bool:
    """POSITIVE EVIDENCE ONLY: True iff we recorded an inbound from `wa_id` within the configured window.
    Fails CLOSED on every error (no table, no row, bad timestamp) → the caller takes the approved-template
    path, which is always deliverable business-initiated. SYNCHRONOUS."""
    return window_open_at(last_inbound_at(wa_id, phone_number_id=phone_number_id), now=now)


_TRACKING_CACHE = {"at": 0.0, "value": None}
_TRACKING_TTL = 300.0  # seconds


def tracking_available(force: bool = False) -> bool:
    """True when the window table is reachable (mig 723 run). DIAGNOSTICS ONLY — never gates a send.

    Cached for 5 minutes: `/notify/health` is called on every notify page load AND every Send-report
    modal open, and this is the only key in it that costs a round trip. `force=True` bypasses the cache.
    A negative result is cached too, so a pre-migration deploy doesn't probe on every modal open."""
    import time
    now = time.time()
    if not force and _TRACKING_CACHE["value"] is not None and \
            (now - _TRACKING_CACHE["at"]) < _TRACKING_TTL:
        return bool(_TRACKING_CACHE["value"])
    try:
        _table().select("wa_id").limit(1).execute()
        val = True
    except Exception:
        val = False
    _TRACKING_CACHE.update({"at": now, "value": val})
    return val
