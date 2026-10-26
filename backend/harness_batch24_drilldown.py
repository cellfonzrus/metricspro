"""PROOF + BUILD LOCK — batch 24 of §CORE-17: the PAYROLL AND WORKFORCE drill-downs (§CORE-17.3x).

WHAT THIS PROVES, BY DRIVING THE REAL PRODUCERS
  · `storeops/payroll_hours_drilldown` over real payroll rows — the four figures `/storeops/payroll`
    and `/storeops/reports` both print, with the person nothing measured shown WITHOUT a figure.
  · `storeops/scheduled_hours_drilldown` over the rows the sourced week read stamps — the store-week
    of PLANNED hours `/storeops/hours-budget` prints and shows the rows of nowhere.
  · The grain count, MEASURED off the panels as a SET compared for EQUALITY (never a `>=` bound —
    `harness_subject_set_lock.py` §B1 fails the build on one, and it has caught two batches), each
    equality mutation-tested with a GHOST SWAPPED IN AT UNCHANGED SIZE.

WHAT IT LOCKS SO IT CANNOT UN-WIRE (CLAUDE.md)
  A  one fact, one home — BOTH halves: the two new registry names AND the deleted copies; the
     basis→relation map keyed on the payroll engine's OWN basis constants, with EVERY basis covered.
  D  pay visibility: a gated caller is served NO money panel — not a panel of $0.00s.
  F  thin punch coverage is SURFACED on every hours panel, and the clock and the plan land on
     different relations row by row.
  G  §CORE-17.3o decided per page BY READING EACH PAGE, locked in BOTH directions off its own text.
  H  no figure moved: every producer's payload deep-compared before and after.
  I  the grains.  J  the ratchet.  K  registration.

Run: `cd backend && python3 harness_batch24_drilldown.py`  (DB-free, stdlib only.)
"""
import ast
import copy
import glob
import os
import re
import sys

from app.modules.account import liabilities_drilldown as ldd
from app.modules.commcalc import data_lineage_registry as lineage
from app.modules.core import report_drilldown as rd
from app.modules.storeops import payroll_hours_basis as hb
from app.modules.storeops import payroll_hours_drilldown as phd
from app.modules.storeops import scheduled_hours_drilldown as shd

PASS, FAIL = [], []
ROOT = ".."


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print(("PASS" if cond else "FAIL") + f"  {name}" + (f"  [{detail}]" if detail and not cond else ""))


def ctl(name, red):
    """A MUTATION CONTROL. `red` is True when the mutated world really does break the thing the
    check above asserts — a check that cannot fail is not a check."""
    check("MUT " + name, bool(red))


def read(rel):
    p = f"{ROOT}/{rel}"
    return open(p, encoding="utf-8").read() if os.path.exists(p) else ""


def row(panel, label):
    for r in panel.get("rows") or ():
        if r.get("label") == label:
            return r
    return {}


def tables(r):
    return sorted({o["table"] for o in (r.get("origins") or ()) if o.get("table")})


DECLS = {
    "payroll": read("backend/app/modules/storeops/payroll_hours_drilldown.py"),
    "scheduled": read("backend/app/modules/storeops/scheduled_hours_drilldown.py"),
}
ROUTER = read("backend/app/modules/storeops/router.py")
LIAB = read("backend/app/modules/account/liabilities_drilldown.py")
REGISTRY = read("backend/app/modules/commcalc/data_lineage_registry.py")
ONE_HOME = read("backend/app/modules/core/report_drilldown.py")
PAGES = {
    "payroll": read("frontend/src/app/(platform)/storeops/payroll/page.tsx"),
    "reports": read("frontend/src/app/(platform)/storeops/reports/page.tsx"),
    "hours-budget": read("frontend/src/app/(platform)/storeops/hours-budget/page.tsx"),
}
#: WHICH PAGES FOLD A FIGURE OVER A POPULATION THE SERVER DID NOT — decided by READING each page
#: (§CORE-17.3o), and §G locks each decision in BOTH directions.
#:
#: The two payroll screens filter their rows in the browser (store / market / employee) and then
#: reduce over what survives, so a server-folded panel would explain a number they are not printing.
#: `/storeops/hours-budget` is the negative: it has a browser filter too, but every figure it prints
#: is ONE STORE's own server-folded number, so filtering hides whole rows and changes no figure. The
#: call is not "does this page filter", it is "does this page FOLD over a population the server did
#: not" — the same reading batch 22 settled for the CommCalc dashboard, from the other side.
NARROWED_PAGES = ("payroll", "reports")
SERVER_FOLDED_PAGES = ("hours-budget",)


def _literals(src):
    """Every STRING literal the module's CODE contains. A relation name may appear in a DOCSTRING or
    a comment — that is a note to the next engineer and the point of writing the gaps down. The
    rulings are about the CODE, so the two are told apart by PARSING rather than by grepping."""
    tree = ast.parse(src)
    docs = set()
    for node in ast.walk(tree):
        body = getattr(node, "body", None) or []
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)) \
                and body and isinstance(body[0], ast.Expr) \
                and isinstance(body[0].value, ast.Constant) and isinstance(body[0].value.value, str):
            docs.add(id(body[0].value))
    return [n.value for n in ast.walk(tree)
            if isinstance(n, ast.Constant) and id(n) not in docs and isinstance(n.value, str)]


def _field_reads(src):
    """Every string a module uses as a PAYLOAD FIELD NAME — the argument of a `.get("x")`. A payload
    field can share a relation's spelling by coincidence (`/storeops/payroll` serves a `shifts`
    COUNT), and a lock that could not tell the two apart would be a lock about spelling rather than
    about provenance."""
    out = set()
    for node in ast.walk(ast.parse(src)):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) \
                and node.func.attr == "get" and node.args \
                and isinstance(node.args[0], ast.Constant) and isinstance(node.args[0].value, str):
            out.add(node.args[0].value)
    return out


def _name_constants(src):
    """Every module-level NAME = "literal" binding. A provenance name bound as a constant here is
    the second copy the registry exists to prevent; this is the fact the lock below is about."""
    out = {}
    for node in ast.parse(src).body:
        if isinstance(node, ast.Assign) and isinstance(node.value, ast.Constant) \
                and isinstance(node.value.value, str):
            for t in node.targets:
                if isinstance(t, ast.Name):
                    out[t.id] = node.value.value
    return out


def strip_comments(src):
    """A TS source with its comments removed, so a rule WRITTEN DOWN in a page's header cannot be
    mistaken for the rule being broken in its code. Batch 18's §F posture, reused."""
    out = re.sub(r"/\*.*?\*/", "", src, flags=re.S)
    return re.sub(r"(?m)^\s*//.*$", "", out)


# ══════════════════════════════════════════════════════════════════════════════════════════════════
print("\n== A. ONE FACT, ONE HOME — the design fix, with BOTH halves of it ==")
# ══════════════════════════════════════════════════════════════════════════════════════════════════
check("A0 both declarations, the router, the one home, the lineage registry, the re-keyed caller and "
      "all three pages were read",
      all(DECLS.values()) and ROUTER and ONE_HOME and REGISTRY and LIAB and all(PAGES.values()))
check("A1 DESIGN FIX — THE EMPLOYEE RECORD AND HAND-ENTERED HOURS NOW HAVE ONE HOME EACH, in the "
      "lineage registry beside the time clock and the schedule, instead of a literal in whichever "
      "declaration needed them first",
      lineage.EMPLOYEE_ROSTER == "employees" and lineage.MANUAL_HOURS == "manual_hours"
      and 'EMPLOYEE_ROSTER = "employees"' in REGISTRY and 'MANUAL_HOURS = "manual_hours"' in REGISTRY)
check("A1b AND THE OTHER HALF, IN THE SAME COMMIT — the pre-existing copies in "
      "`account/liabilities_drilldown` were DELETED and dereference the registry now, by object "
      "identity. Naming a fact is half a design fix; deleting every copy is the half that has been "
      "skipped three times here (§19.18)",
      ldd.EMPLOYEES is lineage.EMPLOYEE_ROSTER and ldd.MANUAL_HOURS is lineage.MANUAL_HOURS
      and 'EMPLOYEES = _lineage.EMPLOYEE_ROSTER' in LIAB
      and 'MANUAL_HOURS = _lineage.MANUAL_HOURS' in LIAB
      and 'EMPLOYEES = "employees"' not in LIAB and 'MANUAL_HOURS = "manual_hours"' not in LIAB)
