"""Pick-don't-type OPTIONS for the Commission-Plan / installment-schedule editors (RULE THREE §3b).

OWNER DIRECTIVE 2026-07-25: "Value not entered in commission plan should be from a drop down menu of
available options." A plan RULE pays on a match (`match_field` / `match_op` / `match_value`) against a sale
line. Hand-typing that value fails silently and in money:
  • a value this tenant's data never contains matches NOTHING → the rule pays $0 and no screen says so;
  • two hand-typed CONTAINS patterns that overlap ('home internet' + 'vhi', luxelink 2026-07-25) match the
    SAME lines → the rep is paid twice for one sale.

This module answers both from the tenant's OWN data. It is READ-ONLY and money-free: nothing here is called
by `_run_calculation`, `calculator.py` or `commission_engine.preview` — it only fills dropdowns and a
warning line in the editor.

TWO THINGS ARE RETURNED
  1. VOCABULARY — the fields / operators / payout kinds / tier bases the ENGINE actually understands,
     derived from `commission_engine` itself (`MATCH_FIELDS`, `PAYOUT_KINDS`, `_rule_matches`,
     `_tier_basis`) rather than re-typed in the UI. Two pages had already drifted from the engine
     (`plan-installments` offered 7 of the 10 match fields), which is exactly how a config UI teaches an
     operator that a capability doesn't exist.
  2. OPTIONS — the DISTINCT observed values per field, with a line count each, over the tenant's recent
     sales, plus the FACET table (distinct combinations of the seven real match fields + count). Rule
     matching depends only on those seven columns, so a few hundred facet rows let the editor compute an
     EXACT matched-line count per rule and an EXACT rule-vs-rule overlap — with no per-keystroke round trip
     and without ever pulling sale lines into the browser.

AGGREGATION HAPPENS IN POSTGRES (migration 240: `plan_match_facets` / `plan_match_facet_totals` /
`plan_sales_periods`). If those functions are absent the module degrades to a BOUNDED Python scan and says
so (`source: 'scan'`, `bounded: true`) — the feature works before the migration runs.

TENANT SAFETY: every read is `.eq("org_id", org_id)` / passes `p_org`, and the TTL cache key always carries
the org (blank org is never cached). No cross-tenant vocabulary, no house-only lists.
"""
import calendar
from datetime import datetime, timezone

from app.modules.commcalc import commission_engine
from app.modules.commcalc import accessory_catalog as _cache

# ── vocabulary (the ENGINE is the source of truth) ────────────────────────────────────────────────
# Operators, verbatim from commission_engine._rule_matches: 'contains' (substring), 'in' (comma list),
# and EVERYTHING ELSE falls through to the final `have == want` branch — i.e. equals is both the default
# and the behaviour of any unrecognised op. Offering only these three is therefore complete, and
# `save_commission_plan` already coerces anything else to 'equals'.
MATCH_OPS = ("equals", "contains", "in")

# The two SYNTHETIC match fields the engine stamps per line, with their CLOSED value vocabularies:
#   'accessory'         — commission_engine.preview stamps 'yes'/'no' from the shared AccessoryClassifier.
#   'activation_bucket' — router._resolve_ct_bucket / _classify_blank_ct_txn can only ever return
#                         'premium' | 'upgrade' | 'byod' (both hard-restrict to that set), so this list is
#                         the classifier's whole vocabulary, not a guess.
SYNTHETIC_VALUES = {
    "accessory": ("yes", "no"),
    "activation_bucket": ("premium", "upgrade", "byod"),
}

# The real sales columns the facet aggregate covers = every engine match field that is an actual column.
# Derived from the engine's own set, so a new field added there shows up here as "no options available"
# instead of silently pretending to have suggestions.
FACET_COLUMNS = ("department", "category", "contract_type", "tender_type",
                 "trans_type", "product_desc", "sku")

