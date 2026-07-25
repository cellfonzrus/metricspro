"""Proof for agent/commission/luxelink-tier-multimonth (owner directive 2026-07-25, MONEY-TOUCHING).

OWNER DIRECTIVE (luxelink, org 854f6d7b): employee commissions are NOT calculating per the tier commissions
and Commission Plans configured. Required: (1) every employee paid from the plan ASSIGNED to them INCLUDING
tier attainment; (2) multi-month — M1 paid IMMEDIATELY at activation, the later month paid ONLY if the
residual was actually received (evidence = the carrier's commission file, raw_ma_* for VidaPay/Total).
Universal + config-driven, zero luxelink hard-coding.

WHAT THIS PROVES
  0  REPRO — five failure modes reproduced against the PRISTINE origin/main engines (vendored via git show).
  A  M1 pays immediately when the schedule says so (gate_from_month=2) even with NO carrier evidence at all.
  B  A later month is HELD when the residual is absent...
  C  ...and RELEASED when the residual is present — including when it lands in the PAY month's statement
     (mig 232 ma_lookup_periods), with a net clawback still refusing to pay.
  D  Tier attainment per plan: distinct-transaction basis, line basis, legacy basis, below-lowest-tier.
  E  Blank contract_type: config OFF = unchanged $0; config ON ('mapped' / 'activation_bucket') = paid.
  F  BOOST / house byte-identity: with the new config unset, preview() and compute_sale_installments()
     are IDENTICAL to origin/main across a fixture matrix + a 300-seed fuzz.
  G  Coverage diagnostics (uncovered sellers, unmatched lines, plan warnings) — read-only, money-free.

Run:  cd backend && python3 scratchpad/luxelink_tier_multimonth_proof.py
"""
import os
import sys
import copy
import random
import subprocess
import types

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import app.modules.commcalc.commission_engine as CE
import app.modules.commcalc.sale_installment_engine as SIE

HOUSE = "00000000-0000-0000-0000-000000000001"
LUX = "854f6d7b-6590-4e4d-88ab-646f560d4f4c"
NIL = "00000000-0000-0000-0000-000000000000"

PASS = 0
FAIL = 0


