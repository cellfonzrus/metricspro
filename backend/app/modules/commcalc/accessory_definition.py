"""WHAT COUNTS AS AN ACCESSORY — a per-tenant DEFINITION (migration 257).

OWNER DIRECTIVE 2026-08-01 (verbatim): "accessory option will be as per mapped manually and anything
which says accesspories or category accesory since every company defines in a different way ,
generally all screen protectors, cases headset , earphones, charger, cables , adapters fall under th
ecategory of accessories".

Read literally, that is TWO mechanisms and one observation:
  ① MANUAL MAPPING     — "as per mapped manually": the tenant maps its OWN items/departments/categories.
  ② THE FIELD RULE     — "anything which says accessories or category accessory": when the line's
                          DEPARTMENT or CATEGORY field itself says accessory, it is one.
  ③ "every company defines it differently" — so this is per-tenant CONFIG, never code (RULE TWO). The
     seven classes the owner listed are seeded as PROPOSALS for the owner to confirm, not as law.

──────────────────────────────────────────────────────────────────────────────────────────────────
THIS MODULE PAYS NOBODY. It is a DEFINITION plus a read-only comparison. It is not read by
calculator.py, commission_engine.py, sale_installment_engine.py, _run_calculation, rep_commissions,
targets_engine.py or the P&L. Wiring the PAY basis onto it is a separate, owner-gated change; the
exact diff is filed in docs/handoffs/commission.md.
──────────────────────────────────────────────────────────────────────────────────────────────────

WHY A NEW DEFINITION AT ALL, GIVEN THERE ARE ALREADY FIVE CLASSIFIERS
Accessory classification in this codebase is deliberately divergent across ~8 surfaces (memory
[[accessory-flow-divergences]]): `router._is_accessory` (department/category/keyword sets),
`accessory_catalog.AccessoryClassifier` (that PLUS the product catalog — the one the money path reads
through the synthetic `accessory` match_field), `sale_installment_engine.classify_line`,
`sales_analyzer._is_accessory_line` (`'ondigo'` or `'accessor'` in dept/category) and the
`gp_category_map` department→'accessory' map. Unifying them silently would move P&L, GP and analyzer
numbers. So this module does NOT replace any of them. It adds ONE explicit, owner-owned definition and
a report that shows, line by line, where the existing surfaces agree with it and where they do not.
The owner then decides which surface (if any) adopts it.

MATCHING — EXACT ON A WHOLE FIELD VALUE, CASE-INSENSITIVE
`normalize()` is `strip()` + `casefold()`. That is a DELIBERATE divergence from the MA product-name
classification (migration 254, which is case-SENSITIVE), and the reason is structural, not stylistic:
  · There, the mapped key is a free-form product NAME whose suffix carries meaning
    ('… Plan $65' vs '… Plan $65 New Activation Commission'), so any normalization risked collapsing
    two different things.
  · Here, the mapped key is a WHOLE FIELD VALUE picked from a dropdown of values the tenant's own
    `raw_sales` actually contains. Two values that differ only by case ('Accessories' / 'ACCESSORIES')
    are the same department typed twice by the POS — treating them as two rows is the bug, not the
    fix. Every existing accessory classifier in this codebase already lowercases both sides
    (`router._is_accessory`, `classify_line`, `AccessoryClassifier`), so matching this way keeps the
    new definition comparable to them.
The stored `match_value` keeps the tenant's own spelling; only the comparison is folded.

NO PRODUCT-NAME KEYWORD MATCHING. The field rule (②) may only be applied to the DEPARTMENT and
CATEGORY fields — `token_fields` is validated against `TOKEN_FIELDS` and `product_desc`/`sku` are
REFUSED, with the refusal reported. Name-keyword matchers are a known live bug class here: 'TW EDGE
SPF Month 1' is the Total Wireless EDGE financing tender and not a Motorola Edge
([[edge-is-financing-not-device-model]]); by the same token a 'case' keyword hits 'Phone Case' and
'Casement', and 'charger' hits 'Charger Port Repair'. The owner's list of general classes is therefore
seeded as a CLASS VOCABULARY to label mappings with — never as a set of name keywords to match on.

THE FIELD RULE IS NOT ENOUGH ON ITS OWN — PROVEN ON LIVE DATA (luxelink, July 2026).
The owner's own July export changes the spelling of BOTH classifying fields MID-MONTH for the SAME
physical products: 'Case BYOD' / 'Screen Protectors BYOD' ship as
department='BrandedHandset', category='HandsetBranded' from 07-02 to 07-08, then as
department='Handset', category='Accessories' from 07-09 onward. A category-field rule alone therefore
MISSES THE ENTIRE FIRST WEEK. That is why mechanism ① (the manual item map, keyed on the product
description) is load-bearing rather than a nice-to-have, and why this module ships three things the
owner can act on:
  · `propose_from_data()` — INFERENCE FROM THE TENANT'S OWN ROWS: a product description that is an
    accessory on at least one line (because that line's category field said so, or because the owner
    confirmed it) is PROPOSED as a product-description mapping, which then also catches the same
    product on the lines whose category field spells it differently. Nothing is invented: every
    proposal cites the evidence line that produced it.
  · `spelling_drift()` — names the products whose own lines disagree, with the department/category
    spellings and the date range of each, so the hole is visible instead of inferred.
  · per-MECHANISM attribution in `agreement()` — caught by the field rule / by a confirmed map / by a
    proposed map / uncaught — so "the rule covers it" is a measured claim, not an assumption.
Live data also settles one more thing: `sku` is NULL on every accessory line in that export, so a
SKU-keyed mapping would match nothing. SKU stays an available field (other tenants carry it) but the
payload reports its real coverage so the UI can say so rather than offering a dead option.

SET-UP FEES ARE NEVER ACCESSORIES (standing owner rule, 2026-07-17). `classify()` checks the tenant's
configured set-up-fee keywords FIRST and returns `is_accessory=False, matched_by='setup_fee'`. A
set-up fee counts toward the accessory TARGET elsewhere; it is never folded into the accessory basis.

PROPOSED vs CONFIRMED (mirrors the MA product-name classification, mig 254). Every seeded class and
every mapping starts
`status='proposed'`. `classify(..., mode='confirmed')` counts only what the owner confirmed;
`mode='proposed'` counts confirmed + proposed. The DELTA between the two readings is the impact
preview, so the owner sees exactly what confirming would do before confirming it.

PURE + DB-FREE: no client, no network. All DB orchestration lives in router.py.
"""

