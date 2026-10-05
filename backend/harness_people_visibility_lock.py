"""BUILD LOCK — "whose shifts may this login READ?" stays ONE fact that every schedule read
dereferences (owner directive 2026-10-05, index §14w; the design-fix clause "lock it so it cannot
un-wire").

Stdlib only, no DB, no imports of the application. The behavioural PROOF is
`harness_people_visibility.py` (31 checks over the real endpoints); this file guards the SHAPE.

It fails the build when:
  1. The ruling moves or is copied — `people_visibility` / `visible_people_keyset` must be
     defined in `app/core/scope.py` and nowhere else.
  2. The wiring is copied — `schedule_emp_ids` must be defined in `storeops/router.py` and nowhere
     else, and must dereference `core.scope.visible_people_keyset` rather than re-deriving a span.
  3. A wired read stops calling it — every endpoint in WIRED below must still reference
     `schedule_emp_ids` inside its own handler body.
  4. A NEW person-keyed schedule/attendance read lands unwired — any GET handler in
     `storeops/router.py` that reads `shifts`, `shift_templates`, `time_off_requests`,
     `shift_swap_requests` or `timelog` must reference `schedule_emp_ids`, or be named in EXCUSED
     with a reason. A silent sibling is the duplicate defect this whole package exists to stop.
  5. The market-grant refusal is undone — `schedule_emp_ids` must feed the ruling
     `_caller_org_unit_codes` (the org tree ALONE), never `_caller_span_codes`, which unions the
     login's market pin and is precisely what put the whole company on a rep's schedule.

Run: `cd backend && python3 harness_people_visibility_lock.py`
"""
import os
import re
import sys

PASS, FAIL = [], []


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print(("PASS" if cond else "FAIL") + f"  {name}" + (f"  [{detail}]" if detail and not cond else ""))


HERE = os.path.dirname(os.path.abspath(__file__))
RULING_HOME = "app/core/scope.py"
WIRING_HOME = "app/modules/storeops/router.py"

# The reads bound to the ruling. A route listed here may not quietly stop asking.
WIRED = ("/shifts", "/schedule/hours-trend", "/shift-templates", "/time-off", "/shift-swaps",
         "/timeclock/list")

# A person-keyed table read that is deliberately NOT person-gated, each with the reason it is safe.
EXCUSED = {
    "/staffing-heatmap": "store-level AGGREGATE, no names in the payload — gated by the STORE keyset",
    "/timeclock/status": "token identity: already reads ONLY the signed-in employee's own punch",
    "/payroll": "pay surface — gated by pay_visibility + scope_emp_ids (§14 pay RBAC), not schedule",
    "/payroll-by-store": "store-level labour aggregate — pay_visibility + store keyset",
    "/payroll-change-log": "audit trail — pay_visibility + scope_emp_ids",
    "/accountability": "manager-only board; its own org-chain gate (§13)",
    "/salary-owed": "pay surface — pay_visibility gate",
    "/google-reviews/my": "token identity: the caller's OWN reviews only",
}

PERSON_TABLES = ("shifts", "shift_templates", "time_off_requests", "shift_swap_requests", "timelog")

# A wired read asks through the ONE gate helper, or through the ruling's wiring point directly (the
# punch list does, because it owns an id-form reconciliation the helper must not duplicate).
GATE = re.compile(r"gate_person_rows|schedule_emp_ids")


def read(rel):
    with open(os.path.join(HERE, rel), encoding="utf-8") as fh:
        return fh.read()


def code_of(src):
    """`src` with comment lines and docstrings removed — crude, and enough for a literal scan.

    Every check that asserts a name is ABSENT must run on this, not on the raw source: these
    functions carry long explanations that name the very mechanisms they no longer use (that is
    the point of the explanation), and three checks have now been tripped by their own prose.
    """
    body = "\n".join(l for l in src.split("\n") if not l.lstrip().startswith("#"))
    body = re.sub(r'"""[\s\S]*?"""', "", body)
    return re.sub(r"'''[\\s\\S]*?'''", "", body)


