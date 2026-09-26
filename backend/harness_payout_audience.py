"""PROOF — the EMPLOYEE payout report shows only the lines that paid and no carrier commission; the MANAGER view
is unchanged (owner 2026-09-26: "on the employee commission payout report we only need o show the line they are
getting paid and other lines should be hidden and carrier commission not be displayed").

DB-free: the REAL `/commission-explain`, `/commission-statement` and `/commissions/{period}` handlers over the REAL
engine (plan pay gate + activation events), on an in-memory read-only client, with the two invoices the owner
pointed at, line for line as they sit in raw_sales (org f4f1c16e, customer omitted):
  · Z1321IN11092 (2026-07-02) — one activation; 3 activation-type lines (carrier $150 / $0 / $45);
  · Z1321IN10551 (2026-05-02 shape) — one activation; 4 activation-type lines (carrier $150, and the
    "ADD A LINE SMART PHONE KICKER" pair $0 / $100 — the pair is one line's tracking SKU + rebate SKU:
    879 of 882 phone lines live carry it as exactly that pair).

  A. MANAGER (default): every matched line with Price / GP (the carrier $), the ⛔ reasons — BYTE-IDENTICAL to
     the handler before this change (audience absent == audience=manager == the drill-down itself).
  B. EMPLOYEE: one paid row per invoice for $10; no carrier field anywhere in the payload (Price, GP, ⛔,
     would-have-paid, data quality, MA cross-reference); every total unchanged to the cent.
  C. A SELF-scoped caller is ALWAYS served the employee form (asking for 'manager' changes nothing) and may not
     read another rep (403).
  D. The rep rows (`/commissions/{period}`) and the Incentive Statement: the employee form drops the
     carrier-paid dealer figures and the commission-ledger buckets; the manager form is unchanged.
  E. `is_paid_line` reads the engine's verdict: suppressed / non-qualifying / $0 lines are not paid; a flat
     bonus and a negative line are.

    python3 backend/harness_payout_audience.py
"""
import asyncio
import copy
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from app.modules.commcalc import payout_audience as PA              # noqa: E402
from app.modules.commcalc import router as R                        # noqa: E402

ORG = "00000000-0000-0000-0000-0000000a0d01"
PERIOD = "July 2026"
P = F = 0


def check(label, ok, detail=""):
    global P, F
    if ok:
        P += 1
        print("  PASS  %s" % label)
    else:
        F += 1
        print("  FAIL  %s   %s" % (label, str(detail)[:300]))


def section(t):
    print("\n── %s %s" % (t, "─" * max(0, 96 - len(t))))


class _Q:
    def __init__(self, rows):
        self._rows = list(rows)

    def select(self, *a, **k):
        return self

    def eq(self, col, val):
        self._rows = [r for r in self._rows if str(r.get(col)) == str(val)]
        return self

    def in_(self, col, vals):
        vs = {str(v) for v in vals}
        self._rows = [r for r in self._rows if str(r.get(col)) in vs]
        return self

    def order(self, col, desc=False):
        self._rows = sorted(self._rows, key=lambda r: (r.get(col) or 0), reverse=desc)
        return self

    def _noop(self, *a, **k):
        return self
    neq = not_ = is_ = gte = lte = gt = lt = ilike = like = limit = range = _noop

    def execute(self):
        return type("R", (), {"data": copy.deepcopy(self._rows), "count": len(self._rows)})()


class FakeClient:
    def __init__(self, tables):
        self._t = tables

    def schema(self, name):
        t = self._t

        class S:
            def table(self, n):
                return _Q(t.get((name, n), []))

            def rpc(self, *a, **k):
                return type("R", (), {"execute": lambda s=None: type("R2", (), {"data": []})()})()
        return S()

    def table(self, n):
        return _Q(self._t.get(("public", n), []))

    def rpc(self, *a, **k):
        return type("R", (), {"execute": lambda s=None: type("R2", (), {"data": []})()})()


TENANT_ADR = {"fields": ["category", "product_desc"],
              "tokens": {"byod": ["customer provided", "byod", "customer owned"], "port": ["port"],
                         "upgrade": ["upgrade"], "activation": ["new activation", "add a line", "new act", "prepaid"],
                         "hardware_only": ["hardware only", "prepaid"]}}
