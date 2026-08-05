"""PROOF — trans 3207's Screen Protector / Case pay $0, and WHY. (mod-commission, owner 2026-08-05)

OWNER REPORT (luxelink, July 2026, rep "Espinoza, Carolina", transaction 3207):
  "I mapped `Accessories Package` as an accessory, added the NY market assignment and re-ran July.
   The Screen Protector and Case lines STILL pay $0 and they still show under the `edge` bucket."

The pre-registered hypothesis was "the category/edge rule is OVERRIDING the product-level accessory
mapping". §A DISPROVES that and §B proves the real mechanism:

  * Rules do NOT consume lines. `commission_engine.preview` recomputes `_matched` from the rep's FULL
    line list for every rule, and `plan_pay_gate.select_paying_lines` suppresses per RULE, not
    globally. An accessory rule would happily pay a line the edge rule collapsed. (§A)
  * The accessory rule matched NOTHING. The synthetic `accessory` match_field is stamped by
    `accessory_catalog.AccessoryClassifier` = accessory_config's department/category/product-keyword
    lists + the raw_catalog category layer. The owner's mapping lives in
    `commcalc.accessory_definition_map` (mig 257), which THE PAY PATH HAS NEVER READ — migration 257
    says so in its own header. Two surfaces; the money path reads the other one. (§B)

THE FIX (mig 276 + this branch): a per-tenant switch `accessory_config.definition_drives_pay`,
DEFAULT FALSE, that makes the stamp `legacy OR catalog OR the tenant's CONFIRMED definition` —
strictly additive. Default-off means merging this moves $0 (§E is the byte-identity negative control
against the pre-change engine); money moves only when a human flips it AND presses Run Commission.

Run:  cd backend && python3 scratchpad/accessory_definition_pay_proof.py
"""
import copy
import json
import os
import subprocess
import sys
import types

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import app.modules.commcalc.commission_engine as CE           # noqa: E402
import app.modules.commcalc.plan_pay_gate as GATE             # noqa: E402

HOUSE = "00000000-0000-0000-0000-000000000001"
LUX = "854f6d7b-6590-4e4d-88ab-646f560d4f4c"
OTHER = "11111111-2222-3333-4444-555555555555"
REP = "Espinoza, Carolina"
STORE = "4640-A W Diversey Ave"
PERIOD = "July 2026"
RATE = 0.175                       # the corrected accessory rate (owner ran 17.5 -> 0.175 on 08-01)
EDGE_TENDER = "TW EDGE SPF Month 1"

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


# ── differential base: the engine as it stands on main, before this branch ──────────────────────
_PINNED_BASE = "1b56a8a"


def _load_old():
    repo = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))

    def show(p):
        return subprocess.check_output(["git", "-C", repo, "show", f"{_PINNED_BASE}:{p}"], text=True)

    m = types.ModuleType("OLD_commission_engine")
    m.__dict__["__name__"] = "OLD_commission_engine"
    exec(compile(show("backend/app/modules/commcalc/commission_engine.py"),
                 "OLD_commission_engine.py", "exec"), m.__dict__)
    return m


OLD_CE = _load_old()
print(f"(differential pinned to the pre-change engine @ {_PINNED_BASE})")


# ── fake supabase (same convention as harness_storeops_scope_wiring / accessory_basis_guard_proof) ──
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
        return True

    def execute(self):
        if self.t not in self.store:
            raise Exception(f'relation "commcalc.{self.t}" does not exist')
        rows = [dict(r) for r in self.store.get(self.t, []) if self._m(r)]
        # a SELECT of a column the table does not have is how this fake simulates "migration unapplied"
        probe = self.store.get(self.t, [])
        if self.cols and self.cols != "*" and probe:
            for c in [x.strip() for x in str(self.cols).split(",")]:
                if c and c not in probe[0] and not c.startswith("count"):
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
        self.store, self.writes = store, []

    def schema(self, s):
        return FakeClient._Sch(self.store, self.writes)

    class _Sch:
        def __init__(self, store, writes):
            self.store, self.writes = store, writes

        def table(self, t):
            return FakeQuery(self.store, t, self.writes)


