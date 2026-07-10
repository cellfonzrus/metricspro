"""Tenant-configurable closing tenders + smart value→tender mapping (migration 111).

Doctrine: an EMPTY config falls back to the hardcoded CANON_TENDERS / _canon_tender that live in
router.py, so a tenant that hasn't opted in behaves byte-for-byte identically. Used by the 3-way
recon, the sales / X-report bucketing, the closing form, and the smart-detect wizard.
"""


def load_tender_config(client, org_id):
    """(defs, map_rows) for a tenant. defs = active tender field definitions (empty → use hardcoded);
    map_rows = the raw-label→tender rules."""
    try:
        defs = (client.schema("commcalc").table("closing_tender_def").select("*")
                .eq("org_id", org_id).eq("is_active", True).order("sort_order").execute().data) or []
    except Exception:
        defs = []   # table not migrated yet → hardcoded fallback
    try:
        maps = (client.schema("commcalc").table("closing_tender_map").select("*")
                .eq("org_id", org_id).execute().data) or []
    except Exception:
        maps = []
    return defs, maps


def tender_axis(defs, canon_tenders, canon_labels):
    """The recon axis: the tenant's tender keys + labels when defined, else the hardcoded 7.
    Returns (keys, labels, recon_class_by_key, include_in_total_by_key)."""
    if defs:
        keys = [d.get("tender_key") for d in defs if d.get("tender_key")]
        labels = {d.get("tender_key"): (d.get("label") or d.get("tender_key")) for d in defs}
        rclass = {d.get("tender_key"): (d.get("recon_class") or "other") for d in defs}
        intotal = {d.get("tender_key"): bool(d.get("include_in_total", True)) for d in defs}
        return keys, labels, rclass, intotal
    keys = list(canon_tenders)
    labels = dict(canon_labels)
    # hardcoded recon classes for the built-in 7 (cash gate = cash; card gate = credit + ext_cc)
    rclass = {"cash": "cash", "credit": "card", "ext_cc": "card", "gift": "other",
              "store_acct": "other", "zelle": "other", "acima": "other"}
    return keys, labels, rclass, {k: True for k in keys}


def make_resolver(map_rows, report, hardcoded, axis_keys):
    """f(raw)->tender_key for a given report leg ('x_report'|'sales'). Uses the tenant map (rules for
    this report + 'both'), tested by ascending priority (specific before generic); when no rule matches
    it falls back to `hardcoded` (the built-in _canon_tender). A fallback key not in the tenant's axis
    is dropped (None) so a custom-only tenant doesn't get phantom standard buckets."""
    axis = set(axis_keys or [])
    rules = []
    for r in map_rows:
        rep = (r.get("report") or "both")
        if rep not in (report, "both"):
            continue
        labels = [str(x).strip().lower() for x in (r.get("source_labels") or []) if str(x).strip()]
        if not labels:
            continue
        rules.append((r.get("priority") if r.get("priority") is not None else 100,
                      r.get("tender_key"), (r.get("match_mode") or "substring"), labels))
    rules.sort(key=lambda x: x[0])

    def resolve(raw):
        t = (raw or "").strip().lower()
        if not t:
            return None
        for _pri, key, mode, labels in rules:
            if mode == "exact":
                if t in labels:
                    return key
            elif any(lab in t for lab in labels):
                return key
        fb = hardcoded(raw)                     # built-in substring rules
        if fb is None:
            return None
        return fb if fb in axis else None       # only keep a fallback that exists on this tenant's axis

    return resolve


def suggest_for_labels(raw_labels, keys, labels, hardcoded):
    """Smart suggestion per distinct raw POS label → the best tender on the tenant's axis + confidence
    ('exact' = a built-in rule matched an axis tender, 'fuzzy' = label/key token overlap, '' = none).
    Mirrors column_mapping.suggest's confidence tiers so the wizard can colour the dropdowns."""
    axis = set(keys or [])
    tokens = {k: (str(labels.get(k) or k) + " " + str(k)).lower().replace("_", " ") for k in keys}
    out = []
    for raw in raw_labels:
        r = str(raw or "").strip()
        if not r:
            continue
        suggested, conf = "", ""
        fb = hardcoded(r)
        if fb and fb in axis:
            suggested, conf = fb, "exact"
        else:
            low = r.lower()
            for k in keys:
                toks = [w for w in tokens[k].split() if len(w) > 2]
                if any(w in low or low in w for w in toks):
                    suggested, conf = k, "fuzzy"
                    break
        out.append({"raw_label": r, "suggested_tender": suggested, "confidence": conf})
    return out


# The 7 built-in tenders as seedable definitions (recon_class drives the cash/credit gate + 2-way recon).
STANDARD_DEFS = [
    ("cash", "Cash", "cash", True), ("credit", "Credit", "card", True),
    ("ext_cc", "External Credit Card", "card", True), ("gift", "Gift Card", "other", True),
    ("store_acct", "Store Account", "other", True), ("zelle", "Zelle / CashApp", "other", True),
    ("acima", "ACIMA (lease)", "other", True),
]