RULES = [{"id": "RACT", "label": "Activation", "match_field": "activation_bucket", "match_op": "in",
          "match_value": "premium,byod", "qualifies": True, "payout_kind": "flat_per_unit", "amount": 10.0,
          "pct": 0.0, "tiered": False, "unit_basis": None, "sort": 0},
         {"id": "RUPG", "label": "Upgrade", "match_field": "activation_bucket", "match_op": "in",
          "match_value": "upgrade", "qualifies": True, "payout_kind": "flat_per_unit", "amount": 5.0,
          "pct": 0.0, "tiered": False, "unit_basis": None, "sort": 1}]
REP = "Jona Sejat"


def L(tid, product, category, serial="", ext=0.0, voided="No"):
    return {"org_id": ORG, "period": PERIOD, "trans_id": tid, "trans_date": "2026-07-02", "store": "Store 1321",
            "salesperson": REP, "user_login": REP, "department": "Activations (Price Sheet)",
            "category": ">> Activations (Price Sheet) >> Carrier >> " + category, "product_desc": product,
            "serial_1": serial, "mdn": "", "ext_price": ext, "gp": ext, "voided": voided, "trans_type": None,
            "contract_type": "", "quantity": 1.0}


P1 = "9297458084"
Z11092 = [
    L("Z1321IN11092", "iPhone Rate Plan (DPA)", "Rate Plans"),
    L("Z1321IN11092", "Device Payment Agreement Loan Number", "Integration SKUs", "1852600095"),
    L("Z1321IN11092", "Device Payment Agreement Rebate Amount", "Integration SKUs", P1, 1110.0),
    L("Z1321IN11092", "DPA New Act iPhone (Rate Plan Rebate)", "Rate Plan Rebates", P1, 150.0),
    L("Z1321IN11092", "VERIZON BUSINESS TRACKING NEW ACTIVATION", "Integration SKUs >> Business Skus", P1),
    L("Z1321IN11092", "SMB NEW ACCOUNT SMART PHONE KICKER", "VZ Kickers", P1, 170.0),
    L("Z1321IN11092", "32066 My Biz Plan - New Act", "Additional Spiffs (Promotions)", P1, 45.0),
    L("Z1321IN11092", "APPLE IPHONE 17 PRO 256GB SILVER", "Cellular Equipment >> SmartPhones", "354198260632397", 1110.0),
]
P2 = "3478963977"
Z10551 = [
    L("Z1321IN10551", "iPhone Rate Plan (DPA)", "Rate Plans"),
    L("Z1321IN10551", "Device Payment Agreement Financed Amount", "Integration SKUs", P2, -840.0, "Yes"),
    L("Z1321IN10551", "Device Payment Agreement Loan Number", "Integration SKUs", "1672844107"),
    L("Z1321IN10551", "Device Payment Agreement Rebate Amount", "Integration SKUs", P2, 840.0),
    L("Z1321IN10551", "DPA New Act iPhone (Rate Plan Rebate)", "Rate Plan Rebates", P2, 150.0),
    L("Z1321IN10551", "DPA Device Rebate", "Equip Rebates (VZ Commission)", P2),
    L("Z1321IN10551", "DECLINE PROTECTION COVERAGE", "Features >> Features - No SPF", P2),
    L("Z1321IN10551", "ADD A LINE SMART PHONE KICKER", "VZ Kickers", P2, 0.0),
    L("Z1321IN10551", "ADD A LINE SMART PHONE KICKER", "VZ Kickers", P2, 100.0),
    L("Z1321IN10551", "63215 Unlimited Welcome Smartphone/iPhone - New Act", "VZ Kickers", P2),
    L("Z1321IN10551", "APPLE IPHONE 17 256GB MIST BLUE", "Apple >> Apple Smartphones", "353502587453513", 840.0),
]
TABLES = {
    ("commcalc", "commission_plan"): [{"id": "FLAT", "org_id": ORG, "name": "Flat Comission", "is_active": True,
                                       "carrier_id": None, "base_tier_metric": "none", "commission_basis": "rules",
                                       "activation_source": "inherit"}],
    ("commcalc", "commission_rule"): [dict(r, org_id=ORG, plan_id="FLAT") for r in RULES],
    ("commcalc", "commission_tier"): [],
    ("commcalc", "commission_plan_assignment"): [{"id": "A1", "org_id": ORG, "plan_id": "FLAT", "scope": "default",
                                                  "scope_value": None}],
    ("commcalc", "accessory_config"): [{"org_id": ORG, "activation_details_rules": TENANT_ADR,
                                        "contract_type_map": {}, "activation_rules": []}],
    ("commcalc", "raw_sales"): Z11092 + Z10551,
    ("commcalc", "rep_commissions"): [{"org_id": ORG, "period": PERIOD, "epay_salesperson": REP, "storeops_name": REP,
                                       "store": "Store 1321", "total_payout": 20.0, "plan_comm": 20.0, "subtotal": 20.0,
                                       "boost_commission": 310.0, "boost_reimbursement": 12.5,
                                       "premium_acts": 2, "byod_acts": 0, "upgrade_acts": 0}],
}
_FAKE = FakeClient(TABLES)
R.sb = lambda: _FAKE
R.require_org = lambda org_id: None
_SELF = {"keys": None}
R._caller_rep_keys = lambda authorization, org_id: _SELF["keys"]
R._can_view_statement_held = lambda authorization, org_id: True      # the manager holds the grant


