"""Proof harness for the face-descriptor retention schedule + deletion job (mod-people, migration 422).

Run: python3 backend/harness_face_retention.py   (no network, no DB — an in-memory fake client)

Proves the rule the owner decision of 2026-08-09 actually specified, plus the negative controls
(fleet rule): things that must NOT happen.

  1. "Whichever is first" — a termination_date 90 days ago is due (purpose_satisfied); a termination
     500 days ago with a 1-year-old last interaction is STILL governed by the 1095-day backstop, not
     the (already-passed) purpose window, when the backstop is later than purpose+90 — i.e. the
     function picks the actual MIN of the two computed dates, not "prefer termination".
  2. The 1095-day statutory backstop fires on its own for an employee who is NEVER terminated.
  3. clamp_retention_days cannot be pushed past the 1095-day statutory ceiling — not by a huge
     number, not by a negative one, not by garbage input.
  4. A dry run NEVER deletes anything and NEVER writes an audit log row.
  5. A real run deletes exactly the due rows, logs exactly the due rows (never the raw descriptor
     vector — checked explicitly), and leaves not-yet-due rows and OTHER-TENANT rows untouched
     (multi-tenant negative control).
  6. tenant_disabled_purge purges EVERY descriptor for that tenant regardless of date, only when BOTH
     face_recognition_enabled=False AND purge_on_disable=True — three negative controls (enabled=True,
     purge_on_disable=False, migration-not-applied) all prove NOTHING is purged.
  7. destroy_one_employee_request destroys immediately regardless of any date math, is a true no-op
     (ok=True, destroyed=0) when nobody has a template on file, and never touches another employee's
     or another tenant's row.
  8. A completely empty tenant (no employees, no descriptors — the new Vzone tenant, verified live
     2026-08-09) is a clean no-op, not a crash.
  9. Everything degrades to `available=False` / zero destruction pre-migration (columns absent),
     never raises.
"""
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from app.modules.storeops import face_retention as R   # noqa: E402

PASS, FAIL = [], []


def check(label, cond):
    (PASS if cond else FAIL).append(label)
    print(("  ok   " if cond else "  FAIL ") + label)


# ── in-memory stand-in for the Supabase client (same shape as harness_face_recognition.py's) ───────
class _Q:
    def __init__(self, rows, cols, patch=None, delete=False, insert_row=None):
        self._rows, self._cols = rows, cols
        self._patch, self._delete, self._insert_row = patch, delete, insert_row
        self._filters = []          # list of (col, op, val); op in eq/in/is/isnot
        self._next_negated = False  # set by .not_, consumed by the NEXT .is_() call only
        self._order_col, self._order_desc = None, False
        self._limit_n = None

    def eq(self, col, val):
        self._filters.append((col, "eq", val)); return self

    def in_(self, col, vals):
        self._filters.append((col, "in", set(str(v) for v in vals))); return self

    def is_(self, col, val):
        want_null = (val == "null" or val is None)
        op = "isnot" if self._next_negated else "is"
        self._next_negated = False
        self._filters.append((col, op, want_null)); return self

    @property
    def not_(self):
        self._next_negated = True
        return self

    def limit(self, n):
        self._limit_n = n; return self

    def order(self, col, desc=False):
        self._order_col, self._order_desc = col, desc; return self

    def _matches(self, r):
        for col, op, val in self._filters:
            if op == "eq" and str(r.get(col)) != str(val):
                return False
            if op == "in" and str(r.get(col)) not in val:
                return False
            if op == "is" and (r.get(col) is None) != val:
                return False
            if op == "isnot" and (r.get(col) is None) == val:
                return False
        return True

    def execute(self):
        if self._insert_row is not None:
            row = dict(self._insert_row)
            self._rows.append(row)
            return type("R", (), {"data": [row]})()
        hit = [r for r in self._rows if self._matches(r)]
        if self._delete:
            for r in hit:
                self._rows.remove(r)
            return type("R", (), {"data": [dict(r) for r in hit]})()
        if self._patch is not None:
            for r in hit:
                r.update(self._patch)
            return type("R", (), {"data": [dict(r) for r in hit]})()
        if self._order_col:
            hit = sorted(hit, key=lambda r: str(r.get(self._order_col) or ""), reverse=self._order_desc)
        if self._limit_n:
            hit = hit[: self._limit_n]
        if self._cols == "*":
            out = [dict(r) for r in hit]
        else:
            keys = [c.strip() for c in self._cols.split(",")]
            out = [{k: r[k] for k in keys if k in r} for r in hit]  # absent column == absent key
        return type("R", (), {"data": out})()


