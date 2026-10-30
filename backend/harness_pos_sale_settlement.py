#!/usr/bin/env python3
"""PROOF + LOCK: the cash / credit mismatch an edit leaves behind (index §POS-13).

DB-free. Imports the REAL module, reads the REAL router, the REAL migration and the REAL page, so a
lock here fails the build rather than describing an intention.

Owner 2026-10-30: *"i need the pos POS — Cash Register to have the ability to edit the sale from the
prior day and create log of the cash or credit mismatch"*.

WHAT THIS HARNESS IS FOR. The edit itself was already live; what was missing is the log, and the way
a log like this goes wrong is not by crashing. It goes wrong by producing confident figures it never
measured. Three properties stop that, and CLAUDE.md requires that a design fix ship with a check
that FAILS THE BUILD if a caller stops dereferencing the shared fact:

  §A  one home for the comparison, and no caller holds a second opinion
  §B  an unrecorded tender is never a $0.00 tender — the 4,603-row property
  §C  a part-tendered sale is compared against total − balance, never the total
  §D  which tender is short is never guessed when the sale carried two
  §E  the verdict is deposit_recon's existing truth table, not a fourth one
  §F  the band is declared, and HOUSE_BAND is the only dollar literal
  §G  the void sibling is covered by the same one home
  §H  the preview and the save are ONE computation (the dry run)
  §I  an edit that creates unexplained money will not save unless it can be logged
  §J  the migration extends mig 1103's table and signature — no sibling, no overload
  §K  negative controls — each lock really fails when its subject is broken
"""
import ast
import pathlib
import re
import sys

ROOT = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from app.modules.closing import deposit_recon as DR          # noqa: E402
from app.modules.pos import sale_settlement as S             # noqa: E402

MOD = (ROOT / "app/modules/pos/sale_settlement.py").read_text()
ROUTER = (ROOT / "app/modules/pos/router.py").read_text()
MIG = (ROOT.parent / "database/migrations/1106_pos_sale_tender_mismatch.sql").read_text()
MIG_1103 = (ROOT.parent / "database/migrations/1103_pos_sale_amendment.sql").read_text()
PAGE = (ROOT.parent / "frontend/src/app/(platform)/pos/sales/page.tsx").read_text()

_passed = 0
_failed = 0


def ok(label, cond, detail=""):
    global _passed, _failed
    if cond:
        _passed += 1
        print(f"  PASS  {label}")
    else:
        _failed += 1
        print(f"  FAIL  {label}" + (f"  [{detail}]" if detail else ""))



def _code_only(text: str) -> str:
    """The module with every comment and every docstring REMOVED.

    The locks that assert "this word appears in no branch" have to read code, not prose. Without
    this, §F7 counted the `$0.00` in "an unrecorded tender is not a $0.00 tender", §K4 matched the
    word "org" in a sentence about orgs, and §K9 matched "THE CLASS, NOT THE INSTANCE" — three locks
    that would have passed whatever the code did. `tokenize` drops comments; the AST walk drops the
    docstring of the module and of every function and class.
    """
    import io
    import tokenize as _tk
    # Only DOCSTRINGS go; every other string literal stays, or a lock that has to see a dict key
    # (§C4 reads `"balance"`) would be testing a view with no keys in it.
    docs = set()
    try:
        tree = ast.parse(text)
    except SyntaxError:                             # pragma: no cover - a syntax error fails louder
        return text
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            d = ast.get_docstring(node, clean=False)
            if d:
                docs.add(d)
    out = []
    try:
        for tok in _tk.generate_tokens(io.StringIO(text).readline):
            if tok.type in (_tk.COMMENT, _tk.NL):
                continue
            if tok.type == _tk.STRING:
                try:
                    if ast.literal_eval(tok.string) in docs:
                        continue
                except (ValueError, SyntaxError):
                    pass
            out.append(tok.string)
    except (_tk.TokenError, IndentationError):      # pragma: no cover
        return text
    return " ".join(out)


CODE = _code_only(MOD)

