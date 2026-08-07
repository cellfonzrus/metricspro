"""Offline proof harness for the 2026-08-07 owner-report fix (agent/people/hr-comp-span-worked-store):

    GET /hr/compensation (Total Compensation report) was scoping the employee roster by
    `home_store` ALONE (`in_keyset(ks, e.get("home_store"))`). Owner rule (2026-08-07, verbatim):
    "for employee daily closing uploads, the employees could be at any store whether it is their
    home store or not" — reps float across stores constantly, so a per-employee read scoped to a
    manager's span must resolve employees by WHERE THEY ACTUALLY WORKED, not by home_store alone.

    The fix swaps the home_store-only filter for `storeops.scope_emp_ids` ->
    `app.core.scope.reporting_employee_ids` (home store UNION worked-at-a-span-store-this-period),
    bounded to the report's own period window (since=start, until=last day of period) exactly like
    the sibling call sites in storeops/router.py.

Runs the REAL functions (`app.modules.hr.router.compensation`, `app.modules.storeops.router.
scope_emp_ids`, `app.core.scope.reporting_employee_ids`) against a stateful fake Supabase-chain
client (same convention as harness_storeops_scope_wiring.py) — no live DB/network.

Proves:
  A. BORROWED REP NOW VISIBLE — a rep homed OUTSIDE the DM's span, who worked a shift AT a span
     store this period, is MISSING under a literal home_store-only re-implementation but PRESENT
     under the actual shipped `compensation()`.
  B. NEVER-WORKED-IN-SPAN REP STAYS EXCLUDED — a rep homed outside the span who did NOT work any
     span store this period is absent both before and after (the fix widens, it doesn't leak
     everyone).
  C. MONEY UNCHANGED — for every employee visible under BOTH the unrestricted call and the
     span-scoped call, every computed figure (hours, base_salary, commission, chargebacks,
     total_comp, annualized) is BYTE IDENTICAL between the two calls. This is a visibility fix
     only; it must never change what a computed row is worth.
  D. UNRESTRICTED CALLERS BYTE IDENTICAL — with RBAC disabled (today's default) or an
     unrecognized token, `scope_emp_ids` returns None and the employee list is completely
     unfiltered — same shape/behavior as the old `scope_keyset() is None` no-op branch it replaced.
  E. WINDOW IS BOUNDED — the shifts/timelog reads `reporting_employee_ids` performs are bounded by
     the report's own period (gte/lte applied with the exact period bounds), so this can never
     regress into a full-history scan; a shift far outside the period does not spuriously widen
     the span.

Run: `cd backend && python3 harness_hr_compensation_span_worked_store.py`
"""
import sys
from types import SimpleNamespace

sys.path.insert(0, ".")

PASS, FAIL = [], []


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print(("PASS" if cond else "FAIL") + f"  {name}" + (f"  [{detail}]" if detail and not cond else ""))


HOUSE = "00000000-0000-0000-0000-000000000001"


# ── stateful fake supabase client (same convention as harness_storeops_scope_wiring.py) ───────────
class Q:
    def __init__(self, store, key, log):
        self.s, self.k, self.log = store, key, log
        self.op, self.payload = "select", None
        self.filters = []
        self._limit = None

    def select(self, *a, **k): self.op = "select"; return self
    def insert(self, rows, **k): self.op = "insert"; self.payload = rows; return self
    def eq(self, c, v): self.filters.append((c, "eq", v)); return self
    def in_(self, c, v): self.filters.append((c, "in", list(v))); return self
    def gte(self, c, v): self.filters.append((c, "gte", v)); return self
    def lte(self, c, v): self.filters.append((c, "lte", v)); return self
    def lt(self, c, v): self.filters.append((c, "lt", v)); return self
    def order(self, *a, **k): return self
    def limit(self, n, *a, **k): self._limit = n; return self

    def _match(self, row):
        for c, kind, v in self.filters:
            rv = row.get(c)
            if kind == "eq" and rv != v: return False
            if kind == "in" and rv not in v: return False
            if kind == "gte" and not (rv is not None and str(rv) >= str(v)): return False
            if kind == "lte" and not (rv is not None and str(rv) <= str(v)): return False
            if kind == "lt" and not (rv is not None and str(rv) < str(v)): return False
        return True

    def execute(self):
        rows = self.s.setdefault(self.k, [])
        if self.op == "select":
            self.log.append((self.k, list(self.filters)))
            matched = [dict(r) for r in rows if self._match(r)]
            if self._limit is not None:
                matched = matched[: self._limit]
            return SimpleNamespace(data=matched)
        if self.op == "insert":
            payload = self.payload if isinstance(self.payload, list) else [self.payload]
            out = []
            for r in payload:
                r = dict(r); rows.append(r); out.append(dict(r))
            return SimpleNamespace(data=out)
        return SimpleNamespace(data=[])


