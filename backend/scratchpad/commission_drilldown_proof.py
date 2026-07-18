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
import sys, os, json, random, subprocess, importlib.util, tempfile
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.modules.commcalc import commission_engine as ce
from app.modules.commcalc import sale_installment_engine as sie
from app.modules.commcalc import commission_drilldown as dd

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))


def _vendor_orig_engine():
    """Load origin/main's commission_engine.py VERBATIM as a separate module, so the differential tests
    the NEW code against the ACTUAL pre-drill engine (not a self-referential detail=False vs detail=True).
    Returns the module, or None if origin/main is unavailable."""
    try:
        src = subprocess.check_output(
            ["git", "-C", REPO, "show", "origin/main:backend/app/modules/commcalc/commission_engine.py"],
            stderr=subprocess.DEVNULL).decode()
    except Exception:
        return None
    with tempfile.NamedTemporaryFile("w", suffix="_ceo.py", delete=False) as f:
        f.write(src); path = f.name
    spec = importlib.util.spec_from_file_location("commission_engine_orig", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


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


def _j(o):
    return json.dumps(o, sort_keys=True, default=str)


# ── N1: orig-vendored differential — new engine default/forced output == origin/main's, verbatim ────
_FIELDS = ["contract_type", "department", "category", "product_desc", "tender_type", "any"]
_OPS = ["equals", "contains", "in"]
_KINDS = ["flat_per_unit", "pct_gp", "pct_mrc", "pct_price_over_cost", "flat"]
_SCOPES = ["employee", "role", "store", "market", "default"]
_REPS = ["Antunez, Diana", "Diana Antunez", "Bob Nomatch", "Islam Khan, Ariful", "zoe smith"]
_STORES = ["NYC-1", "NYC-2", "", "LA-9"]
_VALS = ["activation", "upgrade", "accessories", "byod", "case", "", "sales", "manager"]


def _rand_store(rng, org="ORG-F"):
    nplans = rng.randint(1, 3)
    plans, rules, tiers, assigns = [], [], [], []
    for pi in range(nplans):
        pid = f"P{pi}"
        plans.append(dict(id=pid, org_id=org, name=f"Plan {pi}", is_active=rng.random() > 0.2,
                          carrier_id="C1", base_tier_metric=rng.choice(["none", "units"])))
        for ti in range(rng.randint(0, 2)):
            tiers.append(dict(id=f"{pid}-t{ti}", org_id=org, plan_id=pid,
                              min_count=rng.randint(0, 5), multiplier=round(rng.uniform(0.5, 1.5), 2)))
        for ri in range(rng.randint(0, 4)):
            rules.append(dict(id=f"{pid}-r{ri}", org_id=org, plan_id=pid, sort=ri,
                              label=f"r{ri}", match_field=rng.choice(_FIELDS), match_op=rng.choice(_OPS),
                              match_value=rng.choice(_VALS), payout_kind=rng.choice(_KINDS),
                              amount=rng.randint(0, 30), pct=round(rng.uniform(0, 0.2), 3),
                              tiered=rng.random() > 0.5, qualifies=rng.random() > 0.15))
        for ai in range(rng.randint(0, 3)):
            assigns.append(dict(id=f"{pid}-a{ai}", org_id=org, plan_id=pid, scope=rng.choice(_SCOPES),
                                scope_value=rng.choice(_REPS + _STORES + _VALS), priority=rng.randint(0, 3)))
    sales = []
    for si in range(rng.randint(0, 20)):
        sales.append(_sale(org_id=org, salesperson=rng.choice(_REPS), store=rng.choice(_STORES),
                           contract_type=rng.choice(_VALS), department=rng.choice(_VALS),
                           category=rng.choice(_VALS), product_desc=rng.choice(["Total 5G $50/mo", "Case", "x"]),
                           tender_type=rng.choice(_VALS), ext_price=rng.randint(0, 80), gp=rng.randint(0, 40),
                           mdn=str(rng.randint(1000, 9999)), serial_1=str(rng.randint(10000, 99999)),
                           trans_id=f"T{si}", voided=rng.choice(["NO", "NO", "YES"]),
                           trans_type=rng.choice(["Sale", "Sale", "Return"])))
    return FakeClient({
        "commcalc.commission_plan": plans, "commcalc.commission_rule": rules,
        "commcalc.commission_tier": tiers, "commcalc.commission_plan_assignment": assigns,
        "commcalc.raw_sales": sales, "commcalc.daily_sales_feed": [], "commcalc.raw_mi": [],
        "commcalc.raw_catalog": [], "commcalc.store_mapping": [
            dict(org_id=org, store_address="NYC-1", store_code="NYC-1", market="Metro NY")],
        "storeops.employees": [dict(id=1, org_id=org, name="Diana Antunez", role="sales")],
    }, ), plans


def differential(orig):
    print("\n[0] orig-vendored differential — NEW engine == origin/main's, verbatim (fuzz 200)")
    if orig is None:
        check("origin/main commission_engine available", False, "git show failed — differential skipped")
        return
    rng = random.Random(20260718)
    mism_def = mism_forced = mism_resolve = 0
    N = 200
    for _ in range(N):
        client, plans = _rand_store(rng)
        # default preview (money path) must match EXACTLY
        if _j(ce.preview(client, "ORG-F", PER)) != _j(orig.preview(client, "ORG-F", PER)):
            mism_def += 1
        # forced-plan preview (used by /payout-plans/preview) must match EXACTLY
        for p in plans:
            if _j(ce.preview(client, "ORG-F", PER, plan_id=p["id"])) != \
               _j(orig.preview(client, "ORG-F", PER, plan_id=p["id"])):
                mism_forced += 1
                break
        # _resolve_plan_for winner (single source of truth) must match
        np_, _rd = ce._load_plans(client, "ORG-F")
        op_, _rd2 = orig._load_plans(client, "ORG-F")
        for rep in _REPS:
            for store in _STORES:
                wn = ce._resolve_plan_for(rep, store, "Metro NY", np_, rep_role="sales")
                wo = orig._resolve_plan_for(rep, store, "Metro NY", op_, rep_role="sales")
                if (wn or {}).get("id") != (wo or {}).get("id"):
                    mism_resolve += 1
                    break
    check(f"default preview identical across {N} random tenants", mism_def == 0, f"{mism_def} mismatch")
    check(f"forced-plan preview identical across {N} random tenants", mism_forced == 0, f"{mism_forced} mismatch")
    check("_resolve_plan_for winner identical", mism_resolve == 0, f"{mism_resolve} mismatch")

    # N2 regression: forced-plan preview with a NON-NUMERIC assignment priority — the lazy short-circuit
    # means _resolve_plan_for is never called, so BOTH engines succeed and match (the eager drift is gone).
    bad = FakeClient({
        "commcalc.commission_plan": [dict(id="PX", org_id="ORG-F", name="X", is_active=True)],
        "commcalc.commission_rule": [dict(id="rx", org_id="ORG-F", plan_id="PX", sort=0, match_field="any",
                                          payout_kind="flat_per_unit", amount=5, qualifies=True)],
        "commcalc.commission_tier": [],
        "commcalc.commission_plan_assignment": [dict(id="ax", org_id="ORG-F", plan_id="PX",
                                                     scope="employee", scope_value="Diana Antunez",
                                                     priority="not-a-number")],
        "commcalc.raw_sales": [_sale(org_id="ORG-F", salesperson="Diana Antunez", store="NYC-1",
                                     contract_type="activation", trans_id="T1")],
        "commcalc.daily_sales_feed": [], "commcalc.raw_mi": [], "commcalc.raw_catalog": [],
        "commcalc.store_mapping": [], "storeops.employees": [],
    })
    try:
        same = _j(ce.preview(bad, "ORG-F", PER, plan_id="PX")) == _j(orig.preview(bad, "ORG-F", PER, plan_id="PX"))
        check("N2: forced-plan preview w/ non-numeric priority — lazy, both succeed & match", same)
    except Exception as e:
        check("N2: forced-plan preview w/ non-numeric priority — lazy, both succeed & match", False, str(e)[:80])


def main():
    orig = _vendor_orig_engine()
    differential(orig)

    c = build_store()

    print("\n[1] preview detail-mode preserves the default money + adds no key leak to the default shape")
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

    print("\n[8] held-reason selection matches the actual gate criterion per mode (N3/N4)")
    mi_inactive = {"subscriber_status": "Deactivated", "actual_mi_payout": 0, "actual_atu_payout": 0}
    mi_active_nores = {"subscriber_status": "Active", "actual_mi_payout": 0, "actual_atu_payout": 0}
    def reason(status, gate_mode, mi, gate_kind=None, stored=False):
        row = {"status": status, "gate_mode": gate_mode}
        if gate_kind:
            row["gate_kind"] = gate_kind
        return dd._installment_reason(row, mi, stored=stored)[0]
    check("paid_residual + inactive → line_inactive",
          reason("withheld_unpaid", "paid_residual", mi_inactive) == "line_inactive")
    check("paid_residual + active+resid0 → residual_not_received",
          reason("withheld_unpaid", "paid_residual", mi_active_nores) == "residual_not_received")
    check("N4: nonzero_residual + inactive+resid0 → residual_not_received (NOT line_inactive)",
          reason("withheld_unpaid", "nonzero_residual", mi_inactive) == "residual_not_received")
    check("active_status + inactive → line_inactive",
          reason("withheld_unpaid", "active_status", mi_inactive) == "line_inactive")
    check("held + no raw_mi + LIVE → no_mi_match",
          reason("withheld_unpaid", "paid_residual", None, stored=False) == "no_mi_match")
    check("N3: held + no raw_mi + STORED (no gate_kind) → held_stored (reduced confidence)",
          reason("withheld_unpaid", "paid_residual", None, stored=True) == "held_stored")
    check("activation gate held → activation_payment_missing",
          reason("withheld_unpaid", "paid_residual", None, gate_kind="activation_payment") == "activation_payment_missing")

    print("\n[9] _no_plan_narration carries explicit tier fields (N5 — no blank '× tier =')")
    nn = dd._no_plan_narration(c, ORG, PER, "Bob Nomatch", ce)
    check("has base_payout/tiered_payout/tier_multiplier=1.0",
          nn.get("base_payout") == 0.0 and nn.get("tiered_payout") == 0.0 and nn.get("tier_multiplier") == 1.0)

    print("\n" + ("ALL PASS" if not FAILS else f"FAILURES: {FAILS}"))
    return 1 if FAILS else 0


if __name__ == "__main__":
    sys.exit(main())