# ── custom-report vocabulary (owner 2026-08-26) ────────────────────────────────────────────────────
# A self-serve CUSTOM REPORT (mig 099: Activation Details, Sales by Product, Bill Payments, or any future
# sheet) lands EVERY source row verbatim as JSONB in commcalc.raw_custom_import, keyed by its ORIGINAL
# column header. The commission ENGINE keys a rule on a match field (department / category / contract_type /
# product_desc / …). This map names which raw headers ARE each engine match field, so a custom report's
# department/category/type VALUES become SELECTABLE in the plan editor the moment the report is ingested —
# by ANY path (manual upload, email/FTP sweep, API) — because all paths write to raw_custom_import and this
# module reads it live. DATA-DRIVEN: any future custom report contributes automatically (no per-report
# code); a new header alias is the only edit ever needed. ADDITIVE + SAFE: it only ADDS options, never
# removes one, and never changes a payout — the pay path never calls this module.
#
# MONEY HONESTY: the engine still reads raw_sales / daily_sales_feed only (commission_engine._read_sales),
# so a custom-report row is NOT in the engine's line set. A rule keyed on a value that exists ONLY in a
# custom report is therefore SELECTABLE but pays $0 today. Such values are flagged `custom_only` and the
# field carries a note saying so — see field_options(). Wiring the engine to pay custom lines is a separate,
# deliberately-deferred money decision (double-count / grain / no-rep-dimension risk — see the report).
_CUSTOM_IMPORT_TABLE = "raw_custom_import"

# engine match field  ->  the raw custom-report column headers (normalised, lower-cased) that ARE it.
# Keys are a subset of FACET_COLUMNS (the engine fields that are real columns). Aliases are matched against
# each captured row's header after strip().lower(), so 'Contract Type', 'contract_type' and 'CONTRACT TYPE'
# all resolve. Deliberately NOT mapping a bare 'type' (it would swallow Customer Type / Tender Type / …).
CUSTOM_FIELD_ALIASES = {
    "department":    ("department", "dept"),
    "category":      ("category", "product category"),
    "contract_type": ("contract type", "contract_type", "activation type", "action type"),
    "product_desc":  ("product desc", "product description", "product", "product_desc",
                      "item description", "item"),
    "tender_type":   ("tender type", "tender_type", "tender"),
    "sku":           ("sku", "sku#", "item sku"),
    "trans_type":    ("trans type", "transaction type", "trans_type"),
}

FIELD_HELP = {
    "any": "matches every line (a blanket rule) — no value needed",
    "contract_type": "the POS Contract Type as written on the line (blank on ~most non-phone lines)",
    "tender_type": "how the customer paid (e.g. an Acima lease tender)",
    "department": "the POS department the item belongs to",
    "category": "the POS category the item belongs to",
    "product_desc": "the item description — use 'contains' for a pattern; suggestions are real descriptions",
    "sku": "exact SKU; use 'in' for a comma list. NOT present on daily-feed lines",
    "trans_type": "Sale / Return / … (Returns never pay)",
    "accessory": "the shared accessory classification of the line (catalog + department/category/keyword)",
    "activation_bucket": ("the activation bucket resolved from THIS tenant's classification settings — "
                          "so a BLANK Contract Type can still count"),
}

PAYOUT_KIND_LABELS = {
    "flat_per_unit": ("Flat $ per unit", "amount"),
    "pct_mrc": ("% of MRC (raw_mi)", "pct"),
    "pct_gp": ("% of GP", "pct"),
    "pct_price": ("% of price (sale price)", "pct"),
    "pct_price_over_cost": ("% of price − cost", "pct"),
    "flat": ("Flat $ once", "amount"),
}

# commission_engine._tier_basis: 'lines' | 'transactions' are honoured, ANY other value (including NULL)
# means the legacy per-rule-unit count. '' is the wire value for "legacy".
TIER_BASES = (
    {"value": "", "label": "Legacy — every qualifying matched line (default)",
     "help": "one activation that rings 3 lines counts 3; a line matched by two rules counts twice"},
    {"value": "transactions", "label": "Distinct transactions matching the tier rule",
     "help": "“30 activations” means 30 transactions"},
    {"value": "lines", "label": "Lines matching the tier rule",
     "help": "counts matched line items, de-duplicated by nothing"},
)

# base_tier_metric is read by commission_engine._tier_multiplier ONLY as an on/off switch ('' / 'none'
# disables tiering entirely); WHAT is counted comes from the tier count basis + tier matcher. These are the
# labels the product has used; a tenant's own stored values are unioned in by field_options() so an
# existing plan's metric is never dropped from its own dropdown (zero-wipe).
TIER_METRIC_SUGGESTIONS = ("none", "activations", "upgrades", "boxes")

_CACHE_KIND = "plan_field_options"


def _now_business():
    """Today in the business timezone (falls back to UTC) — the anchor for the recent-months window."""
    try:
        from zoneinfo import ZoneInfo
        from app.core.config import settings
        return datetime.now(timezone.utc).astimezone(
            ZoneInfo(getattr(settings, "BUSINESS_TZ", None) or "America/New_York")).date()
    except Exception:
        return datetime.now(timezone.utc).date()