def sale(total=210.13, balance=0.0, **kw):
    row = {"id": "s-1", "transaction_id": 4605, "status": "completed",
           "total": total, "balance": balance}
    row.update(kw)
    return row


def pay(method, amount):
    return {"payment_method": method, "amount": amount}


# The region of the router that owns §POS-13's reads. Bounded for the same reason §POS-12's is: an
# unbounded split would make every count below meaningless.
REGION = ROUTER.split("# ── Editing a rung sale in place", 1)
REGION = (REGION[1].split("# ── Register (cash drawer) sessions", 1)[0] if len(REGION) == 2 else "")
REPORT = ROUTER.split('@router.get("/reports/tender-mismatch")', 1)
REPORT = (REPORT[1].split('@router.get("/reports/activations")', 1)[0] if len(REPORT) == 2 else "")


# ── §A one home ──────────────────────────────────────────────────────────────────────────────────
print("\n§A  one home for the comparison")
ok("A1. the module answers it at all", S.outstanding(sale(), [pay("cash", 190.53)])["measured"])
ok("A2. the amend region is bounded", 0 < len(REGION) < 14000)
ok("A3. the report region exists and is bounded", 0 < len(REPORT) < 9000, len(REPORT))
ok("A4. the save dereferences the one home for the comparison", "_settle.outstanding(" in REGION)
ok("A5. the save dereferences the one home for the log row", "_settle.record(" in REGION)
ok("A6. the save dereferences the one home for the sentence", "_settle.sentence(" in REGION)
ok("A7. the report dereferences the SAME one home, not a read of its own",
   "_settle.outstanding(" in REPORT and "_settle.summarise(" in REPORT)
# The arithmetic must live in ONE place. A router that subtracts a tender from a total itself is the
# sibling derivation the house rules forbid, and it is the thing most likely to be written by hand.
_SUBTRACTS = re.compile(r"(tender\w*|tendered)\s*-\s*(total|expected)|"
                        r"(total|expected)\s*-\s*(tender\w*|tendered)")
ok("A8. no caller subtracts a tender from a total itself",
   not _SUBTRACTS.search(REGION) and not _SUBTRACTS.search(REPORT))
ok("A9. the browser does not derive the mismatch either — it shows what the server sent",
   not _SUBTRACTS.search(PAGE))


# ── §B absence is never zero (the 4,603-row property) ────────────────────────────────────────────
print("\n§B  an unrecorded tender is not a $0.00 tender")
_t = S.tendered([])
ok("B1. no payment row answers present: False", _t["present"] is False)
ok("B2. ...and a total of None, not 0.00", _t["total"] is None)
_o = S.outstanding(sale(total=210.13), [])
ok("B3. the comparison refuses rather than reporting a shortage", _o["measured"] is False)
ok("B4. ...with no variance", _o["variance"] is None and _o["verdict"] is None)
ok("B5. ...naming the reason as a sentence a screen can show",
   _o["reason"] == "tender_unrecorded" and _o["reason_label"] in S.UNMEASURED_REASONS.values())
ok("B6. NOT MEASURABLE IS NOT A FINDING", _o["mismatch"] is False)
ok("B7. the log row files the reason and NO figures",
   S.record(sale(), [])["tender_reason"] == "tender_unrecorded"
   and S.record(sale(), [])["tender_variance"] is None)
ok("B8. the sentence claims no dollar figure it did not measure",
   "$" not in S.sentence(sale(), _o))
# The rollup is where this would leak back in as money.
_many = [{"measured": False, "mismatch": False, "variance": None, "direction": None,
          "reason": "no payment was ever recorded against this sale"}] * 4603
_sum = S.summarise(_many)
ok("B9. 4,603 unmeasurable sales add $0.00 to every total, and 0 findings",
   _sum["not_measurable"] == 4603 and _sum["mismatches"] == 0
   and _sum["to_collect"] == 0.0 and _sum["to_refund"] == 0.0 and _sum["net_variance"] == 0.0)