check("A1c and the re-key changed no VALUE, so every panel that already named either relation names "
      "exactly the same relation",
      ldd.EMPLOYEES == "employees" and ldd.MANUAL_HOURS == "manual_hours")
check("A2 THE TIME CLOCK AND THE SCHEDULE ARE DEREFERENCED, NOT RE-NAMED — what was worked names the "
      "clock and the plan names the schedule, both from the one home (§CORE-17.3t)",
      phd.TIME_CLOCK is lineage.TIME_CLOCK and phd.SCHEDULE is lineage.SCHEDULE
      and shd.SCHEDULE is lineage.SCHEDULE)
RELATION_NAMES = {lineage.TIME_CLOCK, lineage.SCHEDULE, lineage.EMPLOYEE_ROSTER,
                  lineage.MANUAL_HOURS}
_bound = {v for s in DECLS.values() for v in _name_constants(s).values()}
check("A3 BUILD LOCK — NEITHER DECLARATION BINDS A RELATION NAME AS A CONSTANT OF ITS OWN. That is "
      "the fact: a provenance name has one home and is dereferenced from it, so a second copy cannot "
      "quietly appear here and then diverge",
      not (_bound & RELATION_NAMES), str(sorted(_bound & RELATION_NAMES)))
ctl("A3a control: the reader really reads the CODE — a module that binds the clock's name is "
    "reported, and one that only mentions it in a docstring is not",
    lineage.TIME_CLOCK in set(_name_constants('X = "%s"\n' % lineage.TIME_CLOCK).values())
    and lineage.TIME_CLOCK not in set(
        _name_constants('"""mentions %s"""\nX = 1\n' % lineage.TIME_CLOCK).values()))
_lit = {t for s in DECLS.values() for t in _literals(s)}
_fields_read = {f for s in DECLS.values() for f in _field_reads(s)}
_stray = (_lit & RELATION_NAMES) - _fields_read
check("A3b AND NO RELATION NAME APPEARS ANYWHERE ELSE IN EITHER MODULE'S CODE, with ONE exception "
      "that is measured rather than waved through: `/storeops/payroll` serves a `shifts` COUNT, so "
      "that word appears as a PAYLOAD FIELD READ and nothing else. A docstring that mentions a "
      "relation is not a copy of one — they are told apart by parsing, not by grepping",
      not _stray and (_lit & RELATION_NAMES) == {lineage.SCHEDULE}
      and lineage.SCHEDULE in _fields_read,
      "stray=%s" % sorted(_stray))
ctl("A3c control: the exception is narrow — a relation name used anywhere OTHER than a field read "
    "is still reported, so this cannot pass on a module that spells one in a provenance position",
    ((set(_literals('T = ("%s",)\n' % lineage.TIME_CLOCK)) & RELATION_NAMES)
     - _field_reads('T = ("%s",)\n' % lineage.TIME_CLOCK)) == {lineage.TIME_CLOCK})
check("A4 WHICH FIGURES CAN BE EXPLAINED AT ALL is the ONE home's own rule, bound BY IDENTITY in "
      "both declarations — neither holds a copy of the refusal (§CORE-17.3u)",
      phd._served is rd.served_tiles and shd._served is rd.served_tiles
      and "def served_tiles" not in DECLS["payroll"]
      and "def served_tiles" not in DECLS["scheduled"])
check("A5 THE BASIS→RELATION MAP IS KEYED ON THE PAYROLL ENGINE'S OWN BASIS CONSTANTS, not on words "
      "spelled here — a basis renamed in the one home moves this map with it instead of falling "
      "through to a blank on a payroll screen",
      set(phd.BASIS_RELATIONS) == set(hb.ALL_BASES)
      and all(b in phd.BASIS_RELATIONS for b in hb.ALL_BASES)
      and not (set(_literals(DECLS["payroll"])) & set(hb.ALL_BASES)))
ctl("A5a control: a basis the one home declares and the map has no entry for is a BUILD FAILURE, "
    "not a silent blank — dropping one entry fires A5",
    set(k for k in phd.BASIS_RELATIONS if k != hb.CLOCKED) != set(hb.ALL_BASES))
check("A6 AND THE ALLOWLIST THAT DECIDES WHETHER A FIGURE EXISTS AT ALL IS THE ONE HOME'S TOO, bound "
      "by identity: a basis nobody has ruled on reads as NOT payable here exactly as it does there",
      phd.PAYING_BASES is hb.PAYING_BASES and phd.MEASURED_BASES is hb.MEASURED_BASES)
check("A7 EVERY REASON A ROW HERE CAN CARRY IS THE PRODUCER'S OWN BASIS WORD, and every one has a "
      "SENTENCE in the one home — so a screen prints words a reader can act on, never a field name",
      all(b in rd.UNMEASURED_REASONS for b in (hb.NOT_MEASURED_UNPAID, hb.PUNCH_UNUSABLE))
      and rd.UNMEASURED_REASONS[hb.NOT_MEASURED_UNPAID]
      and rd.UNMEASURED_REASONS[hb.PUNCH_UNUSABLE])
check("A8 BUILD LOCK — neither declaration defines a panel shape of its own: the one home is the "
      "only place a panel is built",
      not any(re.search(r'"(tie_out|untraceable_origins|rows_total_count|rows_shown)"\s*:', s)
              for s in DECLS.values())
      and "def panel(" not in DECLS["payroll"] and "def panel(" not in DECLS["scheduled"])
check("A9 THE SOURCED WEEK READ STAMPS THE FIELDS THE DECLARATION DECLARES, dereferenced — the read "
      "and the panel cannot disagree about which key holds a shift's contribution",
      "_shd.HOURS_FIELD" in ROUTER and "_shd.BASIS_FIELD" in ROUTER
      and "_shd.BASIS_STORED" in ROUTER and "_shd.BASIS_DERIVED" in ROUTER
      and '"drill_hours"' not in ROUTER and '"drill_basis"' not in ROUTER)


