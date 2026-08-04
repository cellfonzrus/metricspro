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


def _plan_tables(rules, tiers=(), plan_extra=None, org=ORG, plan_id="P1"):
    plan = {"id": plan_id, "org_id": org, "name": "Sim Test Plan", "is_active": True,
            "carrier_id": None, "base_tier_metric": "none", "tier_count_basis": None,
            "tier_below_min_multiplier": None}
    plan.update(plan_extra or {})
    return {
        ("commcalc", "commission_plan"): [plan],
        ("commcalc", "commission_rule"): [dict(r, org_id=org, plan_id=plan_id) for r in rules],
        ("commcalc", "commission_tier"): [dict(t, org_id=org, plan_id=plan_id) for t in tiers],
        ("commcalc", "commission_plan_assignment"): [
            {"id": "A1", "org_id": org, "plan_id": plan_id, "scope": "default", "scope_value": None}],
        ("commcalc", "store_mapping"): [
            {"org_id": org, "store_code": "1234", "store_address": STORE, "market": "NY"}],
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


# ── F. TENANT MODE: the acting org must come from the VERIFIED org_id, not an arbitrary membership ──
# THE DEFECT THIS LEG PINS DOWN (owner-reported 2026-08-04, luxelink): the simulator resolved the
# caller's tenant with `app_users .eq(auth_id) .limit(1)` and IGNORED the org_id query param. One
# login has one app_users row PER TENANT (mig 706), so limit(1) picked an arbitrary membership — in
# practice the house/Boost one — and `_carrier_mode` then answered 'boost' for a PLAN-mode tenant,
# producing "Your tenant is paid by the Boost component engine" on a Commission-Plans tenant.
ORG_BOOST = "00000000-0000-0000-0000-0000000000b0"   # house-shaped: default carrier IS Boost
ORG_PLAN = "00000000-0000-0000-0000-0000000000f1"    # luxelink-shaped: default carrier is NOT Boost
REP_PLAN = "rivera, ana"
STORE_PLAN = "77 PLAN AVE"


def _two_org_tables():
    """One login (UID-MULTI) that is a member of BOTH orgs, plus a super-admin (UID-ROOT) whose only
    membership is the Boost org — the owner's real shape when acting as another tenant."""
    t = _plan_tables(
        [{"id": "R9", "label": "New Activation", "match_field": "contract_type",
          "match_op": "equals", "match_value": "New Activation",
          "payout_kind": "flat_per_unit", "amount": 40, "pct": 0, "tiered": False, "sort": 1}],
        org=ORG_PLAN, plan_id="PP1")
    t[("commcalc", "carrier")] = [
        {"id": "C1", "org_id": ORG_BOOST, "name": "Boost Mobile", "code": "boost", "is_default": True},
        {"id": "C2", "org_id": ORG_PLAN, "name": "Total Wireless", "code": "total", "is_default": True},
    ]
    t[("commcalc", "store_mapping")] = [
        {"org_id": ORG_PLAN, "store_code": "77", "store_address": STORE_PLAN, "market": "FL"},
        {"org_id": ORG_BOOST, "store_code": "1234", "store_address": STORE, "market": "NY"},
    ]
    au = [
        # DELIBERATE ORDER: the Boost membership is FIRST, so a limit(1)/rows[0] resolution lands on
        # Boost. That is exactly the bug, and it is what the negative control below reproduces.
        {"auth_id": "UID-MULTI", "org_id": ORG_BOOST, "employee_id": "E1", "store_code": STORE,
         "store_codes": [], "full_name": "Jane Doe", "role": "rep", "super_admin": False,
         "is_default_org": True},
        {"auth_id": "UID-MULTI", "org_id": ORG_PLAN, "employee_id": "E2", "store_code": STORE_PLAN,
         "store_codes": [], "full_name": "Jane Doe", "role": "rep", "super_admin": False,
         "is_default_org": False},
        {"auth_id": "UID-ROOT", "org_id": ORG_BOOST, "employee_id": "", "store_code": "",
         "store_codes": [], "full_name": "Owner", "role": "admin", "super_admin": True,
         "is_default_org": True},
    ]
    t[("storeops", "app_users")] = au
    t[("public", "app_users")] = au
    t[("storeops", "employees")] = [
        {"id": 1, "org_id": ORG_BOOST, "employee_id": "E1", "name": "Jane Doe",
         "epay_salesperson": REP, "home_store": STORE, "email": "jane@x.com", "is_active": True},
        {"id": 2, "org_id": ORG_PLAN, "employee_id": "E2", "name": "Ana Rivera",
         "epay_salesperson": REP_PLAN, "home_store": STORE_PLAN, "email": "ana@x.com",
         "is_active": True},
        {"id": 3, "org_id": ORG_PLAN, "employee_id": "E3", "name": "Bob Smith",
         "epay_salesperson": "smith, bob", "home_store": STORE_PLAN, "email": "bob@x.com",
         "is_active": True},
    ]
    t[("storeops", "roles")] = [
        {"org_id": ORG_BOOST, "name": "rep", "permissions": {"scope": "self"}},
        {"org_id": ORG_PLAN, "name": "rep", "permissions": {"scope": "self"}},
        {"org_id": ORG_BOOST, "name": "admin", "permissions": {"scope": "all"}},
        {"org_id": ORG_PLAN, "name": "admin", "permissions": {"scope": "all"}},
    ]
    return t


def run_tenant_mode():
    print("── F. TENANT MODE HONORS org_id (the luxelink defect) ──────────────────────────")
    client = FakeClient(_two_org_tables())
    import app.modules.core.router as core
    orig_uid = core._uid_from_token
    core._uid_from_token = lambda auth: ({"multi": "UID-MULTI", "root": "UID-ROOT"}
                                         .get(str(auth).split()[-1].lower()))
    results = []
    try:
        # F0. the resolver itself is org-aware and PURE — no client, no network.
        rows = _two_org_tables()[("storeops", "app_users")][:2]
        results.append(("_pick_membership honors requested org",
                        ps._pick_membership(rows, ORG_PLAN)["org_id"] == ORG_PLAN, ORG_PLAN[-4:]))
        results.append(("_pick_membership falls back to default",
                        ps._pick_membership(rows, "")["org_id"] == ORG_BOOST, "default"))
        results.append(("_pick_membership ignores a NON-member org (no widening)",
                        ps._pick_membership(rows, "00000000-0000-0000-0000-0000000000ff")["org_id"]
                        == ORG_BOOST, "falls back"))

        # F1. THE FIX. Same login, same token — only org_id differs. Mode must follow the ORG.
        cp = ps.context(client, "Bearer multi", PERIOD, requested_org=ORG_PLAN)
        results.append(("plan-mode org → carrier_mode 'plan'", cp.get("carrier_mode") == "plan",
                        cp.get("carrier_mode")))
        results.append(("plan-mode org → ok, NO Boost message",
                        cp.get("ok") is True and cp.get("unsupported") is None
                        and "Boost component engine" not in str(cp.get("reason") or ""),
                        f"ok={cp.get('ok')} plan={(cp.get('plan') or {}).get('name')}"))
        results.append(("plan-mode org → acted in that org", cp.get("org_id") == ORG_PLAN,
                        str(cp.get("org_id"))[-4:]))
        results.append(("plan-mode org → the RIGHT employee (E2/Ana), not the house one",
                        cp.get("employee_id") == "E2" and cp.get("rep_name") == REP_PLAN,
                        f"{cp.get('employee_id')}/{cp.get('rep_name')}"))
        results.append(("plan-mode org → levers came from THAT org's plan",
                        len(cp.get("levers") or []) == 1, str(len(cp.get("levers") or []))))

        # F2. Boost-mode org keeps the existing, correct guidance — the fix must not flip it.
        cb = ps.context(client, "Bearer multi", PERIOD, requested_org=ORG_BOOST)
        results.append(("boost-mode org → carrier_mode 'boost'", cb.get("carrier_mode") == "boost",
                        cb.get("carrier_mode")))
        results.append(("boost-mode org → keeps the Boost guidance",
                        cb.get("unsupported") == "boost"
                        and "Boost component engine" in str(cb.get("reason") or ""),
                        str(cb.get("unsupported"))))

        # F3. A real projection, end to end, in the plan tenant (the thing the owner could not get).
        sim = ps.run(client, "Bearer multi", PERIOD, {"rule:R9": {"units": 7}},
                     requested_org=ORG_PLAN)
        got = _money((sim.get("result") or {}).get("total_payout"))
        results.append(("plan tenant produces dollars (7 x $40)", got == 280.0, f"${got:,.2f}"))

        # F4. SUPER-ADMIN CROSS-TENANT: membership only in Boost, acting in the plan tenant. No
        #     employee record there → roster to pick from, then a real simulation for that rep.
        cs = ps.context(client, "Bearer root", PERIOD, requested_org=ORG_PLAN)
        results.append(("super-admin follows the switcher into the plan tenant",
                        cs.get("org_id") == ORG_PLAN and cs.get("carrier_mode") == "plan",
                        f"{str(cs.get('org_id'))[-4:]}/{cs.get('carrier_mode')}"))
        results.append(("super-admin w/o employee link → pick-an-employee, not 403/Boost",
                        cs.get("needs_rep") is True and cs.get("unsupported") is None,
                        str(cs.get("reason"))[:44]))
        roster = [p["value"] for p in (cs.get("reps") or [])]
        results.append(("roster = THAT tenant's people only (pick-don't-type)",
                        cs.get("can_pick_rep") is True and sorted(roster) == sorted([REP_PLAN, "smith, bob"]),
                        str(roster)))
        cr = ps.context(client, "Bearer root", PERIOD, requested_rep=REP_PLAN, requested_org=ORG_PLAN)
        results.append(("super-admin + picked rep → that rep's OWN store drives the plan",
                        cr.get("ok") is True and cr.get("store") == STORE_PLAN,
                        f"{cr.get('store')}"))
        sr = ps.run(client, "Bearer root", PERIOD, {"rule:R9": {"units": 3}},
                    requested_rep=REP_PLAN, requested_org=ORG_PLAN)
        gotr = _money((sr.get("result") or {}).get("total_payout"))
        results.append(("super-admin simulates any employee in any tenant (3 x $40)",
                        gotr == 120.0, f"${gotr:,.2f}"))

        # F5. ISOLATION UNCHANGED. A normal rep asking for a tenant they don't belong to lands back
        #     in their own default membership — the org_id param can never widen access here.
        au = _two_org_tables()[("storeops", "app_users")]
        solo_tables = _two_org_tables()
        solo_tables[("storeops", "app_users")] = [au[0]]
        solo_tables[("public", "app_users")] = [au[0]]
        solo = FakeClient(solo_tables)
        ci = ps.context(solo, "Bearer multi", PERIOD, requested_org=ORG_PLAN)
        results.append(("non-member org requested → stays in own tenant (no leak)",
                        ci.get("org_id") == ORG_BOOST and ci.get("carrier_mode") == "boost",
                        str(ci.get("org_id"))[-4:]))
        # ... and a rep still cannot name a coworker.
        from fastapi import HTTPException
        try:
            ps.context(client, "Bearer multi", PERIOD, requested_rep="smith, bob",
                       requested_org=ORG_PLAN)
            results.append(("rep naming a coworker → 403", False, "allowed (LEAK)"))
        except HTTPException as e:
            results.append(("rep naming a coworker → 403", e.status_code == 403, str(e.status_code)))

        # F6. NEGATIVE CONTROL — reproduce the OLD resolution (first membership row wins, org_id
        #     ignored) and show it yields the WRONG mode for the plan tenant. Proves this leg would
        #     have FAILED before the fix, i.e. it is not a tautology.
        old_org = str(ps._memberships(client, "UID-MULTI")[0].get("org_id") or "")
        results.append(("negative control: old limit(1) resolution → wrong mode",
                        old_org == ORG_BOOST and ps._carrier_mode(client, old_org) == "boost"
                        and ps._carrier_mode(client, ORG_PLAN) == "plan",
                        f"old={old_org[-4:]}→boost, verified={ORG_PLAN[-4:]}→plan"))
    finally:
        core._uid_from_token = orig_uid
    ok = True
    for label, good, detail in results:
        ok = ok and good
        print(f"  {label:<58} {'PASS' if good else 'FAIL'}   ({detail})")
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
    e = run_tenant_mode()
    print("\n" + ("ALL PASS" if (a and b and c and d and e) else "FAILURES ABOVE"))
    sys.exit(0 if (a and b and c and d and e) else 1)
