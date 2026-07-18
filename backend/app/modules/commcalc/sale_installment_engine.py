"""Sale-triggered multi-month rep-pay engine — Commission-Plan payout type (migration 201).

DOCTRINE (owner, 2026-07-14; commission-0 §7b): rep multi-month commission is a COMMISSION-PLAN payout
type triggered by the SALE LINE (M1..N relative to the sale's trans_date), resolved per rep by plan
assignment — NOT by the carrier statement roster. The legacy raw_mi installment_engine (mig 057) STAYS,
demoted to dealer-revenue recon; THIS engine is the new sale-triggered path beside it.

PAID GATE (owner rev-B correction, §7b decision 2): months 1..N pay ONLY when, at calc time, the sold
line is ACTIVE and the dealer is receiving residual on that line — proven by raw_mi presence for that
line/period. "We pay as we get paid." A sold-but-unpaid line is WITHHELD ($0) and surfaced as TWO flags
(sources 'commission_rebate_tracking' + 'employee_miss'). Clawback is optional/off (schedule config).

MONTH-1 "PAID AT ACTIVATION" (owner directive 2026-07-16, mig 210): a schedule may set m1_gate=
'activation_payment' so month_index 1 qualifies when the ACTIVATION TRANSACTION ITSELF shows a first-month
payment collected at the register (configurable matcher — see DEFAULT_ACTIVATION_PAYMENT_MATCHER), INSTEAD
OF the raw_mi residual gate. Months 2..N keep the schedule's per-month gate. This is DISTINCT from
gate_from_month=2 ("month 1 always pays, ungated"): here month 1 IS gated, on the sale's own payment.
m1_gate='inherit' (default) is byte-identical to pre-mig-210.

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
    _read_employee_roles, _canon_person,
)

ORG_ID = "00000000-0000-0000-0000-000000000001"

# The line classifications the user chooses from (§7b decision 1). Carrier-agnostic; reuse the existing
# classifier CONFIG to auto-suggest — never a new sixth classifier.
CLASSIFICATIONS = ("accessory", "activation", "upgrade", "swap", "bill_payment", "rebate", "misc_other")

# ── MONTH-1 "PAID AT ACTIVATION" GATE (mig 210) ─────────────────────────────────────────────────────
# Owner directive 2026-07-16: month_index 1 may qualify when the ACTIVATION TRANSACTION ITSELF shows a
# first-month payment collected at the register — INSTEAD OF the raw_mi carrier-residual gate. Months
# 2..N keep the schedule's existing per-month gate. This is DISTINCT from gate_from_month=2 ("month 1
# always pays, ungated") — here month 1 IS gated, on the sale's own payment.
#
# WHAT COUNTS is CONFIGURABLE per org (RULE TWO — no hard-coded product/category/carrier). Default
# studied against the luxelink B2BSoft "Sales Transaction Details" sample: the customer's first-month
# payment surfaces as System "Access Charge"/"Wallet Funding" lines and Rtr "Other Carr. payments"
# plan/airtime lines, each carrying a NONZERO Ext Price (= the amount actually rung). The device line
# (BrandedHandset) and the bookkeeping "Activation payment" line (Ext Price ALWAYS 0; negative GP = the
# DEALER's activation cost, not customer money) are deliberately NOT the signal. Ext Price is the default
# value field because it is the amount charged to the customer; GP is dealer profit and Unit Price is a
# list price present even when nothing was rung.
DEFAULT_ACTIVATION_PAYMENT_MATCHER = {
    "departments": ["system", "rtr"],
    "categories": ["system", "other carr. payments"],
    "product_keywords": ["access charge", "wallet funding", "airtime", "recharge", "refill",
                         "rtr", "plan", "first month", "monthly"],
    "value_field": "ext_price",   # the numeric column that proves money was collected
    "min_amount": 0.01,           # the line must charge at least this (excludes the $0 bookkeeping line)
}

# The DUAL-CATEGORY item_mapping (mig 210) is the AUTHORITATIVE matcher when configured: an item mapped
# (in commcalc.item_mapping) to sales_category OR kpi_category == 'activation_payment', with money
# collected, IS a first-month payment. The heuristic matcher above is the seeded FALLBACK used until the
# org maps items to that category. "The mapping is the matcher, with a seeded default." (owner 2026-07-16)
ACTIVATION_PAYMENT_CATEGORY = "activation_payment"


def _norm_matcher(m):
    """Normalize a stored/default matcher into lowercased sets + a value field + a min amount. PURE."""
    m = m or {}
    return {
        "departments": {str(x).strip().lower() for x in (m.get("departments") or []) if str(x).strip()},
        "categories": {str(x).strip().lower() for x in (m.get("categories") or []) if str(x).strip()},
        "product_keywords": {str(x).strip().lower() for x in (m.get("product_keywords") or []) if str(x).strip()},
        "value_field": (str(m.get("value_field") or "ext_price").strip().lower() or "ext_price"),
        "min_amount": safe_float(m.get("min_amount")) if m.get("min_amount") is not None else 0.01,
    }


def _load_activation_matcher(client, org_id):
    """The org's activation-payment matcher (commission_org_config.activation_payment_matcher), falling
    back to DEFAULT_ACTIVATION_PAYMENT_MATCHER when unset or when mig 210 isn't applied. Returns a
    NORMALIZED matcher (sets). Degrades to the default on any error — never raises."""
    stored = None
    try:
        rows = (client.schema("commcalc").table("commission_org_config")
                .select("activation_payment_matcher").eq("org_id", org_id).limit(1).execute().data) or []
        if rows:
            stored = rows[0].get("activation_payment_matcher")
    except Exception:
        stored = None
    return _norm_matcher(stored or DEFAULT_ACTIVATION_PAYMENT_MATCHER)


def _item_key(sku, desc):
    """Mirror of router._item_key: SKU if present, else upper(trim(desc)). The item_mapping join key."""
    s = str(sku or "").strip()
    if s and s.lower() not in ("nan", "none", "0", "0.0"):
        return s.upper()[:200]
    return str(desc or "").strip().upper()[:200]


def _load_item_map(client, org_id):
    """{item_key -> row} of the org's item_mapping (with the mig-210 sales_category/kpi_category). Empty
    dict if migration 041/210 isn't applied. Self-contained (no router import)."""
    try:
        rows = (client.schema("commcalc").table("item_mapping").select("*")
                .eq("org_id", org_id).limit(100000).execute().data) or []
        return {r["item_key"]: r for r in rows if r.get("item_key")}
    except Exception:
        return {}


