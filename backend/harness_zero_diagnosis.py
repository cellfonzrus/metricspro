"""Harness for the "plan attached but $0 — why" per-rule diagnosis (read/display only).

Proves, with NO database (a tiny in-memory fake postgrest client seeded with commcalc fixtures), that
`commission_drilldown.explain_rep` surfaces the engine's OWN coverage explanation for an ATTACHED rep
whose rules match nothing:

  A. CT-KEYED RULE OVER BLANK-CT LINES → zero_diagnosis present, warnings include ct_rules_vs_blank_ct
     for the attached plan, and the 0-match rule carries a field_distribution over THIS rep's lines with
     a high blank_pct on contract_type. total_payout is $0. NO payout math is changed.
  B. A RULE THAT MATCHES (match_field 'any', flat per unit) → plan pays > $0 → zero_diagnosis ABSENT
     (the negative control: the panel only appears when it should).
  C. _field_distribution unit checks: blank_pct math, top_values ordering, and the synthetic/computed
     field flag (accessory / activation_bucket / any) is not given a blank-vs-present breakdown.

Run:  python harness_zero_diagnosis.py
"""
import sys

from app.modules.commcalc import commission_drilldown as D

_passed = _failed = 0


def check(name, cond, got=None, want=None):
    global _passed, _failed
    if cond:
        _passed += 1
        print(f"  PASS  {name}")
    else:
        _failed += 1
        extra = "" if got is None and want is None else f"   (got={got!r} want={want!r})"
        print(f"  FAIL  {name}{extra}")


ORG = "00000000-0000-0000-0000-0000000000aa"
PERIOD = "2026-07"
REP = "John Smith"


# ── minimal in-memory postgrest-style client (select-only; degrades to [] for unseeded tables) ──────
class _Resp:
    def __init__(self, data):
        self.data = data


class _Q:
    def __init__(self, rows):
        self.rows = list(rows)
        self.filters = []

    def select(self, *a, **k):
        return self

    def eq(self, k, v):
        self.filters.append(("eq", k, v)); return self

    def in_(self, k, v):
        self.filters.append(("in", k, list(v))); return self

    def order(self, col, desc=False):
        self._order = (col, desc); return self

    def limit(self, n):
        return self

    def range(self, a, b):
        self._range = (a, b); return self

    def _keep(self, r):
        for op, k, v in self.filters:
            if op == "eq" and r.get(k) != v:
                return False
            if op == "in" and r.get(k) not in v:
                return False
        return True

    def execute(self):
        out = [r for r in self.rows if self._keep(r)]
        rng = getattr(self, "_range", None)
        if rng:
            out = out[rng[0]:rng[1] + 1]
        return _Resp(out)


class _Table:
    def __init__(self, store, schema, name):
        self.rows = store.get((schema, name), [])

    def select(self, *a, **k):
        return _Q(self.rows).select(*a, **k)


class _Schema:
    def __init__(self, store, schema):
        self.store, self.schema = store, schema

    def table(self, name):
        return _Table(self.store, self.schema, name)

    def rpc(self, name, params):
        raise Exception(f"PGRST202 {name} not available")


class FakeClient:
    def __init__(self, store):
        self.store = store

    def schema(self, name):
        return _Schema(self.store, name)


# ── fixtures ────────────────────────────────────────────────────────────────────────────────────
PLAN_ID = "plan-1111"


def _sales(contract_types):
    """One raw_sales row per contract_type value ('' == blank), all for REP in PERIOD."""
    out = []
    for i, ct in enumerate(contract_types):
        out.append({
            "org_id": ORG, "period": PERIOD, "trans_id": f"T{i}", "trans_date": "2026-07-05",
            "store": "Store A", "salesperson": REP, "department": "Phones", "category": "Handset",
            "product_desc": f"Device {i}", "contract_type": ct, "tender_type": "cash",
            "ext_price": 500.0 + i, "gp": 100.0 + i, "mdn": f"555000{i:04d}",
            "serial_1": f"IMEI{i:010d}", "voided": None, "trans_type": "Sale",
        })
    return out


def _store(rule):
    """A commcalc store seeded with ONE active plan whose single rule is `rule`, assigned to REP by name,
    plus this rep's blank-heavy sale lines. 10 lines: 8 blank contract_type (80%), 2 'premium'."""
    return {
        ("commcalc", "commission_plan"): [
            {"id": PLAN_ID, "org_id": ORG, "name": "Activation Plan", "is_active": True,
             "base_tier_metric": "none", "carrier_id": None},
        ],
        ("commcalc", "commission_rule"): [dict(rule, org_id=ORG, plan_id=PLAN_ID)],
        ("commcalc", "commission_tier"): [],
        ("commcalc", "commission_plan_assignment"): [
            {"id": "a1", "org_id": ORG, "plan_id": PLAN_ID, "scope": "employee",
             "scope_value": REP, "priority": 0},
        ],
        ("commcalc", "raw_sales"): _sales(["", "", "", "", "", "", "", "", "premium", "premium"]),
    }


