"""Vision health — what is wrong with the cameras, and whether the platform can fix it itself.

WHY THIS FILE EXISTS. `GET /vision/status` already knows everything: whether Google is linked, when
its token last worked, how many events arrived, how many cameras are assigned, whether an edge agent
is alive. It has known it all along. What it has never done is RUN ON ITS OWN — it answers only when
somebody opens the settings page, so the first thing that notices cameras are dark is a person
wondering why a report is empty. The owner put it plainly: check every day, fix what can be fixed,
and only then ask a human.

This module is the deciding half of that loop, and it is separated from the doing half on purpose.
Deciding "the refresh token aged out because the consent screen is still in Testing" is a judgement
about evidence; retrying a token is an action with a network call in it. The judgement is what goes
wrong silently, so the judgement is what gets proven offline — see harness_vision_health.py.

THE DOCTRINE IT FOLLOWS is the data-health monitor's (docs/DATA_HEALTH_MONITOR.md), deliberately,
because it is already proven in this platform and operators already recognise its shape:

    auto-check  ->  auto-fix what is genuinely fixable  ->  re-check  ->  escalate only what is left

AUTO-FIX IS A SHORT LIST, AND THAT IS THE POINT. A monitor that "tries things" against a camera
estate can do real damage: hammering Google's executeCommand budget, or re-authorising in a loop.
Only two actions here are safe to take unattended, and both are idempotent and cheap:

    retry_token      a token refresh that failed on a 5xx or a timeout is a RETRY. One that failed
                     on invalid_grant is NOT — no amount of retrying mints a grant the user revoked,
                     and hammering it looks like an attack on their account.
    resync_devices   re-list the devices from Google. Safe, read-only, and it is the actual fix when
                     a camera was renamed or re-homed in the Google Home app.

NOTE WHAT IS DELIBERATELY NOT AUTOMATED, because it looks automatable. When Google's events stop
arriving, the cause is almost always the Pub/Sub push subscription — which lives in the CUSTOMER's
own Google Cloud project, somewhere this platform cannot reach. Re-listing devices would "do
something" and change nothing, and a monitor that runs a fix unable to address the cause reports a
repair it did not make. That one escalates, naming the two things to check.

Everything else ESCALATES with the specific thing a person has to do. Publishing an OAuth consent
screen, plugging a store PC back in, and drawing a counting line are not things software may do on
somebody's behalf, and pretending otherwise would produce a monitor that reports "fixed" over a shop
that is still counting nobody.

PURE. No database, no network, no clock — (snapshot, now) -> findings. That is what lets the harness
drive it through a year of failure shapes in a second, including the ones that only happen weekly.
"""

from datetime import datetime, timedelta, timezone

# ── severities, worst first ───────────────────────────────────────────────────────────────────────
# DOWN      nothing is being recorded; the tenant is losing data right now.
# DEGRADED  something still works, but a number somebody reads is wrong or missing.
# SETUP     never finished being configured. Not a regression — but it looks identical on a report.
DOWN, DEGRADED, SETUP = "down", "degraded", "setup"
_ORDER = {DOWN: 0, DEGRADED: 1, SETUP: 2}

# Google expires a refresh token after SEVEN DAYS while the OAuth consent screen is in Testing. A
# token that died at eight days or less, having been minted and then simply aged out, is that — and
# it is the single most common reason a camera estate goes dark, on a weekly rhythm nobody connects
# to a Google setting. Eight rather than seven because clocks, and because the failure is noticed on
# the next check rather than at the instant of expiry.
TESTING_TOKEN_DAYS = 8

# An estate that normally produces events and has produced none for this long is not quiet, it is
# broken. Six hours spans an overnight gap for a shop that shuts, without waiting a whole day to
# notice a Pub/Sub push that stopped at opening time.
EVENT_SILENCE_HOURS = 6

# An edge agent checks in every minute or so. Ten minutes is a restart; an hour is a problem.
AGENT_OFFLINE_MINUTES = 60


def _parse(ts):
    """A timestamp from JSON, or None. Never raises — a monitor that dies on a malformed row is
    worse than one that treats it as unknown."""
    if not ts:
        return None
    try:
        s = str(ts).strip().replace("Z", "+00:00")
        d = datetime.fromisoformat(s)
        return d if d.tzinfo else d.replace(tzinfo=timezone.utc)
    except (TypeError, ValueError):
        return None


def _int(v):
    """A count from a payload, or 0. int() on a stray string raises, and this runs on a schedule
    against whatever shape the status endpoint happens to return after somebody edits it."""
    try:
        return int(v)
    except (TypeError, ValueError):
        return 0


def _hours_since(ts, now):
    d = _parse(ts)
    return None if d is None else (now - d).total_seconds() / 3600.0


def _finding(code, severity, title, detail, remedy, auto=None, where=None):
    return {"code": code, "severity": severity, "title": title, "detail": detail,
            "remedy": remedy, "auto": auto, "where": where}


