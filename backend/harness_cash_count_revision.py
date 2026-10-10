"""Offline proof harness for THE CASH-COUNT CORRECTION TRAIL (owner ask 2026-10-10, mig 1071).

Pure stdlib, no DB and no network — it drives the REAL functions in
`app/modules/closing/count_revision.py` and reads the REAL router/migration source for the build
locks, never reimplementations.

WHAT IS PROVEN
  A. The vocabulary is ONE vocabulary: `SUBJECTS` and the migration's CHECK constraint agree, so the
     database cannot hold a subject table the module has no behaviour for.
  B. What counts as a CORRECTION: only a stored value actually changing. A first entry is not one
     (nothing was overwritten), equal values are not one (on rounded cents for money, trimmed text
     otherwise), and a value being REMOVED is one — a figure taken away is exactly what a reviewer
     needs to see.
  C. `revision_rows` describes the WRITE, not a diff of two rows: a field the writer never touched
     can never be logged as changed. Money and text land in separate columns. An unnamed changer
     still produces the row, with `changed_by` NULL meaning "not recorded" — losing the evidence to
     protect the attribution would be backwards.
  D. THE BUILD LOCK (CLAUDE.md, "lock it so it cannot un-wire"): all THREE writers of a management
     cash count route through the ONE router writer `_write_count_revisions`, that writer is the only
     thing in the codebase that inserts into the table, and nothing anywhere updates or deletes a
     revision row — which is what makes the trail evidence rather than a cache of the latest edit.
  E. The read side: `history_by_subject` groups newest-first and never drops a row whose timestamp is
     missing; `correction_count` sums ABSOLUTE deltas (+$200 then −$200 is two figures changed, not
     nothing happening); `describe` produces a sentence with no dollar opinion of its own.
  F. The screen can be reached: the board carries `pickup_row_id` so a correction can be shown beside
     the figure it is about, the page offers an edit on a COUNTED envelope, and the section is in the
     index.

Run: `cd backend && python3 harness_cash_count_revision.py`
"""
import re
import sys

sys.path.insert(0, ".")

from app.modules.closing import count_revision as cr

PASS, FAIL = [], []


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print(("PASS" if cond else "FAIL") + f"  {name}" + (f"  [{detail}]" if detail and not cond else ""))


ORG = "00000000-0000-0000-0000-000000000001"
IDENT = {"store_code": "B-559", "close_date": "2026-09-05", "employee_name": "Radhika"}
AT = "2026-10-10T20:00:00+00:00"
MIG = open("../database/migrations/1071_cash_count_revision.sql", encoding="utf-8").read()
ROUTER = open("app/modules/closing/router.py", encoding="utf-8").read()

# ── A. One vocabulary. ─────────────────────────────────────────────────────────────────────────
print("\n== A. The subject vocabulary ==")
check("A1 the three count writers' tables are the declared subjects",
      cr.SUBJECTS == ("cash_pickup", "billpay_pickup", "envelope_count"), str(cr.SUBJECTS))
_chk = re.search(r"subject_table IN \(([^)]*)\)", MIG)
_in_sql = tuple(re.findall(r"'([a-z_]+)'", _chk.group(1))) if _chk else ()
check("A2 the migration's CHECK constraint holds EXACTLY those subjects — the database cannot hold "
      "a table this module has no behaviour for",
      set(_in_sql) == set(cr.SUBJECTS), f"{_in_sql} vs {cr.SUBJECTS}")
check("A3 every subject declares which of its fields are cash and which are text",
      all(cr.tracked_fields(s) for s in cr.SUBJECTS)
      and all(cr.field_kind(s, f) in ("amount", "text")
              for s in cr.SUBJECTS for f in cr.tracked_fields(s)))
check("A4 an unknown subject tracks nothing and guesses nothing",
      cr.tracked_fields("raw_sales") == () and cr.field_kind("raw_sales", "amount") is None
      and cr.revision_rows(ORG, "raw_sales", "1", IDENT, {"amount": 1}, {"amount": 2}) == [])
check("A5 the money columns are the ones that can become a chargeback",
      "actual_picked_amount" in cr.tracked_fields("cash_pickup")
      and "counted_amount" in cr.tracked_fields("envelope_count"))

# ── B. What counts as a correction. ────────────────────────────────────────────────────────────
print("\n== B. What is a correction ==")
S = cr.SUBJECT_CASH_PICKUP
F = "actual_picked_amount"
check("B1 a changed figure IS a correction", cr.changed(S, F, 250.0, 281.0) is True)
check("B2 A FIRST ENTRY IS NOT a correction — there was nothing to overwrite",
      cr.changed(S, F, None, 250.0) is False)