def window_labels(months=3, period=""):
    """(month labels newest-first, every period spelling to query).

    The window is the last `months` calendar months INCLUDING the current one, plus — always — whatever
    period the editor is currently previewing, so the options can never be empty just because the operator
    is looking at an older month. Spellings go through commission_engine._pvariants (the SAME helper the
    pay path uses), so 'June 2026' and '2026-06' are both covered."""
    today = _now_business()
    n = max(1, min(int(months or 3), 24))
    labels, y, m = [], today.year, today.month
    for _ in range(n):
        labels.append(f"{calendar.month_name[m]} {y}")
        m -= 1
        if m == 0:
            m, y = 12, y - 1
    extra = (period or "").strip()
    if extra and extra not in labels:
        labels.append(extra)
    spellings = []
    for lab in labels:
        for v in commission_engine._pvariants(lab):
            if v not in spellings:
                spellings.append(v)
    return labels, spellings


def vocabulary():
    """The engine's own vocabulary, shaped for a <select>. PURE (no I/O, no org)."""
    fields = []
    for f in sorted(commission_engine.MATCH_FIELDS, key=lambda x: (x != "any", x)):
        fields.append({
            "value": f,
            "label": f,
            "help": FIELD_HELP.get(f, ""),
            "synthetic": f in SYNTHETIC_VALUES,
            # a closed vocabulary means the picker must NOT offer free entry
            "closed": f in SYNTHETIC_VALUES,
            "column": f in FACET_COLUMNS,
        })
    return {
        "match_fields": fields,
        "match_ops": [
            {"value": "equals", "label": "equals", "help": "exact value (case-insensitive)"},
            {"value": "contains", "label": "contains", "help": "substring pattern — the only op that may need free text"},
            {"value": "in", "label": "in", "help": "any of a comma-separated list"},
        ],
        # 'pct_price' (% of the SALE PRICE / ext_price) was defined in PAYOUT_KIND_LABELS and fully
        # supported by commission_engine._line_payout, but was DROPPED from this served list — so the plan
        # editor's payout-kind dropdown never offered it, while the frontend FALLBACK_VOCAB (used only when
        # this endpoint fails) DID. That left an owner unable to build "10% of accessory SALES $" from the
        # editor: %-of-GP and %-of-(price-cost) both depend on the B2B catalog cost, which is untrustworthy
        # for accessories (see _line_payout, owner 2026-08-04), and %-of-MRC does not apply to accessories.
        # Surfacing it changes NO payout math (the kind already computed identically wherever a saved rule
        # used it); it only lets the editor SELECT the already-correct kind. Ordered to match the fallback.
        "payout_kinds": [{"value": k, "label": PAYOUT_KIND_LABELS[k][0], "uses": PAYOUT_KIND_LABELS[k][1]}
                         for k in ("flat_per_unit", "pct_mrc", "pct_gp", "pct_price",
                                   "pct_price_over_cost", "flat")
                         if k in commission_engine.PAYOUT_KINDS],
        "tier_bases": [dict(b) for b in TIER_BASES],
        "tier_metrics": list(TIER_METRIC_SUGGESTIONS),
    }


# ── facet aggregation ─────────────────────────────────────────────────────────────────────────────
def _facets_rpc(client, org_id, spellings, source, limit):
    """(rows, totals) from migration 240's Postgres aggregate, or (None, None) when it isn't there."""
    try:
        rows = (client.schema("commcalc").rpc("plan_match_facets", {
            "p_org": org_id, "p_periods": spellings, "p_source": source, "p_limit": int(limit) + 1,
        }).execute().data) or []
    except Exception:
        return None, None
    totals = None
    try:
        t = (client.schema("commcalc").rpc("plan_match_facet_totals", {
            "p_org": org_id, "p_periods": spellings, "p_source": source,
        }).execute().data) or []
        if t:
            totals = {"lines": int(t[0].get("lines") or 0), "combos": int(t[0].get("combos") or 0)}
    except Exception:
        totals = None
    return rows, totals