ok("B10. ...and are SAID, not swallowed", S.blockers(_many)[0]["count"] == 4603)
# The database must not be able to hold a row that is both.
ok("B11. the migration forbids a row carrying both a reason and a variance",
   "pos_sale_amend_tender_either_ck" in MIG
   and "tender_reason is null or tender_variance is null" in MIG)
ok("B12. the migration's columns are NULL on absence, never 0.00",
   "NULL, never 0.00" in MIG)


# ── §C a part-tendered sale ──────────────────────────────────────────────────────────────────────
print("\n§C  compared against total − balance, never the total")
ok("C1. a balance reduces what the tender is compared against",
   S.expected(sale(total=210.13, balance=50.00))["amount"] == 160.13)
ok("C2. no balance leaves the total alone",
   S.expected(sale(total=210.13, balance=0))["amount"] == 210.13)
ok("C3. a part-tendered sale paid to its balance is SETTLED, not short",
   S.outstanding(sale(total=210.13, balance=50.00), [pay("cash", 160.13)])["mismatch"] is False)
ok("C4. the module reads the balance column by name, in code", '"balance"' in CODE)
ok("C5. a sale with no total is not measurable, not a $0.00 sale",
   S.outstanding(sale(total=None), [pay("cash", 10.0)])["reason"] == "total_unrecorded")


# ── §D which tender is never guessed ─────────────────────────────────────────────────────────────
print("\n§D  which tender is short is never guessed")
_one = S.outstanding(sale(total=210.13), [pay("cash", 190.53)])
ok("D1. one method is attributed to that method", _one["attribution"]["method"] == "cash")
_two = S.outstanding(sale(total=250.00), [pay("cash", 100.0), pay("credit_card", 90.53)])
ok("D2. two methods attribute to NEITHER", _two["attribution"]["method"] is None)
ok("D3. ...and say so", _two["attribution"]["ambiguous"] is True)
ok("D4. ...and name both, so the till can ask about the right ones",
   _two["attribution"]["methods"] == ["cash", "credit_card"])
ok("D5. the sentence asks rather than asserting",
   "which of them" in S.sentence(sale(total=250.00), _two))
# RULE TWO: the tenant's own tender words, never this module's.
ok("D6. no tender method is spelled in a branch in the module CODE",
   not re.search(r"(cash|credit_card|debit_card|card_external)", CODE))
ok("D7. no carrier, tenant or POS vendor name appears in the module",
   not any(w in CODE.lower() for w in
           ("cellfonz", "luxelink", "vzone", "ny logistics", "boost", "total wireless", "b2bsoft")))


# ── §E the verdict is the platform's own truth table ─────────────────────────────────────────────
print("\n§E  no fourth set of words for short / over / ok")
ok("E1. the module calls deposit_recon.status_for", "deposit_recon.status_for(" in MOD)
ok("E2. ...and does not reimplement the comparison",
   not re.search(r'return\s+["\'](short|over|ok)["\']', MOD))
for _tot, _tend in ((210.13, 190.53), (150.00, 190.53), (190.53, 190.53)):
    _r = S.outstanding(sale(total=_tot), [pay("cash", _tend)])
    ok(f"E3. {_tend} against {_tot} agrees with status_for",
       _r["verdict"] == DR.status_for(_r["variance"], 0.0))
ok("E4. negative is short here as everywhere else",
   S.outstanding(sale(total=210.13), [pay("cash", 190.53)])["variance"] < 0)
ok("E5. the direction is this module's own answer, and is not a fourth verdict",
   set(S.DIRECTIONS) == {"collect", "refund", "settled"})
ok("E6. the migration constrains the verdict to those three words",
   "tender_verdict in ('short', 'over', 'ok')" in MIG)
ok("E7. ...and the direction to these three", "tender_direction in ('collect', 'refund', 'settled')" in MIG)


# ── §F the band is declared ──────────────────────────────────────────────────────────────────────
print("\n§F  the band is config, never code (RULE TWO)")
ok("F1. the house band is zero, unlike §72's five", S.HOUSE_BAND == 0.0)
ok("F2. the setting has a one home for its key", S.BAND_SETTING == "tender_mismatch_band")
ok("F3. a declared band is honoured", S.declared_band("2.50") == 2.50)
for bad in (None, "", "nonsense", -5):
    ok(f"F4. {bad!r} falls back to the house band, never widening it",
       S.declared_band(bad) == S.HOUSE_BAND)
