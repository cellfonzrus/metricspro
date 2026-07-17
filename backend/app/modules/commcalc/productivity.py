"""Productivity · weighted stack-ranking · performance-review — the PURE compute layer.

NON-money / display-analytics only. Everything here is unit-testable with no DB: the router does the
cross-module reads (the shared sales aggregation, StoreOps time-clock hours, Daily Targets attainment,
KPI metrics, DM store-visit upkeep) and passes plain dicts in.

ONE unified per-org ITEM REGISTRY powers BOTH the stack ranker (items with count_in_stack_ranker) and the
performance review (items with count_in_review). Registry rows are code DEFAULTS overlaid by an org-override
table (commcalc.productivity_item, mig 215) — the report_pull code-default + org-override pattern, NOT
SEED_VERSION provisioning: a brand-new tenant needs zero seed rows and still sees the placeholders.

COMMISSION TIE-IN — INERT / OPT-IN. `perf_kpi_value` exposes a per-rep period score under a KPI key
('performance_score' / 'perf:<item_key>') that a payout engine COULD reference. It is inert: no calc engine
(calculator.py / commission_engine.py) imports this module, so commission outputs are byte-identical whether
or not the productivity tables exist or are populated. Activation is owner-gated (see the module return).
"""

# ── SOURCE CATALOG ───────────────────────────────────────────────────────────────────────────────
# Every source_key maps to an EXISTING system read (wired in router.py — never a forked classifier).
# value_type: number | dollar | percent | score.   grain: rep | store (a store metric is applied to the
# store's reps). default_standard: the seed target when an item using this source has none set.
SOURCE_CATALOG = {
    # The shared commission sales aggregation (_sales_cell_agg) — the SAME distinct-txn definitions the
    # Sales Report / Executive MTD / Daily Targets use. NOT a new accessory/activation matcher.
    "acc_sales":     {"label": "Accessory sales", "value_type": "dollar", "grain": "rep",
                      "default_standard": 1000.0, "desc": "Accessory revenue (shared _sales_cell_agg accessory_rev)."},
    "activations":   {"label": "Activations", "value_type": "number", "grain": "rep",
                      "default_standard": 40.0, "desc": "Premium activations, distinct transaction (_prem)."},
    "upgrades":      {"label": "Upgrades", "value_type": "number", "grain": "rep",
                      "default_standard": 10.0, "desc": "Upgrade transactions, distinct (_upg)."},
    "swaps":         {"label": "Swaps", "value_type": "number", "grain": "rep",
                      "default_standard": 10.0, "desc": "Swap transactions, distinct (_swap)."},
    "boxes":         {"label": "Boxes", "value_type": "number", "grain": "rep",
                      "default_standard": 50.0, "desc": "Box units sold (dept in _BOX_DEPTS)."},
    # Feature-1 productivity output (boxes/acc per hour worked).
    "boxes_per_hour": {"label": "Boxes / hour", "value_type": "number", "grain": "rep",
                       "default_standard": 1.0, "desc": "Boxes ÷ hours worked (StoreOps time-clock)."},
    "acc_per_hour":  {"label": "Accessory $ / hour", "value_type": "dollar", "grain": "rep",
                      "default_standard": 25.0, "desc": "Accessory $ ÷ hours worked (StoreOps time-clock)."},
    "hours_worked":  {"label": "Hours worked", "value_type": "number", "grain": "rep",
                      "default_standard": 160.0, "desc": "Closed time-clock punch hours (storeops.timelog)."},
    # Daily Targets attainment (targets_engine — per-rep MTD achieved vs target).
    "targets_attainment": {"label": "Targets attainment", "value_type": "percent", "grain": "rep",
                           "default_standard": 100.0, "desc": "Daily-Targets MTD activations achieved ÷ target × 100."},
    # KPI Metrics module per-rep values (rep_commissions.kpi_values) — cleanly per-rep, whole-number %.
    "kpi_attainment": {"label": "KPI attainment", "value_type": "percent", "grain": "rep",
                       "default_standard": 100.0, "desc": "% of the rep's KPIs (ATU/Protect/BYOD/Family/3MR/AAL) that met target."},
    "kpi_atu":        {"label": "ATU %", "value_type": "percent", "grain": "rep", "default_standard": 55.0, "desc": "rep_commissions.kpi_values.atu"},
    "kpi_protect":    {"label": "Protect %", "value_type": "percent", "grain": "rep", "default_standard": 80.0, "desc": "rep_commissions.kpi_values.protect"},
    "kpi_byod":       {"label": "BYOD %", "value_type": "percent", "grain": "rep", "default_standard": 35.0, "desc": "rep_commissions.kpi_values.byod"},
    "kpi_familyplan": {"label": "Family Plan %", "value_type": "percent", "grain": "rep", "default_standard": 45.0, "desc": "rep_commissions.kpi_values.familyplan"},
    "kpi_tmr3":       {"label": "3MR %", "value_type": "percent", "grain": "rep", "default_standard": 70.0, "desc": "rep_commissions.kpi_values.tmr3"},
    "kpi_aal":        {"label": "AAL %", "value_type": "percent", "grain": "rep", "default_standard": 5.0, "desc": "rep_commissions.kpi_values.aal"},
    # DM store-visit upkeep — a STORE metric applied to the store's reps for the period.
    "store_upkeep":  {"label": "Store upkeep", "value_type": "percent", "grain": "store",
                      "default_standard": 90.0, "desc": "Avg DM store-visit checklist pass-rate (storevisit) for the period × 100."},
}


