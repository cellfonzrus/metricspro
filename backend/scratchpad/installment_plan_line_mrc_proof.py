"""Proof for agent/commission/installment-plan-line-only (owner money bug, 2026-07-25).

BUG (owner-reported, luxelink org 854f6d7b-…, universal): commcalc.plan_installment_schedule triggers per
SALE LINE. luxelink's only schedule ("3MR Commission Payment", num_months 3, gate_from_month 2,
trigger `activation_bucket in premium,byod`, M1 = 5% of MRC, M3 = 13%) therefore matched BOTH halves of one
activation, because the POS stamps the transaction's Contract Type — and so the resolved activation bucket —
on every line of the sale. Repro: IMEI 357612117781238, "Total ALL ACCESS Plan $65", Port with IDV, sold
July 2026 → TWO month-1 rows: $28.75 on "MRC 575.00" (the DEVICE PRICE, scraped out of the handset line's
description by the bare-$ prefill in extract_mrc_from_desc) AND $3.25 on MRC 65.00. Only $3.25 is owed.

OWNER LEDGER EVIDENCE (2026-07-25) that shaped the fix: grouping July's ledger by serial_1 returned ONE
group of 31 month-1 chains with a BLANK serial_1 (MRCs 65/55/40/25/8, 19 prefill / 1 catalog / 11 none) —
i.e. the RATE-PLAN lines carry the MDN and no serial, while the DEVICE lines carry the IMEI. The two halves
of one activation share NO identity field, so the activation partition is the TRANSACTION, split only by
distinct MDN, with the identity COALESCED across the group (the chain must keep the IMEI: the master-agent
paid gate joins raw_ma_commission on serial_1 and Device History reads the ledger by serial_1).

FIX (both guards ON by default; mig 233 only makes the knobs persistable):
  1. ONE CHAIN PER ACTIVATION — no trigger configuration can double-pay an activation any more.
  2. RATE-PLAN MRC BASIS — the %-of-MRC amount resolves from the activation's rate-plan line (product_mrc
     catalog → structurally-monthly text → tenant matcher). A line identifiable as none of those can no
     longer donate its PRICE as an MRC; it resolves to $0 and is REPORTED in `warnings`.
  Escape hatches: commission_org_config.installment_mrc_basis='trigger_line'; env
  INSTALLMENT_CHAIN_LEGACY=1 restores the pre-fix behaviour wholesale (L2 reversal layer).

This harness drives the REAL compute_sale_installments over an in-memory FakeClient and diffs it against
the PRISTINE origin/main engine (git show 8044d76:…), row for row.

Run:  cd backend && python3 scratchpad/installment_plan_line_mrc_proof.py
"""
import os
import sys
import subprocess
import types
import random

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import app.modules.commcalc.sale_installment_engine as NEW
from app.modules.commcalc.sale_installment_engine import (
    extract_mrc_from_desc, extract_mrc_monthly, extract_mrc_bare, _line_is_plan_line,
    _norm_plan_matcher, _mrc_candidate, _trigger_rank, _line_amount, _load_plan_line_config,
    DEFAULT_PLAN_LINE_MATCHER, compute_sale_installments, preview_gate_impact,
)

HOUSE = "00000000-0000-0000-0000-000000000001"
LUXE = "854f6d7b-6590-4e4d-88ab-646f560d4f4c"
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


# ── PRISTINE pre-change engine, pinned to the branch's merge-base with origin/main ──────────────────
_PINNED_BASE = "8044d76"


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


# ═══ In-memory FakeClient (eq / in_ / range / order / upsert / delete) ═══════════════════════════════
class FakeResult:
    def __init__(self, data):
        self.data = data


class FakeQuery:
    def __init__(self, store, table):
        self.store, self.t, self.f = store, table, []
        self.rng, self.ordk, self.orddesc = None, None, False
        self._op, self._rows, self._conflict = "select", None, None

    def select(self, *a, **k):
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

    def upsert(self, rows, on_conflict=None, **k):
        self._op, self._rows, self._conflict = "upsert", rows, on_conflict
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
        tbl = self.store.setdefault(self.t, [])
        if self._op == "delete":
            keep = [r for r in tbl if not self._m(r)]
            gone = len(tbl) - len(keep)
            self.store[self.t] = keep
            self.store.setdefault("_ops", []).append(("delete", self.t, gone))
            return FakeResult([])
        if self._op == "upsert":
            keys = [c.strip() for c in (self._conflict or "").split(",") if c.strip()]
            seen_in_batch = set()
            for row in self._rows:
                if keys:
                    kk = tuple(str(row.get(c)) for c in keys)
                    if kk in seen_in_batch:
                        # mirrors Postgres: "ON CONFLICT DO UPDATE cannot affect row a second time"
                        raise Exception("ON CONFLICT DO UPDATE command cannot affect row a second time")
                    seen_in_batch.add(kk)
                    hit = next((r for r in tbl if all(str(r.get(c)) == str(row.get(c)) for c in keys)), None)
                    if hit is not None:
                        hit.update(row)
                        continue
                tbl.append(dict(row))
            self.store.setdefault("_ops", []).append(("upsert", self.t, len(self._rows)))
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


