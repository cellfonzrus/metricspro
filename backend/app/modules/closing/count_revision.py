"""A MANAGEMENT CASH COUNT MAY BE CORRECTED, AND EVERY CORRECTION IS ON THE RECORD.

OWNER ASK 2026-10-10, verbatim: *"also need to edit the cash entered by the management during cash
recon"*.

WHAT WAS ACTUALLY WRONG, MEASURED IN THE CODE BEFORE TOUCHING IT
────────────────────────────────────────────────────────────────
Two separate defects wearing one hat:

  1. **No way in.** The deposit-accountability board offered `📂 open & count` on a SEALED envelope
     only. Once a count existed the cell rendered "N counted · by X" as text, so a typo — a
     transposed figure, a count entered against the wrong envelope — could not be corrected from the
     screen it was entered on.
  2. **And no record if you got in anyway.** The three writers that can set a management cash count
     all UPDATE or UPSERT in place, so a second save REPLACED the first with nothing kept: not the
     previous figure, not who had entered it, not that it had ever been different.

Defect 2 is the one that matters, and it is NOT new — it is migration `935` happening again. That
migration exists because *"the verification row is an UPSERT, so a second DM save overwrote the
previous `dm_*` correction with no history"*. The same sentence is true of a cash count, and a cash
count is worse: when it reads short, `envelope_report.chargeback_parent_row` can turn it into money
off a named rep's pay. A figure that can take money off somebody must not be silently replaceable.

THE CLASS, NOT THE INSTANCE (CLAUDE.md, "A fix is a DESIGN fix or it is not a fix")
──────────────────────────────────────────────────────────────────────────────────
The instance is "the open-and-count figure cannot be edited on the recon board". The class is:

  **A CASH FIGURE A HUMAN TYPED IS A STATEMENT BY THAT HUMAN, AND REPLACING IT WITHOUT KEEPING THE
  OLD ONE DESTROYS EVIDENCE.**

So the fix is not an edit button on one screen. It is ONE append-only trail that EVERY writer of a
management cash count appends to, for every org, every tenant and every one of the three tables —
and a build lock that fails if a fourth writer appears without it. The three writers:

    closing/router._confirm_pickup_impl   the DM's count at pickup        (cash_pickup)
    closing/router._open_count_impl       the late open on the board      (cash_pickup / billpay_pickup)
    closing/router.save_envelope_count    management's count on the receipt (envelope_count)

ONE FACT, ONE HOME, DEREFERENCED. `revision_rows()` below is the only place that decides what a
correction IS and what gets recorded about it. The router has ONE writer
(`_write_count_revisions`) that inserts what this returns, and
`backend/harness_cash_count_revision.py` §D fails the build if a writer of a count column stops
calling it, or if anything else inserts into the table.

WHAT COUNTS AS A CORRECTION. Only a stored value actually CHANGING. Re-saving the same number is not
a correction and writes nothing — a trail that logs no-ops is a trail nobody reads. Comparison is on
rounded cents, so 250 and 250.00 are the same figure; and a first entry (prior absent) is NOT a
correction either, because there was nothing to overwrite. `$0.00 → $0.00` is a no-op, but
`absent → $0.00` is a first entry and `$0.00 → absent` IS a correction: a figure being taken away is
exactly the change somebody would want to see.

NEVER A CHANGE WITHOUT A CHANGER. A revision row carries `changed_by`; where the caller cannot name
one the row still records the change with `changed_by` NULL, which reads as "not recorded" rather
than attributing it to whoever happens to be nearby. The alternative — dropping the revision because
the actor is unknown — loses the evidence to protect the attribution, which is backwards.

💰 MOVES NO MONEY. This module writes visibility records only. It books nothing, pays nothing,
settles nothing, and no payout path reads what it writes. Correcting a count can of course change a
variance that a human later turns into a chargeback — through the EXISTING, human-ticked
`envelope_report` flow, never from here.

RULE TWO: no carrier, tenant, store or product name appears here. The three subject tables are
parameters, not branches.

PURE: no I/O, no framework import, no clock of its own — the caller passes `changed_at`.
Proof: backend/harness_cash_count_revision.py. Registered in docs/SYSTEM_DATA_FLOW_INDEX.md §73.
"""
from __future__ import annotations

