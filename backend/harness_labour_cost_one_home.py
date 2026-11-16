"""Proof + LOCK — one home for "what did this store's labour cost this month" (index §STOREOPS-10 /
§ACCOUNT-05).

Owner report 2026-11-16, verbatim: *"The payroll for luxelink is being calculated properly but it is
not reflecting per store expenses in dashboard, gross profit or p&l, they should all be updated from
here and it is quick fix but hasnt been happening, if the agent cannot do it assign somebody senior
to fix the platform wide issue not for just this tenant"*.

WHAT WENT WRONG, measured live against production on the day (October 2026, every tenant):

    tenant          Payroll Report   what the books carried
    ──────────────  ──────────────   ──────────────────────
    Cellfonz R Us      $72,905.30    $0.00   (a refusal nothing else honoured)
    LuxeLink          $121,632.66    $116,826.26
    Vzone              $20,449.79    $20,010.00
    NY LOGISTICS       $20,160.28    $18,328.79

EVERY store of EVERY tenant disagreed, because two derivations answered one question and each knew
something the other did not — the Payroll Report applied the salaried pay-basis override, the lunch
auto-deduction and an inactive employee's real activity; `coa.derive_wage_cells` read the
per-employee `pay_from_schedule` tick and the clock-evidence verdict.

WHAT THIS HARNESS FAILS THE BUILD FOR
  §A  the pure shaping is wrong (fold, key spaces, the unattributed bucket);
  §B  the "no store" sentinel is RE-SPELLED instead of dereferenced from `payroll_salary`;
  §C  a shifts x rate wage derivation REAPPEARS in the finance module;
  §D  `coa._wage_measurement` stops dereferencing `storeops/labour_cost`;
  §E  the producer stops ordering its rows, so the same month answers two figures;
  §F  the pay path stops carrying the employee row (the per-rep `pay_from_schedule` tick).

DB-free, pure-stdlib. Run: python3 backend/harness_labour_cost_one_home.py  (exit 0 = proofs hold)
"""
import ast
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from app.modules.storeops import labour_cost as lc            # noqa: E402
from app.modules.storeops import payroll_salary as ps         # noqa: E402
from app.modules.storeops import payroll_evidence as _pe      # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
FAIL = 0


def check(name, cond, detail=None):
    global FAIL
    print(("  PASS  " if cond else "  FAIL  ") + name + ("" if cond or detail is None else f"  — {detail}"))
    if not cond:
        FAIL += 1


def src(rel):
    with open(os.path.join(HERE, rel)) as f:
        return f.read()


COA = src("app/modules/account/coa.py")
ROUTER = src("app/modules/storeops/router.py")
LCSRC = src("app/modules/storeops/labour_cost.py")

# ── §A  THE PURE SHAPING ────────────────────────────────────────────────────────────────────────
print("§A  the pure shaping — one producer answer, two key spaces")
ROWS = [{"store_code": "QV", "hours": 10.0, "amount": 150.0},
        {"store_code": "QV", "hours": 2.0, "amount": 30.0},       # same store twice: summed
        {"store_code": "Grand", "hours": 8.0, "amount": 160.0},
        {"store_code": ps.UNASSIGNED_STORE, "hours": 0.0, "amount": 6500.0},
        {"store_code": "", "hours": 0.0, "amount": 100.0}]
cells = lc.fold_rows(ROWS)
check("two rows for one store SUM, never overwrite",
      cells["QV"] == {"hours": 12.0, "amount": 180.0}, cells.get("QV"))
check("the unattributed bucket and the empty code fold TOGETHER, under ''",
      cells[""]["amount"] == 6600.0, cells.get(""))
check("code space keys by the shift's own code", lc.cells_by_code(cells)["Grand"] == 160.0)

CODE2ADDR = {"qv": "218-80 Hempstead Avenue", "grand": "3966 W Grand Ave"}
fold = lambda s: (str(s or "").strip().lower()) or None      # noqa: E731
addr = lc.cells_by_address(cells, CODE2ADDR, norm=fold)
check("address space maps declared codes to their canonical address",
      addr["218-80 Hempstead Avenue"] == 180.0 and addr["3966 W Grand Ave"] == 160.0, addr)
check("labour attributable to NO store keys None — company-wide, never a store called 'Unassigned'",
      addr[None] == 6600.0 and ps.UNASSIGNED_STORE not in addr
      and ps.UNASSIGNED_STORE.lower() not in addr, addr)
check("a code the map does not know KEEPS ITS OWN SPELLING — the money stays visible",
      lc.cells_by_address({"Nowhere": {"amount": 42.0}}, CODE2ADDR, norm=fold) == {"nowhere": 42.0})