def explain(audience=""):
    return R.commission_explain(PERIOD, rep=REP, audience=audience, authorization="", org_id=ORG)


def act_lines(res, tid):
    for rb in (res.get("plan_component") or {}).get("rules") or []:
        if rb.get("rule_id") == "RACT":
            return [ln for ln in rb.get("lines") or [] if ln.get("trans_id") == tid]
    return []


# ═════════════════════════════════════════════════════════════════════════════════════════════════
section("A. MANAGER view — unchanged: every matched line with the carrier $")
m0, mm, md = explain(""), explain("manager"), explain("junk")
check("A1 audience absent == 'manager' == an unknown value — byte-identical JSON (the default is today's payload)",
      json.dumps(m0, sort_keys=True, default=str) == json.dumps(mm, sort_keys=True, default=str)
      == json.dumps(md, sort_keys=True, default=str))
check("A2 ...and it carries no `audience` key (the manager payload is exactly the pre-change one)", "audience" not in m0)
l1 = act_lines(m0, "Z1321IN11092")
check("A3 Z1321IN11092 manager: 3 lines with the carrier $ (Price 150 / 0 / 45), one paid $10, two ⛔",
      sorted(l["ext_price"] for l in l1) == [0.0, 45.0, 150.0] and sum(1 for l in l1 if l.get("suppressed")) == 2
      and round(sum(l["amount"] for l in l1), 2) == 10.0, [(l["product"], l["ext_price"], l["amount"]) for l in l1])
l2 = act_lines(m0, "Z1321IN10551")
check("A4 Z1321IN10551 manager: 4 lines — the owner's $150 / $0 / $100 visible, rep paid $10 once",
      sorted(l["ext_price"] for l in l2) == [0.0, 0.0, 100.0, 150.0] and round(sum(l["amount"] for l in l2), 2) == 10.0,
      [(l["product"], l["ext_price"], l["amount"]) for l in l2])
check("A5 the manager drill still carries the fields an employee may not see (the diagnostic keeps them)",
      bool(PA.disallowed_fields(m0)))
from app.modules.commcalc import commission_drilldown as _DD   # noqa: E402
_pre = _DD.explain_rep(_FAKE, ORG, PERIOD, REP, carrier_mode=R._resolve_carrier_mode([]),
                       identity_map=R._rep_canon_map(_FAKE, ORG))
check("A6 the manager payload == the drill-down the handler returned BEFORE this change (explain_rep, same inputs)",
      json.dumps(_pre, sort_keys=True, default=str) == json.dumps(m0, sort_keys=True, default=str))