def py_files():
    for root, dirs, files in os.walk(os.path.join(HERE, "app")):
        dirs[:] = [d for d in dirs if d != "__pycache__"]
        for f in files:
            if f.endswith(".py"):
                yield os.path.relpath(os.path.join(root, f), HERE).replace(os.sep, "/")


# ── 1. the ruling has ONE home ───────────────────────────────────────────────────────────────────
for fn in ("people_visibility", "visible_people_keyset", "visible_store_codes",
           "worked_store_codes"):
    homes = [p for p in py_files() if re.search(rf"^def {fn}\(", read(p), re.M)]
    check(f"1. `{fn}` is defined ONLY in {RULING_HOME}", homes == [RULING_HOME], str(homes))

# ── 2. the wiring has ONE home and dereferences the ruling ───────────────────────────────────────
wiring_homes = [p for p in py_files() if re.search(r"^def schedule_emp_ids\(", read(p), re.M)]
check(f"2a. `schedule_emp_ids` is defined ONLY in {WIRING_HOME}",
      wiring_homes == [WIRING_HOME], str(wiring_homes))

router = read(WIRING_HOME)
m = re.search(r"^def schedule_emp_ids\(.*?(?=^def |\Z)", router, re.M | re.S)
body = m.group(0) if m else ""
check("2b. it dereferences core.scope.visible_people_keyset (no second derivation)",
      "visible_people_keyset" in body)
callers = sum(len(re.findall(r"visible_people_keyset\(", read(p)))
              for p in py_files() if p != RULING_HOME)
check("2c. nothing else re-derives the ruling — visible_people_keyset has exactly one call site",
      callers == 1, str(callers))

# ── 5. the market-grant refusal stands ───────────────────────────────────────────────────────────
check("5a. it feeds the ORG TREE alone (_caller_org_unit_codes)", "_caller_org_unit_codes" in body)
check("5b. and NOT _caller_span_codes, which unions the login's market pin",
      "_caller_span_codes" not in body, body)
scope_src = read(RULING_HOME)
ruling = re.search(r"^def visible_people_keyset\(.*?(?=^def |\Z)", scope_src, re.M | re.S)
ruling = ruling.group(0) if ruling else ""
store_ruling = re.search(r"^def visible_store_codes\(.*?(?=^def |\Z)", scope_src, re.M | re.S)
store_ruling = store_ruling.group(0) if store_ruling else ""
check("5c. the market grant is read in ONE place — inside the store ruling's market/region branch "
      "— and nowhere else in either ruling",
      store_ruling.count("login_grant_codes") == 1
      and re.search(r'scope in \("market", "region", "regional"\)[\s\S]{0,240}login_grant_codes',
                    store_ruling) is not None
      and "login_grant_codes" not in ruling,
      f"store={store_ruling.count('login_grant_codes')} people={ruling.count('login_grant_codes')}")
ruling_code = code_of(ruling)
check("5d. the PEOPLE ruling does not re-derive the store dimension — it dereferences "
      "visible_store_codes, so a manager's people list and their report cannot disagree about the "
      "same person (owner 2026-10-05)",
      "visible_store_codes" in ruling_code and "self_store_codes" not in ruling_code,
      [l for l in ruling_code.split("\n") if "self_store_codes" in l])
check("5e. and it no longer reads employees.home_store, which the owner ruled out as an answer to "
      "'whose performance may I see' — the parameter stays for its callers but is unused",
      not re.search(r"employee_home_store\s*=\s*employee_home_store", ruling_code),
      [l for l in ruling_code.split("\n") if "employee_home_store" in l])

# ── 3 & 4. every person-keyed GET either asks, or is excused ─────────────────────────────────────
lines = router.split("\n")
routes = []
for i, l in enumerate(lines):
    mm = re.match(r'@router\.(get|post|put|patch|delete)\("([^"]+)"', l.strip())
    if mm:
        routes.append((i, mm.group(1), mm.group(2)))


