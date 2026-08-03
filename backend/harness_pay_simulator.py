"""HARNESS — employee pay simulator (mod-commission, 2026-08-03).

Proves the three things the package claims:

  A. PARITY — for N synthetic scenarios, the simulator's dollars are EXACTLY what
     `commission_engine.preview()` produces for the same lines. The simulator must add no arithmetic
     of its own: if these ever diverge, a duplicate formula has crept in.
  B. GROUND TRUTH — the same scenarios also match a hand-computed expectation, so "parity" cannot be
     satisfied by two copies of the same bug (a simulator that returns preview()'s output is trivially
     "in parity" — this leg proves the LINES actually exercise the rules).
  C. SELF-ONLY — `require_self` refuses another rep with 403, allows the caller's own name, and lets a
     company-wide ('all') role through.

Runs OFFLINE against an in-memory fake Supabase client — no network, no database, no writes.
    python3 backend/harness_pay_simulator.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from app.modules.commcalc import commission_engine as ce      # noqa: E402
from app.modules.commcalc import pay_simulator as ps          # noqa: E402

ORG = "00000000-0000-0000-0000-0000000000aa"
REP = "doe, jane"
STORE = "1234 MAIN ST"
PERIOD = "June 2026"


# ── in-memory fake of the supabase client surface preview() actually uses ──────────────────────────
class _Q:
    def __init__(self, rows):
        self._rows = list(rows)

    def select(self, *a, **k):
        return self

    def eq(self, col, val):
        self._rows = [r for r in self._rows if str(r.get(col)) == str(val)]
        return self

    def in_(self, col, vals):
        vs = {str(v) for v in vals}
        self._rows = [r for r in self._rows if str(r.get(col)) in vs]
        return self

    def order(self, *a, **k):
        return self

    def limit(self, n):
        self._rows = self._rows[:n]
        return self

    def range(self, a, b):
        self._rows = self._rows[a:b + 1]
        return self

    def execute(self):
        return type("R", (), {"data": list(self._rows)})()


class _Schema:
    def __init__(self, store, name):
        self._store, self._name = store, name

    def table(self, t):
        return _Q(self._store.get((self._name, t), []))


class FakeClient:
    def __init__(self, tables):
        self._t = tables

    def schema(self, name):
        return _Schema(self._t, name)

    def table(self, t):                      # public schema (app_users, stores, app_config)
        return _Q(self._t.get(("public", t), []))

    def rpc(self, *a, **k):
        return type("R", (), {"execute": lambda s=None: type("R2", (), {"data": []})()})()


def _plan_tables(rules, tiers=(), plan_extra=None):
    plan = {"id": "P1", "org_id": ORG, "name": "Sim Test Plan", "is_active": True,
            "carrier_id": None, "base_tier_metric": "none", "tier_count_basis": None,
            "tier_below_min_multiplier": None}
    plan.update(plan_extra or {})
    return {
        ("commcalc", "commission_plan"): [plan],
        ("commcalc", "commission_rule"): [dict(r, org_id=ORG, plan_id="P1") for r in rules],
        ("commcalc", "commission_tier"): [dict(t, org_id=ORG, plan_id="P1") for t in tiers],
        ("commcalc", "commission_plan_assignment"): [
            {"id": "A1", "org_id": ORG, "plan_id": "P1", "scope": "default", "scope_value": None}],
        ("commcalc", "store_mapping"): [
            {"org_id": ORG, "store_code": "1234", "store_address": STORE, "market": "NY"}],
    }


# ── scenarios: (name, rules, tiers, plan_extra, inputs, expected_total) ────────────────────────────
SCENARIOS = [
    (
        "S1 flat_per_unit x2 rules, no tier",
        [
            {"id": "R1", "label": "New Activation", "match_field": "contract_type",
             "match_op": "equals", "match_value": "New Activation",
             "payout_kind": "flat_per_unit", "amount": 25, "pct": 0, "tiered": False, "sort": 1},
            {"id": "R2", "label": "Upgrade", "match_field": "contract_type",
             "match_op": "equals", "match_value": "Upgrade",
             "payout_kind": "flat_per_unit", "amount": 10, "pct": 0, "tiered": False, "sort": 2},
        ],
        (), None,
        {"rule:R1": {"units": 12}, "rule:R2": {"units": 5}},
        12 * 25 + 5 * 10,                                            # 350.00
    ),
    (
        "S2 pct_gp accessories + flat bonus + tier x1.25",
        [
            {"id": "R3", "label": "Accessory %", "match_field": "department",
             "match_op": "equals", "match_value": "Ondigo",
             "payout_kind": "pct_gp", "amount": 0, "pct": 0.10, "tiered": True, "sort": 1},
            {"id": "R4", "label": "Monthly bonus", "match_field": "contract_type",
             "match_op": "equals", "match_value": "New Activation",
             "payout_kind": "flat", "amount": 100, "pct": 0, "tiered": False, "sort": 2},
        ],
        ({"id": "T1", "min_count": 10, "multiplier": 1.25},),
        {"base_tier_metric": "units"},
        {"rule:R3": {"units": 20, "amount": 30}, "rule:R4": {"units": 3}},
        # tiered leg: 20 lines x (10% of $30 GP) = $60, x1.25 (23 qualifying units >= 10) = $75
        # base leg:   the $100 flat bonus, paid once
        75.0 + 100.0,                                                # 175.00
    ),
    (
        "S3 pct_mrc residual-style + below-lowest-tier floor 0.5",
        [
            {"id": "R5", "label": "Rate plan %", "match_field": "category",
             "match_op": "equals", "match_value": "Rate Plan",
             "payout_kind": "pct_mrc", "amount": 0, "pct": 0.5, "tiered": True, "sort": 1},
        ],
        ({"id": "T2", "min_count": 30, "multiplier": 2.0},),
        {"base_tier_metric": "units", "tier_below_min_multiplier": 0.5},
        {"rule:R5": {"units": 8, "amount": 50}},
        # 8 lines x (50% of $50 MRC) = $200 tiered; 8 units < 30 → below-min floor 0.5 → $100
        100.0,
    ),
]


def _money(v):
    return round(float(v or 0), 2)


def run_parity():
    print("── A/B. PARITY + GROUND TRUTH ─────────────────────────────────────────────────")
    ok = True
    for name, rules, tiers, extra, inputs, expected in SCENARIOS:
        client = FakeClient(_plan_tables(rules, tiers, extra))
        plan, ready, reason = ps._resolve_my_plan(client, ORG, REP, STORE, "NY")
        assert plan is not None, f"{name}: no plan resolved ({reason})"

        # 1. the simulator's own answer (the thing the endpoint returns)
        sim = ps.simulate(client, ORG, PERIOD, REP, STORE, "NY", inputs)
        sim_total = _money((sim.get("result") or {}).get("total_payout"))

        # 2. the ENGINE's answer over the very same lines, called directly — no pay_simulator in the
        #    path except the line builder. If (1) != (2) the simulator has grown its own arithmetic.
        lines, mrc, _applied, _warn = ps.build_lines(client, ORG, plan, REP, STORE, PERIOD, inputs)
        pv = ce.preview(client, ORG, PERIOD, plan_id="P1", only_rep=REP,
                        sales_override=lines, mrc_override=mrc)
        eng_total = _money((pv.get("by_rep") or [{}])[0].get("total_payout"))

        p_ok = sim_total == eng_total
        g_ok = sim_total == _money(expected)
        ok = ok and p_ok and g_ok
        print(f"  {name}")
        print(f"     simulator ${sim_total:>10,.2f} | engine ${eng_total:>10,.2f} | "
              f"expected ${_money(expected):>10,.2f}   parity={'PASS' if p_ok else 'FAIL'} "
              f"truth={'PASS' if g_ok else 'FAIL'}")
        if not (p_ok and g_ok):
            print(f"     result={sim.get('result')}  warnings={sim.get('warnings')}")
    return ok


def run_no_persist():
    print("── D. NO-WRITE ─────────────────────────────────────────────────────────────────")
    # The fake client has no insert/update/upsert/delete at all: any write attempt would raise
    # AttributeError. A clean run of the parity scenarios above is therefore also the proof that
    # neither pay_simulator nor preview() writes anything on this path.
    import re
    src = open(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                            "app/modules/commcalc/pay_simulator.py"), encoding="utf-8").read()
    # DB write verbs only. `.update(` is excluded from the bare scan because dict.update() is used to
    # assemble the synthetic line dicts; a DB update always rides a `.table(...)` chain, which the
    # second scan catches.
    bad = [w for w in (".insert(", ".upsert(", ".delete(", ".rpc(") if w in src]
    chained = [ln.strip() for ln in src.splitlines()
               if ".table(" in ln and re.search(r"\.(insert|upsert|update|delete)\(", ln)]
    ok = not bad and not chained
    print("  fake client exposes no write verbs and the parity run completed → no writes attempted")
    print(f"  pay_simulator.py bare write verbs : {bad or 'none'}")
    print(f"  pay_simulator.py .table() writes  : {chained or 'none'}   {'PASS' if ok else 'FAIL'}")
    return ok


def run_self_only():
    print("── C. SELF-ONLY ENFORCEMENT ────────────────────────────────────────────────────")
    from fastapi import HTTPException
    tables = _plan_tables(SCENARIOS[0][1])
    _au = [{"auth_id": "UID-JANE", "org_id": ORG, "employee_id": "E100",
            "store_code": STORE, "store_codes": [], "full_name": "Jane Doe",
            "role": "rep", "super_admin": False, "is_default_org": True}]
    tables[("storeops", "app_users")] = _au      # where app_users actually lives (migration 003)
    tables[("public", "app_users")] = _au
    tables[("storeops", "employees")] = [
        {"org_id": ORG, "employee_id": "E100", "name": "Jane Doe",
         "epay_salesperson": REP, "home_store": STORE}]
    tables[("storeops", "roles")] = [
        {"org_id": ORG, "name": "rep", "permissions": {"scope": "self"}},
        {"org_id": ORG, "name": "admin", "permissions": {"scope": "all"}}]
    client = FakeClient(tables)

    import app.modules.core.router as core
    orig_uid = core._uid_from_token
    core._uid_from_token = lambda auth: ("UID-JANE" if "jane" in str(auth).lower() else None)
    results = []
    try:
        me = ps.require_self(client, "Bearer jane", "")
        results.append(("self, no rep param", me["rep_name"] == REP, me["rep_name"]))

        me = ps.require_self(client, "Bearer jane", REP)
        results.append(("self, own rep name", me["rep_name"] == REP, me["rep_name"]))

        try:
            ps.require_self(client, "Bearer jane", "smith, bob")
            results.append(("OTHER rep → 403", False, "allowed (LEAK)"))
        except HTTPException as e:
            results.append(("OTHER rep → 403", e.status_code == 403, f"{e.status_code}"))

        try:
            ps.require_self(client, "Bearer nobody", "")
            results.append(("no token → 401", False, "allowed (LEAK)"))
        except HTTPException as e:
            results.append(("no token → 401", e.status_code == 401, f"{e.status_code}"))

        # an 'all'-scope leader may pass another rep (they already read every rep's pay)
        _au[0]["role"] = "admin"
        me = ps.require_self(client, "Bearer jane", "smith, bob")
        results.append(("'all' scope may pass a rep", me.get("impersonated") is True, me["rep_name"]))
    finally:
        core._uid_from_token = orig_uid
    ok = True
    for label, good, detail in results:
        ok = ok and good
        print(f"  {label:<32} {'PASS' if good else 'FAIL'}   ({detail})")
    return ok


def run_gates():
    print("── E. WHAT-IF REPORT GATES (default-closed) ────────────────────────────────────")
    from app.modules.commcalc import whatif_gates as wg
    cases = [
        ("no caller (unresolvable)", None, False),
        ("rep, no grant", {"role": "rep", "perms": {"scope": "self"}}, False),
        ("market manager, no grant", {"role": "dm", "perms": {"scope": "market"}}, False),
        ("market manager, granted via data", {"role": "dm", "perms": {"scope": "market", "data": {wg.EMPLOYEE_PAYOUT: True}}}, True),
        ("market manager, granted via modules", {"role": "dm", "perms": {"scope": "market", "modules": {wg.EMPLOYEE_PAYOUT: True}}}, True),
        ("admin role", {"role": "admin", "perms": {"scope": "market"}}, True),
        ("all-scope", {"role": "dm", "perms": {"scope": "all"}}, True),
        ("super admin", {"super_admin": True, "perms": {}}, True),
    ]
    ok = True
    for label, caller, want in cases:
        got = wg.whatif_report_allowed(caller, wg.EMPLOYEE_PAYOUT)
        ok = ok and (got == want)
        print(f"  {label:<38} allowed={str(got):<5} want={str(want):<5} "
              f"{'PASS' if got == want else 'FAIL'}")
    # a grant on ONE report never opens another
    c = {"role": "dm", "perms": {"scope": "market", "data": {wg.CARRIER_INCOME: True}}}
    cross = [wg.whatif_report_allowed(c, k) for k in wg.WHATIF_REPORTS]
    good = cross == [False, False, False, True]
    ok = ok and good
    print(f"  {'per-report isolation (1 grant ≠ 4)':<38} {cross}  {'PASS' if good else 'FAIL'}")
    return ok


if __name__ == "__main__":
    a = run_parity()
    b = run_self_only()
    c = run_no_persist()
    d = run_gates()
    print("\n" + ("ALL PASS" if (a and b and c and d) else "FAILURES ABOVE"))
    sys.exit(0 if (a and b and c and d) else 1)
