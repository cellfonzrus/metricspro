"""Pure-logic proof harness for people-4 (multi-file onboarding documents, migration 402).

Runs the ACTUAL shipped functions from app.modules.hr.router (_do_onboard_upload,
_do_onboard_delete_document, _employee_can_delete_document) against a tiny in-memory fake Supabase
client + fake storage backend — no live DB/network. Run: `python3 harness_multifile_docs.py` from
backend/.

Proves:
  1. Append-not-replace: two uploads to the same task both survive in `documents`; the FIRST file's
     storage object is never removed by the second upload (this is the root-cause fix itself).
  2. Employee-delete truth table (_employee_can_delete_document, pure function) — all 5 statuses x 3
     uploaded_role values.
  3. End-to-end delete via _do_onboard_delete_document: employee can delete their own file only while
     'pending', can never delete an admin-uploaded file, admin can delete either at any status; a
     successful delete actually removes the storage object, a rejected one leaves it untouched; every
     delete (accepted or not attempted) that DOES go through is audited via onboarding_event.
  4. Migration 402's single-document -> documents[0] backfill transform (translated 1:1 from the SQL,
     re-run twice to prove idempotency).
  5. ZIP export file-naming rule (unsuffixed for a single file, -1/-2/... for multiple) matches the
     literal expression in onboarding_compliance_export.
"""
import asyncio
import os
import sys

# Anchored to THIS FILE's directory, not the shell's cwd, so the harness runs identically from
# `backend/` and from the repo root (commit 564c171f). Run from the root, the old cwd-relative
# sys.path + open() died with FileNotFoundError, which reads as "not run" rather than "failed".
_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _HERE)

PASS = []
FAIL = []


def check(name, cond, detail=""):
    if cond:
        PASS.append(name)
    else:
        FAIL.append(f"{name} :: {detail}")


# ── Fake Supabase client ─────────────────────────────────────────────────────────────────────────
class FakeQuery:
    def __init__(self, store, table):
        self.store = store
        self.table_name = table
        self.filters = []
        self._limit = None
        self._select_cols = "*"
        self._mode = None
        self._payload = None
        self._on_conflict = None

    def select(self, cols):
        self._select_cols = cols
        self._mode = "select"
        return self

    def eq(self, k, v):
        self.filters.append((k, v))
        return self

    def ilike(self, k, v):
        self.filters.append((k, v))
        return self

    def limit(self, n):
        self._limit = n
        return self

    def order(self, *a, **k):
        return self

    def _match(self, row):
        return all(row.get(k) == v for k, v in self.filters)

    def upsert(self, payload, on_conflict=None):
        self._mode = "upsert"
        self._payload = payload
        self._on_conflict = (on_conflict or "").split(",")
        return self

    def update(self, payload):
        self._mode = "update"
        self._payload = payload
        return self

    def insert(self, payload):
        self._mode = "insert"
        self._payload = payload
        return self

    def delete(self):
        self._mode = "delete"
        return self

    def execute(self):
        rows = self.store.setdefault(self.table_name, [])
        if self._mode == "select":
            matched = [r for r in rows if self._match(r)]
            if self._limit:
                matched = matched[: self._limit]
            return FakeResult(matched)
        if self._mode == "insert":
            self.store[self.table_name].append(dict(self._payload))
            return FakeResult([self._payload])
        if self._mode == "upsert":
            key_vals = {k: self._payload.get(k) for k in self._on_conflict}
            existing = next((r for r in rows if all(r.get(k) == v for k, v in key_vals.items())), None)
            if existing:
                existing.update(self._payload)
            else:
                rows.append(dict(self._payload))
            return FakeResult([self._payload])
        if self._mode == "update":
            matched = [r for r in rows if self._match(r)]
            for r in matched:
                r.update(self._payload)
            return FakeResult(matched)
        if self._mode == "delete":
            matched = [r for r in rows if self._match(r)]
            self.store[self.table_name] = [r for r in rows if r not in matched]
            return FakeResult(matched)
        raise RuntimeError("no mode set")


class FakeResult:
    def __init__(self, data):
        self.data = data


class FakeSchemaTable:
    def __init__(self, store):
        self.store = store

    def table(self, name):
        return FakeQuery(self.store, name)


