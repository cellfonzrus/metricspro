"""LOCK — ONE ROW, ONE SAVE for the employee record (owner report 2026-09-29, index §19.35).

Owner: "i just saved hourly salary in vzone but it did not save when i came back".

THE DEFECT (proved from live data, read-only). HR → Employees & Pay gave every row THREE independent save
buttons — pay (in the LAST column, past Email and Phone, off the right edge of a 13-column table), one
beside Lunch and one beside Face recognition — and each saved only its own slice. On 2026-09-29 17:49 UTC
the Vzone admin typed hourly rates, set Lunch to On and pressed the Lunch button on four rows. The
system access log (core.access_log) holds exactly four `PUT /api/v1/storeops/employees/{238,278,279,240}/
lunch-config` (all 200) and NO `PATCH /api/v1/storeops/employees/{id}` for Vzone that day;
storeops.payroll_change_log has the four lunch rows and no pay row; E278/E279/E240 still carry pay_rate 0.
The rates never left the browser, and the page gave no sign that anything was pending. The backend write
path is sound (`harness_payroll_salary_router_integration.py` drives the real `update_employee`).

THE CLASS: a row whose edits persist through more than one save action, so a success shown for one reads
as "the row is saved" while the other slices' edits sit only in page state and are discarded on the next
load. Sibling with the same shape: Roles & Access (row "Save" wrote email + role; Pay $/hr sat in the ✏️
Edit panel behind "💾 Save details").

THE DESIGN FIX — one home, dereferenced: `frontend/src/lib/employeeRowSlices.ts` is the ONLY place a row
editor builds an employee-record write (`PATCH /storeops/employees/{id}`, `PUT …/lunch-config`,
`PUT …/face-config`); `frontend/src/lib/rowSave.ts::planRowSave` plans a request for EVERY edited slice
of a row from a registered `*_ROW_SLICES` set; `useUnsavedGuard` guards leaving with unsaved rows; and
"is this field edited" is `rowSave.fieldsDirty`, which the StoreOps setup grids now call instead of their
own copies. The behaviour is proved by `frontend/prove_row_save.mjs` (the real modules, DB-free).

WHAT THIS LOCK FAILS ON
  1. a frontend file that writes the employee record (PATCH/PUT on `/api/v1/storeops/employees/${…}`)
     outside the one home — unless it is listed in `frontend/row_save_pending.txt` (the ratchet: may only
     shrink, PINNED_MAX must equal its length, a stale line fails) or excused below with a reason;
  2. a file that uses the slices without the engine (`planRowSave`) or without the leave guard;
  3. `planRowSave` called with a SUBSET of a row's slices (a single `EMP_*_SLICE`, an inline array) —
     the exact way the one-button-per-slice defect would come back;
  4. a second copy of the "is this field edited" comparison outside rowSave.ts;
  5. a PATCH slice that names a field the backend's `EMP_FIELDS` does not accept (it would be dropped by
     `update_employee` and the "Saved" message would lie), or lunch/face bodies the endpoints do not read;
  6. the node proof not being run by a workflow.

Run against the tree before the fix, it fails on (1) naming `hr/page.tsx` and `admin/roles/page.tsx`:
    ROW_SAVE_ROOT=<a checkout of main> python3 backend/harness_row_save_lock.py
Stdlib only: `python3 backend/harness_row_save_lock.py`.
"""
import ast
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.environ.get("ROW_SAVE_ROOT") or os.path.join(HERE, ".."))
FRONT = os.path.join(ROOT, "frontend")
SRC = os.path.join(FRONT, "src")

HOME = "src/lib/employeeRowSlices.ts"
ENGINE = "src/lib/rowSave.ts"
PENDING_FILE = os.path.join(FRONT, "row_save_pending.txt")
PINNED_MAX = 3
# One-click ACTIONS, not row editors: the value is picked and written in the same click, so no typed
# edit can be left pending. Permanent, each with its reason.
EXCUSED = {
    "src/app/(platform)/commcalc/_lib/coverageDiagnosis.tsx":
        "the uncovered-rep 'Link' button writes the one epay_salesperson value chosen in that click",
}

PASS, FAIL = [], []


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name if cond else f"{name} :: {detail}")


def read(rel, base=FRONT):
    try:
        with open(os.path.join(base, rel), encoding="utf-8") as fh:
            return fh.read()
    except OSError:
        return None


def frontend_files():
    out = {}
    for dp, dn, fn in os.walk(SRC):
        dn[:] = [d for d in dn if d not in ("node_modules", ".next")]
        for f in fn:
            if f.endswith((".ts", ".tsx")):
                p = os.path.join(dp, f)
                with open(p, encoding="utf-8") as fh:
                    out[os.path.relpath(p, FRONT).replace(os.sep, "/")] = fh.read()
    return out