class T:
    def __init__(self, rows):
        self.rows = rows

    def select(self, cols="*", **kw):
        return _Q(self.rows, cols)

    def update(self, patch):
        return _Q(self.rows, "*", patch=patch)

    def delete(self):
        return _Q(self.rows, "*", delete=True)

    def insert(self, row):
        return _Q(self.rows, "*", insert_row=row)


class FakeClient:
    def __init__(self, tenants=None, employees=None, face_descriptors=None, timelog=None,
                 face_retention_log=None):
        self.tables = {
            "tenants": tenants or [],
            "employees": employees or [],
            "face_descriptors": face_descriptors or [],
            "timelog": timelog or [],
            "face_retention_log": face_retention_log if face_retention_log is not None else [],
        }

    def table(self, name):
        return T(self.tables.setdefault(name, []))


ORG = "00000000-0000-0000-0000-000000000001"
OTHER = "854f6d7b-6590-4e4d-88ab-646f560d4f4c"
EMPTY_TENANT = "f4f1c16e-2acf-4221-854a-c29a605754a7"

NOW = datetime(2026, 8, 9, tzinfo=timezone.utc)
TODAY = NOW.date()


def iso(d):
    return d.isoformat() if isinstance(d, date) else d


def dt_iso(d):
    return datetime(d.year, d.month, d.day, 12, 0, tzinfo=timezone.utc).isoformat()


def tenant(org=ORG, retention_days=90, purge_on_disable=False, face_recognition_enabled=False, migrated=True):
    row = {"org_id": org}
    if migrated:
        row.update({"face_retention_days": retention_days,
                    "face_recognition_purge_on_disable": purge_on_disable,
                    "face_recognition_enabled": face_recognition_enabled})
    return row


def emp(eid, org=ORG, name=None, termination_date=None):
    return {"org_id": org, "employee_id": eid, "name": name or eid, "termination_date": termination_date}


def desc(eid, org=ORG, registered=None, updated=None, did=None):
    return {"id": did or f"desc-{org[:4]}-{eid}", "org_id": org, "employee_id": eid,
            "registered_at": registered or dt_iso(TODAY - timedelta(days=400)),
            "updated_at": updated or registered or dt_iso(TODAY - timedelta(days=400))}


print("\n(1) 'whichever is first' — purpose_satisfied when it's the earlier date")
c1 = FakeClient(
    tenants=[tenant(retention_days=90)],
    employees=[emp("E1", termination_date=iso(TODAY - timedelta(days=100)))],   # terminated 100d ago
    face_descriptors=[desc("E1", registered=dt_iso(TODAY - timedelta(days=500)))],
)
r1 = R.compute_due(ORG, c1, today=TODAY)
check("available", r1["available"] is True)
check("exactly 1 item due", len(r1["items"]) == 1)
check("trigger is purpose_satisfied (100d term > 90d retention)", r1["items"][0]["trigger"] == R.TRIGGER_PURPOSE)
check("retention_days_applied is the tenant's 90", r1["items"][0]["retention_days_applied"] == 90)

print("\n(1b) not yet due — terminated 10 days ago, 90-day window")
c1b = FakeClient(
    tenants=[tenant(retention_days=90)],
    employees=[emp("E1", termination_date=iso(TODAY - timedelta(days=10)))],
    face_descriptors=[desc("E1", registered=dt_iso(TODAY - timedelta(days=500)))],
)
r1b = R.compute_due(ORG, c1b, today=TODAY)
check("NOT yet due (10d < 90d window)", len(r1b["items"]) == 0)

print("\n(2) statutory backstop fires on its own — never terminated, last interaction 3+ years ago")
c2 = FakeClient(
    tenants=[tenant(retention_days=90)],
    employees=[emp("E2", termination_date=None)],   # still employed / never terminated
    face_descriptors=[desc("E2", registered=dt_iso(TODAY - timedelta(days=1100)))],  # > 1095 days
)
r2 = R.compute_due(ORG, c2, today=TODAY)
check("backstop item due", len(r2["items"]) == 1)
check("trigger is statutory_backstop", r2["items"][0]["trigger"] == R.TRIGGER_BACKSTOP)
check("retention_days_applied is the statutory 1095, not the tenant's 90",
      r2["items"][0]["retention_days_applied"] == R.STATUTORY_BACKSTOP_DAYS)

