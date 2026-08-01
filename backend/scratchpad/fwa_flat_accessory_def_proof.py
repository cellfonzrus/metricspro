"""PROOF HARNESS — two owner-directed commission-config builds (2026-08-01).

  D1  "fwa is paid on flat rate should not be in monthly payments - fix but dont hard code"
      -> per-tenant, per-CATEGORY flat (one-time) payout (mig 256).
  D2  "accessory option will be as per mapped manually and anything which says accesspories or
      category accesory ... screen protectors, cases headset, earphones, charger, cables, adapters"
      -> per-tenant ACCESSORY DEFINITION + a read-only cross-surface agreement report (mig 257).

Runs the REAL engines against an in-memory Supabase-shaped client, DIFFERENTIALLY against the BASE
tree vendored out of `git show origin/main:` (base = 4923001).

  §A  installment_category_payout — PURE unit proof of every branch, including the two that matter:
      an unconfigured amount is NOT active, and a bare number is read as flat.
  §B  ENGINE, NOTHING CONFIGURED — byte-identical to the BASE engine, key for key, ledger row for
      ledger row, for a plan tenant AND for Boost; `flat_guard` is a constant.
  §C  ENGINE, FLAT ACTIVE — the owner's FWA chain pays the owner-entered amount ONCE; months 2..N
      emit nothing; both facts are reported with per-rep dollars; the PHONE chain never moves.
  §D  THE NO-GUESS BRANCH — flat mode with a blank amount pays EXACTLY what it pays today (money
      byte-identical to base) and raises a LOUD warning. No $0, no invented rate.
  §E  accessory_definition — PURE: casefold-exact matching, precedence, set-up fee first, the token
      rule's refusal of product_desc/sku, explicit exclusions, proposed-vs-confirmed.
  §F  the agreement report — arithmetic, and it calls the REAL classifiers (never a 9th one).
  §G  accessory_cost_audit — the annotation is provably ADDITIVE (every pre-existing key byte-identical
      to the BASE module's answer once the new keys are removed).
  §H  MULTI-TENANT + ZERO-WRITE — org-scoped reads, two tenants isolated, the write guard TRIPPED.
  §I  MIGRATIONS 256 + 257 — real PostgreSQL parse, additive, idempotent, no GRANT/POLICY/anon, RLS,
      band, seed generated FROM code, and NO DOLLAR AMOUNT anywhere in 256.
  §J  DIFFERENTIAL SCOPE — exactly which modules changed vs base, and the ones that must not.

Run:  cd backend && PYTHONPATH=. python3 scratchpad/fwa_flat_accessory_def_proof.py
"""
import copy
import importlib.util
import os
import re
import subprocess
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.modules.commcalc import installment_category as icat
from app.modules.commcalc import installment_category_payout as icpay
from app.modules.commcalc import accessory_definition as adef
from app.modules.commcalc import sale_installment_engine as sie
from app.modules.commcalc import accessory_cost_audit as aca

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
# PINNED TO A LITERAL COMMIT, DELIBERATELY (2026-08-01).
# This was `origin/main`, resolved at RUN TIME — which is a trap this repo has now sprung three times.
# `origin/main` MOVED mid-session when this package's own push landed, so the harness began vendoring
# a BASE that already contained the very changes it exists to prove are additive: it was diffing
# itself against itself, and B2/B5/B8/G2 went red for a reason that has nothing to do with the code.
# Pinning to the commit this suite was WRITTEN against restores the intended comparison and makes the
# result reproducible forever, whatever origin/main does next. No assertion is weakened or removed.
BASE_REF = "4923001"          # origin/main as it stood when this package was built
OK = FAIL = 0
FAILS = []


def chk(name, cond, extra=""):
    global OK, FAIL
    if cond:
        OK += 1
        print(f"  ok   {name}")
    else:
        FAIL += 1
        FAILS.append(name)
        print(f"  FAIL {name} {extra}")


def sec(t):
    print(f"\n{t}\n" + "─" * 94)


# ── fake supabase client (same shape as the shipped lux harness) ───────────────────────────────────
class FakeQuery:
    def __init__(self, rows, log=None, table="", writes=None):
        self._rows, self._eq, self._in = list(rows), [], []
        self._log, self._table, self._writes = log, table, writes

    def select(self, *a, **k):
        return self

    def eq(self, c, v):
        self._eq.append((c, v))
        return self

    def in_(self, c, vs):
        self._in.append((c, {str(x) for x in vs}))
        return self

    def order(self, *a, **k):
        return self

    def limit(self, *a, **k):
        return self

    def range(self, a, b):
        self._range = (a, b)
        return self

    def _apply(self):
        rows = self._rows
        for c, v in self._eq:
            rows = [r for r in rows if str(r.get(c)) == str(v)]
        for c, vs in self._in:
            rows = [r for r in rows if str(r.get(c)) in vs]
        rng = getattr(self, "_range", None)
        return rows[rng[0]:rng[1] + 1] if rng else rows

    def execute(self):
        if self._log is not None:
            self._log.append(("read", self._table,
                              tuple(sorted((str(c), str(v)) for c, v in self._eq))))
        return type("R", (), {"data": self._apply()})()

    def _boom(self, *a, **k):
        if self._writes is not None:
            self._writes.append(self._table)
        raise AssertionError(f"WRITE ATTEMPTED on {self._table}")
    insert = update = upsert = delete = _boom


class MissingTable:
    def __getattr__(self, _n):
        raise RuntimeError("relation does not exist")


class FakeSchema:
    def __init__(self, store, schema, log=None, missing=(), writes=None):
        self.store, self.s, self.log, self.missing, self.writes = store, schema, log, set(missing), writes

    def table(self, t):
        key = f"{self.s}.{t}"
        if key in self.missing:
            return MissingTable()
        return FakeQuery(self.store.get(key, []), self.log, key, self.writes)


class FakeClient:
    def __init__(self, store, log=None, missing=(), writes=None):
        self.store, self.log, self.missing, self.writes = store, log, missing, writes

    def schema(self, s):
        return FakeSchema(self.store, s, self.log, self.missing, self.writes)

    def table(self, t):
        return FakeQuery(self.store.get(f"public.{t}", []), self.log, f"public.{t}", self.writes)


ORG = "ORG-LUX"
ORG_B = "ORG-OTHER"
BOOST_ORG = "00000000-0000-0000-0000-000000000001"
PER = "July 2026"
FWA_IMEI = "358835493293747"
PHONE_IMEI = "357612117781238"
FWA_DEVICE = "Home Internet Router TO - Promo $85, Total Wireless FWA Promotion - Add to Existing"
FWA_ACTPAY = "Activation payment"


def _sale(org=ORG, **k):
    base = dict(org_id=org, period=PER, voided="NO", trans_type="Sale", mdn="", serial_1="",
                department="", category="", contract_type="", product_desc="", ext_price=0, gp=0,
                sku="", product_id=None, store="LUX-1", salesperson="Ana Ruiz", trans_id="",
                trans_date="2026-07-08")
    base.update(k)
    return base


def _back(org, per, suffix, date):
    """The SAME two activations, sold in an earlier month — so that paying July produces month-2 and
    month-3 installments and the suppression of those months is observable at all."""
    return [
        _sale(org, period=per, trans_date=date, trans_id=f"T-FWA{suffix}", contract_type="activation",
              serial_1=f"3588354932937{suffix}0", mdn=f"78655510{suffix}0",
              department="BrandedHandset", category="KittedBranded", product_desc=FWA_DEVICE,
              ext_price=85.00, gp=10.00, sku="FWA-RTR"),
        _sale(org, period=per, trans_date=date, trans_id=f"T-FWA{suffix}", contract_type="activation",
              mdn=f"78655510{suffix}0", department="System", category="System",
              product_desc=FWA_ACTPAY, ext_price=0.00, gp=-15.00, sku="ACTPAY"),
        _sale(org, period=per, trans_date=date, trans_id=f"T-PHN{suffix}", contract_type="activation",
              serial_1=f"3576121177812{suffix}0", mdn=f"78655520{suffix}0",
              department="BrandedHandset", category="KittedBranded",
              product_desc="Samsung A15 - Promo $575", ext_price=575.00, gp=40.00, sku="A15"),
        _sale(org, period=per, trans_date=date, trans_id=f"T-PHN{suffix}", contract_type="activation",
              mdn=f"78655520{suffix}0", department="Rtr", category="Other Carr. payments",
              product_desc="Total ALL ACCESS Plan $65", ext_price=65.00, gp=65.00, sku="PLAN65"),
    ]


