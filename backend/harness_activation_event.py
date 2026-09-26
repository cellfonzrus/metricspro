"""PROOF — rep commission pays PER ACTIVATION and PER UPGRADE, never per sale line (owner 2026-09-25).

Owner, verbatim: *"commisison for teh reps need to be claculated per action and per upgrade as defined in
teh incentive payout , the system sis calculating per line item"* — and on the breakdown screen, invoice
Z1321IN11092 (2026-07-02): three lines under the plan rule "Activation" at $10/unit, $30.00, for ONE
activation (the rate-plan rebate line, the tracking line and the plan line all name phone line 9297458084).

THE CLASS. A `flat_per_unit` rule keyed on the ACTIVATION TYPE (`activation_bucket`) paid once per matching
LINE; an activation is one EVENT that several lines describe. THE definition of an event lives in ONE place —
`line_class.activation_events` (per invoice: one per phone line the activation-type lines name, else per
device, else the invoice; evidence lines fold in; the event's type by precedence) — and every surface that
pays or counts per activation dereferences it (`harness_activation_event_lock.py` fails the build otherwise).

DB-free: the REAL engine (`commission_engine.preview`), the REAL gate, the REAL `_sales_cell_agg` and the REAL
calculator, over an in-memory read-only client. No network, no writes.

  A. THE OWNER'S INVOICE — Z1321IN11092 as it sits in raw_sales (21 lines, 3 voided) under the tenant's own
     rules: $30 → $10. ARMED: the pre-fix engine (the same code with the event basis switched off, which is
     what the engine did before) still pays $30, and the regression check goes RED on it.
  B. SEVERAL ACTIVATIONS ON ONE INVOICE — 3 financed phones + 1 BYOD (the Z1321IN11301 shape): $40, never $10
     (not per invoice) and never $110 (not per line). The unattributable BYOD evidence line pays nothing.
  C. AN UPGRADE WITH A LINE CLASSED 'activation' — the "New Activation (VZ Commission)" kicker on an upgrade
     (the Z1321IN11252 shape): Upgrade $5 once; the Activation rule pays $0 for it (another type's event).
  D. THE EVENT DEFINITION — phone key, device fallback, invoice fallback, evidence, precedence, mixed classes,
     blank trans ids, per-org config (`event.keys` / `precedence` / `count_unit`), a phone on two invoices.
  E. BYTE-IDENTITY — unit 'transaction' (the house default) reproduces EXACTLY the retired
     `classify_line(row) → set.add(tid)` of every counting surface, over a 400-seed fuzz, for each of the three
     trans-id spellings; `_sales_cell_agg` equals the retired aggregation; the Boost calculator's counts are
     unchanged; a plan with no activation-type $/unit rule returns a byte-identical preview.
  F. %-OF-BASIS RULES ARE NEVER COLLAPSED — a pct rule on activation_bucket stays per line.
  G. THE COUNTING SURFACES UNDER `count_unit: 'event'` — `_sales_cell_agg` counts 4 activations on the
     4-phone invoice (the owner's number), one bucket per event.

    python3 backend/harness_activation_event.py
"""
import copy
import json
import os
import random
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from app.modules.commcalc import commission_engine as ce            # noqa: E402
from app.modules.commcalc import line_class as LC                   # noqa: E402
from app.modules.commcalc import plan_pay_gate as G                 # noqa: E402
from app.modules.commcalc import router as cr                       # noqa: E402
from app.modules.commcalc import calculator as calc                 # noqa: E402

ORG = "00000000-0000-0000-0000-0000000e7e01"
PERIOD = "July 2026"
PLAN = "FLAT"
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


# ── in-memory READ-ONLY client (the engine's read surface; no write verbs exist) ─────────────────
class _Q:
    def __init__(self, rows):
        self._rows = list(rows)

    def select(self, *a, **k):
        return self

    def eq(self, col, val):
        self._rows = [r for r in self._rows if str(r.get(col)) == str(val)]
        return self

    def neq(self, col, val):
        self._rows = [r for r in self._rows if str(r.get(col)) != str(val)]
        return self

    def in_(self, col, vals):
        vs = {str(v) for v in vals}
        self._rows = [r for r in self._rows if str(r.get(col)) in vs]
        return self

    def _noop(self, *a, **k):
        return self
    not_ = is_ = gte = lte = gt = lt = order = ilike = like = _noop

    def limit(self, n):
        self._rows = self._rows[:n]
        return self

    def range(self, a, b):
        self._rows = self._rows[a:b + 1]
        return self

    def execute(self):
        return type("R", (), {"data": copy.deepcopy(self._rows)})()


