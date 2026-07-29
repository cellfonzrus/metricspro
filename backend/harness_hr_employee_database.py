"""Integration-style proof for the Employee Database report (OWNER DIRECTIVE 2026-07-29).

Runs the ACTUAL shipped `hr_employee_database` / `hr_employee_database_fields` / `_mask_last4` /
`_mask_ssn` / `_dd_field_is_masked` / `_require_admin_reveal` from app.modules.hr.router against an
in-memory fake Supabase client (same convention as harness_payroll_salary_router_integration.py /
harness_hr_compliance_doc_filters.py) — no live DB/network. Run:
`python3 harness_hr_employee_database.py` from backend/.

Proves (see PROOF section in the final report for the mapping to the work order's checklist):
  1. GATE DENIAL BEFORE ANY DATA READ — an unrecognized/unauthenticated caller under RBAC enforcement
     gets 401 for both reveal=false and reveal=true WITHOUT the `employees` table ever being touched
     (instrumented table-access log, not just "an exception happened").
  2. Masked-by-default: an HR-titled (non-admin) caller's reveal=false response has dd_routing/
     dd_account rendered as last-4-real/rest-masked, dd_bank_name/dd_account_type UNMASKED, and ssn
     always "(not collected)".
  3. reveal=true from an HR-titled (non-admin, non-super-admin) caller is REJECTED 403 — narrower
     than the base HR/admin page gate — before any employee row is read (same instrumented proof).
  4. reveal=true from an admin (and, separately, a super_admin with a non-'admin' role title) caller
     succeeds and returns the FULL (unmasked) dd_routing/dd_account values, and writes exactly one
     onboarding_event audit row per call (actor/employee_ids/fields captured).
  5. A plain 'rep' role is denied the WHOLE report (reveal=false too) — page-level HR/admin gate.
  6. Org isolation: an org2 caller only ever sees org2's own employee, never org1's, in both directions.
  7. Field selection (`fields=`) honored: only the requested keys appear in each row (plus the always-
     present `employee_id` row-identity key).
  8. Employee selection (`employee_ids=`) honored: narrows to exactly the requested employee(s).
  9. include_inactive toggles an inactive employee in/out; document status distinguishes "(inactive —
     not on Documents board)" from a real "{verified}/{total} verified" computed via the SAME
     onboarding_doc_status() the Documents board itself uses (not re-derived).
 10. Direct-deposit columns are discovered dynamically per-org from onboarding_intake_field
     (section='direct_deposit') — org2 (no such fields configured) gets an EMPTY dd column set, never
     fabricated; the masked/unmasked split (`_dd_field_is_masked`) is exactly routing+account masked,
     bank_name+account_type not.
 11. Encryption-key-lost degrade: a value encrypted under a key no longer configured renders
     "(unavailable — encryption key rotated/lost)" in BOTH masked and revealed responses — never a
     crash, never silently blank.
 12. Pure masking-format unit checks: `_mask_last4` / `_mask_ssn` (SSN not wired to real data today —
     see the router docstring — but the format is proven ready).
"""
import sys

sys.path.insert(0, ".")

PASS, FAIL = [], []


def check(name, cond, detail=""):
    if cond:
        PASS.append(name)
    else:
        FAIL.append(f"{name} :: {detail}")


# ── Fake Supabase client (same convention as harness_payroll_salary_router_integration.py) ─────────
ACCESS_LOG = []  # every table name .table() was called with, since the last reset


class FakeQuery:
    def __init__(self, store, table):
        self.store = store
        self.table_name = table
        self.filters = []
        self._limit = None
        self._mode = None
        self._payload = None

    def select(self, cols):
        self._mode = "select"; return self

    def eq(self, k, v):
        self.filters.append(("eq", k, v)); return self

    def in_(self, k, vals):
        self.filters.append(("in", k, set(vals))); return self

    def order(self, *a, **k):
        return self

    def limit(self, n):
        self._limit = n; return self

    def insert(self, payload):
        self._mode = "insert"; self._payload = payload; return self

    def update(self, payload):
        self._mode = "update"; self._payload = payload; return self

    def _match(self, row):
        for op, k, v in self.filters:
            rv = row.get(k)
            if op == "eq" and rv != v:
                return False
            if op == "in" and rv not in v:
                return False
        return True

    def execute(self):
        rows = self.store.setdefault(self.table_name, [])
        if self._mode == "insert":
            payloads = self._payload if isinstance(self._payload, list) else [self._payload]
            out = []
            for p in payloads:
                row = dict(p)
                row.setdefault("id", f"{self.table_name}-{len(rows)}")
                rows.append(row)
                out.append(row)
            return FakeResult(out)
        if self._mode == "update":
            matched = [r for r in rows if self._match(r)]
            for r in matched:
                r.update(self._payload)
            return FakeResult(matched)
        # default/select
        matched = [dict(r) for r in rows if self._match(r)]
        if self._limit:
            matched = matched[: self._limit]
        return FakeResult(matched)


