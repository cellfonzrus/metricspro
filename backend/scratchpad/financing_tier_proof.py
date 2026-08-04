"""Proof for agent/commission/financing-report — OWNER DIRECTIVE + ANSWERS 2026-08-04. MONEY-TOUCHING.

DIRECTIVE: "need another report for tracking the financing, edge in case of total and acima in case of
boost … should have assignable target for each store in target area and target based commission payout
right now we have flat payment, need it tiered levels."
ANSWERS:   "achieved rate applies to that months sales, attainment is monthly."

THE ONE ASSERTION EVERYTHING ELSE HANGS OFF (section A): with NO financing tiers configured, the payout
is **byte-identical** to the pre-change engine — same dict, key for key, cent for cent — across a matrix
of fixtures x detail x coverage, plus a randomised fuzz. Section B then proves the tiered arithmetic is
right when an owner DOES configure it, and section C proves the detection registry cannot silently
mis-classify (word anchoring, the tender-vs-model-name trap, the "not configured claims nothing" rule).

Run:  cd backend && python3 scratchpad/financing_tier_proof.py
"""
import copy
import json
import os
import random
import subprocess
import sys
import types

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import app.modules.commcalc.commission_engine as CE                     # noqa: E402
import app.modules.commcalc.financing_tiers as FT                       # noqa: E402
import app.modules.commcalc.financing_registry as FR                    # noqa: E402
import app.modules.commcalc.financing_report as FREP                    # noqa: E402

HOUSE = "00000000-0000-0000-0000-000000000001"
LUX = "854f6d7b-6590-4e4d-88ab-646f560d4f4c"
OTHER = "11111111-2222-3333-4444-555555555555"
TW_FIN = "TW Financing"          # the tenant's own tender label — only the MECHANISM is asserted
PERIOD = "July 2026"

PASS = FAIL = 0
FAILED = []


def check(name, cond, extra=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  ok  {name}")
    else:
        FAIL += 1
        FAILED.append(name)
        print(f"FAIL  {name}   {extra}")


# ── PRISTINE pre-change engine, pinned to this package's BASE commit ─────────────────────────────
_PINNED_BASE = "bd01381"


def _load_old():
    repo = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))

    def show(p):
        return subprocess.check_output(["git", "-C", repo, "show", f"{_PINNED_BASE}:{p}"], text=True)

    old = types.ModuleType("OLD_commission_engine")
    exec(compile(show("backend/app/modules/commcalc/commission_engine.py"),
                 "OLD_commission_engine.py", "exec"), old.__dict__)
    old._ref = _PINNED_BASE
    return old


OLD_CE = _load_old()
print(f"(differential pinned to the pre-change engine @ {OLD_CE._ref})")


# ═══ In-memory FakeClient (PostgREST-shaped: an absent table RAISES) ════════════════════════════
class FakeResult:
    def __init__(self, data):
        self.data = data


class FakeQuery:
    def __init__(self, store, table, writes):
        self.store, self.t, self.f, self.w = store, table, [], writes
        self.rng, self.ordk, self.orddesc, self.cols = None, None, False, None

    def select(self, *a, **k):
        self.cols = a[0] if a else None
        return self

    def eq(self, c, v):
        self.f.append(("eq", c, v)); return self

    def neq(self, c, v):
        self.f.append(("neq", c, v)); return self

    def in_(self, c, v):
        self.f.append(("in", c, list(v))); return self

    def is_(self, c, v):
        self.f.append(("is", c, v)); return self

    def order(self, col, desc=False, **k):
        self.ordk, self.orddesc = col, bool(desc); return self

    def limit(self, n):
        return self

    def range(self, a, b):
        self.rng = (a, b); return self

    def insert(self, *a, **k):
        self.w.append(("insert", self.t)); return self

    def update(self, *a, **k):
        self.w.append(("update", self.t)); return self

    def upsert(self, *a, **k):
        self.w.append(("upsert", self.t)); return self

    def delete(self, *a, **k):
        self.w.append(("delete", self.t)); return self

    def _m(self, r):
        for k, c, v in self.f:
            rv = r.get(c)
            if k == "eq" and rv != v:
                return False
            if k == "neq" and rv == v:
                return False
            if k == "in" and rv not in v:
                return False
            if k == "is" and v == "null" and rv is not None:
                return False
        return True

    def execute(self):
        if self.t not in self.store:
            raise Exception(f'relation "commcalc.{self.t}" does not exist')
        rows = [dict(r) for r in self.store.get(self.t, []) if self._m(r)]
        if self.cols and self.cols != "*" and rows:
            for c in [x.strip() for x in str(self.cols).split(",")]:
                if c and c not in rows[0] and not c.startswith("count"):
                    raise Exception(f"column {self.t}.{c} does not exist")
        if self.ordk:
            rows.sort(key=lambda r: (r.get(self.ordk) is None, str(r.get(self.ordk))),
                      reverse=self.orddesc)
        if self.rng:
            a, b = self.rng
            rows = rows[a:b + 1]
        return FakeResult(rows)