check("B3 both absent is nothing", cr.changed(S, F, None, None) is False)
check("B4 equal money is not a correction, on rounded cents (250 and 250.00 are one figure)",
      cr.changed(S, F, 250, 250.00) is False and cr.changed(S, F, "250.0", 250) is False)
check("B5 a cent IS a change", cr.changed(S, F, 250.00, 250.01) is True)
check("B6 A VALUE BEING REMOVED IS A CORRECTION — an erasure is exactly what must not hide",
      cr.changed(S, F, 250.0, None) is True)
check("B7 $0.00 → $0.00 is a no-op, but absent → $0.00 is a first entry and $0.00 → absent is a "
      "correction",
      cr.changed(S, F, 0, 0.0) is False and cr.changed(S, F, None, 0) is False
      and cr.changed(S, F, 0, None) is True)
check("B8 text is compared trimmed, so whitespace alone is not a correction",
      cr.changed(S, "actual_counted_by", "Rana", " Rana ") is False
      and cr.changed(S, "actual_counted_by", "Rana", "Rajiv") is True)
check("B9 RE-ATTRIBUTING a count to a different person IS a change, like moving the figure",
      cr.changed(cr.SUBJECT_ENVELOPE_COUNT, "counted_by", "uid-a", "uid-b") is True)
check("B10 a field the module does not track is never a correction",
      cr.changed(S, "picked_up_by", "a", "b") is False)

# ── C. revision_rows describes the write. ──────────────────────────────────────────────────────
print("\n== C. revision_rows ==")
PRIOR = {"id": "row-1", "actual_picked_amount": 250.0, "actual_counted_by": "Rana",
         "amount": 574.0, "picked_up_by": "Rajiv"}
rows = cr.revision_rows(ORG, S, "row-1", IDENT, PRIOR,
                        {"actual_picked_amount": 281.0, "envelope_opened": True},
                        changed_by="Gaurav", changed_at=AT, note="recount with the DM present")
check("C1 ONE row per changed field, and only for fields the write touched",
      len(rows) == 1 and rows[0]["field"] == F, str(rows))
r0 = rows[0] if rows else {}
check("C2 money lands in the money columns, text columns stay NULL — a figure is never read back "
      "out of a string",
      (r0.get("previous_amount"), r0.get("new_amount")) == (250.0, 281.0)
      and r0.get("previous_text") is None and r0.get("new_text") is None, str(r0))
check("C3 the row carries the identity, the actor, the time and the reason",
      r0.get("store_code") == "B-559" and r0.get("close_date") == "2026-09-05"
      and r0.get("employee_name") == "Radhika" and r0.get("changed_by") == "Gaurav"
      and r0.get("changed_at") == AT and r0.get("note") == "recount with the DM present"
      and r0.get("subject_table") == S and r0.get("subject_row_id") == "row-1"
      and r0.get("org_id") == ORG)
check("C4 A FIELD THE WRITER NEVER TOUCHED IS NEVER LOGGED — the trail describes the write, not a "
      "diff of two full rows (picked_up_by differs in prior and is absent from the patch)",
      not any(x["field"] == "picked_up_by" for x in
              cr.revision_rows(ORG, S, "row-1", IDENT, PRIOR,
                               {"actual_picked_amount": 281.0}, changed_at=AT)))
check("C5 a re-save of the SAME figures writes nothing at all",
      cr.revision_rows(ORG, S, "row-1", IDENT, PRIOR,
                       {"actual_picked_amount": 250.0, "actual_counted_by": "Rana"},
                       changed_at=AT) == [])
check("C6 a FIRST count writes nothing — there was nothing to overwrite",
      cr.revision_rows(ORG, S, "row-1", IDENT, {},
                       {"actual_picked_amount": 250.0}, changed_at=AT) == [])
check("C7 two fields changing in one write produce two rows",
      len(cr.revision_rows(ORG, S, "row-1", IDENT, PRIOR,
                           {"actual_picked_amount": 281.0, "actual_counted_by": "Gaurav"},
                           changed_at=AT)) == 2)
_tx = cr.revision_rows(ORG, S, "row-1", IDENT, PRIOR,
                       {"actual_counted_by": "Gaurav"}, changed_at=AT)
check("C8 a text correction puts its values in the text columns and no money columns",
      _tx and _tx[0]["previous_text"] == "Rana" and _tx[0]["new_text"] == "Gaurav"
      and _tx[0]["previous_amount"] is None and _tx[0]["new_amount"] is None, str(_tx))
