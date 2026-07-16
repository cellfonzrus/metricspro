# SNAPSHOT: verbatim copy of sale_installment_engine.py at HEAD (pre-mig-210, base origin/main 144a076→f2a792e)
# BEFORE the installment-edit-m1gate changes. Used ONLY by installment_edit_m1gate_proof.py to prove the
# new engine is byte-identical for schedules that do NOT opt into m1_gate=activation_payment. Do not edit.
"""Sale-triggered multi-month rep-pay engine — Commission-Plan payout type (migration 201).

DOCTRINE (owner, 2026-07-14; commission-0 §7b): rep multi-month commission is a COMMISSION-PLAN payout
type triggered by the SALE LINE (M1..N relative to the sale's trans_date), resolved per rep by plan
assignment — NOT by the carrier statement roster. The legacy raw_mi installment_engine (mig 057) STAYS,
demoted to dealer-revenue recon; THIS engine is the new sale-triggered path beside it.

PAID GATE (owner rev-B correction, §7b decision 2): months 1..N pay ONLY when, at calc time, the sold
line is ACTIVE and the dealer is receiving residual on that line — proven by raw_mi presence for that
line/period. "We pay as we get paid." A sold-but-unpaid line is WITHHELD ($0) and surfaced as TWO flags
(sources 'commission_rebate_tracking' + 'employee_miss'). Clawback is optional/off (schedule config).

MRC (§7b decision 1): pct_mrc lines resolve MRC from the product_mrc CATALOG (mig 074), auto-prefilled by
extracting the $ from the product-description text, user-confirmed. NEVER from the carrier statement.

USER-DEFINED MONTHS (§7b decision 3): which sales generate installments (backfill vs cutover) is config on
the schedule — effective_from/effective_to window and/or an explicit eligible_sale_periods list. Nothing
hardcoded.

BOOST-SAFE + READ-ONLY-by-default: with no plan_installment_schedule the engine returns EMPTY (Boost has
none → byte-identical). Degrades to a no-op if migration 201 isn't applied (tables absent). persist=True is
the opt-in that writes the sale_installment_ledger; the pay contribution is wired in _apply_new_engines.

The classifier, the MRC extractor and the gate are PURE functions (config passed as args) so they are
unit-testable without a database (see scratchpad proof harness).
"""
import re
import calendar
from datetime import date

from app.modules.commcalc.calculator import parse_period, safe_float
from app.modules.commcalc.installment_engine import (
    _pvariants, _period_index, _shift_period, _load_product_mrc, _catalog_mrc, _read_mi,
)
from app.modules.commcalc.commission_engine import (
    _load_plans, _resolve_plan_for, _read_sales, _read_store_market, _rule_matches, _norm_mdn,
)

ORG_ID = "00000000-0000-0000-0000-000000000001"

# The line classifications the user chooses from (§7b decision 1). Carrier-agnostic; reuse the existing
# classifier CONFIG to auto-suggest — never a new sixth classifier.
CLASSIFICATIONS = ("accessory", "activation", "upgrade", "swap", "bill_payment", "rebate", "misc_other")

# ── MRC PREFILL: extract the $ monthly charge from a product-description text (PURE) ────────────────
_MONEY = r"\$?\s*([0-9]{1,3}(?:,[0-9]{3})*(?:\.[0-9]{1,2})?|[0-9]+(?:\.[0-9]{1,2})?)"
# a money token that is ADJACENT to a monthly keyword (…/mo, per month, monthly, /mth, mo.)
_MONTHLY_AFTER = re.compile(_MONEY + r"\s*(?:/\s*)?(?:mo\b|mo\.|month(?:ly)?\b|/mth\b|per\s+month\b|rec(?:urring)?\b)", re.I)
_MONTHLY_BEFORE = re.compile(r"(?:mrc|monthly|per\s+month|rec(?:urring)?)\D{0,6}" + _MONEY, re.I)
_ANY_DOLLAR = re.compile(r"\$\s*([0-9]{1,3}(?:,[0-9]{3})*(?:\.[0-9]{1,2})?|[0-9]+(?:\.[0-9]{1,2})?)")


def _to_f(tok):
    try:
        return float(str(tok).replace(",", "").strip())
    except Exception:
        return None


