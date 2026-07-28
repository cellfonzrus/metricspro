"""DEVICE-CATEGORY QUALIFICATION for the multi-month (sale-triggered) payout — migration 245.

OWNER DIRECTIVE 2026-07-27 (verbatim): "the tablet dont qualify for the monthly payout, need a provision
to be added in the commision plan, what to exclude and what to include, like phones, tablets, home
internet, sim accessories, these will be default checked and the user should remove them as needed,
tablet and sim excluded by default".

WHAT THIS IS: a per-tenant (RULE TWO) include/exclude switch, per DEVICE CATEGORY, applied to the
sale-triggered installment chain BEFORE it pays. A chain whose category is excluded produces NO
installment (no ledger row, no pay, no withheld flag — it is not "held pending residual", it simply does
not qualify) and is REPORTED: per-category counts + dollars in `category_guard`, plus capped example
warnings the Calculate/preview surfaces render.

THREE LAYERS, all optional, each degrading to the layer below (so this file works with NO migration):
  1. per SCHEDULE   — plan_installment_schedule.qualifying_categories (jsonb; NULL = inherit)
  2. per ORG        — commission_org_config.installment_category_qualification (jsonb; NULL = defaults)
  3. code defaults  — DEFAULT_QUALIFICATION below = the owner's stated defaults (everything ON except
                      TABLET and SIM). Existing tenants get these through this fallback — deliberately
                      NOT a data migration that stamps rows.

HOW A LINE'S CATEGORY IS KNOWN (the honest answer, in resolution order — every layer is real data that
already exists; nothing new is asked of the POS):
  a. the tenant's OWN rules (commcalc.installment_category_rule) — pick-don't-type, editable, priority
     ordered. Consulted FIRST so a tenant can always override a built-in.
  b. the PRODUCT CATALOG (commcalc.raw_catalog + catalog_category_override, migs 230/231) — the b2bsoft
     "Product Update" Department/Category per product, matched by normalized Product Desc / SKU / UPC.
     Only when the tenant has uploaded one.
  c. the POS columns on the 78-col "Sales Transaction Details" export that raw_sales already stores:
     Department, Category, Product Desc, SKU.
  d. STRUCTURE: the serial's own shape — a 14-16 digit IMEI means a device was activated, a 19-20 digit
     ICCID means a SIM. This is what rescues a tenant whose Department/Category are blank.
  e. the existing accessory classifier (accessory_config / catalog, via classify_line) — never a new
     sixth accessory classifier (see [[accessory-flow-divergences]]).
  f. otherwise UNKNOWN — which by default still PAYS (nothing silently disappears) but is counted and
     warned about, so the operator maps it instead of discovering a silent zero months later.

PURE: every resolver here takes its config as an argument (no DB), so the whole thing is unit-testable.
"""
import re

# The categories the owner named. `unknown` is not a device category — it is the honest "we could not
# tell" bucket, and it is a first-class switch so a tenant can decide whether an unclassifiable
# activation pays (default: yes + warn) or waits for a mapping.
CATEGORY_KEYS = ("phone", "tablet", "home_internet", "sim", "accessory", "unknown")

CATEGORY_LABELS = {
    "phone": "Phones",
    "tablet": "Tablets",
    "home_internet": "Home internet",
    "sim": "SIM / SIM kits",
    "accessory": "Accessories",
    "unknown": "Could not be classified",
}

# OWNER DEFAULTS (2026-07-27): everything checked EXCEPT tablet and SIM. `unknown` defaults to TRUE so
# that turning this feature on can never silently STOP paying something we merely failed to classify —
# it pays and shouts. A tenant that wants the opposite unchecks it.
DEFAULT_QUALIFICATION = {
    "phone": True,
    "tablet": False,
    "home_internet": True,
    "sim": False,
    "accessory": True,
    "unknown": True,
}

# ── the rule vocabulary (also what the admin UI offers — pick-don't-type, §3b) ──────────────────────
MATCH_FIELDS = ("product_desc", "department", "category", "sku", "catalog_category", "serial_kind")
MATCH_OPS = ("contains", "equals", "word", "in")

