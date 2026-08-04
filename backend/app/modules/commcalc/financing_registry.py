"""FINANCING VENDOR REGISTRY — who finances a sale, per tenant, per carrier (migration 272).

OWNER DIRECTIVE (in-chat 2026-08-04, verbatim): "need another report for tracking the financing, edge in
case of total and acima in case of boost, acima could also be added to total at a later date and more
vendors can be added to both carriers, this will be called Financing report…"

WHAT THIS MODULE IS
  The single place that answers "is this sale line a financed sale, and by whom?" — as CONFIG, not code:
    * a per-tenant list of vendors (commcalc.financing_vendor),
    * each assigned to one or MORE carriers (commcalc.financing_vendor_carrier) — so moving ACIMA onto
      Total later is one row, never a code change,
    * each recognised by DETECTION RULES in the same (field, operator, value) vocabulary the commission
      plan matcher already uses.

WHAT IT DELIBERATELY DOES **NOT** DO
  It does not invent a sixth classifier and it does not guess a pattern. Two of the three detection
  sources INHERIT a mapping the tenant already owns:

    'plan_rule'    — take the matcher straight off an existing commcalc.commission_rule. This is how the
                     Edge vendor reuses the SAME tender matcher the edge pay rule uses (the rule was
                     re-keyed from product_desc to tender_type on 2026-07-27 precisely because "edge" is
                     the TENDER, not the Motorola Edge — see [[edge-is-financing-not-device-model]]).
                     The report can therefore never disagree with what actually pays.
    'acima_config' — take the tenant's legacy ACIMA tender mapping
                     (commcalc.commission_config.acima_tenders, migration 094), which is what
                     calculator.py already counts the Boost ACIMA spiff from, with the SAME `contains`
                     semantics. Not configured -> the vendor honestly reports "detection not configured"
                     instead of matching something invented here.
    'rules'        — the vendor's own explicit rows.

TWO SEPARATE GUARDS AGAINST THE MODEL-NAME COLLISION CLASS — and it matters that they are separate:
  1. WORD-ANCHORED BY DEFAULT. `word` matches the token, never a substring, so 'edge' cannot be found
     inside 'wedge' (this is the same guard the RTR payout exclusion uses: 'RTR' is not 'CARTRIDGE').
  2. THE FIELD, NOT THE OPERATOR, IS THE REAL DEFENCE. Word-anchoring does **not** save a
     product-description rule: 'edge' IS a standalone token in "MOTOROLA EDGE 50 PRO". That is exactly
     the bug of 2026-07-27 ("these sales dont qualify as edge, it is the name of phone model which is
     edge … edge is only of the tender method is tw finnacing"). A financed sale is identified by HOW
     THE CUSTOMER PAID — `tender_type` — and every matcher on any other field is returned with a
     `field_warning` the admin UI shows in red. The registry does not forbid it (a tenant whose POS
     really does mark financing in a Department must be able to say so); it refuses to let it be quiet.

FIELD VOCABULARY is deliberately narrower than the pay engine's: only columns that exist in BOTH
`raw_sales` AND `daily_sales_feed`, because the report reads the union of the two (selecting a
feed-absent column such as `sku` throws and would silently return zero rows).

MULTI-TENANT: every loader takes org_id and scopes on it; every writer stamps it. PURE where possible —
every matcher/normalizer takes its config as an argument so the whole thing is unit-testable with no DB.
DEGRADES: with migration 272 unapplied every loader returns the code seeds with `table_ready=False`, so
the pages render an honest "run migration 272" state instead of breaking.
"""
import re

VENDOR_TABLE = "financing_vendor"
CARRIER_TABLE = "financing_vendor_carrier"
RULE_TABLE = "financing_detection_rule"
TARGET_TABLE = "financing_target"

# Columns present in BOTH raw_sales and daily_sales_feed (see module docstring).
MATCH_FIELDS = ("tender_type", "product_desc", "department", "category", "contract_type", "trans_type")
MATCH_FIELD_LABELS = {
    "tender_type": "Tender type — how the customer paid (this is where a lease/financing tender lands)",
    "product_desc": "Product description",
    "department": "Department",
    "category": "Category",
    "contract_type": "Contract type",
    "trans_type": "Transaction type",
}
MATCH_OPS = ("word", "equals", "contains", "in", "prefix", "suffix")
MATCH_OP_LABELS = {
    "word": "is the word (anchored — 'edge' will not match 'wedge')",
    "equals": "is exactly",
    "contains": "contains (substring — can collide with device model names)",
    "in": "is one of (comma separated)",
    "prefix": "starts with",
    "suffix": "ends with",
}
DETECTION_SOURCES = ("rules", "plan_rule", "acima_config")
AMOUNT_BASES = ("unit_line", "transaction")