def _line_value_ok(row, matcher):
    """The 'money collected' gate: the line's value_field is at least min_amount. PURE."""
    return safe_float(row.get(matcher.get("value_field") or "ext_price")) >= safe_float(matcher.get("min_amount"))


def _line_class_matches(row, matcher):
    """HEURISTIC classification only (department OR category OR product-desc keyword) — the seeded fallback
    'is this a payment/plan/airtime line'. Value gate applied separately. PURE (matcher = normalized sets)."""
    dept = str(row.get("department", "") or "").strip().lower()
    cat = str(row.get("category", "") or "").strip().lower()
    prod = str(row.get("product_desc", "") or "").strip().lower()
    kws = matcher.get("product_keywords") or set()
    return ((dept and dept in (matcher.get("departments") or set()))
            or (cat and cat in (matcher.get("categories") or set()))
            or (bool(kws) and prod and any(k in prod for k in kws)))


def _line_mapped_ap(row, item_map, ap_key):
    """True if the line's item is mapped (item_mapping) to the activation-payment category in EITHER
    dimension (sales_category or kpi_category). PURE — item_map prebuilt {item_key -> row}."""
    m = item_map.get(_item_key(row.get("sku"), row.get("product_desc") or row.get("item_desc")))
    if not m:
        return False
    return (str(m.get("sales_category") or "").strip().lower() == ap_key
            or str(m.get("kpi_category") or "").strip().lower() == ap_key)


def _line_is_activation_payment(row, matcher):
    """True if ONE sale line is a first-month PAYMENT line via the HEURISTIC matcher (class + value). Kept
    for the seeded-default path + unit tests. PURE."""
    return _line_class_matches(row, matcher) and _line_value_ok(row, matcher)


def _build_trans_index(sales):
    """{trans_id -> [all sale lines on that transaction]} so the activation-payment gate can inspect the
    WHOLE transaction (the payment/System lines, not just the triggering line). PURE."""
    idx = {}
    for r in sales:
        tid = str(r.get("trans_id") or "").strip()
        if tid:
            idx.setdefault(tid, []).append(r)
    return idx