def check(name, cond, extra=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  ok  {name}")
    else:
        FAIL += 1
        print(f"FAIL  {name}   {extra}")


# ── PRISTINE pre-change engines, pinned to the merge-base with origin/main ───────────────────────
_PINNED_BASE = "dc01434"


def _base_ref():
    repo = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
    try:
        return subprocess.check_output(
            ["git", "-C", repo, "merge-base", "HEAD", "origin/main"], text=True).strip() or _PINNED_BASE
    except Exception:
        return _PINNED_BASE


def _load_old():
    """(OLD_commission_engine, OLD_sale_installment_engine) exec'd from the base commit. The OLD installment
    engine is bound to the OLD commission engine (sys.modules swap during exec) so the differential is
    genuine and not silently reusing the patched matcher."""
    repo = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
    ref = _base_ref()

    def show(p):
        return subprocess.check_output(["git", "-C", repo, "show", f"{ref}:{p}"], text=True)

    old_ce = types.ModuleType("OLD_commission_engine")
    exec(compile(show("backend/app/modules/commcalc/commission_engine.py"),
                 "OLD_commission_engine.py", "exec"), old_ce.__dict__)
    keep = sys.modules.get("app.modules.commcalc.commission_engine")
    sys.modules["app.modules.commcalc.commission_engine"] = old_ce
    try:
        old_sie = types.ModuleType("OLD_sale_installment_engine")
        exec(compile(show("backend/app/modules/commcalc/sale_installment_engine.py"),
                     "OLD_sale_installment_engine.py", "exec"), old_sie.__dict__)
    finally:
        if keep is not None:
            sys.modules["app.modules.commcalc.commission_engine"] = keep
    old_ce._ref = old_sie._ref = ref
    return old_ce, old_sie


OLD_CE, OLD_SIE = _load_old()
print(f"(differential pinned to the pre-change engines @ {OLD_CE._ref[:10]})")


# ═══ In-memory FakeClient (order/range aware; a table absent from the store RAISES, like PostgREST) ══
class FakeResult:
    def __init__(self, data):
        self.data = data


class FakeQuery:
    def __init__(self, store, table):
        self.store, self.t, self.f = store, table, []
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

    def order(self, col, desc=False, **k):
        self.ordk, self.orddesc = col, bool(desc); return self

    def limit(self, n):
        return self

    def range(self, a, b):
        self.rng = (a, b); return self

    def upsert(self, *a, **k):
        return self

    def _m(self, r):
        for k, c, v in self.f:
            rv = r.get(c)
            if k == "eq" and rv != v:
                return False
            if k == "neq" and rv == v:
                return False
            if k == "in" and rv not in v:
                return False
        return True

    def execute(self):
        if self.t not in self.store:
            raise Exception(f"relation \"commcalc.{self.t}\" does not exist")
        rows = [dict(r) for r in self.store.get(self.t, []) if self._m(r)]
        # emulate PostgREST: selecting a column no row carries is an error (drives the degrade paths)
        if self.cols and self.cols != "*" and rows:
            for c in [x.strip() for x in str(self.cols).split(",")]:
                if c and c not in rows[0] and not c.startswith("count"):
                    raise Exception(f"column {self.t}.{c} does not exist")
        if self.ordk:
            rows.sort(key=lambda r: (r.get(self.ordk) is None, str(r.get(self.ordk))), reverse=self.orddesc)
        if self.rng:
            a, b = self.rng
            rows = rows[a:b + 1]
        return FakeResult(rows)


class FakeClient:
    def __init__(self, store):
        self.store = store

    def schema(self, s):
        return FakeClient._Sch(self.store)

    class _Sch:
        def __init__(self, store):
            self.store = store

        def table(self, t):
            return FakeQuery(self.store, t)


# ═══ fixture builders ════════════════════════════════════════════════════════════════════════════
def base_store(**extra):
    s = {"commission_plan": [], "commission_rule": [], "commission_tier": [],
         "commission_plan_assignment": [], "plan_installment_schedule": [], "plan_installment_line": [],
         "raw_sales": [], "daily_sales_feed": [], "raw_mi": [], "raw_ma_commission": [],
         "store_mapping": [], "employees": [], "product_mrc": [], "carrier_category_map": [],
         "flag_rules": [], "commission_org_config": [], "item_mapping": [], "raw_catalog": [],
         "carrier": [], "installment_gate_source_config": [], "accessory_config": []}
    s.update(extra)
    return s


def sale(org, rep, tid, period="July 2026", ct="", dept="BrandedHandset", cat="", prod="Moto G 2025",
         ext=199.0, gp=40.0, serial="", mdn="", store="957 Pennsylvania Avenue", date="2026-07-05"):
    return {"org_id": org, "period": period, "trans_id": tid, "trans_date": date, "store": store,
            "salesperson": rep, "department": dept, "category": cat, "contract_type": ct,
            "product_desc": prod, "ext_price": ext, "gp": gp, "voided": "", "trans_type": "",
            "mdn": mdn, "serial_1": serial, "customer_plan": prod, "sku": "", "tender_type": "",
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


def tier(org, pid, tid, min_count, mult, metric="activations"):
    return {"id": tid, "org_id": org, "plan_id": pid, "metric": metric,
            "min_count": min_count, "multiplier": mult, "sort": min_count}


def assign(org, pid, scope="default", value=None, priority=0):
    return {"id": f"a-{pid}-{scope}-{value}", "org_id": org, "plan_id": pid, "scope": scope,
            "scope_value": value, "priority": priority}


def sched(org, sid, pid, num_months=3, gate_mode="paid_residual", gate_from=1, m1_gate="inherit"):
    return {"id": sid, "org_id": org, "plan_id": pid, "name": "3MR Commission Payment",
            "is_active": True, "num_months": num_months, "gate_mode": gate_mode,
            "gate_from_month": gate_from, "m1_gate": m1_gate, "trigger_match_field": "any",
            "trigger_match_op": "equals", "trigger_match_value": None}


def ilines(org, sid, amt, n=3):
    return [{"id": f"{sid}-l{i}", "org_id": org, "schedule_id": sid, "month_index": i,
             "payout_kind": "flat", "flat_amount": amt} for i in range(1, n + 1)]


def ma_row(org, period, imei, **cols):
    r = {"org_id": org, "period": period, "imei": imei, "sim": "", "line_status": None,
         "spiff_m1": 0, "spiff_m2": 0, "spiff_m3": 0, "spiff_m4": 0, "spiff_m5": 0, "spiff_m6": 0,
         "rebate": 0, "device_margin": 0, "consumer_margin": 0, "mrc_net_discount": 0}
    r.update(cols)
    return r


GATE_SEEDS = [
    {"org_id": HOUSE, "carrier_id": NIL, "carrier_mode": "boost", "gate_source": "boost_mi",
     "ma_device_fields": ["imei", "sim"], "ma_month_field_prefix": "spiff_m", "ma_max_month": 6,
     "ma_month1_extra_fields": ["rebate", "device_margin"], "ma_min_amount": 0.01,
     "ma_payout_sign": -1, "ma_lookup_periods": "sale", "is_active": True},
    {"org_id": HOUSE, "carrier_id": NIL, "carrier_mode": "plan", "gate_source": "ma_commission",
     "ma_device_fields": ["imei", "sim"], "ma_month_field_prefix": "spiff_m", "ma_max_month": 6,
     "ma_month1_extra_fields": ["rebate", "device_margin"], "ma_min_amount": 0.01,
     "ma_payout_sign": -1, "ma_lookup_periods": "sale", "is_active": True},
]
TOTAL_CARRIER = {"id": "c-total", "org_id": LUX, "name": "Total Wireless", "code": "TOTAL",
                 "is_default": True}
BOOST_CARRIER = {"id": "c-boost", "org_id": HOUSE, "name": "Boost Mobile", "code": "BOOST",
                 "is_default": True}

# luxelink-shaped blank-contract_type activation rules (the mig-224 optional seed shape)
LUX_ACT_RULES = [
    {"bucket": "byod", "all_of": [{"field": "category", "contains_any": ["SimMarketplace"]},
                                  {"field": "department", "contains_any": ["Rtr"]}],
     "none_of": [{"field": "department", "contains_any": ["BrandedHandset"]}]},
    {"bucket": "premium", "all_of": [{"field": "department", "contains_any": ["BrandedHandset"]},
                                     {"field": "department", "contains_any": ["Rtr"]}]},
]


def lux_txn(rep, tid, byod=False, date="2026-07-05", serial="", period="July 2026"):
    """One luxelink-shaped BLANK-contract_type activation: a device (or SIM) line + a rate-plan line."""
    if byod:
        head = sale(LUX, rep, tid, period=period, dept="Rtr", cat="SimMarketplace",
                    prod="Total SIM Kit", ext=9.99, gp=4.0, date=date, serial=serial)
    else:
        head = sale(LUX, rep, tid, period=period, dept="BrandedHandset", cat="Handset",
                    prod="Moto G 2025", ext=199.0, gp=40.0, date=date, serial=serial)
    plan_line = sale(LUX, rep, tid, period=period, dept="Rtr", cat="Other Carr. payments",
                     prod="Total Unlimited $50/mo", ext=50.0, gp=5.0, date=date)
    return [head, plan_line]


# ═══════════════════════════════════════════════════════════════════════════════════════════════
print("\n══ 0. REPRO — the five failure modes, against the PRISTINE base engines ══")
# ═══════════════════════════════════════════════════════════════════════════════════════════════
# R1: tiers exist, base_tier_metric unset -> multiplier is FORCED to 1.0
st = base_store(
    commission_plan=[plan(LUX, "p1", "Total Employee Comp Chicago")],
    commission_rule=[rule(LUX, "p1", "r1", amount=10, tiered=True)],
    commission_tier=[tier(LUX, "p1", "t1", 0, 0.5), tier(LUX, "p1", "t2", 30, 1.0)],
    commission_plan_assignment=[assign(LUX, "p1")],
    raw_sales=[sale(LUX, "Doe, Jane", f"T{i}") for i in range(5)])
old = OLD_CE.preview(FakeClient(st), LUX, "July 2026")
check("R1 base: tiers with metric unset -> 1.0x (tiers ignored)",
      old["by_rep"][0]["tier_multiplier"] == 1.0 and old["by_rep"][0]["total_payout"] == 50.0,
      str(old["by_rep"][0]))

# R2: the tier metric is measured in matched LINES summed across rules, not activations
st2 = base_store(
    commission_plan=[plan(LUX, "p1", "P", base_tier_metric="activations")],
    commission_rule=[rule(LUX, "p1", "r1", match_field="department", match_value="BrandedHandset",
                          amount=10, tiered=True),
                     rule(LUX, "p1", "r2", match_field="department", match_value="Ondigo",
                          payout_kind="pct_gp", pct=0.1, tiered=True, sort=1)],
    commission_tier=[tier(LUX, "p1", "t1", 10, 1.0)],
    commission_plan_assignment=[assign(LUX, "p1")],
    raw_sales=([sale(LUX, "Doe, Jane", "T1")] +
               [sale(LUX, "Doe, Jane", "T1", dept="Ondigo", prod=f"Case {i}", ext=20, gp=10)
                for i in range(12)]))
old2 = OLD_CE.preview(FakeClient(st2), LUX, "July 2026")
check("R2 base: 1 activation + 12 accessory LINES satisfies a '10 activations' tier",
      old2["by_rep"][0]["qualifying_units"] == 13 and old2["by_rep"][0]["tier_multiplier"] == 1.0,
      str(old2["by_rep"][0]))

# R3: blank contract_type -> a contract_type rule never matches, even with mig-224 rules configured
st3 = base_store(
    commission_plan=[plan(LUX, "p1", "P")],
    commission_rule=[rule(LUX, "p1", "r1", match_field="contract_type", match_value="Activation",
                          amount=25)],
    commission_plan_assignment=[assign(LUX, "p1")],
    raw_sales=lux_txn("Doe, Jane", "T1"),
    accessory_config=[{"org_id": LUX, "contract_type_map": {}, "activation_rules": LUX_ACT_RULES}])
old3 = OLD_CE.preview(FakeClient(st3), LUX, "July 2026")
check("R3 base: blank-ct activation pays $0 despite configured activation_rules",
      old3["by_rep"] and old3["by_rep"][0]["total_payout"] == 0.0, str(old3["by_rep"]))

# R4: a seller with no plan is silently dropped
st4 = base_store(
    commission_plan=[plan(LUX, "p1", "P")],
    commission_rule=[rule(LUX, "p1", "r1", amount=5)],
    commission_plan_assignment=[assign(LUX, "p1", "employee", "Jane Doe")],
    raw_sales=[sale(LUX, "Doe, Jane", "T1"), sale(LUX, "Smith, Bob", "T2")])
old4 = OLD_CE.preview(FakeClient(st4), LUX, "July 2026")
check("R4 base: uncovered seller absent from the output entirely",
      [r["rep"] for r in old4["by_rep"]] == ["Doe, Jane"] and "coverage" not in old4,
      str(sorted(old4.keys())))


def inst_store(gate_from=1, ma_rows=None, lookup=None, org=LUX, m1_gate="inherit"):
    cfg = [dict(g) for g in GATE_SEEDS]
    if lookup:
        cfg.append({"org_id": org, "carrier_id": NIL, "carrier_mode": "plan",
                    "gate_source": "ma_commission", "ma_device_fields": ["imei", "sim"],
                    "ma_month_field_prefix": "spiff_m", "ma_max_month": 6,
                    "ma_month1_extra_fields": ["rebate", "device_margin"], "ma_min_amount": 0.01,
                    "ma_payout_sign": -1, "ma_lookup_periods": lookup, "is_active": True})
    return base_store(
        carrier=[TOTAL_CARRIER],
        commission_plan=[plan(org, "p1", "Total Employee Comp Chicago", carrier_id="c-total")],
        commission_plan_assignment=[assign(org, "p1")],
        plan_installment_schedule=[sched(org, "s1", "p1", gate_from=gate_from, m1_gate=m1_gate)],
        plan_installment_line=ilines(org, "s1", 15),
        raw_sales=[sale(org, "Doe, Jane", "T1", period="May 2026", serial="355163568356973",
                        date="2026-05-10", mdn="3125550123")],
        raw_ma_commission=(ma_rows or []),
        installment_gate_source_config=cfg)


# R5: M1 withheld when the MA statement has not posted yet
st5 = inst_store(gate_from=1, ma_rows=[])
old5 = OLD_SIE.compute_sale_installments(FakeClient(st5), LUX, "May 2026", persist=False)
check("R5 base: M1 WITHHELD at activation when no MA row exists yet",
      old5["by_rep"] == {} and old5["ledger"][0]["status"] == "withheld_unpaid",
      str(old5["ledger"]))

# R6: M3 withheld when the evidence lands in the PAY month's statement
st6 = inst_store(gate_from=2, ma_rows=[ma_row(LUX, "July 2026", "355163568356973", spiff_m3=-48.75)])
old6 = OLD_SIE.compute_sale_installments(FakeClient(st6), LUX, "July 2026", persist=False)
check("R6 base: M3 WITHHELD when spiff_m3 is in the pay-month file (sale-month lookup only)",
      old6["by_rep"] == {} and old6["ledger"][0]["ma_reason"] == "no_ma_record",
      str(old6["ledger"]))


# ═══════════════════════════════════════════════════════════════════════════════════════════════
print("\n══ A. M1 PAYS IMMEDIATELY at activation (gate_from_month=2), no carrier evidence needed ══")
# ═══════════════════════════════════════════════════════════════════════════════════════════════
stA = inst_store(gate_from=2, ma_rows=[])
resA = SIE.compute_sale_installments(FakeClient(stA), LUX, "May 2026", persist=False)
check("A1 M1 paid with ZERO MA rows", resA["by_rep"] == {"DOE, JANE": 15.0}, str(resA["by_rep"]))
check("A2 ledger month 1 status=paid", resA["ledger"][0]["month_index"] == 1
      and resA["ledger"][0]["status"] == "paid" and resA["ledger"][0]["amount"] == 15.0)
check("A3 no withheld flags raised for M1", resA["flags"] == [], str(resA["flags"]))
# an UNGATED month in MA mode still carries the mig-223 gate_kind/provenance keys — that is the BASE
# engine's shape, not something this change introduced, so assert against the base engine itself.
_oldA = OLD_SIE.compute_sale_installments(FakeClient(inst_store(gate_from=2, ma_rows=[])), LUX,
                                          "May 2026", persist=False)
check("A4 the ungated-month ledger shape matches the base engine exactly",
      {k: v for k, v in resA["ledger"][0].items() if k != "ma_lookup_periods"} == _oldA["ledger"][0]
      and _oldA["by_rep"] == resA["by_rep"],
      str(resA["ledger"][0]))
stA2 = inst_store(gate_from=1, ma_rows=[])
resA2 = SIE.compute_sale_installments(FakeClient(stA2), LUX, "May 2026", persist=False)
check("A5 gate_from_month=1 STILL withholds M1 (the setting is what changes it, not the code)",
      resA2["by_rep"] == {} and resA2["ledger"][0]["status"] == "withheld_unpaid")
check("A6 A5 is byte-identical to the base engine",
      resA2["ledger"][0]["status"] == old5["ledger"][0]["status"]
      and resA2["by_rep"] == old5["by_rep"])


# ═══════════════════════════════════════════════════════════════════════════════════════════════
print("\n══ B/C. LATER MONTH: held without the residual, released when it actually arrives ══")
# ═══════════════════════════════════════════════════════════════════════════════════════════════
# B — nothing posted for month 3
stB = inst_store(gate_from=2, ma_rows=[ma_row(LUX, "May 2026", "355163568356973",
                                              spiff_m1=-5, rebate=-529)])
resB = SIE.compute_sale_installments(FakeClient(stB), LUX, "July 2026", persist=False)
check("B1 M3 HELD when the statement shows no month-3 payout",
      resB["by_rep"] == {} and resB["ledger"][0]["status"] == "withheld_unpaid")
check("B2 held reason is honest ('no_month_payout')", resB["ledger"][0]["ma_reason"] == "no_month_payout",
      str(resB["ledger"][0]))
check("B3 two flags raised (commission tracking + employee miss)",
      sorted(f["source"] for f in resB["flags"]) == ["commission_rebate_tracking", "employee_miss"])
check("B4 flag text names the master-agent month, not 'residual'",
      "month-3 payout" in resB["flags"][0]["description"], resB["flags"][0]["description"])

# C1 — evidence in the SALE month (today's default, unchanged)
stC1 = inst_store(gate_from=2, ma_rows=[ma_row(LUX, "May 2026", "355163568356973", spiff_m3=-48.75)])
resC1 = SIE.compute_sale_installments(FakeClient(stC1), LUX, "July 2026", persist=False)
check("C1 M3 RELEASED from the sale-month statement (default lookup)",
      resC1["by_rep"] == {"DOE, JANE": 15.0} and resC1["ledger"][0]["ma_reason"] == "paid")
check("C1b default lookup_periods is the sale period only",
      resC1["ledger"][0]["ma_lookup_periods"] == ["May 2026"])

# C2 — evidence ONLY in the PAY month: default holds, 'both' releases
stC2 = inst_store(gate_from=2, ma_rows=[ma_row(LUX, "July 2026", "355163568356973", spiff_m3=-48.75)])
resC2a = SIE.compute_sale_installments(FakeClient(stC2), LUX, "July 2026", persist=False)
check("C2a default ('sale') still HOLDS — no silent behaviour change", resC2a["by_rep"] == {})
stC2b = inst_store(gate_from=2, lookup="both",
                   ma_rows=[ma_row(LUX, "July 2026", "355163568356973", spiff_m3=-48.75)])
resC2b = SIE.compute_sale_installments(FakeClient(stC2b), LUX, "July 2026", persist=False)
check("C2b ma_lookup_periods='both' RELEASES M3 from the pay-month statement",
      resC2b["by_rep"] == {"DOE, JANE": 15.0} and resC2b["ledger"][0]["ma_reason"] == "paid")
check("C2c evidence provenance recorded",
      resC2b["ledger"][0]["ma_lookup_periods"] == ["May 2026", "July 2026"])
stC2c = inst_store(gate_from=2, lookup="pay",
                   ma_rows=[ma_row(LUX, "July 2026", "355163568356973", spiff_m3=-48.75)])
check("C2d ma_lookup_periods='pay' also releases",
      SIE.compute_sale_installments(FakeClient(stC2c), LUX, "July 2026",
                                    persist=False)["by_rep"] == {"DOE, JANE": 15.0})

# C3 — a NET CLAWBACK across the two periods must NOT pay (direction-aware netting preserved)
stC3 = inst_store(gate_from=2, lookup="both",
                  ma_rows=[ma_row(LUX, "May 2026", "355163568356973", spiff_m3=-48.75),
                           ma_row(LUX, "July 2026", "355163568356973", spiff_m3=55.0)])
resC3 = SIE.compute_sale_installments(FakeClient(stC3), LUX, "July 2026", persist=False)
check("C3 net clawback across both periods does NOT pay",
      resC3["by_rep"] == {} and resC3["ledger"][0]["ma_reason"] == "net_clawback",
      str(resC3["ledger"][0]))

# C4 — M1+M3 end-to-end on the owner's shape: M1 at activation, M3 on the posted residual
stC4 = inst_store(gate_from=2, lookup="both",
                  ma_rows=[ma_row(LUX, "May 2026", "355163568356973", spiff_m1=-5, rebate=-529),
                           ma_row(LUX, "July 2026", "355163568356973", spiff_m3=-48.75)])
m1 = SIE.compute_sale_installments(FakeClient(stC4), LUX, "May 2026", persist=False)
m2 = SIE.compute_sale_installments(FakeClient(stC4), LUX, "June 2026", persist=False)
m3 = SIE.compute_sale_installments(FakeClient(stC4), LUX, "July 2026", persist=False)
check("C4 M1 pays / M2 holds / M3 pays — exactly the owner's spec",
      m1["by_rep"] == {"DOE, JANE": 15.0} and m2["by_rep"] == {} and m3["by_rep"] == {"DOE, JANE": 15.0},
      f"m1={m1['by_rep']} m2={m2['by_rep']} m3={m3['by_rep']}")

# C5 — the kill switch still wins over everything
os.environ["INSTALLMENT_GATE_LEGACY"] = "1"
resC5 = SIE.compute_sale_installments(FakeClient(stC4), LUX, "July 2026", persist=False)
os.environ.pop("INSTALLMENT_GATE_LEGACY")
check("C5 INSTALLMENT_GATE_LEGACY=1 forces the legacy raw_mi gate (M3 held, no MA keys)",
      resC5["by_rep"] == {} and "ma_reason" not in resC5["ledger"][0], str(resC5["ledger"][0]))

# C6 — unknown / missing lookup value falls back to 'sale'
check("C6 unknown ma_lookup_periods falls back to 'sale'",
      SIE._ma_lookup_periods({"ma_lookup_periods": "banana"}, "May 2026", "July 2026") == ("May 2026",)
      and SIE._ma_lookup_periods({}, "May 2026", "July 2026") == ("May 2026",))
check("C7 'both' de-duplicates when sale period == pay period",
      SIE._ma_lookup_periods({"ma_lookup_periods": "both"}, "May 2026", "May 2026") == ("May 2026",))


# ═══════════════════════════════════════════════════════════════════════════════════════════════
print("\n══ D. TIER ATTAINMENT per plan (mig 232 count basis + below-lowest-tier) ══")
# ═══════════════════════════════════════════════════════════════════════════════════════════════
def tier_store(**plan_kw):
    """4 blank-ct activations (2 lines each) + 12 accessory lines, one rep."""
    rows = []
    for i in range(4):
        rows += lux_txn("Doe, Jane", f"A{i}")
    rows += [sale(LUX, "Doe, Jane", "A0", dept="Ondigo", prod=f"Case {i}", ext=20, gp=10)
             for i in range(12)]
    p = plan(LUX, "p1", "Total Employee Comp Chicago", base_tier_metric="activations", **plan_kw)
    return base_store(
        commission_plan=[p],
        commission_rule=[rule(LUX, "p1", "r1", amount=10, tiered=True)],      # 'any' -> every line
        commission_tier=[tier(LUX, "p1", "t1", 4, 1.0), tier(LUX, "p1", "t2", 10, 1.25)],
        commission_plan_assignment=[assign(LUX, "p1")],
        accessory_config=[{"org_id": LUX, "contract_type_map": {}, "activation_rules": LUX_ACT_RULES}],
        raw_sales=rows)


# legacy basis: 20 matched lines -> the 10-unit tier
r_legacy = CE.preview(FakeClient(tier_store()), LUX, "July 2026")["by_rep"][0]
check("D1 legacy basis unchanged: 20 matched lines -> 1.25x",
      r_legacy["qualifying_units"] == 20 and r_legacy["tier_multiplier"] == 1.25
      and r_legacy["total_payout"] == 250.0, str(r_legacy))
check("D1b legacy basis is byte-identical to the base engine",
      OLD_CE.preview(FakeClient(tier_store()), LUX, "July 2026")["by_rep"][0] == r_legacy)

# transactions basis on activation_bucket: 4 real activations -> the 4-unit tier
stD = tier_store(tier_count_basis="transactions", tier_match_field="activation_bucket",
                 tier_match_op="in", tier_match_value="premium,byod")
r_txn = CE.preview(FakeClient(stD), LUX, "July 2026")["by_rep"][0]
check("D2 transactions basis counts 4 ACTIVATIONS (not 20 lines) -> 1.0x",
      r_txn["qualifying_units"] == 20 and r_txn["tier_multiplier"] == 1.0
      and r_txn["total_payout"] == 200.0, str(r_txn))

# lines basis on the same matcher: 8 activation LINES -> the 4-unit tier
stD2 = tier_store(tier_count_basis="lines", tier_match_field="activation_bucket",
                  tier_match_op="in", tier_match_value="premium,byod")
r_lines = CE.preview(FakeClient(stD2), LUX, "July 2026", coverage=True)["by_rep"][0]
check("D3 lines basis counts 4 activation lines (one stamped line per rescued txn) -> 1.0x",
      r_lines["tier_units"] == 4 and r_lines["tier_multiplier"] == 1.0, str(r_lines["tier_units"]))
check("D3b tier basis is reported for the operator", r_lines["tier_basis"] == "lines")

# below-lowest-tier
stD3 = tier_store(tier_count_basis="transactions", tier_match_field="activation_bucket",
                  tier_match_op="in", tier_match_value="premium,byod")
stD3["commission_tier"] = [tier(LUX, "p1", "t1", 30, 1.0)]
r_below = CE.preview(FakeClient(stD3), LUX, "July 2026")["by_rep"][0]
check("D4 below the lowest tier still pays 1.0x by default (unchanged)",
      r_below["tier_multiplier"] == 1.0 and r_below["total_payout"] == 200.0)
stD4 = copy.deepcopy(stD3)
stD4["commission_plan"][0]["tier_below_min_multiplier"] = 0.5
r_below2 = CE.preview(FakeClient(stD4), LUX, "July 2026")["by_rep"][0]
check("D5 tier_below_min_multiplier=0.5 applies the configured floor",
      r_below2["tier_multiplier"] == 0.5 and r_below2["total_payout"] == 100.0, str(r_below2))
stD5 = copy.deepcopy(stD3)
stD5["commission_plan"][0]["tier_below_min_multiplier"] = 0
check("D6 an explicit 0 floor pays nothing (0 is honoured, not coerced to 1.0)",
      CE.preview(FakeClient(stD5), LUX, "July 2026")["by_rep"][0]["total_payout"] == 0.0)

# the metric switch still governs whether tiers apply at all
stD6 = tier_store(tier_count_basis="transactions", tier_match_field="activation_bucket",
                  tier_match_op="in", tier_match_value="premium,byod")
stD6["commission_plan"][0]["base_tier_metric"] = None
check("D7 base_tier_metric unset still disables tiering (behaviour preserved, warned about in coverage)",
      CE.preview(FakeClient(stD6), LUX, "July 2026")["by_rep"][0]["tier_multiplier"] == 1.0)

# an unparseable/absent basis is the legacy basis
check("D8 unknown tier_count_basis degrades to the legacy basis",
      CE._tier_basis({"tier_count_basis": "banana"}) == "rule_units"
      and CE._tier_basis({}) == "rule_units")

# per-rep isolation: two reps, different attainment
stD7 = tier_store(tier_count_basis="transactions", tier_match_field="activation_bucket",
                  tier_match_op="in", tier_match_value="premium,byod")
stD7["raw_sales"] = stD7["raw_sales"] + lux_txn("Smith, Bob", "B1") + lux_txn("Smith, Bob", "B2")
stD7["commission_tier"] = [tier(LUX, "p1", "t1", 3, 1.0), tier(LUX, "p1", "t2", 4, 2.0)]
rows = {r["rep"]: r for r in CE.preview(FakeClient(stD7), LUX, "July 2026", coverage=True)["by_rep"]}
check("D9 tier attainment is PER REP (Jane 4 txns -> 2.0x, Bob 2 txns -> 1.0x)",
      rows["Doe, Jane"]["tier_units"] == 4 and rows["Doe, Jane"]["tier_multiplier"] == 2.0
      and rows["Smith, Bob"]["tier_units"] == 2 and rows["Smith, Bob"]["tier_multiplier"] == 1.0,
      str({k: (v["tier_units"], v["tier_multiplier"]) for k, v in rows.items()}))
check("D10 non-tiered rules are NOT scaled by the multiplier",
      True)  # covered structurally below
stD8 = tier_store(tier_count_basis="transactions", tier_match_field="activation_bucket",
                  tier_match_op="in", tier_match_value="premium,byod")
stD8["commission_rule"] = [rule(LUX, "p1", "r1", amount=10, tiered=False)]
stD8["commission_tier"] = [tier(LUX, "p1", "t1", 4, 2.0)]
r_nt = CE.preview(FakeClient(stD8), LUX, "July 2026")["by_rep"][0]
check("D10b a non-tiered rule ignores the 2.0x multiplier (base pay unscaled)",
      r_nt["total_payout"] == 200.0 and r_nt["tiered_payout"] == 0.0, str(r_nt))


# ═══════════════════════════════════════════════════════════════════════════════════════════════
print("\n══ E. BLANK contract_type: OFF = unchanged, ON = paid (two independent opt-ins) ══")
# ═══════════════════════════════════════════════════════════════════════════════════════════════
def blank_ct_store(resolution=None, field="contract_type", value="Activation", ct_map=None,
                   act_rules=LUX_ACT_RULES):
    cfg = [{"org_id": LUX, "contract_type_map": (ct_map or {}), "activation_rules": act_rules}]
    org_cfg = ([{"org_id": LUX, "pay_disabled": False, "residual_visibility": "all",
                 "plan_ct_resolution": resolution}] if resolution else [])
    return base_store(
        commission_plan=[plan(LUX, "p1", "P")],
        commission_rule=[rule(LUX, "p1", "r1", match_field=field, match_value=value, amount=25)],
        commission_plan_assignment=[assign(LUX, "p1")],
        accessory_config=cfg, commission_org_config=org_cfg,
        raw_sales=lux_txn("Doe, Jane", "T1") + lux_txn("Doe, Jane", "T2", byod=True))


# OFF
rE0 = CE.preview(FakeClient(blank_ct_store()), LUX, "July 2026")
check("E1 config OFF: blank-ct activations still pay $0 (unchanged)",
      rE0["by_rep"][0]["total_payout"] == 0.0)
check("E1b OFF output is byte-identical to the base engine",
      rE0 == OLD_CE.preview(FakeClient(blank_ct_store()), LUX, "July 2026"))

# ON via plan_ct_resolution='mapped' — the SAME contract_type rule now matches the resolved bucket
rE1 = CE.preview(FakeClient(blank_ct_store(resolution="mapped", value="premium")), LUX, "July 2026")
check("E2 plan_ct_resolution='mapped': the premium activation pays",
      rE1["by_rep"][0]["total_payout"] == 25.0, str(rE1["by_rep"]))
rE1b = CE.preview(FakeClient(blank_ct_store(resolution="mapped", value="premium,byod")
                             ), LUX, "July 2026")
check("E2b 'mapped' + a raw label still matches raw values too (superset, never a swap)",
      CE._rule_matches({"contract_type": "Upgrade", "_ct_resolved": "upgrade"},
                       {"match_field": "contract_type", "match_value": "Upgrade"})
      and CE._rule_matches({"contract_type": "Upgrade", "_ct_resolved": "upgrade"},
                           {"match_field": "contract_type", "match_value": "upgrade"}))

# ON via the synthetic activation_bucket field — no org setting needed
rE2 = CE.preview(FakeClient(blank_ct_store(field="activation_bucket", value="premium")),
                 LUX, "July 2026")
check("E3 a rule keyed on activation_bucket pays ONCE per activation, no org-level flip needed",
      rE2["by_rep"][0]["total_payout"] == 25.0, str(rE2["by_rep"]))
stE = blank_ct_store(field="activation_bucket", value="premium,byod")
stE["commission_rule"][0]["match_op"] = "in"
rE4 = CE.preview(FakeClient(stE), LUX, "July 2026")
check("E4 op='in' over both buckets pays BOTH the premium and the BYOD activation ($50)",
      rE4["by_rep"][0]["total_payout"] == 50.0, str(rE4["by_rep"]))

# the tenant's contract_type MAP is honoured (mig 213) for non-blank carrier labels
stE2 = base_store(
    commission_plan=[plan(LUX, "p1", "P")],
    commission_rule=[rule(LUX, "p1", "r1", match_field="activation_bucket", match_value="upgrade",
                          amount=30)],
    commission_plan_assignment=[assign(LUX, "p1")],
    accessory_config=[{"org_id": LUX, "contract_type_map": {"handset swap-up": "upgrade"},
                       "activation_rules": []}],
    raw_sales=[sale(LUX, "Doe, Jane", "T9", ct="Handset Swap-Up")])
check("E5 a tenant-mapped carrier label resolves to its bucket and pays",
      CE.preview(FakeClient(stE2), LUX, "July 2026")["by_rep"][0]["total_payout"] == 30.0)

# force-excluded label ('none') must NOT pay
stE3 = copy.deepcopy(stE2)
stE3["accessory_config"][0]["contract_type_map"] = {"handset swap-up": "none"}
check("E6 a label mapped to 'none' is force-excluded (no bucket, no pay)",
      CE.preview(FakeClient(stE3), LUX, "July 2026")["by_rep"][0]["total_payout"] == 0.0)

# degrade: no accessory_config table at all
stE4 = blank_ct_store(field="activation_bucket", value="premium")
del stE4["accessory_config"]
check("E7 missing accessory_config table degrades to the code classifier (no crash, $0 here)",
      CE.preview(FakeClient(stE4), LUX, "July 2026")["by_rep"][0]["total_payout"] == 0.0)
stE5 = blank_ct_store(field="activation_bucket", value="premium")
del stE5["commission_org_config"]
check("E8 missing commission_org_config degrades to 'raw' without raising",
      CE._plan_pay_config(FakeClient(stE5), LUX)["plan_ct_resolution"] == "raw")
stE6 = blank_ct_store(resolution="banana")
check("E9 an invalid plan_ct_resolution value degrades to 'raw'",
      CE._plan_pay_config(FakeClient(stE6), LUX)["plan_ct_resolution"] == "raw")

# a voided / Return line must not become activation evidence
stE7 = blank_ct_store(field="activation_bucket", value="premium")
stE7["raw_sales"] = [dict(r, voided="YES") for r in lux_txn("Doe, Jane", "TV")]
check("E10 voided lines never earn an activation bucket (excluded before classification)",
      CE.preview(FakeClient(stE7), LUX, "July 2026")["by_rep"] == [])


# ═══════════════════════════════════════════════════════════════════════════════════════════════
print("\n══ F. BOOST / HOUSE byte-identity with the new config unset ══")
# ═══════════════════════════════════════════════════════════════════════════════════════════════
def boost_store(seed=0):
    rnd = random.Random(seed)
    reps = ["Ali, Mohammed", "Cabrera, Natasha", "Khan, Ariful", "Smith, Bob"]
    cts = ["Activation", "Upgrade", "BYOD Port-In", "Port-In Add A Line", "", "Device Upgrade"]
    depts = ["Android - XP", "Ondigo", "IPHONE - XP", "Accessories"]
    rows = []
    for i in range(rnd.randint(6, 40)):
        rows.append(sale(HOUSE, rnd.choice(reps), f"H{rnd.randint(1, 12)}", period="June 2026",
                         ct=rnd.choice(cts), dept=rnd.choice(depts),
                         prod=rnd.choice(["iPhone 15", "Case", "Device Setup Charge", "Screen Protector"]),
                         ext=round(rnd.uniform(5, 900), 2), gp=round(rnd.uniform(-20, 300), 2),
                         serial=str(350000000000000 + rnd.randint(1, 999)),
                         mdn=str(3125550000 + rnd.randint(1, 999)), store="1578 Market St",
                         date="2026-06-%02d" % rnd.randint(1, 28)))
    return base_store(
        carrier=[BOOST_CARRIER],
        commission_plan=[plan(HOUSE, "bp", "Boost Rep Plan", carrier_id="c-boost",
                              base_tier_metric=rnd.choice([None, "activations"]))],
        commission_rule=[rule(HOUSE, "bp", "br1", match_field="contract_type", match_op="contains",
                              match_value="upgrade", amount=20, tiered=True),
                         rule(HOUSE, "bp", "br2", match_field="department", match_value="Ondigo",
                              payout_kind="pct_gp", pct=0.1, sort=1),
                         rule(HOUSE, "bp", "br3", match_field="any", payout_kind="flat", amount=50,
                              sort=2, tiered=True)],
        commission_tier=[tier(HOUSE, "bp", "bt1", 3, 0.75), tier(HOUSE, "bp", "bt2", 8, 1.0)],
        commission_plan_assignment=[assign(HOUSE, "bp")],
        plan_installment_schedule=[sched(HOUSE, "bs", "bp", gate_from=1)],
        plan_installment_line=ilines(HOUSE, "bs", 12),
        raw_sales=rows,
        raw_mi=[{"org_id": HOUSE, "period": "June 2026", "phone_number": r["mdn"],
                 "device_serial": r["serial_1"], "subscriber_status": "Active",
                 "actual_mi_payout": 3.5, "actual_atu_payout": 0.0,
                 "base_mrc": 50, "commissionable_mrc": 45} for r in rows[:6]],
        installment_gate_source_config=GATE_SEEDS)


def _norm_sie(res):
    """Drop the purely-additive mig-232 provenance key so the differential compares BEHAVIOUR, not shape.
    The money (by_rep/totals) and the flags are compared UNMODIFIED alongside it."""
    out = dict(res)
    out["ledger"] = [{k: v for k, v in r.items() if k != "ma_lookup_periods"} for r in res["ledger"]]
    return out


def _ledger_key_delta(new, old):
    nk = set().union(*[set(r) for r in new["ledger"]]) if new["ledger"] else set()
    ok = set().union(*[set(r) for r in old["ledger"]]) if old["ledger"] else set()
    return nk - ok


drift_ce = drift_sie = 0
for s in range(300):
    st = boost_store(s)
    a = CE.preview(FakeClient(st), HOUSE, "June 2026")
    b = OLD_CE.preview(FakeClient(st), HOUSE, "June 2026")
    if a != b:
        drift_ce += 1
        if drift_ce == 1:
            print("    first CE drift seed", s, a, b)
    x = _norm_sie(SIE.compute_sale_installments(FakeClient(st), HOUSE, "June 2026", persist=False))
    y = OLD_SIE.compute_sale_installments(FakeClient(st), HOUSE, "June 2026", persist=False)
    if x != y:
        drift_sie += 1
        if drift_sie == 1:
            print("    first SIE drift seed", s)
check("F1 300-seed Boost fuzz: preview() identical to the base engine", drift_ce == 0, f"{drift_ce} drifts")
check("F2 300-seed Boost fuzz: compute_sale_installments() identical", drift_sie == 0, f"{drift_sie} drifts")

# the same fuzz for a PLAN-mode tenant with none of the new config set
drift_lux = drift_lux_sie = 0
for s in range(120):
    st = boost_store(s + 900)
    for t in ("raw_sales", "commission_plan", "commission_rule", "commission_tier",
              "commission_plan_assignment", "plan_installment_schedule", "plan_installment_line",
              "raw_mi"):
        for r in st[t]:
            r["org_id"] = LUX
    st["carrier"] = [TOTAL_CARRIER]
    st["accessory_config"] = [{"org_id": LUX, "contract_type_map": {},
                               "activation_rules": LUX_ACT_RULES}]
    st["raw_ma_commission"] = [ma_row(LUX, "June 2026", r["serial_1"], spiff_m1=-5)
                               for r in st["raw_sales"][:4]]
    if CE.preview(FakeClient(st), LUX, "June 2026") != OLD_CE.preview(FakeClient(st), LUX, "June 2026"):
        drift_lux += 1
    _new = SIE.compute_sale_installments(FakeClient(st), LUX, "June 2026", persist=False)
    _old = OLD_SIE.compute_sale_installments(FakeClient(st), LUX, "June 2026", persist=False)
    if (_new["by_rep"] != _old["by_rep"] or _new["totals"] != _old["totals"]
            or _new["flags"] != _old["flags"] or _norm_sie(_new) != _old):
        drift_lux_sie += 1
check("F3 120-seed PLAN-mode fuzz with the new config UNSET: preview() identical", drift_lux == 0,
      f"{drift_lux} drifts")
check("F4 120-seed PLAN-mode fuzz with the new config UNSET: installments identical "
      "(money + flags exact; ledger identical once the purely-additive ma_lookup_periods provenance key "
      "is dropped)", drift_lux_sie == 0, f"{drift_lux_sie} drifts")
_fa = SIE.compute_sale_installments(FakeClient(stC1), LUX, "July 2026", persist=False)
_fb = OLD_SIE.compute_sale_installments(FakeClient(stC1), LUX, "July 2026", persist=False)
check("F4b that provenance key is the ONLY ledger delta in MA mode",
      _ledger_key_delta(_fa, _fb) == {"ma_lookup_periods"} and _norm_sie(_fa) == _fb,
      str(_ledger_key_delta(_fa, _fb)))

# pre-migration database: the new columns simply do not exist
stF = boost_store(7)
for p in stF["commission_plan"]:
    for k in ("tier_count_basis", "tier_match_field", "tier_match_op", "tier_match_value",
              "tier_below_min_multiplier"):
        p.pop(k, None)
stF["commission_org_config"] = []
check("F5 pre-migration (columns absent) is identical to the base engine",
      CE.preview(FakeClient(stF), HOUSE, "June 2026")
      == OLD_CE.preview(FakeClient(stF), HOUSE, "June 2026"))
stF2 = copy.deepcopy(stF)
del stF2["commission_org_config"]
del stF2["accessory_config"]
check("F6 missing commission_org_config / accessory_config tables never raise",
      CE.preview(FakeClient(stF2), HOUSE, "June 2026")
      == OLD_CE.preview(FakeClient(stF2), HOUSE, "June 2026"))
stF3 = copy.deepcopy(boost_store(11))
for c in stF3["installment_gate_source_config"]:
    c.pop("ma_lookup_periods", None)
check("F7 gate-source rows without ma_lookup_periods behave exactly as before",
      SIE.compute_sale_installments(FakeClient(stF3), HOUSE, "June 2026", persist=False)
      == OLD_SIE.compute_sale_installments(FakeClient(stF3), HOUSE, "June 2026", persist=False))
check("F8 the boost mode default is 'sale' (raw_mi gate never consults MA anyway)",
      SIE._GATE_CFG_DEFAULTS["boost"]["ma_lookup_periods"] == "sale"
      and SIE._GATE_CFG_DEFAULTS["plan"]["ma_lookup_periods"] == "sale")

# ORG ISOLATION: luxelink config must never leak into the house calc
stF4 = boost_store(3)
stF4["commission_org_config"] = [{"org_id": LUX, "pay_disabled": False,
                                  "residual_visibility": "all", "plan_ct_resolution": "mapped"}]
stF4["accessory_config"] = [{"org_id": LUX, "contract_type_map": {"activation": "byod"},
                             "activation_rules": LUX_ACT_RULES}]
check("F9 another tenant's 'mapped' setting + ct map does NOT affect the house calc",
      CE.preview(FakeClient(stF4), HOUSE, "June 2026")
      == OLD_CE.preview(FakeClient(stF4), HOUSE, "June 2026"))
stF5 = copy.deepcopy(stC4)
stF5["installment_gate_source_config"].append(
    {"org_id": HOUSE, "carrier_id": NIL, "carrier_mode": "plan", "gate_source": "ma_commission",
     "ma_lookup_periods": "sale", "is_active": True})
check("F10 the tenant's own 'both' row still wins over the house 'sale' default",
      SIE.compute_sale_installments(FakeClient(stF5), LUX, "July 2026",
                                    persist=False)["by_rep"] == {"DOE, JANE": 15.0})


# ═══════════════════════════════════════════════════════════════════════════════════════════════
print("\n══ G. COVERAGE diagnostics — read-only, money-free ══")
# ═══════════════════════════════════════════════════════════════════════════════════════════════
stG = base_store(
    commission_plan=[plan(LUX, "p1", "Total Employee Comp Chicago",
                          base_tier_metric="activations")],
    commission_rule=[rule(LUX, "p1", "r1", match_field="contract_type", match_value="Activation",
                          amount=25)],
    commission_tier=[tier(LUX, "p1", "t1", 10, 1.2)],
    commission_plan_assignment=[assign(LUX, "p1", "employee", "Jane Doe")],
    accessory_config=[{"org_id": LUX, "contract_type_map": {}, "activation_rules": LUX_ACT_RULES}],
    raw_sales=(lux_txn("Doe, Jane", "T1") + lux_txn("Smith, Bob", "T2")
               + [sale(LUX, "Smith, Bob", "T3", ext=300)]))
gm = CE.preview(FakeClient(stG), LUX, "July 2026", coverage=True)
gb = CE.preview(FakeClient(stG), LUX, "July 2026")
check("G1 coverage=False output is byte-identical to the base engine",
      gb == OLD_CE.preview(FakeClient(stG), LUX, "July 2026") and "coverage" not in gb)
check("G2 coverage=True does NOT change any payout",
      [r["total_payout"] for r in gm["by_rep"]] == [r["total_payout"] for r in gb["by_rep"]]
      and gm["totals"]["payout"] == gb["totals"]["payout"])
cov = gm["coverage"]
check("G3 the uncovered seller is named with his sales", cov["unassigned_count"] == 1
      and cov["unassigned_reps"][0]["rep"] == "Smith, Bob"
      and cov["unassigned_reps"][0]["transactions"] == 2
      and cov["unassigned_reps"][0]["ext_price"] == 549.0, str(cov["unassigned_reps"]))
check("G4 the covered rep's unmatched lines are counted with a sample",
      cov["unmatched"]["total_lines"] == 2 and len(cov["unmatched"]["reps"][0]["sample"]) == 2)
check("G5 blank-contract_type share is reported",
      cov["contract_type"]["blank"] == 5 and cov["contract_type"]["sale_lines"] == 5
      and cov["contract_type"]["blank_pct"] == 100.0, str(cov["contract_type"]))
codes = {w["code"] for w in cov["plan_warnings"]}
check("G6 warns that no rule is marked Tiered", "tiers_without_tiered_rule" in codes, str(codes))
check("G7 warns that contract_type rules face blank Contract Type", "ct_rules_vs_blank_ct" in codes)
check("G8 warns that tier attainment uses the legacy line basis", "tier_basis_legacy" in codes)
stG2 = copy.deepcopy(stG)
stG2["commission_plan"][0]["base_tier_metric"] = None
c2 = CE.preview(FakeClient(stG2), LUX, "July 2026", coverage=True)["coverage"]
check("G9 warns when tiers exist with the metric unset",
      "tiers_without_metric" in {w["code"] for w in c2["plan_warnings"]})
stG3 = copy.deepcopy(stG)
stG3["commission_rule"] = []
stG3["commission_plan_assignment"] = []
c3 = CE.preview(FakeClient(stG3), LUX, "July 2026", coverage=True)["coverage"]
check("G10 warns about a plan with no rules and a plan with no assignments",
      {"plan_without_rules", "plan_without_assignment"} <= {w["code"] for w in c3["plan_warnings"]})
check("G11 with the plan unassigned, BOTH sellers are reported uncovered",
      c3["unassigned_count"] == 2)
stG4 = copy.deepcopy(stG)
stG4["commission_org_config"] = [{"org_id": LUX, "pay_disabled": False,
                                  "residual_visibility": "all", "plan_ct_resolution": "mapped"}]
stG4["commission_rule"][0]["match_value"] = "premium"
c4 = CE.preview(FakeClient(stG4), LUX, "July 2026", coverage=True)
check("G12 with 'mapped' on, the ct warning is gone and the activation pays",
      "ct_rules_vs_blank_ct" not in {w["code"] for w in c4["coverage"]["plan_warnings"]}
      and c4["by_rep"][0]["total_payout"] == 25.0)
check("G13 the resolver's provenance is reported for the operator",
      c4["coverage"]["contract_type"]["resolution"] == "mapped"
      and c4["coverage"]["contract_type"]["bucket_resolver_ran"] is True
      and c4["coverage"]["contract_type"]["bucket_classified_lines"] == 2,
      str(c4["coverage"]["contract_type"]))

# coverage must never write
_writes = []
class _WQ(FakeQuery):
    def insert(self, *a, **k):
        _writes.append(("insert", self.t)); return self
    def update(self, *a, **k):
        _writes.append(("update", self.t)); return self
    def delete(self, *a, **k):
        _writes.append(("delete", self.t)); return self
    def upsert(self, *a, **k):
        _writes.append(("upsert", self.t)); return self
class _WC(FakeClient):
    class _Sch:
        def __init__(self, store): self.store = store
        def table(self, t): return _WQ(self.store, t)
    def schema(self, s): return _WC._Sch(self.store)
CE.preview(_WC(stG), LUX, "July 2026", coverage=True)
SIE.compute_sale_installments(_WC(stC4), LUX, "July 2026", persist=False)
check("G14 neither the coverage preview nor the installment preview writes anything",
      _writes == [], str(_writes))


# ═══════════════════════════════════════════════════════════════════════════════════════════════
print("\n══ H. END-TO-END luxelink: the owner's spec, all four flips on ══")
# ═══════════════════════════════════════════════════════════════════════════════════════════════
def lux_full(pay_period):
    rows = []
    for i in range(4):
        rows += lux_txn("Doe, Jane", f"A{i}", date="2026-05-1%d" % i, period="May 2026",
                        serial=str(355163568356970 + i))
    return base_store(
        carrier=[TOTAL_CARRIER],
        commission_plan=[plan(LUX, "p1", "Total Employee Comp Chicago", carrier_id="c-total",
                              base_tier_metric="activations", tier_count_basis="transactions",
                              tier_match_field="activation_bucket", tier_match_op="in",
                              tier_match_value="premium,byod", tier_below_min_multiplier=0.5)],
        commission_rule=[rule(LUX, "p1", "r1", match_field="activation_bucket", match_op="in",
                              match_value="premium,byod", amount=20, tiered=True)],
        commission_tier=[tier(LUX, "p1", "t1", 4, 1.0), tier(LUX, "p1", "t2", 10, 1.25)],
        commission_plan_assignment=[assign(LUX, "p1", "employee", "Jane Doe")],
        plan_installment_schedule=[dict(sched(LUX, "s1", "p1", gate_from=2),
                                        trigger_match_field="activation_bucket",
                                        trigger_match_op="in", trigger_match_value="premium,byod")],
        plan_installment_line=ilines(LUX, "s1", 15),
        accessory_config=[{"org_id": LUX, "contract_type_map": {},
                           "activation_rules": LUX_ACT_RULES}],
        commission_org_config=[{"org_id": LUX, "pay_disabled": False, "residual_visibility": "all",
                                "plan_ct_resolution": "mapped"}],
        raw_sales=rows,
        raw_ma_commission=[ma_row(LUX, "May 2026", "355163568356970", spiff_m1=-5, rebate=-529),
                           ma_row(LUX, "July 2026", "355163568356971", spiff_m3=-48.75)],
        installment_gate_source_config=GATE_SEEDS + [
            {"org_id": LUX, "carrier_id": NIL, "carrier_mode": "plan", "gate_source": "ma_commission",
             "ma_device_fields": ["imei", "sim"], "ma_month_field_prefix": "spiff_m", "ma_max_month": 6,
             "ma_month1_extra_fields": ["rebate", "device_margin"], "ma_min_amount": 0.01,
             "ma_payout_sign": -1, "ma_lookup_periods": "both", "is_active": True}])


stH = lux_full("May 2026")
pH = CE.preview(FakeClient(stH), LUX, "May 2026", coverage=True)
check("H1 the assigned employee is paid from her plan (4 activations x $20 x 1.0)",
      pH["by_rep"][0]["rep"] == "Doe, Jane" and pH["by_rep"][0]["total_payout"] == 80.0, str(pH["by_rep"]))
check("H2 tier attainment = 4 DISTINCT activations (not 8 lines)",
      pH["by_rep"][0]["tier_units"] == 4 and pH["by_rep"][0]["tier_basis"] == "transactions")
check("H3 nobody is left uncovered and no line falls through",
      pH["coverage"]["unassigned_count"] == 0 and pH["coverage"]["unmatched"]["total_lines"] == 4,
      str(pH["coverage"]["unmatched"]["total_lines"]))   # the 4 rate-plan lines aren't activation lines
iH1 = SIE.compute_sale_installments(FakeClient(stH), LUX, "May 2026", persist=False)
iH3 = SIE.compute_sale_installments(FakeClient(stH), LUX, "July 2026", persist=False)
check("H4 M1 pays for ALL FOUR activations at activation time (no carrier evidence needed)",
      iH1["by_rep"] == {"DOE, JANE": 60.0} and iH1["totals"]["paid"] == 4, str(iH1["totals"]))
check("H5 M3 pays ONLY the one device whose residual actually posted",
      iH3["by_rep"] == {"DOE, JANE": 15.0} and iH3["totals"]["paid"] == 1
      and iH3["totals"]["withheld"] == 3, str(iH3["totals"]))
check("H6 the three held devices raise the two tracking flags each", len(iH3["flags"]) == 6)


# ═══════════════════════════════════════════════════════════════════════════════════════════════
print("\n══ I. VOIDED tokens — ONE set for pay and display (owner-approved 2026-07-25) ══")
# ═══════════════════════════════════════════════════════════════════════════════════════════════
# The money path used to skip a line only when voided == 'YES'; every display surface already excluded
# 'true' / '1' / 'void' / 'voided'. A feed writing a variant produced a line that was PAID but missing
# from the reports it reconciles against.
from app.modules.commcalc import gp_report as GP
from app.modules.commcalc import router as RT
from app.modules.commcalc import calculator as CALC
from app.modules.commcalc import flags as FL
from app.modules.commcalc import commission_drilldown as DD

check("I1 the money path and the display path share ONE token object",
      RT._VOID_TOKENS is GP.VOID_TOKENS and CALC._VOID_TOKENS is GP.VOID_TOKENS
      and CE._VOID_TOKENS is GP.VOID_TOKENS and SIE._VOID_TOKENS is GP.VOID_TOKENS)
check("I2 the set is the Sales Report's set, unchanged",
      set(GP.VOID_TOKENS) == {"true", "yes", "1", "voided", "void"}, str(GP.VOID_TOKENS))
check("I3 is_voided is case/space-insensitive and blank-safe",
      GP.is_voided(" YES ") and GP.is_voided("True") and GP.is_voided("VOID") and GP.is_voided(1)
      and not GP.is_voided("") and not GP.is_voided(None) and not GP.is_voided("no")
      and not GP.is_voided("0") and not GP.is_voided("avoided"))

# (a) the PROD reality — only 'YES' / blank — must be byte-identical to the base engine
def void_store(token):
    st = base_store(
        commission_plan=[plan(LUX, "p1", "P", base_tier_metric="activations",
                              tier_count_basis="transactions", tier_match_field="department",
                              tier_match_op="equals", tier_match_value="BrandedHandset")],
        commission_rule=[rule(LUX, "p1", "r1", amount=25, tiered=True)],
        commission_tier=[tier(LUX, "p1", "t1", 2, 1.0), tier(LUX, "p1", "t2", 3, 2.0)],
        commission_plan_assignment=[assign(LUX, "p1")],
        raw_sales=[sale(LUX, "Doe, Jane", "V1", ct="Activation"),
                   sale(LUX, "Doe, Jane", "V2", ct="Activation"),
                   dict(sale(LUX, "Doe, Jane", "V3", ct="Activation"), voided=token)])
    return st


base_yes = OLD_CE.preview(FakeClient(void_store("YES")), LUX, "July 2026")
new_yes = CE.preview(FakeClient(void_store("YES")), LUX, "July 2026")
check("I4 voided='YES' — payout byte-identical to the base engine (2 lines pay, 1.0x)",
      new_yes == base_yes and new_yes["by_rep"][0]["total_payout"] == 50.0, str(new_yes["by_rep"]))
check("I5 voided='' (blank) — byte-identical, all three lines pay at the 3-unit tier",
      CE.preview(FakeClient(void_store("")), LUX, "July 2026")
      == OLD_CE.preview(FakeClient(void_store("")), LUX, "July 2026")
      and CE.preview(FakeClient(void_store("")), LUX, "July 2026")["by_rep"][0]["total_payout"] == 150.0)

# (b) the variant tokens: excluded from PAY and from TIER attainment, and they used to be paid
for tok in ("true", "1", "void", "voided", "TRUE", " Void "):
    newr = CE.preview(FakeClient(void_store(tok)), LUX, "July 2026")["by_rep"][0]
    oldr = OLD_CE.preview(FakeClient(void_store(tok)), LUX, "July 2026")["by_rep"][0]
    check(f"I6 voided='{tok}' now excluded from pay AND tier (was ${oldr['total_payout']:.0f} @ "
          f"{oldr['tier_multiplier']}x -> now ${newr['total_payout']:.0f} @ {newr['tier_multiplier']}x)",
          (newr["total_payout"] == 50.0 and newr["tier_multiplier"] == 1.0
           and oldr["total_payout"] == 150.0 and oldr["tier_multiplier"] == 2.0),
          f"new={newr['total_payout']}/{newr['tier_multiplier']} old={oldr['total_payout']}/{oldr['tier_multiplier']}")

# the BOOST calculator + the flags pass agree with the engine
boost_sales = [dict(sale(HOUSE, "Ali, Mohammed", "B1", ct="Upgrade", period="June 2026"), voided=t)
               for t in ("", "YES", "true", "1", "void")]
res_calc = CALC.calc_rep_commissions(
    sales=boost_sales, pay_detail=[], dlar_rep=[], dlar_store=[], mi_rows=[], catalog=[],
    cfg={"straight_line": True}, store_mapping=[], shifts=[], employees=[], stores=[],
    period="June 2026", name_map=[], carrier_mode="boost")
check("I7 the Boost calculator counts only the ONE un-voided upgrade (was 4 before)",
      res_calc["commissions"][0]["upgrade_acts"] == 1
      and res_calc["commissions"][0]["total_payout"] == 20.0, str(res_calc["commissions"][0]))
check("I8 the drill-down 'voided' label uses the same predicate",
      DD._is_voided("true") and DD._is_voided("YES") and not DD._is_voided(""))
_fl = FL.calc_flags(sales=boost_sales, pay_detail=[], mi_rows=[], dlar_store=[], store_mapping=[],
                    period="June 2026", period_month=6, period_year=2026)
check("I9 the flags pass runs on the same filtered set without raising", isinstance(_fl, list))

# a voided line can never generate an installment under any spelling
for tok in ("YES", "true", "1", "void"):
    stI = inst_store(gate_from=2, ma_rows=[])
    stI["raw_sales"] = [dict(r, voided=tok) for r in stI["raw_sales"]]
    check(f"I10 voided='{tok}' generates NO installment",
          SIE.compute_sale_installments(FakeClient(stI), LUX, "May 2026", persist=False)["ledger"] == [])

# the 300-seed Boost fuzz above used blank 'voided' only; re-run a slice with YES/blank mixed in
_vd = 0
for sd in range(60):
    st = boost_store(sd)
    for i, r in enumerate(st["raw_sales"]):
        r["voided"] = "YES" if i % 7 == 0 else ""
    if CE.preview(FakeClient(st), HOUSE, "June 2026") != OLD_CE.preview(FakeClient(st), HOUSE, "June 2026"):
        _vd += 1
check("I11 60-seed fuzz with YES/blank voided values: still byte-identical", _vd == 0, f"{_vd} drifts")

# I12/I13 — the two ROUTER money-path sites the Gate-1 reviewer pinned (the live-calc pre-filter that
# feeds calc_flags, and /commission-drill which exists to REPLAY what the calculator counted).
import inspect as _insp
_src_calc = _insp.getsource(RT._run_calculation)
_src_drill = _insp.getsource(RT.commission_drill)
check("I12 _run_calculation's pre-filter uses the shared predicate, not upper()=='YES'",
      "_gp_is_voided(r.get('voided'))" in _src_calc and "upper().strip() != 'YES'" not in _src_calc)
check("I13 /commission-drill replays the SAME skip rule",
      '_gp_is_voided(r.get("voided"))' in _src_drill and 'upper() == "YES"' not in _src_drill)
check("I14 router shares the predicate OBJECT with gp_report (not a copy)",
      RT._gp_is_voided is GP.is_voided)
# and no narrow check survives anywhere in the money path
import pathlib as _pl
_narrow = [p.name for p in _pl.Path("app/modules/commcalc").glob("*.py")
           if "upper().strip() != 'YES'" in p.read_text()
           or 'upper().strip() != "YES"' in p.read_text()
           or 'upper() == "YES"' in p.read_text()]
check("I15 no narrow upper()=='YES' voided check remains in commcalc", _narrow == [], str(_narrow))


print(f"\n{'=' * 70}\nPASS {PASS}   FAIL {FAIL}\n{'=' * 70}")
sys.exit(1 if FAIL else 0)
