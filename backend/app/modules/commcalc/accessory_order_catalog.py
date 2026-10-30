"""THE ACCESSORY CATALOG — the kinds of accessory that can be ordered, and how each one is chosen
(§COMMCALC-49.12). The ONE home for the ordering vocabulary.

OWNER ASK (2026-10-30, verbatim): *"scren protectors are clear and privacy, cases are silicon ,
kicstand, drop proof heavy duty, flowers, diamonds etc and option to add more with pictures ,
chargers could be single usb or dual usb for both type a and type c or dual with type a and type c
in one, the chargers could be 5 w , 18 w , 20 w, 45 w , 60 w, 100w , the earphones could be iphone
compatible , 3.5mm or type c compatible"* — and, on cables: *"the cables are common to a lot of
phones, current standard is type c which should be assigned ot the phone model in the beinging but
it should be editable as a drop down if oit changes later"*.

════════════════════════════════════════════════════════════════════════════════════════════════════
THE ONE DISTINCTION THIS FILE EXISTS TO HOLD: SPLIT vs ASSIGNED
════════════════════════════════════════════════════════════════════════════════════════════════════
The owner's two sentences describe two different kinds of accessory, and conflating them is what
makes an order sheet useless:

  · **SPLIT** — a case and a screen protector are bought FOR A PHONE, and the quantity for that
    phone divides across the styles on offer. Silicone / kickstand / heavy duty are a MIX, the way
    the colours are a mix. So these belong per phone model, in columns.
  · **ASSIGNED** — a cable, a charging cube and a pair of earphones are bought BY CONNECTOR, and
    *"the cables are common to a lot of phones"*. Each phone is ASSIGNED one connector (house:
    whatever the declared current standard is), and the order consolidates across every phone that
    takes the same one. Splitting a cable order per phone would hand a buyer thirty rows of the same
    cable.

That is the whole shape: `grain='split'` divides one phone's quantity across a dimension's options;
`grain='assigned'` takes ONE option per phone and sums across phones.

════════════════════════════════════════════════════════════════════════════════════════════════════
DUPLICATE CHECK (build gate, CLAUDE.md) — WHAT WAS SEARCHED AND WHAT IS REUSED
════════════════════════════════════════════════════════════════════════════════════════════════════
Searched the index and `commcalc.accessory_config`'s own columns for an existing home for "what kinds
of accessory are there" before writing: §19.55 (`accessory_config.categories` /
`catalog_accessory_categories` / `product_keywords`), §61 (Accessory Target Allocation), §COMMCALC-49
(the order plan's `attach_plan`), §36 (`asset/purchase_orders` and its vendor catalogue), §51 (the
vendor order route).

**`commcalc/accessory_catalog.py` AND `accessory_config.categories` ANSWER A DIFFERENT QUESTION AND
ARE DELIBERATELY NOT REUSED — and the near-miss is recorded here on purpose.** That module (migs
230/231, §19.55) is the catalog-driven classification of a SOLD line: it answers *"was this sale an
accessory, and which accessory category did the tenant file it under"*, from an uploaded product
catalog keyed by product id or UPC. This file answers *"what kinds of accessory can be BOUGHT, and
what does a buyer pick between"* — styles, types, wattages, connectors. One looks backwards at what
was sold and the other forwards at what to order, and the live config shows why they cannot be one
list: one tenant's `categories` holds `Screen protectors`, `Cases`, `Earphones`, `Speakers`, the
spellings of its own POS, with no style, no wattage and no connector in them.

The names are one word apart, which is exactly how a sibling derivation gets born, so this module is
`accessory_ORDER_catalog` and the relationship is written into the index (§COMMCALC-49.12) rather
than left for the next reader to re-derive.

**REUSED:** `normalise_shares` below is the share-reading rule, and `accessory_order_plan`'s colour
mix now DEREFERENCES it rather than keeping the second copy it shipped with — the two mixes read a
declared percentage identically, so there is one rule for it.

════════════════════════════════════════════════════════════════════════════════════════════════════
RULE TWO, AND WHY A HOUSE DEFAULT IS RIGHT HERE
════════════════════════════════════════════════════════════════════════════════════════════════════
CLAUDE.md bans a house default that is really one tenant's VOCABULARY — the words a tenant's own feed
uses, like its POS department names. This catalog is the opposite kind of thing: `silicone`,
`kickstand`, `clear`, `privacy`, `20w`, `usb_c` are the generic names of a kind of product, they are
read against NO tenant's feed, and the same list is right for every tenant on the platform. So the
house ships a starting catalog and every org may extend or replace any part of it — which is the
`etc`/*"option to add more with pictures"* half of the ask.

No carrier, tenant, brand or product name appears in this file, and a test asserts it over the AST.

PURE. Every input is handed in; no table, no router, no clock.
"""
from __future__ import annotations