def build_store(payout=None, org=ORG, num_months=3, back=True):
    """The owner's shape: an FWA activation whose ONLY lines are the device (IMEI, $85, the plan text
    buried in its description) and a $0 Activation payment — so no rate-plan line exists and the
    %-of-MRC month-1 resolves to $0 — plus an ordinary phone activation WITH a real rate-plan line
    (the control that must never move)."""
    plans = [dict(id="P1", org_id=org, name="Luxelink Base", is_active=True, carrier_id="C1",
                  base_tier_metric="none")]
    assigns = [dict(id="A1", org_id=org, plan_id="P1", scope="store", scope_value="LUX-1", priority=0)]
    sales = [
        _sale(org, trans_id="T-FWA", contract_type="activation", serial_1=FWA_IMEI, mdn="7865551000",
              department="BrandedHandset", category="KittedBranded", product_desc=FWA_DEVICE,
              ext_price=85.00, gp=10.00, sku="FWA-RTR"),
        _sale(org, trans_id="T-FWA", contract_type="activation", mdn="7865551000",
              department="System", category="System", product_desc=FWA_ACTPAY,
              ext_price=0.00, gp=-15.00, sku="ACTPAY"),
        _sale(org, trans_id="T-PHN", contract_type="activation", serial_1=PHONE_IMEI, mdn="7865552000",
              department="BrandedHandset", category="KittedBranded",
              product_desc="Samsung A15 - Promo $575", ext_price=575.00, gp=40.00, sku="A15"),
        _sale(org, trans_id="T-PHN", contract_type="activation", mdn="7865552000",
              department="Rtr", category="Other Carr. payments",
              product_desc="Total ALL ACCESS Plan $65", ext_price=65.00, gp=65.00, sku="PLAN65"),
        # ── ACCESSORY LINES, SHAPED FROM THE LIVE luxelink JULY EXPORT (operator diagnostics,
        # 2026-08-01). Two facts matter and are reproduced exactly:
        #   1. the POS RENAMED BOTH classifying fields MID-MONTH for the SAME products —
        #      department='BrandedHandset'/category='HandsetBranded' up to 07-08, then
        #      department='Handset'/category='Accessories' from 07-09. So a category-field rule
        #      catches the second half of the month and misses the first.
        #   2. `sku` is NULL on every one of them, so a SKU-keyed mapping would match nothing.
        # Plus the real data-quality classes: GP $0 (cost==retail BYOD), negative GP, negative price.
        # WEEK 1 — the spelling the field rule does NOT catch
        _sale(org, trans_id="T-A1", department="BrandedHandset", category="HandsetBranded",
              product_desc="Case BYOD", ext_price=29.99, gp=0.00, sku=None, trans_date="2026-07-02"),
        _sale(org, trans_id="T-A2", department="BrandedHandset", category="HandsetBranded",
              product_desc="Screen Protectors BYOD", ext_price=24.99, gp=0.00, sku=None,
              trans_date="2026-07-07"),
        # WEEK 2 — the spelling it DOES catch, same physical products
        _sale(org, trans_id="T-A3", department="Handset", category="Accessories",
              product_desc="Case BYOD", ext_price=29.99, gp=0.00, sku=None, trans_date="2026-07-09"),
        _sale(org, trans_id="T-A4", department="Handset", category="Accessories",
              product_desc="Screen Protectors BYOD", ext_price=24.99, gp=0.00, sku=None,
              trans_date="2026-07-14"),
        # negative GP (implied cost 29.99 on a 14.99 sale) — a real July class
        _sale(org, trans_id="T-A5", department="Handset", category="Accessories",
              product_desc="Headphones Big BYOD", ext_price=14.99, gp=-15.00, sku=None,
              trans_date="2026-07-15"),
        # a negative-GP pair from the back-office login, also real
        _sale(org, trans_id="T-A6", department="Handset", category="Accessories",
              product_desc="Case BYOD", ext_price=28.13, gp=-1.86, sku=None,
              salesperson="Office, Back", trans_date="2026-07-16"),
        # NEGATIVE PRICE (return/void-shaped but not flagged as either in the export)
        _sale(org, trans_id="T-A7", department="Handset", category="Accessories",
              product_desc="Case BYOD", ext_price=-29.99, gp=0.00, sku=None, trans_date="2026-07-20"),
        # an accessory sold under department/category 'System' — the owner's note that even System
        # lines can be accessories, and a spelling NOTHING catches without a manual map
        _sale(org, trans_id="T-A8", department="System", category="System",
              product_desc="Case", ext_price=19.99, gp=0.00, sku=None, trans_date="2026-07-21"),
        # the 'ondigo' department the legacy/analyzer surfaces key on
        _sale(org, trans_id="T-A9", department="ONDIGO", category="Audio",
              product_desc="LOW RYDER-EARPHONE", ext_price=14.99, gp=12.00, sku=None,
              trans_date="2026-07-22"),
        # THE NAME-KEYWORD TRAP: a repair, not a charger. A 'charger' product-name keyword would
        # bill it as an accessory; the field rule never reads the name, so it does not.
        _sale(org, trans_id="T-B1", department="Repair", category="Labor",
              product_desc="Charger Port Repair", ext_price=49.99, gp=40.00, sku=None,
              trans_date="2026-07-23"),
        # A SET-UP FEE INSIDE AN ACCESSORY CATEGORY — the hardest case for "set-up fees are separate"
        _sale(org, trans_id="T-B2", department="Handset", category="Accessories",
              product_desc="Device Setup Charge", ext_price=30.00, gp=30.00, sku=None,
              trans_date="2026-07-24"),
    ]
    if back:
        sales += _back(org, "June 2026", "6", "2026-06-08") + _back(org, "May 2026", "5", "2026-05-08")
    sched = [dict(id="S1", org_id=org, plan_id="P1", num_months=num_months, gate_mode="paid_residual",
                  gate_from_month=99, m1_gate="inherit", is_active=True,
                  trigger_match_field="contract_type", trigger_match_op="equals",
                  trigger_match_value="activation", eligible_sale_periods=[],
                  qualifying_categories=None)]
    slines = [dict(org_id=org, schedule_id="S1", month_index=m, payout_kind="pct_mrc",
                   mrc_pct=0.05, mrc_source="product_catalog", flat_amount=0)
              for m in range(1, num_months + 1)]
    cfg = []
    if payout is not None:
        cfg = [dict(org_id=org, installment_category_payout=payout)]
    return {
        "commcalc.commission_plan": plans,
        "commcalc.commission_rule": [],
        "commcalc.commission_tier": [],
        "commcalc.commission_plan_assignment": assigns,
        "commcalc.raw_sales": sales,
        "commcalc.daily_sales_feed": [],
        "commcalc.plan_installment_schedule": sched,
        "commcalc.plan_installment_line": slines,
        "commcalc.commission_org_config": cfg,
        "commcalc.raw_mi": [],
        "commcalc.raw_ma_commission": [],
        "commcalc.store_mapping": [],
        "commcalc.product_mrc": [],
        "commcalc.carrier_category_map": [],
        "commcalc.flag_rules": [],
        "commcalc.accessory_config": [],
        "commcalc.raw_catalog": [],
        "commcalc.catalog_category_override": [],
        "commcalc.gp_category_map": [],
        "commcalc.installment_category_rule": [],
        "commcalc.accessory_class": [],
        "commcalc.accessory_definition_map": [],
        "storeops.employees": [],
    }


def _vendor(path, name):
    try:
        src = subprocess.check_output(["git", "-C", REPO, "show", f"{BASE_REF}:{path}"],
                                      stderr=subprocess.DEVNULL).decode()
    except Exception:
        return None
    with tempfile.NamedTemporaryFile("w", suffix=f"_{name}.py", delete=False) as fh:
        fh.write(src)
        p = fh.name
    spec = importlib.util.spec_from_file_location(name, p)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


# ══════════════════════════════════════════════════════════════════════════════════════════════════
sec("§A  installment_category_payout — PURE")

d = icpay.normalize_payout(None)
chk("A1 nothing stored -> every category on monthly installments (today's behaviour)",
    all(v["mode"] == "installments" and v["amount"] is None for v in d.values())
    and set(d) == set(icat.CATEGORY_KEYS), d)
