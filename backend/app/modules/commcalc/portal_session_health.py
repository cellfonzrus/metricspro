"""Durable-session HEALTH for merchant-portal logins — the pure state machine behind the status chip.

WHY (owner directive 2026-09-04, "get the 2FA workaround fixed"). The workaround for a 2FA'd portal is
not a bypass — it is DURABLE SESSION REUSE: a human satisfies the challenge ONCE on the live-login
screencast (live_login.py), the authenticated storage_state is persisted per (org, source), and every
daily pull afterwards rides that session. The whole approach lives or dies on one thing: does the owner
find out BEFORE the nightly pull that a session has gone stale? A silently dead session is a connector
that looks configured, reports nothing, and is discovered weeks later by a hole in the recon.

So the session's condition is a FIRST-CLASS, computed state — never a human's memory. This module is
that computation: a data_source row + a clock in, one state + a human sentence + a notify decision out.
It follows the precedent the platform already set for sweep/data_source health (portal_backoff.humanize,
_strip_source_pw's computed `blocked` chip): compute in a pure function, render a chip, never make the
page reason about clock skew.

STATES (ordered by severity — `worse_of` picks the one that must be shown):
  healthy        a saved session with comfortable life left; the daily pull will just work.
  never_linked   the source is configured but nobody has ever signed in. Nothing will pull.
  expiring_soon  a saved session inside the warn window. Still works today; ask for a re-login now,
                 in daylight, rather than at 3am when the pull fails.
  expired        the stored session is past its expiry. The next pull WILL need a human.
  needs_login    the portal actually rejected/invalidated us (auth_status flipped back). A human must
                 re-authenticate on the live screencast.
  error          the last attempt failed for a non-auth reason (proxy, portal down, parse). Not a
                 session problem — surfaced distinctly so a re-login is not prescribed for it.

NOTIFY-ONCE. `should_notify` gates on the state AND on what was last notified for this source, so a
chip that stays red for a week does not send seven identical alerts — a notification fires when the
state gets WORSE (or after the re-notify interval), which is the same discipline the epay-discrepancy
and connector-health alerts use.

PURE: stdlib datetime only. No DB, no network. harness_portal_session_health.py proves every branch.
"""
from datetime import datetime, timedelta, timezone

# Severity order — index is the rank `worse_of` and the escalation check compare. Lower is better.
# The ordering is a JUDGEMENT about how big a hole each state leaves in the recon, not alphabetical:
#   healthy       data is flowing.
#   expiring_soon still flowing today; a human has time to act in daylight.
#   error         the session is fine but the last pull failed for another reason (proxy, portal down,
#                 parse). Actionable, but NOT by signing in — so it must not outrank the states whose
#                 fix is a re-login, or the banner would prescribe the wrong remedy.
#   expired       tonight's pull will need a human. It has worked before.
#   needs_login   the portal ACTIVELY rejected us — a working connector just broke.
#   never_linked  nobody has ever signed in: this source has produced nothing, ever. The largest hole
#                 a recon can have, so it speaks loudest in a roll-up.
STATES = ("healthy", "expiring_soon", "error", "expired", "needs_login", "never_linked")
_RANK = {s: i for i, s in enumerate(STATES)}

# A session inside this many hours of expiry is "expiring_soon". House default; per-source override via
# data_source.session_warn_hours (mig 955) — RULE TWO: the tenant's tolerance is config, not a constant.
DEFAULT_WARN_HOURS = 24
# Re-notify about a stuck state at most this often (hours).
RENOTIFY_HOURS = 24
# A saved session with NO recorded expiry is treated as good for this long after it was linked. Portals
# do not publish their session lifetime; assuming "forever" is how a dead session hides.
ASSUMED_TTL_HOURS = 12

_ACTIONABLE = ("never_linked", "expired", "needs_login")   # states a human can fix by signing in


def _dt(v):
    """Parse a timestamp (ISO string or datetime) to an aware UTC datetime, or None. Never raises."""
    if v in (None, ""):
        return None
    if isinstance(v, datetime):
        return v if v.tzinfo else v.replace(tzinfo=timezone.utc)
    s = str(v).strip().replace("Z", "+00:00")
    try:
        d = datetime.fromisoformat(s)
    except ValueError:
        try:
            d = datetime.fromisoformat(s.split(".")[0])
        except ValueError:
            return None
    return d if d.tzinfo else d.replace(tzinfo=timezone.utc)


def _hours(a, b):
    return (a - b).total_seconds() / 3600.0


def worse_of(*states):
    """The most severe of several states — so one chip can speak for a source with several signals."""
    seen = [s for s in states if s in _RANK]
    return max(seen, key=lambda s: _RANK[s]) if seen else "healthy"


