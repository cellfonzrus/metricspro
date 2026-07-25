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
import os
import re
import threading
import time
import weakref

from app.modules.commcalc.calculator import safe_float


# ── ORG-SCOPED CONFIG CACHE (Gate-1 follow-up ② — 2026-07-25) ───────────────────────────────────────
# ONE request used to hit the same tables over and over: GET /commcalc/catalog alone did
# list_catalog (raw_catalog + catalog_category_override) + catalog_categories (both AGAIN) +
# _accessory_config (NINE separate single-row probes of accessory_config, plus flag_rules +
# gp_category_map, plus — when catalog classification is ON — build_catalog_sets, i.e. BOTH catalog
# tables a THIRD time) = 17 queries. At ~100ms per query (each get_supabase() builds a fresh client +
# TLS session) that is seconds of pure round-trip for one page load.
#
# TWO LAYERS, and the DEFAULT one cannot go stale:
#
#   L1 — PER-REQUEST (always on, the layer the nit asked for). Keyed on the CLIENT OBJECT (a
#        WeakKeyDictionary) + org_id. Every handler does `client = sb()` ONCE and threads that same client
#        through list_catalog / catalog_categories / _accessory_config, so the repeats inside a request
#        collapse; a DIFFERENT request gets a different client from get_supabase() and therefore a clean
#        miss. Nothing survives the request, so no read can ever serve data written after it. The weak key
#        means entries disappear with the client (no id() reuse hazard, no unbounded growth).
#
#   L2 — CROSS-REQUEST TTL (OPT-IN, default OFF). Set COMMCALC_CFG_CACHE_TTL=<seconds> to also memoize
#        across requests for that many seconds. Left off by default deliberately: a direct SQL-Editor edit
#        to accessory_config / raw_catalog does not go through an endpoint, so it would be invisible for
#        up to the TTL. Turn it on once the tenant edits config only through the UI.
#
# TENANT SAFETY (both layers): the cache key ALWAYS includes org_id, and a blank/None org_id is NEVER
# cached (a tenant-less key any other blank caller could read). Both layers are dropped by invalidate()
# from every endpoint that writes accessory_config / catalog_category_override / raw_catalog /
# gp_category_map / flag_rules, so an edit is live on the very next read.
# Values are READ-ONLY by contract; the accessors hand back a copy of the container.
_CACHE_TTL_ENV = "COMMCALC_CFG_CACHE_TTL"
_CACHE_TTL_DEFAULT = 0.0                      # L2 off unless the operator opts in
_cache: dict = {}                             # L2: (kind, org) -> (expires_at, value)
_client_cache: "weakref.WeakKeyDictionary" = weakref.WeakKeyDictionary()   # L1: client -> {(kind, org): value}
_cache_lock = threading.RLock()
_cache_stats = {"hit_request": 0, "hit_ttl": 0, "miss": 0, "skip": 0, "invalidate": 0}


def cache_ttl() -> float:
    """L2 lifetime in seconds. Env-tunable (infrastructure, not tenant business config); 0 = L2 OFF."""
    try:
        v = float(os.getenv(_CACHE_TTL_ENV) or _CACHE_TTL_DEFAULT)
    except (TypeError, ValueError):
        v = _CACHE_TTL_DEFAULT
    return v if v > 0 else 0.0


def cache_get(kind: str, org_id, client=None):
    """Cached value for (kind, org_id) — the request-scoped layer first, then the opt-in TTL layer.
    None on miss/expiry/blank-org."""
    org = str(org_id or "").strip()
    if not org:
        _cache_stats["skip"] += 1
        return None
    if client is not None:
        with _cache_lock:
            try:
                ent = _client_cache.get(client)
            except TypeError:                 # a client that can't be weak-referenced
                ent = None
            if ent is not None and (kind, org) in ent:
                _cache_stats["hit_request"] += 1
                return ent[(kind, org)]
    if cache_ttl() > 0:
        with _cache_lock:
            ent = _cache.get((kind, org))
            if ent and ent[0] > time.monotonic():
                _cache_stats["hit_ttl"] += 1
                return ent[1]
            if ent:
                _cache.pop((kind, org), None)
    _cache_stats["miss"] += 1
    return None


def cache_put(kind: str, org_id, value, client=None):
    """Memoize `value` for (kind, org_id) in the request-scoped layer (and the TTL layer when enabled).
    Blank org → no-op. Returns `value` so callers can `return cache_put(...)`."""
    org = str(org_id or "").strip()
    if not org:
        return value
    if client is not None:
        with _cache_lock:
            try:
                _client_cache.setdefault(client, {})[(kind, org)] = value
            except TypeError:
                pass
    ttl = cache_ttl()
    if ttl > 0:
        with _cache_lock:
            _cache[(kind, org)] = (time.monotonic() + ttl, value)
    return value


