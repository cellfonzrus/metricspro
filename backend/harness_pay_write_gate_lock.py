"""LOCK — every write of a pay-gated employee column goes through ONE gate (index §19.41).

  python3 backend/harness_pay_write_gate_lock.py

Owner decision 2026-10-03: "only people allowed to see pay may set pay when adding employees". The
#342 trace found the pay-write policy lived inline in `update_employee` alone: `POST /storeops/employees`,
`POST /storeops/employees/bulk` and `POST /hr/employees` wrote `pay_rate` unchecked and
`bulk_payscale` only asked "is a manager". The class: a pay write gated on one entry point and not the
others. The design: `storeops/router.py::gate_pay_write` is the one gate. This lock FAILS THE BUILD if

  G1  the gate stops being the gate (no `_require_manager`, no `_payvis.can_see_pay`, no refusal);
  G2  ANY function under backend/app writes `storeops.employees` (insert / upsert / update) with a
      payload that can carry a pay-gated column and does not call `gate_pay_write` BEFORE the write.
      "Can carry" = a dict literal naming a gated column or spreading `**` something, or a function
      that builds its payload from `EMP_FIELDS` or names a gated column as a string (docstrings are
      not code and are ignored). The gated set is READ from the shipped `_PAY_GATED_FIELDS`;
  G3  `_PAY_GATED_FIELDS` is read anywhere but inside `gate_pay_write` (a second copy of the policy);
  G4  the HR intake form's propagation allow-list (`_PROPAGATABLE`) admits a pay-gated column;
  G5  a frontend screen that POSTs to an employee CREATE endpoint does not read the reply through
      `rowSave.notSavedNote` / `notSavedFields` — a dropped pay field must be said, not swallowed;
  G6  `rowSave.ts` reads NOT_SAVED_KEYS anywhere but its one reader `notSavedFields`.

Negative controls (C*) prove each detector goes RED on the defect it exists for, including the
pre-fix `create_employee` transcribed from main.
"""
import ast
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
APP = os.path.join(HERE, "app")
ROUTER = os.path.join(APP, "modules", "storeops", "router.py")
HR = os.path.join(APP, "modules", "hr", "router.py")
FE = os.path.join(ROOT, "frontend", "src")
ROWSAVE = os.path.join(FE, "lib", "rowSave.ts")
PASS = FAIL = 0


def check(label, ok, detail=""):
    global PASS, FAIL
    PASS += bool(ok)
    FAIL += (not ok)
    print(f"  {'PASS' if ok else 'FAIL'}  {label}" + ("" if ok else f"\n        {detail}"))


def read(p):
    with open(p, encoding="utf-8") as fh:
        return fh.read()


def _assigned_literal(src, name):
    for n in ast.parse(src).body:
        if isinstance(n, ast.Assign) and any(getattr(t, "id", None) == name for t in n.targets):
            return ast.literal_eval(n.value)
    return None


ROUTER_SRC = read(ROUTER)
GATED = set(_assigned_literal(ROUTER_SRC, "_PAY_GATED_FIELDS") or ())


# ── detectors ──────────────────────────────────────────────────────────────────────────────────────
def _functions(tree):
    """Every function, innermost-first ownership: a node belongs to the nearest enclosing def."""
    out = []
    for n in ast.walk(tree):
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)):
            out.append(n)
    return out


def _own_nodes(fn):
    """Nodes of `fn` that are not inside a nested def."""
    stack, out = list(ast.iter_child_nodes(fn)), []
    while stack:
        n = stack.pop()
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
            continue
        out.append(n)
        stack.extend(ast.iter_child_nodes(n))
    return out


def _docstring_nodes(fn):
    if fn.body and isinstance(fn.body[0], ast.Expr) and isinstance(getattr(fn.body[0], "value", None), ast.Constant) \
            and isinstance(fn.body[0].value.value, str):
        return {id(fn.body[0].value)}
    return set()