class FakeClient:
    def __init__(self, store):
        self.store = store
        self.writes = []

    def schema(self, s):
        return FakeClient._Sch(self.store, self.writes)

    class _Sch:
        def __init__(self, store, writes):
            self.store, self.writes = store, writes

        def table(self, t):
            return FakeQuery(self.store, t, self.writes)


# ═══ fixtures ═══════════════════════════════════════════════════════════════════════════════════
def base_store(**extra):
    s = {"commission_plan": [], "commission_rule": [], "commission_tier": [],
         "commission_plan_assignment": [], "plan_installment_schedule": [], "plan_installment_line": [],
         "raw_sales": [], "daily_sales_feed": [], "raw_mi": [], "raw_ma_commission": [],
         "store_mapping": [], "employees": [], "product_mrc": [], "carrier_category_map": [],
         "commission_org_config": [], "item_mapping": [], "raw_catalog": [], "carrier": [],
         "installment_gate_source_config": [], "accessory_config": [], "contract_type_map": [],
         "activation_rules": [], "stores": []}
    s.update(extra)
    return s


def sale(org, rep, tid, period=PERIOD, ct="", dept="", cat="", prod="Moto G 2025",
         ext=199.0, gp=40.0, serial="", mdn="", store="4640-A W Diversey Ave", date="2026-07-12",
         tender="", sku="", trans_type=""):
    return {"org_id": org, "period": period, "trans_id": tid, "trans_date": date, "store": store,
            "salesperson": rep, "department": dept, "category": cat, "contract_type": ct,
            "product_desc": prod, "ext_price": ext, "gp": gp, "voided": "", "trans_type": trans_type,
            "mdn": mdn, "serial_1": serial, "customer_plan": prod, "sku": sku, "tender_type": tender,
            "product_id": None}


def plan(org, pid, name, **kw):
    p = {"id": pid, "org_id": org, "name": name, "carrier_id": None, "base_tier_metric": None,
         "is_active": True}
    p.update(kw)
    return p


def rule(org, pid, rid, **kw):
    r = {"id": rid, "org_id": org, "plan_id": pid, "label": None, "match_field": "any",
         "match_op": "equals", "match_value": None, "qualifies": True,
         "payout_kind": "flat_per_unit", "amount": 0, "pct": 0, "tiered": False, "sort": 0}
    r.update(kw)
    return r


def assign(org, pid, scope="default", value=None, priority=0):
    return {"id": f"a-{pid}-{scope}-{value}", "org_id": org, "plan_id": pid, "scope": scope,
            "scope_value": value, "priority": priority}


def tier(org, pid, **kw):
    t = {"id": kw.pop("id", f"t-{pid}-{kw.get('min_count', 0)}-{kw.get('min_attainment_pct', 0)}"),
         "org_id": org, "plan_id": pid, "metric": None, "min_count": 0, "multiplier": 1, "sort": 0,
         "rule_id": None, "unit_rate": None, "min_attainment_pct": None, "label": None}
    t.update(kw)
    return t


# One financed sale as the POS really rings it: the tender is stamped on EVERY line, the IMEI only on
# the handset line (this is the mechanism the 2026-08-01 unit-dedup package proved and fixed).
def financed_txn(org, rep, tid, store, date, device_price=599.99, imei="356938035643809"):
    return [
        sale(org, rep, tid, prod="IPHONE 16E BLK 128GB", ext=device_price, gp=60.0, serial=imei,
             mdn="7185551212", store=store, date=date, tender=TW_FIN, ct="Port with IDV"),
        sale(org, rep, tid, prod="Unlimited Premium $60", ext=0.0, gp=0.0, store=store, date=date,
             tender=TW_FIN),
        sale(org, rep, tid, prod="Case BYOD", ext=29.99, gp=20.0, store=store, date=date,
             tender=TW_FIN, dept="Ondigo"),
        sale(org, rep, tid, prod="Screen Protector", ext=19.99, gp=15.0, store=store, date=date,
             tender=TW_FIN, dept="Ondigo"),
    ]