print("\n(2b) NEGATIVE CONTROL — never terminated, last interaction only 2 years ago -> NOT due")
c2b = FakeClient(
    tenants=[tenant(retention_days=90)],
    employees=[emp("E2", termination_date=None)],
    face_descriptors=[desc("E2", registered=dt_iso(TODAY - timedelta(days=730)))],
)
r2b = R.compute_due(ORG, c2b, today=TODAY)
check("NOT due (2y < 3y statutory backstop)", len(r2b["items"]) == 0)

print("\n(2c) backstop still binds even with a termination date far in the future window")
# Terminated recently (10 days ago) with a huge configured retention window (1000 days) — but the
# EMPLOYEE'S own last interaction was already 1100 days ago (e.g. long dormant, rehired, terminated
# again) -> MIN(term+1000, last_interaction+1095) = the backstop, not the purpose window.
c2c = FakeClient(
    tenants=[tenant(retention_days=1000)],
    employees=[emp("E2c", termination_date=iso(TODAY - timedelta(days=10)))],
    face_descriptors=[desc("E2c", registered=dt_iso(TODAY - timedelta(days=1100)))],
)
r2c = R.compute_due(ORG, c2c, today=TODAY)
check("due (backstop already passed even though purpose window has not)", len(r2c["items"]) == 1)
check("trigger is statutory_backstop (the actual MIN, not 'prefer termination')",
      r2c["items"][0]["trigger"] == R.TRIGGER_BACKSTOP)

print("\n(3) clamp_retention_days cannot exceed the statutory ceiling, in any direction")
check("huge number clamps to 1095", R.clamp_retention_days(999999) == R.STATUTORY_BACKSTOP_DAYS)
check("negative number clamps to the floor (1)", R.clamp_retention_days(-5) == R.FACE_RETENTION_DAYS_MIN)
check("zero clamps to the floor (1)", R.clamp_retention_days(0) == R.FACE_RETENTION_DAYS_MIN)
check("garbage input falls back to the 90-day default", R.clamp_retention_days("banana") == R.FACE_RETENTION_DAYS_DEFAULT)
check("exactly 1095 stays 1095 (ceiling is inclusive)", R.clamp_retention_days(1095) == 1095)
check("1096 clamps DOWN to 1095", R.clamp_retention_days(1096) == 1095)

print("\n(4) NEGATIVE CONTROL — a dry run deletes nothing and logs nothing")
c4 = FakeClient(
    tenants=[tenant(retention_days=90)],
    employees=[emp("E4", termination_date=iso(TODAY - timedelta(days=200)))],
    face_descriptors=[desc("E4")],
)
before_count = len(c4.tables["face_descriptors"])
res4 = R.destroy(ORG, c4, dry_run=True, destroyed_by="test@x.com")
check("dry run reports 1 candidate", res4["candidates"] == 1)
check("dry run destroyed count is 0", res4["destroyed"] == 0)
check("dry_run=True on the response", res4["dry_run"] is True)
check("the descriptor row STILL EXISTS after a dry run", len(c4.tables["face_descriptors"]) == before_count)
check("NO audit log row was written by a dry run", len(c4.tables["face_retention_log"]) == 0)