def _facets_scan(client, org_id, spellings, source, max_rows=20000):
    """BOUNDED fallback when migration 240 hasn't run: page the seven match columns (nothing else) and
    aggregate in Python. Hard-capped at `max_rows` lines so a large tenant can never turn an options
    lookup into a 40k-row pull; the cap is reported to the UI as `bounded`."""
    table = "daily_sales_feed" if source == "feed" else "raw_sales"
    cols = [c for c in FACET_COLUMNS if not (source == "feed" and c == "sku")]
    sel = ",".join(cols + ["voided"])
    agg, scanned, start, page, hit_cap, errored = {}, 0, 0, 1000, False, False
    while True:
        try:
            rows = (client.schema("commcalc").table(table).select(sel)
                    .eq("org_id", org_id).in_("period", spellings)
                    .range(start, start + page - 1).execute().data) or []
        except Exception:
            errored = start == 0     # the FIRST page failing = we never read anything (unreachable table)
            break
        for r in rows:
            if commission_engine._is_voided(r.get("voided")):
                continue
            if str(r.get("trans_type", "") or "").strip() == "Return":
                continue
            key = tuple(str(r.get(c, "") or "").strip() for c in FACET_COLUMNS)
            agg[key] = agg.get(key, 0) + 1
            scanned += 1
        if len(rows) < page:
            break
        start += page
        if start >= max_rows:
            hit_cap = True
            break
    out = [dict(zip(FACET_COLUMNS, k), lines=v) for k, v in agg.items()]
    out.sort(key=lambda x: (-x["lines"], x["product_desc"], x["category"]))
    return out, {"lines": scanned, "combos": len(out)}, hit_cap, errored


def _load_facets(client, org_id, spellings, limit):
    """Facet rows for the tenant, mirroring commission_engine._read_sales' source precedence: raw_sales
    first, daily_sales_feed only when raw_sales has nothing for the window. Returns a dict ready to ship."""
    source_used, used_rpc, bounded, degraded = "raw_sales", True, False, False
    rows, totals = _facets_rpc(client, org_id, spellings, "raw_sales", limit)
    if rows is not None and not rows:
        f_rows, f_tot = _facets_rpc(client, org_id, spellings, "feed", limit)
        if f_rows:
            rows, totals, source_used = f_rows, f_tot, "feed"
    if rows is None:                                   # migration 240 not applied → bounded scan
        used_rpc = False
        rows, totals, bounded, err1 = _facets_scan(client, org_id, spellings, "raw_sales")
        if not rows:
            rows, totals, bounded, err2 = _facets_scan(client, org_id, spellings, "feed")
            source_used = "feed" if rows else "raw_sales"
            degraded = err1 and err2                   # neither table could be read at all
    rows = rows or []
    truncated = len(rows) > limit
    rows = rows[:limit]
    covered = sum(int(r.get("lines") or 0) for r in rows)
    total_lines = int((totals or {}).get("lines") or covered)
    return {
        "rows": rows, "source_table": source_used, "source": "rpc" if used_rpc else "scan",
        "bounded": bounded, "degraded": degraded, "truncated": truncated or bounded,
        "lines_covered": covered, "lines_total": total_lines,
        "combos_total": int((totals or {}).get("combos") or len(rows)),
    }


# ── custom-report value harvest (data-driven; NO per-report hardcoding) ───────────────────────────
def _custom_report_values(client, org_id, max_rows=20000):
    """Distinct values per engine match field observed in THIS org's custom-report captures
    (commcalc.raw_custom_import, mig 099), harvested by mapping each captured row's ORIGINAL column header
    to an engine match field via CUSTOM_FIELD_ALIASES.

    DATA-DRIVEN: every custom report (whatever report_key) contributes its department / category /
    contract_type / product / … values automatically — no per-report code. BOUNDED: a hard-capped page
    scan (`max_rows`) so a large tenant can never turn an options lookup into a giant pull; the cap is
    reported. Org-scoped on every read. NEVER raises — degrades to ({}, set(), False).

    Returns (values_by_field, report_keys, bounded):
      values_by_field: {engine_field: {display_value: custom_line_count}}
      report_keys:     the distinct report_key(s) seen (for the summary / transparency)
      bounded:         True when the scan hit the row cap (values may be incomplete)."""
    alias_to_field = {}
    for field, aliases in CUSTOM_FIELD_ALIASES.items():
        for a in aliases:
            alias_to_field[str(a).strip().lower()] = field
    values: dict = {}
    report_keys: set = set()
    start, page, bounded = 0, 1000, False
    while True:
        try:
            rows = (client.schema("commcalc").table(_CUSTOM_IMPORT_TABLE).select("data,report_key")
                    .eq("org_id", org_id).range(start, start + page - 1).execute().data) or []
        except Exception:
            break                              # table absent (pre-mig-099) / unreadable → no custom options
        for r in rows:
            data = r.get("data")
            if not isinstance(data, dict):
                continue
            rk = str(r.get("report_key") or "").strip()
            if rk:
                report_keys.add(rk)
            for k, v in data.items():
                field = alias_to_field.get(str(k or "").strip().lower())
                if not field:
                    continue
                val = str(v if v is not None else "").strip()
                if not val or val.lower() in ("nan", "none", "null"):
                    continue
                d = values.setdefault(field, {})
                d[val] = d.get(val, 0) + 1
        if len(rows) < page:
            break
        start += page
        if start >= max_rows:
            bounded = True
            break
    return values, report_keys, bounded