def base_store(**extra):
    s = {"commission_plan": [], "commission_rule": [], "commission_tier": [],
         "commission_plan_assignment": [], "plan_installment_schedule": [], "plan_installment_line": [],
         "raw_sales": [], "daily_sales_feed": [], "raw_mi": [], "raw_ma_commission": [],
         "store_mapping": [], "employees": [], "product_mrc": [], "carrier_category_map": [],
         "commission_org_config": [], "item_mapping": [], "raw_catalog": [], "carrier": [],
         "installment_gate_source_config": [], "accessory_config": [], "contract_type_map": [],
         "activation_rules": [], "accessory_definition_map": [], "accessory_class": [],
         "payout_exclusion_map": []}
    s.update(extra)
    return s


def sale(tid, prod, ext=0.0, gp=0.0, dept="", cat="", serial="", tender=EDGE_TENDER,
         org=LUX, rep=REP, date="2026-07-12"):
    return {"org_id": org, "period": PERIOD, "trans_id": tid, "trans_date": date, "store": STORE,
            "salesperson": rep, "department": dept, "category": cat, "contract_type": "",
            "product_desc": prod, "ext_price": ext, "gp": gp, "voided": "", "trans_type": "",
            "mdn": "", "serial_1": serial, "customer_plan": prod, "sku": "", "tender_type": tender,
            "product_id": None}


def plan(org, pid, name):
    return {"id": pid, "org_id": org, "name": name, "carrier_id": None, "base_tier_metric": None,
            "is_active": True}


def rule(org, pid, rid, **kw):
    r = {"id": rid, "org_id": org, "plan_id": pid, "label": None, "match_field": "any",
         "match_op": "equals", "match_value": None, "qualifies": True,
         "payout_kind": "flat_per_unit", "amount": 0, "pct": 0, "tiered": False, "sort": 0}
    r.update(kw)
    return r


def assign(org, pid, scope="default", value=None, priority=0):
    return {"id": f"a-{pid}-{scope}-{value}", "org_id": org, "plan_id": pid, "scope": scope,
            "scope_value": value, "priority": priority}


# ══ TRANSACTION 3207, AS THE OWNER DESCRIBES IT ═══════════════════════════════════════════════════
# One FINANCED (TW EDGE) sale. Every line carries the transaction-level tender, exactly one line
# carries a device IMEI, and the accessory lines carry the tenant's EARLY-JULY field spelling
# (department 'BrandedHandset' / category 'HandsetBranded') — the spelling that makes a category-field
# rule miss the first week of the month (mig 257 header, proven on the owner's own export).
IMEI = "356938035643809"


def txn_3207():
    return [
        sale("3207", "Motorola Moto G Play 2026", ext=129.99, gp=40.00,
             dept="BrandedHandset", cat="HandsetBranded", serial=IMEI),
        sale("3207", "Screen Protectors BYOD", ext=24.97, gp=0.00,
             dept="BrandedHandset", cat="HandsetBranded"),
        sale("3207", "Case BYOD", ext=19.99, gp=0.00,
             dept="BrandedHandset", cat="HandsetBranded"),
        sale("3207", "Total Wireless $50 Unlimited", ext=50.00, gp=0.00, dept="RatePlan", cat="Plan"),
        sale("3207", "Activation payment - Total Wireless", ext=10.00, gp=10.00,
             dept="Fees", cat="Fees"),
        sale("3207", "Access Charge", ext=5.00, gp=5.00, dept="Fees", cat="Fees"),
        sale("3207", "Wallet Funding", ext=73.00, gp=0.00, dept="Fees", cat="Fees"),
        sale("3207", "Protect+ Device Protection", ext=8.00, gp=8.00, dept="Fees", cat="Fees"),
    ]