print("\n(5) a real run destroys exactly the due rows, logs them, leaves everything else alone")
c5 = FakeClient(
    tenants=[tenant(org=ORG, retention_days=90), tenant(org=OTHER, retention_days=90)],
    employees=[
        emp("DUE1", org=ORG, name="Due One", termination_date=iso(TODAY - timedelta(days=200))),
        emp("NOTDUE", org=ORG, name="Not Due", termination_date=iso(TODAY - timedelta(days=5))),
        emp("OTHERORG", org=OTHER, name="Other Tenant Person", termination_date=iso(TODAY - timedelta(days=200))),
    ],
    face_descriptors=[
        desc("DUE1", org=ORG, did="d-due1"),
        desc("NOTDUE", org=ORG, did="d-notdue"),
        desc("OTHERORG", org=OTHER, did="d-other"),
    ],
)
computed5 = R.compute_due(ORG, c5, today=TODAY)
check("only DUE1 computed as due for ORG", [i["employee_id"] for i in computed5["items"]] == ["DUE1"])
res5 = R.destroy(ORG, c5, computed=computed5, dry_run=False, destroyed_by="hr@cellfonzrus.com")
check("destroyed count is 1", res5["destroyed"] == 1)
remaining_ids = {r["id"] for r in c5.tables["face_descriptors"]}
check("DUE1's descriptor is GONE", "d-due1" not in remaining_ids)
check("NOTDUE's descriptor (same tenant, not yet due) is UNTOUCHED", "d-notdue" in remaining_ids)
check("MULTI-TENANT: the OTHER tenant's descriptor is UNTOUCHED by an ORG-scoped run", "d-other" in remaining_ids)
log_rows = c5.tables["face_retention_log"]
check("exactly 1 audit row written", len(log_rows) == 1)
check("audit row org-scoped correctly", log_rows[0]["org_id"] == ORG)
check("audit row identifies the right employee", log_rows[0]["employee_id"] == "DUE1")
check("audit row records the trigger", log_rows[0]["trigger"] == R.TRIGGER_PURPOSE)
check("audit row records who destroyed it", log_rows[0]["destroyed_by"] == "hr@cellfonzrus.com")
check("audit row NEVER carries a 'descriptor' key (no biometric vector logged)",
      "descriptor" not in log_rows[0])

print("\n(6) tenant_disabled_purge — purges EVERYTHING for that tenant, only on the exact combination")
c6 = FakeClient(
    tenants=[tenant(retention_days=90, purge_on_disable=True, face_recognition_enabled=False)],
    employees=[emp("P1", termination_date=None), emp("P2", termination_date=None)],
    face_descriptors=[desc("P1"), desc("P2", registered=dt_iso(TODAY))],  # P2 enrolled TODAY — still purged
)
computed6 = R.compute_due(ORG, c6, today=TODAY)
check("purge_all is True", computed6["purge_all"] is True)
check("purge_reason is tenant_disabled_purge", computed6["purge_reason"] == R.TRIGGER_TENANT_PURGE)
check("BOTH employees are in the purge list, even the one enrolled TODAY", len(computed6["items"]) == 2)
res6 = R.destroy(ORG, c6, computed=computed6, dry_run=False, destroyed_by="admin@x.com")
check("both descriptors destroyed", res6["destroyed"] == 2)
check("face_descriptors table is now empty for this tenant", len(c6.tables["face_descriptors"]) == 0)

print("\n(6b) NEGATIVE CONTROL — face recognition ENABLED (even with purge_on_disable=True) purges nothing")
c6b = FakeClient(
    tenants=[tenant(retention_days=90, purge_on_disable=True, face_recognition_enabled=True)],
    employees=[emp("P1", termination_date=None)],
    face_descriptors=[desc("P1", registered=dt_iso(TODAY))],
)
computed6b = R.compute_due(ORG, c6b, today=TODAY)
check("purge_all is False when the tenant is still enabled", computed6b["purge_all"] is False)
check("nothing due (fresh enrollment, no termination, no backstop)", len(computed6b["items"]) == 0)

print("\n(6c) NEGATIVE CONTROL — disabled but purge_on_disable=False (today's DEFAULT posture) purges nothing")
c6c = FakeClient(
    tenants=[tenant(retention_days=90, purge_on_disable=False, face_recognition_enabled=False)],
    employees=[emp("P1", termination_date=None)],
    face_descriptors=[desc("P1", registered=dt_iso(TODAY))],
)
computed6c = R.compute_due(ORG, c6c, today=TODAY)
check("purge_all is False by default (the 77 real descriptors stay kept)", computed6c["purge_all"] is False)
check("nothing due (fresh enrollment, no termination, no backstop)", len(computed6c["items"]) == 0)

print("\n(7) employee-request deletion — immediate, regardless of date math")
c7 = FakeClient(
    face_descriptors=[desc("REQ1", org=ORG, did="d-req1", registered=dt_iso(TODAY))],  # enrolled TODAY
)
res7 = R.destroy_one_employee_request(ORG, "REQ1", "Request One", c7, destroyed_by="hr@x.com",
                                       note="emailed HR 2026-08-09 asking for deletion")