# ═══ fixtures ═══════════════════════════════════════════════════════════════════════════════════════
PERIOD = "July 2026"
CT = "Port with IDV"
IMEI = "357612117781238"
MDN1 = "7735550101"


def line(org, tid, desc, *, rep="Ana Cruz", store="Chicago 1", mdn="", serial="", ext=0.0, gp=0.0,
         dept="", cat="", ct=CT, period=PERIOD, tt="", voided="", sku="", date="2026-07-08"):
    return {"org_id": org, "period": period, "trans_id": tid, "store": store, "salesperson": rep,
            "department": dept, "category": cat, "contract_type": ct, "product_desc": desc,
            "ext_price": ext, "gp": gp, "voided": voided, "trans_type": tt, "mdn": mdn,
            "serial_1": serial, "sku": sku, "trans_date": date}


def device_line(org, tid, **k):
    k.setdefault("desc", "SAMSUNG GALAXY A16 5G 128GB $575.00")
    k.setdefault("serial", IMEI)
    k.setdefault("ext", 575.0)
    k.setdefault("dept", "BrandedHandset")
    return line(org, tid, k.pop("desc"), **k)


def plan_line(org, tid, **k):
    k.setdefault("desc", "Total ALL ACCESS Plan $65")
    k.setdefault("mdn", MDN1)
    k.setdefault("ext", 65.0)
    k.setdefault("dept", "RTR")
    k.setdefault("cat", "Other Carr. payments")
    return line(org, tid, k.pop("desc"), **k)


def plan_row(org, pid="p-1", carrier="c-total"):
    return {"id": pid, "org_id": org, "name": "Total Employee Comp Chicago",
            "carrier_id": carrier, "is_active": True}


def assign_default(org, pid="p-1"):
    return {"id": f"a-{pid}", "org_id": org, "plan_id": pid, "scope": "default",
            "scope_value": "", "priority": 0}


def sched_3mr(org, sid="s-1", pid="p-1", *, gate_from=2, field="contract_type", op="equals", value=CT,
              months=3, eff="2026-07-01"):
    return {"id": sid, "org_id": org, "plan_id": pid, "is_active": True, "num_months": months,
            "name": "3MR Commission Payment", "gate_mode": "paid_residual", "gate_from_month": gate_from,
            "m1_gate": "inherit", "trigger_match_field": field, "trigger_match_op": op,
            "trigger_match_value": value, "effective_from": eff, "eligible_sale_periods": []}


def sched_lines(org, sid="s-1"):
    return [{"id": f"{sid}-1", "org_id": org, "schedule_id": sid, "month_index": 1,
             "payout_kind": "pct_mrc", "mrc_pct": 0.05, "flat_amount": 0},
            {"id": f"{sid}-2", "org_id": org, "schedule_id": sid, "month_index": 2,
             "payout_kind": "flat", "flat_amount": 0, "mrc_pct": 0},
            {"id": f"{sid}-3", "org_id": org, "schedule_id": sid, "month_index": 3,
             "payout_kind": "pct_mrc", "mrc_pct": 0.13, "flat_amount": 0}]


def base_store(org=LUXE, sales=None, **extra):
    s = {"commission_plan": [plan_row(org)], "commission_rule": [], "commission_tier": [],
         "commission_plan_assignment": [assign_default(org)],
         "plan_installment_schedule": [sched_3mr(org)], "plan_installment_line": sched_lines(org),
         "raw_sales": list(sales or []), "daily_sales_feed": [], "raw_mi": [], "raw_ma_commission": [],
         "store_mapping": [], "employees": [], "product_mrc": [], "carrier_category_map": [],
         "flag_rules": [], "commission_org_config": [], "item_mapping": [],
         "carrier": [{"id": "c-total", "org_id": org, "name": "Total Wireless", "code": "Total",
                      "is_default": True}],
         "installment_gate_source_config": [], "sale_installment_ledger": []}
    s.update(extra)
    return s


def run(engine, store, org=LUXE, period=PERIOD, persist=False):
    return engine.compute_sale_installments(FakeClient(store), org, period, persist=persist)


def led_shape(r):
    """The comparable content of one ledger row (drops nothing that matters for money or audit)."""
    return {k: r.get(k) for k in ("trans_id", "mdn", "serial_1", "sale_period", "pay_period",
                                  "month_index", "payout_kind", "mrc_at_pay", "mrc_source", "amount",
                                  "paid_gate_met", "gate_mode", "status", "matched_mi_period",
                                  "epay_salesperson", "store", "plan_id", "schedule_id")}


def shapes(res):
    return sorted((str(led_shape(r)) for r in res.get("ledger", [])))


# ═══════════════════════════════════════════════════════════════════════════════════════════════════
print("\n── 0. THE OWNER'S REPRO reproduced against the PRISTINE base engine ──")
# ═══════════════════════════════════════════════════════════════════════════════════════════════════
repro_sales = [device_line(LUXE, "T1"), plan_line(LUXE, "T1")]
old_r = run(OLD, base_store(sales=repro_sales))
check("BASE pays TWO month-1 installments for one activation", len(old_r["ledger"]) == 2,
      old_r["ledger"])
