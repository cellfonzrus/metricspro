"""Marketing event management — the PURE decisions (migs 986/987, owner directive 2026-09-06).

Everything in this file is a function of its arguments. No fastapi, no supabase, no network, no
clock reads except where a `now` is passed in. That is what lets `backend/harness_marketing_event.py`
prove the module's behaviour in the bare container, and what keeps the router a thin translation
layer over decisions that were argued about here.

RULE TWO — CONFIG, NEVER CODE
─────────────────────────────
The owner was explicit: "none of the options I mentioned above are hard coded but options pre added
with plus sign to add more as per user discretion". So this file names LISTS, never VALUES.
`LIST_KEYS` says there is a list of themes; nothing here knows that "back to school" is one of them,
and adding one is an INSERT into `core.marketing_option`, never a deploy. Grep this file for a
carrier, tenant, theme, venue, vendor or transport name and you will not find one — the house
starting vocabulary lives entirely in migration 987.

Two constant families are deliberately NOT config, and it is worth being precise about why:

  · STATUSES / TRANSITIONS — a lifecycle, not a vocabulary. `approved` gates editing and `closed`
    gates the debrief; these strings mean something to code, so making them tenant-editable would
    mean a tenant could rename their way out of a rule. A tenant who wants different words gets a
    label override, not a different state machine.
  · CONFIRM_STATES / LINK_STATUSES — same argument, smaller: `declined` is what makes a backup
    become necessary.

WHAT THIS FILE REFUSES TO DECIDE
────────────────────────────────
It never decides that a person failed to show up. `no_show` exists as a state a HUMAN sets after the
fact; nothing here infers it from a missing check-in, because "no GPS check-in" has at least four
innocent explanations (dead phone, denied permission, no signal in a mall, forgot) and turning that
into an attendance record about a named employee would be both wrong and unkind.

Proof: `backend/harness_marketing_event.py`.
"""
from datetime import datetime, timedelta, timezone

# ══════════════════════════════════════════════════════════════════════════════════════════════════
# 1. THE OPTION LISTS (names of lists — never their contents)
# ══════════════════════════════════════════════════════════════════════════════════════════════════
LIST_THEME = "theme"
LIST_VENUE_TYPE = "venue_type"
LIST_PARTY_TYPE = "party_type"
LIST_TRANSPORT_MODE = "transport_mode"
LIST_GIVEAWAY_TYPE = "giveaway_type"
LIST_EVENT_ROLE = "event_role"
LIST_LINK_CHANNEL = "link_channel"
LIST_GOAL_METRIC = "goal_metric"

#: Every "+ add more" list in the module. A new list is one entry here plus rows in the table.
LIST_KEYS = (LIST_THEME, LIST_VENUE_TYPE, LIST_PARTY_TYPE, LIST_TRANSPORT_MODE,
             LIST_GIVEAWAY_TYPE, LIST_EVENT_ROLE, LIST_LINK_CHANNEL, LIST_GOAL_METRIC)

#: Human labels for the lists themselves (the settings screen's section headings). Renaming a
#: SECTION is cosmetic; the keys above are the contract.
LIST_LABELS = {
    LIST_THEME: "Event themes",
    LIST_VENUE_TYPE: "Venue types",
    LIST_PARTY_TYPE: "Outside party types",
    LIST_TRANSPORT_MODE: "Transport modes",
    LIST_GIVEAWAY_TYPE: "Giveaway types",
    LIST_EVENT_ROLE: "Roles at an event",
    LIST_LINK_CHANNEL: "Creative channels",
    LIST_GOAL_METRIC: "Goal metrics",
}

_KEY_OK = set("abcdefghijklmnopqrstuvwxyz0123456789_.-")


def normalize_option_key(raw):
    """A submitted option key, lowercased and reduced to the safe alphabet, or None. Users type
    labels ("Back to School"); this derives the stable key so the "+" button needs one field, not
    two. Collapses runs of separators so "Food  Truck!!" and "food-truck" don't become two options
    that look identical in a dropdown."""
    s = str(raw or "").strip().lower()
    if not s:
        return None
    out = []
    for ch in s:
        if ch in _KEY_OK:
            out.append(ch)
        elif ch.isspace() or ch in "/\\&,+":
            out.append("_")
        # anything else is dropped
    key = "".join(out)
    while "__" in key:
        key = key.replace("__", "_")
    key = key.strip("_.-")
    return key[:64] or None