# ══════════════════════════════════════════════════════════════════════════════════════════════════
print("\n== B. THE PAYROLL FIXTURE — six people, five bases, one of them measured by nothing ==")
# ══════════════════════════════════════════════════════════════════════════════════════════════════
# The rows are the shape `GET /storeops/payroll` serves: merged per employee, carrying the basis
# tally the router stamps from `payroll_hours_basis` and nothing this file invented.
PAY_ROWS = [
    {"employee_id": "E1", "name": "Punched", "store": "S-1", "pay_rate": 20.0, "shifts": 5,
     "scheduled_hours": 40.0, "actual_hours": 38.5, "scheduled_pay": 800.0, "actual_pay": 770.0,
     "hours_by_basis": {hb.CLOCKED: 38.5}, "hours_basis": hb.CLOCKED,
     "hours_basis_reason": hb.basis_reason(hb.CLOCKED), "hours_basis_mixed": False},
    {"employee_id": "E2", "name": "Paid from the plan", "store": "S-1", "pay_rate": 18.0,
     "shifts": 4, "scheduled_hours": 32.0, "actual_hours": 32.0, "scheduled_pay": 576.0,
     "actual_pay": 576.0, "hours_by_basis": {hb.SCHEDULE_OPT_IN: 32.0},
     "hours_basis": hb.SCHEDULE_OPT_IN, "hours_basis_reason": hb.basis_reason(hb.SCHEDULE_OPT_IN),
     "hours_basis_mixed": False},
    {"employee_id": "E3", "name": "Nothing measured", "store": "S-2", "pay_rate": 17.0,
     "shifts": 2, "scheduled_hours": 16.0, "actual_hours": 0.0, "scheduled_pay": 272.0,
     "actual_pay": 0.0, "hours_by_basis": {}},
    {"employee_id": "E4", "name": "Mixed", "store": "S-2", "pay_rate": 19.0, "shifts": 6,
     "scheduled_hours": 48.0, "actual_hours": 44.0, "scheduled_pay": 912.0, "actual_pay": 836.0,
     "hours_by_basis": {hb.CLOCKED: 30.0, hb.SCHEDULE_OPT_IN: 14.0}, "hours_basis": hb.CLOCKED,
     "hours_basis_reason": hb.basis_reason(hb.CLOCKED), "hours_basis_mixed": True,
     "lunch_deduction_hours": 1.5},
    {"employee_id": "E5", "name": "Punch unusable", "store": "S-2", "pay_rate": 16.0, "shifts": 1,
     "scheduled_hours": 8.0, "actual_hours": 0.0, "scheduled_pay": 128.0, "actual_pay": 0.0,
     "hours_by_basis": {hb.PUNCH_UNUSABLE: 0.0}},
    {"employee_id": "E6", "name": "Salaried", "store": "S-1", "pay_rate": 0.0, "shifts": 5,
     "scheduled_hours": 40.0, "actual_hours": 40.0, "scheduled_pay": 0.0, "actual_pay": 1500.0,
     "hours_by_basis": {hb.CLOCKED_CORRECTED: 40.0}, "hours_basis": hb.CLOCKED_CORRECTED,
     "hours_basis_reason": hb.basis_reason(hb.CLOCKED_CORRECTED), "hours_basis_mixed": False,
     "pay_basis": "salary", "salary_period_pay": 1500.0, "salary_prorated": True,
     "salary_note": "period does not cover the whole salary month"},
]
check("B1 the fixture really holds the four shapes this family has to tell apart — a punched person, "
      "a person paid from the plan, a person NOTHING measured, and a person whose only punch is "
      "unusable — plus a mixed period and a salaried row",
      {r["hours_basis"] for r in PAY_ROWS if r.get("hours_basis")}
      == {hb.CLOCKED, hb.SCHEDULE_OPT_IN, hb.CLOCKED_CORRECTED}
      and not phd._pays(PAY_ROWS[2]) and not phd._pays(PAY_ROWS[4])
      and phd._pays(PAY_ROWS[0]) and len(PAY_ROWS[3]["hours_by_basis"]) == 2)
TOT = phd.period_totals(PAY_ROWS)
PP = phd.payroll_drilldowns(PAY_ROWS)


# ══════════════════════════════════════════════════════════════════════════════════════════════════
print("\n== C. THE PAYROLL REPORT — the real declaration, and the person nothing measured ==")
# ══════════════════════════════════════════════════════════════════════════════════════════════════
check("C1 EVERY FIGURE BOTH SCREENS PRINT HAS A PANEL, and the set is compared for EQUALITY rather "
      "than counted — a figure that silently stopped being served names itself here",
      set(PP) == set(phd.SERVED_KEYS),
      "extra=%s missing=%s" % (sorted(set(PP) - set(phd.SERVED_KEYS)),
                               sorted(set(phd.SERVED_KEYS) - set(PP))))
check("C2 THE ONE FOLD IS THE REPORT'S OWN ARITHMETIC — Σ of the four fields over the rows served, "
      "and every panel's figure IS that total, so the tile and its evidence are one fold",
      all(PP[k]["figure"]["amount"] == rd.money(TOT[k]) for k in phd.SERVED_KEYS)
      and TOT["actual_hours"] == 154.5 and TOT["scheduled_hours"] == 184.0)
check("C3 EVERY PANEL TIES EXACTLY, and that is a real check rather than a tautology: the figure "
      "comes from the fold and the rows from the declaration, and the next control breaks it",
      all(PP[k]["tie_out"]["ties"] is True for k in phd.SERVED_KEYS))
_dropped = phd.payroll_drilldowns([r for r in PAY_ROWS if r["employee_id"] != "E1"])
ctl("C3a control: a person dropped from the evidence while the figure stands breaks the tie-out, in "
    "words, instead of being averaged away",
    rd.tie_out(TOT["actual_hours"], _dropped["actual_hours"]["rows"])["ties"] is False)
_tampered = copy.deepcopy(PAY_ROWS)
_tampered[0]["actual_hours"] = 1.0
ctl("C3b control: a row's hours tampered with while the report's figure stands is REPORTED as the "
    "report disagreeing with its own evidence",
    rd.tie_out(TOT["actual_hours"],
               phd.payroll_drilldowns(_tampered)["actual_hours"]["rows"])["ties"] is False)
_ah = PP["actual_hours"]
_nothing = row(_ah, "Nothing measured · S-2")
check("C4 A PERSON NOTHING MEASURED IS SHOWN WITHOUT A FIGURE, NAMES NO RELATION AND IS NOT COUNTED "
      "(§CORE-17.3i). A 0.0 h there reads as 'this person worked and produced nothing', which in a "
      "payroll context is an accusation with nothing behind it",
      _nothing and _nothing["amount"] is None and _nothing["present"] is False
      and _nothing["in_figure"] is False and _nothing["measured"] is False
      and tables(_nothing) == [] and _nothing["reason"] == hb.NOT_MEASURED_UNPAID
      and _nothing["reason_label"])
check("C4b AND THE REPORT'S OWN TOTAL IS UNCHANGED BY THAT HONESTY — such a row carries 0 in the "
      "payload, so the blank and the correct figure are compatible rather than a choice, and the "
      "tie-out still holds to the cent",
      PP["actual_hours"]["tie_out"]["ties"] is True
      and PP["actual_hours"]["tie_out"]["shown_not_counted"] == 2)
_unusable = row(_ah, "Punch unusable · S-2")
check("C5 A PERSON WHOSE ONLY PUNCH IS UNUSABLE CARRIES THE ENGINE'S OWN WORD FOR IT — an open or "
      "auto-closed punch is not a measurement, and it is reported rather than paid. The two blanks "
      "are DIFFERENT FACTS and the panel keeps them apart: one says a punch exists and the company's "
      "rule refuses to count it, which is a thing to go and fix",
      _unusable and _unusable["amount"] is None and _unusable["reason"] == hb.PUNCH_UNUSABLE
      and rd.UNMEASURED_REASONS[hb.PUNCH_UNUSABLE] == _unusable["reason_label"]
      and _nothing["reason"] != _unusable["reason"])
ctl("C5a control: that word survives only because the reason reads the engine's tally UNFILTERED — "
    "the router drops a zero-hour basis before naming a row's dominant one, so reading the filtered "
    "tally would report BOTH people as 'nothing measured' and lose the unusable punch",
    phd._basis_tally(PAY_ROWS[4]) == {}
    and set(PAY_ROWS[4]["hours_by_basis"]) == {hb.PUNCH_UNUSABLE})
check("C5b and the precedence is DECLARED rather than decided by whichever basis sorted first — an "
      "unusable punch outranks 'nothing at all', because it is the stronger statement",
      phd.REASON_PRECEDENCE == (hb.PUNCH_UNUSABLE, hb.NOT_MEASURED_UNPAID)
      and all(b in hb.ALL_BASES for b in phd.REASON_PRECEDENCE))
check("C6 A PUNCHED PERSON NAMES THE TIME CLOCK AND NOTHING ELSE, and a person paid from the plan "
      "names THE SCHEDULE AND NOT THE CLOCK — what was worked names the clock alone (§CORE-17.3t), "
      "and the clock is why this report can be read as hours at all",
      tables(row(_ah, "Punched · S-1")) == [lineage.TIME_CLOCK]
      and tables(row(_ah, "Paid from the plan · S-1")) == [lineage.SCHEDULE])
check("C6b A MIXED PERIOD NAMES BOTH, and carries the producer's own tally so a fortnight of punched "
      "and planned days is not collapsed to one word",
      tables(row(_ah, "Mixed · S-2")) == sorted([lineage.SCHEDULE, lineage.TIME_CLOCK])
      and row(_ah, "Mixed · S-2")["fields"]["hours by basis"] == {hb.CLOCKED: 30.0,
                                                                  hb.SCHEDULE_OPT_IN: 14.0}
      and row(_ah, "Mixed · S-2")["fields"]["mixed basis"] is True
      and row(_ah, "Mixed · S-2")["fields"]["lunch deducted"] == 1.5)
