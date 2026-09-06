"""Marketing contributions to the UNIVERSAL admin-attention system (core/import_health.py).

DUPLICATE CHECK (CLAUDE.md build gate). The platform already owns "what needs a human": ~40
providers registered across a dozen modules, collected by `core.import_health.collect_attention`,
surfaced in the admin attention panel, deduped for notification through `storeops.alert_log`
(mig 433) and rolled up into the super-admin control box (mig 970), which derives ONE LAMP PER LIVE
PROVIDER with no extra registration. So marketing writes NO notifier, NO alert table, NO sweep and
NO second definition of "an event is in trouble" — it registers here and everything else follows.

The readiness rules themselves are NOT in this file. They are `event_logic.event_readiness`, the
same function the event page banner and the `/marketing/summary` dashboard call. That is deliberate:
a notification that disagrees with the screen it links to is worse than no notification.

Registered from marketing/router.py's own guarded bottom-of-file import — no NEEDS CORE, no main.py
change (the storevisit precedent). Read-only: nothing here writes event data.
"""
from datetime import datetime, timedelta, timezone

try:
    from app.modules.core.import_health import register_provider
except Exception:                      # import_health not present in this deployment yet
    def register_provider(*_a, **_k):
        def _deco(fn):
            return fn
        return _deco

from app.modules.marketing import event_logic as L

DEEP_LINK = "/marketing"
DEEP_LINK_LABEL = "Open Marketing"


def _cfg(client, org_id):
    try:
        rows = (client.schema("core").table("marketing_config").select("*")
                .eq("org_id", org_id).limit(1).execute().data) or []
    except Exception:
        return dict(L.DEFAULT_CONFIG)
    return L.resolve_config(rows[0] if rows else None)


def _imminent_events(client, org_id, lead_hours):
    """Events whose call time is inside the lead window. Bounded by a date range on the query, not
    fetched-then-filtered: an org with years of events must not pull all of them to find tomorrow's.

    Events are read from `event_start` because that column is indexed; the precise imminence test
    (which prefers `staff_call_at`) happens in `event_readiness`. The query window is deliberately
    widened by a day at each end so an event whose call time is the evening before its start, or
    whose start is late on the last day of the window, is not missed by the coarse filter.
    """
    now = datetime.now(timezone.utc)
    lo = (now - timedelta(days=1)).date().isoformat()
    hi = (now + timedelta(hours=float(lead_hours) + 24)).date().isoformat()
    try:
        return (client.schema("core").table("marketing_event").select("*")
                .eq("org_id", org_id).eq("is_active", True)
                .in_("status", [L.STATUS_APPROVED, L.STATUS_LIVE])
                .gte("event_start", lo).lte("event_start", hi + "T23:59:59+00:00")
                .limit(200).execute().data) or []
    except Exception:
        return []


def _children(client, org_id, table, event_ids):
    if not event_ids:
        return {}
    try:
        rows = (client.schema("core").table(table).select("*")
                .eq("org_id", org_id).in_("event_id", event_ids).limit(5000).execute().data) or []
    except Exception:
        return {}
    out = {}
    for r in rows:
        out.setdefault(r.get("event_id"), []).append(r)
    return out


def _readiness_by_event(client, org_id):
    """(events, {event_id: readiness}) for every imminent event — computed ONCE and shared by all
    three providers below, so a tenant with events pays for one pass, not three."""
    cfg = _cfg(client, org_id)
    events = _imminent_events(client, org_id, cfg["staffing_alert_lead_hours"])
    if not events:
        return [], {}, cfg
    ids = [e["id"] for e in events if e.get("id")]
    staff = _children(client, org_id, "marketing_event_staff", ids)
    checklist = _children(client, org_id, "marketing_event_checklist_item", ids)
    vendors = _children(client, org_id, "marketing_event_vendor", ids)
    try:
        opt_rows = (client.schema("core").table("marketing_option")
                    .select("list_key,key,label,sort_order,is_active,extra")
                    .eq("org_id", org_id).limit(2000).execute().data) or []
    except Exception:
        opt_rows = []
    transport_opts = L.resolve_options([], opt_rows, L.LIST_TRANSPORT_MODE)
    now = datetime.now(timezone.utc)
    ready = {}
    for e in events:
        ready[e["id"]] = L.event_readiness(e, staff.get(e["id"], []), checklist.get(e["id"], []),
                                           vendors.get(e["id"], []), now=now,
                                           lead_hours=cfg["staffing_alert_lead_hours"],
                                           transport_options=transport_opts)
    return events, ready, cfg