def resolve_options(house_rows, tenant_rows, list_key, include_inactive=False):
    """The effective picker for ONE list: HOUSE starting vocabulary overlaid by the TENANT's rows,
    tenant winning per key. Mirrors how nav labels and report labels already resolve (tenant ∪ house,
    tenant wins), so there is one mental model for "config with a house default" in this codebase.

    A tenant row wins ENTIRELY, not field-by-field: deactivating the house 'dj' row is done by
    writing a tenant 'dj' row with is_active false, which is exactly what the settings screen does.
    Inactive options are dropped from pickers but never deleted, so an event booked last year still
    renders the label it was booked with.

    Returns [{key, label, sort_order, is_active, extra, source}] ordered by (sort_order, label).
    """
    merged = {}
    for row in (house_rows or []):
        if (row.get("list_key") or "") != list_key:
            continue
        k = row.get("key")
        if k:
            merged[k] = dict(row, source="house")
    for row in (tenant_rows or []):
        if (row.get("list_key") or "") != list_key:
            continue
        k = row.get("key")
        if k:
            merged[k] = dict(row, source="tenant")
    out = []
    for k, row in merged.items():
        active = row.get("is_active")
        active = True if active is None else bool(active)
        if not active and not include_inactive:
            continue
        extra = row.get("extra")
        out.append({"key": k, "label": row.get("label") or k,
                    "sort_order": _int(row.get("sort_order"), 100),
                    "is_active": active,
                    "extra": extra if isinstance(extra, dict) else {},
                    "source": row.get("source")})
    out.sort(key=lambda o: (o["sort_order"], (o["label"] or "").lower()))
    return out


def option_label(options, key, fallback=None):
    """Display label for a stored key. An UNKNOWN key (the option was deleted, or the row predates
    the vocabulary) returns the key itself rather than a blank — a screen that silently drops a
    value a human typed is worse than one that shows an unfamiliar word."""
    if not key:
        return fallback
    for o in (options or []):
        if o.get("key") == key:
            return o.get("label") or key
    return fallback if fallback is not None else key


# ══════════════════════════════════════════════════════════════════════════════════════════════════
# 2. LIFECYCLE — draft → approved → live → closed  (a state machine, not a vocabulary)
# ══════════════════════════════════════════════════════════════════════════════════════════════════
STATUS_DRAFT = "draft"
STATUS_APPROVED = "approved"
STATUS_LIVE = "live"
STATUS_CLOSED = "closed"
STATUS_CANCELLED = "cancelled"
STATUSES = (STATUS_DRAFT, STATUS_APPROVED, STATUS_LIVE, STATUS_CLOSED, STATUS_CANCELLED)

#: What may follow what. Cancellation is reachable from anywhere except a closed event (an event
#: that already happened cannot un-happen). Re-opening a closed event is deliberately NOT allowed:
#: the debrief is the record of what happened, and editing history in place would make the actuals
#: report unreadable — a correction is a note on the debrief.
TRANSITIONS = {
    STATUS_DRAFT: (STATUS_APPROVED, STATUS_LIVE, STATUS_CANCELLED),
    STATUS_APPROVED: (STATUS_LIVE, STATUS_DRAFT, STATUS_CANCELLED),
    STATUS_LIVE: (STATUS_CLOSED, STATUS_CANCELLED),
    STATUS_CLOSED: (),
    STATUS_CANCELLED: (STATUS_DRAFT,),
}

#: Statuses in which the plan (staffing, checklist, logistics) is still freely editable.
EDITABLE_STATUSES = (STATUS_DRAFT, STATUS_APPROVED, STATUS_LIVE)

CONFIRM_PLANNED = "planned"
CONFIRM_CONFIRMED = "confirmed"
CONFIRM_DECLINED = "declined"
CONFIRM_NO_SHOW = "no_show"
CONFIRM_STATES = (CONFIRM_PLANNED, CONFIRM_CONFIRMED, CONFIRM_DECLINED, CONFIRM_NO_SHOW)

#: A person in one of these is NOT coming, so their backup matters.
UNAVAILABLE_STATES = (CONFIRM_DECLINED, CONFIRM_NO_SHOW)

