"""Catalog-driven accessory classification (migs 230/231) — an ADDITIVE, config-gated layer.

OWNER DIRECTIVE 2026-07-24 (luxelink): "anything labeled as accessory category should be classified and
used towards accessory sales and eventually commission paid out for the accessories." A tenant uploads a
product catalog (b2bsoft "Product Update" — the house Product-ID variant OR the TOTAL/luxelink UPC variant)
whose rows carry a Department + Category. Lines whose product (matched by UPC → SKU → product_id →
normalized Product Desc) carries an ACCESSORY category (the default = the catalog's own 'Accessories'; the
SET of accessory categories is per-org config) are classified as accessory sales.

WHY A SEPARATE MODULE (no circular import): both the DISPLAY path (`router._is_accessory` / `_sales_cell_agg`)
and the MONEY path (`commission_engine.preview`, a rule keyed on the synthetic `accessory` match_field) need
the SAME classifier. Putting it here lets both import it without router<->engine cycles.

ADDITIVE + BOOST-SAFE: the classifier is only consulted when a tenant turns
`accessory_config.catalog_classify_enabled` ON (default FALSE) — the house/Boost org keeps its exact
department/category/keyword classification (byte-identical). When enabled, the catalog match can only ADD
accessory lines the legacy classifier missed; it never REMOVES one (see `AccessoryClassifier.is_accessory_row`).
Set-up-fee lines stay separate (the caller checks `_is_setup_fee` FIRST). Everything degrades gracefully:
a missing table/column → empty sets → no-op.
"""
import re

from app.modules.commcalc.calculator import safe_float


def norm_desc(s):
    """Normalize a product description for matching: trim + collapse whitespace + lowercase."""
    return re.sub(r"\s+", " ", str(s or "").strip().lower())


def clean_key(v):
    """Normalize a UPC / SKU key: trim, strip only a TRAILING '.0' (Excel numeric-cell artifact — NOT every
    '.0', so 'V2.0-CASE' is preserved), lowercase. Symmetric with the catalog ingest (router _dot0) so both
    join sides agree."""
    s = str(v or "").strip()
    if s.endswith(".0"):
        s = s[:-2]
    return s.strip().lower()


def pid_key(v):
    """Normalize a numeric product_id to a stable string key ('' when absent/zero)."""
    f = safe_float(v)
    if not f:
        return ""
    try:
        return str(int(f)) if float(f).is_integer() else str(f)
    except Exception:
        return str(v).strip()


# ── config resolution ──────────────────────────────────────────────────────────────────────────────
def accessory_category_set(client, org_id):
    """The set (lowercased) of catalog Category values that count as ACCESSORY for catalog classification,
    plus whether catalog classification is enabled. From commcalc.accessory_config (mig 231):
      catalog_classify_enabled  (bool, default false)
      catalog_accessory_categories (text[], default {} → the catalog's own 'Accessories' when enabled)
    Never raises → ({}, False) if the columns/table are absent (pre-231)."""
    enabled = False
    cats = []
    try:
        rows = (client.schema("commcalc").table("accessory_config")
                .select("catalog_classify_enabled,catalog_accessory_categories")
                .eq("org_id", org_id).limit(1).execute().data) or []
        if rows:
            enabled = bool(rows[0].get("catalog_classify_enabled"))
            cats = [str(c).strip().lower() for c in (rows[0].get("catalog_accessory_categories") or []) if str(c).strip()]
    except Exception:
        return set(), False
    if enabled and not cats:
        # Sensible default: the b2bsoft catalog's own 'Accessories' department/category label.
        cats = ["accessories"]
    return set(cats), enabled


def _load_overrides(client, org_id):
    """Per-org category OVERRIDES (mig 230 commcalc.catalog_category_override) — the user-editable layer on
    top of the loaded catalog file (deliverable 3, non-destructive). Returns dicts keyed by match_type:
      {'upc': {key: cat}, 'sku': {...}, 'product_id': {...}, 'product_desc': {normdesc: cat}}
    Category values lowercased. Never raises → all-empty if the table is absent (pre-230)."""
    out = {"upc": {}, "sku": {}, "product_id": {}, "product_desc": {}}
    try:
        rows = (client.schema("commcalc").table("catalog_category_override")
                .select("match_type,match_value,category").eq("org_id", org_id)
                .limit(100000).execute().data) or []
    except Exception:
        return out
    for r in rows:
        mt = str(r.get("match_type") or "").strip().lower()
        if mt not in out:
            continue
        mv = str(r.get("match_value") or "").strip().lower()
        cat = str(r.get("category") or "").strip().lower()
        if mv and cat:
            out[mt][mv] = cat
    return out


def _load_catalog_rows(client, org_id):
    """All catalog rows for the org (select * so a missing TOTAL-variant column never errors). Uses the
    same high .limit as raw_catalog's other reads (the org may hold a few thousand rows). Never raises."""
    try:
        return (client.schema("commcalc").table("raw_catalog").select("*")
                .eq("org_id", org_id).limit(100000).execute().data) or []
    except Exception:
        return []