def _activation_payment_met(trans_id, trans_index, matcher, item_map=None, ap_key=None, has_ap_mappings=False):
    """True if the activation transaction shows any qualifying first-month payment line.
    AUTHORITATIVE when the org has mapped items to the activation-payment category (has_ap_mappings): a
    line qualifies iff its item is mapped to `ap_key` (either dimension) AND money was collected.
    Otherwise falls back to the seeded HEURISTIC matcher (class + value). PURE."""
    if not trans_id:
        return False
    for r in (trans_index.get(trans_id) or []):
        if not _line_value_ok(r, matcher):
            continue
        if has_ap_mappings:
            if _line_mapped_ap(r, item_map or {}, ap_key or ACTIVATION_PAYMENT_CATEGORY):
                return True
        else:
            if _line_class_matches(r, matcher):
                return True
    return False


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


# ── CONFIG-DRIVEN PAID-GATE EVIDENCE SOURCE (mig 223; RULE TWO) ──────────────────────────────────────
# The gate above proves "dealer paid this month" ONLY from raw_mi — a Boost/ePay-only table. Master-agent-
# fed tenants (Total Wireless via VidaPay; data in raw_ma_* from mig 083) have EMPTY raw_mi, so every gated
# month is withheld_unpaid forever. This block resolves the gate's evidence SOURCE per (org, carrier) from
# config (installment_gate_source_config, mig 223) — mirroring whatif.py's mig-209 dispatch exactly — so:
#   • boost mode → 'boost_mi'      → the raw_mi _gate_met above (BYTE-IDENTICAL to pre-mig-223).
#   • plan  mode → 'ma_commission' → raw_ma_commission per-IMEI per-month spiffs (the fix).
# The code defaults below make Boost resolve to raw_mi with ZERO owner action even if mig 223 is unrun.
_NIL_CARRIER = "00000000-0000-0000-0000-000000000000"
_HOUSE_ORG = ORG_ID

_GATE_CFG_DEFAULTS = {
    "boost": {"gate_source": "boost_mi", "ma_device_fields": ["imei", "sim"],
              "ma_month_field_prefix": "spiff_m", "ma_max_month": 6,
              "ma_month1_extra_fields": ["rebate", "device_margin"], "ma_min_amount": 0.01},
    "plan":  {"gate_source": "ma_commission", "ma_device_fields": ["imei", "sim"],
              "ma_month_field_prefix": "spiff_m", "ma_max_month": 6,
              "ma_month1_extra_fields": ["rebate", "device_margin"], "ma_min_amount": 0.01},
}
_GATE_CFG_KEYS = ("gate_source", "ma_device_fields", "ma_month_field_prefix", "ma_max_month",
                  "ma_month1_extra_fields", "ma_min_amount")

# raw_ma_commission numeric payout columns aggregated into the gate index (per-month spiffs + the
# activation-time payouts that count for month 1). Kept as a fixed superset so ONE index serves every
# carrier's config; _gate_met_ma picks which of these matter for a given month/config.
_MA_NUMERIC_COLS = ("spiff_m1", "spiff_m2", "spiff_m3", "spiff_m4", "spiff_m5", "spiff_m6",
                    "rebate", "device_margin", "consumer_margin", "mrc_net_discount")
_MA_DEVICE_COLS = ("imei", "sim")


def _norm_imei(v):
    """Digit-normalize a device serial/IMEI for the MA join. Strips an Excel-float trailing '.0' first
    (so '355163568356973.0' → '355163568356973'), then keeps digits only. UNLIKE _norm_mdn this does NOT
    truncate to the last 10 — IMEIs are 15 digits and must compare in full. PURE."""
    s = str(v or "").strip()
    if s.endswith(".0"):
        s = s[:-2]
    return "".join(ch for ch in s if ch.isdigit())