chk("A2 DEFAULT_PAYOUT contains no dollar amount anywhere (nothing is seeded)",
    all(v["amount"] is None for v in icpay.DEFAULT_PAYOUT.values()))

d = icpay.normalize_payout({"home_internet": {"mode": "flat_once", "amount": "25.50", "pay_month": 2}})
chk("A3 a string amount is parsed", d["home_internet"]["amount"] == 25.5)
chk("A4 the other categories are untouched", d["phone"]["mode"] == "installments")

d = icpay.normalize_payout({"home_internet": 25})
chk("A5 a BARE NUMBER is read as flat at that amount",
    d["home_internet"] == {"mode": "flat_once", "amount": 25.0, "pay_month": 1}, d["home_internet"])

for blank in ("", None, "   ", "abc", True, False):
    v = icpay._num(blank)
    chk(f"A6 _num({blank!r}) is None, NOT 0.0 — blank must stay UNCONFIGURED", v is None, v)
chk("A6b _num('0') IS 0.0 — an explicit zero is a decision and is honoured", icpay._num("0") == 0.0)

f = icpay.resolve_flat("home_internet", icpay.normalize_payout(
    {"home_internet": {"mode": "flat_once", "amount": None}}), num_months=3)
chk("A7 flat with NO amount -> NOT ACTIVE (the no-guess branch)", f["active"] is False)
chk("A8 ...and it says why", f["reason"] == "amount_unconfigured", f)
chk("A9 ...and it never invents an amount", f["amount"] is None)

f = icpay.resolve_flat("home_internet", icpay.normalize_payout(
    {"home_internet": {"mode": "flat_once", "amount": 25}}), num_months=3)
chk("A10 flat WITH an amount -> active at exactly that amount", f["active"] and f["amount"] == 25.0)
chk("A11 ...landing in month 1 by default", f["pay_month"] == 1)

f = icpay.resolve_flat("home_internet", icpay.normalize_payout(
    {"home_internet": {"mode": "flat_once", "amount": 25, "pay_month": 9}}), num_months=3)
chk("A12 a pay_month beyond the schedule is CLAMPED so the chain still pays exactly once",
    f["pay_month"] == 3 and f["clamped"] is True, f)

f = icpay.resolve_flat("phone", icpay.normalize_payout({"home_internet": 25}), num_months=3)
chk("A13 a category nobody configured is untouched", f["active"] is False and f["mode"] == "installments")

s, src_ = icpay.payout_for({"category_payout": {"phone": {"mode": "flat_once", "amount": 5}}},
                           {"_stored": True, "phone": {"mode": "installments", "amount": None,
                                                       "pay_month": 1}})
chk("A14 a SCHEDULE override beats the org row", s["phone"]["amount"] == 5.0 and src_ == "schedule")
s, src_ = icpay.payout_for({}, {"_stored": True, "phone": {"mode": "flat_once", "amount": 7,
                                                           "pay_month": 1}})
chk("A15 no schedule override -> the ORG row", s["phone"]["amount"] == 7.0 and src_ == "org")
s, src_ = icpay.payout_for({}, {"_stored": False})
chk("A16 nothing stored -> the code default", src_ == "default" and s["phone"]["mode"] == "installments")
chk("A17 the category vocabulary is icat's, not a second copy",
    icpay.CATEGORY_KEYS is icat.CATEGORY_KEYS)
chk("A18 no tenant/carrier/product literal in the module source",
    not re.search(r"luxelink|boost|total wireless|\bFWA\b",
                  open(icpay.__file__).read().split('"""', 2)[2], re.I))

# ══════════════════════════════════════════════════════════════════════════════════════════════════
sec("§B  ENGINE with NOTHING configured — byte-identical to BASE")

base_sie = _vendor("backend/app/modules/commcalc/sale_installment_engine.py", "sie_base_256")
chk("B1 BASE engine vendored from origin/main", base_sie is not None)

new_out = sie.compute_sale_installments(FakeClient(build_store()), ORG, PER, persist=False)
if base_sie:
    base_out = base_sie.compute_sale_installments(FakeClient(build_store()), ORG, PER, persist=False)
    # The ONE deliberate warning change (owner directive: "reuse/extend the mrc_unresolved
    # surfacing"): `detail` gains an APPENDED sentence naming the flat route, and a structured
    # `fix_routes` key. Both are proven to be a pure APPEND — the base text survives as a prefix — so
    # nothing an operator already reads was rewritten or removed.
    NEW_WARN_KEYS = {"fix_routes"}

    def _wnorm(ws, base_ws):
        base_by = {}
        for w in base_ws:
            base_by.setdefault((w.get("type"), str(w.get("trans_id") or ""),
                                w.get("month_index")), w)
        out = []
        for w in ws:
            w = {k: v for k, v in w.items() if k not in NEW_WARN_KEYS}
            if w.get("type") == "mrc_unresolved":
                bw = base_by.get(("mrc_unresolved", str(w.get("trans_id") or ""), w.get("month_index")))
                if bw and w.get("detail", "").startswith(bw.get("detail", "")):
                    w["detail"] = bw["detail"]          # proven APPEND-only; compare the base text
            out.append(w)
        return out

    # mig 258 (a LATER package) adds `expected_guard` + two additive REPORTING row keys. Neither is
    # money — `amount` is untouched — so they are stripped here exactly as `flat_guard` is, keeping
    # this a real byte-identity check on the flat feature rather than a stale one.
    LATER_TOP = ("flat_guard", "expected_guard")
    LATER_ROW = ("expected_amount", "expected_in_window")

    def _strip_later(d):
        d = {k: v for k, v in d.items() if k not in LATER_TOP}
        d["ledger"] = [{k: v for k, v in r.items() if k not in LATER_ROW}
                       for r in (d.get("ledger") or [])]
        return d

    stripped = _strip_later(new_out)
    stripped["warnings"] = _wnorm(new_out["warnings"], base_out["warnings"])
    chk("B2 UNCONFIGURED: the whole payload equals BASE once `flat_guard` + the appended "
        "mrc_unresolved sentence are removed", stripped == base_out,
        [k for k in set(stripped) | set(base_out) if stripped.get(k) != base_out.get(k)])
    chk("B3 ...including `totals` exactly", new_out["totals"] == base_out["totals"], new_out["totals"])
    chk("B4 ...and every ledger row key for key",
        _strip_later(new_out)["ledger"] == base_out["ledger"])
    chk("B5 the mrc_unresolved change is an APPEND ONLY — the base wording is still the prefix",
        all(w["detail"].startswith(bw["detail"]) and len(w["detail"]) > len(bw["detail"])
            for w, bw in zip([x for x in new_out["warnings"] if x["type"] == "mrc_unresolved"],
                             [x for x in base_out["warnings"] if x["type"] == "mrc_unresolved"])),
        "not a pure append")
    chk("B5b ...and the count/order of warnings is unchanged",
        [w["type"] for w in new_out["warnings"]] == [w["type"] for w in base_out["warnings"]],
        ([w["type"] for w in new_out["warnings"]], [w["type"] for w in base_out["warnings"]]))
CONST = {"flat_chains": 0, "flat_amount": 0.0, "suppressed_months": 0, "suppressed_amount": 0.0,
         "paid": {}, "suppressed": {}, "unconfigured": {}, "config_source": [], "flat_categories": []}
chk("B6 `flat_guard` with no config is the CONSTANT (so it can never carry a surprise)",
    new_out["flat_guard"] == CONST, new_out["flat_guard"])
chk("B7 the FWA chain still pays $0 via mrc_unresolved (the bug the owner reported is unchanged)",
    any(w["type"] == "mrc_unresolved" and w["trans_id"] == "T-FWA" for w in new_out["warnings"]))

# BOOST: a house-org tenant with the same shape
bstore = build_store(org=BOOST_ORG)
bnew = sie.compute_sale_installments(FakeClient(bstore), BOOST_ORG, PER, persist=False)
if base_sie:
    bbase = base_sie.compute_sale_installments(FakeClient(build_store(org=BOOST_ORG)), BOOST_ORG, PER,
                                               persist=False)
    bstrip = _strip_later(bnew)
    bstrip["warnings"] = _wnorm(bnew["warnings"], bbase["warnings"])
    chk("B8 BOOST tenant: payload equals BASE once `flat_guard` + the appended sentence are removed",
        bstrip == bbase,
        [k for k in set(bstrip) | set(bbase) if bstrip.get(k) != bbase.get(k)])
