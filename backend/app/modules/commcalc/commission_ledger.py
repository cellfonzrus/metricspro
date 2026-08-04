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
# `product_class` (2026-08-01, owner-gated) is NOT a text matcher: the pattern is a CLASS KEY and the
# rule matches when the LINE'S OWN CONFIRMED CLASS equals it. This module has NO dependency on the
# classification — the caller compiles a {label: class} index onto the rule (the way a compiled regex
# would be attached) and a rule WITHOUT that index can never match, so a tenant whose wiring mode is
# 'legacy' classifies byte-identically whether or not such rows exist. See ma_class_wiring.py.
MATCH_OPS = ["contains", "equals", "product_class"]
CLASS_MATCH_OP = "product_class"
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


# ── COMMISSION LEG (1st month vs M2–M12) — owner directive 2026-08-04 ────────────────────────────
# A SECOND, ORTHOGONAL dimension over the five canonical categories, not a re-categorisation: every
# ledger line keeps the category it already has and additionally reports WHICH LEG of the activation's
# life the money is. The rules live in `commcalc/commission_legs.py` (the ONE shared classifier, also
# consumed by the Gross Profit report) so the ledger and the GP report can never drift apart.
#
# DERIVED AT READ TIME — nothing is stamped on a commission_ledger row, so there is no backfill and the
# ingest path is untouched. Precedence, highest first:
#   1. the matched map rule's explicit `leg_bucket` (mig 274; NULL on every pre-existing rule)
#   2. the line's own `payment_month` — already parsed at build time by parse_payment_month()
#   3. the org's label rules / per-label overrides in commission_legs
#   4. the org's configured `unlabeled_bucket` (default: the honest 'unsplit')
LEG_BUCKETS = ("m1", "trailing", "unsplit")
LEG_LABELS = {"m1": "1st Month", "trailing": "M2–M12", "unsplit": "Unsplit"}


def _first_matching_rule(row, rules):
    """The rule that classified this line, re-derived with the SAME two-pass order `classify` uses, so a
    rule-level leg override applies to exactly the lines that rule categorised. None if nothing matched."""
    if not rules:
        return None
    ot, pn = row.get("order_type"), row.get("product_name")
    is_payout = _sf(row.get("raw_amount")) < 0
    for pass_class in (True, False):
        for rule in rules:
            if (rule.get("match_op") == CLASS_MATCH_OP) != pass_class:
                continue
            if rule.get("sign_rule") == "any" or is_payout:
                if _match(rule, ot, pn):
                    return rule
    return None


def leg_of(row, rules=None, legcls=None):
    """(leg_bucket, leg_month, why) for ONE ledger row. PURE apart from the injected classifier.
    `legcls` is a commission_legs.LegClassifier; None = its DB-free code defaults."""
    from app.modules.commcalc import commission_legs as _legs
    legcls = legcls or _legs.default_classifier()
    rule = _first_matching_rule(row, rules)
    if rule is not None:
        rb = str(rule.get("leg_bucket") or "").strip().lower()
        if rb in LEG_BUCKETS:
            return rb, (1 if rb == "m1" else None), "rule_override"
    mo = row.get("payment_month")
    if mo not in (None, ""):
        try:
            n = int(mo)
        except (TypeError, ValueError):
            n = None
        if n and n > 0:
            return _legs.bucket_for_leg(n, legcls.cfg), n, "payment_month"
    return legcls.label(row.get("product_name"))


def _match(rule, order_type, product_name):
    op = rule.get("match_op") or "contains"
    pat = str(rule.get("pattern") or "").lower()
    if not pat:
        return False
    if op == CLASS_MATCH_OP:
        # FAIL-CLOSED: no compiled index -> no match, ever. The index holds ONLY owner-CONFIRMED
        # (product_name -> class) mappings, so a proposed/ambiguous classification cannot move a dollar.
        idx = rule.get("_class_index")
        if not idx:
            return False
        cls = idx.get(str(product_name if product_name is not None else "").strip())
        return bool(cls) and str(cls).strip().lower() == pat
    field = rule.get("match_field") or "product_name"
    val = (product_name if field == "product_name" else order_type) or ""
    val = str(val).lower()
    return val == pat if op == "equals" else (pat in val)


def classify(raw_amount, order_type, product_name, rules):
    """Return (category, is_payout) for one source line. Negative amount = payout → the first matching
    rule's category; positive = a dealer 'charge' (never a payout bucket); an unmatched payout = 'other'
    (surfaced, never silently dropped). A rule with sign_rule='any' can also classify a positive line.

    TWO PASSES since 2026-08-01: a `product_class` rule (the line's owner-CONFIRMED MA product class) is
    tried first, then every rule in priority order as before. A tenant with no product_class rules — or
    with ledger wiring left in its default 'legacy' mode, where no class index is ever compiled onto the
    rules — gets exactly the same answer as before, which the differential proof asserts name by name."""
    amt = _sf(raw_amount)
    is_payout = amt < 0
    # PASS 1 — the line's CONFIRMED product class, if the tenant wired it (design of record: the class is
    # consulted FIRST and the keyword rules are the fallback for names nobody has classified). When no
    # product_class rule exists, or none carries a compiled index, this loop matches nothing and the
    # result is bit-for-bit what PASS 2 alone produced before this existed.
    for rule in rules:
        if rule.get("match_op") != CLASS_MATCH_OP:
            continue
        if rule.get("sign_rule") == "any" or is_payout:
            if _match(rule, order_type, product_name):
                return rule.get("category") or "other", (rule.get("category") not in ("charge", "exclude"))
    # PASS 2 — today's rules, in priority order, unchanged.
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


