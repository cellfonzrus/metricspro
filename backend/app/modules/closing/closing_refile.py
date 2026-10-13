"""THE ONE HOME for "a closing was filed against the wrong store — move it, with everything keyed to
it" (index §29.14).

OWNER, 2026-10-13, verbatim: *"also the manager should be able to go in and move the wrong closing
tor trh cioreect store"*. He asked after running a hand-written SQL runbook to move one misfiled
closing (Nostrand → 531 Utica) and watching its safety block abort, correctly, because the day had
meanwhile been re-entered by hand at the destination. Both halves of that experience are the
requirement: a manager must be able to do this from the screen, and the move must REFUSE rather than
create the duplicate it is trying to undo.

THE CLASS, NOT THE INSTANCE (CLAUDE.md, "A fix is a DESIGN fix"). "Rana Bilal's 2026-10-10 closing is
at the wrong store" is the instance. The general fact that was missing is that **a closing's store is
treated as immutable by the platform, while a rep picking the wrong store is an ordinary mistake** —
so the only repair was hand-written SQL, per incident, by whoever happened to be asked. And the store
is not carried by the closing alone: thirteen other tables key rows to `(close_date, store_code)` or
to the closing row itself, and SQL that moves one and forgets the others leaves the store-day
half-moved, which is worse than not moving it.

ONE FACT, ONE HOME, DEREFERENCED. `TABLES` below is the registry of EVERY table in the schema that
carries both `close_date` and `store_code`, each classified by what a re-file must do with it:

  · FOLLOWS — the row is part of this rep's store-day and is re-keyed with it (the cash the DM picked
    up, the bill-pay cash, the expenses paid out of the envelope, the envelope count, the declared
    metric lines, the cash-count revisions). Leaving these behind is what makes a half-move.
  · STAYS   — the row records something that genuinely happened at the ORIGINAL store, or is a feed
    the platform does not own. `closing_attempt` is the audit of what was ATTEMPTED, and he really
    did attempt it at Nostrand (same posture as the store-identity merge, index §64);
    `raw_epay_daily_tx` is a carrier feed keyed to where the transactions physically happened.
  · BLOCKS  — money has already been settled against the store-day downstream (a bank deposit, a
    deposit adjustment, a management review decision). Re-filing underneath a settled figure would
    silently restate it, so the re-file is REFUSED and names what has to be cleared first.
  · REVIEW  — the row is a human's judgement about a set of closings that the move changes
    (`daily_closing_verification` and its audit: a DM verified a store-day, and after the move that
    verification covers a different set of rows). Never silently moved and never deleted — the
    manager is TOLD, in the response, that it has to be re-checked.

`backend/harness_closing_refile.py` re-derives the table list FROM THE MIGRATIONS and FAILS THE BUILD
if any table carrying both columns is not classified here, so a future table cannot quietly join the
schema and be forgotten by the move.

THE DUPLICATE TRAP IS DEREFERENCED, NOT RE-DECIDED. Whether the destination store-day is already
closed is `closing/closing_grain`'s question, asked through `blocks_other_reps` — the same registry
`dedup_key` and `create_row` ask. A re-file that landed on an already-closed store-day would create
exactly the duplicate PR #458 exists to prevent, and it would do it with a manager's permissions.

RULE TWO — config, never code: no tenant, store or carrier name appears here. PURE: no I/O, no
framework import. The caller reads the rows and counts; this module decides.
"""
from __future__ import annotations

from . import closing_grain as _grain

#: The row being moved. Named once so the registry below and the harness agree on what the subject is.
SUBJECT_TABLE = "commcalc.daily_closing"

# ── What a re-file does with a table ────────────────────────────────────────────────────────────
FOLLOWS = "follows"
STAYS = "stays"
BLOCKS = "blocks"
REVIEW = "review"
SUBJECT = "subject"
DISPOSITIONS = (FOLLOWS, STAYS, BLOCKS, REVIEW, SUBJECT)