# ═════════════════════════════════════════════════════════════════════════════════════════════════
section("B. EMPLOYEE view — one paid row per activation, no carrier commission anywhere")
e = explain("employee")
e1, e2 = act_lines(e, "Z1321IN11092"), act_lines(e, "Z1321IN10551")
check("B1 Z1321IN11092 employee: ONE row, $10.00 — the paid line, named by its activation",
      len(e1) == 1 and e1[0]["amount"] == 10.0 and e1[0]["event_key"] == P1, e1)
check("B2 Z1321IN10551 employee: ONE row, $10.00 — the two ⛔ kicker lines ($0 / $100) are gone",
      len(e2) == 1 and e2[0]["amount"] == 10.0 and e2[0]["event_key"] == P2, e2)
check("B3 the payload carries NO carrier-commission field (Price, GP, ⛔, would-have-paid, data quality, MA)",
      PA.disallowed_fields(e) == [], PA.disallowed_fields(e)[:5])
txt = json.dumps(e, default=str)
check("B4 the owner's carrier figures ($150 / $100 / $45 / $170) appear nowhere in the employee JSON",
      not any(f'"{k}": {v}' in txt for k in ("ext_price", "gp") for v in ("150.0", "100.0", "45.0", "170.0")))
check("B5 the payload says whom it was served for", e.get("audience") == "employee")
pm, pe = m0["plan_component"], e["plan_component"]
check("B6 totals UNCHANGED to the cent: total, base, tiered, qualifying units, every rule payout",
      all(pm[k] == pe[k] for k in ("total_payout", "base_payout", "tiered_payout", "qualifying_units"))
      and [r["payout"] for r in pm["rules"]] == [r["payout"] for r in pe["rules"]])
check("B7 Σ employee line $ == Σ manager line $ == Σ rule payouts ($20.00 — two activations)",
      PA.rule_line_total(e) == PA.rule_line_total(m0) == round(sum(r["payout"] for r in pm["rules"]), 2) == 20.0)
check("B8 the reconciliation (the paid rep_commissions figures) is identical",
      json.dumps(m0.get("reconciliation"), sort_keys=True) == json.dumps(e.get("reconciliation"), sort_keys=True))

# ═════════════════════════════════════════════════════════════════════════════════════════════════
section("C. a SELF-scoped caller (a rep) is always the employee, for their own rep only")
_SELF["keys"] = {REP.upper()}
s_as_manager = explain("manager")
check("C1 a rep asking for the MANAGER form still gets the employee form", s_as_manager.get("audience") == "employee"
      and PA.disallowed_fields(s_as_manager) == [])
try:
    R.commission_explain(PERIOD, rep="Someone Else", audience="manager", authorization="", org_id=ORG)
    check("C2 a rep reading ANOTHER rep's drill is refused", False)
except R.HTTPException as ex:
    check("C2 a rep reading ANOTHER rep's drill is refused 403", ex.status_code == 403)
rows_self = asyncio.run(R.get_commissions(PERIOD, authorization="", org_id=ORG))
check("C3 a rep's own /commissions row carries no carrier-paid dealer figure",
      rows_self and PA.disallowed_fields(rows_self[0], "rep_row") == [] and "boost_commission" not in rows_self[0])
_SELF["keys"] = None

# ═════════════════════════════════════════════════════════════════════════════════════════════════
section("D. rep rows and the Incentive Statement")
rm = asyncio.run(R.get_commissions(PERIOD, authorization="", org_id=ORG))
re_ = asyncio.run(R.get_commissions(PERIOD, authorization="", org_id=ORG, audience="employee"))
check("D1 manager rows unchanged (the dealer figures stay for the manager)", rm[0].get("boost_commission") == 310.0)
check("D2 employee rows drop boost_commission / boost_reimbursement and keep every pay field",
      "boost_commission" not in re_[0] and "boost_reimbursement" not in re_[0]
      and {k: v for k, v in rm[0].items() if k in PA.EMPLOYEE_REP_ROW_FIELDS} == re_[0]
      and PA.disallowed_fields(re_[0], "rep_row") == [])
R._statement_buckets = lambda client, org_id, period, rep, source_report="ma_daily_tx": {
    "commission": 310.0, "spiff": 0.0, "equipment_rebate": 150.0, "residual_monthly": 0.0, "chargeback": 0.0}