class _Schema:
    def __init__(self, store, name):
        self._store, self._name = store, name

    def table(self, t):
        return _Q(self._store.get((self._name, t), []))

    def rpc(self, *a, **k):
        return type("R", (), {"execute": lambda s=None: type("R2", (), {"data": []})()})()


class FakeClient:
    def __init__(self, tables):
        self._t = tables

    def schema(self, name):
        return _Schema(self._t, name)

    def table(self, t):
        return _Q(self._t.get(("public", t), []))

    def rpc(self, *a, **k):
        return type("R", (), {"execute": lambda s=None: type("R2", (), {"data": []})()})()


# THE TENANT'S RULES AS STORED (accessory_config.activation_details_rules, org f4f1c16e…, read live
# 2026-09-25) and ITS PLAN ("Flat Comission": Activation $10/unit premium+byod, Upgrade $5/unit upgrade).
TENANT_ADR = {"fields": ["category", "product_desc"],
              "tokens": {"byod": ["customer provided", "byod", "customer owned"], "port": ["port"],
                         "upgrade": ["upgrade"], "activation": ["new activation", "add a line", "new act", "prepaid"],
                         "hardware_only": ["hardware only", "prepaid"]}}
ACT_RULE = {"id": "RACT", "label": "Activation", "match_field": "activation_bucket", "match_op": "in",
            "match_value": "premium,byod", "qualifies": True, "payout_kind": "flat_per_unit", "amount": 10.0,
            "pct": 0.0, "tiered": False, "unit_basis": None, "sort": 0}
UPG_RULE = {"id": "RUPG", "label": "Upgrade", "match_field": "activation_bucket", "match_op": "in",
            "match_value": "upgrade", "qualifies": True, "payout_kind": "flat_per_unit", "amount": 5.0,
            "pct": 0.0, "tiered": False, "unit_basis": None, "sort": 1}
# the pre-fix engine: the SAME code with the ⑥ auto event basis switched off — every activation-type $/unit
# rule then resolves 'per_line', exactly what `resolve_unit_basis` returned before 2026-09-25
PRE_FIX = {"unit_basis": {"auto_event_fields": []}}


def tables(rows, rules=(ACT_RULE, UPG_RULE), adr=TENANT_ADR, org=ORG):
    return {
        ("commcalc", "commission_plan"): [
            {"id": PLAN, "org_id": org, "name": "Flat Comission", "is_active": True, "carrier_id": None,
             "base_tier_metric": "none", "commission_basis": "rules", "activation_source": "inherit"}],
        ("commcalc", "commission_rule"): [dict(r, org_id=org, plan_id=PLAN) for r in rules],
        ("commcalc", "commission_tier"): [],
        ("commcalc", "commission_plan_assignment"): [
            {"id": "A1", "org_id": org, "plan_id": PLAN, "scope": "default", "scope_value": None}],
        ("commcalc", "accessory_config"): [{"org_id": org, "activation_details_rules": adr,
                                            "contract_type_map": {}, "activation_rules": []}],
        ("commcalc", "raw_sales"): [dict(r, org_id=org, period=PERIOD) for r in rows],
    }


def L(tid, product, category, serial="", ext=0.0, voided="No", rep="Jona Sejat", mdn=""):
    return {"trans_id": tid, "trans_date": "2026-07-02", "store": "Store 1321", "salesperson": rep,
            "user_login": rep, "department": "Activations (Price Sheet)",
            "category": ">> Activations (Price Sheet) >> Carrier >> " + category, "product_desc": product,
            "serial_1": serial, "mdn": mdn, "ext_price": ext, "gp": ext, "voided": voided, "trans_type": None,
            "contract_type": "", "quantity": 1.0}


def pay(rows, rules=(ACT_RULE, UPG_RULE), gate=None, detail=True, org=ORG):
    return ce.preview(FakeClient(tables(rows, rules, org=org)), org, PERIOD, detail=detail, gate_override=gate)


def rule_of(res, rid):
    for rep in res.get("by_rep") or []:
        for rb in rep.get("rules") or []:
            if rb.get("rule_id") == rid:
                return rb
    return {"payout": 0.0, "lines": [], "qualifying_units": 0, "matched_lines": 0}


def txn_pay(res, rid, tid):
    return round(sum((l.get("amount") or 0) for l in rule_of(res, rid).get("lines") or [] if l["trans_id"] == tid), 2)