def flat_fixture(org=LUX, tiers=None, amount=25.0, extra_sales=None, **rule_kw):
    """One plan, one tender-keyed flat_per_unit rule (the luxelink 'edge' shape), N financed sales."""
    rows = []
    rows += financed_txn(org, "CAROLINA", "3207", "957 Pennsylvania Ave", "2026-07-03")
    rows += financed_txn(org, "CAROLINA", "3311", "957 Pennsylvania Ave", "2026-07-09")
    rows += financed_txn(org, "MARTINEZ", "4748", "957 Pennsylvania Ave", "2026-07-14")
    rows += [sale(org, "CAROLINA", "9001", prod="Moto G Power", ext=129.99, gp=30.0,
                  store="957 Pennsylvania Ave", date="2026-07-05", tender="Cash",
                  serial="356938035640001")]
    if extra_sales:
        rows += extra_sales
    kw = {"payout_kind": "flat_per_unit", "amount": amount}
    kw.update(rule_kw)
    r = rule(org, "p1", "r-fin", label="edge", match_field="tender_type", match_op="equals",
             match_value=TW_FIN, **kw)
    return base_store(
        commission_plan=[plan(org, "p1", "Luxelink")],
        commission_rule=[r],
        commission_tier=list(tiers or []),
        commission_plan_assignment=[assign(org, "p1")],
        raw_sales=rows,
        store_mapping=[{"org_id": org, "store_code": "PENN", "store_address": "957 Pennsylvania Ave",
                        "market": "NY"}],
        stores=[{"org_id": org, "store_code": "PENN", "address": "957 Pennsylvania Ave", "market": "NY"}],
    )


def run(engine, store, org=LUX, **kw):
    return engine.preview(FakeClient(store), org, PERIOD, **kw)


def norm(d):
    """JSON-comparable, with the keys the NEW engine may add stripped ONLY when they are absent from the
    old result — so an unexpected new key still fails."""
    return json.loads(json.dumps(d, sort_keys=True, default=str))


# ════════════════════════════════════════════════════════════════════════════════════════════════
print("\nA. NEGATIVE CONTROL — no financing tiers configured => BYTE-IDENTICAL to the base engine")
# ════════════════════════════════════════════════════════════════════════════════════════════════
matrix = {
    "A1 flat rule only": flat_fixture(),
    "A2 flat rule + a plain plan-wide MULTIPLIER tier (the pre-existing tier feature)":
        flat_fixture(tiers=[tier(LUX, "p1", min_count=2, multiplier=1.5)]),
    "A3 flat rule marked tiered + multiplier tier + plan metric":
        flat_fixture(tiers=[tier(LUX, "p1", min_count=2, multiplier=1.5)], tiered=True),
    "A4 pct_gp rule (no per-unit payout at all)":
        flat_fixture(payout_kind="pct_gp", pct=0.175),
    "A5 house org, different tender label": flat_fixture(org=HOUSE),
    "A6 no sales at all": base_store(
        commission_plan=[plan(LUX, "p1", "Luxelink")],
        commission_rule=[rule(LUX, "p1", "r-fin", match_field="tender_type", match_op="equals",
                              match_value=TW_FIN, amount=25.0)],
        commission_plan_assignment=[assign(LUX, "p1")]),
}
for name, st in matrix.items():
    for detail in (False, True):
        for coverage in (False, True):
            org = HOUSE if "house" in name else LUX
            a = norm(run(OLD_CE, copy.deepcopy(st), org, detail=detail, coverage=coverage))
            b = norm(run(CE, copy.deepcopy(st), org, detail=detail, coverage=coverage))
            check(f"{name} · detail={detail} coverage={coverage} identical", a == b,
                  extra=f"old_total={a.get('totals')} new_total={b.get('totals')}")

# A7 — a tier row that is NOT a financing rate tier (no unit_rate) must stay a multiplier tier
st = flat_fixture(tiers=[tier(LUX, "p1", min_count=2, multiplier=1.5, rule_id="r-fin")])
check("A7 rule_id set but NO unit_rate => still inert (it is not a rate tier)",
      norm(run(OLD_CE, copy.deepcopy(st))) == norm(run(CE, copy.deepcopy(st))))

# A8 — migration 273 unapplied (commission_tier table missing entirely)
st = flat_fixture()
del st["commission_tier"]
check("A8 commission_tier table absent (mig 059/273 unapplied) => identical, no crash",
      norm(run(OLD_CE, copy.deepcopy(st))) == norm(run(CE, copy.deepcopy(st))))

# A9 — randomised fuzz over rep/store/tender/price shapes
random.seed(20260804)
fuzz_ok = True
for i in range(120):
    org = random.choice([HOUSE, LUX, OTHER])
    rows = []
    for j in range(random.randint(0, 6)):
        rows += financed_txn(org, random.choice(["A REP", "B REP", "C REP"]), f"t{i}{j}",
                             random.choice(["957 Pennsylvania Ave", "4640-A W Diversey Ave"]),
                             f"2026-07-{random.randint(1, 28):02d}",
                             device_price=round(random.uniform(0, 1200), 2),
                             imei=str(random.randint(10 ** 13, 10 ** 14 - 1)))
    st = flat_fixture(org=org, amount=round(random.uniform(0, 60), 2), extra_sales=rows,
                      tiered=random.choice([True, False]))
    if random.random() < 0.4:
        st["commission_tier"] = [tier(org, "p1", min_count=random.randint(1, 5),
                                      multiplier=round(random.uniform(0.5, 2), 2))]
    a = norm(run(OLD_CE, copy.deepcopy(st), org, detail=(i % 2 == 0)))
    b = norm(run(CE, copy.deepcopy(st), org, detail=(i % 2 == 0)))
    if a != b:
        fuzz_ok = False
        print(f"    fuzz mismatch at i={i}")
        break