# The owner's ACCESSORY DEFINITION (mig 257) — mapped on /commcalc/accessory-definition, CONFIRMED.
ACC_MAP = [
    {"id": "m1", "org_id": LUX, "match_field": "product_desc", "match_value": "Screen Protectors BYOD",
     "is_accessory": True, "accessory_class": "screen_protector", "status": "confirmed"},
    {"id": "m2", "org_id": LUX, "match_field": "product_desc", "match_value": "Case BYOD",
     "is_accessory": True, "accessory_class": "case", "status": "confirmed"},
    {"id": "m3", "org_id": LUX, "match_field": "product_desc", "match_value": "Accessories Package",
     "is_accessory": True, "accessory_class": "other_accessory", "status": "confirmed"},
]


def lux_store(definition_drives_pay=None, mapped=True, sales=None, org=LUX,
              acc_categories=("Accessory",), field_rule=None):
    """The luxelink July world. `definition_drives_pay=None` = the mig-276 column DOES NOT EXIST
    (pre-migration); True/False = it exists with that value."""
    s = base_store()
    s["commission_plan"] = [plan(org, "p-lux", "Total Employee Comp NY")]
    s["commission_rule"] = [
        # the EDGE rule — a TW-financing TENDER rule (NOT a device model). flat_per_unit on a
        # transaction-level field => the pay gate auto-collapses it to one payment per DEVICE.
        rule(org, "p-lux", "r-edge", label="edge", match_field="tender_type", match_op="contains",
             match_value="edge", payout_kind="flat_per_unit", amount=25.0, sort=0),
        # the ACCESSORY rule — exactly what the owner's id-agnostic one-liner set.
        rule(org, "p-lux", "r-acc", label="accessory", match_field="accessory", match_op="equals",
             match_value="yes", payout_kind="pct_price", pct=RATE, sort=1),
    ]
    s["commission_plan_assignment"] = [assign(org, "p-lux", "default")]
    s["store_mapping"] = [{"org_id": org, "store_address": STORE.lower(),
                           "store_code": "IL01", "market": "NY"}]
    s["raw_sales"] = ([dict(r, org_id=org) for r in txn_3207()] if sales is None else sales)
    # accessory_config as luxelink actually holds it: the SINGULAR 'Accessory' category, no catalog.
    cfg = {"org_id": org,
           "departments": [], "categories": list(acc_categories), "product_keywords": [],
           "catalog_classify_enabled": False, "catalog_accessory_categories": [],
           "definition_field_rule": field_rule, "setup_fee_products": ["Device Setup Charge"],
           "setup_fee_keywords": ["Device Setup Charge"]}
    if definition_drives_pay is not None:
        cfg["definition_drives_pay"] = bool(definition_drives_pay)
    s["accessory_config"] = [cfg]
    if mapped:
        s["accessory_definition_map"] = [dict(m, org_id=org) for m in ACC_MAP]
    return s


def rules_of(res, rep=REP):
    for r in res.get("by_rep") or []:
        if r.get("rep") == rep:
            return {rb.get("label"): rb for rb in (r.get("rules") or [])}
    return {}


def total(res, rep=REP):
    for r in res.get("by_rep") or []:
        if r.get("rep") == rep:
            return round(r.get("total_payout") or 0, 2)
    return 0.0


def run(store, **kw):
    return CE.preview(FakeClient(copy.deepcopy(store)), LUX, PERIOD, **kw)


# ══ §A — DISPROVE the registered hypothesis: nothing "overrides" the accessory rule ══════════════
print("\n── A. THE EDGE RULE DOES NOT SWALLOW THE ACCESSORY LINES ────────────────────────────────")
_a = run(lux_store(), detail=True)
_ar = rules_of(_a)
_edge_lines = _ar["edge"]["lines"]
_supp = [l for l in _edge_lines if l.get("suppressed")]
check("A1  the edge rule MATCHED all 8 lines of trans 3207 (it keys on the transaction's tender)",
      _ar["edge"]["matched_lines"] == 8, str(_ar["edge"]["matched_lines"]))
