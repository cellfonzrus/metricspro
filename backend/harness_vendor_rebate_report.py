"""Offline harness for the vendor rebate/commission report parser (no file, no DB).

Feeds SYNTHETIC rows (fake data) shaped like the RQ / WirelessDotCom Vendor Rebate History export and
asserts the owner-confirmed classification (device rebate vs commission vs trade-in vs totals-row),
GP = Unit Rebate − Related Cost on device lines, phone/date coercion, and the store/period aggregation.

Run:  cd backend && python harness_vendor_rebate_report.py
"""
import sys
from datetime import datetime

from app.modules.pos import vendor_rebate_report as V

_p = _f = 0


def ok(name, cond):
    global _p, _f
    if cond:
        _p += 1; print(f"  ok   {name}")
    else:
        _f += 1; print(f"  FAIL {name}")


def row(**k):
    return k


def main():
    # classification
    ok("device rebate classified", V.classify("Device Payment Agreement Rebate Amount") == V.KIND_DEVICE_REBATE)
    ok("commission classified", V.classify("New Activation (Rate Plan Rebate)") == V.KIND_COMMISSION)
    ok("upgrade ispu = commission", V.classify("Upgrade - ISPU") == V.KIND_COMMISSION)
    ok("trade-in classified", V.classify("Verizon Carrier Trade In Rebate") == V.KIND_TRADE_IN)
    ok("blank name = skip (totals row)", V.classify("") == V.KIND_SKIP)
    ok("case-insensitive device rebate", V.classify("device payment agreement REBATE amount") == V.KIND_DEVICE_REBATE)

    # coercion
    ok("phone from 10-digit tracking", V._phone(9295452246) == "9295452246")
    ok("phone strips leading 1", V._phone("19295452246") == "9295452246")
    ok("phone rejects alphanumeric ref", V._phone("E254401434291") is None)
    ok("iso date from datetime", V._iso_date(datetime(2025, 9, 2, 17, 47)) == "2025-09-02")
    ok("iso date from m/d/y", V._iso_date("5/5/2025") == "2025-05-05")
    ok("money parses", V.money("$1,299.99") == 1299.99)

    rows = [
        # device rebate, positive GP (rebate 818 - cost 768 = 50)
        row(product_name="Device Payment Agreement Rebate Amount", unit_rebate=818, related_cost=768,
            customer="ALISHER TULYAGANOV", tracking_no=9295452246, customer_identifier="0489565971",
            device_name="SAMSUNG GALAXY S25", device_sku="CLVZSA006416", imei="350024515330906",
            rate_plan="Smart Phone Rate Plan (DPA)", sold_on=datetime(2025, 9, 2), store_name="WZ1321", zip="11214"),
        # device rebate, NEGATIVE GP (rebate 120 - cost 212.8 = -92.8)
        row(product_name="Device Payment Agreement Rebate Amount", unit_rebate=120, related_cost=212.8,
            customer="JODY MILLER", tracking_no=7188133749, imei="016237004081584",
            device_name="KYOCERA DURAXV", sold_on=datetime(2025, 10, 8), store_name="WZ1321"),
        # commission line (rate-plan) — no cost offset
        row(product_name="New Activation (Rate Plan Rebate)", unit_rebate=120, related_cost=0,
            customer="RUTH KAHN", tracking_no=9295493052, imei="350065655571106",
            rate_plan="New Activation Rate Plan", sold_on=datetime(2025, 9, 3), store_name="WZ1321"),
        # upgrade commission
        row(product_name="Upgrade - ISPU", unit_rebate=75, related_cost=0,
            customer="A B", tracking_no=7185106816, imei="352404328688906",
            sold_on=datetime(2025, 11, 1), store_name="WZ1321"),
        # trade-in pass-through (no P&L income), alphanumeric tracking (no phone)
        row(product_name="Verizon Carrier Trade In Rebate", unit_rebate=0, related_cost=0,
            customer="AARON ADLER", tracking_no="E254401434291", sold_on=datetime(2025, 9, 1), store_name="WZ1321"),
        # totals row (blank product name, huge amount) — must be dropped
        row(product_name=None, unit_rebate=2001293.16, related_cost=0, store_name="WZ1321"),
    ]
    res = V.normalize_report(rows)
    t = res["totals"]

    ok("totals row dropped", t["dropped"]["totals_row"] == 1)
    ok("trade-in counted as pass-through", t["dropped"]["trade_in"] == 1)
    ok("activations = non-trade-in, non-totals", t["activations"] == 4)  # 5 kept − 1 trade-in
    ok("commission income = 120 + 75", t["commission_income"] == 195.0)
    ok("device rebate total = 818 + 120", t["device_rebate"] == 938.0)
    ok("device cost total = 768 + 212.8", t["device_cost"] == 980.8)
    ok("device GP = 50 + (-92.8) = -42.8 (net negative honored)", t["device_gp"] == -42.8)
    ok("gross profit = device GP + commission", t["gross_profit_total"] == round(-42.8 + 195.0, 2))

    acts = {a["customer_name"]: a for a in res["activations"]}
    d = acts["ALISHER TULYAGANOV"]
    ok("device line GP computed", d["device_gp"] == 50.0 and d["commission"] == 0.0)
    ok("device line phone from tracking", d["phone"] == "9295452246")
    ok("device line account_number from identifier", d["account_number"] == "0489565971")
    ok("device line period", d["period"] == "2025-09")
    c = acts["RUTH KAHN"]
    ok("commission line: commission set, no device GP", c["commission"] == 120.0 and d["device_gp"] != c["device_gp"] or c["device_gp"] == 0.0)
    ti = acts["AARON ADLER"]
    ok("trade-in: no commission, no phone (alphanumeric ref)", ti["commission"] == 0.0 and ti["phone"] is None)

    # aggregation
    sp = res["summary_by_store_period"]
    sep = {(s["store_name"], s["period"]): s for s in sp}
    ok("sep bucket 2025-09 commission", sep[("WZ1321", "2025-09")]["commission_income"] == 120.0)
    ok("sep bucket 2025-09 device GP", sep[("WZ1321", "2025-09")]["device_gp"] == 50.0)
    ok("distinct customers", t["distinct_customers"] == 5)

    fam = {f["product_name"]: f for f in res["families"]}
    ok("family kinds", fam["Device Payment Agreement Rebate Amount"]["kind"] == "device_rebate"
       and fam["Upgrade - ISPU"]["kind"] == "commission")

    # ── write path (stub client — no DB) ───────────────────────────────────────────────────────
    store = {
        ("pos", "customers"): [{"id": "c-existing", "phone_primary": "9295493052"}],  # RUTH already exists
        ("pos", "activations"): [],
        ("commcalc", "activation_rebate_ledger"): [],
    }
    client = _StubClient(store)
    out = V.import_report(client, "org1", res, store_code="WZ1321")
    custs = store[("pos", "customers")]
    ok("existing customer not duplicated (RUTH skipped)", sum(1 for c in custs if c.get("phone_primary") == "9295493052") == 1)
    ok("new customers created for new phones", out["customers_created"] == 3)  # ALISHER, JODY, A B (RUTH exists, AARON no phone)
    acts = store[("pos", "activations")]
    ok("activations created (trade-in excluded)", out["activations_created"] == 4 and len(acts) == 4)
    ok("activation linked to customer_id", any(a.get("customer_id") == "c-existing" for a in acts))
    ok("activation carrier + model mapped", all(a.get("carrier") == "Verizon" for a in acts) and any("SAMSUNG" in (a.get("phone_model") or "") for a in acts))
    led = store[("commcalc", "activation_rebate_ledger")]
    ok("ledger row per store/period", len(led) == out["ledger_periods"] and len(led) >= 1)
    ok("ledger commission + rebate populated", any(r["commission_amount"] == 120.0 for r in led) and any(r["device_rebate_amount"] == 818.0 for r in led))

    # idempotent re-run: same file → no new customers/activations, ledger upserts in place
    out2 = V.import_report(client, "org1", res, store_code="WZ1321")
    ok("re-run creates no duplicate customers", out2["customers_created"] == 0)
    ok("re-run creates no duplicate activations", out2["activations_created"] == 0)
    ok("re-run ledger stays one row per store/period", len(store[("commcalc", "activation_rebate_ledger")]) == len(led))

    print(f"\n{_p} passed, {_f} failed")
    return 1 if _f else 0