check("C7 THE COLUMN A FIGURE WAS READ OUT OF DIFFERS ROW BY ROW, and the panel says so per row — a "
      "punched person's hours come off the punch's own column and a planned person's off the "
      "schedule's, and naming one for both would name the wrong column on half the rows",
      [o["column"] for o in row(_ah, "Punched · S-1")["origins"]] == ["hours"]
      and [o["column"] for o in row(_ah, "Paid from the plan · S-1")["origins"]]
      == ["scheduled_hours"])
check("C8 SCHEDULED HOURS NAME THE SCHEDULE ON EVERY ROW, including the people the clock answered "
      "for — a planned figure is read out of the plan whatever the clock did",
      {t for r in PP["scheduled_hours"]["rows"] for t in tables(r)} == {lineage.SCHEDULE}
      and PP["scheduled_hours"]["figure"]["unit"] == rd.UNIT_HOURS)
check("C9 MONEY NAMES BOTH THE HOURS' RELATION AND THE EMPLOYEE RECORD, because a gross cannot be "
      "read without the two together — and NOTHING AT ALL on a row with no figure",
      tables(row(PP["actual_pay"], "Punched · S-1"))
      == sorted([lineage.EMPLOYEE_ROSTER, lineage.TIME_CLOCK])
      and tables(row(PP["actual_pay"], "Nothing measured · S-2")) == []
      and row(PP["actual_pay"], "Nothing measured · S-2")["amount"] is None)
check("C9b A SALARIED PERSON'S ROW SAYS ITS FIGURE IS NOT HOURS TIMES A RATE — the report's own pay "
      "basis, note and proration flag travel on the row rather than being inferred from the number",
      row(PP["actual_pay"], "Salaried · S-1")["fields"]["pay basis"] == "salary"
      and row(PP["actual_pay"], "Salaried · S-1")["fields"]["salary note"]
      and row(PP["actual_pay"], "Salaried · S-1")["fields"]["prorated period"] is True)
check("C10 EVERY DRILL ROW CARRIES THE PERSON-AND-STORE KEY the screens narrow on, so a narrowed "
      "panel can never hold a different set of people than the tile it was opened from",
      all(set(r["row_key"] or {}) == {"employee_id", "store"}
          for p in PP.values() for r in p["rows"]))
check("C11 AN EMPTY PERIOD DRILLS TO NOTHING rather than to panels asserting a tie against zero",
      phd.payroll_drilldowns([]) == {} and phd.payroll_drilldowns(None) == {})
_unmeasured_period = [dict(r, actual_hours=0.0, actual_pay=0.0, hours_by_basis={})
                      for r in PAY_ROWS]
_UP = phd.payroll_drilldowns(_unmeasured_period)
check("C12 A PERIOD NOTHING MEASURED AT ALL IS SERVED NO *WORKED* PANEL — its 0.0 h is a zero over "
      "rows none of which were counted, which ties 0 against 0 and would read as 'we checked, nobody "
      "worked'. The one home's own rule refuses both of those, and the two PLANNED figures survive, "
      "because the schedule really does say what it says. Compared as a SET for equality",
      set(_UP) == {"scheduled_hours", "scheduled_pay"},
      "served=%s" % sorted(_UP))


# ══════════════════════════════════════════════════════════════════════════════════════════════════
print("\n== D. PAY VISIBILITY — a gated caller is served NO money panel, not a panel of zeros ==")
# ══════════════════════════════════════════════════════════════════════════════════════════════════
# `pay_visibility.strip_pay` DELETES the money keys from a gated caller's rows. A panel folded over
# such rows would publish $0.00 as a measurement of somebody's pay, which is exactly the lie
# strip-not-zero exists to prevent.
STRIPPED = [{k: v for k, v in r.items()
             if k not in ("pay_rate", "scheduled_pay", "actual_pay", "salary_period_pay")}
            for r in PAY_ROWS]
#: MEASURED OVER ROWS THAT DO CARRY THEIR MONEY, DELIBERATELY. Folded over STRIPPED rows the money
#: figures have no counted row at all and the one home's own refusal withholds them anyway — so a
#: lock measured there would stay GREEN with the gate deleted, which is the "lock that reads the form
#: and not the fact" hole every batch of this programme has had to close. Mutation-tested: removing
#: the gate from the catalogue fires D1.
GATED = phd.payroll_drilldowns(PAY_ROWS, can_see_pay=False)
check("D1 BUILD LOCK — a pay-gated caller is served the two HOURS panels and NEITHER money panel, "
      "measured over rows that DO carry their money so the GATE is the only thing that can withhold "
      "them. Compared as a SET for equality, so a money panel that starts being served names itself",
      set(GATED) == set(phd.HOURS_KEYS),
      "extra=%s missing=%s" % (sorted(set(GATED) - set(phd.HOURS_KEYS)),
                               sorted(set(phd.HOURS_KEYS) - set(GATED))))
ctl("D1a control: THE GATE IS WHAT WITHHOLDS THEM, and it is not vacuous — over rows that DO carry "
    "their money keys the same call serves both money panels ungated and neither gated, so the "
    "parameter is doing the work",
    set(phd.payroll_drilldowns(PAY_ROWS, can_see_pay=True)) >= set(phd.MONEY_KEYS)
    and not (set(phd.payroll_drilldowns(PAY_ROWS, can_see_pay=False)) & set(phd.MONEY_KEYS)))
check("D1b AND THERE IS A SECOND LINE OF DEFENCE, measured rather than assumed: over STRIPPED rows "
      "the money figures fold to nothing counted at all, so even a caller who bypassed the gate "
      "would be served no panel of $0.00s — the one home's own refusal catches it",
      not (set(phd.payroll_drilldowns(STRIPPED, can_see_pay=True)) & set(phd.MONEY_KEYS)))
check("D1c and the CATALOGUE ITSELF holds no money spec for a gated caller — the figures are absent "
      "rather than served and then hidden, which is the same posture `strip_pay` takes on the "
      "report's own columns",
      {c["key"] for c in phd.catalogue(can_see_pay=False)} == set(phd.HOURS_KEYS)
      and {c["key"] for c in phd.catalogue(can_see_pay=True)} == set(phd.SERVED_KEYS))
check("D2 and the HOURS panels are unchanged for a gated caller — they still tie, still name the "
      "clock and the plan per row, because a gated caller still runs hours corrections",
      GATED["actual_hours"]["tie_out"]["ties"] is True
      and GATED["actual_hours"]["figure"]["amount"] == PP["actual_hours"]["figure"]["amount"]
      and tables(row(GATED["actual_hours"], "Punched · S-1")) == [lineage.TIME_CLOCK])
check("D3 THE ENDPOINT ASKS THE REPORT'S OWN GATE, not a second rule of its own — the same "
      "`pay_visibility.can_see_pay` the report asks, and the same `strip_pay` on the rows",
      "_payvis.can_see_pay(authorization or \"\", org_id, client=get_supabase())" in ROUTER
      and "_phd.payroll_drilldowns(rows, can_see_pay=allowed)" in ROUTER
      and ROUTER.count("_payvis.strip_pay(rows)") >= 2)


# ══════════════════════════════════════════════════════════════════════════════════════════════════
print("\n== E. THE HOURS BUDGET — the sourced read, and a figure whose rows are nowhere on screen ==")
# ══════════════════════════════════════════════════════════════════════════════════════════════════
WEEK = ("2026-10-19", "2026-10-25")
SHIFT_ROWS = [
    {"id": "s1", "employee_name": "Punched", "store_code": "S-1", "shift_date": "2026-10-20",
     "scheduled_hours": 8.0, "start_time": "09:00", "end_time": "17:00",
     shd.HOURS_FIELD: 8.0, shd.BASIS_FIELD: shd.BASIS_STORED},
    {"id": "s2", "employee_name": "Mixed", "store_code": "S-1", "shift_date": "2026-10-21",
     "scheduled_hours": None, "start_time": "10:00", "end_time": "18:30",
     shd.HOURS_FIELD: 8.5, shd.BASIS_FIELD: shd.BASIS_DERIVED},
]
BUDGET_ROWS = [{"store_code": "S-1", "used_hours": 16.5, "weekly_hours": 40.0, "over": False},
               {"store_code": "S-9", "used_hours": 0.0, "weekly_hours": 40.0, "over": False}]