# Built-in rules. These are the FALLBACK TAIL: a tenant's own rows are evaluated first, and these keep
# working for every tenant that has configured nothing (which is all of them today).
#
# PRIORITY IS THE WHOLE DESIGN. One activation's lines disagree by construction — a tablet sale carries a
# tablet device line, a "Tablet Plan" line AND a SIM line — so the chain's category is the STRONGEST
# (lowest-priority-number) hit across all of its lines:
#     home internet (10s) < tablet (20s) < phone (30s) < sim (40s) < structural IMEI (90) < accessory (95)
# so a phone sold with a SIM kit is a PHONE, a tablet sold with a SIM kit is a TABLET, a SIM sold with no
# device at all (real BYOD) is a SIM, and a case sold with a handset never out-votes the handset.
DEFAULT_CATEGORY_RULES = [
    # ── home internet ───────────────────────────────────────────────────────────────────────────
    {"category_key": "home_internet", "match_field": "product_desc", "match_op": "contains",
     "match_value": "home internet", "priority": 10},
    {"category_key": "home_internet", "match_field": "product_desc", "match_op": "contains",
     "match_value": "internet gateway", "priority": 12},
    {"category_key": "home_internet", "match_field": "product_desc", "match_op": "word",
     "match_value": "vhi", "priority": 14},
    {"category_key": "home_internet", "match_field": "catalog_category", "match_op": "contains",
     "match_value": "home internet", "priority": 14},
    # ── tablet ──────────────────────────────────────────────────────────────────────────────────
    {"category_key": "tablet", "match_field": "product_desc", "match_op": "word",
     "match_value": "tablet", "priority": 20},
    {"category_key": "tablet", "match_field": "product_desc", "match_op": "word",
     "match_value": "tab", "priority": 22},
    {"category_key": "tablet", "match_field": "product_desc", "match_op": "word",
     "match_value": "ipad", "priority": 22},
    {"category_key": "tablet", "match_field": "catalog_category", "match_op": "contains",
     "match_value": "tablet", "priority": 24},
    {"category_key": "tablet", "match_field": "department", "match_op": "contains",
     "match_value": "tablet", "priority": 26},
    {"category_key": "tablet", "match_field": "category", "match_op": "contains",
     "match_value": "tablet", "priority": 26},
    # ── phone ───────────────────────────────────────────────────────────────────────────────────
    {"category_key": "phone", "match_field": "category", "match_op": "contains",
     "match_value": "kittedbranded", "priority": 30},
    {"category_key": "phone", "match_field": "department", "match_op": "contains",
     "match_value": "brandedhandset", "priority": 32},
    {"category_key": "phone", "match_field": "department", "match_op": "contains",
     "match_value": "handset", "priority": 34},
    {"category_key": "phone", "match_field": "category", "match_op": "contains",
     "match_value": "handset", "priority": 34},
    {"category_key": "phone", "match_field": "product_desc", "match_op": "word",
     "match_value": "phone", "priority": 36},
    {"category_key": "phone", "match_field": "catalog_category", "match_op": "contains",
     "match_value": "phone", "priority": 36},
    # ── SIM ─────────────────────────────────────────────────────────────────────────────────────
    {"category_key": "sim", "match_field": "product_desc", "match_op": "contains",
     "match_value": "sim kit", "priority": 40},
    {"category_key": "sim", "match_field": "product_desc", "match_op": "contains",
     "match_value": "sim card", "priority": 40},
    {"category_key": "sim", "match_field": "product_desc", "match_op": "word",
     "match_value": "sim", "priority": 44},
    {"category_key": "sim", "match_field": "category", "match_op": "contains",
     "match_value": "simmarketplace", "priority": 44},
    {"category_key": "sim", "match_field": "catalog_category", "match_op": "contains",
     "match_value": "sim", "priority": 46},
    {"category_key": "sim", "match_field": "serial_kind", "match_op": "equals",
     "match_value": "iccid", "priority": 48},
    # ── structural tail: an activation that carries a real device IMEI and matched nothing more
    #    specific is a PHONE. Deliberately last so any wording/department/catalog signal beats it.
    {"category_key": "phone", "match_field": "serial_kind", "match_op": "equals",
     "match_value": "imei", "priority": 90},
]

