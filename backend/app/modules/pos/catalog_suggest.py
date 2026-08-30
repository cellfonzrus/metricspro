"""POS catalog SUGGESTION engine — powers the "define your catalog" step of the setup wizard.

WHY THIS EXISTS (owner report 2026-08-30)
    "the POs wizard ... won't let me go forward in Vzone and is stuck at 3/8, it does not let you
     add the department or categories or any item ... fix the entire wizard to let the user define
     what they have to define and then also it should get the repopulated suggestions it has learnt
     from the other POS in the system to ask the user which one of the combinations they would like
     to use — the idea is to be smart and make their lives easier not complex."

    The Catalog steps (departments → categories → products) previously offered only two paths:
      1. `import from your sales history` (commcalc.item_mapping) — EMPTY for a store like Vzone that
         was never fed a sales file, so it reported "0 records" and there was nowhere to go; and
      2. a CSV template to download, fill and re-upload elsewhere.
    Neither lets an operator simply type "Phones" and move on, so a fresh tenant got wedged on the
    first Catalog step. This module adds the third path the directive asks for: pickable, pre-built
    catalog COMBINATIONS the operator chooses from, tailored by what MetricsPro already knows about
    THIS store, and enriched by the structural taxonomy other stores in the system already use.

THREE SUGGESTION SOURCES, in increasing specificity
    1. PRESETS        curated cellular-retail catalog shapes (generic, not tenant-specific). This is
                      the reliable floor — a brand-new store with zero data still gets a sensible,
                      complete catalog to pick from. `derive_catalog()` fills each preset's category
                      lists from the tenant's OWN signals where available, so the preset is not a
                      dead constant but adapts to what this store actually sells.
    2. OWN DATA       device models and rate plans this store has ALREADY recorded (pos.activations),
                      turned into concrete department/category/product candidates. Same-org only.
    3. LEARNED        the department/category STRUCTURE other stores in the system settled on,
                      aggregated across orgs and surfaced ONLY as anonymous common taxonomy. See the
                      privacy rule on `learned_taxonomy()` — this is structural labels, never data.

PRIVACY / MULTI-TENANCY
    onboarding.py's import sources are deliberately SAME-ORG ONLY because they copy DATA (products,
    costs, inventory, customer-derived rows) and a cross-org copy would leak one tenant's book into
    another. Catalog STRUCTURE is a different thing: the fact that wireless stores have a "Phones"
    department with an "Accessories" sibling is shared industry taxonomy, not a tenant secret. So the
    LEARNED source crosses orgs, but under a strict rule that keeps it from ever carrying private
    data: it reads ONLY department/category NAME strings (never costs, SKUs, prices, customers or
    counts of a store's private items) and surfaces a (department, category) pair ONLY when it occurs
    in TWO OR MORE distinct orgs — so one store's idiosyncratic private label ("John's clearance
    corner") is never shown to another tenant, while genuinely common taxonomy is. Nothing is
    hard-coded per tenant; the shapes are derived or industry-generic.
"""
import re as _re
from collections import Counter, defaultdict

# ── Deterministic text classifier ────────────────────────────────────────────────────────────────
# Nothing about a tenant is hard-coded here: we read STRUCTURE out of the free-text model/plan/item
# strings a store has recorded, using generic cellular-retail vocabulary. A first-word manufacturer
# and a keyword device-type is exactly how a person would file these, so the machine files them the
# same way and the operator can re-file anything they disagree with.

# Canonical spelling for the makers we see most, so "APPLE"/"apple"/"Apple" collapse to one category.
# An unknown first token is still accepted (title-cased) — the list normalizes, it does not gate.
_MAKER_CANON = {
    "APPLE": "Apple", "SAMSUNG": "Samsung", "GOOGLE": "Google", "MOTOROLA": "Motorola",
    "MOTO": "Motorola", "KYOCERA": "Kyocera", "TCL": "TCL", "NOKIA": "Nokia", "LG": "LG",
    "ONEPLUS": "OnePlus", "SONY": "Sony", "CELERO": "Celero", "SCHOK": "Schok", "BLU": "BLU",
    "ALCATEL": "Alcatel", "ZTE": "ZTE", "CAT": "CAT", "HMD": "HMD", "REVVL": "TCL",
}

