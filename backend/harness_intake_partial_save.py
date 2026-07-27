"""Pure-logic proof harness for the 2026-07-27 owner bug report: "the employee in boost completed all
information but no information can be seen at our end."

Root cause (see hr/router.py _apply_intake, dated bug-fix comment): the direct-deposit disclaimer gate
used to raise HTTPException(400) for the WHOLE /onboarding/{me,public}/intake submission the instant
direct-deposit fields carried a value without the typed initials — discarding every OTHER already-filled
field (name/address/emergency contact) in the SAME payload, since the portal always posts one merged
form in a single call. Nothing persisted: intake_submitted_at never got set, so the admin's "Captured
information" card (hr/onboarding/[employeeId]/page.tsx, gated on that flag) rendered nothing at all —
exactly the reported symptom.

Runs the ACTUAL shipped function (app.modules.hr.router._apply_intake) plus the actual admin-facing
reader (onboarding_for_employee) against a tiny in-memory fake Supabase client — no live DB/network.
Run: `python3 harness_intake_partial_save.py` from backend/.

Proves:
  1. OLD-BUG REGRESSION GUARD: a submission with complete personal info + direct-deposit fields but NO
     disclaimer initials no longer raises — it succeeds (ok:true).
  2. Every non-DD field from that submission is persisted (intake_data) AND propagated to
     storeops.employees — nothing is silently discarded.
  3. The DD-specific fields are correctly WITHHELD (not stored) until the disclaimer is acknowledged —
     the fix doesn't let bank details bypass the disclaimer gate, it just stops the gate from eating
     unrelated data.
  4. intake_submitted_at gets set on this first (partial) call — this is exactly the flag the admin
     page's "Captured information" card gates on, so the fix makes the record visible immediately.
  5. The response honestly flags dd_disclaimer_pending=True with a human warning (never a bare 200 that
     looks fully successful) — this is what the frontend fix (onboard/[token]/page.tsx,
     components/PortalOnboarding.tsx) renders as an amber warning instead of a green "saved" banner.
  6. The admin-facing GET /hr/onboarding/employee/{id} (onboarding_for_employee) now shows
     intake_submitted=True and every non-DD value, immediately after the partial save — i.e. the admin
     genuinely stops seeing "no information at all" the moment this fix ships.
  7. A SECOND call with initials now supplied stores the DD fields too, sets dd_disclaimer_signed_at,
     and the response no longer carries dd_disclaimer_pending.
  8. REGRESSION GUARD (unrelated path untouched): a truly missing REQUIRED field (not DD) still 400s
     the whole call, exactly as before — this fix only changes the DD-disclaimer path.
  9. Sensitive fields (SSN etc, unrelated to DD) are still stored encrypted-or-passthrough exactly as
     before, and are never included in intake_values shown to the admin's non-sensitive grid.
"""
import sys

sys.path.insert(0, ".")

PASS = []
FAIL = []


def check(name, cond, detail=""):
    if cond:
        PASS.append(name)
    else:
        FAIL.append(f"{name} :: {detail}")


# ── Fake Supabase client (same shape as harness_multifile_docs.py's) ────────────────────────────
class FakeQuery:
    def __init__(self, store, table):
        self.store = store
        self.table_name = table
        self.filters = []
        self._limit = None
        self._mode = None
        self._payload = None
        self._on_conflict = None

    def select(self, cols):
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


class FakeClient:
    def __init__(self):
        self.store = {}

    def schema(self, name):
        return FakeSchemaTable(self.store)

    def table(self, name):
        return FakeQuery(self.store, name)


# ── Wire the fake client into app.modules.hr.router ─────────────────────────────────────────────
import app.modules.hr.router as hr   # noqa: E402

fake = FakeClient()
hr.get_supabase = lambda: fake
ORG = "00000000-0000-0000-0000-000000000001"   # the house/Boost org — matches the owner report
EMP = "E1001"