check("A9 120-case fuzz — every payout identical", fuzz_ok)

# ════════════════════════════════════════════════════════════════════════════════════════════════
print("\nB. TIERED ARITHMETIC — what an owner gets once they configure levels")
# ════════════════════════════════════════════════════════════════════════════════════════════════
# The fixture: 3 financed transactions at ONE store (PENN), so the store's financing units = 3.
# CAROLINA financed 2 of them, MARTINEZ 1. Nothing about these numbers is seeded in the product.
COUNT_TIERS = [
    tier(LUX, "p1", id="c1", rule_id="r-fin", min_count=2, unit_rate=30.0, label="2+ units"),
    tier(LUX, "p1", id="c2", rule_id="r-fin", min_count=4, unit_rate=40.0, label="4+ units"),
]
ATT_TIERS = [
    tier(LUX, "p1", id="a1", rule_id="r-fin", min_attainment_pct=50, unit_rate=20.0, label="50%"),
    tier(LUX, "p1", id="a2", rule_id="r-fin", min_attainment_pct=100, unit_rate=35.0, label="100%"),
]


def fin_target(org, code, units, vendor=None, period=PERIOD):
    return {"org_id": org, "period": period, "store_code": code, "vendor_key": vendor,
            "target_units": units, "target_amount": None}


def with_targets(st, targets):
    st["financing_target"] = targets
    return st


# B1 — count tiers, store scope: store has 3 units => tier "2+" => $30/unit for EVERYONE at the store
st = flat_fixture(tiers=COUNT_TIERS)
res = run(CE, copy.deepcopy(st), detail=True)
by_rep = {r["rep"]: r for r in res["by_rep"]}
check("B1 CAROLINA 2 units x $30 = $60 (was 2 x $25 = $50)",
      by_rep["CAROLINA"]["total_payout"] == 60.0, by_rep["CAROLINA"]["total_payout"])
check("B1 MARTINEZ 1 unit x $30 = $30 — the STORE reached the tier, so every rep earns its rate",
      by_rep["MARTINEZ"]["total_payout"] == 30.0, by_rep["MARTINEZ"]["total_payout"])
check("B1 the plain-cash sale still pays nothing",
      all(r["total_payout"] in (60.0, 30.0) for r in res["by_rep"]))

# B2 — rep scope: each rep's OWN count decides. CAROLINA 2 => $30; MARTINEZ 1 => below lowest => flat $25
st = flat_fixture(tiers=COUNT_TIERS, unit_tier_scope="rep")
res = run(CE, copy.deepcopy(st))
by_rep = {r["rep"]: r for r in res["by_rep"]}
check("B2 rep scope — CAROLINA (2 units) reaches 2+ => $60", by_rep["CAROLINA"]["total_payout"] == 60.0,
      by_rep["CAROLINA"]["total_payout"])
check("B2 rep scope — MARTINEZ (1 unit) reaches no tier => keeps the flat $25",
      by_rep["MARTINEZ"]["total_payout"] == 25.0, by_rep["MARTINEZ"]["total_payout"])

# B3 — ATTAINMENT tiers with a store target of 3: 3/3 = 100% => $35/unit (OWNER: monthly attainment,
#      whole-month rate)
st = with_targets(flat_fixture(tiers=ATT_TIERS), [fin_target(LUX, "PENN", 3)])
res = run(CE, copy.deepcopy(st))
by_rep = {r["rep"]: r for r in res["by_rep"]}
check("B3 100% attainment => $35/unit — CAROLINA 2 x 35 = $70", by_rep["CAROLINA"]["total_payout"] == 70.0,
      by_rep["CAROLINA"]["total_payout"])
check("B3 100% attainment => MARTINEZ 1 x 35 = $35", by_rep["MARTINEZ"]["total_payout"] == 35.0)

# B4 — target 6 => 3/6 = 50% => the $20 tier, which is BELOW the flat $25. Tiers may pay less; that is
#      the owner's ladder, and it must be applied faithfully rather than floored at the flat amount.
st = with_targets(flat_fixture(tiers=ATT_TIERS), [fin_target(LUX, "PENN", 6)])
res = run(CE, copy.deepcopy(st))
by_rep = {r["rep"]: r for r in res["by_rep"]}
check("B4 50% attainment => $20/unit (a tier may pay less than the flat rate)",
      by_rep["CAROLINA"]["total_payout"] == 40.0, by_rep["CAROLINA"]["total_payout"])

