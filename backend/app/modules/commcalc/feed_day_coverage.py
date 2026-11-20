"""ONE home for *"which store-days of a per-store feed do we have, and which are still to fetch?"*

Index §COMMCALC-82. Lock: `backend/harness_feed_day_coverage.py`.

WHY THIS EXISTS. The processor back office is fronted by a security appliance that refuses automated
clients from every address tried (§COMMCALC-74/75), and the owner confirmed it offers no scheduled
delivery of its own. So the file arrives because a PERSON fetched it, one login per store. That is
survivable for 29 stores only if the screen says what is still outstanding, which is a question
nothing answered: the feed knew what had landed, and nobody could ask what had not.

THE DISTINCTION THIS MODULE EXISTS TO KEEP. Three states, not two:

  have     — rows landed for that store and day;
  missing  — the feed is readable and holds nothing for that store and day, so a person must fetch it;
  unknown  — the feed could not be READ, so nothing can be said about any day.

Collapsing the third into the second is the defect worth naming: an unreadable feed would render as
"every store-day missing" and send someone to download 29 files a day that are already landed. "I
could not look" and "there is nothing there" send an operator to do opposite things, so they are
never the same answer here. Same class as §PAYABLES-02's `can_date` (row count is not measurement)
and as `portal_backfill._rows` returning None rather than [] (no memory ⇒ no backfill).

NO TENANT, CARRIER, PORTAL OR STORE NAME APPEARS HERE (RULE TWO). The caller passes the stores its
own org declared and the days it wants; a store this module was not given is never a cell, which is
what makes one tenant's landed days unable to fill another's grid.
"""
from datetime import date, timedelta

#: The widest grid a screen is ever handed. A person reads a grid, and a year of columns is not a
#: grid — the catch-up is worked in pages, so the range is clamped rather than refused.
MAX_DAYS = 62

#: The window a screen opens on when the operator has named none. A WINDOW is a policy, which is the
#: one thing a house default is right for — it is nobody's vocabulary, so no tenant's words hide in it.
DEFAULT_DAYS = 14

#: A day with rows but no LINES is still a day nobody fetched: the feed holds a placeholder, not a
#: file. Spelled once, here, so no surface invents its own threshold.
MIN_LINES_FOR_HAVE = 1

HAVE, MISSING, UNKNOWN = "have", "missing", "unknown"

_UNREADABLE_NOTE = ("The settlement feed could not be read, so this grid cannot say which days are "
                    "outstanding. Nothing is reported as missing on a read that did not happen.")
_NO_STORES_NOTE = ("No store has declared a back-office login yet, so there is nothing to fetch. "
                   "Declare one login per store and its store code; the grid fills from them.")


def _day(value):
    """A 'YYYY-MM-DD' string, or None when the value is not a date. Never raises."""
    if isinstance(value, date):
        return value.isoformat()
    text = str(value or "").strip()[:10]
    if len(text) != 10:
        return None
    try:
        return date.fromisoformat(text).isoformat()
    except ValueError:
        return None


def days_in_range(date_from, date_to):
    """The inclusive RUN of days from `date_from` to `date_to`, oldest first, clamped to MAX_DAYS.

    PURE. An unreadable or backwards range is an empty run rather than a raise or a guess: a grid
    drawn over days nobody asked for would be read as an answer about them.

    Named `days_in_range` and not `days_between` on purpose: three modules already answer "HOW MANY
    days between two dates" with an int (`ma_handset_cogs`, `device_history`, `portout_flags`), which
    is a different question. Two functions with one name is how a caller reaches for the wrong one.
    """
    a, b = _day(date_from), _day(date_to)
    if not (a and b) or a > b:
        return ()
    start, end = date.fromisoformat(a), date.fromisoformat(b)
    span = min((end - start).days + 1, MAX_DAYS)
    return tuple((start + timedelta(days=i)).isoformat() for i in range(span))


def default_range(today=None):
    """The ('YYYY-MM-DD', 'YYYY-MM-DD') window a screen opens on, ending TODAY, DEFAULT_DAYS wide.

    PURE. A caller that names its own range never reaches here; this exists so a screen opening for
    the first time shows a readable fortnight rather than either nothing or a year of columns.
    """
    end = today if isinstance(today, date) else date.today()
    return ((end - timedelta(days=DEFAULT_DAYS - 1)).isoformat(), end.isoformat())


def _cell(landed, store, day):
    """One store-day, from the landed map. `landed` is None for "could not read"."""
    if landed is None:
        return {"day": day, "state": UNKNOWN}
    got = landed.get((store, day)) or {}
    try:
        lines = int(got.get("lines") or 0)
    except (TypeError, ValueError):
        lines = 0
    if lines < MIN_LINES_FOR_HAVE:
        # Deliberately carries NO figures: a missing day has no money, and printing 0.00 beside it
        # would read as a measured zero rather than as a file nobody has fetched.
        return {"day": day, "state": MISSING}
    return {"day": day, "state": HAVE, "lines": lines,
            "payment": got.get("payment"), "fee": got.get("fee")}


def grid(stores, days, landed):
    """The store-by-day grid a person works from, and the counts beside it.

    PURE. `stores` are the store codes the caller's own org declared, `days` a run from
    `days_in_range`, and `landed` the feed's own {(store, day): {payment, fee, lines}} map — or None
    when the feed could not be read, which is reported as `measured: False` with every cell UNKNOWN.

    `undeclared_stores` names store codes the feed holds rows for that nobody declared, because a
    file uploaded under a mis-keyed store must be visible rather than dropped.
    """
    stores = tuple(s for s in (stores or ()) if str(s or "").strip())
    days = tuple(d for d in (days or ()) if _day(d))
    measured = landed is not None
    rows, have, missing = [], 0, 0
    for store in stores:
        cells = [_cell(landed, store, d) for d in days]
        s_have = sum(1 for c in cells if c["state"] == HAVE)
        s_missing = sum(1 for c in cells if c["state"] == MISSING)
        have += s_have
        missing += s_missing
        rows.append({"store": store, "days": cells, "have": s_have, "missing": s_missing,
                     "complete": bool(days) and measured and s_missing == 0})
    declared = set(stores)
    undeclared = sorted({s for (s, _d) in (landed or {}) if s and s not in declared})
    note = ""
    if not measured:
        note = _UNREADABLE_NOTE
    elif not stores:
        note = _NO_STORES_NOTE
    return {"stores": rows, "days": list(days), "have": have, "missing": missing,
            "measured": measured, "note": note, "undeclared_stores": undeclared}


def coverage(client, org_id, stores, date_from, date_to):
    """The grid for one org over one range, read through the feed's OWN per-store-day home.

    The read is `epay_ingest.per_store_day`, which is also the portal leg of the fee reconciliation —
    this module adds no second query over the settlement table, so the grid cannot disagree with the
    recon about what landed. A read that fails answers None there, which becomes `measured: False`
    here rather than a grid full of missing days.
    """
    from app.modules.commcalc import epay_ingest as _epay
    days = days_in_range(date_from, date_to)
    codes = tuple(s for s in (stores or ()) if str(s or "").strip())
    if not days:
        return grid(codes, (), {})
    landed = _epay.per_store_day(client, org_id, days[0], days[-1], store_codes=codes or None)
    return grid(codes, days, landed)