# The accessory signal comes from the EXISTING classifier (classify_line), not from a rule row. It is
# deliberately the WEAKEST rung in the ladder: an activation's accessories (case, screen protector) must
# never out-vote the device that was actually activated — including a device recognised only by its IMEI
# (the structural tail at 90). An accessory-ONLY sale still resolves to accessory, because it is then the
# only signal present.
ACCESSORY_PRIORITY = 95

_WORD_RX_CACHE = {}


def _word_hit(text, kw):
    """Whole-word/phrase match, alphanumeric-aware — the same semantics the rate-plan matcher uses so
    'tab' never matches 'table' and 'sim' never matches 'simple'. PURE."""
    rx = _WORD_RX_CACHE.get(kw)
    if rx is None:
        rx = _WORD_RX_CACHE[kw] = re.compile(r"(?<![a-z0-9])" + re.escape(kw) + r"(?![a-z0-9])", re.I)
    return bool(rx.search(text))


def serial_kind(serial):
    """'imei' | 'iccid' | '' for a raw serial string. STRUCTURAL, carrier-agnostic: an ICCID is 18-22
    digits (SIM), an IMEI/MEID is 14-17 (device). Anything else is unknown. PURE."""
    s = re.sub(r"[^0-9]", "", str(serial or ""))
    if not s:
        return ""
    if 18 <= len(s) <= 22:
        return "iccid"
    if 14 <= len(s) <= 17:
        return "imei"
    return ""


def _field_value(row, field, catalog_cat=""):
    if field == "product_desc":
        return str(row.get("product_desc") or row.get("customer_plan") or "").strip().lower()
    if field == "department":
        return str(row.get("department") or "").strip().lower()
    if field == "category":
        return str(row.get("category") or "").strip().lower()
    if field == "sku":
        return str(row.get("sku") or "").strip().lower()
    if field == "catalog_category":
        return str(catalog_cat or "").strip().lower()
    if field == "serial_kind":
        return serial_kind(row.get("serial_1"))
    return ""


def _rule_hits(row, rule, catalog_cat=""):
    """True if ONE rule matches ONE sale line. PURE."""
    field = str(rule.get("match_field") or "product_desc").strip().lower()
    op = str(rule.get("match_op") or "contains").strip().lower()
    want = str(rule.get("match_value") or "").strip().lower()
    if not want:
        return False
    have = _field_value(row, field, catalog_cat)
    if not have:
        return False
    if op == "equals":
        return have == want
    if op == "word":
        return _word_hit(have, want)
    if op == "in":
        return have in {p.strip() for p in want.split(",") if p.strip()}
    return want in have          # 'contains' — and the documented fall-through for anything else


def normalize_rules(rows):
    """Normalize + order a mix of stored rows and built-ins into the evaluation list. Stored (tenant)
    rules keep their own priority but sort BEFORE a built-in of the same priority, so a tenant override
    always wins a tie. PURE."""
    out = []
    for i, r in enumerate(rows or []):
        ck = str(r.get("category_key") or "").strip().lower()
        if ck not in CATEGORY_KEYS or ck == "unknown":
            continue
        if r.get("is_active") is False:
            continue
        try:
            pri = int(r.get("priority") if r.get("priority") is not None else 100)
        except Exception:
            pri = 100
        out.append({"category_key": ck,
                    "match_field": str(r.get("match_field") or "product_desc").strip().lower(),
                    "match_op": str(r.get("match_op") or "contains").strip().lower(),
                    "match_value": str(r.get("match_value") or "").strip(),
                    "priority": pri,
                    "source": r.get("source") or "tenant",
                    "_ord": (pri, 0 if (r.get("source") or "tenant") == "tenant" else 1, i)})
    out.sort(key=lambda x: x["_ord"])
    return out