class FakeBucket:
    def __init__(self, objects):
        self.objects = objects   # path -> bytes

    def upload(self, path, data, opts=None):
        self.objects[path] = data
        return {"path": path}

    def remove(self, paths):
        for p in paths:
            self.objects.pop(p, None)
        return {"removed": paths}

    def download(self, path):
        if path not in self.objects:
            raise Exception("not found")
        return self.objects[path]

    def create_signed_url(self, path, expires):
        return {"signedURL": f"https://signed/{path}?e={expires}"}

    def list(self, prefix):
        out = []
        for p in self.objects:
            if p.startswith(prefix + "/"):
                name = p[len(prefix) + 1:]
                out.append({"name": name, "id": "x", "updated_at": "2026-01-01T00:00:00",
                           "metadata": {"size": len(self.objects[p])}})
        return out


class FakeStorage:
    def __init__(self, objects):
        self.objects = objects
        self._bucket = FakeBucket(objects)

    def get_bucket(self, name):
        return {"name": name}

    def create_bucket(self, name):
        return {"name": name}

    def from_(self, name):
        return self._bucket


class FakeClient:
    def __init__(self):
        self.store = {}
        self.objects = {}
        self.storage = FakeStorage(self.objects)

    def schema(self, name):
        return FakeSchemaTable(self.store)

    def table(self, name):
        return FakeQuery(self.store, name)


class FakeUploadFile:
    def __init__(self, filename, data, content_type="image/jpeg"):
        self.filename = filename
        self._data = data
        self.content_type = content_type

    async def read(self):
        return self._data


JPEG = b"\xff\xd8\xff" + b"\x00" * 40   # sniffable JPEG header, well under any size check


# ── Wire the fake client into app.modules.hr.router ─────────────────────────────────────────────
import app.modules.hr.router as hr   # noqa: E402

fake = FakeClient()
hr.get_supabase = lambda: fake

async def _drain(aw):
    return await aw


def _call(fn):
    """Call a helper WITHOUT caring whether it is `async def` today.

    `hr._do_onboard_delete_document` was `async def` when this harness was written and is now a
    plain `def` — the correct shape: unlike `_do_onboard_upload` (which MUST stay async, because it
    awaits `UploadFile.read()`), the delete path awaits nothing and only drives the synchronous
    supabase/storage clients. The hard-coded `await` here therefore blew up with `TypeError: object
    dict can't be used in 'await' expression` on the first SUCCESSFUL delete, killing t3e/t3f/t3g
    and everything after them. Note the two REJECTION probes (t3b/t3d) kept passing throughout —
    the HTTPException is raised before the bad `await` is ever evaluated — so this file reported
    "some passed" while its positive path had not run for months.
    """
    def _inner(*a, **k):
        import inspect
        r = fn(*a, **k)
        return asyncio.run(_drain(r)) if inspect.isawaitable(r) else r
    return _inner


ORG = "org-1"
EMP = "emp-1"
TASK = "ss_card"
hr._so().table("onboarding_task").upsert(
    {"org_id": ORG, "id": TASK, "label": "Social Security Card", "requires_signature": False}, on_conflict="org_id,id"
).execute()


# ── 1. Append-not-replace ────────────────────────────────────────────────────────────────────────
async def t1():
    r1 = await hr._do_onboard_upload(ORG, EMP, TASK, FakeUploadFile("front.jpg", JPEG), "Jose Utero", uploaded_role="employee")
    row = hr._doc_row(ORG, EMP, TASK)
    check("t1a: first upload lands in documents[]", len(row.get("documents") or []) == 1, row)
    path_a = row["documents"][0]["path"]
    check("t1b: file A actually in fake storage", path_a in fake.objects)

    r2 = await hr._do_onboard_upload(ORG, EMP, TASK, FakeUploadFile("back.jpg", JPEG), "Jose Utero", uploaded_role="employee")
    row = hr._doc_row(ORG, EMP, TASK)
    docs = row.get("documents") or []
    check("t1c: BOTH files present after 2nd upload (append, not replace)", len(docs) == 2, docs)
    names = sorted(f["name"] for f in docs)
    check("t1d: both original filenames preserved", names == ["back.jpg", "front.jpg"], names)
    check("t1e: old file A NOT removed from storage by the 2nd upload (root-cause fix)", path_a in fake.objects)
    check("t1f: document_path mirrors the LATEST file (back-compat)", row.get("document_path") == docs[1]["path"], row)
    check("t1g: response reports file_id + running count", r2.get("file_id") and r2.get("documents_count") == 2, r2)