check("destroyed despite being enrolled today (no date math applies to a request)", res7["destroyed"] == 1)
check("descriptor row is gone", not any(d["id"] == "d-req1" for d in c7.tables["face_descriptors"]))
check("audit row logged with employee_request trigger",
      c7.tables["face_retention_log"][0]["trigger"] == R.TRIGGER_EMPLOYEE_REQUEST)
check("audit row carries the note", "2026-08-09" in (c7.tables["face_retention_log"][0]["notes"] or ""))

print("\n(7b) NEGATIVE CONTROL — a true no-op when nobody has a template on file")
c7b = FakeClient(face_descriptors=[])
res7b = R.destroy_one_employee_request(ORG, "NOBODY", "Nobody", c7b, destroyed_by="hr@x.com")
check("ok=True, destroyed=0 (not an error — there was simply nothing to destroy)",
      res7b == {"ok": True, "destroyed": 0, "detail": "no biometric template on file for this employee"})
check("no audit row written for a no-op", len(c7b.tables["face_retention_log"]) == 0)

print("\n(7c) NEGATIVE CONTROL — a request for one employee never touches another employee's row")
c7c = FakeClient(face_descriptors=[desc("KEEP", org=ORG, did="d-keep"), desc("REQ2", org=ORG, did="d-req2")])
R.destroy_one_employee_request(ORG, "REQ2", "Req Two", c7c, destroyed_by="hr@x.com")
check("KEEP's descriptor is untouched", any(d["id"] == "d-keep" for d in c7c.tables["face_descriptors"]))
check("REQ2's descriptor is gone", not any(d["id"] == "d-req2" for d in c7c.tables["face_descriptors"]))

print("\n(8) a completely empty tenant is a clean no-op, not a crash (the new Vzone tenant)")
c8 = FakeClient(tenants=[tenant(org=EMPTY_TENANT, retention_days=90)], employees=[], face_descriptors=[])
computed8 = R.compute_due(EMPTY_TENANT, c8, today=TODAY)
check("available (migration applied)", computed8["available"] is True)
check("purge_all is False", computed8["purge_all"] is False)
check("zero items — no crash on an empty roster", computed8["items"] == [])
res8 = R.destroy(EMPTY_TENANT, c8, computed=computed8, dry_run=False, destroyed_by="system:pg_cron")
check("destroy() on an empty tenant is also a clean no-op", res8["destroyed"] == 0)

print("\n(9) DEGRADE — pre-migration (columns absent) is available=False and destroys nothing, never raises")
c9 = FakeClient(tenants=[{"org_id": ORG}],   # no face_retention_days column at all
                employees=[emp("E9", termination_date=iso(TODAY - timedelta(days=2000)))],
                face_descriptors=[desc("E9")])
cfg9, avail9 = R.get_tenant_retention_config(ORG, c9)
check("availability is False when the columns don't exist", avail9 is False)
computed9 = R.compute_due(ORG, c9, today=TODAY)
check("compute_due also reports unavailable", computed9["available"] is False)
check("compute_due returns zero items pre-migration (even though E9 is 2000 days overdue)",
      computed9["items"] == [])
res9 = R.destroy(ORG, c9, dry_run=False, destroyed_by="system:pg_cron")
check("destroy() is a no-op pre-migration — nothing destroyed, no crash", res9["destroyed"] == 0)
check("the descriptor row SURVIVES pre-migration despite being 2000 days overdue",
      len(c9.tables["face_descriptors"]) == 1)


class BoomClient:
    def table(self, _name):
        raise RuntimeError("unknown column (real PostgREST behaviour)")


check("a raising client also degrades cleanly (get_tenant_retention_config)",
      R.get_tenant_retention_config(ORG, BoomClient())[1] is False)
check("a raising client also degrades cleanly (compute_due)",
      R.compute_due(ORG, BoomClient(), today=TODAY)["available"] is False)
check("a raising client on destroy_one_employee_request returns ok=False, never raises",
      R.destroy_one_employee_request(ORG, "X", "X", BoomClient())["ok"] is False)

print(f"\n{len(PASS)} passed, {len(FAIL)} failed")
if FAIL:
    for f in FAIL:
        print("  FAILED: " + f)
    sys.exit(1)
