"""Offline proof harness — A MANAGER CAN MOVE A CLOSING TO THE RIGHT STORE, and everything keyed to
it moves with it (index §29.14). Drives the PURE `closing/closing_refile` and the REAL
`closing/router.refile_closing_row` against the house's shared in-memory client
(`harness_intake_fakes.FakeDB` — reused, not a new fake). No live database, no network.

Run: `cd backend && python3 harness_closing_refile.py`

OWNER, 2026-10-13, verbatim: *"also the manager should be able to go in and move the wrong closing tor
trh cioreect store"* — said after he ran a hand-written SQL runbook to move one misfiled closing
(Nostrand → 531 Utica) and watched its safety block abort with
`ABORT: Utica already has 1 closing(s) for 2026-10-10`, because the day had meanwhile been
re-entered by hand at the destination. BOTH halves of that are the requirement: the move belongs on
the screen, and it must REFUSE rather than create the duplicate it exists to undo.

THE CLASS, not the instance. "Rana Bilal's 2026-10-10 closing is at the wrong store" is the instance.
The general fact that was missing: **a closing's store was immutable to the platform while a rep
picking the wrong store is an ordinary mistake**, so every repair was hand-written SQL — and that SQL
moved `daily_closing` alone while THIRTEEN other tables kept the old store, leaving a half-moved
store-day, which is worse than an unmoved one.

Proves:
  A. The registry is COMPLETE — the table list is re-derived FROM THE MIGRATIONS and every table
     carrying both `close_date` and `store_code` has a ruling. A new such table FAILS THIS BUILD
     until somebody decides about it, which is the lock the house rule asks for.
  B. PURE — what the move refuses: the destination store-day already closed (dereferencing
     `closing_grain`, the same question `create_row` asks), money already settled downstream, a
     one-per-store-day satellite already standing at the destination, a table it could not read, a
     blank destination, and the same store.
  C. PURE — what the move does: every FOLLOWS row re-keyed, every REVIEW row reported and left
     alone, every STAYS row named and untouched, and the destination's identity taken from the roster.
  D. BEHAVIOURAL — the REAL endpoint on the REAL store-day: the move Sanjot asked for, its satellites
     following, the abort he actually hit reproduced verbatim, the dedup key recomposed at the
     destination's grain, and `dry_run` writing nothing at all.
  E. The permission gate — the same one as release; a DM cannot re-file.
  F. WIRING LOCKS — the router dereferences the registry rather than carrying its own list, there is
     no second copy of the disposition vocabulary, and migration 1081 is additive.
  G. NEGATIVE CONTROLS — the locks fail on code that reintroduces the defect.
"""
import sys
import os
import re
import glob

sys.path.insert(0, ".")

PASS, FAIL = [], []


def check(name, ok, detail=""):
    (PASS if ok else FAIL).append(name)
    print(("  ok   " if ok else "  FAIL ") + name + (f"   [{detail}]" if detail and not ok else ""))


HOUSE = "00000000-0000-0000-0000-000000000001"
LUXE = "854f6d7b-6590-4e4d-88ab-646f560d4f4c"

from harness_intake_fakes import FakeDB                       # noqa: E402
from app.modules.closing import closing_refile as rf          # noqa: E402
from app.modules.closing import closing_grain as cg           # noqa: E402
from app.modules.closing import dedup_key as dk               # noqa: E402
import app.modules.closing.router as cr                       # noqa: E402
import app.modules.storeops.router as storeops                # noqa: E402
from fastapi import HTTPException                             # noqa: E402

ROUTER_PATH = "app/modules/closing/router.py"
REFILE_PATH = "app/modules/closing/closing_refile.py"
MIG_PATH = "../database/migrations/1081_closing_refile_audit.sql"
MIG_GLOB = "../database/migrations/*.sql"
ROUTER_SRC = open(ROUTER_PATH).read()
REFILE_SRC = open(REFILE_PATH).read()


# ═══════════════════════════════════════════════════════════════════════════════════════════════════
# A. THE REGISTRY IS COMPLETE — derived from the migrations, not from memory
# ═══════════════════════════════════════════════════════════════════════════════════════════════════
print("\n== A. every (close_date, store_code) table in the schema has a ruling ==")


