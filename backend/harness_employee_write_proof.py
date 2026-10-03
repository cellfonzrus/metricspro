"""PROOF (DB-free, stdlib only) — two employee-write defects, fixed as designs (index §19.41, §19.42).

  python3 backend/harness_employee_write_proof.py

Every function under test is the REAL shipped source, AST-extracted from storeops/router.py,
hr/router.py and core/router.py and exec'd against an in-memory database that actually filters,
inserts and updates (a fake that ignored `.eq` would hide both defects). The pay gate's own module
(`storeops/pay_visibility.py`) and the identity helper (`storeops/payroll_log_identity.py`) are
imported for real. Each defect is first REPRODUCED with the pre-fix source transcribed from `main`
(2026-10-03), then shown fixed.

§A  PAY LOCK ON CREATE (owner decision 2026-10-03: "only people allowed to see pay may set pay when
    adding employees"). Pre-fix: POST /storeops/employees, /storeops/employees/bulk and /hr/employees
    wrote pay_rate with no check; bulk-payscale checked only "is a manager". Now every one goes
    through `gate_pay_write`, the policy `update_employee` already had.
§B  EMPLOYEE NUMBER MISSING IN THE PAYROLL CHANGE LOG (Vzone, 2026-10-02T21:23:19, id 237 'Shweta'
    logged with employee_id NULL while the record holds 'E237'). Pre-fix: the log row took whatever
    the caller held; update_employee logged BEFORE minting the business id. Now the one inserter builds
    identity from the stored record through `payroll_log_identity.resolve_log_identity`.
"""
import ast
import copy
import itertools
import os
import sys
import types

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
PASS = FAIL = 0


def check(label, got, want=True):
    global PASS, FAIL
    ok = got == want
    PASS += ok
    FAIL += (not ok)
    print(f"  {'PASS' if ok else 'FAIL'}  {label}" + ("" if ok else f"\n        got:  {got!r}\n        want: {want!r}"))


def section(t):
    print(f"\n── {t}")


# ── stdlib stubs for the modules the extracted functions reach ─────────────────────────────────────
class HTTPException(Exception):
    def __init__(self, status_code, detail=None):
        super().__init__(detail)
        self.status_code, self.detail = status_code, detail


_fastapi = types.ModuleType("fastapi")
_fastapi.HTTPException = HTTPException
_fastapi.Header = lambda default="": default
sys.modules.setdefault("fastapi", _fastapi)

ORG, OTHER = "VZ", "OTHER"
CALLERS = {   # token -> (uid, caller) ; role names exactly as tenants use them
    "Bearer admin": ("u-admin", {"org_id": ORG, "role": "admin", "email": "admin@x", "perms": {"scope": "all"}}),
    "Bearer mm": ("u-mm", {"org_id": ORG, "role": "market_manager", "email": "mm@x", "perms": {"scope": "market"}}),
    "Bearer dm": ("u-dm", {"org_id": ORG, "role": "district_manager", "email": "dm@x", "perms": {"scope": "market"}}),
    "Bearer sm": ("u-sm", {"org_id": ORG, "role": "store_manager", "email": "sm@x", "perms": {"scope": "store"}}),
    "Bearer sm-grant": ("u-smg", {"org_id": ORG, "role": "store_manager", "email": "smg@x",
                                  "perms": {"scope": "store", "data": {"employee_pay_rates": True}}}),
    "Bearer rep": ("u-rep", {"org_id": ORG, "role": "sales_rep", "email": "rep@x", "perms": {"scope": "self"}}),
}
BY_UID = {uid: c for uid, c in CALLERS.values()}

_core = types.ModuleType("app.modules.core.router")
_core._uid_from_token = lambda auth: (CALLERS.get(auth) or (None, None))[0]
_core._resolve_caller = lambda client, uid, org=None: copy.deepcopy(BY_UID.get(uid))
sys.modules["app.modules.core.router"] = _core
_tm = types.ModuleType("app.core.tenant_middleware")
_tm.caller_app_user_http = lambda uid, cols="": copy.deepcopy(BY_UID.get(uid))
_tm.caller_app_user = lambda uid, cols="": copy.deepcopy(BY_UID.get(uid))
sys.modules["app.core.tenant_middleware"] = _tm


# ── an in-memory PostgREST that really filters / inserts / updates ─────────────────────────────────
class _Resp:
    def __init__(self, data):
        self.data = data