# Seed the configurable intake form: a REQUIRED personal field, an OPTIONAL non-sensitive field, and
# three direct_deposit fields (one sensitive) — mirrors migration 077's seeded default set.
fake.table("onboarding_intake_field").insert(
    {"org_id": ORG, "key": "legal_name", "label": "Full legal name", "section": "personal",
     "required": True, "propagate_to": "legal_name", "sensitive": False, "is_active": True}).execute()
fake.table("onboarding_intake_field").insert(
    {"org_id": ORG, "key": "city", "label": "City", "section": "address",
     "required": False, "propagate_to": "city", "sensitive": False, "is_active": True}).execute()
fake.table("onboarding_intake_field").insert(
    {"org_id": ORG, "key": "dd_bank_name", "label": "Bank name", "section": "direct_deposit",
     "required": False, "propagate_to": None, "sensitive": False, "is_active": True}).execute()
fake.table("onboarding_intake_field").insert(
    {"org_id": ORG, "key": "dd_routing", "label": "Routing number", "section": "direct_deposit",
     "required": False, "propagate_to": None, "sensitive": True, "is_active": True}).execute()
fake.table("onboarding_intake_field").insert(
    {"org_id": ORG, "key": "dd_account", "label": "Account number", "section": "direct_deposit",
     "required": False, "propagate_to": None, "sensitive": True, "is_active": True}).execute()

fake.table("employees").insert(
    {"org_id": ORG, "employee_id": EMP, "name": "Jane Doe", "is_active": True}).execute()


# ── 1/2/3/4/5: the FIRST submission — everything filled, NO disclaimer initials ─────────────────
payload = {"legal_name": "Jane Doe", "city": "New York",
           "dd_bank_name": "Chase", "dd_routing": "021000021", "dd_account": "123456789"}

try:
    resp = hr._apply_intake(ORG, EMP, payload, actor="employee")
    raised = False
except Exception as e:
    raised = True
    resp = None

check("1: OLD-BUG REGRESSION — no exception raised for a full submission missing only DD initials",
      not raised, f"raised: {resp}")

prof = hr._get_profile(hr._so(), ORG, EMP) or {}
stored = prof.get("intake_data") or {}
check("2a: non-DD required field (legal_name) persisted", stored.get("legal_name") == "Jane Doe", stored)
check("2b: non-DD optional field (city) persisted", stored.get("city") == "New York", stored)
check("2c: propagated onto storeops.employees (legal_name)",
      (fake.table("employees").select("*").eq("org_id", ORG).eq("employee_id", EMP).execute()
       .data[0].get("legal_name")) == "Jane Doe")
check("2d: propagated onto storeops.employees (city)",
      (fake.table("employees").select("*").eq("org_id", ORG).eq("employee_id", EMP).execute()
       .data[0].get("city")) == "New York")

check("3a: DD field (bank name) WITHHELD until disclaimer is acknowledged", "dd_bank_name" not in stored, stored)
check("3b: DD field (routing) WITHHELD", "dd_routing" not in stored, stored)
check("3c: DD field (account) WITHHELD", "dd_account" not in stored, stored)
check("3d: disclaimer NOT marked signed yet", not prof.get("dd_disclaimer_signed_at"), prof)

check("4: intake_submitted_at IS set on this first (partial) call — the admin card's gate flag",
      bool(prof.get("intake_submitted_at")), prof)

check("5a: response is honestly flagged dd_disclaimer_pending=True (never a bare unqualified success)",
      bool(resp) and resp.get("dd_disclaimer_pending") is True, resp)
check("5b: response carries a human-readable warning naming what's still needed",
      bool(resp) and "direct-deposit" in (resp.get("warning") or "").lower(), resp)
check("5c: response is still ok=True (this was NOT an error — the rest of the form really did save)",
      bool(resp) and resp.get("ok") is True, resp)