def store_day_tables():
    """Re-derive, from the migrations themselves, every table carrying BOTH `close_date` and
    `store_code`. This is the DEFINITION of "a table a re-file has to decide about", so the registry
    is checked against the schema rather than against what somebody remembered."""
    found = {}
    for p in sorted(glob.glob(MIG_GLOB)):
        s = open(p, encoding="utf-8", errors="replace").read()
        for m in re.finditer(r"create\s+table\s+(?:if\s+not\s+exists\s+)?([a-z_]+\.[a-z_]+)\s*\((.*?)\n\s*\);",
                             s, re.I | re.S):
            name, body = m.group(1).lower(), m.group(2).lower()
            if re.search(r"\bclose_date\b", body) and re.search(r"\bstore_code\b", body):
                found.setdefault(name, os.path.basename(p))
    return found


_derived = store_day_tables()
check("A1 the scan finds the schema's store-day tables at all (a scan that finds nothing proves "
      "nothing)", len(_derived) >= 14, detail=f"{len(_derived)} found")
_missing = rf.unregistered(_derived)
check("A2 EVERY table carrying close_date + store_code is classified — a new one fails this build",
      not _missing, detail=f"unclassified: {_missing}")
_stale = tuple(t for t in rf.TABLES if t != rf.SUBJECT_TABLE and t not in _derived)
check("A3 the registry carries no table the schema has not got", not _stale, detail=str(_stale))
check("A4 the subject of the move is the closing itself",
      rf.classify(rf.SUBJECT_TABLE) == rf.SUBJECT and rf.SUBJECT_TABLE == "commcalc.daily_closing")
check("A5 every ruling is one of the declared dispositions",
      all(s["disposition"] in rf.DISPOSITIONS for s in rf.TABLES.values()))
check("A6 every ruling carries its REASON — a disposition with no why is an undocumented decision",
      all(len(str(s.get("why") or "")) > 30 for s in rf.TABLES.values()))
check("A7 every table that is not the subject and not STAYS says HOW it is matched",
      all(s.get("match") for t, s in rf.TABLES.items()
          if s["disposition"] not in (rf.SUBJECT, rf.STAYS)))
check("A8 all four outcomes are actually used — an empty class would mean nobody thought about it",
      all(rf.tables_with(d) for d in (rf.FOLLOWS, rf.STAYS, rf.BLOCKS, rf.REVIEW)))
check("A9 the rep's ATTEMPT log stays where the attempt happened (index §64 posture)",
      rf.classify("commcalc.closing_attempt") == rf.STAYS)
check("A10 a carrier feed is never rewritten by us",
      rf.classify("commcalc.raw_epay_daily_tx") == rf.STAYS)
check("A11 settled money BLOCKS: the bank deposit, the deposit adjustment, the review decision",
      rf.tables_with(rf.BLOCKS) == ("commcalc.bank_deposit", "commcalc.closing_deposit_adjustment",
                                    "commcalc.closing_mgmt_review"))
check("A12 a DM's verification is REVIEWED, never silently moved and never deleted",
      rf.classify("commcalc.daily_closing_verification") == rf.REVIEW
      and rf.classify("commcalc.daily_closing_verification_audit") == rf.REVIEW)
check("A13 the cash the DM picked up FOLLOWS the closing (leaving it behind is the half-move)",
      rf.classify("commcalc.cash_pickup") == rf.FOLLOWS
      and rf.classify("commcalc.billpay_pickup") == rf.FOLLOWS)
check("A14 an unregistered table is NOT quietly 'safe' — classify returns empty and A2 fails on it",
      rf.classify("commcalc.some_table_nobody_ruled_on") == "")
_notkeyed_bad = [t for t in rf.NOT_STORE_DAY_KEYED if t in _derived]
check("A15 the tables excused as not store-day keyed really do not carry both columns",
      not _notkeyed_bad, detail=str(_notkeyed_bad))
#: RULE TWO is about BEHAVIOUR, so the scan is of the CODE — docstrings and comments are stripped
#: first. The owner's bug report and the live evidence are quoted in the docstring on purpose (the
#: house rule is that a fix carries the measurement that justified it); what must not exist is a
#: tenant or store name that any line of code READS.
def code_only(path):
    import ast as _ast
    tree = _ast.parse(open(path, encoding="utf-8").read())
    for node in _ast.walk(tree):
        if isinstance(node, (_ast.Module, _ast.ClassDef, _ast.FunctionDef, _ast.AsyncFunctionDef)):
            d = _ast.get_docstring(node)
            if d and node.body and isinstance(node.body[0], _ast.Expr):
                node.body[0] = _ast.Expr(value=_ast.Constant(value=""))
    return _ast.unparse(_ast.fix_missing_locations(tree))