ok("F5. the router reads the key from the one home, not a string of its own",
   "_settle.BAND_SETTING" in ROUTER and '"tender_mismatch_band"' not in REGION)
ok("F6. the router resolves it through the ONE pos config home",
   "_poscfg.effective(rows, _settle.BAND_SETTING" in ROUTER)
# HOUSE_BAND must be the only dollar literal, or a second one is a second policy.
_literals = re.findall(r"(?<![\w.])\d+\.\d{2}(?![\w])", CODE)
ok("F7. HOUSE_BAND is the only dollar literal in the module CODE",
   all(float(x) == S.HOUSE_BAND for x in _literals), _literals)
ok("F8. the module takes the band as an ARGUMENT, so it reads no org's config",
   "band=None" in MOD and "get_supabase" not in CODE and "org_id" not in CODE)


# ── §G the void sibling ──────────────────────────────────────────────────────────────────────────
print("\n§G  the void sibling is answered by the same one home")
_v = S.outstanding(sale(total=190.53, status="voided"), [pay("cash", 190.53)])
ok("G1. a voided sale expects no tender at all", _v["expected"]["amount"] == 0.0)
ok("G2. ...so the whole tender is outstanding", _v["variance"] == 190.53)
ok("G3. ...as a refund", _v["direction"] == S.REFUND and _v["mismatch"] is True)
ok("G4. the void stamp alone is enough, not just the status",
   S.expected(sale(voided_at="2026-10-30T12:00:00Z"))["amount"] == 0.0)
ok("G5. the report lists voided sales rather than filtering them out",
   "voided" in REPORT.lower())
ok("G6. the rollup counts them separately so they can be acted on",
   "voided_with_tender" in MOD)
ok("G7. mig 725's void_sale is deliberately NOT reopened, and the migration says why",
   "void_sale" in MIG and "deliberately NOT touched" in MIG)


# ── §H the preview and the save are one computation ──────────────────────────────────────────────
print("\n§H  the dry run — one computation, not two that agree today")
ok("H1. the save route takes a dry run", 'body.get("dry_run")' in REGION)
ok("H2. the dry run returns BEFORE the write", REGION.index('body.get("dry_run")')
   < REGION.index('rpc("amend_sale"'))
ok("H3. the mismatch is computed BEFORE the dry-run return, so both paths share it",
   REGION.index("_settle.outstanding(") < REGION.index('body.get("dry_run")'))
ok("H4. the dry run returns the same keys the save does",
   '"tender": settlement' in REGION and '"tender_message": tender_sentence' in REGION)
ok("H5. there is no SECOND preview route to drift from the save",
   REGION.count("@router.") == 3)


# ── §I it will not save what it cannot log ───────────────────────────────────────────────────────
print("\n§I  an edit that creates unexplained money must be loggable")
ok("I1. the router probes for the migration rather than assuming it",
   "def _tender_log_available(" in ROUTER)
ok("I2. the probe asks the database for the column", 'select("tender_variance")' in ROUTER)
ok("I3. a mismatch that cannot be logged refuses the save",
   'settlement["mismatch"] and not _tender_log_available(' in REGION)
ok("I4. ...with a 503, the same fail-closed shape §POS-12 uses", "503" in REGION)
ok("I5. ...and the refusal is raised BEFORE the write",
   REGION.index('settlement["mismatch"] and not _tender_log_available(')
   < REGION.index('rpc("amend_sale"'))
ok("I6. a SETTLED edit is not held up by a migration it does not use",
   'settlement["mismatch"] and not' in REGION)
ok("I7. the record travels in the patch, so no new parameter is needed",
   '"tender": tender' in REGION)


# ── §J the migration extends, it does not sibling ────────────────────────────────────────────────
print("\n§J  mig 1106 extends mig 1103's own table and signature")
ok("J1. it alters mig 1103's table rather than creating another",
   "alter table pos.sale_amendments" in MIG)