# device-type from a keyword anywhere in the model string → (category, department, system_category)
_TYPE_RULES = [
    (_re.compile(r"\b(ipad|tab\b|tablet|galaxy tab|mediapad)\b", _re.I), "Tablets"),
    (_re.compile(r"\b(watch|band|wearable|gizmo)\b", _re.I), "Wearables"),
    (_re.compile(r"\b(hotspot|jetpack|mifi|inseego|hub|orbic speed|gateway)\b", _re.I), "Hotspots"),
    (_re.compile(r"\b(flip|razr|duraxv|dura\b|gusto|jitterbug|coolpad snap|basic|classic|"
                 r"go flip|convoy)\b", _re.I), "Basic Phones"),
]
_SMARTPHONE_HINTS = _re.compile(
    r"\b(iphone|galaxy [saz]\d|pixel|moto [ge]|revvl|celero|stylo|oneplus|edge|note\d?|ultra|"
    r"pro max|blade|axon|nord)\b", _re.I)

# accessory taxonomy — keyword → category. Used to bucket free-text non-phone item descriptions.
_ACCESSORY_RULES = [
    (_re.compile(r"\b(case|cover|otterbox|folio|bumper|pouch)\b", _re.I), "Cases"),
    (_re.compile(r"\b(screen|tempered|glass|protector|protection)\b", _re.I), "Screen Protection"),
    (_re.compile(r"\b(charger|cable|adapter|cord|power ?bank|wall|car charge|usb|type-?c|lightning)\b",
                 _re.I), "Chargers & Cables"),
    (_re.compile(r"\b(earbud|headphone|headset|speaker|airpod|audio|buds)\b", _re.I), "Audio"),
    (_re.compile(r"\b(mount|holder|stand|grip|popsocket|ring)\b", _re.I), "Mounts & Holders"),
    (_re.compile(r"\b(sim|e-?sim|sim card)\b", _re.I), "SIM Cards"),
]
_PLAN_HINT = _re.compile(r"\b(plan|unlimited|prepaid|gb\b|byod|activation|rate plan|monthly|"
                         r"line|talk|text|data)\b", _re.I)


def _maker(model: str) -> str:
    tok = (model or "").strip().split()
    if not tok:
        return ""
    head = tok[0].upper().strip(".,")
    return _MAKER_CANON.get(head, tok[0].title() if head.isalpha() else "")


def _device_type(model: str) -> str:
    m = model or ""
    for rx, label in _TYPE_RULES:
        if rx.search(m):
            return label
    if _SMARTPHONE_HINTS.search(m):
        return "Smartphones"
    return "Smartphones"   # a wireless store's default device is a smartphone


def derive_catalog(device_names, plan_names=None, product_names=None) -> dict:
    """PURE. From free-text signals a store has recorded, derive the structural pieces a catalog
    needs. No I/O, so it is unit-tested offline against synthetic strings.

    Returns the raw building blocks (ranked, de-duplicated) that `build_presets()` assembles into
    pickable combinations:
        manufacturers  makers seen, most-frequent first     (e.g. ['Apple','Samsung'])
        device_types   device buckets seen                  (e.g. ['Smartphones','Basic Phones'])
        accessory_cats accessory categories seen in items   (e.g. ['Cases','Chargers & Cables'])
        has_plans      the store has recorded rate plans
        devices        distinct model strings (product candidates), most-frequent first
    """
    device_names = [d for d in (device_names or []) if (d or "").strip()]
    plan_names = [p for p in (plan_names or []) if (p or "").strip()]
    product_names = [p for p in (product_names or []) if (p or "").strip()]

    makers, types, dev_counter = Counter(), Counter(), Counter()
    for d in device_names:
        d = d.strip()
        dev_counter[d[:120]] += 1
        mk = _maker(d)
        if mk:
            makers[mk] += 1
        types[_device_type(d)] += 1

    acc = Counter()
    for p in product_names:
        for rx, label in _ACCESSORY_RULES:
            if rx.search(p):
                acc[label] += 1
                break

    has_plans = bool(plan_names) or any(_PLAN_HINT.search(p) for p in product_names)

    return {
        "manufacturers": [m for m, _ in makers.most_common()],
        "device_types": [t for t, _ in types.most_common()],
        "accessory_cats": [a for a, _ in acc.most_common()],
        "has_plans": has_plans,
        "devices": [d for d, _ in dev_counter.most_common(200)],
        "plans": sorted({p.strip()[:120] for p in plan_names})[:200],
    }


