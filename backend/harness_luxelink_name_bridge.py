"""Harness for the Luxelink commission fix — part (a): the DURABLE ENGINE NAME-BRIDGE.

Proves, with NO database (a fake Supabase client + direct calls to the PURE resolver):

  1. BRIDGE ATTACHES & PAYS: an employee-scope plan pinned under a rep's ROSTER name ("Robert Smith")
     now attaches to that rep's sales carrying a DIFFERENT POS name ("Bob Smith") when the deterministic
     identity map connects them — the rep is paid > 0 instead of silently skipped ($0).
  2. NO MIS-ATTACH: a rep the map does NOT connect ("Jane Doe") resolves to NO plan and is NOT attached
     to the bridged rep's plan (negative control).
  3. REGRESSION (empty/None map): output is byte-identical to the pre-change behaviour — the bridged
     rep resolves to NO plan and is paid $0, exactly as before the fix.
  4. EXACT MATCH UNTOUCHED: a rep whose POS name already equals the roster name still matches, with or
     without a map supplied.
  5. MAP SHAPE/SOURCE: the map the calc threads in (_rep_canon_map over commcalc.name_map) has the shape
     the bridge expects — {POS salesperson (UPPER) -> roster name}.

Run:  python harness_luxelink_name_bridge.py
"""

import sys
import types

from app.modules.commcalc import commission_engine as ce

_passed = 0
_failed = 0


def check(name, cond, got=None, want=None):
    global _passed, _failed
    if cond:
        _passed += 1
        print(f"  PASS  {name}")
    else:
        _failed += 1
        extra = "" if got is None and want is None else f"   (got={got!r} want={want!r})"
        print(f"  FAIL  {name}{extra}")


# ── fake Supabase client (chained query builder; table-keyed fixtures) ────────────────────────────────
class _FakeQ:
    def __init__(self, rows):
        self._rows = rows

    # every builder method is a no-op that returns self; the fixtures are already scoped per table
    def select(self, *a, **k): return self
    def eq(self, *a, **k): return self
    def in_(self, *a, **k): return self
    def neq(self, *a, **k): return self
    def gte(self, *a, **k): return self
    def lt(self, *a, **k): return self
    def order(self, *a, **k): return self
    def range(self, *a, **k): return self
    def limit(self, *a, **k): return self

    def execute(self):
        return types.SimpleNamespace(data=list(self._rows), count=len(self._rows))


class _FakeClient:
    def __init__(self, tables):
        self._tables = tables

    def schema(self, _s):
        return self

    def table(self, name):
        return _FakeQ(self._tables.get(name, []))


ORG = "org-lux"
PERIOD = "2026-08"

PLAN = {"id": "P1", "name": "Accessory Plan", "is_active": True}
RULE = {"id": "R1", "plan_id": "P1", "payout_kind": "flat_per_unit", "amount": 25,
        "match_field": "any", "match_op": "equals", "match_value": "", "qualifies": True, "sort": 0}
ASSIGN = {"id": "A1", "plan_id": "P1", "scope": "employee", "scope_value": "Robert Smith", "priority": 0}


def _sale(rep, tid):
    return {"salesperson": rep, "store": "S1", "period": PERIOD, "trans_id": tid,
            "trans_type": "Sale", "voided": None, "ext_price": 100.0, "gp": 40.0,
            "product_desc": "Case", "contract_type": "Accessory", "mdn": "", "serial_1": ""}


def _client():
    return _FakeClient({
        "commission_plan": [PLAN],
        "commission_rule": [RULE],
        "commission_tier": [],
        "commission_plan_assignment": [ASSIGN],
        "raw_sales": [_sale("Bob Smith", "T1"), _sale("Bob Smith", "T2"), _sale("Jane Doe", "T3")],
        # everything else degrades to defaults / empty:
        "commission_org_config": [], "store_mapping": [], "stores": [], "employees": [],
        "raw_mi": [], "raw_catalog": [], "daily_sales_feed": [],
    })


# the deterministic POS->roster identity map (what _rep_canon_map produces from commcalc.name_map).
IDMAP = {"BOB SMITH": "Robert Smith"}


def _by_rep(pr):
    return {r["rep"]: r for r in (pr.get("by_rep") or [])}