ok("J2. it creates NO new table", not re.search(r"create\s+table", MIG, re.I))
ok("J3. every column is added idempotently",
   MIG.count("add column if not exists") >= 14)
ok("J4. pos.amend_sale keeps its EXISTING signature, so no overload is created",
   "create or replace function pos.amend_sale(" in MIG
   and "p_reason    text default null" in MIG
   and "p_tender" not in MIG.split("$$", 1)[0])
ok("J5. ...and the migration never drops the function to change it",
   not re.search(r"^\s*drop function", MIG, re.I | re.M))
ok("J6. the signature it replaces is byte-identical to mig 1103's",
   MIG_1103.split("create or replace function pos.amend_sale(", 1)[1].split("returns", 1)[0]
   == MIG.split("create or replace function pos.amend_sale(", 1)[1].split("returns", 1)[0])
ok("J7. a migration grants what it touches, service_role only",
   "grant select, insert, update, delete on pos.sale_amendments to service_role;" in MIG
   and " to anon" not in MIG)
ok("J8. it carries a REVERT note", "-- REVERT:" in MIG)
ok("J9. it declares that it moves no money", "MOVES NO MONEY" in MIG)
ok("J10. it files the record from the one home rather than re-deriving in plpgsql",
   "sale_settlement" in MIG and "tender_variance')::numeric" in MIG)
ok("J11. the migration number is not claimed twice",
   len(list((ROOT.parent / "database/migrations").glob("1106_*.sql"))) == 1)


# ── §K negative controls ─────────────────────────────────────────────────────────────────────────
print("\n§K  controls — each lock really fails when its subject is broken")
ok("K1. control: a $0.00 tender and an ABSENT tender do not answer the same",
   S.outstanding(sale(total=0.0), [pay("cash", 0.0)])["measured"] is True
   and S.outstanding(sale(total=0.0), [])["measured"] is False)
ok("K2. control: a declared band really changes the finding, so F3 is not vacuous",
   S.outstanding(sale(total=192.0), [pay("cash", 190.53)], band=5.0)["mismatch"] is False
   and S.outstanding(sale(total=192.0), [pay("cash", 190.53)])["mismatch"] is True)
ok("K3. control: the band never changes the MONEY, only whether it counts",
   S.outstanding(sale(total=192.0), [pay("cash", 190.53)], band=5.0)["variance"]
   == S.outstanding(sale(total=192.0), [pay("cash", 190.53)])["variance"] == -1.47)
ok("K4. control: one tenant's band cannot answer for another's sale — the CODE holds no org id",
   "org_id" not in CODE and "organization_id" not in CODE)
ok("K5. control: the subtraction detector in A8 really matches a hand-rolled one",
   bool(_SUBTRACTS.search("x = tendered - total")))
ok("K6. control: the module is importable with no database and no app config",
   "get_supabase" not in CODE and "import os" not in CODE)
ok("K7. control: the module takes no clock", "datetime" not in CODE)
try:
    _funcs = {n.name for n in ast.walk(ast.parse(MOD)) if isinstance(n, ast.FunctionDef)}
except SyntaxError:
    _funcs = set()
ok("K8. control: every function the router and the tests call exists",
   {"tendered", "expected", "outstanding", "record", "sentence", "declared_band",
    "attribution", "summarise", "blockers"} <= _funcs, sorted(_funcs))
ok("K9. control: the one home is a module, not a class on the router",
   not re.search(r"\bclass\b", CODE))
ok("K9b. control: the code-only view really strips prose, so K4/K7/K9 are not vacuous",
   "THE CLASS, NOT THE INSTANCE" in MOD and "THE CLASS" not in CODE
   and "4,603" in MOD and "4,603" not in CODE)
ok("K10. control: the rollup really separates measured money from unmeasured",
   S.summarise([{"measured": True, "mismatch": True, "variance": -10.0,
                 "direction": S.COLLECT}])["to_collect"] == 10.0)


print(f"\n{_passed} passed, {_failed} failed")
sys.exit(1 if _failed else 0)