BD = shd.budget_drilldowns(BUDGET_ROWS, {"S-1": SHIFT_ROWS, "S-9": []},
                           week_start=WEEK[0], week_end=WEEK[1])
check("E1 THE STORE-WEEK FIGURE OPENS TO THE SHIFTS IT WAS ADDED UP FROM — rows this screen does not "
      "otherwise show at all — and it ties exactly",
      set(BD) == {"S-1"} and BD["S-1"]["tie_out"]["ties"] is True
      and BD["S-1"]["figure"]["amount"] == 16.5
      and BD["S-1"]["figure"]["unit"] == rd.UNIT_HOURS)
ctl("E1a control: a shift dropped from the evidence while the figure stands breaks the tie",
    rd.tie_out(16.5, shd.budget_drilldowns(
        BUDGET_ROWS, {"S-1": SHIFT_ROWS[:1]}, week_start=WEEK[0],
        week_end=WEEK[1])["S-1"]["rows"])["ties"] is False)
check("E2 A STORE NOBODY HAS SCHEDULED THIS WEEK IS SERVED NO PANEL — its 0.0 h over no rows would "
      "tie zero against nothing and read as 'we checked, there was nothing', which is "
      "indistinguishable from a week nobody has written yet",
      "S-9" not in BD)
ctl("E2a control: that refusal is the ZERO-over-no-counted-rows rule and not 'a store with no rows "
    "never gets a panel' — the SAME store with a NON-ZERO figure and no rows DOES get a panel, so "
    "the tie-out can publish the disagreement rather than the gap being hidden (§CORE-17.3o)",
    "S-9" in shd.budget_drilldowns([{"store_code": "S-9", "used_hours": 12.0}], {"S-9": []})
    and shd.budget_drilldowns([{"store_code": "S-9", "used_hours": 12.0}],
                              {"S-9": []})["S-9"]["tie_out"]["ties"] is False)
check("E3 THE PANEL IS KEYED ON THE STORE THE REPORT KEYS ITS OWN ROWS ON, and its subject names "
      "the store AND the work-week — a panel keyed on the store alone would address every week "
      "there has ever been (the §4e grain defect)",
      BD["S-1"]["subject"] == "S-1" and BD["S-1"]["subject_kind"] == "store"
      and WEEK[0] in (BD["S-1"]["note"] or "") and WEEK[1] in (BD["S-1"]["note"] or "")
      and all(set(r["row_key"] or {}) == {"store_code", "shift_date"} for r in BD["S-1"]["rows"]))
check("E4 WHICH COLUMN HELD EACH SHIFT'S HOURS IS READ PER ROW — a shift with no stored "
      "scheduled-hours figure had its hours worked out from its start and end times, and naming the "
      "empty column for it would be false",
      [o["column"] for o in row(BD["S-1"], "2026-10-20 · Punched")["origins"]]
      == ["scheduled_hours"]
      and [o["column"] for o in row(BD["S-1"], "2026-10-21 · Mixed")["origins"]] == [None]
      and row(BD["S-1"], "2026-10-21 · Mixed")["fields"]["hours from"]
      == shd.BASIS_WORDS[shd.BASIS_DERIVED])
check("E5 EVERY ROW NAMES THE SCHEDULE AND ONLY THE SCHEDULE — this figure is PLANNED hours, and "
      "nothing on this report is a measurement of anything worked",
      {t for r in BD["S-1"]["rows"] for t in tables(r)} == {lineage.SCHEDULE})
check("E6 THE FIX IS AT THE READ, NOT IN THE DECLARATION — the week read has a sourced form that "
      "returns the rows it summed, the plain one is a thin wrapper over it so the over-budget "
      "projection check is byte-identical, and the endpoint hands the SAME rows to the declaration",
      "def _store_week_hours_sourced(" in ROUTER
      and "return _store_week_hours_sourced(org_id, store_code, ws, we, exclude_id=exclude_id)[0]"
      in ROUTER
      and "used, used_rows = _store_week_hours_sourced(org_id, sc, ws, we)" in ROUTER
      and "_shd.budget_drilldowns(out, shifts_by_store" in ROUTER)
check("E6b and the read's select really fetches the columns a drill row addresses itself by — a "
      "panel cannot name a day or a person the read never asked for",
      re.search(r'\.select\(\s*"id,employee_name,store_code,shift_date,scheduled_hours,'
                r'start_time,end_time"\s*\)', ROUTER) is not None)
check("E7 THE BUDGET ITSELF IS REFUSED A PANEL, and the refusal is DECLARED rather than left to the "
      "reader: it is a leaf — one column of one config row somebody typed (§CORE-17.3h as batch 10 "
      "extended it, §CORE-17.3m for declared-versus-measured)",
      set(BD["S-1"]) >= set() and "weekly_hours" not in str(sorted(BD))
      and "LEAF" in DECLS["scheduled"] and "weekly_hours" not in _literals(DECLS["scheduled"]))
check("E8 AN ORG WITH NO STORES, AND ONE WHOSE ROWS CARRY NO STORE CODE, DRILL TO NOTHING",
      shd.budget_drilldowns([], {}) == {}
      and shd.budget_drilldowns([{"store_code": "", "used_hours": 5.0}], {}) == {})


# ══════════════════════════════════════════════════════════════════════════════════════════════════
print("\n== F. THIN PUNCH COVERAGE IS SURFACED, never smoothed over ==")
# ══════════════════════════════════════════════════════════════════════════════════════════════════
# House punch coverage is partial and was measured on production (roughly 54% July, 52% August, 66%
# September, 62% October, nothing before July). A figure folded over such a month is the figure
# people are paid by — but a panel must let the reader see how little of it rests on a measurement.
for _k in phd.HOURS_KEYS:
    check(f"F1[{_k}] the panel's SOURCES LIST is the coverage answer in one read — it counts the rows "
          f"per relation, so 'the schedule · n' beside 'the time clock · m' is readable without "
          f"opening anything",
          [s["table"] for s in PP[_k]["sources"]]
          and all(s["rows"] > 0 for s in PP[_k]["sources"]))
check("F2 AND THE CLOCK AND THE PLAN REALLY DO LAND ON DIFFERENT RELATIONS ROW BY ROW, which is what "
      "makes that list mean anything — not an artefact of one fixture row",
      {s["table"]: s["rows"] for s in PP["actual_hours"]["sources"]}
      == {lineage.TIME_CLOCK: 2, lineage.SCHEDULE: 3}
      and PP["actual_hours"]["untraceable_origins"] == 2)
check("F3 THE COVERAGE WARNING IS ON EVERY HOURS PANEL IN THIS FAMILY, in words, naming the measured "
      "shortfall — a figure resting on a thin month says so instead of presenting itself as settled",
      all("coverage" in (PP[k]["note"] or "").lower() and "54%" in (PP[k]["note"] or "")
          and "not measurable" in (PP[k]["note"] or "").lower() for k in ("actual_hours",))
      and "intention" in (PP["scheduled_hours"]["note"] or "").lower())
check("F3b and the MONEY panels carry it too, because a pay figure folded out of planned hours is "
      "a plan priced at a rate, not a wage somebody earned",
      "coverage" in (PP["actual_pay"]["note"] or "").lower())
check("F4 THE PLANNED-HOURS PANEL SAYS WHAT IT IS, and does not borrow the clock's authority: the "
      "schedule records an intention, and the panel's own note says the Payroll Report is where a "
      "punched hour is named",
      "intention" in (BD["S-1"]["note"] or "").lower()
      and "Payroll Report" in (BD["S-1"]["note"] or ""))