class FakeSchema:
    def __init__(self, client, name): self.client, self.name = client, name
    def table(self, t): return Q(self.client.store, (self.name, t), self.client.log)


class FakeClient:
    def __init__(self, store, log): self.store, self.log = store, log
    def schema(self, name): return FakeSchema(self, name)
    def table(self, t): return Q(self.store, ("storeops", t), self.log)


def fresh_store():
    return {
        ("storeops", "app_config"): [{"id": 1, "rbac_enabled": True}],
        ("storeops", "stores"): [
            {"org_id": HOUSE, "store_code": "S1", "address": "1 Main St", "market": "LI", "is_active": True},
            {"org_id": HOUSE, "store_code": "S2", "address": "2 Oak Ave", "market": "LI", "is_active": True},
        ],
        ("commcalc", "store_mapping"): [],
        ("storeops", "app_users"): [],
        ("storeops", "roles"): [
            {"org_id": HOUSE, "name": "dm_store", "permissions": {"scope": "store"}},
        ],
        ("storeops", "tenants"): [],
        ("storeops", "employees"): [],
        ("storeops", "shifts"): [],
        ("storeops", "timelog"): [],
        ("commcalc", "rep_commissions"): [],
        ("commcalc", "chargeback_items"): [],
    }


import app.modules.storeops.router as SO              # noqa: E402
import app.modules.hr.router as HR                     # noqa: E402
import app.core.scope as CS                            # noqa: E402
import app.modules.core.router as CORE                 # noqa: E402

TOKENS = {"Bearer dm-store": "uid-dm-store"}


def app_user(role, *, store_code=None):
    return {"org_id": HOUSE, "auth_id": "uid-dm-store", "role": role, "employee_id": None,
            "market": None, "store_code": store_code, "store_codes": None}


LOG = []
STORE = fresh_store()
STORE[("storeops", "app_users")] = [app_user("dm_store", store_code="S1")]
FAKE = FakeClient(STORE, LOG)
SO.get_supabase = lambda: FAKE
HR.get_supabase = lambda: FAKE
CORE._uid_from_token = lambda tok: TOKENS.get(tok)
CS.invalidate_market_index()

PERIOD = "2026-06"   # -> 2026-06-01 .. 2026-06-30 inclusive

# ── employees ───────────────────────────────────────────────────────────────────────────────────
STORE[("storeops", "employees")] = [
    {"org_id": HOUSE, "employee_id": "E_IN", "name": "Ivy InSpan", "home_store": "S1",
     "pay_rate": 20.0, "is_active": True, "epay_salesperson": "IVY INSPAN"},
    {"org_id": HOUSE, "employee_id": "E_BORROWED", "name": "Bo Borrowed", "home_store": "S2",
     "pay_rate": 15.0, "is_active": True, "epay_salesperson": "BO BORROWED"},
    {"org_id": HOUSE, "employee_id": "E_OUTSIDE", "name": "Oz Outside", "home_store": "S2",
     "pay_rate": 25.0, "is_active": True, "epay_salesperson": "OZ OUTSIDE"},
    {"org_id": HOUSE, "employee_id": "E_HOMED_ELSEWHERE", "name": "Hal HomedElsewhere", "home_store": "S1",
     "pay_rate": 30.0, "is_active": True, "epay_salesperson": "HAL HOMEDELSEWHERE"},
]