# B5 — NO TARGET SET: attainment is unknowable, so NOTHING changes and it is REPORTED
st = flat_fixture(tiers=ATT_TIERS)          # no financing_target rows at all
res = run(CE, copy.deepcopy(st))
by_rep = {r["rep"]: r for r in res["by_rep"]}
check("B5 no target => flat $25 kept (never treated as 0% attainment)",
      by_rep["CAROLINA"]["total_payout"] == 50.0 and by_rep["MARTINEZ"]["total_payout"] == 25.0,
      f'{by_rep["CAROLINA"]["total_payout"]}/{by_rep["MARTINEZ"]["total_payout"]}')
check("B5 and it is reported, not silent",
      any(n.get("reason") == "no_target" for n in
          (res.get("financing_tiers") or {}).get("not_applied", [])))

# B6 — a target of 0 is the same as no target (never a divide-by-zero, never 'infinite attainment')
st = with_targets(flat_fixture(tiers=ATT_TIERS), [fin_target(LUX, "PENN", 0)])
res = run(CE, copy.deepcopy(st))
check("B6 target 0 => treated as no target, flat kept",
      {r["rep"]: r["total_payout"] for r in res["by_rep"]} == {"CAROLINA": 50.0, "MARTINEZ": 25.0})

# B7 — WHOLE-MONTH vs MARGINAL (owner chose whole-month; marginal stays available as config)
rates_whole, tier_w, why_w = FT.per_unit_rates(
    [FT.normalize_tier(t) for t in COUNT_TIERS], units=5, attainment_pct=None, mode="whole_month")
rates_marg, tier_m, why_m = FT.per_unit_rates(
    [FT.normalize_tier(t) for t in COUNT_TIERS], units=5, attainment_pct=None, mode="marginal")
check("B7 whole-month (OWNER DEFAULT): 5 units all at the achieved $40 = $200",
      rates_whole == [40.0] * 5 and sum(rates_whole) == 200.0, rates_whole)
check("B7 marginal: units 1 pays nothing-stated…, 2-3 at $30, 4-5 at $40",
      rates_marg == [] or rates_marg == [30.0, 30.0, 30.0, 40.0, 40.0], rates_marg)
check("B7 the DEFAULT mode is whole_month", FT.DEFAULT_TIER_MODE == "whole_month")
check("B7 the DEFAULT scope is store", FT.DEFAULT_TIER_SCOPE == "store")

# B8 — a rate tier on a NON per-unit rule is refused (and reported), never applied to a % rule
st = flat_fixture(tiers=COUNT_TIERS, payout_kind="pct_gp", pct=0.175)
res_new = run(CE, copy.deepcopy(st))
res_old = run(OLD_CE, copy.deepcopy(st))
check("B8 rate tiers on a pct_gp rule change nothing",
      {r["rep"]: r["total_payout"] for r in res_new["by_rep"]} ==
      {r["rep"]: r["total_payout"] for r in res_old["by_rep"]})
check("B8 …and the refusal is reported",
      any(n.get("code") == "tier_rule_not_per_unit"
          for n in (res_new.get("financing_tiers") or {}).get("notes", [])))

# B9 — TENANT ISOLATION: luxelink's tiers cannot reach the house org's identical plan
st = flat_fixture(org=HOUSE)
st["commission_tier"] = [dict(t, org_id=LUX) for t in COUNT_TIERS]
check("B9 another tenant's rate tiers never price this tenant's units",
      norm(run(OLD_CE, copy.deepcopy(st), HOUSE)) == norm(run(CE, copy.deepcopy(st), HOUSE)))

# B10 — the unit count is the DEDUPED device count, not the line count (4 lines per financed sale)
st = flat_fixture(tiers=COUNT_TIERS)
res = run(CE, copy.deepcopy(st), detail=True)
fin = (res.get("financing_tiers") or {}).get("applied") or []
check("B10 store measured units = 3 devices, not 12 lines",
      all(a["measured_units"] == 3 for a in fin), [a["measured_units"] for a in fin])
check("B10 attainment/tier are reported per rep with the store's number",
      all(a["store_code"] == "PENN" for a in fin))

# B11 — per-line drill-down amounts are rewritten too (a $60 total must not show 2 x $25 lines)
st = flat_fixture(tiers=COUNT_TIERS)
res = run(CE, copy.deepcopy(st), detail=True)
carol = [r for r in res["by_rep"] if r["rep"] == "CAROLINA"][0]
lines = [ln for rb in carol["rules"] for ln in (rb.get("lines") or []) if ln.get("amount")]
check("B11 every paying line shows the tier rate", [ln["amount"] for ln in lines] == [30.0, 30.0],
      [ln["amount"] for ln in lines])
check("B11 …and keeps what it would have paid before", all(
    ln.get("amount_before_tier") == 25.0 for ln in lines))

# B12 — VENDOR-SPECIFIC target wins over the store total when the tenant sets one
st = with_targets(flat_fixture(tiers=ATT_TIERS, financing_vendor_key="edge"),
                  [fin_target(LUX, "PENN", 6), fin_target(LUX, "PENN", 3, vendor="edge")])