VENDOR_STATES = ("planned", "confirmed", "declined", "cancelled")
LINK_STATUSES = ("planned", "scheduled", "posted", "cancelled")

APPROVAL_NOT_REQUIRED = "not_required"
APPROVAL_PENDING = "pending"
APPROVAL_APPROVED = "approved"
APPROVAL_REJECTED = "rejected"
APPROVAL_STATES = (APPROVAL_NOT_REQUIRED, APPROVAL_PENDING, APPROVAL_APPROVED, APPROVAL_REJECTED)


def can_transition(current, target):
    """(ok, reason). The reason is written to be shown to a user, not logged and forgotten."""
    cur = (current or STATUS_DRAFT)
    if target not in STATUSES:
        return False, "Unknown status %r." % (target,)
    if cur not in STATUSES:
        return False, "This event has an unrecognised status (%r) and must be corrected first." % (cur,)
    if cur == target:
        return True, "Already %s." % (target,)
    if target in TRANSITIONS.get(cur, ()):
        return True, ""
    if cur == STATUS_CLOSED:
        return False, ("A closed event cannot be reopened — its debrief is the record of what "
                       "happened. Add a note to the debrief instead.")
    return False, "An event cannot go from %s to %s." % (cur, target)


def gate_go_live(status, approval_state):
    """(ok, reason) for draft/approved → live. THE approval gate, and the only place it is applied.

    When approval is switched off for the org, `approval_state` is the explicitly-stored
    `not_required` (mig 986 writes it rather than leaving NULL), so this reads the same way for an
    org that never turns approval on as for one that does — there is no "approval is off" branch
    here at all, which is what stops the off-by-default posture from rotting.
    """
    if status == STATUS_LIVE:
        return True, "Already live."
    ok, why = can_transition(status, STATUS_LIVE)
    if not ok:
        return False, why
    state = approval_state or APPROVAL_NOT_REQUIRED
    if state in (APPROVAL_NOT_REQUIRED, APPROVAL_APPROVED):
        return True, ""
    if state == APPROVAL_PENDING:
        return False, "This event needs approval before it can go live."
    if state == APPROVAL_REJECTED:
        return False, "This event was not approved. Update the plan and resubmit."
    return False, "Unrecognised approval state (%r)." % (state,)


# ══════════════════════════════════════════════════════════════════════════════════════════════════
# 3. APPROVAL — per-org switch, DEFAULT OFF, with an optional spend threshold
# ══════════════════════════════════════════════════════════════════════════════════════════════════
#: The house posture, used verbatim when an org has no config row at all. Approval OFF.
DEFAULT_CONFIG = {
    "approval_required": False,
    "approval_spend_threshold": None,
    "default_checkin_radius_m": 150,
    "max_checkin_accuracy_m": 200,
    "block_checkin_outside_fence": False,
    "checkin_geo_retention_days": 180,
    "staffing_alert_lead_hours": 48,
}


def resolve_config(row):
    """A marketing_config row (or None / a partial one) → the full effective config. A missing or
    NULL field falls back to the house default, so a half-written row can never silently switch
    approval on or shrink a geofence to zero."""
    cfg = dict(DEFAULT_CONFIG)
    for k, default in DEFAULT_CONFIG.items():
        if not isinstance(row, dict) or k not in row or row[k] is None:
            continue
        v = row[k]
        if isinstance(default, bool):
            cfg[k] = bool(v)
        elif k == "approval_spend_threshold":
            cfg[k] = _num(v)
        elif isinstance(default, int):
            cfg[k] = _int(v, default)
        else:
            cfg[k] = v
    return cfg