# ── the ONE seeded thing in this module, and it is DATA, not a branch ─────────────────────────────
# The owner named two vendors and their starting carrier. Each seed ships with **no invented pattern**:
# its detection INHERITS a mapping the tenant already owns, and if that mapping is empty the vendor
# reports "detection not configured". A stored row with the same vendor_key REPLACES its seed entirely.
VENDOR_SEEDS = [
    {"vendor_key": "edge", "label": "Edge financing",
     "detection_source": "plan_rule", "detection_ref": {"rule_ids": []},
     "amount_basis": "unit_line", "sort_order": 10, "enabled": True, "source": "seed",
     "carriers": [{"carrier_name": "Total"}],
     "notes": ("Edge is a TENDER (how the customer financed the sale), not a device model. Point this "
               "vendor at the plan rule that already pays the edge tender so the report and the payout "
               "can never disagree.")},
    {"vendor_key": "acima", "label": "ACIMA lease-to-own",
     "detection_source": "acima_config", "detection_ref": None,
     "amount_basis": "unit_line", "sort_order": 20, "enabled": True, "source": "seed",
     "carriers": [{"carrier_name": "Boost"}],
     "notes": ("Inherits this tenant's existing ACIMA tender mapping (Settings → ACIMA tenders), which is "
               "the same mapping the Boost ACIMA spiff is counted from. Add carriers here to pay ACIMA on "
               "another carrier — no code change is needed.")},
]

_WORD_RX_CACHE = {}


def _word_hit(text, kw):
    """Whole-word/phrase match, alphanumeric-aware — the same semantics the rate-plan matcher and the
    payout-exclusion map use, so 'edge' never matches 'Motorola Edge 50'... but DOES match the standalone
    token 'Edge'. PURE."""
    rx = _WORD_RX_CACHE.get(kw)
    if rx is None:
        rx = _WORD_RX_CACHE[kw] = re.compile(r"(?<![a-z0-9])" + re.escape(kw) + r"(?![a-z0-9])", re.I)
    return bool(rx.search(text))


def clean_key(v):
    """A vendor key: lowercase, alphanumeric + underscore. PURE."""
    s = re.sub(r"[^a-z0-9_]+", "_", str(v or "").strip().lower()).strip("_")
    return s[:48]


# ══ normalisation (PURE) ═════════════════════════════════════════════════════════════════════════
def normalize_matcher(row):
    """One detection rule (stored row, or a matcher lifted off a commission_rule) → the canonical shape,
    or None when unusable. PURE. An unknown field/op is REJECTED rather than silently coerced, because a
    silently-coerced matcher is how a report reports zero and calls it healthy."""
    if not isinstance(row, dict):
        return None
    f = str(row.get("match_field") or "").strip().lower()
    op = str(row.get("match_op") or "word").strip().lower()
    v = str(row.get("match_value") or "").strip()
    if f not in MATCH_FIELDS or not v:
        return None
    if op not in MATCH_OPS:
        return None
    try:
        pri = int(row.get("priority") if row.get("priority") is not None else 100)
    except (TypeError, ValueError):
        pri = 100
    return {"id": row.get("id"), "match_field": f, "match_op": op, "match_value": v,
            "priority": pri, "enabled": bool(row.get("enabled", True)),
            "source": row.get("source") or "tenant", "notes": row.get("notes") or None,
            "field_warning": field_warning(f, op, v)}


