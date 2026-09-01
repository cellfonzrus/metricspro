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
import os
import re
import calendar
from datetime import date

from app.modules.commcalc.calculator import parse_period, safe_float
# ONE shared voided token set for pay + display (owner 2026-07-25) — see gp_report.VOID_TOKENS.
from app.modules.commcalc.gp_report import is_voided as _is_voided, VOID_TOKENS as _VOID_TOKENS
from app.modules.commcalc.installment_engine import (
    _pvariants, _period_index, _shift_period, _load_product_mrc, _catalog_mrc, _read_mi,
)
from app.modules.commcalc.commission_engine import (
    _load_plans, _resolve_plan_for, _read_sales, _read_store_market, _rule_matches, _norm_mdn,
    _read_employee_roles, _canon_person,
)
from app.modules.commcalc import installment_category as icat
from app.modules.commcalc import installment_category_payout as icpay
from app.modules.commcalc import expected_commission as xcomm
# THE "MONTH n" parser (mig 308 / MA TX): the ONE regex that reads 'TBV MONTH 5 New Activation SPF'
# → 5 already lives in the Commission Ledger. REUSED, never re-implemented, so the ledger's month
# attribution and the installment gate can never drift apart on wording.
from app.modules.commcalc.commission_ledger import parse_payment_month

ORG_ID = "00000000-0000-0000-0000-000000000001"

# The SCHEMA ceiling for a multi-month schedule (mig 308 CHECK 1..16 on plan_installment_schedule
# .num_months). The ACTUAL horizon is always config — the schedule's own num_months (and, for MA
# gates, installment_gate_source_config.ma_max_month) — this constant only clamps a mis-entered row
# to what the database itself allows. Was 12 (a comment-only cap in mig 201) before the MA TX
# "MONTH 2..16" payout wording (owner spec 2026-09-01) required months 13..16.
MAX_SCHEDULE_MONTHS = 16

# ── READ-ONLY SIMULATION KEY ────────────────────────────────────────────────────────────────────────
# The employee pay simulator ("what would I make?") has to ask this engine what a sale it has NOT MADE
# YET would pay. It hands compute_sale_installments a `_sales_override` of synthetic activations, each
# carrying its own typed rate-plan MRC under this key — because a line that does not exist has no
# product-catalog row and no description to extract an MRC from, and the engine would otherwise
# correctly resolve it to $0 (rank 4, 'none') and the rep would be told the residual is worth nothing.
# NOTHING that comes out of raw_sales carries this key, so every real activation resolves exactly as it
# does today. persist=True with an override is REFUSED below: a simulated line must never reach the
# ledger.
SIM_MRC_KEY = "_sim_mrc"

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


_DURATION_RX = re.compile(r"^\s*[0-9]{1,3}\s*(?:mo\b|mo\.|month)", re.I)


def _is_duration_token(s, m):
    """True when the '<n> month' the monthly matcher just found is a DURATION, not a price.

    OWNER TABLET REPRO 2026-07-27: "… - Promo $279.99, 6 Month Plan Required" made extract_mrc_monthly
    return **6.00** — the promo's term length read as a monthly charge, which then outranked the real
    rate-plan line (rank 1 beats rank 2). A token is a duration when it carries NEITHER a '$' NOR a '/'
    (so "$25/mo" and "25/mo" are untouched), is a bare integer glued to a month noun, AND the text
    states a real $ amount somewhere else — i.e. the sentence already said what the money is. PURE."""
    tok = s[m.start():m.end()]
    if "$" in tok or "/" in tok:
        return False
    if not _DURATION_RX.match(tok):
        return False
    return bool(_ANY_DOLLAR.search(s[:m.start()] + s[m.end():]))


def extract_mrc_monthly(desc):
    """The MONTHLY-KEYWORD-ANCHORED half of extract_mrc_from_desc: a $ amount that the text itself calls
    a recurring/monthly charge ("$25/mo", "MRC $30", "$50 per month"). This is a STRUCTURAL rate-plan
    signal — a hardware price is never written this way — so it is trusted on ANY line. None if absent.
    A bare "<n> month" TERM LENGTH is rejected (see _is_duration_token). PURE (no I/O)."""
    s = "" if desc is None else str(desc)
    if not s.strip():
        return None
    for rx in (_MONTHLY_AFTER, _MONTHLY_BEFORE):
        for m in rx.finditer(s):
            v = _to_f(m.group(1))
            if not (v and v > 0):
                continue
            if rx is _MONTHLY_AFTER and _is_duration_token(s, m):
                continue
            return round(v, 2)
    return None


def extract_mrc_bare(desc):
    """The LAST-RESORT half of extract_mrc_from_desc: any bare $-prefixed amount ("Unlimited $50").

    ⚠ THIS IS THE ONE THAT CAN TURN A DEVICE PRICE INTO AN "MRC" (owner repro 2026-07-25: the device
    line "… $575.00" produced MRC 575 and paid a 5% installment on it). It is therefore only consulted
    for a line that IDENTIFIES AS A RATE-PLAN LINE (_line_is_plan_line) when the engine resolves an
    installment's MRC — see _mrc_candidate. Kept public + unbounded for the display-only MRC-mapping
    prefill, which a human confirms. PURE (no I/O)."""
    s = "" if desc is None else str(desc)
    if not s.strip():
        return None
    m = _ANY_DOLLAR.search(s)
    if m:
        v = _to_f(m.group(1))
        if v and v > 0:
            return round(v, 2)
    return None


_PLAN_ANCHOR_WINDOW = 48


def extract_mrc_bare_anchored(desc, kws=None):
    """extract_mrc_bare, except that when a description carries SEVERAL $ amounts, the one CLOSEST to a
    rate-plan word wins instead of the first one.

    OWNER TABLET REPRO 2026-07-27: "Samsung Galaxy Tab A11+ 5G TO - Promo $279.99, Min $50 tablet plan
    w/6 months of service" → the first $ is the DEVICE PROMO PRICE and the plan-adjacent $ is the plan's
    monthly minimum. Used only on a HARDWARE line that has already been demoted below every real
    rate-plan line — never for the ordinary single-$ case, which is byte-identical to extract_mrc_bare.
    PURE (no I/O)."""
    s = "" if desc is None else str(desc)
    if not s.strip():
        return None
    hits = list(_ANY_DOLLAR.finditer(s))
    if not hits:
        return None
    if len(hits) > 1 and kws:
        anchors = []
        for k in kws:
            rx = _KW_RX_CACHE.get(k)
            if rx is None:
                rx = _KW_RX_CACHE[k] = re.compile(r"(?<![a-z0-9])" + re.escape(k) + r"(?![a-z0-9])", re.I)
            anchors.extend(mm.start() for mm in rx.finditer(s))
        if anchors:
            best, best_gap = None, None
            for h in hits:
                v = _to_f(h.group(1))
                if not (v and v > 0):
                    continue
                gap = min(abs(a - h.start()) for a in anchors)
                if gap <= _PLAN_ANCHOR_WINDOW and (best_gap is None or gap < best_gap):
                    best, best_gap = v, gap
            if best is not None:
                return round(best, 2)
    v = _to_f(hits[0].group(1))
    if v and v > 0:
        return round(v, 2)
    return None


def extract_mrc_from_desc(desc):
    """Best-effort MONTHLY recurring charge ($) extracted from a product-description string, or None.

    Preference order so a device PRICE doesn't masquerade as an MRC:
      1. a $ amount adjacent to a monthly keyword  ("$25/mo", "25 monthly", "$50 per month")
      2. a monthly keyword followed by a $ amount   ("MRC $30", "monthly: 40")
      3. a bare $-prefixed amount                    ("Unlimited $50")  — last resort
    Commas are stripped ("$1,234.00" → 1234.0). Returns None for 0 / no match. PURE (no I/O).
    UNCHANGED contract — now expressed as its two halves so the money path can bound step 3."""
    return extract_mrc_monthly(desc) or extract_mrc_bare(desc)


# ── RATE-PLAN LINE IDENTIFICATION + ONE-CHAIN-PER-ACTIVATION (mig 233; owner money fix 2026-07-25) ───
# A multi-month schedule pays ONCE PER ACTIVATION, on the ACTIVATION'S RATE-PLAN MRC — never on a device
# or accessory line's price. Two independent guards enforce that, and BOTH default ON:
#   (1) chain dedupe: every qualifying line of one activation collapses to ONE installment chain per
#       schedule (the ledger's own UNIQUE grain: trans_id + mdn/serial). No trigger configuration can
#       double-pay an activation any more.
#   (2) MRC basis: the %-of-MRC amount resolves from the activation's rate-plan line, chosen by the
#       product_mrc CATALOG first (user-confirmed), then a structurally-monthly description, then a
#       tenant-configurable rate-plan matcher. A line that identifies as none of those can no longer
#       donate a bare $ amount (i.e. its PRICE) as an MRC.
# WHAT COUNTS AS A RATE-PLAN LINE IS CONFIGURABLE PER TENANT (RULE TWO): commission_org_config
# .plan_line_matcher, same shape as the activation-payment matcher. The seeded default below is keyword-
# first on purpose — department/category naming is tenant-specific, so it stays EMPTY by default and the
# tenant fills it from its own real values (pick-don't-type, §3b).
DEFAULT_PLAN_LINE_MATCHER = {
    "departments": [],
    "categories": [],
    "product_keywords": ["plan", "unlimited", "airtime", "access charge", "monthly", "mrc",
                         "per month", "rate plan", "talk & text"],
}

# Line classes that can NEVER be the activation's rate plan, whatever their wording says (a
# "PLANTRONICS $89.99" accessory must not donate an MRC). Uses the EXISTING classifier config
# (classify_line — mig 092 accessory config + mig 038 carrier_category_map); no new classifier.
_NON_PLAN_CLASSES = ("accessory", "bill_payment", "rebate")

# 'plan_line' = the fix (default). 'trigger_line' = the pre-fix resolution, kept as the documented
# escape hatch for a tenant whose rate-plan wording the matcher cannot express yet.
# 'ma_tx_activation' (mig 308) = resolve the activation's MRC from the linked MA Daily Tx ACTIVATION
# row's retail_cost (two-hop join through raw_ma_commission), FALLING THROUGH to the 'plan_line'
# ladder when no linked activation is found — a broken linkage must never zero out a payable chain.
_MRC_BASIS_VALUES = ("plan_line", "trigger_line", "ma_tx_activation")

# L2 KILL SWITCH (reversal layer, same doctrine as INSTALLMENT_GATE_LEGACY): truthy restores BOTH the
# per-line chains and the unbounded MRC prefill — i.e. the exact pre-fix behaviour, including its
# double-pay. Emergency reversal only; read at compute time so a Railway toggle needs no redeploy.
def _chain_legacy_forced():
    """True when env INSTALLMENT_CHAIN_LEGACY is truthy (per-activation dedupe + plan-line MRC OFF)."""
    return str(os.getenv("INSTALLMENT_CHAIN_LEGACY", "")).strip().lower() in _LEGACY_TRUTHY


def _norm_plan_matcher(m):
    """Normalize a stored/default rate-plan matcher into lowercased sets. PURE."""
    m = m or {}
    return {
        "departments": {str(x).strip().lower() for x in (m.get("departments") or []) if str(x).strip()},
        "categories": {str(x).strip().lower() for x in (m.get("categories") or []) if str(x).strip()},
        "product_keywords": {str(x).strip().lower() for x in (m.get("product_keywords") or []) if str(x).strip()},
    }


_KW_RX_CACHE = {}


def _kw_hit(text, kws):
    """True if any keyword appears in `text` as a WHOLE WORD/PHRASE. PURE.

    A plain substring test is not safe here: the money path uses this to decide whether a line may
    donate a bare $ amount as an MRC, and "PLANTRONICS BT HEADSET $89.99" contains "plan" (the proof
    harness caught exactly that, and would have paid 5% of $89.99). Boundaries are alphanumeric-aware,
    so "Plan.", "plan,", "$65 Plan" and "ALL ACCESS Plan" all still hit while "plantronics",
    "planning" and "planet" do not.
    NOTE: the ACTIVATION-PAYMENT matcher (_line_class_matches) deliberately keeps its historical
    substring semantics — changing it would move a different, already-shipped money gate."""
    for k in (kws or ()):
        rx = _KW_RX_CACHE.get(k)
        if rx is None:
            rx = _KW_RX_CACHE[k] = re.compile(r"(?<![a-z0-9])" + re.escape(k) + r"(?![a-z0-9])", re.I)
        if rx.search(text):
            return True
    return False


def _line_is_plan_line(row, matcher):
    """True if this sale line looks like the activation's RATE-PLAN line per the tenant's matcher
    (department OR category OR a whole-word product-description keyword). PURE (normalized sets)."""
    dept = str(row.get("department", "") or "").strip().lower()
    cat = str(row.get("category", "") or "").strip().lower()
    prod = str(row.get("product_desc", "") or row.get("customer_plan", "") or "").strip().lower()
    kws = (matcher or {}).get("product_keywords") or set()
    return ((dept and dept in ((matcher or {}).get("departments") or set()))
            or (cat and cat in ((matcher or {}).get("categories") or set()))
            or (bool(kws) and bool(prod) and _kw_hit(prod, kws)))


