"""Proof + LOCK — a manager's salary follows the stores they COVER, not the store they stood in
(index §STOREOPS-11).

OWNER 2026-11-16, verbatim: *"brendas salary is attached to diversey nbut her salary should be
divided among the store she is handling"*. The even split is his OWN earlier directive, 2026-09-09:
*"if th market manager is 78000 and he handles all markets then his salary will be 78000/ the number
of stores in each market"* — so this harness never asks which arithmetic; it asserts that one.

WHAT WENT WRONG. `payroll_salary.allocate_across_stores` spread a salaried person's pay over the
stores they personally WORKED, and at zero hours put 100% on their `home_store`. Right for a rep,
who is paid for being in one store. Wrong for a manager, who is paid to cover several and may clock
in at none: measured live (LuxeLink, October 2026) one market manager's whole $5,280.23 booked to a
single store, and four other salaried people landed the same way in the same month.

THE DEFECT CLASS THIS HARNESS EXISTS TO STOP, measured before it could ship. The coverage ladder's
rung 2 reads `employees.org_unit_id`. That column says where a person is PLACED, not what they are
RESPONSIBLE for. Live, one tenant has THIRTEEN employees carrying one and almost all are rank and
file — three of them salaried STORE staff ($45,000.00, $44,999.80, $44,999.78) placed under the same
market node as the market manager. Read placement as coverage and ~$135,000 a year of ordinary store
pay spreads over ten stores those people never worked at, while their own store loses its own
employee's salary. So the pay path asks the ladder with `seed_from_placement=False`: only an explicit
`storeops.org_managers` assignment moves a salary that already lands on a store.

  §A  the rule itself — coverage beats hours, the split is even, and it foots to the cent
  §B  what it must NOT change — one covered store, no coverage, the 'Unassigned' sentinel
  §C  it REFUSES rather than guesses — a spread that does not foot is not used
  §D  ONE HOME — no coverage resolution and no second spread in `payroll_salary`
  §E  THE LADDER's two callers, and the assignment-only posture of the pay path
  §F  THE INTERLOCK — the two paths that spread a salary are disjoint
  §G  CROSS-TENANT (owner 2026-10-30) — one org's assignments never reach another's read

DB-free, pure-stdlib. Run: python3 backend/harness_manager_coverage_allocation.py (exit 0 = holds)
"""
import ast
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from app.modules.storeops import payroll_salary as ps          # noqa: E402
from app.modules.storeops import overhead_allocation as oh     # noqa: E402
from harnesslib import py_code_only                            # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
FAIL = 0
RAN = 0


def check(name, cond, detail=None):
    global FAIL, RAN
    RAN += 1
    print(("  PASS  " if cond else "  FAIL  ") + name + ("" if cond or detail is None else f"  — {detail}"))
    if not cond:
        FAIL += 1


def src(rel):
    with open(os.path.join(HERE, rel)) as f:
        return f.read()


PS_SRC = src("app/modules/storeops/payroll_salary.py")
OH_SRC = src("app/modules/storeops/overhead_allocation.py")
ROUTER = src("app/modules/storeops/router.py")

TEN = ["Armitage", "Belmont", "Cermark", "Chicago heights", "Cicero",
       "Diversey", "Grand", "lawrence", "s3352", "s3735"]

# ── §A  THE RULE ────────────────────────────────────────────────────────────────────────────────
print("§A  coverage beats worked hours, the split is even, and it foots to the cent")

a = ps.allocate_across_stores({}, 5280.23, "Diversey", covered=TEN)
check("a zero-hours manager's pay spreads over every covered store",
      sorted(a) == sorted(TEN), sorted(a))
check("the split sums EXACTLY to the derived pay",
      round(sum(a.values()), 2) == 5280.23, round(sum(a.values()), 2))
check("it is an EVEN split (owner 2026-09-09), within the residue cent",
      max(a.values()) - min(a.values()) <= 0.03, sorted(a.values()))
check("the home store keeps only its own even share, not the whole salary",
      a["Diversey"] <= 530.0, a["Diversey"])