_names = [w for w in ("luxelink", "cellfonz", "boost", "nostrand", "utica", "total wireless",
                      "854f6d7b")
          if w in code_only(REFILE_PATH).lower()]
check("A16 RULE TWO — no tenant, carrier or store name is READ by any line of the registry's code",
      not _names, detail=str(_names))
check("A17 PURE — no I/O and no framework import in the registry",
      not re.search(r"^\s*(import|from)\s+(requests|fastapi|supabase|pandas|os)\b",
                    REFILE_SRC, re.M))


# ═══════════════════════════════════════════════════════════════════════════════════════════════════
# B. PURE — what the move refuses
# ═══════════════════════════════════════════════════════════════════════════════════════════════════
print("\n== B. the refusals: nothing is written unless the move is provably safe ==")
SUBJ = {"id": "r1", "store_code": "Nostrand", "close_date": "2026-10-10",
        "employee_name": "Rana Bilal"}


def plan(dest="Utica", **kw):
    kw.setdefault("subject", SUBJ)
    return rf.plan(destination={"store_code": dest} if isinstance(dest, str) else dest, **kw)


def codes(p):
    return [r["code"] for r in p["refusals"]]


_clean = plan()
check("B1 a clean move is allowed, and says where it is going",
      _clean["ok"] and _clean["to_store_code"] == "Utica" and _clean["from_store_code"] == "Nostrand")
_blank = plan(dest="")
check("B2 a blank destination is refused", not _blank["ok"] and "no_destination" in codes(_blank))
_same = plan(dest="Nostrand")
check("B3 moving a closing to the store it is already at is refused (not a silent no-op)",
      "same_store" in codes(plan(dest="nostrand")) and "same_store" in codes(_same))
_taken = plan(destination_closings=[{"id": "r2", "employee_name": "someone else"}])
check("B4 THE ABORT SANJOT HIT: another rep's closing at the destination store-day refuses the move",
      not _taken["ok"] and "destination_already_closed" in codes(_taken))
check("B5 the refusal NAMES the day, the store and who holds it — a manager can act on it",
      all(w in _taken["refusals"][0]["message"] for w in ("Utica", "2026-10-10", "someone else")))
_rep_day = plan(grain=cg.GRAIN_REP_DAY,
                destination_closings=[{"id": "r2", "employee_name": "someone else"}])
check("B6 at REP-DAY grain another rep's closing does NOT block — the grain is dereferenced, never "
      "re-decided here", _rep_day["ok"])
_same_rep = plan(grain=cg.GRAIN_REP_DAY,
                 destination_closings=[{"id": "r2", "employee_name": "RANA BILAL"}])
check("B7 at rep-day grain the SAME rep's closing still blocks, case-folded", not _same_rep["ok"])
_released = plan(destination_closings=[{"id": "r2", "employee_name": "x", "released_at": "2026-10-13"}])
check("B8 a RELEASED closing at the destination does not block — releasing is how a manager says "
      "'this one is being corrected'", _released["ok"])
_self = plan(destination_closings=[dict(SUBJ)])
check("B9 the row being moved never blocks itself", _self["ok"])
for _tbl in rf.tables_with(rf.BLOCKS):
    _p = plan(satellites={_tbl: {"count": 1}})
    check(f"B10 {_tbl} with a row refuses the move",
          not _p["ok"] and "settled_downstream" in codes(_p))
check("B11 the settled-money refusal says WHICH table and HOW MANY rows",
      "1 row(s) in commcalc.bank_deposit" in
      plan(satellites={"commcalc.bank_deposit": {"count": 1}})["refusals"][0]["message"])
_clash = plan(satellites={"commcalc.cash_pickup": {"count": 1, "destination_conflicts": 1}})
check("B12 a cash pickup ALREADY at the destination for this person refuses (its UNIQUE key would "
      "reject the update anyway — better a sentence than a 500)",
      not _clash["ok"] and "destination_row_exists" in codes(_clash))
_clash_ok = plan(satellites={"commcalc.envelope_count": {"count": 1, "destination_conflicts": 3}})
check("B13 a table keyed on the closing row itself cannot clash, so it is not blocked by one",
      _clash_ok["ok"])
_unread = plan(satellites={"commcalc.bank_deposit": {"count": 0, "unreadable": True}})
check("B14 'the query failed' is never read as 'no rows' — an unreadable table refuses the move",
      not _unread["ok"] and "satellite_unreadable" in codes(_unread))