CLASS_TABLE = "accessory_class"
MAP_TABLE = "accessory_definition_map"

STATUSES = ("proposed", "confirmed")

# The fields a MANUAL mapping may key on, most specific first. Precedence is this order: an item-level
# mapping beats a department-level one, so mapping ONE product out of an accessory department works.
MATCH_FIELDS = ("sku", "product_desc", "category", "department")
MATCH_FIELD_LABELS = {
    "sku": "SKU",
    "product_desc": "Product description",
    "category": "Category",
    "department": "Department",
}

# The ONLY fields the token rule may read. product_desc / sku are refused on purpose (module header).
TOKEN_FIELDS = ("department", "category")

# The owner's own wording: "anything which says accesspories or category accesory". Stored as config;
# these are the defaults, editable per tenant. Matched case-insensitively as a SUBSTRING of the whole
# FIELD value — which is what makes 'Accessories', 'ACCESSORY', 'Acc/Accessories' and 'Ondigo
# Accessories' all land, without ever reading a product name.
DEFAULT_TOKENS = ("accessor",)

DEFAULT_FIELD_RULE = {
    "enabled": True,
    "token_fields": list(TOKEN_FIELDS),
    "tokens": list(DEFAULT_TOKENS),
}

# ── the class vocabulary — the owner's own list, seeded as PROPOSALS ────────────────────────────────
# (class_key, label, description). The owner named seven; `other_accessory` exists so a mapping never
# has to be forced into a class that does not fit. Nothing here is a matcher — these are LABELS.
DEFAULT_CLASSES = [
    ("screen_protector", "Screen protectors", "Tempered glass, film, privacy screens.", 10),
    ("case", "Cases", "Phone/tablet cases, covers, folios, bumpers.", 20),
    ("headset", "Headsets", "Over-ear / on-ear headsets and headphones.", 30),
    ("earphone", "Earphones", "Earbuds, in-ear phones, wireless buds.", 40),
    ("charger", "Chargers", "Wall bricks, car chargers, wireless pads, power banks.", 50),
    ("cable", "Cables", "USB / lightning / HDMI and other cables.", 60),
    ("adapter", "Adapters", "Dongles, converters, jack and port adapters.", 70),
    ("other_accessory", "Other accessory", "An accessory that does not fit the classes above. Exists so "
     "a mapping is never forced into the wrong class.", 900),
]
CLASS_KEYS = tuple(c[0] for c in DEFAULT_CLASSES)
CLASS_LABELS = {c[0]: c[1] for c in DEFAULT_CLASSES}