def _table_name(chain):
    """The constant table name a call chain addresses: X.table("employees").insert(...) -> 'employees'."""
    n = chain
    while isinstance(n, (ast.Call, ast.Attribute)):
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute) and n.func.attr == "table" \
                and n.args and isinstance(n.args[0], ast.Constant):
            return n.args[0].value
        n = n.func if isinstance(n, ast.Call) else n.value
    return None


def employee_writes(fn):
    """[(lineno, payload_node)] for every insert/upsert/update on table('employees') owned by fn."""
    out = []
    for n in _own_nodes(fn):
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute) and n.func.attr in ("insert", "upsert", "update") \
                and _table_name(n.func.value) == "employees":
            out.append((n.lineno, n.args[0] if n.args else None))
    return out


def payload_may_carry_pay(fn, payload, gated):
    if isinstance(payload, ast.Dict):
        if any(k is None for k in payload.keys):                    # {**something}
            return True
        return any(isinstance(k, ast.Constant) and k.value in gated for k in payload.keys)
    docs = _docstring_nodes(fn)
    for n in _own_nodes(fn):
        if isinstance(n, ast.Name) and n.id == "EMP_FIELDS":
            return True
        if isinstance(n, ast.Constant) and isinstance(n.value, str) and n.value in gated and id(n) not in docs:
            return True
    return False


def gate_calls(fn):
    return sorted(n.lineno for n in _own_nodes(fn) if isinstance(n, ast.Call) and (
        (isinstance(n.func, ast.Name) and n.func.id == "gate_pay_write")
        or (isinstance(n.func, ast.Attribute) and n.func.attr == "gate_pay_write")))


def ungated_pay_writers(src, path, gated):
    """[(path:line, fn, why)] — the G2 detector."""
    bad = []
    for fn in _functions(ast.parse(src)):
        if fn.name == "gate_pay_write":
            continue
        for line, payload in employee_writes(fn):
            if not payload_may_carry_pay(fn, payload, gated):
                continue
            gl = [g for g in gate_calls(fn) if g < line]
            if not gl:
                bad.append((f"{os.path.relpath(path, ROOT)}:{line}", fn.name,
                            "writes a pay-gated employee column without calling gate_pay_write first"))
    return bad


def gate_is_wired(src):
    fn = next((f for f in _functions(ast.parse(src)) if f.name == "gate_pay_write"), None)
    if fn is None:
        return False, "gate_pay_write not found"
    seg = ast.get_source_segment(src, fn) or ""
    need = {"_require_manager(": "_require_manager", "can_see_pay(": "_payvis.can_see_pay",
            "PAY_WRITE_REFUSED": "the 403 refusal", "_PAY_GATED_FIELDS": "the gated set"}
    missing = [v for k, v in need.items() if k not in seg]
    return (not missing), f"missing: {missing}"


def gated_set_readers(src):
    """Functions that READ `_PAY_GATED_FIELDS` (G3) — only the gate may."""
    out = set()
    for fn in _functions(ast.parse(src)):
        for n in _own_nodes(fn):
            if isinstance(n, ast.Name) and n.id == "_PAY_GATED_FIELDS" and isinstance(n.ctx, ast.Load):
                out.add(fn.name)
    return out


CREATE_POST = re.compile(r"api\(\s*['\"`]/api/v1/(?:storeops/employees(?:/bulk)?|hr/employees)['\"`]\s*,\s*\{\s*method:\s*['\"]POST['\"]")


def fe_create_screens_unread(files):
    """G5 — {path} of frontend files that POST to an employee create endpoint without reading the reply
    through notSavedNote / notSavedFields."""
    out = []
    for p, t in files.items():
        if CREATE_POST.search(t) and not re.search(r"\bnotSaved(?:Note|Fields)\(", t):
            out.append(p)
    return out