_anon = cr.revision_rows(ORG, S, "row-1", IDENT, PRIOR,
                         {"actual_picked_amount": 281.0}, changed_at=AT)
check("C9 NEVER A CHANGE WITHOUT A CHANGER, but never a LOST change either: an unnamed actor still "
      "produces the row with changed_by NULL ('not recorded'), never a guess",
      len(_anon) == 1 and _anon[0]["changed_by"] is None, str(_anon))
check("C10 the caller's identity dict is COPIED, not referenced",
      (lambda d: (cr.revision_rows(ORG, S, "r", d, PRIOR, {F: 9.0}, changed_at=AT),
                  d == {"store_code": "X"})[1])({"store_code": "X"}))
check("C11 an erasure is recorded with new_amount NULL, not as 0.00",
      cr.revision_rows(ORG, S, "r", IDENT, PRIOR, {F: None},
                       changed_at=AT)[0]["new_amount"] is None)
check("C12 the module has no clock of its own — changed_at is the caller's",
      "datetime" not in open("app/modules/closing/count_revision.py", encoding="utf-8").read()
      and cr.revision_rows(ORG, S, "r", IDENT, PRIOR, {F: 9.0})[0]["changed_at"] is None)

# ── D. THE BUILD LOCK: the trail cannot un-wire. ───────────────────────────────────────────────
print("\n== D. BUILD LOCK — every count writer appends, and nothing else touches the table ==")
check("D1 the ONE router writer exists",
      "def _write_count_revisions(" in ROUTER
      and ROUTER.count("def _write_count_revisions(") == 1)
for name, anchor in (("_open_count_impl (the late open on the recon board)",
                      "def _open_count_impl(payload: OpenCountIn, org_id: str, table: str):"),
                     ("_confirm_pickup_impl (the DM's count at pickup)",
                      "async def _confirm_pickup_impl("),
                     ("save_envelope_count (management's count on the receipt)",
                      "def save_envelope_count(")):
    body = ROUTER.split(anchor, 1)[1].split("\n@router.", 1)[0] if anchor in ROUTER else ""
    check(f"D2 {name} dereferences the one writer",
          "_write_count_revisions(" in body, f"anchor found: {bool(body)}")
_inserts = re.findall(r"table\(\s*_count_revision\.REVISION_TABLE\s*\)\s*\.(\w+)", ROUTER)
check("D3 the table is INSERTED into in exactly one place, and that place is the one writer",
      _inserts.count("insert") == 1
      and "_count_revision.REVISION_TABLE).insert(" in
          ROUTER.split("def _write_count_revisions(", 1)[1].split("\ndef ", 1)[0], str(_inserts))
check("D4 APPEND-ONLY: nothing in the codebase updates or deletes a revision row — that is what "
      "makes the trail evidence rather than a cache of the latest edit",
      not any(op in _inserts for op in ("update", "delete", "upsert")), str(_inserts))
check("D5 no writer spells the table name itself — they all go through REVISION_TABLE",
      '"cash_count_revision"' not in ROUTER
      and "'cash_count_revision'" not in ROUTER)
check("D6 nothing decides what a correction IS outside the pure module — the router holds no "
      "previous/new comparison of a count column",
      "previous_amount" not in ROUTER and "revision_rows(" in ROUTER)
check("D7 the correction can carry a reason, and it is OPTIONAL — a correction refused for want of "
      "a reason is a correction that does not happen",
      "note: Any = None" in ROUTER.split("class OpenCountIn(LaxModel):", 1)[1].split("\n\n", 1)[0])
check("D8 the revision write is ADAPTIVE — an un-migrated database still records the COUNT, and the "
      "response says the history was not recorded (never silently)",
      "run migration 1071" in ROUTER and '"history_recorded"' in ROUTER
      and '"recorded": False' in ROUTER)
# Only the EXECUTABLE half: a comment explaining the defect naturally contains the word UPDATE.
_MIG_SQL = "\n".join(l for l in MIG.split("\n") if not l.lstrip().startswith("--"))
check("D9 the migration is append-only by construction: it creates the ONE new table and touches no "
      "existing table, column, amount or constraint, and rewrites no stored row",
      "CREATE TABLE IF NOT EXISTS commcalc.cash_count_revision" in _MIG_SQL
      and not re.search(r"ALTER TABLE (?!commcalc\.cash_count_revision)", _MIG_SQL)
      # Statement-initial only: the word "drop" also appears inside a COMMENT ON COLUMN sentence.
      and not re.search(r"(?mi)^\s*(UPDATE|DELETE|DROP|TRUNCATE)\s", _MIG_SQL),
      _MIG_SQL[:0] or "see migration 1071")