check("A2  ...and pays $25.00 ONCE, anchored on the single IMEI line — the owner's own ruling",
      _ar["edge"]["payout"] == 25.0 and _ar["edge"]["unit_basis"] == "per_device",
      json.dumps({k: _ar["edge"].get(k) for k in ("payout", "unit_basis")}))
check("A3  the 7 non-device lines are suppressed ON THE EDGE RULE ONLY, reason "
      "'unit_not_device_line' — this is the ⛔ the owner sees",
      len(_supp) == 7 and {l["suppressed_by"] for l in _supp} == {"unit_not_device_line"},
      str([l.get("suppressed_by") for l in _supp]))
check("A4  the SCREEN PROTECTOR and the CASE are among them",
      {"Screen Protectors BYOD", "Case BYOD"} <= {str(l["product"]) for l in _supp})
# The decisive one: suppression is PER RULE. The accessory rule sees the same 8 lines untouched.
_acc = _ar["accessory"]
check("A5  the ACCESSORY rule evaluated the SAME 8 lines independently — no line was consumed, "
      "no exclusivity, no first-match-wins (the registered hypothesis is DISPROVEN)",
      _acc["matched_lines"] == 0 and _ar["edge"]["matched_lines"] == 8,
      f"accessory matched={_acc['matched_lines']}")
check("A6  ...it matched ZERO lines and therefore paid $0.00 — that, not the edge rule, is the $0",
      _acc["payout"] == 0.0, str(_acc["payout"]))
check("A7  today the rep's whole 3207 payout is the single $25.00 edge unit", total(_a) == 25.0,
      str(total(_a)))

print("\n── B. WHY IT MATCHED ZERO: two accessory surfaces, and pay reads the other one ──────────")
_stamp = _a.get("accessory_stamp") or {}
check("B1  the engine stamped every line `accessory = no`",
      _stamp.get("lines") == 8 and _stamp.get("yes") == 0, json.dumps(_stamp, default=str)[:300])
check("B2  ...although the owner CONFIRMED both products on the Accessory Definition page",
      len([m for m in ACC_MAP if m["is_accessory"]]) == 3)
# prove the definition itself is fine — it is simply not consulted by the stamp
_pred = GATE.accessory_predicate(FakeClient(copy.deepcopy(lux_store())), LUX)
check("B3  the tenant's OWN definition says YES for both lines (so the mapping is not the problem)",
      _pred is not None and _pred(txn_3207()[1]) and _pred(txn_3207()[2]))
check("B4  the PAY-path classifier says NO for both — accessory_config holds the SINGULAR "
      "'Accessory' category and the lines carry 'HandsetBranded'",
      _stamp.get("by_catalog_or_legacy") == 0 and _stamp.get("definition_drives_pay") is False)
check("B5  the diagnostic says so in plain English instead of leaving a silent $0",
      "Accessory Definition mapping page" in str(_stamp.get("note")), str(_stamp.get("note"))[:160])
_cov = run(lux_store(), coverage=True).get("coverage") or {}
_warn = [w for w in (_cov.get("plan_warnings") or [])
         if w.get("code") == "accessory_rule_classifies_nothing"]
check("B6  ...and the plan-coverage report raises it as a HIGH-severity plan warning",
      len(_warn) == 1 and _warn[0]["severity"] == "high",
      json.dumps(_cov.get("plan_warnings"), default=str)[:300])

print("\n── C. THE FIX — the switch ON, and the owner's expected $4.37 ───────────────────────────")
_c = run(lux_store(definition_drives_pay=True), detail=True)
_cr = rules_of(_c)
_camt = {str(l["product"]): round(l["amount"], 2) for l in (_cr["accessory"]["lines"] or [])}
check("C1  the accessory rule now matches exactly the 2 accessory lines",
      _cr["accessory"]["matched_lines"] == 2, str(_camt))
check("C2  Screen Protector $24.97 x 17.5% = $4.37 — the figure the owner predicted",
      _camt.get("Screen Protectors BYOD") == 4.37, str(_camt))