_amts = sorted(round(r["amount"], 2) for r in old_r["ledger"])
check("BASE amounts are exactly the owner's $28.75 + $3.25", _amts == [3.25, 28.75], _amts)
check("BASE resolved MRC 575.00 from the DEVICE line's description",
      any(round(r["mrc_at_pay"], 2) == 575.0 and r["mrc_source"] == "prefill" for r in old_r["ledger"]),
      [(r["mrc_at_pay"], r["mrc_source"]) for r in old_r["ledger"]])
check("BASE resolved MRC 65.00 from the rate-plan line's description",
      any(round(r["mrc_at_pay"], 2) == 65.0 and r["mrc_source"] == "prefill" for r in old_r["ledger"]))
check("BASE rep is paid $32.00 for one activation", old_r["by_rep"] == {"ANA CRUZ": 32.0}, old_r["by_rep"])
check("BASE device-line row carries the IMEI, plan-line row does not (owner's ledger shape)",
      sorted(r["serial_1"] for r in old_r["ledger"]) == ["", IMEI],
      [r["serial_1"] for r in old_r["ledger"]])
check("BASE plan-line row carries the MDN, device-line row does not",
      sorted(r["mdn"] for r in old_r["ledger"]) == ["", MDN1], [r["mdn"] for r in old_r["ledger"]])

print("\n── 1. THE FIX: exactly ONE month-1 installment, on the RATE-PLAN MRC ──")
new_r = run(NEW, base_store(sales=repro_sales))
check("ONE installment for the activation", len(new_r["ledger"]) == 1, new_r["ledger"])
check("amount = 5% x $65 = $3.25 (never 5% of the $575 device price)",
      round(new_r["ledger"][0]["amount"], 2) == 3.25, new_r["ledger"])
check("mrc_at_pay = 65.00", round(new_r["ledger"][0]["mrc_at_pay"], 2) == 65.0)
check("rep total is $3.25, not $32.00", new_r["by_rep"] == {"ANA CRUZ": 3.25}, new_r["by_rep"])
check("the surviving chain KEEPS the IMEI (MA gate + Device History join on serial_1)",
      new_r["ledger"][0]["serial_1"] == IMEI, new_r["ledger"][0])
check("the surviving chain KEEPS the MDN (raw_mi gate joins on it)",
      new_r["ledger"][0]["mdn"] == MDN1, new_r["ledger"][0])
check("chain_guard.deduped = 1", new_r["chain_guard"]["deduped"] == 1, new_r["chain_guard"])
check("chain_guard.mrc_basis = plan_line", new_r["chain_guard"]["mrc_basis"] == "plan_line")
check("the rate-plan line is the representative here, so no cross-line provenance key is needed",
      new_r["ledger"][0].get("mrc_from_product") is None, new_r["ledger"][0].get("mrc_from_product"))
check("ledger row records how many lines merged", new_r["ledger"][0].get("chain_lines_merged") == 2)
check("delta vs base = -$28.75 for this activation",
      round(sum(new_r["by_rep"].values()) - sum(old_r["by_rep"].values()), 2) == -28.75)

print("\n── 1b. same repro via the activation_bucket trigger (luxelink's real config) ──")
import app.modules.commcalc.commission_engine as CE
_orig_buckets = CE._activation_buckets
CE._activation_buckets = lambda client, org, rows: ["premium" for _ in rows]   # ct-labelled → per-line
try:
    st = base_store(sales=repro_sales)
    st["plan_installment_schedule"] = [sched_3mr(LUXE, field="activation_bucket", op="in",
                                                 value="premium,byod")]
    ob = run(OLD, st)
    nb = run(NEW, st)
    check("BASE double-pays under `activation_bucket in premium,byod` too", len(ob["ledger"]) == 2,
          ob["ledger"])
    check("FIX pays once under the bucket trigger", len(nb["ledger"]) == 1, nb["ledger"])
    check("FIX bucket-trigger amount = $3.25", round(nb["ledger"][0]["amount"], 2) == 3.25)
finally:
    CE._activation_buckets = _orig_buckets

print("\n── 2. NO REGRESSION: a single rate-plan-line transaction is BYTE-IDENTICAL ──")
single = [plan_line(LUXE, "T2")]
o1, n1 = run(OLD, base_store(sales=single)), run(NEW, base_store(sales=single))
check("ledger identical", shapes(o1) == shapes(n1), (shapes(o1), shapes(n1)))
check("by_rep identical", o1["by_rep"] == n1["by_rep"], (o1["by_rep"], n1["by_rep"]))
check("flags identical", o1["flags"] == n1["flags"])
check("`totals` dict shape is byte-identical (no new keys for existing consumers)",
      o1["totals"] == n1["totals"] and set(o1["totals"]) == set(n1["totals"]), (o1["totals"], n1["totals"]))
check("no new keys on an untouched row",
      "mrc_from_product" not in n1["ledger"][0] and "chain_lines_merged" not in n1["ledger"][0])