def strip_ts_comments(src):
    """Blank out // and /* */ comments (length-preserving), leaving string literals intact."""
    out, i, n, q = list(src), 0, len(src), None
    while i < n:
        c = src[i]
        if q:
            if c == "\\":
                i += 2
                continue
            if c == q:
                q = None
        elif c in "'\"`":
            q = c
        elif src.startswith("//", i):
            j = src.find("\n", i)
            j = n if j == -1 else j
            out[i:j] = " " * (j - i)
            i = j
            continue
        elif src.startswith("/*", i):
            j = src.find("*/", i + 2)
            j = n if j == -1 else j + 2
            out[i:j] = [ch if ch == "\n" else " " for ch in src[i:j]]
            i = j
            continue
        i += 1
    return "".join(out)


def rowsave_single_reader(src):
    """G6 — NOT_SAVED_KEYS is iterated only inside notSavedFields (comments do not count)."""
    src = strip_ts_comments(src)
    uses = [m.start() for m in re.finditer(r"\bNOT_SAVED_KEYS\b", src)]
    m = re.search(r"export function notSavedFields\(.*?\n}\n", src, re.S)
    if not m:
        return False, "notSavedFields not found"
    lo, hi = m.span()
    decl = re.search(r"export const NOT_SAVED_KEYS\s*=", src)
    stray = [u for u in uses if not (lo <= u < hi) and not (decl and u == decl.start() + len("export const "))]
    return (not stray), f"{len(stray)} read(s) of NOT_SAVED_KEYS outside notSavedFields"


# ══════════════════════════════════════════════════════════════════════════════════════════════════
print("G0. the gated set is read from the shipped router")
check("G0 _PAY_GATED_FIELDS found and holds the pay columns", {"pay_rate", "pay_basis", "pay_amount"} <= GATED, GATED)

print("G1. the gate is wired")
ok, why = gate_is_wired(ROUTER_SRC)
check("G1 gate_pay_write calls _require_manager + _payvis.can_see_pay and refuses with PAY_WRITE_REFUSED", ok, why)

print("G2. every employee pay writer under backend/app calls the gate before writing")
all_bad, writers = [], []
for dp, _, fs in os.walk(APP):
    for f in fs:
        if not f.endswith(".py"):
            continue
        p = os.path.join(dp, f)
        s = read(p)
        if "employees" not in s:
            continue
        try:
            tree = ast.parse(s)
        except SyntaxError:
            continue
        for fn in _functions(tree):
            for line, payload in employee_writes(fn):
                if payload_may_carry_pay(fn, payload, GATED):
                    writers.append(f"{os.path.relpath(p, ROOT)}:{line} {fn.name}")
        all_bad += ungated_pay_writers(s, p, GATED)
for w in sorted(writers):
    print(f"        pay-carrying writer: {w}")
check("G2a the scan found the known pay writers (create, bulk create, update, bulk payscale, HR create)",
      {"create_employee", "bulk_create_employees", "update_employee", "bulk_payscale", "hr_create_employee"}
      <= {w.split()[-1] for w in writers}, sorted(writers))
check("G2b none of them writes before the gate", not all_bad, "\n        ".join(f"{a} {b}: {c}" for a, b, c in all_bad))

print("G3. one copy of the policy")
readers = gated_set_readers(ROUTER_SRC) | gated_set_readers(read(HR))
check("G3 only gate_pay_write reads _PAY_GATED_FIELDS", readers == {"gate_pay_write"}, sorted(readers))

print("G4. the intake form cannot propagate a pay column")
prop = set(_assigned_literal(read(HR), "_PROPAGATABLE") or ())
check("G4 _PROPAGATABLE found and holds no pay-gated column", bool(prop) and not (prop & GATED), sorted(prop & GATED))

print("G5/G6. the frontend says what was not written")
fe_files = {}
for dp, _, fs in os.walk(FE):
    for f in fs:
        if f.endswith((".ts", ".tsx")):
            p = os.path.join(dp, f)
            fe_files[os.path.relpath(p, ROOT)] = read(p)
creators = sorted(p for p, t in fe_files.items() if CREATE_POST.search(t))
for c in creators:
    print(f"        create screen: {c}")