#: How a category's quantity is decided. See the header — this is the file's load-bearing idea.
GRAIN_SPLIT = "split"
GRAIN_ASSIGNED = "assigned"
GRAINS = (GRAIN_SPLIT, GRAIN_ASSIGNED)

#: Where a set of shares came from.
SHARE_BASES = ("even_split", "declared", "declared_normalised")
#: Where a model's assigned option came from.
SPEC_BASES = ("declared", "house_default")


def _num(v, default=0.0):
    try:
        return float(str(v).replace(",", "").replace("%", "").strip())
    except (TypeError, ValueError):
        return default


# ══════════════════════════════════════════════════════════════════════════════════════════════════
# THE HOUSE CATALOG — the owner's own list, in generic words
# ══════════════════════════════════════════════════════════════════════════════════════════════════
# `per_device` is the attach rate: how many of this kind per phone sold. `None` means NOT ORDERED —
# a rate nobody has declared, which is reported as a dash with its reason rather than as a zero
# (the same honesty rule the rest of this report rests on). The owner named earphones as a kind of
# thing to carry but gave no rate, so earphones arrive un-rated rather than invented at one each.
HOUSE_CATALOG = (
    {"key": "case", "label": "Case", "grain": GRAIN_SPLIT, "per_device": 1.0,
     "colour_split": True,
     "dimensions": ({"key": "style", "label": "Style", "options": (
         {"key": "silicone", "label": "Silicone"},
         {"key": "kickstand", "label": "Kickstand"},
         {"key": "heavy_duty", "label": "Drop proof / heavy duty"},
         {"key": "floral", "label": "Floral"},
         {"key": "diamond", "label": "Diamond"},
     )},)},
    {"key": "screen_protector", "label": "Screen protector", "grain": GRAIN_SPLIT,
     "per_device": 1.0, "colour_split": False,
     "dimensions": ({"key": "type", "label": "Type", "options": (
         {"key": "clear", "label": "Clear"},
         {"key": "privacy", "label": "Privacy"},
     )},)},
    {"key": "cable", "label": "Cable", "grain": GRAIN_ASSIGNED, "per_device": 1.0,
     "colour_split": False,
     "dimensions": ({"key": "connector", "label": "Connector", "default": "usb_c_to_usb_c",
                     "options": (
         {"key": "usb_c_to_usb_c", "label": "USB-C to USB-C"},
         {"key": "usb_c_to_lightning", "label": "USB-C to Lightning"},
         {"key": "usb_a_to_usb_c", "label": "USB-A to USB-C"},
         {"key": "usb_a_to_lightning", "label": "USB-A to Lightning"},
     )},)},
    {"key": "charger", "label": "Charging cube", "grain": GRAIN_ASSIGNED, "per_device": 1.0,
     "colour_split": False,
     "dimensions": (
         {"key": "ports", "label": "Ports", "default": "single_usb_c", "options": (
             {"key": "single_usb_a", "label": "Single USB-A"},
             {"key": "single_usb_c", "label": "Single USB-C"},
             {"key": "dual_usb_a", "label": "Dual USB-A"},
             {"key": "dual_usb_c", "label": "Dual USB-C"},
             {"key": "dual_usb_a_and_c", "label": "Dual, one USB-A and one USB-C"},
         )},
         {"key": "wattage", "label": "Wattage", "default": "20w", "options": (
             {"key": "5w", "label": "5 W"},
             {"key": "18w", "label": "18 W"},
             {"key": "20w", "label": "20 W"},
             {"key": "45w", "label": "45 W"},
             {"key": "60w", "label": "60 W"},
             {"key": "100w", "label": "100 W"},
         )},
     )},
    {"key": "earphones", "label": "Earphones", "grain": GRAIN_ASSIGNED, "per_device": None,
     "colour_split": False,
     "dimensions": ({"key": "fit", "label": "Fit", "default": "usb_c", "options": (
         {"key": "usb_c", "label": "USB-C"},
         {"key": "lightning", "label": "Lightning"},
         {"key": "jack_3_5mm", "label": "3.5 mm jack"},
     )},)},
)