def field_warning(match_field, match_op="word", match_value=""):
    """A plain-English warning for a matcher that is likely to mis-classify, else None. PURE.

    Financing is a PAYMENT METHOD, so `tender_type` is the only field that carries it reliably. A
    product-description matcher looks fine and quietly claims every handset whose MODEL NAME contains
    the word — word-anchoring does not help, because 'edge' is a real token in "MOTOROLA EDGE 50 PRO".
    This is the 2026-07-27 bug in its general form."""
    f = str(match_field or "").strip().lower()
    if f == "tender_type":
        if str(match_op or "").strip().lower() == "contains":
            return ("'contains' can match part of another tender's name. 'is the word' is safer unless "
                    "you need a partial match.")
        return None
    if f in ("product_desc", "category", "department"):
        return (f"This matches on {MATCH_FIELD_LABELS.get(f, f)}, not on how the customer paid. A device "
                f"whose MODEL NAME contains “{match_value}” will be counted as a financed sale "
                f"even with word matching (“edge” is a real word inside “MOTOROLA EDGE 50”). "
                f"Use Tender type unless your POS genuinely records financing here.")
    return (f"Financing is normally identified by the Tender type — double-check that "
            f"{MATCH_FIELD_LABELS.get(f, f)} really identifies a financed sale here.")


def normalize_vendor(row):
    """One stored/seeded vendor row → the canonical shape, or None when it has no key. PURE."""
    if not isinstance(row, dict):
        return None
    key = clean_key(row.get("vendor_key"))
    if not key:
        return None
    ds = str(row.get("detection_source") or "rules").strip().lower()
    if ds not in DETECTION_SOURCES:
        ds = "rules"
    ab = str(row.get("amount_basis") or "unit_line").strip().lower()
    if ab not in AMOUNT_BASES:
        ab = "unit_line"
    ref = row.get("detection_ref")
    if not isinstance(ref, dict):
        ref = None
    try:
        sort_order = int(row.get("sort_order") if row.get("sort_order") is not None else 100)
    except (TypeError, ValueError):
        sort_order = 100
    return {"id": row.get("id"), "vendor_key": key,
            "label": str(row.get("label") or key).strip() or key,
            "enabled": bool(row.get("enabled", True)),
            "detection_source": ds, "detection_ref": ref, "amount_basis": ab,
            "sort_order": sort_order, "notes": row.get("notes") or None,
            "source": row.get("source") or "tenant"}


def matcher_hits(row, m):
    """True if ONE matcher matches ONE sale line. PURE.

    `word` (the default) is WORD-ANCHORED — the token, never a substring. This is the guard against the
    model-name collision class."""
    have = str(row.get(m["match_field"], "") or "").strip()
    if not have:
        return False
    want, op = m["match_value"], m["match_op"]
    hl, wl = have.lower(), want.lower()
    if op == "word":
        return _word_hit(have, want)
    if op == "equals":
        return hl == wl
    if op == "contains":
        return wl in hl
    if op == "in":
        return hl in {p.strip().lower() for p in want.split(",") if p.strip()}
    if op == "prefix":
        return hl.startswith(wl)
    if op == "suffix":
        return hl.endswith(wl)
    return False


def classify_line(row, resolved_vendors):
    """(vendor_key, matcher) for ONE sale line, or (None, None). PURE.

    Vendors are consulted in `sort_order` then key order and the FIRST hit wins, so a tenant controls
    precedence explicitly instead of discovering it. A vendor with no usable matcher can never claim a
    line — which is why an unconfigured vendor reports zero rather than everything."""
    for v in resolved_vendors:
        if not v.get("enabled"):
            continue
        for m in v.get("matchers") or []:
            if not m.get("enabled", True):
                continue
            if matcher_hits(row, m):
                return v["vendor_key"], m
    return None, None


# ══ loaders (all org-scoped; all degrade; none raise) ════════════════════════════════════════════
def _safe(fn, default):
    try:
        return fn()
    except Exception:
        return default


def table_ready(client):
    """True when migration 272 is applied (drives the pages' run-migration hint)."""
    try:
        client.schema("commcalc").table(VENDOR_TABLE).select("org_id").limit(1).execute()
        return True
    except Exception:
        return False


