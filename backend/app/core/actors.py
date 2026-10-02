"""WHO DID THIS, as a NAME — the one home for turning an actor uid into something a person reads.

THE GAP THIS CLOSES (found 2026-10-01 answering the owner's "with dates and by who"). §19.34 made
actor identity correct: `_caller_uid` stamps the signed-in auth uid as a canonical UUID, or NULL, never
a sentinel. What it did NOT do is give anyone a way to DISPLAY that. Every consumer renders the stored
value through `frontend/src/lib/actor.ts::actorLabel`, which maps NULL and the retired 'web' to
"system" and otherwise returns the string unchanged — so an actor column shows a RAW UUID. Grep
confirmed it: `/commcalc/daily-commission` renders "Recorded by" as actorLabel(recorded_by),
`plan-installments` as "by {actorLabel(changed_by)}", `ingest-guard` and `commission-discrepancy` the
same. Correct storage, unreadable screen.

The mapping was never missing from the DATA — `storeops.app_users` carries `auth_id`, `full_name` and
`email`. It was simply never joined. This module is that join, once, so no caller invents a second one.

POSTURE
  · Best-effort and non-fatal: a failed read returns {} and every caller falls back to showing the uid
    (or "system" for NULL) exactly as today. A display helper must never break a money report.
  · ORG-SCOPED, like every sensitive read in this codebase — a uid is resolved only against the
    app_users rows of the org being asked about.
  · NAME ONLY by default. `email` is a contact detail, not a label; it is used solely as the fallback
    when a user has no `full_name`, so a row never renders as a bare UUID when a name exists. No other
    column is read, so this can never widen into a people directory.
  · Unresolved uid -> ABSENT from the map (never a guess, never a fabricated name). A caller that gets
    nothing back shows what it showed before.
"""
import re

from app.core.database import get_supabase

_NAME_COLS = "auth_id,full_name,email"

# A CANONICAL ACTOR UUID, and nothing else, is a thing to look up. Several actor columns in this
# codebase are MIXED by history: `envelope_count.counted_by` holds a UUID since §47 and the retired
# 'management' sentinel before it, while `daily_closing_verification.verified_by`,
# `cash_pickup.picked_up_by` and `handed_to` have always held a person's NAME ('Rana', 'Ismail').
# Asking the resolver about a name is harmless but pointless; the thing that must never happen is a
# name being replaced or mangled on its way to the screen. So the UUID test lives HERE, next to the
# resolver, rather than being re-guessed by each caller (§19.34's one-home rule applied to display).
_UUID = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", re.I)


def is_actor_uid(v):
    """PURE: True when `v` is a canonical actor UUID — the only shape worth resolving."""
    return bool(_UUID.match(str(v or "").strip()))


def actor_names(uids, org_id, client=None):
    """{uid: display name} for the uids that resolve, org-scoped. Never raises.

    Only the uids passed in are looked up (one `in_` read, not a table scan), and only those that
    actually match an app_users row of this org appear in the result.
    """
    want = sorted({str(u).strip() for u in (uids or []) if str(u or "").strip()})
    if not want or not str(org_id or "").strip():
        return {}
    try:
        rows = (client or get_supabase()).schema("storeops").table("app_users") \
            .select(_NAME_COLS).eq("org_id", org_id).in_("auth_id", want).execute().data or []
    except Exception as e:                                   # pragma: no cover - display-only guard
        print(f"WARN core.actors actor_names failed (showing raw ids): {e}")
        return {}
    out = {}
    for r in rows:
        uid = str(r.get("auth_id") or "").strip()
        if not uid:
            continue
        name = str(r.get("full_name") or "").strip() or str(r.get("email") or "").strip()
        if name:
            out[uid] = name
    return out


def name_for(uid, names):
    """The display name for one uid, or the uid itself when it did not resolve — so a caller never
    has to decide what "unknown" looks like, and never prints None."""
    u = str(uid or "").strip()
    if not u:
        return None
    return (names or {}).get(u) or u


def resolve_actor_names(values, org_id, client=None):
    """{value: display} for a MIXED list of actor values: each UUID resolved to a person, each
    already-human string mapped to ITSELF, untouched.

    This is what a report with several actor columns of different vintages should call — it resolves
    in ONE batched read (only the UUIDs are looked up) and guarantees a name is never altered. A UUID
    that does not resolve maps to itself, the `name_for` posture, so nothing ever renders as None.
    """
    vals = [str(v).strip() for v in (values or []) if str(v or "").strip()]
    names = actor_names([v for v in vals if is_actor_uid(v)], org_id, client=client)
    return {v: (names.get(v) or v) for v in vals}