def _items_for(client, org_id, wanted_keys, key, label, severity, summarize):
    """Shared shape for the three providers: find imminent events carrying one of `wanted_keys` and
    emit ONE item naming how many, with the first two by name. Naming the events is what makes the
    notification actionable — "3 events need staffing" without saying which is a puzzle, not an
    alert."""
    events, ready, _ = _readiness_by_event(client, org_id)
    hits = []
    for e in events:
        issues = (ready.get(e["id"]) or {}).get("issues") or []
        found = [i for i in issues if i["key"] in wanted_keys]
        if found:
            hits.append((e, found))
    if not hits:
        return []
    names = ", ".join('"%s"' % (h[0].get("title") or "Untitled event") for h in hits[:2])
    more = "" if len(hits) <= 2 else " (+%d more)" % (len(hits) - 2)
    return [{
        "group": "other", "key": key, "severity": severity, "label": label,
        "detail": summarize(hits, names + more),
        "count": len(hits),
        "deep_link": DEEP_LINK, "deep_link_label": DEEP_LINK_LABEL,
    }]


@register_provider("marketing_event_staffing", label="Event staffing not confirmed",
                   group="other", cost="cheap")
def _p_event_staffing(client, org_id, ctx):
    """An event happening within the org's lead window whose people have not confirmed, or who have
    no staff planned at all. This is the one that costs real money when it is missed: a table booked,
    a DJ paid, and nobody rostered to stand at it."""
    return _items_for(
        client, org_id, {"no_staff", "unconfirmed_staff"},
        "marketing_event_staffing", "Event staffing not confirmed", "error",
        lambda hits, names: (
            "%d upcoming event(s) have staff who have not confirmed, or no staff planned at all: "
            "%s. Confirm the roster, or the event runs short-handed." % (len(hits), names)))


@register_provider("marketing_event_backup", label="Event has no backup staff",
                   group="other", cost="cheap")
def _p_event_backup(client, org_id, ctx):
    """The owner asked for a named backup per role. `uncovered_slot` (somebody is not coming AND has
    no available backup) is an ERROR because it is a hole that already exists; `no_backup` on its own
    is a WARNING because it is only a risk. Reported as one item with the worse severity when both
    are present — two notifications about the same event on the same evening is noise."""
    events, ready, _ = _readiness_by_event(client, org_id)
    uncovered, no_backup = [], []
    for e in events:
        keys = {i["key"] for i in ((ready.get(e["id"]) or {}).get("issues") or [])}
        if "uncovered_slot" in keys:
            uncovered.append(e)
        elif "no_backup" in keys:
            no_backup.append(e)
    if not uncovered and not no_backup:
        return []
    worst = uncovered or no_backup
    names = ", ".join('"%s"' % (e.get("title") or "Untitled event") for e in worst[:2])
    more = "" if len(worst) <= 2 else " (+%d more)" % (len(worst) - 2)
    if uncovered:
        detail = ("%d upcoming event(s) have someone who is not coming and NO available backup: "
                  "%s. Name a backup, or the slot is simply empty." % (len(uncovered), names + more))
        severity = "error"
    else:
        detail = ("%d upcoming event(s) have planned staff with no named backup: %s. If one person "
                  "drops out there is no fallback." % (len(no_backup), names + more))
        severity = "warning"
    return [{"group": "other", "key": "marketing_event_backup", "severity": severity,
             "label": "Event has no backup staff", "detail": detail,
             "count": len(uncovered) + len(no_backup),
             "deep_link": DEEP_LINK, "deep_link_label": DEEP_LINK_LABEL}]


@register_provider("marketing_event_checklist", label="Event checklist not packed",
                   group="other", cost="cheap")
def _p_event_checklist(client, org_id, ctx):
    """Kit not packed for an event that is about to happen — including the case of an event with no
    checklist at all, which is how a team arrives without the table."""
    return _items_for(
        client, org_id, {"no_checklist", "checklist_incomplete"},
        "marketing_event_checklist", "Event checklist not packed", "warning",
        lambda hits, names: (
            "%d upcoming event(s) have unpacked checklist items, or no checklist at all: %s."
            % (len(hits), names)))


@register_provider("marketing_event_approval", label="Event waiting for approval",
                   group="other", cost="cheap")
def _p_event_approval(client, org_id, ctx):
    """Only ever fires for an org that TURNED APPROVAL ON. With the default-off posture this returns
    an empty list without reading a single event row, so the feature nobody enabled costs nothing and
    — more importantly — never nags an org about a workflow it does not use."""
    cfg = _cfg(client, org_id)
    if not cfg["approval_required"]:
        return []
    try:
        rows = (client.schema("core").table("marketing_event").select("id,title,event_start")
                .eq("org_id", org_id).eq("is_active", True)
                .eq("approval_state", L.APPROVAL_PENDING).limit(200).execute().data) or []
    except Exception:
        return []
    if not rows:
        return []
    names = ", ".join('"%s"' % (r.get("title") or "Untitled event") for r in rows[:2])
    more = "" if len(rows) <= 2 else " (+%d more)" % (len(rows) - 2)
    return [{"group": "other", "key": "marketing_event_approval", "severity": "warning",
             "label": "Event waiting for approval", "detail":
                 "%d event(s) cannot go live until approved: %s." % (len(rows), names + more),
             "count": len(rows), "deep_link": DEEP_LINK, "deep_link_label": DEEP_LINK_LABEL}]
