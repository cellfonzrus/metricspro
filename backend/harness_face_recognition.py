"""Proof harness for the kiosk face-recognition master switch (mod-people, migration 420).

Run: python3 backend/harness_face_recognition.py   (no network, no DB — an in-memory fake client)

Proves the five things the owner directive of 2026-08-09 actually asked for, plus the fail-closed
degrade that makes the feature off in production the moment the code deploys:

  1. Every tenant is OFF by default, and OFF is a HARD gate — no per-employee value re-enables it.
  2. Pre-migration (columns absent) also resolves OFF, not "assume the default".
  3. Once ON, an unassigned employee follows the tenant default, either direction.
  4. A per-employee assignment and a recorded consent DECLINE both win over the tenant default.
  5. Turning it ON stamps consent for employees with NO record, and never touches a 'declined' one
     or overwrites a real 'manual' signature's own date/source.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from app.modules.storeops import face_recognition as F   # noqa: E402

PASS, FAIL = [], []


def check(label, cond):
    (PASS if cond else FAIL).append(label)
    print(("  ok   " if cond else "  FAIL ") + label)


# ── an in-memory stand-in for the Supabase client, deliberately schemaless ────────────────────────
class _Q:
    def __init__(self, rows, cols, store=None, patch=None):
        self._rows, self._cols, self._store, self._patch = rows, cols, store, patch
        self._filters = []

    def eq(self, col, val):
        self._filters.append((col, "eq", val)); return self

    def is_(self, col, val):
        self._filters.append((col, "is", val)); return self

    def limit(self, _n):
        return self

    def _matches(self, r):
        for col, op, val in self._filters:
            if op == "eq" and str(r.get(col)) != str(val):
                return False
            if op == "is" and val == "null" and r.get(col) is not None:
                return False
        return True

    def execute(self):
        hit = [r for r in self._rows if self._matches(r)]
        if self._patch is not None:            # UPDATE
            for r in hit:
                r.update(self._patch)
            return type("R", (), {"data": [dict(r) for r in hit]})()
        if self._cols == "*":
            out = [dict(r) for r in hit]
        else:
            keys = [c.strip() for c in self._cols.split(",")]
            out = [{k: r[k] for k in keys if k in r} for r in hit]   # absent column == absent key
        return type("R", (), {"data": out})()


class FakeClient:
    def __init__(self, tenants, employees):
        self.tables = {"tenants": tenants, "employees": employees}

    def table(self, name):
        rows = self.tables.setdefault(name, [])
        return type("T", (), {
            "select": lambda _s, cols="*", **kw: _Q(rows, cols),
            "update": lambda _s, patch: _Q(rows, "*", patch=patch),
        })()


ORG = "00000000-0000-0000-0000-000000000001"
OTHER = "854f6d7b-6590-4e4d-88ab-646f560d4f4c"


def migrated_tenant(enabled=False, default_for_employees=True, org=ORG):
    return {"org_id": org, "face_recognition_enabled": enabled,
            "face_recognition_default_for_employees": default_for_employees,
            "face_recognition_enabled_at": None, "face_recognition_enabled_by": None}


def emp(eid, org=ORG, **kw):
    row = {"org_id": org, "employee_id": eid, "face_recognition_enabled": None,
           "face_consent_status": None, "face_consent_at": None, "face_consent_source": None}
    row.update(kw)
    return row


print("\n(1) OFF is the default, and it is a HARD gate")
c = FakeClient([migrated_tenant(enabled=False)],
               [emp("E1"), emp("E2", face_recognition_enabled=True)])
cfg, avail = F.get_tenant_face_config(ORG, c)
check("tenant reads as available (migration applied)", avail is True)
check("tenant default is disabled", cfg["enabled"] is False)
r1 = F.resolve_employee_face(cfg, F.get_employee_face_row(ORG, "E1", c), avail)
r2 = F.resolve_employee_face(cfg, F.get_employee_face_row(ORG, "E2", c), avail)
check("unassigned employee -> OFF", r1 == {"enabled": False, "reason": "tenant_disabled"})
check("employee explicitly assigned ON still -> OFF (master switch wins)",
      r2 == {"enabled": False, "reason": "tenant_disabled"})

print("\n(2) pre-migration fails CLOSED, it does not assume a default")
c_pre = FakeClient([{"org_id": ORG}], [{"org_id": ORG, "employee_id": "E1"}])
cfg_pre, avail_pre = F.get_tenant_face_config(ORG, c_pre)
check("availability is False when the columns don't exist", avail_pre is False)
check("resolves OFF with reason not_configured",
      F.resolve_employee_face(cfg_pre, {}, avail_pre) == {"enabled": False, "reason": "not_configured"})


class BoomClient:
    def table(self, _name):
        raise RuntimeError("unknown column (real PostgREST behaviour)")


cfg_b, avail_b = F.get_tenant_face_config(ORG, BoomClient())
check("a raising client also degrades to OFF, never a 500", avail_b is False and cfg_b["enabled"] is False)
check("get_employee_face_row swallows the same failure", F.get_employee_face_row(ORG, "E1", BoomClient()) == {})

print("\n(3) once ON, an unassigned employee follows the tenant default")
c_on = FakeClient([migrated_tenant(enabled=True, default_for_employees=True)], [emp("E1")])
cfg_on, av_on = F.get_tenant_face_config(ORG, c_on)
check("default_for_employees=true -> ON",
      F.resolve_employee_face(cfg_on, F.get_employee_face_row(ORG, "E1", c_on), av_on)
      == {"enabled": True, "reason": "tenant_default"})
c_sel = FakeClient([migrated_tenant(enabled=True, default_for_employees=False)], [emp("E1")])
cfg_sel, av_sel = F.get_tenant_face_config(ORG, c_sel)
check("default_for_employees=false -> OFF until assigned",
      F.resolve_employee_face(cfg_sel, F.get_employee_face_row(ORG, "E1", c_sel), av_sel)
      == {"enabled": False, "reason": "tenant_default_off"})

print("\n(4) per-employee assignment and a consent DECLINE both beat the tenant default")
c4 = FakeClient([migrated_tenant(enabled=True, default_for_employees=True)],
                [emp("OFFP", face_recognition_enabled=False),
                 emp("ONP", face_recognition_enabled=True),
                 emp("NOPE", face_consent_status="declined"),
                 emp("NOPE2", face_recognition_enabled=True, face_consent_status="declined")])
cfg4, av4 = F.get_tenant_face_config(ORG, c4)
res = {e: F.resolve_employee_face(cfg4, F.get_employee_face_row(ORG, e, c4), av4)
       for e in ("OFFP", "ONP", "NOPE", "NOPE2")}
check("assigned OFF -> off", res["OFFP"] == {"enabled": False, "reason": "employee_unassigned"})
check("assigned ON  -> on", res["ONP"] == {"enabled": True, "reason": "employee_assigned"})
check("declined consent -> off", res["NOPE"] == {"enabled": False, "reason": "consent_declined"})
check("declined consent beats an explicit ON assignment", res["NOPE2"] == {"enabled": False, "reason": "consent_declined"})

print("\n(5) turning it ON stamps consent for the unrecorded only")
rows = [emp("A"), emp("B"),
        emp("C", face_consent_status="declined", face_consent_at="2026-08-01T00:00:00Z", face_consent_source="declined"),
        emp("D", face_consent_status="signed", face_consent_at="2026-07-04T00:00:00Z", face_consent_source="manual:hr@x.com"),
        emp("X", org=OTHER)]
c5 = FakeClient([migrated_tenant(enabled=True)], rows)
n = F.stamp_assumed_consent_for_all(ORG, c5, who="owner@cellfonzrus.com")
by_id = {r["employee_id"]: r for r in rows}
check("stamped exactly the 2 employees with no record", n == 2)
check("A now signed", by_id["A"]["face_consent_status"] == "signed")
check("A carries a real timestamp", bool(by_id["A"]["face_consent_at"]))
check("A's source says how it was obtained", str(by_id["A"]["face_consent_source"]).startswith("assumed_on_enable"))
check("a recorded DECLINE survives the switch", by_id["C"]["face_consent_status"] == "declined")
check("a real signed release keeps its own date", by_id["D"]["face_consent_at"] == "2026-07-04T00:00:00Z")
check("a real signed release keeps its own source", by_id["D"]["face_consent_source"] == "manual:hr@x.com")
check("MULTI-TENANT: the other tenant's employee is untouched", by_id["X"]["face_consent_status"] is None)
check("stamping degrades to None (never raises) pre-migration",
      F.stamp_assumed_consent_for_all(ORG, BoomClient()) is None)

print("\n(6) the admin panel's summary counts")
s = F.consent_summary({r["employee_id"]: r for r in rows if r["org_id"] == ORG})
check("summary counts signed/declined/unrecorded", (s["signed"], s["declined"], s["unrecorded"]) == (3, 1, 0))
check("summary counts assignment states", (s["assigned_on"], s["assigned_off"], s["unassigned"]) == (0, 0, 4))

print(f"\n{len(PASS)} passed, {len(FAIL)} failed")
if FAIL:
    for f in FAIL:
        print("  FAILED: " + f)
    sys.exit(1)