def extract_mrc_from_desc(desc):
    """Best-effort MONTHLY recurring charge ($) extracted from a product-description string, or None.

    Preference order so a device PRICE doesn't masquerade as an MRC:
      1. a $ amount adjacent to a monthly keyword  ("$25/mo", "25 monthly", "$50 per month")
      2. a monthly keyword followed by a $ amount   ("MRC $30", "monthly: 40")
      3. a bare $-prefixed amount                    ("Unlimited $50")  — last resort
    Commas are stripped ("$1,234.00" → 1234.0). Returns None for 0 / no match. PURE (no I/O)."""
    s = "" if desc is None else str(desc)
    if not s.strip():
        return None
    for rx in (_MONTHLY_AFTER, _MONTHLY_BEFORE):
        m = rx.search(s)
        if m:
            v = _to_f(m.group(1))
            if v and v > 0:
                return round(v, 2)
    m = _ANY_DOLLAR.search(s)
    if m:
        v = _to_f(m.group(1))
        if v and v > 0:
            return round(v, 2)
    return None


# ── CLASSIFIER: reuse existing config to label a line (PURE given the config) ──────────────────────
def classify_line(row, acc_sets=None, ccmap_rules=None):
    """Classify one sale line into a CLASSIFICATIONS value, reusing existing classifier CONFIG:
      - accessory: department/category/product in the mig-092 accessory config (acc_sets).
      - rebate / bill_payment: matched by carrier_category_map (mig 038) rules OR obvious tokens.
      - activation / upgrade / swap: from contract_type / trans_type.
    acc_sets = {'departments','categories','products'} lowercased sets (from _accessory_config).
    ccmap_rules = commcalc.carrier_category_map rows (raw_category/match_type/component/subtype).
    PURE — pass config in; no DB. Falls back to 'misc_other'."""
    acc = acc_sets or {}
    dept = str(row.get("department", "") or "").strip().lower()
    cat = str(row.get("category", "") or "").strip().lower()
    prod = str(row.get("product_desc", "") or "").strip().lower()
    ct = str(row.get("contract_type", "") or "").strip().lower()
    tt = str(row.get("trans_type", "") or "").strip().lower()

    # accessory (mig 092 config)
    if dept and dept in (acc.get("departments") or set()):
        return "accessory"
    if cat and cat in (acc.get("categories") or set()):
        return "accessory"
    if (acc.get("products") or set()) and prod and any(k in prod for k in acc["products"]):
        return "accessory"

    # carrier_category_map (mig 038): a rule whose subtype/component marks the line
    for r in (ccmap_rules or []):
        raw = str(r.get("raw_category", "") or "").strip().lower()
        if not raw:
            continue
        mt = (r.get("match_type") or "exact").lower()
        hay = f"{ct} {tt} {cat} {prod}"
        hit = (raw in hay) if mt in ("contains", "prefix", "regex") else (raw == ct or raw == cat)
        if hit:
            comp = str(r.get("component", "") or "").upper()
            sub = str(r.get("subtype", "") or "").lower()
            if comp == "REIMBURSEMENT" or "rebate" in sub or "subsidy" in sub:
                return "rebate"

    # obvious tokens
    blob = f"{ct} {tt} {cat} {prod}"
    if any(k in blob for k in ("bill pay", "recharge", "refill", "top up", "topup", "wallet funding", "reboost", "re-boost")):
        return "bill_payment"
    if "rebate" in blob or "reimburs" in blob:
        return "rebate"
    if "upgrade" in ct or "upgrade" in tt:
        return "upgrade"
    if any(k in ct for k in ("swap", "sim swap", "exchange")):
        return "swap"
    if any(k in ct for k in ("activation", "port-in", "port in", "add a line", "aal", "byod", "new line")):
        return "activation"
    return "misc_other"


# ── PAID GATE: match a sold line to raw_mi for the PAY period + check active/paying (PURE) ──────────
def _mi_index(mi_rows):
    """Index raw_mi rows for one period by normalized MDN and by normalized device serial (the two
    reliable per-line/per-subscriber keys). Returns {'mdn':{...}, 'serial':{...}}."""
    by_mdn, by_serial = {}, {}
    for r in mi_rows:
        m = _norm_mdn(r.get("phone_number") or r.get("mdn"))
        if m:
            by_mdn.setdefault(m, r)
        s = _norm_mdn(r.get("device_serial"))
        if s:
            by_serial.setdefault(s, r)
    return {"mdn": by_mdn, "serial": by_serial}