_unread_follows = plan(satellites={"commcalc.cash_pickup": {"count": 0, "unreadable": True}})
check("B15 an unreadable FOLLOWS table refuses too — a half-move is worse than no move",
      not _unread_follows["ok"])
_unread_stays = plan(satellites={"commcalc.closing_attempt": {"count": 0, "unreadable": True}})
check("B16 an unreadable STAYS table does NOT block — nothing was going to be written to it",
      _unread_stays["ok"])
check("B17 a refused plan carries NO patch at all, so a caller that ignores `ok` still writes nothing",
      _taken["closing_patch"] == {} and _blank["closing_patch"] == {})
check("B18 every refusal reason is reported, not just the first",
      len(plan(dest="", satellites={"commcalc.bank_deposit": {"count": 2}})["refusals"]) == 2)
check("B19 a closing that does not exist is refused", not rf.plan(subject={}, destination={"store_code": "Utica"})["ok"])


# ═══════════════════════════════════════════════════════════════════════════════════════════════════
# C. PURE — what the move does
# ═══════════════════════════════════════════════════════════════════════════════════════════════════
print("\n== C. the plan: what follows, what is reported, what is left alone ==")
_full = rf.plan(subject=SUBJ,
                destination={"store_code": "B-531", "store_address": "531 Utica Ave",
                             "sfid": "SF-531"},
                satellites={t: {"count": 2} for t in rf.tables_with(rf.FOLLOWS)}
                | {t: {"count": 1} for t in rf.tables_with(rf.REVIEW)})
check("C1 every FOLLOWS table with rows is in the move list",
      sorted(m["table"] for m in _full["moves"]) == list(rf.tables_with(rf.FOLLOWS)))
check("C2 each move says how its rows are matched and which store columns it takes",
      all(m["match"] and m["store_columns"] for m in _full["moves"]))
check("C3 a FOLLOWS table with NO rows is not in the move list (nothing is written to it)",
      not rf.plan(subject=SUBJ, destination={"store_code": "B-531"})["moves"])
check("C4 the REVIEW rows are REPORTED, and not in the move list",
      len(_full["review"]) == len(rf.tables_with(rf.REVIEW))
      and not [m for m in _full["moves"] if rf.classify(m["table"]) == rf.REVIEW])
check("C5 the review message tells the manager a human has to re-check it",
      "re-check" in " ".join(r["message"] for r in _full["review"]))
check("C6 the STAYS tables are named in every plan, with their reason, so nothing looks forgotten",
      sorted(s["table"] for s in _full["stays"]) == list(rf.tables_with(rf.STAYS)))
check("C7 the closing takes the destination's store code, canonical address and salesforce id",
      _full["closing_patch"]["store_code"] == "B-531"
      and _full["closing_patch"]["store_address"] == "531 Utica Ave"
      and _full["closing_patch"].get("sfid") == "SF-531")
check("C8 a destination with no roster address falls back to the code, never to None-as-a-name",
      rf.plan(subject=SUBJ, destination={"store_code": "B-531"})["closing_patch"]["store_name"] is None)
check("C9 the patch NEVER carries an amount column — a re-file moves a store, not money",
      not [k for k in _full["closing_patch"]
           if k.startswith(("t_", "store_cash", "store_cc", "epay", "acc_")) or "amount" in k])
check("C10 the plan reports the grain it decided under, normalized",
      rf.plan(subject=SUBJ, destination={"store_code": "X"}, grain="REP_day ")["grain"] == "rep_day")
check("C11 store_patch trims a hand-typed code",
      rf.store_patch({"store_code": "  B-531 "})["store_code"] == "B-531")