class _Q:
    def __init__(self, db, name):
        self.db, self.name, self.filters, self.op, self.payload, self.n = db, name, [], "select", None, None

    def select(self, *a, **k):
        return self

    def eq(self, col, val):
        self.filters.append((col, lambda v, x=val: str(v) == str(x) if v is not None else False))
        return self

    def ilike(self, col, val):
        self.filters.append((col, lambda v, x=val: str(v or "").lower() == str(x).lower()))
        return self

    def is_(self, col, val):
        self.filters.append((col, lambda v: v is None))
        return self

    def limit(self, n):
        self.n = n
        return self

    def order(self, *a, **k):
        return self

    def insert(self, payload):
        self.op, self.payload = "insert", payload
        return self

    def update(self, payload):
        self.op, self.payload = "update", dict(payload)
        return self

    def delete(self):
        self.op = "delete"
        return self

    def _match(self, r):
        return all(f(r.get(c)) for c, f in self.filters)

    def execute(self):
        if self.name in self.db.fail:
            raise RuntimeError(f"{self.name} unavailable")
        rows = self.db.tables.setdefault(self.name, [])
        if self.op == "insert":
            out = []
            for p in (self.payload if isinstance(self.payload, list) else [self.payload]):
                r = dict(p)
                if self.name == "employees":
                    r.setdefault("id", next(self.db.ids))
                    r.setdefault("pay_rate", 0)          # mig 003: pay_rate NUMERIC DEFAULT 0
                    r.setdefault("employee_id", None)
                rows.append(r)
                out.append(copy.deepcopy(r))
            self.db.writes.append((self.name, "insert", copy.deepcopy(self.payload)))
            return _Resp(out)
        hit = [r for r in rows if self._match(r)]
        if self.op == "delete":
            self.db.tables[self.name] = [r for r in rows if not self._match(r)]
            return _Resp(copy.deepcopy(hit))
        if self.op == "update":
            for r in hit:
                r.update(self.payload)
            self.db.writes.append((self.name, "update", copy.deepcopy(self.payload)))
            return _Resp(copy.deepcopy(hit))
        return _Resp(copy.deepcopy(hit[: self.n] if self.n else hit))


class FakeDB:
    def __init__(self, employees=(), tenants=None, fail=()):
        self.ids = itertools.count(500)
        self.tables = {"employees": [dict(e) for e in employees],
                       "tenants": tenants if tenants is not None else [{"org_id": ORG, "pay_visibility": "manager_up"}],
                       "app_config": [{"id": 1, "rbac_enabled": True}], "roles": [], "payroll_change_log": []}
        self.writes, self.fail = [], set(fail)

    def schema(self, _name):
        return self

    def table(self, name):
        return _Q(self, name)

    @property
    def log(self):
        return self.tables["payroll_change_log"]

    def emp(self, pk):
        return next(r for r in self.tables["employees"] if str(r["id"]) == str(pk))


DB = [FakeDB()]
_dbmod = types.ModuleType("app.core.database")
_dbmod.get_supabase = lambda: DB[0]
sys.modules["app.core.database"] = _dbmod

from app.modules.storeops import pay_visibility as _payvis          # noqa: E402  (real)
from app.modules.storeops import payroll_log_identity as _log_identity   # noqa: E402  (real)
from app.modules.storeops import payroll_salary                     # noqa: E402  (real)


# ── load the shipped functions ─────────────────────────────────────────────────────────────────────
def _src(*parts):
    p = os.path.join(HERE, "app", "modules", *parts)
    with open(p, encoding="utf-8") as fh:
        return fh.read(), p


def _consts(src, names):
    out = {}
    for n in ast.parse(src).body:
        if isinstance(n, ast.Assign):
            for t in n.targets:
                if getattr(t, "id", None) in names:
                    out[t.id] = ast.literal_eval(n.value)
    return out


def _load_into(ns, parts, names):
    src, path = _src(*parts)
    found = {}
    for n in ast.parse(src).body:
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name in names:
            n.decorator_list = []
            found[n.name] = n
    missing = set(names) - set(found)
    if missing:
        raise SystemExit(f"FAIL — not found in {path}: {sorted(missing)}")
    exec(compile(ast.Module(body=list(found.values()), type_ignores=[]), path, "exec"), ns)
    return ns