check("G5a the create screens were found (HR · People, StoreOps Admin, StoreOps setup)", len(creators) >= 3, creators)
unread = fe_create_screens_unread(fe_files)
check("G5b every one reads the reply through notSavedNote / notSavedFields", not unread, unread)
ok, why = rowsave_single_reader(read(ROWSAVE))
check("G6 rowSave reads NOT_SAVED_KEYS only in notSavedFields", ok, why)

print("C. negative controls (each detector goes RED on its defect)")
PREFIX_CREATE = '''
def create_employee(emp: dict, org_id: str = ORG_ID):
    """Create an employee (StoreOps Admin)."""
    row = {k: emp[k] for k in EMP_FIELDS if k in emp}
    row["org_id"] = org_id
    r = sb().table("employees").insert(row).execute()
    return _ensure_employee_id(r.data[0]) if r.data else row
'''
check("C1 the PRE-FIX create_employee (main, 2026-10-03) -> RED", bool(ungated_pay_writers(PREFIX_CREATE, ROUTER, GATED)))
check("C2 an ungated insert with a pay_rate literal -> RED", bool(ungated_pay_writers(
    "def f(org_id):\n    sb().table('employees').insert({'name': 'x', 'pay_rate': 9}).execute()\n", ROUTER, GATED)))
check("C3 a bulk payscale update with no gate -> RED", bool(ungated_pay_writers(
    "def f(r):\n    sb().table('employees').update({'pay_rate': r}).eq('id', 1).execute()\n", ROUTER, GATED)))
check("C4 the gate called AFTER the write -> RED", bool(ungated_pay_writers(
    "def f(row, a, o):\n    sb().table('employees').insert(row).execute()\n    gate_pay_write(row, a, o)\n"
    "    x = EMP_FIELDS\n", ROUTER, GATED)))
check("C5 a `**body` payload (unknown keys) with no gate -> RED", bool(ungated_pay_writers(
    "def f(body):\n    sb().table('employees').insert({**body, 'org_id': 1}).execute()\n", ROUTER, GATED)))
check("C6 a non-pay writer (face consent / is_active) -> GREEN", not ungated_pay_writers(
    "def f():\n    '''mentions pay_rate in prose only'''\n    sb().table('employees').update({'is_active': False}).execute()\n",
    ROUTER, GATED))
check("C7 a nested-schema chain is still seen -> RED", bool(ungated_pay_writers(
    "def f(c):\n    c.schema('storeops').table('employees').insert({'pay_amount': 1}).execute()\n", ROUTER, GATED)))
check("C8 the gate with its visibility check removed -> RED", not gate_is_wired(
    "def gate_pay_write(rows, a, o):\n    _require_manager(a, o)\n    raise HTTPException(403, PAY_WRITE_REFUSED)\n"
    "    _PAY_GATED_FIELDS\n")[0])
check("C9 a second copy of the policy (another function filtering on _PAY_GATED_FIELDS) -> RED",
      gated_set_readers("def gate_pay_write():\n    _PAY_GATED_FIELDS\n"
                        "def other(row):\n    return {k: v for k, v in row.items() if k not in _PAY_GATED_FIELDS}\n")
      != {"gate_pay_write"})
check("C10 a create screen that ignores the reply -> RED", fe_create_screens_unread(
    {"x.tsx": "await api('/api/v1/hr/employees', { method: 'POST', body })\nsetMsg('Saved')"}) == ["x.tsx"])
check("C11 a PATCH or a GET is not a create -> GREEN", fe_create_screens_unread(
    {"x.tsx": "await api('/api/v1/storeops/employees', { method: 'GET' })"}) == [])
check("C12 a second NOT_SAVED_KEYS reader in rowSave -> RED", not rowsave_single_reader(
    read(ROWSAVE) + "\nfor (const k of NOT_SAVED_KEYS) console.log(k)\n")[0])

print(f"\n{PASS} passed, {FAIL} failed")
sys.exit(1 if FAIL else 0)