def summarize(rows, rules=None, legcls=None):
    """Roll a list of ledger rows into: per-category totals + counts, per-(category,payment_month) matrix,
    payout grand total, charge total, and the 'other' (unmapped-payout) count for surfacing gaps.

    ALSO (owner 2026-08-04) the COMMISSION LEG dimension: the same payout money split into the 1st-month
    leg vs the M2–M12 trailing legs, per category and in total. That is a DECOMPOSITION — for every
    category, m1 + trailing + unsplit == that category's existing, unchanged total, and the same holds
    for the grand payout total; `leg_identity_ok` proves it in the payload instead of asserting it.
    `rules`/`legcls` are optional: without them the leg is derived from each line's own payment month and
    label, which is exactly what pre-extension callers get plus the new (additive) keys."""
    cats = {c: {"total": 0.0, "count": 0} for c in CATEGORIES}
    by_month = {}
    payout_total = charge_total = other_total = 0.0
    other_count = 0
    leg_cats = {c: {b: 0.0 for b in LEG_BUCKETS} for c in list(CATEGORIES) + ["other"]}
    leg_tot = {b: 0.0 for b in LEG_BUCKETS}
    leg_ladder, leg_unmapped = {}, {}
    for r in rows:
        cat = r.get("category")
        amt = _sf(r.get("payout_total"))
        booked = None
        if cat in cats:
            cats[cat]["total"] += amt
            cats[cat]["count"] += 1
            mo = r.get("payment_month")
            key = f"{cat}|{mo if mo is not None else 0}"
            by_month[key] = round(by_month.get(key, 0.0) + amt, 2)
            payout_total += amt
            booked = cat
        elif cat == "other":
            other_total += amt
            other_count += 1
            payout_total += amt
            booked = "other"
        elif cat == "charge":
            charge_total += _sf(r.get("raw_amount"))
        if booked is None:                 # a charge is not a payout — it has no leg
            continue
        bucket, leg_month, _why = leg_of(r, rules, legcls)
        if bucket not in LEG_BUCKETS:
            bucket = "unsplit"
        leg_cats[booked][bucket] += amt
        leg_tot[bucket] += amt
        lk = "unknown" if leg_month in (None, "") else str(int(leg_month))
        leg_ladder[lk] = round(leg_ladder.get(lk, 0.0) + amt, 2)
        if bucket == "unsplit":            # surface WHAT is unattributed, the way 'other' is surfaced
            lbl = str(r.get("product_name") or "(blank)")
            u = leg_unmapped.setdefault(lbl, {"label": lbl, "amount": 0.0, "lines": 0})
            u["amount"] = round(u["amount"] + amt, 2)
            u["lines"] += 1
    for c in cats:
        cats[c]["total"] = round(cats[c]["total"], 2)
    for c in leg_cats:
        for b in LEG_BUCKETS:
            leg_cats[c][b] = round(leg_cats[c][b], 2)
    for b in LEG_BUCKETS:
        leg_tot[b] = round(leg_tot[b], 2)
    payout_total = round(payout_total, 2)
    identity_ok = abs(round(sum(leg_tot.values()), 2) - payout_total) < 0.01 and all(
        abs(round(sum(leg_cats[c].values()), 2) - cats[c]["total"]) < 0.01 for c in CATEGORIES)
    return {
        "categories": cats,
        "category_labels": CATEGORY_LABELS,
        "by_month": by_month,
        "payout_total": payout_total,
        "charge_total": round(charge_total, 2),
        "other_total": round(other_total, 2),
        "other_count": other_count,
        "line_count": len(rows),
        # ── commission LEG dimension (additive; categories above are byte-identical) ──
        "legs": leg_tot,
        "leg_labels": LEG_LABELS,
        "leg_buckets": list(LEG_BUCKETS),
        "by_category_leg": leg_cats,
        "leg_ladder": leg_ladder,
        "leg_unmapped": sorted(leg_unmapped.values(), key=lambda x: -abs(x["amount"]))[:50],
        "leg_unmapped_total": round(leg_tot["unsplit"], 2),
        "leg_identity_ok": identity_ok,
        "leg_basis": ("1st Month = commission for a number in the month it activated; M2–M12 = commission "
                      "received later for an already-activated number. Derived per line from the map "
                      "rule's leg override, else the line's own payment month, else its label."),
    }