asyncio.run(t1())


# ── 2. Employee-delete truth table (pure function) ───────────────────────────────────────────────
STATUSES = ["pending", "submitted", "verified", "na", "returned"]
ROLES = ["employee", "admin", "recovered", None]
EXPECTED = {("pending", "employee"): True}   # every other combination is False
for s in STATUSES:
    for role in ROLES:
        expect = EXPECTED.get((s, role), False)
        got = hr._employee_can_delete_document(s, role)
        check(f"t2: status={s} role={role} -> {expect}", got == expect, got)


# ── 3/4. End-to-end delete permission + storage removal + audit ─────────────────────────────────
async def t3():
    fake.store["employee_onboarding"] = []
    fake.store["onboarding_event"] = []
    fake.objects.clear()
    r_emp = await hr._do_onboard_upload(ORG, EMP, TASK, FakeUploadFile("emp_file.jpg", JPEG), "Jose", uploaded_role="employee")
    r_adm = await hr._do_onboard_upload(ORG, EMP, TASK, FakeUploadFile("hr_file.jpg", JPEG), "HR Team", uploaded_role="admin")
    row = hr._doc_row(ORG, EMP, TASK)
    emp_file_id = next(f["id"] for f in row["documents"] if f["uploaded_role"] == "employee")
    adm_file_id = next(f["id"] for f in row["documents"] if f["uploaded_role"] == "admin")
    check("t3a: status is 'pending' before either file existed... now 'submitted' after upload",
          row["status"] == "submitted", row["status"])

    # status is 'submitted' right now (real, unchanged mechanic) -> employee delete of THEIR OWN file
    # must be rejected per the literal spec (submitted/returned/verified/na all lock employee-delete).
    try:
        _call(hr._do_onboard_delete_document)(ORG, EMP, TASK, emp_file_id, "employee", "employee")
        check("t3b: employee delete rejected while status=submitted", False, "did not raise")
    except Exception as e:
        check("t3b: employee delete rejected while status=submitted", getattr(e, "status_code", None) == 403, e)
    check("t3c: file NOT removed from storage on a rejected delete",
          any(f["id"] == emp_file_id for f in hr._doc_row(ORG, EMP, TASK)["documents"]))

    # HR resets the task back to 'pending' (existing "Reset" action) with files still attached — this is
    # the real, non-vacuous window in which the employee-delete gate opens.
    fake.table("employee_onboarding").upsert(
        {"org_id": ORG, "employee_id": EMP, "task_id": TASK, "status": "pending"},
        on_conflict="org_id,employee_id,task_id").execute()

    # Employee still cannot delete the ADMIN-uploaded file, even while pending.
    try:
        _call(hr._do_onboard_delete_document)(ORG, EMP, TASK, adm_file_id, "employee", "employee")
        check("t3d: employee cannot delete an admin-uploaded file", False, "did not raise")
    except Exception as e:
        check("t3d: employee cannot delete an admin-uploaded file", getattr(e, "status_code", None) == 403, e)

    # Employee CAN delete their own file now that the task is back to 'pending'.
    path_emp = next(f["path"] for f in hr._doc_row(ORG, EMP, TASK)["documents"] if f["id"] == emp_file_id)
    res = _call(hr._do_onboard_delete_document)(ORG, EMP, TASK, emp_file_id, "employee", "employee")
    check("t3e: employee CAN delete their own file while pending", res.get("documents_count") == 1, res)
    check("t3f: storage object actually removed on a successful delete", path_emp not in fake.objects)
    row = hr._doc_row(ORG, EMP, TASK)
    check("t3g: document_path mirror updated after delete", row.get("document_path") != path_emp, row)

    # Admin can delete the remaining (admin-owned) file regardless of status.
    fake.table("employee_onboarding").upsert(
        {"org_id": ORG, "employee_id": EMP, "task_id": TASK, "status": "verified"},
        on_conflict="org_id,employee_id,task_id").execute()
    path_adm = next(f["path"] for f in hr._doc_row(ORG, EMP, TASK)["documents"] if f["id"] == adm_file_id)
    res2 = _call(hr._do_onboard_delete_document)(ORG, EMP, TASK, adm_file_id, "HR Team", "admin")
    check("t3h: admin CAN delete even while status=verified", res2.get("documents_count") == 0, res2)
    check("t3i: admin delete also removes the storage object", path_adm not in fake.objects)

    events = [e for e in fake.store["onboarding_event"] if e.get("event_type") == "doc_deleted"]
    check("t3j: every accepted delete is audited (2 doc_deleted events, none for the 2 rejections)",
          len(events) == 2, events)
    check("t3k: audit rows record actor + role", events[0]["actor"] == "employee" and events[0]["detail"]["deleted_by_role"] == "employee")
    check("t3l: second audit row is the admin delete", events[1]["detail"]["deleted_by_role"] == "admin")