def _carrier_mode_map(client, org_id):
    """({carrier_id -> 'boost'|'plan'}, default_mode). Uses the router's SINGLE-SOURCE carrier-mode
    resolver (lazy import + the same fallback whatif.py uses) so this never duplicates carrier-name logic.
    A plan's carrier_id decides which evidence source its gate uses; a NULL-carrier plan uses default_mode."""
    try:
        carriers = (client.schema("commcalc").table("carrier").select("id,name,code,is_default")
                    .eq("org_id", org_id).execute().data) or []
    except Exception:
        carriers = []
    try:
        from app.modules.commcalc.router import _resolve_carrier_mode
    except Exception:
        def _resolve_carrier_mode(cs):
            def _is_boost(c):
                return 'boost' in ((c.get('code') or '') + ' ' + (c.get('name') or '')).lower()
            cs = cs or []
            if not cs:
                return 'boost'
            d = next((c for c in cs if c.get('is_default')), None)
            if d is not None:
                return 'boost' if _is_boost(d) else 'plan'
            return 'boost' if any(_is_boost(c) for c in cs) else 'plan'
    mode_by_id = {c.get("id"): _resolve_carrier_mode([c]) for c in carriers}
    return mode_by_id, _resolve_carrier_mode(carriers)


def _load_gate_source_rows(client, org_id):
    """(org_rows, house_rows) from installment_gate_source_config (mig 223). Empty lists if the table is
    absent — the resolver then falls back to the per-mode code defaults (Boost byte-identical)."""
    def _read(oid):
        try:
            return (client.schema("commcalc").table("installment_gate_source_config").select("*")
                    .eq("org_id", oid).eq("is_active", True).execute().data) or []
        except Exception:
            return []
    org_rows = _read(org_id)
    house_rows = org_rows if org_id == _HOUSE_ORG else _read(_HOUSE_ORG)
    return org_rows, house_rows


def _resolve_gate_cfg(org_rows, house_rows, carrier_id, mode):
    """Resolve the gate evidence config for (carrier_id, mode). Order: org-carrier → org-mode-default →
    house-mode-default → per-mode code default. PURE (rows passed in). Never raises."""
    mode = mode if mode in _GATE_CFG_DEFAULTS else "boost"
    base = dict(_GATE_CFG_DEFAULTS[mode])
    base["_resolved_from"] = "code_default"
    cid = str(carrier_id) if carrier_id else None
    chosen, src = None, None
    if cid:
        chosen = next((r for r in org_rows if str(r.get("carrier_id")) == cid
                       and str(r.get("carrier_id")) != _NIL_CARRIER), None)
        if chosen:
            src = "org_carrier"
    if chosen is None:
        chosen = next((r for r in org_rows if str(r.get("carrier_id")) == _NIL_CARRIER
                       and (r.get("carrier_mode") or "boost") == mode), None)
        if chosen:
            src = "org_mode_default"
    if chosen is None:
        chosen = next((r for r in house_rows if str(r.get("carrier_id")) == _NIL_CARRIER
                       and (r.get("carrier_mode") or "boost") == mode), None)
        if chosen:
            src = "house_mode_default"
    if chosen is not None:
        merged = dict(base)
        for k in _GATE_CFG_KEYS:
            v = chosen.get(k)
            if v is not None and v != "" and v != []:
                merged[k] = v
        merged["_resolved_from"] = src
        return merged
    return base


def _read_ma_commission(client, org_id, period):
    """Paginated raw_ma_commission for one period (select * so a missing optional column never errors).
    'June 2026' vs '2026-06' handled via _pvariants. [] if the table is absent (mig 083 unrun)."""
    out, start, page = [], 0, 1000
    while True:
        try:
            rows = (client.schema("commcalc").table("raw_ma_commission").select("*")
                    .eq("org_id", org_id).in_("period", _pvariants(period))
                    .range(start, start + page - 1).execute().data) or []
        except Exception:
            break
        out.extend(rows)
        if len(rows) < page:
            break
        start += page
    return out


def _ma_gate_index(ma_rows):
    """{norm_device_key -> {numeric_col -> NET summed amount}} for one period's raw_ma_commission. A row
    contributes under BOTH its normalized imei and sim keys; MULTIPLE rows per device/period (base +
    adjustment) are SUMMED per column so a clawback nets out (the owner's repro: one IMEI, two rows). PURE."""
    idx = {}
    for r in ma_rows:
        keys = set()
        for f in _MA_DEVICE_COLS:
            k = _norm_imei(r.get(f))
            if k:
                keys.add(k)
        if not keys:
            continue
        for k in keys:
            agg = idx.setdefault(k, {})
            for c in _MA_NUMERIC_COLS:
                agg[c] = safe_float(agg.get(c)) + safe_float(r.get(c))
    return idx