# ── contract-type / activation-bucket config (REUSED, never re-implemented) ───────────────────────
def _ct_context(client, org_id):
    """What a contract_type rule can match for THIS tenant: the resolution mode (mig 232) plus the
    tenant's own contract_type → bucket map (mig 213) and blank-CT activation rules (mig 224). Reuses
    commission_engine's readers, so the editor can never describe a classification the pay path doesn't do."""
    try:
        pay_cfg = commission_engine._plan_pay_config(client, org_id)
    except Exception:
        pay_cfg = {"plan_ct_resolution": "raw"}
    try:
        ct_map, rules = commission_engine._read_ct_classification_config(client, org_id)
    except Exception:
        ct_map, rules = {}, []
    buckets = {}
    for _ct, b in (ct_map or {}).items():
        b = str(b or "").strip().lower()
        if b and b != "none":
            buckets[b] = buckets.get(b, 0) + 1
    rule_buckets = {}
    for r in (rules or []):
        b = str((r or {}).get("bucket") or "").strip().lower()
        if b:
            rule_buckets[b] = rule_buckets.get(b, 0) + 1
    return {
        "resolution": pay_cfg.get("plan_ct_resolution", "raw"),
        "mapped_contract_types": len(ct_map or {}),
        "buckets_from_map": buckets,
        "buckets_from_rules": rule_buckets,
        "ct_map": ct_map or {},
    }


def _resolved_bucket(ct, ct_map):
    """The activation bucket a CONTRACT-TYPE value resolves to for this tenant ('' when none). Calls the
    SHARED display resolver (router._resolve_ct_bucket honouring the tenant's map); falls back to the code
    classifier if the router can't be imported. Never raises. Used only to annotate options and to let the
    editor mirror the engine's `_ct_resolved` candidate when resolution == 'mapped'."""
    try:
        from app.modules.commcalc.router import _resolve_ct_bucket
        return str(_resolve_ct_bucket(str(ct or ""), ct_map) or "")
    except Exception:
        try:
            from app.modules.commcalc.calculator import classify_contract_type
            return str(classify_contract_type(str(ct or "")) or "")
        except Exception:
            return ""


# ── periods ──────────────────────────────────────────────────────────────────────────────────────
def _sales_periods(client, org_id, limit=36):
    """[{value, lines, sources}] — the period labels this tenant actually has sales for, newest-data-first.
    Returns [] (page keeps its free-text period box) when migration 240 hasn't run."""
    try:
        rows = (client.schema("commcalc").rpc("plan_sales_periods", {
            "p_org": org_id, "p_limit": int(limit)}).execute().data) or []
    except Exception:
        return []
    merged = {}
    for r in rows:
        p = str(r.get("period") or "").strip()
        if not p:
            continue
        e = merged.setdefault(p, {"value": p, "lines": 0, "sources": []})
        e["lines"] += int(r.get("lines") or 0)
        src = str(r.get("source") or "")
        if src and src not in e["sources"]:
            e["sources"].append(src)
    return sorted(merged.values(), key=lambda x: -x["lines"])[:limit]


