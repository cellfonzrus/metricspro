"""PROOF — the EMPLOYEE payout report shows only the lines that paid and no carrier commission; the MANAGER view
is unchanged (owner 2026-09-26: "on the employee commission payout report we only need o show the line they are
getting paid and other lines should be hidden and carrier commission not be displayed").

DB-free: the REAL `/commission-explain`, `/commission-statement` and `/commissions/{period}` handlers over the REAL
engine (plan pay gate + activation events), on an in-memory read-only client, with the two invoices the owner
pointed at, line for line as they sit in raw_sales (org f4f1c16e; the customer on Z1321IN11092's lines, and on
Z1321IN10551 only its invoice header — so both halves of the sale-customer rule are exercised):
  · Z1321IN11092 (2026-07-02) — one activation; 3 activation-type lines (carrier $150 / $0 / $45);
  · Z1321IN10551 (2026-05-02 shape) — one activation; 4 activation-type lines (carrier $150, and the
    "ADD A LINE SMART PHONE KICKER" pair $0 / $100 — the pair is one line's tracking SKU + rebate SKU:
    879 of 882 phone lines live carry it as exactly that pair).

  A. MANAGER on the Rep Incentive report: every matched line with its ⛔ reason — and, since the owner's decision of
     2026-09-28 (index §6m: "it should not show any commission received on the rep incentive report, that is only
     for the eyes of the management, gated out from all levels"), NO Price / GP / carrier field (Jona Sejat, July
     2026: no ext_price or gp key anywhere). The CARRIER view (`view=carrier`, the diagnostic) is the drill-down
     itself, for a holder of the carrier permission only.
  B. EMPLOYEE: one paid row per invoice for $10; no carrier field anywhere in the payload (Price, GP, ⛔,
     would-have-paid, data quality, MA cross-reference); every total unchanged to the cent.
  C. A SELF-scoped caller is ALWAYS served the employee form (asking for 'manager' changes nothing) and may not
     read another rep (403).
  D. The rep rows (`/commissions/{period}`) and the Incentive Statement: no dealer figure and no commission-ledger
     bucket for ANY audience (§6m); the manager statement keeps the ⛔ held lines (grant held).
  E. `is_paid_line` reads the engine's verdict: suppressed / non-qualifying / $0 lines are not paid; a flat
     bonus and a negative line are.
  F. (index §6j) the paid row names its SALE — the action, the phone line, the customer (THE sale-customer rule;
     only `trans_id,customer` read, org-scoped); the manager row carries them too; the statement lists them.
  G. (§6j/§6m) WHO: /me's viewer payload hides every registered carrier page from anyone without the carrier
     permission; each refusal names its registered label; the self-scope answer is one function; a manager viewing
     the Rep Incentive page (no audience sent) gets the manager report — reasons, no carrier money.
  H. (§6j) ONE employee over a MONTH RANGE: each month IS that month's single statement (to the cent), month
     totals + the grand total are sums of them; CSV / PDF; a rep may run it only for themselves; the 12-month cap.
  I. (§6m) THE CARRIER PERMISSION — one home (`payout_audience.carrier_view_allowed`), per-org role config with the
     top-management default: an owner / admin gets Price / GP on commission-explain's carrier view and is not
     refused carrier-vs-pay; a store manager / market manager / rep is refused 403 on both; an org can grant it to
     a store manager or take it from a company-wide role; no caller is refused while logins are on.
  J. (§6m) EVERY Rep Incentive surface × EVERY audience (employee, store manager, owner WITH the permission): the
     plan drill, the Boost drill, the rows, the month range, the statement (single / range JSON + CSV / batch) and
     the emailed Incentives report carry NO carrier field.

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


CUST1, CUST2 = "CHEORGE CHEISHVILI INC", "MALIKA,R IRGASHEVA"


def L(tid, product, category, serial="", ext=0.0, voided="No"):
    return {"org_id": ORG, "period": PERIOD, "trans_id": tid, "trans_date": "2026-07-02", "store": "Store 1321",
            "customer": CUST1 if tid == "Z1321IN11092" else "",
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
    ("commcalc", "raw_sales"): Z11092 + Z10551 + [dict(r, period="June 2026", trans_date="2026-06-02", trans_id="Z1321IN10551J")
                                                   for r in Z10551],
    # the invoice header (sales by invoice, mig 1012) — the fallback half of the sale-customer rule
    ("commcalc", "raw_sales_invoice"): [{"org_id": ORG, "trans_id": "Z1321IN10551", "customer": CUST2},
                                        {"org_id": ORG, "trans_id": "Z1321IN10551J", "customer": CUST2},
                                        {"org_id": "another-org", "trans_id": "Z1321IN11092", "customer": "LEAKED NAME"}],
    ("commcalc", "rep_commissions"): [{"org_id": ORG, "period": PERIOD, "epay_salesperson": REP, "storeops_name": REP,
                                       "store": "Store 1321", "total_payout": 20.0, "plan_comm": 20.0, "subtotal": 20.0,
                                       "boost_commission": 310.0, "boost_reimbursement": 12.5,
                                       "premium_acts": 2, "byod_acts": 0, "upgrade_acts": 0},
                                      {"org_id": ORG, "period": "June 2026", "epay_salesperson": REP, "storeops_name": REP,
                                       "store": "Store 1321", "total_payout": 10.0, "plan_comm": 10.0, "subtotal": 10.0,
                                       "boost_commission": 150.0, "boost_reimbursement": 0.0,
                                       "premium_acts": 1, "byod_acts": 0, "upgrade_acts": 0}],
}
_FAKE = FakeClient(TABLES)
R.sb = lambda: _FAKE
R.require_org = lambda org_id: None
_SELF = {"keys": None}
R._caller_rep_keys = lambda authorization, org_id: _SELF["keys"]
R._can_view_statement_held = lambda authorization, org_id: True      # the manager holds the grant

# WHO IS SIGNED IN (the carrier permission, index §6m): the REAL `_can_view_carrier_commission` resolves the caller
# through core's `_uid_from_token` + `_resolve_caller` — stubbed here to a token → caller table (roles as each org's
# Roles & Access would hold them), and the login master switch ON (the live posture).
from app.modules.core import router as _CORE          # noqa: E402
from app.modules.storeops import router as _SO_R      # noqa: E402
CALLERS = {
    "owner": {"super_admin": False, "role": "owner", "perms": {"scope": "all"}},
    "admin": {"super_admin": False, "role": "admin", "perms": {"scope": "all", "modules": {"admin": True}}},
    "sm": {"super_admin": False, "role": "store_manager", "perms": {"scope": "store"}},
    "mm": {"super_admin": False, "role": "market_manager", "perms": {"scope": "market"}},
    "sm_granted": {"super_admin": False, "role": "store_manager",
                   "perms": {"scope": "store", "data": {"carrier_commission_view": True}}},
    "vp_locked": {"super_admin": False, "role": "vp", "perms": {"scope": "all", "data": {"carrier_commission_view": False}}},
    "platform": {"super_admin": True, "role": "store_manager", "perms": {"scope": "store"}},
    "rep": {"super_admin": False, "role": "sales_rep", "perms": {"scope": "self", "data": {"carrier_commission_view": True}}},
}
_CORE._uid_from_token = lambda tok: (tok[4:] if isinstance(tok, str) and tok.startswith("tok:") else None)
_CORE._resolve_caller = lambda client, uid, active_org=None: CALLERS.get(uid)
_LOGIN = {"on": True}
_SO_R._rbac_enabled = lambda org_id=None: _LOGIN["on"]
# the Boost discrepancy engine opens its OWN database client — never reached from a proof (DB-free, no writes)
R.run_discrepancy = lambda period, org_id: {"period": period, "flagged": 0, "total_gap_usd": 0.0}


def explain(audience="", authorization=""):
    return R.commission_explain(PERIOD, rep=REP, audience=audience, authorization=authorization, org_id=ORG)


def carrier_explain(authorization):
    return R.commission_explain(PERIOD, rep=REP, view="carrier", authorization=authorization, org_id=ORG)


def refused(call):
    """(status, detail) of an HTTPException the call raised — (None, None) when it was not refused. A fake-store
    error that is not an HTTPException counts as 'not refused' (the gate is the first line of every carrier handler)."""
    try:
        call()
    except R.HTTPException as ex:
        return ex.status_code, str(ex.detail)
    except Exception:
        pass
    return None, None


def act_lines(res, tid):
    for rb in (res.get("plan_component") or {}).get("rules") or []:
        if rb.get("rule_id") == "RACT":
            return [ln for ln in rb.get("lines") or [] if ln.get("trans_id") == tid]
    return []


# ═════════════════════════════════════════════════════════════════════════════════════════════════
# OWNER DECISION 2026-09-28 (index §6m) — reverses the part of §6j (#309) that gave a manager Price / GP on the Rep
# Incentive report: "the incoming commission is shown on the line item as $150 and $5 on the first transaction ...
# it should not show any commission received on the rep incentive report, that is only for the eyes of the
# management, gated out from all levels". A1–A6 used to assert the manager drill carried the carrier $; they now
# assert it carries the ⛔ reasons and NOT the carrier $, and A6 moved to the carrier view (the diagnostic).
section("A. MANAGER on the Rep Incentive report — every line with its ⛔ reason, NO carrier $ (owner 2026-09-28)")
m0, mm, md = explain("", "tok:sm"), explain("manager", "tok:sm"), explain("junk", "tok:sm")
check("A1 audience absent == 'manager' == an unknown value — byte-identical JSON",
      json.dumps(m0, sort_keys=True, default=str) == json.dumps(mm, sort_keys=True, default=str)
      == json.dumps(md, sort_keys=True, default=str))
check("A2 ...served as the manager audience, not the carrier view", m0.get("audience") == "manager"
      and "carrier_view" not in m0)
l1 = act_lines(m0, "Z1321IN11092")
check("A3 Z1321IN11092 manager: 3 lines, one paid $10, two ⛔ WITH their reasons — and no Price / GP on any of them",
      len(l1) == 3 and sum(1 for l in l1 if l.get("suppressed")) == 2
      and all(l.get("suppressed_reason") for l in l1 if l.get("suppressed"))
      and round(sum(l["amount"] for l in l1), 2) == 10.0 and not any("ext_price" in l or "gp" in l for l in l1),
      [(l.get("product"), l.get("amount"), l.get("suppressed_reason")) for l in l1])
l2 = act_lines(m0, "Z1321IN10551")
check("A4 Z1321IN10551 manager: 4 lines, rep paid $10 once — the owner's $150 / $0 / $100 appear nowhere",
      len(l2) == 4 and round(sum(l["amount"] for l in l2), 2) == 10.0
      and not any("ext_price" in l or "gp" in l for l in l2), l2)
_m_txt = json.dumps(m0, default=str)
check("A5 JONA SEJAT JULY 2026, viewed as a manager: NO `ext_price` and NO `gp` key anywhere in the drill; no carrier "
      "field at all (implied cost, data quality, MA cross-reference)",
      '"ext_price"' not in _m_txt and '"gp"' not in _m_txt and PA.carrier_fields_in(m0) == [],
      PA.carrier_fields_in(m0)[:6])
from app.modules.commcalc import commission_drilldown as _DD   # noqa: E402
_pre = _DD.explain_rep(_FAKE, ORG, PERIOD, REP, carrier_mode=R._resolve_carrier_mode([]),
                       identity_map=R._rep_canon_map(_FAKE, ORG))
c_owner = carrier_explain("tok:owner")
check("A6 the CARRIER view (an owner, `view=carrier`) == the full drill-down explain_rep produced (+ its stamps)",
      json.dumps(dict(_pre, audience="manager", carrier_view=True), sort_keys=True, default=str)
      == json.dumps(c_owner, sort_keys=True, default=str))
check("A7 the manager (Rep Incentive) drill == THE Rep Incentive shaping of that same drill-down, nothing else",
      json.dumps(PA.rep_incentive_explain(_pre, "manager"), sort_keys=True, default=str)
      == json.dumps(m0, sort_keys=True, default=str))
_pre_l = act_lines(_pre, "Z1321IN11092")
check("A8 THE REPORTED DEFECT, reproduced: the unshaped drill-down (what a manager was served before 2026-09-28) "
      "carries the carrier's $150 / $45 on Jona's July lines — so A3–A5 are proving something",
      sorted(l.get("ext_price") for l in _pre_l) == [0.0, 45.0, 150.0] and bool(PA.carrier_fields_in(_pre)))

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
section("D. rep rows and the Incentive Statement — no carrier money for ANY audience (owner 2026-09-28)")
rm = asyncio.run(R.get_commissions(PERIOD, authorization="", org_id=ORG))
re_ = asyncio.run(R.get_commissions(PERIOD, authorization="", org_id=ORG, audience="employee"))
# was "manager rows unchanged (the dealer figures stay for the manager)" — reversed by the owner's decision (§6m)
check("D1 manager rows carry NO dealer figure (boost_commission / boost_reimbursement) — the shape asks nobody who is looking",
      "boost_commission" not in rm[0] and "boost_reimbursement" not in rm[0] and PA.carrier_fields_in(rm) == [], rm[0])
check("D2 the employee rows == the manager rows: one allow-list, every pay field kept",
      "boost_commission" not in re_[0] and "boost_reimbursement" not in re_[0] and rm == re_
      and {k: v for k, v in TABLES[("commcalc", "rep_commissions")][0].items() if k in PA.EMPLOYEE_REP_ROW_FIELDS}.items()
      <= re_[0].items() and PA.disallowed_fields(re_[0], "rep_row") == [])
# the ledger rollup a statement USED to read — planted with real money, so a statement that still read it would show
_BUCKETS_READ = []


def _planted_buckets(client, org_id, period, rep, source_report="ma_daily_tx"):
    _BUCKETS_READ.append(period)
    return {"commission": 310.0, "spiff": 0.0, "equipment_rebate": 150.0, "residual_monthly": 0.0, "chargeback": 0.0}


R._statement_buckets = _planted_buckets
sm0 = R.commission_statement_document(REP, PERIOD, fmt="json", audience="", authorization="", org_id=ORG)
smm = R.commission_statement_document(REP, PERIOD, fmt="json", audience="manager", authorization="", org_id=ORG)
se = R.commission_statement_document(REP, PERIOD, fmt="json", audience="employee", authorization="", org_id=ORG)
check("D3 the manager statement is byte-identical (audience absent == manager)",
      json.dumps(sm0, sort_keys=True, default=str) == json.dumps(smm, sort_keys=True, default=str))
# was "the manager statement still shows the ledger buckets" — reversed by the owner's decision (§6m)
check("D4 the manager statement: NO ledger buckets (never even read) — the ⛔ held lines stay (grant held)",
      (sm0.get("summary") or {}).get("has_buckets") is False and (sm0.get("summary") or {}).get("buckets") == []
      and _BUCKETS_READ == [] and bool(sm0.get("held")) and PA.carrier_fields_in(sm0) == [],
      (_BUCKETS_READ, PA.carrier_fields_in(sm0)[:5]))
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
_mm_mgr = PA.rep_incentive_explain(_mm_ex, "manager")
check("D9b ...and the MANAGER's multi-month table the same: no Price / GP, no MA cross-reference / 'MA says paid', no MI "
      "ref (owner 2026-09-28) — the $7.50 and the installment's status stay",
      PA.carrier_fields_in(_mm_mgr) == [] and _mm_mgr["multimonth_component"]["devices"][0]["installments"][0]["amount"] == 7.5,
      PA.carrier_fields_in(_mm_mgr))
_drill = {"rep": REP, "period": PERIOD, "source": "raw_sales",
          "premium": {"count": 1, "sales": 150.0, "gp": 150.0, "items": [{"trans_id": "Z1321IN11092", "date": "2026-07-02",
                      "product": "DPA New Act iPhone (Rate Plan Rebate)", "ext_price": 150.0, "gp": 150.0, "mdn": P1}]},
          "accessories": {"count": 1, "sales": 40.0, "gp": 22.0, "items": [{"trans_id": "A1", "product": "Case",
                          "ext_price": 40.0, "gp": 22.0}]}}
_de = PA.employee_drill(_drill)
check("D10 Boost drill (employee): a count-paid bucket carries the transaction without its $150; the accessory bucket keeps its basis",
      "ext_price" not in _de["premium"]["items"][0] and "sales" not in _de["premium"]
      and _de["accessories"]["items"][0]["gp"] == 22.0 and PA.disallowed_fields(_de, "drill") == [])
_dm = PA.rep_incentive_drill(_drill, "manager")
check("D10b Boost drill (MANAGER, owner 2026-09-28): the same shape as the employee's — no $150 on the count-paid "
      "bucket; only the audience stamp differs",
      {k: v for k, v in _dm.items() if k != "audience"} == {k: v for k, v in _de.items() if k != "audience"}
      and _dm["audience"] == "manager" and PA.disallowed_fields(_dm, "drill") == [])
_SELF["keys"] = {REP.upper()}
CARRIER_CALLS = (
    ("carrier-vs-pay", lambda a: R.carrier_vs_pay_report(PERIOD, org_id=ORG, authorization=a)),
    ("discrepancy", lambda a: asyncio.run(R.get_discrepancy_results(PERIOD, org_id=ORG, authorization=a))),
    ("phantom", lambda a: asyncio.run(R.get_phantom_payments(PERIOD, org_id=ORG, authorization=a))),
    ("discrepancy-run", lambda a: R.run_discrepancy_check({"period": "2026-07"}, org_id=ORG, authorization=a)),
    ("discrepancy-appeals", lambda a: R.list_discrepancy_appeals(org_id=ORG, authorization=a)),
    ("commission-device", lambda a: R.commission_device("354198260632397", org_id=ORG, authorization=a)),
    ("commission-explain?view=carrier", lambda a: carrier_explain(a)))
refused_rep = [n for n, c in CARRIER_CALLS if refused(lambda: c("tok:rep"))[0] == 403]
check("D11 every carrier surface refuses a rep (403) — even one whose role row says granted: %s" % ", ".join(refused_rep),
      len(refused_rep) == len(CARRIER_CALLS), refused_rep)
_SELF["keys"] = None
_mgr_open = [n for n, c in CARRIER_CALLS if refused(lambda: c("tok:owner"))[0] != 403]
check("D11b ...and an owner (the carrier permission's house default) is refused none of them",
      len(_mgr_open) == len(CARRIER_CALLS), _mgr_open)
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

# ═════════════════════════════════════════════════════════════════════════════════════════════════
section("F. the paid row names its SALE — action · phone line · customer (index §6j)")
f1, f2 = act_lines(e, "Z1321IN11092"), act_lines(e, "Z1321IN10551")
check("F1 Z1321IN11092 employee row: 'New activation' · 9297458084 · the customer on the sale lines",
      len(f1) == 1 and f1[0].get("event_label") == "New activation" and f1[0].get("phone") == P1
      and f1[0].get("customer") == CUST1, f1)
check("F2 Z1321IN10551 employee row: the customer from the INVOICE HEADER (its lines name none)",
      len(f2) == 1 and f2[0].get("customer") == CUST2 and f2[0].get("phone") == P2, f2)
check("F3 another org's invoice header never names this org's customer (org-scoped read)",
      "LEAKED NAME" not in json.dumps(e) and "LEAKED NAME" not in json.dumps(m0))
check("F4 phone / customer / event_label are on the employee allow-list on purpose (no disallowed field)",
      {"phone", "customer", "event_label"} <= set(PA.EMPLOYEE_LINE_FIELDS) and PA.disallowed_fields(e) == [])
check("F5 the manager rows carry the same sale identity (every matched line of the invoice)",
      all(l.get("customer") == CUST1 and l.get("phone") == P1 for l in act_lines(m0, "Z1321IN11092")))
from app.modules.commcalc import inventory_sold_recon as _ISR   # noqa: E402
_raw = TABLES[("commcalc", "raw_sales")]
_hdr = {r["trans_id"]: r["customer"] for r in TABLES[("commcalc", "raw_sales_invoice")] if r["org_id"] == ORG}
check("F6 the name IS the sale-customer rule the inventory integrity report uses (inventory_sold_recon.sale_customer)",
      _ISR.invoice_customer_map(_raw, _hdr).get("Z1321IN11092") == CUST1
      and _ISR.invoice_customer_map(_raw, _hdr).get("Z1321IN10551") == CUST2
      and _ISR.sale_customer({"trans_id": "Z1321IN10551", "customer": ""}, _hdr) == CUST2)
_seen = []


class _Spy(FakeClient):
    def schema(self, name):
        base = FakeClient.schema(self, name)
        seen = _seen

        class S:
            def table(self_, n):
                q = base.table(n)
                sel0, eq0 = q.select, q.eq

                def select(*a, **k):
                    seen.append((n, a[0] if a else ""))
                    return sel0(*a, **k)

                def eq(col, val):
                    seen.append((n, "eq:" + col))
                    return eq0(col, val)
                q.select, q.eq = select, eq
                return q
        return S()


_DD._sale_customers(_Spy(TABLES), ORG, ["Z1321IN11092", "Z1321IN10551"])
_sels = {c for t, c in _seen if not c.startswith("eq:")}
check("F7 the name read selects ONLY trans_id,customer (no id number, no other customer field) and is org-scoped",
      _sels == {"trans_id,customer"} and ("raw_sales", "eq:org_id") in _seen and ("raw_sales_invoice", "eq:org_id") in _seen,
      _seen)
se_lines = se.get("sale_lines") or []
check("F8 the employee statement lists its sales that paid: 2 rows, action · phone · customer, no product / Price / GP",
      len(se_lines) == 2 and all(r["status"] == "Paid" and r.get("customer") and r.get("phone") for r in se_lines)
      and not any(k in r for r in se_lines for k in ("product", "ext_price", "gp")), se_lines)
sm_lines = sm0.get("sale_lines") or []
# was "... the product, Price / GP, the ⛔ reasons" — Price / GP reversed by the owner's decision of 2026-09-28 (§6m)
check("F9 the manager statement (held grant) lists every matched line — the product and the ⛔ reasons, NO Price / GP",
      len(sm_lines) > len(se_lines) and any(r.get("product") for r in sm_lines)
      and any(r["status"] != "Paid" for r in sm_lines) and not any("ext_price" in r or "gp" in r for r in sm_lines))
check("F10 Σ statement sale-line $ == Σ drill-down line $ (the lines restate the engine, nothing added)",
      round(sum(r["amount_raw"] for r in se_lines), 2) == PA.rule_line_total(e) == 20.0)

# ═════════════════════════════════════════════════════════════════════════════════════════════════
section("G. WHO is looking — /me's viewer payload, the registry, the self scope (index §6j, §6m)")
vp_rep, vp_sm, vp_top = PA.viewer_payload(True, True), PA.viewer_payload(False, False), PA.viewer_payload(False, True)
check("G1 a rep is told: audience employee, every carrier page refused (Pay Discrepancy among them) — even if a role "
      "row claims the permission",
      vp_rep["audience"] == "employee" and vp_rep["carrier_view"] is False and "/commcalc/discrepancy" in vp_rep["refused_pages"]
      and set(vp_rep["refused_pages"]) == set(PA.MANAGER_ONLY_PAGES))
# was "a manager is told: nothing refused" — since 2026-09-28 only a holder of the carrier permission is (§6m)
check("G2 a manager WITHOUT the carrier permission is told: audience manager, every carrier page refused "
      "(the commission-explain diagnostic among them); top management WITH it: nothing refused",
      vp_sm == {"audience": "manager", "carrier_view": False, "refused_pages": list(PA.MANAGER_ONLY_PAGES)}
      and "/commcalc/commission-explain" in vp_sm["refused_pages"]
      and vp_top == {"audience": "manager", "carrier_view": True, "refused_pages": []})
msgs = []
for call in (lambda: R.carrier_vs_pay_report(PERIOD, org_id=ORG, authorization="tok:sm"),
             lambda: asyncio.run(R.get_discrepancy_results(PERIOD, org_id=ORG, authorization="tok:sm")),
             lambda: R.list_discrepancy_appeals(org_id=ORG, authorization="tok:sm")):
    msgs.append(refused(call)[1])
check("G3 each refusal names its REGISTERED surface and THE permission (never a role)", msgs == [
    PA.refusal_message("carrier_vs_pay"), PA.refusal_message("pay_discrepancy"),
    PA.refusal_message("commission_discrepancy")] and all("carrier_commission_view" in (m or "") for m in msgs)
      and msgs[1].startswith("Pay Discrepancy shows carrier commission"), msgs)
try:
    PA.manager_only_label("not_registered")
    check("G4 an unregistered manager-only key raises (a refusal must be registered for the nav to hide it)", False)
except KeyError:
    check("G4 an unregistered manager-only key raises (a refusal must be registered for the nav to hide it)", True)
from app.modules.storeops import router as _SO   # noqa: E402
_orig = (_SO._rbac_enabled, _SO._role_scope)
_SO._rbac_enabled = lambda org_id=None: True
_SO._role_scope = lambda org_id, role: {"rep": "self", "dm": "market", "owner": "all"}.get(role, "self")
g5 = (_SO.role_is_self_scoped(ORG, "rep"), _SO.role_is_self_scoped(ORG, "dm"), _SO.role_is_self_scoped(ORG, "owner"))
_SO._rbac_enabled = lambda org_id=None: False
g5off = _SO.role_is_self_scoped(ORG, "rep")
_SO._rbac_enabled, _SO._role_scope = _orig
check("G5 ONE self-scope answer (the nav's and the server's): rep yes, DM / owner no, RBAC off never",
      g5 == (True, False, False) and g5off is False, (g5, g5off))
mgr_page = explain("", "tok:owner")         # the Rep Incentive page sends no audience (and no view)
# was "... gets the FULL report: ⛔ lines, Price / GP" — Price / GP reversed by the owner's decision of 2026-09-28 (§6m)
check("G6 a manager — even an owner with the carrier permission — on the Rep Incentive page (no audience, no view) "
      "gets the manager report: ⛔ lines with reasons, NO Price / GP",
      mgr_page.get("audience") == "manager" and any(l.get("suppressed") for l in act_lines(mgr_page, "Z1321IN11092"))
      and not any("ext_price" in l or "gp" in l for l in act_lines(mgr_page, "Z1321IN11092"))
      and PA.carrier_fields_in(mgr_page) == [])
_SELF["keys"] = {REP.upper()}
check("G7 ...while a rep on the same page (no audience sent) gets the employee report", explain("").get("audience") == "employee")
_SELF["keys"] = None

# ═════════════════════════════════════════════════════════════════════════════════════════════════
section("H. ONE employee over a MONTH RANGE — each month IS the single statement (index §6j)")


def stmt(period, audience=""):
    return R.commission_statement_document(REP, period, fmt="json", audience=audience, authorization="", org_id=ORG)


def rng(audience="", fmt="json", rep_=REP, pf="2026-06", pt="2026-07"):
    return R.commission_statement_document(rep_, "", fmt=fmt, audience=audience, period_from=pf, period_to=pt,
                                           authorization="", org_id=ORG)


for aud in ("manager", "employee"):
    rg = rng(aud)
    singles = [stmt("June 2026", aud), stmt("July 2026", aud)]
    check("H1 [%s] months = June, July — each month's section == that month's single statement, byte for byte" % aud,
          [m["period"] for m in rg["months"]] == ["June 2026", "July 2026"]
          and json.dumps(rg["statements"], sort_keys=True, default=str) == json.dumps(singles, sort_keys=True, default=str))
    check("H2 [%s] month totals == the single statements' payout of record; grand total == their sum, to the cent" % aud,
          [m["total_raw"] for m in rg["months"]] == [d["summary"]["total_raw"] for d in singles] == [10.0, 20.0]
          and rg["grand_total_raw"] == 30.0 and rg["grand_total"] == "$30.00", (rg["months"], rg["grand_total"]))
re_rng = rng("employee")
check("H3 the employee range lists paid rows only (1 in June, 2 in July) and no carrier field",
      [m["paid_lines"] for m in re_rng["months"]] == [1, 2]
      and all(r["status"] == "Paid" and "ext_price" not in r for d in re_rng["statements"] for r in d["sale_lines"]))
csv_e = rng("employee", fmt="csv").body.decode()
csv_m = rng("manager", fmt="csv").body.decode()
hdr_e, hdr_m = csv_e.splitlines()[0], csv_m.splitlines()[0]
check("H4 employee CSV: action / phone / customer, no product / Price / GP column; month totals + the grand total",
      "customer" in hdr_e and "product" not in hdr_e and "ext_price" not in hdr_e and "gp" not in hdr_e.split(",")
      and CUST1 in csv_e and "month total" in csv_e and "grand total" in csv_e and ",30.0" in csv_e, hdr_e)
# was "manager CSV: adds product, status, Price and GP" — Price / GP reversed by the owner's decision of 2026-09-28 (§6m)
check("H5 manager CSV: adds product and status — NO Price / GP column",
      all(k in hdr_m.split(",") for k in ("product", "status"))
      and not any(k in hdr_m.split(",") for k in ("ext_price", "gp")), hdr_m)
pdf = rng("employee", fmt="pdf")
check("H6 the range PDF renders (the totals page, then each month)", pdf.body[:4] == b"%PDF" and len(pdf.body) > 2000)
single_csv = R.commission_statement_document(REP, "July 2026", fmt="csv", authorization="", org_id=ORG).body.decode()
check("H7 one month as CSV is the one-month range", "July 2026" in single_csv and "grand total" in single_csv)
_SELF["keys"] = {REP.upper()}
try:
    rng("", rep_="Someone Else")
    check("H8 a rep running the range for ANOTHER employee is refused", False)
except R.HTTPException as ex:
    check("H8 a rep running the range for ANOTHER employee is refused 403", ex.status_code == 403)
own = rng("manager")
check("H9 a rep's own range is always the employee form (asking for manager changes nothing)",
      own["audience"] == "employee" and all(d.get("audience") == "employee" for d in own["statements"]))
_SELF["keys"] = None
try:
    rng("", pf="2025-01", pt="2026-07")
    check("H10 more than 12 months is refused (the Rep Incentive range cap)", False)
except R.HTTPException as ex:
    check("H10 more than 12 months is refused 400 (the Rep Incentive range cap)", ex.status_code == 400)

# ═════════════════════════════════════════════════════════════════════════════════════════════════
section("I. THE CARRIER PERMISSION — one home, per-org roles, top management by default (owner 2026-09-28, §6m)")
_truth = {k: PA.carrier_view_allowed(c, caller_is_self=(k == "rep")) for k, c in CALLERS.items()}
check("I1 house default: owner / admin (company-wide) and the platform super admin YES; store manager / market "
      "manager NO; a rep NO even when its role row says granted",
      _truth == {"owner": True, "admin": True, "sm": False, "mm": False, "sm_granted": True, "vp_locked": False,
                 "platform": True, "rep": False} and PA.carrier_view_allowed(None) is False, _truth)
check("I2 per-org config wins both ways: an org GRANTS it to a store manager; an org TAKES it from a company-wide role",
      _truth["sm_granted"] is True and _truth["vp_locked"] is False)
check("I3 the carrier's money and the ⛔ reasons are different lists; neither is on an employee allow-list, and the "
      "manager's line list (employee + reasons) carries no carrier field",
      not set(PA.KNOWN_CARRIER_FIELDS) & set(PA.MANAGER_REASON_FIELDS)
      and not (set(PA.KNOWN_CARRIER_FIELDS) | set(PA.MANAGER_REASON_FIELDS)) & set().union(*[set(v) for v in PA._ALLOW.values()])
      and not set(PA.KNOWN_CARRIER_FIELDS) & set(PA.EMPLOYEE_LINE_FIELDS + PA.MANAGER_REASON_FIELDS))
for who in ("owner", "admin"):
    ce = carrier_explain("tok:" + who)
    cl = act_lines(ce, "Z1321IN11092")
    check("I4 [%s] WITH the permission: commission-explain's carrier view carries the carrier's Price 150 / 0 / 45 and "
          "GP, stamped carrier_view" % who,
          ce.get("carrier_view") is True and sorted(l["ext_price"] for l in cl) == [0.0, 45.0, 150.0]
          and all("gp" in l for l in cl), [(l.get("product"), l.get("ext_price")) for l in cl])
    check("I5 [%s] ...and carrier-vs-pay is not refused" % who,
          refused(lambda: R.carrier_vs_pay_report(PERIOD, org_id=ORG, authorization="tok:" + who))[0] != 403)
for who in ("sm", "mm", "vp_locked"):
    s1 = refused(lambda: carrier_explain("tok:" + who))
    s2 = refused(lambda: R.carrier_vs_pay_report(PERIOD, org_id=ORG, authorization="tok:" + who))
    check("I6 [%s] WITHOUT the permission: 403 on commission-explain's carrier view AND on carrier-vs-pay" % who,
          s1[0] == 403 and s2[0] == 403 and "carrier_commission_view" in (s1[1] or ""), (s1, s2))
check("I7 [store manager GRANTED by the org] not refused on either",
      refused(lambda: carrier_explain("tok:sm_granted"))[0] != 403
      and refused(lambda: R.carrier_vs_pay_report(PERIOD, org_id=ORG, authorization="tok:sm_granted"))[0] != 403)
_sg = explain("", "tok:sm_granted")
check("I8 the permission NEVER opens carrier money on the Rep Incentive report (a granted viewer's drill is carrier-free)",
      PA.carrier_fields_in(_sg) == [] and _sg.get("audience") == "manager")
check("I9 NO caller while logins are on (a scheduled / emailed run): refused; an unverifiable token: refused",
      refused(lambda: carrier_explain(""))[0] == 403 and refused(lambda: carrier_explain("garbage"))[0] == 403
      and not R._can_view_carrier_commission("", ORG))
_LOGIN["on"] = False
check("I10 the open app (login master switch OFF, no token — no role exists to gate): not refused",
      R._can_view_carrier_commission("", ORG) and refused(lambda: carrier_explain(""))[0] != 403)
_LOGIN["on"] = True
_raise_orig = _CORE._resolve_caller
_CORE._resolve_caller = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("resolver down"))
check("I11 FAILS CLOSED: a resolver fault refuses (never opens carrier money)", not R._can_view_carrier_commission("tok:owner", ORG))
_CORE._resolve_caller = _raise_orig
try:
    R._require_carrier_view("tok:owner", ORG, "not_registered")
    check("I12 a refusal naming an UNREGISTERED carrier surface raises before deciding anything", False)
except KeyError:
    check("I12 a refusal naming an UNREGISTERED carrier surface raises before deciding anything", True)

# ═════════════════════════════════════════════════════════════════════════════════════════════════
section("J. EVERY Rep Incentive surface × EVERY audience carries NO carrier field (owner 2026-09-28, §6m)")
VIEWERS = (("employee (a rep)", "tok:rep", {REP.upper()}), ("store manager", "tok:sm", None),
           ("owner WITH the carrier permission", "tok:owner", None))
try:
    from app.modules.notify import report_registry as _RR   # noqa: E402
except Exception as _rr_e:          # the registry imports three routers; say so rather than skip silently
    _RR = None
    print("  NOTE  notify report_registry not importable here (%s) — the Incentives email is covered by the lock" % _rr_e)
for label, tok, keys in VIEWERS:
    _SELF["keys"] = keys
    got = {"plan drill": explain("", tok)}
    try:
        _drill_live, _drill_src = R.commission_drill(PERIOD, rep=REP, authorization=tok, org_id=ORG), "the live handler"
    except R.HTTPException:
        raise
    except Exception as _de_e:      # the fake store may lack a table the Boost replay reads — shape the fixture instead
        _drill_live = PA.rep_incentive_drill(_drill, "employee" if keys else "manager")
        _drill_src = "the fixture (handler: %s)" % type(_de_e).__name__
    _drill_src += ", %d premium item(s)" % len((_drill_live.get("premium") or {}).get("items") or [])
    got["rows"] = asyncio.run(R.get_commissions(PERIOD, authorization="", org_id=ORG))
    got["month range rows"] = asyncio.run(R.get_commissions_range("2026-06", "2026-07", authorization="", org_id=ORG))
    got["statement"] = R.commission_statement_document(REP, PERIOD, fmt="json", authorization=tok, org_id=ORG)
    got["statement range"] = R.commission_statement_document(REP, "", fmt="json", period_from="2026-06",
                                                             period_to="2026-07", authorization=tok, org_id=ORG)
    got["statement batch"] = R.commission_statements_batch(PERIOD, reps=REP, fmt="json", authorization=tok, org_id=ORG)
    if _RR is not None:
        got["emailed Incentives"] = asyncio.run(_RR._commissions(ORG, {"period": PERIOD}, authorization=""))
    leaks = {k: PA.carrier_fields_in(v)[:3] for k, v in got.items() if PA.carrier_fields_in(v)}
    check("J1 [%s] %s: no carrier field anywhere" % (label, " · ".join(got)), not leaks, leaks)
    check("J2 [%s] Boost drill (%s): no Price / GP on a count-paid bucket (only the %% basis buckets keep the "
          "customer's price)" % (label, _drill_src), PA.disallowed_fields(_drill_live, "drill") == [],
          PA.disallowed_fields(_drill_live, "drill")[:3])
    _csv = R.commission_statement_document(REP, "", fmt="csv", period_from="2026-06", period_to="2026-07",
                                           authorization=tok, org_id=ORG).body.decode().splitlines()[0].split(",")
    check("J3 [%s] the statement CSV export has no Price / GP column" % label,
          not {"ext_price", "gp"} & set(_csv), _csv)
    _txt = json.dumps(got, default=str)
    check("J4 [%s] the carrier's $150 / $45 / $170 / $100 for these invoices appear nowhere as a line figure" % label,
          not any(f'"{k}": {v}' in _txt for k in ("ext_price", "gp") for v in ("150.0", "100.0", "45.0", "170.0")))
_SELF["keys"] = None

print("\n" + "=" * 100)
print("%d passed, %d failed" % (P, F))
sys.exit(1 if F else 0)