# ── THE OWNER'S INVOICE, line for line (raw_sales, org f4f1c16e…, trans Z1321IN11092; customer omitted) ──
PHONE = "9297458084"
Z1321IN11092 = [
    L("Z1321IN11092", "iPhone Rate Plan (DPA)", "Rate Plans"),
    L("Z1321IN11092", "Device Payment Agreement Financed Amount", "Integration SKUs >> Installment Offset", PHONE, -1110.0, "Yes"),
    L("Z1321IN11092", "Device Payment Agreement Loan Number", "Integration SKUs >> Other Installment SKUs", "1852600095"),
    L("Z1321IN11092", "Device Payment Agreement Rebate Amount", "Integration SKUs >> Other Installment SKUs", PHONE, 1110.0),
    L("Z1321IN11092", "Device Payment Agreement Financing Fee", "Integration SKUs >> Other Installment SKUs", PHONE, -33.3, "Yes"),
    L("Z1321IN11092", "DPA New Act iPhone (Rate Plan Rebate)", "Rate Plan Rebates", PHONE, 150.0),
    L("Z1321IN11092", "DPA Device Rebate", "Equip Rebates (VZ Commission) >> Device Payment (Reimbursement)", PHONE),
    L("Z1321IN11092", "VERIZON BUSINESS TRACKING NEW ACTIVATION", "Integration SKUs >> Business Skus", PHONE),
    L("Z1321IN11092", "88623 TOTAL MOBILE PROTECTION - BUSINESS NEW", "Features >> Features - SPF", PHONE),
    L("Z1321IN11092", "SMB NEW ACCOUNT SMART PHONE KICKER", "VZ Kickers", PHONE),
    L("Z1321IN11092", "SMB NEW ACCOUNT SMART PHONE KICKER", "VZ Kickers", PHONE, 170.0),
    L("Z1321IN11092", "TMP SINGLE LINE BUSINESS - NEW (PSP)", "Features >> Features - SPF", PHONE, 65.0),
    L("Z1321IN11092", "Volume Bonus - Consumer Phone Activation Kicker", "VZ Kickers", PHONE, -12.0, "Yes"),
    L("Z1321IN11092", "Volume Bonus - Consumer Phone Activation Kicker", "VZ Kickers", PHONE, 12.0),
    L("Z1321IN11092", "3377 50GB Additional MHS", "Features >> Features - SPF", PHONE, 5.0),
    L("Z1321IN11092", "3373 Premium Network Experience", "Features >> Features - SPF", PHONE, 10.0),
    L("Z1321IN11092", "3373", "Features >> Features - SPF", "0542840320"),
    L("Z1321IN11092", "3377", "Features >> Features - SPF", "0542840320"),
    L("Z1321IN11092", "3588", "Features >> Features - SPF", "0542840320"),
    L("Z1321IN11092", "APPLE IPHONE 17 PRO 256GB SILVER", "Cellular Equipment >> SmartPhones >> Apple", "354198260632397", 1110.0),
    L("Z1321IN11092", "3588 Enhanced Video Calling", "Features >> Features - SPF", PHONE, 5.0),
    L("Z1321IN11092", "32066 My Biz Plan - New Act", "Additional Spiffs (Promotions)", PHONE, 45.0),
]

# 3 financed phones + 1 BYOD phone on ONE invoice (the Z1321IN11301 shape, trimmed to the activation lines)
MULTI = [L("Z1321IN11301", "Customer Owned Device (START PAW)", "Cellular Equipment >> Customer Provided Device", "353138601621353"),
         L("Z1321IN11301", "New Activation Rate Plan", "Rate Plans"),
         L("Z1321IN11301", "New Activation (Rate Plan Rebate)", "Rate Plan Rebates", "6462011035"),
         L("Z1321IN11301", "MTM Smart Phone", "Equip Rebates (VZ Commission) >> New Activation (VZ Commission)", "6462011035", 150.0),
         L("Z1321IN11301", "63215 Unlimited Welcome Smartphone/iPhone - New Act", "VZ Kickers", "6462011035")]
for _ph in ("2679044499", "5169149951", "9175199951"):
    MULTI += [L("Z1321IN11301", "DPA New Act iPhone (Rate Plan Rebate)", "Rate Plan Rebates", _ph, 150.0),
              L("Z1321IN11301", "63217 Unlimited Plus Smart/iPhone - New Act", "VZ Kickers", _ph, 60.0),
              L("Z1321IN11301", "NEW ACCOUNT SMART PHONE KICKER", "VZ Kickers", _ph, 195.0)]