# ── the payload ──────────────────────────────────────────────────────────────────────────────────
def field_options(client, org_id, months=3, period="", limit=4000, value_limit=400,
                  plan_values=None):
    """Everything the plan editors need to make every value a PICK.

    plan_values: {field: [values already stored on this tenant's plans]} — merged into the option lists
    (flagged `stored_only`) so a value that predates the current data still DISPLAYS and is never silently
    wiped by the picker (zero-wipe discipline)."""
    labels, spellings = window_labels(months, period)
    facets = _load_facets(client, org_id, spellings, limit)
    ctx = _ct_context(client, org_id)
    rows = facets["rows"]

    # per-field distinct values + line counts, straight off the facet grain
    fields = {}
    for col in FACET_COLUMNS:
        counts = {}
        for r in rows:
            v = str(r.get(col) or "").strip()
            if not v:
                continue
            counts[v] = counts.get(v, 0) + int(r.get("lines") or 0)
        vals = sorted(({"value": k, "lines": v} for k, v in counts.items()),
                      key=lambda x: (-x["lines"], x["value"].lower()))
        entry = {
            "values": vals[:value_limit],
            "distinct": len(vals),
            "truncated": bool(facets["truncated"] or len(vals) > value_limit),
            "free_text": col == "product_desc",       # only a substring pattern legitimately needs typing
            "note": None,
        }
        if col == "sku" and facets["source_table"] == "feed":
            entry["note"] = ("This tenant's sales for the window come from the daily email feed, which "
                             "carries no SKU column — a sku rule cannot match those lines.")
        if col == "contract_type":
            entry["resolution"] = ctx["resolution"]
            # 'mapped' (mig 232) lets a contract_type rule ALSO match the resolved bucket. Offer the bucket
            # names as values ONLY when the tenant is actually in that mode — otherwise the option would be
            # a rule that can never match.
            if ctx["resolution"] == "mapped":
                for b in SYNTHETIC_VALUES["activation_bucket"]:
                    lines = sum(int(r.get("lines") or 0) for r in rows
                                if _resolved_bucket(r.get("contract_type"), ctx["ct_map"]) == b)
                    entry["values"].append({"value": b, "lines": lines, "resolved_bucket": True})
                entry["note"] = ("Contract-type resolution is 'mapped': a rule also matches the RESOLVED "
                                 "activation bucket (premium / upgrade / byod).")
            else:
                entry["note"] = ("Contract-type resolution is 'raw': only the literal value above matches. "
                                 "Key the rule on activation_bucket to pay on blank Contract Type lines.")
        fields[col] = entry

    # synthetic fields: closed vocabularies, no free text
    for f, vals in SYNTHETIC_VALUES.items():
        entry = {"values": [{"value": v} for v in vals], "distinct": len(vals), "truncated": False,
                 "free_text": False, "closed": True, "note": None}
        if f == "activation_bucket":
            entry["note"] = (
                f"Resolved from this tenant's own classification settings: "
                f"{ctx['mapped_contract_types']} contract type(s) mapped, "
                f"{sum(ctx['buckets_from_rules'].values())} blank-Contract-Type activation rule(s)."
                if (ctx["mapped_contract_types"] or ctx["buckets_from_rules"])
                else ("No contract-type map or activation rules are configured for this tenant yet, so this "
                      "field falls back to the built-in classifier (BYOD / Upgrade / premium keywords)."))
            for v in entry["values"]:
                v["config_hits"] = (ctx["buckets_from_map"].get(v["value"], 0)
                                    + ctx["buckets_from_rules"].get(v["value"], 0))
        fields[f] = entry
    fields["any"] = {"values": [], "distinct": 0, "truncated": False, "free_text": False,
                     "closed": True, "note": "A blanket rule — no value is used."}

    # engine match fields with no option source at all (a field added to the engine after this module)
    for f in commission_engine.MATCH_FIELDS:
        if f not in fields:
            fields[f] = {"values": [], "distinct": 0, "truncated": False, "free_text": True,
                         "closed": False,
                         "note": "No observed values available for this field — type the value."}

    # facet table for the editor's exact matched-line / overlap arithmetic (dictionary-encoded so the
    # payload stays small: one small int per column instead of the string).
    dict_map = {c: [] for c in FACET_COLUMNS}
    index = {c: {} for c in FACET_COLUMNS}
    enc_rows = []
    ct_resolved_dict, ct_resolved_index = [], {}
    mapped = ctx["resolution"] == "mapped"
    for r in rows:
        enc = []
        for c in FACET_COLUMNS:
            v = str(r.get(c) or "").strip()
            i = index[c].get(v)
            if i is None:
                i = index[c][v] = len(dict_map[c])
                dict_map[c].append(v)
            enc.append(i)
        if mapped:
            rv = _resolved_bucket(r.get("contract_type"), ctx["ct_map"])
            j = ct_resolved_index.get(rv)
            if j is None:
                j = ct_resolved_index[rv] = len(ct_resolved_dict)
                ct_resolved_dict.append(rv)
            enc.append(j)
        enc.append(int(r.get("lines") or 0))
        enc_rows.append(enc)

    # CUSTOM-REPORT VALUES (owner 2026-08-26). A self-serve custom report (Activation Details / Sales by
    # Product / any future sheet) lands in raw_custom_import with its own department / category / type /
    # product columns. Surface those distinct values into the SAME field pickers so the plan editor can
    # ASSIGN them to a commission rule. DATA-DRIVEN (CUSTOM_FIELD_ALIASES maps headers → engine fields; no
    # per-report code) and reached by EVERY ingest path (all write raw_custom_import; this reads it live).
    # ADDITIVE: never drops a value, never changes a payout. HONEST: a custom-only value is flagged
    # `custom_only` with lines=0 (it is NOT in raw_sales) and the field carries a note that a rule on it is
    # selectable but pays $0 until the custom-report money path is wired (the engine reads raw_sales today).
    cr_values, cr_reports, cr_bounded = _custom_report_values(client, org_id)
    custom_field_summary = {}
    for f, valcounts in cr_values.items():
        entry = fields.get(f)
        if not entry:
            continue
        have = {str(v.get("value")).strip().lower() for v in entry["values"]}
        added = 0
        for val, cnt in sorted(valcounts.items(), key=lambda kv: (-kv[1], kv[0].lower())):
            if val.lower() in have:
                continue                       # already a live raw_sales value — don't duplicate or reflag
            entry["values"].append({"value": val, "lines": 0, "custom_only": True,
                                    "source": "custom_report", "custom_lines": cnt})
            have.add(val.lower())
            added += 1
        if added:
            entry["custom_report_values"] = added
            entry["truncated"] = True          # a custom-sourced list is never a closed picker → allow typing
            if f == "department":
                # The Activation Details report's Department column (= service plan) IS wired to pay: a
                # `department in <these>` flat-per-unit rule pays $1/activation on a plan whose activation
                # source is "Activation Details report" (the engine stamps department onto the bridged
                # activation lines). Values from OTHER custom reports (e.g. Sales by Product) still don't pay.
                _cr_note = (f"{added} department value(s) come from custom reports. The Activation Details "
                            "service-plan values PAY when the plan's activation source is 'Activation Details "
                            "report' — build a department rule on them to pay per activation. Department "
                            "values from other reports are selectable but do not pay yet.")
            else:
                _cr_note = (f"{added} value(s) come from custom reports (e.g. Activation Details / Sales by "
                            "Product) and are not in this tenant's raw_sales. A rule on a custom-only value is "
                            "SELECTABLE but will not pay until the custom-report money path is wired — the "
                            "commission engine reads raw_sales / the daily feed today.")
            entry["note"] = (entry["note"] + " " + _cr_note) if entry.get("note") else _cr_note
            custom_field_summary[f] = added

    # the tenant's own stored plan values, so an option list never drops a value already in use
    for f, vals in (plan_values or {}).items():
        entry = fields.get(f)
        if not entry:
            continue
        have = {str(v.get("value")).strip().lower() for v in entry["values"]}
        for v in vals:
            v = str(v or "").strip()
            if v and v.lower() not in have:
                entry["values"].append({"value": v, "lines": 0, "stored_only": True})
                have.add(v.lower())

    return {
        "ready": True,
        "window": {"months": max(1, min(int(months or 3), 24)), "labels": labels,
                   "periods_queried": spellings},
        "source": facets["source"], "source_table": facets["source_table"],
        "bounded": facets["bounded"], "degraded": facets["degraded"],
        "vocab": vocabulary(),
        "fields": fields,
        "facets": {
            "columns": list(FACET_COLUMNS),
            "dict": dict_map,
            "ct_resolved": ct_resolved_dict if mapped else None,
            "rows": enc_rows,
            "truncated": facets["truncated"],
            "lines_covered": facets["lines_covered"],
            "lines_total": facets["lines_total"],
            "combos_total": facets["combos_total"],
        },
        "contract_type_resolution": ctx["resolution"],
        # Which custom reports contributed selectable values, and to which fields. A rule on any of these
        # custom-only values is SELECTABLE but pays $0 until the custom-report money path is wired — the
        # engine reads raw_sales today (see the per-field note). Present (possibly empty) whenever the read
        # succeeded, so a consumer can tell "no custom reports" from "custom reads failed".
        "custom_reports": {
            "report_keys": sorted(cr_reports),
            "fields": custom_field_summary,
            "bounded": cr_bounded,
            "note": ("Values from custom reports are offered for assignment but do not pay yet — the "
                     "commission engine computes against raw_sales / the daily feed, not custom-report "
                     "lines.") if custom_field_summary else None,
        },
        "note": None if enc_rows else (
            "This tenant's sales could not be read right now, so the value pickers are empty — you can "
            "still type a value, and it will be checked the next time this loads."
            if facets["degraded"] else
            "No sale lines found for the last few months — the pickers will be empty until this tenant's "
            "sales land (raw_sales or the daily feed)."),
    }