res = run(CE, copy.deepcopy(st))
check("B12 the vendor's own target (3) is used, not the store total (6) => 100% => $35/unit",
      {r["rep"]: r["total_payout"] for r in res["by_rep"]} == {"CAROLINA": 70.0, "MARTINEZ": 35.0},
      {r["rep"]: r["total_payout"] for r in res["by_rep"]})

# ════════════════════════════════════════════════════════════════════════════════════════════════
print("\nC. DETECTION REGISTRY — it must not be able to silently mis-classify")
# ════════════════════════════════════════════════════════════════════════════════════════════════
edge_tender = FR.normalize_matcher({"match_field": "tender_type", "match_op": "word",
                                    "match_value": "edge"})
edge_product = FR.normalize_matcher({"match_field": "product_desc", "match_op": "word",
                                     "match_value": "edge"})
check("C1 a tender matcher carries NO field warning", edge_tender["field_warning"] is None)
check("C2 a product_desc matcher DOES — word-anchoring does not save it "
      "('edge' is a real token in 'MOTOROLA EDGE 50')",
      edge_product["field_warning"] and "MODEL NAME" in edge_product["field_warning"])
check("C3 word anchoring still blocks the substring case ('edge' is not in 'wedge')",
      FR.matcher_hits({"product_desc": "Wedge Stand"}, edge_product) is False)
check("C4 …and the real model name DOES trip it — which is why the warning exists",
      FR.matcher_hits({"product_desc": "MOTOROLA EDGE 50 PRO"}, edge_product) is True)
check("C5 the tender matcher ignores the model name entirely",
      FR.matcher_hits({"tender_type": "Cash", "product_desc": "MOTOROLA EDGE 50 PRO"},
                      edge_tender) is False)
check("C6 an unknown field is REJECTED, not coerced",
      FR.normalize_matcher({"match_field": "sku", "match_op": "word", "match_value": "x"}) is None)
check("C7 an unknown operator is REJECTED",
      FR.normalize_matcher({"match_field": "tender_type", "match_op": "regex",
                            "match_value": "x"}) is None)
check("C8 a blank value is REJECTED (a blank 'contains' would match every line)",
      FR.normalize_matcher({"match_field": "tender_type", "match_op": "contains",
                            "match_value": "  "}) is None)

# C9 — a vendor with no matchers claims NOTHING (the "detection not configured" contract)
unconfigured = [{"vendor_key": "acima", "label": "ACIMA", "enabled": True, "matchers": [],
                 "sort_order": 10}]
check("C9 an unconfigured vendor classifies no line",
      FR.classify_line({"tender_type": "Financing"}, unconfigured) == (None, None))

# C10 — first hit wins in sort_order, so precedence is chosen, not discovered
vendors = [
    {"vendor_key": "a", "label": "A", "enabled": True, "sort_order": 10,
     "matchers": [FR.normalize_matcher({"match_field": "tender_type", "match_op": "contains",
                                        "match_value": "financ"})]},
    {"vendor_key": "b", "label": "B", "enabled": True, "sort_order": 20,
     "matchers": [FR.normalize_matcher({"match_field": "tender_type", "match_op": "word",
                                        "match_value": "Financing"})]},
]
check("C10 precedence follows sort_order",
      FR.classify_line({"tender_type": "Financing"}, vendors)[0] == "a")
check("C11 a disabled vendor never claims a line",
      FR.classify_line({"tender_type": "Financing"},
                       [dict(vendors[0], enabled=False)]) == (None, None))

# C12 — the seeds ship with NO invented pattern and NO dollar value
seed_json = json.dumps(FR.VENDOR_SEEDS)
check("C12 the seeded vendors carry no explicit detection pattern",
      all(not (s.get("detection_ref") or {}).get("rule_ids") for s in FR.VENDOR_SEEDS))
check("C13 no seed contains a rate, amount or threshold",
      not any(k in seed_json for k in ('"amount"', '"unit_rate"', '"rate"', '"min_count"')))

# ════════════════════════════════════════════════════════════════════════════════════════════════
print("\nD. REPORT — units are devices, amounts are labelled, targets are honest")
# ════════════════════════════════════════════════════════════════════════════════════════════════
import app.modules.commcalc.plan_pay_gate as GATE                      # noqa: E402

rows = []
rows += financed_txn(LUX, "CAROLINA", "3207", "957 Pennsylvania Ave", "2026-07-03")
rows += financed_txn(LUX, "CAROLINA", "3311", "957 Pennsylvania Ave", "2026-07-09")
rows += financed_txn(LUX, "MARTINEZ", "4748", "957 Pennsylvania Ave", "2026-07-14")
rows += [sale(LUX, "CAROLINA", "9001", prod="Moto G Power", ext=129.99, tender="Cash",
              store="957 Pennsylvania Ave")]