def _gate_met_ma(sale_line, ma_index, month_index, cfg):
    """(met, evidence): does the sold line qualify to be paid in month `month_index` per the MASTER-AGENT
    statement? Rule (documented in the commit): match the sold device's serial (raw_sales.serial_1, digit-
    normalized) to a raw_ma_commission device in the SALE period; month N is PAID iff at least one of month
    N's evidence columns has |NET amount| >= min (net = summed across the device's base+adjustment rows;
    sign-agnostic because MA amounts are negative = payout to dealer). Month N's evidence column is
    '{prefix}{N}' (spiff_mN); month 1 ALSO counts the configured activation-time payouts (rebate,
    device_margin) so a plan that pays no M1 spiff but a rebate still qualifies. line_status is deliberately
    NOT keyed on (NULL in real rows) — a posted payout IS the proof the line is active + paying. PURE."""
    cand = []
    for f in ("serial_1", "imei"):
        k = _norm_imei(sale_line.get(f))
        if k:
            cand.append(k)
    agg = next((ma_index[k] for k in cand if k in ma_index), None)
    if agg is None:
        return False, {"matched": False, "reason": "no_ma_record"}
    prefix = (cfg.get("ma_month_field_prefix") or "spiff_m")
    max_month = int(cfg.get("ma_max_month") or 6)
    min_amt = safe_float(cfg.get("ma_min_amount")) if cfg.get("ma_min_amount") is not None else 0.01
    if month_index > max_month:
        return False, {"matched": True, "reason": "month_beyond_ma_columns", "max_month": max_month}
    cols = [f"{prefix}{month_index}"]
    if month_index == 1:
        cols += [str(c).strip() for c in (cfg.get("ma_month1_extra_fields") or []) if str(c).strip()]
    per_col, paid = {}, False
    for c in cols:
        net = round(safe_float(agg.get(c)), 2)
        per_col[c] = net
        if abs(net) >= min_amt:
            paid = True
    return paid, {"matched": True, "reason": ("paid" if paid else "no_month_payout"), "evidence": per_col}


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
def compute_sale_installments(client, org_id, pay_period, persist=False, _gate_source_override=None):
    """Sale-triggered installments that LAND in `pay_period`. Read-only unless persist=True.
    Returns {pay_period, by_rep:{REPUPPER:amount}, ledger:[...], flags:[...], totals, schedules, note}.

    A qualifying sold line in period S schedules a payout for month_index = (P - S) + 1 (1..N). The
    line pays only if it is inside the schedule's user-defined effective window AND the paid gate is met
    for pay_period P. A gated-off (withheld) line emits the two flags.

    The paid gate's EVIDENCE SOURCE is config-driven per carrier (mig 223): Boost carriers prove paid from
    raw_mi (byte-identical to pre-mig-223); master-agent-fed carriers prove paid from raw_ma_commission
    per-month spiffs. `_gate_source_override` forces every gate to that source (used by preview_gate_impact
    to diff new-vs-legacy) — never set it in the live pay path."""
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
    role_by_rep = _read_employee_roles(client, org_id)   # {_canon_person(name) -> role} for scope='role'

    # PAID-GATE EVIDENCE SOURCE per carrier (mig 223; RULE TWO). Resolve once, cache per carrier_id. A Boost
    # carrier → 'boost_mi' (unchanged raw_mi gate); a master-agent carrier → 'ma_commission'. This is the
    # ONLY new dispatch — Boost carriers never enter the MA branch, so their gate outcomes stay byte-identical.
    carrier_mode_by_id, default_mode = _carrier_mode_map(client, org_id)
    gate_org_rows, gate_house_rows = _load_gate_source_rows(client, org_id)
    _gate_cfg_cache, _ma_index_cache = {}, {}

    def _gate_cfg_for(carrier_id):
        ck = str(carrier_id) if carrier_id else "__default__"
        if ck not in _gate_cfg_cache:
            mode = (carrier_mode_by_id.get(carrier_id) if carrier_id else default_mode) or default_mode
            cfg = _resolve_gate_cfg(gate_org_rows, gate_house_rows, carrier_id, mode)
            if _gate_source_override:
                cfg = dict(cfg)
                cfg["gate_source"] = _gate_source_override
            _gate_cfg_cache[ck] = cfg
        return _gate_cfg_cache[ck]

    def _ma_index_for(period):
        if period not in _ma_index_cache:
            _ma_index_cache[period] = _ma_gate_index(_read_ma_commission(client, org_id, period))
        return _ma_index_cache[period]

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

    # MONTH-1 "paid at activation" gate (mig 210): only prep the matcher when at least one schedule opts
    # in — so a schedule that doesn't opt in is byte-identical to pre-mig-210 (no extra reads, no new
    # ledger fields). act_matcher is normalized (sets); trans_index is built per sale_period on demand.
    any_activation = any((str(s.get("m1_gate") or "inherit").strip().lower()) == "activation_payment" for s in scheds)
    act_matcher = _load_activation_matcher(client, org_id) if any_activation else None
    # DUAL-CATEGORY item mapping (mig 210): when the org has mapped any item to the activation-payment
    # category, the mapping is AUTHORITATIVE; else the seeded heuristic matcher is the fallback.
    ap_item_map = _load_item_map(client, org_id) if any_activation else {}
    has_ap_mappings = any(
        (str(v.get("sales_category") or "").strip().lower() == ACTIVATION_PAYMENT_CATEGORY
         or str(v.get("kpi_category") or "").strip().lower() == ACTIVATION_PAYMENT_CATEGORY)
        for v in ap_item_map.values()) if any_activation else False

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
        # month_index 1 <=> sale_period == pay_period (k=0), so the activation-payment gate only ever
        # needs the trans index for that period. Built from the FULL read (payment/System lines included).
        trans_index = _build_trans_index(sales) if (any_activation and sale_period == pay_period) else None
        valid = [r for r in sales
                 if str(r.get("voided", "") or "").upper().strip() != "YES"
                 and str(r.get("trans_type", "") or "").strip() != "Return"]
        for line in valid:
            rep = str(line.get("salesperson", "") or "").strip()
            if not rep or rep.lower() == "admin":
                continue
            store = str(line.get("store", "") or "").strip()
            market = store_market.get(store.lower()) or store_market.get(store.split(" ")[0].lower(), "")
            plan = _resolve_plan_for(rep, store, market, plans, rep_role=role_by_rep.get(_canon_person(rep)))
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
                m1_gate = (str(sched.get("m1_gate") or "inherit").strip().lower())
                gate_cfg = _gate_cfg_for(carrier_id)
                gate_source = (gate_cfg.get("gate_source") or "boost_mi")
                mi_row, ma_ev = None, None
                # MONTH-1 "paid at activation" (mig 210): month 1 qualifies when the ACTIVATION TRANSACTION
                # shows a first-month payment (configurable matcher), NOT via a carrier statement. Months
                # 2..N keep the schedule's existing gate. Only opted-in rows carry the extra ledger fields,
                # so an 'inherit' schedule is byte-identical to pre-mig-210. m1_gate wins over gate_from_month
                # for month 1 (the owner wants month 1 GATED on the sale's own payment, not ungated).
                if month_index == 1 and m1_gate == "activation_payment":
                    gate_met = _activation_payment_met(str(line.get("trans_id") or "").strip(),
                                                       trans_index or {}, act_matcher or {},
                                                       ap_item_map, ACTIVATION_PAYMENT_CATEGORY, has_ap_mappings)
                    gate_kind = "activation_payment"
                elif gate_source == "ma_commission":
                    # MASTER-AGENT paid gate (mig 223): prove "dealer paid month N" from raw_ma_commission
                    # (per-IMEI per-month spiffs) instead of raw_mi. Read the SALE (activation) period — that
                    # row carries the device's forward M1-M6 schedule (owner repro: the June row holds both
                    # spiff_m1 and spiff_m2). This branch is NEVER reached for a Boost carrier.
                    gated = month_index >= gate_from and gate_mode != "none"
                    if gated:
                        gate_met, ma_ev = _gate_met_ma(line, _ma_index_for(sale_period), month_index, gate_cfg)
                    else:
                        gate_met = True
                    gate_kind = "ma_residual"
                else:
                    # BOOST / raw_mi paid gate — UNCHANGED (byte-identical to pre-mig-223).
                    gated = month_index >= gate_from and gate_mode != "none"
                    gate_met, mi_row = _gate_met(line, mi_index, gate_mode) if gated else (True, None)
                    gate_kind = None

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
                    # TWO flags for a sold-but-unpaid line (existing flags machinery; delete-first by source).
                    # Under the month-1 activation-payment gate the miss reason is "no first-month payment
                    # collected at activation" rather than "not receiving residual" — same two flag sources.
                    base = {"period": pay_period, "period_month": pm.get("month"),
                            "period_year": pm.get("year"), "epay_salesperson": rep,
                            "store_address": store, "mdn": mdn, "imei": serial,
                            "amount": round(safe_float(amount), 2)}
                    if gate_kind == "activation_payment":
                        desc1 = (f"Month {month_index} installment ${safe_float(amount):,.2f} WITHHELD — no "
                                 f"qualifying first-month payment collected at activation for trans "
                                 f"{str(line.get('trans_id') or '').strip() or (mdn or serial)}.")
                        coach1 = ("Month 1 pays only when payment is received at activation — verify the "
                                  "first-month / plan / access-charge payment was rung on this sale.")
                        desc2 = (f"{rep} sold {mdn or serial} but no activation payment was collected — "
                                 f"month {month_index} commission gated.")
                        coach2 = "Confirm the customer paid their first month at the point of sale."
                    elif gate_kind == "ma_residual":
                        # MASTER-AGENT gate miss — say EXACTLY why (no MA record / no month-N payout / beyond
                        # the MA month columns) rather than the raw_mi "not receiving residual" text.
                        reason = (ma_ev or {}).get("reason")
                        if reason == "no_ma_record":
                            why = (f"no master-agent commission record found for device {serial or mdn} in "
                                   f"{sale_period}")
                        elif reason == "month_beyond_ma_columns":
                            why = (f"master-agent data covers months 1-{(ma_ev or {}).get('max_month')}; there "
                                   f"is no month-{month_index} payout column")
                        else:
                            why = (f"no master-agent month-{month_index} payout posted for device "
                                   f"{serial or mdn}")
                        desc1 = (f"Month {month_index} installment ${safe_float(amount):,.2f} WITHHELD — {why} "
                                 f"(dealer not yet paid). Tracked; pays when the MA statement posts the payout.")
                        coach1 = ("We pay as we get paid — recheck when the master-agent commission posts this "
                                  "month's spiff/residual for the line.")
                        desc2 = (f"{rep} sold {mdn or serial} but the master-agent statement shows no "
                                 f"month-{month_index} payout — commission gated.")
                        coach2 = "Verify the line stayed active and the carrier posted this month's residual/spiff."
                    else:
                        desc1 = (f"Month {month_index} installment ${safe_float(amount):,.2f} WITHHELD — line "
                                 f"{mdn or serial} not active/receiving residual (dealer unpaid). Tracked; "
                                 f"pays when residual resumes.")
                        coach1 = "We pay as we get paid — recheck when the carrier residual posts."
                        desc2 = (f"{rep} sold {mdn or serial} but the line is not active/paying residual — "
                                 f"month {month_index} commission gated.")
                        coach2 = "Review activation quality / early deactivation for this line."
                    flags.append({**base, "flag_type": "INSTALLMENT_WITHHELD_UNPAID",
                                  "source": "commission_rebate_tracking", "severity": "MEDIUM",
                                  "description": desc1, "coaching_note": coach1})
                    flags.append({**base, "flag_type": "SOLD_LINE_NOT_PAYING",
                                  "source": "employee_miss", "severity": "MEDIUM",
                                  "description": desc2, "coaching_note": coach2})

                ledger_row = {
                    "org_id": org_id, "trans_id": str(line.get("trans_id") or "").strip(),
                    "mdn": mdn, "serial_1": serial, "plan_id": plan.get("id"),
                    "schedule_id": sched.get("id"), "store": store, "epay_salesperson": rep,
                    "sale_period": sale_period, "pay_period": pay_period, "month_index": month_index,
                    "payout_kind": iline.get("payout_kind"), "mrc_at_pay": mrc, "mrc_source": mrc_src,
                    "amount": round(safe_float(amount) if gate_met else 0.0, 2),
                    "paid_gate_met": gate_met, "gate_mode": gate_mode, "status": status,
                    "matched_mi_period": pay_period if mi_row is not None else None,
                }
                # OPT-IN ONLY: the month-1 activation-payment rows carry the extra provenance fields so the
                # preview can show WHY month 1 qualified. Rows on schedules that don't opt in stay byte-
                # identical to pre-mig-210 (no new keys → no persistence/shape drift).
                if gate_kind == "activation_payment":
                    ledger_row["gate_kind"] = "activation_payment"
                    ledger_row["activation_payment_matched"] = gate_met
                # MASTER-AGENT gate rows (mig 223) carry the per-month evidence + match flag so the preview
                # explains WHY it paid/withheld. Only the MA path adds these keys; the Boost/raw_mi path is
                # untouched (no new keys → byte-identical ledger shape for Boost).
                elif gate_kind == "ma_residual":
                    ledger_row["gate_kind"] = "ma_residual"
                    ledger_row["gate_source"] = gate_source
                    ledger_row["ma_matched"] = bool((ma_ev or {}).get("matched"))
                    ledger_row["ma_evidence"] = (ma_ev or {}).get("evidence")
                    ledger_row["ma_reason"] = (ma_ev or {}).get("reason")
                ledger.append(ledger_row)

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


