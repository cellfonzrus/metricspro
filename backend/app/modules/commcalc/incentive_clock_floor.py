"""THE DAY A STORE STARTED CLOCKING IN — derived, STORED, then read. Never recomputed for money.

Owner Sanjot, 2026-11-27 14:35 ET, verbatim: *"let the daily cincentive as itonly affects ny
logistics bee ffeceive going forward based on eh acual clock in, if te hours are udpated manuay then
we cannot oay for thsose hours"*.

WHAT THIS IS FOR. `commcalc/router._incentive_hours_for_day` weighted an hours-split
`threshold_pool` by `actual_hours` else `scheduled_hours` — in practice the SCHEDULE, since
`shifts.actual_hours` carries a figure on 7 of 7,740 live rows (index §14y). Moving it to closed
punches changes **who gets paid what** out of a fixed pool, so it may only apply GOING FORWARD.
This module is the boundary that makes "going forward" a fact rather than a hope.

★ WHY DERIVED, AND WHY NOT A LITERAL ★ — "the day they started clocking in" is a fact in each
tenant's own punch history. A hardcoded date, or one tenant's date standing in for another's, is the
per-tenant code RULE TWO forbids and the exact class of the device-line default (§COMMCALC-49.10). A
store with no closed punch has **NO floor** and is reported unmeasurable; it is never handed another
store's date and never silently equal-split.

★ WHY DERIVED → *STORED* → READ, AND NEVER DERIVED AT READ TIME ★ — this is the load-bearing part.
`derive` answers from the punch history, so a punch BACKFILLED into an earlier day moves its answer.
If the money read the derivation, that backfill would walk the floor backwards and restate a period
already paid — the one thing the owner's "going forward" forbids. So `derive` only ever **PROPOSES**;
a human accepts; `in_effect` reads the **stored** row and recomputes nothing. The proof pins exactly
this (`test_a_stored_floor_is_read_and_a_later_backfilled_punch_cannot_walk_it_backwards`).

★ NO STORED FLOOR MEANS THE RULE IS NOT IN EFFECT ★ — so deploying this code moves no money at all
until a floor is accepted per store. The stored row IS the switch. This is the opposite default from
`payroll_hours_basis.clocked_basis_effective_from`, where a NULL floor means "the declared mode
applies with no floor"; here an absent floor means "keep today's behaviour". Stated out loud because
the two columns look alike and mean opposite things when empty.

PER STORE, NOT PER ORG. Stores began clocking at different times and will continue to — a tenant
rolling out the clock store by store is the normal case, not the edge. An org-wide floor would
either hold back the stores that are ready or restate the ones that are not.

ITS OWN TABLE, NOT A COLUMN ON `payroll_hours_basis_config`. That table is the per-ORG home for the
PAY basis knobs (§14y/§14z, one row one feature); this is a per-STORE boundary for an INCENTIVE, a
different question with a different grain. Sharing the pay table's row would also hand this rule the
date an org chose for payroll, which is the mistake `corrections_effective_from` already has its own
column to avoid.

PURE except `load`. No arithmetic about money lives here — only the boundary.
"""

#: The table the floor is stored in. ONE name, dereferenced by every reader (index §CORE-13 style),
#: so a rename cannot leave a second spelling behind.
FLOOR_TABLE = "incentive_clock_floor"

#: Why a (store, day) is not yet weighted by punches. Words, so a surface never composes its own.
NOT_IN_EFFECT_NO_FLOOR = "no_floor_accepted"
NOT_IN_EFFECT_BEFORE_FLOOR = "before_floor"
IN_EFFECT = "in_effect"

REASONS = {
    IN_EFFECT:
        "This store's incentive pool is split by closed clock punches from its accepted start date.",
    NOT_IN_EFFECT_NO_FLOOR:
        "No clock-in start date has been accepted for this store, so its incentive pool is split "
        "exactly as it is today. Nothing changes until a start date is accepted.",
    NOT_IN_EFFECT_BEFORE_FLOOR:
        "This day falls before the date this store started clocking in, so it is split exactly as "
        "it is today — a period already paid is never restated.",
}


def _day(v):
    s = str(v or "")[:10]
    return s if len(s) == 10 and s[4] == "-" and s[7] == "-" else ""