# ── Preset library (generic cellular-retail catalog shapes) ───────────────────────────────────────
# Each preset is a complete, pickable catalog: departments, the categories under each, and the
# system_category the register uses. `build_presets()` personalises the two device-facing categories
# from `derive_catalog()` so a store that only sells Apple/Samsung is not handed a Motorola category
# it will never use — but the SHAPE is industry-generic, not tenant-specific.
_DEFAULT_ACCESSORY_CATS = ["Cases", "Screen Protection", "Chargers & Cables", "Audio", "SIM Cards"]


def _dept(short, full, system_category, categories):
    return {"short_name": short, "full_name": full, "system_category": system_category,
            "categories": [c for c in categories if c]}


def build_presets(derived: dict) -> list:
    """Assemble the pickable combinations. Returns a list of presets; each has an id, a label, a
    one-line why, and a concrete department/category tree ready to apply."""
    d = derived or {}
    makers = d.get("manufacturers") or []
    types = d.get("device_types") or []
    acc = d.get("accessory_cats") or []
    accessory_cats = acc or _DEFAULT_ACCESSORY_CATS

    # phone sub-categories: use the device TYPES the store actually sells, else a sensible default
    phone_cats = [t for t in types if t in ("Smartphones", "Basic Phones")] or ["Smartphones",
                                                                               "Basic Phones"]
    tab_cats = [t for t in ["Tablets", "Wearables", "Hotspots"] if t in types] or ["Tablets",
                                                                                   "Wearables"]
    maker_cats = (makers or ["Apple", "Samsung", "Google", "Motorola"])[:8] + ["Other"]

    services = _dept("Plans & Services", "Plans & Services", "Service",
                     ["Rate Plans", "Activations & Upgrades", "Protection / Insurance"])

    by_type = {
        "id": "by_type",
        "label": "By product type",
        "why": "Departments by what a thing IS — Phones, Tablets & Wearables, Accessories, Services. "
               "The most common shape for a wireless store; a cashier browses to the type, then the "
               "item.",
        "departments": [
            _dept("Phones", "Phones", "Cell Phone", phone_cats),
            _dept("Tablets & Wearables", "Tablets & Wearables", "Cell Phone", tab_cats),
            _dept("Accessories", "Accessories", "Accessory", accessory_cats),
            services,
        ],
    }
    by_maker = {
        "id": "by_maker",
        "label": "By manufacturer",
        "why": "Phones grouped by brand (Apple, Samsung, …) — handy if you shop and report by "
               "manufacturer. Accessories and services stay by type.",
        "departments": [
            _dept("Phones", "Phones", "Cell Phone", maker_cats),
            _dept("Accessories", "Accessories", "Accessory", accessory_cats),
            services,
        ],
    }
    simple = {
        "id": "simple",
        "label": "Keep it simple",
        "why": "Three departments only — Phones, Accessories, Services. Start here and split later; "
               "you can add categories any time.",
        "departments": [
            _dept("Phones", "Phones", "Cell Phone", ["Smartphones"]),
            _dept("Accessories", "Accessories", "Accessory", ["General Accessories"]),
            _dept("Plans & Services", "Plans & Services", "Service", ["Rate Plans"]),
        ],
    }
    return [by_type, by_maker, simple]


# ── LEARNED taxonomy: structural labels common across stores in the system ────────────────────────
def rank_learned(dept_rows, cat_rows) -> list:
    """PURE. Given department rows [{org_id, short_name}] and category rows
    [{org_id, name, department}] gathered ACROSS orgs, return the (department → categories) structure
    that is COMMON — i.e. each surfaced label occurs in ≥2 distinct orgs. Anonymous aggregate only:
    no org is named, no counts of any store's private items, just "N setups use this".

    This is the privacy rule that lets the suggestion cross orgs safely (see module docstring):
    a label one single store invented never reaches ≥2 orgs, so it is never suggested elsewhere.
    """
    dept_orgs = defaultdict(set)
    for r in dept_rows or []:
        name = (r.get("short_name") or "").strip()
        org = r.get("org_id")
        if name and org:
            dept_orgs[name].add(org)

    # category → set(orgs), and category → the department name it hangs under (most common)
    cat_orgs = defaultdict(set)
    cat_dept = defaultdict(Counter)
    for r in cat_rows or []:
        name = (r.get("name") or "").strip()
        org = r.get("org_id")
        if not (name and org):
            continue
        cat_orgs[name].add(org)
        dep = (r.get("department") or "").strip()
        if dep:
            cat_dept[name][dep] += 1

    common_depts = {n for n, orgs in dept_orgs.items() if len(orgs) >= 2}
    out = defaultdict(list)
    # attach each common category to its most-common (also-common) department
    for cat, orgs in cat_orgs.items():
        if len(orgs) < 2:
            continue
        dep = next((d for d, _ in cat_dept[cat].most_common() if d in common_depts), None)
        if not dep:
            continue
        out[dep].append({"name": cat, "orgs": len(orgs)})

    # every common department shows, even if it has no (yet-common) categories
    result = []
    for dep in sorted(common_depts, key=lambda d: -len(dept_orgs.get(d, ()))):
        cats = sorted(out.get(dep, []), key=lambda c: -c["orgs"])
        result.append({"department": dep, "orgs": len(dept_orgs.get(dep, ())),
                       "categories": [c["name"] for c in cats]})
    return result