SO_SRC, _ = _src("storeops", "router.py")
SO = types.ModuleType("app.modules.storeops.router")
SO.__dict__.update({"HTTPException": HTTPException, "Header": _fastapi.Header, "ORG_ID": ORG,
                    "_payvis": _payvis, "_log_identity": _log_identity, "payroll_salary": payroll_salary,
                    "get_supabase": lambda: DB[0], "sb": lambda: DB[0], "Any": object})
SO.__dict__.update(_consts(SO_SRC, {"EMP_FIELDS", "_PAY_GATED_FIELDS", "_PAY_LOGGED_FIELDS",
                                    "PAY_WRITE_REFUSED", "_SHIFT_LOGGED_FIELDS"}))
SO.BulkCreateEmployeesIn = SO.BulkPayscaleIn = types.SimpleNamespace
_load_into(SO.__dict__, ("storeops", "router.py"),
           ["gate_pay_write", "_require_manager", "create_employee", "bulk_create_employees",
            "update_employee", "bulk_payscale", "_ensure_employee_id", "_log_payroll_change",
            "_who_for_log", "set_employee_lunch_config", "_log_shift_edit", "delete_manual_hours"])
sys.modules["app.modules.storeops.router"] = SO

HR = {"HTTPException": HTTPException, "Header": _fastapi.Header, "ORG_ID": ORG, "_payvis": _payvis,
      "get_supabase": lambda: DB[0], "_so": lambda: DB[0]}


async def _maybe_await(v):
    return v


HR["_maybe_await"] = _maybe_await
_load_into(HR, ("hr", "router.py"), ["hr_create_employee"])
CORE = {}
_load_into(CORE, ("core", "router.py"), ["_ensure_employee"])


def run(db, fn, *a, **k):
    DB[0] = db
    import asyncio
    import inspect
    r = fn(*a, **k)
    return asyncio.run(r) if inspect.iscoroutine(r) else r


def status_of(db, fn, *a, **k):
    try:
        run(db, fn, *a, **k)
        return 200
    except HTTPException as e:
        return e.status_code


def new_people(db):
    return [r for r in db.tables["employees"] if r["id"] >= 500]


# ── transcribed PRE-FIX sources (main, 2026-10-03) — the reproductions ──────────────────────────────
PREFIX_CREATE = '''
def create_employee(emp, org_id=ORG_ID):
    row = {k: emp[k] for k in EMP_FIELDS if k in emp}
    if not (row.get("name") or "").strip():
        raise HTTPException(400, "name required")
    row["org_id"] = org_id
    if row.get("is_active") is None:
        row["is_active"] = True
    if not (row.get("employee_id") or "").strip():
        row.pop("employee_id", None)
    r = sb().table("employees").insert(row).execute()
    return _ensure_employee_id(r.data[0]) if r.data else row
'''
PREFIX_LOG = '''
def _log_payroll_change(org_id, *, field, entry_point, employee_id=None, employee_name=None,
                         store_code=None, work_date=None, before=None, after=None,
                         source_table=None, source_id=None, who=None, reason=None, employee_row=None):
    row = {"org_id": org_id, "employee_id": employee_id, "employee_name": employee_name,
           "store_code": store_code, "field": field, "before_value": before, "after_value": after,
           "entry_point": entry_point, "source_table": source_table, "source_id": source_id}
    sb().table("payroll_change_log").insert(row).execute()
'''
# update_employee's pre-fix tail, verbatim in order: log from the UPDATE echo, THEN mint.
PREFIX_UPDATE_TAIL = '''
def prefix_tail(org_id, row, before, after):
    for f in _PAY_LOGGED_FIELDS:
        if f in row and str(before.get(f) or "") != str(after.get(f) or ""):
            _log_payroll_change(org_id, field=f, entry_point="pay_basis_change",
                                 employee_id=after.get("employee_id"), employee_name=after.get("name"),
                                 before=before.get(f), after=after.get(f),
                                 source_table="employees", source_id=after.get("id"), who={})
    return _ensure_employee_id(after)
'''


def prefix_ns():
    ns = dict(SO.__dict__)
    exec(PREFIX_CREATE + PREFIX_LOG + PREFIX_UPDATE_TAIL, ns)
    return ns


ALICE = {"id": 1, "org_id": ORG, "employee_id": "E1", "name": "Alice", "home_store": "S1",
         "pay_rate": 18.0, "pay_basis": "hourly", "pay_amount": None}