# an UPGRADE whose SPIFF line sits under a "New Activation" category (the Z1321IN11252 shape)
UPG_WITH_ACT_LINE = [
    L("Z1321IN11252", "Upgrade Rate Plan", "Rate Plans"),
    L("Z1321IN11252", "Upgrade - ISPU", "Equip Rebates (VZ Commission) >> Upgrades (VZ Commission)", "9176004288", 75.0),
    L("Z1321IN11252", "Installment Rebate ISPU Upgrade", "Integration SKUs >> Other Installment SKUs", "9176004288", 1210.0),
    L("Z1321IN11252", "FAST START SPF - ISPU", "Equip Rebates (VZ Commission) >> New Activation (VZ Commission)", "9176004288", 25.0),
    L("Z1321IN11252", "APPLE IPHONE 17 PRO MAX 256GB DEEP BLUE", "Apple >> Apple Smartphones", "356392245740940"),
]


# ═════════════════════════════════════════════════════════════════════════════════════════════════
section("A. the owner's invoice Z1321IN11092: one activation, 3 activation-type lines → $10, not $30")
after = pay(Z1321IN11092)
before = pay(Z1321IN11092, gate=PRE_FIX)
ract = rule_of(after, "RACT")
lines = [l for l in ract.get("lines") or []]
check("A1 the three lines the owner saw are the rule's matched lines",
      sorted(l["product"] for l in lines) == sorted(["DPA New Act iPhone (Rate Plan Rebate)",
                                                    "VERIZON BUSINESS TRACKING NEW ACTIVATION",
                                                    "32066 My Biz Plan - New Act"]), [l["product"] for l in lines])
check("A2 pre-fix engine pays $30.00 on this invoice (the owner's screen, reproduced)",
      txn_pay(before, "RACT", "Z1321IN11092") == 30.0, txn_pay(before, "RACT", "Z1321IN11092"))
check("A3 the fix pays $10.00 — one activation", txn_pay(after, "RACT", "Z1321IN11092") == 10.0,
      txn_pay(after, "RACT", "Z1321IN11092"))
check("A4 Units = 1 (qualifying units are events, not lines)", ract.get("qualifying_units") == 1, ract.get("qualifying_units"))
check("A5 every line is shown; two pay $0 with the per-event reason",
      len(lines) == 3 and sum(1 for l in lines if l.get("suppressed")) == 2
      and all(G.SUPPRESS_LABELS["unit_same_event"] == l.get("suppressed_reason") for l in lines if l.get("suppressed")))
check("A6 each line carries its event: phone line 9297458084, type activation",
      {(l.get("event_key"), l.get("event_type")) for l in lines} == {(PHONE, "activation")})
check("A7 the paying line is the highest-priced one (the $150 rebate line — deterministic)",
      [l["product"] for l in lines if not l.get("suppressed")] == ["DPA New Act iPhone (Rate Plan Rebate)"])
check("A8 the rule resolved per_event from the auto default (source 'auto_event_field')",
      (ract.get("unit_basis"), ract.get("unit_basis_source")) == ("per_event", "auto_event_field"),
      (ract.get("unit_basis"), ract.get("unit_basis_source")))


def _regression_holds(res):
    return txn_pay(res, "RACT", "Z1321IN11092") == 10.0


check("A9 ARMED: the regression check is RED on the pre-fix engine", not _regression_holds(before))
check("A10 ... and GREEN on the fix", _regression_holds(after))
check("A11 the pay-gate report names the event basis and counts the collapse",
      (after.get("pay_gate") or {}).get("unit", {}).get("by_rule", {}).get("RACT", {}).get("basis") == "per_event"
      and (after.get("pay_gate") or {}).get("unit", {}).get("activation_events", {}).get("events") == 1)

# ═════════════════════════════════════════════════════════════════════════════════════════════════
section("B. several activations on one invoice: one per phone line — $40, not $10 and not per line")
b_after, b_before = pay(MULTI), pay(MULTI, gate=PRE_FIX)
check("B1 pre-fix pays per line: $%.2f" % txn_pay(b_before, "RACT", "Z1321IN11301"),
      txn_pay(b_before, "RACT", "Z1321IN11301") == 10.0 * len([1 for r in MULTI if LC.classify_line(r, LC.resolve_rules(TENANT_ADR)) in ("premium", "byod")]))
check("B2 the fix pays 4 activations = $40.00", txn_pay(b_after, "RACT", "Z1321IN11301") == 40.0,
      txn_pay(b_after, "RACT", "Z1321IN11301"))
_bl = rule_of(b_after, "RACT").get("lines") or []
check("B3 the four paying lines name four different phone lines",
      sorted(l["event_key"] for l in _bl if not l.get("suppressed")) == ["2679044499", "5169149951", "6462011035", "9175199951"])