def derive(punch_rows):
    """PURE: `{(org_id, store_code): 'YYYY-MM-DD'}` — the first CLOSED punch per store.

    A **PROPOSAL ONLY.** Nothing that decides money may read this; see the module note. `punch_rows`
    are `storeops.timelog` rows carrying at least `org_id`, `store_code`, `work_date`, `clock_out`
    and `hours`.

    A punch with no `clock_out`, or no `hours`, measured nothing and therefore cannot be the day a
    store started clocking in — it is skipped rather than counted, which is `punch_usability`'s
    CLOSED test stated at this grain (the org's auto-clockout declaration is not consulted: an
    auto-closed punch still evidences that the clock was in use that day, which is the only thing a
    floor is about).

    A store with no closed punch is **ABSENT from the result**, never present with None: an absent
    key is unmeasurable and the caller must say so, while a None would invite `or <some date>`.
    """
    out = {}
    for p in (punch_rows or ()):
        if not isinstance(p, dict):
            continue
        if not p.get("clock_out") or p.get("hours") is None:
            continue
        day = _day(p.get("work_date"))
        store = str(p.get("store_code") or "").strip()
        org = p.get("org_id")
        if not day or not store or not org:
            continue
        key = (org, store)
        if key not in out or day < out[key]:
            out[key] = day
    return out


def in_effect(floor_row, day):
    """PURE: is punch-only incentive weighting in effect for this store on this business date?

    `floor_row` is the STORED row (or None / {} when none has been accepted). The boundary is
    **INCLUSIVE** of `effective_from` — the owner said *"effective going forward"* from the day they
    started clocking in, so that day itself counts.

    False for an undated `day`: an undated row keeps the answer it has rather than being pushed to
    one side of a boundary it never named, the same posture `payroll_hours_basis.day_is_before_floor`
    takes.
    """
    # A WRONG-SHAPED ROW IS NOT A FLOOR. Caught by this module's own negative control (G4): a
    # caller that passed the date string instead of the row raised AttributeError here, which would
    # have 500'd the incentive endpoint rather than leaving the split as it is. Anything that is not
    # a mapping carries no accepted date, so it reads as "not in effect" — the safe direction, and
    # the same `isinstance(..., dict) else {}` posture `payroll_evidence` takes with its result.
    floor = _day((floor_row if isinstance(floor_row, dict) else {}).get("effective_from"))
    d = _day(day)
    if not floor or not d:
        return False
    return d >= floor


def status(floor_row, day):
    """PURE: `(in_effect, reason)` — the verdict WITH its word, so no caller can show one without
    the other. The same discipline `payroll_hours_basis.resolve_day` applies to hours and basis."""
    if not _day((floor_row if isinstance(floor_row, dict) else {}).get("effective_from")):
        return False, NOT_IN_EFFECT_NO_FLOOR
    if not in_effect(floor_row, day):
        return False, NOT_IN_EFFECT_BEFORE_FLOOR
    return True, IN_EFFECT


def reason_sentence(reason):
    """PURE: the reader-facing sentence for a verdict word."""
    return REASONS.get(reason, f"This incentive floor verdict ({reason!r}) has no ruling in "
                               f"incentive_clock_floor — it is treated as NOT in effect.")


def load(client, org_id):
    """`{store_code: row}` of the ACCEPTED floors for this org, org-scoped.

    NEVER RAISES, and an unreadable table is **not** an empty one in meaning — but it IS in effect:
    a failed read returns `{}`, which `status` reads as `no_floor_accepted`, i.e. today's behaviour.
    That is the safe direction here (the money does not move), and it is why the caller carries the
    read failure out to the surface rather than this function hiding it. A database that has not run
    the migration behaves exactly as it does today, which is the whole point of the table being the
    switch.

    Returns `({store_code: row}, blocker_or_None)` — the blocker shape `payroll_evidence` uses, so a
    caller already carrying one list of blockers can append to it.
    """
    try:
        rows = (client.schema("commcalc").table(FLOOR_TABLE)
                .select("store_code,effective_from,derived_from,accepted_by,accepted_at")
                .eq("org_id", org_id).execute().data) or []
    except Exception as e:                       # noqa: BLE001 — reported, never raised
        return {}, {"table": f"commcalc.{FLOOR_TABLE}", "reason": str(e)}
    out = {}
    for r in rows:
        code = str((r or {}).get("store_code") or "").strip()
        if code:
            out[code] = r
    return out, None