check("C3  Case $19.99 x 17.5% = $3.50", _camt.get("Case BYOD") == 3.50, str(_camt))
check("C4  the accessory rule pays $7.87 in total", _cr["accessory"]["payout"] == 7.87,
      str(_cr["accessory"]["payout"]))
check("C5  NON-ACCESSORY LEGS UNCHANGED — the edge rule still pays $25.00 exactly once per device",
      _cr["edge"]["payout"] == 25.0 and _cr["edge"]["matched_lines"] == 8, str(_cr["edge"]["payout"]))
check("C6  ...the rate plan / activation fee / access charge / wallet / protection lines still pay "
      "$0 under the accessory rule",
      set(_camt) == {"Screen Protectors BYOD", "Case BYOD"}, str(sorted(_camt)))
check("C7  the rep's total moves $25.00 -> $32.87, i.e. the accessory delta and nothing else",
      total(_c) == 32.87 and round(total(_c) - total(_a), 2) == 7.87, str(total(_c)))
_cs = _c.get("accessory_stamp") or {}
check("C8  the stamp attributes the 2 lines to the DEFINITION (not to the catalog/legacy lists)",
      _cs.get("by_definition") == 2 and _cs.get("by_catalog_or_legacy") == 0,
      json.dumps(_cs, default=str)[:240])

print("\n── D. STRICTLY ADDITIVE — nothing that pays today stops paying ──────────────────────────")
# a tenant whose accessory_config DOES carry the right category already pays; the switch must not
# change that line by a cent, and must not remove it.
_d_off = run(lux_store(acc_categories=("HandsetBranded",)), detail=True)
_d_on = run(lux_store(definition_drives_pay=True, acc_categories=("HandsetBranded",)), detail=True)
check("D1  a tenant already classified by its category list pays the same with the switch ON",
      total(_d_off) == total(_d_on), f"{total(_d_off)} vs {total(_d_on)}")
check("D2  ...and those lines are attributed to the legacy list, not double-counted",
      (_d_on.get("accessory_stamp") or {}).get("by_definition") == 0,
      json.dumps(_d_on.get("accessory_stamp"), default=str)[:200])
# an explicit is_accessory=false mapping is a DEFINITION-side exclusion. Documented limitation: the
# stamp is additive, so it can never REMOVE a line the legacy list already claims.
_excl = lux_store(definition_drives_pay=True, acc_categories=("HandsetBranded",))
_excl["accessory_definition_map"].append(
    {"id": "m9", "org_id": LUX, "match_field": "product_desc", "match_value": "Case BYOD",
     "is_accessory": False, "accessory_class": None, "status": "confirmed"})
check("D3  an is_accessory=false mapping never REMOVES a legacy accessory (additive by design — "
      "removal would cut someone's pay and is a separate owner decision)",
      total(run(_excl)) == total(_d_off), str(total(run(_excl))))
# set-up fees are never accessories (standing owner rule 2026-07-17)
_sf = lux_store(definition_drives_pay=True)
_sf["raw_sales"] = txn_3207() + [sale("3207", "Device Setup Charge", ext=35.00, gp=35.00,
                                      dept="Fees", cat="Fees")]
_sf["accessory_definition_map"].append(
    {"id": "m8", "org_id": LUX, "match_field": "department", "match_value": "Fees",
     "is_accessory": True, "accessory_class": "other_accessory", "status": "confirmed"})
_sfa = {str(l["product"]) for l in (rules_of(run(_sf, detail=True))["accessory"]["lines"] or [])}
check("D4  a set-up fee is never turned into an accessory by the definition, even when its whole "
      "department is mapped", "Device Setup Charge" not in _sfa, str(sorted(_sfa)))
# PROPOSED mappings must not pay — only CONFIRMED ones
_prop = lux_store(definition_drives_pay=True)
for m in _prop["accessory_definition_map"]:
    m["status"] = "proposed"