b = ps.allocate_across_stores({"Grand": 160.0}, 5280.23, "Diversey", covered=TEN)
check("WORKED HOURS ARE IGNORED for a covered person — a manager is not paid for standing there",
      b == a, {k: v for k, v in b.items() if a.get(k) != v})

c = ps.allocate_across_stores({}, 1.0, "Diversey", covered=TEN)
check("a salary smaller than one cent per store still foots",
      round(sum(c.values()), 2) == 1.0, c)
d = ps.allocate_across_stores({}, 0.0, "Diversey", covered=TEN)
check("nothing earned spreads nothing (and never invents a cell)", d == {"Diversey": 0.0}, d)

e = ps.allocate_across_stores({}, 78000.0 / 12, "Grand", covered=TEN)
check("the owner's own example: his monthly pay / the number of stores he handles",
      round(sorted(e.values())[0], 2) == 650.0, sorted(e.values())[:1])

# ── §B  WHAT IT MUST NOT CHANGE ─────────────────────────────────────────────────────────────────
print("§B  everyone the org assigned nothing keeps today's rule, to the cent")

HRS = {"Cicero": 115.04, "Diversey": 17.74, "Grand": 79.5}
check("no covered set ⇒ the hours split, unchanged",
      ps.allocate_across_stores(HRS, 3832.42, "Diversey")
      == ps.allocate_across_stores(HRS, 3832.42, "Diversey", covered=None))
check("an EMPTY covered set is not a coverage claim",
      ps.allocate_across_stores(HRS, 3832.42, "Diversey", covered=[])
      == ps.allocate_across_stores(HRS, 3832.42, "Diversey"))
one = ps.allocate_across_stores({}, 4428.57, "Belmont", covered=["Belmont"])
check("ONE covered store is not rung 1 — indistinguishable from an ordinary store employee",
      one == {"Belmont": 4428.57}, one)
check("blank-and-blank still lands in the visible sentinel, never nowhere",
      ps.allocate_across_stores({}, 6642.86, None, covered=[])
      == {ps.UNASSIGNED_STORE: 6642.86})
check("a covered set of blanks is no covered set",
      ps.allocate_across_stores({}, 100.0, "Grand", covered=["", "   ", None])
      == {"Grand": 100.0})
check("duplicate covered codes are one store, not two shares",
      len(ps.allocate_across_stores({}, 100.0, "Grand", covered=["A", "A", "B"])) == 2)

# ── §C  IT REFUSES RATHER THAN GUESSES ──────────────────────────────────────────────────────────
print("§C  an injected spread that does not foot is NOT used — the pay still books")

short = ps.allocate_across_stores(HRS, 3832.42, "Diversey", covered=TEN,
                                  spread=lambda amt, codes: {codes[0]: amt - 100.0})
check("a spread that loses money is refused and the hours rule stands",
      short == ps.allocate_across_stores(HRS, 3832.42, "Diversey"), short)
over = ps.allocate_across_stores(HRS, 3832.42, "Diversey", covered=TEN,
                                 spread=lambda amt, codes: {c: amt for c in codes})
check("a spread that invents money is refused too",
      over == ps.allocate_across_stores(HRS, 3832.42, "Diversey"))
empty = ps.allocate_across_stores(HRS, 3832.42, "Diversey", covered=TEN,
                                  spread=lambda amt, codes: {})
check("a spread that answers nothing falls back rather than dropping the salary",
      empty == ps.allocate_across_stores(HRS, 3832.42, "Diversey"))
raised = ps.allocate_across_stores(HRS, 3832.42, "Diversey", covered=TEN,
                                   spread=lambda amt, codes: 1 / 0)
check("a spread that RAISES never takes the pay with it",
      round(sum(raised.values()), 2) == 3832.42, raised)
inj = ps.allocate_across_stores({}, 900.0, "Grand", covered=["A", "B", "C"],
                                spread=lambda amt, codes: {"A": 450.0, "B": 300.0, "C": 150.0})
check("a declared basis IS honoured when it foots (this module decides no basis)",
      inj == {"A": 450.0, "B": 300.0, "C": 150.0}, inj)

# ── §D  ONE HOME ────────────────────────────────────────────────────────────────────────────────
print("§D  one home — the pure module resolves no coverage and spreads no second way")

