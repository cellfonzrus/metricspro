"""THE one home for "how long may we keep where somebody was, and what happens when that runs out".

THE CLASS, not the instance (owner directive 2026-09-20). The punch geofence shipped a tenant
setting called `punch_geo_retention_days` and nothing deleted anything, which the security pass
caught. The honest finding was that this is the SECOND instance of one class, not a new bug: event
check-in GPS has carried `checkin_geo_retention_days` since mig 986 with the same hole, declared on
the control box as `marketing_checkin_gps_retention` = `unmonitored` ("no purge job runs" — index
§23.7). A retention window that deletes nothing is worse than no window at all: it reads as a
promise. So the fix is the MECHANISM — one registry of every relation that stores where a person
was, one date arithmetic, one sweep, and every caller dereferencing it.

WHAT IS PURGED AND WHAT SURVIVES. The sweep removes the COORDINATES (latitude, longitude, accuracy)
and keeps the VERDICT and the DISTANCE. That is the whole point of the design: management's question
is "was this punch at the store, and how far away was it", and both answers survive a purge, so
honouring the retention promise costs the business nothing it actually reviews. A purge therefore
never changes a flag, a report or an export — only the ability to re-derive one from raw position.

NO SECOND VOCABULARY. The coordinate column names are NOT written here. They are dereferenced from
`store_geofence.PUNCH_COLUMNS`, which is already the one home for "which columns hold this record's
fix", so a column renamed there cannot leave a copy behind here holding the old spelling. Event
check-ins reuse the storevisit capture contract verbatim (mig 027 → mig 986, index §23.5), which is
why one entry serves both.

NOT A POLICY OF ITS OWN. The window is each tenant's declared number of days, read from that
relation's own policy column. The house default is a POLICY (a number of days), which is exactly
what a house default is right for — never a tenant's own vocabulary, which it is never allowed to be
(owner directive 2026-10-30).

Pinned by `harness_geo_retention_lock.py`, which fails the build if a coordinate column is spelled
outside the one home, if a relation that stores a fix is missing from the registry, if a writer stops
stamping the commitment, or if the sweep is ever made to touch a verdict.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from app.modules.core import store_geofence as _sgeo

# The floor every tenant's window is clamped up to. A zero or negative "days" is a typo, and a typo
# must not become an instruction to delete today's coordinates before anyone has reviewed them.
MIN_RETENTION_DAYS = 1
# The house default, in days. A POLICY, not a vocabulary.
DEFAULT_RETENTION_DAYS = 180

# The column that carries the commitment on the row itself. Stamped at write time so the promise is
# visible in the data rather than buried in a job somebody has to go and find. Two spellings exist
# because `marketing_event_checkin` shipped first and its column name is already in production data;
# renaming a live column to tidy this would be a migration that buys nothing.
PUNCH_STAMP_COLUMN = "geo_purge_after_date"
CHECKIN_STAMP_COLUMN = "purge_after_date"


def _fix_columns(kind):
    """The (lat, lng, accuracy) column names for one capture kind — READ from the one home."""
    cols = _sgeo.PUNCH_COLUMNS[kind]
    return (cols["lat"], cols["lng"], cols["accuracy"])


def _verdict_columns(kind):
    """The (decision, distance) column names for one capture kind — the part a purge KEEPS."""
    cols = _sgeo.PUNCH_COLUMNS[kind]
    return (cols["decision"], cols["distance"])


# ── THE REGISTRY ─────────────────────────────────────────────────────────────────────────────────
# Every relation in the platform that stores where a person was. A relation absent from here is a
# relation nothing purges, so the lock fails the build on one.
#
#   relation        schema-qualified, as the index names it
#   table           the PostgREST table name the backend reaches it by
#   schema          the PostgREST schema header
#   captures        the capture kinds this relation stores, in `store_geofence.KINDS` order
#   stamp           the column carrying this row's purge-after date
#   policy_table    the relation holding the tenant's declared window
#   policy_column   the column holding it
#   migration       the migration that adds the stamp column (for the honest degrade message)
LOCATION_RELATIONS = (
    {
        "relation": "storeops.timelog",
        "schema": "storeops",
        "table": "timelog",
        "captures": (_sgeo.KIND_CLOCK_IN, _sgeo.KIND_CLOCK_OUT),
        "stamp": PUNCH_STAMP_COLUMN,
        "policy_table": "storeops.tenants",
        "policy_column": "punch_geo_retention_days",
        "migration": "1113",
        "what": "where a shift was started and ended",
    },
    {
        "relation": "storeops.store_visits",
        "schema": "storeops",
        "table": "store_visits",
        "captures": (_sgeo.KIND_STORE_VISIT,),
        "stamp": PUNCH_STAMP_COLUMN,
        "policy_table": "storeops.tenants",
        "policy_column": "punch_geo_retention_days",
        "migration": "1113",
        "what": "where a manager was when they logged a store visit",
    },
    {
        # Reuses the storevisit capture contract verbatim (mig 027 → mig 986, index §23.5), which is
        # why its fix columns come from the SAME entry of the one home.
        "relation": "marketing.marketing_event_checkin",
        "schema": "marketing",
        "table": "marketing_event_checkin",
        "captures": (_sgeo.KIND_STORE_VISIT,),
        "stamp": CHECKIN_STAMP_COLUMN,
        "policy_table": "core.marketing_config",
        "policy_column": "checkin_geo_retention_days",
        "migration": "986",
        "what": "where a rep was when they checked in to an event",
    },
)

RELATIONS_BY_NAME = {r["relation"]: r for r in LOCATION_RELATIONS}


def fix_columns_for(entry):
    """Every coordinate column this relation stores — the columns a purge CLEARS."""
    out = []
    for kind in entry["captures"]:
        out.extend(_fix_columns(kind))
    # dedupe, order preserved: one relation may capture one kind twice under different spellings
    return tuple(dict.fromkeys(out))


def verdict_columns_for(entry):
    """Every verdict column this relation stores — the columns a purge must LEAVE ALONE."""
    out = []
    for kind in entry["captures"]:
        out.extend(_verdict_columns(kind))
    return tuple(dict.fromkeys(out))


def retention_days(value, default=DEFAULT_RETENTION_DAYS):
    """A tenant's declared window as a usable number of days.

    Junk, blank and None fall back to the house default rather than to zero: "we could not read the
    setting" must never resolve to "delete everything". Clamped up to `MIN_RETENTION_DAYS`."""
    try:
        days = int(float(value))
    except (TypeError, ValueError):
        days = int(default)
    if days < MIN_RETENTION_DAYS:
        days = MIN_RETENTION_DAYS
    return days