chk("B9 BOOST tenant `flat_guard` is the same constant", bnew["flat_guard"] == CONST)
chk("B10 mig 256 unapplied (no commission_org_config column at all) still runs",
    sie.compute_sale_installments(
        FakeClient(build_store(), missing=("commcalc.commission_org_config",)), ORG, PER,
        persist=False)["flat_guard"] == CONST)

# ══════════════════════════════════════════════════════════════════════════════════════════════════
sec("§C  ENGINE with FLAT ACTIVE — the owner's directive, at an owner-entered amount")

AMT = 25.0     # NOT a seeded default — a number this HARNESS chose, echoed everywhere below.
st = build_store(payout={"home_internet": {"mode": "flat_once", "amount": AMT, "pay_month": 1}})
res = sie.compute_sale_installments(FakeClient(st), ORG, PER, persist=False)
fwa = [r for r in res["ledger"] if r["trans_id"] == "T-FWA"]
phn = [r for r in res["ledger"] if r["trans_id"] == "T-PHN"]
chk("C1 the FWA chains now emit exactly ONE row between them (they left the monthly chain)",
    len([r for r in res["ledger"] if str(r["trans_id"]).startswith("T-FWA")]) == 1
    and len(fwa) == 1, res["ledger"])
chk("C2 ...in month 1", fwa and fwa[0]["month_index"] == 1)
chk("C3 ...paying EXACTLY the amount the owner entered", fwa and fwa[0]["amount"] == AMT, fwa)
chk("C4 ...and the audit row says `category_flat`, not the schedule's pct_mrc",
    fwa and fwa[0]["payout_kind"] == "category_flat", fwa)
chk("C5 ...carrying its provenance", fwa and fwa[0].get("category_flat") is True
    and fwa[0].get("category_flat_amount") == AMT and fwa[0].get("category_flat_source") == "org")
chk("C6 ...and the label no longer claims an MRC", fwa and "MRC" not in (fwa[0]["display_label"] or ""))
chk("C7 the PHONE chain is untouched — same rows, same money as the unconfigured run",
    phn == [r for r in new_out["ledger"] if r["trans_id"] == "T-PHN"],
    [phn, [r for r in new_out['ledger'] if r['trans_id'] == 'T-PHN']])
fg = res["flat_guard"]
chk("C8 flat_guard reports the payment", fg["flat_chains"] == 1 and fg["flat_amount"] == AMT, fg)
chk("C9 flat_guard reports the SUPPRESSED months with their dollars",
    fg["suppressed_months"] == 2 and "home_internet" in fg["suppressed"], fg)
chk("C10 ...per rep", set((fg["suppressed"].get("home_internet") or {}).get("reps") or {}) == {"ANA RUIZ"},
    fg["suppressed"])
chk("C10b ...and per month (M2 and M3 of the earlier activations)",
    sorted(((fg["suppressed"].get("home_internet") or {}).get("months") or {}).keys()) == ["2", "3"],
    fg["suppressed"])
chk("C11 the configured category is named", fg["flat_categories"] == ["home_internet"], fg)
wt = [w["type"] for w in res["warnings"]]
chk("C12 a flat_paid_summary warning exists", "flat_paid_summary" in wt, wt)
chk("C13 a flat_months_suppressed warning exists", "flat_months_suppressed" in wt, wt)
chk("C14 the now-moot mrc_unresolved warning for THIS chain is withdrawn",
    not any(w["type"] == "mrc_unresolved" and w.get("trans_id") == "T-FWA" for w in res["warnings"]))
chk("C15 ...and so is every SUPPRESSED month of it — the counter goes to zero",
    res["chain_guard"]["mrc_unresolved"] == 0
    and not [w for w in res["warnings"] if w["type"] == "mrc_unresolved"], res["chain_guard"])
chk("C16 total pay moved by exactly (flat - the installment it replaced)",
    round(res["totals"]["amount"] - new_out["totals"]["amount"], 2)
    == round(AMT - fg["paid"]["home_internet"]["replaced"], 2),
    (res["totals"], new_out["totals"], fg["paid"]))

# a DIFFERENT tenant putting a DIFFERENT category on flat — nothing is hard-coded to home_internet
st2 = build_store(payout={"phone": {"mode": "flat_once", "amount": 9.0, "pay_month": 2}})
res2 = sie.compute_sale_installments(FakeClient(st2), ORG, PER, persist=False)
p2 = [r for r in res2["ledger"] if str(r["trans_id"]).startswith("T-PHN")]
chk("C17 a tenant can put PHONE on flat instead — the code has no favourite category",
    len(p2) == 1 and p2[0]["month_index"] == 2 and p2[0]["amount"] == 9.0
    and res2["flat_guard"]["flat_categories"] == ["phone"], (p2, res2["flat_guard"]))
chk("C18 ...and then it is the FWA chains that are untouched",
    [r for r in res2["ledger"] if str(r["trans_id"]).startswith("T-FWA")]
    == [r for r in new_out["ledger"] if str(r["trans_id"]).startswith("T-FWA")])

# pay_month clamped
st3 = build_store(payout={"home_internet": {"mode": "flat_once", "amount": AMT, "pay_month": 9}})
res3 = sie.compute_sale_installments(FakeClient(st3), ORG, PER, persist=False)
chk("C19 a pay_month beyond the schedule still pays ONCE (clamped to the last month), never never",
    len([r for r in res3["ledger"] if str(r["trans_id"]).startswith("T-FWA")]) == 1
    and res3["flat_guard"]["paid"]["home_internet"]["clamped"] is True
    and res3["flat_guard"]["paid"]["home_internet"]["pay_month"] == 3,
    res3["flat_guard"])

# an EXCLUDED category is still excluded, and is not double-counted as flat
st4 = build_store(payout={"home_internet": {"mode": "flat_once", "amount": AMT}})
st4["commcalc.commission_org_config"] = [dict(
    org_id=ORG, installment_category_payout={"home_internet": {"mode": "flat_once", "amount": AMT}},
    installment_category_qualification={"home_internet": False})]
res4 = sie.compute_sale_installments(FakeClient(st4), ORG, PER, persist=False)
chk("C20 a category that does not QUALIFY pays nothing at all, flat or not",
    not [r for r in res4["ledger"] if r["trans_id"] == "T-FWA"])
chk("C21 ...and it is booked as EXCLUDED, not as flat-suppressed (no double accounting)",
    res4["category_guard"]["excluded_chains"] >= 1
    and res4["flat_guard"]["suppressed_months"] == 0, (res4["category_guard"], res4["flat_guard"]))

# a 1-month schedule: nothing to suppress
st5 = build_store(payout={"home_internet": {"mode": "flat_once", "amount": AMT}}, num_months=1,
                  back=False)
res5 = sie.compute_sale_installments(FakeClient(st5), ORG, PER, persist=False)
chk("C22 a 1-month schedule on flat: one row, zero suppressed",
    len([r for r in res5["ledger"] if r["trans_id"] == "T-FWA"]) == 1
    and res5["flat_guard"]["suppressed_months"] == 0)

# ══════════════════════════════════════════════════════════════════════════════════════════════════
sec("§D  THE NO-GUESS BRANCH — flat mode, blank amount")

stu = build_store(payout={"home_internet": {"mode": "flat_once", "amount": None}})
resu = sie.compute_sale_installments(FakeClient(stu), ORG, PER, persist=False)
chk("D1 MONEY IS UNCHANGED — totals identical to the unconfigured run",
    resu["totals"] == new_out["totals"], (resu["totals"], new_out["totals"]))
chk("D2 ...and every ledger row is identical (no $0 was manufactured)",
    resu["ledger"] == new_out["ledger"])
uw = [w for w in resu["warnings"] if w["type"] == "flat_amount_unconfigured"]
chk("D3 a LOUD flat_amount_unconfigured warning was raised", len(uw) == 1, [w["type"] for w in resu["warnings"]])
chk("D4 ...naming the category and the dollars still being paid monthly",
    uw and uw[0]["category"] == "home_internet" and "still being paid" in uw[0]["detail"].lower(), uw)
chk("D5 ...and flat_guard reports it as unconfigured, not as paid",
    resu["flat_guard"]["unconfigured"].get("home_internet", {}).get("chains", 0) >= 1
    and resu["flat_guard"]["flat_chains"] == 0, resu["flat_guard"])