# The surfaces the agreement report compares. `combined` is the one the MONEY path reads (the
# synthetic `accessory` match_field in commission_engine goes through
# accessory_catalog.AccessoryClassifier.is_accessory_row), so it is the reference column.
SURFACES = ("legacy", "catalog", "combined", "installment", "analyzer", "gp_map",
            "definition_confirmed", "definition_proposed")
SURFACE_LABELS = {
    "legacy": "Accessory settings (department / category / product keywords)",
    "catalog": "Product catalog category (migs 230/231)",
    "combined": "PAY BASIS — settings OR catalog (what commission rules read)",
    "installment": "Installment classifier (sale_installment_engine.classify_line)",
    "analyzer": "Sales Analyzer ('ondigo' or 'accessor' in department/category)",
    "gp_map": "GP category map (department mapped to 'accessory')",
    "definition_confirmed": "THIS definition — confirmed mappings only",
    "definition_proposed": "THIS definition — confirmed + proposed",
}


# How a line came to be (or not be) an accessory. Reported separately because the live July export
# proved that the field rule alone leaves a week-shaped hole (module header).
MECHANISMS = ("map_confirmed", "map_proposed", "field_token", "setup_fee", "none")
MECHANISM_LABELS = {
    "map_confirmed": "Your confirmed mapping",
    "map_proposed": "A proposed mapping (not yet confirmed)",
    "field_token": "The department/category field rule",
    "setup_fee": "Set-up fee — excluded on purpose",
    "none": "Nothing matched it",
}


def mechanism_of(verdict):
    """The MECHANISM key for one classify() verdict. PURE."""
    by = (verdict or {}).get("matched_by")
    if by == "setup_fee":
        return "setup_fee"
    if by == "map":
        return "map_confirmed" if (verdict or {}).get("status") == "confirmed" else "map_proposed"
    if by == "field_token":
        return "field_token"
    return "none"


def normalize(value):
    """The ONLY normalization applied to a mapped field value: trim + casefold. See the module header
    for why this is case-INsensitive while the mig-254 product-name classification is case-sensitive.
    PURE."""
    return str(value if value is not None else "").strip().casefold()


def normalize_class(value):
    v = str(value if value is not None else "").strip().lower()
    return v or None


def normalize_field_rule(stored):
    """A stored field-rule config -> the full dict, with the defaults filling anything absent, and with
    any non-TOKEN_FIELD refused. Returns (rule, refused_fields). PURE; never raises.

    The refusal is REPORTED rather than silently dropped: a tenant that tried to point the token rule
    at product descriptions needs to be told why it did not take, not left wondering."""
    rule = {"enabled": bool(DEFAULT_FIELD_RULE["enabled"]),
            "token_fields": list(DEFAULT_FIELD_RULE["token_fields"]),
            "tokens": list(DEFAULT_FIELD_RULE["tokens"])}
    refused = []
    if not isinstance(stored, dict):
        return rule, refused
    if "enabled" in stored:
        rule["enabled"] = bool(stored.get("enabled"))
    tf = stored.get("token_fields")
    if isinstance(tf, (list, tuple)):
        keep = []
        for f in tf:
            k = str(f or "").strip().lower()
            if not k:
                continue
            if k in TOKEN_FIELDS:
                if k not in keep:
                    keep.append(k)
            elif k not in refused:
                refused.append(k)
        rule["token_fields"] = keep
    tk = stored.get("tokens")
    if isinstance(tk, (list, tuple)):
        toks = []
        for t in tk:
            s = normalize(t)
            if s and s not in toks:
                toks.append(s)
        rule["tokens"] = toks
    return rule, refused