asyncio.run(t3())


# ── 5. Migration 402 backfill transform (translated 1:1 from the SQL) idempotency ───────────────
def simulate_backfill(row):
    """Mirrors 402_hr_multifile_documents.sql's UPDATE exactly: only touches a row whose `documents`
    is null/empty AND whose document_path is set."""
    docs = row.get("documents")
    if row.get("document_path") and (docs is None or docs == []):
        row["documents"] = [{
            "id": "generated-uuid", "path": row["document_path"], "name": row.get("document_name"),
            "content_type": None, "uploaded_at": row.get("submitted_at") or row.get("updated_at") or "now",
            "uploaded_by": None, "uploaded_role": "unknown"}]
    return row


pre402_row = {"document_path": "org/emp/abc_ssn.jpg", "document_name": "ssn.jpg",
              "submitted_at": "2026-06-01", "documents": None}
after1 = simulate_backfill(dict(pre402_row))
check("t5a: backfill creates documents[0] from the legacy single doc", len(after1["documents"]) == 1, after1)
check("t5b: backfilled entry's uploaded_role is 'unknown' (conservative — never employee-self-delete-eligible)",
      after1["documents"][0]["uploaded_role"] == "unknown")
after2 = simulate_backfill(dict(after1))
check("t5c: idempotent — re-running on an already-migrated row is a no-op", after2["documents"] == after1["documents"], after2)

no_doc_row = {"document_path": None, "document_name": None, "documents": None}
after3 = simulate_backfill(dict(no_doc_row))
check("t5d: a task with nothing uploaded is untouched (documents stays empty)", after3["documents"] is None, after3)


# ── 6. ZIP export naming rule (mirrors onboarding_compliance_export's literal expression) ────────
import re as _re  # noqa: E402

with open(os.path.join(_HERE, "app/modules/hr/router.py"), encoding="utf-8") as fh:
    src = fh.read()
check("t6a: export's suffix expression is present verbatim (kept in sync with this proof)",
      'suffix = f"-{i + 1}" if n > 1 else ""' in src)


def zip_name(doc_label, i, n, ext):
    safe_doc = _re.sub(r"[^A-Za-z0-9 _.-]+", "_", doc_label).strip() or "document"
    suffix = f"-{i + 1}" if n > 1 else ""
    return f"{safe_doc}{suffix}.{ext}"


check("t6b: single file -> unsuffixed (back-compat, no regression)", zip_name("SS Card", 0, 1, "jpg") == "SS Card.jpg")
check("t6c: two files -> -1 / -2", [zip_name("SS Card", i, 2, "jpg") for i in range(2)] == ["SS Card-1.jpg", "SS Card-2.jpg"])
check("t6d: three files -> -1 / -2 / -3", [zip_name("Doc", i, 3, "pdf") for i in range(3)] == ["Doc-1.pdf", "Doc-2.pdf", "Doc-3.pdf"])


# ── Report ─────────────────────────────────────────────────────────────────────────────────────
print(f"\n{len(PASS)} passed, {len(FAIL)} failed")
for f in FAIL:
    print("FAIL:", f)
if FAIL:
    sys.exit(1)
print("ALL GREEN")