rows += [dict(financed_txn(LUX, "CAROLINA", "9999", "957 Pennsylvania Ave", "2026-07-20")[0],
              voided="true")]
vendors = [{"vendor_key": "edge", "label": "Edge financing", "enabled": True,
            "detection_source": "rules", "amount_basis": "unit_line", "sort_order": 10,
            "detection_status": "configured", "detection_note": "", "carriers": [],
            "matchers": [FR.normalize_matcher({"match_field": "tender_type", "match_op": "equals",
                                               "match_value": TW_FIN})]}]
idx = FT.build_store_index([{"store_code": "PENN", "store_address": "957 Pennsylvania Ave"}], [])
out = FREP.build(rows, vendors, {("PENN", ""): {"units": 4, "amount": None}}, idx,
                 lambda s: "NY", PERIOD, gate=GATE, unit_cfg=dict(GATE.UNIT_DEFAULTS),
                 month_days=31, days_elapsed=20)
check("D1 3 financed transactions => 3 UNITS (not 12 lines)", out["totals"]["units"] == 3,
      out["totals"])
check("D2 the voided financed sale is excluded", out["totals"]["transactions"] == 3)
check("D3 financed amount = the device line's Ext Price (3 x 599.99)",
      out["totals"]["amount"] == round(3 * 599.99, 2), out["totals"]["amount"])
check("D4 attainment 3/4 = 75%", out["by_store"][0]["attainment_pct"] == 75.0,
      out["by_store"][0]["attainment_pct"])
check("D5 MTD pace projects 3 x 31/20 = 4.7 units", out["by_store"][0]["projected_units"] == 4.7,
      out["by_store"][0]["projected_units"])
check("D6 vendor x store x rep rows: CAROLINA 2, MARTINEZ 1",
      sorted((r["rep"], r["units"]) for r in out["rows"]) == [("CAROLINA", 2), ("MARTINEZ", 1)],
      [(r["rep"], r["units"]) for r in out["rows"]])
check("D7 the tender values present are listed for pick-don't-type mapping",
      {f["value"] for f in out["tender_values"]} == {TW_FIN, "Cash"},
      out["tender_values"])
check("D8 the amount basis is stated on the payload, not implied",
      "Ext Price of the financed device line" in out["amount_note"])

# D9 — a store with a target but no financing sales is REPORTED at 0 units (not omitted)
out2 = FREP.build([], vendors, {("PENN", ""): {"units": 4, "amount": None}}, idx, lambda s: "NY",
                  PERIOD, gate=GATE, unit_cfg=dict(GATE.UNIT_DEFAULTS))
check("D9 a targeted store with no financing still appears, at 0",
      len(out2["by_store"]) == 1 and out2["by_store"][0]["units"] == 0)
check("D10 a store with NO target reports attainment None, never 0%",
      FREP.build(rows, vendors, {}, idx, lambda s: "NY", PERIOD, gate=GATE,
                 unit_cfg=dict(GATE.UNIT_DEFAULTS))["by_store"][0]["attainment_pct"] is None)
# D11 — an unconfigured vendor produces zeros AND says why
out3 = FREP.build(rows, [dict(vendors[0], matchers=[], detection_status="not_configured",
                              detection_note="Detection not configured")],
                  {}, idx, lambda s: "NY", PERIOD, gate=GATE, unit_cfg=dict(GATE.UNIT_DEFAULTS))
check("D11 an unconfigured vendor reports 0 with its reason attached",
      out3["totals"]["units"] == 0 and
      out3["vendors"][0]["detection_status"] == "not_configured")

# ════════════════════════════════════════════════════════════════════════════════════════════════
print("\nE. STORE + TARGET RESOLUTION")
# ════════════════════════════════════════════════════════════════════════════════════════════════
idx2 = FT.build_store_index([{"store_code": "PENN", "store_address": "957 Pennsylvania Ave"}],
                            [{"store_code": "DIV", "address": "4640-A W Diversey Ave"}])
check("E1 the POS address resolves to the store code",
      FT.resolve_store_code("957 Pennsylvania Ave", idx2) == "PENN")
check("E2 the leading store number resolves too", FT.resolve_store_code("957", idx2) == "PENN")
check("E3 the storeops roster is a fallback source",
      FT.resolve_store_code("4640-A W Diversey Ave", idx2) == "DIV")
check("E4 an unknown store keeps its own name (never silently merged into another store)",
      FT.resolve_store_code("123 Nowhere St", idx2) == "123 nowhere st")
ctx = {"targets": {("PENN", ""): {"units": 5}, ("PENN", "edge"): {"units": 2}}}
check("E5 vendor target beats store total", FT.target_for(ctx, "PENN", "edge") == (2.0, "vendor"))
check("E6 no vendor row => store total", FT.target_for(ctx, "PENN", "acima") == (5.0, "store"))
check("E7 no rows at all => (None, 'none') — never 0", FT.target_for(ctx, "OTHER", None) == (None, "none"))