# ══════════════════════════════════════════════════════════════════════════════════════════════════
section("A0. the defect, reproduced on the pre-fix create path")
db = FakeDB([ALICE])
run(db, prefix_ns()["create_employee"], {"name": "Newbie", "pay_rate": 25.0})
check("A0  PRE-FIX: a district manager (below the pay line) adding a person WROTE pay_rate 25 — no check at all",
      new_people(db)[0]["pay_rate"], 25.0)

section("A1. POST /storeops/employees — the shipped create goes through the gate")
db = FakeDB([ALICE])
out = run(db, SO.create_employee, {"name": "Newbie", "pay_rate": 25.0}, authorization="Bearer dm", org_id=ORG)
check("A1a below the pay line (DM): the person IS added", [p["name"] for p in new_people(db)], ["Newbie"])
check("A1b ...but the rate is NOT written (the column keeps its default)", new_people(db)[0]["pay_rate"], 0)
check("A1c ...and the reply NAMES it — never silently dropped", out.get("pay_fields_ignored"), ["pay_rate"])
check("A1d ...and the echo carries no pay figure for a caller who may not see pay", "pay_rate" in out, False)
check("A1e ...and the new person has a business id", out.get("employee_id"), f"E{new_people(db)[0]['id']}")
db = FakeDB([ALICE])
out = run(db, SO.create_employee, {"name": "Newbie", "pay_rate": 25.0}, authorization="Bearer sm", org_id=ORG)
check("A1f store manager (below the pay line): same — added, rate dropped and named",
      (new_people(db)[0]["pay_rate"], out.get("pay_fields_ignored")), (0, ["pay_rate"]))
for tok in ("Bearer admin", "Bearer mm", "Bearer sm-grant"):
    db = FakeDB([ALICE])
    out = run(db, SO.create_employee, {"name": "Newbie", "pay_rate": 25.0, "pay_basis": "hourly"},
              authorization=tok, org_id=ORG)
    check(f"A1g {tok.split()[1]} (allowed to see pay): the rate IS written, nothing reported ignored",
          (new_people(db)[0]["pay_rate"], out.get("pay_fields_ignored")), (25.0, None))
db = FakeDB([ALICE], tenants=[{"org_id": ORG, "pay_visibility": "manager_up", "pay_visible_roles": ["district_manager"]}])
run(db, SO.create_employee, {"name": "Newbie", "pay_rate": 25.0}, authorization="Bearer dm", org_id=ORG)
check("A1h CONFIG, NEVER CODE: the org lists its DM role as pay-visible -> the DM's create writes the rate",
      new_people(db)[0]["pay_rate"], 25.0)
db = FakeDB([ALICE])
check("A1i a plain rep sending a rate is refused like update_employee refuses it (403, nothing added)",
      (status_of(db, SO.create_employee, {"name": "N", "pay_rate": 25.0}, authorization="Bearer rep", org_id=ORG),
       new_people(db)), (403, []))
db = FakeDB([ALICE])
run(db, SO.create_employee, {"name": "NoPay", "phone": "1"}, authorization="", org_id=ORG)
check("A1j a create with NO pay field resolves no auth and is unchanged (the StoreOps screens now send none)",
      [p["name"] for p in new_people(db)], ["NoPay"])
db = FakeDB([ALICE])
run(db, SO.create_employee, {"name": "T", "termination_date": "2026-12-31"}, authorization="Bearer dm", org_id=ORG)
check("A1k every gated field is gated, not only pay_rate (termination_date dropped for the DM)",
      "termination_date" in new_people(db)[0], False)

section("A2. POST /storeops/employees/bulk — the sheet upload")
rows = [{"name": "B1", "pay_rate": 20.0}, {"name": "B2", "pay_rate": 21.0, "pay_basis": "hourly"}, {"name": "B3"}]
db = FakeDB([ALICE])
out = run(db, SO.bulk_create_employees, types.SimpleNamespace(employees=rows, rows=None),
          authorization="Bearer dm", org_id=ORG)