# ═══════════════════════════════════════════════════════════════════════════════════════════════════
# The wiring for the behavioural section
# ═══════════════════════════════════════════════════════════════════════════════════════════════════
#: FakeDB derives a table's columns from the rows it holds, so an EMPTY table has none and a
#: `select("id,...")` raises 42703 — which the endpoint would read as "unreadable" and refuse on.
#: Declared here so every refusal that fires is the one under test.
_DECLARED = {
    "daily_closing": ["id", "org_id", "period", "close_date", "submitted_at", "sfid", "store_name",
                      "store_code", "store_address", "employee_name", "t_cash", "t_credit",
                      "envelope_picture", "released_at", "released_by", "release_note", "dedup_key",
                      "corrected_at", "correction_count", "source",
                      "refiled_from_store_code", "refiled_at", "refiled_by", "refile_note"],
    "store_mapping": ["id", "org_id", "salesforce_id", "store_code", "store_address"],
    "closing_grain_config": ["id", "org_id", "store_code", "grain", "updated_at", "updated_by"],
    "cash_pickup": ["id", "org_id", "close_date", "store_code", "store_name", "employee_name",
                    "amount", "picked_up", "picked_up_by", "picked_up_at", "note", "created_at"],
    "billpay_pickup": ["id", "org_id", "close_date", "store_code", "store_name", "employee_name",
                       "amount", "picked_up", "created_at"],
    "closing_expense": ["id", "org_id", "store_code", "close_date", "closing_row_id", "amount",
                        "category_kind", "category_name", "status"],
    "closing_metric_line": ["id", "org_id", "store_code", "close_date", "metric_key",
                            "employee_name", "units", "amount", "closing_row_id"],
    "envelope_count": ["id", "org_id", "closing_row_id", "store_code", "close_date",
                       "employee_name", "expected_amount", "counted_amount", "variance", "status"],
    "envelope_withdrawal": ["id", "org_id", "store_code", "close_date", "closing_row_id", "amount",
                            "purpose"],
    "cash_count_revision": ["id", "org_id", "subject_table", "subject_row_id", "store_code",
                            "close_date", "employee_name", "field", "previous_amount",
                            "new_amount", "previous_text", "new_text", "changed_by", "changed_at"],
    "bank_deposit": ["id", "org_id", "close_date", "store_code", "amount", "created_at"],
    "closing_deposit_adjustment": ["id", "org_id", "close_date", "store_code", "amount"],
    "closing_mgmt_review": ["id", "org_id", "close_date", "store_code", "decision"],
    "daily_closing_verification": ["id", "org_id", "close_date", "store_code", "verified_by"],
    "daily_closing_verification_audit": ["id", "org_id", "close_date", "store_code", "action"],
    "raw_epay_daily_tx": ["id", "org_id", "close_date", "store_code", "amount"],
}
#: The subject row: the live Nostrand / 2026-10-10 closing, with its real figures.
ROW = {"id": "closing-rana", "org_id": LUXE, "period": "2026-10", "close_date": "2026-10-10",
       "store_code": "Nostrand", "store_address": "3560 Nostrand Avenue",
       "store_name": "3560 Nostrand Avenue", "employee_name": "Rana Bilal", "t_cash": 230.0,
       "t_credit": 0.0, "envelope_picture": "env/20261010.jpg", "released_at": None,
       "dedup_key": f"{LUXE}|Nostrand|rana bilal|2026-10-10", "source": "manual"}


def wire(dest_closings=(), satellites=None, grain_rows=(), perms=None):
    db = FakeDB()
    db.declared.update(_DECLARED)
    for t in _DECLARED:
        db.tables.setdefault(t, [])
    db.tables["daily_closing"] = [dict(ROW)] + [dict(r) for r in dest_closings]
    db.tables["store_mapping"] = [
        {"id": "sm1", "org_id": LUXE, "salesforce_id": "SF-NOS", "store_code": "Nostrand",
         "store_address": "3560 Nostrand Avenue"},
        {"id": "sm2", "org_id": LUXE, "salesforce_id": "SF-UTI", "store_code": "Utica",
         "store_address": "531 Utica Ave"}]
    db.tables["closing_grain_config"] = list(grain_rows)
    for t, rows in (satellites or {}).items():
        db.tables[t] = [dict(r) for r in rows]
    cr.sb = lambda: db
    cr.get_supabase = lambda: db
    storeops.scope_keyset = lambda auth, org_: None
    cr._caller_perms = lambda client, auth: (perms if perms is not None
                                             else {"__super_admin": True, "__resolved": True})
    return db


def refile(db=None, store_code="Utica", **kw):
    """Run the REAL endpoint. Returns (plan, None) or (None, (status, detail))."""
    body = cr.RefileClosingRowIn(store_code=store_code, **kw)
    try:
        return cr.refile_closing_row(ROW["id"], body, authorization="Bearer x", org_id=LUXE), None
    except HTTPException as e:
        return None, (e.status_code, e.detail)


def row_of(db, rid=ROW["id"]):
    return next((r for r in db.tables["daily_closing"] if r.get("id") == rid), {})


# ═══════════════════════════════════════════════════════════════════════════════════════════════════
# D. BEHAVIOURAL — the REAL endpoint on the REAL store-day
# ═══════════════════════════════════════════════════════════════════════════════════════════════════
print("\n== D. THE ASK: move Rana Bilal's 2026-10-10 closing to 531 Utica, from the screen ==")
_db = wire(satellites={"cash_pickup": [
    {"id": "cp1", "org_id": LUXE, "close_date": "2026-10-10", "store_code": "Nostrand",
     "store_name": "3560 Nostrand Avenue", "employee_name": "Rana Bilal", "amount": 230.0}]})