def effective_rules(stored_rows):
    """The tenant's rules (if any) + the built-in tail. A tenant that configures ONE rule does not lose
    every built-in — that failure mode ("I added a rule and everything became unknown") is exactly what
    silent zeros are made of. PURE."""
    builtin = [dict(r, source="builtin") for r in DEFAULT_CATEGORY_RULES]
    return normalize_rules(list(stored_rows or []) + builtin)


def resolve_line_category(row, rules, catalog_cat=""):
    """(category_key, rule) for ONE sale line — the strongest (lowest priority) matching rule, or
    (None, None). PURE."""
    for r in rules:
        if _rule_hits(row, r, catalog_cat):
            return r["category_key"], r
    return None, None


def resolve_chain_category(lines, rules, catalog_cat_of=None, is_accessory=None):
    """(category_key, evidence) for a whole ACTIVATION (all of its sale lines).

    The chain's category is the STRONGEST signal any of its lines carries (see the priority ladder
    above), because one activation's lines legitimately disagree: the tablet, its plan and its SIM are
    three lines of one sale. Returns 'unknown' with the products it looked at when nothing matched, so
    the caller can WARN instead of silently paying or silently dropping. PURE — `catalog_cat_of(row)`
    and `is_accessory(row)` are injected."""
    best = None                                    # (priority, rank_in_rules, category, evidence)
    for ln in (lines or []):
        cc = ""
        if catalog_cat_of is not None:
            try:
                cc = catalog_cat_of(ln) or ""
            except Exception:
                cc = ""
        # An ACCESSORY line contributes ONLY "accessory" — never a device signal. Otherwise a "Phone
        # Case" would make an accessory sale a phone, and a "Tablet Case" sold with a handset would
        # make a PHONE activation a TABLET and stop it paying. The accessory verdict comes from the
        # existing classifier (accessory_config / catalog), not from a sixth classifier.
        is_acc = False
        if is_accessory is not None:
            try:
                is_acc = bool(is_accessory(ln))
            except Exception:
                is_acc = False
        if is_acc:
            if best is None or (ACCESSORY_PRIORITY, 0) < best[0]:
                best = ((ACCESSORY_PRIORITY, 0), "accessory",
                        {"product": str(ln.get("product_desc") or "")[:160],
                         "matched_field": "accessory_classifier",
                         "matched_value": "accessory_config", "rule_source": "classifier",
                         "catalog_category": cc or None})
            continue
        for i, r in enumerate(rules):
            if _rule_hits(ln, r, cc):
                key = (r["priority"], i)
                if best is None or key < best[0]:
                    best = (key, r["category_key"],
                            {"product": str(ln.get("product_desc") or "")[:160],
                             "matched_field": r["match_field"], "matched_value": r["match_value"],
                             "rule_source": r.get("source") or "builtin",
                             "catalog_category": cc or None})
                break                              # first (strongest) hit for THIS line
    if best is None:
        return "unknown", {"product": None, "matched_field": None, "matched_value": None,
                           "rule_source": None,
                           "products": [str(l.get("product_desc") or "")[:120] for l in (lines or [])[:6]]}
    return best[1], best[2]


# ── qualification config (three layers) ────────────────────────────────────────────────────────────
def normalize_qualification(stored):
    """A stored qualification (dict OR list of included keys OR None) → the full dict, with the owner's
    defaults filling every key the tenant did not state. PURE. None/garbage → DEFAULT_QUALIFICATION."""
    out = dict(DEFAULT_QUALIFICATION)
    if isinstance(stored, dict):
        for k in CATEGORY_KEYS:
            if k in stored:
                out[k] = bool(stored[k])
    elif isinstance(stored, (list, tuple, set)):
        want = {str(x).strip().lower() for x in stored}
        for k in CATEGORY_KEYS:
            out[k] = k in want
    return out


def qualification_for(sched, org_qual):
    """(qualification dict, source) for ONE schedule: its own qualifying_categories if it states any,
    else the org's, else the code defaults. PURE."""
    own = (sched or {}).get("qualifying_categories")
    if isinstance(own, dict) and own:
        return normalize_qualification(own), "schedule"
    if isinstance(own, (list, tuple)) and len(own) > 0:
        return normalize_qualification(own), "schedule"
    if isinstance(org_qual, dict) and org_qual.get("_stored"):
        return normalize_qualification({k: v for k, v in org_qual.items() if k in CATEGORY_KEYS}), "org"
    return dict(DEFAULT_QUALIFICATION), "default"