check("A2a below the pay line: all three people added", out.get("inserted"), 3)
check("A2b ...no row carries the uploaded rate", [p["pay_rate"] for p in new_people(db)], [0, 0, 0])
check("A2c ...pay_basis was not written either", any("pay_basis" in p for p in new_people(db)), False)
check("A2d ...and the reply names every dropped column once", out.get("pay_fields_ignored"), ["pay_basis", "pay_rate"])
check("A2e ...each bulk-added person was minted a business id", all(p["employee_id"] for p in new_people(db)), True)
db = FakeDB([ALICE])
out = run(db, SO.bulk_create_employees, types.SimpleNamespace(employees=rows, rows=None),
          authorization="Bearer admin", org_id=ORG)
check("A2f admin: the rates land, nothing reported", ([p["pay_rate"] for p in new_people(db)], out.get("pay_fields_ignored")),
      ([20.0, 21.0, 0], None))
db = FakeDB([ALICE])
check("A2g the caller's own rows are never mutated by the gate", (run(db, SO.gate_pay_write, rows, "Bearer dm", ORG)[0][0],
                                                                    rows[0]), ({"name": "B1"}, {"name": "B1", "pay_rate": 20.0}))

section("A3. POST /hr/employees — HR · People, the single front door")
db = FakeDB([ALICE])
out = run(db, HR["hr_create_employee"], {"name": "Hira", "pay_rate": 30.0}, org_id=ORG, authorization="Bearer dm")
check("A3a below the pay line: added, rate not written, named back at the top of the reply",
      (new_people(db)[0]["name"], new_people(db)[0]["pay_rate"], out.get("pay_fields_ignored")), ("Hira", 0, ["pay_rate"]))
check("A3b ...and the returned employee carries no pay figure", "pay_rate" in out["employee"], False)
db = FakeDB([ALICE])
out = run(db, HR["hr_create_employee"], {"name": "Hira", "pay_rate": 30.0}, org_id=ORG, authorization="Bearer mm")
check("A3c market manager: the rate lands", (new_people(db)[0]["pay_rate"], out.get("pay_fields_ignored")), (30.0, None))

section("A4. POST /storeops/employees/bulk-payscale — it checked only 'is a manager'")
db = FakeDB([ALICE])
check("A4a below the pay line: refused outright (403) — every payload is pay-only",
      status_of(db, SO.bulk_payscale, types.SimpleNamespace(rows=[{"employee_id": "E1", "pay_rate": 99}], employees=None),
                authorization="Bearer dm", org_id=ORG), 403)
check("A4b ...and the rate is untouched", db.emp(1)["pay_rate"], 18.0)
db = FakeDB([ALICE])
out = run(db, SO.bulk_payscale, types.SimpleNamespace(rows=[{"employee_id": "E1", "pay_rate": 99}], employees=None),
          authorization="Bearer admin", org_id=ORG)
check("A4c admin: updated", (out["updated"], db.emp(1)["pay_rate"]), (1, 99.0))
check("A4d the refusal sentence is the one shared constant", SO.PAY_WRITE_REFUSED.startswith("Your role can't set pay"), True)

section("A5. PATCH /storeops/employees/{id} — unchanged behavior, now through the same gate")
db = FakeDB([ALICE])
out = run(db, SO.update_employee, "1", {"phone": "5", "pay_rate": None}, authorization="Bearer dm", org_id=ORG)
check("A5a DM mixed save: phone lands, pay untouched, pay named (the destruction path stays closed)",
      (db.emp(1)["phone"], db.emp(1)["pay_rate"], out.get("pay_fields_ignored")), ("5", 18.0, ["pay_rate"]))
db = FakeDB([ALICE])
check("A5b DM pay-only save: 403", status_of(db, SO.update_employee, "1", {"pay_rate": 40}, authorization="Bearer dm", org_id=ORG), 403)
db = FakeDB([ALICE])
run(db, SO.update_employee, "1", {"pay_rate": 40}, authorization="Bearer admin", org_id=ORG)
check("A5c admin pay save lands", db.emp(1)["pay_rate"], 40)

# ══════════════════════════════════════════════════════════════════════════════════════════════════
SHWETA = {"id": 237, "org_id": ORG, "employee_id": None, "name": "Shweta", "home_store": "S7",
          "pay_rate": 0, "pay_basis": "hourly", "pay_amount": None}
SS = {"id": 240, "org_id": ORG, "employee_id": "E240", "name": "ss", "home_store": "S7",
      "pay_rate": 0, "pay_basis": "hourly", "pay_amount": None}