def effective_category(row, overrides):
    """The effective category for a catalog row: an OVERRIDE (precedence upc → sku → product_id →
    normalized product_desc) wins over the file's own Category. Lowercased. '' when unknown."""
    upc = clean_key(row.get("upc"))
    if upc and upc in overrides["upc"]:
        return overrides["upc"][upc]
    sku = clean_key(row.get("sku"))
    if sku and sku in overrides["sku"]:
        return overrides["sku"][sku]
    pid = pid_key(row.get("product_id"))
    if pid and pid in overrides["product_id"]:
        return overrides["product_id"][pid]
    d = norm_desc(row.get("product_desc"))
    if d and d in overrides["product_desc"]:
        return overrides["product_desc"][d]
    return str(row.get("category") or "").strip().lower()


class AccessoryClassifier:
    """Combined accessory classifier: the legacy department/category/keyword lists (byte-identical to
    router._is_accessory) PLUS the catalog layer. `is_accessory_row` returns True if EITHER says accessory
    (purely additive — never removes a legacy accessory)."""

    def __init__(self, depts, cats, kws, acc_desc, acc_sku, acc_upc, acc_pid):
        self.depts = set(depts or ())
        self.cats = set(cats or ())
        self.kws = list(kws or ())
        self.acc_desc = set(acc_desc or ())
        self.acc_sku = set(acc_sku or ())
        self.acc_upc = set(acc_upc or ())
        self.acc_pid = set(acc_pid or ())
        self.has_catalog = bool(self.acc_desc or self.acc_sku or self.acc_upc or self.acc_pid)

    # Catalog-only check keyed on normalized product_desc — the ONE field the display aggregation
    # (`_sales_cell_agg` → `_is_accessory(dept, category, product, acfg)`) actually carries.
    def is_catalog_accessory_desc(self, product_desc):
        d = norm_desc(product_desc)
        return bool(d) and d in self.acc_desc

    # Full-row catalog check (used where the row carries sku/product_id/upc too — the commission engine
    # + the accessory report). Precedence UPC → SKU → product_id → normalized Product Desc.
    def is_catalog_accessory_row(self, row):
        upc = clean_key(row.get("upc"))
        if upc and upc in self.acc_upc:
            return True
        sku = clean_key(row.get("sku"))
        if sku and sku in self.acc_sku:
            return True
        pid = pid_key(row.get("product_id"))
        if pid and pid in self.acc_pid:
            return True
        return self.is_catalog_accessory_desc(row.get("product_desc"))

    # legacy department/category/keyword classification (mirrors router._is_accessory exactly)
    def is_legacy_accessory_row(self, row):
        d = str(row.get("department") or "").strip().lower()
        if d and d in self.depts:
            return True
        c = str(row.get("category") or "").strip().lower()
        if c and c in self.cats:
            return True
        if self.kws:
            p = str(row.get("product_desc") or "").strip().lower()
            if p and any(k in p for k in self.kws):
                return True
        return False

    def is_accessory_row(self, row):
        """ADDITIVE: legacy OR catalog. Never removes a legacy accessory."""
        return self.is_legacy_accessory_row(row) or self.is_catalog_accessory_row(row)


def build_catalog_sets(client, org_id, acc_cats=None):
    """Build the accessory-key SETS from the org's catalog + overrides: the normalized product_desc / sku /
    upc / product_id of every catalog row whose EFFECTIVE category is in `acc_cats`. `acc_cats` defaults to
    the resolved accessory_category_set(...). Returns (acc_desc, acc_sku, acc_upc, acc_pid, enabled).
    Never raises. When classification is disabled or no catalog is loaded, all sets are empty (no-op)."""
    if acc_cats is None:
        acc_cats, enabled = accessory_category_set(client, org_id)
    else:
        acc_cats, enabled = set(str(c).strip().lower() for c in acc_cats if str(c).strip()), True
    if not enabled or not acc_cats:
        return set(), set(), set(), set(), enabled
    overrides = _load_overrides(client, org_id)
    rows = _load_catalog_rows(client, org_id)
    acc_desc, acc_sku, acc_upc, acc_pid = set(), set(), set(), set()
    for r in rows:
        cat = effective_category(r, overrides)
        if not cat or cat not in acc_cats:
            continue
        d = norm_desc(r.get("product_desc"))
        if d:
            acc_desc.add(d)
        s = clean_key(r.get("sku"))
        if s:
            acc_sku.add(s)
        u = clean_key(r.get("upc"))
        if u:
            acc_upc.add(u)
        p = pid_key(r.get("product_id"))
        if p:
            acc_pid.add(p)
    return acc_desc, acc_sku, acc_upc, acc_pid, enabled