PS_CODE = py_code_only(PS_SRC)
for forbidden in ("org_managers", "org_unit_id", "org_span_for_manager", "subtree",
                  "classify_employee", "market_by_code"):
    check(f"`payroll_salary` holds no coverage resolution of its own ({forbidden})",
          forbidden not in PS_CODE)
check("`payroll_salary` reaches no database", "schema(" not in PS_CODE and "import" in PS_CODE)
check("the covered set and the spread are both INJECTED arguments",
      "covered=" in PS_CODE and "spread=" in PS_CODE)
check("`covered_stores` is still defined exactly once in the backend",
      sum(py_code_only(src(os.path.join("app/modules/storeops", f))).count("def covered_stores(")
          for f in os.listdir(os.path.join(HERE, "app/modules/storeops")) if f.endswith(".py")) == 1)
check("`coverage_for` is defined exactly once",
      PS_CODE.count("def coverage_for(") == 0 and OH_SRC.count("def coverage_for(") == 1)
tree = ast.parse(PS_SRC)
fn = next(n for n in ast.walk(tree)
          if isinstance(n, ast.FunctionDef) and n.name == "allocate_across_stores")
args = [a.arg for a in fn.args.args]
check("the rule's signature names the covered set and the injected spread",
      "covered" in args and "spread" in args, args)

# ── §E  THE LADDER'S TWO CALLERS ────────────────────────────────────────────────────────────────
print("§E  one ladder, two callers, and the pay path takes assignments only")

ohtree = ast.parse(OH_SRC)
cov = next(n for n in ast.walk(ohtree)
           if isinstance(n, ast.FunctionDef) and n.name == "coverage_for")
check("`coverage_for` takes the posture as a named argument, never a module flag",
      "seed_from_placement" in [a.arg for a in cov.args.kwonlyargs])
check("`gather` keeps its documented posture (placement is its only evidence)",
      "seed_from_placement=True" in py_code_only(OH_SRC))
RCODE = py_code_only(ROUTER)
check("the PAY path asks with seed_from_placement=False — a placement is not a responsibility",
      "seed_from_placement=False" in RCODE)
check("the pay path never asks with placement seeding", "seed_from_placement=True" not in RCODE)
check("`gather` no longer resolves the tree itself — it dereferences the context",
      "org_coverage_context(" in py_code_only(OH_SRC).split("def gather(")[-1])

# A salaried STORE employee placed under a market node: the measured defect class.
CTX = {"ok": True,
       "stores": [{"store_code": c, "org_unit_id": "u-mkt"} for c in TEN],
       "org_codes": list(TEN), "market_by_code": {}, "codes_by_market": {},
       "unit_by_id": {"u-mkt": {"id": "u-mkt", "name": "Mkt", "code": "mkt:x"}},
       "managed_by_emp": {"MGR": ["u-mkt"]},
       "subtree": (lambda uid: {"u-mkt"} if uid == "u-mkt" else set())}
CFG = oh.resolve_config(None)
staff = {"employee_id": "STAFF", "home_store": "Grand", "org_unit_id": "u-mkt"}
codes, how = oh.coverage_for(CTX, staff, CFG, seed_from_placement=False)
check("a salaried store employee PLACED under a market node covers NOTHING",
      codes == [] and how == oh.COVER_NONE, (codes, how))
pcodes, phow = oh.coverage_for(CTX, staff, CFG, seed_from_placement=True)
check("...while the unattached-person path still reads that placement (unchanged)",
      len(pcodes) == len(TEN), (len(pcodes), phow))
mcodes, mhow = oh.coverage_for(CTX, {"employee_id": "MGR", "home_store": "Grand"}, CFG,
                               seed_from_placement=False)
check("an ASSIGNED manager covers the stores beneath the unit they were assigned",
      sorted(mcodes) == sorted(TEN), (len(mcodes), mhow))
check("no employee id, no coverage",
      oh.coverage_for(CTX, {"home_store": "Grand"}, CFG, seed_from_placement=False)
      == ([], oh.COVER_NONE))