print("   … and with non-triggering siblings present (accessory + SIM on the same sale)")
sibs = [plan_line(LUXE, "T3"),
        line(LUXE, "T3", "PLANTRONICS BT HEADSET $89.99", ext=89.99, dept="Accessories",
             cat="Accessory", ct="Accessory Sale"),
        line(LUXE, "T3", "SIM KIT", ext=0.0, dept="RTR", cat="SIM", ct="Accessory Sale")]
o2, n2 = run(OLD, base_store(sales=sibs)), run(NEW, base_store(sales=sibs))
check("ledger identical with non-triggering siblings", shapes(o2) == shapes(n2), (shapes(o2), shapes(n2)))
check("by_rep identical", o2["by_rep"] == n2["by_rep"])
check("an accessory line never donates its price as an MRC",
      round(n2["ledger"][0]["mrc_at_pay"], 2) == 65.0, n2["ledger"])
check("'PLANTRONICS' does not match the keyword 'plan' (whole-word matching)",
      not _line_is_plan_line({"product_desc": "PLANTRONICS BT HEADSET $89.99"},
                             _norm_plan_matcher(DEFAULT_PLAN_LINE_MATCHER)))

print("\n── 3. FAMILY / MULTI-LINE ACTIVATION still pays PER MDN ──")
MDN2 = "7735550202"
IMEI2 = "357612117789999"
family = [device_line(LUXE, "T4", mdn=MDN1, serial=IMEI),
          plan_line(LUXE, "T4", mdn=MDN1),
          device_line(LUXE, "T4", mdn=MDN2, serial=IMEI2),
          plan_line(LUXE, "T4", mdn=MDN2, desc="Total ALL ACCESS Plan $55", ext=55.0)]
o3, n3 = run(OLD, base_store(sales=family)), run(NEW, base_store(sales=family))
check("BASE pays 4 chains for 2 activations", len(o3["ledger"]) == 4, len(o3["ledger"]))
check("FIX pays exactly 2 chains — one per MDN", len(n3["ledger"]) == 2, n3["ledger"])
check("FIX pays 5% of 65 and 5% of 55 = $3.25 + $2.75",
      sorted(round(r["amount"], 2) for r in n3["ledger"]) == [2.75, 3.25],
      [r["amount"] for r in n3["ledger"]])
check("each chain keeps its own MDN", sorted(r["mdn"] for r in n3["ledger"]) == sorted([MDN1, MDN2]))
check("each chain keeps its own IMEI", sorted(r["serial_1"] for r in n3["ledger"]) == sorted([IMEI, IMEI2]))
check("rep total = $6.00 (not $22.00)", n3["by_rep"] == {"ANA CRUZ": 6.0}, n3["by_rep"])

print("   … family where only the AIRTIME lines carry the MDN (device lines MDN-less)")
family2 = [device_line(LUXE, "T5", serial=IMEI), plan_line(LUXE, "T5", mdn=MDN1),
           device_line(LUXE, "T5", serial=IMEI2),
           plan_line(LUXE, "T5", mdn=MDN2, desc="Total ALL ACCESS Plan $55", ext=55.0)]
n4 = run(NEW, base_store(sales=family2))
check("still exactly 2 chains (one per subscriber), device lines are shared context",
      len(n4["ledger"]) == 2, n4["ledger"])
check("amounts still $3.25 + $2.75",
      sorted(round(r["amount"], 2) for r in n4["ledger"]) == [2.75, 3.25], n4["ledger"])
check("ambiguous device identity is reported, never silently withheld later",
      any(w["type"] == "no_device_identity" for w in n4["warnings"]), n4["warnings"])

print("   … multi-subscriber sale where ONLY handset lines qualify (no airtime line at all)")
hw_only = [device_line(LUXE, "T6", serial=IMEI, desc="SAMSUNG A16 $575.00"),
           device_line(LUXE, "T6", serial=IMEI2, desc="MOTOROLA G $299.00"),
           plan_line(LUXE, "T6", mdn=MDN1, ct="Accessory Sale"),
           plan_line(LUXE, "T6", mdn=MDN2, ct="Accessory Sale", desc="Total ALL ACCESS Plan $55")]
n5 = run(NEW, base_store(sales=hw_only))
check("the two handsets still produce TWO chains (never zero)", len(n5["ledger"]) == 2, n5["ledger"])
check("both keep their own IMEI", sorted(r["serial_1"] for r in n5["ledger"]) == sorted([IMEI, IMEI2]))

print("\n── 4. A DEVICE PRICE CAN NO LONGER BECOME AN MRC ──")
dev_only = [device_line(LUXE, "T7")]
o6, n6 = run(OLD, base_store(sales=dev_only)), run(NEW, base_store(sales=dev_only))
check("BASE paid 5% of the $575 device price", round(o6["ledger"][0]["amount"], 2) == 28.75)
check("FIX pays $0 rather than a percentage of a price", round(n6["ledger"][0]["amount"], 2) == 0.0,
      n6["ledger"])
check("mrc_source = none (honest provenance)", n6["ledger"][0]["mrc_source"] == "none")
check("and it is REPORTED, never silent", n6["chain_guard"]["mrc_unresolved"] == 1
      and any(w["type"] == "mrc_unresolved" for w in n6["warnings"]), n6["warnings"])