def plan_stored_values(client, org_id):
    """{field: [values]} already stored on this org's plans / tier matchers / installment triggers, so the
    pickers can DISPLAY a value that is no longer in the data instead of blanking it (zero-wipe). Read-only,
    org-scoped, never raises."""
    out = {}

    def _add(field, value):
        f = str(field or "").strip().lower()
        v = str(value or "").strip()
        if not f or not v or f == "any":
            return
        out.setdefault(f, [])
        if v not in out[f]:
            out[f].append(v)

    try:
        for r in (client.schema("commcalc").table("commission_rule")
                  .select("match_field,match_value").eq("org_id", org_id)
                  .limit(5000).execute().data) or []:
            _add(r.get("match_field"), r.get("match_value"))
    except Exception:
        pass
    try:      # pre-mig-232 database → the tier_match_* columns don't exist → skip, never 500
        for r in (client.schema("commcalc").table("commission_plan")
                  .select("tier_match_field,tier_match_value")
                  .eq("org_id", org_id).limit(2000).execute().data) or []:
            _add(r.get("tier_match_field"), r.get("tier_match_value"))
    except Exception:
        pass
    try:
        for r in (client.schema("commcalc").table("plan_installment_schedule")
                  .select("trigger_match_field,trigger_match_value").eq("org_id", org_id)
                  .limit(2000).execute().data) or []:
            _add(r.get("trigger_match_field"), r.get("trigger_match_value"))
    except Exception:
        pass
    return out