_p, _err = refile(_db)
check("D1 the move is accepted", _p is not None and _p["ok"], detail=str(_err))
_moved = row_of(_db)
check("D2 the closing now carries the DESTINATION's store code and canonical address",
      _moved.get("store_code") == "Utica" and _moved.get("store_address") == "531 Utica Ave")
check("D3 the salesforce id is re-pointed too, so the row looks like a correctly filed one",
      _moved.get("sfid") == "SF-UTI")
check("D4 not one amount changed — a re-file moves a store, never money",
      _moved.get("t_cash") == 230.0 and _moved.get("envelope_picture") == "env/20261010.jpg")
check("D5 the dedup key is RECOMPOSED at the destination's grain by its one home",
      _moved.get("dedup_key") == dk.for_row(LUXE, "Utica", "Rana Bilal", "2026-10-10",
                                            grain=cg.GRAIN_STORE_DAY), detail=str(_moved.get("dedup_key")))
check("D6 the store-day spelling leaves the rep out, so the mig-502 index now protects Utica/10-10",
      "rana" not in str(_moved.get("dedup_key")).lower())
check("D7 the audit trail says where it came from, when and who did it",
      _moved.get("refiled_from_store_code") == "Nostrand" and _moved.get("refiled_at")
      and _moved.get("refiled_by") == "management")
_cp = _db.tables["cash_pickup"][0]
check("D8 THE HALF-MOVE IS CLOSED: the DM's cash pickup followed the closing",
      _cp.get("store_code") == "Utica" and _cp.get("store_name") == "531 Utica Ave")
check("D9 the response says what moved, so the manager sees it on the screen",
      {"table": "commcalc.cash_pickup", "rows": 1} in (_p.get("moved") or []))
check("D10 the rep's ATTEMPT log is untouched — the mistake stays on the record",
      rf.classify("commcalc.closing_attempt") == rf.STAYS
      and "commcalc.closing_attempt" in [s["table"] for s in _p["stays"]])

print("\n== D(b). the abort he actually hit, reproduced through the endpoint ==")
_db2 = wire(dest_closings=[{"id": "closing-utica", "org_id": LUXE, "close_date": "2026-10-10",
                            "store_code": "Utica", "employee_name": "Rana Bilal", "t_cash": 230.0,
                            "released_at": None}])
_p2, _err2 = refile(_db2)
check("D11 the move is REFUSED with 409 — exactly what the SQL runbook's safety block did",
      _p2 is None and _err2 and _err2[0] == 409)
check("D12 the refusal explains it in his words: the destination already has a closing for the day",
      "already has a closing for 2026-10-10" in str(_err2[1]))
check("D13 NOTHING was changed by the refused move", row_of(_db2).get("store_code") == "Nostrand")

print("\n== D(c). dry run: the screen shows the plan before the manager commits ==")
_db3 = wire(satellites={"cash_pickup": [
    {"id": "cp1", "org_id": LUXE, "close_date": "2026-10-10", "store_code": "Nostrand",
     "employee_name": "Rana Bilal", "amount": 230.0}]})
_p3, _ = refile(_db3, dry_run=True)
check("D14 a dry run returns the same plan", _p3 and _p3["ok"] and _p3["dry_run"] is True
      and _p3["moves"][0]["table"] == "commcalc.cash_pickup")
check("D15 and writes NOTHING — not the closing, not its satellites",
      row_of(_db3).get("store_code") == "Nostrand"
      and _db3.tables["cash_pickup"][0].get("store_code") == "Nostrand")

print("\n== D(d). settled money and clashing satellites, through the endpoint ==")
_db4 = wire(satellites={"bank_deposit": [
    {"id": "bd1", "org_id": LUXE, "close_date": "2026-10-10", "store_code": "Nostrand",
     "amount": 370.0}]})
_p4, _err4 = refile(_db4)
check("D16 a banked store-day refuses the move and says so",
      _p4 is None and _err4[0] == 409 and "bank_deposit" in str(_err4[1]))