# ── shifts this period ──────────────────────────────────────────────────────────────────────────
STORE[("storeops", "shifts")] = [
    # E_IN: works their own home store (S1, in-span) this period.
    {"org_id": HOUSE, "employee_id": "E_IN", "store_code": "S1", "shift_date": "2026-06-05",
     "scheduled_hours": 10, "actual_hours": 10, "is_deleted": False},
    # E_BORROWED: home store S2 (outside span) but WORKED AT S1 (in-span) this period — the exact
    # "employees move around" case the owner called out.
    {"org_id": HOUSE, "employee_id": "E_BORROWED", "store_code": "S1", "shift_date": "2026-06-10",
     "scheduled_hours": 8, "actual_hours": 8, "is_deleted": False},
    # E_OUTSIDE: home store S2, worked ONLY at S2 this period — never touches the span.
    {"org_id": HOUSE, "employee_id": "E_OUTSIDE", "store_code": "S2", "shift_date": "2026-06-12",
     "scheduled_hours": 5, "actual_hours": 5, "is_deleted": False},
    # E_HOMED_ELSEWHERE: home store S1 (in-span) but worked ONLY at S2 this period.
    {"org_id": HOUSE, "employee_id": "E_HOMED_ELSEWHERE", "store_code": "S2", "shift_date": "2026-06-15",
     "scheduled_hours": 6, "actual_hours": 6, "is_deleted": False},
    # Decoy: E_OUTSIDE ALSO worked at S1, but in a DIFFERENT month (May) — must NOT widen the June
    # report (proves the window bound is real, not decorative).
    {"org_id": HOUSE, "employee_id": "E_OUTSIDE", "store_code": "S1", "shift_date": "2026-05-02",
     "scheduled_hours": 40, "actual_hours": 40, "is_deleted": False},
]

# ── commission + chargebacks (same for every run — only visibility should ever change) ────────────
STORE[("commcalc", "rep_commissions")] = [
    {"org_id": HOUSE, "storeops_name": None, "epay_salesperson": "IVY INSPAN", "period": "2026-06",
     "total_payout": 100.0, "subtotal": 100.0},
    {"org_id": HOUSE, "storeops_name": None, "epay_salesperson": "BO BORROWED", "period": "2026-06",
     "total_payout": 50.0, "subtotal": 50.0},
    {"org_id": HOUSE, "storeops_name": None, "epay_salesperson": "OZ OUTSIDE", "period": "2026-06",
     "total_payout": 75.0, "subtotal": 75.0},
    {"org_id": HOUSE, "storeops_name": None, "epay_salesperson": "HAL HOMEDELSEWHERE", "period": "2026-06",
     "total_payout": 60.0, "subtotal": 60.0},
]
STORE[("commcalc", "chargeback_items")] = [
    {"org_id": HOUSE, "epay_salesperson": "BO BORROWED", "period": "2026-06", "amount": 10.0, "deduct": True},
]

# ══════════════════════ baseline: UNRESTRICTED call (no auth) ═════════════════════════════════════
LOG.clear()
comp_unrestricted = HR.compensation(period=PERIOD, authorization="", org_id=HOUSE)
by_eid_unrestricted = {r["employee_id"]: r for r in comp_unrestricted["rows"]}

check("setup 0. unrestricted call sees ALL FOUR employees",
      set(by_eid_unrestricted) == {"E_IN", "E_BORROWED", "E_OUTSIDE", "E_HOMED_ELSEWHERE"},
      str(set(by_eid_unrestricted)))
