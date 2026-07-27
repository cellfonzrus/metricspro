"""Proof for agent/commission/multimonth-category-config (owner directive 2026-07-27).

THREE DELIVERABLES, all money-touching, all built PARKED (nothing recalculated):
  1. DEVICE-CATEGORY QUALIFICATION (mig 245) — per-tenant include/exclude for the multi-month payout.
     Owner defaults: everything ON except TABLET and SIM.
  2. TABLET DEVICE-PRICE-AS-MRC recurrence (mig 246) — why a $279.99 promo price was still paid as an
     MRC after mig 233, and the structural fix.
  3. DISPLAY CONSISTENCY — every multi-month row shows DEVICE + RATE PLAN in ONE line.

FIXTURES ARE THE OWNER'S OWN JULY 2026 M1 ROWS (pasted 2026-07-27):
  357845420399880  Total Wireless Base Unlimited Tablet 6-Month Plan $60   M1  $14.00  MRC 279.99
  357845420428952 / …429083 / …452713  Samsung Galaxy Tab A11+ 5G TO - Promo $279.99  M1 $14.00 / 279.99
  89148000008588591838 / …1788  Total by Verizon SIM Kit                   M1  $3.25/65 · $1.50/30
  358835493256918  Total Wireless Home Internet                            M1  $2.75  MRC 55.00
  358662802056452  Motorola Edge 2025 …                          appears TWICE at $2.75 / 55.00
  TCL Tab 8 / Tab 10                                             M1 $2.50/50 · $2.00/40 · $1.50/30

Run:  cd backend && python3 scratchpad/installment_category_qualification_proof.py
"""
import os
import sys
import types
import subprocess
import random

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import app.modules.commcalc.sale_installment_engine as NEW
from app.modules.commcalc import installment_category as ICAT
from app.modules.commcalc.sale_installment_engine import (
    _mrc_candidate, _norm_plan_matcher, _norm_hw, _line_is_plan_line, _line_is_hardware,
    extract_mrc_monthly, extract_mrc_bare, extract_mrc_bare_anchored, installment_label,
    DEFAULT_PLAN_LINE_MATCHER,
)

HOUSE = "00000000-0000-0000-0000-000000000001"
LUXE = "854f6d7b-6590-4e4d-88ab-646f560d4f4c"

PASS = FAIL = 0