# ── IMPACT PREVIEW (read-only; Gate-2 review artifact for mig 223) ──────────────────────────────────
def _flip_key(r):
    """Stable per-installment identity for diffing two gate runs."""
    return (r.get("sale_period"), r.get("month_index"), str(r.get("trans_id") or ""),
            str(r.get("mdn") or ""), str(r.get("serial_1") or ""))


def preview_gate_impact(client, org_id, pay_period):
    """READ-ONLY impact preview for the mig-223 gate change. Runs the installment preview TWICE — once with
    the new config-driven gate, once forced to the LEGACY raw_mi gate (_gate_source_override='boost_mi') —
    and reports every row that FLIPS withheld_unpaid → payable under the new gate, with per-rep + total
    dollars. For a Boost-mode org the two runs are byte-identical → zero flips (that IS the safety proof).
    Writes NOTHING (both runs persist=False). Never triggers a real calculate/persist."""
    new = compute_sale_installments(client, org_id, pay_period, persist=False)
    old = compute_sale_installments(client, org_id, pay_period, persist=False, _gate_source_override="boost_mi")
    old_status = {_flip_key(r): (r.get("status"), safe_float(r.get("amount"))) for r in old.get("ledger", [])}

    flips, regressions = [], []
    by_rep, total = {}, 0.0
    for r in new.get("ledger", []):
        k = _flip_key(r)
        os_status, _os_amt = old_status.get(k, (None, 0.0))
        new_status = r.get("status")
        if new_status == "paid" and os_status == "withheld_unpaid":
            amt = round(safe_float(r.get("amount")), 2)
            rep = r.get("epay_salesperson") or ""
            flips.append({"rep": rep, "store": r.get("store"), "sale_period": r.get("sale_period"),
                          "pay_period": pay_period, "month_index": r.get("month_index"),
                          "imei": r.get("serial_1"), "mdn": r.get("mdn"), "amount": amt,
                          "gate_source": r.get("gate_source"), "ma_evidence": r.get("ma_evidence")})
            by_rep[rep] = round(by_rep.get(rep, 0.0) + amt, 2)
            total += amt
        elif new_status == "withheld_unpaid" and os_status == "paid":
            # Should NEVER happen — the fix only OPENS MA gates; a close-direction flip is a red flag.
            regressions.append({"rep": r.get("epay_salesperson"), "sale_period": r.get("sale_period"),
                                "month_index": r.get("month_index"), "imei": r.get("serial_1"),
                                "amount": round(safe_float(r.get("amount")), 2)})

    flips.sort(key=lambda x: -x["amount"])
    return {
        "org_id": org_id, "pay_period": pay_period,
        "boost_safe": len(flips) == 0 and len(regressions) == 0,   # true for Boost-mode orgs
        "flips_to_payable": flips,
        "flip_count": len(flips),
        "regressions_to_withheld": regressions,   # MUST be empty
        "by_rep": by_rep,
        "total_newly_payable": round(total, 2),
        "new_totals": new.get("totals"),
        "legacy_totals": old.get("totals"),
        "note": ("No installment schedules (or migration 201 not applied)." if not new.get("schedules")
                 else None),
    }