check("B4 the customer-owned-device line and the unnumbered rate-plan line are EVIDENCE — shown, $0, named",
      all(l.get("suppressed_reason") == G.SUPPRESS_LABELS["unit_event_evidence"]
          for l in _bl if l["product"] in ("Customer Owned Device (START PAW)", "New Activation Rate Plan")))
_ev = LC.activation_events([r for r in MULTI], LC.resolve_rules(TENANT_ADR))
check("B5 the event definition reports the shared evidence as ambiguous (never guesses whose it is)",
      any(a["code"] == "event_evidence_shared" and a["trans_id"] == "Z1321IN11301" for a in _ev["ambiguous"]))

# ═════════════════════════════════════════════════════════════════════════════════════════════════
section("C. an upgrade with a line classed 'activation': Upgrade $5 once; Activation $0")
c = pay(UPG_WITH_ACT_LINE)
check("C1 Upgrade pays once: $5.00", txn_pay(c, "RUPG", "Z1321IN11252") == 5.0, txn_pay(c, "RUPG", "Z1321IN11252"))
check("C2 Activation pays nothing for it (the event is an upgrade)", txn_pay(c, "RACT", "Z1321IN11252") == 0.0,
      txn_pay(c, "RACT", "Z1321IN11252"))
_cl = rule_of(c, "RACT").get("lines") or []
check("C3 ...and says why on the line", len(_cl) == 1 and _cl[0].get("suppressed_reason") == G.SUPPRESS_LABELS["unit_event_other_type"])
c_pre = pay(UPG_WITH_ACT_LINE, gate=PRE_FIX)
check("C4 pre-fix paid $10 activation + $15 upgrade (3 lines) on this ONE upgrade — live Z1321IN11252 exactly",
      (txn_pay(c_pre, "RACT", "Z1321IN11252"), txn_pay(c_pre, "RUPG", "Z1321IN11252")) == (10.0, 15.0),
      (txn_pay(c_pre, "RACT", "Z1321IN11252"), txn_pay(c_pre, "RUPG", "Z1321IN11252")))

# ═════════════════════════════════════════════════════════════════════════════════════════════════
section("D. the event definition (line_class.activation_events)")
R = LC.resolve_rules(TENANT_ADR)
H = LC.HOUSE_RULES


def ct(tid, c, serial="", mdn=""):
    return {"trans_id": tid, "contract_type": c, "serial_1": serial, "mdn": mdn}


ev = LC.activation_events([ct("1", "Activation", "", "5165550101"), ct("1", "Activation", "", "5165550102"),
                           ct("1", "Activation", "", "5165550101")], H)
check("D1 two phone lines → two events; the repeated phone folds", len(ev["events"]) == 2)
ev = LC.activation_events([ct("1", "Activation", "356938035643809"), ct("1", "Activation", "356938035643810")], H)
check("D2 no phone anywhere → one event per DEVICE", sorted(e["key_kind"] for e in ev["events"]) == ["device", "device"])
ev = LC.activation_events([ct("1", "Activation"), ct("1", "Activation")], H)
check("D3 neither → ONE event for the invoice, reported", len(ev["events"]) == 1 and ev["events"][0]["key_kind"] == "invoice"
      and any(a["code"] == "event_no_key" for a in ev["ambiguous"]))
ev = LC.activation_events([ct("1", "Activation", "", "5165550101"), ct("1", "Activation", "356938035643809")], H)
check("D4 a device-only line beside ONE phone line is that activation's evidence", len(ev["events"]) == 1
      and len(ev["events"][0]["lines"]) == 2)
ev = LC.activation_events([ct("1", "BYOD Activation", "356938035643809"), ct("1", "Activation", "", "5165550101")],
                          LC.resolve_rules({"event": {"keys": ["device", "phone"]}}))
check("D5 per-org key order ('device' first) is honoured", [e["key_kind"] for e in ev["events"]] == ["device"])
ev = LC.activation_events([ct("1", "BYOD Activation", "", "5165550101"), ct("1", "Activation", "", "5165550101")], H)
check("D6 mixed classes on one phone line → one event, type by precedence (byod), reported",
      len(ev["events"]) == 1 and ev["events"][0]["cls"] == "byod" and ev["events"][0]["bucket"] == "byod"
      and any(a["code"] == "event_mixed_classes" for a in ev["ambiguous"]))
ev = LC.activation_events([ct("1", "BYOD Activation", "", "5165550101"), ct("1", "Activation", "", "5165550101")],
                          LC.resolve_rules({"event": {"precedence": ["activation"]}}))