EMP_PATH = re.compile(r"`/api/v1/storeops/employees/\$\{")
WRITE_METHOD = re.compile(r"""method\s*:\s*['"](PATCH|PUT)['"]""")


def employee_record_writes(text):
    """Line numbers where an employee-record path literal is written with PATCH/PUT (method within the
    same call — the next 400 characters, which is how every caller in this tree spells it)."""
    hits = []
    for m in EMP_PATH.finditer(text):
        window = text[m.start(): m.start() + 400]
        mm = WRITE_METHOD.search(window)
        # the method must belong to THIS call: no second `api(` / `fetch(` before it
        if mm and not re.search(r"\b(api|fetch)\(", window[1:mm.start()]):
            hits.append(text.count("\n", 0, m.start()) + 1)
    return hits


def pending_entries():
    try:
        with open(PENDING_FILE, encoding="utf-8") as fh:
            return [l.strip() for l in fh if l.strip() and not l.lstrip().startswith("#")]
    except OSError:
        return None


files = frontend_files()

print("1. one home for employee-record writes")
writers = {rel: employee_record_writes(t) for rel, t in files.items()}
writers = {rel: lines for rel, lines in writers.items() if lines}
pending = pending_entries()
check("1a the ratchet file exists", pending is not None, PENDING_FILE)
pending = pending or []
home_src = files.get(HOME)
check("1b the one home exists", home_src is not None, HOME)
# The home itself builds its paths through a helper, so the scan finds its literal only via `empPath`.
check("1c the one home builds the employee path", bool(home_src) and "`/api/v1/storeops/employees/${" in home_src)
for rel, lines in sorted(writers.items()):
    if rel == HOME:
        continue
    ok = rel in pending or rel in EXCUSED
    check(f"1d {rel} writes the employee record only through the one home", ok,
          f"lines {lines}: PATCH/PUT /api/v1/storeops/employees/${{…}} written directly — build it in "
          f"{HOME} and plan it with rowSave.planRowSave (index §19.35)")
for rel in pending:
    check(f"1e ratchet entry still needed: {rel}", rel in writers,
          "it no longer writes the employee record — delete its line and lower PINNED_MAX")
check("1f the ratchet may only shrink (PINNED_MAX == entries)", len(pending) == PINNED_MAX,
      f"{len(pending)} entries, PINNED_MAX {PINNED_MAX}")
for rel in EXCUSED:
    check(f"1g excused file still exists and still writes: {rel}", rel in writers, "remove the stale excuse")

print("2. the slices are only used with the engine and the leave guard")
users = {rel: t for rel, t in files.items()
         if rel not in (HOME, ENGINE) and re.search(r"""from\s+['"]@/lib/employeeRowSlices['"]""", t)}
check("2a the two pay editors use the one home",
      {"src/app/(platform)/hr/page.tsx", "src/app/(platform)/admin/roles/page.tsx"} <= set(users), sorted(users))
for rel, t in sorted(users.items()):
    check(f"2b {rel} plans with planRowSave", "planRowSave(" in t)
    check(f"2c {rel} installs useUnsavedGuard", "useUnsavedGuard(" in t)

print("3. a row's plan is never a subset of its slices")
PLAN_CALL = re.compile(r"planRowSave\(([^;]*?)\)")
for rel, t in sorted(files.items()):
    if rel == ENGINE:
        continue
    for m in PLAN_CALL.finditer(t):
        args = [a.strip() for a in m.group(1).split(",")]
        third = args[2] if len(args) > 2 else ""
        check(f"3a {rel}:{t.count(chr(10), 0, m.start()) + 1} plans a registered *_ROW_SLICES set",
              bool(re.fullmatch(r"[A-Z][A-Z0-9_]*_ROW_SLICES", third)), f"third argument is {third!r}")
exported_sets = re.findall(r"export const ([A-Z_]+_ROW_SLICES)\s*=\s*\[([^\]]*)\]", home_src or "")
check("3b the home registers the HR and Roles row sets",
      {"HR_EMPLOYEE_ROW_SLICES", "ROLES_EMPLOYEE_ROW_SLICES"} <= {n for n, _ in exported_sets}, exported_sets)
hr_set = dict(exported_sets).get("HR_EMPLOYEE_ROW_SLICES", "")
check("3c the HR row set carries pay, lunch AND face (the three buttons the defect split)",
      all(s in hr_set for s in ("EMP_PAY_SLICE", "EMP_LUNCH_SLICE", "EMP_FACE_SLICE")), hr_set)
roles_set = dict(exported_sets).get("ROLES_EMPLOYEE_ROW_SLICES", "")
check("3d the Roles row set carries details (pay) AND email", all(s in roles_set for s in ("EMP_DETAILS_SLICE", "EMP_EMAIL_SLICE")), roles_set)

