"""LOCK — a payroll change-log row says WHO from the stored employee record (index §19.45).

  python3 backend/harness_payroll_log_identity_lock.py

Owner report 2026-10-03: Vzone's change log showed Shweta's pay edit (id 237) with no employee number
while the record holds 'E237'. `update_employee` logged its UPDATE echo before the business id was
minted. The class: a change-log row's identity came from whatever the caller happened to hold. The
design: `storeops/router.py::_log_payroll_change` is the table's only writer and builds every row's
(employee_id, employee_name, store_code) through `payroll_log_identity.resolve_log_identity`, which
reads the stored person and mints a missing business id through the one mint. This lock FAILS THE
BUILD if

  L1  anything under backend/app other than `_log_payroll_change` inserts / upserts / updates
      `payroll_change_log`, or builds a log row (a dict with both 'entry_point' and 'before_value');
  L2  `_log_payroll_change` stops calling `resolve_log_identity` before its insert, stops passing it
      the one mint, or puts a caller's loose parameter (not the helper's answer) in the row's
      employee_id / employee_name / store_code;
  L3  a `_log_payroll_change(...)` call about an employees row (`source_table="employees"`) does not
      hand over the stored row as `employee_row=`;
  L4  an insert into `storeops.employees` anywhere under backend/app does not pass its result to
      `_ensure_employee_id` — the one mint (a person with no business id is the person whose log
      row said NULL);
  L5  the helper's roster read stops being org-scoped;
  L6  a migration writes payroll_change_log rows with INSERT (a SQL writer the helper never sees).

Negative controls (C*) prove each detector goes RED — including the pre-fix `_log_payroll_change`
and the pre-fix Roles insert, transcribed from main.
"""
import ast
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
APP = os.path.join(HERE, "app")
ROUTER = os.path.join(APP, "modules", "storeops", "router.py")
HELPER = os.path.join(APP, "modules", "storeops", "payroll_log_identity.py")
MIGS = os.path.join(ROOT, "database", "migrations")
PASS = FAIL = 0
IDENT_KEYS = ("employee_id", "employee_name", "store_code")


def check(label, ok, detail=""):
    global PASS, FAIL
    PASS += bool(ok)
    FAIL += (not ok)
    print(f"  {'PASS' if ok else 'FAIL'}  {label}" + ("" if ok else f"\n        {detail}"))


def read(p):
    with open(p, encoding="utf-8") as fh:
        return fh.read()


def _functions(tree):
    return [n for n in ast.walk(tree) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]


def _own_nodes(fn):
    stack, out = list(ast.iter_child_nodes(fn)), []
    while stack:
        n = stack.pop()
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
            continue
        out.append(n)
        stack.extend(ast.iter_child_nodes(n))
    return out


def _table_name(chain):
    n = chain
    while isinstance(n, (ast.Call, ast.Attribute)):
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute) and n.func.attr == "table" \
                and n.args and isinstance(n.args[0], ast.Constant):
            return n.args[0].value
        n = n.func if isinstance(n, ast.Call) else n.value
    return None


def writes_to(fn, table, verbs=("insert", "upsert", "update")):
    return [n for n in _own_nodes(fn) if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
            and n.func.attr in verbs and _table_name(n.func.value) == table]


def _owner_of(tree, node_ids):
    out = {}
    for fn in _functions(tree):
        for n in _own_nodes(fn):
            if id(n) in node_ids:
                out[id(n)] = fn.name
    return out


# ── detectors ──────────────────────────────────────────────────────────────────────────────────────
def foreign_log_writers(src, path):
    """L1 — [(where, fn)] writing payroll_change_log or building a log row outside _log_payroll_change."""
    tree, bad = ast.parse(src), []
    for fn in _functions(tree):
        if fn.name == "_log_payroll_change":
            continue
        for c in writes_to(fn, "payroll_change_log"):
            bad.append((f"{os.path.relpath(path, ROOT)}:{c.lineno}", fn.name, "writes payroll_change_log"))
        for n in _own_nodes(fn):
            if isinstance(n, ast.Dict):
                keys = {k.value for k in n.keys if isinstance(k, ast.Constant)}
                if {"entry_point", "before_value"} <= keys:
                    bad.append((f"{os.path.relpath(path, ROOT)}:{n.lineno}", fn.name, "builds a change-log row"))
    # module-level writes (outside any def) count too
    owned = {id(n) for fn in _functions(tree) for n in _own_nodes(fn)}
    for n in ast.walk(tree):
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute) and n.func.attr in ("insert", "upsert", "update") \
                and _table_name(n.func.value) == "payroll_change_log" and id(n) not in owned:
            bad.append((f"{os.path.relpath(path, ROOT)}:{n.lineno}", "<module>", "writes payroll_change_log"))
    return bad