section("B0. the defect, reproduced with the pre-fix log + pre-fix ordering")
db = FakeDB([SHWETA, SS])
ns = prefix_ns()
after = dict(SHWETA, pay_basis="monthly", pay_amount=20000.0)
DB[0] = db
ns["prefix_tail"](ORG, {"pay_basis": "monthly", "pay_amount": 20000.0}, dict(SHWETA), after)
check("B0a PRE-FIX: both of Shweta's rows were logged with employee_id NULL (the owner's evidence)",
      [r["employee_id"] for r in db.log], [None, None])
check("B0b ...while the record was minted 'E237' a moment later", db.emp(237)["employee_id"], "E237")

section("B1. the owner's exact save, through the shipped update_employee + _log_payroll_change")
db = FakeDB([SHWETA, SS])
run(db, SO.update_employee, "237", {"pay_basis": "monthly", "pay_amount": 20000}, authorization="Bearer admin", org_id=ORG)
run(db, SO.update_employee, "240", {"pay_basis": "monthly", "pay_amount": 10}, authorization="Bearer admin", org_id=ORG)
check("B1a Shweta's two rows now carry E237", [(r["field"], r["employee_id"]) for r in db.log if r["source_id"] == "237"],
      [("pay_basis", "E237"), ("pay_amount", "E237")])
check("B1b ss's rows still carry E240 (the row that was right stays right)",
      {r["employee_id"] for r in db.log if r["source_id"] == "240"}, {"E240"})
check("B1c the stored record and the log agree", db.emp(237)["employee_id"], "E237")
check("B1d the employee-level row now carries the person's home store (was NULL)",
      {r["store_code"] for r in db.log}, {"S7"})
check("B1e the actor is still recorded", {r["changed_by_email"] for r in db.log}, {"admin@x"})

section("B2. siblings — every writer, same answer")
db = FakeDB([dict(SHWETA)])
run(db, SO.bulk_payscale, types.SimpleNamespace(rows=[{"name": "Shweta", "pay_rate": 17}], employees=None),
    authorization="Bearer admin", org_id=ORG)
check("B2a bulk_payscale for a person with no business id: minted, logged as E237",
      (db.log[0]["entry_point"], db.log[0]["employee_id"], db.emp(237)["employee_id"]), ("bulk_payscale", "E237", "E237"))
db = FakeDB([dict(SHWETA, lunch_deduction_enabled=None, lunch_deduction_minutes=None)])
run(db, SO.set_employee_lunch_config, "237", {"enabled": True}, authorization="Bearer admin", org_id=ORG)
check("B2b per-employee lunch override (it logged before.employee_id): now E237",
      (db.log[0]["entry_point"], db.log[0]["employee_id"]), ("lunch_deduction_config", "E237"))
db = FakeDB([dict(SHWETA, employee_id="E237")])
DB[0] = db
SO._log_shift_edit(ORG, {"id": 9, "employee_id": "237", "employee_name": "Shweta K.", "store_code": "S2",
                         "shift_date": "2026-10-01", "scheduled_hours": 8},
                   {"id": 9, "employee_id": "237", "employee_name": "Shweta K.", "store_code": "S2",
                    "shift_date": "2026-10-01", "scheduled_hours": 6}, {"email": "dm@x", "role": "district_manager"})
check("B2c shift edit on a Schedule-page shift (employee_id = the NUMERIC pk '237'): logged as E237, stored name",
      (db.log[0]["employee_id"], db.log[0]["employee_name"]), ("E237", "Shweta"))
check("B2d ...and the row keeps the EVENT's store (where the shift was), not the home store", db.log[0]["store_code"], "S2")
db = FakeDB([dict(SHWETA, employee_id="E237")])
run(db, SO._log_payroll_change, ORG, field="manual_hours", entry_point="manual_hours_add", employee_id="E237",
    work_date="2026-10-01", after=3, source_table="manual_hours")
check("B2e manual-hours add (it passed no name at all): the stored name is filled in",
      (db.log[0]["employee_id"], db.log[0]["employee_name"]), ("E237", "Shweta"))
db = FakeDB([])
run(db, SO._log_payroll_change, ORG, field="clock_out", entry_point="force_clockout_cron", employee_id="E999",
    employee_name="Gone Person", store_code="S1", source_table="timelog", source_id="t1")