def purge_after(now=None, days=None, default=DEFAULT_RETENTION_DAYS):
    """THE date on or after which this row's coordinates may be removed, as `YYYY-MM-DD`.

    One date arithmetic for the whole platform. `marketing.event_logic.purge_after_date` delegates
    here rather than keeping its own copy, which is what stops the two windows drifting."""
    n = now or datetime.now(timezone.utc)
    return (n + timedelta(days=retention_days(days, default))).date().isoformat()


def stamp_columns(entry, *, now=None, days=None):
    """The retention columns to write alongside a fix. `{}` for a relation with no stamp column."""
    stamp = entry.get("stamp")
    if not stamp:
        return {}
    return {stamp: purge_after(now=now, days=days)}


def is_due(row, entry, now=None):
    """Is this row past its own retention date? A row with NO stamp is not due.

    An unstamped row is REPORTED (see `retention_summary`), never purged on a guess: it predates the
    stamp, and guessing its age from a neighbouring timestamp would be inventing the commitment that
    the design exists to make explicit."""
    stamp = (row or {}).get(entry.get("stamp") or "")
    if not stamp:
        return False
    today = (now or datetime.now(timezone.utc)).date().isoformat()
    return str(stamp)[:10] <= today


def already_purged(row, entry):
    """True when every coordinate column on this row is already empty — nothing left to clear."""
    return all((row or {}).get(c) in (None, "") for c in fix_columns_for(entry))


def cleared_columns(entry):
    """The update that removes a row's coordinates and NOTHING else.

    The verdict columns are deliberately absent: a purged row still says `outside` and still says how
    many metres, so no flag, report or export changes when the promise is honoured."""
    return {c: None for c in fix_columns_for(entry)}


def retention_summary(rows, entry, now=None):
    """What is due, what is already clear, and what carries no commitment at all.

    `no_retention_stamp` is the honest count that keeps a pre-stamp backlog visible instead of
    letting it look like compliance."""
    rows = list(rows or [])
    due = [r for r in rows if is_due(r, entry, now=now) and not already_purged(r, entry)]
    return {
        "relation": entry["relation"],
        "what": entry.get("what"),
        "rows": len(rows),
        "due_for_purge": len(due),
        "already_clear": sum(1 for r in rows if already_purged(r, entry)),
        "no_retention_stamp": sum(1 for r in rows if not (r or {}).get(entry.get("stamp") or "")),
        "as_of": (now or datetime.now(timezone.utc)).date().isoformat(),
    }