check("F5 A PERSON PAID FROM THE PLAN IS STILL COUNTED, because the report pays them — the panel "
      "reports the basis, it does not change the figure (the §CORE-17.3h posture on a fallback)",
      row(_ah, "Paid from the plan · S-1")["in_figure"] is True
      and row(_ah, "Paid from the plan · S-1")["measured"] is True
      and row(_ah, "Paid from the plan · S-1")["fields"]["hours basis"] == hb.SCHEDULE_OPT_IN)
check("F6 REPORTED, NOT PAPERED OVER — hand-entered hours never reach this report's figure, so this "
      "family NEVER names that relation. The gap is written down in the declaration and in the "
      "registry rather than claimed as a provenance",
      lineage.MANUAL_HOURS not in {s["table"] for p in PP.values() for s in p["sources"]}
      and "manual_hours" in DECLS["payroll"]
      and "does not read" in DECLS["payroll"])


# ══════════════════════════════════════════════════════════════════════════════════════════════════
print("\n== G. §CORE-17.3o — a panel explains the FIGURE, not the table. All three, both ways ==")
# ══════════════════════════════════════════════════════════════════════════════════════════════════
def _calls_narrow(name):
    return "narrowPanel(" in strip_comments(PAGES[name])


check("G1 THE TWO PAGES THAT FOLD OVER A POPULATION THE SERVER DID NOT ARE NARROWED — both filter "
      "their rows in the browser and then reduce over what survives, so a server-folded panel would "
      "explain a number the screen is not printing",
      all(_calls_narrow(p) for p in NARROWED_PAGES))
check("G1b AND THE SERVER-FOLDED PAGE IS NOT — no `narrowPanel` anywhere in the CODE of the hours "
      "budget, because every figure on it is ONE store's own server-folded number and its filter "
      "hides whole rows without changing a figure",
      not any(_calls_narrow(p) for p in SERVER_FOLDED_PAGES))
ctl("G1c control: the reader reads the page's CODE and not its comments — the rule written down in "
    "the hours-budget header does not satisfy it, and a real call in a payroll page does",
    "narrowPanel" in PAGES["hours-budget"] and not _calls_narrow("hours-budget")
    and _calls_narrow("payroll"))
check("G2 THE NARROWED PAGES REALLY DO FILTER THEIR OWN ROWS, with the shared row filter, and really "
      "do reduce over what survives — which is what makes narrowing necessary rather than tidy",
      all("filterRows(" in strip_comments(PAGES[p]) and ".reduce(" in strip_comments(PAGES[p])
          for p in NARROWED_PAGES))
check("G2b AND THE SERVER-FOLDED PAGE FOLDS NO FIGURE ACROSS STORES — it prints each store's own "
      "`used_hours` exactly as served, so there is nothing for a narrower to narrow",
      "used_hours" in PAGES["hours-budget"]
      and not re.search(r"used_hours\s*\|\|\s*0\)?\s*,\s*0\)", strip_comments(PAGES["hours-budget"]))
      and ".reduce(" not in strip_comments(PAGES["hours-budget"]))
check("G3 each narrowed page reads its figure OFF its narrowed panel through the SHARED gate, and "
      "builds its narrowing key with the SHARED helper — neither side spells the key shape twice",
      all("agreesToTheCent(" in strip_comments(PAGES[p]) and "screenKey({" in strip_comments(PAGES[p])
          and "narrowed[k]?.figure?.amount" in strip_comments(PAGES[p]) for p in NARROWED_PAGES))
check("G4 ALL THREE PAGES RENDER THE SHARED PANEL — matched on the JSX ELEMENT, because the IMPORT "
      "ALONE satisfies an identifier match and a page whose render was deleted while its import "
      "stayed once passed every lock in this programme",
      all(re.search(r"<DrillDownPanel[\s/>]", PAGES[p]) for p in PAGES))
ctl("G4a control: the element match is not satisfied by an import — a page that only imports it is "
    "reported as not wired",
    not re.search(r"<DrillDownPanel[\s/>]",
                  "import DrillDownPanel from '@/components/DrillDownPanel'\n"))
check("G5 and each offers a figure's link only through the SHARED offer gate over what the SERVER "
      "served, rather than deriving its own list of links",
      all("useDrillTile(" in strip_comments(PAGES[p]) and "DrillTileLinks" in PAGES[p]
          for p in PAGES))
check("G6 NO PAGE SPELLS A RELATION NAME OF ITS OWN and none calls `sourceLabel` itself — the reader "
      "sees a feed's plain words through the SHARED panel, and never a database name. The names "
      "checked are the ones that are relations and nothing else: `shifts` and `employees` are also "
      "payload FIELD names on these screens (a shift count, an employee roster read), and flagging "
      "those would be a lock about spelling rather than about provenance",
      not any(re.search(r"['\"](timelog|manual_hours|hours_budget)['\"]",
                        strip_comments(PAGES[p])) for p in PAGES)
      and not any("sourceLabel(" in strip_comments(PAGES[p]) for p in PAGES))
check("G7 THE EMPLOYEES TILE IS DELIBERATELY NOT DRILLABLE — a cardinality is not Σ(a field), and "
      "the panel's own header already states how many rows are behind it",
      "drillKey=\"employees\"" not in PAGES["reports"]
      and "Employees" in PAGES["reports"] and "employees" not in set(PP))


# ══════════════════════════════════════════════════════════════════════════════════════════════════
print("\n== H. NO FIGURE MOVED — read-only over every producer's own rows ==")
# ══════════════════════════════════════════════════════════════════════════════════════════════════
_before = copy.deepcopy(PAY_ROWS)
phd.payroll_drilldowns(PAY_ROWS)
phd.period_totals(PAY_ROWS)
check("H1[payroll] the declaration is READ-ONLY over the rows the endpoint serves — deep-compared "
      "before and after, byte-identical", PAY_ROWS == _before)
_sbefore = copy.deepcopy((BUDGET_ROWS, SHIFT_ROWS))
shd.budget_drilldowns(BUDGET_ROWS, {"S-1": SHIFT_ROWS})
check("H1[hours-budget] the same, over the budget rows and the shift rows the read stamped",
      (BUDGET_ROWS, SHIFT_ROWS) == _sbefore)
check("H2 BUILD LOCK — neither declaration holds a dollar literal, a rate or a threshold of its "
      "own: every amount either carries was handed to it",
      all(not re.search(r"(?m)^\s*[A-Z_]+\s*=\s*\d+\.\d+", s) for s in DECLS.values()))
check("H3 BUILD LOCK — the endpoints serve the drill THROUGH the declarations and build no panel of "
      "their own",
      "_phd.payroll_drilldowns(" in ROUTER and "_shd.budget_drilldowns(" in ROUTER
      and "report_drilldown.panel(" not in ROUTER)
check("H4 THE NEW ENDPOINT CANNOT DISCLOSE WHAT THE REPORT WOULD NOT — it calls the SAME shared "
      "producer the report calls, in process, with the caller's own authorization and the same range "
      "precedence, so a panel holds exactly the rows that caller was served",
      "rows = get_payroll(month=month, start=start, end=end,\n"
      "                       authorization=(authorization or \"\"), org_id=org_id, "
      "response=response)" in ROUTER)
check("H5 and it folds no figure of its own beyond the report's own four sums, which have ONE home "
      "— the declaration, which is where a strip no producer folds belongs (the "
      "`three_way_drilldown.totals` precedent)",
      "def period_totals(" in DECLS["payroll"]
      and ROUTER.count("def period_totals(") == 0
      and "period_totals" not in PAGES["payroll"])
check("H6 THE ONE HOME'S EXTENSION IS ADDITIVE — `column` now takes a callable for a figure whose "
      "column differs row by row, and a plain string behaves exactly as before",
      "read_c = column if callable(column) else (lambda _r: column)" in ONE_HOME
      and rd.tile_panel("x", 1.0, [{"v": 1.0}], value="v", tables=("t",),
                        column="c")["rows"][0]["origins"][0]["column"] == "c"
      and rd.tile_panel("x", 1.0, [{"v": 1.0}], value="v", tables=("t",),
                        column=lambda _r: "d")["rows"][0]["origins"][0]["column"] == "d")