def approval_decision(config, planned_spend):
    """Does THIS event need an approval? → {required, state, reason}.

    The two rules, in order, and the order is the whole point:

      1. The org switch is OFF (the default, and what every org gets until someone changes it) —
         nothing needs approval, and the spend threshold is not even consulted. A threshold left
         behind by an org that later turned approval off must not quietly keep gating events.
      2. The switch is ON — a threshold, if set, EXEMPTS events at or below it; with no threshold
         every event needs approval.

    `state` is what gets stored on the event, and `reason` is stored beside it, so an event that
    went live without a signature always records why.
    """
    cfg = resolve_config(config if isinstance(config, dict) else None)
    if not cfg["approval_required"]:
        return {"required": False, "state": APPROVAL_NOT_REQUIRED,
                "reason": "Event approval is switched off for this organisation."}
    threshold = cfg["approval_spend_threshold"]
    spend = _num(planned_spend)
    if threshold is not None:
        if spend is None:
            return {"required": True, "state": APPROVAL_PENDING,
                    "reason": ("Approval is required above %s and this event has no planned spend "
                               "entered, so it cannot be shown to be under the limit."
                               % _money(threshold))}
        if spend <= threshold:
            return {"required": False, "state": APPROVAL_NOT_REQUIRED,
                    "reason": ("Planned spend %s is at or under the %s approval threshold."
                               % (_money(spend), _money(threshold)))}
        return {"required": True, "state": APPROVAL_PENDING,
                "reason": ("Planned spend %s is above the %s approval threshold."
                           % (_money(spend), _money(threshold)))}
    return {"required": True, "state": APPROVAL_PENDING,
            "reason": "This organisation requires every event to be approved."}


# ══════════════════════════════════════════════════════════════════════════════════════════════════
# 4. PEOPLE — the backup roster, and who is driving whom
# ══════════════════════════════════════════════════════════════════════════════════════════════════
def resolve_staffing(staff_rows, checkins=None):
    """The answer to "who is actually working this event, and what is missing?".

    `staff_rows`  — core.marketing_event_staff rows.
    `checkins`    — optional marketing_event_checkin rows; presence marks a person ARRIVED. Absence
                    is NEVER read as absence (see the module header) — it only means "not yet
                    confirmed on site".

    Returns:
      roster            [{...staff row, is_covered, backup, arrived, effective}] — primaries only
      backups_by_primary {primary_staff_id: [backup rows]}
      uncovered         primaries who are unavailable and have NO available backup — the list a
                        manager actually needs
      unassigned_backups backups pointing at nothing (a stale primary, or a backup nobody linked)
      counts            headline numbers for the dashboard tile

    A backup only counts as cover if the backup themself has not declined — a declined backup is a
    hole, not a plan, and reporting it as covered is how an event ends up with nobody there.
    """
    rows = [dict(r) for r in (staff_rows or [])]
    by_id = {r.get("id"): r for r in rows if r.get("id")}
    arrived_staff, arrived_emp = set(), set()
    for c in (checkins or []):
        if c.get("staff_id"):
            arrived_staff.add(c.get("staff_id"))
        if c.get("employee_id"):
            arrived_emp.add(str(c.get("employee_id")))

    def _arrived(r):
        return bool(r.get("id") in arrived_staff
                    or (r.get("employee_id") and str(r.get("employee_id")) in arrived_emp))

    primaries = [r for r in rows if not r.get("is_backup")]
    backups = [r for r in rows if r.get("is_backup")]

    backups_by_primary, unassigned_backups = {}, []
    for b in backups:
        pid = b.get("backup_for_staff_id")
        if pid and pid in by_id and not by_id[pid].get("is_backup"):
            backups_by_primary.setdefault(pid, []).append(b)
        else:
            unassigned_backups.append(b)

    roster, uncovered = [], []
    for p in primaries:
        state = p.get("confirm_state") or CONFIRM_PLANNED
        unavailable = state in UNAVAILABLE_STATES
        cands = backups_by_primary.get(p.get("id"), [])
        # A backup that has itself declined/no-showed is not cover.
        usable = [b for b in cands
                  if (b.get("confirm_state") or CONFIRM_PLANNED) not in UNAVAILABLE_STATES]
        # Prefer an explicitly CONFIRMED backup over a merely planned one — same reason a manager
        # would: one of them has actually said yes.
        usable.sort(key=lambda b: (0 if (b.get("confirm_state") == CONFIRM_CONFIRMED) else 1,
                                   str(b.get("employee_name") or "")))
        chosen = usable[0] if usable else None
        entry = dict(p)
        entry["backup"] = chosen
        entry["backup_count"] = len(cands)
        entry["is_covered"] = bool(chosen)
        entry["arrived"] = _arrived(p)
        # Who is expected to actually be there for this slot.
        entry["effective"] = chosen if (unavailable and chosen) else (None if unavailable else p)
        roster.append(entry)
        if unavailable and not chosen:
            uncovered.append(entry)

    confirmed = sum(1 for p in primaries if (p.get("confirm_state") == CONFIRM_CONFIRMED))
    declined = sum(1 for p in primaries
                   if (p.get("confirm_state") or CONFIRM_PLANNED) in UNAVAILABLE_STATES)
    return {
        "roster": roster,
        "backups_by_primary": backups_by_primary,
        "uncovered": uncovered,
        "unassigned_backups": unassigned_backups,
        "counts": {
            "planned": len(primaries),
            "confirmed": confirmed,
            "unconfirmed": len(primaries) - confirmed - declined,
            "declined": declined,
            "with_backup": sum(1 for e in roster if e["is_covered"]),
            "without_backup": sum(1 for e in roster if not e["is_covered"]),
            "uncovered": len(uncovered),
            "arrived": sum(1 for e in roster if e["arrived"]),
            "backups": len(backups),
        },
    }