class FakeResult:
    def __init__(self, data):
        self.data = data


class FakeSchemaClient:
    def __init__(self, store):
        self.store = store

    def schema(self, name):
        return self

    def table(self, name):
        ACCESS_LOG.append(name)
        return FakeQuery(self.store, name)


STORE = {}
FAKE_CLIENT = FakeSchemaClient(STORE)


def fake_get_supabase():
    return FAKE_CLIENT


import app.modules.hr.router as hr_router          # noqa: E402
import app.modules.core.router as core_router_mod  # noqa: E402
from fastapi import HTTPException                  # noqa: E402
from app.core import crypto                         # noqa: E402
from app.core.config import settings                 # noqa: E402

hr_router.get_supabase = fake_get_supabase
core_router_mod._uid_from_token = lambda auth: {
    "Bearer admin": "admin-uid", "Bearer hr": "hr-uid", "Bearer rep": "rep-uid",
    "Bearer super": "super-uid", "Bearer org2admin": "org2admin-uid",
}.get(auth)

ORG1, ORG2 = "ORG1", "ORG2"

STORE["app_config"] = [{"id": 1, "rbac_enabled": True}]
STORE["app_users"] = [
    {"auth_id": "admin-uid", "org_id": ORG1, "email": "admin@x.com", "role": "admin", "super_admin": False},
    {"auth_id": "hr-uid", "org_id": ORG1, "email": "hr@x.com", "role": "hr_manager", "super_admin": False},
    {"auth_id": "rep-uid", "org_id": ORG1, "email": "rep@x.com", "role": "rep", "super_admin": False},
    {"auth_id": "super-uid", "org_id": ORG1, "email": "root@x.com", "role": "owner", "super_admin": True},
    {"auth_id": "org2admin-uid", "org_id": ORG2, "email": "admin2@x.com", "role": "admin", "super_admin": False},
]
STORE["roles"] = []

# ── Real Fernet key configured for this run (restored in finally:) ─────────────────────────────────
from cryptography.fernet import Fernet  # noqa: E402
REAL_KEY, REAL_KEYS = settings.FIELD_ENCRYPTION_KEY, settings.FIELD_ENCRYPTION_KEYS
settings.FIELD_ENCRYPTION_KEY = Fernet.generate_key().decode()
settings.FIELD_ENCRYPTION_KEYS = ""

STORE["employees"] = [
    {"employee_id": "E1", "org_id": ORG1, "name": "Alice Active", "legal_name": "Alice B. Active",
     "home_store": "S1", "role": "Sales Rep", "is_active": True, "phone": "555-1000",
     "email": "alice@work.com", "address_line1": "1 Main St", "address_line2": "", "city": "Metropolis",
     "state": "NY", "zip": "10001", "date_of_birth": "1990-05-01", "hire_date": "2024-01-01"},
    {"employee_id": "E2", "org_id": ORG1, "name": "Bob Inactive", "home_store": "S1", "role": "Rep",
     "is_active": False, "phone": "555-2000", "email": "bob@work.com"},
    {"employee_id": "F1", "org_id": ORG2, "name": "Foreign Frank", "home_store": "SX", "role": "Rep",
     "is_active": True, "phone": "555-9999", "email": "frank@other.com"},
]

DD_ROUTING_PLAIN = "021000021"
DD_ACCOUNT_PLAIN = "123456789012"
STORE["employee_onboarding_profile"] = [
    {"employee_id": "E1", "org_id": ORG1, "workflow_status": "active", "docs_sent_at": "2026-06-01",
     "invited_at": "2026-05-30",
     "intake_data": {
         "dd_bank_name": "Chase Bank",
         "dd_routing": crypto.encrypt(DD_ROUTING_PLAIN),
         "dd_account": crypto.encrypt(DD_ACCOUNT_PLAIN),
         "dd_account_type": "Checking",
         "personal_email": "alice.personal@example.com",
     }},
    {"employee_id": "F1", "org_id": ORG2, "workflow_status": "active", "intake_data": {
        "dd_routing": crypto.encrypt("111111118"), "dd_account": crypto.encrypt("999999999999")}},
]