def source_catalog():
    """The pickable source list for the 'add item' affordance (RULE THREE — pick, don't type)."""
    return [{"source_key": k, **v} for k, v in SOURCE_CATALOG.items()]


# ── DEFAULT REGISTRY ITEMS (changeable placeholders) ─────────────────────────────────────────────
# Seeded in code, overlaid by the org-override table. `standard=None` on a ranking item ⇒ it ranks
# RELATIVE to the cohort (value ÷ field-max) instead of against an absolute target.
def _item(item_key, label, source_key, standard, stype, weight, stack, review, sort):
    return {"item_key": item_key, "label": label, "source_key": source_key, "standard": standard,
            "standard_type": stype, "weight": float(weight), "count_in_stack_ranker": bool(stack),
            "count_in_review": bool(review), "enabled": True, "hidden": False,
            "is_seed_default": True, "sort": sort}


DEFAULT_ITEMS = [
    # Stack-ranking placeholders (rank relative to the field — no absolute standard).
    _item("acc_sales", "Accessory sales", "acc_sales", None, "dollar", 1.0, True, False, 10),
    _item("activations", "Activations", "activations", None, "number", 1.0, True, False, 20),
    _item("upgrades", "Upgrades", "upgrades", None, "number", 1.0, True, False, 30),
    _item("swaps", "Swaps", "swaps", None, "number", 1.0, True, False, 40),
    _item("boxes", "Boxes", "boxes", None, "number", 1.0, True, False, 50),
    # Performance-review placeholders (measured against a definable standard).
    _item("targets_achieved", "Targets achieved", "targets_attainment", 100.0, "percent", 1.0, False, True, 60),
    _item("kpi_achieved", "KPI achieved", "kpi_attainment", 100.0, "percent", 1.0, False, True, 70),
    _item("accessory_sales", "Accessory sales (review)", "acc_sales", 1000.0, "dollar", 1.0, False, True, 80),
    _item("store_upkeep", "Store upkeep", "store_upkeep", 90.0, "percent", 1.0, False, True, 90),
]

# The overridable columns an org row may carry (item_key identifies the row).
_OVERRIDE_COLS = ("label", "source_key", "standard", "standard_type", "weight",
                  "count_in_stack_ranker", "count_in_review", "enabled", "hidden", "sort")