def load_vendors(client, org_id):
    """(vendors, ready). The tenant's vendor rows layered over the code seeds: a stored row with the same
    vendor_key REPLACES its seed (so a tenant can rename Edge, disable it, or repoint its detection and
    the seed never comes back). Carrier assignments are attached to each vendor."""
    stored = []
    ready = True
    try:
        stored = (client.schema("commcalc").table(VENDOR_TABLE).select("*")
                  .eq("org_id", org_id).limit(500).execute().data) or []
    except Exception:
        stored, ready = [], False
    carriers = _safe(lambda: (client.schema("commcalc").table(CARRIER_TABLE).select("*")
                              .eq("org_id", org_id).limit(2000).execute().data) or [], [])
    by_vendor_carriers = {}
    for c in carriers:
        k = clean_key(c.get("vendor_key"))
        if not k:
            continue
        by_vendor_carriers.setdefault(k, []).append(
            {"id": c.get("id"), "carrier_id": c.get("carrier_id"),
             "carrier_name": (c.get("carrier_name") or "").strip() or None,
             "enabled": bool(c.get("enabled", True)), "source": "tenant"})

    out, seen = [], set()
    for r in stored:
        n = normalize_vendor(dict(r, source="tenant"))
        if not n:
            continue
        n["carriers"] = by_vendor_carriers.get(n["vendor_key"], [])
        seen.add(n["vendor_key"])
        out.append(n)
    for s in VENDOR_SEEDS:
        if s["vendor_key"] in seen:
            continue
        n = normalize_vendor(dict(s))
        n["carriers"] = by_vendor_carriers.get(n["vendor_key"]) or [
            dict(c, source="seed", carrier_id=None, id=None, enabled=True) for c in (s.get("carriers") or [])]
        out.append(n)
    out.sort(key=lambda v: (v["sort_order"], v["vendor_key"]))
    return out, ready


def load_detection_rules(client, org_id):
    """{vendor_key: [matcher, …]} from the tenant's own detection rows, priority ordered."""
    rows = _safe(lambda: (client.schema("commcalc").table(RULE_TABLE).select("*")
                          .eq("org_id", org_id).limit(5000).execute().data) or [], [])
    by_vendor = {}
    for r in rows:
        m = normalize_matcher(dict(r, source="tenant"))
        if not m:
            continue
        by_vendor.setdefault(clean_key(r.get("vendor_key")), []).append(m)
    for k in by_vendor:
        by_vendor[k].sort(key=lambda m: (m["priority"], str(m.get("id") or "")))
    return by_vendor


def load_plan_rule_matchers(client, org_id):
    """{rule_id: matcher} for every commission_rule whose matcher a financing vendor could inherit.

    Only rules whose match_field is in the report's field vocabulary are offered — a rule keyed on `sku`
    cannot be used, because the report's union read does not carry that column. Each entry keeps the
    rule's plan + label so the picker can show a human "Luxelink plan · edge · tender type is 'TW
    FINANCING'" instead of a UUID (RULE THREE)."""
    rules = _safe(lambda: (client.schema("commcalc").table("commission_rule")
                           .select("id,plan_id,label,match_field,match_op,match_value,payout_kind,amount,"
                                   "financing_vendor_key")
                           .eq("org_id", org_id).limit(5000).execute().data) or [], [])
    if not rules:
        rules = _safe(lambda: (client.schema("commcalc").table("commission_rule")
                               .select("id,plan_id,label,match_field,match_op,match_value,payout_kind,amount")
                               .eq("org_id", org_id).limit(5000).execute().data) or [], [])
    plans = _safe(lambda: (client.schema("commcalc").table("commission_plan")
                           .select("id,name").eq("org_id", org_id).limit(500).execute().data) or [], [])
    plan_name = {str(p.get("id")): p.get("name") for p in plans}
    out = {}
    for r in rules:
        m = normalize_matcher(dict(r, source="plan_rule", priority=100, enabled=True))
        entry = {"rule_id": str(r.get("id")), "plan_id": str(r.get("plan_id") or ""),
                 "plan_name": plan_name.get(str(r.get("plan_id") or "")) or "",
                 "label": r.get("label") or "", "payout_kind": r.get("payout_kind"),
                 "amount": r.get("amount"),
                 "match_field": r.get("match_field"), "match_op": r.get("match_op"),
                 "match_value": r.get("match_value"),
                 "financing_vendor_key": r.get("financing_vendor_key"),
                 "usable": m is not None, "matcher": m,
                 "unusable_reason": None if m else (
                     f"this rule matches on '{r.get('match_field')}' "
                     f"{r.get('match_op')} — the Financing report can only read "
                     f"{', '.join(MATCH_FIELDS)}")}
        out[entry["rule_id"]] = entry
    return out