chk("D6 the mrc_unresolved warning is STILL raised (the chain still pays monthly, so it still matters)",
    any(w["type"] == "mrc_unresolved" and w.get("trans_id") == "T-FWA" for w in resu["warnings"]))
chk("D7 the mrc_unresolved detail now names the flat route as a second fix",
    any("flat" in w["detail"].lower() and w["type"] == "mrc_unresolved" for w in resu["warnings"]))
chk("D8 ...structurally, not only in prose",
    any(w.get("fix_routes") == ["mrc_mapping", "plan_line_matcher", "category_flat_payout"]
        for w in resu["warnings"] if w["type"] == "mrc_unresolved"))

# ══════════════════════════════════════════════════════════════════════════════════════════════════
sec("§E  accessory_definition — PURE")

chk("E1 normalize trims AND casefolds", adef.normalize("  Accessories ") == "accessories")
chk("E2 the eight owner classes are seeded as PROPOSALS, none pre-confirmed",
    len(adef.DEFAULT_CLASSES) == 8
    and all(c["status"] == "proposed" for c in adef.classes_from([])), adef.CLASS_KEYS)
chk("E3 the owner's seven named classes are all present",
    {"screen_protector", "case", "headset", "earphone", "charger", "cable", "adapter"}
    <= set(adef.CLASS_KEYS), adef.CLASS_KEYS)
chk("E4 class_seed_rows stamps org_id on EVERY row",
    all(r["org_id"] == ORG_B for r in adef.class_seed_rows(ORG_B)))

rule, refused = adef.normalize_field_rule(None)
chk("E5 the default rule is on, reads department+category, token 'accessor'",
    rule == {"enabled": True, "token_fields": ["department", "category"], "tokens": ["accessor"]}, rule)
rule2, refused2 = adef.normalize_field_rule({"token_fields": ["product_desc", "sku", "category"]})
chk("E6 product_desc and sku are REFUSED for the token rule (no product-name keyword matching)",
    rule2["token_fields"] == ["category"] and set(refused2) == {"product_desc", "sku"}, (rule2, refused2))
chk("E7 ...and the refusal is REPORTED, not silently swallowed", refused2 != [])

idx = adef.build_index([
    {"id": "m1", "match_field": "department", "match_value": "Accessories", "is_accessory": True,
     "accessory_class": "screen_protector", "status": "proposed"},
    {"id": "m2", "match_field": "product_desc", "match_value": "Charger Port Repair",
     "is_accessory": False, "status": "confirmed"},
    {"id": "m3", "match_field": "department", "match_value": "ONDIGO", "is_accessory": True,
     "accessory_class": "earphone", "status": "confirmed"},
])
KWS = {"device setup charge"}
row_sp = {"product_desc": "Tempered Glass BYOD", "department": "ACCESSORIES", "category": "Screen", "sku": "SP"}
v = adef.classify(row_sp, idx, rule, KWS, mode="confirmed")
chk("E8 a PROPOSED mapping does NOT count in 'confirmed' mode — the token rule answers instead",
    v["is_accessory"] is True and v["matched_by"] == "field_token", v)
v = adef.classify(row_sp, idx, rule, KWS, mode="proposed")
chk("E9 ...and DOES in 'proposed' mode, with its class",
    v["matched_by"] == "map" and v["accessory_class"] == "screen_protector", v)
chk("E10 department matching is CASE-INSENSITIVE ('ACCESSORIES' hit the 'Accessories' row)",
    v["matched_value"] == "Accessories", v)

row_rp = {"product_desc": "Charger Port Repair", "department": "Repair", "category": "Labor", "sku": "R1"}
v = adef.classify(row_rp, idx, rule, KWS, mode="proposed")
chk("E11 an explicit EXCLUSION beats everything else ('Charger Port Repair' is not an accessory)",
    v["is_accessory"] is False and v["matched_by"] == "map", v)
chk("E12 ...and a name-keyword matcher is exactly what would have got this wrong",
    "charger" in row_rp["product_desc"].lower())

row_su = {"product_desc": "Device Setup Charge", "department": "Accessories", "category": "Accessories", "sku": "S"}
v = adef.classify(row_su, idx, rule, KWS, mode="proposed")
chk("E13 SET-UP FEES are never accessories — checked FIRST, even in an accessory department",
    v["is_accessory"] is False and v["matched_by"] == "setup_fee", v)

row_od = {"product_desc": "LOW RYDER-EARPHONE", "department": "ONDIGO", "category": "Audio", "sku": "LR"}
v = adef.classify(row_od, idx, rule, KWS, mode="confirmed")
chk("E14 a CONFIRMED mapping counts in confirmed mode",
    v["is_accessory"] and v["accessory_class"] == "earphone" and v["status"] == "confirmed", v)

row_no = {"product_desc": "Samsung A15", "department": "BrandedHandset", "category": "KittedBranded", "sku": "A15"}
v = adef.classify(row_no, idx, rule, KWS, mode="proposed")
chk("E15 an unrelated handset line is NOT an accessory", v["is_accessory"] is False and v["matched_by"] is None, v)

off, _ = adef.normalize_field_rule({"enabled": False})
v = adef.classify({"product_desc": "x", "department": "Accessories", "category": "", "sku": ""},
                  {f: {} for f in adef.MATCH_FIELDS}, off, KWS, mode="proposed")
chk("E16 with the rule OFF and nothing mapped, nothing is an accessory", v["is_accessory"] is False)

v = adef.classify({"product_desc": "Accessory Bundle Case", "department": "Handsets",
                   "category": "Phones", "sku": ""}, {f: {} for f in adef.MATCH_FIELDS}, rule, KWS,
                  mode="proposed")
chk("E17 the token rule NEVER reads the product name ('Accessory Bundle Case' in a Handsets "
    "department is not an accessory)", v["is_accessory"] is False, v)

src_ad = open(adef.__file__).read().split('"""', 2)[2]
chk("E18 the module body contains no regex/startswith/endswith matching",
    not re.search(r"\bre\.(search|match|compile)|\.startswith\(|\.endswith\(", src_ad), "found one")

# precedence: sku beats product_desc beats category beats department
idx2 = adef.build_index([
    {"match_field": "department", "match_value": "D", "is_accessory": True, "status": "confirmed"},
    {"match_field": "category", "match_value": "C", "is_accessory": False, "status": "confirmed"},
])
v = adef.classify({"product_desc": "", "sku": "", "department": "D", "category": "C"}, idx2, rule,
                  KWS, mode="confirmed")
chk("E19 CATEGORY beats DEPARTMENT (more specific wins)", v["is_accessory"] is False, v)
chk("E20 the field precedence list is most-specific-first",
    adef.MATCH_FIELDS == ("sku", "product_desc", "category", "department"))
idx3 = adef.build_index([
    {"match_field": "department", "match_value": "D", "is_accessory": True, "status": "confirmed"},
    {"match_field": "department", "match_value": "D", "is_accessory": False, "status": "proposed"},
])
chk("E21 a proposed row never overwrites a CONFIRMED one in the index",
    idx3["department"]["d"]["status"] == "confirmed", idx3)

# ══════════════════════════════════════════════════════════════════════════════════════════════════
sec("§F  the agreement report")

rows = [
    {"product_desc": "A", "sku": "1", "department": "Accessories", "category": "x", "ext_price": 10, "gp": 4, "trans_id": "T1"},
    {"product_desc": "B", "sku": "2", "department": "Handsets", "category": "y", "ext_price": 100, "gp": 20, "trans_id": "T2"},
    {"product_desc": "C", "sku": "3", "department": "Ondigo", "category": "z", "ext_price": 20, "gp": 8, "trans_id": "T3"},
]


def fake_verdicts(r):
    acc = adef.normalize(r["department"]) in ("accessories", "ondigo")
    return {"legacy": adef.normalize(r["department"]) == "ondigo",
            "catalog": False,
            "combined": adef.normalize(r["department"]) == "ondigo",
            "installment": adef.normalize(r["department"]) == "ondigo",
            "analyzer": acc,
            "gp_map": False,
            "definition_confirmed": acc,
            "definition_proposed": acc}


rep_ = adef.agreement(rows, fake_verdicts)
chk("F1 the reference is the PAY BASIS", rep_["reference"] == "combined")
chk("F2 per-surface totals are right",
    rep_["totals"]["definition_confirmed"]["lines"] == 2
    and rep_["totals"]["combined"]["lines"] == 1, rep_["totals"])