def classes_from(rows):
    """Effective class vocabulary: the tenant's saved rows, else the built-in list (as proposals).
    Each entry is {class_key, label, description, sort_order, status, source}. PURE."""
    out = []
    for r in (rows or []):
        key = normalize_class(r.get("class_key"))
        if not key or r.get("is_active") is False:
            continue
        st = str(r.get("status") or "proposed").strip().lower()
        out.append({"class_key": key, "label": r.get("label") or key,
                    "description": r.get("description") or "",
                    "sort_order": int(r.get("sort_order") or 500),
                    "status": st if st in STATUSES else "proposed",
                    "is_active": True, "source": "config"})
    if not out:
        out = [{"class_key": k, "label": lab, "description": desc, "sort_order": so,
                "status": "proposed", "is_active": True, "source": "default"}
               for (k, lab, desc, so) in DEFAULT_CLASSES]
    seen, dedup = set(), []
    for c in sorted(out, key=lambda x: (x["sort_order"], x["class_key"])):
        if c["class_key"] in seen:
            continue
        seen.add(c["class_key"])
        dedup.append(c)
    return dedup


def class_seed_rows(org_id):
    """The built-in classes as INSERT-ready rows for ONE tenant, status='proposed'. org_id is stamped
    on every row (RULE ONE: config rows carry the tenant). PURE."""
    return [{"org_id": org_id, "class_key": k, "label": lab, "description": desc,
             "sort_order": so, "status": "proposed", "is_active": True}
            for (k, lab, desc, so) in DEFAULT_CLASSES]


def build_index(map_rows):
    """{match_field: {normalized value: row}} from the saved mappings. A later duplicate never
    overwrites a CONFIRMED row with a proposed one. PURE."""
    idx = {f: {} for f in MATCH_FIELDS}
    for r in (map_rows or []):
        f = str(r.get("match_field") or "").strip().lower()
        if f not in idx:
            continue
        v = normalize(r.get("match_value"))
        if not v:
            continue
        prev = idx[f].get(v)
        st = str(r.get("status") or "proposed").strip().lower()
        if prev is not None and str(prev.get("status") or "") == "confirmed" and st != "confirmed":
            continue
        idx[f][v] = r
    return idx


def _row_field(row, field):
    if field == "sku":
        return normalize(row.get("sku"))
    if field == "product_desc":
        return normalize(row.get("product_desc"))
    if field == "category":
        return normalize(row.get("category"))
    if field == "department":
        return normalize(row.get("department"))
    return ""


def is_setup_fee(row, setup_keywords):
    """The tenant's configured set-up-fee keywords, matched on the product description. The ONE place
    this module reads a product name — and it does so to EXCLUDE, never to include, which is the safe
    direction. `setup_keywords` is the caller's resolved set (router._accessory_config's
    `setup_fee_products`). PURE."""
    kws = setup_keywords or set()
    if not kws:
        return False
    p = normalize(row.get("product_desc"))
    return bool(p) and any(normalize(k) in p for k in kws)


def field_token_hit(row, rule):
    """(field, value, token) for the first token match on an allowed FIELD, else (None, None, None).
    Never reads product_desc or sku — token_fields is already validated. PURE."""
    if not rule or not rule.get("enabled"):
        return None, None, None
    for f in (rule.get("token_fields") or ()):
        if f not in TOKEN_FIELDS:          # belt-and-braces; normalize_field_rule already refused it
            continue
        v = _row_field(row, f)
        if not v:
            continue
        for t in (rule.get("tokens") or ()):
            if t and t in v:
                return f, v, t
    return None, None, None


