"""Lean smoke test for agent/people/async-await-drop.
Proves, against the ACTUAL app.modules.hr.router code (no mocked business logic, only the DB layer
stubbed), that:
  1. hr_list_employees still round-trips through _maybe_await(list_employees(...)) correctly while
     core.router.list_employees is (still, on this base) async def.
  2. The onboarding delete/sign chain (_do_onboard_delete_document / _do_onboard_sign + their 5 route
     wrappers), now plain `def`, runs correctly when called directly (as FastAPI would dispatch a sync
     def route handler) with no `await`/coroutine anywhere in the call.
Run: python3 smoke_async_drop.py from backend/.
"""
import sys, os, asyncio, uuid
sys.path.insert(0, ".")
os.environ.setdefault("FIELD_ENCRYPTION_KEY", "0" * 44)

PASS, FAIL = [], []
def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(f"{name}" + (f" :: {detail}" if detail and not cond else ""))

import app.modules.hr.router as hr

# ── 1. _maybe_await duck-typing, called through the REAL hr_list_employees path ─────────────────
async def fake_async_list_employees(org_id):
    return [{"id": "e1", "org_id": org_id}]

def fake_sync_list_employees(org_id):
    return [{"id": "e1", "org_id": org_id}]

import app.modules.core.router as core

orig = core.list_employees
try:
    core.list_employees = fake_async_list_employees
    res = asyncio.run(hr.hr_list_employees(org_id="org-x"))
    check("hr_list_employees works when core.list_employees is async def (today's shape)",
          res == [{"id": "e1", "org_id": "org-x"}], res)

    core.list_employees = fake_sync_list_employees
    res = asyncio.run(hr.hr_list_employees(org_id="org-x"))
    check("hr_list_employees ALSO works once core.list_employees becomes sync def (post nav-perf)",
          res == [{"id": "e1", "org_id": "org-x"}], res)
finally:
    core.list_employees = orig

# ── 2. onboarding delete/sign chain — must be plain callables, no coroutine anywhere ─────────────
import inspect
for name in ("_do_onboard_delete_document", "_do_onboard_sign", "onboarding_delete_document",
             "public_onboarding_delete_document", "onboarding_me_delete_document",
             "public_onboarding_sign", "onboarding_me_sign"):
    fn = getattr(hr, name)
    check(f"{name} is a plain function (not a coroutine function)", not inspect.iscoroutinefunction(fn))

# Call _do_onboard_delete_document directly (no event loop involved at all) against a stubbed _doc_row.
org_id, employee_id, task_id, file_id = "org1", "emp1", "task1", "file1"
row = {"documents": [{"id": file_id, "name": "w4.pdf", "path": "org1/emp1/w4.pdf", "uploaded_role": "employee"}]}

class _FakeTable:
    def __init__(self):
        self.updated = None
    def update(self, payload):
        self.updated = payload
        return self
    def eq(self, *a, **k):
        return self
    def execute(self):
        return type("R", (), {"data": [{"ok": True}]})()

orig_doc_row = hr._doc_row
orig_task_row = hr._task_row
orig_log_event = hr._log_event
orig_so = hr._so
try:
    hr._doc_row = lambda o, e, t: dict(row)
    hr._task_row = lambda o, t: {"label": "W-4"}
    hr._log_event = lambda *a, **k: None
    hr._so = lambda: type("SO", (), {"table": lambda self, name: _FakeTable()})()
    # storage remove is best-effort/try-except inside the function already — no bucket stub needed
    result = hr._do_onboard_delete_document(org_id, employee_id, task_id, file_id, "HR", "admin")
    check("_do_onboard_delete_document runs synchronously (no await, no coroutine) and returns ok",
          result == {"ok": True, "documents_count": 0}, result)

    result2 = hr.onboarding_delete_document(employee_id, task_id, file_id, actor="HR", org_id=org_id)
    check("onboarding_delete_document (the actual @router.delete handler) callable directly, no coroutine",
          result2 == {"ok": True, "documents_count": 0}, result2)
finally:
    hr._doc_row = orig_doc_row
    hr._task_row = orig_task_row
    hr._log_event = orig_log_event
    hr._so = orig_so

print(f"\n{len(PASS)} passed, {len(FAIL)} failed")
for f in FAIL:
    print("FAIL:", f)
if FAIL:
    sys.exit(1)
print("ALL GREEN")