#: Which product classes (§62's classifier) are worth ordering accessories for. A POLICY, not a
#: vocabulary: a router and a SIM kit take no case, and counting them over-orders. House default
#: measured against the live feed — one tenant's September had 188 such units inside its own device
#: departments, which would have bought 188 cases for routers and SIM cards.
HOUSE_ACCESSORY_DEVICE_CLASSES = ("phone", "tablet")


# ══════════════════════════════════════════════════════════════════════════════════════════════════
# THE SHARE-READING RULE (ONE home — `accessory_order_plan`'s colour mix reads it too)
# ══════════════════════════════════════════════════════════════════════════════════════════════════
def normalise_shares(rows, *, fallback=None):
    """Declared `[{key, label, share}, ...]` → `(rows summing to 1.0, basis)`. PURE.

    Shares may be declared as percentages (70) or as fractions (0.7); which was meant is decided by
    the TOTAL over the whole declaration, not per row, so `70 / 30` and `0.7 / 0.3` cannot be read
    differently. A total that is not 100% is normalised and the basis SAYS so — a missing tenth is a
    typo, not a group nobody named.

    Nothing usable declared → `fallback` if one was given (already-shared rows), else an EVEN split
    over `fallback`-less input is the caller's business: this returns `([], 'even_split')` so the
    caller can decide, which is why `even_split_over` exists beside it."""
    out = []
    if rows is not None and not isinstance(rows, (str, bytes)) and hasattr(rows, "__iter__"):
        for r in rows:
            if not isinstance(r, dict):
                continue
            key = str(r.get("key") or "").strip().lower()
            share = _num(r.get("share"), 0.0)
            if not key or share <= 0:
                continue
            item = {"key": key, "label": str(r.get("label") or key).strip(), "share": share}
            if r.get("image_url"):
                item["image_url"] = str(r.get("image_url")).strip()
            out.append(item)
    if not out:
        if fallback:
            return tuple(dict(f) for f in fallback), "even_split"
        return (), "even_split"
    total = sum(r["share"] for r in out)
    if total > 1.5:                      # percentages, decided once over the whole declaration
        total /= 100.0
        for r in out:
            r["share"] /= 100.0
    basis = "declared" if abs(total - 1.0) <= 0.005 else "declared_normalised"
    for r in out:
        r["share"] = r["share"] / total if total > 0 else 0.0
    return tuple(out), basis