STORE["onboarding_intake_field"] = [
    {"org_id": ORG1, "key": "dd_bank_name", "label": "Bank name", "section": "direct_deposit",
     "field_type": "text", "sensitive": True, "is_active": True, "sort_order": 400},
    {"org_id": ORG1, "key": "dd_routing", "label": "Routing number", "section": "direct_deposit",
     "field_type": "text", "sensitive": True, "is_active": True, "sort_order": 410},
    {"org_id": ORG1, "key": "dd_account", "label": "Account number", "section": "direct_deposit",
     "field_type": "text", "sensitive": True, "is_active": True, "sort_order": 420},
    {"org_id": ORG1, "key": "dd_account_type", "label": "Account type", "section": "direct_deposit",
     "field_type": "select", "sensitive": True, "is_active": True, "sort_order": 430},
    # a NON-direct-deposit sensitive field, to prove the dynamic filter only surfaces section=='direct_deposit'
    {"org_id": ORG1, "key": "alien_registration_number", "label": "A-Number", "section": "work_eligibility",
     "field_type": "text", "sensitive": True, "is_active": True, "sort_order": 210},
    # ORG2 deliberately has NO direct_deposit fields configured (isolation + "never fabricated" proof).
]

STORE["onboarding_category"] = [
    {"id": "cat1", "org_id": ORG1, "key": "docs", "label": "Docs", "sort_order": 10, "is_active": True}]
STORE["onboarding_task"] = [
    {"id": "task-w4", "org_id": ORG1, "category_id": "cat1", "key": "w4", "label": "W-4",
     "owner_role": "employee", "requires_upload": True, "is_fillable": False, "applies_state": None,
     "is_active": True, "sort_order": 10}]
STORE["employee_onboarding"] = [
    {"org_id": ORG1, "employee_id": "E1", "task_id": "task-w4", "status": "verified",
     "updated_at": "2026-06-05T00:00:00Z"}]
STORE["onboarding_event"] = []


def reset_access_log():
    ACCESS_LOG.clear()


def call(fn, **kw):
    reset_access_log()
    return fn(**kw)