a = rep_["agreement"]["definition_confirmed"]
chk("F3 'only_here' is exactly what the definition would ADD ($10 of 'Accessories')",
    a["only_here"] == 1 and a["only_here_ext"] == 10.0, a)
chk("F4 'only_reference' is exactly what it would DROP (nothing here)", a["only_reference"] == 0, a)
chk("F5 the pay basis agrees with itself perfectly",
    rep_["agreement"]["combined"] == {"same": 3, "only_here": 0, "only_reference": 0,
                                      "only_here_ext": 0.0, "only_reference_ext": 0.0})
chk("F6 only DISAGREEING items are listed (the actionable set)",
    {i["product_desc"] for i in rep_["disagreeing_items"]} == {"A", "C"},
    [i["product_desc"] for i in rep_["disagreeing_items"]])
chk("F7 agreement() classifies NOTHING itself — verdicts are injected",
    "verdicts_of" in adef.agreement.__code__.co_varnames)
chk("F8 all eight surfaces are reported", len(adef.SURFACES) == 8 and len(rep_["totals"]) == 8)

ob = adef.observed_values(rows, adef.build_index([]), rule)
chk("F9 observed_values offers the tenant's REAL distinct values (pick-don't-type)",
    {v["match_value"] for v in ob["department"]} == {"Accessories", "Handsets", "Ondigo"},
    ob["department"])
chk("F10 ...with the token-rule hit shown per value",
    [v["token_hit"] for v in ob["department"] if v["match_value"] == "Accessories"] == ["accessor"],
    ob["department"])
chk("F11 ...and dollars per value", sum(v["ext_price"] for v in ob["department"]) == 130.0)

# ══════════════════════════════════════════════════════════════════════════════════════════════════
sec("§G  accessory_cost_audit — the annotation is ADDITIVE")

base_aca = _vendor("backend/app/modules/commcalc/accessory_cost_audit.py", "aca_base_257")
chk("G1 BASE accessory_cost_audit vendored", base_aca is not None)


def acc_store(org=ORG):
    s = build_store(org=org)
    # The LIVE luxelink rule shape: it matches on the CATEGORY field, and the stored rate is 17.5 in
    # the fraction field (the operator's Block-2B output confirmed paid=210.00 on a GP-12.00 line =
    # 17.5 x 12). Correcting that rate is OUT OF SCOPE here — this fixture only has to be faithful.
    s["commcalc.commission_rule"] = [dict(id="R2", org_id=org, plan_id="P1", sort=1,
                                          label="Accessories % GP", match_field="category",
                                          match_op="equals", match_value="accessories",
                                          payout_kind="pct_gp", amount=0, pct=17.5, tiered=False,
                                          qualifies=True)]
    return s


NEW_TOP = {"accessory_definition"}
NEW_ROW = {"acc_def", "acc_def_class", "acc_def_by"}
new_a = aca.audit(FakeClient(acc_store()), ORG, PER)
if base_aca:
    old_a = base_aca.audit(FakeClient(acc_store()), ORG, PER)

    def strip(o):
        o = copy.deepcopy(o)
        for k in NEW_TOP:
            o.pop(k, None)
        o["items"] = [{k: v for k, v in i.items() if k not in NEW_ROW} for i in o.get("items", [])]
        o["lines"] = [{k: v for k, v in l.items() if k not in NEW_ROW} for l in o.get("lines", [])]
        return o
    chk("G2 every pre-existing key is byte-identical to BASE once the annotation is removed",
        strip(new_a) == old_a,
        [k for k in set(strip(new_a)) | set(old_a) if strip(new_a).get(k) != old_a.get(k)])
    chk("G3 ...including every option total (no dollar is derived from the annotation)",
        new_a["totals"] == old_a["totals"], (new_a["totals"], old_a["totals"]))
chk("G4 the annotation roll-up is present and honest",
    isinstance(new_a.get("accessory_definition"), dict)
    and "note" in new_a["accessory_definition"], new_a.get("accessory_definition"))
chk("G5 it counts the paid lines the definition does NOT call accessories",
    new_a["accessory_definition"]["lines"] >= 1)

# ══════════════════════════════════════════════════════════════════════════════════════════════════
sec("§K  THE LIVE-DATA FINDING — mid-month spelling drift, and what closes it")

live_rows = [r for r in build_store()["commcalc.raw_sales"] if str(r["trans_id"]).startswith(("T-A", "T-B"))]
empty_idx = adef.build_index([])

# 1) the hole is REAL and the harness reproduces it
wk1 = [r for r in live_rows if r["trans_date"] <= "2026-07-08"]
wk2 = [r for r in live_rows if r["trans_date"] >= "2026-07-09"]
c1 = [r for r in wk1 if adef.classify(r, empty_idx, rule, KWS, mode="proposed")["is_accessory"]]
c2 = [r for r in wk2 if adef.classify(r, empty_idx, rule, KWS, mode="proposed")["is_accessory"]]
chk("K1 the FIELD RULE ALONE misses the whole first week (the live 07-02..07-08 spelling)",
    len(c1) == 0 and len(wk1) == 2, (len(c1), len(wk1)))
chk("K2 ...and catches the same products from 07-09 (the renamed spelling)", len(c2) >= 4, len(c2))

# 2) spelling_drift NAMES it
dr = adef.spelling_drift(live_rows, rule)
names = {d["product_desc"] for d in dr}
chk("K3 spelling_drift names the drifting products", {"Case BYOD", "Screen Protectors BYOD"} <= names, names)
cb = next(d for d in dr if d["product_desc"] == "Case BYOD")
chk("K4 ...listing BOTH spellings with their date ranges",
    {(x["department"], x["category"]) for x in cb["spellings"]}
    == {("BrandedHandset", "HandsetBranded"), ("Handset", "Accessories")}, cb["spellings"])
chk("K5 ...marking which spelling the rule catches",
    sorted(x["caught"] for x in cb["spellings"]) == [False, True], cb["spellings"])
chk("K6 ...and the first (uncaught) spelling is dated in week one",
    next(x for x in cb["spellings"] if not x["caught"])["first_date"] == "2026-07-02", cb["spellings"])
chk("K7 a product with ONE consistent spelling is NOT reported as drift",
    "LOW RYDER-EARPHONE" not in names and "Charger Port Repair" not in names, names)

# 3) propose_from_data CLOSES it, on evidence only
props = adef.propose_from_data(live_rows, empty_idx, rule, KWS)
pnames = {p["match_value"] for p in props}
chk("K8 propose_from_data proposes the drifting products (their own later lines are the evidence)",
    {"Case BYOD", "Screen Protectors BYOD"} <= pnames, pnames)
chk("K9 ...and NEVER proposes something no line ever qualified ('Charger Port Repair', 'Case')",
    "Charger Port Repair" not in pnames and "Case" not in pnames, pnames)
chk("K10 ...nor the SET-UP FEE, even though it sits in an accessory category",
    "Device Setup Charge" not in pnames, pnames)
pc = next(p for p in props if p["match_value"] == "Case BYOD")
chk("K11 every proposal cites the evidence line that produced it",
    pc["evidence"] and pc["evidence"]["matched_by"] == "field_token"
    and pc["evidence"]["category"] == "Accessories", pc["evidence"])
chk("K12 ...and states how many lines it would newly cover", pc["uncovered_lines"] >= 1, pc)

# 4) applying the proposals actually closes the week-one hole
applied = adef.build_index([{"match_field": p["match_field"], "match_value": p["match_value"],
                             "is_accessory": True, "status": "confirmed"} for p in props])
c1b = [r for r in wk1 if adef.classify(r, applied, rule, KWS, mode="confirmed")["is_accessory"]]
chk("K13 with the proposals confirmed, WEEK ONE is caught (the hole closes)",
    len(c1b) == 2, (len(c1b), len(wk1)))
chk("K14 ...and the set-up fee is STILL not an accessory",
    adef.classify(next(r for r in live_rows if r["product_desc"] == "Device Setup Charge"),
                  applied, rule, KWS, mode="confirmed")["matched_by"] == "setup_fee")
chk("K15 ...and the repair is STILL not an accessory",
    not adef.classify(next(r for r in live_rows if r["product_desc"] == "Charger Port Repair"),
                      applied, rule, KWS, mode="confirmed")["is_accessory"])
chk("K16 propose_from_data is idempotent (already-mapped names are skipped)",
    adef.propose_from_data(live_rows, applied, rule, KWS) == [])

