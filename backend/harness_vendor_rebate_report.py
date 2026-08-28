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

    print(f"\n{_p} passed, {_f} failed")
    return 1 if _f else 0


if __name__ == "__main__":
    sys.exit(main())