def assess(snap, now=None):
    """The snapshot from /vision/status -> an ordered list of findings.

    `snap` is trusted to be that shape and is read defensively anyway: this runs unattended on a
    schedule, and a monitor that throws is a monitor nobody notices has stopped.
    """
    now = now or datetime.now(timezone.utc)
    snap = snap or {}
    g = snap.get("google") or {}
    ev = snap.get("events") or {}
    cams = snap.get("cameras") or {}
    agents = snap.get("edge_agents") or {}
    cfg = snap.get("config") or {}
    out = []

    # ── 1. THE GOOGLE GRANT, which is the root of everything else ────────────────────────────────
    if not g.get("linked"):
        out.append(_finding(
            "google_not_linked", SETUP,
            "Google is not connected",
            "No Google Device Access credential is stored for this company, so no camera can be "
            "reached at all.",
            "Run Vision → Camera Setup. It walks through the three Google consoles one screen at a "
            "time and checks each step.",
            where="/vision/onboarding"))
        return out                      # nothing below can be judged without a grant

    err = (g.get("last_error") or "").lower()
    status = (g.get("status") or "").lower()
    bad_grant = "invalid_grant" in err or status == "revoked"

    if bad_grant:
        # THE WEEKLY SIGNATURE. A grant that was minted, worked, and then aged out inside eight days
        # is the Testing-mode expiry — not a revocation, not a password change. Naming it is the
        # whole value of this check: the generic message sends people hunting a Google outage, and
        # they reconnect, and it dies again the following week.
        issued = _parse(g.get("token_issued_at"))
        died = _parse(g.get("last_error_at")) or now
        weekly = issued is not None and timedelta(0) <= (died - issued) <= timedelta(days=TESTING_TOKEN_DAYS)
        if weekly:
            days = round((died - issued).total_seconds() / 86400.0, 1)
            out.append(_finding(
                "google_token_expired_testing", DOWN,
                "Google sign-in expired after %s days — the consent screen is still in Testing" % days,
                "The grant was created and then expired %s days later, which is Google's seven-day "
                "limit on refresh tokens while an OAuth consent screen is in Testing. Reconnecting "
                "restores the cameras, and they will go dark again the same way next week until the "
                "app is published." % days,
                "TWO STEPS, and the second is the one that matters. (1) Reconnect Google in Vision "
                "→ Settings to bring the cameras back now. (2) In the Google Cloud console open "
                "APIs & Services → OAuth consent screen and PUBLISH the app to Production. Until "
                "that is done this recurs every seven days.",
                where="/vision/settings"))
        else:
            out.append(_finding(
                "google_grant_revoked", DOWN,
                "Google sign-in is no longer valid",
                "Google rejected the stored grant (invalid_grant). That happens when the "
                "authorisation was revoked, the Google account's password changed, or the token sat "
                "unused too long. It cannot be retried — a new authorisation is required.",
                "Reconnect Google in Vision → Settings using the same Google account that owns the "
                "cameras.",
                where="/vision/settings"))
    elif status == "error":
        # Not invalid_grant: a 5xx, a timeout, a transient refusal. This one IS a retry, and it is
        # the main thing this monitor fixes without telling anybody.
        out.append(_finding(
            "google_transient_error", DEGRADED,
            "Google was unreachable on the last attempt",
            "The last token refresh failed, but not because the grant is bad: %s" % (
                (g.get("last_error") or "no detail recorded")[:200]),
            "Usually clears by itself. If it persists for more than a few hours, reconnect Google "
            "in Vision → Settings.",
            auto="retry_token", where="/vision/settings"))

    # ── 2. ARE GOOGLE'S OWN EVENTS STILL ARRIVING ────────────────────────────────────────────────
    # A push subscription that breaks does not announce itself: Google retries into the void for
    # days. The only honest test is whether rows actually landed.
    if not bad_grant:
        last_ev = ev.get("last_event_at")
        silent_h = _hours_since(last_ev, now)
        if ev.get("available") and not last_ev and not ev.get("last_7d"):
            out.append(_finding(
                "events_never_arrived", SETUP,
                "No camera events have ever arrived",
                "Google is connected, but not one person or motion event has reached the platform. "
                "The Pub/Sub subscription is either not created or is pushing somewhere else.",
                "Vision → Camera Setup, the Pub/Sub steps. The subscription must push to this "
                "platform's events endpoint and sdm-publisher@googlegroups.com needs the Publisher "
                "role on the topic.",
                where="/vision/onboarding"))
        elif silent_h is not None and silent_h >= EVENT_SILENCE_HOURS:
            out.append(_finding(
                "events_stopped", DOWN,
                "Camera events stopped %d hours ago" % int(silent_h),
                "Events were arriving and have stopped. Google retries a broken push subscription "
                "silently, so nothing else will report this. Busy Hours is frozen as of the last "
                "event.",
                "Check the Pub/Sub subscription still points at this platform and its endpoint is "
                "reachable; then re-run the connection check in Vision → Settings.",
                where="/vision/settings"))

    # ── 3. THE CAMERAS THEMSELVES ────────────────────────────────────────────────────────────────
    total = _int(cams.get("total"))
    if not bad_grant and total == 0:
        out.append(_finding(
            "no_cameras_synced", SETUP,
            "No cameras have been pulled from Google",
            "The connection works but the device list is empty. Either no camera has been shared "
            "with this Device Access project, or the list has never been synced.",
            "Vision → Settings → Sync cameras. If it still finds none, the cameras were not "
            "included when the Google account authorised the project.",
            auto="resync_devices", where="/vision/settings"))

    unassigned = _int(cams.get("unassigned"))
    if unassigned:
        out.append(_finding(
            "cameras_unassigned", DEGRADED,
            "%d camera%s not assigned to a store" % (unassigned, "" if unassigned == 1 else "s"),
            "A camera with no store cannot appear in any store's numbers. Its events are stored and "
            "then excluded from every report, which reads as a quiet shop rather than a setup gap.",
            "Vision → Settings → Cameras, and set the store on each one.",
            where="/vision/settings"))

    # THE SILENT ZERO. An entrance with no counting line builds no gate, counts nobody, and reports
    # zero — which is indistinguishable from a day when nobody came in. This is the single failure
    # this estate actually had, on every camera, for weeks.
    entrances = _int(cams.get("entrances"))
    lined = cams.get("entrances_with_line")
    if entrances and isinstance(lined, int) and lined < entrances:
        missing = entrances - lined
        out.append(_finding(
            "entrances_without_line", DEGRADED,
            "%d entrance%s counting nobody" % (missing, "" if missing == 1 else "s"),
            "These cameras are marked as entrances but have no counting line drawn across the "
            "doorway, so the analyzer builds no gate for them and they report zero people in and "
            "zero out. Zero looks exactly like a quiet day.",
            "Vision → Counting Lines. Take a picture, drag a line across the doorway, then drag the "
            "test marker through it the way a customer walks in and check it says IN before saving.",
            where="/vision/lines"))

    # ── 4. THE STORE PC, for anything beyond Google's own events ─────────────────────────────────
    want_edge = bool(cfg.get("enabled")) and entrances > 0
    a_total = _int(agents.get("total"))
    if want_edge and a_total == 0:
        out.append(_finding(
            "no_edge_agent", SETUP,
            "No store PC is enrolled",
            "Counting people in and out, the heat map and floor activity all need a small computer "
            "in the store watching the video. Without one, only Busy Hours works.",
            "Vision → Settings → Analyzers to enrol a machine, then follow the store analyzer "
            "runbook on that PC.",
            where="/vision/settings"))
    elif a_total:
        last_seen_h = _hours_since(agents.get("last_seen_at"), now)
        if _int(agents.get("online")) == 0:
            detail = ("No enrolled store PC has checked in"
                      + (" for %d hours" % int(last_seen_h) if last_seen_h is not None else " recently")
                      + ". While it is down there is no door count, no heat map and no floor "
                        "activity — Google's own Busy Hours keeps working.")
            out.append(_finding(
                "edge_agent_offline", DOWN,
                "The store PC is not reporting",
                detail,
                "Check the machine is powered on, awake and on the network, and that the analyzer "
                "service is running. This cannot be fixed remotely.",
                where="/vision/settings"))

    out.sort(key=lambda f: (_ORDER.get(f["severity"], 9), f["code"]))
    return out