def classify(row, index, rule, setup_keywords=None, mode="confirmed"):
    """The definition, applied to ONE sale line. PURE.

    mode='confirmed' -> only owner-CONFIRMED mappings count (proposed rows read as if absent).
    mode='proposed'  -> confirmed + proposed.

    Returns {is_accessory, accessory_class, matched_by, matched_field, matched_value, status}.
    `matched_by` is one of: 'setup_fee' | 'map' | 'field_token' | None.

    PRECEDENCE, most specific first:
      0. set-up fee            -> NOT an accessory, always, whatever anything else says.
      1. a MANUAL mapping on sku, then product_desc, then category, then department. A mapping may say
         is_accessory=False, which is an explicit EXCLUSION (one product carved out of an accessory
         department) and beats the token rule.
      2. the FIELD TOKEN rule on department / category.
      3. otherwise: not an accessory.
    """
    blank = {"is_accessory": False, "accessory_class": None, "matched_by": None,
             "matched_field": None, "matched_value": None, "status": None}
    if is_setup_fee(row, setup_keywords):
        return {**blank, "matched_by": "setup_fee",
                "matched_field": "product_desc",
                "matched_value": str(row.get("product_desc") or "")[:200]}
    want_confirmed = (mode != "proposed")
    for f in MATCH_FIELDS:
        v = _row_field(row, f)
        if not v:
            continue
        m = (index.get(f) or {}).get(v)
        if not m:
            continue
        st = str(m.get("status") or "proposed").strip().lower()
        if want_confirmed and st != "confirmed":
            continue
        return {"is_accessory": bool(m.get("is_accessory", True)),
                "accessory_class": normalize_class(m.get("accessory_class")),
                "matched_by": "map", "matched_field": f,
                "matched_value": str(m.get("match_value") or "")[:200], "status": st}
    f, v, t = field_token_hit(row, rule)
    if f:
        return {"is_accessory": True, "accessory_class": None, "matched_by": "field_token",
                "matched_field": f, "matched_value": v[:200], "status": "rule",
                "token": t}
    return blank


# ── the cross-surface agreement report (read-only; the scope-control deliverable) ───────────────────
def _blank_bucket():
    return {"lines": 0, "ext_price": 0.0, "gp": 0.0, "trans": set()}


def _add(bucket, ext, gp, tid):
    bucket["lines"] += 1
    bucket["ext_price"] = round(bucket["ext_price"] + ext, 2)
    bucket["gp"] = round(bucket["gp"] + gp, 2)
    if tid:
        bucket["trans"].add(tid)