# ══════════════════════════════════════════════════════════════════════════════════════════════
# I/O layer — reads live data and assembles the suggestion payload. The functions above are pure so
# the classification and ranking are unit-tested offline; everything below is thin glue over the DB.
# ══════════════════════════════════════════════════════════════════════════════════════════════
def _page(client, schema, table, cols, org_id=None, cap=20000):
    """Paged select. org_id=None reads across orgs (LEARNED taxonomy only — never for data)."""
    out, page = [], 0
    while page * 1000 < cap:
        q = client.schema(schema).table(table).select(cols)
        if org_id is not None:
            q = q.eq("org_id", org_id)
        rows = q.range(page * 1000, page * 1000 + 999).execute().data or []
        out.extend(rows)
        if len(rows) < 1000:
            break
        page += 1
    return out


def own_signals(client, org_id: str) -> dict:
    """Free-text this store has ALREADY recorded: device models + rate plans from pos.activations.
    Same-org. Best-effort — a store with no activations yet just yields empty lists and the presets
    carry the experience."""
    devices, plans = [], []
    try:
        rows = _page(client, "pos", "activations", "phone_model,plan_description", org_id, cap=20000)
        devices = [r.get("phone_model") for r in rows if (r.get("phone_model") or "").strip()]
        plans = [r.get("plan_description") for r in rows if (r.get("plan_description") or "").strip()]
    except Exception:
        pass
    return {"devices": devices, "plans": plans, "products": []}


def learned_taxonomy(client, exclude_org: str = "") -> list:
    """Common department/category STRUCTURE across every store in the system (see module docstring
    and `rank_learned`'s privacy rule). Reads NAMES only, across orgs, and surfaces a label only when
    it is shared by ≥2 orgs. Best-effort: any read problem yields an empty list, never an error."""
    try:
        depts = _page(client, "pos", "departments", "org_id,short_name", None, cap=20000)
        cats_raw = _page(client, "pos", "categories", "org_id,name,department_id", None, cap=40000)
        # resolve each category's department NAME within its own org (never cross-org)
        dept_by_id = {(d.get("org_id"), _id(d)): (d.get("short_name") or "").strip()
                      for d in depts if _id(d)}
        cat_rows = [{"org_id": c.get("org_id"), "name": c.get("name"),
                     "department": dept_by_id.get((c.get("org_id"), c.get("department_id")), "")}
                    for c in cats_raw]
        if exclude_org:
            depts = [d for d in depts if d.get("org_id") != exclude_org]
            cat_rows = [c for c in cat_rows if c.get("org_id") != exclude_org]
        return rank_learned(depts, cat_rows)
    except Exception:
        return []


def _id(row):
    return row.get("id")


def build_suggestions(client, org_id: str) -> dict:
    """The whole suggestion payload the wizard renders: what the store already has, personalised
    presets, product candidates from its own devices, and the common taxonomy other stores use."""
    have = {"departments": [], "categories": []}
    try:
        have["departments"] = [{"id": d.get("id"), "short_name": d.get("short_name")}
                               for d in _page(client, "pos", "departments",
                                              "id,short_name", org_id, cap=2000)]
        have["categories"] = [{"id": c.get("id"), "name": c.get("name"),
                               "department_id": c.get("department_id")}
                              for c in _page(client, "pos", "categories",
                                             "id,name,department_id", org_id, cap=5000)]
    except Exception:
        pass

    sig = own_signals(client, org_id)
    derived = derive_catalog(sig["devices"], sig["plans"], sig["products"])
    presets = build_presets(derived)
    learned = learned_taxonomy(client, exclude_org=org_id)

    return {
        "have": have,
        "presets": presets,
        "learned": learned,
        "derived": {
            "manufacturers": derived["manufacturers"],
            "device_types": derived["device_types"],
            "devices": derived["devices"][:60],   # product candidates for the Products step
            "plans": derived["plans"][:60],
            "has_own_data": bool(sig["devices"] or sig["plans"]),
        },
    }