def handler_src(start):
    """The decorated function's OWN body — ends at the next top-level `def`/decorator, NOT at the
    next route. Slicing route-to-route swept the module-level helpers that happen to sit between two
    endpoints into the handler and produced false positives."""
    j = start + 1
    while j < len(lines) and not re.match(r"^def ", lines[j]):
        j += 1                                          # the handler's own def line
    j += 1
    while j < len(lines):
        if re.match(r"^(def |@|class )", lines[j]):
            break
        j += 1
    return "\n".join(lines[start:j])
unwired = []
for idx, (i, meth, path) in enumerate(routes):
    handler = handler_src(i)
    if meth != "get":
        continue
    if not any(f'table("{t}")' in handler for t in PERSON_TABLES):
        continue
    if GATE.search(handler):
        continue
    if path in EXCUSED:
        continue
    unwired.append(path)
check("4. no person-keyed schedule/attendance GET reads shifts without the gate "
      "(wire it, or name it in EXCUSED with a reason)", not unwired, str(unwired))

for path in WIRED:
    hit = False
    for idx, (i, meth, p) in enumerate(routes):
        if p != path or meth != "get":
            continue
        hit = hit or bool(GATE.search(handler_src(i)))
    check(f"3. GET {path} still dereferences the gate", hit)

# ── 6. the frontend mirror agrees with the ruling ────────────────────────────────────────────────
# `frontend/src/lib/rbac.ts` carries a MIRROR so the Roles & Access box shows what the server will
# actually do. A mirror that drifts is worse than no mirror — it tells an operator they granted
# something the server refuses.
FE = os.path.normpath(os.path.join(HERE, "..", "frontend", "src", "lib", "rbac.ts"))
with open(FE, encoding="utf-8") as fh:
    fe = fh.read()
fe_fn = re.search(r"export function peopleVisibility\([\s\S]*?\n\}", fe)
fe_fn = fe_fn.group(0) if fe_fn else ""
check("6a. frontend rbac.ts carries the peopleVisibility mirror", bool(fe_fn))
py_fn = re.search(r"^def people_visibility\(.*?(?=^def |\Z)", scope_src, re.M | re.S)
py_fn = py_fn.group(0) if py_fn else ""
for tier, members in (("all", ("all", "company")),
                      ("span", ("market", "region", "regional"))):
    check(f"6b. the '{tier}' tier lists the same scopes on both sides: {', '.join(members)}",
          all(f"\"{m}\"" in py_fn for m in members) and all(f"'{m}'" in fe_fn for m in members),
          f"py={py_fn.count(tier)} fe={fe_fn.count(tier)}")
check("6c. both sides fail NARROW — the last word of each derivation is 'self'",
      py_fn.rstrip().endswith("return PEOPLE_SELF") and "return 'self'\n}" in fe_fn + "\n",
      fe_fn[-60:])

# ── 7. the gate helper has ONE home, and the person set REPLACES the store gate ──────────────────
check("7a. `gate_person_rows` is defined ONLY in the wiring home",
      [p for p in py_files() if re.search(r"^def gate_person_rows\(", read(p), re.M)] == [WIRING_HOME])
gate = re.search(r"^def gate_person_rows\(.*?(?=^def |\Z)", router, re.M | re.S)
gate = gate.group(0) if gate else ""
check("7b. when people-visibility restricts, it returns on the PERSON set BEFORE consulting the "
      "store keyset — a stacked store gate would hide a rep's own shift at a store they covered",
      gate.index("keep_visible_people") < gate.index("scope_keyset"), "order")

# ── 8. the REPORTING span is EVIDENCE, resolved in one home (owner 2026-10-05) ───────────────────
# *"the store pin is not desired, the employee shoudl have teh visibility in the store they have
# been schduled and actually worked ... the concept of home store does not apply"*.
cs = re.search(r"^def caller_scope\(.*?(?=^def |\Z)", router, re.M | re.S)
cs = cs.group(0) if cs else ""
cs_code = code_of(cs)
check("8a. caller_scope DECIDES NOTHING — it dereferences core.scope.visible_store_codes, the one "
      "home for 'whose stores reach this login'",
      "visible_store_codes" in cs_code, cs_code[-400:])