sm0 = R.commission_statement_document(REP, PERIOD, fmt="json", audience="", authorization="", org_id=ORG)
smm = R.commission_statement_document(REP, PERIOD, fmt="json", audience="manager", authorization="", org_id=ORG)
se = R.commission_statement_document(REP, PERIOD, fmt="json", audience="employee", authorization="", org_id=ORG)
check("D3 the manager statement is byte-identical (audience absent == manager)",
      json.dumps(sm0, sort_keys=True, default=str) == json.dumps(smm, sort_keys=True, default=str))
check("D4 the manager statement still shows the ledger buckets and the ⛔ held lines (grant held)",
      (sm0.get("summary") or {}).get("has_buckets") is True and bool(sm0.get("held")))
from app.modules.commcalc import plan_pay_gate as _G   # noqa: E402
_gate_reasons = set(_G.SUPPRESS_LABELS.values())
check("D5 the employee statement: no ledger buckets (carrier commission), no ⛔ plan line held",
      (se.get("summary") or {}).get("has_buckets") is False
      and not any(h.get("reason") in _gate_reasons for h in (se.get("held") or []))
      and any(h.get("reason") in _gate_reasons for h in (sm0.get("held") or [])))
check("D6 the employee statement earns exactly what the manager's does", json.dumps(se.get("earned"), sort_keys=True)
      == json.dumps(sm0.get("earned"), sort_keys=True))
bat = R.commission_statements_batch(PERIOD, reps=REP, fmt="json", audience="employee", authorization="", org_id=ORG)
check("D7 the batch statement honours the employee audience too", (bat["statements"][0].get("summary") or {}).get("has_buckets") is False)
_SELF["keys"] = {REP.upper()}
bat_self = R.commission_statements_batch(PERIOD, reps=f"{REP},Someone Else", fmt="json", audience="manager",
                                         authorization="", org_id=ORG)
check("D8 a rep's batch renders ONLY their own statement, in the employee form", bat_self["count"] == 1
      and (bat_self["statements"][0].get("summary") or {}).get("has_buckets") is False)
_SELF["keys"] = None

# ═════════════════════════════════════════════════════════════════════════════════════════════════
section("D'. every other surface an employee reaches")
# a multi-month device exactly as commission_drilldown builds it (sale line Price / GP + the MA cross-reference)
_mm_ex = {"period": PERIOD, "rep": REP, "plan_component": None, "multimonth_component": {
    "devices": [{"imei": "354198260632397", "mdn": P1, "trans_id": "T", "sale_period": PERIOD, "product": "Phone",
                 "contract_type": "", "ext_price": 1110.0, "gp": 0.0, "device_product": "Phone", "plan_product": "Plan",
                 "device_category": "phone", "label": "Phone — Plan", "ma_matches": [{"spiff_total_paid": 195.0}],
                 "ma_says_paid": True, "held_but_ma_paid": False,
                 "installments": [{"label": "M2", "month_index": 2, "amount": 7.5, "status": "paid", "mi_ref": {"x": 1},
                                   "gate_mode": "ma", "mrc_at_pay": 75.0, "promoted_by": "someone"}]}],
    "totals": {"paid": 1, "withheld": 0, "amount": 7.5}, "schedules": 1, "note": None, "category_guard": {"x": 1}},
    "reconciliation": {"plan_comm": 0, "installment_comm_sale": 7.5, "total_payout": 7.5}}
_mm_emp = PA.employee_explain(_mm_ex)
check("D9 multi-month: the device's sale Price / GP, the MA cross-reference and the carrier refs are gone; the rep's $7.50 stays",
      PA.disallowed_fields(_mm_emp) == [] and _mm_emp["multimonth_component"]["devices"][0]["installments"][0]["amount"] == 7.5
      and "ma_matches" not in _mm_emp["multimonth_component"]["devices"][0])