def invalidate(org_id=None, kind=None):
    """Drop cached config from BOTH layers. `org_id=None` → every org (tests); `kind=None` → every kind.
    Call this from EVERY write that changes accessory_config / catalog_category_override / raw_catalog /
    gp_category_map / flag_rules so a saved category or a re-uploaded catalog is live immediately."""
    org = str(org_id or "").strip()
    n = 0
    with _cache_lock:
        for k in [k for k in _cache if (not org or k[1] == org) and (kind is None or k[0] == kind)]:
            _cache.pop(k, None)
            n += 1
        for ent in list(_client_cache.values()):
            for k in [k for k in ent if (not org or k[1] == org) and (kind is None or k[0] == kind)]:
                ent.pop(k, None)
                n += 1
        _cache_stats["invalidate"] += n
    return n


def cache_snapshot():
    """Diagnostics for the harness: (sorted TTL-layer keys, stats copy, request-layer key count)."""
    with _cache_lock:
        req = sorted({k for ent in list(_client_cache.values()) for k in ent})
        return sorted(_cache.keys()), dict(_cache_stats), req


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
    Never raises → ({}, False) if the columns/table are absent (pre-231).
    Memoized per-org for cache_ttl() seconds (Gate-1 ②); invalidated by every accessory_config write."""
    _c = cache_get("acc_cats", org_id, client)
    if _c is not None:
        return set(_c[0]), _c[1]
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
    cache_put("acc_cats", org_id, (set(cats), enabled), client)
    return set(cats), enabled


def _load_overrides(client, org_id):
    """Per-org category OVERRIDES (mig 230 commcalc.catalog_category_override) — the user-editable layer on
    top of the loaded catalog file (deliverable 3, non-destructive). Returns dicts keyed by match_type:
      {'upc': {key: cat}, 'sku': {...}, 'product_id': {...}, 'product_desc': {normdesc: cat}}
    Category values lowercased. Never raises → all-empty if the table is absent (pre-230).
    Memoized per-org for cache_ttl() seconds (Gate-1 ②) — list_catalog, catalog_categories and
    build_catalog_sets all need it inside ONE request; invalidated by every override write."""
    _c = cache_get("overrides", org_id, client)
    if _c is not None:
        return {k: dict(v) for k, v in _c.items()}
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
    cache_put("overrides", org_id, out, client)
    return {k: dict(v) for k, v in out.items()}


def _load_catalog_rows(client, org_id):
    """All catalog rows for the org (select * so a missing TOTAL-variant column never errors). Uses the
    same high .limit as raw_catalog's other reads (the org may hold a few thousand rows). Never raises.
    Memoized per-org for cache_ttl() seconds (Gate-1 ②) — this is the biggest read in the module and one
    /catalog request needed it three times; invalidated by every catalog upload."""
    _c = cache_get("catalog_rows", org_id, client)
    if _c is not None:
        return list(_c)
    try:
        rows = (client.schema("commcalc").table("raw_catalog").select("*")
                .eq("org_id", org_id).limit(100000).execute().data) or []
    except Exception:
        return []
    cache_put("catalog_rows", org_id, rows, client)
    return list(rows)


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
    Never raises. When classification is disabled or no catalog is loaded, all sets are empty (no-op).
    The DEFAULT (acc_cats=None) path — the one router._accessory_config and commission_engine.preview both
    take — is memoized per-org for cache_ttl() seconds (Gate-1 ②) so a single request builds these sets
    once instead of re-reading raw_catalog per call. An EXPLICIT acc_cats is never cached (caller-specific).
    The returned sets are read-only by contract (AccessoryClassifier copies them into itself)."""
    if acc_cats is None:
        _c = cache_get("catsets", org_id, client)
        if _c is not None:
            return _c
        acc_cats, enabled = accessory_category_set(client, org_id)
        _cacheable = True
    else:
        _cacheable = False
        acc_cats, enabled = set(str(c).strip().lower() for c in acc_cats if str(c).strip()), True
    if not enabled or not acc_cats:
        out = (set(), set(), set(), set(), enabled)
        return cache_put("catsets", org_id, out, client) if _cacheable else out
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
    out = (acc_desc, acc_sku, acc_upc, acc_pid, enabled)
    return cache_put("catsets", org_id, out, client) if _cacheable else out


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
    multiselect. Never raises.

    Gate-1 follow-up ③ (2026-07-25) — DE-DUPED CASE-INSENSITIVELY. Effective/override categories are stored
    lowercased while the file's Category keeps its own casing, so 'Accessories' and 'accessories' used to be
    offered as two separate options in the same <select> (and the second one looked like a different
    category to the user). One option per case-folded category now, displayed with the FILE spelling when
    the catalog has one (that is the spelling the tenant recognizes), else the stored/override spelling.
    Matching is unaffected: list_catalog compares case-insensitively and overrides are lowercased on save."""
    overrides = _load_overrides(client, org_id)
    rows = _load_catalog_rows(client, org_id)
    display: dict = {}      # casefolded -> spelling to show

    def offer(value, prefer=False):
        v = str(value or "").strip()
        if not v:
            return
        k = v.lower()
        if k not in display or (prefer and display[k] != v and display[k] == k):
            # `prefer` = a FILE spelling; it wins over an all-lowercase stored/override spelling.
            display[k] = v

    for r in rows:
        offer(r.get("category"), prefer=True)
        offer(effective_category(r, overrides))
    # override categories that may not appear on any current row (kept visible)
    for mt in overrides.values():
        for c in mt.values():
            offer(c)
    return sorted(display.values(), key=lambda s: s.lower())