def evaluate(row, now=None, warn_hours=None):
    """The health of ONE data_source's portal session. PURE.

    `row` is the data_source row (or any dict carrying the same keys): auth_status, session_state /
    has_session, session_expires_at, last_run_at, last_attempt_at, last_status, session_warn_hours.
    Reads `has_session` when present so this works equally on a RAW row and on the SECRET-STRIPPED
    public row the API returns — the chip must never require the caller to hold session_state.

    Returns {state, headline, detail, hours_left, actionable, needs_human, since}. No secret is read
    and none can appear in the output: only booleans and timestamps are touched."""
    now = _dt(now) or datetime.now(timezone.utc)
    warn = warn_hours if warn_hours is not None else row.get("session_warn_hours")
    try:
        warn = float(warn)
        if warn <= 0:
            warn = DEFAULT_WARN_HOURS
    except (TypeError, ValueError):
        warn = DEFAULT_WARN_HOURS

    has_session = row.get("has_session")
    if has_session is None:
        has_session = bool(row.get("session_state"))
    has_session = bool(has_session)
    auth = str(row.get("auth_status") or "").strip().lower()
    exp = _dt(row.get("session_expires_at"))
    linked = _dt(row.get("session_linked_at")) or _dt(row.get("last_run_at"))
    hours_left = round(_hours(exp, now), 2) if exp else None

    # 1. The portal itself told us the session is no longer good. This outranks any stored expiry:
    #    a session can be invalidated long before the clock says it should be.
    if auth in ("needs_2fa", "needs_login", "unauthenticated"):
        return _out("needs_login", hours_left, linked,
                    "Sign-in needed — the portal asked for the second factor again.",
                    "The saved session was invalidated by the portal. Open the live login and complete "
                    "the challenge once; the daily pull resumes on the new session.")

    # 2. Never linked — configured, but nobody has ever signed in. Nothing will ever pull.
    if not has_session:
        if auth == "error":
            return _out("error", hours_left, linked,
                        "Last sign-in attempt failed.",
                        str(row.get("auth_message") or "The last sign-in did not complete.")[:300])
        return _out("never_linked", hours_left, linked,
                    "Not signed in yet — no data will pull.",
                    "This portal has never been linked. Open the live login, complete the portal's "
                    "second factor once, and the session is saved for the daily pull.")

    # 3. A stored session, judged against its expiry (or the assumed TTL when the portal published none).
    #    When the expiry is ASSUMED, the warn window must be a FRACTION of that assumed life, not the
    #    absolute warn_hours: the house warn window (24h) is wider than the assumed TTL (12h), so using
    #    it would mark every such session "expiring soon" from the second it was linked — a warning that
    #    is always on is a warning nobody reads.
    if exp is None and linked is not None:
        assumed = linked + timedelta(hours=ASSUMED_TTL_HOURS)
        hours_left = round(_hours(assumed, now), 2)
        exp = assumed
        warn = min(warn, ASSUMED_TTL_HOURS / 3.0)
    if exp is not None:
        if hours_left is not None and hours_left <= 0:
            return _out("expired", hours_left, linked,
                        "The saved session has expired.",
                        "The next scheduled pull will need a human. Re-link the portal on the live "
                        "login when convenient — before tonight's run.")
        if hours_left is not None and hours_left <= warn:
            return _out("expiring_soon", hours_left, linked,
                        "Session expires in %s." % _humanize_hours(hours_left),
                        "Still working. Re-link now, in daylight, rather than discovering it failed "
                        "after the overnight pull.")

    # 4. Session looks fine — but did the last ATTEMPT fail for a non-auth reason? Report that honestly
    #    instead of a green tick, without prescribing a re-login (a proxy fault is not a session fault).
    if str(row.get("last_status") or "").strip().lower() in ("error", "failed", "failure"):
        return _out("error", hours_left, linked,
                    "Session is valid, but the last pull failed.",
                    str(row.get("auth_message") or row.get("last_detail") or
                        "The last pull failed for a non-authentication reason.")[:300])

    return _out("healthy", hours_left, linked,
                "Signed in — the daily pull is riding the saved session.",
                ("Valid for about %s." % _humanize_hours(hours_left)) if hours_left is not None
                else "No expiry published by the portal; health is re-checked on every pull.")


def _out(state, hours_left, linked, headline, detail):
    return {"state": state, "headline": headline, "detail": detail,
            "hours_left": hours_left, "actionable": state in _ACTIONABLE,
            "needs_human": state in _ACTIONABLE,
            "since": linked.isoformat() if linked else None}


def _humanize_hours(h):
    if h is None:
        return "an unknown time"
    if h < 0:
        return "0 minutes"
    if h < 1:
        return "%d minutes" % max(1, int(round(h * 60)))
    if h < 48:
        return "%d hours" % int(round(h))
    return "%d days" % int(round(h / 24.0))


def should_notify(health, last_notified_state=None, last_notified_at=None, now=None):
    """Fire an alert for this session? PURE.

    True when the state is one a human must act on AND it either got WORSE than what we last told them,
    or the same bad news is now older than RENOTIFY_HOURS. A healthy/expiring state never pages: the
    chip carries it. This is the notify-once discipline the platform's other health alerts use — an
    integration that cries every tick is one the owner learns to ignore."""
    state = (health or {}).get("state")
    if state not in _ACTIONABLE:
        return False
    prev = str(last_notified_state or "").strip().lower()
    if prev not in _RANK:
        return True
    if _RANK[state] > _RANK[prev]:
        return True
    if _RANK[state] < _RANK[prev]:
        return False                      # it improved but is still actionable — the chip says so
    at = _dt(last_notified_at)
    if at is None:
        return True
    now = _dt(now) or datetime.now(timezone.utc)
    return _hours(now, at) >= RENOTIFY_HOURS


def summarize(rows, now=None):
    """Roll several sources' health into one banner payload: the worst state, how many need a human,
    and the per-source list the settings page renders. PURE."""
    now = _dt(now) or datetime.now(timezone.utc)
    items = []
    for r in rows or []:
        h = evaluate(r, now=now)
        items.append({"source_id": r.get("id"), "label": r.get("label"),
                      "processor": r.get("processor"), **h})
    worst = worse_of(*[i["state"] for i in items]) if items else "healthy"
    return {"worst": worst, "needs_human": sum(1 for i in items if i["needs_human"]),
            "total": len(items), "items": items}