_drill = {"rep": REP, "period": PERIOD, "source": "raw_sales",
          "premium": {"count": 1, "sales": 150.0, "gp": 150.0, "items": [{"trans_id": "Z1321IN11092", "date": "2026-07-02",
                      "product": "DPA New Act iPhone (Rate Plan Rebate)", "ext_price": 150.0, "gp": 150.0, "mdn": P1}]},
          "accessories": {"count": 1, "sales": 40.0, "gp": 22.0, "items": [{"trans_id": "A1", "product": "Case",
                          "ext_price": 40.0, "gp": 22.0}]}}
_de = PA.employee_drill(_drill)
check("D10 Boost drill (employee): a count-paid bucket carries the transaction without its $150; the accessory bucket keeps its basis",
      "ext_price" not in _de["premium"]["items"][0] and "sales" not in _de["premium"]
      and _de["accessories"]["items"][0]["gp"] == 22.0 and PA.disallowed_fields(_de, "drill") == [])
_SELF["keys"] = {REP.upper()}
refused = []
for name, call in (("carrier-vs-pay", lambda: R.carrier_vs_pay_report(PERIOD, org_id=ORG, authorization="")),
                   ("discrepancy", lambda: asyncio.run(R.get_discrepancy_results(PERIOD, org_id=ORG, authorization=""))),
                   ("phantom", lambda: asyncio.run(R.get_phantom_payments(PERIOD, org_id=ORG, authorization=""))),
                   ("discrepancy-appeals", lambda: R.list_discrepancy_appeals(org_id=ORG, authorization="")),
                   ("commission-device", lambda: R.commission_device("354198260632397", org_id=ORG, authorization=""))):
    try:
        call()
    except R.HTTPException as ex:
        if ex.status_code == 403:
            refused.append(name)
    except Exception:
        pass
check("D11 the manager-only carrier reports refuse a rep (403): %s" % ", ".join(refused), len(refused) == 5, refused)
_SELF["keys"] = None
try:
    R.carrier_vs_pay_report(PERIOD, org_id=ORG, authorization="")
    _mgr_ok = True
except R.HTTPException as ex:
    _mgr_ok = ex.status_code != 403
except Exception:
    _mgr_ok = True            # the fake store lacks that report's tables — anything but a 403 is "not refused"
check("D11b ...and a manager is not refused", _mgr_ok)
_SELF["keys"] = {REP.upper()}
try:
    R.commission_drill(PERIOD, rep="Someone Else", authorization="", org_id=ORG)
    check("D12 a rep drilling another rep's Boost components is refused", False)
except R.HTTPException as ex:
    check("D12 a rep drilling another rep's Boost components is refused 403", ex.status_code == 403)
_SELF["keys"] = None
_row = dict(TABLES[("commcalc", "rep_commissions")][0], acc_target=100.0, kpi_values={"atu": 1.2})
check("D13 the employee dashboard's commission row (core /employee-dashboard) goes through the same allow-list",
      "boost_commission" not in PA.employee_rep_row(_row) and PA.employee_rep_row(_row)["kpi_values"] == {"atu": 1.2})

# ═════════════════════════════════════════════════════════════════════════════════════════════════
section("E. is_paid_line reads the engine's verdict")
check("E1 a suppressed line is not paid", not PA.is_paid_line({"amount": 0.0, "suppressed": True}))
check("E2 a non-qualifying line is not paid", not PA.is_paid_line({"amount": 0.0, "qualifies": False}))
check("E3 a $0 line (a $0 rate) is not paid", not PA.is_paid_line({"amount": 0.0}))
check("E4 a flat bonus (paid once per rep, no per-line $) is paid", PA.is_paid_line({"amount": None, "flat_once": True}))
check("E5 a negative line moves pay, so it is shown", PA.is_paid_line({"amount": -2.5}))
check("E7 no known carrier field is on any employee allow-list",
      not set(PA.KNOWN_CARRIER_FIELDS) & set().union(*[set(v) for v in PA._ALLOW.values()]))
check("E6 resolve: self → employee whatever is asked; others → the declared audience, default manager",
      PA.resolve("manager", True) == "employee" and PA.resolve("employee", False) == "employee"
      and PA.resolve("", False) == "manager" and PA.resolve("x", False) == "manager")

print("\n" + "=" * 100)
print("%d passed, %d failed" % (P, F))
sys.exit(1 if F else 0)