def even_split_over(options):
    """Options → the same options carrying an EVEN share each. PURE.

    An even split is a POLICY starting point, not a measurement, and every caller labels it
    `even_split` so the screen can say "declare your own mix" rather than present a guess as a
    finding. The last share absorbs the rounding so the shares sum to exactly 1.0."""
    opts = [o for o in (options or ()) if isinstance(o, dict) and str(o.get("key") or "").strip()]
    n = len(opts)
    if not n:
        return ()
    each = 1.0 / n
    out = []
    for i, o in enumerate(opts):
        item = {"key": str(o["key"]).strip().lower(),
                "label": str(o.get("label") or o["key"]).strip(),
                "share": (1.0 - each * (n - 1)) if i == n - 1 else each}
        if o.get("image_url"):
            item["image_url"] = str(o["image_url"]).strip()
        out.append(item)
    return tuple(out)


# ══════════════════════════════════════════════════════════════════════════════════════════════════
# THE DECLARATIONS
# ══════════════════════════════════════════════════════════════════════════════════════════════════
def _options(raw, house=()):
    out = []
    seen = set()
    if raw is not None and not isinstance(raw, (str, bytes)) and hasattr(raw, "__iter__"):
        for o in raw:
            if not isinstance(o, dict):
                continue
            key = str(o.get("key") or "").strip().lower()
            if not key or key in seen:
                continue
            seen.add(key)
            item = {"key": key, "label": str(o.get("label") or key).strip()}
            if o.get("image_url"):
                item["image_url"] = str(o["image_url"]).strip()
            out.append(item)
    return tuple(out) if out else tuple(dict(o) for o in house)


def _dimensions(raw, house=()):
    by_key = {d["key"]: d for d in house}
    out = []
    if raw is not None and not isinstance(raw, (str, bytes)) and hasattr(raw, "__iter__"):
        for d in raw:
            if not isinstance(d, dict):
                continue
            key = str(d.get("key") or "").strip().lower()
            if not key:
                continue
            h = by_key.get(key, {})
            opts = _options(d.get("options"), h.get("options", ()))
            dim = {"key": key, "label": str(d.get("label") or h.get("label") or key).strip(),
                   "options": opts}
            default = str(d.get("default") or h.get("default") or "").strip().lower()
            dim["default"] = default if any(o["key"] == default for o in opts) else (
                opts[0]["key"] if opts else "")
            out.append(dim)
    if out:
        return tuple(out)
    return tuple({"key": d["key"], "label": d["label"],
                  "options": tuple(dict(o) for o in d["options"]),
                  "default": str(d.get("default") or (d["options"][0]["key"] if d["options"] else ""))}
                 for d in house)


def resolve_catalog(raw=None):
    """An org's declared catalog → `(categories, basis)`. PURE.

    A declared category REPLACES the house one of the same key, field by field, so an org can add a
    case style without restating the wattages. A key the house does not know is added as declared —
    that is the *"option to add more"* half of the ask, and it is why nothing here is a closed enum.
    Missing / junk → the house catalog unchanged."""
    house = {c["key"]: c for c in HOUSE_CATALOG}
    if raw is None or isinstance(raw, (str, bytes)) or not hasattr(raw, "__iter__"):
        return tuple(_category(dict(c), house) for c in HOUSE_CATALOG), "house_default"
    declared, seen = [], set()
    for c in raw:
        if not isinstance(c, dict):
            continue
        key = str(c.get("key") or "").strip().lower()
        if not key or key in seen:
            continue
        seen.add(key)
        declared.append(_category(c, house))
    if not declared:
        return tuple(_category(dict(c), house) for c in HOUSE_CATALOG), "house_default"
    # A house category the org did not mention is KEPT, so extending the catalog cannot silently
    # delete the one kind the declaration forgot to restate.
    for c in HOUSE_CATALOG:
        if c["key"] not in seen:
            declared.append(_category(dict(c), house))
    return tuple(declared), "declared"