print("4. one comparison of an edited value")
COPY = re.compile(r"String\(\s*\w+\[\s*\w+\s*\]\s*\?\?\s*''\s*\)\s*!==\s*String\(")
copies = sorted(rel for rel, t in files.items() if rel != ENGINE and COPY.search(t))
check("4a no private copy of the dirty comparison outside rowSave.ts", not copies,
      f"{copies} — use rowSave.fieldsDirty / fieldChanged")
setup_lib = files.get("src/app/(platform)/storeops/setup/lib.tsx", "")
check("4b the setup grids' isDirty dereferences rowSave.fieldsDirty", "fieldsDirty(" in setup_lib)

print("5. the slices match what the backend reads")
router_src = read("backend/app/modules/storeops/router.py", ROOT) or ""
emp_fields = set()
try:
    for node in ast.parse(router_src).body:
        if isinstance(node, ast.Assign) and any(getattr(t, "id", "") == "EMP_FIELDS" for t in node.targets):
            emp_fields = set(ast.literal_eval(node.value))
except SyntaxError:
    pass
check("5a EMP_FIELDS parsed from storeops/router.py", bool(emp_fields))


def slice_block(name):
    m = re.search(r"export const %s\b.*?\n}\n" % name, home_src or "", re.S)
    return m.group(0) if m else ""


for name in ("EMP_PAY_SLICE", "EMP_DETAILS_SLICE", "EMP_EMAIL_SLICE"):
    blk = slice_block(name)
    fields = re.findall(r"'([a-z_]+)'", (re.search(r"fields:\s*\[([^\]]*)\]", blk) or [None, ""])[1]) if blk else []
    check(f"5b {name} is a PATCH of fields update_employee accepts", bool(fields) and "method: 'PATCH'" in blk
          and set(fields) <= emp_fields, f"fields {fields} not all in EMP_FIELDS")
lunch_handler = (re.search(r"def set_employee_lunch_config\(.*?\n(?=def |@router)", router_src, re.S) or [""])[0]
face_handler = (re.search(r"def set_employee_face_config\(.*?\n(?=def |@router)", router_src, re.S) or [""])[0]
check("5c lunch-config reads the {enabled, minutes} the lunch slice sends",
      '"enabled" in body' in lunch_handler and '"minutes" in body' in lunch_handler
      and "enabled, minutes:" in slice_block("EMP_LUNCH_SLICE"))
check("5d face-config reads the {enabled, consent} the face slice sends",
      "enabled" in face_handler and "consent" in face_handler and "consent:" in slice_block("EMP_FACE_SLICE"))

print("6. the behavioural proof is run by CI")
wf_dir = os.path.join(ROOT, ".github", "workflows")
wf_text = ""
for f in sorted(os.listdir(wf_dir)) if os.path.isdir(wf_dir) else []:
    with open(os.path.join(wf_dir, f), encoding="utf-8") as fh:
        wf_text += fh.read()
check("6a frontend/prove_row_save.mjs exists", read("prove_row_save.mjs") is not None)
check("6b a workflow runs node prove_row_save.mjs", re.search(r"run:.*\bnode\s+prove_row_save\.mjs", wf_text) is not None)

print("7. planted controls (the detectors are not vacuous)")
check("7a a direct PATCH is detected", employee_record_writes(
    "await api(`/api/v1/storeops/employees/${e.id}`, { method: 'PATCH', body: JSON.stringify(b) })") == [1])
check("7b the pre-fix Lunch button's PUT is detected", employee_record_writes(
    "await api(`/api/v1/storeops/employees/${e.id}/lunch-config`, { method: 'PUT', body })") == [1])
check("7c a DELETE is not an edit", employee_record_writes(
    "await api(`/api/v1/storeops/employees/${e.id}`, { method: 'DELETE' })") == [])
check("7d a later call's method is not borrowed", employee_record_writes(
    "await api(`/api/v1/storeops/employees/${e.id}`)\nawait api('/x', { method: 'PATCH' })") == [])
_sub = PLAN_CALL.search("planRowSave(e, snap, [EMP_LUNCH_SLICE], ctx)")
check("7e a subset plan is rejected", _sub is not None and not re.fullmatch(
    r"[A-Z][A-Z0-9_]*_ROW_SLICES", [a.strip() for a in _sub.group(1).split(",")][2]))
check("7f the old private isDirty copy is detected",
      COPY.search("return fields.some(f => String(row[f] ?? '') !== String(orig[f] ?? ''))") is not None)

print()
for p in PASS:
    print(f"  PASS  {p}")
for f in FAIL:
    print(f"  FAIL  {f}")
print(f"\n{len(PASS)} passed, {len(FAIL)} failed")
sys.exit(1 if FAIL else 0)