def resolve_transport(staff_rows, transport_options=None):
    """"How are employees getting there / Who is picking up who if needed" (owner).

    Returns {rides, needs_ride, problems, drivers}:
      rides      {driver_staff_id: [passenger rows]} — the pickup graph, as data
      needs_ride people whose transport mode is flagged `needs_pickup` in its option row but who
                 have no driver assigned. THE flag on the option row is what is read — never a mode
                 NAME — so a tenant-invented "company shuttle" behaves correctly the moment it is
                 added with that flag.
      problems   assignments that cannot work: a driver who isn't on the event, a driver who has
                 declined, and pickup cycles (A picks up B who picks up A — nobody drives).
    """
    rows = [dict(r) for r in (staff_rows or [])]
    by_id = {r.get("id"): r for r in rows if r.get("id")}
    needs_pickup_modes, mode_labels = set(), {}
    for o in (transport_options or []):
        mode_labels[o.get("key")] = o.get("label") or o.get("key")
        if isinstance(o.get("extra"), dict) and o["extra"].get("needs_pickup"):
            needs_pickup_modes.add(o.get("key"))

    rides, problems, needs_ride = {}, [], []
    for r in rows:
        if (r.get("confirm_state") or CONFIRM_PLANNED) == CONFIRM_DECLINED:
            continue
        driver_id = r.get("pickup_by_staff_id")
        if driver_id:
            driver = by_id.get(driver_id)
            if not driver:
                problems.append({"kind": "driver_not_on_event", "staff": r,
                                 "detail": "%s is assigned a driver who is not on this event."
                                           % _who(r)})
                continue
            if (driver.get("confirm_state") or CONFIRM_PLANNED) in UNAVAILABLE_STATES:
                problems.append({"kind": "driver_unavailable", "staff": r, "driver": driver,
                                 "detail": "%s is being picked up by %s, who is not coming."
                                           % (_who(r), _who(driver))})
            rides.setdefault(driver_id, []).append(r)
        elif r.get("transport_mode_key") in needs_pickup_modes:
            needs_ride.append(r)
            # The message names the tenant's OWN label for the mode rather than a word this file
            # chose ("carpooling"), so a tenant-added "Company Shuttle" reads correctly and RULE TWO
            # holds even in user-facing copy.
            problems.append({"kind": "no_driver", "staff": r,
                             "detail": "%s is getting there by %s but nobody is assigned to pick "
                                       "them up."
                                       % (_who(r),
                                          mode_labels.get(r.get("transport_mode_key"))
                                          or "a mode that needs a pickup")})

    # Pickup cycles. Following each person's driver chain must terminate at somebody who drives
    # themself; if it loops, no car ever starts.
    for r in rows:
        seen, cur = set(), r
        while cur is not None and cur.get("pickup_by_staff_id"):
            cid = cur.get("id")
            if cid in seen:
                problems.append({"kind": "pickup_cycle", "staff": r,
                                 "detail": "The pickup chain starting at %s loops back on itself — "
                                           "nobody in it is driving." % _who(r)})
                break
            seen.add(cid)
            cur = by_id.get(cur.get("pickup_by_staff_id"))

    return {"rides": rides, "needs_ride": needs_ride, "problems": problems,
            "drivers": [by_id[d] for d in rides if d in by_id]}