def main():
    plans, ready = ce._load_plans(_client(), ORG)
    check("fixtures load: 1 plan with 1 rule + 1 employee assignment", ready and len(plans) == 1
          and len(plans[0]["rules"]) == 1 and len(plans[0]["assignments"]) == 1)

    # ── (1) direct resolver: bridge attaches; without a map it does not ───────────────────────────────
    print("\n(1) _resolve_plan_for — the exact predicate that decides attach vs. skip ($0):")
    p_bridged = ce._resolve_plan_for("Bob Smith", "S1", "", plans, identity_map=IDMAP)
    check("Bob Smith (POS) attaches Robert Smith's plan via the bridge", p_bridged is not None
          and p_bridged.get("id") == "P1", p_bridged and p_bridged.get("id"))
    p_nomap = ce._resolve_plan_for("Bob Smith", "S1", "", plans, identity_map=None)
    check("Bob Smith with NO map → no plan (unchanged pre-fix behaviour)", p_nomap is None, p_nomap)
    p_emptymap = ce._resolve_plan_for("Bob Smith", "S1", "", plans, identity_map={})
    check("Bob Smith with EMPTY map → no plan (byte-identical to no map)", p_emptymap is None, p_emptymap)

    # ── (2) negative control: a rep the map does NOT connect never mis-attaches ───────────────────────
    print("\n(2) no mis-attach for a rep the map does not connect:")
    p_jane = ce._resolve_plan_for("Jane Doe", "S1", "", plans, identity_map=IDMAP)
    check("Jane Doe (not in map) → no plan even with the map present", p_jane is None, p_jane)

    # ── (4) exact match still works with and without a map ────────────────────────────────────────────
    print("\n(4) exact roster-name match is untouched by the bridge:")
    p_exact = ce._resolve_plan_for("Robert Smith", "S1", "", plans, identity_map=None)
    check("Robert Smith exact match (no map)", p_exact is not None and p_exact.get("id") == "P1")
    p_exact2 = ce._resolve_plan_for("Robert Smith", "S1", "", plans, identity_map=IDMAP)
    check("Robert Smith exact match (map present, unaffected)",
          p_exact2 is not None and p_exact2.get("id") == "P1")

    # ── (1/2/3) end-to-end preview: the money output ──────────────────────────────────────────────────
    print("\n(1/3) preview() end-to-end — the actual payout:")
    pr_map = ce.preview(_client(), ORG, PERIOD, identity_map=IDMAP)
    br_map = _by_rep(pr_map)
    check("WITH map: Bob Smith is paid", "Bob Smith" in br_map, sorted(br_map))
    check("WITH map: Bob Smith total_payout == 50.00 (2 lines x $25)",
          abs(float((br_map.get("Bob Smith") or {}).get("total_payout", 0)) - 50.0) < 0.005,
          (br_map.get("Bob Smith") or {}).get("total_payout"))
    check("WITH map: Bob Smith plan_name == 'Accessory Plan'",
          (br_map.get("Bob Smith") or {}).get("plan_name") == "Accessory Plan")
    check("WITH map: Jane Doe NOT paid (no plan, no mis-attach)", "Jane Doe" not in br_map, sorted(br_map))

    print("\n(3) preview() with NO map — regression guard (must equal pre-fix behaviour):")
    pr_none = ce.preview(_client(), ORG, PERIOD, identity_map=None)
    br_none = _by_rep(pr_none)
    check("NO map: Bob Smith is NOT paid (silently skipped, exactly as before)",
          "Bob Smith" not in br_none, sorted(br_none))
    check("NO map: no reps paid at all (Robert has no sales lines under that spelling)",
          br_none == {}, sorted(br_none))
    pr_empty = ce.preview(_client(), ORG, PERIOD, identity_map={})
    check("EMPTY map: identical to NO map (both skip Bob)", _by_rep(pr_empty) == br_none)
    # DEFAULT call (no identity_map kwarg at all) must also be byte-identical to the None call.
    pr_default = ce.preview(_client(), ORG, PERIOD)
    check("DEFAULT preview (no identity_map kwarg) == NO-map preview (byte-identical default)",
          _by_rep(pr_default) == br_none)

    # ── (5) map shape/source: _rep_canon_map over commcalc.name_map ───────────────────────────────────
    print("\n(5) identity-map shape/source the calc threads in (_rep_canon_map / commcalc.name_map):")
    from app.modules.commcalc.router import _rep_canon_map
    nm_client = _FakeClient({
        "name_map": [{"epay_salesperson": "Bob Smith", "storeops_name": "Robert Smith"}],
        "rep_aliases": [],
    })
    built = _rep_canon_map(nm_client, ORG)
    check("map shape is {POS name (UPPER) -> roster name}", built == {"BOB SMITH": "Robert Smith"}, built)
    check("map built from name_map drives the bridge identically",
          ce._resolve_plan_for("Bob Smith", "S1", "", plans, identity_map=built) is not None
          and ce._resolve_plan_for("Bob Smith", "S1", "", plans, identity_map=built).get("id") == "P1")

    print(f"\n==== {_passed} passed, {_failed} failed ====")
    return 1 if _failed else 0


if __name__ == "__main__":
    sys.exit(main())
