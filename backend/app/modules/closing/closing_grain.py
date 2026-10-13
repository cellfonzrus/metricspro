"""THE ONE HOME for the fact "how many daily closings does one store-day take".

OWNER BUG REPORT (2026-10-13), verbatim: *"2 reps did the closing for Oct 10 for nostrand ave, for
luxelink, by rana Bilal and Moza Ali, one the system should not have taken duplicate entries - it
should have flagged the second person that they cannot enter"*.

MEASURED LIVE BEFORE WRITING A LINE (read-only, Supabase REST):
  · `Nostrand` / 2026-10-10 holds THREE accepted closings — Rana Bilal $230 (23:03), moza ali $140
    (23:04) and a third, Dixit Chauhan $0, submitted 2026-10-13 16:35. The store-day's declared cash
    reads $370 against a POS day of $140.
  · Every other Nostrand store-day from 2026-09-05 to 2026-10-12 holds exactly ONE closing. The
    tenant's real convention is one closing per store-day; 2026-10-10 is the exception.
  · House-wide, 54 store-days carry more than one closing. The ones with two DIFFERENT reps — the
    class this module closes — number 16 and arrive at a steady 3–4 a month across BOTH tenants
    (B-2612 07-02, B-11634 07-04, B-418 07-08, B-196 07-28, B-3565 07-29, B-196 08-05, B-103 08-14,
    Lefferts 08-19, B-3565 08-24, kedzie 09-09, B-3565 09-16, B-652 09-19, Lefferts 09-22, B-196
    09-25, Cicero 10-04, Nostrand 10-10). The rest are one rep repeating, which mig 502 already
    stopped.

THE CLASS, NOT THE INSTANCE (CLAUDE.md, "A fix is a DESIGN fix"). "Nostrand took two closings" is the
instance. The general fact that was wrong is that **the platform had exactly one answer to "is this
store-day already closed?", hardcoded as "only if this same REP already closed it"** — the dedup key
`(org, store, employee, day)` of `closing/dedup_key`, the database index built from it, and the
pre-insert guard in `router.create_row` all carried the rep in the key. So a second person closing
the same store-day was not a duplicate to ANY of them, for any store, any rep, any tenant: the money
doubles into `closing_summary`, the cash recon, the envelope report and the deposit chain, and
nobody is told.

It is also why submitting at the WRONG store goes unnoticed. The close gate matches a rep against the
POS day for the store they picked; Rana Bilal had no POS activity at Nostrand on 2026-10-10 (he had
$459.29 at `Utica`, where no closing was submitted at all), so the gate had nothing to compare and
let him through as recon-pending. The grain below stops the second row; the missing-rep signal is
reported separately rather than guessed at here.

ONE FACT, ONE HOME, DEREFERENCED — NEVER COPIED. The grain lives here and nowhere else:
  · `closing/dedup_key.for_row(..., grain=...)` composes the key at this grain, so the EXISTING
    partial unique index `daily_closing_one_active_per_rep_day` (mig 502, widened by mig 1037)
    enforces it with no second index and no new column — the duplicate mechanism is extended, not
    duplicated (CLAUDE.md duplicate-check gate);
  · `closing/router.create_row` asks `blocks_other_reps` whether another rep's row stands in the way;
  · `closing/router._ingest_dataframe` (the closing-sheet upload — the SECOND writer of
    `daily_closing`, which set no dedup key at all and could insert the same shape by file) asks the
    same question before inserting;
  · the derive sweep (`closing_source.derive_row` via `router._derive_closing_day`) is EXCUSED by
    construction and the excuse is locked: it is already store-day grained, writes at most one row
    per store-day, and never overwrites a human's row (`kept_manual`).
`backend/harness_closing_store_day_grain.py` FAILS THE BUILD if any of those callers stops
dereferencing this module, or if a second copy of the vocabulary appears.

RULE TWO — config, never code. No tenant, carrier or store name appears here. The grain is a per-org
config row with a per-store override and a house default, read exactly the way
`closing/closing_source` is read. The house default is ONE CLOSING PER STORE-DAY: that is the owner's
ruling, and the live measurement above says every tenant already works that way in practice. A
tenant that genuinely runs an envelope per rep per day switches a config row — it does not get code.

PURE: no I/O, no framework import. The caller reads the config rows; this module decides.
"""
from __future__ import annotations