CT_RULE = {"id": "r-ct", "label": "Activation", "match_field": "contract_type",
           "match_op": "equals", "match_value": "new", "payout_kind": "flat_per_unit",
           "amount": 20.0, "pct": 0.0, "tiered": False, "sort": 0}
ANY_RULE = {"id": "r-any", "label": "Every line", "match_field": "any",
            "match_op": "equals", "match_value": "", "payout_kind": "flat_per_unit",
            "amount": 5.0, "pct": 0.0, "tiered": False, "sort": 0}


# ═══════════════════════════════════════════════════════════════════════════════════════════════
print("A. contract_type-keyed rule over blank-CT lines → zero_diagnosis surfaced")
res = D.explain_rep(FakeClient(_store(CT_RULE)), ORG, PERIOD, REP)
pc = res.get("plan_component") or {}
check("A1 plan attached (plan_name present)", pc.get("plan_name") == "Activation Plan", pc.get("plan_name"))
check("A2 plan pays $0", (pc.get("total_payout") or 0) == 0, pc.get("total_payout"))
zd = pc.get("zero_diagnosis")
check("A3 zero_diagnosis attached", isinstance(zd, dict), type(zd).__name__)
codes = {w.get("code") for w in ((zd or {}).get("warnings") or [])}
check("A4 warnings include ct_rules_vs_blank_ct", "ct_rules_vs_blank_ct" in codes, sorted(codes))
check("A5 warnings scoped to the attached plan",
      all(w.get("plan") == "Activation Plan" for w in (zd or {}).get("warnings") or []))
zr = (zd or {}).get("rules") or []
ctr = next((r for r in zr if r.get("match_field") == "contract_type"), None)
check("A6 the ct rule is listed as 0-match", ctr is not None and ctr.get("matched_lines") == 0,
      ctr and ctr.get("matched_lines"))
fd = (ctr or {}).get("field_distribution") or {}
check("A7 field_distribution blank_pct high (8/10 = 80%)", fd.get("blank_pct") == 80.0, fd.get("blank_pct"))
check("A8 field_distribution top_values shows the present value 'premium'",
      any(tv.get("value") == "premium" and tv.get("count") == 2 for tv in (fd.get("top_values") or [])),
      fd.get("top_values"))
check("A9 legacy _zero_reasons still present (graceful shape kept)",
      isinstance(res.get("zero_explanation"), list) and len(res["zero_explanation"]) > 0)

print("\nB. a rule that DOES match (match_field 'any') → plan pays, zero_diagnosis ABSENT")
res2 = D.explain_rep(FakeClient(_store(ANY_RULE)), ORG, PERIOD, REP)
pc2 = res2.get("plan_component") or {}
check("B1 plan attached", pc2.get("plan_name") == "Activation Plan", pc2.get("plan_name"))
check("B2 plan pays > $0 (10 lines × $5)", (pc2.get("total_payout") or 0) > 0, pc2.get("total_payout"))
check("B3 NO zero_diagnosis when a rule matches", "zero_diagnosis" not in pc2,
      list(pc2.keys()) if "zero_diagnosis" in pc2 else "absent")

print("\nC. _field_distribution unit behaviour")
lines = [{"contract_type": ""}, {"contract_type": ""}, {"contract_type": "new"},
         {"contract_type": "upgrade"}, {"contract_type": "new"}]
d = D._field_distribution(lines, "contract_type")
check("C1 blank_pct = 2/5 = 40%", d.get("blank_pct") == 40.0, d.get("blank_pct"))
check("C2 top_values ordered by count desc ('new' first, count 2)",
      d["top_values"][0] == {"value": "new", "count": 2}, d.get("top_values"))
check("C3 total counts all lines", d.get("total") == 5, d.get("total"))
for syn in ("accessory", "activation_bucket", "any", ""):
    ds = D._field_distribution(lines, syn)
    check(f"C4 '{syn or '(empty)'}' flagged computed_field (no blank breakdown)",
          ds.get("computed_field") is True and "blank_pct" not in ds, ds)

print(f"\n{'='*60}\n{_passed} passed, {_failed} failed")
sys.exit(1 if _failed else 0)