# ════════════════════════════════════════════════════════════════════════════════════════════════
print("\nF. NOTHING IS HARD-CODED (AST + source scan)")
# ════════════════════════════════════════════════════════════════════════════════════════════════
import ast                                                             # noqa: E402

VENDOR_WORDS = ("acima", "edge", "boost", "total wireless", "luxelink")


def _docstrings(tree):
    """The doc-comment string nodes — PROSE is allowed to name a vendor (and must, to be honest about
    where a rule came from); EXECUTABLE strings are not."""
    out = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            body = getattr(node, "body", None) or []
            if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant) \
                    and isinstance(body[0].value.value, str):
                out.add(id(body[0].value))
    return out


for mod, path in (("financing_tiers", FT.__file__), ("financing_report", FREP.__file__)):
    src = open(path).read()
    tree = ast.parse(src)
    docs = _docstrings(tree)
    # every string constant that is NOT a docstring, minus the ONE data table that is allowed to name
    # the owner's two starting vendors (financing_registry.VENDOR_SEEDS).
    seed_ids = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign) and any(
                isinstance(t, ast.Name) and t.id == "VENDOR_SEEDS" for t in node.targets):
            seed_ids = {id(n) for n in ast.walk(node)}
    lits = [n.value.lower() for n in ast.walk(tree)
            if isinstance(n, ast.Constant) and isinstance(n.value, str)
            and id(n) not in docs and id(n) not in seed_ids]
    names = " ".join(lits)
    hits = [w for w in VENDOR_WORDS if w in names]
    check(f"F: {mod} — no vendor/carrier/tenant name in EXECUTABLE code (prose is allowed)",
          not hits, hits)
    nums = [n.value for n in ast.walk(tree)
            if isinstance(n, ast.Constant) and isinstance(n.value, (int, float))
            and not isinstance(n.value, bool)]
    check(f"F: {mod} contains no money-shaped constant",
          not any(float(x) in (25.0, 30.0, 35.0, 40.0, 20.0) for x in nums), nums)

# The REGISTRY is the one module allowed to name the owner's two starting vendors — it is the seed
# table. Assert the naming is confined to exactly two documented places and nowhere else:
#   1. VENDOR_SEEDS (the data table itself)
#   2. the 'acima_config' detection SOURCE + its documented fallback value, which mirror
#      calculator.py's existing ACIMA tender mapping rather than inventing a second one.
_tree = ast.parse(open(FR.__file__).read())
_docs = _docstrings(_tree)
_seed_ids = set()
for node in ast.walk(_tree):
    if isinstance(node, ast.Assign) and any(
            isinstance(t, ast.Name) and t.id == "VENDOR_SEEDS" for t in node.targets):
        _seed_ids = {id(n) for n in ast.walk(node)}
_ALLOWED = {"acima_config", "acima", "acima_tenders"}   # the source token, its documented fallback
                                                        # value, and the EXISTING config column it reads


def _is_identifier_literal(v):
    """A literal a machine acts on (a key, a column, a token) rather than a sentence a human reads."""
    return " " not in v.strip() and len(v.strip()) <= 24


_stray = sorted({n.value for n in ast.walk(_tree)
                 if isinstance(n, ast.Constant) and isinstance(n.value, str)
                 and id(n) not in _docs and id(n) not in _seed_ids
                 and _is_identifier_literal(n.value)
                 and any(w in n.value.lower() for w in VENDOR_WORDS)
                 and n.value not in _ALLOWED})
check("F: financing_registry uses a vendor name as an IDENTIFIER only in VENDOR_SEEDS + the "
      "documented acima_config inheritance (prose may name anything)", not _stray, _stray)
check("F: …and VENDOR_SEEDS is a plain data literal (no call, no branch inside it)",
      not any(isinstance(n, (ast.Call, ast.If, ast.Compare)) for n in ast.walk(_tree)
              if id(n) in _seed_ids))

# and no branch anywhere compares against a vendor name
for mod, path in (("financing_tiers", FT.__file__), ("financing_report", FREP.__file__),
                  ("financing_registry", FR.__file__)):
    tree = ast.parse(open(path).read())
    branchy = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Compare):
            for c in list(node.comparators) + [node.left]:
                if isinstance(c, ast.Constant) and isinstance(c.value, str) \
                        and c.value.strip().lower() in VENDOR_WORDS:
                    branchy.append(c.value)
    check(f"F: {mod} branches on NO vendor name (RULE TWO)", not branchy, branchy)

# ════════════════════════════════════════════════════════════════════════════════════════════════
print(f"\n{'='*70}\n{PASS} passed · {FAIL} failed")
if FAILED:
    for f in FAILED:
        print("   FAILED:", f)
sys.exit(1 if FAIL else 0)
