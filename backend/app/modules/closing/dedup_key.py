"""THE dedup key of a daily closing — one home, dereferenced by both callers (index §29.11).

A closing is unique per (org, store, employee, day). That fact was spelled TWICE: once as an f-string
in `closing/router.create_row`, once as a SQL expression in migration 502 (the backfill + the partial
unique index it feeds). The two had already drifted — Python folded a STRIPPED name
(`employee_name.strip().lower()`), SQL folded the raw one (`lower(coalesce(employee_name,''))`), so a
name stored with a stray space produced two different keys for one person and the database index could
not see the duplicate it exists to stop. Two copies of one fact, diverging exactly as the house rule
says they will.

ONE HOME: `for_row()` below is the key. `SQL_EXPR` is the same formula as a SQL expression, and the
migration that builds the index MUST contain it verbatim — `harness_closing_submit_refusal.py` reads
both and FAILS THE BUILD if the router stops dereferencing `for_row`, or if a migration spells a
dedup-key formula that is not `SQL_EXPR`.

PURE: no I/O.
"""

from . import closing_grain as _grain

#: The canonical SQL spelling of `for_row` AT REP-DAY GRAIN, as it must appear in any migration that
#: computes the key. No migration computes the store-day spelling: migration 1080 deliberately
#: recomputes NOTHING, because rewriting a stored key at the new grain would collide on the 16
#: store-days that historically hold two reps.
#: Column-qualified with the alias `d` (the shape migration 502's UPDATE already uses).
SQL_EXPR = ("d.org_id::text || '|' || btrim(coalesce(d.store_code,'')) || '|' || "
            "lower(btrim(coalesce(d.employee_name,''))) || '|' || d.close_date::text")


def _fold_store(v) -> str:
    """The store half: whitespace-trimmed, case PRESERVED. A store_code is an identifier the platform
    assigns ('B-117', 'Utica'); two codes differing only in case are two codes, and collapsing them
    here would let one store's closing block another's."""
    return str(v or "").strip()


def _fold_employee(v) -> str:
    """The employee half: whitespace-trimmed AND case-folded, because the name is typed by a person
    and 'rohit' / 'Rohit' is one rep. Deliberately not a nickname matcher — 'Rohit' and
    'Rohit Kumar' stay different people here; converging rep identity is a roster question, not a
    dedup-key question, and guessing it here would silently refuse a real second rep's closing."""
    return str(v or "").strip().lower()


def for_row(org_id, store_code, employee_name, close_date, grain=None) -> str:
    """THE dedup key. Returns '' when the key cannot identify a closing (no store or no employee
    name) — the caller must treat an empty key as "not dedupable" rather than storing it, because a
    stored empty-ish key would make two unrelated identity-less closings collide.

    `grain` is the tenant's declared answer to "how many closings does one store-day take"
    (`closing/closing_grain`, owner 2026-10-13). At the house default — ONE per store-day — the key
    leaves the EMPLOYEE OUT, so the partial unique index mig 502 built on this column (widened by
    mig 1037) refuses a second rep's row for the same store-day with no new index and no new column.
    The grain is DEREFERENCED, never re-decided here, so the key and the router's pre-insert guard
    cannot disagree about what a duplicate is.

    The two spellings can never collide: a store-day key has three `|` segments, a rep-day key four,
    and an employee name cannot contain `|` in a way that closes that gap (the date is always the
    last segment). Existing rows keep whatever key they were written with — this function changes
    what NEW submits are keyed by, and no migration recomputes stored keys (the 16 historical
    two-rep store-days would collide if one did, and mig 1037's §3 guard would correctly abort).
    """
    store, emp = _fold_store(store_code), _fold_employee(employee_name)
    if not store or not emp:
        return ""
    if _grain.key_omits_employee(grain if grain is not None else _grain.HOUSE_DEFAULT):
        return f"{org_id}|{store}|{close_date}"
    return f"{org_id}|{store}|{emp}|{close_date}"


def dedupable(store_code, employee_name) -> bool:
    """Whether a submit carries enough identity to be deduped at all. A closing with no store or no
    employee name cannot be: that is a REFUSAL case (`closing/submit_refusal`), not a row to let
    through unprotected — which is what the old `if store and emp:` guard did, writing the row with
    no dedup_key and no database protection at all."""
    return bool(_fold_store(store_code) and _fold_employee(employee_name))