def _trigger_rank(line, matcher):
    """Deterministic ordering used to pick a chain's REPRESENTATIVE trigger line when several lines of one
    activation match the schedule's trigger. Identity FIRST (the ledger row and the paid gate are keyed on
    MDN/serial, so a line carrying both must win), then rate-plan-ness, then a stable value key so the same
    file always picks the same line regardless of row order. PURE."""
    return (0 if _norm_mdn(line.get("mdn")) else 1,
            0 if _norm_mdn(line.get("serial_1")) else 1,
            0 if _line_is_plan_line(line, matcher) else 1,
            str(line.get("product_desc") or ""), str(line.get("sku") or ""),
            str(line.get("serial_1") or ""), str(line.get("mdn") or ""),
            str(line.get("salesperson") or ""))


DEFAULT_HARDWARE_LINE_MATCHER = {"departments": [], "categories": []}


def _norm_hw(m, enabled=True):
    """Normalize the tenant's HARDWARE-line matcher into lowercased sets + the guard switch. PURE."""
    m = m or {}
    return {"enabled": bool(enabled),
            "departments": {str(x).strip().lower() for x in (m.get("departments") or []) if str(x).strip()},
            "categories": {str(x).strip().lower() for x in (m.get("categories") or []) if str(x).strip()}}


def _line_is_hardware(line, matcher, hw):
    """True if this sale line is a DEVICE line — the thing whose $ is a PRICE, never a monthly charge.

    OWNER TABLET RECURRENCE 2026-07-27: mig 233 bounded the bare-$ prefill to lines that "identify as a
    rate-plan line", but a tablet's DEVICE line identifies as one, because its promo text contains the
    whole word "plan" ("… Promo $279.99, Min $50 tablet plan w/6 months of service"). Wording alone can
    therefore never separate the two halves of an activation. STRUCTURE can: the rate-plan/airtime line
    carries the MDN and a blank Serial 1, the device line carries the IMEI (mig-233's own ledger
    evidence, 31/31 July chains).

    Order: the tenant's explicit hardware departments/categories → an EXEMPTION for a department/category
    the tenant has declared to be its rate-plan line (so a POS that stamps Serial 1 on the airtime line
    is not penalised) → the structural IMEI test. An ICCID (SIM) is NOT hardware for this purpose: a SIM
    line carries no price worth mistaking for an MRC. PURE."""
    if not hw or not hw.get("enabled", True):
        return False
    dept = str(line.get("department") or "").strip().lower()
    cat = str(line.get("category") or "").strip().lower()
    if dept and dept in (hw.get("departments") or set()):
        return True
    if cat and cat in (hw.get("categories") or set()):
        return True
    if dept and dept in ((matcher or {}).get("departments") or set()):
        return False
    if cat and cat in ((matcher or {}).get("categories") or set()):
        return False
    return icat.serial_kind(line.get("serial_1")) == "imei"


def _is_own_price(line, amount):
    """True when `amount` IS this line's own price (Ext Price or Unit Price, to the cent, incl. the
    qty-multiple). A device line's description repeats its price; that number can never be an MRC. PURE."""
    a = round(safe_float(amount), 2)
    if a <= 0:
        return False
    for k in ("ext_price", "unit_price", "price", "retail_price"):
        v = round(safe_float(line.get(k)), 2)
        if v and abs(v - a) < 0.01:
            return True
        if v and a > 0 and abs(v % a) < 0.01 and v >= a:      # qty 2 x $279.99 = $559.98
            return True
    return False


def _mrc_candidate(line, catalog, carrier_id, matcher, acc=None, ccmap=None, hw=None):
    """(rank, mrc, source) — how good a RATE-PLAN MRC source this one line is. LOWER RANK WINS. PURE.

      0    product_mrc CATALOG hit (user-confirmed; authoritative, mirrors _line_amount's first choice)
      1    the description is structurally monthly ("$25/mo", "MRC $30") on a NON-hardware line
      2    a NON-hardware line matches the tenant's rate-plan matcher AND carries a bare $ ("… Plan $65")
      2.4  structurally monthly, but written on a HARDWARE (device) line — usable, never preferred
      2.6  a HARDWARE line whose PLAN-ADJACENT $ is provably not its own price ("Min $50 tablet plan")
      3    the line matches the matcher but carries no $ at all              → (0.0, 'none')
      4    the line is not identifiable as a rate plan                       → (0.0, 'none')
      9    the line can NEVER be a rate plan: its class is accessory/bill-payment/rebate, OR it is a
           hardware line whose only $ is its own price                       → (0.0, 'none')

    The 2.4/2.6 rungs are the 2026-07-27 tablet fix: a device line may still donate an MRC when NOTHING
    better exists, but it can never beat a real rate-plan line and can never donate its own PRICE. With
    `hw=None` (no hardware guard) the ladder is byte-identical to mig 233.

    Ranks 3/4/9 deliberately resolve to $0 rather than to the line's PRICE: paying 5% of a $575 handset
    because its description happened to contain a $ amount is exactly the bug this fixes. An activation
    that lands there is counted + reported in the result's `warnings` so it is never a SILENT zero."""
    if line.get(SIM_MRC_KEY) is not None:
        # READ-ONLY SIMULATION ONLY (pay_simulator): the line IS the rep's typed "plan MRC $/mo", so it
        # is the authoritative rate-plan source for its own chain. No row that ever came out of
        # raw_sales carries this key, so every real activation resolves exactly as before.
        return 0, round(safe_float(line.get(SIM_MRC_KEY)), 2), "simulated"
    desc_key = str(line.get("customer_plan") or line.get("product_desc") or "").strip()
    cat_mrc = _catalog_mrc(catalog, carrier_id, desc_key)
    if cat_mrc is not None:
        return 0, round(safe_float(cat_mrc), 2), "product_catalog"
    if acc is not None:
        try:
            if classify_line(line, acc, ccmap) in _NON_PLAN_CLASSES:
                return 9, 0.0, "none"
        except Exception:
            pass
    is_hw = _line_is_hardware(line, matcher, hw)
    monthly = extract_mrc_monthly(line.get("product_desc"))
    if monthly:
        # NO price-equality veto here on purpose: "$45/mo" is the text calling itself a monthly charge,
        # and a rate-plan line's Ext Price legitimately EQUALS its MRC (the first month is what was rung).
        # The veto below exists only for a BARE $ on a hardware line, where the $ is a price tag.
        return (2.4 if is_hw else 1), round(safe_float(monthly), 2), "prefill"
    if _line_is_plan_line(line, matcher):
        if is_hw:
            bare = extract_mrc_bare_anchored(line.get("product_desc"),
                                             (matcher or {}).get("product_keywords") or set())
            if bare and _is_own_price(line, bare):
                return 9, 0.0, "none"
            if bare:
                return 2.6, round(safe_float(bare), 2), "prefill"
            return 3, 0.0, "none"
        bare = extract_mrc_bare(line.get("product_desc"))
        if bare:
            return 2, round(safe_float(bare), 2), "prefill"
        return 3, 0.0, "none"
    return 4, 0.0, "none"


def _load_plan_line_config(client, org_id):
    """(mrc_basis, normalized rate-plan matcher) for the tenant (mig 233). Degrades to the code defaults
    ('plan_line' + DEFAULT_PLAN_LINE_MATCHER) when the migration isn't applied. Never raises."""
    basis, stored = "plan_line", None
    try:
        rows = (client.schema("commcalc").table("commission_org_config")
                .select("installment_mrc_basis,plan_line_matcher")
                .eq("org_id", org_id).limit(1).execute().data) or []
        if rows:
            b = str(rows[0].get("installment_mrc_basis") or "").strip().lower()
            if b in _MRC_BASIS_VALUES:
                basis = b
            stored = rows[0].get("plan_line_matcher")
    except Exception:
        pass
    if _chain_legacy_forced():
        basis = "trigger_line"
    return basis, _norm_plan_matcher(stored or DEFAULT_PLAN_LINE_MATCHER)


def _load_hardware_guard(client, org_id):
    """The tenant's HARDWARE-line guard (mig 246): (enabled, normalized matcher-ish dict). Defaults to
    ON with empty department/category sets — i.e. the structural IMEI test only. Degrades to the default
    when the migration isn't applied. The INSTALLMENT_CHAIN_LEGACY kill switch turns it OFF with
    everything else. Never raises."""
    enabled, stored = True, None
    try:
        rows = (client.schema("commcalc").table("commission_org_config")
                .select("installment_mrc_hardware_guard,hardware_line_matcher")
                .eq("org_id", org_id).limit(1).execute().data) or []
        if rows:
            v = rows[0].get("installment_mrc_hardware_guard")
            if v is not None:
                enabled = bool(v)
            stored = rows[0].get("hardware_line_matcher")
    except Exception:
        pass
    if _chain_legacy_forced():
        enabled = False
    return _norm_hw(stored or DEFAULT_HARDWARE_LINE_MATCHER, enabled)


# ── ONE CONSISTENT MULTI-MONTH ROW LABEL (owner directive 2026-07-27, deliverable 3) ───────────────
def installment_label(device_product, plan_product, mrc=None):
    """The single display string every multi-month surface uses: DEVICE — RATE PLAN — MRC $x.

    OWNER 2026-07-27: "some items are picking the phones some are picking the rate plans, it should show
    the phones and rate plan in one line and be consistently displayed to avoid confusion." An activation
    is TWO lines (device + plan) and every surface used to show whichever one it happened to look up
    first. Presentation only — no money reads this. PURE."""
    dev = str(device_product or "").strip()
    pln = str(plan_product or "").strip()
    parts = [p for p in (dev, pln) if p]
    if not parts:
        return ""
    if mrc is not None and safe_float(mrc) > 0:
        parts.append(f"MRC ${safe_float(mrc):,.2f}")
    return " — ".join(parts)


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
def repU_cat(rep):
    """by_rep's key shape for the exclusion accounting (so a blast-radius table lines up with pay)."""
    return str(rep or "").strip().upper()


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
              "ma_month1_extra_fields": ["rebate", "device_margin"], "ma_min_amount": 0.01,
              "ma_payout_sign": -1, "ma_lookup_periods": "sale",
              "ma_tx_activation_order_type": "Activation Order"},
    "plan":  {"gate_source": "ma_commission", "ma_device_fields": ["imei", "sim"],
              "ma_month_field_prefix": "spiff_m", "ma_max_month": 6,
              "ma_month1_extra_fields": ["rebate", "device_margin"], "ma_min_amount": 0.01,
              "ma_payout_sign": -1, "ma_lookup_periods": "sale",
              # mig 308: gate_source stays 'ma_commission' BY DEFAULT — 'ma_tx' (the raw_ma_daily_tx
              # union gate) is a per-org/per-carrier config opt-in, never a silent default flip.
              "ma_tx_activation_order_type": "Activation Order"},
}
_GATE_CFG_KEYS = ("gate_source", "ma_device_fields", "ma_month_field_prefix", "ma_max_month",
                  "ma_month1_extra_fields", "ma_min_amount", "ma_payout_sign",
                  # mig 232: WHICH statement period(s) prove month N was actually received —
                  # 'sale' (default, byte-identical) | 'pay' | 'both'.
                  "ma_lookup_periods",
                  # mig 308 (gate_source='ma_tx'): the raw_ma_daily_tx.order_type value that marks
                  # the M1 ACTIVATION row. Config, not code — VidaPay says 'Activation Order'.
                  "ma_tx_activation_order_type")


def _ma_lookup_periods(cfg, sale_period, pay_period):
    """The raw_ma_commission period(s) the paid gate reads as month-N evidence (mig 232). PURE.

    'sale' (DEFAULT, byte-identical to pre-mig-232): the activation month's row — it carries the device's
      forward M1-M6 schedule and is refreshed cumulatively as payouts post.
    'pay' : the paying month's statement (a carrier that posts month N in month N's own file).
    'both': BOTH, de-duplicated and NETTED together — the rows are fed into ONE index, so base +
      adjustment rows across both periods sum exactly like today's multi-row netting and a clawback still
      cannot read as paid. Unknown/blank values fall back to 'sale'."""
    mode = str(cfg.get("ma_lookup_periods") or "sale").strip().lower()
    if mode == "pay":
        return (pay_period,)
    if mode == "both":
        return (sale_period,) if sale_period == pay_period else (sale_period, pay_period)
    return (sale_period,)

# L2 KILL SWITCH (reversal layer, owner-mandated 2026-07-18): when env INSTALLMENT_GATE_LEGACY is truthy,
# compute_sale_installments forces the vendored LEGACY raw_mi gate for ALL orgs/modes — instant Railway env
# toggle, no redeploy. Read at COMPUTE time (never import-cached) so a restart picks up the toggle.
_LEGACY_TRUTHY = {"1", "true", "yes", "on", "y", "t"}