# ── Minimal Supabase-style stub (schema().table().select()/.insert()/.upsert()/.eq()/.range()) ────
class _Result:
    def __init__(self, data):
        self.data = data


class _Query:
    def __init__(self, rows):
        self._rows = rows

    def select(self, *_a, **_k):
        return self

    def eq(self, *_a, **_k):
        return self

    def range(self, lo, hi):
        self._slice = (lo, hi)
        return self

    def execute(self):
        pend = getattr(self, "_pending", None)
        if pend is not None:
            self._pending = None
            return _Result([dict(r) for r in pend])
        lo, hi = getattr(self, "_slice", (0, 10 ** 9))
        return _Result([dict(r) for r in self._rows[lo:hi + 1]])

    def insert(self, rows):
        rows = rows if isinstance(rows, list) else [rows]
        inserted = []
        for r in rows:
            row = dict(r)
            row.setdefault("id", f"id{len(self._rows)}")
            self._rows.append(row)
            inserted.append(row)
        self._pending = inserted
        return self

    def upsert(self, rows, on_conflict=None):
        keys = (on_conflict or "").split(",") if on_conflict else []
        rows = rows if isinstance(rows, list) else [rows]
        touched = []
        for r in rows:
            match = None
            if keys:
                for existing in self._rows:
                    if all(existing.get(k) == r.get(k) for k in keys):
                        match = existing
                        break
            if match is not None:
                match.update(r)
                touched.append(match)
            else:
                row = dict(r); row.setdefault("id", f"id{len(self._rows)}")
                self._rows.append(row); touched.append(row)
        self._pending = touched
        return self


class _Schema:
    def __init__(self, store, schema):
        self._store, self._schema = store, schema

    def table(self, name):
        return _Query(self._store.setdefault((self._schema, name), []))


class _StubClient:
    def __init__(self, store):
        self._store = store

    def schema(self, name):
        return _Schema(self._store, name)


if __name__ == "__main__":
    sys.exit(main())