def resolve_registry(org_rows):
    """Merge code DEFAULT_ITEMS with the org override rows (commcalc.productivity_item) → the effective
    registry. An org row with a default's item_key OVERRIDES its columns (edit / disable / hide / delete-a-
    default = a hidden override). An org row whose item_key is not a default is a CUSTOM item. Hidden items
    are dropped. Deterministic order: sort, then item_key."""
    by_key = {d["item_key"]: dict(d) for d in DEFAULT_ITEMS}
    for r in (org_rows or []):
        k = str(r.get("item_key") or "").strip()
        if not k:
            continue
        base = by_key.get(k)
        if base is None:
            base = {"item_key": k, "is_seed_default": False, "source_key": "", "label": k,
                    "standard": None, "standard_type": "number", "weight": 1.0,
                    "count_in_stack_ranker": True, "count_in_review": False,
                    "enabled": True, "hidden": False, "sort": 500}
        for c in _OVERRIDE_COLS:
            if c in r and r[c] is not None:
                base[c] = r[c]
        # normalize types
        base["weight"] = _f(base.get("weight"), 1.0)
        base["standard"] = None if base.get("standard") in (None, "") else _f(base.get("standard"))
        base["sort"] = int(_f(base.get("sort"), 500))
        for b in ("count_in_stack_ranker", "count_in_review", "enabled", "hidden"):
            base[b] = bool(base.get(b))
        by_key[k] = base
    items = [v for v in by_key.values() if not v.get("hidden")]
    items.sort(key=lambda x: (int(x.get("sort") or 0), str(x.get("item_key"))))
    return items


def _f(v, default=0.0):
    try:
        if v is None or v == "":
            return default
        return float(v)
    except (TypeError, ValueError):
        return default


# ── ATTAINMENT + WEIGHTED SCORE (shared by ranker + review) ──────────────────────────────────────
def attainment(value, standard, field_max=None):
    """Ratio of a rep's value to its target. Absolute mode when a standard is set (value ÷ standard);
    otherwise RELATIVE mode (value ÷ the cohort field-max) for stack-ranking items with no absolute
    target. Returns None (⇒ n/a, excluded) when the value is missing OR neither a positive standard nor a
    positive field-max exists — never a divide-by-zero, never a 0 that tanks a score."""
    if value is None:
        return None
    v = _f(value)
    if standard is not None and _f(standard) > 0:
        return v / _f(standard)
    if field_max is not None and _f(field_max) > 0:
        return v / _f(field_max)
    return None


def weighted_score(items, values, field_max_by_source):
    """Weighted-attainment score for ONE rep over `items` (already filtered to the surface).
    score = Σ(attainment_i · weight_i) ÷ Σ(weight_i) over items whose attainment is computable — an item
    with a missing value / uncomputable attainment is EXCLUDED from both sums (n/a), so it never zeroes the
    score. Returns (score_pct, breakdown[]). `values` = {source_key: value_or_None} for this rep."""
    num = den = 0.0
    breakdown = []
    for it in items:
        if not it.get("enabled"):
            continue
        src = it.get("source_key")
        val = values.get(src)
        att = attainment(val, it.get("standard"), field_max_by_source.get(src))
        w = _f(it.get("weight"), 1.0)
        entry = {"item_key": it.get("item_key"), "label": it.get("label"), "source_key": src,
                 "value": (None if val is None else round(_f(val), 2)), "standard": it.get("standard"),
                 "standard_type": it.get("standard_type"), "weight": w,
                 "attainment": (None if att is None else round(att * 100, 1)),
                 "met": (None if (att is None or it.get("standard") is None) else att >= 1.0),
                 "weighted": (None if att is None else round(att * w * 100, 2)), "na": att is None}
        breakdown.append(entry)
        if att is not None and w > 0:
            num += att * w
            den += w
    score = round((num / den) * 100, 1) if den > 0 else None
    return score, breakdown