def apply_suggestion(client, org_id: str, departments=None, categories=None,
                     system_categories=None) -> dict:
    """Create the picked departments / categories / system-categories, IDEMPOTENTLY: a name that
    already exists (case-insensitive) is skipped, never duplicated or overwritten, so re-applying a
    preset or mixing two of them is safe. Categories are attached to their department by NAME, using
    whatever already exists plus whatever this call creates.

    departments: [{short_name, full_name?, system_category?}]
    categories:  [{name, department?}]   department = the department short_name to file it under
    system_categories: [name, ...]
    """
    departments = departments or []
    categories = categories or []
    system_categories = list(system_categories or [])
    created = {"departments": 0, "categories": 0, "system_categories": 0}

    # ── system categories first: a product needs its system_category to already exist ─────────────
    # collect the ones named anywhere in this payload so "By type" (Cell Phone/Accessory/Service)
    # works even if the org only had the four builtins.
    for dep in departments:
        sc = (dep.get("system_category") or "").strip()
        if sc:
            system_categories.append(sc)
    have_sc = _existing_lower(client, "system_categories", "name", org_id)
    seen_sc = set()
    for name in system_categories:
        n = (name or "").strip()
        if not n or n.lower() in have_sc or n.lower() in seen_sc:
            continue
        seen_sc.add(n.lower())
        try:
            client.schema("pos").table("system_categories").insert(
                {"org_id": org_id, "name": n, "is_builtin": False}).execute()
            created["system_categories"] += 1
        except Exception:
            pass

    # ── departments ───────────────────────────────────────────────────────────────────────────────
    have_dep = _existing_lower(client, "departments", "short_name", org_id)
    for dep in departments:
        name = (dep.get("short_name") or "").strip()
        if not name or name.lower() in have_dep:
            continue
        try:
            client.schema("pos").table("departments").insert(
                {"org_id": org_id, "short_name": name[:60],
                 "full_name": (dep.get("full_name") or name)[:120]}).execute()
            have_dep.add(name.lower())
            created["departments"] += 1
        except Exception:
            pass

    # department name → id (re-read so freshly-created ones resolve)
    dep_ids = {(d.get("short_name") or "").strip().lower(): d.get("id")
               for d in _page(client, "pos", "departments", "id,short_name", org_id, cap=2000)}

    # departments named implicitly by a category's `department` but not in the departments list
    for cat in categories:
        dep = (cat.get("department") or "").strip()
        if dep and dep.lower() not in dep_ids:
            try:
                client.schema("pos").table("departments").insert(
                    {"org_id": org_id, "short_name": dep[:60], "full_name": dep[:120]}).execute()
                created["departments"] += 1
            except Exception:
                pass
    if any((c.get("department") or "").strip().lower() not in dep_ids for c in categories):
        dep_ids = {(d.get("short_name") or "").strip().lower(): d.get("id")
                   for d in _page(client, "pos", "departments", "id,short_name", org_id, cap=2000)}

    # ── categories ────────────────────────────────────────────────────────────────────────────────
    have_cat = _existing_lower(client, "categories", "name", org_id)
    for cat in categories:
        name = (cat.get("name") or "").strip()
        if not name or name.lower() in have_cat:
            continue
        try:
            client.schema("pos").table("categories").insert(
                {"org_id": org_id, "name": name[:80],
                 "department_id": dep_ids.get((cat.get("department") or "").strip().lower())}).execute()
            have_cat.add(name.lower())
            created["categories"] += 1
        except Exception:
            pass

    return {"created": created}


def _existing_lower(client, table, col, org_id) -> set:
    try:
        return {(r.get(col) or "").strip().lower()
                for r in _page(client, "pos", table, col, org_id, cap=5000)
                if (r.get(col) or "").strip()}
    except Exception:
        return set()