# ── loaders (the only I/O in this module; every one degrades to the code default) ───────────────────
def load_category_rules(client, org_id):
    """The org's own installment_category_rule rows + the built-in tail. Missing table (migration 245
    not applied) → built-ins only. Never raises.

    ORDERED, and the ordering is LOAD-BEARING (Gate-1 N3, 2026-07-27). `normalize_rules` keeps a
    stable sort on (priority, tenant-before-builtin, ARRIVAL INDEX), so two tenant rules at the SAME
    priority are tie-broken by the order this function returns them in. Unordered, that is whatever
    Postgres felt like — which would make a rep's pay depend on physical row order. Sorting by
    `priority` then `created_at` then `id` makes the tie-break deterministic AND identical to what the
    admin UI lists (`GET /plan-installments/category-qualification` orders by priority), so what the
    operator sees is the order the money is decided in.

    `created_at`/`id` may not exist on a hand-made table, so the ordered read falls back to the plain
    read rather than losing the tenant's rules entirely."""
    rows = []
    try:
        q = (client.schema("commcalc").table("installment_category_rule").select("*")
             .eq("org_id", org_id))
        try:
            rows = (q.order("priority").order("created_at").order("id")
                    .limit(2000).execute().data) or []
        except Exception:
            rows = (client.schema("commcalc").table("installment_category_rule").select("*")
                    .eq("org_id", org_id).limit(2000).execute().data) or []
    except Exception:
        rows = []
    # Belt-and-braces: sort in Python too, so a client/stub that ignores .order() still yields a
    # deterministic evaluation order. PURE given the rows.
    try:
        rows.sort(key=lambda r: (int(r.get("priority") if r.get("priority") is not None else 100),
                                 str(r.get("created_at") or ""), str(r.get("id") or "")))
    except Exception:
        pass
    for r in rows:
        r["source"] = "tenant"
    return effective_rules(rows)


def load_org_qualification(client, org_id):
    """The org-level qualification dict. Carries `_stored` so qualification_for can tell "the tenant
    saved this" from "nothing configured". Missing column/table → defaults. Never raises."""
    stored = None
    try:
        rows = (client.schema("commcalc").table("commission_org_config")
                .select("installment_category_qualification")
                .eq("org_id", org_id).limit(1).execute().data) or []
        if rows:
            stored = rows[0].get("installment_category_qualification")
    except Exception:
        stored = None
    out = normalize_qualification(stored)
    out["_stored"] = stored is not None
    return out


def build_catalog_category_lookup(client, org_id):
    """`row -> catalog category` using the EXISTING product catalog (migs 230/231): normalized Product
    Desc / SKU / UPC → the catalog row's effective category. Returns a callable (always safe) and never
    raises; with no catalog loaded it returns a lookup that always answers ''."""
    by_desc, by_sku, by_upc = {}, {}, {}
    try:
        from app.modules.commcalc import accessory_catalog as ac
        overrides = ac._load_overrides(client, org_id)
        for r in ac._load_catalog_rows(client, org_id):
            cat = ac.effective_category(r, overrides)
            if not cat:
                continue
            d = ac.norm_desc(r.get("product_desc"))
            if d:
                by_desc.setdefault(d, cat)
            s = ac.clean_key(r.get("sku"))
            if s:
                by_sku.setdefault(s, cat)
            u = ac.clean_key(r.get("upc"))
            if u:
                by_upc.setdefault(u, cat)

        def _lookup(row):
            s = ac.clean_key(row.get("sku"))
            if s and s in by_sku:
                return by_sku[s]
            u = ac.clean_key(row.get("upc"))
            if u and u in by_upc:
                return by_upc[u]
            d = ac.norm_desc(row.get("product_desc"))
            return by_desc.get(d, "") if d else ""
    except Exception:
        def _lookup(row):
            return ""
    return _lookup