def _field_maxes(items, per_rep_values):
    """Max non-None value per source_key across the cohort (for RELATIVE attainment)."""
    srcs = {it.get("source_key") for it in items}
    out = {}
    for src in srcs:
        best = None
        for rv in per_rep_values.values():
            v = rv.get(src)
            if v is None:
                continue
            fv = _f(v)
            if best is None or fv > best:
                best = fv
        out[src] = best
    return out


def compute_rankings(registry, per_rep_values):
    """Stack ranking — the registry items with count_in_stack_ranker, weighted-attainment scored per rep.
    Deterministic ties: score desc, then rep display-name asc. `per_rep_values` = {rep_canon: {source_key:
    value, '_label': display, '_market': m, '_stores': [..]}}."""
    items = [it for it in registry if it.get("count_in_stack_ranker") and it.get("enabled")]
    fmax = _field_maxes(items, per_rep_values)
    rows = []
    for rep, vals in per_rep_values.items():
        score, breakdown = weighted_score(items, vals, fmax)
        rows.append({"rep": vals.get("_label") or rep, "rep_key": rep,
                     "market": vals.get("_market") or "", "stores": vals.get("_stores") or [],
                     "score": score, "breakdown": breakdown})
    rows.sort(key=lambda r: (-(r["score"] if r["score"] is not None else -1.0), str(r["rep"]).lower()))
    rank = 0
    prev = object()
    for i, r in enumerate(rows):
        if r["score"] != prev:      # standard competition ranking (ties share a rank)
            rank = i + 1
            prev = r["score"]
        r["rank"] = rank
    return {"items": items, "rows": rows}


def compute_review(registry, per_rep_values):
    """Performance review — the registry items with count_in_review, per-rep scorecard vs each item's
    definable standard. Same weighted-attainment core; a missing-source item shows n/a and is excluded
    from the total (never a crash / never a zero that tanks the review)."""
    items = [it for it in registry if it.get("count_in_review") and it.get("enabled")]
    fmax = _field_maxes(items, per_rep_values)
    rows = []
    for rep, vals in per_rep_values.items():
        score, breakdown = weighted_score(items, vals, fmax)
        rows.append({"rep": vals.get("_label") or rep, "rep_key": rep,
                     "market": vals.get("_market") or "", "stores": vals.get("_stores") or [],
                     "review_score": score, "items": breakdown})
    rows.sort(key=lambda r: (-(r["review_score"] if r["review_score"] is not None else -1.0), str(r["rep"]).lower()))
    return {"items": items, "rows": rows}


# ── FEATURE 1 — per-employee productivity vs the store baseline ──────────────────────────────────
def _rate(numer, hours):
    """Output per hour, guarding zero hours (an employee with sales but no punches must NOT divide by
    zero). Returns None when hours <= 0."""
    h = _f(hours)
    if h <= 0:
        return None
    return _f(numer) / h


