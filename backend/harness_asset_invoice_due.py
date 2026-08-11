"""Offline proof harness for Upcoming Invoice Payment Due (mod-asset, 2026-08-05 dispatch,
backend/app/modules/asset/invoice_due.py).

No database, no network: a small recording fake Supabase/PostgREST client (real eq/in_/gte/lte/
ilike/order/range/delete/insert semantics — extends the b2b-inventory harness's proven upsert-safe
fake with the extra chain methods this module needs) feeds the REAL module code directly:
invoice_due_list / invoice_due_detail / invoice_due_filter_options / sync_invoice_due_flags /
invoice_due_allowed, plus the pure helpers (_period_variants, _invoice_rollup, _commission_for_imeis).

Covers: per-IMEI sold/reimbursed/unsold rollup, the M1-vs-trailing-vs-unsplit commission split
(residual/M2-M12 exclusion), the INFO-ONLY net-deduction math, multi-tenant scoping, filter/market
narrowing, migration-not-run degrade, the permission gate, and the flags-sync idiom.

Run:  cd backend && python3 harness_asset_invoice_due.py
"""
import sys
from datetime import date, timedelta

sys.path.insert(0, ".")

PASS, FAIL = 0, 0


def ok(name, cond, extra=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  ok   {name}")
    else:
        FAIL += 1
        print(f"  FAIL {name} {extra}")


ORG_A = "00000000-0000-0000-0000-0000000000aa"
ORG_B = "00000000-0000-0000-0000-0000000000bb"
TODAY = date.today()


# ── minimal fake supabase/postgrest client — extends the b2b-inventory harness's proven fake with
# gte/lte/ilike/order/range/limit (this module's endpoints need all of them) ───────────────────────
class _Resp:
    def __init__(self, data):
        self.data = data


class _Q:
    def __init__(self, store, schema, table, log):
        self.store, self.schema, self.table, self.log = store, schema, table, log
        self.filters = []
        self._order = None
        self._range = None
        self._limit = None
        self._op = "select"
        self._payload = None

    def select(self, *a, **k):
        return self

    def eq(self, k, v):
        self.filters.append(("eq", k, v)); return self

    def in_(self, k, v):
        self.filters.append(("in", k, v)); return self

    def gte(self, k, v):
        self.filters.append(("gte", k, v)); return self

    def lte(self, k, v):
        self.filters.append(("lte", k, v)); return self

    def ilike(self, k, v):
        self.filters.append(("ilike", k, v.strip("%").lower())); return self

    def order(self, k, desc=False):
        self._order = (k, desc); return self

    def range(self, a, b):
        self._range = (a, b); return self

    def limit(self, n):
        self._limit = n; return self

    def insert(self, rows):
        self._op, self._payload = "insert", rows; return self

    def delete(self):
        self._op = "delete"; return self

    def _keep(self, r):
        for op, k, v in self.filters:
            rv = r.get(k)
            if op == "eq" and rv != v:
                return False
            if op == "in" and rv not in v:
                return False
            if op == "gte" and not (rv is not None and str(rv) >= str(v)):
                return False
            if op == "lte" and not (rv is not None and str(rv) <= str(v)):
                return False
            if op == "ilike" and v not in str(rv or "").lower():
                return False
        return True

    def execute(self):
        key = (self.schema, self.table)
        rows = self.store.setdefault(key, [])
        if self._op == "insert":
            payload = self._payload if isinstance(self._payload, list) else [self._payload]
            for r in payload:
                rows.append(dict(r))
            self.log.append(("insert", key, payload))
            return _Resp(payload)
        if self._op == "delete":
            kept = [r for r in rows if not self._keep(r)]
            self.store[key] = kept
            self.log.append(("delete", key, list(self.filters)))
            return _Resp(None)
        out = [r for r in rows if self._keep(r)]
        if self._order:
            k, desc = self._order
            out = sorted(out, key=lambda r: (r.get(k) is None, r.get(k)), reverse=desc)
        if self._range:
            a, b = self._range
            out = out[a:b + 1]
        if self._limit is not None:
            out = out[: self._limit]
        return _Resp(out)


class _Table:
    def __init__(self, store, schema, table, log):
        self.store, self.schema, self.table, self.log = store, schema, table, log

    def select(self, *a, **k):
        return _Q(self.store, self.schema, self.table, self.log).select(*a, **k)

    def insert(self, rows):
        return _Q(self.store, self.schema, self.table, self.log).insert(rows)

    def delete(self):
        return _Q(self.store, self.schema, self.table, self.log).delete()


class _Schema:
    def __init__(self, store, schema, log):
        self.store, self.schema, self.log = store, schema, log

    def table(self, name):
        return _Table(self.store, self.schema, name, self.log)


class FakeClient:
    def __init__(self, store=None):
        self.store = store if store is not None else {}
        self.log = []

    def schema(self, name):
        return _Schema(self.store, name, self.log)


class _BrokenTable:
    def select(self, *a, **k):
        raise Exception('PGRST205 relation "commcalc.vip_invoices" does not exist')


class BrokenVipInvoicesClient(FakeClient):
    def schema(self, name):
        real = super().schema(name)
        if name == "commcalc":
            orig_table = real.table

            def table(tname):
                if tname == "vip_invoices":
                    return _BrokenTable()
                return orig_table(tname)
            real.table = table
        return real


import app.modules.asset.invoice_due as INV  # noqa: E402


def iso(d):
    return d.isoformat()


# ═══════════════════════════════════════════════════════════════════════════════════════════════
print("A. invoice_due_allowed — pure permission gate")
ok("super_admin -> allow", INV.invoice_due_allowed({"super_admin": True}))
ok("scope=='all' -> allow", INV.invoice_due_allowed({"perms": {"scope": "all"}}))
ok("role=='admin' -> allow", INV.invoice_due_allowed({"role": "Admin"}))
ok("grant key in perms.modules -> allow",
   INV.invoice_due_allowed({"perms": {"modules": ["asset_invoice_due"]}}))
ok("grant key truthy in perms.data -> allow",
   INV.invoice_due_allowed({"perms": {"data": {"asset_invoice_due": True}}}))
ok("caller=None (RBAC off / unresolvable token) -> degrade OPEN", INV.invoice_due_allowed(None))
ok("ordinary caller, no grant -> DENY",
   not INV.invoice_due_allowed({"role": "rep", "perms": {"scope": "store"}}))

# ═══════════════════════════════════════════════════════════════════════════════════════════════
print("\nB. _period_variants — both spellings, PURE, idempotent on garbage input")
ok("'June 2026' -> adds '2026-06'", "2026-06" in INV._period_variants("June 2026"))
ok("'2026-06' -> adds 'June 2026'", "June 2026" in INV._period_variants("2026-06"))
ok("garbage -> just itself, never raises", INV._period_variants("garbage") == ["garbage"])
ok("empty -> []", INV._period_variants("") == [])


def base_store():
    return {
        ("commcalc", "vip_invoices"): [],
        ("commcalc", "vip_invoice_devices"): [],
        ("commcalc", "asset_ledger"): [],
        ("commcalc", "raw_payment_detail"): [],
        ("commcalc", "commission_leg_config"): [],
        ("commcalc", "commission_leg_label_map"): [],
        ("commcalc", "flags"): [],
    }


def mk_invoice(org, vip_id, inv_no, location, status, due, grand_total, period="July 2026",
              pm=7, py=2026):
    return {"org_id": org, "vip_id": vip_id, "invoice_number": inv_no, "order_number": f"O{vip_id}",
            "location": location, "status": status, "grand_total": grand_total, "sub_total": grand_total,
            "shipping": 0, "discount": 0, "other_cost": 0, "other_deductions": 0, "tax": 0,
            "created_on": "2026-07-01T00:00:00", "due_date": due, "period": period,
            "period_month": pm, "period_year": py}


def mk_device(org, vip_id, inv_no, location, serial, imei=None, product="iPhone 15"):
    return {"org_id": org, "vip_invoice_id": vip_id, "invoice_number": inv_no, "location": location,
            "created_on": "2026-07-01T00:00:00", "serial": serial, "product_name": product,
            "imei": imei or serial, "sim": None, "period": "July 2026", "period_month": 7, "period_year": 2026}


def mk_asset(org, esn_imei, store, market, sold_date=None, reimb=0, owed=500.0, model="iPhone 15"):
    return {"org_id": org, "esn_imei": esn_imei, "store": store, "market": market,
            "device_model": model, "category": "On Inventory" if not sold_date else "Sold",
            "date_sold": sold_date, "reimbursement": reimb,
            "reimbursement_date": "2026-07-10" if reimb else None,
            "owed_to_vip": owed, "selling_price": 600.0 if sold_date else None, "status": "Open"}


def mk_pay(org, imei, ptype, amount, period="July 2026"):
    return {"org_id": org, "imei": imei, "payment_type": ptype, "amount": amount,
            "payment_date": "2026-07-15", "period": period}


class _AuthNone:
    pass


AUTH = ""   # empty header -> _uid_from_token returns falsy -> caller=None -> RBAC-plumbing branch


# ═══════════════════════════════════════════════════════════════════════════════════════════════
print("\nC. per-invoice rollup — sold / reimbursed / not-sold / M1 commission / net-due estimate")
store = base_store()
store[("commcalc", "vip_invoices")] = [
    mk_invoice(ORG_A, 1001, "INV-1001", "1800 Great Neck Rd", "Open", iso(TODAY + timedelta(days=3)), 1500.0),
]
store[("commcalc", "vip_invoice_devices")] = [
    mk_device(ORG_A, 1001, "INV-1001", "1800 Great Neck Rd", "111111111111111"),  # sold + reimbursed + commission
    mk_device(ORG_A, 1001, "INV-1001", "1800 Great Neck Rd", "222222222222222"),  # sold, not reimbursed
    mk_device(ORG_A, 1001, "INV-1001", "1800 Great Neck Rd", "333333333333333"),  # not sold (on inventory)
    mk_device(ORG_A, 1001, "INV-1001", "1800 Great Neck Rd", "444444444444444"),  # unmatched (no asset row)
]
store[("commcalc", "asset_ledger")] = [
    mk_asset(ORG_A, "111111111111111", "1800 Great Neck Rd", "LI", sold_date="2026-07-05", reimb=75.0),
    mk_asset(ORG_A, "222222222222222", "1800 Great Neck Rd", "LI", sold_date="2026-07-06", reimb=0),
    mk_asset(ORG_A, "333333333333333", "1800 Great Neck Rd", "LI", sold_date=None, reimb=0),
]
store[("commcalc", "raw_payment_detail")] = [
    mk_pay(ORG_A, "111111111111111", "New Activation Bounty - Month 1", 30.0),
    mk_pay(ORG_A, "111111111111111", "New Activation Bounty - Month 2", 10.0),   # trailing, excluded
    mk_pay(ORG_A, "111111111111111", "Boost Auto Top-Up", 5.0),                 # unsplit, excluded
]
c = FakeClient(store=store)
INV.sb = lambda: c
out = INV.invoice_due_list(org_id=ORG_A, authorization=AUTH)
ok("available=True, one invoice row", out["available"] and len(out["rows"]) == 1, out)
row = out["rows"][0]
ok("device_count=4, matched=3, unmatched=1", row["device_count"] == 4 and row["matched_count"] == 3
   and row["unmatched_count"] == 1, row)
ok("sold_count=2, not_sold_count=1", row["sold_count"] == 2 and row["not_sold_count"] == 1, row)
ok("reimbursed_count=1", row["reimbursed_count"] == 1, row)
ok("commission_earned_m1 = 30.0 ONLY (Month-2 and unlabeled excluded)",
   row["commission_earned_m1"] == 30.0, row)
ok("net_due_estimate = grand_total - commission_earned_m1 = 1470.0",
   row["net_due_estimate"] == 1470.0, row)
ok("net_due_estimate_note names it INFO ONLY / UNVERIFIED",
   "INFO ONLY" in row["net_due_estimate_note"] and "UNVERIFIED" in row["net_due_estimate_note"], row)
ok("basis_note names residual/M2-M12 exclusion", "M2-12" in out["basis_note"].replace(" ", "")
   or "Residual" in out["basis_note"] or "residual" in out["basis_note"], out["basis_note"])
ok("market resolved from matched devices ('LI', mode of the 3 matched rows)", row["market"] == "LI", row)
ok("READ endpoint wrote nothing (no insert/delete logged)", c.log == [], c.log)

# ═══════════════════════════════════════════════════════════════════════════════════════════════
print("\nD. multi-tenant scoping — org A and org B never bleed, even with IDENTICAL serials")
store2 = base_store()
store2[("commcalc", "vip_invoices")] = [
    mk_invoice(ORG_A, 2001, "A-2001", "Store A", "Open", iso(TODAY + timedelta(days=5)), 900.0),
    mk_invoice(ORG_B, 2001, "B-2001", "Store B", "Open", iso(TODAY + timedelta(days=5)), 111.0),
]
store2[("commcalc", "vip_invoice_devices")] = [
    mk_device(ORG_A, 2001, "A-2001", "Store A", "999999999999999"),
    mk_device(ORG_B, 2001, "B-2001", "Store B", "999999999999999"),   # SAME serial, different org
]
store2[("commcalc", "asset_ledger")] = [
    mk_asset(ORG_A, "999999999999999", "Store A", "NJ", sold_date="2026-07-01", reimb=0),
    mk_asset(ORG_B, "999999999999999", "Store B", "NY", sold_date=None, reimb=0),
]
store2[("commcalc", "raw_payment_detail")] = [
    mk_pay(ORG_A, "999999999999999", "New Activation Bounty - Month 1", 40.0),
    mk_pay(ORG_B, "999999999999999", "New Activation Bounty - Month 1", 999.0),
]
c2 = FakeClient(store=store2)
INV.sb = lambda: c2
out_a = INV.invoice_due_list(org_id=ORG_A, authorization=AUTH)
out_b = INV.invoice_due_list(org_id=ORG_B, authorization=AUTH)
ok("org A sees only its own invoice", len(out_a["rows"]) == 1 and out_a["rows"][0]["invoice_number"] == "A-2001", out_a)
ok("org B sees only its own invoice", len(out_b["rows"]) == 1 and out_b["rows"][0]["invoice_number"] == "B-2001", out_b)
ok("org A commission is its own $40, NOT org B's $999 (identical serial, no cross-tenant bleed)",
   out_a["rows"][0]["commission_earned_m1"] == 40.0, out_a["rows"][0])
ok("org B commission is its own $999", out_b["rows"][0]["commission_earned_m1"] == 999.0, out_b["rows"][0])
ok("org A sold_count reflects ORG A's asset row (sold), not org B's (unsold)",
   out_a["rows"][0]["sold_count"] == 1, out_a["rows"][0])
ok("org B sold_count reflects ORG B's asset row (unsold)", out_b["rows"][0]["not_sold_count"] == 1, out_b["rows"][0])

# ═══════════════════════════════════════════════════════════════════════════════════════════════
print("\nE. filters — status / date range / store / market (incl. NO_MARKET_SENTINEL)")
store3 = base_store()
store3[("commcalc", "vip_invoices")] = [
    mk_invoice(ORG_A, 3001, "OPEN-SOON", "Store X", "Open", iso(TODAY + timedelta(days=2)), 100.0),
    mk_invoice(ORG_A, 3002, "OPEN-FAR", "Store X", "Open", iso(TODAY + timedelta(days=90)), 200.0),
    mk_invoice(ORG_A, 3003, "PAID", "Store Y", "Paid In Full", iso(TODAY + timedelta(days=2)), 300.0),
]
store3[("commcalc", "vip_invoice_devices")] = [
    mk_device(ORG_A, 3001, "OPEN-SOON", "Store X", "aaaaaaaaaaaaaaa"),
    mk_device(ORG_A, 3002, "OPEN-FAR", "Store X", "bbbbbbbbbbbbbbb"),
    mk_device(ORG_A, 3003, "PAID", "Store Y", "ccccccccccccccc"),
]
store3[("commcalc", "asset_ledger")] = [
    mk_asset(ORG_A, "aaaaaaaaaaaaaaa", "Store X", "NJ"),
    mk_asset(ORG_A, "bbbbbbbbbbbbbbb", "Store X", None),   # no market -> "(no market)" bucket
    mk_asset(ORG_A, "ccccccccccccccc", "Store Y", "NY"),
]
c3 = FakeClient(store=store3)
INV.sb = lambda: c3
out = INV.invoice_due_list(org_id=ORG_A, status="Open", authorization=AUTH)
ok("status=Open excludes the Paid In Full invoice", {r["invoice_number"] for r in out["rows"]} == {"OPEN-SOON", "OPEN-FAR"}, out["rows"])
out = INV.invoice_due_list(org_id=ORG_A, date_from=iso(TODAY), date_to=iso(TODAY + timedelta(days=7)), authorization=AUTH)
ok("due-date range narrows to the 2 invoices due within 7 days",
   {r["invoice_number"] for r in out["rows"]} == {"OPEN-SOON", "PAID"}, out["rows"])
out = INV.invoice_due_list(org_id=ORG_A, store="Store Y", authorization=AUTH)
ok("store filter narrows to Store Y only", {r["invoice_number"] for r in out["rows"]} == {"PAID"}, out["rows"])
out = INV.invoice_due_list(org_id=ORG_A, market="NJ", authorization=AUTH)
ok("market=NJ narrows to OPEN-SOON only", {r["invoice_number"] for r in out["rows"]} == {"OPEN-SOON"}, out["rows"])
out = INV.invoice_due_list(org_id=ORG_A, market=INV.NO_MARKET_SENTINEL, authorization=AUTH)
ok("market=(no market) sentinel narrows to OPEN-FAR only (its device has no market)",
   {r["invoice_number"] for r in out["rows"]} == {"OPEN-FAR"}, out["rows"])

# ═══════════════════════════════════════════════════════════════════════════════════════════════
print("\nF. degrade — vip_invoices table missing (migration 008 not run) -> honest note, never a 500")
cb = BrokenVipInvoicesClient(store=base_store())
INV.sb = lambda: cb
out = INV.invoice_due_list(org_id=ORG_A, authorization=AUTH)
ok("available=False with an explanatory note, empty rows (no crash)",
   out["available"] is False and out["rows"] == [] and "VIP Wireless Workbook" in out["note"], out)
out = INV.invoice_due_filter_options(org_id=ORG_A, authorization=AUTH)
ok("filter-options degrades the same way", out["available"] is False, out)

# ═══════════════════════════════════════════════════════════════════════════════════════════════
print("\nG. detail endpoint — per-IMEI drill-down + period-total commission footer (info-only)")
store4 = base_store()
store4[("commcalc", "vip_invoices")] = [
    mk_invoice(ORG_A, 4001, "INV-4001", "Store Z", "Open", iso(TODAY + timedelta(days=1)), 1000.0, period="August 2026", pm=8, py=2026),
]
store4[("commcalc", "vip_invoice_devices")] = [
    mk_device(ORG_A, 4001, "INV-4001", "Store Z", "aaa111111111111"),
]
store4[("commcalc", "asset_ledger")] = [
    mk_asset(ORG_A, "aaa111111111111", "Store Z", "NJ", sold_date="2026-08-01", reimb=20.0),
]
store4[("commcalc", "raw_payment_detail")] = [
    mk_pay(ORG_A, "aaa111111111111", "New Activation Bounty - Month 1", 25.0, period="August 2026"),
    # a DIFFERENT device, elsewhere in the org, also earning M1 commission the same period —
    # must show up in the period total but NOT in this invoice's own commission figure.
    mk_pay(ORG_A, "zzz999999999999", "BR BYOD SPIFF - Month 1", 60.0, period="2026-08"),  # other spelling
]
c4 = FakeClient(store=store4)
INV.sb = lambda: c4
detail = INV.invoice_due_detail(4001, org_id=ORG_A, authorization=AUTH)
ok("one device row, matched, sold, reimbursed", len(detail["devices"]) == 1
   and detail["devices"][0]["matched"] and detail["devices"][0]["sold"] and detail["devices"][0]["reimbursed"], detail)
ok("device's own M1 commission = 25.0, trailing/unsplit = 0", detail["devices"][0]["commission_m1"] == 25.0
   and detail["devices"][0]["commission_trailing"] == 0.0 and detail["devices"][0]["commission_unsplit"] == 0.0, detail["devices"][0])
ok("commission_lines carries the raw ePay line the $25 traces to (never a black box)",
   detail["devices"][0]["commission_lines"] and detail["devices"][0]["commission_lines"][0]["type"]
   == "New Activation Bounty - Month 1", detail["devices"][0])
foot = detail["period_commission_footer"]
ok("invoice's own commission in the footer = 25.0", foot["invoice_commission_m1"] == 25.0, foot)
ok("period total picks up BOTH spellings ('August 2026' AND '2026-08') = 25 + 60 = 85.0",
   foot["period_total_commission_m1"] == 85.0, foot)
ok("difference = period total minus this invoice's = 60.0", foot["difference"] == 60.0, foot)
ok("footer explicitly labeled INFO ONLY", "INFO ONLY" in foot["note"], foot)

# ═══════════════════════════════════════════════════════════════════════════════════════════════
print("\nH. flags sync — delete-first-by-source, overdue=critical, due-soon=warning, "
      "Voided/Paid untouched, other sources' flags never touched")
store5 = base_store()
store5[("commcalc", "vip_invoices")] = [
    mk_invoice(ORG_A, 5001, "OVERDUE-1", "S1", "Open", iso(TODAY - timedelta(days=2)), 500.0),
    mk_invoice(ORG_A, 5002, "SOON-1", "S1", "Open", iso(TODAY + timedelta(days=3)), 200.0),
    mk_invoice(ORG_A, 5003, "FAR-1", "S1", "Open", iso(TODAY + timedelta(days=30)), 300.0),
    mk_invoice(ORG_A, 5004, "VOIDED-BUT-OVERDUE", "S1", "Voided", iso(TODAY - timedelta(days=10)), 999.0),
    mk_invoice(ORG_A, 5005, "PAID-BUT-OVERDUE", "S1", "Paid In Full", iso(TODAY - timedelta(days=10)), 999.0),
]
store5[("commcalc", "flags")] = [
    {"org_id": ORG_A, "source": "asset_appeal", "flag_type": "Appeal Denied", "period": "July 2026"},
]
c5 = FakeClient(store=store5)
INV.sb = lambda: c5
result = INV.sync_invoice_due_flags(org_id=ORG_A, authorization=AUTH)
ok("sync reports 2 flags written (overdue + due-soon; far/voided/paid excluded)",
   result["flags_written"] == 2, result)
written = [r for r in c5.store[("commcalc", "flags")] if r.get("source") == "asset_invoice_due"]
ok("overdue invoice -> severity critical", any(r["severity"] == "critical" for r in written), written)
ok("due-soon invoice -> severity warning", any(r["severity"] == "warning" for r in written), written)
ok("far-out invoice produced NO flag", not any("FAR-1" in (r.get("description") or "") for r in written), written)
ok("Voided invoice produced NO flag despite being overdue",
   not any("VOIDED" in (r.get("description") or "") for r in written), written)
ok("Paid In Full invoice produced NO flag despite being overdue",
   not any("PAID-BUT-OVERDUE" in (r.get("description") or "") for r in written), written)
other_flags = [r for r in c5.store[("commcalc", "flags")] if r.get("source") == "asset_appeal"]
ok("a DIFFERENT source's pre-existing flag is untouched (delete-first is source-scoped)",
   len(other_flags) == 1, other_flags)

# re-run sync to prove delete-first-by-source is idempotent (no duplication on a second run)
result2 = INV.sync_invoice_due_flags(org_id=ORG_A, authorization=AUTH)
written2 = [r for r in c5.store[("commcalc", "flags")] if r.get("source") == "asset_invoice_due"]
ok("re-running sync does not duplicate rows (delete-first really deletes)", len(written2) == 2, written2)

# ═══════════════════════════════════════════════════════════════════════════════════════════════
print(f"\n{'='*70}\nTOTAL: {PASS} passed, {FAIL} failed\n{'='*70}")
sys.exit(1 if FAIL else 0)