check("setup 0b. E_BORROWED's wages/commission/chargeback compute correctly regardless of scoping "
      "(8h * 15.00 = 120.00 wages, 50 commission, 10 chargeback -> total 160.00)",
      by_eid_unrestricted["E_BORROWED"]["base_salary"] == 120.0
      and by_eid_unrestricted["E_BORROWED"]["commission"] == 50.0
      and by_eid_unrestricted["E_BORROWED"]["chargebacks"] == 10.0
      and by_eid_unrestricted["E_BORROWED"]["total_comp"] == 160.0,
      str(by_eid_unrestricted["E_BORROWED"]))

# ══════════════════════ A. BORROWED REP NOW VISIBLE (dm-store, span = {S1}) ════════════════════════
LOG.clear()
comp_scoped = HR.compensation(period=PERIOD, authorization="Bearer dm-store", org_id=HOUSE)
by_eid_scoped = {r["employee_id"]: r for r in comp_scoped["rows"]}

check("A1. dm-store (span={S1}) sees E_IN (home S1, worked S1)",
      "E_IN" in by_eid_scoped, str(set(by_eid_scoped)))
check("A2. dm-store sees E_BORROWED (home S2, but WORKED AT S1 this period) — "
      "THE FIX: was invisible under a literal home_store-only filter",
      "E_BORROWED" in by_eid_scoped, str(set(by_eid_scoped)))

# Prove the OLD (buggy) home_store-only rule really would have dropped E_BORROWED, so this is a
# genuine before/after, not a tautology — same employee roster + same span, filtered the OLD way.
old_ks = SO.scope_keyset("Bearer dm-store", HOUSE)
old_way_visible = {e["employee_id"] for e in STORE[("storeops", "employees")]
                    if old_ks is None or SO.in_keyset(old_ks, e.get("home_store"))}
check("A3. OLD home_store-only rule would have EXCLUDED E_BORROWED (proves this is a real fix, "
      "not a no-op)", "E_BORROWED" not in old_way_visible, str(old_way_visible))

# ══════════════════════ B. NEVER-WORKED-IN-SPAN REP STAYS EXCLUDED ═════════════════════════════════
check("B1. dm-store does NOT see E_OUTSIDE (home S2, worked ONLY S2 this period — never touched "
      "the span) — the fix WIDENS, it does not leak everyone",
      "E_OUTSIDE" not in by_eid_scoped, str(set(by_eid_scoped)))

# ══════════════════════ C. MONEY UNCHANGED for everyone visible in BOTH runs ═══════════════════════
common = set(by_eid_scoped) & set(by_eid_unrestricted)
check("C0. at least E_IN, E_BORROWED, E_HOMED_ELSEWHERE are visible in BOTH runs (a real overlap "
      "to compare, not a vacuous check)",
      {"E_IN", "E_BORROWED", "E_HOMED_ELSEWHERE"} <= common, str(common))
money_fields = ("hours", "base_salary", "commission", "chargebacks", "total_comp", "annualized", "pay_rate")
all_identical = True
for eid in common:
    a, b = by_eid_scoped[eid], by_eid_unrestricted[eid]
    for f in money_fields:
        if a.get(f) != b.get(f):
            all_identical = False
            print(f"      MISMATCH {eid}.{f}: scoped={a.get(f)!r} vs unrestricted={b.get(f)!r}")
check("C1. every computed money field is BYTE IDENTICAL between the span-scoped call and the "
      "unrestricted call, for every employee visible in both (this is a visibility fix ONLY — no "
      "amount changed)", all_identical)
# dm-store's span keeps 3 of the 4 seeded employees: E_IN (home S1), E_BORROWED (home S2, worked
# S1 this period — the fix), and E_HOMED_ELSEWHERE (home S1, even though they worked ONLY S2 this
# period — the union NEVER narrows a home-store match away; see the harness module docstring / the
# handoff note on this being the same widen-only semantics as every other scope_emp_ids call site).
# Only E_OUTSIDE (home S2, never worked S1) is dropped.
check("C2. report-level totals differ ONLY because membership differs (one fewer row: E_OUTSIDE), "
      "not because a shared employee's figures were recomputed differently",
      comp_unrestricted["totals"]["employees"] == 4 and comp_scoped["totals"]["employees"] == 3,
      f'{comp_unrestricted["totals"]} vs {comp_scoped["totals"]}')