check("B2f a person no longer on the roster: the caller's values are kept, the row is still written",
      (db.log[0]["employee_id"], db.log[0]["employee_name"], db.log[0]["store_code"]), ("E999", "Gone Person", "S1"))
db = FakeDB([])
run(db, SO._log_payroll_change, ORG, field="lunch_deduction_enabled", entry_point="lunch_deduction_config",
    before=False, after=True, source_table="tenants")
check("B2g a tenant-level row (no person): written with no identity, as before",
      (len(db.log), db.log[0]["employee_id"]), (1, None))

db = FakeDB([dict(SHWETA, employee_id="E237")])
db.tables["manual_hours"] = [{"id": "m1", "org_id": ORG, "employee_id": "E237", "work_date": "2026-10-01", "hours": 2.5}]
run(db, SO.delete_manual_hours, "m1", authorization="Bearer admin", org_id=ORG)
run(db, SO.delete_manual_hours, "m1", authorization="Bearer admin", org_id=ORG)   # the double-click
check("B2h manual-hours delete: the real delete is logged once, with the person; the repeat DELETE of an "
      "entry already gone logs NOTHING (it used to log a delete with no person, date or hours)",
      [(r["entry_point"], r["employee_id"], r["before_value"]) for r in db.log],
      [("manual_hours_delete", "E237", "2.5")])

section("B3. org scoping, the numeric/business collision rule, never raising")
db = FakeDB([dict(SHWETA, employee_id="E237"),
             {"id": 900, "org_id": OTHER, "employee_id": "E555", "name": "Other Org Person", "home_store": "X"}])
run(db, SO._log_payroll_change, ORG, field="f", entry_point="shift_edit", employee_id="E555", employee_name="hint",
    source_table="shifts")
check("B3a another tenant's person is never borrowed (org-scoped lookup) — the hint stays",
      (db.log[0]["employee_id"], db.log[0]["employee_name"]), ("E555", "hint"))
db = FakeDB([{"id": 42, "org_id": ORG, "employee_id": "E42", "name": "Pk Forty-Two", "home_store": "S1"},
             {"id": 77, "org_id": ORG, "employee_id": "42", "name": "Business Forty-Two", "home_store": "S2"}])
run(db, SO._log_payroll_change, ORG, field="f", entry_point="shift_edit", employee_id="42", source_table="shifts")
check("B3b '42' is someone's BUSINESS id -> that person, never pk 42 (payroll_identity's collision rule)",
      db.log[0]["employee_name"], "Business Forty-Two")
db = FakeDB([dict(SHWETA, employee_id="E237")], fail=("employees",))
run(db, SO._log_payroll_change, ORG, field="f", entry_point="shift_edit", employee_id="E237", employee_name="Shweta",
    source_table="shifts")
check("B3c the roster read fails -> the log row is STILL written from the hints (best-effort, never a 500)",
      (len(db.log), db.log[0]["employee_id"]), (1, "E237"))
check("B3d pure core: stored values win, hints fill gaps, event store beats home store",
      _log_identity.identity_from_stored({"employee_id": "E1", "name": "A", "home_store": "H"},
                                         hint_employee_id="1", hint_name="a.", hint_store="S"),
      {"employee_id": "E1", "employee_name": "A", "store_code": "S"})

section("B4. the source of NULL business ids — the Roles path now mints")
db = FakeDB([])
DB[0] = db
CORE["_ensure_employee"](db, ORG, "new@x", full_name="Via Roles")
check("B4a a person added through Roles & Access gets E<pk> (it got none, which is how 237 had none)",
      new_people(db)[0]["employee_id"], f"E{new_people(db)[0]['id']}")
_pre = {}
exec(compile(ast.parse('''
def _ensure_employee(client, org_id, email, full_name=None, store_code=None, emp_emails=None):
    email = (email or "").strip().lower()
    client.schema("storeops").table("employees").insert({
        "org_id": org_id, "name": (full_name or email), "email": email,
        "home_store": store_code or None, "is_active": True,
    }).execute()
    return True
'''), "prefix", "exec"), _pre)
db = FakeDB([])
_pre["_ensure_employee"](db, ORG, "new@x", full_name="Via Roles")
check("B4b PRE-FIX control: the Roles insert left employee_id NULL", new_people(db)[0]["employee_id"], None)

print(f"\n{PASS} passed, {FAIL} failed")
sys.exit(1 if FAIL else 0)