def worst(findings):
    """The single severity to show on a badge, or None when everything is healthy."""
    for sev in (DOWN, DEGRADED, SETUP):
        if any(f["severity"] == sev for f in (findings or [])):
            return sev
    return None


def auto_actions(findings):
    """The distinct auto-fixes worth attempting, in a stable order.

    De-duplicated because two findings can call for the same action and the fix should run once —
    re-listing devices twice in a tick spends Google's command budget for nothing.
    """
    seen, out = set(), []
    for f in (findings or []):
        a = f.get("auto")
        if a and a not in seen:
            seen.add(a)
            out.append(a)
    return out


def alert_ref(org_id, findings, day):
    """The dedupe key for one day's alert.

    Keyed on the SET OF CODES, not just the day: a tenant whose cameras go down at 9am and whose
    store PC then dies at noon should hear about the second one. Keyed on codes rather than on the
    full text so a count changing from 3 to 4 does not re-alert all day.
    """
    codes = ",".join(sorted({f["code"] for f in (findings or [])})) or "none"
    return "vision_health:%s:%s:%s" % (org_id, day, codes)


def summarise(findings):
    """The alert body: what is wrong, and what to do about it, in that order.

    Written to be read on a phone by somebody who was not thinking about cameras. One block per
    finding, worst first, each ending in the action — because an alert that describes a problem
    without naming the next step just moves the puzzle.
    """
    if not findings:
        return "All camera checks passed."
    lines = []
    for f in findings:
        tag = {DOWN: "DOWN", DEGRADED: "DEGRADED", SETUP: "NOT SET UP"}.get(f["severity"], "?")
        lines.append("[%s] %s\n%s\nWhat to do: %s" % (tag, f["title"], f["detail"], f["remedy"]))
    return "\n\n".join(lines)