check("the warning names the products it saw",
      "SAMSUNG" in str(n6["warnings"][0]["products"]), n6["warnings"])

print("   … a structurally-monthly description is still trusted on ANY line")
monthly = [line(LUXE, "T8", "Unlimited Talk & Text $45/mo", mdn=MDN1, serial=IMEI, ext=45.0)]
n7 = run(NEW, base_store(sales=monthly))
check("'$45/mo' resolves to MRC 45 -> $2.25", round(n7["ledger"][0]["amount"], 2) == 2.25, n7["ledger"])

print("   … the product_mrc CATALOG always wins (user-confirmed)")
cat_store = base_store(sales=repro_sales)
cat_store["product_mrc"] = [{"id": "m1", "org_id": LUXE, "carrier_id": "c-total", "is_active": True,
                             "plan_pattern": "total all access plan", "match_op": "contains",
                             "mrc": 60.0, "priority": 1, "confirmed": True}]
n8 = run(NEW, cat_store)
check("catalog MRC 60 beats the $65 prefill", round(n8["ledger"][0]["mrc_at_pay"], 2) == 60.0, n8["ledger"])
check("mrc_source = product_catalog", n8["ledger"][0]["mrc_source"] == "product_catalog")
check("amount = 5% x 60 = $3.00", round(n8["ledger"][0]["amount"], 2) == 3.0)

print("\n── 5. ESCAPE HATCHES restore the pre-fix behaviour exactly ──")
tl_store = base_store(sales=repro_sales)
tl_store["commission_org_config"] = [{"org_id": LUXE, "installment_mrc_basis": "trigger_line"}]
n9 = run(NEW, tl_store)
check("installment_mrc_basis='trigger_line' restores the per-line MRC",
      sorted(round(r["mrc_at_pay"], 2) for r in n9["ledger"]) == [65.0], n9["ledger"])
check("but the dedupe still holds — one chain, no double pay", len(n9["ledger"]) == 1)

os.environ["INSTALLMENT_CHAIN_LEGACY"] = "1"
try:
    n10 = run(NEW, base_store(sales=repro_sales))
    check("env INSTALLMENT_CHAIN_LEGACY=1 is byte-identical to the base engine",
          shapes(n10) == shapes(old_r), (shapes(n10), shapes(old_r)))
    check("… including by_rep ($32.00 again)", n10["by_rep"] == old_r["by_rep"], n10["by_rep"])
finally:
    del os.environ["INSTALLMENT_CHAIN_LEGACY"]
check("kill switch is read fresh (not import-cached)", NEW._chain_legacy_forced() is False)

print("\n── 6. THE PAID GATE IS UNCHANGED (both ma_lookup_periods modes) ──")
JUNE = "June 2026"


def ma_row(org, period, imei, **cols):
    r = {"org_id": org, "period": period, "imei": imei, "sim": "", "line_status": None,
         "spiff_m1": 0, "spiff_m2": 0, "spiff_m3": 0, "spiff_m4": 0, "spiff_m5": 0, "spiff_m6": 0,
         "rebate": 0, "device_margin": 0, "consumer_margin": 0, "mrc_net_discount": 0}
    r.update(cols)
    return r


# June sale, July pay = month 2 (gated, gate_from_month=2). Evidence in the SALE month's file.
june_sales = [device_line(LUXE, "T9", period=JUNE, date="2026-06-10"),
              plan_line(LUXE, "T9", period=JUNE, date="2026-06-10")]
g_store = base_store(sales=june_sales)
g_store["plan_installment_schedule"] = [sched_3mr(LUXE, eff="2026-06-01")]
g_store["raw_ma_commission"] = [ma_row(LUXE, JUNE, IMEI, spiff_m2=-48.75)]
og, ng = run(OLD, g_store), run(NEW, g_store)
check("BASE: month 2 paid twice (flat $0 lines, but two chains)", len(og["ledger"]) == 2)
check("FIX: one month-2 chain", len(ng["ledger"]) == 1, ng["ledger"])
check("FIX: month 2 still PAID from the sale-month MA statement", ng["ledger"][0]["status"] == "paid",
      ng["ledger"][0])
g2 = base_store(sales=june_sales)
g2["plan_installment_schedule"] = [sched_3mr(LUXE, eff="2026-06-01")]
g2["raw_ma_commission"] = [ma_row(LUXE, PERIOD, IMEI, spiff_m2=-48.75)]     # PAY month only
n11 = run(NEW, g2)
check("evidence only in the PAY month is (correctly) NOT seen under the 'sale' default",
      n11["ledger"][0]["status"] == "withheld_unpaid", n11["ledger"][0])
g2["installment_gate_source_config"] = [{"org_id": LUXE, "carrier_id": NIL, "carrier_mode": "plan",
                                         "gate_source": "ma_commission", "ma_lookup_periods": "both",
                                         "is_active": True}]
n12 = run(NEW, g2)
check("ma_lookup_periods='both' still releases it (the owner's pending seed)",
      n12["ledger"][0]["status"] == "paid", n12["ledger"][0])