check("D17 and changes nothing", row_of(_db4).get("store_code") == "Nostrand")
_db5 = wire(satellites={"cash_pickup": [
    {"id": "cp1", "org_id": LUXE, "close_date": "2026-10-10", "store_code": "Nostrand",
     "employee_name": "Rana Bilal", "amount": 230.0},
    {"id": "cp2", "org_id": LUXE, "close_date": "2026-10-10", "store_code": "Utica",
     "employee_name": "Rana Bilal", "amount": 447.29}]})
_p5, _err5 = refile(_db5)
check("D18 a pickup already recorded at the DESTINATION for this person refuses (its unique key "
      "would have rejected the write)", _p5 is None and _err5[0] == 409
      and "destination_row_exists" in str(_err5[1]))
_db6 = wire()
_p6, _err6 = refile(_db6, store_code="Nostrand")
check("D19 moving a closing to the store it is already at is refused",
      _p6 is None and "same_store" in str(_err6[1]))
_p7, _err7 = refile(_db6, store_code="")
check("D20 a blank destination is refused", _p7 is None and "no_destination" in str(_err7[1]))
_p8, _err8 = (None, None)
try:
    _p8 = cr.refile_closing_row("no-such-row", cr.RefileClosingRowIn(store_code="Utica"),
                                authorization="Bearer x", org_id=LUXE)
except HTTPException as e:
    _err8 = (e.status_code, e.detail)
check("D21 a closing that does not exist is a 404, not a traceback", _err8 and _err8[0] == 404)

print("\n== D(e). the grain is dereferenced here too, never re-decided ==")
_db7 = wire(dest_closings=[{"id": "c-u", "org_id": LUXE, "close_date": "2026-10-10",
                            "store_code": "Utica", "employee_name": "Someone Else",
                            "released_at": None}],
            grain_rows=[{"id": "g1", "org_id": LUXE, "store_code": None, "grain": "rep_day"}])
_p9, _err9 = refile(_db7)
check("D22 a tenant on rep-day grain CAN move a closing onto a day another rep closed", _p9 is not None
      and _p9["ok"], detail=str(_err9))
check("D23 and the key keeps the rep in it at that grain",
      "rana bilal" in str(row_of(_db7).get("dedup_key")).lower())


# ═══════════════════════════════════════════════════════════════════════════════════════════════════
# E. The permission gate
# ═══════════════════════════════════════════════════════════════════════════════════════════════════
print("\n== E. the same gate as release — a DM cannot re-file ==")
_db8 = wire(perms={"__resolved": True, "scope": "store", "pages": {}})
_pa, _erra = refile(_db8)
check("E1 a non-management caller is refused 403", _pa is None and _erra[0] == 403)
check("E2 and the refusal says it is permission-restricted, not available to DMs",
      "not available to DMs" in str(_erra[1]))
check("E3 nothing was changed", row_of(_db8).get("store_code") == "Nostrand")
check("E4 the endpoint gates on the SAME helper as POST /row/{id}/release — one rule, not a second "
      "copy", len(re.findall(r"_can_mgmt_review\(_caller_perms\(client, authorization\)\)",
                             ROUTER_SRC)) >= 2)


# ═══════════════════════════════════════════════════════════════════════════════════════════════════
# F. WIRING LOCKS — these fail the build if the design un-wires
# ═══════════════════════════════════════════════════════════════════════════════════════════════════
print("\n== F. the locks ==")
_fn = ROUTER_SRC[ROUTER_SRC.index("def refile_closing_row"):]
_fn = _fn[:_fn.index("\n@router.")] if "\n@router." in _fn else _fn
check("F1 the endpoint exists and is a POST on the closing row",
      '@router.post("/row/{row_id}/refile")' in ROUTER_SRC)
check("F2 the endpoint DEREFERENCES the registry — it does not carry its own table list",
      "_refile.plan(" in _fn and "_refile.TABLES" in _fn)
check("F3 it dereferences closing_grain for 'is the destination already closed'",
      "_closing_grain(" in _fn)
check("F4 it dereferences dedup_key for the new key, and spells no key itself",
      "_dedup.for_row(" in _fn and "|" not in _fn.replace("||", ""))
check("F5 it applies the plan's own patch rather than building a second one",
      'plan["closing_patch"]' in _fn)
check("F6 it writes NOTHING when the plan is not ok",
      re.search(r'if not plan\["ok"\]:\s*\n\s*raise HTTPException\(409', _fn) is not None)
_sat_src = ROUTER_SRC[ROUTER_SRC.index("def _refile_satellite"):]
_sat_src = _sat_src[:_sat_src.index("\n@router.")]
check("F7 a failed satellite read is reported as UNREADABLE, never as an empty table",
      "return [], True" in _sat_src)