# ── How a row is matched to the closing being moved ─────────────────────────────────────────────
#: the satellite carries `closing_row_id` — the precise, unambiguous link
MATCH_ROW_ID = "closing_row_id"
#: the satellite carries (org_id, close_date, store_code, employee_name) — one row per rep per
#: store-day, which is how the pickups are keyed
MATCH_STORE_DAY_REP = "store_day_rep"
#: the row is about the store-day as a WHOLE, not one person's envelope — only ever counted here,
#: never re-keyed, which is why BLOCKS and REVIEW rows carry it. Spelled `whole_store_day` and not
#: `store_day` on purpose: `closing/closing_grain` owns the token `store_day` as a GRAIN, and
#: `harness_closing_store_day_grain.py` §H3a fails the build on a second spelling of it. A match
#: strategy and a grain are different facts and must not share a literal by coincidence.
MATCH_STORE_DAY = "whole_store_day"

TABLES = {
    SUBJECT_TABLE: {
        "disposition": SUBJECT, "match": None, "store_columns": ("store_code", "store_address", "store_name"),
        "why": "the closing itself — the row being re-filed",
    },
    # ── FOLLOWS: part of this rep's store-day ───────────────────────────────────────────────────
    "commcalc.cash_pickup": {
        "disposition": FOLLOWS, "match": MATCH_STORE_DAY_REP, "store_columns": ("store_code", "store_name"),
        "destination_unique": True,
        "why": "the store cash the DM collected for this rep's store-day; UNIQUE (org, close_date, "
               "store_code, employee_name), so a pickup already recorded at the destination blocks",
    },
    "commcalc.billpay_pickup": {
        "disposition": FOLLOWS, "match": MATCH_STORE_DAY_REP, "store_columns": ("store_code", "store_name"),
        "destination_unique": True,
        "why": "the bill-pay cash collected for this rep's store-day; same unique key as cash_pickup",
    },
    "commcalc.closing_expense": {
        "disposition": FOLLOWS, "match": MATCH_ROW_ID, "store_columns": ("store_code",),
        "why": "an expense paid out of THIS envelope — it belongs to the store the envelope belongs to",
    },
    "commcalc.closing_metric_line": {
        "disposition": FOLLOWS, "match": MATCH_ROW_ID, "store_columns": ("store_code",),
        "destination_unique": True,
        "why": "the declared counts submitted WITH this closing; UNIQUE (org, store_code, close_date, "
               "metric_key, employee_name)",
    },
    "commcalc.envelope_count": {
        "disposition": FOLLOWS, "match": MATCH_ROW_ID, "store_columns": ("store_code",),
        "why": "management's count of THIS envelope (keyed UNIQUE on closing_row_id, so it cannot clash)",
    },
    "commcalc.envelope_withdrawal": {
        "disposition": FOLLOWS, "match": MATCH_ROW_ID, "store_columns": ("store_code",),
        "why": "cash drawn down from THIS envelope",
    },
    "commcalc.cash_count_revision": {
        "disposition": FOLLOWS, "match": MATCH_STORE_DAY_REP, "store_columns": ("store_code",),
        "why": "the append-only audit of every amount corrected on this rep's store-day (the pickups "
               "and the envelope count — see closing/count_revision.SUBJECTS); it follows the rows it "
               "describes, and it is keyed by identity rather than by closing_row_id, which it has not got",
    },
    # ── STAYS: a true record of the original store, or a feed we do not own ──────────────────────
    "commcalc.closing_attempt": {
        "disposition": STAYS, "match": None, "store_columns": (),
        "why": "the audit of what was ATTEMPTED. The rep really did try to close at the original "
               "store, and re-keying it would erase the only evidence of the mistake being repaired",
    },
    "commcalc.raw_epay_daily_tx": {
        "disposition": STAYS, "match": None, "store_columns": (),
        "why": "a carrier bill-pay feed, keyed to where the transactions physically happened; the "
               "platform does not own this row and must never rewrite it",
    },
    # ── BLOCKS: money already settled against the store-day ─────────────────────────────────────
    "commcalc.bank_deposit": {
        "disposition": BLOCKS, "match": MATCH_STORE_DAY, "store_columns": (),
        "why": "a bank deposit has been recorded against this store-day; moving a closing out from "
               "under a banked figure would restate it with nothing saying so",
    },
    "commcalc.closing_deposit_adjustment": {
        "disposition": BLOCKS, "match": MATCH_STORE_DAY, "store_columns": (),
        "why": "a deposit reconciliation adjustment is booked against this store-day",
    },
    "commcalc.closing_mgmt_review": {
        "disposition": BLOCKS, "match": MATCH_STORE_DAY, "store_columns": (),
        "why": "management has already recorded a review decision for this store-day",
    },
    # ── REVIEW: a human judgement the move invalidates — reported, never moved, never deleted ───
    "commcalc.daily_closing_verification": {
        "disposition": REVIEW, "match": MATCH_STORE_DAY, "store_columns": (),
        "why": "a DM verified this store-day against the closings it then held. After the move it "
               "covers a different set, so a human has to re-check it",
    },
    "commcalc.daily_closing_verification_audit": {
        "disposition": REVIEW, "match": MATCH_STORE_DAY, "store_columns": (),
        "why": "the audit trail of that verification — kept verbatim, flagged for re-check with it",
    },
}