check("the sum is preserved across both key spaces",
      round(sum(lc.cells_by_code(cells).values()), 2) == round(sum(addr.values()), 2) == 6940.0)
check("an empty producer answer is an empty dict, never a zero cell",
      lc.fold_rows([]) == {} and lc.cells_by_address({}, CODE2ADDR) == {})
check("the door shapes whatever the producer returns and derives nothing of its own",
      lc.per_store("ORG", month="2026-10",
                   producer=lambda **kw: {"stores": ROWS})["Grand"]["amount"] == 160.0)
check("the door hands the producer the period AND the caller's own database handle",
      (lambda seen: (lc.per_store("ORG", start="2026-10-01", end="2026-10-31", so="HANDLE",
                                  producer=lambda **kw: seen.update(kw) or {"stores": []}), seen)[1]
       )({}) == {"month": None, "start": "2026-10-01", "end": "2026-10-31",
                 "org_id": "ORG", "so": "HANDLE", "authorization": ""})
check("the door asks the producer for the WHOLE org, never the caller's own store keyset — a "
      "statement narrowed to whoever triggered the compute would understate the books in silence",
      'authorization=""' in LCSRC)

# ── §B  THE SENTINEL IS DEREFERENCED, NOT RE-SPELLED ────────────────────────────────────────────
print("§B  the 'no store' sentinel has ONE spelling")
check("`payroll_salary.UNASSIGNED_STORE` is the one home for it", ps.UNASSIGNED_STORE == "Unassigned")
def _executable(source):
    """The module's CODE with every docstring and comment removed — prose may NAME a literal the
    code must not SPELL, and a lock that cannot tell them apart punishes the explanation."""
    t = ast.parse(source)
    for node in ast.walk(t):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            body = getattr(node, "body", None)
            if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant) \
                    and isinstance(body[0].value.value, str):
                body.pop(0)
    return ast.unparse(t)


LC_CODE = _executable(LCSRC)
check("labour_cost dereferences it and spells no literal of its own",
      "UNASSIGNED_STORE" in LC_CODE and "Unassigned" not in LC_CODE,
      [l for l in LC_CODE.splitlines() if "Unassigned" in l][:2])
check("the finance module spells no copy of it either",
      "Unassigned" not in _executable(COA))
check("`is_unattributed` answers for BOTH spellings and is case-blind",
      lc.is_unattributed("") and lc.is_unattributed(None) and lc.is_unattributed("  ")
      and lc.is_unattributed("Unassigned") and lc.is_unattributed("unassigned")
      and not lc.is_unattributed("QV"))

# ── §C  NO SECOND DERIVATION IN FINANCE ─────────────────────────────────────────────────────────
print("§C  the finance module derives no wage of its own")
tree = ast.parse(COA)
names = {n.name for n in ast.walk(tree) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))}
check("`derive_wage_cells` / `derive_wage_cells_with_basis` are GONE, not renamed",
      not any("derive_wage_cell" in n or "wage_cells" in n for n in names), sorted(names & {
          n for n in names if "wage" in n}))
code_only = "\n".join(l for l in COA.splitlines() if not l.lstrip().startswith("#"))
check("no `pay_rate` arithmetic anywhere in the statement builder",
      "pay_rate" not in code_only, [l for l in code_only.splitlines() if "pay_rate" in l][:2])
check("no local `actual_hours or scheduled_hours` hours rule either",
      "scheduled_hours" not in code_only or "resolve_shift_row" not in code_only)
check("`monthly_salary_equivalent` STAYS — a conversion is not a measurement, and "
      "storeops/overhead_allocation dereferences it", "monthly_salary_equivalent" in names)

# ── §D  THE DEREFERENCE IS WIRED ────────────────────────────────────────────────────────────────
print("§D  the books read the one home")
wm = COA[COA.index("def _wage_measurement("):COA.index("def store_code_to_address(")]
check("`_wage_measurement` calls `labour_cost.per_store`", "_labour_cost.per_store(" in wm)
check("…and shapes it through `labour_cost`, never a key rule of its own",
      wm.count("_labour_cost.cells_by_address(") == 2 and "for code" not in wm)
check("…and hands the producer ITS OWN storeops handle, so one database answers",
      "so=so" in wm)
# OWNER RULING 2026-11-16 — "Book it — report the coverage". The figure BOOKS and the coverage is
# STATED; withholding the line published $0.00 of payroll beside a report showing the money.
check("the clock-evidence verdict is still CONSULTED — a caller that stops asking reads as one that "
      "never asked, and the lock cannot tell the difference", "_payevid.refusal(" in wm)
check("…and it no longer WITHHOLDS the line: the figure books",
      "raise _payevid.EvidenceRefused(" not in wm and "raise" not in wm)
