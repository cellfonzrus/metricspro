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

THE BUCKETS ARE CONFIG, NOT A LIST IN CODE (owner directive 2026-09-20: "this is not to be hardcoded,
the user should be able to define the buckets and also assign the bucket to a bigger category on the
P&L"). `commcalc.commission_bucket` (mig 1009) holds one row per bucket per org — key, label, KIND
(earned | deduction, which decides the sign a line books with), order, active, the neutral hint words
the onboarding intake pre-places labels with, and the P&L line it rolls up to (`pl_line_key`, a key of
account/coa.PL_SPEC). The house org seeds the five above plus `chargebacks`, `vendor_fee` and
`misc_charges`; a tenant inherits the house rows and overrides per key. The five above stay COLUMN-
BACKED (their mig-071 amount columns are still written, so every existing reader is byte-identical);
every other bucket is read by (category, payout_total) — no schema change per bucket. `CATEGORIES` /
`CATEGORY_LABELS` are the column-backed five and the display fallback; `HOUSE_BUCKETS` mirrors the
1009 seed and is what a database without the table reads for DISPLAY — a line may not be BOOKED to a
column-less bucket until the table exists (the landing paths refuse, naming the migration).

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


# ── THE ONE DERIVATION OF A COMMISSION-FAMILY MAPPING KEY (owner 2026-09-20; index §30.10) ────────
# THE CLASS THIS CLOSES: the column-mapping key ignored the STATEMENT TYPE. Every reader and writer
# keyed `commcalc.column_mapping` by (org, MAPPING_REPORT_KEY, carrier, field), so a residual
# statement from the same carrier — a different layout, its own sign — would have OVERWRITTEN the
# commission statement's column map and its `sign_convention` (which lives on that key's `raw_amount`
# row). The RULES were already namespaced per statement (`commission_category_map.source_report =
# <carrier>__<statement slug>`); the mapping was the one fact without its home. Now:
#
#     mapping key  =  MAPPING_REPORT_KEY                        for the default type ('commission')
#                  =  MAPPING_REPORT_KEY + '__' + <token>       for every other statement type
#
# where <token> is the registry's statement-type vocabulary (report_kinds.statement_type_token: the
# `statement_type` of the commission-landing report-kind rows — 'residual' today; a third type is a
# registry ROW, not code). The default maps onto TODAY's key byte-for-byte, so every mapping row and
# sign answer saved before this existed is read exactly as before — nothing is re-keyed.
# `harness_mapping_key_lock.py` fails the build on any literal MAPPING_REPORT_KEY used as a mapping
# key outside this function; `harness_statement_type_mapping.py` proves the behaviour.
def mapping_report_key(statement_type="", registry_rows=None):
    """THE column_mapping `report_key` for a commission-family statement of `statement_type` (free
    text as the person typed it at 3.1, or the registry token, or blank = the default type).
    `registry_rows` = the merged report-kind rows (report_kinds.load_registry) when the caller has
    them; None reads the mirror, which always carries the house vocabulary. PURE."""
    from app.modules.commcalc import column_mapping as _cm
    from app.modules.commcalc import report_kinds as _rk
    tok = _rk.statement_type_token(statement_type, registry_rows)
    return _cm.variant_report_key(MAPPING_REPORT_KEY, "" if tok == _rk.STATEMENT_TYPE_DEFAULT else tok)


def mapping_report_keys(registry_rows=None):
    """One mapping key per statement type the registry names (default first) — what the Column
    Mapping page's picker lists, so a type added as a registry row is editable with no code. PURE."""
    from app.modules.commcalc import report_kinds as _rk
    return [mapping_report_key(t, registry_rows) for t in _rk.statement_types(registry_rows)]


def statement_type_of_source_report(source_report):
    """The statement-type TEXT a ledger `source_report` carries — the part after the carrier code in
    the intake's `<carrier>__<statement slug>` (onboarding_intake.source_report_key), '' for a
    template key with no statement part ('ma_daily_tx'). What a READ endpoint that knows only the
    source_report hands to mapping_report_key. PURE."""
    from app.modules.commcalc import column_mapping as _cm
    return _cm.split_report_key(source_report)[1]

# THE COLUMN-BACKED buckets (the five amount columns on commission_ledger, mig 071) + non-payout
# sentinels. Every OTHER bucket lives in the registry below and is read by (category, payout_total).
CATEGORIES = ["commission", "spiff", "equipment_rebate", "residual_monthly", "autopay_residual"]
COLUMN_BACKED = tuple(CATEGORIES)
CATEGORY_LABELS = {
    "commission": "Commission",
    "spiff": "Spiff",
    "equipment_rebate": "Equipment rebate",
    "residual_monthly": "Residual / monthly incentives",
    "autopay_residual": "Auto Pay residual",
    "chargebacks": "Chargebacks",
    "vendor_fee": "Vendor fee",
    "misc_charges": "Misc charges",
    # `charge` is NOT a bucket and NOT the same thing as `misc_charges`: it is the sentinel for a line
    # that is not part of the statement's payout at all — the dealer's own bill / activation payment
    # on a master-agent feed (a DIFFERENT money stream, `payout_negative`). It books nothing and sits
    # outside every tie-out. `misc_charges` is a DEDUCTION BUCKET: money the carrier took off the
    # statement (an adjustment, a charitable contribution), booked signed so the buckets still sum to
    # the statement's own total.
    "charge": "Bill / activation payment (not a payout — outside the buckets, not a deduction)",
    "other": "Other payout (unmapped)",
}

# ── THE BUCKET REGISTRY (mig 1009) — per-org rows, house defaults, tenant overrides per key ─────
BUCKET_TABLE = "commission_bucket"
BUCKET_MIGRATION = "1009_commission_bucket_registry.sql"
KIND_EARNED, KIND_DEDUCTION = "earned", "deduction"
BUCKET_KINDS = (KIND_EARNED, KIND_DEDUCTION)
KIND_LABELS = {KIND_EARNED: "Earned (money the carrier pays us)",
               KIND_DEDUCTION: "Deduction (money taken off the statement — books signed, never abs())"}
BUCKETS_TENANT, BUCKETS_HOUSE, BUCKETS_BUILTIN = "tenant", "house", "builtin"
_BUCKET_KEY_RE = re.compile(r"^[a-z][a-z0-9_]{0,63}$")
# MIRRORS THE 1009 SEED EXACTLY (harness_commission_ledger_sign.py parses the migration and compares).
# What a database WITHOUT the table reads, for display only. Neutral English hint words — no carrier,
# tenant or product name (RULE TWO). Tuple order = (key, label, kind, sort_order, hint_words, pl_line_key,
# is_builtin); is_builtin marks the five column-backed keys, whose key can never change.
HOUSE_BUCKETS = [
    ("commission", "Commission", KIND_EARNED, 10,
     ["commission", "activation", "upgrade", "price plan", "new account", "add-a-line", "add a line", "new line"],
     "carrier_comm", True),
    ("spiff", "Spiff", KIND_EARNED, 20, ["spiff", "spf", "incentive", "bonus", "bounty"], "carrier_comm", True),
    ("equipment_rebate", "Equipment rebate", KIND_EARNED, 30,
     ["rebate", "subsidy", "promo", "trade-in", "trade in", "trade"], "device_rebate", True),
    ("residual_monthly", "Residual / monthly incentives", KIND_EARNED, 40,
     ["residual", "monthly incentive"], "mi_income", True),
    ("autopay_residual", "Auto Pay residual", KIND_EARNED, 50,
     ["autopay residual", "auto pay residual", "auto-pay residual", "autopay", "auto pay", "auto-pay"], "mi_income", True),
    ("chargebacks", "Chargebacks", KIND_DEDUCTION, 60,
     ["chargeback", "charge back", "charge-back", "deactivation", "deactivate", "deact", "clawback",
      "claw back", "claw-back", "reversal", "reversed"], "chargebacks", False),
    ("vendor_fee", "Vendor fee", KIND_DEDUCTION, 70, ["service fee", "vendor fee", "fee"], "vip_fees", False),
    ("misc_charges", "Misc charges", KIND_DEDUCTION, 80,
     ["adjustment", "adjust", "misc", "miscellaneous", "charitable", "contribution", "other charge"],
     "store_opex", False),
]


def _bucket_dict(key, label, kind, sort_order, hint_words, pl_line_key, is_builtin, **extra):
    d = {"key": key, "label": label, "kind": kind, "sort_order": sort_order, "is_active": True,
         "hint_words": list(hint_words or []), "pl_line_key": pl_line_key, "is_builtin": bool(is_builtin),
         "column_backed": key in COLUMN_BACKED, "org_id": None, "id": None}
    d.update(extra)
    return d


def builtin_buckets():
    """The house defaults as bucket dicts — what a pre-1009 database reads for DISPLAY. PURE."""
    return [_bucket_dict(*t) for t in HOUSE_BUCKETS]


def normalise_bucket(row):
    """One registry row (or a POST body) -> a valid bucket dict, or None when the key is not a key.
    Unknown kinds read as 'earned' (a typo can never make money book the wrong way silently — the
    endpoint validates before writing; this is the READ side's tolerance). PURE."""
    key = str((row or {}).get("key") or "").strip().lower()
    if not _BUCKET_KEY_RE.match(key):
        return None
    kind = str(row.get("kind") or KIND_EARNED).strip().lower()
    words = row.get("hint_words")
    if isinstance(words, str):
        words = [w for w in re.split(r"[,\n;]+", words)]
    words = [str(w).strip().lower() for w in (words or []) if str(w).strip()]
    try:
        order = int(row.get("sort_order") if row.get("sort_order") is not None else 100)
    except (TypeError, ValueError):
        order = 100
    active = row.get("is_active")
    return _bucket_dict(key, str(row.get("label") or key).strip() or key,
                        kind if kind in BUCKET_KINDS else KIND_EARNED, order, words,
                        (str(row.get("pl_line_key") or "").strip() or None),
                        bool(row.get("is_builtin")) or key in COLUMN_BACKED,
                        is_active=(True if active is None else bool(active)),
                        org_id=row.get("org_id"), id=row.get("id"))


def merge_buckets(house_rows, tenant_rows):
    """House defaults with the tenant's rows overriding PER KEY (mig-207 report_pull_map pattern):
    a tenant that adds one bucket keeps the house ones; a tenant row for a house key replaces it.
    Sorted by (sort_order, key). PURE."""
    out = {}
    for r in house_rows or []:
        b = normalise_bucket(r)
        if b:
            b["source"] = BUCKETS_HOUSE
            out[b["key"]] = b
    for r in tenant_rows or []:
        b = normalise_bucket(r)
        if b:
            b["source"] = BUCKETS_TENANT
            out[b["key"]] = b
    return sorted(out.values(), key=lambda b: (b["sort_order"], b["key"]))


def load_buckets_meta(client, org_id):
    """(buckets, meta) for ONE org. Two ORG-SCOPED reads: the house org's rows (the defaults) and this
    org's rows (the overrides), merged per key. A database without the table (mig 1009 not applied)
    returns the built-in mirror of the seed with ready=False, so display works and a caller that
    would BOOK to a column-less bucket can refuse, naming the migration."""
    meta = {"ready": True, "migration": BUCKET_MIGRATION, "source": BUCKETS_BUILTIN,
            "tenant_rows": 0, "house_rows": 0}
    try:
        house = (client.schema("commcalc").table(BUCKET_TABLE).select("*")
                 .eq("org_id", ORG_HOUSE).execute().data) or []
        tenant = [] if org_id == ORG_HOUSE else (
            (client.schema("commcalc").table(BUCKET_TABLE).select("*")
             .eq("org_id", org_id).execute().data) or [])
    except Exception:
        meta["ready"] = False
        return builtin_buckets(), meta
    meta["house_rows"], meta["tenant_rows"] = len(house), len(tenant)
    if not house and not tenant:
        # the table exists but the seed has not landed: still the built-ins, still say so
        meta["source"] = BUCKETS_BUILTIN
        return builtin_buckets(), meta
    meta["source"] = BUCKETS_TENANT if tenant else BUCKETS_HOUSE
    merged = merge_buckets(house or builtin_buckets(), tenant)
    return merged, meta


def load_buckets(client, org_id):
    return load_buckets_meta(client, org_id)[0]


def active_buckets(buckets=None):
    return [b for b in (buckets if buckets is not None else builtin_buckets()) if b.get("is_active", True)]


def bucket_keys(buckets=None, active_only=True):
    src = active_buckets(buckets) if active_only else (buckets if buckets is not None else builtin_buckets())
    return [b["key"] for b in src]


def bucket_labels(buckets=None):
    out = dict(CATEGORY_LABELS)
    for b in (buckets if buckets is not None else builtin_buckets()):
        out[b["key"]] = b["label"]
    return out


def bucket_by_key(buckets=None):
    return {b["key"]: b for b in (buckets if buckets is not None else builtin_buckets())}


def deduction_keys(buckets=None):
    return {b["key"] for b in (buckets if buckets is not None else builtin_buckets()) if b.get("kind") == KIND_DEDUCTION}


def bucket_kind(category, buckets=None):
    """'earned' | 'deduction' for a registry bucket key; None for a sentinel or an unknown key."""
    b = bucket_by_key(buckets).get(str(category or ""))
    return b.get("kind") if b else None


def unbookable_categories(rows, buckets_meta):
    """The categories among `rows` that are NOT column-backed while the registry table is absent —
    the set a landing path must refuse (naming BUCKET_MIGRATION) rather than write a bucket key the
    database cannot describe. Empty when the table exists or every row is column-backed/sentinel."""
    if (buckets_meta or {}).get("ready", True):
        return []
    seen = set()
    for r in rows or []:
        c = str(r.get("category") or "")
        if c and c not in COLUMN_BACKED and c not in ("charge", "other", "exclude"):
            seen.add(c)
    return sorted(seen)
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
# THE THIRD NAMED CONVENTION (onboarding intake, 2026-09-20; mig 1008). The intake's 3.4 question —
# "In this file, is money you EARNED positive or negative?" — has two answers, and BOTH net a
# reversal into the bucket it reverses (design §3.6: a chargeback is the same money coming back, never
# a different stream). `payout_negative` cannot say that: on the master-agent feeds it describes, the
# opposite sign IS a different stream (the dealer buying airtime), so it books a positive as a charge.
# A statement written negative-earned WITH positive chargebacks therefore needs its own name, or the
# intake would either inflate (abs()) or leave the chargebacks outside the buckets and never tie out.
# Nothing declares this until a tenant answers 3.4 "earned is negative"; every existing row is
# unchanged (NULL still reads as payout_negative).
SIGN_PAYOUT_NEGATIVE_NETTED = "payout_negative_netted"
SIGN_CONVENTIONS = (SIGN_PAYOUT_NEGATIVE, SIGN_PAYOUT_POSITIVE, SIGN_PAYOUT_NEGATIVE_NETTED)
SIGN_CONVENTION_DEFAULT = SIGN_PAYOUT_NEGATIVE
SIGN_CONVENTION_LABELS = {
    SIGN_PAYOUT_NEGATIVE: "A NEGATIVE amount is money earned (a positive is a charge to us)",
    SIGN_PAYOUT_POSITIVE: "A POSITIVE amount is money earned (a negative is a chargeback that nets off)",
    SIGN_PAYOUT_NEGATIVE_NETTED: "A NEGATIVE amount is money earned (a positive is a chargeback that nets off)",
}
CONVENTIONS = {
    SIGN_PAYOUT_NEGATIVE: {"payout_sign": PAYOUT_SIGN_NEGATIVE, "reversal_handling": REVERSAL_CHARGE},
    SIGN_PAYOUT_POSITIVE: {"payout_sign": PAYOUT_SIGN_POSITIVE, "reversal_handling": REVERSAL_SIGNED},
    SIGN_PAYOUT_NEGATIVE_NETTED: {"payout_sign": PAYOUT_SIGN_NEGATIVE, "reversal_handling": REVERSAL_SIGNED},
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
    """Extract the payment month TOKEN from a product label: 'TBV MONTH 4 …' -> 4,
    'Commission - M1 Proration' -> 1, 'SPF Month 1' -> 1. None when no month token is present.

    This answers "does this label SAY a month", and nothing else. It is NOT the answer to "which
    month-of-life leg is this row" — a carrier states that in more than one form. Ask
    `month_leg_of()` for the leg; it delegates here for the token case. (Enforced:
    backend/harness_ma_income_one_home_guard.py fails the build if a leg question is asked of this
    function outside this module.)"""
    m = _MONTH_RE.search(str(product_name or ""))
    if not m:
        return None
    g = m.group(1) or m.group(2)
    try:
        return int(g)
    except (TypeError, ValueError):
        return None


# ── "WHICH MONTH-OF-LIFE LEG IS THIS ROW" — ONE HOME (owner report 2026-09-21) ───────────────────
# Owner, on the Gross Profit M1 tile: "the m1 commission cannot be 2300". He was right, and the
# reason is a vocabulary gap, not arithmetic.
#
# The master agent states the FIRST month's commission in TWO forms, and which one it uses changed
# part-way through the year (measured, org 854f6d7b…, all 8 periods Feb–Sep 2026):
#     "TBV MONTH 2 New Activation Commission"          -> a MONTH TOKEN   (parse_payment_month)
#     "Total Wireless 5G Unlimited $55 New Activation Commission"
#                                                      -> NO token: the leg is stated by the label
#                                                         being an ACTIVATION commission at all
# Feb–May 2026 carry the second form ONLY (0.00 of token-M1 in all four months); June onwards carry
# both. Reading the token alone therefore drops most of M1 into the honest-but-wrong "no month"
# bucket: August 2026 read M1 = $2,300.40 when the month's real M1 is $6,049.96 — 1,218 rows and
# $19,292.54 platform-wide sat unlabelled across the eight periods. No dollar was ever lost (every
# column total was right); the LEG was wrong, which is exactly the number the owner reads.
#
# So the leg question gets its own function, and every caller asks THIS one. The token still wins
# when present — a label that says MONTH 2 is month 2 even though it also says "New Activation
# Commission" — so this can only ADD a leg where there was none, never move one.
#
# RULE TWO: the vocabulary is data, not a branch. `activation_labels` overrides per org (the
# existing `commcalc.commission_leg_label_map` remains the per-exact-label authority and still
# wins upstream in commission_legs); the default below is a LABEL FORM, not a carrier, tenant or
# product name, and it is what makes this correct out of the box rather than stored-but-inert.
ACTIVATION_LEG_MONTH = 1
ACTIVATION_LABEL_PATTERNS = (r"\bnew\s+activation\s+(?:commission|spf)\b",
                             r"\bactivation\s+commission\b")
_ACTIVATION_RE = re.compile("|".join(ACTIVATION_LABEL_PATTERNS), re.I)


def month_leg_of(product_name, activation_labels=None):
    """PURE: the month-of-life leg a product label states, or None when it states none.

    Precedence, highest first:
      1. an explicit month TOKEN  ("MONTH 4", "M1")  -> that month   [parse_payment_month]
      2. the label is an ACTIVATION commission with no token          -> ACTIVATION_LEG_MONTH (1)
      3. otherwise None — reported as having no month, never guessed into one.

    `activation_labels` = per-org regex list replacing the default form. A bad pattern is ignored
    rather than raising, so a tenant cannot take a report down with a typo."""
    name = str(product_name or "")
    n = parse_payment_month(name)
    if n:
        return n
    if not name.strip():
        return None
    rx = _ACTIVATION_RE
    if activation_labels:
        try:
            rx = re.compile("|".join(str(p) for p in activation_labels), re.I)
        except re.error:
            rx = _ACTIVATION_RE
    return ACTIVATION_LEG_MONTH if rx.search(name) else None


# ── COMMISSION LEG (1st month vs M2–M12) — owner directive 2026-08-04 ────────────────────────────
# A SECOND, ORTHOGONAL dimension over the five canonical categories, not a re-categorisation: every
# ledger line keeps the category it already has and additionally reports WHICH LEG of the activation's
# life the money is. The rules live in `commcalc/commission_legs.py` (the ONE shared classifier, also
# consumed by the Gross Profit report) so the ledger and the GP report can never drift apart.
#
# DERIVED AT READ TIME — nothing is stamped on a commission_ledger row, so there is no backfill and the
# ingest path is untouched. Precedence, highest first:
#   1. the matched map rule's explicit `leg_bucket` (mig 274; NULL on every pre-existing rule)
#   2. the line's own `payment_month` — already resolved at build time by month_leg_of()
#   3. the org's label rules / per-label overrides in commission_legs
#   4. the org's configured `unlabeled_bucket` (default: the honest 'unsplit')
LEG_BUCKETS = ("m1", "trailing", "unsplit")
LEG_LABELS = {"m1": "1st Month", "trailing": "M2–M12", "unsplit": "Unsplit"}


def _first_matching_rule(row, rules, conv=None, buckets=None):
    """The rule that classified this line, re-derived with the SAME two-pass order `classify` uses, so a
    rule-level leg override applies to exactly the lines that rule categorised. None if nothing matched."""
    if not rules:
        return None
    ot, pn = row.get("order_type"), row.get("product_name")
    conv = conv or DEFAULT_CONVENTION
    d = direction(row.get("raw_amount"), conv)
    ded = deduction_keys(buckets)
    for pass_class in (True, False):
        for rule in rules:
            if (rule.get("match_op") == CLASS_MATCH_OP) != pass_class:
                continue
            if _rule_may_match(rule, d, conv, ded):
                if _match(rule, ot, pn):
                    return rule
    return None


def leg_of(row, rules=None, legcls=None, conv=None, buckets=None):
    """(leg_bucket, leg_month, why) for ONE ledger row. PURE apart from the injected classifier.
    `legcls` is a commission_legs.LegClassifier; None = its DB-free code defaults."""
    from app.modules.commcalc import commission_legs as _legs
    legcls = legcls or _legs.default_classifier()
    rule = _first_matching_rule(row, rules, conv, buckets)
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


def _rule_may_match(rule, d, conv, deductions=None):
    """May this rule be TRIED against a line pointing direction `d`? Under the default convention this is
    exactly the old `rule['sign_rule'] == 'any' or amount < 0`. Under 'signed' reversal handling a
    reversal is also offered to the rules, because it is the same money coming back and it has to find
    the bucket it reverses — it books NEGATIVE, never abs(). A rule that targets a DEDUCTION bucket is
    offered a line pointing EITHER way: a deduction naturally points against the earned direction, and a
    refund of one points with it — both are that bucket's money and both book SIGNED."""
    if d == DIR_PAYOUT:
        return True
    if rule.get("sign_rule") == "any":
        return True
    if deductions and rule.get("category") in deductions:
        return d != DIR_FLAT
    return d == DIR_REVERSAL and (conv or DEFAULT_CONVENTION).get("reversal_handling") == REVERSAL_SIGNED


def _booking_for(category, d, conv, deductions=None):
    """How a matched line books: DIR_PAYOUT (+|amt|), DIR_REVERSAL (−|amt|) or None (books nothing).
    A 'charge'/'exclude' category books nothing, as before. Under the default convention every booked
    line is DIR_PAYOUT, so the magnitude is abs() exactly as it has always been.

    THE DEDUCTION SIGN RULE (mig 1009): a line in a DEDUCTION bucket books its CANONICAL SIGNED amount
    (raw × payout_sign) whichever way it points and whatever the reversal handling — DIR_REVERSAL
    (−|amt|) when it points against the earned direction (a −1,500 deactivation under payout_positive;
    a fee written as a positive charge under payout_negative), DIR_PAYOUT (+|amt|) when it points with
    it (a fee REFUND reduces the deduction). Never abs(): that is how Σ over every bucket, earned and
    deduction, stays equal to the statement's own total."""
    if category in ("charge", "exclude"):
        return None
    if deductions and category in deductions:
        return DIR_REVERSAL if d == DIR_REVERSAL else DIR_PAYOUT
    if d == DIR_REVERSAL and (conv or DEFAULT_CONVENTION).get("reversal_handling") == REVERSAL_SIGNED:
        return DIR_REVERSAL
    return DIR_PAYOUT


def classify_line(raw_amount, order_type, product_name, rules, conv=None, buckets=None):
    """(category, booking) for one source line under a sign convention. `booking` is DIR_PAYOUT,
    DIR_REVERSAL or None (books nothing) — see _booking_for. PURE.

    Negative amount = payout is no longer assumed: `conv['payout_sign']` says which direction is money
    EARNED, and it defaults to the MA convention so every existing caller is byte-identical. A line
    pointing the other way is a reversal; under the default handling it stays a 'charge' exactly as
    before, and under 'signed' it books into its own bucket as a NEGATIVE so the bucket reads net.

    `buckets` is the org's bucket registry (mig 1009; None = the built-in house defaults): it says which
    categories are DEDUCTION buckets, which book signed either way. An existing rule targets none of
    them, so every existing rule-set classifies byte-identically (the differential proof).

    TWO PASSES since 2026-08-01: a `product_class` rule (the line's owner-CONFIRMED MA product class) is
    tried first, then every rule in priority order as before. A tenant with no product_class rules — or
    with ledger wiring left in its default 'legacy' mode, where no class index is ever compiled onto the
    rules — gets exactly the same answer as before, which the differential proof asserts name by name."""
    conv = conv or DEFAULT_CONVENTION
    d = direction(raw_amount, conv)
    ded = deduction_keys(buckets)
    # PASS 1 — the line's CONFIRMED product class, if the tenant wired it (design of record: the class is
    # consulted FIRST and the keyword rules are the fallback for names nobody has classified). When no
    # product_class rule exists, or none carries a compiled index, this loop matches nothing and the
    # result is bit-for-bit what PASS 2 alone produced before this existed.
    for pass_class in (True, False):
        for rule in rules:
            if (rule.get("match_op") == CLASS_MATCH_OP) != pass_class:
                continue
            if _rule_may_match(rule, d, conv, ded) and _match(rule, order_type, product_name):
                cat = rule.get("category") or "other"
                return cat, _booking_for(cat, d, conv, ded)
    # Nothing matched. A line pointing the payout way is an UNMAPPED payout ('other' — surfaced, never
    # silently dropped); one pointing the other way is a charge, unless reversals are being netted, in
    # which case it is an unmapped reversal and surfaces the same way with the opposite sign.
    if d == DIR_PAYOUT:
        return "other", DIR_PAYOUT
    if d == DIR_REVERSAL and conv.get("reversal_handling") == REVERSAL_SIGNED:
        return "other", DIR_REVERSAL
    return "charge", None


def classify(raw_amount, order_type, product_name, rules, conv=None, buckets=None):
    """(category, is_payout) — the long-standing two-value form, unchanged for every existing caller.
    `is_payout` is True for a line in the payout stream, which under 'signed' reversal handling includes
    a netted chargeback; `build_row` uses classify_line so it can tell the two apart."""
    cat, booking = classify_line(raw_amount, order_type, product_name, rules, conv, buckets)
    return cat, booking is not None


def build_row(src, base, rules, conv=None, buckets=None):
    """Build one commission_ledger row from a mapped source row `src` (keys: account_id/account_name/
    store/rep_user/order_number/order_type/product_name/trans_date/due_date/raw_amount). `base` carries
    org_id + period. The payout magnitude is booked into the matched category column: +|amount| for a
    line pointing the way the template's convention says money is EARNED, and −|amount| for a reversal
    when that template nets reversals. `conv` defaults to the MA convention, so an existing caller that
    passes no convention builds a byte-identical row.

    The row SHAPE is unchanged by the bucket registry (mig 1009): the five column-backed buckets still
    land in their own column; a line in any other bucket (a deduction, or one the tenant defined)
    carries its bucket key in `category` and its SIGNED amount in `payout_total`, and all five columns
    read 0 — no schema change per bucket, and every reader of the five columns is byte-identical."""
    order_type = src.get("order_type")
    product_name = src.get("product_name")
    raw = _sf(src.get("raw_amount"))
    category, booking = classify_line(raw, order_type, product_name, rules, conv, buckets)
    is_payout = booking is not None
    magnitude = booked_amount(raw, booking)
    row = dict(base)
    row.update({
        "source_report": base.get("source_report") or "ma_daily_tx",
        "account_id": src.get("account_id"), "account_name": src.get("account_name"),
        "store": src.get("store"), "rep_user": src.get("rep_user"),
        "order_number": src.get("order_number"), "order_type": order_type,
        "product_name": product_name, "trans_date": src.get("trans_date"),
        "due_date": src.get("due_date"), "payment_month": month_leg_of(product_name),
        "category": category, "raw_amount": round(raw, 2), "is_payout": is_payout,
        "payout_total": magnitude,
        "commission": 0, "spiff": 0, "equipment_rebate": 0, "residual_monthly": 0, "autopay_residual": 0,
    })
    if category in CATEGORIES:
        row[category] = magnitude
    return row


def summarize(rows, rules=None, legcls=None, conv=None, buckets=None, buckets_meta=None):
    """Roll a list of ledger rows into: per-category totals + counts, per-(category,payment_month) matrix,
    payout grand total, charge total, and the 'other' (unmapped-payout) count for surfacing gaps.

    THE BUCKETS COME FROM THE REGISTRY (mig 1009): `buckets` is the org's merged bucket list
    (load_buckets); None = the built-in house defaults. `categories` carries one entry per registry
    bucket (the five column-backed keys are ALWAYS present, so every existing reader keeps its keys) with
    its kind / label / order, and the payload adds the roll-up the owner asked for: `earned_total` (Σ
    earned buckets), `deductions_total` (Σ deduction buckets, signed), `net_total`. `payout_total` keeps
    its meaning — the NET the statement pays: every booked line's signed amount, earned buckets + other
    (unmapped) + deductions — which is exactly what it summed to before deductions existed (there were
    none), so every existing figure is byte-identical. A line whose category is a key the registry does
    not list (a deactivated or deleted bucket) is NOT dropped: it is summed under `unlisted` and still
    counted in payout_total, so money can never disappear from the total by editing the registry.

    ALSO (owner 2026-08-04) the COMMISSION LEG dimension: the same payout money split into the 1st-month
    leg vs the M2–M12 trailing legs, per category and in total. That is a DECOMPOSITION — for every
    category, m1 + trailing + unsplit == that category's existing, unchanged total, and the same holds
    for the grand payout total; `leg_identity_ok` proves it in the payload instead of asserting it.
    `rules`/`legcls` are optional: without them the leg is derived from each line's own payment month and
    label, which is exactly what pre-extension callers get plus the new (additive) keys."""
    reg = list(buckets) if buckets is not None else builtin_buckets()
    by_key = {b["key"]: b for b in reg}
    ordered = [b["key"] for b in reg]
    for c in CATEGORIES:                       # the column-backed five are always reported
        if c not in by_key:
            by_key[c] = _bucket_dict(c, CATEGORY_LABELS[c], KIND_EARNED, 999, [], None, True, is_active=False)
            ordered.append(c)
    cats = {c: {"total": 0.0, "count": 0, "kind": by_key[c]["kind"], "label": by_key[c]["label"],
                "active": bool(by_key[c].get("is_active", True)), "column_backed": c in COLUMN_BACKED,
                "sort_order": by_key[c]["sort_order"], "pl_line_key": by_key[c].get("pl_line_key")}
            for c in ordered}
    by_month = {}
    payout_total = charge_total = other_total = unlisted_total = 0.0
    other_count = unlisted_count = 0
    unlisted_keys = {}
    leg_cats = {c: {b: 0.0 for b in LEG_BUCKETS} for c in ordered + ["other", "unlisted"]}
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
        elif cat and cat != "exclude" and r.get("is_payout"):
            unlisted_total += amt
            unlisted_count += 1
            unlisted_keys[cat] = unlisted_keys.get(cat, 0) + 1
            payout_total += amt
            booked = "unlisted"
        if booked is None:                 # a charge is not a payout — it has no leg
            continue
        bucket, leg_month, _why = leg_of(r, rules, legcls, conv, reg)
        if bucket not in LEG_BUCKETS:
            bucket = "unsplit"
        leg_cats[booked][bucket] += amt
        leg_tot[bucket] += amt
        # ONE home for the rung key (commission_legs.ladder_key, §4a.2) — this was the FOURTH local
        # copy of the convention and the guard's CHECK 2c found it.
        from app.modules.commcalc import commission_legs as _cl_legs
        lk = _cl_legs.ladder_key(leg_month)
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
    earned_total = round(sum(v["total"] for v in cats.values() if v["kind"] == KIND_EARNED), 2)
    deductions_total = round(sum(v["total"] for v in cats.values() if v["kind"] == KIND_DEDUCTION), 2)
    identity_ok = abs(round(sum(leg_tot.values()), 2) - payout_total) < 0.01 and all(
        abs(round(sum(leg_cats[c].values()), 2) - cats[c]["total"]) < 0.01 for c in cats)
    return {
        "categories": cats,
        "category_labels": bucket_labels(reg),
        "by_month": by_month,
        "payout_total": payout_total,
        "charge_total": round(charge_total, 2),
        "other_total": round(other_total, 2),
        "other_count": other_count,
        "line_count": len(rows),
        # ── the bucket registry (mig 1009; additive) ──
        "buckets": [dict(b) for b in reg],
        "bucket_kinds": list(BUCKET_KINDS),
        "bucket_kind_labels": KIND_LABELS,
        "bucket_source": (buckets_meta or {}).get("source", BUCKETS_BUILTIN if buckets is None else BUCKETS_TENANT),
        "bucket_ready": bool((buckets_meta or {}).get("ready", buckets is not None)),
        "bucket_migration": BUCKET_MIGRATION,
        "earned_total": earned_total,
        "deductions_total": deductions_total,
        "net_total": payout_total,
        "unlisted_total": round(unlisted_total, 2),
        "unlisted_count": unlisted_count,
        "unlisted_keys": unlisted_keys,
        "payout_total_basis": ("payout_total = the NET the statement pays: Σ earned buckets + unmapped "
                               "('other') + deductions (signed) + any line filed under a bucket key the "
                               "registry no longer lists ('unlisted'); charges are outside it."),
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