check("D7 a per-org precedence reorders it (activation first)", ev["events"][0]["cls"] == "activation")
ev = LC.activation_events([ct("", "Activation", "", "5165550101"), ct("", "Activation", "", "5165550101")], H)
check("D8 lines with NO trans id are never merged into one event", len(ev["events"]) == 2)
ev = LC.activation_events([ct("1", "Activation", "", "5165550101"), ct("1", "Activation", "", "5165550102")],
                          LC.resolve_rules({"event": {"keys": []}}))
check("D9 keys: [] → one event per invoice (a tenant may choose it)", len(ev["events"]) == 1)
ev = LC.activation_events([ct("1", "Upgrade", "", "5165550101"), ct("2", "Upgrade", "", "5165550101")], H)
check("D10 the same phone upgraded on two invoices: two events, reported for a person to judge",
      len(ev["events"]) == 2 and any(a["code"] == "event_key_on_several_invoices" for a in ev["ambiguous"]))
ev = LC.activation_events([ct("1", "Accessory"), ct("1", "Hardware Only", "356938035643809")],
                          LC.resolve_rules({"tokens": {"hardware_only": ["hardware only"]}}))
check("D11 non-activation lines and hardware-only lines make no event", ev["events"] == [])
check("D12 junk config falls to the house default",
      LC.resolve_event({"keys": ["nonsense"], "precedence": "x", "count_unit": "lines"})["count_unit"] == "transaction"
      and LC.resolve_event(None) == dict(LC.HOUSE_EVENT, source="house"))
check("D13 the phone key is THE customer phone rule (a device id is never a phone; the mdn column wins)",
      LC.line_event_keys({"serial_1": "356938035643809", "mdn": "(516) 555-0101"}) == {"phone": "5165550101", "device": "356938035643809"})

# ═════════════════════════════════════════════════════════════════════════════════════════════════
section("E. byte-identity: count_unit 'transaction' (house) == the retired per-transaction counting")
rnd = random.Random(20260925)
CTS = ["Activation", "BYOD Activation", "Upgrade", "Port In", "Activation AAL", "", "Accessory", "Hardware Only",
       "BYOD Upgrade", "SWAP", "port with idv", None]


def fuzz_rows(n):
    out = []
    for _ in range(n):
        out.append({"trans_id": rnd.choice(["", "12", "12.0", " 13 ", "14", "15", "A-1", None, "16"]),
                    "contract_type": rnd.choice(CTS), "salesperson": rnd.choice(["Rep A", "Rep B", "admin", ""]),
                    "store": rnd.choice(["S1", "S2"]), "trans_date": rnd.choice(["2026-07-01", "2026-07-02"]),
                    "serial_1": rnd.choice(["", "5165550101", "5165550102", "356938035643809", "1852600095"]),
                    "mdn": rnd.choice(["", "", "5165550103"]), "voided": rnd.choice(["No", "No", "Yes", ""]),
                    "trans_type": rnd.choice(["Sale", "Sale", "Return", None]), "ext_price": rnd.choice([0, 10, 25.5]),
                    "gp": 1.0, "department": rnd.choice(["", "Accessories", "Phones"]), "category": "",
                    "product_desc": rnd.choice(["x", "case", "Device Setup Charge"])})
    return out


def retired_sets(rows, rules, tof, require_tid, skip):
    s = {"premium": set(), "byod": set(), "upgrade": set()}
    for r in rows:
        if skip is not None and skip(r):
            continue
        tid = tof(r)
        cls = LC.classify_line(r, rules)
        if cls and (tid or not require_tid):
            s[cls].add(tid)
    return s


def unit_sets(rows, rules, tof, require_tid, skip):
    s = {"premium": set(), "byod": set(), "upgrade": set()}
    for u in LC.activation_units(rows, rules, skip=skip, txn_of=tof, require_txn=require_tid):
        if u:
            s[u[0]].add(u[1])
    return s


SPELLINGS = {
    "_sales_cell_agg / commission-drill / closing (strip, needs an id)": (lambda r: str(r.get("trans_id") or "").strip(), True),
    "calculator ('.0' trimmed, a blank id counts once)": (lambda r: str(r.get("trans_id", "")).replace(".0", "").strip(), False),
}
bad = []
for seed in range(400):
    rows = fuzz_rows(rnd.randint(0, 30))
    rules = rnd.choice([H, R, LC.resolve_rules(None, {"byod port aal": "byod", "swap": "none"})])
    for name, (tof, req) in SPELLINGS.items():
        if retired_sets(rows, rules, tof, req, cr._line_skip) != unit_sets(rows, rules, tof, req, cr._line_skip):
            bad.append((seed, name))
check("E1 activation_units('transaction') == the retired classify_line → set.add(tid), 400 seeds × 2 spellings × 3 rule sets",
      not bad, bad[:3])