def load_acima_tenders(client, org_id):
    """(values, configured). The tenant's legacy ACIMA tender mapping (commission_config.acima_tenders,
    migration 094) — the SAME list calculator.py counts the Boost ACIMA spiff from.

    `configured` is False when the tenant has never saved one. In that case calculator.py falls back to
    the substring 'acima', and this function reports that fallback WITH configured=False so the UI can
    say "inherited default — nothing mapped yet" rather than implying somebody chose it. (On a real
    78-column April 2026 house export the Tender Type values were Cash / Externel Credit Card / Zelle /
    Gift Card / Cash App / Credit Card / Debit Card / **Financing** / N/A — no literal 'ACIMA' string at
    all, so this distinction is not academic.)"""
    row = None
    try:
        rows = (client.schema("commcalc").table("commission_config").select("acima_tenders")
                .eq("org_id", org_id).limit(1).execute().data) or []
        row = rows[0] if rows else None
    except Exception:
        row = None
    vals = [str(t).strip() for t in ((row or {}).get("acima_tenders") or []) if str(t).strip()]
    if vals:
        return vals, True
    return ["acima"], False


def resolve_vendors(client, org_id, vendors=None):
    """The vendors with their detection RESOLVED into concrete matchers + an honest status.

    Each vendor gains:
      matchers            [{match_field, match_op, match_value, …}]  — possibly []
      detection_status    'configured' | 'not_configured' | 'inherited_default' | 'unusable'
      detection_note      one plain-English line the page renders as-is
    A vendor with no matchers claims NO lines — the report then shows "detection not configured" instead
    of a zero that looks like a business fact."""
    if vendors is None:
        vendors, _ready = load_vendors(client, org_id)
    own = load_detection_rules(client, org_id)
    plan_rules = None
    acima = None
    out = []
    for v in vendors:
        v = dict(v)
        src = v["detection_source"]
        matchers, status, note = [], "not_configured", ""
        if src == "rules":
            matchers = [m for m in own.get(v["vendor_key"], []) if m.get("enabled", True)]
            if matchers:
                status, note = "configured", f"{len(matchers)} detection rule(s) mapped for this vendor."
            else:
                note = ("Detection not configured — add a rule that says how a "
                        f"{v['label']} sale is recognised (usually the Tender type).")
        elif src == "plan_rule":
            if plan_rules is None:
                plan_rules = load_plan_rule_matchers(client, org_id)
            ids = [str(x) for x in ((v.get("detection_ref") or {}).get("rule_ids") or [])]
            picked, bad = [], []
            for rid in ids:
                e = plan_rules.get(rid)
                if e and e.get("matcher"):
                    m = dict(e["matcher"])
                    m["source"] = "plan_rule"
                    m["from_rule"] = rid
                    m["from_rule_label"] = (f"{e['plan_name']} · {e['label']}".strip(" ·")) or rid
                    picked.append(m)
                elif e:
                    bad.append(e)
            matchers = picked
            if picked:
                status = "configured"
                note = ("Detection inherited from the pay rule(s) " +
                        ", ".join(f"“{m['from_rule_label']}”" for m in picked) +
                        " — the report and the payout therefore always agree on what a financed sale is.")
            elif bad:
                status = "unusable"
                note = bad[0]["unusable_reason"]
            else:
                note = ("Detection not configured — pick the commission-plan rule that already pays this "
                        "vendor's financed sales and its matcher will be reused here.")
        elif src == "acima_config":
            if acima is None:
                acima = load_acima_tenders(client, org_id)
            vals, configured = acima
            matchers = [normalize_matcher({"match_field": "tender_type", "match_op": "contains",
                                           "match_value": x, "priority": 100, "enabled": True,
                                           "source": "acima_config"}) for x in vals]
            matchers = [m for m in matchers if m]
            if configured:
                status = "configured"
                note = ("Detection inherited from this tenant's ACIMA tender mapping (" +
                        ", ".join(f"“{x}”" for x in vals) + ") — the same mapping the ACIMA spiff is "
                        "counted from.")
            else:
                status = "inherited_default"
                note = ("Nothing mapped yet — falling back to the built-in ACIMA tender match "
                        "(“acima”), which is also what the ACIMA spiff falls back to. If your POS spells "
                        "the lease tender differently, map it below: the tender values actually present "
                        "in this period are listed for you to pick from.")
        v["matchers"] = matchers
        v["detection_status"] = status
        v["detection_note"] = note
        out.append(v)
    return out