def inserter_reads_identity(src):
    """L2 — (ok, why) for _log_payroll_change."""
    fn = next((f for f in _functions(ast.parse(src)) if f.name == "_log_payroll_change"), None)
    if fn is None:
        return False, "_log_payroll_change not found"
    params = {a.arg for a in fn.args.args + fn.args.kwonlyargs}
    helper = [n for n in _own_nodes(fn) if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
              and n.func.attr == "resolve_log_identity"]
    ins = writes_to(fn, "payroll_change_log", ("insert",))
    if not helper:
        return False, "does not call resolve_log_identity"
    if not ins:
        return False, "no insert found"
    if min(h.lineno for h in helper) > min(i.lineno for i in ins):
        return False, "resolve_log_identity is called after the insert"
    if not any(k.arg == "mint" and isinstance(k.value, ast.Name) and k.value.id == "_ensure_employee_id"
               for h in helper for k in h.keywords):
        return False, "resolve_log_identity is not handed the one mint (mint=_ensure_employee_id)"
    if not any(k.arg == "employee_row" for h in helper for k in h.keywords):
        return False, "employee_row is not passed to the helper"
    rows = [n for n in _own_nodes(fn) if isinstance(n, ast.Dict)
            and {"entry_point", "before_value"} <= {k.value for k in n.keys if isinstance(k, ast.Constant)}]
    if len(rows) != 1:
        return False, f"expected one row dict, found {len(rows)}"
    for k, v in zip(rows[0].keys, rows[0].values):
        if isinstance(k, ast.Constant) and k.value in IDENT_KEYS:
            if isinstance(v, ast.Name) and v.id in params:
                return False, f"row['{k.value}'] is the caller's loose parameter `{v.id}`"
            if not isinstance(v, ast.Subscript):
                return False, f"row['{k.value}'] is not read from the helper's answer"
    return True, ""


def employees_source_without_row(src, path):
    """L3 — calls about an employees row that do not pass employee_row=."""
    bad = []
    for n in ast.walk(ast.parse(src)):
        if isinstance(n, ast.Call) and getattr(n.func, "id", getattr(n.func, "attr", None)) == "_log_payroll_change":
            kw = {k.arg: k.value for k in n.keywords}
            st = kw.get("source_table")
            if isinstance(st, ast.Constant) and st.value == "employees" and "employee_row" not in kw:
                bad.append(f"{os.path.relpath(path, ROOT)}:{n.lineno}")
    return bad


def unminted_employee_inserts(src, path):
    """L4 — functions inserting into employees that never call _ensure_employee_id after the insert."""
    bad = []
    for fn in _functions(ast.parse(src)):
        ins = writes_to(fn, "employees", ("insert", "upsert"))
        if not ins:
            continue
        mints = [n.lineno for n in _own_nodes(fn) if isinstance(n, ast.Call)
                 and getattr(n.func, "id", getattr(n.func, "attr", None)) == "_ensure_employee_id"]
        if not any(m >= min(i.lineno for i in ins) for m in mints):
            bad.append(f"{os.path.relpath(path, ROOT)}:{ins[0].lineno} {fn.name}")
    return bad


def helper_org_scoped(src):
    fn = next((f for f in _functions(ast.parse(src)) if f.name == "_one"), None)
    if fn is None:
        return False
    seg = ast.get_source_segment(src, fn) or ""
    return '.eq("org_id", org_id)' in seg and 'table("employees")' in seg


SQL_INSERT = re.compile(r"insert\s+into\s+storeops\.payroll_change_log(?![a-z0-9_])", re.I)


def sql_log_writers(files):
    out = []
    for name, text in files.items():
        body = "\n".join(l for l in text.splitlines() if not l.lstrip().startswith("--"))
        if SQL_INSERT.search(body):
            out.append(name)
    return out


# ══════════════════════════════════════════════════════════════════════════════════════════════════
ROUTER_SRC = read(ROUTER)
print("L1. one writer of payroll_change_log under backend/app")
foreign, l3, l4, calls = [], [], [], 0
for dp, _, fs in os.walk(APP):
    for f in fs:
        if not f.endswith(".py"):
            continue
        p = os.path.join(dp, f)
        s = read(p)
        try:
            ast.parse(s)
        except SyntaxError:
            continue
        if "payroll_change_log" in s:
            foreign += foreign_log_writers(s, p)
        if "_log_payroll_change" in s:
            calls += len(re.findall(r"\b_log_payroll_change\(", s))
            l3 += employees_source_without_row(s, p)
        if "employees" in s:
            l4 += unminted_employee_inserts(s, p)
check("L1 nothing but _log_payroll_change writes the table or builds its row", not foreign,
      "\n        ".join(" ".join(x) for x in foreign))
print(f"        ({calls} call sites of _log_payroll_change found, all routed through the one inserter)")

print("L2. the inserter builds identity from the stored record")
ok, why = inserter_reads_identity(ROUTER_SRC)
check("L2 _log_payroll_change -> resolve_log_identity(employee_row=…, mint=_ensure_employee_id) before insert; "
      "row identity read from its answer", ok, why)