def retired_cell_agg_sets(rows, acfg):
    """The retired `_sales_cell_agg` activation block, verbatim semantics (per (store, rep, day) cell)."""
    line_rules = cr._line_rules_of(acfg)
    blank = cr._blank_ct_bucket_map(rows, line_rules, acfg.get("activation_rules"))
    out = {}
    for r in rows:
        if cr._line_skip(r):
            continue
        rep = str(r.get("salesperson") or "").strip()
        if not rep or rep.lower() == "admin":
            continue
        k = (str(r.get("store") or "").strip(), rep, str(r.get("trans_date") or "")[:10])
        a = out.setdefault(k, {"_prem": set(), "_byod": set(), "_upg": set(), "_port": set()})
        tid = str(r.get("trans_id") or "").strip()
        full = LC.activation_class(r, line_rules)
        c = LC.bucket_of(full)
        if tid and c == "byod":
            a["_byod"].add(tid)
        elif tid and c == "upgrade":
            a["_upg"].add(tid)
        elif tid and c == "premium":
            a["_prem"].add(tid)
            if full == "port":
                a["_port"].add(tid)
        elif tid and not c and tid in blank:
            a[{"byod": "_byod", "upgrade": "_upg", "premium": "_prem"}[blank[tid]]].add(tid)
    return out


mism = []
for seed in range(200):
    rows = fuzz_rows(rnd.randint(1, 40))
    acfg = cr._accessory_config(FakeClient({("commcalc", "accessory_config"): [
        {"org_id": ORG, "activation_details_rules": rnd.choice([None, TENANT_ADR])}]}), ORG)
    new = cr._sales_cell_agg(copy.deepcopy(rows), acfg, exec_cfg={"phones": {"rules": []}, "bill_payment": {"rules": []},
                                                                  "activation_fee": {"rules": []}, "protect": {"rules": []}})
    old = retired_cell_agg_sets(rows, acfg)
    got = {k: {s: v[s] for s in ("_prem", "_byod", "_upg", "_port")} for k, v in new.items()}
    if got != old:
        mism.append(seed)
check("E2 _sales_cell_agg activation sets byte-identical to the retired block (200 seeds, house + tenant rules)",
      not mism, mism[:5])

# the Boost calculator's counts (house org, payout_config path)
_e3_bad, _e3_rows = [], 0
for _seed in range(60):
    _crow = fuzz_rows(rnd.randint(5, 60))
    _res = calc.calc_rep_commissions(_crow, [], [], [], [], [], {}, [], [], [], [], "July 2026", [],
                                     carrier_mode="boost")["commissions"]
    _valid = [r for r in _crow if not calc._is_voided(r.get("voided")) and str(r.get("trans_type", "")).strip() != "Return"]
    _want = {}
    for r in _valid:
        rep = str(r.get("salesperson", "")).strip()
        if not rep or rep.lower() == "admin":
            continue
        e = _want.setdefault(rep.upper(), {"premium": set(), "byod": set(), "upgrade": set()})
        c = LC.classify_line(r, None)
        if c:
            e[c].add(str(r.get("trans_id", "")).replace(".0", "").strip())
    for row in _res:
        w = _want.get(str(row.get("epay_salesperson") or "").upper())
        if w is None:
            continue
        _e3_rows += 1
        if (row["premium_acts"], row["byod_acts"], row["upgrade_acts"]) != (len(w["premium"]), len(w["byod"]), len(w["upgrade"])):
            _e3_bad.append((_seed, row.get("epay_salesperson")))
check("E3 the Boost calculator's premium/byod/upgrade counts == the retired distinct-trans-id counts (%d rep rows)" % _e3_rows,
      not _e3_bad and _e3_rows > 0, _e3_bad[:3])

# a tenant whose plan has NO activation-type $/unit rule: the preview is byte-identical with or without ⑥
OTHER = [{"id": "RVHI", "label": "VHI", "match_field": "product_desc", "match_op": "contains", "match_value": "home internet",
          "qualifies": True, "payout_kind": "flat_per_unit", "amount": 2.0, "pct": 0.0, "tiered": False, "unit_basis": None, "sort": 0},
         {"id": "REDG", "label": "edge", "match_field": "tender_type", "match_op": "contains", "match_value": "financing",
          "qualifies": True, "payout_kind": "flat_per_unit", "amount": 25.0, "pct": 0.0, "tiered": False, "unit_basis": None, "sort": 1},
         {"id": "RACC", "label": "Acc", "match_field": "department", "match_op": "equals", "match_value": "accessories",
          "qualifies": True, "payout_kind": "pct_price", "amount": 0.0, "pct": 0.1, "tiered": False, "unit_basis": None, "sort": 2}]