check("…the coverage sentence is carried back to the caller, from its ONE home",
      "_payevid.coverage_sentence(" in wm and "verdict.update(" in wm)
check("the statement PRINTS it rather than writing a sentence of its own",
      '_wage_verdict["note"]' in COA and "_wage_refusal" not in COA)
check("the coverage sentence is `payroll_evidence`'s, and states the gap as missing clock data",
      "missing clock data" in _pe.coverage_sentence(
          {"coverage": {"scheduled_days": 510, "measured_days": 321, "pct": 62.94}})
      and "62.94%" in _pe.coverage_sentence(
          {"coverage": {"scheduled_days": 510, "measured_days": 321, "pct": 62.94}}))
check("a fully measured period says nothing — a note nobody needs is noise",
      _pe.coverage_sentence({"coverage": {"scheduled_days": 10, "measured_days": 10, "pct": 100.0}}) is None
      and _pe.coverage_sentence({}) is None)
check("`refusal` is UNCHANGED and still answers for a path that is about to PAY",
      _pe.refusal({"refused": True, "reason": "r"}) == "r" and _pe.refusal({"refused": False, "reason": "r"}) is None)
check("both key spaces still have exactly one entry point",
      "def wages_by_store(" in COA and "def wages_by_code(" in COA
      and COA.count("_wage_measurement(client, org_id, period") == 3)

# ── §E  THE SAME MONTH ANSWERS ONE FIGURE ───────────────────────────────────────────────────────
print("§E  the producer is reproducible")
prod = ROUTER[ROUTER.index("def get_payroll_by_store("):ROUTER.index("@router.get(\"/payroll-by-store\")")]
check("the shift rows are ORDERED before money is accumulated into floats",
      "shifts = sorted(" in prod, "live: $6,083.83 / $6,083.84 at `Cicero` on alternate runs")
check("the punch rows are ordered too", "tl = sorted(" in prod)
check("a punch read that FAILS does not delete the wage line — the schedule still pays and the "
      "failure is reported, never swallowed",
      "_punch_read_failed" in prod and prod.count("_punch_read_failed") >= 3
      and 'out["punch_read_failed"]' in prod)
check("the salary override walks the roster in a fixed order",
      "sorted(salaried.items()" in src("app/modules/storeops/payroll_salary.py"))
check("the per-store allocation breaks ties on the store key, not on row order",
      "(-kv[1], str(kv[0]))" in src("app/modules/storeops/payroll_salary.py"))
check("allocation is exact to the cent and reproducible across orderings",
      ps.allocate_across_stores({"A": 1.0, "B": 1.0, "C": 1.0}, 100.0, None)
      == ps.allocate_across_stores({"C": 1.0, "B": 1.0, "A": 1.0}, 100.0, None)
      and round(sum(ps.allocate_across_stores({"A": 1.0, "B": 1.0, "C": 1.0}, 100.0, None).values()), 2) == 100.0)
check("no worked hours and no home store lands in the ONE sentinel bucket",
      ps.allocate_across_stores({}, 6500.0, None) == {ps.UNASSIGNED_STORE: 6500.0})

# ── §F  THE PER-REP TICK REACHES THE PAY PATH ───────────────────────────────────────────────────
print("§F  the per-employee `pay_from_schedule` tick reaches the figure (mig 1073)")
check("`_shift_actual_contribution` takes an employee row and hands it to the one home",
      "def _shift_actual_contribution(s, manual_days, punch_days, hours_cfg=None, employee_row=None)" in ROUTER
      and "resolve_shift_row(s, cfg, employee_row, cell, d)" in ROUTER)
check("…and so does the basis-carrying twin",
      "def _shift_actual_contribution_with_basis(s, manual_days, punch_days, hours_cfg=None, employee_row=None)" in ROUTER)
check("the store-grain producer passes the row (it passed None, so only the org default applied)",
      "_shift_actual_contribution(s, manual_days, punch_days, _hb_cfg, emp_by_id.get(eid))" in ROUTER)
check("the employee-grain report passes it too, so the two grains cannot disagree",
      "_shift_actual_contribution_with_basis(s, manual_days, punch_days, _hb_cfg, emp)" in ROUTER)
check("the roster read PROJECTS the column, else the tick could never be seen",
      "_hours_basis.CHANGE_FIELD" in ROUTER)
check("no pay-path call resolves a shift row with a hardcoded None employee",
      "resolve_shift_row(s, cfg, None," not in ROUTER)

print()
if FAIL:
    print(f"{FAIL} proof(s) FAILED")
    sys.exit(1)
print("ALL PROOFS HOLD — one home for per-store labour cost; the books read the Payroll Report's figure")