print("L3. a caller holding the stored row hands it over")
check("L3 every source_table='employees' call passes employee_row=", not l3, l3)

print("L4. every employees insert mints a business id")
check("L4 every insert into employees passes its result to _ensure_employee_id", not l4, l4)

print("L5. the helper's read is org-scoped")
check("L5 payroll_log_identity._one filters .eq('org_id', org_id) on employees",
      os.path.exists(HELPER) and helper_org_scoped(read(HELPER)), f"{os.path.relpath(HELPER, ROOT)} missing or unscoped")

print("L6. no SQL writer of the log")
migs = {f: read(os.path.join(MIGS, f)) for f in os.listdir(MIGS) if f.endswith(".sql")}
check("L6 no migration INSERTs into storeops.payroll_change_log", not sql_log_writers(migs), sql_log_writers(migs))

print("C. negative controls")
PREFIX_LOG = '''
def _log_payroll_change(org_id, *, field, entry_point, employee_id=None, employee_name=None,
                         store_code=None, work_date=None, before=None, after=None,
                         source_table=None, source_id=None, who=None, reason=None):
    try:
        row = {
            "org_id": org_id, "employee_id": employee_id, "employee_name": employee_name,
            "store_code": store_code, "work_date": (str(work_date)[:10] if work_date else None),
            "field": field, "before_value": (None if before is None else str(before)),
            "after_value": (None if after is None else str(after)),
            "entry_point": entry_point, "source_table": source_table,
        }
        sb().table("payroll_change_log").insert(row).execute()
    except Exception as e:
        print(e)
'''
check("C1 the PRE-FIX _log_payroll_change (row from loose params, main 2026-10-03) -> RED",
      not inserter_reads_identity(PREFIX_LOG)[0])
check("C2 the helper called AFTER the insert -> RED", not inserter_reads_identity(
    "def _log_payroll_change(org_id, *, employee_id=None):\n"
    "    row = {'employee_id': ident['employee_id'], 'entry_point': 1, 'before_value': 2}\n"
    "    sb().table('payroll_change_log').insert(row).execute()\n"
    "    ident = _log_identity.resolve_log_identity(sb(), org_id, employee_row=None, mint=_ensure_employee_id)\n")[0])
check("C3 the helper without the mint -> RED", not inserter_reads_identity(
    "def _log_payroll_change(org_id, *, employee_id=None):\n"
    "    ident = _log_identity.resolve_log_identity(sb(), org_id, employee_row=None)\n"
    "    row = {'employee_id': ident['employee_id'], 'entry_point': 1, 'before_value': 2}\n"
    "    sb().table('payroll_change_log').insert(row).execute()\n")[0])
check("C4 a second inserter elsewhere -> RED", bool(foreign_log_writers(
    "def sweep(o):\n    sb().table('payroll_change_log').insert({'org_id': o}).execute()\n", ROUTER)))
check("C5 a second row-builder elsewhere -> RED", bool(foreign_log_writers(
    "def build(o):\n    return {'entry_point': 'x', 'before_value': 1, 'employee_id': o}\n", ROUTER)))
check("C6 the pre-fix update_employee call (loose employee_id, no employee_row) -> RED", bool(employees_source_without_row(
    "def update_employee():\n    _log_payroll_change(o, field=f, entry_point='pay_basis_change',\n"
    "        employee_id=after.get('employee_id'), source_table='employees', source_id=1)\n", ROUTER)))
check("C7 a shift-table call (no stored row to hand over) -> GREEN", not employees_source_without_row(
    "def f():\n    _log_payroll_change(o, field=f, entry_point='shift_edit', employee_id=e, source_table='shifts')\n", ROUTER))
check("C8 the PRE-FIX Roles insert (core._ensure_employee, never minted) -> RED", bool(unminted_employee_inserts(
    "def _ensure_employee(client, org_id, email):\n"
    "    client.schema('storeops').table('employees').insert({'org_id': org_id, 'email': email}).execute()\n"
    "    return True\n", ROUTER)))
check("C9 an un-scoped helper read -> RED", not helper_org_scoped(
    "def _one(client, org_id, col, val):\n    return client.table(\"employees\").select('*').eq(col, val).execute()\n"))
check("C10 a SQL function inserting log rows -> RED; a comment mentioning it -> GREEN",
      (sql_log_writers({"x.sql": "INSERT INTO storeops.payroll_change_log (org_id) VALUES (1);"}),
       sql_log_writers({"y.sql": "-- INSERT INTO storeops.payroll_change_log is done by the app"})) == (["x.sql"], []))
check("C11 a table that merely STARTS with the name (mig 1054's own ledger) is not the log -> GREEN",
      sql_log_writers({"z.sql": "insert into storeops.payroll_change_log_id_backfill (log_id) select 1;"}) == [])

print(f"\n{PASS} passed, {FAIL} failed")
sys.exit(1 if FAIL else 0)