# ── The vocabulary. Two values, and nothing may invent a third (`normalize` folds anything else to
#    the house default rather than letting an unknown string decide whether a store-day is closed).
GRAIN_STORE_DAY = "store_day"
GRAIN_REP_DAY = "rep_day"
GRAINS = (GRAIN_STORE_DAY, GRAIN_REP_DAY)

#: The house default — ONE closing per store-day (owner 2026-10-13). Deliberately the value a tenant
#: with no config row, and a tenant whose migration has not run yet, resolves to: the protection is
#: live without waiting on a schema change, which is the whole point of the report.
HOUSE_DEFAULT = GRAIN_STORE_DAY

LABELS = {
    GRAIN_STORE_DAY: "One closing for the whole store-day",
    GRAIN_REP_DAY: "One closing per rep per day",
}

#: What each grain means in a sentence, for the setup screen. Says what it COSTS, not just what it is.
HELP = {
    GRAIN_STORE_DAY: ("Whoever closes the store first owns the day. A second person is turned away "
                      "and told to ask a manager, so one day's cash can never be declared twice."),
    GRAIN_REP_DAY: ("Every rep who worked declares their own envelope, so one store-day can hold "
                    "several closings. Only pick this if each rep really banks their own cash."),
}


def normalize(value) -> str:
    """Any stored / posted value → one of `GRAINS`. Unknown, blank or None → `HOUSE_DEFAULT`."""
    v = str(value or "").strip().lower()
    return v if v in GRAINS else HOUSE_DEFAULT


def resolve(cfg_rows, store_code=None) -> str:
    """THE RESOLVER. `cfg_rows` are the org's `commcalc.closing_grain_config` rows (the caller does
    the read); the row with a blank `store_code` is the org default, a row matching `store_code`
    (case-insensitively) is the per-store override.

    Precedence: per-store override → org default → `HOUSE_DEFAULT`. A table or rows that do not exist
    yet give `[]` here, so a tenant whose migration has not run resolves to one-per-store-day.
    """
    rows = list(cfg_rows or [])
    out = HOUSE_DEFAULT
    for r in rows:
        if not str((r or {}).get("store_code") or "").strip():
            out = normalize((r or {}).get("grain"))
            break
    code = str(store_code or "").strip()
    if code:
        for r in rows:
            if str((r or {}).get("store_code") or "").strip().upper() == code.upper():
                out = normalize((r or {}).get("grain"))
                break
    return out


def grain_map(cfg_rows, store_codes) -> dict:
    """`{store_code: grain}` for every code given — the batch form of `resolve`, so a sheet upload
    covering many stores resolves from ONE config read instead of one per row."""
    return {c: resolve(cfg_rows, c) for c in (store_codes or []) if str(c or "").strip()}


def blocks_other_reps(grain) -> bool:
    """True when ANOTHER rep's closing for this store-day stands in the way of this submit — the
    question the submit endpoint and the sheet upload are each really asking. False means the old
    behaviour exactly: only this rep's own row can block them."""
    return normalize(grain) == GRAIN_STORE_DAY


def key_omits_employee(grain) -> bool:
    """Whether the dedup key for this grain leaves the employee out. `closing/dedup_key.for_row`
    dereferences this rather than re-deciding it, so the key and the pre-insert guard can never
    disagree about what a duplicate is — the exact divergence CLAUDE.md's "one fact, one home" rule
    exists to stop, and the one mig 1037 already had to repair once between Python and SQL."""
    return normalize(grain) == GRAIN_STORE_DAY


def refusal_detail(store_code, close_date, other_name) -> str:
    """The audited circumstance of a store-day refusal: which store, which day, whose closing holds
    it. Recorded on `commcalc.closing_attempt`, never used as the submitter-facing sentence (those
    words live in `closing/submit_refusal.REFUSALS`, once)."""
    return (f"store {str(store_code or '').strip()[:40]!r} on {close_date} is already closed by "
            f"{str(other_name or 'another rep').strip()[:60]!r}")


def refusal_suffix(other_name) -> str:
    """Appended to the declared refusal sentence so the second rep knows WHO holds the day — being
    turned away without being told by whom is what sends them to resubmit instead of to a manager.
    Empty when the holding row carries no name (a derived or nameless row), so the sentence still
    reads correctly."""
    name = str(other_name or "").strip()
    return f"It was submitted by {name}." if name else ""
