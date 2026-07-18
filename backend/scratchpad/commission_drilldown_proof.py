"""Proof harness for the commission drill-down (READ-ONLY 'how was this calculated').

Runs the REAL engine + drilldown code against a synthetic in-memory Supabase-shaped client. Proves:
  1. preview(detail=False, only_rep=None) is BYTE-IDENTICAL to before (no new keys leak; totals equal
     the detail-mode totals) — the live calc path is unchanged.
  2. _resolve_plan_for(explain=False) winner == explain=True winner plan (single source of truth).
  3. Plan component drill: winning assignment narration + per-rule matched sale lines + per-line $.
  4. Multi-month component: per-device M1 installment with HELD gate + reason classification, and the
     MA-file cross-reference surfacing "MA says paid" next to a held installment (the owner's IMEI case).
  5. A $0 rep gets an explicit nearest-miss explanation (not an empty page).
  6. Device search by IMEI: sale line + plan pay + installment(+gate) + MA match.
  7. org isolation: a read for org B never returns org A rows.

Run: PYTHONPATH=backend python3 backend/scratchpad/commission_drilldown_proof.py
"""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.modules.commcalc import commission_engine as ce
from app.modules.commcalc import sale_installment_engine as sie
from app.modules.commcalc import commission_drilldown as dd


# ── minimal chainable fake matching the supabase-py builder surface the code uses ──────────────────
class FakeQuery:
    def __init__(self, rows):
        self._rows = list(rows)
        self._eq = []
        self._in = []
    def select(self, *a, **k): return self
    def eq(self, col, val): self._eq.append((col, val)); return self
    def in_(self, col, vals): self._in.append((col, set(str(v) for v in vals))); return self
    def order(self, *a, **k): return self
    def limit(self, *a, **k): return self
    def range(self, a, b):
        self._range = (a, b); return self
    def _apply(self):
        rows = self._rows
        for col, val in self._eq:
            rows = [r for r in rows if str(r.get(col)) == str(val)]
        for col, vals in self._in:
            rows = [r for r in rows if str(r.get(col)) in vals]
        rng = getattr(self, "_range", None)
        if rng:
            rows = rows[rng[0]:rng[1] + 1]
        return rows
    def execute(self):
        return type("R", (), {"data": self._apply()})()


class FakeSchema:
    def __init__(self, store, schema): self.store, self.schema_name = store, schema
    def table(self, t): return FakeQuery(self.store.get(f"{self.schema_name}.{t}", []))


class FakeClient:
    def __init__(self, store): self.store = store
    def schema(self, s): return FakeSchema(self.store, s)
    def table(self, t): return FakeQuery(self.store.get(f"public.{t}", []))


ORG = "ORG-T"; ORGB = "ORG-B"; PER = "June 2026"
IMEI = "355163568356973"


def _sale(**k):
    base = dict(org_id=ORG, period=PER, voided="NO", trans_type="Sale", mdn="", serial_1="",
                department="", category="", contract_type="", product_desc="", ext_price=0, gp=0,
                tender_type="", store="", salesperson="", trans_id="", trans_date="2026-06-05")
    base.update(k); return base