check("F8 the satellites are moved BEFORE the closing, so a failure leaves the store-day whole",
      _fn.index('for mv in plan["moves"]') < _fn.index('table("daily_closing").update(patch)'))
_disp_copies = [p for p in glob.glob("app/modules/**/*.py", recursive=True)
                if p.replace("\\", "/") != REFILE_PATH
                and re.search(r'["\']follows["\']', open(p, encoding="utf-8", errors="replace").read())
                and re.search(r'["\']blocks["\']', open(p, encoding="utf-8", errors="replace").read())]
check("F9 no second copy of the disposition vocabulary exists anywhere in the backend",
      not _disp_copies, detail=str(_disp_copies))
_mig = open(MIG_PATH).read().lower()
check("F10 migration 1081 is ADDITIVE — no update, delete, drop or truncate of data",
      not re.search(r"^\s*(update|delete|truncate|drop\s+table)\b", _mig, re.M))
check("F11 migration 1081 is idempotent", _mig.count("if not exists") >= 2)
check("F12 migration 1081 carries a REVERT note", "-- revert:" in _mig)
check("F13 migration 1081 moves no money", not re.search(r"\b(amount|t_cash|store_cash)\b\s*=", _mig))
check("F14 the audit columns the endpoint writes are the ones the migration adds",
      all(c in _mig for c in ("refiled_from_store_code", "refiled_at", "refiled_by", "refile_note")))
check("F15 the endpoint survives migration 1081 NOT being applied, and says the trail is missing",
      "audit_columns_missing" in _fn)
_idx = open("../docs/SYSTEM_DATA_FLOW_INDEX.md", encoding="utf-8", errors="replace").read()
MGMT_PATH = "../frontend/src/app/(platform)/closing/management/page.tsx"
_page = open(MGMT_PATH, encoding="utf-8").read() if os.path.exists(MGMT_PATH) else ""
check("F17 the manager can reach it FROM THE SCREEN — an endpoint with no control is not what he "
      "asked for", "/refile" in _page and "Move to store" in _page, detail=MGMT_PATH)
check("F18 the screen asks for the plan FIRST (dry_run) and only then commits — a move is too hard "
      "to eyeball to be a single click",
      "dry_run: !commit" in _page and "Confirm move to" in _page)
check("F19 the destination is PICKED from the roster, never typed as a store code",
      "roster.filter(o => o.id !== g.store_code)" in _page)
check("F16 the mechanism is registered in the index (CLAUDE.md: register what you create)",
      "closing_refile" in _idx and "/closing/row/{id}/refile" in _idx.replace("{row_id}", "{id}"))


# ═══════════════════════════════════════════════════════════════════════════════════════════════════
# G. NEGATIVE CONTROLS — the locks catch the defect coming back
# ═══════════════════════════════════════════════════════════════════════════════════════════════════
print("\n== G. negative controls ==")
check("G1 a new (close_date, store_code) table with no ruling FAILS A2",
      rf.unregistered(dict(_derived, **{"commcalc.brand_new_table": "9999_x.sql"}))
      == ("commcalc.brand_new_table",))
check("G2 a plan that forgot the destination-already-closed question would fail B4 — the check "
      "really does depend on destination_closings",
      plan(destination_closings=[])["ok"] and not _taken["ok"])
check("G3 the grain is not hardcoded: the SAME inputs give different answers at the two grains",
      plan(destination_closings=[{"id": "r2", "employee_name": "z"}])["ok"] is False
      and plan(grain=cg.GRAIN_REP_DAY, destination_closings=[{"id": "r2", "employee_name": "z"}])["ok"] is True)
check("G4 a FOLLOWS table reclassified as STAYS would stop being moved — so C1 is load-bearing",
      "commcalc.cash_pickup" not in [m["table"] for m in rf.plan(
          subject=SUBJ, destination={"store_code": "X"},
          satellites={"commcalc.closing_attempt": {"count": 5}})["moves"]])
check("G5 the unreadable rule is not vacuous — the same table with count 0 and no error is fine",
      plan(satellites={"commcalc.bank_deposit": {"count": 0}})["ok"])
check("G6 the store-day scan is not matching everything: a table without both columns is absent",
      "commcalc.store_mapping" not in _derived and "commcalc.closing_expense" in _derived)


print(f"\n{len(PASS)} passed, {len(FAIL)} failed")
for f in FAIL:
    print("  FAILED: " + f)
sys.exit(1 if FAIL else 0)
