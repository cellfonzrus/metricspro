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
classified once and displayed as it's paid.

WHICH DIRECTION IS "EARNED" IS THE FEED'S OWN CONVENTION, AND IT IS CONFIG (owner bug report 2026-09-20).
On the MA Daily Tx (Total/VidaPay) a NEGATIVE amount is a payout and positives are dealer charges; other
statements state the same money the other way up (a positive is what the carrier owes, a negative is a
deactivation clawing part of it back). That direction used to be the hard-coded `amt < 0` below, so a
statement written the other way up booked its EARNINGS as charges and its CHARGEBACKS as the payout —
exactly backwards. It is now DECLARED WHEN THE AMOUNT COLUMN IS MAPPED (owner directive 2026-09-20):
`commcalc.column_mapping.sign_convention` on the `raw_amount` row, per (org, report, carrier), read by
`convention_from_mapping()`. An undeclared mapping — and a database where migration 1006 has not run,
so the column is not there at all — resolves to the old behaviour, so every shipped feed is
byte-identical. No carrier name appears here or anywhere downstream.

DEGRADES GRACEFULLY + BOOST-SAFE: when the map table is empty/un-migrated the classifier falls back to
the built-in DEFAULT_RULES **of that template** (DEFAULT_RULES_BY_TEMPLATE, which mirrors the 071 seed),
so it works the moment the code deploys. A template with no built-in defaults — every tenant-created
rule-set — falls back to NOTHING and its lines surface as unmapped 'other', because inheriting another
template's rules produces a confident wrong answer (owner bug report 2026-09-20: a rule-set with zero
rows silently classified one carrier's statement with another carrier's patterns). This module touches
only the 071/1006 tables — never the live calc, rep_commissions, carrier_commission, or legacy uploads.
"""
import re

ORG_HOUSE = "00000000-0000-0000-0000-000000000001"
LEDGER_TABLE = "commission_ledger"
MAP_TABLE = "commission_category_map"
# The sign convention is declared on the AMOUNT COLUMN'S MAPPING ROW (mig 1006 adds
# commcalc.column_mapping.sign_convention), never on a template and never in code — see below.
CONVENTION_MIGRATION = "1006_column_mapping_sign_convention.sql"
MAPPING_REPORT_KEY = "commission_ledger"    # the column_mapping report_key this ledger imports through
AMOUNT_FIELD = "raw_amount"                 # the mapped column whose sign convention is being declared
DEFAULT_SOURCE_REPORT = "ma_daily_tx"

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

# ── WHICH SIGN IS MONEY EARNED — DECLARED WHEN THE AMOUNT COLUMN IS MAPPED ──────────────────────────
# OWNER DIRECTIVE 2026-09-20: "the inverted sign should not be hard coded, it should be a part of
# mapping — we should not be making new rules for <a carrier>, we should be able to test this as a new
# carrier to be able to work with the system we built."
#
# So this is NOT a template constant, NOT a per-carrier branch and NOT a category-map rule. It is one
# more thing the tenant states about the AMOUNT COLUMN while mapping the file, right beside its header
# and its transform: commcalc.column_mapping.sign_convention on the `raw_amount` row, per (org, report,
# carrier) — the same key that already decides which header feeds the amount. A brand-new carrier
# declares it once in the mapping wizard and every downstream number follows; nobody edits the
# database, and no code knows the carrier's name.
#
# `sign_rule` on a category rule is a DIFFERENT axis and is not a substitute for this one: it says
# whether a rule may match a line pointing the "wrong" way, and a matched line still booked `abs()` —
# so under `sign_rule='any'` a −$1,500 deactivation was booked as +$1,500 EARNED. Direction-blind, and
# it points the total the wrong way. Hence the convention below, and booked_amount()'s signed result.
PAYOUT_SIGN_NEGATIVE = -1     # a NEGATIVE amount is money earned (the long-standing default)
PAYOUT_SIGN_POSITIVE = 1      # a POSITIVE amount is money earned
PAYOUT_SIGNS = (PAYOUT_SIGN_NEGATIVE, PAYOUT_SIGN_POSITIVE)
# What a line pointing AGAINST the convention is:
#   'charge' — not a payout at all: out of the five buckets, into charge_total. What every shipped
#              template does today, because on those feeds the opposite sign IS a different money
#              stream (the dealer buying airtime or devices), not a reversal of commission.
#   'signed' — the SAME money coming back: it books into the category it reverses as a NEGATIVE, so
#              the bucket reads NET. Never abs(): a clawback must reduce a bucket, never inflate it.
REVERSAL_CHARGE = "charge"
REVERSAL_SIGNED = "signed"
REVERSAL_MODES = (REVERSAL_CHARGE, REVERSAL_SIGNED)

# THE NAMED CONVENTIONS — the whole vocabulary a mapping can declare. Two named behaviours, not two
# free knobs: a tenant picks what their file DOES, not how the engine should behave.
SIGN_PAYOUT_NEGATIVE = "payout_negative"
SIGN_PAYOUT_POSITIVE = "payout_positive"
SIGN_CONVENTIONS = (SIGN_PAYOUT_NEGATIVE, SIGN_PAYOUT_POSITIVE)
SIGN_CONVENTION_DEFAULT = SIGN_PAYOUT_NEGATIVE
SIGN_CONVENTION_LABELS = {
    SIGN_PAYOUT_NEGATIVE: "A NEGATIVE amount is money earned (a positive is a charge to us)",
    SIGN_PAYOUT_POSITIVE: "A POSITIVE amount is money earned (a negative is a chargeback that nets off)",
}
CONVENTIONS = {
    SIGN_PAYOUT_NEGATIVE: {"payout_sign": PAYOUT_SIGN_NEGATIVE, "reversal_handling": REVERSAL_CHARGE},
    SIGN_PAYOUT_POSITIVE: {"payout_sign": PAYOUT_SIGN_POSITIVE, "reversal_handling": REVERSAL_SIGNED},
}
# Absent declaration, absent column, absent migration => this, for EVERY report and every template. It
# is exactly what every caller had before the convention existed, which is what makes it provable.
DEFAULT_CONVENTION = dict(CONVENTIONS[SIGN_CONVENTION_DEFAULT])
# The three directions a line can point. 'flat' (a zero amount) is neither earned nor reversed and is
# never booked — a $0 line must not count as a payout (the all-zero-device sentinel bug class).
DIR_PAYOUT, DIR_REVERSAL, DIR_FLAT = "payout", "reversal", "flat"


def convention_named(name):
    """The convention a declared name selects. Anything unknown, blank or None -> the default, so a
    stale or mistyped config value can never invent a third direction. PURE."""
    return dict(CONVENTIONS.get(str(name or "").strip().lower(), CONVENTIONS[SIGN_CONVENTION_DEFAULT]))


def normalise_convention(raw):
    """A convention dict (or a row carrying payout_sign/reversal_handling) -> a valid convention, with
    every invalid or missing value falling back to DEFAULT_CONVENTION. PURE."""
    conv = dict(DEFAULT_CONVENTION)
    if not raw:
        return conv
    try:
        sign = int(_sf(raw.get("payout_sign")))
    except (TypeError, ValueError):
        sign = 0
    if sign in PAYOUT_SIGNS:
        conv["payout_sign"] = sign
    mode = str(raw.get("reversal_handling") or "").strip().lower()
    if mode in REVERSAL_MODES:
        conv["reversal_handling"] = mode
    return conv


def convention_from_mapping(mapping_rules, amount_field=AMOUNT_FIELD):
    """(convention, meta) from the tenant's COLUMN-MAPPING rules — the amount column's own declaration.

    PURE: the caller has already loaded the mapping (column_mapping.load_rules, which is org- and
    carrier-scoped). A mapping with no `sign_convention` (not declared, or migration 1006 not applied,
    so the column is absent from the row) resolves to the default, which is byte-identical to the
    behaviour before this existed. meta says WHICH it was, so a payload can be honest about it."""
    row = None
    for r in mapping_rules or []:
        if str((r or {}).get("target_field") or "") == amount_field:
            row = r
            break
    declared = str((row or {}).get("sign_convention") or "").strip().lower()
    known = declared in SIGN_CONVENTIONS
    return convention_named(declared), {
        "amount_field": amount_field,
        "amount_header": (row or {}).get("source_header"),
        "declared": declared or None,
        "name": declared if known else SIGN_CONVENTION_DEFAULT,
        "source": "mapping" if known else ("default" if row is not None else "unmapped"),
        "label": SIGN_CONVENTION_LABELS[declared if known else SIGN_CONVENTION_DEFAULT],
        "options": [{"value": v, "label": SIGN_CONVENTION_LABELS[v]} for v in SIGN_CONVENTIONS],
        "is_default": not known or declared == SIGN_CONVENTION_DEFAULT,
    }


def direction(raw_amount, conv=None):
    """Which way one line points under a convention: DIR_PAYOUT (money earned), DIR_REVERSAL (money
    clawed back) or DIR_FLAT (zero). PURE. Under the default convention this is exactly `amt < 0`."""
    conv = conv or DEFAULT_CONVENTION
    v = _sf(raw_amount) * (conv.get("payout_sign") or PAYOUT_SIGN_NEGATIVE)
    if v > 0:
        return DIR_PAYOUT
    return DIR_REVERSAL if v < 0 else DIR_FLAT


def booked_amount(raw_amount, booking):
    """The magnitude a line books into its bucket: +|amount| when it is earned, −|amount| when it is a
    netted reversal, 0.0 when it books nothing. PURE, and the ONLY place a bucket amount is formed."""
    amt = round(abs(_sf(raw_amount)), 2)
    if booking == DIR_PAYOUT:
        return amt
    if booking == DIR_REVERSAL:
        return -amt
    return 0.0

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

# WHICH TEMPLATES THOSE DEFAULTS DESCRIBE (owner bug report 2026-09-20). Before this map, `load_rules`
# returned them for ANY template with no rows of its own — so a rule-set a TENANT created for a carrier
# nobody had seen before was classified with a master agent's keywords and reported a confident wrong
# answer. The defaults are the MA label vocabulary ('Commission', 'SPF', 'Spiff', 'Autopay Residual',
# 'Residual', 'Subsidy'), so they are the fallback for the SHIPPED MA templates that speak it and for
# nothing else:
#   · the Daily Tx template  — these rules ARE its 071 seed, mirrored so pre/post-migration match
#   · the MA Commission Details template — the same feed family, the same words, no seed of its own;
#     it has relied on this fallback since it was added (2026-07-30) and keeps it unchanged
# Deliberately NOT the Boost template: it ships its own complete 140-rule set (mig 072) in the house
# org, and MA keywords are not its vocabulary — borrowing them is the very defect this map closes.
# Measured 2026-09-20 before changing it: NO ledger row anywhere on the platform is filed under the
# Boost template, so this moves nothing; a Boost tenant with no rules saw every line as a 'charge'
# before and still does, now with the honest "no rules configured" note beside it.
# Any template not listed falls back to NO rules: every line surfaces as unmapped ('other'/'charge'),
# which the setup wizard and the Category Map page already show and link to.
DEFAULT_RULES_BY_TEMPLATE = {
    DEFAULT_SOURCE_REPORT: DEFAULT_RULES,
    "ma_commission": DEFAULT_RULES,
}
RULES_TENANT, RULES_BUILTIN, RULES_NONE = "tenant", "builtin_default", "none"


def default_rules_for(source_report):
    """The built-in fallback rules for ONE template, as rule dicts. [] for a template that has none."""
    return [{"match_field": mf, "match_op": op, "pattern": pat, "category": cat,
             "sign_rule": sr, "priority": pr}
            for (mf, op, pat, cat, sr, pr) in DEFAULT_RULES_BY_TEMPLATE.get(source_report, ())]


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


TEMPLATE_SCAN_CAP = 10000


def list_templates(client, org_id):
    """Built-in templates + every rule-set THIS TENANT actually has, whether it has rules yet or not.
    Each: {key, label, builtin, rule_count, ledger_lines}.

    A template used to appear here only once it HAD rules (it was counted off commission_category_map
    alone) — so a tenant who imported a brand-new carrier's statement under a new template key could
    not select that template on the Category Map page, and therefore could not create its FIRST rule
    without someone editing the database. That is the chicken-and-egg the 2026-09-20 onboarding test
    hit. A template the tenant's own LEDGER ROWS name is listed too, with its line count, so the path
    from "imported, nothing bucketed" to "bucketed" runs entirely through the UI.

    Both reads are ORG-SCOPED. The ledger scan is capped; `scan_truncated` says so rather than
    quietly under-counting."""
    counts, lines, truncated = {}, {}, False
    try:
        rows = (client.schema("commcalc").table(MAP_TABLE).select("source_report")
                .eq("org_id", org_id).execute().data) or []
        for r in rows:
            k = r.get("source_report")
            if k:
                counts[k] = counts.get(k, 0) + 1
    except Exception:
        pass
    try:
        led = (client.schema("commcalc").table(LEDGER_TABLE).select("source_report")
               .eq("org_id", org_id).limit(TEMPLATE_SCAN_CAP).execute().data) or []
        truncated = len(led) >= TEMPLATE_SCAN_CAP
        for r in led:
            k = r.get("source_report")
            if k:
                lines[k] = lines.get(k, 0) + 1
    except Exception:
        pass
    seen = set(counts) | set(lines)
    keys = list(BUILTIN_TEMPLATES) + [k for k in sorted(seen) if k not in BUILTIN_TEMPLATES]
    return [{"key": k, "label": BUILTIN_TEMPLATES.get(k, k), "builtin": k in BUILTIN_TEMPLATES,
             "rule_count": counts.get(k, 0), "ledger_lines": lines.get(k, 0),
             "scan_truncated": truncated} for k in keys]


def _sf(v):
    from app.modules.commcalc.calculator import safe_float
    return safe_float(v)


def load_rules_meta(client, org_id, source_report=DEFAULT_SOURCE_REPORT):
    """(rules, rules_source) for (org, source_report), ascending priority (first match wins). Rules with
    source_report '*' (any report) are included. ORG-SCOPED: a tenant only ever sees its own rules.

    rules_source says WHERE the answer came from, so every payload can be honest about it:
        'tenant'          — the org's own rows
        'builtin_default' — no rows, and this template has built-in defaults (the 071 seed's mirror)
        'none'            — no rows and no built-in defaults: nothing is classified, and saying so is
                            the fix for the 2026-09-20 cross-template fallback defect
    """
    rows = []
    try:
        rows = (client.schema("commcalc").table(MAP_TABLE).select("*")
                .eq("org_id", org_id).in_("source_report", [source_report, "*"]).execute().data) or []
    except Exception:
        rows = []
    if rows:
        return sorted(rows, key=lambda r: (r.get("priority") if r.get("priority") is not None else 100)), RULES_TENANT
    builtin = default_rules_for(source_report)
    return builtin, (RULES_BUILTIN if builtin else RULES_NONE)


def load_rules(client, org_id, source_report=DEFAULT_SOURCE_REPORT):
    """Effective classification rules for (org, source_report). See load_rules_meta for the provenance."""
    return load_rules_meta(client, org_id, source_report)[0]


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


def _first_matching_rule(row, rules, conv=None):
    """The rule that classified this line, re-derived with the SAME two-pass order `classify` uses, so a
    rule-level leg override applies to exactly the lines that rule categorised. None if nothing matched."""
    if not rules:
        return None
    ot, pn = row.get("order_type"), row.get("product_name")
    conv = conv or DEFAULT_CONVENTION
    d = direction(row.get("raw_amount"), conv)
    for pass_class in (True, False):
        for rule in rules:
            if (rule.get("match_op") == CLASS_MATCH_OP) != pass_class:
                continue
            if _rule_may_match(rule, d, conv):
                if _match(rule, ot, pn):
                    return rule
    return None


def leg_of(row, rules=None, legcls=None, conv=None):
    """(leg_bucket, leg_month, why) for ONE ledger row. PURE apart from the injected classifier.
    `legcls` is a commission_legs.LegClassifier; None = its DB-free code defaults."""
    from app.modules.commcalc import commission_legs as _legs
    legcls = legcls or _legs.default_classifier()
    rule = _first_matching_rule(row, rules, conv)
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


def _rule_may_match(rule, d, conv):
    """May this rule be TRIED against a line pointing direction `d`? Under the default convention this is
    exactly the old `rule['sign_rule'] == 'any' or amount < 0`. Under 'signed' reversal handling a
    reversal is also offered to the rules, because it is the same money coming back and it has to find
    the bucket it reverses — it books NEGATIVE, never abs()."""
    if d == DIR_PAYOUT:
        return True
    if rule.get("sign_rule") == "any":
        return True
    return d == DIR_REVERSAL and (conv or DEFAULT_CONVENTION).get("reversal_handling") == REVERSAL_SIGNED


def _booking_for(category, d, conv):
    """How a matched line books: DIR_PAYOUT (+|amt|), DIR_REVERSAL (−|amt|) or None (books nothing).
    A 'charge'/'exclude' category books nothing, as before. Under the default convention every booked
    line is DIR_PAYOUT, so the magnitude is abs() exactly as it has always been."""
    if category in ("charge", "exclude"):
        return None
    if d == DIR_REVERSAL and (conv or DEFAULT_CONVENTION).get("reversal_handling") == REVERSAL_SIGNED:
        return DIR_REVERSAL
    return DIR_PAYOUT


def classify_line(raw_amount, order_type, product_name, rules, conv=None):
    """(category, booking) for one source line under a sign convention. `booking` is DIR_PAYOUT,
    DIR_REVERSAL or None (books nothing) — see _booking_for. PURE.

    Negative amount = payout is no longer assumed: `conv['payout_sign']` says which direction is money
    EARNED, and it defaults to the MA convention so every existing caller is byte-identical. A line
    pointing the other way is a reversal; under the default handling it stays a 'charge' exactly as
    before, and under 'signed' it books into its own bucket as a NEGATIVE so the bucket reads net.

    TWO PASSES since 2026-08-01: a `product_class` rule (the line's owner-CONFIRMED MA product class) is
    tried first, then every rule in priority order as before. A tenant with no product_class rules — or
    with ledger wiring left in its default 'legacy' mode, where no class index is ever compiled onto the
    rules — gets exactly the same answer as before, which the differential proof asserts name by name."""
    conv = conv or DEFAULT_CONVENTION
    d = direction(raw_amount, conv)
    # PASS 1 — the line's CONFIRMED product class, if the tenant wired it (design of record: the class is
    # consulted FIRST and the keyword rules are the fallback for names nobody has classified). When no
    # product_class rule exists, or none carries a compiled index, this loop matches nothing and the
    # result is bit-for-bit what PASS 2 alone produced before this existed.
    for pass_class in (True, False):
        for rule in rules:
            if (rule.get("match_op") == CLASS_MATCH_OP) != pass_class:
                continue
            if _rule_may_match(rule, d, conv) and _match(rule, order_type, product_name):
                cat = rule.get("category") or "other"
                return cat, _booking_for(cat, d, conv)
    # Nothing matched. A line pointing the payout way is an UNMAPPED payout ('other' — surfaced, never
    # silently dropped); one pointing the other way is a charge, unless reversals are being netted, in
    # which case it is an unmapped reversal and surfaces the same way with the opposite sign.
    if d == DIR_PAYOUT:
        return "other", DIR_PAYOUT
    if d == DIR_REVERSAL and conv.get("reversal_handling") == REVERSAL_SIGNED:
        return "other", DIR_REVERSAL
    return "charge", None


def classify(raw_amount, order_type, product_name, rules, conv=None):
    """(category, is_payout) — the long-standing two-value form, unchanged for every existing caller.
    `is_payout` is True for a line in the payout stream, which under 'signed' reversal handling includes
    a netted chargeback; `build_row` uses classify_line so it can tell the two apart."""
    cat, booking = classify_line(raw_amount, order_type, product_name, rules, conv)
    return cat, booking is not None


def build_row(src, base, rules, conv=None):
    """Build one commission_ledger row from a mapped source row `src` (keys: account_id/account_name/
    store/rep_user/order_number/order_type/product_name/trans_date/due_date/raw_amount). `base` carries
    org_id + period. The payout magnitude is booked into the matched category column: +|amount| for a
    line pointing the way the template's convention says money is EARNED, and −|amount| for a reversal
    when that template nets reversals. `conv` defaults to the MA convention, so an existing caller that
    passes no convention builds a byte-identical row."""
    order_type = src.get("order_type")
    product_name = src.get("product_name")
    raw = _sf(src.get("raw_amount"))
    category, booking = classify_line(raw, order_type, product_name, rules, conv)
    is_payout = booking is not None
    magnitude = booked_amount(raw, booking)
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


def summarize(rows, rules=None, legcls=None, conv=None):
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
        bucket, leg_month, _why = leg_of(r, rules, legcls, conv)
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