def _category(c, house):
    key = str(c.get("key") or "").strip().lower()
    h = house.get(key, {})
    grain = str(c.get("grain") or h.get("grain") or GRAIN_SPLIT).strip().lower()
    if grain not in GRAINS:
        grain = GRAIN_SPLIT
    per = c.get("per_device", h.get("per_device", 1.0))
    per = None if per is None else max(0.0, _num(per, 0.0))
    split = c.get("colour_split", h.get("colour_split", False))
    return {"key": key, "label": str(c.get("label") or h.get("label") or key).strip(),
            "grain": grain, "per_device": per, "colour_split": bool(split),
            "dimensions": _dimensions(c.get("dimensions"), h.get("dimensions", ()))}


def resolve_option_mix(category, raw=None):
    """For a SPLIT category: `(shares, basis)` over its first dimension's options. PURE.

    Nothing declared → an EVEN split with `basis='even_split'`, which the screen states. An even
    split is not a measurement and the report never pretends otherwise; it is there so the grid
    reads as a usable starting sheet instead of a wall of dashes."""
    dims = (category or {}).get("dimensions") or ()
    opts = dims[0]["options"] if dims else ()
    rows, basis = normalise_shares(raw, fallback=even_split_over(opts))
    if basis == "even_split":
        return even_split_over(opts), "even_split"
    # A declared mix may only name options the catalog knows, else a column would appear with no
    # product behind it.
    known = {o["key"] for o in opts}
    kept = [r for r in rows if r["key"] in known]
    if not kept:
        return even_split_over(opts), "even_split"
    total = sum(r["share"] for r in kept)
    for r in kept:
        r["share"] = r["share"] / total if total > 0 else 0.0
    return tuple(kept), basis


def default_spec(category):
    """For an ASSIGNED category: `{dim_key: option_key}` as the house standard. PURE."""
    return {d["key"]: d.get("default") or (d["options"][0]["key"] if d["options"] else "")
            for d in ((category or {}).get("dimensions") or ())}


def resolve_model_specs(raw=None):
    """Declared per-model assignments → `{model_key_lower: {category: {dim: option}}}`. PURE.

    This is the *"assigned ot the phone model in the beinging but ... editable as a drop down"* half
    of the ask: the house standard applies until somebody says otherwise for one phone, and what
    they said is stored per model rather than per order."""
    out: dict = {}
    if raw is None or not isinstance(raw, (list, tuple)):
        return out
    for r in raw:
        if not isinstance(r, dict):
            continue
        model = " ".join(str(r.get("model") or "").split()).lower()
        cat = str(r.get("category") or "").strip().lower()
        dim = str(r.get("dimension") or "").strip().lower()
        opt = str(r.get("option") or "").strip().lower()
        if not (model and cat and dim and opt):
            continue
        out.setdefault(model, {}).setdefault(cat, {})[dim] = opt
    return out


def spec_for(model, category, specs=None):
    """`({dim: option}, basis)` for one model and one ASSIGNED category. PURE.

    A declared option that the catalog does not offer is IGNORED rather than honoured, so deleting a
    connector from the catalog cannot leave an order line nobody can buy."""
    house = default_spec(category)
    key = " ".join(str(model or "").split()).lower()
    declared = ((specs or {}).get(key) or {}).get(str((category or {}).get("key") or ""), {})
    offered = {d["key"]: {o["key"] for o in d["options"]} for d in (category or {}).get("dimensions", ())}
    out, basis = dict(house), "house_default"
    for dim, opt in (declared or {}).items():
        if dim in offered and opt in offered[dim]:
            out[dim] = opt
            basis = "declared"
    return out, basis


def option_label(category, dim_key, option_key):
    """The reader's words for one option. PURE."""
    for d in (category or {}).get("dimensions") or ():
        if d["key"] == dim_key:
            for o in d["options"]:
                if o["key"] == option_key:
                    return o["label"]
    return str(option_key or "")


def spec_label(category, spec):
    """An assigned spec → one human line, e.g. 'Single USB-C · 20 W'. PURE."""
    parts = [option_label(category, d["key"], (spec or {}).get(d["key"]))
             for d in (category or {}).get("dimensions") or ()]
    return " · ".join(p for p in parts if p)