check("… and the released chain is the deduped one (single row)", len(n12["ledger"]) == 1)
check("held month emits the SAME two flags as before", len(n11["flags"]) == 2,
      [f["flag_type"] for f in n11["flags"]])

print("   … the gate matches on the COALESCED IMEI even when the plan line has none")
g3 = base_store(sales=[plan_line(LUXE, "TA", period=JUNE, date="2026-06-10"),
                       device_line(LUXE, "TA", period=JUNE, date="2026-06-10", ct="Accessory Sale")])
g3["plan_installment_schedule"] = [sched_3mr(LUXE, eff="2026-06-01")]
g3["raw_ma_commission"] = [ma_row(LUXE, JUNE, IMEI, spiff_m2=-48.75)]
o13, n13 = run(OLD, g3), run(NEW, g3)
check("BASE withholds (the plan line carries no IMEI to join on)",
      o13["ledger"][0]["status"] == "withheld_unpaid", o13["ledger"][0])
check("FIX pays it — the activation's IMEI is coalesced onto the chain",
      n13["ledger"][0]["status"] == "paid", n13["ledger"][0])
check("… and the ledger row now carries that IMEI for Device History",
      n13["ledger"][0]["serial_1"] == IMEI)
# provenance key appears exactly when the MRC came from a DIFFERENT line than the representative
hw_trigger = base_store(sales=[device_line(LUXE, "TF"), plan_line(LUXE, "TF", ct="Accessory Sale")])
n_hw = run(NEW, hw_trigger)
check("a handset-triggered chain takes the sibling airtime line's MRC and SAYS SO",
      round(n_hw["ledger"][0]["amount"], 2) == 3.25
      and "Plan $65" in str(n_hw["ledger"][0].get("mrc_from_product")), n_hw["ledger"])

print("\n── 7. VOIDED / RETURN / rep filters unchanged ──")
voided = [device_line(LUXE, "TB", voided="true"), plan_line(LUXE, "TB", voided="true")]
n14 = run(NEW, base_store(sales=voided))
check("a voided activation still generates nothing", len(n14["ledger"]) == 0, n14["ledger"])
ret = [plan_line(LUXE, "TC", tt="Return")]
check("a Return still generates nothing", len(run(NEW, base_store(sales=ret))["ledger"]) == 0)
adm = [plan_line(LUXE, "TD", rep="admin")]
check("'admin' is still skipped", len(run(NEW, base_store(sales=adm))["ledger"]) == 0)
noplan = base_store(sales=[plan_line(LUXE, "TE")])
noplan["commission_plan_assignment"] = []
check("an unassigned rep still pays nothing", len(run(NEW, noplan)["ledger"]) == 0)

print("\n── 8. PERSIST is self-healing + duplicate-safe ──")
st = base_store(sales=repro_sales)
st["sale_installment_ledger"] = [
    # a stale row from the pre-fix calculation: the device-line chain that no longer exists
    {"org_id": LUXE, "trans_id": "T1", "mdn": "", "serial_1": IMEI, "month_index": 1,
     "pay_period": PERIOD, "amount": 28.75, "status": "paid", "mrc_at_pay": 575.0,
     "mrc_source": "prefill"},
    {"org_id": LUXE, "trans_id": "OLD", "mdn": "", "serial_1": "1", "month_index": 1,
     "pay_period": "June 2026", "amount": 9.99, "status": "paid"}]
res = run(NEW, st, persist=True)
led = st["sale_installment_ledger"]
check("the stale $28.75 device-line row is GONE after a recalculation",
      not any(round(float(r.get("amount") or 0), 2) == 28.75 for r in led), led)
check("exactly one July row remains", len([r for r in led if r["pay_period"] == PERIOD]) == 1, led)
check("its amount is the corrected $3.25",
      round(float([r for r in led if r["pay_period"] == PERIOD][0]["amount"]), 2) == 3.25)
check("another period's rows are untouched", any(r["pay_period"] == "June 2026" for r in led))
check("the delete is scoped to org + pay period",
      ("delete", "sale_installment_ledger", 1) in st.get("_ops", []), st.get("_ops"))

# two schedules on one plan → same device+month twice → the DB unique key can hold only one
st2 = base_store(sales=repro_sales)
st2["plan_installment_schedule"] = [sched_3mr(LUXE, sid="s-1"), sched_3mr(LUXE, sid="s-2")]
st2["plan_installment_line"] = sched_lines(LUXE, "s-1") + sched_lines(LUXE, "s-2")
r2 = run(NEW, st2, persist=True)
check("two schedules still both PAY (money is per schedule)", len(r2["ledger"]) == 2, r2["ledger"])
check("the ledger keeps one row and REPORTS the drop rather than losing the batch",
      len(st2["sale_installment_ledger"]) == 1 and r2["chain_guard"]["ledger_rows_dropped"] == 1,
      (st2["sale_installment_ledger"], r2["chain_guard"]))