#: Tables deliberately absent from the live schema scan because they do not carry BOTH columns, listed
#: so the harness's negative controls have something to assert against rather than a silent omission.
NOT_STORE_DAY_KEYED = ("commcalc.pos_tender_summary", "core.crm_lead")


def classify(table) -> str:
    """The disposition of a table, or '' when it is not registered. An UNREGISTERED table is not
    'safe to ignore' — it is a build failure in the harness, because nobody decided about it."""
    return (TABLES.get(str(table or "").strip().lower()) or {}).get("disposition", "")


def tables_with(disposition) -> tuple:
    return tuple(sorted(t for t, spec in TABLES.items() if spec["disposition"] == disposition))


def unregistered(tables) -> tuple:
    """Which of `tables` this registry has no ruling for. The harness feeds it the list it derives
    from the migrations, so a new (close_date, store_code) table fails the build until classified."""
    return tuple(sorted(t for t in tables if not classify(t)))


def store_patch(destination) -> dict:
    """The store identity columns a FOLLOWS row takes from the destination. Only the columns that
    table actually has are written by the caller (see `TABLES[...]["store_columns"]`)."""
    return {
        "store_code": (destination.get("store_code") or "").strip(),
        "store_address": destination.get("store_address") or None,
        "store_name": destination.get("store_name") or destination.get("store_address") or None,
        "sfid": (destination.get("sfid") or "").strip() or None,
    }


def _destination_blocked_by(subject, destination_closings, grain) -> dict:
    """Whether the destination store-day is already closed in a way that forbids the move. Asks
    `closing_grain`, never re-decides: at store-day grain ANY other closing blocks; at rep-day grain
    only one by the SAME rep does. A row released by management does not block — releasing is exactly
    how a manager says "this one is being corrected"."""
    emp = (subject.get("employee_name") or "").strip().lower()
    store_day = _grain.blocks_other_reps(grain)
    for r in destination_closings or []:
        if str(r.get("id") or "") == str(subject.get("id") or ""):
            continue
        if r.get("released_at"):
            continue
        if store_day or (r.get("employee_name") or "").strip().lower() == emp:
            return r
    return {}