def _match_mi(sale_line, mi_index):
    """The raw_mi row for a sold line in the pay period, matched by MDN first then device serial.
    This IS the line-matching key of the paid gate. Returns the row or None."""
    m = _norm_mdn(sale_line.get("mdn"))
    if m and m in mi_index["mdn"]:
        return mi_index["mdn"][m]
    s = _norm_mdn(sale_line.get("serial_1"))
    if s and s in mi_index["serial"]:
        return mi_index["serial"][s]
    return None


def _gate_met(sale_line, mi_index, gate_mode):
    """(met, mi_row): does the sold line qualify to be paid this month? gate_mode:
      'none'            → always paid (pure calendar; the gate is OFF for this schedule).
      'active_status'   → the line is present in raw_mi and subscriber_status is Active.
      'nonzero_residual'→ present and (actual_mi_payout+actual_atu_payout) > 0.
      'paid_residual'   → present AND Active AND residual > 0 (default; "paid + residual received").
    PURE — mi_index is prebuilt from the pay-period raw_mi."""
    if gate_mode == "none":
        return True, None
    row = _match_mi(sale_line, mi_index)
    if row is None:
        return False, None
    active = str(row.get("subscriber_status") or "").strip().lower().startswith("activ")
    resid = safe_float(row.get("actual_mi_payout")) + safe_float(row.get("actual_atu_payout"))
    if gate_mode == "active_status":
        return active, row
    if gate_mode == "nonzero_residual":
        return resid > 0, row
    return (active and resid > 0), row


# ── USER-DEFINED effective window (backfill vs cutover) (PURE) ──────────────────────────────────────
def _sale_date(sale_line):
    s = str(sale_line.get("trans_date") or "")[:10]
    try:
        return date.fromisoformat(s) if len(s) == 10 else None
    except Exception:
        return None


def _in_effective_window(sale_line, sale_period, sched):
    """True if this sold line is eligible to generate installments under the schedule's USER-DEFINED
    month config. An explicit eligible_sale_periods list (any spelling) OVERRIDES the date window.
    Both unset → eligible (no floor). PURE."""
    elig = [str(p).strip() for p in (sched.get("eligible_sale_periods") or []) if str(p).strip()]
    if elig:
        want = set()
        for p in elig:
            want.update(_pvariants(p))
        return any(v in want for v in _pvariants(sale_period))
    d = _sale_date(sale_line)
    ef, et = sched.get("effective_from"), sched.get("effective_to")
    if ef:
        try:
            if d is None or d < date.fromisoformat(str(ef)[:10]):
                return False
        except Exception:
            pass
    if et:
        try:
            if d is None or d > date.fromisoformat(str(et)[:10]):
                return False
        except Exception:
            pass
    return True


# ── amount for one installment line (PURE given the catalog) ───────────────────────────────────────
def _line_amount(sale_line, iline, catalog, carrier_id):
    """(amount, mrc, mrc_source) for one installment line on one sold line.
    flat → flat_amount. pct_mrc → mrc_pct × MRC where MRC = product_mrc catalog (keyed on the line's
    customer_plan/product_desc), falling back to a description-extracted prefill, then 0. PURE."""
    kind = (iline.get("payout_kind") or "flat").strip().lower()
    if kind != "pct_mrc":
        return round(safe_float(iline.get("flat_amount")), 2), 0.0, "flat"
    plan = str(sale_line.get("customer_plan") or sale_line.get("product_desc") or "").strip()
    mrc = _catalog_mrc(catalog, carrier_id, plan)
    src = "product_catalog"
    if mrc is None:
        mrc = extract_mrc_from_desc(sale_line.get("product_desc"))
        src = "prefill" if mrc is not None else "none"
    mrc = safe_float(mrc)
    return round(safe_float(iline.get("mrc_pct")) * mrc, 2), round(mrc, 2), src


# ── config loading ────────────────────────────────────────────────────────────────────────────────
def _load_schedules(client, org_id):
    """(schedules, lines_by_schedule_id). Empty if migration 201 isn't applied yet."""
    try:
        scheds = (client.schema("commcalc").table("plan_installment_schedule").select("*")
                  .eq("org_id", org_id).eq("is_active", True).execute().data) or []
        lines = (client.schema("commcalc").table("plan_installment_line").select("*")
                 .eq("org_id", org_id).execute().data) or []
    except Exception:
        return [], {}
    by_sched = {}
    for ln in lines:
        by_sched.setdefault(ln.get("schedule_id"), []).append(ln)
    return scheds, by_sched


def _load_ccmap(client, org_id):
    try:
        return (client.schema("commcalc").table("carrier_category_map").select("*")
                .eq("org_id", org_id).eq("is_active", True).execute().data) or []
    except Exception:
        return []


