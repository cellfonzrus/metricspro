"""BUILD LOCK — "whose shifts may this login READ?" stays ONE fact that every schedule read
dereferences (owner directive 2026-10-05, index §14w; the design-fix clause "lock it so it cannot
un-wire").

Stdlib only, no DB, no imports of the application. The behavioural PROOF is
`harness_schedule_visibility.py` (31 checks over the real endpoints); this file guards the SHAPE.

It fails the build when:
  1. The ruling moves or is copied — `schedule_visibility` / `schedule_people_keyset` must be
     defined in `app/core/scope.py` and nowhere else.
  2. The wiring is copied — `schedule_emp_ids` must be defined in `storeops/router.py` and nowhere
     else, and must dereference `core.scope.schedule_people_keyset` rather than re-deriving a span.
  3. A wired read stops calling it — every endpoint in WIRED below must still reference
     `schedule_emp_ids` inside its own handler body.
  4. A NEW person-keyed schedule/attendance read lands unwired — any GET handler in
     `storeops/router.py` that reads `shifts`, `shift_templates`, `time_off_requests`,
     `shift_swap_requests` or `timelog` must reference `schedule_emp_ids`, or be named in EXCUSED
     with a reason. A silent sibling is the duplicate defect this whole package exists to stop.
  5. The market-grant refusal is undone — `schedule_emp_ids` must feed the ruling
     `_caller_org_unit_codes` (the org tree ALONE), never `_caller_span_codes`, which unions the
     login's market pin and is precisely what put the whole company on a rep's schedule.

Run: `cd backend && python3 harness_schedule_visibility_lock.py`
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


def read(rel):
    with open(os.path.join(HERE, rel), encoding="utf-8") as fh:
        return fh.read()


def py_files():
    for root, dirs, files in os.walk(os.path.join(HERE, "app")):
        dirs[:] = [d for d in dirs if d != "__pycache__"]
        for f in files:
            if f.endswith(".py"):
                yield os.path.relpath(os.path.join(root, f), HERE).replace(os.sep, "/")


# ── 1. the ruling has ONE home ───────────────────────────────────────────────────────────────────
for fn in ("schedule_visibility", "schedule_people_keyset"):
    homes = [p for p in py_files() if re.search(rf"^def {fn}\(", read(p), re.M)]
    check(f"1. `{fn}` is defined ONLY in {RULING_HOME}", homes == [RULING_HOME], str(homes))

# ── 2. the wiring has ONE home and dereferences the ruling ───────────────────────────────────────
wiring_homes = [p for p in py_files() if re.search(r"^def schedule_emp_ids\(", read(p), re.M)]
check(f"2a. `schedule_emp_ids` is defined ONLY in {WIRING_HOME}",
      wiring_homes == [WIRING_HOME], str(wiring_homes))

router = read(WIRING_HOME)
m = re.search(r"^def schedule_emp_ids\(.*?(?=^def |\Z)", router, re.M | re.S)
body = m.group(0) if m else ""
check("2b. it dereferences core.scope.schedule_people_keyset (no second derivation)",
      "schedule_people_keyset" in body)
callers = sum(len(re.findall(r"schedule_people_keyset\(", read(p)))
              for p in py_files() if p != RULING_HOME)
check("2c. nothing else re-derives the ruling — schedule_people_keyset has exactly one call site",
      callers == 1, str(callers))

# ── 5. the market-grant refusal stands ───────────────────────────────────────────────────────────
check("5a. it feeds the ORG TREE alone (_caller_org_unit_codes)", "_caller_org_unit_codes" in body)
check("5b. and NOT _caller_span_codes, which unions the login's market pin",
      "_caller_span_codes" not in body, body)
scope_src = read(RULING_HOME)
ruling = re.search(r"^def schedule_people_keyset\(.*?(?=^def |\Z)", scope_src, re.M | re.S)
ruling = ruling.group(0) if ruling else ""
check("5c. the ruling reads a market grant only inside its market/region branch",
      ruling.count("login_grant_codes") == 1
      and re.search(r'scope in \("market", "region", "regional"\)[\s\S]{0,200}login_grant_codes', ruling)
      is not None)

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
    if "schedule_emp_ids" in handler:
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
        hit = hit or "schedule_emp_ids" in handler_src(i)
    check(f"3. GET {path} still dereferences schedule_emp_ids", hit)

# ── 6. the frontend mirror agrees with the ruling ────────────────────────────────────────────────
# `frontend/src/lib/rbac.ts` carries a MIRROR so the Roles & Access box shows what the server will
# actually do. A mirror that drifts is worse than no mirror — it tells an operator they granted
# something the server refuses.
FE = os.path.normpath(os.path.join(HERE, "..", "frontend", "src", "lib", "rbac.ts"))
with open(FE, encoding="utf-8") as fh:
    fe = fh.read()
fe_fn = re.search(r"export function scheduleVisibility\([\s\S]*?\n\}", fe)
fe_fn = fe_fn.group(0) if fe_fn else ""
check("6a. frontend rbac.ts carries the scheduleVisibility mirror", bool(fe_fn))
py_fn = re.search(r"^def schedule_visibility\(.*?(?=^def |\Z)", scope_src, re.M | re.S)
py_fn = py_fn.group(0) if py_fn else ""
for tier, members in (("all", ("all", "company")),
                      ("span", ("market", "region", "regional"))):
    check(f"6b. the '{tier}' tier lists the same scopes on both sides: {', '.join(members)}",
          all(f"\"{m}\"" in py_fn for m in members) and all(f"'{m}'" in fe_fn for m in members),
          f"py={py_fn.count(tier)} fe={fe_fn.count(tier)}")
check("6c. both sides fail NARROW — the last word of each derivation is 'self'",
      py_fn.rstrip().endswith("return SCHEDULE_SELF") and "return 'self'\n}" in fe_fn + "\n",
      fe_fn[-60:])

print(f"\n{len(PASS)} passed, {len(FAIL)} failed")
if FAIL:
    print("FAILED:\n  " + "\n  ".join(FAIL))
sys.exit(1 if FAIL else 0)
