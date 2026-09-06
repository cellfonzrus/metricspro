"""Offline proof harness for the people-domain (storeops/hr) admin-attention providers added by the
2026-07-26 settings-audit package (backend/app/modules/storeops/attention.py,
backend/app/modules/hr/attention.py).

No database, no network: a small recording fake Supabase client (supporting the actual .eq/.gte/.lte/
.in_ filters these providers use — the shared core harness_import_health.py's fake client stubs those
out, so a purpose-built one lives here) feeds the REAL provider functions via the REAL
register_provider() decorator + collect_attention() aggregator from app.modules.core.import_health.

Proves:
  A. storeops_no_payscale — only ACTIVE employees with a null/zero pay_rate are flagged; a rate>0 or
     an explicitly inactive employee never is.
  B. storeops_stores_no_coverage — only an ACTIVE store that both (a) has a punch in the last 30 days
     and (b) has no active home-store employee AND nothing scheduled in the next 14 days is flagged;
     a store missing any one of those three conditions is not.
  C. storeops_kiosk_no_face_template — only an employee with 2+ KIOSK punches and zero
     face_descriptors row is flagged; a non-kiosk device, a single punch, and an enrolled employee are
     each individually proven NOT to trip it.
  D. hr_onboarding_stuck — the tenant's configured onboarding_stuck_days threshold is honored (a
     profile just under the threshold doesn't fire, just over does); a provisioned/active profile
     never counts regardless of age; a missing invited_at never crashes/counts.
  E. hr_pii_encryption — the exact two shapes from the dispatch: ciphertext-exists-but-key-missing
     (ERROR) and key-configured-but-sensitive-plaintext-exists (WARNING, using the org's OWN
     configured sensitive-field registry, not a hard-coded key list); NEITHER item fires on a clean
     org; the provider never calls crypto.decrypt() anywhere (grepped, see check E7).
  F. ORG ISOLATION — every one of the 5 providers, run for two different orgs sharing overlapping
     employee_ids, never leaks org B's rows into org A's finding (and vice versa).
  G. Registration wiring itself — both attention.py modules register through the REAL
     import_health.register_provider, appear in collect_attention()'s cheap/heavy split exactly as
     declared, and a raising provider is isolated (never taken down by another provider's bug).
  H. Every item from all 5 providers carries group='other' — the ONLY group of the aggregator's four
     (import/mapping/duplicate/other) that frontend/src/components/AdminAttention.tsx's modal actually
     renders (GROUP_ORDER); any other group string is counted in the header pill but invisible in the
     modal body (Gate-1 finding, 2026-07-26).

Run:  cd backend && python3 harness_people_attention.py
"""
import os
import sys
from datetime import datetime, timezone, timedelta

sys.path.insert(0, ".")

# FACE ID IS OFF BY DEFAULT, PLATFORM-WIDE. `storeops.face_recognition` reads a global kill switch
# (FACE_ID_ENABLED, owner directive 2026-08-14) and migration 420 added a PER-TENANT master switch on
# storeops.tenants (owner directive 2026-08-09). With face recognition off there is nothing to
# enroll, so `storeops_kiosk_no_face_template` correctly returns NOTHING — it must not nag an admin
# about a feature the owner deliberately switched off, and it fails CLOSED when the config cannot be
# read. Section C exercises that provider's detection logic, so it has to turn the feature ON; before
# this the fixture predated both switches and section C was asserting against a provider that could
# never fire. The gate itself is now asserted too (C-gate below), which is the behaviour that
# actually shipped.
os.environ.setdefault("FACE_ID_ENABLED", "1")

PASS, FAIL = 0, 0