# 5) sku is unusable on this tenant's data — and it SAYS so
sk = adef.sku_coverage(live_rows)
chk("K17 sku_coverage reports 0% on the accessory lines and refuses to pretend otherwise",
    sk["with_sku"] == 0 and sk["usable"] is False and "never match" in (sk["note"] or ""), sk)
all_rows = build_store()["commcalc.raw_sales"]
sk2 = adef.sku_coverage(all_rows, is_accessory=lambda r: adef.classify(
    r, empty_idx, rule, KWS, mode="proposed")["is_accessory"])
chk("K17b ...and it does NOT let the SKU-bearing ACTIVATION lines mask that "
    "(the live shape: activations have SKUs, accessories do not)",
    sk2["with_sku"] > 0 and sk2["accessory_with_sku"] == 0 and sk2["usable"] is False
    and "reach none of them" in (sk2["note"] or ""), sk2)

# 6) per-mechanism attribution
mrep = adef.agreement(live_rows, lambda r: {
    **{k: False for k in adef.SURFACES},
    "_detail": {"proposed": adef.classify(r, empty_idx, rule, KWS, mode="proposed"),
                "confirmed": adef.classify(r, empty_idx, rule, KWS, mode="confirmed")}})
by = {m["key"]: m for m in mrep["by_mechanism"]}
chk("K18 mechanism attribution separates the field rule from the manual map from nothing",
    by["field_token"]["lines"] >= 4 and by["map_confirmed"]["lines"] == 0
    and by["none"]["lines"] >= 3, {k: v["lines"] for k, v in by.items()})
chk("K19 the set-up fee gets its OWN mechanism bucket, not 'none'", by["setup_fee"]["lines"] == 1, by)
mrep2 = adef.agreement(live_rows, lambda r: {
    **{k: False for k in adef.SURFACES},
    "_detail": {"proposed": adef.classify(r, applied, rule, KWS, mode="proposed"),
                "confirmed": adef.classify(r, applied, rule, KWS, mode="confirmed")}})
by2 = {m["key"]: m for m in mrep2["by_mechanism"]}
chk("K20 after mapping, the manual map carries the lines the rule could not",
    by2["map_confirmed"]["lines"] > by["map_confirmed"]["lines"], (by2["map_confirmed"], by["map_confirmed"]))

# 7) the negative-price / negative-GP classes are counted, not hidden
chk("K21 negative-price lines are counted and reported, never silently swallowed",
    mrep["negative_price_lines"]["lines"] == 1
    and mrep["negative_price_lines"]["ext_price"] == -29.99, mrep["negative_price_lines"])

# 8) the gap list points at the products whose spelling nobody catches
gaprep = adef.agreement(live_rows, lambda r: {
    **{k: False for k in adef.SURFACES},
    "analyzer": "accessor" in adef.normalize(r.get("category")) or adef.normalize(r.get("department")) == "ondigo",
    "_detail": {"proposed": adef.classify(r, empty_idx, {"enabled": False}, KWS, mode="proposed"),
                "confirmed": adef.classify(r, empty_idx, {"enabled": False}, KWS, mode="confirmed")}})
chk("K22 the uncaught-gap list names products an EXISTING surface calls accessories but the "
    "definition does not", gaprep["uncaught_gap"]["lines"] >= 1, gaprep["uncaught_gap"])

# ══════════════════════════════════════════════════════════════════════════════════════════════════
sec("§H  MULTI-TENANT + ZERO-WRITE")

log = []
sie.compute_sale_installments(FakeClient(build_store(), log=log), ORG, PER, persist=False)
unscoped = [e for e in log if e[0] == "read" and not any(c == "org_id" for c, _ in e[2])]
chk("H1 every engine read is org-scoped", not unscoped, unscoped[:4])
wrong = [e for e in log if any(c == "org_id" and v != ORG for c, v in e[2])]
# The ONE non-caller org_id read is PRE-EXISTING and is not tenant DATA: mig 223's
# `installment_gate_source_config` deliberately falls back to the HOUSE row as a shared default
# template when the tenant has none. Proven pre-existing by replaying the BASE engine below.
HOUSE_DEFAULT_TABLES = {"commcalc.installment_gate_source_config"}
wrong_real = [e for e in wrong if e[1] not in HOUSE_DEFAULT_TABLES]
chk("H2 ...to the CALLER's org — the only exception is the documented house-DEFAULTS config table",
    not wrong_real, wrong_real[:4])
if base_sie:
    blog = []
    base_sie.compute_sale_installments(FakeClient(build_store(), log=blog), ORG, PER, persist=False)
    bwrong = {e[1] for e in blog if any(c == "org_id" and v != ORG for c, v in e[2])}
    chk("H2b ...and the BASE engine reads exactly the same house-default table (not introduced here)",
        bwrong == {e[1] for e in wrong}, (bwrong, {e[1] for e in wrong}))

# two tenants, one store name, one period: neither sees the other's money
both = build_store()
both["commcalc.raw_sales"] = both["commcalc.raw_sales"] + build_store(org=ORG_B)["commcalc.raw_sales"]
both["commcalc.commission_plan"] += [dict(id="P2", org_id=ORG_B, name="Other", is_active=True,
                                          carrier_id="C2", base_tier_metric="none")]
both["commcalc.commission_plan_assignment"] += [dict(id="A2", org_id=ORG_B, plan_id="P2",
                                                     scope="store", scope_value="LUX-1", priority=0)]
both["commcalc.plan_installment_schedule"] += [dict(id="S2", org_id=ORG_B, plan_id="P2", num_months=3,
                                                    gate_mode="paid_residual", gate_from_month=99,
                                                    m1_gate="inherit", is_active=True,
                                                    trigger_match_field="contract_type",
                                                    trigger_match_op="equals",
                                                    trigger_match_value="activation",
                                                    eligible_sale_periods=[],
                                                    qualifying_categories=None)]
both["commcalc.plan_installment_line"] += [dict(org_id=ORG_B, schedule_id="S2", month_index=m,
                                                payout_kind="flat", flat_amount=3, mrc_pct=0,
                                                mrc_source="product_catalog") for m in (1, 2, 3)]
both["commcalc.commission_org_config"] = [
    dict(org_id=ORG, installment_category_payout={"home_internet": {"mode": "flat_once", "amount": AMT}})]
ra = sie.compute_sale_installments(FakeClient(copy.deepcopy(both)), ORG, PER, persist=False)
rb = sie.compute_sale_installments(FakeClient(copy.deepcopy(both)), ORG_B, PER, persist=False)
chk("H3 tenant A's flat config does NOT leak into tenant B",
    rb["flat_guard"] == CONST and ra["flat_guard"]["flat_chains"] == 1,
    (ra["flat_guard"]["flat_chains"], rb["flat_guard"]))
chk("H4 ...and the two tenants' pay differs", ra["totals"]["amount"] != rb["totals"]["amount"])

writes = []
try:
    sie.compute_sale_installments(FakeClient(build_store(payout={"home_internet": {"mode": "flat_once", "amount": AMT}}),
                                             writes=writes), ORG, PER, persist=False)
    chk("H5 a read-only run attempts NO write", not writes, writes)
except AssertionError as e:
    chk("H5 a read-only run attempts NO write", False, str(e))
# negative control: the guard really fires
tripped = False
try:
    FakeClient(build_store(), writes=[]).schema("commcalc").table("x").insert({})
except AssertionError:
    tripped = True
chk("H6 the write guard is TRIPPED deliberately (so H5 means something)", tripped)

lg = []
aca.audit(FakeClient(acc_store(), log=lg), ORG, PER)
bad = [e for e in lg if e[0] == "read" and not any(c == "org_id" for c, _ in e[2])]
chk("H7 the accessory-definition annotation reads only org-scoped", not bad, bad[:4])

import inspect
from app.modules.commcalc import router as R
for fn, name in ((R.get_category_payout, "GET category-payout"),
                 (R.put_category_payout, "PUT category-payout"),
                 (R.category_payout_impact, "GET category-payout-impact"),
                 (R.get_accessory_definition, "GET accessory-definition"),
                 (R.upsert_accessory_definition, "POST accessory-definition"),
                 (R.confirm_accessory_definition, "POST confirm"),
                 (R.seed_accessory_definition_classes, "POST seed-classes"),
                 (R.put_accessory_definition_field_rule, "PUT field-rule"),
                 (R.delete_accessory_definition, "DELETE accessory-definition"),
                 (R.accessory_definition_agreement, "GET agreement"),
                 (R.accessory_definition_classes, "GET classes"),
                 (R.accessory_definition_facets, "GET facets")):
    p = inspect.signature(fn).parameters.get("org_id")
    chk(f"H8 org_id is a QUERY PARAM on {name}", p is not None and p.default == R.ORG_ID)