def agreement(rows, verdicts_of, reference="combined", max_examples=300):
    """Compare every accessory surface line by line. PURE — `verdicts_of(row)` is injected and returns
    {surface_key: bool} for ONE row, so this function does no classification of its own and cannot
    become a ninth classifier.

    Returns per-surface totals, the pairwise agreement against `reference` (the PAY BASIS), and the
    disagreeing ITEMS aggregated (never a raw line dump — the item is what the owner maps).
    """
    from app.modules.commcalc.calculator import safe_float

    totals = {s: _blank_bucket() for s in SURFACES}
    agree = {s: {"same": 0, "only_here": 0, "only_reference": 0,
                 "only_here_ext": 0.0, "only_reference_ext": 0.0} for s in SURFACES}
    items = {}
    n = 0
    # PER-MECHANISM ATTRIBUTION (added 2026-08-01 after the live July export showed the field rule
    # missing a whole week). "The rule covers it" has to be a measured claim.
    mech = {m: {"lines": 0, "ext_price": 0.0, "gp": 0.0} for m in MECHANISMS}
    # A line NOTHING in the definition caught, that some OTHER surface calls an accessory: the
    # actionable gap list.
    gap = {"lines": 0, "ext_price": 0.0, "products": {}}
    neg_price = {"lines": 0, "ext_price": 0.0}
    for r in (rows or []):
        v = verdicts_of(r) or {}
        ext = safe_float(r.get("ext_price"))
        gp = safe_float(r.get("gp"))
        tid = str(r.get("trans_id") or "").strip()
        n += 1
        ref = bool(v.get(reference))
        if ext < 0:
            neg_price["lines"] += 1
            neg_price["ext_price"] = round(neg_price["ext_price"] + ext, 2)
        _det = (v.get("_detail") or {}).get("proposed") or {}
        _m = mechanism_of(_det)
        mech[_m]["lines"] += 1
        mech[_m]["ext_price"] = round(mech[_m]["ext_price"] + ext, 2)
        mech[_m]["gp"] = round(mech[_m]["gp"] + gp, 2)
        if _m == "none" and any(bool(v.get(s2)) for s2 in
                                ("legacy", "catalog", "combined", "installment", "analyzer", "gp_map")):
            gap["lines"] += 1
            gap["ext_price"] = round(gap["ext_price"] + ext, 2)
            gk = str(r.get("product_desc") or "")[:160]
            gp_e = gap["products"].setdefault(gk, {"product_desc": gk, "lines": 0, "ext_price": 0.0,
                                                   "departments": set(), "categories": set()})
            gp_e["lines"] += 1
            gp_e["ext_price"] = round(gp_e["ext_price"] + ext, 2)
            gp_e["departments"].add(str(r.get("department") or ""))
            gp_e["categories"].add(str(r.get("category") or ""))
        for s in SURFACES:
            hit = bool(v.get(s))
            if hit:
                _add(totals[s], ext, gp, tid)
            a = agree[s]
            if hit == ref:
                a["same"] += 1
            elif hit and not ref:
                a["only_here"] += 1
                a["only_here_ext"] = round(a["only_here_ext"] + ext, 2)
            elif ref and not hit:
                a["only_reference"] += 1
                a["only_reference_ext"] = round(a["only_reference_ext"] + ext, 2)
        # ITEM aggregation: only where the surfaces DISAGREE — that is the whole actionable list.
        if len({bool(v.get(s)) for s in SURFACES}) > 1:
            key = (normalize(r.get("product_desc")), normalize(r.get("sku")))
            it = items.setdefault(key, {
                "product_desc": str(r.get("product_desc") or ""), "sku": str(r.get("sku") or ""),
                "department": str(r.get("department") or ""), "category": str(r.get("category") or ""),
                "lines": 0, "ext_price": 0.0, "gp": 0.0,
                "verdicts": {s: bool(v.get(s)) for s in SURFACES},
                "definition_detail": v.get("_detail")})
            it["lines"] += 1
            it["ext_price"] = round(it["ext_price"] + ext, 2)
            it["gp"] = round(it["gp"] + gp, 2)
            for s in SURFACES:
                if bool(v.get(s)) != it["verdicts"][s]:
                    # the same item classified two ways within one period (e.g. two departments)
                    it["verdicts"][s] = True
                    it["mixed"] = True

    out_tot = {}
    for s, b in totals.items():
        out_tot[s] = {"label": SURFACE_LABELS[s], "lines": b["lines"],
                      "ext_price": round(b["ext_price"], 2), "gp": round(b["gp"], 2),
                      "transactions": len(b["trans"])}
    out_items = sorted(items.values(), key=lambda x: -x["ext_price"])[:max_examples]
    gap_products = sorted(({**g, "departments": sorted(g["departments"]),
                            "categories": sorted(g["categories"])}
                           for g in gap["products"].values()),
                          key=lambda x: -x["ext_price"])[:max_examples]
    return {"rows_read": n, "reference": reference, "reference_label": SURFACE_LABELS[reference],
            "surfaces": [{"key": s, "label": SURFACE_LABELS[s]} for s in SURFACES],
            "totals": out_tot, "agreement": agree,
            "disagreeing_items": out_items, "disagreeing_item_count": len(items),
            "by_mechanism": [{"key": m, "label": MECHANISM_LABELS[m], **mech[m]} for m in MECHANISMS],
            "uncaught_gap": {"lines": gap["lines"], "ext_price": round(gap["ext_price"], 2),
                             "products": gap_products, "product_count": len(gap["products"]),
                             "note": ("Lines that NO part of your definition caught, but some existing "
                                      "classifier does call an accessory. Each one is a hole — usually a "
                                      "product whose department/category is spelled differently on those "
                                      "rows. Map the product description and the hole closes.")},
            "negative_price_lines": neg_price}