check("8b. and holds no basis of its own: no pin read, no market read, no home_store read, no "
      "second evidence scan",
      not re.search(r"self_store_codes|_login_extra_codes|home_store|WORKED_AT_SOURCES", cs_code),
      [l for l in cs_code.split("\n")
       if re.search(r"self_store_codes|_login_extra_codes|home_store|WORKED_AT_SOURCES", l)])
check("8c. the tier it passes is the DECLARED one, with the canonical scope stamped on it — never a "
      "role name spelled in code (RULE TWO)",
      re.search(r'perms\["scope"\]\s*=\s*scope', cs_code) is not None
      and "role_perms=perms" in cs_code, cs_code[-400:])
check("8d. visible_store_codes is defined ONLY in the ruling home",
      [q for q in py_files() if re.search(r"^def visible_store_codes\(", read(q), re.M)]
      == [RULING_HOME],
      str([q for q in py_files() if re.search(r"^def visible_store_codes\(", read(q), re.M)]))
# ── 8e-8g. ONE evidence registry, dereferenced by BOTH directions ────────────────────────────────
wsc = re.search(r"^def worked_store_codes\(.*?(?=^def |\Z)", scope_src, re.M | re.S)
wsc = wsc.group(0) if wsc else ""
rei = re.search(r"^def reporting_employee_ids\(.*?(?=^def |\Z)", scope_src, re.M | re.S)
rei = rei.group(0) if rei else ""
check("8e. 'what records a person being somewhere' is ONE registry (WORKED_AT_SOURCES), declared "
      "once in the ruling home",
      [q for q in py_files() if re.search(r"^WORKED_AT_SOURCES\s*=", read(q), re.M)]
      == [RULING_HOME],
      str([q for q in py_files() if re.search(r"^WORKED_AT_SOURCES\s*=", read(q), re.M)]))
check("8f. and BOTH directions dereference it — employee->stores and stores->employees — rather "
      "than carrying a private copy of the table, column and soft-delete knowledge",
      "WORKED_AT_SOURCES" in code_of(wsc) and "WORKED_AT_SOURCES" in code_of(rei),
      f"worked={('WORKED_AT_SOURCES' in code_of(wsc))} "
      f"reporting={('WORKED_AT_SOURCES' in code_of(rei))}")
check("8g. neither spells a table name of its own — a third kind of evidence lands in both or in "
      "neither",
      not re.search(r'table\(\s*["\']', code_of(wsc))
      and not re.search(r'table\(\s*["\']shifts|table\(\s*["\']timelog', code_of(rei)),
      [l for l in code_of(wsc + rei).split("\n") if re.search(r'table\(\s*["\']', l)])
check("8h. an empty evidence answer is a DENY-ALL, never the unrestricted None",
      "return set()" in code_of(wsc) and "return None" not in code_of(wsc),
      code_of(wsc)[-300:])

# ── 9. ONE answer to "is this login an individual contributor" ────────────────────────────────────
ric = re.search(r"^def role_is_self_scoped\(.*?(?=^def |\Z)", router, re.M | re.S)
ric = ric.group(0) if ric else ""
check("9a. role_is_self_scoped dereferences the declaration (not a second copy of the derivation)",
      "people_visibility" in ric and "PEOPLE_SELF" in ric)
check("9a2. and stamps the CANONICAL _role_scope on the perms before reading it — off the raw "
      "roles.permissions blob, a DM or owner whose row omits `scope` derives 'self' and reads as "
      "an individual contributor (harness_payout_audience.py G5 caught exactly that)",
      re.search(r'perms\["scope"\]\s*=\s*scope', ric) is not None, ric[-400:])
cc = read("app/modules/commcalc/router.py")
flags = re.search(r"^def get_flags\(.*?(?=^def |\Z)", cc, re.M | re.S)
flags = flags.group(0) if flags else ""
check("9b. GET /flags gates on the person through the existing one-homes — _caller_rep_keys for "
      "'which rep rows are mine' and payout_audience.mine_only for 'is this row mine'",
      "_caller_rep_keys" in flags and "mine_only" in flags)
code_only = "\n".join(l for l in flags.split("\n") if not l.lstrip().startswith("#"))
check("9c. and writes no predicate of its own (no hand-rolled rep-name comparison in the CODE)",
      "epay_salesperson" not in code_only,
      [l for l in code_only.split("\n") if "epay_salesperson" in l])


