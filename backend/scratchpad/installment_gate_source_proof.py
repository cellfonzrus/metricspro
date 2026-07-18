"""Proof for agent/commission/installment-gate-universal (owner-approved 2026-07-18, money-touching).

CHANGE (mig 223): sale_installment_engine._gate_met proved "dealer paid this month" ONLY from raw_mi — a
Boost/ePay-only table. For master-agent-fed tenants (Total Wireless via VidaPay; raw_ma_* from mig 083)
raw_mi is EMPTY, so every gated installment month was withheld_unpaid forever. The gate's EVIDENCE SOURCE is
now config-driven per carrier (installment_gate_source_config, mirroring whatif.py's mig-209 dispatch):
  • boost mode → 'boost_mi'      → the UNCHANGED raw_mi gate (byte-identical).
  • plan  mode → 'ma_commission' → raw_ma_commission per-IMEI per-month spiffs (the fix).

MA GATE RULE (documented): match the sold device serial (raw_sales.serial_1, digit-normalized IMEI) to a
raw_ma_commission device in the SALE (activation) period — that row carries the forward M1-M6 schedule
(owner repro: the June row holds both spiff_m1 and spiff_m2). Month N is PAID iff at least one of month N's
evidence columns has |NET| >= min, where net sums the device's base+adjustment rows (sign-agnostic; MA
amounts are negative = payout to dealer). Month N's column is spiff_mN; month 1 ALSO counts the configured
activation payouts (rebate, device_margin). line_status is NEVER keyed on (NULL in real rows).

This harness drives the REAL compute_sale_installments + preview_gate_impact over an in-memory FakeClient,
and compares the BOOST path against the PRISTINE origin/main engine (git show HEAD:...) row-for-row.

Run:  cd backend && python3 scratchpad/installment_gate_source_proof.py
"""
import os
import sys
import subprocess
import types

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import app.modules.commcalc.sale_installment_engine as NEW
from app.modules.commcalc.sale_installment_engine import (
    _norm_imei, _ma_gate_index, _gate_met_ma, _resolve_gate_cfg, _carrier_mode_map,
    compute_sale_installments, preview_gate_impact,
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


# ── Load the PRISTINE pre-change engine, PINNED to the branch's merge-base with origin/main (m4) ─────
# NOT `HEAD:` (self-referential once this package is committed). Use the merge-base with origin/main so the
# genuine pre-mig-223 engine is loaded even after further commits; fall back to the named base SHA 18df5c4.
_PINNED_BASE = "18df5c4"


def _load_old_engine():
    repo = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
    ref = _PINNED_BASE
    try:
        ref = subprocess.check_output(
            ["git", "-C", repo, "merge-base", "HEAD", "origin/main"], text=True).strip() or _PINNED_BASE
    except Exception:
        ref = _PINNED_BASE
    src = subprocess.check_output(
        ["git", "-C", repo, "show", f"{ref}:backend/app/modules/commcalc/sale_installment_engine.py"],
        text=True)
    mod = types.ModuleType("OLD_sale_installment_engine")
    mod.__dict__["__name__"] = "OLD_sale_installment_engine"
    exec(compile(src, "OLD_sale_installment_engine.py", "exec"), mod.__dict__)
    mod._loaded_from = ref
    return mod


OLD = _load_old_engine()
print(f"(differential pinned to pre-change engine @ {OLD._loaded_from[:10]})")


# ═══ In-memory FakeClient (order/range aware; schema-agnostic, unique table names) ═══════════════════
class FakeResult:
    def __init__(self, data):
        self.data = data


class FakeQuery:
    def __init__(self, store, table):
        self.store, self.t, self.f = store, table, []
        self.rng, self.ordk, self.orddesc = None, None, False

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

    def upsert(self, *a, **k):
        return self

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
        rows = [dict(r) for r in self.store.get(self.t, []) if self._m(r)]
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


# ═══ fixture builders ═══════════════════════════════════════════════════════════════════════════════
def sale(org, rep, tid, serial, mdn="", store="Store 5", period="June 2026", ct="New Activation"):
    return {"org_id": org, "period": period, "trans_id": tid, "store": store, "salesperson": rep,
            "category": "", "department": "", "contract_type": ct, "product_desc": "Total Unlimited $50/mo",
            "ext_price": 50.0, "gp": 10.0, "voided": "", "trans_type": "", "mdn": mdn,
            "serial_1": serial, "customer_plan": "Total Unlimited $50/mo"}


def ma_row(org, period, imei, **cols):
    r = {"org_id": org, "period": period, "imei": imei, "sim": "", "line_status": None,
         "spiff_m1": 0, "spiff_m2": 0, "spiff_m3": 0, "spiff_m4": 0, "spiff_m5": 0, "spiff_m6": 0,
         "rebate": 0, "device_margin": 0, "consumer_margin": 0, "mrc_net_discount": 0}
    r.update(cols)
    return r


def mi_row(org, period, phone, serial, active=True, mi=0.0, atu=0.0):
    return {"org_id": org, "period": period, "phone_number": phone, "device_serial": serial,
            "subscriber_status": "Active" if active else "Deactivated",
            "actual_mi_payout": mi, "actual_atu_payout": atu}


def plan(org, pid, name, carrier_id):
    return {"id": pid, "org_id": org, "name": name, "carrier_id": carrier_id, "is_active": True}


def default_assign(org, pid):
    return {"id": f"a-{pid}", "org_id": org, "plan_id": pid, "scope": "default",
            "scope_value": "", "priority": 0}


def sched(org, sid, pid, num_months=3, gate_mode="paid_residual", gate_from=1, m1_gate="inherit"):
    return {"id": sid, "org_id": org, "plan_id": pid, "is_active": True, "num_months": num_months,
            "gate_mode": gate_mode, "gate_from_month": gate_from, "m1_gate": m1_gate,
            "trigger_match_field": None, "trigger_match_op": None, "trigger_match_value": None}


def flat_lines(org, sid, amt, n=3):
    return [{"id": f"{sid}-l{i}", "org_id": org, "schedule_id": sid, "month_index": i,
             "payout_kind": "flat", "flat_amount": amt} for i in range(1, n + 1)]


def base_store(extra=None):
    s = {"commission_plan": [], "commission_rule": [], "commission_tier": [],
         "commission_plan_assignment": [], "plan_installment_schedule": [], "plan_installment_line": [],
         "raw_sales": [], "daily_sales_feed": [], "raw_mi": [], "raw_ma_commission": [],
         "store_mapping": [], "employees": [], "product_mrc": [], "carrier_category_map": [],
         "flag_rules": [], "commission_org_config": [], "item_mapping": [],
         "carrier": [], "installment_gate_source_config": []}
    if extra:
        for k, v in extra.items():
            s[k] = v
    return s


GATE_SEEDS = [
    {"org_id": HOUSE, "carrier_id": NIL, "carrier_mode": "boost", "gate_source": "boost_mi",
     "ma_device_fields": ["imei", "sim"], "ma_month_field_prefix": "spiff_m", "ma_max_month": 6,
     "ma_month1_extra_fields": ["rebate", "device_margin"], "ma_min_amount": 0.01, "is_active": True},
    {"org_id": HOUSE, "carrier_id": NIL, "carrier_mode": "plan", "gate_source": "ma_commission",
     "ma_device_fields": ["imei", "sim"], "ma_month_field_prefix": "spiff_m", "ma_max_month": 6,
     "ma_month1_extra_fields": ["rebate", "device_margin"], "ma_min_amount": 0.01, "is_active": True},
]


# ═══════════════════════════════════════════════════════════════════════════════════════════════════
# 1. UNIT — _norm_imei (IMEI digit-normalization; NOT last-10 truncation)
# ═══════════════════════════════════════════════════════════════════════════════════════════════════
print("\n── 1. _norm_imei (IMEI digit normalization) ──")
check("strips Excel-float .0", _norm_imei("355163568356973.0") == "355163568356973")
check("plain 15-digit unchanged", _norm_imei("355163568356973") == "355163568356973")
check("full 15 digits kept (NOT last-10)", len(_norm_imei("355163568356973")) == 15)
check("strips separators/spaces", _norm_imei(" 35516-3568 356973 ") == "355163568356973")
check("empty -> ''", _norm_imei(None) == "" and _norm_imei("") == "")


# ═══════════════════════════════════════════════════════════════════════════════════════════════════
# 2. UNIT — _ma_gate_index + _gate_met_ma (adjustment double-rows, negative signs, NULL status)
# ═══════════════════════════════════════════════════════════════════════════════════════════════════
print("\n── 2. _ma_gate_index + _gate_met_ma ──")
REPRO = "355163568356973"
cfg_plan = _resolve_gate_cfg([], [], "c-total", "plan")
check("plan-mode code default = ma_commission", cfg_plan["gate_source"] == "ma_commission")

# owner repro data shape: TWO rows same IMEI/period (base + adjustment), negatives, NULL line_status
ma_rows = [
    ma_row(LUXE, "June 2026", REPRO, spiff_m1=-5, spiff_m2=-48.75, rebate=-529, device_margin=-20),
    ma_row(LUXE, "June 2026", REPRO, spiff_m2=-5),   # adjustment row, line_status NULL
]
idx = _ma_gate_index(ma_rows)
check("index keyed by normalized IMEI", REPRO in idx)
check("adjustment rows SUMMED (spiff_m2 = -48.75 + -5)", abs(idx[REPRO]["spiff_m2"] - (-53.75)) < 1e-9,
      idx[REPRO].get("spiff_m2"))
check("spiff_m1 net = -5", abs(idx[REPRO]["spiff_m1"] - (-5)) < 1e-9)

# month 1 paid via spiff_m1 (negative)
met1, ev1 = _gate_met_ma({"serial_1": REPRO}, idx, 1, cfg_plan)
check("month 1 PAID (negative spiff_m1)", met1 is True, ev1)
check("month 1 evidence carries spiff_m1", ev1["evidence"].get("spiff_m1") == -5)
# month 2 paid via netted adjustment
met2, ev2 = _gate_met_ma({"serial_1": REPRO}, idx, 2, cfg_plan)
check("month 2 PAID (netted -53.75)", met2 is True, ev2)
# month 3 has no spiff -> no_month_payout
met3, ev3 = _gate_met_ma({"serial_1": REPRO}, idx, 3, cfg_plan)
check("month 3 NOT paid (no spiff_m3)", met3 is False and ev3["reason"] == "no_month_payout", ev3)
# IMEI normalization on the JOIN: sale serial carries the Excel .0 suffix
metx, evx = _gate_met_ma({"serial_1": REPRO + ".0"}, idx, 1, cfg_plan)
check("IMEI .0 suffix still matches on join", metx is True, evx)
# missing device -> no_ma_record
metn, evn = _gate_met_ma({"serial_1": "999999999999999"}, idx, 1, cfg_plan)
check("unknown device -> no_ma_record", metn is False and evn["reason"] == "no_ma_record", evn)
# month 1 via rebate ONLY (spiff_m1 == 0) — activation payout counts for month 1
idx2 = _ma_gate_index([ma_row(LUXE, "June 2026", "111122223333444", rebate=-529)])
metr, evr = _gate_met_ma({"serial_1": "111122223333444"}, idx2, 1, cfg_plan)
check("month 1 PAID via rebate alone (month1 extra field)", metr is True, evr)
metr2, evr2 = _gate_met_ma({"serial_1": "111122223333444"}, idx2, 2, cfg_plan)
check("month 2 NOT paid on rebate (extra fields are month-1 only)", metr2 is False, evr2)
# clawback: full reversal nets to EXACT 0 -> not paid, reason no_month_payout (no directional charge)
idx3 = _ma_gate_index([ma_row(LUXE, "June 2026", "222233334444555", spiff_m2=-40),
                       ma_row(LUXE, "June 2026", "222233334444555", spiff_m2=40)])
metc, evc = _gate_met_ma({"serial_1": "222233334444555"}, idx3, 2, cfg_plan)
check("exact-zero net -> NOT paid (no_month_payout)", metc is False and evc["reason"] == "no_month_payout", evc)
# beyond MA month columns
met7, ev7 = _gate_met_ma({"serial_1": REPRO}, idx, 7, cfg_plan)
check("month 7 -> month_beyond_ma_columns", met7 is False and ev7["reason"] == "month_beyond_ma_columns", ev7)

print("\n── 2b. M2 DIRECTION-AWARE paid test (net must be a payout, not a charge) ──")
# over-reversal: -48.75 base + +55.00 reversal = net +6.25 (dealer CHARGED) -> NOT paid, reason net_clawback
idx_over = _ma_gate_index([ma_row(LUXE, "June 2026", "444455556666777", spiff_m2=-48.75),
                           ma_row(LUXE, "June 2026", "444455556666777", spiff_m2=55.00)])
mo, eo = _gate_met_ma({"serial_1": "444455556666777"}, idx_over, 2, cfg_plan)
check("over-reversal (net +6.25 = charged) -> WITHHELD", mo is False, eo)
check("over-reversal reason = net_clawback (honest)", eo["reason"] == "net_clawback", eo)
# partial reversal: -48.75 + +5.00 = net -43.75 (still a payout) -> PAID
idx_part = _ma_gate_index([ma_row(LUXE, "June 2026", "555566667777888", spiff_m2=-48.75),
                           ma_row(LUXE, "June 2026", "555566667777888", spiff_m2=5.00)])
mp, ep = _gate_met_ma({"serial_1": "555566667777888"}, idx_part, 2, cfg_plan)
check("partial reversal (net -43.75 = still payout) -> PAID", mp is True, ep)
# a POSITIVE spiff (a plan whose statement pays POSITIVE) with ma_payout_sign=+1 -> paid; with -1 -> charged
cfg_pos = dict(cfg_plan); cfg_pos["ma_payout_sign"] = 1
idx_pos = _ma_gate_index([ma_row(LUXE, "June 2026", "666677778888999", spiff_m1=25)])
check("sign=+1: positive payout -> PAID", _gate_met_ma({"serial_1": "666677778888999"}, idx_pos, 1, cfg_pos)[0] is True)
check("sign=-1: same +25 reads as a CHARGE -> WITHHELD",
      _gate_met_ma({"serial_1": "666677778888999"}, idx_pos, 1, cfg_plan)[0] is False)

print("\n── 2c. m3 zero-min CLAMP (0 is not a no-minimum sentinel) ──")
cfg_zeromin = dict(cfg_plan); cfg_zeromin["ma_min_amount"] = 0
idx_zero = _ma_gate_index([ma_row(LUXE, "June 2026", "777788889999000")])  # ALL columns zero
mz, ez = _gate_met_ma({"serial_1": "777788889999000"}, idx_zero, 1, cfg_zeromin)
check("ma_min_amount=0 clamped -> all-zero device NOT paid", mz is False, ez)
# a real -5 spiff still pays under the clamped default
idx_real = _ma_gate_index([ma_row(LUXE, "June 2026", "777788889999000", spiff_m1=-5)])
check("clamped min still pays a real -5 spiff",
      _gate_met_ma({"serial_1": "777788889999000"}, idx_real, 1, cfg_zeromin)[0] is True)


# ═══════════════════════════════════════════════════════════════════════════════════════════════════
# 3. UNIT — _resolve_gate_cfg resolution order + _carrier_mode_map
# ═══════════════════════════════════════════════════════════════════════════════════════════════════
print("\n── 3. config resolution + carrier-mode map ──")
check("boost-mode code default = boost_mi", _resolve_gate_cfg([], [], "c-b", "boost")["gate_source"] == "boost_mi")
check("plan-mode code default = ma_commission", _resolve_gate_cfg([], [], "c-t", "plan")["gate_source"] == "ma_commission")
# house seed inheritance (org has no rows -> inherits house mode-default)
r_house = _resolve_gate_cfg([], GATE_SEEDS, None, "plan")
check("house plan seed inherited", r_house["gate_source"] == "ma_commission" and r_house["_resolved_from"] == "house_mode_default")
# org-carrier override beats mode default
org_rows = [{"org_id": LUXE, "carrier_id": "c-total", "carrier_mode": "plan", "gate_source": "boost_mi"}]
r_ov = _resolve_gate_cfg(org_rows, GATE_SEEDS, "c-total", "plan")
check("org-carrier row overrides", r_ov["gate_source"] == "boost_mi" and r_ov["_resolved_from"] == "org_carrier")
# carrier-mode map
cmm_store = base_store({"carrier": [
    {"id": "c-boost", "org_id": HOUSE, "name": "Boost Mobile", "code": "Boost", "is_default": True},
    {"id": "c-total", "org_id": HOUSE, "name": "Total Wireless", "code": "Total", "is_default": False}]})
mode_by_id, default_mode = _carrier_mode_map(FakeClient(cmm_store), HOUSE)
check("Boost carrier -> boost", mode_by_id.get("c-boost") == "boost")
check("Total carrier -> plan", mode_by_id.get("c-total") == "plan")
check("house default mode = boost (default carrier is Boost)", default_mode == "boost")
luxe_store = base_store({"carrier": [
    {"id": "c-total", "org_id": LUXE, "name": "Total Wireless", "code": "Total", "is_default": True}]})
_, luxe_default = _carrier_mode_map(FakeClient(luxe_store), LUXE)
check("luxelink default mode = plan", luxe_default == "plan")


# ═══════════════════════════════════════════════════════════════════════════════════════════════════
# 4. BOOST BYTE-IDENTICAL — NEW engine vs PRISTINE origin/main, row-for-row
# ═══════════════════════════════════════════════════════════════════════════════════════════════════
print("\n── 4. BOOST byte-identical (NEW vs origin/main) ──")
DEV_PAID = "490154203237518"     # raw_mi Active + residual -> month 1 pays
DEV_UNPD = "356938035643809"     # no raw_mi -> withheld_unpaid


def boost_store(with_config=True):
    st = base_store({
        "carrier": [{"id": "c-boost", "org_id": HOUSE, "name": "Boost Mobile", "code": "Boost", "is_default": True}],
        "commission_plan": [plan(HOUSE, "p1", "Boost Plan", "c-boost")],
        "commission_plan_assignment": [default_assign(HOUSE, "p1")],
        "plan_installment_schedule": [sched(HOUSE, "s1", "p1", num_months=2, gate_from=1)],
        "plan_installment_line": flat_lines(HOUSE, "s1", 10.0, n=2),
        "raw_sales": [
            sale(HOUSE, "Diana Antunez", "t-paid", DEV_PAID, mdn="6035550111"),
            sale(HOUSE, "Diana Antunez", "t-unpd", DEV_UNPD, mdn="6035550222"),
        ],
        "raw_mi": [mi_row(HOUSE, "June 2026", "6035550111", DEV_PAID, active=True, mi=4.0, atu=1.0)],
    })
    if with_config:
        st["installment_gate_source_config"] = [dict(r) for r in GATE_SEEDS]
    return st


for label, cfg_on in (("with mig-223 seeds", True), ("mig-223 ABSENT (code default)", False)):
    old_res = OLD.compute_sale_installments(FakeClient(boost_store(cfg_on)), HOUSE, "June 2026")
    new_res = NEW.compute_sale_installments(FakeClient(boost_store(cfg_on)), HOUSE, "June 2026")
    check(f"[{label}] by_rep identical", old_res["by_rep"] == new_res["by_rep"], f"{old_res['by_rep']} vs {new_res['by_rep']}")
    check(f"[{label}] totals identical", old_res["totals"] == new_res["totals"], f"{old_res['totals']} vs {new_res['totals']}")
    check(f"[{label}] ledger identical (row-for-row)", old_res["ledger"] == new_res["ledger"],
          "LEDGER DIFF")
    check(f"[{label}] flags identical", old_res["flags"] == new_res["flags"], "FLAGS DIFF")
    # the Boost ledger must carry NONE of the new MA keys
    newkeys = set().union(*[set(r.keys()) for r in new_res["ledger"]]) if new_res["ledger"] else set()
    check(f"[{label}] Boost ledger has NO MA keys",
          not (newkeys & {"gate_kind", "gate_source", "ma_matched", "ma_evidence", "ma_reason"}), newkeys)
    check(f"[{label}] one paid, one withheld", new_res["totals"]["paid"] == 1 and new_res["totals"]["withheld"] == 1,
          new_res["totals"])


# ═══════════════════════════════════════════════════════════════════════════════════════════════════
# 5. MA INTEGRATION — luxelink, the live bug scenario
# ═══════════════════════════════════════════════════════════════════════════════════════════════════
print("\n── 5. MA integration (luxelink) ──")
DEV_A = "355163568356973"   # repro: spiff_m1 -5, spiff_m2 -48.75 (+adj -5) in JUNE row
DEV_B = "111122223333444"   # rebate only -> month 1 pays
DEV_C = "222255558888111"   # NO MA record -> withheld
DEV_D = "333366669999222"   # MA record but spiff_m1 == 0, no rebate -> withheld


def luxe_store(pay_ready_periods=("June 2026",)):
    return base_store({
        "carrier": [{"id": "c-total", "org_id": LUXE, "name": "Total Wireless", "code": "Total", "is_default": True}],
        "commission_plan": [plan(LUXE, "p2", "3MR Commission Payment plan", "c-total")],
        "commission_plan_assignment": [default_assign(LUXE, "p2")],
        "plan_installment_schedule": [sched(LUXE, "s2", "p2", num_months=3, gate_mode="paid_residual",
                                            gate_from=1, m1_gate="inherit")],
        "plan_installment_line": flat_lines(LUXE, "s2", 50.0, n=3),
        "installment_gate_source_config": [dict(r) for r in GATE_SEEDS],
        # JUNE sales (all four devices) — salesperson in POS "Last, First" form is fine (default assign)
        "raw_sales": [
            sale(LUXE, "Antunez, Diana", "tA", DEV_A + ".0", mdn="2015550001"),   # .0 tests IMEI norm
            sale(LUXE, "Antunez, Diana", "tB", DEV_B, mdn="2015550002"),
            sale(LUXE, "Cabrera, Natasha", "tC", DEV_C, mdn="2015550003"),
            sale(LUXE, "Cabrera, Natasha", "tD", DEV_D, mdn="2015550004"),
        ],
        # JUNE raw_ma_commission — note period stored as '2026-06' to prove spelling-duality via _pvariants
        "raw_ma_commission": [
            ma_row(LUXE, "2026-06", DEV_A, spiff_m1=-5, spiff_m2=-48.75, rebate=-529, device_margin=-20),
            ma_row(LUXE, "2026-06", DEV_A, spiff_m2=-5),          # adjustment row, NULL line_status
            ma_row(LUXE, "2026-06", DEV_B, rebate=-529),          # rebate only
            ma_row(LUXE, "2026-06", DEV_D, spiff_m1=0),           # present but no month-1 payout
        ],
        # raw_mi INTENTIONALLY EMPTY (this is the whole bug) — the OLD gate finds nothing here.
        "raw_mi": [],
    })


res = compute_sale_installments(FakeClient(luxe_store()), LUXE, "June 2026")
byrep = res["by_rep"]
led = {r["trans_id"]: r for r in res["ledger"]}
check("DEV_A month1 PAID (spiff_m1, IMEI .0 matched)", led["tA"]["status"] == "paid", led["tA"])
check("DEV_A ledger tagged ma_residual + evidence", led["tA"].get("gate_kind") == "ma_residual"
      and led["tA"].get("ma_evidence", {}).get("spiff_m1") == -5, led["tA"].get("ma_evidence"))
check("DEV_B month1 PAID via rebate", led["tB"]["status"] == "paid", led["tB"])
check("DEV_C WITHHELD (no MA record) + reason", led["tC"]["status"] == "withheld_unpaid"
      and led["tC"].get("ma_reason") == "no_ma_record", led["tC"].get("ma_reason"))
check("DEV_D WITHHELD (no month-1 payout) + reason", led["tD"]["status"] == "withheld_unpaid"
      and led["tD"].get("ma_reason") == "no_month_payout", led["tD"].get("ma_reason"))
check("Diana paid = $100 (DEV_A $50 + DEV_B $50)", abs(byrep.get("ANTUNEZ, DIANA", 0) - 100.0) < 1e-9, byrep)
check("Natasha paid = $0 (both withheld)", "CABRERA, NATASHA" not in byrep, byrep)
# withheld held-reason text is MA-specific (not the raw_mi 'residual' wording)
wf = [f for f in res["flags"] if f["source"] == "commission_rebate_tracking" and "tC-ish" not in ""]
c_flag = next(f for f in res["flags"] if "no master-agent commission record" in f["description"])
check("held-reason names the master-agent statement", "master-agent" in c_flag["description"], c_flag["description"])
check("no raw_mi 'residual' wording on MA holds",
      not any("receiving residual (dealer unpaid)" in f["description"] for f in res["flags"]))

# multi-month: pay JULY, device sold JUNE -> month_index 2 read from the JUNE MA row (forward schedule)
res_july = compute_sale_installments(FakeClient(luxe_store()), LUXE, "July 2026")
led_j = {r["trans_id"]: r for r in res_july["ledger"]}
# DEV_A: month 2 uses June spiff_m2 net (-53.75) -> PAID
check("DEV_A month2 (July pay) PAID from June forward schedule",
      led_j.get("tA", {}).get("month_index") == 2 and led_j["tA"]["status"] == "paid", led_j.get("tA"))
check("DEV_B month2 (July pay) WITHHELD (no June spiff_m2)",
      led_j.get("tB", {}).get("status") == "withheld_unpaid", led_j.get("tB"))


# ═══════════════════════════════════════════════════════════════════════════════════════════════════
# 5b. L2 KILL SWITCH — INSTALLMENT_GATE_LEGACY=1 ⇒ byte-identical to pre-change engine for BOTH modes
# ═══════════════════════════════════════════════════════════════════════════════════════════════════
print("\n── 5b. L2 kill switch (INSTALLMENT_GATE_LEGACY) ──")
# sanity FIRST: with the switch OFF, the MA org DIVERGES from the pinned pre-change engine (fix is active)
off_new = NEW.compute_sale_installments(FakeClient(luxe_store()), LUXE, "June 2026")
off_old = OLD.compute_sale_installments(FakeClient(luxe_store()), LUXE, "June 2026")
check("switch OFF: MA org DIVERGES from pre-change engine (fix live)", off_new["ledger"] != off_old["ledger"])
check("switch OFF: pre-change engine pays $0 (all withheld)", off_old["totals"]["paid"] == 0, off_old["totals"])

os.environ["INSTALLMENT_GATE_LEGACY"] = "1"
try:
    check("kill switch reads truthy", NEW._legacy_gate_forced() is True)
    for label, mk in (("BOOST", lambda: boost_store(True)), ("MA/luxelink", luxe_store)):
        kn = NEW.compute_sale_installments(FakeClient(mk()), HOUSE if label == "BOOST" else LUXE,
                                           "June 2026")
        ko = OLD.compute_sale_installments(FakeClient(mk()), HOUSE if label == "BOOST" else LUXE,
                                           "June 2026")
        check(f"kill-switch ON: [{label}] ledger == pre-change engine", kn["ledger"] == ko["ledger"], "DIFF")
        check(f"kill-switch ON: [{label}] by_rep == pre-change", kn["by_rep"] == ko["by_rep"], f"{kn['by_rep']} vs {ko['by_rep']}")
        check(f"kill-switch ON: [{label}] totals == pre-change", kn["totals"] == ko["totals"], f"{kn['totals']} vs {ko['totals']}")
        check(f"kill-switch ON: [{label}] NO MA keys leak",
              not (set().union(*[set(r) for r in kn["ledger"]], set()) &
                   {"gate_kind", "gate_source", "ma_matched", "ma_evidence", "ma_reason"}))
finally:
    os.environ.pop("INSTALLMENT_GATE_LEGACY", None)
check("kill switch reads falsy after unset", NEW._legacy_gate_forced() is False)


# ═══════════════════════════════════════════════════════════════════════════════════════════════════
# 6. IMPACT PREVIEW — the Gate-2 review artifact
# ═══════════════════════════════════════════════════════════════════════════════════════════════════
print("\n── 6. impact preview (preview_gate_impact) ──")
# Boost: zero flips, boost_safe True
imp_boost = preview_gate_impact(FakeClient(boost_store(True)), HOUSE, "June 2026")
check("Boost impact: boost_safe True", imp_boost["boost_safe"] is True, imp_boost)
check("Boost impact: ZERO flips", imp_boost["flip_count"] == 0, imp_boost["flips_to_payable"])
check("Boost impact: no regressions", imp_boost["regressions_to_withheld"] == [])

# luxelink: flips DEV_A + DEV_B to payable, $50 each, per-rep + total
imp = preview_gate_impact(FakeClient(luxe_store()), LUXE, "June 2026")
check("MA impact: 2 flips to payable", imp["flip_count"] == 2, imp["flips_to_payable"])
check("MA impact: total newly payable = $100", abs(imp["total_newly_payable"] - 100.0) < 1e-9, imp["total_newly_payable"])
check("MA impact: per-rep Diana = $100", abs(imp["by_rep"].get("Antunez, Diana", 0) - 100.0) < 1e-9, imp["by_rep"])
check("MA impact: NO regressions (fix only opens gates)", imp["regressions_to_withheld"] == [], imp["regressions_to_withheld"])
check("MA impact: legacy totals show all withheld", imp["legacy_totals"]["paid"] == 0, imp["legacy_totals"])
check("MA impact: new totals show 2 paid", imp["new_totals"]["paid"] == 2, imp["new_totals"])
check("MA impact: flip row carries ma_evidence", imp["flips_to_payable"][0].get("ma_evidence") is not None)

# n1: _flip_key must include plan_id + schedule_id so two schedules on the same device+month don't collide
r_a = {"sale_period": "June 2026", "month_index": 1, "trans_id": "t1", "mdn": "", "serial_1": "abc",
       "plan_id": "p1", "schedule_id": "s1"}
r_b = {**r_a, "schedule_id": "s2"}
r_c = {**r_a, "plan_id": "p2"}
check("n1: _flip_key distinguishes different schedule_id", NEW._flip_key(r_a) != NEW._flip_key(r_b))
check("n1: _flip_key distinguishes different plan_id", NEW._flip_key(r_a) != NEW._flip_key(r_c))

print("\n─────────────────────────────────────────")
print(f"IMPACT-PREVIEW SAMPLE (luxelink June 2026):")
print(f"  boost_safe={imp['boost_safe']}  flip_count={imp['flip_count']}  total_newly_payable=${imp['total_newly_payable']}")
for f in imp["flips_to_payable"]:
    print(f"    {f['rep']:<16} M{f['month_index']}  IMEI {f['imei']:<18} ${f['amount']:>7.2f}  ev={f['ma_evidence']}")
print(f"  by_rep={imp['by_rep']}")
print("─────────────────────────────────────────")

print(f"\n{'='*50}\nPASS={PASS}  FAIL={FAIL}\n{'='*50}")
sys.exit(1 if FAIL else 0)