def build(client, org_id, depts=None, cats=None, kws=None):
    """Full combined classifier (legacy sets + catalog sets). The engine calls this with no legacy sets →
    it resolves them from accessory_config; the router passes its already-resolved sets to avoid a re-read.
    Returns an AccessoryClassifier (never None; catalog sets empty when disabled → additive no-op)."""
    if depts is None or cats is None or kws is None:
        _d, _c, _k = _resolve_legacy_sets(client, org_id)
        depts = _d if depts is None else depts
        cats = _c if cats is None else cats
        kws = _k if kws is None else kws
    acc_desc, acc_sku, acc_upc, acc_pid, _enabled = build_catalog_sets(client, org_id)
    return AccessoryClassifier(depts, cats, kws, acc_desc, acc_sku, acc_upc, acc_pid)


def _resolve_legacy_sets(client, org_id):
    """The org's accessory departments/categories/keywords (lowercased sets) — mirrors the primary resolution
    of router._accessory_config (accessory_config first, flag_rules fallback). Never raises. Falls back to
    the historical {'ondigo'} department default when nothing is configured (byte-identical to the router)."""
    depts, cats, kws = [], [], []
    try:
        rows = (client.schema("commcalc").table("accessory_config")
                .select("departments,categories,product_keywords")
                .eq("org_id", org_id).limit(1).execute().data) or []
        if rows:
            depts = [d for d in (rows[0].get("departments") or []) if d]
            cats = [c for c in (rows[0].get("categories") or []) if c]
            kws = [k for k in (rows[0].get("product_keywords") or []) if k]
    except Exception:
        pass
    if not depts and not cats and not kws:
        try:
            fr = (client.schema("commcalc").table("flag_rules")
                  .select("accessory_departments,accessory_categories,accessory_product_keywords")
                  .eq("org_id", org_id).eq("id", 1).limit(1).execute().data) or []
            if fr:
                depts = [d for d in (fr[0].get("accessory_departments") or []) if d]
                cats = [c for c in (fr[0].get("accessory_categories") or []) if c]
                kws = [k for k in (fr[0].get("accessory_product_keywords") or []) if k]
        except Exception:
            pass
    # REUSE gp_category_map (mig 069) — a department mapped to 'accessory' there is ALSO an accessory here,
    # so the engine's accessory classification matches router._accessory_config exactly. Empty-safe.
    try:
        gp = (client.schema("commcalc").table("gp_category_map")
              .select("department,category").eq("org_id", org_id)
              .eq("category", "accessory").limit(1000).execute().data) or []
        have = {d.strip().lower() for d in depts}
        for r in gp:
            d = str(r.get("department") or "").strip()
            if d and d.lower() not in have:
                depts.append(d)
                have.add(d.lower())
    except Exception:
        pass
    if not depts and not cats and not kws:
        depts = ["Ondigo"]
    return ({d.strip().lower() for d in depts},
            {c.strip().lower() for c in cats},
            {k.strip().lower() for k in kws})


# ── admin-UI data helpers (deliverable 3) ────────────────────────────────────────────────────────────
def list_catalog(client, org_id, category=None, search=None, only_overridden=False, limit=500):
    """Catalog rows for the org with their FILE category, EFFECTIVE category (override applied), and an
    `overridden` flag — for the Catalog-categories admin page. Filters (category / free-text search /
    only-overridden) applied in Python. Never raises."""
    overrides = _load_overrides(client, org_id)
    rows = _load_catalog_rows(client, org_id)
    acc_cats, _enabled = accessory_category_set(client, org_id)
    cat_l = (category or "").strip().lower()
    q = (search or "").strip().lower()
    out = []
    for r in rows:
        file_cat = str(r.get("category") or "").strip()
        eff = effective_category(r, overrides)
        overridden = bool(eff and eff != file_cat.lower())
        rec = {
            "product_id": r.get("product_id"),
            "product_desc": r.get("product_desc") or "",
            "sku": r.get("sku") or "",
            "upc": r.get("upc") or "",
            "department": r.get("department") or "",
            "file_category": file_cat,
            "effective_category": eff,
            "overridden": overridden,
            "is_accessory": bool(eff and eff in acc_cats),
            "cost": r.get("cost"),
            "retail_price": r.get("retail_price"),
        }
        if cat_l and eff != cat_l and file_cat.lower() != cat_l:
            continue
        if only_overridden and not overridden:
            continue
        if q:
            hay = f"{rec['product_desc']} {rec['sku']} {rec['upc']} {rec['department']} {file_cat}".lower()
            if q not in hay:
                continue
        out.append(rec)
        if len(out) >= max(1, min(limit, 5000)):
            break
    return out


def catalog_categories(client, org_id):
    """Distinct categories present in the org's catalog (FILE categories UNION effective/override
    categories), sorted — the pick-don't-type option list for the category editor + accessory-category
    multiselect. Never raises."""
    overrides = _load_overrides(client, org_id)
    rows = _load_catalog_rows(client, org_id)
    cats = set()
    for r in rows:
        fc = str(r.get("category") or "").strip()
        if fc:
            cats.add(fc)
        eff = effective_category(r, overrides)
        if eff:
            cats.add(eff)
    # override categories that may not appear on any current row (kept visible)
    for mt in overrides.values():
        for c in mt.values():
            if c:
                cats.add(c)
    return sorted(cats, key=lambda s: s.lower())