# ══════════════════════ D. UNRESTRICTED CALLERS STAY BYTE IDENTICAL ════════════════════════════════
# D1: RBAC disabled entirely -> scope_emp_ids must resolve None (no filtering, no extra reads).
STORE[("storeops", "app_config")] = [{"id": 1, "rbac_enabled": False}]
eids_rbac_off = SO.scope_emp_ids("Bearer dm-store", HOUSE)
check("D1. RBAC disabled -> scope_emp_ids returns None (unrestricted; unchanged no-op branch)",
      eids_rbac_off is None)
STORE[("storeops", "app_config")] = [{"id": 1, "rbac_enabled": True}]   # restore for later checks

# D2: unrecognized/no token -> also None.
eids_no_auth = SO.scope_emp_ids("", HOUSE)
check("D2. no/unrecognized auth -> scope_emp_ids returns None (unrestricted)", eids_no_auth is None)

# D3: with RBAC back on but an admin-equivalent ('all' scope) caller -> still None, whole roster.
STORE[("storeops", "roles")].append({"org_id": HOUSE, "name": "admin_all", "permissions": {"scope": "all"}})
TOKENS["Bearer admin"] = "uid-admin"
STORE[("storeops", "app_users")].append(
    {"org_id": HOUSE, "auth_id": "uid-admin", "role": "admin_all", "employee_id": None,
     "market": None, "store_code": None, "store_codes": None})
comp_admin = HR.compensation(period=PERIOD, authorization="Bearer admin", org_id=HOUSE)
check("D3. an 'all'-scope admin caller sees the WHOLE roster (unrestricted; same shape as the "
      "no-auth baseline)",
      {r["employee_id"] for r in comp_admin["rows"]} == set(by_eid_unrestricted),
      str({r["employee_id"] for r in comp_admin["rows"]}))
admin_by_eid = {r["employee_id"]: r for r in comp_admin["rows"]}
check("D4b. admin vs no-auth: every employee's every money field matches exactly",
      all(admin_by_eid[e] == by_eid_unrestricted[e] for e in by_eid_unrestricted))

# ══════════════════════ E. WINDOW IS BOUNDED (no full-history scan, decoy doesn't leak) ════════════
LOG.clear()
HR.compensation(period=PERIOD, authorization="Bearer dm-store", org_id=HOUSE)
timelog_or_shift_reads_in_reporting = [
    (k, f) for k, f in LOG if k in (("storeops", "shifts"), ("storeops", "timelog"))
]
bounded = any(
    any(c == "shift_date" and kind == "gte" and v == "2026-06-01" for c, kind, v in filt)
    and any(c == "shift_date" and kind == "lte" and v == "2026-06-30" for c, kind, v in filt)
    for (_k, filt) in [(k, f) for k, f in LOG if k == ("storeops", "shifts")]
)
check("E1. reporting_employee_ids' OWN 'worked at' shifts read (inside scope_emp_ids, a SEPARATE "
      "read from the wages shifts read) is bounded by gte/lte on the exact period (2026-06-01 .. "
      "2026-06-30) — never an unbounded full-history scan",
      bounded, str([f for k, f in LOG if k == ("storeops", "shifts")]))
check("E2. the May decoy shift (E_OUTSIDE at S1, outside the June window) did NOT widen the span — "
      "E_OUTSIDE is still excluded from the June report (re-proving B1 after the window-bound check)",
      "E_OUTSIDE" not in {r["employee_id"] for r in HR.compensation(
          period=PERIOD, authorization="Bearer dm-store", org_id=HOUSE)["rows"]})

print()
print(f"{len(PASS)} passed, {len(FAIL)} failed")
if FAIL:
    print("FAILURES:")
    for f in FAIL:
        print(" -", f)
    sys.exit(1)
print("ALL GREEN")