def build_store():
    plans = [dict(id="P1", org_id=ORG, name="Total Base", is_active=True, carrier_id="C1",
                  base_tier_metric="none")]
    rules = [
        dict(id="R1", org_id=ORG, plan_id="P1", sort=0, label="Activation $20",
             match_field="contract_type", match_op="equals", match_value="activation",
             payout_kind="flat_per_unit", amount=20, tiered=False, qualifies=True),
        dict(id="R2", org_id=ORG, plan_id="P1", sort=1, label="Accessory 10% GP",
             match_field="department", match_op="equals", match_value="accessories",
             payout_kind="pct_gp", pct=0.1, tiered=False, qualifies=True),
        dict(id="R3", org_id=ORG, plan_id="P1", sort=2, label="Upgrade $10",
             match_field="contract_type", match_op="equals", match_value="upgrade",
             payout_kind="flat_per_unit", amount=10, tiered=False, qualifies=True),
    ]
    assigns = [
        dict(id="A1", org_id=ORG, plan_id="P1", scope="employee", scope_value="Diana Antunez", priority=0),
        dict(id="A2", org_id=ORG, plan_id="P1", scope="store", scope_value="NYC-1", priority=0),
    ]
    sales = [
        # Diana (POS "Last, First") — activation device + an accessory line on the same trans
        _sale(salesperson="Antunez, Diana", store="NYC-1", contract_type="activation",
              serial_1=IMEI, mdn="5551112222", department="phones", product_desc="Total 5G $50/mo",
              ext_price=50, gp=10, trans_id="T100"),
        _sale(salesperson="Antunez, Diana", store="NYC-1", department="accessories",
              product_desc="Phone Case", ext_price=30, gp=15, trans_id="T100"),
        # Bob — activation, but NOT employee-assigned and store not NYC-1 → no plan → $0
        _sale(salesperson="Bob Nomatch", store="NOSTORE", contract_type="activation",
              serial_1="999888777666555", mdn="5559990000", department="phones",
              product_desc="Total 5G $40/mo", ext_price=40, gp=8, trans_id="T200"),
    ]
    sched = [dict(id="S1", org_id=ORG, plan_id="P1", num_months=6, gate_mode="paid_residual",
                  gate_from_month=1, m1_gate="inherit", is_active=True,
                  trigger_match_field="contract_type", trigger_match_op="equals",
                  trigger_match_value="activation", eligible_sale_periods=[])]
    slines = [dict(org_id=ORG, schedule_id="S1", month_index=1, payout_kind="pct_mrc", mrc_pct=0.05,
                   mrc_source="product_catalog", flat_amount=0)]
    for m in range(2, 7):
        slines.append(dict(org_id=ORG, schedule_id="S1", month_index=m, payout_kind="flat",
                           flat_amount=5, mrc_pct=0))
    # MA-file: the June MA rows for IMEI show a paid month-1 spiff (NEGATIVE = paid to dealer, per mig
    # 083) → the owner's exact case: in-app HELD (no raw_mi) but "the June MA file says paid". Real data
    # returns TWO rows for one IMEI/period (base + adjustment) and line_status is NULL (facts from live).
    ma = [dict(org_id=ORG, period=PER, period_month=6, period_year=2026, tx_date="2026-06-05",
               carrier_name="Total by Verizon", activation_order="AO-1", merchant_account_id="M-1",
               imei=IMEI, sim="SIM1", ban="BAN1", activation_type="New", activation_type2="byop",
               mrc_net_discount=50, rebate=-15, line_status=None, user_name="Diana Antunez",
               spiff_m1=-5, spiff_m2=0, spiff_m3=0, spiff_m4=0, spiff_m5=0, spiff_m6=0),
          dict(org_id=ORG, period=PER, period_month=6, period_year=2026, tx_date="2026-06-20",
               carrier_name="Total by Verizon", activation_order="AO-1-ADJ", merchant_account_id="M-1",
               imei=IMEI, sim="SIM1", ban="BAN1", activation_type="Add", activation_type2="byop",
               mrc_net_discount=0, rebate=0, line_status=None, user_name="Diana Antunez",
               spiff_m1=-1, spiff_m2=0, spiff_m3=0, spiff_m4=0, spiff_m5=0, spiff_m6=0)]
    # roster so role-scope / name bridge is exercised (Diana present "First Last")
    employees = [dict(id=1, org_id=ORG, name="Diana Antunez", role="sales")]
    store = {
        "commcalc.commission_plan": plans, "commcalc.commission_rule": rules,
        "commcalc.commission_tier": [], "commcalc.commission_plan_assignment": assigns,
        "commcalc.raw_sales": sales, "commcalc.daily_sales_feed": [],
        "commcalc.raw_mi": [],  # Total → residuals NOT in raw_mi (they arrive in the MA file)
        "commcalc.raw_catalog": [], "commcalc.store_mapping": [
            dict(org_id=ORG, store_address="NYC-1", store_code="NYC-1", market="Metro NY")],
        "storeops.employees": employees,
        "commcalc.plan_installment_schedule": sched, "commcalc.plan_installment_line": slines,
        "commcalc.product_mrc": [], "commcalc.carrier_category_map": [], "commcalc.flag_rules": [],
        "commcalc.commission_org_config": [], "commcalc.item_mapping": [],
        "commcalc.raw_ma_commission": ma, "commcalc.sale_installment_ledger": [],
        "commcalc.rep_commissions": [dict(org_id=ORG, period=PER, epay_salesperson="Antunez, Diana",
                                          storeops_name="Diana Antunez", plan_comm=21.5,
                                          installment_comm_sale=0.0, residual_installment_comm=0.0,
                                          total_payout=21.5)],
        "commcalc.carrier": [dict(org_id=ORG, id="C1", name="Total", is_default=True)],
        # org B — isolation control: same tables, different org, a plan that must NEVER leak into org A
        "commcalc.commission_plan_B": [],
    }
    # org-B rows appended into the SAME tables (to prove .eq(org_id) isolation)
    store["commcalc.commission_plan"].append(dict(id="PB", org_id=ORGB, name="OtherCo", is_active=True))
    store["commcalc.raw_sales"].append(_sale(org_id=ORGB, salesperson="Zed OrgB", store="B-1",
                                             contract_type="activation", serial_1="000",
                                             product_desc="x", ext_price=99, gp=99, trans_id="B1"))
    return FakeClient(store)