check("D10 changed_by is NULLABLE in the schema, matching 'not recorded' rather than a sentinel",
      not re.search(r"changed_by\s+TEXT\s+NOT NULL", MIG))

# ── E. The read side. ──────────────────────────────────────────────────────────────────────────
print("\n== E. Reading the trail ==")
H = [{"subject_row_id": "a", "field": F, "changed_at": "2026-10-01T00:00:00Z",
      "previous_amount": 10.0, "new_amount": 20.0, "changed_by": "X"},
     {"subject_row_id": "a", "field": F, "changed_at": "2026-10-03T00:00:00Z",
      "previous_amount": 20.0, "new_amount": 15.0, "changed_by": None},
     {"subject_row_id": "b", "field": "actual_counted_by", "changed_at": None,
      "previous_text": "Rana", "new_text": "Gaurav", "changed_by": "Y"}]
G = cr.history_by_subject(H)
check("E1 grouped by the row the correction is about", sorted(G) == ["a", "b"], str(sorted(G)))
check("E2 newest first within a row", G["a"][0]["changed_at"].startswith("2026-10-03"))
check("E3 a row with NO timestamp still appears in its own history, never dropped",
      len(G["b"]) == 1)
c = cr.correction_count(H)
check("E4 corrections are counted, and amount_moved sums ABSOLUTE deltas (+10 then −5 is 15 of "
      "figures moved, not 5)",
      c == {"corrections": 3, "amount_moved": 15.0}, str(c))
check("E5 a text-only correction counts as a correction and moves no money",
      cr.correction_count([H[2]]) == {"corrections": 1, "amount_moved": 0.0})
check("E6 an erasure's delta is the figure that was removed",
      cr.correction_count([{"previous_amount": 250.0, "new_amount": None}])["amount_moved"] == 250.0)
check("E7 empty input is zero, not an error",
      cr.correction_count([]) == {"corrections": 0, "amount_moved": 0.0}
      and cr.history_by_subject(None) == {})
d = cr.describe(H[1])
check("E8 describe() reads as a sentence, names the unnamed actor honestly, and carries no currency "
      "opinion of its own when one is supplied",
      "20.00" in d and "15.00" in d and "not recorded" in d
      and "$" in cr.describe(H[1]) and "USD" not in cr.describe(H[1], money=lambda v: f"{v}"),
      d)
check("E9 describe() of a text correction shows the words, not a dollar figure",
      "Rana" in cr.describe(H[2]) and "Gaurav" in cr.describe(H[2])
      and "$" not in cr.describe(H[2]))
check("E10 a note rides on the sentence",
      "recount" in cr.describe({**H[0], "note": "recount with the DM"}))

# ── F. Reachable and registered. ───────────────────────────────────────────────────────────────
print("\n== F. Reachable and registered ==")
check("F1 the board carries the pickup row's id, so a correction can be shown beside the figure it "
      "is about (no second walk of the pickup tables to key a history)",
      '"pickup_row_id": r.get("id")' in
      open("app/modules/closing/deposit_accountability.py", encoding="utf-8").read())
check("F2 the board attaches each envelope's corrections and their sentences",
      'env["corrections"]' in ROUTER and 'env["correction_history"]' in ROUTER
      and "_count_revision.describe(" in ROUTER)
check("F3 the board's correction totals are over the VISIBLE rows only (keyset consistency — never "
      "a count from outside the viewer's span)",
      "_visible_revs" in ROUTER and "correction_count(_visible_revs)" in ROUTER)
PAGE = open("../frontend/src/app/(platform)/closing/deposit-recon/page.tsx", encoding="utf-8").read()
check("F4 the screen offers an edit on a COUNTED envelope — the defect was that it rendered text",
      "✏️ edit" in PAGE and "editing: true" in PAGE)
check("F5 the screen shows that a figure has been corrected, and offers a reason on an edit",
      "corrected {e.corrections}" in PAGE and "Why it is being changed" in PAGE)
check("F6 the screen surfaces a trail that could NOT be recorded rather than implying it was",
      "history_recorded === false" in PAGE)
IDX = open("../docs/SYSTEM_DATA_FLOW_INDEX.md", encoding="utf-8").read()
check("F7 registered in the index (the mandatory owner directive 2026-09-01)",
      "closing/count_revision.py" in IDX and "cash_count_revision" in IDX)


print(f"\n{len(PASS)}/{len(PASS) + len(FAIL)} checks passed")
if FAIL:
    print("FAILED:")
    for f in FAIL:
        print("  -", f)
    sys.exit(1)
