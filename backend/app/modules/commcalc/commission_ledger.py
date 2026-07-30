"""SAP-style CANONICAL commission/payout ledger — normalise ANY carrier's commission file into five
canonical buckets via a per-tenant rule map (commcalc.commission_category_map, migration 071), and book
each line into commcalc.commission_ledger.

THE FIVE CANONICAL CATEGORIES (carrier-agnostic; "essentially the same" as the Boost buckets, SAP-style):
    commission        — Commission
    spiff             — Spiff
    equipment_rebate  — Equipment rebate
    residual_monthly  — Residual / monthly incentives
    autopay_residual  — Auto Pay residual

A payout paid over many months stays ONE category but each installment keeps its payment_month, so it's
classified once and displayed as it's paid. On the MA Daily Tx (Total/VidaPay), a NEGATIVE amount is a
payout; positives are dealer charges (stored, is_payout=false, kept out of the five buckets).

DEGRADES GRACEFULLY + BOOST-SAFE: when the map table is empty/un-migrated the classifier falls back to
DEFAULT_RULES below (mirrors the 071 seed), so it works the moment the code deploys. This module touches
only the two NEW 071 tables — never the live calc, rep_commissions, carrier_commission, or legacy uploads.
"""
import re

ORG_HOUSE = "00000000-0000-0000-0000-000000000001"
LEDGER_TABLE = "commission_ledger"
MAP_TABLE = "commission_category_map"

# canonical payout buckets (the five amount columns on commission_ledger) + non-payout sentinels
CATEGORIES = ["commission", "spiff", "equipment_rebate", "residual_monthly", "autopay_residual"]
CATEGORY_LABELS = {
    "commission": "Commission",
    "spiff": "Spiff",
    "equipment_rebate": "Equipment rebate",
    "residual_monthly": "Residual / monthly incentives",
    "autopay_residual": "Auto Pay residual",
    "charge": "Bill / activation payment (not a payout)",
    "other": "Other payout (unmapped)",
}
MATCH_FIELDS = ["product_name", "order_type"]
MATCH_OPS = ["contains", "equals"]
SIGN_RULES = ["negative_only", "any"]

# Fallback rules when commission_category_map is empty/un-migrated. (match_field, op, pattern, category,
# sign_rule, priority) — mirror the 071 seed EXACTLY so pre/post-migration behaviour is identical.
DEFAULT_RULES = [
    ("product_name", "contains", "Commission", "commission", "negative_only", 10),
    ("product_name", "contains", "SPF", "spiff", "negative_only", 20),
    ("product_name", "contains", "Spiff", "spiff", "negative_only", 21),
    ("product_name", "contains", "Autopay Residual", "autopay_residual", "negative_only", 30),
    ("product_name", "contains", "Residual", "residual_monthly", "negative_only", 40),
    ("product_name", "contains", "Subsidy", "equipment_rebate", "negative_only", 50),
    ("order_type", "contains", "Promo", "equipment_rebate", "negative_only", 51),
]


# Preconfigured TEMPLATES a new tenant can adopt or fork. The source_report key namespaces a whole
# rule-set, so a tenant picks "Total" or "Boost" out of the box, or creates their own. Total (ma_daily_tx)
# ships seeded in 071; Boost (boost) ships seeded in 072 — the curated Boost Description→Category taxonomy
# (Commission Categories Master File) mapped onto these same five canonical buckets (exact rules, sign_rule
# 'any' because Boost commission amounts are POSITIVE, vs MA's negative=payout convention).
# `ma_commission` was added 2026-07-30 alongside the MA-data refresh (ledger_ma_sync.py): the MA
# Commission Details report carries the per-activation components (device/consumer margin, rebate, the
# 1st–6th month spiffs) that MA Daily Tx does not, and it needs its OWN rule namespace so its labels never
# reclassify a Daily Tx line. Adding a key here only adds a picker option + a rule namespace; it seeds no
# rule and moves no money (a label matching no rule is booked 'other' and surfaced).
BUILTIN_TEMPLATES = {
    "ma_daily_tx": "Total Wireless (MA Daily Tx)",
    "ma_commission": "Total Wireless (MA Commission Details)",
    "boost": "Boost (ePay / DLAR)",
}


def list_templates(client, org_id):
    """Built-in templates + any tenant-created source_report rule-set. Each: {key, label, builtin, rule_count}."""
    counts = {}
    try:
        rows = (client.schema("commcalc").table(MAP_TABLE).select("source_report")
                .eq("org_id", org_id).execute().data) or []
        for r in rows:
            k = r.get("source_report")
            if k:
                counts[k] = counts.get(k, 0) + 1
    except Exception:
        pass
    keys = list(BUILTIN_TEMPLATES) + [k for k in sorted(counts) if k not in BUILTIN_TEMPLATES]
    return [{"key": k, "label": BUILTIN_TEMPLATES.get(k, k), "builtin": k in BUILTIN_TEMPLATES,
             "rule_count": counts.get(k, 0)} for k in keys]


def _sf(v):
    from app.modules.commcalc.calculator import safe_float
    return safe_float(v)