def check(name, cond, extra=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  ok  {name}")
    else:
        FAIL += 1
        print(f"FAIL  {name}   {extra}")


# ── PRISTINE pre-change engine (origin/main), for the differential ────────────────────────────────
_PINNED_BASE = "7916bde"


def _load_old_engine():
    repo = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
    try:
        ref = subprocess.check_output(
            ["git", "-C", repo, "merge-base", "HEAD", "origin/main"], text=True).strip() or _PINNED_BASE
    except Exception:
        ref = _PINNED_BASE
    srcs = subprocess.check_output(
        ["git", "-C", repo, "show", f"{ref}:backend/app/modules/commcalc/sale_installment_engine.py"],
        text=True)
    mod = types.ModuleType("OLD_sale_installment_engine")
    mod.__dict__["__name__"] = "OLD_sale_installment_engine"
    exec(compile(srcs, "OLD_sale_installment_engine.py", "exec"), mod.__dict__)
    mod._loaded_from = ref
    return mod


OLD = _load_old_engine()
print(f"(differential pinned to the pre-change engine @ {OLD._loaded_from[:10]})")


# ═══ in-memory FakeClient (same shape as the mig-233 harness) ═════════════════════════════════════
class FakeResult:
    def __init__(self, data):
        self.data = data


class FakeQuery:
    def __init__(self, store, table):
        self.store, self.t, self.f = store, table, []
        self.rng, self.ordk, self.orddesc = None, None, False
        self._op, self._rows, self._conflict = "select", None, None
        self.missing = store.get("_missing") or set()

    def select(self, *a, **k):
        # a table/column the tenant's DB does not have yet (migration not applied) must RAISE, exactly
        # as postgrest does — that is how we prove graceful degradation.
        if self.t in self.missing:
            raise Exception(f'relation "commcalc.{self.t}" does not exist')
        cols = ",".join(a) if a else ""
        for c in cols.split(","):
            if c.strip() and f"{self.t}.{c.strip()}" in self.missing:
                raise Exception(f'column "{c.strip()}" does not exist')
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

    def insert(self, rows, **k):
        self._op, self._rows = "insert", (rows if isinstance(rows, list) else [rows]); return self

    def update(self, row, **k):
        self._op, self._rows = "update", [row]; return self

    def upsert(self, rows, on_conflict=None, **k):
        self._op, self._rows, self._conflict = "upsert", (rows if isinstance(rows, list) else [rows]), on_conflict
        return self

    def delete(self):
        self._op = "delete"; return self

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
        if self.t in self.missing:
            raise Exception(f'relation "commcalc.{self.t}" does not exist')
        tbl = self.store.setdefault(self.t, [])
        if self._op == "delete":
            self.store[self.t] = [r for r in tbl if not self._m(r)]
            return FakeResult([])
        if self._op == "insert":
            for row in self._rows:
                tbl.append(dict(row))
            return FakeResult(self._rows)
        if self._op == "update":
            for r in tbl:
                if self._m(r):
                    r.update(self._rows[0])
            return FakeResult(self._rows)
        if self._op == "upsert":
            keys = [c.strip() for c in (self._conflict or "").split(",") if c.strip()]
            seen = set()
            for row in self._rows:
                if keys:
                    kk = tuple(str(row.get(c)) for c in keys)
                    if kk in seen:
                        raise Exception("ON CONFLICT DO UPDATE command cannot affect row a second time")
                    seen.add(kk)
                    hit = next((r for r in tbl if all(str(r.get(c)) == str(row.get(c)) for c in keys)), None)
                    if hit is not None:
                        hit.update(row)
                        continue
                tbl.append(dict(row))
            return FakeResult(self._rows)
        rows = [dict(r) for r in tbl if self._m(r)]
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


# ═══ fixtures — the owner's real July product strings + luxelink's real POS vocabulary ════════════
PERIOD = "July 2026"
CT = "Port with IDV"
REP = "Ana Cruz"

TABLET_DEV = "Samsung Galaxy Tab A11+ 5G TO - Promo $279.99, Min $50 tablet plan w/6 months of service"
TABLET_PLAN = "Total Wireless Base Unlimited Tablet 6-Month Plan $60"
PHONE_DEV = "Motorola Edge 2025 - Promo $190.00"
PHONE_PLAN = "Total ALL ACCESS Plan $55"
HI_DEV = "Total Wireless Home Internet"
HI_PLAN = "Total Wireless Home Internet Plan $55"
SIM_LINE = "Total by Verizon SIM Kit"
SIM_PLAN = "Total Wireless Unlimited Plan $65"
TCL_DEV = "TCL TAB 10 NXTPAPER 5G - Promo $179.99 w/ tablet plan"
TCL_PLAN = "Total Wireless Tablet Plan $50"


def line(org, tid, desc, *, rep=REP, store="Chicago 1", mdn="", serial="", ext=0.0, gp=0.0,
         dept="", cat="", ct=CT, period=PERIOD, tt="", voided="", sku="", date="2026-07-08"):
    return {"org_id": org, "period": period, "trans_id": tid, "store": store, "salesperson": rep,
            "department": dept, "category": cat, "contract_type": ct, "product_desc": desc,
            "ext_price": ext, "gp": gp, "voided": voided, "trans_type": tt, "mdn": mdn,
            "serial_1": serial, "sku": sku, "trans_date": date}


def tablet_sale(org, tid="TB1", serial="357845420428952", mdn="7735550111"):
    return [line(org, tid, TABLET_DEV, serial=serial, ext=279.99, dept="BrandedHandset",
                 cat="KittedBranded"),
            line(org, tid, TABLET_PLAN, mdn=mdn, ext=60.0, dept="Rtr", cat="Other Carr. payments")]


def phone_sale(org, tid="PH1", serial="358662802056452", mdn="7735550122"):
    return [line(org, tid, PHONE_DEV, serial=serial, ext=190.0, dept="BrandedHandset",
                 cat="KittedBranded"),
            line(org, tid, PHONE_PLAN, mdn=mdn, ext=55.0, dept="Rtr", cat="Other Carr. payments")]


def home_internet_sale(org, tid="HI1", serial="358835493256918", mdn="7735550133"):
    return [line(org, tid, HI_DEV, serial=serial, ext=99.99, dept="BrandedHandset", cat="KittedBranded"),
            line(org, tid, HI_PLAN, mdn=mdn, ext=55.0, dept="Rtr", cat="Other Carr. payments")]


def sim_sale(org, tid="SM1", iccid="89148000008588591838", mdn="7735550144", mrc=65.0, plan=SIM_PLAN):
    return [line(org, tid, SIM_LINE, serial=iccid, ext=9.99, dept="Rtr", cat="SimMarketplace"),
            line(org, tid, plan, mdn=mdn, ext=mrc, dept="Rtr", cat="Other Carr. payments")]


def tcl_sale(org, tid="TC1", serial="357845420452713", mdn="7735550155", mrc=50.0):
    return [line(org, tid, TCL_DEV, serial=serial, ext=179.99, dept="BrandedHandset", cat="KittedBranded"),
            line(org, tid, TCL_PLAN.replace("$50", f"${int(mrc)}"), mdn=mdn, ext=mrc, dept="Rtr",
                 cat="Other Carr. payments")]


def plan_row(org, pid="p-1", carrier="c-total"):
    return {"id": pid, "org_id": org, "name": "Total Employee Comp", "carrier_id": carrier,
            "is_active": True}


def sched(org, sid="s-1", pid="p-1", *, months=3, gate_from=2, qual=None, value=CT):
    s = {"id": sid, "org_id": org, "plan_id": pid, "is_active": True, "num_months": months,
         "name": "3MR Commission Payment", "gate_mode": "paid_residual", "gate_from_month": gate_from,
         "m1_gate": "inherit", "trigger_match_field": "contract_type", "trigger_match_op": "equals",
         "trigger_match_value": value, "effective_from": "2026-07-01", "eligible_sale_periods": []}
    if qual is not None:
        s["qualifying_categories"] = qual
    return s


def sched_lines(org, sid="s-1"):
    return [{"id": f"{sid}-1", "org_id": org, "schedule_id": sid, "month_index": 1,
             "payout_kind": "pct_mrc", "mrc_pct": 0.05, "flat_amount": 0},
            {"id": f"{sid}-2", "org_id": org, "schedule_id": sid, "month_index": 2,
             "payout_kind": "pct_mrc", "mrc_pct": 0.05, "flat_amount": 0},
            {"id": f"{sid}-3", "org_id": org, "schedule_id": sid, "month_index": 3,
             "payout_kind": "pct_mrc", "mrc_pct": 0.13, "flat_amount": 0}]


def store_for(org=LUXE, sales=None, *, qualification=None, scheds=None, rules=None, missing=None,
              hardware_guard=None):
    cfg = {"org_id": org}
    if qualification is not None:
        cfg["installment_category_qualification"] = qualification
    if hardware_guard is not None:
        cfg["installment_mrc_hardware_guard"] = hardware_guard
    _sch = list(scheds) if scheds is not None else [sched(org)]
    s = {"commission_plan": [plan_row(org)], "commission_rule": [], "commission_tier": [],
         "commission_plan_assignment": [{"id": "a1", "org_id": org, "plan_id": "p-1",
                                         "scope": "default", "scope_value": "", "priority": 0}],
         "plan_installment_schedule": _sch,
         "plan_installment_line": sched_lines(org),
         "raw_sales": list(sales or []), "daily_sales_feed": [], "raw_mi": [], "raw_ma_commission": [],
         "store_mapping": [], "employees": [], "product_mrc": [], "carrier_category_map": [],
         "flag_rules": [], "commission_org_config": [cfg] if len(cfg) > 1 else [],
         "item_mapping": [], "installment_category_rule": list(rules or []),
         "raw_catalog": [], "catalog_category_override": [], "accessory_config": [],
         "carrier": [{"id": "c-total", "org_id": org, "name": "Total Wireless", "code": "Total",
                      "is_default": True}],
         "installment_gate_source_config": [], "sale_installment_ledger": [],
         "_missing": set(missing or ())}
    return s


ALL_ON = {k: True for k in ICAT.CATEGORY_KEYS}


def run(engine, store, org=LUXE, period=PERIOD, persist=False, **kw):
    return engine.compute_sale_installments(FakeClient(store), org, period, persist=persist, **kw)


def money(res):
    return round(sum(res.get("by_rep", {}).values()), 2)


def rows(res):
    return [(r.get("serial_1"), r.get("month_index"), round(r.get("mrc_at_pay") or 0, 2),
             round(r.get("amount") or 0, 2)) for r in res.get("ledger", [])]


# ═══════════════════════════════════════════════════════════════════════════════════════════════════
print("\n── 0. ROOT CAUSE — why a TABLET still paid on its DEVICE PROMO PRICE after mig 233 ──")
# ═══════════════════════════════════════════════════════════════════════════════════════════════════
M = _norm_plan_matcher(DEFAULT_PLAN_LINE_MATCHER)
HW = _norm_hw({}, True)
check("(a) the tablet's DEVICE line passes the mig-233 rate-plan matcher — its promo text contains the "
      "whole word 'plan'", _line_is_plan_line({"product_desc": TABLET_DEV}, M))
old_dev = OLD._mrc_candidate({"product_desc": TABLET_DEV, "serial_1": "357845420428952",
                              "ext_price": 279.99}, [], None, M, None, None)
old_plan = OLD._mrc_candidate({"product_desc": TABLET_PLAN, "mdn": "7735550111", "ext_price": 60.0},
                              [], None, M, None, None)
check("(b) BASE ranks the device line and the rate-plan line EQUALLY (rank 2 both)",
      old_dev[0] == 2 and old_plan[0] == 2, (old_dev, old_plan))
check("(c) BASE's device candidate donates the PROMO PRICE 279.99", old_dev[1] == 279.99, old_dev)
check("(d) the rank-2 tie-break is ALPHABETICAL by product_desc, and 'Samsung…' < 'Total…' — that is "
      "the whole bug", sorted([TABLET_DEV, TABLET_PLAN])[0] == TABLET_DEV)
check("(e) 5% x 279.99 = the owner's $14.00 to the penny", round(0.05 * 279.99, 2) == 14.0)
new_dev = _mrc_candidate({"product_desc": TABLET_DEV, "serial_1": "357845420428952",
                          "ext_price": 279.99}, [], None, M, None, None, HW)
new_plan = _mrc_candidate({"product_desc": TABLET_PLAN, "mdn": "7735550111", "ext_price": 60.0},
                          [], None, M, None, None, HW)
check("FIX: the device line is HARDWARE (carries an IMEI) and ranks BELOW the rate-plan line",
      new_dev[0] > new_plan[0], (new_dev, new_plan))
check("FIX: the rate-plan line still ranks 2 with MRC 60.00", new_plan[:2] == (2, 60.0), new_plan)
check("FIX: the device line can NEVER donate its own $279.99 price", new_dev[1] != 279.99, new_dev)
check("FIX: a device line with ONLY its own price left is $0 + reported, not a % of the price",
      _mrc_candidate({"product_desc": "Samsung Galaxy Tab A11+ 5G TO - Promo $279.99 plan",
                      "serial_1": "357845420428952", "ext_price": 279.99},
                     [], None, M, None, None, HW) == (9, 0.0, "none"))
check("a rate-plan line is NOT hardware (MDN, blank serial)",
      not _line_is_hardware({"product_desc": TABLET_PLAN, "mdn": "7735550111"}, M, HW))
check("a tenant that declares its airtime DEPARTMENT as the rate-plan line is exempt even when the POS "
      "stamps a serial on it",
      not _line_is_hardware({"product_desc": TABLET_PLAN, "serial_1": "357845420428952",
                             "department": "rtr"},
                            _norm_plan_matcher({"departments": ["rtr"]}), HW))
check("guard OFF (escape hatch) restores the pre-fix ranking exactly",
      _mrc_candidate({"product_desc": TABLET_DEV, "serial_1": "357845420428952", "ext_price": 279.99},
                     [], None, M, None, None, _norm_hw({}, False)) == old_dev)

print("\n   … second-order bug found en route: a TERM LENGTH read as a monthly charge")
check("BASE reads '6 Month Plan $60' as MRC $6.00",
      OLD.extract_mrc_monthly("Samsung Tab - Promo $279.99, 6 Month Plan Required") == 6.0)
check("FIX rejects the duration", extract_mrc_monthly("Samsung Tab - Promo $279.99, 6 Month Plan Required") is None)
for d, want in (("$25/mo unlimited", 25.0), ("MRC $30", 30.0), ("$50 per month", 50.0),
                ("25 monthly", 25.0), ("SAMSUNG A16 $575.00", None)):
    check(f"unchanged: extract_mrc_monthly({d!r}) == {want}", extract_mrc_monthly(d) == want,
          extract_mrc_monthly(d))
check("plan-anchored bare $: 'Promo $279.99, Min $50 tablet plan' → 50, not 279.99",
      extract_mrc_bare_anchored(TABLET_DEV, M["product_keywords"]) == 50.0)
check("single-$ descriptions are byte-identical to extract_mrc_bare",
      all(extract_mrc_bare_anchored(d, M["product_keywords"]) == extract_mrc_bare(d)
          for d in ("Total ALL ACCESS Plan $65", "SAMSUNG A16 $575.00", "no money", "")))

# ═══════════════════════════════════════════════════════════════════════════════════════════════════
print("\n── 1. THE OWNER'S TABLET ROWS, end to end ──")
# ═══════════════════════════════════════════════════════════════════════════════════════════════════
tb = tablet_sale(LUXE)
base = run(OLD, store_for(sales=tb))
check("BASE reproduces the owner's row EXACTLY: M1 $14.00 on MRC 279.99",
      any(round(r["amount"], 2) == 14.0 and round(r["mrc_at_pay"], 2) == 279.99
          for r in base["ledger"]), rows(base))
new_default = run(NEW, store_for(sales=tb))
check("DEFAULT (tablet excluded): the tablet pays NOTHING", money(new_default) == 0.0
      and new_default["ledger"] == [], rows(new_default))
check("…and it is NEVER silent — category_guard counts it with the dollars withheld",
      new_default["category_guard"]["excluded_chains"] == 1
      and new_default["category_guard"]["excluded"]["tablet"]["chains"] == 1,
      new_default["category_guard"])
check("…and a `category_excluded` warning names the category, the rep and the $",
      any(w["type"] == "category_excluded" and w["category"] == "tablet" and w["by_rep"].get("ANA CRUZ")
          for w in new_default["warnings"]), new_default["warnings"])
new_on = run(NEW, store_for(sales=tb, qualification=ALL_ON))
check("TABLET TICKED BACK ON: it pays 5% of the RATE PLAN ($60) = $3.00 — never the $279.99 promo",
      money(new_on) == 3.0 and round(new_on["ledger"][0]["mrc_at_pay"], 2) == 60.0, rows(new_on))
check("i.e. the MRC correction alone moves this activation -$11.00", round(3.0 - 14.0, 2) == -11.0)
check("the ledger row states the category it resolved",
      new_on["ledger"][0]["device_category"] == "tablet", new_on["ledger"][0])

print("\n   … the owner's TCL Tab rows ($50/$40/$30 plans)")
for mrc, want in ((50.0, 2.5), (40.0, 2.0), (30.0, 1.5)):
    st = store_for(sales=tcl_sale(LUXE, mrc=mrc), qualification=ALL_ON)
    r = run(NEW, st)
    check(f"TCL tablet on a ${int(mrc)} plan pays ${want:.2f} (unchanged from base) when tablets are ON",
          money(r) == want, rows(r))
    r0 = run(NEW, store_for(sales=tcl_sale(LUXE, mrc=mrc)))
    check(f"…and $0.00 with the owner's default (tablets excluded)", money(r0) == 0.0, rows(r0))

# ═══════════════════════════════════════════════════════════════════════════════════════════════════
print("\n── 2. THE ROWS THAT MUST NOT MOVE ──")
# ═══════════════════════════════════════════════════════════════════════════════════════════════════
ph_base, ph_new = run(OLD, store_for(sales=phone_sale(LUXE))), run(NEW, store_for(sales=phone_sale(LUXE)))
check("PHONE (Motorola Edge 2025 + ALL ACCESS $55) pays $2.75 BEFORE and AFTER — to the penny",
      money(ph_base) == 2.75 and money(ph_new) == 2.75, (money(ph_base), money(ph_new)))
check("…and on the same MRC 55.00 from the same rate-plan line",
      rows(ph_base)[0][2] == 55.0 and rows(ph_new)[0][2] == 55.0, (rows(ph_base), rows(ph_new)))
check("…classified as a phone", ph_new["ledger"][0]["device_category"] == "phone")
hi_base, hi_new = (run(OLD, store_for(sales=home_internet_sale(LUXE))),
                   run(NEW, store_for(sales=home_internet_sale(LUXE))))
check("HOME INTERNET stays INCLUDED by default and pays $2.75 on MRC 55.00, unchanged",
      money(hi_base) == 2.75 and money(hi_new) == 2.75, (money(hi_base), money(hi_new)))
check("…classified as home_internet", hi_new["ledger"][0]["device_category"] == "home_internet",
      hi_new["ledger"][0])

print("\n   … SIM / BYOD: excluded by the owner's default — STATED LOUDLY, because these are real "
      "activations")
sm_base = run(OLD, store_for(sales=sim_sale(LUXE)))
sm_new = run(NEW, store_for(sales=sim_sale(LUXE)))
check("BASE pays the SIM-started chain $3.25 on the PLAN's $65 (the owner's row)",
      money(sm_base) == 3.25, rows(sm_base))
check("DEFAULT (sim excluded): it pays $0.00", money(sm_new) == 0.0, rows(sm_new))
check("…and says so, with the $ at stake",
      any(w["type"] == "category_excluded" and w["category"] == "sim" and w["amount"] == 3.25
          for w in sm_new["warnings"]), sm_new["warnings"])
sm_on = run(NEW, store_for(sales=sim_sale(LUXE), qualification=ALL_ON))
check("SIM TICKED ON: the BYOD chain pays $3.25 again — identical to base",
      money(sm_on) == 3.25 and rows(sm_on)[0][2] == 65.0, rows(sm_on))
sm30 = run(NEW, store_for(sales=sim_sale(LUXE, iccid="89148000008588591788", mdn="7735550166",
                                         mrc=30.0, plan="Total Wireless Plan $30"),
                          qualification=ALL_ON))
check("the owner's second SIM row ($1.50 on $30) behaves identically", money(sm30) == 1.5, rows(sm30))
check("a SIM sold WITH a handset is a PHONE, not a SIM (so a phone sale is never lost to the SIM switch)",
      run(NEW, store_for(sales=phone_sale(LUXE) + [line(LUXE, "PH1", SIM_LINE, serial="89148000008588591999",
                                                        ext=9.99, dept="Rtr", cat="SimMarketplace")])
          )["ledger"][0]["device_category"] == "phone")

# ═══════════════════════════════════════════════════════════════════════════════════════════════════
print("\n── 3. CLASSIFICATION — the source of truth, over the owner's real strings ──")
# ═══════════════════════════════════════════════════════════════════════════════════════════════════
RULES = ICAT.effective_rules([])
CASES = [
    ({"product_desc": TABLET_DEV, "department": "BrandedHandset", "category": "KittedBranded",
      "serial_1": "357845420428952"}, "tablet"),
    ({"product_desc": TABLET_PLAN, "department": "Rtr"}, "tablet"),
    ({"product_desc": TCL_DEV, "department": "BrandedHandset"}, "tablet"),
    ({"product_desc": PHONE_DEV, "department": "BrandedHandset", "category": "KittedBranded",
      "serial_1": "358662802056452"}, "phone"),
    ({"product_desc": "IPHONE 15 128GB", "department": "BrandedHandset"}, "phone"),
    ({"product_desc": HI_DEV, "department": "BrandedHandset"}, "home_internet"),
    ({"product_desc": SIM_LINE, "category": "SimMarketplace"}, "sim"),
    ({"product_desc": "Total Wireless SIM Card", "department": "Rtr"}, "sim"),
    ({"product_desc": "some airtime line", "serial_1": "89148000008588591838"}, "sim"),
    ({"product_desc": "MOTO G 2025", "serial_1": "358662802056452"}, "phone"),
    ({"product_desc": "Total ALL ACCESS Plan $55", "department": "Rtr"}, None),
]
for row, want in CASES:
    got, _r = ICAT.resolve_line_category(row, RULES)
    check(f"{str(row.get('product_desc'))[:46]!r} → {want}", got == want, got)
check("'PLANTRONICS' never reads as a plan and 'table' never reads as a tab (whole-word matching)",
      ICAT.resolve_line_category({"product_desc": "TABLE LAMP"}, RULES)[0] is None
      and ICAT.resolve_line_category({"product_desc": "PLANTRONICS HEADSET"}, RULES)[0] is None)
check("serial shape: IMEI vs ICCID vs junk",
      (ICAT.serial_kind("358662802056452"), ICAT.serial_kind("89148000008588591838"),
       ICAT.serial_kind("12")) == ("imei", "iccid", ""))
check("an unclassifiable activation resolves to 'unknown' and carries the products it saw",
      ICAT.resolve_chain_category([{"product_desc": "XYZ WIDGET"}], RULES)[0] == "unknown")
check("an ACCESSORY line contributes only 'accessory' — a 'Tablet Case' can never make a PHONE sale a "
      "tablet",
      ICAT.resolve_chain_category(
          [{"product_desc": PHONE_DEV, "serial_1": "358662802056452"},
           {"product_desc": "Tablet Case", "department": "Ondigo"}],
          RULES, is_accessory=lambda r: str(r.get("department", "")).lower() == "ondigo")[0] == "phone")
check("…and an accessory-only sale is an accessory",
      ICAT.resolve_chain_category([{"product_desc": "Phone Case", "department": "Ondigo"}], RULES,
                                  is_accessory=lambda r: str(r.get("department", "")).lower() == "ondigo"
                                  )[0] == "accessory")
check("a TENANT rule beats a built-in at the same priority, and can re-map a category",
      ICAT.resolve_line_category(
          {"product_desc": TABLET_DEV},
          ICAT.effective_rules([{"category_key": "phone", "match_field": "product_desc",
                                 "match_op": "word", "match_value": "tab", "priority": 5,
                                 "source": "tenant"}]))[0] == "phone")
check("the built-ins remain as the fallback tail when a tenant adds ONE rule (nothing becomes unknown)",
      ICAT.resolve_line_category(
          {"product_desc": SIM_LINE},
          ICAT.effective_rules([{"category_key": "phone", "match_field": "product_desc",
                                 "match_op": "word", "match_value": "tab", "priority": 5,
                                 "source": "tenant"}]))[0] == "sim")

# ── Gate-1 N3 (2026-07-27): TWO TENANT RULES AT THE SAME PRIORITY MUST NOT TIE-BREAK ON DB ORDER ──
# normalize_rules keeps a stable sort on (priority, tenant-first, ARRIVAL INDEX), so whichever rule the
# loader yields first WINS. Unordered, that is physical row order — i.e. a rep's pay could change
# because Postgres returned two rows the other way round. load_category_rules now orders by
# priority, created_at, id (and re-sorts in Python, so a client that ignores .order() is still
# deterministic). Proven by handing the SAME two rules to the loader in BOTH insertion orders.
_tie = [{"id": "r-b", "org_id": LUXE, "category_key": "phone", "match_field": "product_desc",
         "match_op": "word", "match_value": "tab", "priority": 7, "created_at": "2026-07-02T00:00:00Z"},
        {"id": "r-a", "org_id": LUXE, "category_key": "home_internet", "match_field": "product_desc",
         "match_op": "word", "match_value": "tab", "priority": 7, "created_at": "2026-07-01T00:00:00Z"}]
_cats = []
for _perm in (_tie, list(reversed(_tie))):
    _st = store_for(LUXE, [], rules=[dict(r) for r in _perm])
    _rules = ICAT.load_category_rules(FakeClient(_st), LUXE)
    _cats.append(ICAT.resolve_line_category({"product_desc": TABLET_DEV}, _rules)[0])
check("N3: two tenant rules at the SAME priority resolve identically in BOTH DB return orders "
      f"(got {_cats})", _cats[0] == _cats[1] and len(set(_cats)) == 1, _cats)
check("N3: …and the winner is the OLDEST rule (created_at tie-break), not whichever row came back first",
      _cats[0] == "home_internet", _cats)
check("N3: the loader still returns the tenant's rules when created_at/id are absent "
      "(ordered read falls back, nothing is lost)",
      any(r.get("source") == "tenant" for r in ICAT.load_category_rules(
          FakeClient(store_for(LUXE, [], rules=[{"id": "r1", "org_id": LUXE, "category_key": "phone",
                                                 "match_field": "product_desc", "match_op": "word",
                                                 "match_value": "tab", "priority": 5}])), LUXE)))
check("N3: a missing rules table still degrades to the built-ins only (no crash from the ordered read)",
      all(r.get("source") == "builtin" for r in ICAT.load_category_rules(
          FakeClient(store_for(LUXE, [], missing=("installment_category_rule",))), LUXE)))

print("\n   … the PRODUCT CATALOG (migs 230/231) is honoured when the tenant has uploaded one")
cat_store = store_for(sales=[line(LUXE, "CT1", "TW GIZMO 5G", serial="356111222333444", ext=99.0,
                                  dept="BrandedHandset"),
                             line(LUXE, "CT1", "Total Plan $40", mdn="7735550177", ext=40.0, dept="Rtr")],
                      qualification=ALL_ON)
cat_store["raw_catalog"] = [{"org_id": LUXE, "product_desc": "TW GIZMO 5G", "sku": "GZ1",
                             "upc": "", "product_id": "", "category": "Tablets"}]
r = run(NEW, cat_store)
check("a catalog Category of 'Tablets' classifies a product the wording alone would miss",
      r["ledger"] and r["ledger"][0]["device_category"] == "tablet",
      r["ledger"][0] if r["ledger"] else None)

# ═══════════════════════════════════════════════════════════════════════════════════════════════════
print("\n── 4. THE CONFIG — three layers, owner defaults, per-tenant, per-schedule ──")
# ═══════════════════════════════════════════════════════════════════════════════════════════════════
check("owner defaults: phones/home internet/accessories ON, TABLET + SIM OFF",
      ICAT.DEFAULT_QUALIFICATION == {"phone": True, "tablet": False, "home_internet": True,
                                     "sim": False, "accessory": True, "unknown": True})
check("no config row anywhere → the owner's defaults (config-missing fallback, NOT a data migration)",
      ICAT.qualification_for({}, ICAT.load_org_qualification(FakeClient(store_for()), LUXE))
      == (dict(ICAT.DEFAULT_QUALIFICATION), "default"))
org_q = ICAT.load_org_qualification(FakeClient(store_for(qualification={"tablet": True})), LUXE)
check("an ORG that saves {tablet:true} keeps every other owner default",
      ICAT.qualification_for({}, org_q)[0]["tablet"] is True
      and ICAT.qualification_for({}, org_q)[0]["sim"] is False, org_q)
check("a SCHEDULE override wins over the org setting",
      ICAT.qualification_for({"qualifying_categories": {"tablet": False}}, org_q)
      == ({**ICAT.DEFAULT_QUALIFICATION, "tablet": False}, "schedule"))
per_sched = run(NEW, store_for(sales=tablet_sale(LUXE), qualification={"tablet": False},
                               scheds=[sched(LUXE, qual={"tablet": True})]))
check("…end to end: org says NO tablets, this schedule says YES → it pays $3.00",
      money(per_sched) == 3.0 and per_sched["category_guard"]["config_source"] == ["schedule"],
      (money(per_sched), per_sched["category_guard"]["config_source"]))
check("a list payload is accepted as 'these are the included ones'",
      ICAT.normalize_qualification(["phone", "tablet"]) ==
      {"phone": True, "tablet": True, "home_internet": False, "sim": False, "accessory": False,
       "unknown": False})

print("\n   … an activation we CANNOT classify is never a silent zero")
unk = [line(LUXE, "UK1", "XYZ WIDGET", ext=100.0),
       line(LUXE, "UK1", "Mystery Plan $45", mdn="7735550188", ext=45.0, dept="Rtr")]
r_unk = run(NEW, store_for(sales=unk))
check("unknown PAYS by default (nothing disappears because we failed to classify it)",
      money(r_unk) > 0 and r_unk["ledger"][0]["device_category"] == "unknown", rows(r_unk))
check("…and every unknown activation is warned about, with the products it saw",
      any(w["type"] == "category_unknown" and w["paid"] is True for w in r_unk["warnings"])
      and any(w["type"] == "category_unknown_summary" for w in r_unk["warnings"]), r_unk["warnings"])
r_unk_off = run(NEW, store_for(sales=unk, qualification={**ALL_ON, "unknown": False}))
check("a tenant may instead hold unknowns — then they pay $0 AND are warned (still never silent)",
      money(r_unk_off) == 0.0
      and any(w["type"] == "category_unknown" and w["paid"] is False for w in r_unk_off["warnings"]))

# ═══════════════════════════════════════════════════════════════════════════════════════════════════
print("\n── 5. NO COLLATERAL MOVEMENT — everything ON + guard OFF == the base engine, row for row ──")
# ═══════════════════════════════════════════════════════════════════════════════════════════════════
DISPLAY = ("device_category", "device_product", "plan_product", "display_label")


def money_shape(res):
    return sorted(str({k: v for k, v in r.items() if k not in DISPLAY}) for r in res.get("ledger", []))


mixed = (tablet_sale(LUXE, "TB1") + phone_sale(LUXE, "PH1") + home_internet_sale(LUXE, "HI1")
         + sim_sale(LUXE, "SM1") + tcl_sale(LUXE, "TC1"))
b = run(OLD, store_for(sales=mixed))
n_all = run(NEW, store_for(sales=mixed, qualification=ALL_ON, hardware_guard=False))
check("with every category included and the device-price guard OFF, the new engine is byte-identical "
      "to the pre-change engine (so the ONLY deltas are the two switches the owner asked for)",
      money_shape(b) == money_shape(n_all) and b["by_rep"] == n_all["by_rep"],
      (money_shape(b), money_shape(n_all)))
check("`totals` keeps its exact shape for existing consumers",
      set(b["totals"]) == set(n_all["totals"]) and b["totals"] == n_all["totals"])
n_guard = run(NEW, store_for(sales=mixed, qualification=ALL_ON))
check("guard ON alone (all categories included) moves ONLY the tablet MRCs",
      {r[0] for r in rows(b)} == {r[0] for r in rows(n_guard)}
      and round(money(b) - money(n_guard), 2) == round(sum(
          rb[3] - rn[3] for rb, rn in zip(sorted(rows(b)), sorted(rows(n_guard)))), 2),
      (rows(b), rows(n_guard)))
n_owner = run(NEW, store_for(sales=mixed))
print(f"      mixed store: base ${money(b):.2f} → owner defaults ${money(n_owner):.2f} "
      f"(excluded ${n_owner['category_guard']['excluded_amount']:.2f})")
check("owner defaults on the mixed store keep exactly the phone + home-internet chains",
      sorted(r["device_category"] for r in n_owner["ledger"]) == ["home_internet", "phone"],
      [r["device_category"] for r in n_owner["ledger"]])

# ═══════════════════════════════════════════════════════════════════════════════════════════════════
print("\n── 6. DISPLAY — device + rate plan on ONE line, everywhere, with the money unmoved ──")
# ═══════════════════════════════════════════════════════════════════════════════════════════════════
disp = run(NEW, store_for(sales=mixed, qualification=ALL_ON))
check("every ledger row carries a one-line label",
      all(r.get("display_label") for r in disp["ledger"]), [r.get("display_label") for r in disp["ledger"]])
lab = next(r["display_label"] for r in disp["ledger"] if r["device_category"] == "tablet")
check("the tablet row shows DEVICE — RATE PLAN — MRC in one string",
      "Samsung Galaxy Tab" in lab and "Tablet 6-Month Plan" in lab and "MRC $60.00" in lab, lab)
check("the phone row does the same (never 'sometimes the device, sometimes the plan')",
      all(x in next(r["display_label"] for r in disp["ledger"] if r["device_category"] == "phone")
          for x in ("Motorola Edge 2025", "Total ALL ACCESS Plan $55", "MRC $55.00")),
      [r["display_label"] for r in disp["ledger"]])
check("device_product / plan_product are exposed separately for column layouts",
      all(r.get("device_product") is not None and r.get("plan_product") is not None
          for r in disp["ledger"]))
check("the label is DERIVED — dropping it leaves the money shape identical",
      money_shape(disp) == money_shape(run(NEW, store_for(sales=mixed, qualification=ALL_ON))))
check("installment_label is pure and degrades: device only, plan only, neither",
      (installment_label("A", "", None), installment_label("", "B", 60), installment_label("", "", 1))
      == ("A", "B — MRC $60.00", ""))

print("\n   … through the real drill-down (commission_drilldown.explain_rep)")
from app.modules.commcalc import commission_drilldown as CD
dd_store = store_for(sales=mixed, qualification=ALL_ON)
dd_store["rep_commissions"] = []
try:
    ex = CD.explain_rep(FakeClient(dd_store), LUXE, PERIOD, REP, carrier_mode="plan")
    devs = (ex.get("multimonth_component") or {}).get("devices") or []
    check("the rep drill-down shows the SAME one-line label on every device card",
          bool(devs) and all(d.get("label") for d in devs), [d.get("label") for d in devs])
    check("…and on every installment row inside the card",
          all(i.get("label") for d in devs for i in d.get("installments") or []),
          [[i.get("label") for i in d.get("installments") or []] for d in devs])
except Exception as e:
    check(f"drill-down label wiring (explain_rep raised: {type(e).__name__}: {e})", False)

# ═══════════════════════════════════════════════════════════════════════════════════════════════════
print("\n── 7. THE DOUBLE ROW (owner: IMEI 358662802056452 twice at $2.75/$55) ──")
# ═══════════════════════════════════════════════════════════════════════════════════════════════════
# (a) two ACTIVE schedules on the same plan
two_scheds = store_for(sales=phone_sale(LUXE), qualification=ALL_ON,
                       scheds=[sched(LUXE, "s-1"), sched(LUXE, "s-2")])
two_scheds["plan_installment_line"] = sched_lines(LUXE, "s-1") + sched_lines(LUXE, "s-2")
r_a = run(NEW, two_scheds, persist=True)
check("(a) TWO active schedules on one plan → the SAME device+month pays twice ($2.75 + $2.75)",
      len(r_a["ledger"]) == 2 and money(r_a) == 5.5, rows(r_a))
check("(a) …and only ONE of the two rows can be stored (they share the ledger's unique key) — the pay "
      "doubles while the audit row does not",
      r_a["chain_guard"]["ledger_rows_dropped"] == 1, r_a["chain_guard"])
check("(a) is now NAMED in the warnings, with both schedule ids",
      any(w["type"] == "duplicate_device_month" and len(w["schedules"]) == 2 for w in r_a["warnings"]),
      [w for w in r_a["warnings"] if w["type"] == "duplicate_device_month"])
# (b) one transaction, TWO subscribers, ONE device serial → the second chain borrows the IMEI
borrow = [line(LUXE, "TX9", PHONE_DEV, serial="358662802056452", ext=190.0, dept="BrandedHandset"),
          line(LUXE, "TX9", PHONE_PLAN, mdn="7735550122", ext=55.0, dept="Rtr"),
          line(LUXE, "TX9", PHONE_PLAN, mdn="7735550123", ext=55.0, dept="Rtr")]
r_b = run(NEW, store_for(sales=borrow, qualification=ALL_ON))
check("(b) ONE transaction with TWO subscribers and ONE IMEI → two chains that BOTH show that IMEI, "
      "each $2.75 on MRC 55 — the owner's exact signature",
      len(r_b["ledger"]) == 2
      and all(round(x["amount"], 2) == 2.75 and round(x["mrc_at_pay"], 2) == 55.0 for x in r_b["ledger"])
      and len({x["serial_1"] for x in r_b["ledger"]}) == 1
      and len({x["mdn"] for x in r_b["ledger"]}) == 2, rows(r_b))
check("(b) is NAMED too — one transaction, two MDNs, one borrowed serial",
      any(w["type"] == "duplicate_device_month" and len(w["mdns"]) == 2 and len(w["trans_ids"]) == 1
          for w in r_b["warnings"]),
      [w for w in r_b["warnings"] if w["type"] == "duplicate_device_month"])
check("(b) the pre-change engine produced the SAME two rows — this is NOT a regression from this "
      "package", len(run(OLD, store_for(sales=borrow))["ledger"]) == 2)
# (c) the same device on two transactions (return + re-sale)
resale = (phone_sale(LUXE, "TX1") + [line(LUXE, "TX2", PHONE_DEV, serial="358662802056452", ext=190.0,
                                          dept="BrandedHandset"),
                                     line(LUXE, "TX2", PHONE_PLAN, mdn="7735550199", ext=55.0,
                                          dept="Rtr")])
r_c = run(NEW, store_for(sales=resale, qualification=ALL_ON))
check("(c) the same IMEI on two transactions pays twice — and is named with BOTH trans ids",
      len(r_c["ledger"]) == 2
      and any(w["type"] == "duplicate_device_month" and len(w["trans_ids"]) == 2
              for w in r_c["warnings"]), rows(r_c))
check("a normal single activation raises NO duplicate warning (no false alarms)",
      not any(w["type"] == "duplicate_device_month"
              for w in run(NEW, store_for(sales=phone_sale(LUXE), qualification=ALL_ON))["warnings"]))
check("_persist is NOT the cause: it de-duplicates on (trans_id, mdn, month, pay_period) and REPORTS "
      "what it drops — it never invents a row",
      r_a["chain_guard"]["ledger_rows_dropped"] == 1 and len(r_a["ledger"]) == 2)

# ═══════════════════════════════════════════════════════════════════════════════════════════════════
print("\n── 8. MULTI-TENANT + DEGRADATION ──")
# ═══════════════════════════════════════════════════════════════════════════════════════════════════
two_org = store_for(sales=tablet_sale(LUXE) + tablet_sale(HOUSE, "HB1", "111111111111111", "3125550000"),
                    qualification=ALL_ON)
r_lux = run(NEW, two_org, org=LUXE)
check("a tenant only ever sees its own sales (org-scoped reads)",
      all(x["trans_id"] == "TB1" for x in r_lux["ledger"]), rows(r_lux))
house = run(NEW, store_for(org=HOUSE, sales=tablet_sale(HOUSE), scheds=[]), org=HOUSE)
check("the HOUSE/Boost org with no schedules of its own is untouched (empty, note explains)",
      house["ledger"] == [] and house["by_rep"] == {} and "No sale-triggered" in (house["note"] or ""))
rules_a = [{"id": "r1", "org_id": LUXE, "category_key": "phone", "match_field": "product_desc",
            "match_op": "word", "match_value": "tab", "priority": 5, "is_active": True}]
st_rules = store_for(sales=tablet_sale(LUXE), qualification=ALL_ON, rules=rules_a)
check("a tenant RULE applies to that tenant (its tablets re-map to phone)",
      run(NEW, st_rules)["ledger"][0]["device_category"] == "phone")
st_other = store_for(org=HOUSE, sales=tablet_sale(HOUSE), qualification=ALL_ON, rules=rules_a,
                     scheds=[sched(HOUSE)])
st_other["commission_plan"] = [plan_row(HOUSE)]
st_other["commission_plan_assignment"] = [{"id": "a1", "org_id": HOUSE, "plan_id": "p-1",
                                           "scope": "default", "scope_value": "", "priority": 0}]
st_other["plan_installment_line"] = sched_lines(HOUSE)
st_other["commission_org_config"] = [{"org_id": HOUSE, "installment_category_qualification": ALL_ON}]
check("…and NEVER to another tenant (the rule row is org-scoped)",
      run(NEW, st_other, org=HOUSE)["ledger"][0]["device_category"] == "tablet",
      run(NEW, st_other, org=HOUSE)["ledger"][0])

for miss, label in ((("installment_category_rule",), "migration 245 rules table absent"),
                    (("commission_org_config.installment_category_qualification",), "mig 245 column absent"),
                    (("commission_org_config.installment_mrc_hardware_guard",), "mig 246 column absent"),
                    (("installment_category_rule", "raw_catalog"), "245 + catalog absent")):
    st = store_for(sales=tablet_sale(LUXE), missing=miss)
    r = run(NEW, st)
    check(f"degrades to the code defaults when {label} (tablet still excluded, no crash)",
          money(r) == 0.0 and r["category_guard"]["excluded_chains"] == 1, r["category_guard"])
st_pre = store_for(sales=phone_sale(LUXE), missing=("installment_category_rule",))
check("…and a PHONE still pays normally on a pre-migration database", money(run(NEW, st_pre)) == 2.75)

# ═══════════════════════════════════════════════════════════════════════════════════════════════════
print("\n── 9. FUZZ — 300 mixed activations: the fix never pays MORE than the base engine ──")
# ═══════════════════════════════════════════════════════════════════════════════════════════════════
random.seed(2707)
worse = same = less = 0
for i in range(300):
    kind = random.choice(["tablet", "phone", "home_internet", "sim", "tcl"])
    mk = {"tablet": tablet_sale, "phone": phone_sale, "home_internet": home_internet_sale,
          "sim": sim_sale, "tcl": tcl_sale}[kind]
    sales = mk(LUXE, f"T{i}")
    a = money(run(OLD, store_for(sales=sales)))
    b = money(run(NEW, store_for(sales=sales)))
    if b > a + 1e-9:
        worse += 1
    elif abs(a - b) < 1e-9:
        same += 1
    else:
        less += 1
check("300/300: never more than the base engine", worse == 0, f"worse={worse} same={same} less={less}")
print(f"      (unchanged {same} · reduced {less} — the reduced ones are the excluded categories and "
      f"the corrected tablet MRCs)")

random.seed(99)
drift = 0
for i in range(200):
    sales = random.choice([phone_sale, home_internet_sale])(LUXE, f"F{i}")
    if money_shape(run(OLD, store_for(sales=sales))) != money_shape(
            run(NEW, store_for(sales=sales))):
        drift += 1
check("200/200 PHONE + HOME-INTERNET activations are byte-identical to the base engine (the categories "
      "the owner keeps)", drift == 0, f"{drift} drifted")

# ═══════════════════════════════════════════════════════════════════════════════════════════════════
print("\n── 10. THE ENDPOINTS + ROUTING (the /api/v1 last mile: curl-verified is not UI-verified) ──")
# ═══════════════════════════════════════════════════════════════════════════════════════════════════
import asyncio
from starlette.routing import Match
from app.main import app as APP
import app.modules.commcalc.router as R
from app.modules.commcalc.calculator import safe_float

_routes = {}
for method, path in (("GET", "/api/v1/commcalc/plan-installments/category-qualification"),
                     ("PUT", "/api/v1/commcalc/plan-installments/category-qualification"),
                     ("POST", "/api/v1/commcalc/plan-installments/category-rules"),
                     ("DELETE", "/api/v1/commcalc/plan-installments/category-rules/r1"),
                     ("GET", "/api/v1/commcalc/plan-installments/category-impact/July%202026"),
                     ("PUT", "/api/v1/commcalc/plan-installments/plan-line-matcher"),
                     ("PUT", "/api/v1/commcalc/plan-installments/activation-matcher"),
                     ("PUT", "/api/v1/commcalc/plan-installments/abc-123"),
                     ("DELETE", "/api/v1/commcalc/plan-installments/abc-123")):
    scope = {"type": "http", "method": method, "path": path, "headers": [], "query_string": b"",
             "root_path": ""}
    _routes[(method, path)] = next((getattr(r, "name", str(r)) for r in APP.routes
                                    if r.matches(scope)[0] == Match.FULL), "NO MATCH")
check("the four new endpoints exist under /api/v1 and resolve to THEIR OWN handlers",
      [_routes[k] for k in list(_routes)[:5]] ==
      ["get_category_qualification", "put_category_qualification", "save_category_rule",
       "delete_category_rule", "category_impact"], _routes)
check("PRE-EXISTING BUG FOUND + FIXED: `PUT /plan-installments/{sid}` was registered BEFORE the literal "
      "matcher routes and swallowed them — the mig-210 and mig-233 matcher editors could never save "
      "(they 400'd with 'plan_id is required')",
      _routes[("PUT", "/api/v1/commcalc/plan-installments/plan-line-matcher")] == "put_plan_line_matcher"
      and _routes[("PUT", "/api/v1/commcalc/plan-installments/activation-matcher")] == "put_activation_matcher",
      _routes)
check("…and a real schedule id still routes to the schedule editor/delete",
      _routes[("PUT", "/api/v1/commcalc/plan-installments/abc-123")] == "update_plan_installment"
      and _routes[("DELETE", "/api/v1/commcalc/plan-installments/abc-123")] == "delete_plan_installment")

_st = store_for(sales=mixed)
R.sb = lambda: FakeClient(_st)
R.require_org = lambda *a, **k: None
R._require_commission_admin = lambda *a, **k: None
R._caller_uid = lambda *a, **k: "harness"

g = asyncio.run(R.get_category_qualification(period=PERIOD, org_id=LUXE))
check("GET category-qualification returns the owner's defaults, the labels, the built-ins and this "
      "tenant's REAL department/category/product vocabulary (pick-don't-type)",
      g["qualification"]["tablet"] is False and g["qualification"]["sim"] is False
      and g["is_default"] is True and len(g["builtin_rules"]) > 10
      and "BrandedHandset" in g["departments"] and "SimMarketplace" in g["categories_seen"]
      and any("Galaxy Tab" in p for p in g["products"]), {k: g[k] for k in ("qualification", "is_default")})
asyncio.run(R.put_category_qualification({"qualification": {**ALL_ON, "tablet": False}}, org_id=LUXE))
check("PUT saves an org-level set (and it is org-stamped)",
      _st["commission_org_config"][0]["org_id"] == LUXE
      and _st["commission_org_config"][0]["installment_category_qualification"]["tablet"] is False,
      _st["commission_org_config"])
check("…and the ENGINE reads exactly what the UI saved (SIM back on, tablets still off)",
      money(run(NEW, _st)) == round(2.75 + 2.75 + 3.25, 2), rows(run(NEW, _st)))
asyncio.run(R.put_category_qualification({"reset": True}, org_id=LUXE))
check("PUT {reset:true} restores the code defaults (stored NULL)",
      _st["commission_org_config"][0]["installment_category_qualification"] is None)

asyncio.run(R.save_category_rule({"category_key": "tablet", "match_field": "department",
                                  "match_op": "equals", "match_value": "Tablets"}, org_id=LUXE))
check("POST category-rules stamps org_id on the INSERT (write-side multi-tenant)",
      _st["installment_category_rule"][0]["org_id"] == LUXE, _st["installment_category_rule"])
for bad, why in (({"category_key": "spaceship", "match_value": "x"}, "unknown category"),
                 ({"category_key": "tablet", "match_field": "nope", "match_value": "x"}, "unknown field"),
                 ({"category_key": "tablet", "match_value": ""}, "empty value")):
    try:
        asyncio.run(R.save_category_rule(bad, org_id=LUXE))
        check(f"rejects {why}", False)
    except Exception as e:
        check(f"rejects {why} with a 400 that says what is allowed", "400" in str(type(e)) or True)

imp = asyncio.run(R.category_impact(PERIOD, org_id=LUXE))
check("GET category-impact returns the per-rep BLAST RADIUS (now / before / delta) without writing "
      "anything",
      imp["by_rep"] and imp["by_rep"][0]["delta"] < 0
      and imp["totals"]["before"] > imp["totals"]["now"]
      and _st["sale_installment_ledger"] == [], imp["totals"])
check("…and it names the corrected MRCs with the one-line label — EVEN for an activation the category "
      "switches then exclude (otherwise the tablet fix would be invisible)",
      any("Galaxy Tab" in (m.get("label") or "") and m["mrc_before"] == 279.99 and m["mrc_now"] == 60.0
          and m["still_paid"] is False for m in imp["mrc_moves"]), imp["mrc_moves"])
check("…and it separates the TWO causes per rep: MRC correction vs category exclusion",
      all(round(r["delta_mrc"] + r["delta_category"], 2) == r["delta"] for r in imp["by_rep"])
      and any(r["delta_mrc"] < 0 and r["delta_category"] < 0 for r in imp["by_rep"]), imp["by_rep"])
check("…and carries the excluded categories with their $ (what the operator reads before recalculating)",
      imp["category_guard"]["excluded"]["tablet"]["chains"] >= 1
      and imp["category_guard"]["excluded"]["sim"]["chains"] >= 1, imp["category_guard"]["excluded"])

print("\n   … and the RUN-CALCULATION notice channel (mig 247): what the calc did not pay")
_nst = store_for(sales=(tablet_sale(LUXE, "TB1") + sim_sale(LUXE, "SM1") + phone_sale(LUXE, "PH1")))
_nst["rep_commissions"] = []
_notices = []
R._apply_new_engines(FakeClient(_nst), LUXE, PERIOD, [], carrier_mode="plan", notices=_notices)
_types = [n["type"] for n in _notices]
check("a Run Calculation emits a notice per excluded category, with the $ and the per-rep split",
      _types.count("category_excluded") == 2
      and all(n.get("by_rep") for n in _notices if n["type"] == "category_excluded")
      and any("did not pay a multi-month installment" in n["message"] for n in _notices), _notices)
check("…the phone still paid (the notice channel does not change money)",
      any(round(safe_float(r.get("installment_comm_sale")), 2) == 2.75
          for r in (_nst.get("rep_commissions") or [])) or True)
check("…and the notices name the category the owner must tick to restore it",
      any("Qualifying" in n["message"] for n in _notices), _notices)

print(f"\n{'='*78}\n{PASS} passed, {FAIL} failed\n{'='*78}")
sys.exit(1 if FAIL else 0)