#: The one table every correction lands in (mig 1071). Append-only: no writer updates or deletes a
#: revision row, which is what makes it evidence rather than a cache of the latest edit.
REVISION_TABLE = "cash_count_revision"

#: The subject tables a revision can be about. A VALUE, never a branch — adding a fourth count
#: writer means adding its table here and wiring it, and the harness notices if it is not wired.
SUBJECT_CASH_PICKUP = "cash_pickup"
SUBJECT_BILLPAY_PICKUP = "billpay_pickup"
SUBJECT_ENVELOPE_COUNT = "envelope_count"
SUBJECTS = (SUBJECT_CASH_PICKUP, SUBJECT_BILLPAY_PICKUP, SUBJECT_ENVELOPE_COUNT)

#: Which stored fields are a management cash STATEMENT, and therefore evidence. Money first, then the
#: two columns that say who made the statement and when — a correction that silently re-attributes a
#: count to a different person is as much a change as one that moves the figure.
#:
#: `amount_fields` are compared as money (rounded cents); `text_fields` as trimmed strings. The split
#: is declared here so a caller cannot get the comparison wrong for a field.
TRACKED = {
    SUBJECT_CASH_PICKUP: {
        "amount_fields": ("actual_picked_amount", "deposit_amount"),
        "text_fields": ("actual_counted_by", "handed_to"),
    },
    SUBJECT_BILLPAY_PICKUP: {
        "amount_fields": ("actual_picked_amount", "deposit_amount"),
        "text_fields": ("actual_counted_by", "handed_to"),
    },
    SUBJECT_ENVELOPE_COUNT: {
        "amount_fields": ("counted_amount",),
        "text_fields": ("counted_by", "comment"),
    },
}


def _f(v):
    try:
        return round(float(v), 2)
    except (TypeError, ValueError):
        return None


def _s(v):
    return str(v).strip() if v is not None else None


def tracked_fields(subject):
    """PURE: every field a revision can be about, for one subject table. Empty tuple for a subject
    this module has no opinion about — a caller passing an unknown table records nothing rather than
    guessing which of its columns are cash."""
    t = TRACKED.get(str(subject or "").strip())
    if not t:
        return ()
    return tuple(t["amount_fields"]) + tuple(t["text_fields"])


def field_kind(subject, field):
    """PURE: 'amount' | 'text' | None — how a field is compared and rendered."""
    t = TRACKED.get(str(subject or "").strip())
    if not t:
        return None
    if field in t["amount_fields"]:
        return "amount"
    if field in t["text_fields"]:
        return "text"
    return None


def changed(subject, field, prior, new):
    """PURE: did this field's STORED value actually change?

    · A field the write does not touch is not a change (the caller passes only what it wrote).
    · A first entry — prior absent, new present — is NOT a correction: nothing was overwritten.
    · Equal values are not a change, on rounded cents for money and trimmed text otherwise, so
      `250` vs `250.00` and `"Rana"` vs `" Rana "` write nothing.
    · A value being REMOVED (present → absent) IS a change. A figure taken away is precisely what
      somebody reviewing this would want to see, and treating it as a no-op is how an erasure hides.
    """
    kind = field_kind(subject, field)
    if kind is None:
        return False
    if prior is None and new is None:
        return False
    if prior is None:
        return False                     # first entry, not a correction
    if kind == "amount":
        return _f(prior) != _f(new)
    return (_s(prior) or "") != (_s(new) or "")