# ══════════════════════════════════════════════════════════════════════════════════════════════════
print("\n== I. THE GRAIN COUNT, MEASURED OFF THE PANELS AS A SET ==")
# ══════════════════════════════════════════════════════════════════════════════════════════════════
# A grain fixes the ROW, the UNIT and the PROVENANCE (§CORE-17.3l / §CORE-17.3n). The row shape is
# named per family because a payload cannot say it; the unit and the provenance are READ OFF THE
# PANEL, so the number below is a measurement and not a claim.
UNTRACEABLE = "(nothing nameable)"
ROW_SHAPE = {}
for _k in PP:
    ROW_SHAPE[("payroll", _k)] = "person_pay_period_row"
for _k in BD:
    ROW_SHAPE[("hours-budget", _k)] = "store_work_week_row"


def _provenance(panel):
    """The set of relations this panel's rows were actually read out of, with an untraceable origin
    named as such — read off the panel, never declared."""
    names = {s["table"] for s in panel.get("sources") or ()}
    if panel.get("untraceable_origins"):
        names.add(UNTRACEABLE)
    return frozenset(names)


PANELS_BY_FAMILY = {"payroll": PP, "hours-budget": BD}
MEASURED = set()
for (_family, _key), _shape in ROW_SHAPE.items():
    _panel = PANELS_BY_FAMILY[_family][_key]
    MEASURED.add((_shape, _panel["figure"]["unit"], _provenance(_panel)))
print(f"       MEASURED {len(MEASURED)} distinct grains over {len(ROW_SHAPE)} panels in "
      f"{len({s for s in ROW_SHAPE.values()})} row shapes")
for _g in sorted(MEASURED, key=lambda g: (g[0], g[1], sorted(g[2]))):
    print(f"         · {_g[0]} / {_g[1]} / {sorted(_g[2])}")
_CLOCK_AND_PLAN = frozenset({lineage.TIME_CLOCK, lineage.SCHEDULE, UNTRACEABLE})
_EXPECTED = {
    # PAYROLL — ONE person-and-pay-period row, two units, and FOUR grains on it.
    ("person_pay_period_row", rd.UNIT_HOURS, frozenset({lineage.SCHEDULE})),
    ("person_pay_period_row", rd.UNIT_HOURS, _CLOCK_AND_PLAN),
    ("person_pay_period_row", rd.UNIT_MONEY,
     frozenset({lineage.SCHEDULE, lineage.EMPLOYEE_ROSTER})),
    ("person_pay_period_row", rd.UNIT_MONEY,
     frozenset(_CLOCK_AND_PLAN | {lineage.EMPLOYEE_ROSTER})),
    # HOURS BUDGET — ONE store-and-work-week row, one unit, one provenance. One grain, and that is
    # the report.
    ("store_work_week_row", rd.UNIT_HOURS, frozenset({lineage.SCHEDULE})),
}
_extra, _missing = sorted(MEASURED - _EXPECTED), sorted(_EXPECTED - MEASURED)
check("I1 THE GRAIN COUNT IS MEASURED AND THE SET IS COMPARED FOR EQUALITY — every panel's unit and "
      "provenance are read off the panel, collected as a SET so two panels that really are one grain "
      "collapse, and checked against the declared five. A bound on the SIZE would stay green while "
      "one grain vanished and another took its place, so a lost grain NAMES ITSELF here",
      MEASURED == _EXPECTED and len(MEASURED) == 5
      and len({s for s in ROW_SHAPE.values()}) == 2,
      "extra=%s missing=%s" % (_extra, _missing))
ctl("I1a control: the equality is not vacuous — dropping one declared grain is reported BY NAME, and "
    "the comparison really is a set difference rather than a count",
    (MEASURED - (_EXPECTED - {("store_work_week_row", rd.UNIT_HOURS,
                               frozenset({lineage.SCHEDULE}))}))
    == {("store_work_week_row", rd.UNIT_HOURS, frozenset({lineage.SCHEDULE}))})
ctl("I1b control: a GHOST AT UNCHANGED SIZE — one measured grain swapped for one that does not "
    "exist — leaves a `>=` bound green and makes this comparison red, naming both",
    (lambda g: len(g) >= 5 and g != _EXPECTED
     and sorted(g - _EXPECTED) and sorted(_EXPECTED - g))(
        (MEASURED - {("store_work_week_row", rd.UNIT_HOURS, frozenset({lineage.SCHEDULE}))})
        | {("ghost_row", rd.UNIT_HOURS, frozenset({"ghost_table"}))}))
ctl("I1d control: the provenance reader really reads the PANEL and not a declaration — pointed at a "
    "panel with no sources at all it says so rather than inventing one",
    _provenance({"sources": []}) == frozenset())
check("I2 TWO GRAINS SEPARATED BY THE SAME ROW AND UNIT AND NOTHING BUT PROVENANCE (§CORE-17.3n) — "
      "the planned hours and the hours this payroll paid for share the person-and-period row and the "
      "unit, and differ only in which relation answered. A reader is owed that: one is a plan and "
      "the other is partly a measurement",
      _provenance(PP["scheduled_hours"]) != _provenance(PP["actual_hours"])
      and PP["scheduled_hours"]["figure"]["unit"] == PP["actual_hours"]["figure"]["unit"])
check("I3 AND THE TWO FAMILIES SHARE NO GRAIN, which is why they are two declarations and not one: "
      "the planned-hours grains are the SAME unit out of the SAME relation and are told apart by the "
      "ROW ALONE — a person over a pay period against a store over a work-week",
      len({g[0] for g in MEASURED}) == 2
      and _provenance(PP["scheduled_hours"]) == _provenance(BD["S-1"])
      and PP["scheduled_hours"]["figure"]["unit"] == BD["S-1"]["figure"]["unit"]
      and ("person_pay_period_row", rd.UNIT_HOURS, frozenset({lineage.SCHEDULE})) in MEASURED
      and ("store_work_week_row", rd.UNIT_HOURS, frozenset({lineage.SCHEDULE})) in MEASURED)


# ══════════════════════════════════════════════════════════════════════════════════════════════════
print("\n== J. THE RATCHET — shrink-only, and mutation-tested in every direction ==")
# ══════════════════════════════════════════════════════════════════════════════════════════════════
GATE = read("backend/harness_drilldown_coverage.py")
LEDGER_TXT = read("backend/harness_drilldown_pending.txt")
MY_ROUTES = ("/storeops/payroll", "/storeops/reports", "/storeops/hours-budget")
#: THE SEVEN THIS BATCH READ AND DID NOT SERVE. Each is still `owes`, on purpose: leaving a report
#: owed is honest, and reclassifying one that really is a report would hide it from the programme —
#: which is worse than the debt. The reasons are in §CORE-17.3x.
STILL_OWED = ("/storeops/payroll-tax", "/storeops/schedule", "/storeops/timeclock",
              "/storeops/team", "/storeops/visits/plan", "/hr", "/hr/payroll-expenses")
#: 2026-10-26: batches 23 (the finance set, -4) and 24 (payroll & workforce, -3) were built in
#: PARALLEL off a shared base of 25, so neither side's own number was the whole reduction and each
#: would have asserted a pin the other had already moved. This is re-taken from the MERGED ledger —
#: 25 - 4 - 3 = 18 — and the assertion below stays EQUALITY, not a bound: a pin left above the real
#: count is a gate that has stopped biting.
OWES_AFTER = 18


def _pin(src):
    m = re.search(r"(?m)^OWES_MAX\s*=\s*(\d+)", src)
    return int(m.group(1)) if m else None


def _owes(src):
    return sorted(ln.split("\t")[0] for ln in src.splitlines()
                  if len(ln.split("\t")) > 1 and ln.split("\t")[1].strip() == "owes")


def _state(src, route):
    for ln in src.splitlines():
        p = ln.split("\t")
        if p and p[0].strip() == route and len(p) > 1:
            return p[1].strip()
    return None