def ok(name, cond, extra=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  ok   {name}")
    else:
        FAIL += 1
        print(f"  FAIL {name} {extra}")


NOW = datetime.now(timezone.utc)
ORG_A = "00000000-0000-0000-0000-000000000001"
ORG_B = "00000000-0000-0000-0000-0000000000bb"


def iso(dt):
    return dt.isoformat()


def d(days_ago):
    return (NOW - timedelta(days=days_ago)).date().isoformat()


def d_future(days_ahead):
    return (NOW + timedelta(days=days_ahead)).date().isoformat()


# ── fake supabase client — supports eq / gte / lte / in_ for real (unlike the cross-module harness's
#    stub, which no-ops in_() and never applies gte/lte) ───────────────────────────────────────────
class _Q:
    def __init__(self, rows):
        self._rows = list(rows)
        self._eq, self._gte, self._lte, self._in = {}, {}, {}, {}

    def select(self, *a, **k):
        return self

    def eq(self, k, v):
        self._eq[k] = v
        return self

    def gte(self, k, v):
        self._gte[k] = v
        return self

    def lte(self, k, v):
        self._lte[k] = v
        return self

    def in_(self, k, v):
        self._in[k] = set(v)
        return self

    def order(self, *a, **k):
        return self

    def limit(self, *a, **k):
        return self

    def execute(self):
        out = []
        for r in self._rows:
            if any(r.get(k) != v for k, v in self._eq.items()):
                continue
            if any((r.get(k) is None or str(r.get(k)) < str(v)) for k, v in self._gte.items()):
                continue
            if any((r.get(k) is None or str(r.get(k)) > str(v)) for k, v in self._lte.items()):
                continue
            if any(str(r.get(k)) not in {str(x) for x in v} for k, v in self._in.items()):
                continue
            out.append(dict(r))
        return type("R", (), {"data": out})()


class _Schema:
    def __init__(self, store, schema):
        self.store, self.schema = store, schema

    def table(self, name):
        return _Q(self.store.get(f"{self.schema}.{name}", []))


class FakeClient:
    def __init__(self, store):
        self.store = store

    def schema(self, s):
        return _Schema(self.store, s)


# ── fixtures ─────────────────────────────────────────────────────────────────────────────────────────
def build_store():
    return {
        # A: payscale — org A has a $0-rate active emp, a null-rate active emp, a good-rate active
        #    emp, and an INACTIVE $0-rate emp (must NOT be flagged).
        "storeops.employees": [
            {"id": 1, "employee_id": "E1", "org_id": ORG_A, "name": "Zero Rate", "pay_rate": 0,
             "is_active": True, "home_store": "S1"},
            {"id": 2, "employee_id": "E2", "org_id": ORG_A, "name": "Null Rate", "pay_rate": None,
             "is_active": True, "home_store": "S1"},
            {"id": 3, "employee_id": "E3", "org_id": ORG_A, "name": "Good Rate", "pay_rate": 18.5,
             "is_active": True, "home_store": "S2"},
            {"id": 4, "employee_id": "E4", "org_id": ORG_A, "name": "Inactive Zero", "pay_rate": 0,
             "is_active": False, "home_store": "S3"},
            # org B: a DIFFERENT employee also with id-shape overlap potential ("E1") but org-scoped.
            {"id": 101, "employee_id": "E1", "org_id": ORG_B, "name": "B Good Rate", "pay_rate": 22,
             "is_active": True, "home_store": "SB1"},
        ],
        # B: stores + coverage. S3 = active, punched recently, no staff, nothing scheduled -> FLAG.
        #    S1/S2 have staff (E1/E2 home S1, E3 home S2) -> not flagged even though S1 has punches.
        #    S4 = active, punched, unstaffed, BUT has a future shift -> not flagged (schedule exists).
        #    S5 = active, unstaffed, nothing scheduled, but NO recent punch -> not flagged (dormant, fine).
        #    S6 = INACTIVE, punched, unstaffed -> never flagged (not an active store).
        "storeops.stores": [
            {"store_code": "S1", "org_id": ORG_A, "is_active": True},
            {"store_code": "S2", "org_id": ORG_A, "is_active": True},
            {"store_code": "S3", "org_id": ORG_A, "is_active": True},
            {"store_code": "S4", "org_id": ORG_A, "is_active": True},
            {"store_code": "S5", "org_id": ORG_A, "is_active": True},
            {"store_code": "S6", "org_id": ORG_A, "is_active": False},
            {"store_code": "SB1", "org_id": ORG_B, "is_active": True},
        ],
        "storeops.shifts": [
            {"org_id": ORG_A, "store_code": "S4", "shift_date": d_future(3), "is_deleted": False},
        ],
        # C: kiosk enrollment. E10 = 2 kiosk punches, never enrolled -> FLAG. E11 = 1 kiosk punch only
        #    -> not flagged (benefit of the doubt). E12 = 2 kiosk punches AND enrolled -> not flagged.
        #    E13 = 2 punches but device='override' (not kiosk) -> not flagged.
        "storeops.timelog": [
            # (B) coverage punches
            {"org_id": ORG_A, "store_code": "S1", "work_date": d(5), "employee_id": "1", "device": "kiosk"},
            {"org_id": ORG_A, "store_code": "S3", "work_date": d(5), "employee_id": "3", "device": "kiosk"},
            {"org_id": ORG_A, "store_code": "S4", "work_date": d(5), "employee_id": "3", "device": "kiosk"},
            {"org_id": ORG_A, "store_code": "S6", "work_date": d(5), "employee_id": "3", "device": "kiosk"},
            # (C) enrollment punches
            {"org_id": ORG_A, "employee_id": "E10", "employee_name": "Ten", "device": "kiosk", "work_date": d(3)},
            {"org_id": ORG_A, "employee_id": "E10", "employee_name": "Ten", "device": "kiosk", "work_date": d(1)},
            {"org_id": ORG_A, "employee_id": "E11", "employee_name": "Eleven", "device": "kiosk", "work_date": d(2)},
            {"org_id": ORG_A, "employee_id": "E12", "employee_name": "Twelve", "device": "kiosk", "work_date": d(3)},
            {"org_id": ORG_A, "employee_id": "E12", "employee_name": "Twelve", "device": "kiosk", "work_date": d(1)},
            {"org_id": ORG_A, "employee_id": "E13", "employee_name": "Thirteen", "device": "kiosk-override", "work_date": d(3)},
            {"org_id": ORG_A, "employee_id": "E13", "employee_name": "Thirteen", "device": "kiosk-override", "work_date": d(1)},
            # org B: same employee_id string "E10" reused — must not cross-contaminate org A's result.
            {"org_id": ORG_B, "employee_id": "E10", "employee_name": "B Ten", "device": "kiosk", "work_date": d(3)},
            {"org_id": ORG_B, "employee_id": "E10", "employee_name": "B Ten", "device": "kiosk", "work_date": d(1)},
        ],
        "storeops.face_descriptors": [
            {"org_id": ORG_A, "employee_id": "E12"},
            # the (B) coverage-only punches reuse numeric employee ids "1"/"3" — enroll them so the
            # (C) kiosk-enrollment provider's org-A result stays isolated to the intentional E10 case.
            {"org_id": ORG_A, "employee_id": "1"},
            {"org_id": ORG_A, "employee_id": "3"},
            {"org_id": ORG_B, "employee_id": "E10"},   # org B's E10 IS enrolled — org A's E10 still isn't
        ],
        # D: onboarding stuck. org A threshold = 5 days (configured). P1 invited 10d ago, still
        #    'invited' -> FLAG. P2 invited 3d ago, 'in_progress' -> not yet (under threshold). P3
        #    invited 30d ago but 'active' -> never flagged (already through). P4 has no invited_at at
        #    all -> never crashes, never counted.
        "storeops.tenants": [
            # `face_recognition_enabled` must be PRESENT on the row: face_recognition treats a missing
            # column as "migration 420 has not run here" and fails closed (available=False).
            {"org_id": ORG_A, "onboarding_stuck_days": 5, "face_recognition_enabled": True},
            # org B: no row -> code default (7) applies, and face recognition unavailable -> off.
        ],
        "storeops.employee_onboarding_profile": [
            {"org_id": ORG_A, "employee_id": "P1", "workflow_status": "invited", "invited_at": iso(NOW - timedelta(days=10)),
             "intake_data": {}},
            {"org_id": ORG_A, "employee_id": "P2", "workflow_status": "in_progress", "invited_at": iso(NOW - timedelta(days=3)),
             "intake_data": {}},
            {"org_id": ORG_A, "employee_id": "P3", "workflow_status": "active", "invited_at": iso(NOW - timedelta(days=30)),
             "intake_data": {}},
            {"org_id": ORG_A, "employee_id": "P4", "workflow_status": "invited", "invited_at": None,
             "intake_data": {}},
            # org B: invited 8 days ago, default threshold 7 -> FLAG (proves the code-default path).
            {"org_id": ORG_B, "employee_id": "PB1", "workflow_status": "docs_submitted",
             "invited_at": iso(NOW - timedelta(days=8)), "intake_data": {}},
        ],
        "storeops.onboarding_intake_field": [
            {"org_id": ORG_A, "key": "dd_routing", "sensitive": True},
            {"org_id": ORG_A, "key": "dd_account", "sensitive": True},
            {"org_id": ORG_A, "key": "legal_name", "sensitive": False},
        ],
    }


def make_ctx(store, now=None):
    client = FakeClient(store)
    return client, {"now": now or NOW, "feed_health": {}}


def run_group(providers_module, register_fn, key, client, org_id, ctx):
    from app.modules.core import import_health as IH
    # isolate: snapshot + restore the REAL registry so this harness never pollutes other harnesses
    # run in the same process, and re-registering is itself the idempotency proof for "registered twice".
    before = list(IH.PROVIDERS)
    try:
        registered = {p["key"] for p in IH.PROVIDERS}
        assert key in registered, f"{key} not registered"
        spec = next(p for p in IH.PROVIDERS if p["key"] == key)
        return spec["fn"](client, org_id, ctx), spec
    finally:
        IH.PROVIDERS[:] = before


def main():
    from app.modules.core import import_health as IH
    from app.modules.storeops import attention as SA
    from app.modules.hr import attention as HA

    # ── G. registration wiring (through the REAL decorator) ────────────────────────────────────────
    before_keys = {p["key"] for p in IH.PROVIDERS}
    SA.register(IH.register_provider)
    HA.register(IH.register_provider)
    after = {p["key"]: p for p in IH.PROVIDERS}
    for k, expect_cost in (
        ("storeops_no_payscale", "cheap"), ("storeops_stores_no_coverage", "heavy"),
        ("storeops_kiosk_no_face_template", "heavy"), ("hr_onboarding_stuck", "cheap"),
        ("hr_pii_encryption", "cheap"),
    ):
        ok(f"G registered: {k}", k in after, f"missing from {sorted(after)}")
        ok(f"G cost tag correct: {k}", after.get(k, {}).get("cost") == expect_cost,
           after.get(k, {}).get("cost"))
    ok("G re-register is idempotent (no dup)", len(IH.PROVIDERS) == len(after))

    store = build_store()
    client, ctx = make_ctx(store)

    # ── A. no-payscale ──────────────────────────────────────────────────────────────────────────────
    items = after["storeops_no_payscale"]["fn"](client, ORG_A, ctx)
    ok("A fires exactly one item", len(items) == 1, items)
    detail = items[0]["detail"] if items else ""
    ok("A count = 2 (Zero Rate + Null Rate, not Inactive Zero)", items and items[0]["count"] == 2, items)
    ok("A severity=warning", items and items[0]["severity"] == "warning")
    ok("A group='other' (rendered by AdminAttention.tsx GROUP_ORDER)", items and items[0]["group"] == "other", items)
    ok("A names the flagged employees", "Zero Rate" in detail and "Null Rate" in detail, detail)
    ok("A never names the inactive $0 employee", "Inactive Zero" not in detail, detail)
    ok("A never names the good-rate employee", "Good Rate" not in detail, detail)
    items_b = after["storeops_no_payscale"]["fn"](client, ORG_B, ctx)
    ok("A org isolation: org B's good-rate emp never flagged", items_b == [], items_b)

    # ── B. stores no coverage ──────────────────────────────────────────────────────────────────────
    items = after["storeops_stores_no_coverage"]["fn"](client, ORG_A, ctx)
    ok("B fires exactly one item", len(items) == 1, items)
    detail = items[0]["detail"] if items else ""
    ok("B count = 1 (only S3)", items and items[0]["count"] == 1, items)
    ok("B names S3", "S3" in detail, detail)
    ok("B group='other' (rendered by AdminAttention.tsx GROUP_ORDER)", items and items[0]["group"] == "other", items)
    ok("B never flags S1 (staffed)", "S1" not in detail, detail)
    ok("B never flags S2 (staffed, no punch anyway)", "S2" not in detail, detail)
    ok("B never flags S4 (has a future shift)", "S4" not in detail, detail)
    ok("B never flags S5 (no recent punch)", "S5" not in detail, detail)
    ok("B never flags S6 (inactive store)", "S6" not in detail, detail)
    items_b = after["storeops_stores_no_coverage"]["fn"](client, ORG_B, ctx)
    ok("B org isolation: org B has no stores-no-coverage gap", items_b == [], items_b)

    # ── C. kiosk unenrolled face ────────────────────────────────────────────────────────────────────
    items = after["storeops_kiosk_no_face_template"]["fn"](client, ORG_A, ctx)
    ok("C fires exactly one item", len(items) == 1, items)
    detail = items[0]["detail"] if items else ""
    ok("C count = 1 (only Ten/E10)", items and items[0]["count"] == 1, items)
    ok("C names Ten", "Ten" in detail, detail)
    ok("C group='other' (rendered by AdminAttention.tsx GROUP_ORDER)", items and items[0]["group"] == "other", items)
    ok("C never flags Eleven (only 1 kiosk punch)", "Eleven" not in detail, detail)
    ok("C never flags Twelve (enrolled)", "Twelve" not in detail, detail)
    ok("C never flags Thirteen (not device=kiosk)", "Thirteen" not in detail, detail)
    items_b = after["storeops_kiosk_no_face_template"]["fn"](client, ORG_B, ctx)
    ok("C org isolation: org B's own E10 IS enrolled there -> no finding", items_b == [], items_b)

    # ── C-gate. Face recognition OFF ⇒ nothing to enroll ⇒ NO finding (mig 420, owner 2026-08-09;
    # global kill switch, owner 2026-08-14). Fail-closed: an unreadable/absent config counts as off.
    # Without this, section C above could pass while the provider nagged tenants who switched the
    # feature off — and the gate is exactly what made the old fixture stop firing.
    import copy as _copy
    _store_off = _copy.deepcopy(store)
    _store_off["storeops.tenants"][0]["face_recognition_enabled"] = False
    _off_items = after["storeops_kiosk_no_face_template"]["fn"](FakeClient(_store_off), ORG_A, ctx)
    ok("C-gate tenant switch OFF -> no kiosk-enrollment nag (mig 420)", _off_items == [], _off_items)

    _store_unmig = _copy.deepcopy(store)
    _store_unmig["storeops.tenants"][0].pop("face_recognition_enabled", None)
    _unmig_items = after["storeops_kiosk_no_face_template"]["fn"](FakeClient(_store_unmig), ORG_A, ctx)
    ok("C-gate migration 420 not run (column absent) -> fails CLOSED, no finding",
       _unmig_items == [], _unmig_items)

    # ── D. onboarding stuck (+ configured threshold) ───────────────────────────────────────────────
    ok("D threshold reads the configured 5 for org A", HA.onboarding_stuck_days(client, ORG_A) == 5)
    ok("D threshold defaults to 7 for org B (no tenants row)", HA.onboarding_stuck_days(client, ORG_B) == 7)
    items = after["hr_onboarding_stuck"]["fn"](client, ORG_A, ctx)
    ok("D fires exactly one item", len(items) == 1, items)
    ok("D count = 1 (only P1 — P2 under threshold, P3 active, P4 no invited_at)",
       items and items[0]["count"] == 1, items)
    ok("D group='other' (rendered by AdminAttention.tsx GROUP_ORDER)", items and items[0]["group"] == "other", items)
    items_b = after["hr_onboarding_stuck"]["fn"](client, ORG_B, ctx)
    ok("D org B: PB1 (8d, docs_submitted) trips the default-7 threshold", len(items_b) == 1, items_b)
    ok("D org B count = 1", items_b and items_b[0]["count"] == 1, items_b)
    # boundary: bump org A's threshold to 11 -> P1 (10d) no longer over it.
    store2 = build_store()
    store2["storeops.tenants"][0]["onboarding_stuck_days"] = 11
    client2, ctx2 = make_ctx(store2)
    items_boundary = after["hr_onboarding_stuck"]["fn"](client2, ORG_A, ctx2)
    ok("D boundary: raising the threshold past P1's age clears the finding", items_boundary == [],
       items_boundary)

    # ── E. PII encryption — key-missing-with-ciphertext (ERROR) ───────────────────────────────────
    from cryptography.fernet import Fernet
    from app.core.config import settings
    from app.core import crypto
    real_key, real_keys = settings.FIELD_ENCRYPTION_KEY, settings.FIELD_ENCRYPTION_KEYS
    try:
        settings.FIELD_ENCRYPTION_KEY = Fernet.generate_key().decode()
        settings.FIELD_ENCRYPTION_KEYS = ""
        ok("E crypto reports enabled with a key set", crypto.is_enabled())
        ciphertext = crypto.encrypt("123456789")
        ok("E produced real ciphertext", crypto.is_encrypted(ciphertext))

        store_e = build_store()
        store_e["storeops.employee_onboarding_profile"][0]["intake_data"] = {"dd_routing": ciphertext}
        client_e, ctx_e = make_ctx(store_e)

        # E1: key now REMOVED (simulates "lost/rotated away") -> ciphertext exists, no key -> ERROR.
        settings.FIELD_ENCRYPTION_KEY, settings.FIELD_ENCRYPTION_KEYS = "", ""
        ok("E1 crypto now reports disabled", not crypto.is_enabled())
        items = after["hr_pii_encryption"]["fn"](client_e, ORG_A, ctx_e)
        keys = {i["key"] for i in items}
        ok("E1 fires pii_key_missing", "pii_key_missing" in keys, items)
        sev = next(i["severity"] for i in items if i["key"] == "pii_key_missing")
        ok("E1 severity = error", sev == "error", sev)
        grp = next(i["group"] for i in items if i["key"] == "pii_key_missing")
        ok("E1 group='other' (rendered by AdminAttention.tsx GROUP_ORDER)", grp == "other", grp)
        ok("E1 never mentions the actual field value/ciphertext",
           all(ciphertext not in i["detail"] for i in items), items)
        ok("E1 does NOT ALSO fire pii_plaintext_unbackfilled (key is off, can't backfill)",
           "pii_plaintext_unbackfilled" not in keys, items)

        # E2: key restored -> ciphertext readable again -> no error; but a DIFFERENT profile has a
        # plaintext sensitive field sitting unencrypted -> WARNING (and only counts sensitive keys,
        # per the org's OWN onboarding_intake_field registry).
        settings.FIELD_ENCRYPTION_KEY = real_key or Fernet.generate_key().decode()
        store_e["storeops.employee_onboarding_profile"][1]["intake_data"] = {
            "dd_account": "0009876543",            # sensitive key, plaintext -> should count
            "legal_name": "Plain Jane",             # NOT a sensitive key -> must not count
        }
        client_e2, ctx_e2 = make_ctx(store_e)
        items2 = after["hr_pii_encryption"]["fn"](client_e2, ORG_A, ctx_e2)
        keys2 = {i["key"] for i in items2}
        ok("E2 no longer fires pii_key_missing (key restored)", "pii_key_missing" not in keys2, items2)
        ok("E2 fires pii_plaintext_unbackfilled", "pii_plaintext_unbackfilled" in keys2, items2)
        w = next(i for i in items2 if i["key"] == "pii_plaintext_unbackfilled")
        ok("E2 severity = warning", w["severity"] == "warning")
        ok("E2 group='other' (rendered by AdminAttention.tsx GROUP_ORDER)", w["group"] == "other", w)
        ok("E2 count = 1 (only the sensitive key, not legal_name)", w["count"] == 1, w)
        ok("E2 never echoes the plaintext value itself", "0009876543" not in w["detail"], w["detail"])

        # E3: clean org (no ciphertext, no plaintext-sensitive) -> nothing fires.
        clean = build_store()
        client_clean, ctx_clean = make_ctx(clean)
        items3 = after["hr_pii_encryption"]["fn"](client_clean, ORG_A, ctx_clean)
        ok("E3 clean org: nothing fires", items3 == [], items3)

        # E4: org isolation — org A's ciphertext must never affect org B's read (they're on the fake
        # store's org_id-scoped rows only, but confirm explicitly).
        items_b = after["hr_pii_encryption"]["fn"](client_e2, ORG_B, ctx_e2)
        ok("E4 org isolation: org B's own (clean) profile reports nothing", items_b == [], items_b)
    finally:
        settings.FIELD_ENCRYPTION_KEY, settings.FIELD_ENCRYPTION_KEYS = real_key, real_keys

    # E7: static check — the module never calls crypto.decrypt anywhere.
    import inspect
    src = inspect.getsource(HA)
    ok("E7 hr/attention.py never calls crypto.decrypt(", "crypto.decrypt(" not in src, src.count("decrypt"))

    # ── F. exception isolation via the REAL aggregator (collect_attention) ────────────────────────
    from app.modules.core import import_health as IH2
    before_all = list(IH2.PROVIDERS)
    try:
        @IH2.register_provider("boom_test", label="boom", group="other", cost="cheap")
        def _boom(client, org_id, ctx):
            raise RuntimeError("kaboom")
        result = IH2.collect_attention(client, ORG_A, deep=True, feed_h={"ready": True})
        errs = {e["key"] for e in result["provider_errors"]}
        ok("F a raising provider is isolated (reported, not fatal)", "boom_test" in errs, result["provider_errors"])
        found_keys = {i.get("provider") for i in result["items"]}
        ok("F other providers still ran despite the boom", "storeops_no_payscale" in found_keys, found_keys)
        ok("F deep=True ran the heavy providers too",
           "storeops_stores_no_coverage" in found_keys and "storeops_kiosk_no_face_template" in found_keys,
           found_keys)
    finally:
        IH2.PROVIDERS[:] = before_all

    print(f"\n{PASS} passed, {FAIL} failed")
    if FAIL:
        sys.exit(1)


if __name__ == "__main__":
    main()