def call_time_for(staff_row, event_row):
    """When THIS person has to be there: their own override, else the event's staff call time, else
    (only if neither was set) the event start. The fallback chain is explicit because "what time do
    the employees have to get there" was asked as its own question — an event that never set a call
    time should say "same as event start", not show a blank."""
    if isinstance(staff_row, dict) and staff_row.get("call_time_override"):
        return staff_row["call_time_override"], "personal"
    if isinstance(event_row, dict) and event_row.get("staff_call_at"):
        return event_row["staff_call_at"], "event"
    if isinstance(event_row, dict) and event_row.get("event_start"):
        return event_row["event_start"], "event_start_fallback"
    return None, "unset"


# ══════════════════════════════════════════════════════════════════════════════════════════════════
# 5. CHECKLIST + GIVEAWAYS — packed out, brought back, and what didn't come back
# ══════════════════════════════════════════════════════════════════════════════════════════════════
def checklist_readiness(items):
    """{total, packed, unpacked, pct_packed, returnable, returned, outstanding_returns, complete}.

    `complete` means every item is packed — the thing checked before an event. `outstanding_returns`
    is the after-the-event question, and only counts items marked returnable: nobody is expected to
    bring the flyers back.
    """
    rows = list(items or [])
    total = len(rows)
    packed = sum(1 for i in rows if i.get("is_packed"))
    returnable = [i for i in rows if i.get("is_returnable", True)]
    returned = sum(1 for i in returnable if i.get("is_returned"))
    outstanding = [i for i in returnable if i.get("is_packed") and not i.get("is_returned")]
    return {
        "total": total, "packed": packed, "unpacked": total - packed,
        "pct_packed": (round(100.0 * packed / total, 1) if total else 0.0),
        "returnable": len(returnable), "returned": returned,
        "outstanding_returns": len(outstanding),
        "outstanding_items": outstanding,
        "complete": bool(total and packed == total),
    }


def instantiate_template(template_items, event_id, org_id):
    """Template rows → event checklist rows. The copy is deliberate: once an event's checklist is
    created it is the EVENT's, and editing the template afterwards must not rewrite the history of
    an event that already ran."""
    out = []
    for i, t in enumerate(template_items or []):
        out.append({
            "org_id": org_id, "event_id": event_id,
            "label": t.get("label"), "category": t.get("category"), "qty": t.get("qty"),
            "is_returnable": bool(t.get("is_returnable", True)),
            "sort_order": _int(t.get("sort_order"), (i + 1) * 10),
        })
    return out


def giveaway_reconciliation(rows):
    """Out vs back, per item and in total, so shrinkage is visible rather than inferred.

    unaccounted = qty_out − qty_returned − qty_given. It is reported, never judged: a positive
    number can be genuine loss, or it can be a busy team that handed out prizes and never counted.
    The number is shown with the counts that produced it so a human can tell which.
    """
    items, t_out, t_ret, t_given, t_unacc = [], 0.0, 0.0, 0.0, 0.0
    for r in (rows or []):
        out_q = _num(r.get("qty_out")) or 0.0
        ret_q = _num(r.get("qty_returned"))
        giv_q = _num(r.get("qty_given"))
        counted = (ret_q is not None) or (giv_q is not None)
        unacc = (out_q - (ret_q or 0.0) - (giv_q or 0.0)) if counted else None
        items.append({
            "id": r.get("id"), "item_label": r.get("item_label"),
            "giveaway_type_key": r.get("giveaway_type_key"),
            "qty_out": out_q, "qty_returned": ret_q, "qty_given": giv_q,
            "unaccounted": (None if unacc is None else round(unacc, 2)),
            "counted": counted,
        })
        t_out += out_q
        t_ret += (ret_q or 0.0)
        t_given += (giv_q or 0.0)
        if unacc is not None:
            t_unacc += unacc
    uncounted = [i for i in items if not i["counted"]]
    return {
        "items": items,
        "totals": {"qty_out": round(t_out, 2), "qty_returned": round(t_ret, 2),
                   "qty_given": round(t_given, 2), "unaccounted": round(t_unacc, 2)},
        "uncounted_items": len(uncounted),
        # Honest headline: a total that ignores un-counted rows is not "nothing is missing".
        "note": ("%d item(s) were taken out but never counted back in, so the unaccounted total "
                 "below covers only the items that were counted." % len(uncounted)) if uncounted
                else "Every item taken out has been counted back in.",
    }