def revision_rows(org_id, subject, row_id, identity, prior, patch,
                  changed_by=None, changed_at=None, note=None):
    """PURE: one append-only revision row per field whose stored value this write CHANGES.

    `prior`  the stored row as it was read, before the write.
    `patch`  exactly what the writer is about to write (its own update/insert dict) — so a field the
             writer never touches can never be logged as changed, and the trail describes the write
             that actually happened rather than a diff of two full rows.

    Returns `[]` when nothing changed, which is the common case and the reason this is cheap: a
    re-save with the same figures writes nothing at all.

    `identity` is copied, not referenced, and carries the store/day/employee keys so the trail is
    readable without joining back to a row that may itself have moved on.
    """
    ident = dict(identity or {})
    out = []
    for field in tracked_fields(subject):
        if field not in (patch or {}):
            continue
        before, after = (prior or {}).get(field), (patch or {}).get(field)
        if not changed(subject, field, before, after):
            continue
        kind = field_kind(subject, field)
        out.append({
            "org_id": org_id,
            "subject_table": subject,
            "subject_row_id": str(row_id) if row_id is not None else None,
            "store_code": ident.get("store_code"),
            "close_date": ident.get("close_date"),
            "employee_name": ident.get("employee_name"),
            "field": field,
            # Money and text live in separate columns so a figure is never read back out of a string
            # and a reviewer can total the corrections. Both NULL-able: an erasure has no new value.
            "previous_amount": _f(before) if kind == "amount" else None,
            "new_amount": _f(after) if kind == "amount" else None,
            "previous_text": _s(before) if kind == "text" else None,
            "new_text": _s(after) if kind == "text" else None,
            # NEVER A CHANGE WITHOUT A CHANGER — but an unnamed changer never costs us the record.
            "changed_by": _s(changed_by),
            "changed_at": changed_at,
            "note": _s(note),
        })
    return out


def describe(rev, money=None):
    """PURE: one sentence for a revision row, for the board's history popover and the export.

    `money` formats a figure; the default keeps this module free of a currency opinion while still
    producing something readable on its own.
    """
    r = rev or {}
    fmt = money or (lambda v: "—" if v is None else f"${float(v):,.2f}")
    field = r.get("field") or "value"
    if r.get("previous_amount") is not None or r.get("new_amount") is not None:
        was, now = fmt(r.get("previous_amount")), fmt(r.get("new_amount"))
    else:
        was = r.get("previous_text") or "—"
        now = r.get("new_text") or "—"
    who = r.get("changed_by") or "not recorded"
    when = str(r.get("changed_at") or "")[:19].replace("T", " ")
    tail = f" — {r['note']}" if r.get("note") else ""
    return f"{field}: {was} → {now}, changed by {who}{(' on ' + when) if when else ''}{tail}"


def history_by_subject(rows):
    """PURE: revision rows grouped by the row they are about, newest first — the shape a screen needs
    to show "this figure has been corrected" beside the figure itself.

    Sorted on `changed_at` DESCENDING with a stable fallback, so a row whose timestamp is missing
    (an un-migrated clock, a pre-column insert) still appears rather than being dropped from its own
    history.
    """
    out = {}
    for r in rows or []:
        out.setdefault(str((r or {}).get("subject_row_id") or ""), []).append(r)
    for key in out:
        out[key].sort(key=lambda r: (str(r.get("changed_at") or ""), str(r.get("field") or "")),
                      reverse=True)
    return out


def correction_count(rows):
    """PURE: how many corrections a set of revision rows represents, and how much money they moved.

    `amount_moved` is the SUM OF ABSOLUTE deltas, not the net: two corrections of +$200 and −$200 are
    two people's figures being changed, not nothing happening. Text-only corrections count as
    corrections and move no money.
    """
    n, moved = 0, 0.0
    for r in rows or []:
        n += 1
        prev, new = _f((r or {}).get("previous_amount")), _f((r or {}).get("new_amount"))
        if prev is None and new is None:
            continue
        moved = round(moved + abs((new or 0.0) - (prev or 0.0)), 2)
    return {"corrections": n, "amount_moved": moved}