def plan(*, subject, destination, grain=None, destination_closings=(), satellites=None) -> dict:
    """THE decision. Pure: every fact comes in, nothing is read.

    `subject`              the daily_closing row being moved (id, store_code, close_date, employee_name)
    `destination`          {store_code, store_address?, store_name?, sfid?} — the store it belongs to
    `grain`                the destination store's closing grain (`closing_grain.resolve`)
    `destination_closings` the closings already standing at destination store + close_date
    `satellites`           {table: {"count": n, "destination_conflicts": n}} for the registered tables

    Returns {"ok", "refusals", "moves", "review", "stays", "closing_patch"}. `ok` false means NOTHING
    may be written — the caller raises with the refusals and changes nothing, which is the behaviour
    the hand-written runbook's abort had and the screen must keep.
    """
    grain = grain if grain is not None else _grain.HOUSE_DEFAULT
    sats = satellites or {}
    refusals, moves, review, stays = [], [], [], []

    dest_code = (destination or {}).get("store_code") or ""
    dest_code = str(dest_code).strip()
    from_code = str(subject.get("store_code") or "").strip()
    day = str(subject.get("close_date") or "")

    if not subject.get("id"):
        refusals.append({"code": "no_subject", "message": "That closing could not be found."})
    if not dest_code:
        refusals.append({"code": "no_destination",
                         "message": "Pick the store this closing belongs to."})
    elif dest_code.strip().lower() == from_code.strip().lower():
        refusals.append({"code": "same_store",
                         "message": f"This closing is already filed against {from_code}."})

    holder = _destination_blocked_by(subject, destination_closings, grain) if dest_code else {}
    if holder:
        who = (holder.get("employee_name") or "someone").strip() or "someone"
        refusals.append({"code": "destination_already_closed",
                         "message": f"{dest_code} already has a closing for {day}, submitted by "
                                    f"{who}. Moving this one there would make a duplicate. Release "
                                    f"or remove that closing first, then move this one."})

    # A table we could not READ is never assumed empty. "No rows came back" and "the query failed"
    # are different facts, and treating the second as the first is how a move lands underneath a
    # settled deposit. An unreadable FOLLOWS table is also a refusal: a half-move is worse than none.
    for table, got in sorted(sats.items()):
        if (got or {}).get("unreadable") and classify(table) in (BLOCKS, REVIEW, FOLLOWS):
            refusals.append({"code": "satellite_unreadable", "table": table,
                             "message": f"{table} could not be read, so this move cannot be proved "
                                        f"safe. Nothing has been changed."})

    for table in tables_with(BLOCKS):
        n = int((sats.get(table) or {}).get("count") or 0)
        if n:
            refusals.append({"code": "settled_downstream", "table": table, "count": n,
                             "message": f"{n} row(s) in {table} are already settled against "
                                        f"{from_code} / {day} — {TABLES[table]['why']}. Clear that "
                                        f"first; nothing has been changed."})

    for table in tables_with(FOLLOWS):
        spec, got = TABLES[table], (sats.get(table) or {})
        n, clash = int(got.get("count") or 0), int(got.get("destination_conflicts") or 0)
        if clash and spec.get("destination_unique"):
            refusals.append({"code": "destination_row_exists", "table": table, "count": clash,
                             "message": f"{dest_code} / {day} already has {clash} row(s) in {table} "
                                        f"for this person, and that table takes one per store-day per "
                                        f"person. Nothing has been changed."})
        elif n:
            moves.append({"table": table, "match": spec["match"], "count": n,
                          "store_columns": list(spec["store_columns"])})

    for table in tables_with(REVIEW):
        n = int((sats.get(table) or {}).get("count") or 0)
        if n:
            review.append({"table": table, "count": n,
                           "message": f"{n} row(s) in {table} were recorded for {from_code} / {day} "
                                      f"and are LEFT AS THEY ARE — {TABLES[table]['why']}."})

    for table in tables_with(STAYS):
        stays.append({"table": table, "why": TABLES[table]["why"]})

    ok = not refusals
    patch = {}
    if ok:
        sp = store_patch(destination)
        patch = {"store_code": sp["store_code"], "store_address": sp["store_address"],
                 "store_name": sp["store_name"]}
        if sp["sfid"]:
            patch["sfid"] = sp["sfid"]
    return {"ok": ok, "refusals": refusals, "moves": moves, "review": review, "stays": stays,
            "closing_patch": patch, "from_store_code": from_code, "to_store_code": dest_code,
            "close_date": day, "grain": _grain.normalize(grain)}