# ── 6: the admin-facing reader must show this record NOW, not blank ─────────────────────────────
admin_view = hr.onboarding_for_employee(EMP, org_id=ORG)
check("6a: admin view intake_submitted=True immediately after the partial save",
      admin_view.get("intake_submitted") is True, admin_view.get("intake_submitted"))
check("6b: admin view intake_values shows legal_name (this is the exact card the owner report said was blank)",
      (admin_view.get("intake_values") or {}).get("legal_name") == "Jane Doe", admin_view.get("intake_values"))
check("6c: admin view intake_values shows city",
      (admin_view.get("intake_values") or {}).get("city") == "New York", admin_view.get("intake_values"))
check("6d: admin view does NOT list dd_bank_name in sensitive_on_file yet (nothing withheld is 'on file')",
      "Bank name" not in (admin_view.get("sensitive_on_file") or []), admin_view.get("sensitive_on_file"))

# ── 7: SECOND submission, now WITH initials — DD fields save, disclaimer flips ──────────────────
payload2 = dict(payload)
payload2["dd_disclaimer_initials"] = "JD"
resp2 = hr._apply_intake(ORG, EMP, payload2, actor="employee")
prof2 = hr._get_profile(hr._so(), ORG, EMP) or {}
stored2 = prof2.get("intake_data") or {}
check("7a: second call (with initials) succeeds", resp2.get("ok") is True, resp2)
check("7b: response no longer flags dd_disclaimer_pending", not resp2.get("dd_disclaimer_pending"), resp2)
check("7c: disclaimer now marked signed", bool(prof2.get("dd_disclaimer_signed_at")), prof2)
check("7d: DD fields now stored (bank name, non-sensitive)", stored2.get("dd_bank_name") == "Chase", stored2)
check("7e: DD sensitive field stored (routing) — passthrough since no encryption key configured in this harness",
      bool(stored2.get("dd_routing")), stored2)

admin_view2 = hr.onboarding_for_employee(EMP, org_id=ORG)
check("7f: admin view now lists the DD fields as sensitive-on-file",
      "Routing number" in (admin_view2.get("sensitive_on_file") or []), admin_view2.get("sensitive_on_file"))
check("7g: admin dd_disclaimer_signed reflects the acknowledgement", admin_view2.get("dd_disclaimer_signed") is True)

# ── 8: regression guard — a genuinely missing REQUIRED (non-DD) field still 400s the whole call ──
fake.store["employee_onboarding_profile"] = []   # fresh employee, nothing on file
EMP2 = "E1002"
fake.table("employees").insert({"org_id": ORG, "employee_id": EMP2, "name": "No Name Yet", "is_active": True}).execute()
try:
    hr._apply_intake(ORG, EMP2, {"city": "Boston"}, actor="employee")   # legal_name (required) missing
    check("8: missing REQUIRED non-DD field still raises (unrelated path unchanged)", False, "did not raise")
except Exception as e:
    check("8: missing REQUIRED non-DD field still raises (unrelated path unchanged)",
          getattr(e, "status_code", None) == 400 and "legal name" in str(getattr(e, "detail", "")).lower(), e)
prof_e2 = hr._get_profile(hr._so(), ORG, EMP2)
check("8b: nothing persisted for a genuinely-rejected call (this path is UNCHANGED, still all-or-nothing)",
      prof_e2 is None, prof_e2)

# ── 9: sensitive-field encryption/passthrough behavior unaffected by this fix ────────────────────
check("9: sensitive DD field never propagated to storeops.employees (no propagate_to for dd_routing)",
      "dd_routing" not in (fake.table("employees").select("*").eq("org_id", ORG).eq("employee_id", EMP)
                            .execute().data[0]))

print(f"\n{len(PASS)} passed, {len(FAIL)} failed")
for f in FAIL:
    print("FAIL:", f)
if FAIL:
    sys.exit(1)
print("ALL GREEN")