check("an empty context answers NONE rather than guessing",
      oh.coverage_for({"ok": True}, {"employee_id": "MGR"}, CFG, seed_from_placement=False)
      == ([], oh.COVER_NONE))

# ── §F  THE INTERLOCK ───────────────────────────────────────────────────────────────────────────
print("§F  the two paths that spread a salary are disjoint, or one salary books twice")

call = RCODE[RCODE.index("_covered_by_emp: dict"):] if "_covered_by_emp: dict" in RCODE else ""
check("the pay path reads the org's live overhead mode",
      'mode") == "derive"' in call or "mode') == 'derive'" in call)
check("the disjointness test DEREFERENCES classify_employee, never a second copy",
      "classify_employee(" in call)
check("the covered set and the spread both reach the rule",
      "covered_by_emp=" in call and "spread=" in call)
check("the spread is the one home, bound to the tenant's declared basis",
      ".allocate(" in call)
check("`labour_cost` is still the one door onto the producer (§STOREOPS-10 unchanged)",
      "get_payroll_by_store" in src("app/modules/storeops/labour_cost.py"))
# A person overhead_allocation WOULD spread is attached to no store, so this is the shape that must
# never be spread twice: salaried, active, blank home_store, and an assignment.
unattached = {"employee_id": "MGR", "home_store": "", "is_active": True,
              "pay_basis": "annual", "pay_amount": 78000.0}
is_oh, why, _sal = oh.classify_employee(unattached, oh.resolve_config({"mode": "derive"}),
                                         lambda b, a: 6500.0)
check("the overhead population is exactly the people whose pay lands nowhere",
      is_oh and why == oh.WHY_UNATTACHED, (is_oh, why))
attached = dict(unattached, home_store="Diversey")
check("...and a store-attached manager is NOT in it, so the pay path owns them alone",
      oh.classify_employee(attached, oh.resolve_config({"mode": "derive"}),
                           lambda b, a: 6500.0)[0] is False)

# ── §G  CROSS-TENANT ────────────────────────────────────────────────────────────────────────────
print("§G  cross-tenant — one org's assignments can never answer another's question")

OTHER = {"ok": True, "stores": [{"store_code": "Z-1", "org_unit_id": "v-mkt"}],
         "org_codes": ["Z-1"], "market_by_code": {}, "codes_by_market": {},
         "unit_by_id": {"v-mkt": {"id": "v-mkt", "name": "V", "code": "mkt:v"}},
         "managed_by_emp": {"MGR": ["v-mkt"]},
         "subtree": (lambda uid: {"v-mkt"} if uid == "v-mkt" else set())}
oc, _ = oh.coverage_for(OTHER, {"employee_id": "MGR"}, CFG, seed_from_placement=False)
check("the SAME employee id resolves to each org's OWN stores", oc == ["Z-1"], oc)
check("...and the first org's answer is unchanged by the second read",
      sorted(oh.coverage_for(CTX, {"employee_id": "MGR"}, CFG,
                             seed_from_placement=False)[0]) == sorted(TEN))
nomgr, nohow = oh.coverage_for(OTHER, {"employee_id": "STAFF", "org_unit_id": "v-mkt"}, CFG,
                               seed_from_placement=False)
check("an org that has assigned nobody spreads nobody — the inert default",
      nomgr == [] and nohow == oh.COVER_NONE)
check("the covered set is never a store of an org that does not hold it",
      all(c in OTHER["org_codes"] for c in oc))
# RULE TWO reads CODE, not evidence: both modules carry measured comments that name the tenant the
# figure came from, and a guard a truthful comment can break is a guard that gets deleted.
_CODE = (PS_CODE + py_code_only(OH_SRC)).lower()
check("no tenant word reaches the CODE of either module (RULE TWO)",
      "luxelink" not in _CODE and "cellfonz" not in _CODE and "vzone" not in _CODE)
check("no live store or market name is a code literal in the pay rule",
      all(w not in PS_CODE.lower() for w in ("diversey", "narragansett", "cermark", "chicago")))

print()
print(f"checks run: {RAN}   failures: {FAIL}")
print("ALL PROOFS HOLD" if FAIL == 0 else "PROOFS FAILED")
sys.exit(1 if FAIL else 0)