check("D5  a PROPOSED mapping pays nothing — only what the owner CONFIRMED moves money",
      total(run(_prop)) == 25.0, str(total(run(_prop))))

print("\n── E. NEGATIVE CONTROL — merging this moves $0 ──────────────────────────────────────────")
_scenarios = {
    "pre-migration (column absent)": lux_store(),
    "column present, switch OFF": lux_store(definition_drives_pay=False),
    "no definition mapped at all": lux_store(mapped=False),
    "tenant already classified by category": lux_store(acc_categories=("HandsetBranded",)),
}
for _nm, _st in _scenarios.items():
    _new = CE.preview(FakeClient(copy.deepcopy(_st)), LUX, PERIOD)
    _old = OLD_CE.preview(FakeClient(copy.deepcopy(_st)), LUX, PERIOD)
    check(f"E  money-path result is BYTE-IDENTICAL to the pre-change engine — {_nm}",
          json.dumps(_new, sort_keys=True, default=str) == json.dumps(_old, sort_keys=True, default=str),
          (json.dumps(_new, sort_keys=True, default=str)[:200] + " || " +
           json.dumps(_old, sort_keys=True, default=str)[:200]))
_pre = run(lux_store())
check("E5  with the column absent the engine does not raise and reports the switch as OFF",
      (_pre.get("accessory_stamp") or {}).get("definition_drives_pay") is False
      or _pre.get("accessory_stamp") is None, str(_pre.get("accessory_stamp")))
check("E6  nothing was written by ANY of the previews above",
      all(not FakeClient(copy.deepcopy(lux_store())).writes for _ in (0,)))

print("\n── F. MULTI-TENANT — the switch and the definition are per-tenant ───────────────────────")
_other = lux_store(definition_drives_pay=True, org=OTHER)
# the OTHER tenant's rows, read as LUX: org-scoped reads must see nothing
_mix = base_store()
for k in ("commission_plan", "commission_rule", "commission_plan_assignment", "raw_sales",
          "store_mapping", "accessory_config", "accessory_definition_map"):
    _mix[k] = list(_other[k])
_f = CE.preview(FakeClient(copy.deepcopy(_mix)), LUX, PERIOD)
check("F1  another tenant's plan/mapping/switch is invisible to luxelink (org-scoped reads)",
      (_f.get("by_rep") or []) == [], json.dumps(_f.get("by_rep"), default=str)[:200])
_g = CE.preview(FakeClient(copy.deepcopy(_mix)), OTHER, PERIOD)
check("F2  ...and the OTHER tenant gets its own $32.87 from its own switch", total(_g) == 32.87,
      str(total(_g)))
_lux_off_other_on = copy.deepcopy(lux_store(definition_drives_pay=False))
for k in ("commission_plan", "commission_rule", "commission_plan_assignment", "raw_sales",
          "store_mapping", "accessory_config", "accessory_definition_map"):
    _lux_off_other_on[k] = list(_lux_off_other_on[k]) + list(_other[k])
check("F3  luxelink OFF + other-tenant ON coexist: luxelink still $25.00",
      total(CE.preview(FakeClient(_lux_off_other_on), LUX, PERIOD)) == 25.0)

print("\n── G. THE IMPACT ENDPOINT'S OVERRIDE ────────────────────────────────────────────────────")
check("G1  definition_pay_override=False forces OFF even when the tenant switched it ON",
      total(run(lux_store(definition_drives_pay=True), definition_pay_override=False)) == 25.0)
check("G2  definition_pay_override=True forces ON even when the tenant has it OFF",
      total(run(lux_store(definition_drives_pay=False), definition_pay_override=True)) == 32.87)
check("G3  the override writes nothing and needs no config row",
      total(run(lux_store(), definition_pay_override=True)) == 32.87)

print(f"\n{'='*92}\n{PASS} passed, {FAIL} failed" + (f"  ->  {FAILED}" if FAILED else ""))
sys.exit(1 if FAIL else 0)