def _acc_sets(client, org_id):
    """The mig-092 accessory config as lowercased sets (mirrors router._accessory_config, but self-
    contained so the engine has no router import cycle). Falls back to the 'Ondigo' default."""
    depts, cats, kws = [], [], []
    try:
        rows = (client.schema("commcalc").table("flag_rules")
                .select("accessory_departments,accessory_categories,accessory_product_keywords")
                .eq("org_id", org_id).eq("id", 1).limit(1).execute().data) or []
        if rows:
            depts = [d for d in (rows[0].get("accessory_departments") or []) if d]
            cats = [c for c in (rows[0].get("accessory_categories") or []) if c]
            kws = [k for k in (rows[0].get("accessory_product_keywords") or []) if k]
    except Exception:
        pass
    if not depts and not cats and not kws:
        depts = ["Ondigo"]
    return {"departments": {d.strip().lower() for d in depts},
            "categories": {c.strip().lower() for c in cats},
            "products": {k.strip().lower() for k in kws}}


# ── main compute ────────────────────────────────────────────────────────────────────────────────
def compute_sale_installments(client, org_id, pay_period, persist=False):
    """Sale-triggered installments that LAND in `pay_period`. Read-only unless persist=True.
    Returns {pay_period, by_rep:{REPUPPER:amount}, ledger:[...], flags:[...], totals, schedules, note}.

    A qualifying sold line in period S schedules a payout for month_index = (P - S) + 1 (1..N). The
    line pays only if it is inside the schedule's user-defined effective window AND the paid gate is met
    for pay_period P. A gated-off (withheld) line emits the two flags."""
    scheds, lines_by = _load_schedules(client, org_id)
    if not scheds:
        return {"pay_period": pay_period, "by_rep": {}, "ledger": [], "flags": [], "schedules": 0,
                "totals": {"amount": 0.0, "paid": 0, "withheld": 0, "reps": 0},
                "note": "No sale-triggered installment schedules (or migration 201 not applied)."}

    plans, _ready = _load_plans(client, org_id)
    plans_by_id = {p.get("id"): p for p in plans}
    catalog = _load_product_mrc(client, org_id)
    ccmap = _load_ccmap(client, org_id)
    acc = _acc_sets(client, org_id)
    store_market = _read_store_market(client, org_id)

    pay_idx = _period_index(pay_period)
    if pay_idx is None:
        return {"pay_period": pay_period, "by_rep": {}, "ledger": [], "flags": [], "schedules": len(scheds),
                "totals": {"amount": 0.0, "paid": 0, "withheld": 0, "reps": 0},
                "note": f"Unparseable pay_period '{pay_period}'."}

    # horizon: pull sales for pay_period back through the deepest schedule's num_months.
    max_n = min(12, max((int(s.get("num_months") or 1) for s in scheds), default=1))
    sale_periods = [_shift_period(pay_period, -k) for k in range(0, max_n)]
    sale_periods = [p for p in sale_periods if p]

    # paid gate reads raw_mi for the PAY period only (is the line active/paying NOW).
    mi_index = _mi_index(_read_mi(client, org_id, pay_period))

    pm = parse_period(pay_period)
    by_rep, ledger, flags = {}, [], []
    n_paid = n_withheld = 0
    total_amt = 0.0

    for sale_period in sale_periods:
        s_idx = _period_index(sale_period)
        if s_idx is None:
            continue
        month_index = (pay_idx - s_idx) + 1
        if month_index < 1:
            continue
        sales = _read_sales(client, org_id, sale_period)
        valid = [r for r in sales
                 if str(r.get("voided", "") or "").upper().strip() != "YES"
                 and str(r.get("trans_type", "") or "").strip() != "Return"]
        for line in valid:
            rep = str(line.get("salesperson", "") or "").strip()
            if not rep or rep.lower() == "admin":
                continue
            store = str(line.get("store", "") or "").strip()
            market = store_market.get(store.lower()) or store_market.get(store.split(" ")[0].lower(), "")
            plan = _resolve_plan_for(rep, store, market, plans)
            if not plan:
                continue
            for sched in scheds:
                if sched.get("plan_id") != plan.get("id"):
                    continue
                num_months = min(12, int(sched.get("num_months") or 1))
                if month_index > num_months:
                    continue
                if not _rule_matches(line, {"match_field": sched.get("trigger_match_field"),
                                            "match_op": sched.get("trigger_match_op"),
                                            "match_value": sched.get("trigger_match_value")}):
                    continue
                if not _in_effective_window(line, sale_period, sched):
                    continue
                iline = next((l for l in lines_by.get(sched.get("id"), [])
                              if int(l.get("month_index") or 0) == month_index), None)
                if not iline:
                    continue
                carrier_id = plan.get("carrier_id")
                amount, mrc, mrc_src = _line_amount(line, iline, catalog, carrier_id)

                gate_mode = (sched.get("gate_mode") or "paid_residual").strip().lower()
                gate_from = int(sched.get("gate_from_month") or 1)
                gated = month_index >= gate_from and gate_mode != "none"
                gate_met, mi_row = _gate_met(line, mi_index, gate_mode) if gated else (True, None)

                repU = rep.upper()
                mdn = _norm_mdn(line.get("mdn"))
                serial = _norm_mdn(line.get("serial_1"))
                if gate_met:
                    if amount:
                        by_rep[repU] = round(by_rep.get(repU, 0.0) + amount, 2)
                        total_amt += amount
                    n_paid += 1
                    status = "paid"
                else:
                    n_withheld += 1
                    status = "withheld_unpaid"
                    # TWO flags for a sold-but-unpaid line (existing flags machinery; delete-first by source)
                    base = {"period": pay_period, "period_month": pm.get("month"),
                            "period_year": pm.get("year"), "epay_salesperson": rep,
                            "store_address": store, "mdn": mdn, "imei": serial,
                            "amount": round(safe_float(amount), 2)}
                    flags.append({**base, "flag_type": "INSTALLMENT_WITHHELD_UNPAID",
                                  "source": "commission_rebate_tracking", "severity": "MEDIUM",
                                  "description": (f"Month {month_index} installment ${safe_float(amount):,.2f} "
                                                  f"WITHHELD — line {mdn or serial} not active/receiving residual "
                                                  f"(dealer unpaid). Tracked; pays when residual resumes."),
                                  "coaching_note": "We pay as we get paid — recheck when the carrier residual posts."})
                    flags.append({**base, "flag_type": "SOLD_LINE_NOT_PAYING",
                                  "source": "employee_miss", "severity": "MEDIUM",
                                  "description": (f"{rep} sold {mdn or serial} but the line is not active/paying "
                                                  f"residual — month {month_index} commission gated."),
                                  "coaching_note": "Review activation quality / early deactivation for this line."})

                ledger.append({
                    "org_id": org_id, "trans_id": str(line.get("trans_id") or "").strip(),
                    "mdn": mdn, "serial_1": serial, "plan_id": plan.get("id"),
                    "schedule_id": sched.get("id"), "store": store, "epay_salesperson": rep,
                    "sale_period": sale_period, "pay_period": pay_period, "month_index": month_index,
                    "payout_kind": iline.get("payout_kind"), "mrc_at_pay": mrc, "mrc_source": mrc_src,
                    "amount": round(safe_float(amount) if gate_met else 0.0, 2),
                    "paid_gate_met": gate_met, "gate_mode": gate_mode, "status": status,
                    "matched_mi_period": pay_period if mi_row is not None else None,
                })

    if persist:
        _persist(client, org_id, pay_period, ledger)

    ledger.sort(key=lambda x: -(x.get("amount") or 0))
    return {"pay_period": pay_period, "by_rep": by_rep, "ledger": ledger, "flags": flags,
            "schedules": len(scheds),
            "totals": {"amount": round(total_amt, 2), "paid": n_paid, "withheld": n_withheld,
                       "reps": len(by_rep)},
            "note": None}


def _persist(client, org_id, pay_period, ledger):
    """Idempotent upsert of the sale_installment_ledger for this pay_period (opt-in)."""
    rows = [{k: d.get(k) for k in (
        "org_id", "trans_id", "mdn", "serial_1", "plan_id", "schedule_id", "store",
        "epay_salesperson", "sale_period", "pay_period", "month_index", "payout_kind",
        "mrc_at_pay", "mrc_source", "amount", "paid_gate_met", "gate_mode", "status",
        "matched_mi_period")} for d in ledger if d.get("trans_id") or d.get("mdn")]
    for i in range(0, len(rows), 500):
        try:
            client.schema("commcalc").table("sale_installment_ledger").upsert(
                rows[i:i + 500], on_conflict="org_id,trans_id,mdn,month_index,pay_period").execute()
        except Exception:
            pass