def load_rules(client, org_id, source_report="ma_daily_tx"):
    """Effective classification rules for (org, source_report), ascending priority (first match wins).
    Rules with source_report '*' (any report) are included. Falls back to DEFAULT_RULES if the map table
    is empty or migration 071 isn't applied yet."""
    rows = []
    try:
        rows = (client.schema("commcalc").table(MAP_TABLE).select("*")
                .eq("org_id", org_id).in_("source_report", [source_report, "*"]).execute().data) or []
    except Exception:
        rows = []
    if not rows:
        return [{"match_field": mf, "match_op": op, "pattern": pat, "category": cat,
                 "sign_rule": sr, "priority": pr} for (mf, op, pat, cat, sr, pr) in DEFAULT_RULES]
    return sorted(rows, key=lambda r: (r.get("priority") if r.get("priority") is not None else 100))


_MONTH_RE = re.compile(r"MONTH\s*(\d+)|(?<![A-Za-z0-9])M(\d+)(?![A-Za-z0-9])", re.I)


def parse_payment_month(product_name):
    """Extract the payment month from a product label: 'TBV MONTH 4 …' -> 4, 'Commission - M1 Proration'
    -> 1, 'SPF Month 1' -> 1. None when no month token is present."""
    m = _MONTH_RE.search(str(product_name or ""))
    if not m:
        return None
    g = m.group(1) or m.group(2)
    try:
        return int(g)
    except (TypeError, ValueError):
        return None


def _match(rule, order_type, product_name):
    field = rule.get("match_field") or "product_name"
    val = (product_name if field == "product_name" else order_type) or ""
    val = str(val).lower()
    pat = str(rule.get("pattern") or "").lower()
    if not pat:
        return False
    op = rule.get("match_op") or "contains"
    return val == pat if op == "equals" else (pat in val)


def classify(raw_amount, order_type, product_name, rules):
    """Return (category, is_payout) for one source line. Negative amount = payout → the first matching
    rule's category; positive = a dealer 'charge' (never a payout bucket); an unmatched payout = 'other'
    (surfaced, never silently dropped). A rule with sign_rule='any' can also classify a positive line."""
    amt = _sf(raw_amount)
    is_payout = amt < 0
    for rule in rules:
        if rule.get("sign_rule") == "any" or is_payout:
            if _match(rule, order_type, product_name):
                return rule.get("category") or "other", (rule.get("category") not in ("charge", "exclude"))
    return ("other", True) if is_payout else ("charge", False)


def build_row(src, base, rules):
    """Build one commission_ledger row from a mapped source row `src` (keys: account_id/account_name/
    store/rep_user/order_number/order_type/product_name/trans_date/due_date/raw_amount). `base` carries
    org_id + period. The payout magnitude (abs of a negative) is booked into the matched category column."""
    order_type = src.get("order_type")
    product_name = src.get("product_name")
    raw = _sf(src.get("raw_amount"))
    category, is_payout = classify(raw, order_type, product_name, rules)
    magnitude = round(abs(raw), 2) if is_payout else 0.0
    row = dict(base)
    row.update({
        "source_report": base.get("source_report") or "ma_daily_tx",
        "account_id": src.get("account_id"), "account_name": src.get("account_name"),
        "store": src.get("store"), "rep_user": src.get("rep_user"),
        "order_number": src.get("order_number"), "order_type": order_type,
        "product_name": product_name, "trans_date": src.get("trans_date"),
        "due_date": src.get("due_date"), "payment_month": parse_payment_month(product_name),
        "category": category, "raw_amount": round(raw, 2), "is_payout": is_payout,
        "payout_total": magnitude,
        "commission": 0, "spiff": 0, "equipment_rebate": 0, "residual_monthly": 0, "autopay_residual": 0,
    })
    if category in CATEGORIES:
        row[category] = magnitude
    return row


def summarize(rows):
    """Roll a list of ledger rows into: per-category totals + counts, per-(category,payment_month) matrix,
    payout grand total, charge total, and the 'other' (unmapped-payout) count for surfacing gaps."""
    cats = {c: {"total": 0.0, "count": 0} for c in CATEGORIES}
    by_month = {}
    payout_total = charge_total = other_total = 0.0
    other_count = 0
    for r in rows:
        cat = r.get("category")
        amt = _sf(r.get("payout_total"))
        if cat in cats:
            cats[cat]["total"] += amt
            cats[cat]["count"] += 1
            mo = r.get("payment_month")
            key = f"{cat}|{mo if mo is not None else 0}"
            by_month[key] = round(by_month.get(key, 0.0) + amt, 2)
            payout_total += amt
        elif cat == "other":
            other_total += amt
            other_count += 1
            payout_total += amt
        elif cat == "charge":
            charge_total += _sf(r.get("raw_amount"))
    for c in cats:
        cats[c]["total"] = round(cats[c]["total"], 2)
    return {
        "categories": cats,
        "category_labels": CATEGORY_LABELS,
        "by_month": by_month,
        "payout_total": round(payout_total, 2),
        "charge_total": round(charge_total, 2),
        "other_total": round(other_total, 2),
        "other_count": other_count,
        "line_count": len(rows),
    }