def stored_tier_metrics(client, org_id):
    """Distinct base_tier_metric values already stored on this org's plans (zero-wipe for that dropdown)."""
    out = []
    try:
        for r in (client.schema("commcalc").table("commission_plan").select("base_tier_metric")
                  .eq("org_id", org_id).limit(2000).execute().data) or []:
            v = str(r.get("base_tier_metric") or "").strip()
            if v and v not in out:
                out.append(v)
    except Exception:
        return []
    return out


def build(client, org_id, months=3, period="", limit=4000, value_limit=400):
    """Cached entry point used by the endpoint. TTL-bounded per (org, window) through the shared
    commcalc config cache (45s, env COMMCALC_CFG_CACHE_TTL) — the same bound the classification config
    uses, so a fresh upload or a hand-run SQL edit shows up in the pickers within one TTL. Read-only:
    there is no money path to invalidate for."""
    kind = f"{_CACHE_KIND}|{months}|{(period or '').strip()}|{limit}|{value_limit}"
    hit = _cache.cache_get(kind, org_id, client=client)
    if hit is not None:
        return hit
    gen = _cache.cache_generation()
    payload = field_options(client, org_id, months=months, period=period, limit=limit,
                            value_limit=value_limit,
                            plan_values=plan_stored_values(client, org_id))
    metrics = stored_tier_metrics(client, org_id)
    for m in metrics:
        if m not in payload["vocab"]["tier_metrics"]:
            payload["vocab"]["tier_metrics"].append(m)
    payload["periods"] = _sales_periods(client, org_id)
    return _cache.cache_put(kind, org_id, payload, client=client, gen=gen)


# ── shared matcher (kept next to the vocabulary it describes) ─────────────────────────────────────
def facet_matches(row_values, rule, ct_resolved=""):
    """The engine's `_rule_matches` semantics evaluated against a FACET row ({column: value}).

    This exists so the proof harness can assert, case by case, that the browser-side mirror in
    `commcalc/_lib/planMatch.tsx` agrees with `commission_engine._rule_matches` — the editor's
    "matches nothing" / "N lines also match rule X" warnings are only trustworthy if the three
    implementations agree. It is NOT used by any pay path."""
    field = str(rule.get("match_field") or "any").strip().lower()
    if field == "any":
        return True
    if field not in FACET_COLUMNS:
        return None                       # synthetic / unknown field → not analysable from facets
    row = dict(row_values)
    if ct_resolved:
        row["_ct_resolved"] = ct_resolved
    return commission_engine._rule_matches(row, rule)