FAILS = []
def check(name, cond, extra=""):
    print(f"  {'PASS' if cond else 'FAIL'}  {name}" + (f"  · {extra}" if extra else ""))
    if not cond:
        FAILS.append(name)


def main():
    c = build_store()

    print("\n[1] preview default (detail=False) is byte-identical / no key leak")
    pv0 = ce.preview(c, ORG, PER)
    pv1 = ce.preview(c, ORG, PER, detail=True)
    diana0 = next(r for r in pv0["by_rep"] if "antunez" in r["rep"].lower())
    diana1 = next(r for r in pv1["by_rep"] if "antunez" in r["rep"].lower())
    check("default preview has NO detail keys",
          all(k not in diana0 for k in ("assignment", "considered", "tiers", "base_tier_metric")))
    check("default rules carry NO 'lines'", all("lines" not in rb for rb in diana0["rules"]))
    check("default rules EXCLUDE zero-match rule R3", all(rb["rule_id"] != "R3" for rb in diana0["rules"]),
          f"rule ids={[rb['rule_id'] for rb in diana0['rules']]}")
    check("detail total == default total (money unchanged)",
          diana0["total_payout"] == diana1["total_payout"], f"${diana0['total_payout']}")
    check("Diana plan total == $21.50 (20 activation + 1.50 acc GP)", diana0["total_payout"] == 21.5)
    check("org isolation: no org-B rep in preview", all("orgb" not in r["rep"].lower() for r in pv0["by_rep"]))

    print("\n[2] _resolve_plan_for explain winner == default winner (single source)")
    plans, _ = ce._load_plans(c, ORG)
    plain = ce._resolve_plan_for("Antunez, Diana", "NYC-1", "Metro NY", plans, rep_role="sales")
    expl = ce._resolve_plan_for("Antunez, Diana", "NYC-1", "Metro NY", plans, rep_role="sales", explain=True)
    check("winner plan id equal", plain and plain.get("id") == expl["plan"].get("id") == "P1")
    check("winner assignment = EMPLOYEE scope (outranks store)", expl["winner"]["scope"] == "employee",
          f"scope={expl['winner']['scope']} value={expl['winner']['scope_value']}")
    check("considered lists BOTH assignments matched",
          sum(1 for a in expl["considered"] if a["matched"]) == 2)

    print("\n[3] plan component drill (explain_rep, Diana)")
    ex = dd.explain_rep(c, ORG, PER, "Diana Antunez", carrier_mode="plan")
    pc = ex["plan_component"]
    check("plan attached = Total Base", pc["plan_name"] == "Total Base")
    check("assignment narration = employee 'Diana Antunez'",
          pc["assignment"]["scope"] == "employee" and "Diana" in str(pc["assignment"]["scope_value"]))
    r1 = next(r for r in pc["rules"] if r["rule_id"] == "R1")
    check("R1 matched 1 line w/ per-line $20 + IMEI + basis fields",
          r1["matched_lines"] == 1 and r1["lines"][0]["amount"] == 20
          and r1["lines"][0]["imei"] == IMEI and r1["lines"][0]["contract_type"] == "activation")
    r2 = next(r for r in pc["rules"] if r["rule_id"] == "R2")
    check("R2 accessory pct_gp line = $1.50 (10% of 15 GP)", r2["lines"][0]["amount"] == 1.5)
    check("zero-match rule R3 IS shown in detail (to explain $0)",
          any(r["rule_id"] == "R3" for r in pc["rules"]))

    print("\n[4] multi-month component: HELD gate + reason + MA cross-reference (owner's IMEI case)")
    mm = ex["multimonth_component"]
    dev = next(d for d in mm["devices"] if d["imei"] == IMEI)
    inst1 = next(i for i in dev["installments"] if i["month_index"] == 1)
    check("M1 installment present, status HELD", inst1["status"] != "paid")
    check("hold reason = no_mi_match (dealer not shown paid in raw_mi)", inst1["hold_reason"] == "no_mi_match",
          inst1["hold_detail"][:60])
    check("held row pays $0 but shows WITHHELD would-be $2.50 (pct_mrc $50*5%)",
          inst1["amount"] == 0.0 and inst1["withheld_amount"] == 2.5,
          f"amount={inst1['amount']} withheld={inst1['withheld_amount']}")
    check("MA file matched this IMEI — BOTH rows (base + adjustment)",
          len(dev["ma_matches"]) == 2 and all(m["imei"] == IMEI for m in dev["ma_matches"]))
    check("MA payout sign-normalized POSITIVE (spiff_m1 -5 → +5 paid), NULL line_status not treated inactive",
          dev["ma_matches"][0]["spiffs_paid"]["m1"] == 5.0 and dev["ma_matches"][0]["line_status"] is None)
    check("MA says PAID (nonzero spiff) while in-app HELD → held_but_ma_paid flag",
          dev["ma_says_paid"] and dev["held_but_ma_paid"])
    check("reconciliation shows last-calc plan_comm 21.5", ex["reconciliation"]["plan_comm"] == 21.5)

    print("\n[5] $0 rep gets explicit nearest-miss explanation (Bob)")
    exb = dd.explain_rep(c, ORG, PER, "Bob Nomatch", carrier_mode="plan")
    pcb = exb["plan_component"]
    check("Bob has NO plan attached", not pcb.get("plan_name"))
    check("Bob considered list carries nearest-miss assignments",
          any(not a["matched"] for a in (pcb.get("considered") or [])))
    check("zero_explanation is NON-empty and names the unmatched assignments",
          exb["zero_explanation"] and any("assignment matched" in z for z in exb["zero_explanation"]),
          "; ".join(exb["zero_explanation"])[:110])

    print("\n[6] device search by IMEI")
    ds = dd.device_story(c, ORG, IMEI, period=PER)
    check("sale line found for IMEI", any(s["imei"] == IMEI for s in ds["sale_lines"]))
    check("plan pay attributable to device = $20 activation",
          any(p["amount"] == 20 for p in ds["plan_pay"]))
    check("installment(s) with gate reason present (live-merged)",
          ds["installments"] and ds["installments"][0]["hold_reason"] == "no_mi_match")
    check("MA match present (2 rows) + rebate sign-normalized to +$15 paid",
          len(ds["ma_matches"]) == 2 and ds["rebate_total"] == 15.0)

    print("\n[7] org isolation for device story (org B cannot see org A IMEI)")
    dsb = dd.device_story(c, ORGB, IMEI)
    check("org B sees NO sale lines / MA rows for org-A IMEI",
          not dsb["sale_lines"] and not dsb["ma_matches"])

    print("\n" + ("ALL PASS" if not FAILS else f"FAILURES: {FAILS}"))
    return 1 if FAILS else 0


if __name__ == "__main__":
    sys.exit(main())