def _legacy_gate_forced():
    """True when the INSTALLMENT_GATE_LEGACY kill switch is set truthy. Read fresh from the env every call
    (no import-time caching) so toggling it on Railway takes effect on the next process restart."""
    return str(os.getenv("INSTALLMENT_GATE_LEGACY", "")).strip().lower() in _LEGACY_TRUTHY

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
    N's evidence columns has a NET amount in the PAYOUT DIRECTION of at least `min` — i.e.
    (net * ma_payout_sign) >= min, where net = summed across the device's base+adjustment rows. This is
    DIRECTION-AWARE, not magnitude: MA amounts are negative = payout to dealer (ma_payout_sign = -1), so a
    NET CLAWBACK (a reversal that flips the net to a CHARGE) does NOT prove paid — it is held with reason
    'net_clawback'. Month N's evidence column is '{prefix}{N}' (spiff_mN); month 1 ALSO counts the configured
    activation-time payouts (rebate, device_margin) so a plan that pays no M1 spiff but a rebate still
    qualifies. line_status is deliberately NOT keyed on (NULL in real rows) — a posted payout IS the proof
    the line is active + paying; consequently in MA mode ALL gate_modes (active_status / nonzero_residual /
    paid_residual) collapse to this same evidence test (the MA feed carries no per-month line status). PURE.

    ma_min_amount is CLAMPED to the code default when a config row sets it <= 0 (0 is NOT a no-minimum
    sentinel — an all-zero device must not read as paid). ma_payout_sign is coerced to +/-1 (0/invalid → -1)."""
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
    min_amt = safe_float(cfg.get("ma_min_amount"))
    if min_amt <= 0:            # m3: 0 is NOT a no-minimum sentinel — clamp to the code default
        min_amt = 0.01
    raw_sign = safe_float(cfg.get("ma_payout_sign"))
    sign = 1.0 if raw_sign > 0 else -1.0    # coerce to +/-1 (0/invalid → -1 = MA negative-is-payout)
    if month_index > max_month:
        return False, {"matched": True, "reason": "month_beyond_ma_columns", "max_month": max_month}
    cols = [f"{prefix}{month_index}"]
    if month_index == 1:
        cols += [str(c).strip() for c in (cfg.get("ma_month1_extra_fields") or []) if str(c).strip()]
    per_col, paid, charged = {}, False, False
    for c in cols:
        net = round(safe_float(agg.get(c)), 2)
        per_col[c] = net
        directed = net * sign            # amount IN the payout direction (positive = real payout to dealer)
        if directed >= min_amt:
            paid = True
        elif directed <= -min_amt:       # a net CHARGE (reversal beyond the min) — not a payout
            charged = True
    if paid:
        reason = "paid"
    elif charged:                        # M2: over-reversal / net clawback → honest reason, not "no payout"
        reason = "net_clawback"
    else:
        reason = "no_month_payout"
    return paid, {"matched": True, "reason": reason, "evidence": per_col, "payout_sign": sign}


# ── MA DAILY TX joins the multi-month formula (mig 308; owner spec 2026-09-01) ─────────────────────
# raw_ma_daily_tx (the VidaPay per-transaction export, mig 083) carries the activation itself
# (order_type = the configured activation order type; THAT row's retail_cost IS the MRC) and the
# months-2..16 payouts as 'TBV MONTH n …' product wording. It has NO imei/mdn, so a B2B sale reaches
# its MA TX rows through a TWO-HOP join: raw_sales.serial_1 ↔ raw_ma_commission.imei|sim (digit-
# normalized, _norm_imei) → raw_ma_commission.activation_order ↔ raw_ma_daily_tx.order_number.
# order_number is NOT unique in the feed (one order = activation row + MONTH-n rows + adjustments),
# which is exactly why the index below groups and NETS per order. The month wording is parsed by the
# Commission Ledger's parse_payment_month — REUSED, never a second regex.
#
# 💰 MONEY GUARD: the ONLY raw_ma_daily_tx money column these paths read is retail_cost.
# merchant_discount (airtime margin) is not part of this formula, and merchant_invoice is an invoice
# NUMBER stored as NUMERIC (residual_subs._MA_IDENTIFIER_COLUMNS) — it must NEVER be summed; it does
# not appear in the select list or the index. Everything below is PURE (rows/config passed in) so the
# proof harness exercises it without a database.
_MA_TX_SELECT_COLS = ("order_number", "order_type", "product_name", "retail_cost", "account_id")
_MA_TX_MONEY_COLS = ("retail_cost",)


def _norm_order(v):
    """Normalize an order number for the activation_order ↔ order_number join: trim + strip an
    Excel-float trailing '.0'. NOT digit-only — order numbers can be alphanumeric. PURE."""
    s = str(v or "").strip()
    if s.endswith(".0"):
        s = s[:-2]
    return s


def build_ma_link_index(ma_rows):
    """Hop 1 of the two-hop join: {normalized device key -> sorted [activation_order, …]} from
    raw_ma_commission rows. A row contributes under BOTH its imei and sim keys (same posture as
    _ma_gate_index, which keeps the base+adjustment NETTING for the spiff evidence — this index only
    carries the LINK, so multiple rows per device simply union their orders). Rows with no device key
    or no activation_order are skipped; never raises. PURE."""
    idx = {}
    for r in (ma_rows or []):
        order = _norm_order(r.get("activation_order"))
        if not order:
            continue
        for f in _MA_DEVICE_COLS:
            k = _norm_imei(r.get(f))
            if k:
                idx.setdefault(k, set()).add(order)
    return {k: sorted(v) for k, v in idx.items()}


def build_ma_tx_index(tx_rows, cfg):
    """Hop 2: {normalized order_number -> {'activation': {...}|None, 'months': {n: net retail_cost},
    'account_id': str|None}} from raw_ma_daily_tx rows for the lookup period(s).
      • 'activation' — the M1 activation row: order_type equals cfg['ma_tx_activation_order_type']
        (case-insensitive, trimmed; CONFIG, never a literal). Carries retail_cost (the MRC),
        product_name and account_id. The first activation row with a nonzero retail_cost wins as the
        MRC donor; 'count' reports how many were seen.
      • 'months' — per payment month n (parse_payment_month over product_name), the NET summed
        retail_cost across the order's rows, so a base + adjustment/clawback pair nets exactly like
        the ma_commission spiff evidence does.
    Rows with no order_number are skipped; never raises. PURE (rows + config passed in)."""
    act_type = str((cfg or {}).get("ma_tx_activation_order_type") or "Activation Order").strip().lower()
    idx = {}
    for r in (tx_rows or []):
        order = _norm_order(r.get("order_number"))
        if not order:
            continue
        e = idx.setdefault(order, {"activation": None, "months": {}, "account_id": None})
        acct = str(r.get("account_id") or "").strip()
        if acct and not e["account_id"]:
            e["account_id"] = acct
        if str(r.get("order_type") or "").strip().lower() == act_type:
            rc = safe_float(r.get("retail_cost"))
            if e["activation"] is None:
                e["activation"] = {"retail_cost": rc, "product_name": str(r.get("product_name") or ""),
                                   "account_id": acct or None, "count": 1}
            else:
                e["activation"]["count"] += 1
                # a later activation row with money beats an earlier $0 one (deterministic upgrade)
                if not e["activation"]["retail_cost"] and rc:
                    e["activation"]["retail_cost"] = rc
                    e["activation"]["product_name"] = str(r.get("product_name") or "")
                    e["activation"]["account_id"] = acct or e["activation"].get("account_id")
        n = parse_payment_month(r.get("product_name"))
        if n:
            e["months"][n] = round(safe_float(e["months"].get(n)) + safe_float(r.get("retail_cost")), 2)
    return idx


def _ma_tx_orders(serial, indexes):
    """The activation_order list a raw device serial links to, or []. indexes = {'link': …, 'tx': …}."""
    k = _norm_imei(serial)
    if not k:
        return []
    return (indexes or {}).get("link", {}).get(k, [])


def ma_tx_mrc_for(serial, indexes):
    """The sale's MRC from its linked MA TX ACTIVATION row (owner spec: that row's retail_cost IS the
    MRC), or None when the linkage/row/amount is missing — the caller then FALLS THROUGH to the
    existing MRC ladder (a broken linkage never zeroes a payable chain). Returns
    {'mrc', 'order_number', 'account_id'}. MRC is the ABSOLUTE value (a monthly charge has no
    direction; an export that signs dealer-side rows negative still yields the right $). Orders are
    walked in sorted order so the same data always resolves the same row. PURE."""
    for order in _ma_tx_orders(serial, indexes):
        e = (indexes or {}).get("tx", {}).get(order)
        act = (e or {}).get("activation")
        if act and safe_float(act.get("retail_cost")):
            return {"mrc": round(abs(safe_float(act.get("retail_cost"))), 2),
                    "order_number": order,
                    "account_id": act.get("account_id") or (e or {}).get("account_id")}
    return None


def ma_tx_month_evidence(serial, month_index, indexes, cfg):
    """(met, evidence): does the MA Daily Tx feed prove month `month_index` was paid on this device's
    activation order(s)? Direction-checked EXACTLY like the ma_commission gate: month n's evidence is
    the NET retail_cost of the linked orders' 'MONTH n' rows, and it proves paid iff
    (net * ma_payout_sign) >= ma_min_amount — so a net clawback reads as 'net_clawback', never as
    paid. Month 1 ADDITIONALLY counts the existence of the linked ACTIVATION ORDER row itself (the
    activation posting IS month-1 evidence). ma_max_month (config; a Total org row can set 16) caps
    the horizon. min/sign clamped identically to _gate_met_ma. PURE."""
    cfg = cfg or {}
    orders = _ma_tx_orders(serial, indexes)
    if not orders:
        return False, {"matched": False, "reason": "no_ma_tx_link"}
    max_month = int(cfg.get("ma_max_month") or 6)
    if month_index > max_month:
        return False, {"matched": True, "reason": "month_beyond_ma_max", "max_month": max_month}
    min_amt = safe_float(cfg.get("ma_min_amount"))
    if min_amt <= 0:            # 0 is NOT a no-minimum sentinel (same clamp as _gate_met_ma)
        min_amt = 0.01
    raw_sign = safe_float(cfg.get("ma_payout_sign"))
    sign = 1.0 if raw_sign > 0 else -1.0
    net, act_seen, order_hit, acct = 0.0, False, None, None
    for order in orders:
        e = (indexes or {}).get("tx", {}).get(order) or {}
        m = safe_float((e.get("months") or {}).get(month_index))
        if m:
            net = round(net + m, 2)
            order_hit = order_hit or order
            acct = acct or e.get("account_id")
        if e.get("activation") is not None:
            act_seen = True
            order_hit = order_hit or order
            acct = acct or (e.get("activation") or {}).get("account_id") or e.get("account_id")
    directed = net * sign
    paid = directed >= min_amt or (month_index == 1 and act_seen)
    if paid:
        reason = "paid"
    elif directed <= -min_amt:
        reason = "net_clawback"
    else:
        reason = "no_month_payout"
    return paid, {"matched": True, "reason": reason,
                  "evidence": {"month_net": round(net, 2), "activation_order_seen": act_seen,
                               "orders": orders[:8]},
                  "payout_sign": sign, "order_number": order_hit, "account_id": acct}


def _gate_met_ma_tx(sale_line, ma_index, tx_indexes, month_index, cfg):
    """(met, evidence) for gate_source='ma_tx' (mig 308): the UNION of
      (i)  the existing ma_commission spiff evidence (spiff_m1..spiff_m6 + month-1 extras) via
           _gate_met_ma UNCHANGED, and
      (ii) the MA Daily Tx month evidence via ma_tx_month_evidence above.
    Either half proving paid pays the month; neither half can turn a paid month off. Evidence from
    both halves is carried so the preview can say WHICH statement proved it. PURE."""
    met_sp, ev_sp = _gate_met_ma(sale_line, ma_index, month_index, cfg)
    met_tx, ev_tx = False, {"matched": False, "reason": "no_ma_tx_link"}
    for f in ("serial_1", "imei"):
        s = sale_line.get(f)
        if _norm_imei(s):
            met_tx, ev_tx = ma_tx_month_evidence(s, month_index, tx_indexes, cfg)
            if (ev_tx or {}).get("matched"):
                break
    met = bool(met_sp or met_tx)
    sp_r, tx_r = (ev_sp or {}).get("reason"), (ev_tx or {}).get("reason")
    matched = bool((ev_sp or {}).get("matched") or (ev_tx or {}).get("matched"))
    if met:
        reason = "paid"
    elif "net_clawback" in (sp_r, tx_r):
        reason = "net_clawback"
    elif sp_r in ("month_beyond_ma_columns",) and tx_r in ("month_beyond_ma_max", "no_ma_tx_link"):
        reason = "month_beyond_ma_columns"
    elif not matched:
        reason = "no_ma_record"
    else:
        reason = "no_month_payout"
    out = {"matched": matched, "reason": reason,
           "evidence": {"ma_commission": (ev_sp or {}).get("evidence"),
                        "ma_tx": (ev_tx or {}).get("evidence")},
           "payout_sign": (ev_tx or ev_sp or {}).get("payout_sign")}
    if reason == "month_beyond_ma_columns":
        out["max_month"] = (ev_sp or {}).get("max_month") or (ev_tx or {}).get("max_month")
    if (ev_tx or {}).get("order_number"):
        out["order_number"] = ev_tx.get("order_number")
        out["account_id"] = ev_tx.get("account_id")
    return met, out


def _read_ma_tx(client, org_id, period):
    """Paginated raw_ma_daily_tx for one period — ORG-SCOPED, and selecting ONLY the columns this
    formula needs (_MA_TX_SELECT_COLS; deliberately excludes merchant_invoice — an identifier that
    must never be summed — and everything else). Falls back to select('*') if a column is missing in
    an older schema, and to [] if the table is absent (mig 083 unrun). Never raises."""
    out, start, page = [], 0, 1000
    sel = ",".join(_MA_TX_SELECT_COLS)
    while True:
        try:
            rows = (client.schema("commcalc").table("raw_ma_daily_tx").select(sel)
                    .eq("org_id", org_id).in_("period", _pvariants(period))
                    .range(start, start + page - 1).execute().data) or []
        except Exception:
            try:
                rows = (client.schema("commcalc").table("raw_ma_daily_tx").select("*")
                        .eq("org_id", org_id).in_("period", _pvariants(period))
                        .range(start, start + page - 1).execute().data) or []
            except Exception:
                break
        out.extend(rows)
        if len(rows) < page:
            break
        start += page
    return out


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
def _line_amount(sale_line, iline, catalog, carrier_id, mrc_override=None):
    """(amount, mrc, mrc_source) for one installment line on one sold line.
    flat → flat_amount. pct_mrc → mrc_pct × MRC.

    `mrc_override` = an (mrc, source) pair already resolved for the whole ACTIVATION (the rate-plan line
    of this trans/MDN — see _mrc_candidate). When it is None the legacy per-line resolution is used:
    product_mrc catalog keyed on the line's customer_plan/product_desc, falling back to a description-
    extracted prefill, then 0. PURE."""
    kind = (iline.get("payout_kind") or "flat").strip().lower()
    if kind != "pct_mrc":
        return round(safe_float(iline.get("flat_amount")), 2), 0.0, "flat"
    if sale_line.get(SIM_MRC_KEY) is not None:
        mrc, src = safe_float(sale_line.get(SIM_MRC_KEY)), "simulated"
    elif mrc_override is not None:
        mrc, src = mrc_override[0], mrc_override[1]
    else:
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
def compute_sale_installments(client, org_id, pay_period, persist=False, _gate_source_override=None,
                              _config_override=None, _sales_override=None):
    """Sale-triggered installments that LAND in `pay_period`. Read-only unless persist=True.
    Returns {pay_period, by_rep:{REPUPPER:amount}, ledger:[...], flags:[...], totals, schedules, note}.

    A qualifying sold line in period S schedules a payout for month_index = (P - S) + 1 (1..N). The
    line pays only if it is inside the schedule's user-defined effective window AND the paid gate is met
    for pay_period P. A gated-off (withheld) line emits the two flags.

    The paid gate's EVIDENCE SOURCE is config-driven per carrier (mig 223): Boost carriers prove paid from
    raw_mi (byte-identical to pre-mig-223); master-agent-fed carriers prove paid from raw_ma_commission
    per-month spiffs. In MA mode ALL gate_modes (active_status/nonzero_residual/paid_residual) collapse to
    the same MA-evidence test — the MA feed carries no reliable per-month line status. `_gate_source_override`
    forces every gate to that source (used by preview_gate_impact to diff new-vs-legacy).

    MA DAILY TX (mig 308, config opt-in): gate_source='ma_tx' proves month n from the UNION of the
    ma_commission spiffs AND raw_ma_daily_tx 'MONTH n' rows (two-hop join through raw_ma_commission
    .activation_order; ma_max_month up to 16); commission_org_config.installment_mrc_basis=
    'ma_tx_activation' resolves the MRC from the linked MA TX Activation Order row's retail_cost,
    falling through to the plan-line ladder when unlinked.

    L2 KILL SWITCH: env INSTALLMENT_GATE_LEGACY truthy forces the vendored LEGACY raw_mi gate for EVERY
    org/mode (bypassing config resolution entirely) → the exact pre-mig-223 behavior, instant Railway toggle
    with no redeploy.

    `_sales_override` = {sale_period: [line, ...]} — READ-ONLY PROJECTION ONLY (the employee pay
    simulator). When set, the period's sold lines come from the caller instead of raw_sales; a period the
    caller did not supply reads as EMPTY rather than falling back to the database, so a projection can
    never mix invented lines with real ones. Writing is REFUSED outright: a simulated activation must
    never reach sale_installment_ledger. Unset (every calculate, every report) → byte-identical."""
    if _sales_override is not None and persist:
        raise ValueError("compute_sale_installments: _sales_override is a read-only projection hook and "
                         "cannot be persisted.")
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
    # ONE CHAIN PER ACTIVATION + rate-plan MRC basis (mig 233). Both default ON; 'trigger_line' (or the
    # INSTALLMENT_CHAIN_LEGACY env) restores the pre-fix per-line resolution.
    mrc_basis, plan_matcher = _load_plan_line_config(client, org_id)
    hw_guard = _load_hardware_guard(client, org_id)
    chain_legacy = _chain_legacy_forced()
    # DEVICE-CATEGORY QUALIFICATION (mig 245; owner 2026-07-27). Which device categories a multi-month
    # schedule pays on — per schedule, else per org, else the owner's defaults (all but TABLET + SIM).
    # Every loader degrades to the code default, so this works with no migration applied.
    cat_rules = icat.load_category_rules(client, org_id)
    org_qual = icat.load_org_qualification(client, org_id)
    cat_lookup = icat.build_catalog_category_lookup(client, org_id)
    # FLAT (ONE-TIME) PAYOUT BY CATEGORY (mig 256; owner 2026-08-01 "fwa is paid on flat rate should
    # not be in monthly payments - fix but dont hard code"). Same three-layer ladder as the
    # qualification switch above: per schedule -> per org -> code defaults, where the code default is
    # EVERY category on 'installments' — i.e. today's behaviour. Degrades with mig 256 unapplied.
    org_payout = icpay.load_org_payout(client, org_id)
    # EXPECTED vs EARNED + the permission-gated manual promote (mig 258; owner 2026-08-01).
    # `expected_amount` is the PRE-GATE amount — the number the month WOULD pay — and is never
    # summed into by_rep/totals. The promote index is the ONLY new money path: it lets an
    # authorised person pay a month whose gate is unmet, and it SURVIVES RECOMPUTE because it
    # lives in its own table which `_persist` never touches.
    xcfg = xcomm.load_config(client, org_id)
    xpromotes = xcomm.load_promotes(client, org_id, pay_period, _pvariants(pay_period))
    xindex = xcomm.build_index(xpromotes)
    # READ-ONLY A/B OVERRIDE (never used by a calculate — only by the category-impact preview, which
    # runs this engine twice to show the operator the exact per-rep delta BEFORE anything is recomputed).
    qual_override = None
    payout_override = None
    if _config_override:
        if "hardware_guard" in _config_override:
            hw_guard = _norm_hw(hw_guard, bool(_config_override.get("hardware_guard")))
        if _config_override.get("qualification") is not None:
            qual_override = icat.normalize_qualification(_config_override.get("qualification"))
        if _config_override.get("category_payout") is not None:
            payout_override = icpay.normalize_payout(_config_override.get("category_payout"))

    def _is_acc_row(r):
        try:
            return classify_line(r, acc, ccmap) == "accessory"
        except Exception:
            return False
    store_market = _read_store_market(client, org_id)
    role_by_rep = _read_employee_roles(client, org_id)   # {_canon_person(name) -> role} for scope='role'

    # L2 KILL SWITCH: env INSTALLMENT_GATE_LEGACY forces the legacy raw_mi gate for ALL orgs/modes. Reuses
    # the override plumbing so every carrier resolves to 'boost_mi' → the unchanged _gate_met path.
    if _legacy_gate_forced():
        _gate_source_override = "boost_mi"

    # PAID-GATE EVIDENCE SOURCE per carrier (mig 223; RULE TWO). Resolve once, cache per carrier_id. A Boost
    # carrier → 'boost_mi' (unchanged raw_mi gate); a master-agent carrier → 'ma_commission'. This is the
    # ONLY new dispatch — Boost carriers never enter the MA branch, so their gate outcomes stay byte-identical.
    carrier_mode_by_id, default_mode = _carrier_mode_map(client, org_id)
    gate_org_rows, gate_house_rows = _load_gate_source_rows(client, org_id)
    _gate_cfg_cache, _ma_index_cache = {}, {}
    _ma_rows_cache, _ma_tx_idx_cache = {}, {}

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

    def _ma_rows_for(periods):
        """The raw_ma_commission ROWS for a periods tuple, read ONCE per key. Shared by the spiff
        evidence index AND the mig-308 two-hop link index so raw_ma_commission is never read twice
        for the same periods in one compute."""
        if isinstance(periods, str):
            periods = (periods,)
        key = tuple(periods)
        if key not in _ma_rows_cache:
            rows = []
            for p in key:
                rows.extend(_read_ma_commission(client, org_id, p))
            _ma_rows_cache[key] = rows
        return _ma_rows_cache[key]

    def _ma_index_for(periods):
        """MA evidence index for ONE or MORE statement periods (mig 232). A single-period tuple is the
        pre-mig-232 behaviour byte-for-byte; multiple periods are read into the SAME index so their rows
        NET together (a clawback in either period still cancels a payout in the other)."""
        if isinstance(periods, str):
            periods = (periods,)
        key = tuple(periods)
        if key not in _ma_index_cache:
            _ma_index_cache[key] = _ma_gate_index(_ma_rows_for(key))
        return _ma_index_cache[key]

    def _ma_tx_indexes_for(periods, gate_cfg):
        """The mig-308 two-hop indexes {'link': serial→activation_orders, 'tx': order→activation/
        month nets} for a periods tuple — raw_ma_daily_tx read ONCE per compute per key (same
        _ma_lookup_periods semantics as the spiff evidence), org-scoped, needed columns only. Only
        ever called when a chain actually resolves to gate_source='ma_tx' or mrc_basis=
        'ma_tx_activation', so every other tenant performs ZERO extra reads."""
        if isinstance(periods, str):
            periods = (periods,)
        pkey = tuple(periods)
        # keyed on (periods, activation order type): two carriers configured with DIFFERENT
        # order-type spellings must not share one index built with the wrong matcher.
        act = str((gate_cfg or {}).get("ma_tx_activation_order_type") or "Activation Order").strip().lower()
        key = (pkey, act)
        if key not in _ma_tx_idx_cache:
            tx_rows = []
            for p in pkey:
                tx_rows.extend(_read_ma_tx(client, org_id, p))
            _ma_tx_idx_cache[key] = {"link": build_ma_link_index(_ma_rows_for(pkey)),
                                     "tx": build_ma_tx_index(tx_rows, gate_cfg)}
        return _ma_tx_idx_cache[key]

    pay_idx = _period_index(pay_period)
    if pay_idx is None:
        return {"pay_period": pay_period, "by_rep": {}, "ledger": [], "flags": [], "schedules": len(scheds),
                "totals": {"amount": 0.0, "paid": 0, "withheld": 0, "reps": 0},
                "note": f"Unparseable pay_period '{pay_period}'."}

    # horizon: pull sales for pay_period back through the deepest schedule's num_months.
    max_n = min(MAX_SCHEDULE_MONTHS, max((int(s.get("num_months") or 1) for s in scheds), default=1))
    sale_periods = [_shift_period(pay_period, -k) for k in range(0, max_n)]
    sale_periods = [p for p in sale_periods if p]

    # paid gate reads raw_mi for the PAY period only (is the line active/paying NOW).
    mi_index = _mi_index(_read_mi(client, org_id, pay_period))

    # MONTH-1 "paid at activation" gate (mig 210): only prep the matcher when at least one schedule opts
    # in — so a schedule that doesn't opt in is byte-identical to pre-mig-210 (no extra reads, no new
    # ledger fields). act_matcher is normalized (sets); trans_index is built per sale_period on demand.
    any_activation = any((str(s.get("m1_gate") or "inherit").strip().lower()) == "activation_payment" for s in scheds)
    # SYNTHETIC 'activation_bucket' TRIGGER (mig 232): a schedule may trigger on the resolved activation
    # bucket instead of a raw contract_type, so a tenant whose POS leaves Contract Type BLANK can still
    # start a multi-month installment on the ACTIVATION (once per activation transaction — the resolver
    # stamps one representative line per rescued transaction). Stamped ONLY when a schedule actually uses
    # it, so every existing schedule keeps its exact line set and outcome.
    _uses_bucket_trigger = any(
        (str(s.get("trigger_match_field") or "").strip().lower()) == "activation_bucket" for s in scheds)
    act_matcher = _load_activation_matcher(client, org_id) if any_activation else None
    # DUAL-CATEGORY item mapping (mig 210): when the org has mapped any item to the activation-payment
    # category, the mapping is AUTHORITATIVE; else the seeded heuristic matcher is the fallback.
    ap_item_map = _load_item_map(client, org_id) if any_activation else {}
    has_ap_mappings = any(
        (str(v.get("sales_category") or "").strip().lower() == ACTIVATION_PAYMENT_CATEGORY
         or str(v.get("kpi_category") or "").strip().lower() == ACTIVATION_PAYMENT_CATEGORY)
        for v in ap_item_map.values()) if any_activation else False

    pm = parse_period(pay_period)
    by_rep, ledger, flags, warnings = {}, [], [], []
    n_paid = n_withheld = 0
    n_dedup = n_mrc_unresolved = n_mrc_ambiguous = 0
    total_amt = 0.0
    cat_counts, cat_excluded, cat_sources = {}, {}, set()
    n_cat_excluded = n_cat_unknown = 0
    cat_excluded_amt = 0.0
    # mig 256 flat-payout accounting. All zero/empty unless a tenant actually configured a flat
    # category, so an unconfigured tenant's `flat_guard` is a constant and nothing else moves.
    x_applied, x_stale, x_redundant, x_seen_keys = [], [], [], set()
    flat_paid, flat_suppressed, flat_unconfigured = {}, {}, {}
    flat_sources, flat_active_keys, _flat_suppressed_keys = set(), set(), set()
    n_flat_paid = n_flat_suppressed = 0
    flat_paid_amt = flat_suppressed_amt = 0.0

    for sale_period in sale_periods:
        s_idx = _period_index(sale_period)
        if s_idx is None:
            continue
        month_index = (pay_idx - s_idx) + 1
        if month_index < 1:
            continue
        sales = (list((_sales_override or {}).get(sale_period) or [])
                 if _sales_override is not None else _read_sales(client, org_id, sale_period))
        # month_index 1 <=> sale_period == pay_period (k=0), so the activation-payment gate only ever
        # needs the trans index for that period. Built from the FULL read (payment/System lines included).
        trans_index = _build_trans_index(sales) if (any_activation and sale_period == pay_period) else None
        # VOIDED: SHARED token set (owner 2026-07-25) — a voided line must not generate installments
        # under any spelling the POS feed uses.
        valid = [r for r in sales
                 if not _is_voided(r.get("voided"))
                 and str(r.get("trans_type", "") or "").strip() != "Return"]
        if _uses_bucket_trigger:
            try:
                from app.modules.commcalc.commission_engine import _activation_buckets
                for _r, _b in zip(valid, _activation_buckets(client, org_id, valid)):
                    _r["activation_bucket"] = _b or ""
            except Exception:
                pass
        # ── ONE CHAIN PER ACTIVATION (owner money fix 2026-07-25) ───────────────────────────────
        # A POS that stamps the transaction's Contract Type — or a resolved activation_bucket — on EVERY
        # line of the sale makes one trigger match the DEVICE line, the RATE-PLAN line and the SIM line
        # of the SAME activation. Before this guard each matching line started its OWN installment chain,
        # so the rep was paid once per matching LINE. Owner repro (luxelink, IMEI 357612117781238, sold
        # July 2026, trigger `activation_bucket in premium,byod`): the $575 device line AND the $65
        # "Total ALL ACCESS Plan $65" line both matched, producing TWO month-1 installments ($28.75 +
        # $3.25) where only $3.25 is owed.
        #
        # PARTITION = THE TRANSACTION, SPLIT ONLY BY DISTINCT MDN. The owner's July ledger settled how the
        # real feed shapes these lines: the RATE-PLAN / airtime lines carry the MDN and a BLANK serial_1
        # (31 of them in one July group), while the DEVICE lines carry the IMEI. So the two halves of ONE
        # activation share NO identity field, and any "group by mdn-or-serial" key splits them apart.
        # The MDN is the subscriber, so it is the only sound split key: a family/multi-line sale with k
        # distinct MDNs still pays k times, while a SIM or accessory line that happens to carry a serial
        # can no longer manufacture an extra activation.
        # Identity is then COALESCED across the group (MDN from the airtime line, IMEI from the device
        # line, device first by ext_price) — the chain MUST keep the IMEI because the master-agent paid
        # gate joins raw_ma_commission on serial_1/imei and the Device History page reads the ledger by
        # serial_1. `chain_legacy` (env INSTALLMENT_CHAIN_LEGACY) restores per-line chains verbatim.
        chain_key_of, groups, tx_mdns, tx_lines = {}, {}, {}, {}
        for _r in valid:
            _t = str(_r.get("trans_id") or "").strip()
            _m = _norm_mdn(_r.get("mdn"))
            if _t:
                tx_lines.setdefault(_t, []).append(_r)
            if _t and _m:
                tx_mdns.setdefault(_t, set()).add(_m)
        for _i, _r in enumerate(valid):
            _t = str(_r.get("trans_id") or "").strip()
            _m = _norm_mdn(_r.get("mdn"))
            _s = _norm_mdn(_r.get("serial_1"))
            if chain_legacy or not (_t or _m or _s):
                _ck = ("l", str(_i))                    # nothing to group by → pre-fix per-line behaviour
            elif not _t:
                _ck = ("i", _m or _s)                   # no transaction id → identity alone
            elif len(tx_mdns.get(_t) or ()) > 1:
                _ck = ("t", _t, _m)                     # multi-subscriber sale → one chain per MDN
            else:
                _ck = ("t", _t, "")                     # one subscriber → one chain for the transaction
            chain_key_of[id(_r)] = _ck
            groups.setdefault(_ck, []).append(_r)

        def _tx_pool(ck):
            """In a MULTI-SUBSCRIBER transaction the lines that carry no MDN (typically the handsets and
            the SIMs) cannot be attributed to one subscriber — they are shared context: they may donate an
            MRC or an IMEI, but they never create a chain of their own (that is what produced the extra
            device-line chains). Never crosses a transaction boundary."""
            if ck[0] == "t" and ck[1] and ck[2]:
                return groups.get(("t", ck[1], ""), [])
            if ck[0] == "d" and ck[1]:      # device-keyed fallback: the rest of the transaction
                own = {id(r) for r in groups.get(ck, [])}
                return [r for r in tx_lines.get(ck[1], []) if id(r) not in own]
            return []

        def _dev_order(rows):
            """Deterministic 'device first' ordering — highest ext_price, then a stable value key."""
            return sorted(rows, key=lambda x: (-safe_float(x.get("ext_price")),
                                               str(x.get("product_desc") or ""), str(x.get("sku") or ""),
                                               str(x.get("serial_1") or ""), str(x.get("mdn") or "")))

        def _chain_identity(ck, rows):
            """(mdn, serial) for the whole activation, coalesced across its lines. The MDN comes from the
            chain key when the transaction was split per subscriber; the IMEI comes from the group's
            device line, or — only when it is UNAMBIGUOUS (exactly one distinct serial in the shared
            pool) — from the transaction's shared lines. PURE."""
            mdn = ck[2] if (ck[0] == "t" and ck[2]) else ""
            serial = ""
            for r in _dev_order(rows):
                if not serial:
                    serial = _norm_mdn(r.get("serial_1"))
                if not mdn:
                    mdn = _norm_mdn(r.get("mdn"))
            if not serial:
                pool_ser = {_norm_mdn(r.get("serial_1")) for r in _tx_pool(ck)} - {""}
                if len(pool_ser) == 1:
                    serial = next(iter(pool_ser))
            return mdn, serial

        def _mrc_pool(ck):
            """Every line that may carry THIS activation's rate-plan MRC: the activation's own lines plus
            the transaction's shared (MDN-less) lines."""
            return list(groups.get(ck, [])) + list(_tx_pool(ck))

        # PHASE 1 — collect every qualifying (line, schedule) pair, keyed by (schedule, activation).
        # Every filter below is UNCHANGED; only the terminal action moved (collect, don't emit).
        chains, shared = {}, {}
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
                num_months = min(MAX_SCHEDULE_MONTHS, int(sched.get("num_months") or 1))
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
                _lck = chain_key_of[id(line)]
                if _lck[0] == "t" and _lck[1] and not _lck[2] and len(tx_mdns.get(_lck[1]) or ()) > 1:
                    # shared (MDN-less) line of a multi-subscriber sale — context, not an activation
                    shared.setdefault((sched.get("id"), _lck[1]), []).append(
                        (line, rep, store, plan, sched, iline))
                else:
                    chains.setdefault((sched.get("id"), _lck), []).append(
                        (line, rep, store, plan, sched, iline))

        # A shared line pays ONLY when its transaction produced no subscriber chain for that schedule
        # (e.g. the trigger matches handset lines and the airtime lines are absent). It is then keyed by
        # its own device serial, so two genuinely different handsets still pay twice.
        _covered = {(sid, ck[1]) for (sid, ck) in chains if ck[0] == "t" and ck[1]}
        for (_sid, _tid), _sc in shared.items():
            if (_sid, _tid) in _covered:
                n_dedup += len(_sc)
                continue
            for _cand in _sc:
                _dk = ("d", _tid, _norm_mdn(_cand[0].get("serial_1")))
                _g = groups.setdefault(_dk, [])
                if not any(r is _cand[0] for r in _g):     # identity, not dict equality
                    _g.append(_cand[0])
                chains.setdefault((_sid, _dk), []).append(_cand)

        # PHASE 2 — ONE installment per (schedule, activation). Insertion order = first-appearance order
        # of each chain, i.e. the pre-fix emission order when no activation had duplicate lines.
        for (_sched_id, _ck), _cands in chains.items():
            if len(_cands) > 1:
                _cands.sort(key=lambda c: _trigger_rank(c[0], plan_matcher))
                n_dedup += len(_cands) - 1
            line, rep, store, plan, sched, iline = _cands[0]
            carrier_id = plan.get("carrier_id")
            # COALESCED activation identity (see _chain_identity). `gate_line` is the SAME object as
            # `line` whenever the representative already carries the whole identity — so an activation
            # whose lines were never split is byte-identical to the pre-fix gate call.
            mdn, serial = _chain_identity(_ck, groups.get(_ck, [line]))
            if mdn == _norm_mdn(line.get("mdn")) and serial == _norm_mdn(line.get("serial_1")):
                gate_line = line
            else:
                gate_line = {**line, "mdn": mdn, "serial_1": serial}
            if _ck[0] == "t" and _ck[2] and not serial and len(warnings) < 200:
                # subscriber-split chain with no resolvable IMEI: months 2..N are gated on a per-device
                # join (raw_ma_commission / raw_mi), so say so instead of silently withholding later.
                warnings.append({
                    "type": "no_device_identity", "sale_period": sale_period, "month_index": month_index,
                    "rep": rep, "store": store, "trans_id": str(line.get("trans_id") or "").strip(),
                    "mdn": mdn,
                    "detail": ("This subscriber's lines carry no device serial, and the transaction has "
                               "several, so months 2+ cannot be matched to the carrier statement by IMEI "
                               "and will be held. Have the POS export carry Serial 1 on the airtime line.")})
            if len(_cands) > 1 and len(tx_mdns.get(str(line.get("trans_id") or "").strip()) or ()) <= 1:
                # >1 distinct device serial collapsed into ONE chain because the sale carries at most one
                # MDN: two devices really sold on one transaction would be paid once. Never silent.
                _sers = {_norm_mdn(c[0].get("serial_1")) for c in _cands} - {""}
                if len(_sers) > 1 and len(warnings) < 200:
                    warnings.append({
                        "type": "multi_device_single_chain", "sale_period": sale_period,
                        "month_index": month_index, "rep": rep, "store": store,
                        "trans_id": str(line.get("trans_id") or "").strip(),
                        "imeis": sorted(_sers)[:8], "paid_chains": 1,
                        "detail": ("This transaction has several device serials but no per-line mobile "
                                   "number to tell the subscribers apart, so it paid ONE installment. If "
                                   "these were separate activations, the POS export must carry the "
                                   "Activated Mobile Number on each line.")})
            # MRC BASIS (mig 233): a %-of-MRC installment is paid on the ACTIVATION'S RATE-PLAN line —
            # never on a device/hardware line's price. 'trigger_line' = the pre-fix per-line resolution.
            _chain_lines = _mrc_pool(_ck)
            _mrc_override, _mrc_line, _ma_tx_prov = None, None, None
            # MA TX MRC (mig 308): basis 'ma_tx_activation' resolves the MRC from the linked MA Daily
            # Tx ACTIVATION row's retail_cost (two-hop join via the chain's coalesced serial). When no
            # linked activation is found it FALLS THROUGH to the plan_line ladder below — a broken
            # linkage must never zero out a payable chain.
            if (mrc_basis == "ma_tx_activation" and serial
                    and str(iline.get("payout_kind") or "flat").strip().lower() == "pct_mrc"):
                _gcfg = _gate_cfg_for(carrier_id)
                _hit = ma_tx_mrc_for(
                    serial, _ma_tx_indexes_for(_ma_lookup_periods(_gcfg, sale_period, pay_period), _gcfg))
                if _hit is not None:
                    _mrc_override = (_hit["mrc"], "ma_tx_activation")
                    _ma_tx_prov = _hit
            if (_mrc_override is None
                    and mrc_basis in ("plan_line", "ma_tx_activation")
                    and str(iline.get("payout_kind") or "flat").strip().lower() == "pct_mrc"):
                _cs = []
                for _cl in _chain_lines:
                    _rk, _mv, _ms = _mrc_candidate(_cl, catalog, carrier_id, plan_matcher, acc, ccmap,
                                                   hw_guard)
                    _cs.append(((_rk, str(_cl.get("product_desc") or ""), str(_cl.get("sku") or ""),
                                 str(_cl.get("serial_1") or ""), str(_cl.get("mdn") or "")),
                                _rk, _mv, _ms, _cl))
                if _cs:
                    _cs.sort(key=lambda x: x[0])
                    _rk, _mv, _ms, _mrc_line = _cs[0][1], _cs[0][2], _cs[0][3], _cs[0][4]
                    _mrc_override = (_mv, _ms)
                    if _ms == "none":
                        # NEVER a silent $0: say which activation, and what its lines actually said.
                        n_mrc_unresolved += 1
                        if len(warnings) < 200:
                            warnings.append({
                                "type": "mrc_unresolved", "sale_period": sale_period,
                                "month_index": month_index, "rep": rep, "store": store,
                                "trans_id": str(line.get("trans_id") or "").strip(),
                                "mdn": _norm_mdn(line.get("mdn")), "imei": _norm_mdn(line.get("serial_1")),
                                "products": [str(c[4].get("product_desc") or "")[:120] for c in _cs[:6]],
                                "detail": ("No rate-plan line could be identified on this activation, so the "
                                           "%-of-MRC installment resolved to $0 instead of paying a "
                                           "percentage of a device price. Confirm the plan under Plan "
                                           "Installments → MRC mapping, or add its wording to the "
                                           "rate-plan line matcher. If this category is not meant to be "
                                           "paid monthly at all, set it to a one-time FLAT amount under "
                                           "Plan Installments → Flat payout by category."),
                                "fix_routes": ["mrc_mapping", "plan_line_matcher", "category_flat_payout"]})
                    elif len({round(safe_float(c[2]), 2) for c in _cs if c[1] == _rk}) > 1:
                        # two equally-ranked lines disagree on the MRC — the catalog is the tie-breaker
                        n_mrc_ambiguous += 1
                        if len(warnings) < 200:
                            warnings.append({
                                "type": "mrc_ambiguous", "sale_period": sale_period,
                                "month_index": month_index, "rep": rep, "store": store,
                                "trans_id": str(line.get("trans_id") or "").strip(),
                                "imei": _norm_mdn(line.get("serial_1")), "chosen_mrc": round(safe_float(_mv), 2),
                                "candidates": [{"product": str(c[4].get("product_desc") or "")[:120],
                                                "mrc": round(safe_float(c[2]), 2)} for c in _cs[:6]
                                               if c[1] == _rk],
                                "detail": ("Two lines of this activation look equally like the rate plan but "
                                           "imply different MRCs — the highest-confidence one was used. "
                                           "Confirm the correct plan under Plan Installments → MRC mapping.")})
                amount, mrc, mrc_src = _line_amount(line, iline, catalog, carrier_id, _mrc_override)
            elif _mrc_override is not None:
                # MA TX-resolved MRC (mig 308): mrc_source stamps 'ma_tx_activation' on the row.
                amount, mrc, mrc_src = _line_amount(line, iline, catalog, carrier_id, _mrc_override)
            else:
                amount, mrc, mrc_src = _line_amount(line, iline, catalog, carrier_id)

            # ── DEVICE-CATEGORY QUALIFICATION (mig 245; owner 2026-07-27) ──────────────────────
            # "the tablet dont qualify for the monthly payout." Resolved on the WHOLE activation (a
            # tablet sale carries a tablet line, a tablet-plan line and a SIM line), then checked
            # against this schedule's / this org's / the owner's default include-exclude set. A
            # non-qualifying activation emits NO installment at all: no pay, no ledger row and no
            # withheld flag — it is not held pending residual, it simply does not qualify. It is
            # NEVER silent: every excluded chain is counted with its dollars in `category_guard` and
            # summarised in `warnings`, which is what the operator reads after Run Calculation.
            _qual, _qsrc = ((qual_override, "override") if qual_override is not None
                            else icat.qualification_for(sched, org_qual))
            cat_sources.add(_qsrc)
            _cat, _cev = icat.resolve_chain_category(_chain_lines or [line], cat_rules,
                                                     catalog_cat_of=cat_lookup,
                                                     is_accessory=_is_acc_row)

            # ── FLAT (ONE-TIME) PAYOUT BY CATEGORY (mig 256; owner 2026-08-01) ─────────────────
            # "fwa is paid on flat rate should not be in monthly payments - fix but dont hard code".
            # The category is whatever the TENANT's own rules said above — there is no carrier,
            # tenant or product literal in this branch. A category on 'flat_once' WITH an
            # owner-entered amount pays that amount ONCE (month `pay_month`, default the sale
            # month) and its other months emit nothing. A category on 'flat_once' with NO amount
            # is NOT active: the chain keeps paying exactly as it does today and we shout. We never
            # guess a payout and never manufacture a $0.
            _pay_cfg, _pay_src = ((payout_override, "override") if payout_override is not None
                                  else icpay.payout_for(sched, org_payout))
            _flat = icpay.resolve_flat(_cat, _pay_cfg, num_months=int(sched.get("num_months") or 1))
            if _flat["mode"] == "flat_once":
                flat_sources.add(_pay_src)
            if _flat["active"] and month_index == _flat["pay_month"]:
                # The ONE payment. Substituted BEFORE the category counters so every downstream
                # number (category_guard, by_rep, the ledger row, the withheld-flag text) reports
                # the amount actually decided rather than the installment it replaced.
                _fp = flat_paid.setdefault(_cat, {"chains": 0, "amount": 0.0, "replaced": 0.0,
                                                  "flat_amount": _flat["amount"],
                                                  "pay_month": _flat["pay_month"],
                                                  "clamped": bool(_flat["clamped"]),
                                                  "reps": {}, "examples": []})
                _fp["chains"] += 1
                _fp["replaced"] = round(_fp["replaced"] + safe_float(amount), 2)
                _fp["amount"] = round(_fp["amount"] + safe_float(_flat["amount"]), 2)
                _fp["reps"][repU_cat(rep)] = round(_fp["reps"].get(repU_cat(rep), 0.0)
                                                    + safe_float(_flat["amount"]), 2)
                if len(_fp["examples"]) < 8:
                    _fp["examples"].append({
                        "trans_id": str(line.get("trans_id") or "").strip(),
                        "imei": _norm_mdn(line.get("serial_1")), "rep": rep,
                        "month_index": month_index,
                        "installment_amount": round(safe_float(amount), 2),
                        "flat_amount": round(safe_float(_flat["amount"]), 2),
                        "product": (_cev or {}).get("product")})
                n_flat_paid += 1
                flat_paid_amt = round(flat_paid_amt + safe_float(_flat["amount"]), 2)
                flat_active_keys.add((str(line.get("trans_id") or "").strip(), month_index))
                amount = round(safe_float(_flat["amount"]), 2)
            elif _flat["mode"] == "flat_once" and not _flat["active"]:
                # LOUD, never silent. Reported once per category with its chain count + the dollars
                # that are STILL being paid monthly because no amount was entered.
                _fu = flat_unconfigured.setdefault(_cat, {"chains": 0, "amount": 0.0,
                                                          "reason": _flat["reason"]})
                _fu["chains"] += 1
                _fu["amount"] = round(_fu["amount"] + safe_float(amount), 2)
            _cc = cat_counts.setdefault(_cat, {"chains": 0, "amount": 0.0, "qualifies": bool(_qual.get(_cat, True))})
            _cc["chains"] += 1
            _cc["amount"] = round(_cc["amount"] + safe_float(amount), 2)
            if _cat == "unknown":
                n_cat_unknown += 1
                if len(warnings) < 200:
                    warnings.append({
                        "type": "category_unknown", "sale_period": sale_period,
                        "month_index": month_index, "rep": rep, "store": store,
                        "trans_id": str(line.get("trans_id") or "").strip(),
                        "imei": _norm_mdn(line.get("serial_1")), "amount": round(safe_float(amount), 2),
                        "paid": bool(_qual.get("unknown", True)),
                        "products": (_cev or {}).get("products") or [],
                        "detail": ("This activation's device category could not be determined from its "
                                   "Department / Category / product wording, so the multi-month "
                                   "include-exclude list could not be applied. It "
                                   + ("STILL PAID (the default for an unclassifiable activation). "
                                      if _qual.get("unknown", True) else "did NOT pay. ")
                                   + "Map it under Plan Installments → Qualifying categories.")})
            if not _qual.get(_cat, True):
                n_cat_excluded += 1
                cat_excluded_amt = round(cat_excluded_amt + safe_float(amount), 2)
                _ex = cat_excluded.setdefault(_cat, {"chains": 0, "amount": 0.0, "reps": {},
                                                     "examples": []})
                _ex["chains"] += 1
                _ex["amount"] = round(_ex["amount"] + safe_float(amount), 2)
                _ex["reps"][repU_cat(rep)] = round(_ex["reps"].get(repU_cat(rep), 0.0)
                                                   + safe_float(amount), 2)
                if len(_ex["examples"]) < 8:
                    _ex["examples"].append({
                        "trans_id": str(line.get("trans_id") or "").strip(),
                        "imei": _norm_mdn(line.get("serial_1")), "mdn": mdn,
                        "rep": rep, "month_index": month_index,
                        "amount": round(safe_float(amount), 2), "mrc": round(safe_float(mrc), 2),
                        "product": (_cev or {}).get("product"),
                        "matched_field": (_cev or {}).get("matched_field"),
                        "matched_value": (_cev or {}).get("matched_value")})
                continue

            # mig 256: under an ACTIVE flat payout the chain pays ONCE. Every other month of it
            # emits nothing — no ledger row, no withheld flag — because those months do not exist,
            # exactly as a non-qualifying category emits nothing. Counted with the dollars they
            # would have paid, per rep, so the change is never invisible.
            if _flat["active"] and month_index != _flat["pay_month"]:
                n_flat_suppressed += 1
                flat_suppressed_amt = round(flat_suppressed_amt + safe_float(amount), 2)
                _fs = flat_suppressed.setdefault(_cat, {"chains": 0, "amount": 0.0, "reps": {},
                                                        "months": {}, "examples": []})
                _fs["chains"] += 1
                _fs["amount"] = round(_fs["amount"] + safe_float(amount), 2)
                _fs["reps"][repU_cat(rep)] = round(_fs["reps"].get(repU_cat(rep), 0.0)
                                                    + safe_float(amount), 2)
                _fs["months"][str(month_index)] = int(_fs["months"].get(str(month_index), 0)) + 1
                if len(_fs["examples"]) < 8:
                    _fs["examples"].append({
                        "trans_id": str(line.get("trans_id") or "").strip(),
                        "imei": _norm_mdn(line.get("serial_1")), "rep": rep, "mdn": mdn,
                        "month_index": month_index,
                        "amount": round(safe_float(amount), 2),
                        "product": (_cev or {}).get("product")})
                # A suppressed month does not pay at all, so its earlier `mrc_unresolved` warning
                # ("the %-of-MRC installment resolved to $0") is moot for it too — withdraw it with
                # the same key set rather than sending the operator to fix an unread input.
                flat_active_keys.add((str(line.get("trans_id") or "").strip(), month_index))
                _flat_suppressed_keys.add((str(line.get("trans_id") or "").strip(), month_index))
                continue

            # ONE consistent row label (owner 2026-07-27 deliverable 3): DEVICE + RATE PLAN together,
            # on every surface, whichever half the chain's identity happened to come from.
            _dev_line = next((r for r in _dev_order(_chain_lines or [line])
                              if icat.serial_kind(r.get("serial_1")) == "imei"), None)
            _plan_disp = None
            if _mrc_line is not None and not _line_is_hardware(_mrc_line, plan_matcher, hw_guard):
                _plan_disp = _mrc_line
            if _plan_disp is None:
                _plan_disp = next((r for r in (_chain_lines or [line])
                                   if _line_is_plan_line(r, plan_matcher)
                                   and not _line_is_hardware(r, plan_matcher, hw_guard)), None)
            device_product = str((_dev_line or {}).get("product_desc") or "")[:200]
            plan_product = str((_plan_disp or {}).get("product_desc") or "")[:200]
            if not device_product and not plan_product:
                device_product = str(line.get("product_desc") or "")[:200]

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
                    _ma_periods = _ma_lookup_periods(gate_cfg, sale_period, pay_period)
                    gate_met, ma_ev = _gate_met_ma(gate_line, _ma_index_for(_ma_periods),
                                                   month_index, gate_cfg)
                    if ma_ev is not None:
                        ma_ev = {**ma_ev, "lookup_periods": list(_ma_periods)}
                else:
                    gate_met = True
                gate_kind = "ma_residual"
            elif gate_source == "ma_tx":
                # MA DAILY TX gate (mig 308; config opt-in, never a default flip): month-n evidence
                # is the UNION of (i) the ma_commission spiff evidence — reused UNCHANGED — and (ii)
                # the MA Daily Tx 'MONTH n' rows reached through the two-hop
                # serial → raw_ma_commission.activation_order → raw_ma_daily_tx.order_number join.
                # Month 1 additionally counts the linked Activation Order row itself. Direction/
                # min-amount semantics identical to the ma_commission gate; ma_max_month (config,
                # up to 16 for a Total org) caps the horizon.
                gated = month_index >= gate_from and gate_mode != "none"
                if gated:
                    _ma_periods = _ma_lookup_periods(gate_cfg, sale_period, pay_period)
                    gate_met, ma_ev = _gate_met_ma_tx(gate_line, _ma_index_for(_ma_periods),
                                                      _ma_tx_indexes_for(_ma_periods, gate_cfg),
                                                      month_index, gate_cfg)
                    if ma_ev is not None:
                        ma_ev = {**ma_ev, "lookup_periods": list(_ma_periods)}
                else:
                    gate_met = True
                gate_kind = "ma_residual"
            else:
                # BOOST / raw_mi paid gate — UNCHANGED (byte-identical to pre-mig-223).
                gated = month_index >= gate_from and gate_mode != "none"
                gate_met, mi_row = _gate_met(gate_line, mi_index, gate_mode) if gated else (True, None)
                gate_kind = None

            # ── EXPECTED vs EARNED (mig 258; owner 2026-08-01) ────────────────────────────────
            # `expected` is the amount this month WOULD pay — exactly what the gate is about to
            # throw away when it is unmet. It is carried on the row and NEVER added to by_rep or
            # totals: "calculate the expected commission as a separate column but not use that to
            # pay out". EARNED auto-fill needs no code — it IS `gate_met` below, unchanged.
            expected = round(safe_float(amount), 2)
            x_in_window = xcomm.in_window(month_index, xcfg)
            x_prov = None
            if not gate_met and xcfg.get("enabled") and x_in_window:
                _pk = xcomm.promote_key(pay_period, line.get("trans_id"), mdn, month_index)
                _pr = xindex.get(_pk)
                if _pr is not None:
                    x_seen_keys.add(_pk)
                    _ev = xcomm.evaluate(_pr, expected, xcfg)
                    _rec = {"rep": rep, "store": store, "trans_id": str(line.get("trans_id") or "").strip(),
                            "mdn": mdn, "imei": serial, "month_index": month_index,
                            "sale_period": sale_period, "pay_period": pay_period,
                            "expected_now": _ev["expected_now"],
                            "expected_at_promote": _ev["expected_at_promote"],
                            "promoted_by": _pr.get("promoted_by"),
                            "promoted_at": _pr.get("promoted_at"),
                            "reason": _pr.get("reason"), "promote_id": _pr.get("id")}
                    if _ev["stale"]:
                        x_stale.append({**_rec, "mode": _ev["mode"], "paid": bool(_ev["apply"])})
                        if len(warnings) < 200:
                            warnings.append({
                                "type": "promote_expected_changed", "rep": rep, "store": store,
                                "trans_id": _rec["trans_id"], "imei": serial, "mdn": mdn,
                                "month_index": month_index,
                                "expected_at_promote": _ev["expected_at_promote"],
                                "expected_now": _ev["expected_now"], "paid": bool(_ev["apply"]),
                                "detail": (
                                    f"Month {month_index} for {mdn or serial} was manually moved to "
                                    f"EARNED at ${safe_float(_ev['expected_at_promote']):,.2f}, but "
                                    f"this recalculation expects ${safe_float(_ev['expected_now']):,.2f}. "
                                    + ("It was PAID at the current figure, not the approved one — "
                                       "confirm that is right." if _ev["apply"] else
                                       "It was NOT paid: the approved figure no longer matches, so it "
                                       "is being held for re-approval rather than paid at a number "
                                       "nobody approved. Re-approve it to release it."))})
                    if _ev["apply"]:
                        gate_met = True
                        amount = _ev["amount"]
                        x_prov = {**_rec, "stale": _ev["stale"], "amount": _ev["amount"]}
                        x_applied.append(x_prov)
            elif gate_met and xcfg.get("enabled") and x_in_window:
                # The gate met on its own AND a promote exists: the carrier statement caught up, so
                # the promote is now REDUNDANT. Not an error — reported so it can be cleaned up, and
                # explicitly NOT double-counted (the row pays once, through the gate).
                _pk = xcomm.promote_key(pay_period, line.get("trans_id"), mdn, month_index)
                if _pk in xindex:
                    x_seen_keys.add(_pk)
                    x_redundant.append({"rep": rep, "trans_id": _pk[1], "mdn": mdn, "imei": serial,
                                        "month_index": month_index, "amount": expected,
                                        "promote_id": xindex[_pk].get("id"),
                                        "promoted_by": xindex[_pk].get("promoted_by")})

            repU = rep.upper()
            if gate_met:
                if amount:
                    by_rep[repU] = round(by_rep.get(repU, 0.0) + amount, 2)
                    total_amt += amount
                n_paid += 1
                status = "paid_manual_promote" if x_prov is not None else "paid"
            else:
                n_withheld += 1
                status = "withheld_unpaid"
                # TWO flags for a sold-but-unpaid line (existing flags machinery; delete-first by source).
                # Under the month-1 activation-payment gate the miss reason is "no first-month payment
                # collected at activation" rather than "not receiving residual" — same two flag sources.
                base = {"period": pay_period, "period_month": pm.get("month"),
                        "period_year": pm.get("year"), "epay_salesperson": rep,
                        "store_address": store, "mdn": mdn, "imei": serial,
                        # mig 287 identity: the transaction + which installment month it is. Lets the
                        # flag be re-found on the next recalculation so the DM's review survives, and
                        # keeps two installments of the SAME line in one pay period distinct.
                        "source_ref": f"{str(line.get('trans_id') or '').strip()}|m{month_index}",
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
                "device_category": _cat, "device_product": device_product,
                "plan_product": plan_product,
                "display_label": installment_label(device_product, plan_product,
                                                   None if _flat["active"] else
                                                   (mrc if str(iline.get("payout_kind") or "").strip().lower() == "pct_mrc" else None)),
                "org_id": org_id, "trans_id": str(line.get("trans_id") or "").strip(),
                "mdn": mdn, "serial_1": serial, "plan_id": plan.get("id"),
                "schedule_id": sched.get("id"), "store": store, "epay_salesperson": rep,
                "sale_period": sale_period, "pay_period": pay_period, "month_index": month_index,
                # mig 256: a flat-paid chain says so in the audit trail rather than claiming the
                # schedule's kind ('pct_mrc') while paying a flat dollar. Only ever set when the
                # tenant configured a flat category, so every existing row is byte-identical.
                "payout_kind": ("category_flat" if _flat["active"] else iline.get("payout_kind")),
                "mrc_at_pay": mrc, "mrc_source": mrc_src,
                "amount": round(safe_float(amount) if gate_met else 0.0, 2),
                # mig 258: the number this month WOULD pay, carried on every row and summed into
                # NOTHING. `amount` above is untouched — expected is a column, not a payout.
                "expected_amount": expected,
                "expected_in_window": bool(x_in_window),
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
                # mig 232: WHICH statement period(s) the evidence was read from (default ['<sale>'],
                # i.e. pre-mig-232). In-memory only — _persist writes a fixed column list.
                ledger_row["ma_lookup_periods"] = (ma_ev or {}).get("lookup_periods")
            # MA TX provenance (mig 308): the order_number/account_id the two-hop linkage resolved —
            # from the MRC hit (basis 'ma_tx_activation') or the ma_tx gate evidence. Written
            # ADAPTIVELY by _persist (like expected_amount), so a DB without mig 308 degrades; rows
            # whose evidence did not come from MA TX carry NO new keys (shape byte-identical).
            _ma_tx_order = ((_ma_tx_prov or {}).get("order_number")
                            or (ma_ev or {}).get("order_number"))
            if _ma_tx_order:
                ledger_row["order_number"] = str(_ma_tx_order)[:100]
                _ma_tx_acct = ((_ma_tx_prov or {}).get("account_id")
                               or (ma_ev or {}).get("account_id"))
                if _ma_tx_acct:
                    ledger_row["account_id"] = str(_ma_tx_acct)[:100]
            # OPT-IN provenance (absent unless this chain actually needed the new logic, so an
            # ordinary one-line activation keeps the pre-fix ledger shape byte-for-byte). In-memory
            # only — _persist writes a fixed column list.
            # OPT-IN provenance: only a row that a human actually promoted carries these, so every
            # ordinary row keeps its exact pre-258 shape apart from the two expected keys above.
            if x_prov is not None:
                ledger_row["status"] = "paid_manual_promote"
                ledger_row["gate_kind"] = "manual_promote"
                ledger_row["promote_id"] = x_prov.get("promote_id")
                ledger_row["promoted_by"] = x_prov.get("promoted_by")
                ledger_row["promoted_at"] = x_prov.get("promoted_at")
                ledger_row["promote_reason"] = x_prov.get("reason")
                ledger_row["promote_stale"] = bool(x_prov.get("stale"))
            if _flat["active"]:
                ledger_row["category_flat"] = True
                ledger_row["category_flat_amount"] = _flat["amount"]
                ledger_row["category_flat_pay_month"] = _flat["pay_month"]
                ledger_row["category_flat_source"] = _pay_src
            if _mrc_line is not None and _mrc_line is not line:
                ledger_row["mrc_from_product"] = str(_mrc_line.get("product_desc") or "")[:200]
            if len(_cands) > 1:
                ledger_row["chain_lines_merged"] = len(_cands)
            ledger.append(ledger_row)

    # mig 256: a chain paid as a FLAT one-time amount does not care what its MRC resolved to, so the
    # `mrc_unresolved` warning it raised earlier in the loop (before its category was known) is no
    # longer true and would send the operator to fix an input the payout no longer reads. Dropped
    # ONLY for chains that actually went flat, and the counter is corrected with it. No flat
    # configuration => `flat_active_keys` is empty => this is a no-op.
    if flat_active_keys:
        _kept = [w for w in warnings
                 if not (w.get("type") == "mrc_unresolved"
                         and (str(w.get("trans_id") or ""), int(w.get("month_index") or 0))
                         in flat_active_keys)]
        n_mrc_unresolved -= (len(warnings) - len(_kept))
        warnings = _kept

    # SAME DEVICE, SAME MONTH, TWICE (owner 2026-07-27: IMEI 358662802056452 appears twice at $2.75).
    # The chain guard (mig 233) collapses duplicate LINES of one activation, but two chains can still
    # land on one device+month legitimately-looking ways: TWO ACTIVE SCHEDULES on the same plan (each
    # pays its own installment — real double pay, and _persist can only store one of the two rows), a
    # multi-subscriber transaction where the second subscriber BORROWED the only device serial on the
    # receipt, or the same device genuinely sold twice in the month (return + resale). We do not guess
    # which — we NAME it, with the trans/schedule/MDN evidence, so the operator can tell in one look.
    _dev_month = {}
    for _r in ledger:
        _k = (_norm_mdn(_r.get("serial_1")), int(_r.get("month_index") or 0))
        if _k[0]:
            _dev_month.setdefault(_k, []).append(_r)
    for (_ser, _mi), _rs in sorted(_dev_month.items()):
        if len(_rs) < 2:
            continue
        _scheds = {str(x.get("schedule_id") or "") for x in _rs}
        _txs = {str(x.get("trans_id") or "") for x in _rs}
        _mdns = {str(x.get("mdn") or "") for x in _rs}
        if len(_scheds) > 1:
            _why = ("two DIFFERENT installment schedules both pay this device — the rep is paid twice, "
                    "and only one of the two rows can be stored in the ledger (they share its unique "
                    "key). Deactivate the duplicate schedule under Plan Installments.")
        elif len(_txs) > 1:
            _why = ("this device appears on two different transactions in the period (e.g. a return and "
                    "a re-sale). Both chains pay unless one is voided/returned in the POS export.")
        else:
            _why = ("one transaction produced two subscriber chains that resolved to the SAME device "
                    "serial — the second subscriber's lines carry no serial of their own, so the "
                    "receipt's only IMEI was borrowed. If these are two real lines the pay is right and "
                    "only the display repeats; if not, the POS export must carry Serial 1 per line.")
        warnings.append({
            "type": "duplicate_device_month", "imei": _ser, "month_index": _mi,
            "rows": len(_rs), "rep": _rs[0].get("epay_salesperson"),
            "amount": round(sum(safe_float(x.get("amount")) for x in _rs), 2),
            "trans_ids": sorted(_txs), "mdns": sorted(_mdns), "schedules": sorted(_scheds),
            "label": _rs[0].get("display_label"),
            "detail": f"Device {_ser} has {len(_rs)} month-{_mi} installments in {pay_period}: {_why}"})

    # SUMMARY warnings — appended AFTER the per-chain cap so a big month can never hide the headline.
    for _k, _v in sorted(cat_excluded.items()):
        warnings.append({
            "type": "category_excluded", "category": _k,
            "category_label": icat.CATEGORY_LABELS.get(_k, _k),
            "chains": _v["chains"], "amount": round(_v["amount"], 2),
            "by_rep": _v["reps"], "examples": _v["examples"],
            "detail": (f"{_v['chains']} {icat.CATEGORY_LABELS.get(_k, _k).lower()} activation(s) did NOT "
                       f"pay a multi-month installment (${_v['amount']:,.2f} not paid) because "
                       f"'{icat.CATEGORY_LABELS.get(_k, _k)}' is unchecked under Plan Installments → "
                       f"Qualifying categories. Tick it to include them again.")})
    for _k, _v in sorted(flat_unconfigured.items()):
        warnings.append({
            "type": "flat_amount_unconfigured", "category": _k,
            "category_label": icat.CATEGORY_LABELS.get(_k, _k),
            "chains": _v["chains"], "amount": round(_v["amount"], 2),
            "detail": (f"'{icat.CATEGORY_LABELS.get(_k, _k)}' is set to a ONE-TIME FLAT payout but no "
                       f"amount has been entered, so {_v['chains']} activation(s) are STILL being paid "
                       f"as monthly installments (${_v['amount']:,.2f} this period) — nothing was "
                       f"zeroed and nothing was guessed. Enter the flat amount under Plan Installments "
                       f"-> Flat payout by category to make the switch take effect.")})
    for _k, _v in sorted(flat_suppressed.items()):
        warnings.append({
            "type": "flat_months_suppressed", "category": _k,
            "category_label": icat.CATEGORY_LABELS.get(_k, _k),
            "chains": _v["chains"], "amount": round(_v["amount"], 2),
            "by_rep": _v["reps"], "by_month": _v["months"], "examples": _v["examples"],
            "detail": (f"{_v['chains']} {icat.CATEGORY_LABELS.get(_k, _k).lower()} installment month(s) "
                       f"did NOT pay (${_v['amount']:,.2f}) because '{icat.CATEGORY_LABELS.get(_k, _k)}' "
                       f"is configured to pay a ONE-TIME FLAT amount instead of monthly installments. "
                       f"Change it back under Plan Installments -> Flat payout by category.")})
    for _k, _v in sorted(flat_paid.items()):
        warnings.append({
            "type": "flat_paid_summary", "category": _k,
            "category_label": icat.CATEGORY_LABELS.get(_k, _k),
            "chains": _v["chains"], "amount": round(_v["amount"], 2),
            "replaced_amount": round(_v["replaced"], 2), "by_rep": _v["reps"],
            "flat_amount": _v["flat_amount"], "pay_month": _v["pay_month"],
            "clamped": _v["clamped"], "examples": _v["examples"],
            "detail": (f"{_v['chains']} {icat.CATEGORY_LABELS.get(_k, _k).lower()} activation(s) were "
                       f"paid a ONE-TIME FLAT ${_v['flat_amount']:,.2f} in month {_v['pay_month']} "
                       f"(${_v['amount']:,.2f} in total) instead of the schedule's installment "
                       f"(${_v['replaced']:,.2f})."
                       + (" The configured pay month was beyond this schedule's length, so it landed "
                          "on the last month rather than never paying." if _v["clamped"] else ""))})
    if n_cat_unknown:
        warnings.append({
            "type": "category_unknown_summary", "chains": n_cat_unknown,
            "paid": bool(icat.normalize_qualification(
                {k: v for k, v in org_qual.items() if k in icat.CATEGORY_KEYS}).get("unknown", True)),
            "detail": (f"{n_cat_unknown} activation(s) could not be classified into a device category. "
                       f"They were treated per the 'Could not be classified' switch. Add a rule under "
                       f"Plan Installments → Qualifying categories so they stop being guesses.")})

    # mig 258 — a promote that matched NO chain-month this run is REPORTED, never swallowed. The
    # reason matters: a flat-paid category has no months 2..N (mig 256 suppressed them), a
    # non-qualifying category emits nothing (mig 245), a month can fall outside the window, or the
    # transaction simply is not in this period any more.
    x_unapplied = []
    for _k, _pr in sorted(xindex.items()):
        if _k in x_seen_keys:
            continue
        if not xcfg.get("enabled"):
            _why = "disabled"
        elif not xcomm.in_window(_k[3], xcfg):
            _why = "out_of_window"
        elif (_k[1], _k[3]) in _flat_suppressed_keys:
            _why = "month_suppressed"
        else:
            _why = "chain_not_found"
        x_unapplied.append({"trans_id": _k[1], "mdn": _k[2], "month_index": _k[3],
                            "pay_period": _k[0], "promote_id": _pr.get("id"),
                            "promoted_by": _pr.get("promoted_by"),
                            "expected_at_promote": _pr.get("expected_at_promote"),
                            "reason_code": _why,
                            "reason": xcomm.UNAPPLIED_REASONS.get(_why, _why)})
    if x_unapplied:
        warnings.append({
            "type": "promote_unapplied", "count": len(x_unapplied), "items": x_unapplied[:20],
            "detail": (f"{len(x_unapplied)} manual expected-to-earned promote(s) did not apply to any "
                       f"installment this period. Nothing was paid for them and nothing was lost — "
                       f"each one is listed with the reason (the month may not exist because the "
                       f"category is paid as a one-time flat amount, the month may be outside the "
                       f"expected window, or the transaction may not be in this period).")})
    if x_redundant:
        warnings.append({
            "type": "promote_redundant", "count": len(x_redundant), "items": x_redundant[:20],
            "detail": (f"{len(x_redundant)} manual promote(s) are no longer needed — the carrier/"
                       f"master-agent statement has since proved these months paid, so they paid "
                       f"through the normal gate. Each paid ONCE. You can revoke the promotes.")})

    _persisted = None
    if persist:
        _persisted = _persist(client, org_id, pay_period, ledger)

    ledger.sort(key=lambda x: -(x.get("amount") or 0))
    return {"pay_period": pay_period, "by_rep": by_rep, "ledger": ledger, "flags": flags,
            "schedules": len(scheds),
            "totals": {"amount": round(total_amt, 2), "paid": n_paid, "withheld": n_withheld,
                       "reps": len(by_rep)},
            # mig 233 transparency, kept OUT of `totals` so that dict stays byte-identical for every
            # existing consumer/harness: duplicate lines of one activation collapsed into a single
            # chain, and %-of-MRC chains whose rate-plan line is unresolved (paid $0 rather than a
            # percentage of a device price) or ambiguous.
            "chain_guard": {"deduped": n_dedup, "mrc_unresolved": n_mrc_unresolved,
                            "mrc_ambiguous": n_mrc_ambiguous, "mrc_basis": mrc_basis,
                            "hardware_guard": bool(hw_guard.get("enabled")),
                            "ledger_rows_dropped": _persisted.get("dropped", 0) if _persisted else 0},
            # DEVICE-CATEGORY QUALIFICATION (mig 245) — kept OUT of `totals` so that dict stays
            # byte-identical for every existing consumer/harness.
            "category_guard": {"excluded_chains": n_cat_excluded,
                               "excluded_amount": round(cat_excluded_amt, 2),
                               "unknown_chains": n_cat_unknown,
                               "by_category": cat_counts, "excluded": cat_excluded,
                               "config_source": sorted(cat_sources),
                               "qualification": {k: bool(v) for k, v in
                                                 icat.normalize_qualification(
                                                     {k2: v2 for k2, v2 in org_qual.items()
                                                      if k2 in icat.CATEGORY_KEYS}).items()}},
            # FLAT (ONE-TIME) PAYOUT BY CATEGORY (mig 256) — kept OUT of `totals` so that dict stays
            # byte-identical for every existing consumer/harness.
            "flat_guard": {"flat_chains": n_flat_paid, "flat_amount": round(flat_paid_amt, 2),
                           "suppressed_months": n_flat_suppressed,
                           "suppressed_amount": round(flat_suppressed_amt, 2),
                           "paid": flat_paid, "suppressed": flat_suppressed,
                           "unconfigured": flat_unconfigured,
                           "config_source": sorted(flat_sources),
                           "flat_categories": icpay.configured_categories(
                               icpay.normalize_payout({k2: v2 for k2, v2 in org_payout.items()
                                                       if k2 in icpay.CATEGORY_KEYS}))},
            # EXPECTED vs EARNED (mig 258) — kept OUT of `totals` so that dict stays byte-identical
            # for every existing consumer/harness. `expected_total` is a REPORTING figure: it is the
            # sum of what the in-window months WOULD pay, and no payout reads it.
            "expected_guard": {**xcomm.summarize(x_applied, x_unapplied, x_stale, x_redundant),
                               "config": {k: v for k, v in xcfg.items() if not k.startswith("_")},
                               "config_stored": bool(xcfg.get("_stored")),
                               "expected_total": round(sum(safe_float(r.get("expected_amount"))
                                                          for r in ledger
                                                          if r.get("expected_in_window")), 2),
                               "expected_unearned_total": round(
                                   sum(safe_float(r.get("expected_amount")) for r in ledger
                                       if r.get("expected_in_window") and not r.get("paid_gate_met")), 2),
                               "persist_columns": (_persisted or {}).get("columns") if _persisted else None},
            "warnings": warnings,
            "note": None}


def _persist(client, org_id, pay_period, ledger):
    """Write this pay period's sale_installment_ledger (opt-in). Returns {'rows','dropped','deleted'}.

    SELF-HEALING (owner money fix 2026-07-25): the period's existing rows for this org are DELETED first,
    then the fresh set is upserted. The old pure-upsert never removed a row the engine no longer emits, so
    a chain that stopped qualifying — e.g. the duplicate device-line chain this release collapses — would
    have survived every recalculation forever. The delete is skipped when there is nothing to write, so a
    transient read failure can never empty a period; and the write stays an UPSERT so a partially-applied
    delete + retry is still idempotent.

    DUPLICATE-SAFE: rows are de-duplicated on the table's own UNIQUE key
    (org_id, trans_id, mdn, month_index, pay_period) BEFORE the write. Postgres rejects an INSERT ... ON
    CONFLICT that touches one row twice in a single statement, so a single duplicate used to fail — and
    silently discard — a whole 500-row batch. Anything dropped is REPORTED (two schedules paying the same
    device+month cannot both be stored; the money is still correct, only the audit row is).
    """
    cols = ("org_id", "trans_id", "mdn", "serial_1", "plan_id", "schedule_id", "store",
            "epay_salesperson", "sale_period", "pay_period", "month_index", "payout_kind",
            "mrc_at_pay", "mrc_source", "amount", "paid_gate_met", "gate_mode", "status",
            "matched_mi_period")
    # mig 258 adds four columns, mig 308 two more (MA TX provenance). Each set is written ONLY if
    # its migration has been run — see the TIERED ADAPTIVE write below. The lists are deliberately
    # separate so each narrower set can always be fallen back to.
    extra = ("expected_amount", "promote_id", "promoted_by", "promoted_at")
    extra308 = ("order_number", "account_id")
    rows, seen, dropped = [], set(), 0
    for d in ledger:
        if not (d.get("trans_id") or d.get("mdn")):
            continue
        key = (str(d.get("trans_id") or ""), str(d.get("mdn") or ""),
               int(d.get("month_index") or 0), str(d.get("pay_period") or ""))
        if key in seen:
            dropped += 1
            continue
        seen.add(key)
        rows.append({k: d.get(k) for k in (cols + extra + extra308)})
    deleted = False
    if rows:
        try:
            (client.schema("commcalc").table("sale_installment_ledger").delete()
             .eq("org_id", org_id).in_("pay_period", _pvariants(pay_period)).execute())
            deleted = True
        except Exception:
            deleted = False
    # TIERED ADAPTIVE WRITE (mig 258 + mig 308). The delete above has ALREADY run, so a write that
    # fails for every batch would leave the period EMPTY — and the original `except: pass` would have
    # hidden it. Tiers, widest first; a rejection degrades ONE tier for this and every later batch
    # and is REPORTED, never silent:
    #   'extended308' — base + mig-258 + mig-308 (order_number/account_id) columns
    #   'extended'    — base + mig-258 columns (DB without mig 308; expected_amount still lands —
    #                   the 308 columns must never cost a tenant its mig-258 audit trail)
    #   'base'        — the mig-201 column set (DB without mig 258)
    tiers = (("extended308", cols + extra + extra308), ("extended", cols + extra), ("base", cols))
    ti, wrote, failed = 0, 0, 0
    for i in range(0, len(rows), 500):
        batch = rows[i:i + 500]
        while True:
            name, keyset = tiers[ti]
            try:
                client.schema("commcalc").table("sale_installment_ledger").upsert(
                    [{k: r.get(k) for k in keyset} for r in batch],
                    on_conflict="org_id,trans_id,mdn,month_index,pay_period").execute()
                wrote += len(batch)
                break
            except Exception:
                if ti + 1 < len(tiers):
                    ti += 1        # columns absent (migration unrun) — degrade once, keep writing
                    continue
                failed += len(batch)
                break
    return {"rows": len(rows), "dropped": dropped, "deleted": deleted,
            "columns": tiers[ti][0], "written": wrote, "write_failed": failed}


# ── IMPACT PREVIEW (read-only; Gate-2 review artifact for mig 223) ──────────────────────────────────
def _flip_key(r):
    """Stable per-installment identity for diffing two gate runs. Includes plan_id + schedule_id so two
    schedules covering the SAME device+month don't collide in the old-status map (n1)."""
    return (r.get("sale_period"), r.get("month_index"), str(r.get("trans_id") or ""),
            str(r.get("mdn") or ""), str(r.get("serial_1") or ""),
            str(r.get("plan_id") or ""), str(r.get("schedule_id") or ""))


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