def spelling_drift(rows, rule, cap=200):
    """Products whose OWN lines disagree about whether the field rule fires — the live signature of a
    POS that renamed its department/category mid-month for the same physical product. PURE.

    Returns one entry per product description, listing each (department, category) spelling it was
    sold under, that spelling's line count / dollars / date range, and whether the field rule catches
    it. An entry with both a caught and an uncaught spelling is a hole the field rule cannot close.
    """
    from app.modules.commcalc.calculator import safe_float

    per = {}
    for r in (rows or []):
        pd = normalize(r.get("product_desc"))
        if not pd:
            continue
        dep, cat = str(r.get("department") or ""), str(r.get("category") or "")
        f, _v, tok = field_token_hit(r, rule)
        e = per.setdefault(pd, {"product_desc": str(r.get("product_desc") or "")[:200],
                                "spellings": {}})
        k = (dep, cat)
        sp = e["spellings"].setdefault(k, {"department": dep, "category": cat, "lines": 0,
                                           "ext_price": 0.0, "caught": bool(f),
                                           "matched_field": f, "token": tok,
                                           "first_date": None, "last_date": None})
        sp["lines"] += 1
        sp["ext_price"] = round(sp["ext_price"] + safe_float(r.get("ext_price")), 2)
        d = str(r.get("trans_date") or "")[:10]
        if d:
            sp["first_date"] = d if sp["first_date"] is None else min(sp["first_date"], d)
            sp["last_date"] = d if sp["last_date"] is None else max(sp["last_date"], d)
    out = []
    for e in per.values():
        sps = list(e["spellings"].values())
        if len({s["caught"] for s in sps}) < 2:
            continue                       # every spelling agrees — not a drift
        sps.sort(key=lambda x: (x["first_date"] or "", -x["ext_price"]))
        out.append({"product_desc": e["product_desc"], "spellings": sps,
                    "uncaught_lines": sum(s["lines"] for s in sps if not s["caught"]),
                    "uncaught_ext": round(sum(s["ext_price"] for s in sps if not s["caught"]), 2)})
    out.sort(key=lambda x: -x["uncaught_ext"])
    return out[:cap]


def propose_from_data(rows, index, rule, setup_keywords=None, cap=500):
    """PROPOSED product-description mappings INFERRED FROM THE TENANT'S OWN ROWS. PURE.

    The rule is deliberately narrow and evidence-bound: a product description is proposed as an
    accessory ONLY IF at least one of ITS OWN lines is already an accessory under the definition — i.e.
    that line's department/category field said so, or the owner already confirmed a mapping that
    covers it. Nothing is inferred from the product's NAME, and nothing is proposed for a product no
    evidence ever touched.

    Why this exists: the live July export spells the same product's category two different ways in one
    month, so the field rule catches it from the 9th but not from the 2nd. Mapping the DESCRIPTION —
    which does not change — closes the gap, and the evidence for each proposal is the line that
    already qualified. Set-up fees are excluded first and are never proposed.

    Returns [{match_field:'product_desc', match_value, lines, ext_price, evidence:{...},
              covered_lines, uncovered_lines}] — the caller stamps org_id and writes them as
    status='proposed'. Anything already mapped is skipped.
    """
    from app.modules.commcalc.calculator import safe_float

    per = {}
    for r in (rows or []):
        raw = str(r.get("product_desc") or "").strip()
        pd = normalize(raw)
        if not pd:
            continue
        if is_setup_fee(r, setup_keywords):
            continue                                   # never an accessory, never proposed
        e = per.setdefault(pd, {"match_value": raw, "lines": 0, "ext_price": 0.0,
                                "covered_lines": 0, "uncovered_lines": 0, "evidence": None})
        e["lines"] += 1
        e["ext_price"] = round(e["ext_price"] + safe_float(r.get("ext_price")), 2)
        v = classify(r, index, rule, setup_keywords, mode="proposed")
        if v.get("is_accessory"):
            e["covered_lines"] += 1
            if e["evidence"] is None:
                e["evidence"] = {"matched_by": v.get("matched_by"),
                                 "matched_field": v.get("matched_field"),
                                 "matched_value": v.get("matched_value"),
                                 "department": str(r.get("department") or ""),
                                 "category": str(r.get("category") or ""),
                                 "trans_id": str(r.get("trans_id") or ""),
                                 "date": str(r.get("trans_date") or "")[:10]}
        else:
            e["uncovered_lines"] += 1
    out = []
    have = set((index.get("product_desc") or {}).keys())
    for pd, e in per.items():
        if pd in have:
            continue                                   # already mapped — the owner's row wins
        if e["covered_lines"] <= 0:
            continue                                   # no evidence -> no proposal, ever
        out.append({"match_field": "product_desc", "match_value": e["match_value"],
                    "lines": e["lines"], "ext_price": e["ext_price"],
                    "covered_lines": e["covered_lines"], "uncovered_lines": e["uncovered_lines"],
                    "evidence": e["evidence"]})
    # the ones that close the biggest hole first
    out.sort(key=lambda x: (-x["uncovered_lines"], -x["ext_price"]))
    return out[:cap]