# ── 10. a PER-REP store-keyed report carries BOTH dimensions, or neither is enough ──────────────
# The owner's sentence has two halves — *"in the store they have been schduled and actually worked"*
# AND *"only their numbers"* — and the first one WIDENS a rep's store list (84 pinned stores become
# 354 worked, measured 2026-10-05). A report whose rows name a person and which gates on the store
# alone is therefore not a smaller leak than before; it is a bigger one. These three surfaces emit a
# `salesperson` per row, so each must ask the person question too.
PER_REP_SALES = {
    "sales_report": "the grid itself — one row per (store, salesperson, day)",
    "sales_report_detail": "the drill-down — customer name, phone and serial per transaction",
}
for fn_name, what in sorted(PER_REP_SALES.items()):
    body = re.search(rf"^def {fn_name}\(.*?(?=^@router|^def |\Z)", cc, re.M | re.S)
    body = code_of(body.group(0) if body else "")
    check(f"10a. `{fn_name}` ({what}) gates on the STORE", "scope_keyset" in body, body[:200])
    check(f"10b. `{fn_name}` also gates on the PERSON, through the existing one-homes — the wider "
          f"store list is only safe because of this",
          "_caller_rep_keys" in body and ("mine_only" in body or "requested_rep_is_mine" in body),
          body[:200])
    check(f"10c. and writes no rep predicate of its own (no hand-rolled salesperson comparison)",
          not re.search(r'["\']epay_salesperson["\']', body),
          [l for l in body.split("\n") if "epay_salesperson" in l])
# Both of the surfaces that had NO caller at all must keep one.
for fn_name in ("sales_report_detail", "sales_report_narrative"):
    sig = re.search(rf"^def {fn_name}\(([\s\S]*?)\):", cc, re.M)
    check(f"10d. `{fn_name}` takes the caller's identity — it took none at all before 2026-10-05, "
          f"so its gate could not have existed",
          sig is not None and "authorization" in sig.group(1), str(sig and sig.group(1)[:160]))
# The gate's error path must not hand over the tenant.
sr = re.search(r"^def sales_report\(.*?(?=^@router|\Z)", cc, re.M | re.S)
sr = code_of(sr.group(0) if sr else "")
check("10e. the sales report's gate FAILS CLOSED — an exception resolving the span serves nothing, "
      "never the whole tenant (it used to `pass` straight through to unrestricted)",
      re.search(r"except Exception[\s\S]{0,200}out\s*=\s*\[\]", sr) is not None, sr[-500:])
check("10f. and TOTALS are summed after the filters, not before — a rep's totals are a rep's own "
      "rows, not the company's under their name",
      sr.index("mine_only") < sr.index("totals = {"), "order")

# ── 11. the ADMIN DIAGNOSTIC answers with the gate, not with a basis of its own ──────────────────
# `GET /core/scope-preview` is the page an administrator uses to answer "what will this login
# actually see" without logging in as them. It therefore may not compute the span itself: a second
# basis here would have kept printing the PIN answer after the gate moved to evidence, and because
# the page exists to be trusted, the admin would have trusted it.
core_rt = read("app/modules/core/router.py")
sp = re.search(r"^def scope_preview\(.*?(?=^@router|\Z)", core_rt, re.M | re.S)
sp = code_of(sp.group(0) if sp else "")
check("11a. `scope-preview` resolves its `reporting` answer by dereferencing "
      "core.scope.visible_store_codes — the same home the reports read",
      "visible_store_codes" in sp, sp[:200])
check("11b. and holds no span basis of its own — no reporting_span_codes, no self_store_codes, no "
      "home_store read for the reporting answer",
      not re.search(r"reporting_span_codes|self_store_codes", sp),
      [l for l in sp.split("\n")
       if re.search(r"reporting_span_codes|self_store_codes", l)])

print(f"\n{len(PASS)} passed, {len(FAIL)} failed")
if FAIL:
    print("FAILED:\n  " + "\n  ".join(FAIL))
sys.exit(1 if FAIL else 0)