try:
    # ── 1. GATE DENIAL BEFORE ANY DATA READ (unauthenticated / unrecognized, RBAC enforced) ────────
    for reveal in (False, True):
        reset_access_log()
        try:
            hr_router.hr_employee_database(reveal=reveal, authorization="Bearer bogus")
            check(f"1 unauth reveal={reveal} raises", False, "did not raise")
        except HTTPException as e:
            check(f"1 unauth reveal={reveal} raises 401", e.status_code == 401, e.status_code)
            check(f"1 unauth reveal={reveal} never touched employees table", "employees" not in ACCESS_LOG, ACCESS_LOG)

    # ── 2. Masked-by-default for an HR-titled (non-admin) caller ────────────────────────────────────
    r = call(hr_router.hr_employee_database, reveal=False, authorization="Bearer hr")
    check("2 ready", r["ready"] is True)
    e1 = next(x for x in r["employees"] if x["employee_id"] == "E1")
    check("2 dd_routing masked (last4 real)", e1["dd_routing"] == "xxxxx" + DD_ROUTING_PLAIN[-4:], e1["dd_routing"])
    check("2 dd_account masked (last4 real)", e1["dd_account"] == "x" * (len(DD_ACCOUNT_PLAIN) - 4) + DD_ACCOUNT_PLAIN[-4:], e1["dd_account"])
    check("2 dd_bank_name NOT masked", e1["dd_bank_name"] == "Chase Bank", e1["dd_bank_name"])
    check("2 dd_account_type NOT masked", e1["dd_account_type"] == "Checking", e1["dd_account_type"])
    check("2 ssn always not-collected", e1["ssn"] == "(not collected)", e1["ssn"])
    check("2 personal_email surfaced", e1["personal_email"] == "alice.personal@example.com", e1["personal_email"])
    check("2 no full dd value anywhere in non-reveal payload", DD_ROUTING_PLAIN not in str(r) and DD_ACCOUNT_PLAIN not in str(r))

    # ── 3. reveal=true from HR-titled (non-admin) caller REJECTED before any data read ──────────────
    reset_access_log()
    try:
        hr_router.hr_employee_database(reveal=True, authorization="Bearer hr")
        check("3 hr-role reveal=true raises", False, "did not raise")
    except HTTPException as e:
        check("3 hr-role reveal=true -> 403", e.status_code == 403, e.status_code)
        check("3 hr-role reveal=true never touched employees table", "employees" not in ACCESS_LOG, ACCESS_LOG)

    # ── 4. reveal=true succeeds for admin + for a super_admin with a non-'admin' role title ────────
    before_events = len(STORE["onboarding_event"])
    r_admin = call(hr_router.hr_employee_database, reveal=True, authorization="Bearer admin")
    e1r = next(x for x in r_admin["employees"] if x["employee_id"] == "E1")
    check("4 admin reveal -> full routing", e1r["dd_routing"] == DD_ROUTING_PLAIN, e1r["dd_routing"])
    check("4 admin reveal -> full account", e1r["dd_account"] == DD_ACCOUNT_PLAIN, e1r["dd_account"])
    check("4 admin reveal -> ssn still not-collected (nothing to reveal)", e1r["ssn"] == "(not collected)")
    check("4 exactly one audit event written", len(STORE["onboarding_event"]) == before_events + 1, STORE["onboarding_event"])
    ev = STORE["onboarding_event"][-1]
    check("4 audit event type", ev["event_type"] == "employee_database_reveal", ev)
    check("4 audit event actor", ev["actor"] == "admin@x.com", ev)
    check("4 audit event lists E1 in scope", "E1" in (ev.get("detail") or {}).get("employee_ids", []), ev)
    check("4 audit event lists masked fields revealed", set((ev.get("detail") or {}).get("fields", [])) == {"dd_routing", "dd_account"}, ev)

    r_super = call(hr_router.hr_employee_database, reveal=True, authorization="Bearer super")
    e1s = next(x for x in r_super["employees"] if x["employee_id"] == "E1")
    check("4b super_admin (role='owner') reveal -> full routing too", e1s["dd_routing"] == DD_ROUTING_PLAIN, e1s["dd_routing"])

    # ── 5. plain 'rep' role denied the WHOLE report (even reveal=false) ─────────────────────────────
    reset_access_log()
    try:
        hr_router.hr_employee_database(reveal=False, authorization="Bearer rep")
        check("5 rep role denied", False, "did not raise")
    except HTTPException as e:
        check("5 rep role -> 403", e.status_code == 403, e.status_code)
        check("5 rep role never touched employees table", "employees" not in ACCESS_LOG, ACCESS_LOG)

    # ── 6. Org isolation, both directions ────────────────────────────────────────────────────────────
    r1 = call(hr_router.hr_employee_database, reveal=False, authorization="Bearer admin")
    check("6 org1 admin never sees F1 (org2)", all(x["employee_id"] != "F1" for x in r1["employees"]), r1["employees"])
    r2 = call(hr_router.hr_employee_database, reveal=False, authorization="Bearer org2admin")
    ids2 = {x["employee_id"] for x in r2["employees"]}
    check("6b org2 admin sees ONLY F1", ids2 == {"F1"}, ids2)
    fields2 = hr_router.hr_employee_database_fields(authorization="Bearer org2admin")
    dd2 = [f for f in fields2["fields"] if f.get("section") == "direct_deposit"]
    check("6c org2 has NO direct-deposit fields configured -> empty, never fabricated", dd2 == [], dd2)

    # ── 7. Field selection honored ───────────────────────────────────────────────────────────────────
    r_fields = call(hr_router.hr_employee_database, reveal=False, authorization="Bearer admin", fields="name,email")
    row = next(x for x in r_fields["employees"] if x["employee_id"] == "E1")
    check("7 only requested keys (+employee_id) present", set(row.keys()) == {"employee_id", "name", "email"}, row.keys())

    # ── 8. Employee selection honored ───────────────────────────────────────────────────────────────
    r_emp = call(hr_router.hr_employee_database, reveal=False, authorization="Bearer admin", employee_ids="E2")
    check("8 employee_ids narrows to exactly E2", [x["employee_id"] for x in r_emp["employees"]] == ["E2"], r_emp["employees"])

    # ── 9. include_inactive + document status ───────────────────────────────────────────────────────
    r_active_only = call(hr_router.hr_employee_database, reveal=False, authorization="Bearer admin", include_inactive=False)
    check("9a include_inactive=False excludes E2", all(x["employee_id"] != "E2" for x in r_active_only["employees"]))
    r_all = call(hr_router.hr_employee_database, reveal=False, authorization="Bearer admin", include_inactive=True)
    e1_doc = next(x for x in r_all["employees"] if x["employee_id"] == "E1")
    e2_doc = next(x for x in r_all["employees"] if x["employee_id"] == "E2")
    check("9b E1 (active, on Documents board) shows real verified count", e1_doc["doc_status"] == "1/1 verified", e1_doc["doc_status"])
    check("9c E2 (inactive) shows honest inactive note, not fabricated", e2_doc["doc_status"] == "(inactive — not on Documents board)", e2_doc["doc_status"])

    # ── 10. Dynamic direct-deposit discovery + masked/unmasked split ────────────────────────────────
    fields1 = hr_router.hr_employee_database_fields(authorization="Bearer admin")
    dd1 = {f["key"]: f for f in fields1["fields"] if f.get("section") == "direct_deposit"}
    check("10a all 4 dd fields discovered", set(dd1.keys()) == {"dd_bank_name", "dd_routing", "dd_account", "dd_account_type"}, dd1.keys())
    check("10b routing masked", dd1["dd_routing"]["masked"] is True)
    check("10c account masked", dd1["dd_account"]["masked"] is True)
    check("10d bank_name NOT masked", dd1["dd_bank_name"]["masked"] is False)
    check("10e account_type NOT masked", dd1["dd_account_type"]["masked"] is False)
    check("10f non-direct_deposit sensitive field (A-Number) excluded from dd set", "alien_registration_number" not in dd1)

    # ── 11. Encryption-key-lost degrade ──────────────────────────────────────────────────────────────
    saved_key = settings.FIELD_ENCRYPTION_KEY
    settings.FIELD_ENCRYPTION_KEY, settings.FIELD_ENCRYPTION_KEYS = "", ""
    r_lost_masked = call(hr_router.hr_employee_database, reveal=False, authorization="Bearer hr")
    e1_lost = next(x for x in r_lost_masked["employees"] if x["employee_id"] == "E1")
    check("11a masked view: key lost -> honest unavailable note, not a crash",
          e1_lost["dd_routing"] == "(unavailable — encryption key rotated/lost)", e1_lost["dd_routing"])
    r_lost_reveal = call(hr_router.hr_employee_database, reveal=True, authorization="Bearer admin")
    e1_lost_r = next(x for x in r_lost_reveal["employees"] if x["employee_id"] == "E1")
    check("11b reveal view: key lost -> STILL unavailable, never garbage/ciphertext leaked",
          e1_lost_r["dd_routing"] == "(unavailable — encryption key rotated/lost)", e1_lost_r["dd_routing"])
    settings.FIELD_ENCRYPTION_KEY = saved_key

    # ── 12. Pure masking-format unit checks ─────────────────────────────────────────────────────────
    check("12a mask_last4 basic", hr_router._mask_last4("123456789012") == "xxxxxxxx9012")
    check("12b mask_last4 short (<=4) fully masked", hr_router._mask_last4("12") == "xx")
    check("12c mask_last4 empty", hr_router._mask_last4("") == "")
    check("12d mask_last4 none", hr_router._mask_last4(None) == "")
    check("12e mask_ssn 9-digit standard grouping", hr_router._mask_ssn("123-45-6789") == "xxx-xx-6789")
    check("12f mask_ssn non-9-digit falls back to generic last4", hr_router._mask_ssn("12345") == "xxx45" or hr_router._mask_ssn("12345") == hr_router._mask_last4("12345"))
    check("12g dd_field_is_masked: routing/account True, type/name False",
          hr_router._dd_field_is_masked("dd_routing") and hr_router._dd_field_is_masked("dd_account")
          and not hr_router._dd_field_is_masked("dd_account_type") and not hr_router._dd_field_is_masked("dd_bank_name"))

finally:
    settings.FIELD_ENCRYPTION_KEY, settings.FIELD_ENCRYPTION_KEYS = REAL_KEY, REAL_KEYS

print(f"\n{'='*70}\nPASS: {len(PASS)}  FAIL: {len(FAIL)}\n{'='*70}")
for f in FAIL:
    print("FAIL:", f)
if not FAIL:
    print("ALL CHECKS PASSED")
sys.exit(1 if FAIL else 0)