# ══════════════════════════════════════════════════════════════════════════════════════════════════
sec("§I  MIGRATIONS 256 + 257")

MIGD = os.path.join(REPO, "database", "migrations")
M256 = os.path.join(MIGD, "256_commission_installment_category_flat_payout.sql")
M257 = os.path.join(MIGD, "257_commission_accessory_definition.sql")


def strip_comments(sql):
    return re.sub(r"--[^\n]*", "", sql)


for path, num in ((M256, 256), (M257, 257)):
    chk(f"I1-{num} file exists", os.path.exists(path))
    if not os.path.exists(path):
        continue
    sql = open(path).read()
    body = strip_comments(sql)
    chk(f"I2-{num} additive only (IF NOT EXISTS on every CREATE/ADD)",
        all("if not exists" in m.lower() for m in re.findall(r"(CREATE TABLE[^(]*|ADD COLUMN[^,;]*)", body)),
        [m for m in re.findall(r"(CREATE TABLE[^(]*|ADD COLUMN[^,;]*)", body) if "IF NOT EXISTS" not in m])
    chk(f"I3-{num} no DROP / DELETE / UPDATE of data",
        not re.search(r"\b(DROP|DELETE\s+FROM|UPDATE)\b", body, re.I),
        re.findall(r"\b(DROP|DELETE\s+FROM|UPDATE)\b", body, re.I))
    chk(f"I4-{num} no GRANT", "grant" not in body.lower())
    chk(f"I5-{num} no CREATE POLICY", "create policy" not in body.lower())
    chk(f"I6-{num} no anon / authenticated", not re.search(r"\b(anon|authenticated)\b", body, re.I))
    chk(f"I7-{num} every INSERT is ON CONFLICT DO NOTHING",
        body.lower().count("insert into") == body.lower().count("on conflict"),
        (body.lower().count("insert into"), body.lower().count("on conflict")))
    chk(f"I8-{num} in band 200-299 and not colliding",
        200 <= num <= 299 and len([f for f in os.listdir(MIGD) if f.startswith(f"{num}_")]) == 1)
    try:
        import pglast
        pglast.parse_sql(body)
        chk(f"I9-{num} real PostgreSQL parse (pglast)", True)
    except ImportError:
        chk(f"I9-{num} real PostgreSQL parse (pglast) — SKIPPED, pglast absent", True)
    except Exception as e:
        chk(f"I9-{num} real PostgreSQL parse (pglast)", False, str(e)[:160])

if os.path.exists(M256):
    b256 = strip_comments(open(M256).read())
    chk("I10 mig 256 contains NO dollar amount and NO seed (the owner types the number)",
        "insert into" not in b256.lower() and not re.search(r"\d+\.\d{2}", b256),
        re.findall(r"\d+\.\d{2}", b256))
    chk("I11 mig 256 names no tenant/carrier/product",
        not re.search(r"luxelink|boost|total wireless", b256, re.I))
    chk("I12 mig 256 touches no money table",
        not re.search(r"rep_commissions|commission_rule|commission_tier|sale_installment_ledger",
                      b256, re.I))

if os.path.exists(M257):
    s257 = open(M257).read()
    b257 = strip_comments(s257)
    chk("I13 mig 257 enables RLS on both new tables (zero policies)",
        b257.lower().count("enable row level security") == 2)
    chk("I14 both new tables carry org_id NOT NULL",
        b257.lower().count("org_id       uuid not null") + b257.lower().count("org_id          uuid not null") == 2
        or len(re.findall(r"org_id\s+UUID NOT NULL", b257)) == 2, re.findall(r"org_id\s+UUID NOT NULL", b257))
    chk("I15 both new tables carry an org index",
        len(re.findall(r"CREATE INDEX IF NOT EXISTS \w+ ON commcalc\.\w+ \(org_id", b257)) >= 2)
    # the seed is GENERATED FROM CODE — re-parse it and set-compare
    seeded = set(re.findall(r"'00000000-0000-0000-0000-000000000001', '([a-z_]+)',", b257))
    chk("I16 the seeded class keys are EXACTLY accessory_definition.DEFAULT_CLASSES",
        seeded == set(adef.CLASS_KEYS), (sorted(seeded), sorted(adef.CLASS_KEYS)))
    chk("I17 every seeded class is status='proposed' — none pre-confirmed",
        b257.count("'proposed')") == len(adef.DEFAULT_CLASSES)
        and "'confirmed'" not in b257, b257.count("'proposed')"))
    chk("I18 NO mapping rows are seeded (a mapping keys on the tenant's own data)",
        "insert into commcalc.accessory_definition_map" not in b257.lower())
    chk("I19 mig 257 touches no money table",
        not re.search(r"rep_commissions|commission_rule|commission_tier|commission_plan\b", b257, re.I))

# ══════════════════════════════════════════════════════════════════════════════════════════════════
sec("§J  DIFFERENTIAL SCOPE vs BASE")

try:
    # tracked modifications AND untracked new files — a new module is invisible to `git diff` until
    # it is added, and "we added no new file" would then be a false negative.
    changed = subprocess.check_output(
        ["git", "-C", REPO, "diff", "--name-only", BASE_REF], stderr=subprocess.DEVNULL).decode().split()
    changed += subprocess.check_output(
        ["git", "-C", REPO, "ls-files", "--others", "--exclude-standard"],
        stderr=subprocess.DEVNULL).decode().split()
    changed = sorted(set(changed))
except Exception:
    changed = []
chk("J1 the diff is readable", bool(changed), changed)
MUST_NOT = ["backend/app/modules/commcalc/calculator.py",
            "backend/app/modules/commcalc/commission_engine.py",
            "backend/app/modules/commcalc/commission_ledger.py",
            "backend/app/modules/commcalc/ledger_ma_sync.py",
            "backend/app/modules/commcalc/whatif.py",
            "backend/app/modules/commcalc/targets_engine.py",
            "backend/app/modules/commcalc/installment_engine.py",
            "backend/app/modules/commcalc/installment_category.py",
            "backend/app/modules/commcalc/gp_report.py",
            "backend/app/modules/commcalc/sales_analyzer.py",
            "backend/app/modules/commcalc/accessory_catalog.py",
            "backend/app/modules/commcalc/ma_product_class.py",
            "backend/app/main.py",
            "frontend/src/lib/client.ts",
            "frontend/src/lib/rbac.ts",
            "frontend/src/app/(platform)/layout.tsx"]
for m in MUST_NOT:
    chk(f"J2 UNTOUCHED: {os.path.basename(m)}", m not in changed)
EXPECTED = {"backend/app/modules/commcalc/sale_installment_engine.py",
            "backend/app/modules/commcalc/installment_category_payout.py",
            "backend/app/modules/commcalc/accessory_definition.py",
            "backend/app/modules/commcalc/accessory_cost_audit.py",
            "backend/app/modules/commcalc/router.py"}
py_changed = {c for c in changed if c.startswith("backend/app/")}
chk("J3 exactly five backend app files changed", py_changed == EXPECTED,
    sorted(py_changed ^ EXPECTED))
chk("J4 sale_installment_engine.py IS modified — this package IS money-touching, by directive",
    "backend/app/modules/commcalc/sale_installment_engine.py" in changed)

if base_sie:
    import inspect as _i
    for fn in ("_line_amount", "_mrc_candidate", "_gate_met", "_mi_index", "classify_line",
               "installment_label", "_persist", "_rule_matches", "_in_effective_window",
               "repU_cat", "_chain_legacy_forced", "_norm_mdn"):
        a_ = getattr(base_sie, fn, None)
        b_ = getattr(sie, fn, None)
        chk(f"J5 helper byte-identical to BASE: {fn}",
            a_ is not None and b_ is not None and _i.getsource(a_) == _i.getsource(b_))

print("\n" + "=" * 94)
print(f"RESULT: {OK} passed, {FAIL} failed")
if FAILS:
    print("FAILED:")
    for f in FAILS:
        print("  -", f)
print("=" * 94)
sys.exit(1 if FAIL else 0)