def compute_productivity(store_reps, hours_by_key):
    """Feature 1. `store_reps` = {(store_code, rep_canon): {store_label, rep_label, boxes, acc_sales,
    market}}. `hours_by_key` = {(store_code, rep_canon): hours}. Returns per-store groups with rep rows,
    each rep's boxes/hr & acc$/hr AND an index vs the STORE's own baseline (store output ÷ store hours).
    Zero-hours reps are surfaced (no_hours) with null rates/index — never a crash, never a fake 0-rate."""
    stores = {}
    for (code, rep), cell in store_reps.items():
        g = stores.get(code)
        if not g:
            g = stores[code] = {"store_code": code, "store_label": cell.get("store_label") or code,
                                "market": cell.get("market") or "", "reps": {},
                                "store_boxes": 0.0, "store_acc": 0.0, "store_hours": 0.0}
        hrs = _f(hours_by_key.get((code, rep), 0.0))
        boxes = _f(cell.get("boxes"))
        acc = _f(cell.get("acc_sales"))
        g["reps"][rep] = {"rep": cell.get("rep_label") or rep, "rep_key": rep,
                          "boxes": round(boxes, 2), "acc_sales": round(acc, 2), "hours": round(hrs, 2)}
        g["store_boxes"] += boxes
        g["store_acc"] += acc
        g["store_hours"] += hrs
    out = []
    tot = {"boxes": 0.0, "acc_sales": 0.0, "hours": 0.0}
    for code, g in stores.items():
        sb_rate = _rate(g["store_boxes"], g["store_hours"])   # store box/hr baseline
        sa_rate = _rate(g["store_acc"], g["store_hours"])     # store acc$/hr baseline
        reps = []
        for rep, r in g["reps"].items():
            bhr = _rate(r["boxes"], r["hours"])
            ahr = _rate(r["acc_sales"], r["hours"])
            r["boxes_per_hr"] = None if bhr is None else round(bhr, 3)
            r["acc_per_hr"] = None if ahr is None else round(ahr, 2)
            r["no_hours"] = r["hours"] <= 0
            r["boxes_index"] = (None if (bhr is None or not sb_rate) else round(bhr / sb_rate, 3))
            r["acc_index"] = (None if (ahr is None or not sa_rate) else round(ahr / sa_rate, 3))
            reps.append(r)
        reps.sort(key=lambda x: (-(x["boxes_per_hr"] if x["boxes_per_hr"] is not None else -1.0), x["rep"].lower()))
        out.append({"store_code": g["store_code"], "store_label": g["store_label"], "market": g["market"],
                    "store_boxes": round(g["store_boxes"], 2), "store_acc": round(g["store_acc"], 2),
                    "store_hours": round(g["store_hours"], 2),
                    "store_boxes_per_hr": None if sb_rate is None else round(sb_rate, 3),
                    "store_acc_per_hr": None if sa_rate is None else round(sa_rate, 2),
                    "reps": reps})
        tot["boxes"] += g["store_boxes"]; tot["acc_sales"] += g["store_acc"]; tot["hours"] += g["store_hours"]
    out.sort(key=lambda s: str(s["store_label"]).lower())
    t_bhr = _rate(tot["boxes"], tot["hours"])
    t_ahr = _rate(tot["acc_sales"], tot["hours"])
    return {"stores": out,
            "totals": {"boxes": round(tot["boxes"], 2), "acc_sales": round(tot["acc_sales"], 2),
                       "hours": round(tot["hours"], 2),
                       "boxes_per_hr": None if t_bhr is None else round(t_bhr, 3),
                       "acc_per_hr": None if t_ahr is None else round(t_ahr, 2)}}


# ── COMMISSION TIE-IN (INERT / OPT-IN) ───────────────────────────────────────────────────────────
PERF_KPI_PREFIX = "perf:"
PERF_SCORE_KEY = "performance_score"


def perf_kpi_keys(registry):
    """The KPI keys this module can expose to a payout engine: the overall performance_score plus one
    'perf:<item_key>' per review item. Registerable in a Commission Plan — INERT until an engine is wired
    to resolve them AND the owner recalcs (owner-gated; see the return)."""
    keys = [{"kpi_key": PERF_SCORE_KEY, "label": "Performance review score", "value_type": "percent"}]
    for it in registry:
        if it.get("count_in_review") and it.get("enabled"):
            keys.append({"kpi_key": PERF_KPI_PREFIX + it["item_key"],
                         "label": f"Perf: {it.get('label')}", "value_type": "percent"})
    return keys


def perf_kpi_value(kpi_key, review_score, review_breakdown):
    """Resolve a performance KPI key → a per-rep numeric value a payout engine COULD reference.
    'performance_score' → the rep's weighted review score; 'perf:<item_key>' → that item's attainment %.
    Returns None if unknown / n/a. Pure — reading it changes no payout on its own."""
    if kpi_key == PERF_SCORE_KEY:
        return review_score
    if kpi_key and kpi_key.startswith(PERF_KPI_PREFIX):
        want = kpi_key[len(PERF_KPI_PREFIX):]
        for b in (review_breakdown or []):
            if b.get("item_key") == want:
                return b.get("attainment")
    return None