def sku_coverage(rows, is_accessory=None):
    """How many lines carry a SKU — overall AND among the lines that are accessories.

    The second number is the decision-relevant one, and the distinction is a LIVE finding: in the
    luxelink July export the ACTIVATION lines carry SKUs while every ACCESSORY line's SKU is NULL. A
    single overall percentage would have said "SKU is fine" and sent the owner to map on a field that
    reaches none of the products they care about. `is_accessory(row)` is injected; omit it and only the
    overall numbers are reported. PURE."""
    n = have = an = ahave = 0
    for r in (rows or []):
        n += 1
        has = bool(normalize(r.get("sku")))
        if has:
            have += 1
        if is_accessory is not None:
            try:
                acc = bool(is_accessory(r))
            except Exception:
                acc = False
            if acc:
                an += 1
                if has:
                    ahave += 1
    usable = (ahave > 0) if (is_accessory is not None and an) else (have > 0)
    note = None
    if is_accessory is not None and an and not ahave:
        note = ("None of this tenant's ACCESSORY lines carry a SKU (the activation lines do), so a "
                "SKU mapping would reach none of them. Map on the product description instead.")
    elif not have:
        note = ("None of this tenant's sale lines carry a SKU, so a SKU mapping would never match. "
                "Map on the product description instead.")
    return {"lines": n, "with_sku": have, "pct": (round(100.0 * have / n, 1) if n else 0.0),
            "accessory_lines": an, "accessory_with_sku": ahave,
            "accessory_pct": (round(100.0 * ahave / an, 1) if an else 0.0),
            "usable": usable, "note": note}


def observed_values(rows, index, rule, setup_keywords=None, cap=4000):
    """The tenant's REAL distinct values per mappable field, with line counts and dollars, plus each
    value's current mapping — the pick-don't-type option list AND the editable grid in one payload.
    PURE (no DB); `rows` are already org-scoped by the caller."""
    from app.modules.commcalc.calculator import safe_float

    out = {f: {} for f in MATCH_FIELDS}
    for r in (rows or []):
        ext = safe_float(r.get("ext_price"))
        gp = safe_float(r.get("gp"))
        for f in MATCH_FIELDS:
            raw = str(r.get(f) or "").strip()
            key = normalize(raw)
            if not key:
                continue
            e = out[f].setdefault(key, {"match_field": f, "match_value": raw, "lines": 0,
                                        "ext_price": 0.0, "gp": 0.0, "spellings": set()})
            e["lines"] += 1
            e["ext_price"] = round(e["ext_price"] + ext, 2)
            e["gp"] = round(e["gp"] + gp, 2)
            e["spellings"].add(raw)
    res = {}
    for f in MATCH_FIELDS:
        vals = []
        for key, e in out[f].items():
            m = (index.get(f) or {}).get(key)
            token_hit = None
            if f in TOKEN_FIELDS and rule and rule.get("enabled"):
                for t in (rule.get("tokens") or ()):
                    if t and t in key:
                        token_hit = t
                        break
            vals.append({
                "match_field": f, "match_value": e["match_value"],
                "spellings": sorted(e["spellings"]),
                "lines": e["lines"], "ext_price": round(e["ext_price"], 2), "gp": round(e["gp"], 2),
                "mapped": bool(m),
                "id": (m or {}).get("id"),
                "is_accessory": (None if not m else bool(m.get("is_accessory", True))),
                "accessory_class": normalize_class((m or {}).get("accessory_class")),
                "status": (None if not m else str(m.get("status") or "proposed").strip().lower()),
                "note": (m or {}).get("note"),
                "token_hit": token_hit,
            })
        vals.sort(key=lambda x: -x["ext_price"])
        res[f] = vals[:cap]
    return res