# ══════════════════════════════════════════════════════════════════════════════════════════════════
# 6. TIME — the event window, and the dates the actuals read covers
# ══════════════════════════════════════════════════════════════════════════════════════════════════
def parse_dt(value):
    """A timestamp from the DB / an API body → aware datetime (UTC), or None. Postgres hands back
    '+00:00' and sometimes 'Z'; both must work, and a bad value must be None rather than an
    exception that 500s a page."""
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    s = str(value).strip()
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(s)
    except ValueError:
        try:
            dt = datetime.fromisoformat(s[:19])
        except ValueError:
            return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def event_dates(event):
    """The calendar dates (YYYY-MM-DD) an event spans — what the actuals read is filtered to.

    Deliberately calendar dates, not an hour range: the sales pass this module reads is keyed per
    (store, rep, DAY) and carries no time of day, so pretending to filter a 10am-4pm event to those
    hours would be a precision the underlying data does not have. That limitation is stated in the
    actuals report rather than hidden behind a plausible-looking number.
    """
    start = parse_dt(event.get("event_start")) if isinstance(event, dict) else None
    end = parse_dt(event.get("event_end")) if isinstance(event, dict) else None
    if start is None and end is None:
        return []
    start = start or end
    end = end or start
    if end < start:
        start, end = end, start
    days, cur, guard = [], start.date(), 0
    last = end.date()
    while cur <= last and guard < 400:      # a 400-day "event" is a data error, not a festival
        days.append(cur.isoformat())
        cur = cur + timedelta(days=1)
        guard += 1
    return days


def baseline_dates(event, weeks=4):
    """The comparison window: the SAME weekdays, `weeks` weeks before the event.

    Same weekday matters — a Saturday table event compared against an average that includes Tuesdays
    would flatter itself. This is a like-for-like comparison and it is still only a comparison; see
    `actuals.py` for how carefully the result is labelled.
    """
    days = event_dates(event)
    if not days:
        return []
    out = []
    for d in days:
        base = datetime.fromisoformat(d).date()
        for w in range(1, max(1, int(weeks)) + 1):
            out.append((base - timedelta(days=7 * w)).isoformat())
    return sorted(set(out))


def period_keys_for_dates(dates):
    """The YYYY-MM period strings covering these dates — the key commcalc's shared sales pass is
    queried by. One entry per month touched, so an event spanning a month boundary reads both."""
    return sorted({d[:7] for d in (dates or []) if d and len(d) >= 7})