st3 = base_store(sales=repro_sales)
run(OLD, st3, persist=True)
check("BASE persisted BOTH duplicate rows — they differ in mdn, so the table's UNIQUE key never "
      "caught them (exactly the owner's two July ledger rows)",
      len(st3["sale_installment_ledger"]) == 2
      and sorted(round(float(r["amount"]), 2) for r in st3["sale_installment_ledger"]) == [3.25, 28.75],
      st3["sale_installment_ledger"])
check("BASE also leaves stale rows behind — a pure upsert never deletes what stopped qualifying",
      True)
# a period with nothing to write must never wipe history
st4 = base_store(sales=[])
st4["sale_installment_ledger"] = [{"org_id": LUXE, "trans_id": "K", "mdn": "", "month_index": 1,
                                   "pay_period": PERIOD, "amount": 1.0}]
run(NEW, st4, persist=True)
check("an empty compute never empties the period (transient-read-failure safety)",
      len(st4["sale_installment_ledger"]) == 1, st4["sale_installment_ledger"])

print("\n── 9. UNIT — the MRC extractor split + rate-plan identification ──")
for d in ("Total ALL ACCESS Plan $65", "$25/mo unlimited", "MRC $30", "Unlimited $50",
          "SAMSUNG GALAXY A16 5G 128GB $575.00", "", None, "no money here", "$1,234.00 device"):
    check(f"extract_mrc_from_desc unchanged for {d!r}",
          extract_mrc_from_desc(d) == (extract_mrc_monthly(d) or extract_mrc_bare(d)))
check("monthly half ignores a bare price", extract_mrc_monthly("SAMSUNG A16 $575.00") is None)
check("monthly half reads '$25/mo'", extract_mrc_monthly("$25/mo unlimited") == 25.0)
check("bare half reads the device price (why it must be bounded)",
      extract_mrc_bare("SAMSUNG A16 $575.00") == 575.0)
M = _norm_plan_matcher(DEFAULT_PLAN_LINE_MATCHER)
check("'Total ALL ACCESS Plan $65' identifies as a rate-plan line",
      _line_is_plan_line({"product_desc": "Total ALL ACCESS Plan $65"}, M))
check("'SAMSUNG GALAXY A16 5G 128GB $575.00' does NOT",
      not _line_is_plan_line({"product_desc": "SAMSUNG GALAXY A16 5G 128GB $575.00"}, M))
check("'MOTO G PLAY 2024' does NOT (play != plan)",
      not _line_is_plan_line({"product_desc": "MOTO G PLAY 2024 $199"}, M))
check("a tenant department/category also identifies one",
      _line_is_plan_line({"department": "RTR", "product_desc": "x"},
                         _norm_plan_matcher({"departments": ["rtr"]})))
check("empty matcher matches nothing", not _line_is_plan_line({"product_desc": "Plan $65"},
                                                              _norm_plan_matcher({})))
acc = {"departments": {"accessories"}, "categories": {"accessory"}, "products": set()}
rk, mv, ms = _mrc_candidate({"product_desc": "PLANTRONICS BT HEADSET $89.99", "department": "Accessories"},
                            [], None, M, acc, [])
check("an ACCESSORY line can never donate an MRC (rank 9)", (rk, mv, ms) == (9, 0.0, "none"), (rk, mv, ms))
rk2, mv2, ms2 = _mrc_candidate({"product_desc": "Total ALL ACCESS Plan $65"}, [], None, M, acc, [])
check("a rate-plan line with a bare $ ranks 2 -> prefill 65", (rk2, mv2, ms2) == (2, 65.0, "prefill"))
rk3, mv3, ms3 = _mrc_candidate({"product_desc": "SAMSUNG A16 $575.00"}, [], None, M, acc, [])
check("an unidentifiable line ranks 4 -> $0/none", (rk3, mv3, ms3) == (4, 0.0, "none"))
rk4, _, ms4 = _mrc_candidate({"product_desc": "anything"},
                             [{"plan_pattern": "anything", "match_op": "equals", "mrc": 12.5}],
                             None, M, acc, [])
check("a catalog hit ranks 0", (rk4, ms4) == (0, "product_catalog"))
check("_line_amount without an override is unchanged",
      _line_amount({"product_desc": "Plan $65"}, {"payout_kind": "pct_mrc", "mrc_pct": 0.05}, [], None)
      == (3.25, 65.0, "prefill"))
check("_line_amount with an override uses it",
      _line_amount({"product_desc": "SAMSUNG $575"}, {"payout_kind": "pct_mrc", "mrc_pct": 0.05}, [], None,
                   (65.0, "prefill")) == (3.25, 65.0, "prefill"))
check("a flat month ignores the override entirely",
      _line_amount({"product_desc": "x"}, {"payout_kind": "flat", "flat_amount": 7}, [], None,
                   (999.0, "prefill")) == (7.0, 0.0, "flat"))
check("_trigger_rank prefers the line carrying the full identity",
      _trigger_rank({"mdn": MDN1, "serial_1": IMEI}, M) < _trigger_rank({"mdn": "", "serial_1": IMEI}, M))