def plan_purge(rows, entry, now=None):
    """The rows to clear and the update to apply to each — pure, so the sweep is provable with no DB.

    Returns `[(row, update), ...]`. A row that is not due, or whose coordinates are already gone, is
    not in the list: the sweep must be idempotent, because a nightly job that rewrites rows it has
    already cleared turns a retention promise into a churn of pointless writes."""
    out = []
    update = cleared_columns(entry)
    for row in list(rows or []):
        if is_due(row, entry, now=now) and not already_purged(row, entry):
            out.append((row, dict(update)))
    return out


# ── THE SWEEP ────────────────────────────────────────────────────────────────────────────────────
# Deliberately a Python sweep over PostgREST rather than a SQL function (the audit prune's shape).
# Two reasons, both about this feature rather than about taste: the coordinate column names live in
# `store_geofence.PUNCH_COLUMNS` and a SQL function would have to re-spell them, which is the second
# copy the whole design exists to prevent; and a pure planner (`plan_purge`, above) is provable with
# no database, which is what the house asks of logic that decides what gets deleted.
PAGE = 1000


def sweep_relation(client_for, entry, *, now=None, page=PAGE, max_pages=50):
    """Clear the coordinates of every row of one relation that is past its retention date.

    `client_for(entry)` hands back a PostgREST client already pointed at `entry["schema"]`, so this
    function performs no credential or schema resolution of its own. Returns a report, never raises:
    a nightly sweep that 500s is a nightly sweep that silently stops honouring the promise.

    IDEMPOTENT by construction — `plan_purge` skips a row whose coordinates are already gone, so a
    second run in the same night writes nothing. BOUNDED by `max_pages`: a huge backlog is cleared
    over several nights and REPORTED as `more_due`, never turned into one unbounded transaction.
    """
    report = {"relation": entry["relation"], "cleared": 0, "scanned": 0,
              "more_due": False, "ok": True}
    stamp = entry["stamp"]
    today = (now or datetime.now(timezone.utc)).date().isoformat()
    fix_cols = fix_columns_for(entry)
    select = ",".join(("id", stamp) + fix_cols)
    # KEYSET pagination on `id`, NOT limit/offset and NOT "stop when a page cleared nothing".
    # Both of those are broken here for the same reason: clearing a row does not remove it from the
    # `stamp <= today` filter, so a plain `.limit(page)` re-reads the rows it has just cleared for
    # ever, and stopping on an empty plan stops after the first page of a long backlog. Asking for
    # ids ABOVE the last one seen advances regardless of what was written.
    last_id = ""
    try:
        client = client_for(entry)
        for _ in range(max_pages):
            rows = (client.table(entry["table"]).select(select)
                    .not_.is_(stamp, "null").lte(stamp, today)
                    .gt("id", last_id).order("id").limit(page).execute().data) or []
            if not rows:
                break
            report["scanned"] += len(rows)
            last_id = str(rows[-1].get("id") or last_id)
            plan = plan_purge(rows, entry, now=now)
            if plan:
                # ONE round trip per page, not one per row. Every due row takes the IDENTICAL update
                # (each coordinate column to NULL), so a per-row loop would buy nothing and would
                # make the cost of honouring the promise scale with the backlog — which is how a
                # retention sweep ends up being switched off. Pinned by
                # `harness_round_trip_ratchet.py` C1, which fails the build on a per-item query.
                ids = [r["id"] for r, _ in plan]
                client.table(entry["table"]).update(plan[0][1]).in_("id", ids).execute()
                report["cleared"] += len(ids)
        else:
            # The cap was reached with a full last page: there is more to do, and a bounded sweep
            # SAYS so rather than leaving a half-honoured promise looking finished.
            report["more_due"] = True
    except Exception as exc:                                   # noqa: BLE001 — reported, never raised
        report["ok"] = False
        report["error"] = f"{exc} (is migration {entry['migration']} applied?)"
    return report


def sweep_all(client_for, *, now=None, page=PAGE, max_pages=50):
    """Every registered location relation, swept. One relation failing never stops the others.

    The report names each relation, so "the promise is being honoured" is a thing somebody can read
    rather than a thing somebody assumes."""
    reports = [sweep_relation(client_for, e, now=now, page=page, max_pages=max_pages)
               for e in LOCATION_RELATIONS]
    return {"ok": all(r["ok"] for r in reports),
            "cleared": sum(r["cleared"] for r in reports),
            "relations": reports,
            "as_of": (now or datetime.now(timezone.utc)).date().isoformat()}