_orows = [dict(L("T%d" % i, rnd.choice(["Home Internet", "Case", "x"]), "c", rnd.choice(["", "356938035643809"]), 10.0),
               department=rnd.choice(["Accessories", "Phones"]), tender_type=rnd.choice(["Cash", "TW Financing"]))
          for i in range(40)] + Z1321IN11092
_a = pay(_orows, rules=OTHER, org="00000000-0000-0000-0000-0000000e7e02")
_b = pay(_orows, rules=OTHER, gate=PRE_FIX, org="00000000-0000-0000-0000-0000000e7e02")
check("E4 a plan with no activation-type $/unit rule: preview byte-identical with ⑥ on and off (detail mode)",
      json.dumps(_a, sort_keys=True, default=str) == json.dumps(_b, sort_keys=True, default=str))
check("E5 ...and no line carries an event stamp (no events are computed at all)",
      not any("event_id" in l for rep in _a["by_rep"] for rb in rep["rules"] for l in rb.get("lines") or []))
_ex = pay(Z1321IN11092, rules=[dict(ACT_RULE, unit_basis="per_line")])
check("E6 a rule that SAYS per_line keeps paying per line ($30) — the tenant's explicit word wins",
      txn_pay(_ex, "RACT", "Z1321IN11092") == 30.0)

# ═════════════════════════════════════════════════════════════════════════════════════════════════
section("F. %-of-basis rules are never collapsed")
PCT = dict(ACT_RULE, id="RPCT", payout_kind="pct_price", amount=0.0, pct=0.1)
check("F1 resolve_unit_basis: a pct rule on activation_bucket stays per_line",
      G.resolve_unit_basis(PCT, G.UNIT_DEFAULTS)[0] == "per_line")
_f = pay(Z1321IN11092, rules=[PCT])
check("F2 ...and pays 10% of every matched line ($150 + $0 + $45 → $19.50)", txn_pay(_f, "RPCT", "Z1321IN11092") == 19.5,
      txn_pay(_f, "RPCT", "Z1321IN11092"))
check("F3 the tenant can switch ⑥ off (auto_event_fields: [])",
      G.resolve_unit_basis(ACT_RULE, G.normalize_gate_config({"unit_basis": {"auto_event_fields": []}})["unit_basis"])[0] == "per_line")
check("F4 per_event without the event definition pays per line and SAYS so (never a guessed dedup)",
      G.select_paying_lines([{"trans_id": "1"}, {"trans_id": "1"}], "per_event", G.UNIT_DEFAULTS)[2][0]["code"] == "unit_event_unavailable")

# ═════════════════════════════════════════════════════════════════════════════════════════════════
section("G. counting surfaces under count_unit 'event' (a config row, not code)")
_evcfg = dict(TENANT_ADR, event={"count_unit": "event"})
_acfg_ev = cr._accessory_config(FakeClient({("commcalc", "accessory_config"): [
    {"org_id": ORG, "activation_details_rules": _evcfg}]}), ORG)
_acfg_tx = cr._accessory_config(FakeClient({("commcalc", "accessory_config"): [
    {"org_id": ORG, "activation_details_rules": TENANT_ADR}]}), ORG)


def cell_counts(rows, acfg):
    cells = cr._sales_cell_agg(copy.deepcopy(rows), acfg)
    return {s: len(set().union(*[c[s] for c in cells.values()])) for s in ("_prem", "_byod", "_upg")}


_g_ev, _g_tx = cell_counts(MULTI + Z1321IN11092 + UPG_WITH_ACT_LINE, _acfg_ev), cell_counts(MULTI + Z1321IN11092 + UPG_WITH_ACT_LINE, _acfg_tx)
check("G1 house 'transaction' (unchanged): invoices per bucket — 4 activations read as 1, Z…301 also as a byod, "
      "and the upgrade invoice ALSO as an activation", _g_tx == {"_prem": 3, "_byod": 1, "_upg": 1}, _g_tx)
check("G2 'event': 5 activations (4 + 1) and 1 upgrade — each event in ONE bucket",
      _g_ev == {"_prem": 5, "_byod": 0, "_upg": 1}, _g_ev)
check("G3 the upgrade's 'New Activation' spiff line no longer adds an activation under 'event'",
      cell_counts(UPG_WITH_ACT_LINE, _acfg_ev) == {"_prem": 0, "_byod": 0, "_upg": 1})

print("\n" + "=" * 100)
print("%d passed, %d failed" % (P, F))
sys.exit(1 if F else 0)