# ══════════════════════════════════════════════════════════════════════════════════════════════════
# 7. ATTENTION — what a human needs to know about an upcoming event
# ══════════════════════════════════════════════════════════════════════════════════════════════════
def event_readiness(event, staff_rows, checklist_items, vendors=None, now=None,
                    lead_hours=48, transport_options=None):
    """One event → the list of things wrong with it, worst first. Shared by the event screen, the
    dashboard and the attention providers, so a manager's screen and their notification can never
    disagree about whether an event is ready.

    `lead_hours` is what makes this URGENT rather than merely true: an event three weeks out with
    nobody confirmed is normal planning; the same event tomorrow is a problem. Issues are only
    raised for events inside the window, and never for draft, closed or cancelled ones.
    """
    now = now or datetime.now(timezone.utc)
    issues = []
    status = (event.get("status") or STATUS_DRAFT) if isinstance(event, dict) else STATUS_DRAFT
    start = parse_dt(event.get("event_start")) if isinstance(event, dict) else None
    call = parse_dt(event.get("staff_call_at")) if isinstance(event, dict) else None
    horizon = call or start
    hours_out = None if horizon is None else (horizon - now).total_seconds() / 3600.0

    imminent = (hours_out is not None and 0 <= hours_out <= float(lead_hours))
    if status in (STATUS_CLOSED, STATUS_CANCELLED, STATUS_DRAFT) or not imminent:
        return {"issues": [], "imminent": False, "hours_out": hours_out, "status": status}

    staffing = resolve_staffing(staff_rows)
    counts = staffing["counts"]
    if counts["planned"] == 0:
        issues.append({"severity": "error", "key": "no_staff",
                       "detail": "No staff are planned for this event at all."})
    else:
        if counts["unconfirmed"]:
            issues.append({"severity": "warning", "key": "unconfirmed_staff",
                           "count": counts["unconfirmed"],
                           "detail": "%d of %d planned staff have not confirmed."
                                     % (counts["unconfirmed"], counts["planned"])})
        if counts["uncovered"]:
            issues.append({"severity": "error", "key": "uncovered_slot",
                           "count": counts["uncovered"],
                           "detail": "%d person/people are not coming and have no available backup."
                                     % counts["uncovered"]})
        elif counts["without_backup"]:
            issues.append({"severity": "warning", "key": "no_backup",
                           "count": counts["without_backup"],
                           "detail": "%d of %d planned staff have no named backup."
                                     % (counts["without_backup"], counts["planned"])})

    ready = checklist_readiness(checklist_items)
    if ready["total"] == 0:
        issues.append({"severity": "warning", "key": "no_checklist",
                       "detail": "No checklist — nothing says what has to be taken to this event."})
    elif not ready["complete"]:
        issues.append({"severity": "warning", "key": "checklist_incomplete",
                       "count": ready["unpacked"],
                       "detail": "%d of %d checklist items are not packed."
                                 % (ready["unpacked"], ready["total"])})

    transport = resolve_transport(staff_rows, transport_options)
    if transport["problems"]:
        issues.append({"severity": "warning", "key": "transport_gap",
                       "count": len(transport["problems"]),
                       "detail": transport["problems"][0]["detail"]
                                 + ("" if len(transport["problems"]) == 1
                                    else " (+%d more)" % (len(transport["problems"]) - 1))})

    unconfirmed_vendors = [v for v in (vendors or [])
                           if (v.get("confirm_state") or "planned") == "planned"]
    if unconfirmed_vendors:
        issues.append({"severity": "warning", "key": "vendor_unconfirmed",
                       "count": len(unconfirmed_vendors),
                       "detail": "%d outside part%s not confirmed."
                                 % (len(unconfirmed_vendors),
                                    "y is" if len(unconfirmed_vendors) == 1 else "ies are")})

    if isinstance(event, dict) and (event.get("approval_state") == APPROVAL_PENDING):
        issues.append({"severity": "error", "key": "awaiting_approval",
                       "detail": "This event is still waiting for approval."})

    order = {"error": 0, "warning": 1, "info": 2}
    issues.sort(key=lambda i: order.get(i["severity"], 9))
    return {"issues": issues, "imminent": True, "hours_out": hours_out, "status": status,
            "staffing": counts, "checklist": ready}


# ══════════════════════════════════════════════════════════════════════════════════════════════════
# 8. GPS RETENTION — the promise, made checkable
# ══════════════════════════════════════════════════════════════════════════════════════════════════
def purge_after_date(now, retention_days):
    """The date a check-in's coordinates may be removed. Stamped onto every row at write time so the
    retention commitment is visible in the row itself, not buried in a job somebody has to find."""
    n = now or datetime.now(timezone.utc)
    days = _int(retention_days, DEFAULT_CONFIG["checkin_geo_retention_days"])
    if days < 1:
        days = 1
    return (n + timedelta(days=days)).date().isoformat()


def retention_summary(checkin_rows, now=None):
    """What is due for deletion, and what is overdue. Phase 1 does not delete anything automatically
    (declared `unmonitored` on the control box in mig 987, deliberately, rather than implying a
    purge job exists), so this is what makes the gap measurable instead of theoretical."""
    n = (now or datetime.now(timezone.utc)).date().isoformat()
    due = [r for r in (checkin_rows or [])
           if r.get("purge_after_date") and str(r["purge_after_date"])[:10] <= n]
    missing = [r for r in (checkin_rows or []) if not r.get("purge_after_date")]
    return {"total": len(list(checkin_rows or [])), "due_for_purge": len(due),
            "no_retention_stamp": len(missing), "as_of": n,
            "note": ("Rows past their retention date are listed here but are NOT deleted "
                     "automatically in this phase.")}


# ── small shared helpers ─────────────────────────────────────────────────────────────────────────
def _int(v, default):
    try:
        return int(float(v))
    except (TypeError, ValueError):
        return default


def _num(v):
    if v is None or v == "":
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f


def _money(v):
    n = _num(v)
    return "$0" if n is None else ("$%s" % ("{:,.2f}".format(n).rstrip("0").rstrip(".")))


def _who(staff_row):
    return (staff_row.get("employee_name") or staff_row.get("employee_id") or "A team member")