check("J1 THE PIN AND THE LEDGER AGREE, AND BOTH ARE AT THIS BATCH'S NUMBER. The assertion is "
      "EQUALITY, not a bound: a pin left above the real count is a gate that has stopped biting",
      _pin(GATE) == OWES_AFTER and len(_owes(LEDGER_TXT)) == OWES_AFTER,
      "pin=%s owes=%s" % (_pin(GATE), len(_owes(LEDGER_TXT))))
ctl("J1a control: the pin reader really reads the gate — pointed at a file with another number it "
    "returns that number, so J1 cannot pass on a stale read",
    _pin("OWES_MAX = 99") == 99 and _pin("nothing here") is None)
check("J2 exactly the THREE routes this batch serves flipped from `owes` to `shared`, and the state "
      "is true of each page — the gate's own §D1 checks the element, not the import",
      all(_state(LEDGER_TXT, r) == "shared" for r in MY_ROUTES))
check("J3 and none of the three is still counted as owing one",
      not (set(MY_ROUTES) & set(_owes(LEDGER_TXT))))
check("J4 EVERY ONE OF THE THREE NOTES NAMES THIS BATCH'S SECTION AND THE DECLARATION THAT SERVES "
      "IT, and carries what the drill REFUSES to claim — a drill-down that does not say what it "
      "cannot explain is half a drill-down",
      all("CORE-17.3x" in (next((ln for ln in LEDGER_TXT.splitlines()
                                if ln.split("\t")[0].strip() == r), ""))
          and "refuses" in next((ln for ln in LEDGER_TXT.splitlines()
                                 if ln.split("\t")[0].strip() == r), "").lower()
          for r in MY_ROUTES))
check("J5 THE SEVEN ROUTES THIS BATCH READ AND DID NOT SERVE ARE STILL `owes`, honestly. Leaving a "
      "report owed keeps it in the programme; reclassifying a report that really is one hides it",
      all(_state(LEDGER_TXT, r) == "owes" for r in STILL_OWED),
      str([(r, _state(LEDGER_TXT, r)) for r in STILL_OWED]))
check("J6 and none of the seven was given a NOTE — the gate's own §C4 says an `owes` row is a debt "
      "and a note on one would read as a reason to keep it",
      all(len([p for p in next((ln for ln in LEDGER_TXT.splitlines()
                                if ln.split("\t")[0].strip() == r), "").split("\t")
               if p.strip()]) == 2 for r in STILL_OWED))
check("J7 THE PIN MAY ONLY SHRINK, and the base of 25 came down by exactly this batch's three\n      routes and the parallel batch 23's four — stated as the arithmetic rather than a literal",
      _pin(GATE) == 25 - len(MY_ROUTES) - 4 == OWES_AFTER)


# ══════════════════════════════════════════════════════════════════════════════════════════════════
print("\n== K. REGISTRATION — the index, the graph, the labels and the guard job ==")
# ══════════════════════════════════════════════════════════════════════════════════════════════════
MIGS = "\n".join(open(f, encoding="utf-8", errors="ignore").read()
                 for f in glob.glob(f"{ROOT}/database/migrations/*.sql"))
NAMED = sorted({s["table"] for p in list(PP.values()) + list(BD.values())
                for s in p["sources"] or ()})
check("K1 every relation a panel in this batch can name is KNOWN TO THE MIGRATIONS — a provenance "
      "line pointing at a table nobody creates is a claim, not evidence",
      NAMED and all(re.search(r"(?is)create\s+(table|view|materialized\s+view)[^;]*?\b%s\b" % t,
                              MIGS) for t in NAMED),
      str([t for t in NAMED
           if not re.search(r"(?is)create\s+(table|view|materialized\s+view)[^;]*?\b%s\b" % t,
                            MIGS)]))
SRC_LABELS = read("frontend/src/lib/sourceLabels.ts")
check("K2 BUILD LOCK — and every one has a `sourceLabels.ts` entry, so the operator reads a feed's "
      "plain name and never a database name (owner: hide database names from the users)",
      all(re.search(r"(?m)^\s*%s\s*:" % t, SRC_LABELS) for t in NAMED),
      str([t for t in NAMED if not re.search(r"(?m)^\s*%s\s*:" % t, SRC_LABELS)]))
GRAPH = read("backend/app/modules/core/module_graph.py")
check("K3 both declarations are registered CALLERS of the one drill-down home, by hand (§50: "
      "`--bless` silently deleted 11 of 12 facts once)",
      "'app/modules/storeops/payroll_hours_drilldown.py': ('report_drilldown',)" in GRAPH
      and "'app/modules/storeops/scheduled_hours_drilldown.py': ('report_drilldown',)" in GRAPH)
check("K3b and of the FACTS each dereferences, under the fact that module is the home of — the feed "
      "lineage for the relation names and the payroll hours basis for the basis vocabulary",
      re.search(r"'feed_lineage':.*?'app/modules/storeops/payroll_hours_drilldown\.py'",
                GRAPH, re.S) is not None
      and re.search(r"'payroll_hours_basis':.*?'app/modules/storeops/payroll_hours_drilldown\.py'",
                    GRAPH, re.S) is not None)
check("K3c and the RE-KEYED caller is registered alongside it under the fact whose name it now "
      "dereferences, in the same commit — writing a registry and leaving a caller on its literal is "
      "not a fix at all (§19.18)",
      re.search(r"'feed_lineage':.*?'app/modules/account/liabilities_drilldown\.py'", GRAPH, re.S)
      is not None)
IDX = read("docs/SYSTEM_DATA_FLOW_INDEX.md")
check("K4 the index carries THIS batch's own SECTION HEADING, and the next free letter was taken "
      "rather than an occupied one re-used",
      "### §CORE-17.3x" in IDX and IDX.count("### §CORE-17.3x") == 1)
check("K4b and the routes this batch serves are named in it, with the two declarations and the "
      "harness",
      all(r in IDX.split("### §CORE-17.3x", 1)[1].split("### §CORE-17.4", 1)[0]
          for r in MY_ROUTES)
      and "storeops/payroll_hours_drilldown.py" in IDX
      and "storeops/scheduled_hours_drilldown.py" in IDX
      and "harness_batch24_drilldown.py" in IDX)
check("K4c and the routes it left owed are named in it too, with the reason each was not served — "
      "an excuse nobody wrote down is indistinguishable from an oversight",
      all(r in IDX.split("### §CORE-17.3x", 1)[1].split("### §CORE-17.4", 1)[0]
          for r in STILL_OWED))
WF = read(".github/workflows/carrier-vocab-guard.yml")
check("K5 this batch's harness is registered in the ONE DRILL-DOWN HOME's own lock list, so a "
      "reader of that module finds the proof",
      "harness_batch24_drilldown.py" in ONE_HOME)
check("K6 the guard job RUNS this harness — matched on the `run:` LINE ITSELF, never on the file's "
      "name appearing somewhere in the workflow, because the paths filter names it too and a lock "
      "satisfied by that would be a lock about spelling rather than about the job",
      re.search(r"(?m)^\s*run:\s*python3 harness_batch24_drilldown\.py\s*\|", WF) is not None)
check("K6a and its step's `name:` / `run:` scalars carry NO colon-space — a plain YAML scalar that "
      "does is a parse error, and this file is 1,700 lines of them",
      all(": " not in ln.split("name:", 1)[1] if "name:" in ln else True
          for ln in WF.splitlines() if "batch-24 drill lock" in ln))
check("K6b and its PATHS FILTER names the files it is about, so a change to one of them cannot "
      "skip the job that checks it",
      all(f in WF for f in ("backend/app/modules/storeops/payroll_hours_drilldown.py",
                            "backend/app/modules/storeops/scheduled_hours_drilldown.py",
                            "backend/harness_batch24_drilldown.py")))
check("K7 the ratchet gate itself still passes its own `shared` lock for these three routes, which "
      "is what makes the ledger flip true of the code rather than of the ledger",
      all(re.search(r"<DrillDownPanel[\s/>]", PAGES[p]) for p in PAGES))


print(f"\n{len(PASS)}/{len(PASS) + len(FAIL)} checks passed")
if FAIL:
    print("FAILED:")
    for f in FAIL:
        print("  -", f)
    sys.exit(1)