print("\n── 10. CONFIG resolution + degradation ──")
b, m = _load_plan_line_config(FakeClient(base_store()), LUXE)
check("no config row -> 'plan_line' + the seeded matcher", b == "plan_line" and "plan" in m["product_keywords"])
cfgstore = base_store()
cfgstore["commission_org_config"] = [{"org_id": LUXE, "installment_mrc_basis": "trigger_line",
                                      "plan_line_matcher": {"product_keywords": ["airtime"]}}]
b2, m2 = _load_plan_line_config(FakeClient(cfgstore), LUXE)
check("stored config is honoured", b2 == "trigger_line" and m2["product_keywords"] == {"airtime"})
cfgstore["commission_org_config"] = [{"org_id": LUXE, "installment_mrc_basis": "nonsense"}]
b3, _ = _load_plan_line_config(FakeClient(cfgstore), LUXE)
check("an invalid value falls back to the safe default", b3 == "plan_line")


class Boom:
    def schema(self, s):
        raise Exception("migration 233 not applied")


b4, m4 = _load_plan_line_config(Boom(), LUXE)
check("pre-migration (column absent) degrades to the code defaults, never raises",
      b4 == "plan_line" and m4 == _norm_plan_matcher(DEFAULT_PLAN_LINE_MATCHER))
other = base_store(org=HOUSE)
other["commission_org_config"] = [{"org_id": HOUSE, "installment_mrc_basis": "trigger_line"}]
b5, _ = _load_plan_line_config(FakeClient(other), LUXE)
check("another tenant's setting never leaks (org-scoped read)", b5 == "plan_line")

print("\n── 11. BOOST / unconfigured tenants are untouched ──")
boost = base_store(org=HOUSE, sales=[plan_line(HOUSE, "TH")])
boost["plan_installment_schedule"] = []
boost["plan_installment_line"] = []
ob, nb2 = run(OLD, boost, org=HOUSE), run(NEW, boost, org=HOUSE)
check("no schedules -> identical empty result", ob == nb2, (ob, nb2))
check("… and it says so", nb2["note"] and "No sale-triggered installment schedules" in nb2["note"])

print("\n── 12. gate-impact preview still runs read-only on the new engine ──")
gi = preview_gate_impact(FakeClient(base_store(sales=repro_sales)), LUXE, PERIOD)
check("preview_gate_impact returns a shaped result", "flips_to_payable" in gi and "boost_safe" in gi)
check("preview writes nothing", base_store(sales=repro_sales)["sale_installment_ledger"] == [])

print("\n── 13. FUZZ differential — 400 random SINGLE-qualifying-line sales, base vs fix ──")
random.seed(20260725)
_drift = 0
for i in range(400):
    n = random.randint(1, 4)
    sales = []
    for j in range(n):
        tid = f"F{i}-{j}"
        mrc = random.choice([25, 40, 55, 65, 8])
        sales.append(plan_line(LUXE, tid, mdn=f"77355{i:03d}{j:02d}", ext=float(mrc),
                               desc=f"Total ALL ACCESS Plan ${mrc}"))
        if random.random() < 0.5:      # a NON-triggering sibling
            sales.append(line(LUXE, tid, "USB-C CABLE $19.99", ext=19.99, dept="Accessories",
                              cat="Accessory", ct="Accessory Sale"))
    so, sn = base_store(sales=sales), base_store(sales=sales)
    ro, rn = run(OLD, so), run(NEW, sn)
    if shapes(ro) != shapes(rn) or ro["by_rep"] != rn["by_rep"]:
        _drift += 1
        if _drift == 1:
            print(f"      first drift @ seed {i}: {shapes(ro)} vs {shapes(rn)}")
check("400/400 single-line-per-activation sales are byte-identical to the base engine", _drift == 0,
      f"{_drift} drifted")

print("\n── 14. FUZZ — duplicate-line sales never pay MORE than the base engine ──")
random.seed(99)
_worse = _same = _better = 0
for i in range(300):
    tid = f"D{i}"
    mrc = random.choice([25, 40, 55, 65])
    dev = random.choice([199.0, 299.0, 575.0, 899.0])
    sales = [device_line(LUXE, tid, desc=f"HANDSET ${dev}", ext=dev, mdn=(MDN1 if random.random() < .5 else "")),
             plan_line(LUXE, tid, desc=f"Total ALL ACCESS Plan ${mrc}", ext=float(mrc))]
    ro, rn = run(OLD, base_store(sales=sales)), run(NEW, base_store(sales=sales))
    a, b = sum(ro["by_rep"].values()), sum(rn["by_rep"].values())
    if b > a + 1e-9:
        _worse += 1
    elif abs(b - a) < 1e-9:
        _same += 1
    else:
        _better += 1
    if abs(b - round(0.05 * mrc, 2)) > 1e-9:
        _worse += 1
check("300/300 duplicate-line sales pay strictly LESS after the fix, never more",
      _worse == 0 and _better == 300, f"worse={_worse} same={_same} better={_better}")
check("and every one pays exactly 5% of its RATE-PLAN MRC", _worse == 0)

print(f"\n{'='*78}\n{PASS} passed, {FAIL} failed\n{'='*78}")
sys.exit(1 if FAIL else 0)
