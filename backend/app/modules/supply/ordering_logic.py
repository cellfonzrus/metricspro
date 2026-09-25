"""SUPPLY ORDERING — the pure decisions (index §36). No DB, no network, no browser, no app imports.

Everything that decides money or truth in the supply-ordering module lives here so it is proven DB-free by
`backend/harness_supply_ordering.py`; router.py / portal.py / catalog_store.py are HTTP, browser and I/O.

  VENDOR SETUP   validate_vendor · normalize_portal_config · vendor_hosts · vendor_scrape_config
  CATALOG        normalize_catalog_row(s) · item_key · parse_products_upload · map_kit_vendor · offer_from_row
  CART           order_packs · optimize_cart (cheapest plan INCLUDING each vendor's free-shipping threshold,
                 lead-time constraint, stock, pack basis) · po_drafts_from_plan
  ORDER SESSION  parse_recipe · render_step · url_allowed · recipe_status · parse_cart_total · detect_confirmation
  ATTENTION      vendor_attention · summary_tiles

RULE TWO: no vendor, host, selector or wording of any one vendor appears here. Per-vendor behaviour is the
vendor's `portal_config` (a vendors.json block + an optional `ordering` recipe) and its tenant-defined
free_shipping_threshold / shipping_fee_below_threshold / delivery_days_min / delivery_days_max columns.
The generic defaults below (confirmation wording, cart-total wording) are house vocabulary a vendor's config
overrides — never a branch on who the vendor is.

The pricing/matching primitives are NOT re-implemented: they are THE one copy in pricing_core.py.
"""
from __future__ import annotations

import csv
import hashlib
import io
import itertools
import json
import math
import re
from datetime import datetime, timezone
from urllib.parse import quote, urlparse

try:                                     # backend package
    from . import pricing_core as core
except ImportError:                      # loaded by path in a harness
    import pricing_core as core  # type: ignore

AVAILABILITY = ("in_stock", "backorder", "out_of_stock", "unknown")
OPEN_STATUSES = ("draft", "submitted", "partially_received")
SPEND_STATUSES = ("submitted", "partially_received", "received", "closed")
SUPPLY_SOURCE = "supply_cart"            # purchase_order.source for a PO made from a supply cart

# Keys that would put a credential into config. Logins live ONLY in commcalc.data_source (password inside
# router._SOURCE_SECRETS); a portal_config that carries one is refused, never stored.
_SECRET_KEYS = re.compile(r"^(password|passwd|pwd|pass|secret|token|api_key|apikey|username|user|userid|"
                          r"user_id|login_id|email_address|totp|totp_secret|session_state|cookie|cookies)$", re.I)

STEP_ACTIONS = ("goto", "click", "fill", "select", "press", "wait", "wait_for")
_PLACEHOLDERS = ("url", "qty", "sku", "name", "cart_url", "portal_url", "line_no")


def _num(v):
    if v is None or v == "":
        return None
    if isinstance(v, bool):
        return None
    if isinstance(v, (int, float)):
        return float(v)
    s = str(v).strip().replace(",", "")
    if s.startswith("$"):
        s = s[1:].strip()
    try:
        return float(s)
    except ValueError:
        return core.parse_money(v)


def _int(v):
    n = _num(v)
    if n is None:
        return None
    try:
        return int(round(n))
    except (OverflowError, ValueError):
        return None


def _cents(x):
    return int(round(float(x) * 100))


def _dollars(c):
    return round(c / 100.0, 2)


# ══ VENDOR SETUP ═════════════════════════════════════════════════════════════════════════════════════
def validate_vendor(body, existing=None):
    """(clean_patch, errors) for a vendor create/update. A PORTAL vendor (is_price_source) MUST carry the
    tenant-defined free-shipping threshold and the approx delivery time — the owner's rule: "when setting up
    the vendor module the tenant needs to define this". `existing` is the stored row on an update."""
    cur = dict(existing or {})
    b = dict(body or {})
    out, errors = {}, []
    if "name" in b or not existing:
        name = str(b.get("name") or "").strip()
        if not name:
            errors.append("Vendor name is required.")
        out["name"] = name
    for f in ("contact_name", "email", "phone", "terms", "notes", "portal_url"):
        if f in b:
            out[f] = (str(b.get(f)).strip() or None) if b.get(f) is not None else None
    if "portal_url" in out and out["portal_url"]:
        pu = urlparse(out["portal_url"])
        if pu.scheme not in ("http", "https") or not pu.hostname:
            errors.append("Portal URL must be a full http(s) address.")
    if "catalog_urls" in b:
        urls = b.get("catalog_urls") or []
        if isinstance(urls, str):
            urls = [u for u in re.split(r"[\s,]+", urls) if u]
        clean = []
        for u in urls:
            u = str(u).strip()
            if not u:
                continue
            pu = urlparse(u)
            if pu.scheme not in ("http", "https") or not pu.hostname:
                errors.append(f"Catalog link is not a full http(s) address: {u[:80]}")
            else:
                clean.append(u)
        out["catalog_urls"] = clean
    for f in ("free_shipping_threshold", "shipping_fee_below_threshold"):
        if f in b:
            v = _num(b.get(f))
            if b.get(f) not in (None, "") and v is None:
                errors.append(f"{f.replace('_', ' ')} must be a dollar amount.")
            elif v is not None and v < 0:
                errors.append(f"{f.replace('_', ' ')} cannot be negative.")
            out[f] = None if v is None else round(v, 2)
    for f in ("delivery_days_min", "delivery_days_max"):
        if f in b:
            v = _int(b.get(f))
            if b.get(f) not in (None, "") and v is None:
                errors.append(f"{f.replace('_', ' ')} must be a whole number of days.")
            elif v is not None and not (0 <= v <= 365):
                errors.append(f"{f.replace('_', ' ')} must be between 0 and 365.")
            out[f] = v
    if "is_price_source" in b:
        out["is_price_source"] = bool(b.get("is_price_source"))
    if "portal_config" in b:
        cfg, cerr = normalize_portal_config(b.get("portal_config"))
        errors.extend(cerr)
        out["portal_config"] = cfg
    merged = {**cur, **out}
    lo, hi = merged.get("delivery_days_min"), merged.get("delivery_days_max")
    if lo is not None and hi is not None and lo > hi:
        errors.append("Delivery time: the minimum days cannot be more than the maximum.")
    if merged.get("is_price_source"):
        if merged.get("free_shipping_threshold") is None:
            errors.append("A portal vendor needs its free-shipping threshold (the order amount above which "
                          "shipping is free; 0 if shipping is always free).")
        if merged.get("delivery_days_max") is None:
            errors.append("A portal vendor needs its approximate delivery time (days).")
        if not (merged.get("portal_url") or (merged.get("portal_config") or {}).get("login", {}).get("url")):
            errors.append("A portal vendor needs its portal (login page) URL.")
    return out, errors


def _walk_keys(obj, path=""):
    if isinstance(obj, dict):
        for k, v in obj.items():
            yield f"{path}.{k}" if path else str(k), k, v
            yield from _walk_keys(v, f"{path}.{k}" if path else str(k))
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            yield from _walk_keys(v, f"{path}[{i}]")


def _regex_ok(p):
    try:
        re.compile(p)
        return True
    except (re.error, TypeError):
        return False


def normalize_portal_config(cfg):
    """(config dict, errors). The config is a vendors.json block (login / start_urls / follow_patterns /
    skip_patterns / strip_params / selectors / max_pages / delay_seconds) plus an optional `ordering` recipe
    and `stale_after_days`. A config that carries a credential is REFUSED (logins live in data_source)."""
    errors = []
    if cfg in (None, ""):
        return {}, []
    if isinstance(cfg, str):
        try:
            cfg = json.loads(cfg)
        except ValueError as e:
            return {}, [f"Portal config is not valid JSON: {e}"]
    if not isinstance(cfg, dict):
        return {}, ["Portal config must be a JSON object (one vendors.json block)."]
    cfg = json.loads(json.dumps(cfg))    # deep copy, JSON-only values
    for path, k, v in _walk_keys(cfg):
        if _SECRET_KEYS.match(str(k)) and v not in (None, "", [], {}):
            errors.append(f"Portal config must not hold a login ({path}) — enter logins in the vendor's "
                          f"Login box; they are stored with the other portal passwords, never in config.")
    for f in ("follow_patterns", "skip_patterns"):
        pats = cfg.get(f) or []
        if not isinstance(pats, list):
            errors.append(f"{f} must be a list of patterns.")
            continue
        for p in pats:
            if not _regex_ok(p):
                errors.append(f"{f}: not a valid pattern: {str(p)[:60]}")
    for f in ("max_pages", "delay_seconds", "stale_after_days"):
        if f in cfg and _num(cfg.get(f)) is None:
            errors.append(f"{f} must be a number.")
    if "max_pages" in cfg and (_num(cfg.get("max_pages")) or 0) > 5000:
        errors.append("max_pages is capped at 5000.")
    if cfg.get("ordering") is not None:
        _, rerr = parse_recipe(cfg.get("ordering"), hosts=None)
        errors.extend(rerr)
    return cfg, errors


def _host(u):
    try:
        h = urlparse(str(u or "")).hostname
        return h.lower() if h else None
    except ValueError:
        return None


def vendor_hosts(vendor):
    """Every host this vendor's portal lives on (portal_url, catalog links, config URLs, extra_hosts).
    A recipe step may only ever navigate to one of these."""
    cfg = vendor.get("portal_config") or {}
    urls = [vendor.get("portal_url")] + list(vendor.get("catalog_urls") or []) + list(cfg.get("start_urls") or [])
    urls.append((cfg.get("login") or {}).get("url"))
    urls.append((cfg.get("ordering") or {}).get("cart_url"))
    hosts = {_host(u) for u in urls} - {None}
    hosts |= {str(h).lower() for h in (cfg.get("extra_hosts") or []) if h}
    return hosts


def vendor_scrape_config(vendor):
    """The catalog reader's config (catalog_scrape.VendorScraper) for a po_vendor row. The vendor's KEY is
    its id (never a name in code); start_urls are the tenant's catalog links (else the config's)."""
    cfg = dict(vendor.get("portal_config") or {})
    start = list(vendor.get("catalog_urls") or []) or list(cfg.get("start_urls") or [])
    if not start and vendor.get("portal_url"):
        start = [vendor["portal_url"]]
    login = dict(cfg.get("login") or {})
    if vendor.get("portal_url") and not login.get("url"):
        login["url"] = vendor["portal_url"]
    out = {k: v for k, v in cfg.items() if k not in ("ordering", "login", "start_urls", "key")}
    out.update({"key": str(vendor.get("id") or ""), "name": vendor.get("name"), "login": login,
                "start_urls": start, "snapshot_pages": 0})
    out.setdefault("max_pages", 400)
    out.setdefault("delay_seconds", 0.6)
    return out


# ══ CATALOG ══════════════════════════════════════════════════════════════════════════════════════════
def item_key(row):
    """The identity a catalog row is stored under for its vendor: the item number when it has one, else a
    hash of link + name (the same two identities catalog_scrape._dedupe_keys merges on)."""
    sku = core.normalize_sku(row.get("sku"))
    if sku:
        return "s:" + sku
    basis = (str(row.get("url") or "").split("#")[0] + "|" + str(row.get("name") or "").strip().lower())
    return "u:" + hashlib.sha1(basis.encode("utf-8")).hexdigest()[:20]


def normalize_catalog_row(r):
    """(row | None, reason). One product row (kit products.json/csv, or the portal reader) → the snapshot
    shape. A row without a name or a positive price is rejected with its reason, never stored as $0."""
    r = dict(r or {})
    name = str(r.get("name") or "").strip()[:300]
    if len(name) < 2:
        return None, "no product name"
    price = _num(r.get("price"))
    if price is None or price <= 0:
        return None, "no price"
    list_price = _num(r.get("list_price"))
    if list_price is not None and list_price <= price:
        list_price = None
    desc = str(r.get("description") or "")[:600]
    avail = str(r.get("availability") or "").strip().lower().replace(" ", "_").replace("-", "_")
    stock_qty = _int(r.get("stock_qty"))
    if avail not in AVAILABILITY:
        avail, q = core.classify_availability(r.get("stock_text") or r.get("availability") or "")
        stock_qty = stock_qty if stock_qty is not None else q
    pack = _int(r.get("pack_qty"))
    if not pack or pack <= 1:
        pack = core.parse_pack(f"{name} {desc}")
    row = {"name": name, "sku": str(r.get("sku") or "").strip()[:80] or None, "price": round(price, 4),
           "list_price": None if list_price is None else round(list_price, 4), "pack_qty": pack,
           "availability": avail, "stock_qty": stock_qty,
           "stock_text": str(r.get("stock_text") or "")[:200] or None,
           "url": str(r.get("url") or "").strip()[:1000] or None, "description": desc or None}
    row["item_key"] = item_key(row)
    return row, ""


def normalize_catalog_rows(rows):
    """(clean rows deduped on item_key — the later / more complete row wins field by field, rejects)."""
    by_key, order, rejects = {}, [], []
    for i, r in enumerate(rows or []):
        row, why = normalize_catalog_row(r)
        if row is None:
            rejects.append({"row": i + 1, "name": str((r or {}).get("name") or "")[:80], "reason": why})
            continue
        k = row["item_key"]
        if k in by_key:
            cur = by_key[k]
            for f, v in row.items():
                if v not in (None, "", "unknown"):
                    cur[f] = v
        else:
            by_key[k] = row
            order.append(k)
    return [by_key[k] for k in order], rejects


def parse_products_upload(filename, data):
    """Rows from the kit's products.json ({"products": [...]}) or products.csv. Raises ValueError with a
    human reason on anything else."""
    fn = str(filename or "").lower()
    if isinstance(data, bytes):
        text = data.decode("utf-8-sig", errors="replace")
    else:
        text = str(data or "")
    if fn.endswith(".json") or text.lstrip().startswith(("{", "[")):
        try:
            obj = json.loads(text)
        except ValueError as e:
            raise ValueError(f"products.json could not be read: {e}")
        rows = obj.get("products") if isinstance(obj, dict) else obj
        if not isinstance(rows, list):
            raise ValueError("products.json has no 'products' list — upload the file the kit wrote.")
        return [r for r in rows if isinstance(r, dict)]
    if fn.endswith(".csv") or "," in text.split("\n", 1)[0]:
        rdr = csv.DictReader(io.StringIO(text))
        cols = {c.strip().lower() for c in (rdr.fieldnames or [])}
        if not {"name", "price"} <= cols:
            raise ValueError("products.csv needs at least 'name' and 'price' columns (the kit writes them).")
        return [{(k or "").strip().lower(): v for k, v in row.items()} for row in rdr]
    raise ValueError("Upload the kit's products.json or products.csv.")


def _slug(s):
    return re.sub(r"[^a-z0-9]", "", str(s or "").lower())


def map_kit_vendor(kit_key, vendors):
    """Which po_vendor a kit row's `vendor` key belongs to: the vendor whose portal_config.key equals it,
    else the one whose name slug equals it. None = unmapped (reported, never guessed onto a neighbour)."""
    k = _slug(kit_key)
    if not k:
        return None
    for v in vendors or []:
        if _slug((v.get("portal_config") or {}).get("key")) == k:
            return v.get("id")
    hits = [v.get("id") for v in vendors or [] if _slug(v.get("name")) == k]
    return hits[0] if len(hits) == 1 else None


def offer_from_row(row):
    """A snapshot row → the product dict pricing_core compares (vendor = the po_vendor id)."""
    return {"vendor": row.get("vendor_id"), "row_id": row.get("id"), "name": row.get("name") or "",
            "sku": row.get("sku") or "", "price": _num(row.get("price")), "list_price": _num(row.get("list_price")),
            "pack_qty": _int(row.get("pack_qty")), "availability": row.get("availability") or "unknown",
            "stock_qty": _int(row.get("stock_qty")), "url": row.get("url") or "",
            "description": row.get("description") or "", "seen_at": row.get("seen_at")}


# ══ CART OPTIMIZER ═══════════════════════════════════════════════════════════════════════════════════
def order_packs(qty, pack_qty, basis):
    """How many of the vendor's sellable packs to order. basis 'units': the wanted qty is units and each
    pack holds pack_qty (round UP — you cannot order part of a bundle). basis 'packs': qty is already packs."""
    q = max(0, int(qty or 0))
    if basis == "units" and pack_qty and pack_qty > 1:
        return int(math.ceil(q / float(pack_qty)))
    return q


def _vendor_terms(v):
    v = dict(v or {})
    thr = _num(v.get("free_shipping_threshold"))
    fee = _num(v.get("shipping_fee_below_threshold", v.get("shipping_fee")))
    return {"name": v.get("name") or "", "threshold_c": None if thr is None else _cents(thr),
            "fee_c": None if fee is None else _cents(fee),
            "dmin": _int(v.get("delivery_days_min")), "dmax": _int(v.get("delivery_days_max"))}


def _shipping(sub_c, t):
    """(shipping cents, known?) for one vendor subtotal. Free at/above the threshold; the fee below it."""
    if sub_c <= 0:
        return 0, True
    if t["threshold_c"] is not None and sub_c >= t["threshold_c"]:
        return 0, True
    if t["fee_c"] is None:
        return 0, False
    return t["fee_c"], True


def _eligible(offer, packs, terms, opts):
    """(ok, reason, flags) — may this offer fill the line?"""
    flags = []
    if offer.get("price") is None:
        return False, "no price", flags
    if terms is None:
        return False, "vendor not set up for ordering", flags
    av = offer.get("availability") or "unknown"
    if av == "out_of_stock":
        return False, "out of stock", flags
    if av == "backorder":
        if not opts["allow_backorder"]:
            return False, "on backorder", flags
        flags.append("backorder")
    if av == "unknown":
        if not opts["allow_unknown"]:
            return False, "stock not stated", flags
        flags.append("availability unknown — check on the vendor site")
    sq = offer.get("stock_qty")
    if av == "in_stock" and sq is not None and sq < packs:
        return False, f"only {sq} in stock", flags
    md = opts["max_delivery_days"]
    if md is not None:
        if terms["dmax"] is None:
            return False, "delivery time not set for this vendor", flags
        if terms["dmax"] > md:
            return False, f"delivers in up to {terms['dmax']} days (limit {md})", flags
    return True, "", flags


def _evaluate(assign, choices, vendors_t):
    """Total cents for one assignment: {item_idx: vendor}. Returns (total_c, items_c, ship_c, per_vendor)."""
    per = {}
    for i, vid in assign.items():
        per.setdefault(vid, 0)
        per[vid] += choices[i][vid]["cost_c"]
    ship_c, unknown = 0, []
    for vid, sub in per.items():
        s, known = _shipping(sub, vendors_t[vid])
        ship_c += s
        if not known:
            unknown.append(vid)
    items_c = sum(per.values())
    return items_c + ship_c, items_c, ship_c, per, unknown


def _rank(ev, assign, vendors_t):
    total_c, _, _, per, _ = ev
    dmax = max([vendors_t[v]["dmax"] if vendors_t[v]["dmax"] is not None else 10 ** 6 for v in per] or [0])
    return (total_c, len(per), dmax)


def optimize_cart(items, vendors, max_delivery_days=None, allow_backorder=False, allow_unknown=True,
                  exact_limit=200000):
    """THE cart optimizer. PURE.

    items   [{key, label, qty, basis?, offers: [{vendor, price, pack_qty, availability, stock_qty, row_id, sku,
             name, url}]}] — qty is UNITS when every priced offer states its pack size (basis 'units'), else the
             vendor's sellable packs (basis 'packs', said on the line); basis='packs' forces packs.
    vendors {vendor_id: {name, free_shipping_threshold, shipping_fee_below_threshold, delivery_days_min,
             delivery_days_max}} — the tenant-defined terms.

    Returns the cheapest plan INCLUDING shipping: exact search over every assignment when the space is small
    (vendors are few), else every vendor SUBSET with each item on its cheapest vendor inside the subset plus
    single-item move improvement — `method` says which. Out-of-stock (and a known stock shortfall) is never
    chosen; unknown availability is allowed but flagged (allow_unknown=False refuses it); backorder is refused
    unless allow_backorder; max_delivery_days drops vendors whose stated maximum exceeds it (a vendor with no
    stated delivery time is dropped under a limit, and says so). Ties: fewer vendors, then faster delivery."""
    opts = {"max_delivery_days": _int(max_delivery_days), "allow_backorder": bool(allow_backorder),
            "allow_unknown": bool(allow_unknown)}
    vendors_t = {vid: _vendor_terms(v) for vid, v in (vendors or {}).items()}
    lines_meta, choices, unfillable = [], [], []
    for idx, it in enumerate(items or []):
        qty = max(0, int(_int(it.get("qty")) or 0))
        offers = [o for o in (it.get("offers") or []) if o]
        priced = [o for o in offers if _num(o.get("price")) is not None]
        # UNITS only when every priced offer states its pack; a caller may force packs ("packs").
        basis = "units" if (priced and all((_int(o.get("pack_qty")) or 0) > 1 for o in priced)
                            and str(it.get("basis") or "").lower() != "packs") else "packs"
        label = it.get("label") or (max((o.get("name") or "" for o in offers), key=len) if offers else "")
        per_vendor, excluded = {}, []
        for o in offers:
            o = dict(o)
            o["price"] = _num(o.get("price"))
            o["pack_qty"] = _int(o.get("pack_qty"))
            o["stock_qty"] = _int(o.get("stock_qty"))
            packs = order_packs(qty, o["pack_qty"], basis)
            ok, why, flags = _eligible(o, packs, vendors_t.get(o.get("vendor")), opts)
            vname = (vendors_t.get(o.get("vendor")) or {}).get("name") or str(o.get("vendor"))
            if not ok:
                excluded.append({"vendor": o.get("vendor"), "vendor_name": vname, "reason": why})
                continue
            cost_c = _cents(o["price"]) * packs
            cur = per_vendor.get(o["vendor"])
            if cur is None or cost_c < cur["cost_c"]:
                per_vendor[o["vendor"]] = {"offer": o, "packs": packs, "cost_c": cost_c, "flags": flags}
        meta = {"key": it.get("key") or str(idx), "label": label, "qty": qty, "basis": basis, "excluded": excluded}
        if qty <= 0:
            unfillable.append({**meta, "reason": "quantity is zero"})
            continue
        if not per_vendor:
            reasons = "; ".join(sorted({f"{e['vendor_name']}: {e['reason']}" for e in excluded})) or "no offers"
            unfillable.append({**meta, "reason": reasons})
            continue
        lines_meta.append(meta)
        choices.append(per_vendor)

    result = {"method": "none", "total": 0.0, "items_total": 0.0, "shipping_total": 0.0, "vendors": [],
              "lines": [], "unfillable": unfillable, "hints": [], "flags": [], "baselines": {},
              "options": opts}
    if not choices:
        return result

    n_space = 1
    for c in choices:
        n_space *= len(c)
    best_assign, best_rank, best_ev = None, None, None
    if n_space <= exact_limit:
        result["method"] = "exact"
        keys = [sorted(c.keys(), key=str) for c in choices]
        for combo in itertools.product(*keys):
            assign = dict(enumerate(combo))
            ev = _evaluate(assign, choices, vendors_t)
            rk = _rank(ev, assign, vendors_t)
            if best_rank is None or rk < best_rank:
                best_assign, best_rank, best_ev = assign, rk, ev
    else:
        result["method"] = "subset-search"
        all_v = sorted({v for c in choices for v in c}, key=str)
        subsets = []
        if len(all_v) <= 12:
            for r in range(1, len(all_v) + 1):
                subsets.extend(itertools.combinations(all_v, r))
        else:
            subsets = [tuple(all_v)]
        for sub in subsets:
            sset = set(sub)
            if any(not (sset & set(c)) for c in choices):
                continue
            assign = {i: min((v for v in c if v in sset), key=lambda v: (c[v]["cost_c"], str(v)))
                      for i, c in enumerate(choices)}
            ev = _evaluate(assign, choices, vendors_t)
            improved = True
            while improved:                       # single-item moves inside the subset, while they help
                improved = False
                for i, c in enumerate(choices):
                    for v in sorted((v for v in c if v in sset), key=str):
                        if v == assign[i]:
                            continue
                        trial = dict(assign)
                        trial[i] = v
                        tev = _evaluate(trial, choices, vendors_t)
                        if _rank(tev, trial, vendors_t) < _rank(ev, assign, vendors_t):
                            assign, ev, improved = trial, tev, True
            rk = _rank(ev, assign, vendors_t)
            if best_rank is None or rk < best_rank:
                best_assign, best_rank, best_ev = assign, rk, ev

    total_c, items_c, ship_c, per, unknown_ship = best_ev
    result.update({"total": _dollars(total_c), "items_total": _dollars(items_c), "shipping_total": _dollars(ship_c)})
    by_vendor = {}
    for i, vid in sorted(best_assign.items()):
        ch = choices[i][vid]
        o = ch["offer"]
        meta = lines_meta[i]
        alts = sorted(({"vendor": v, "vendor_name": vendors_t[v]["name"], "line_total": _dollars(c2["cost_c"]),
                        "availability": c2["offer"].get("availability")}
                       for v, c2 in choices[i].items() if v != vid), key=lambda a: (a["line_total"], str(a["vendor"])))
        line = {**meta, "vendor": vid, "vendor_name": vendors_t[vid]["name"], "packs": ch["packs"],
                "pack_qty": o.get("pack_qty"), "unit_price": o.get("price"), "line_total": _dollars(ch["cost_c"]),
                "units": ch["packs"] * o["pack_qty"] if meta["basis"] == "units" and o.get("pack_qty") else None,
                "availability": o.get("availability"), "flags": list(ch["flags"]), "row_id": o.get("row_id"),
                "sku": o.get("sku"), "name": o.get("name"), "url": o.get("url"), "alternatives": alts}
        if meta["basis"] == "packs":
            line["flags"].append("pack size not stated by every vendor — quantity is in each vendor's own packs")
        if line["units"] is not None and line["units"] > meta["qty"]:
            line["flags"].append(f"rounded up to whole packs: {line['units']} units for {meta['qty']} wanted")
        result["lines"].append(line)
        by_vendor.setdefault(vid, []).append(line)
    for vid in sorted(by_vendor, key=lambda v: (-per[v], str(v))):
        t = vendors_t[vid]
        sub_c = per[vid]
        s_c, known = _shipping(sub_c, t)
        to_free = None
        if t["threshold_c"] is not None and 0 < sub_c < t["threshold_c"]:
            to_free = _dollars(t["threshold_c"] - sub_c)
        result["vendors"].append({
            "vendor": vid, "vendor_name": t["name"], "subtotal": _dollars(sub_c), "shipping": _dollars(s_c),
            "shipping_known": known, "total": _dollars(sub_c + s_c),
            "free_shipping_threshold": None if t["threshold_c"] is None else _dollars(t["threshold_c"]),
            "shipping_fee_below_threshold": None if t["fee_c"] is None else _dollars(t["fee_c"]),
            "to_free_shipping": to_free, "delivery_days_min": t["dmin"], "delivery_days_max": t["dmax"],
            "lines": by_vendor[vid]})
        if to_free is not None:
            if t["fee_c"] is not None:
                result["hints"].append(f"Add ${to_free:,.2f} more at {t['name']} to reach free shipping "
                                       f"(saves the ${_dollars(t['fee_c']):,.2f} shipping fee).")
            else:
                result["hints"].append(f"Add ${to_free:,.2f} more at {t['name']} to reach free shipping.")
    for vid in unknown_ship:
        result["flags"].append(f"{vendors_t[vid]['name']}: under its free-shipping threshold and no shipping fee "
                               f"is set — shipping is NOT included in the total.")
    dmins = [v["delivery_days_min"] for v in result["vendors"] if v["delivery_days_min"] is not None]
    dmaxs = [v["delivery_days_max"] for v in result["vendors"] if v["delivery_days_max"] is not None]
    result["delivery_days"] = {"min": max(dmins) if dmins else None, "max": max(dmaxs) if dmaxs else None}

    # Baselines — what the plan saves. (1) each item at its cheapest vendor, shipping ignored when choosing;
    # (2) the cheapest single vendor that can fill every line.
    naive = {i: min(c, key=lambda v: (c[v]["cost_c"], str(v))) for i, c in enumerate(choices)}
    nev = _evaluate(naive, choices, vendors_t)
    result["baselines"]["cheapest_each_item"] = _dollars(nev[0])
    singles = []
    for v in sorted({v for c in choices for v in c}, key=str):
        if all(v in c for c in choices):
            singles.append((_evaluate({i: v for i in range(len(choices))}, choices, vendors_t)[0], v))
    if singles:
        sc, sv = min(singles)
        result["baselines"]["best_single_vendor"] = _dollars(sc)
        result["baselines"]["best_single_vendor_id"] = sv
        result["savings_vs_single_vendor"] = _dollars(sc - total_c)
    else:
        result["baselines"]["best_single_vendor"] = None
        result["savings_vs_single_vendor"] = None
    result["savings_vs_cheapest_each_item"] = _dollars(nev[0] - total_c)
    return result


def po_drafts_from_plan(plan):
    """One purchase_order draft per vendor in the plan (the lines + shipping estimate + evidence meta).
    The PO's total = the plan's vendor subtotal + its shipping estimate."""
    out = []
    n_v = max(1, len(plan.get("vendors") or []))
    sav = plan.get("savings_vs_single_vendor")
    for v in plan.get("vendors") or []:
        lines = []
        for i, ln in enumerate(v["lines"]):
            lines.append({"line_no": i + 1, "sku": ln.get("sku") or None, "device_model": (ln.get("name") or ln.get("label") or "Item")[:300],
                          "qty_ordered": int(ln["packs"]), "unit_cost": round(float(ln["unit_price"]), 2),
                          "extended_cost": ln["line_total"],
                          "notes": ("; ".join(ln.get("flags") or []) or None)})
        meta = {"lines": [{"line_no": i + 1, "url": ln.get("url"), "sku": ln.get("sku"), "qty": int(ln["packs"]),
                           "name": ln.get("name"), "row_id": ln.get("row_id"), "label": ln.get("label"),
                           "wanted_qty": ln.get("qty"), "basis": ln.get("basis")} for i, ln in enumerate(v["lines"])],
                "free_shipping_threshold": v.get("free_shipping_threshold"),
                "to_free_shipping": v.get("to_free_shipping"), "shipping_known": v.get("shipping_known"),
                "delivery_days_min": v.get("delivery_days_min"), "delivery_days_max": v.get("delivery_days_max"),
                "plan_method": plan.get("method"),
                # the plan's saving vs the best single vendor, shared evenly across its POs (reporting only)
                "savings": None if sav is None else round(sav / n_v, 2)}
        out.append({"vendor_id": v["vendor"], "vendor_name": v["vendor_name"], "lines": lines,
                    "subtotal": v["subtotal"], "shipping_estimate": v["shipping"],
                    "total": round(v["subtotal"] + v["shipping"], 2), "meta": meta})
    return out


# ══ ORDER SESSION — the vendor's configured recipe ═══════════════════════════════════════════════════
DEFAULT_CONFIRMATION = {
    # House vocabulary (a vendor's config overrides it). A page is a CONFIRMATION only when it says so —
    # an order number pattern alone is not enough (a cart page can print "Order #" too).
    "success_pattern": r"thank\s*you\s*for\s*(?:your\s*)?(?:order|purchase)|order\s*(?:has\s*been\s*)?"
                       r"(?:received|placed|confirmed|submitted|complete)|order\s*confirmation|"
                       r"your\s*order\s*(?:number|no\.?|#)\s*is",
    "ref_pattern": r"order\s*(?:number|no\.?|#|id|reference|ref\.?)\s*(?:is)?\s*[:#]?\s*([A-Z0-9][A-Z0-9\-/]{2,30})",
    "total_pattern": r"(?:order\s*)?total\s*[:\-]?\s*(\$\s?[\d,]+(?:\.\d{2})?)",
}
DEFAULT_CART_TOTAL = (r"sub[\s\-]?total\s*[:\-]?\s*(\$\s?[\d,]+(?:\.\d{2})?)",
                      r"(?:cart|order)?\s*total\s*[:\-]?\s*(\$\s?[\d,]+(?:\.\d{2})?)")


def _parse_steps(steps, where, errors):
    out = []
    if steps in (None, ""):
        return out
    if not isinstance(steps, list):
        errors.append(f"ordering.{where} must be a list of steps.")
        return out
    for i, st in enumerate(steps):
        if not isinstance(st, dict):
            errors.append(f"ordering.{where}[{i}] must be an object.")
            continue
        act = str(st.get("action") or "").strip().lower()
        if act not in STEP_ACTIONS:
            errors.append(f"ordering.{where}[{i}]: unknown action '{act}' (allowed: {', '.join(STEP_ACTIONS)}).")
            continue
        if act == "goto" and not st.get("url"):
            errors.append(f"ordering.{where}[{i}]: goto needs a url.")
            continue
        if act in ("click", "fill", "select", "wait_for") and not st.get("selector"):
            errors.append(f"ordering.{where}[{i}]: {act} needs a selector.")
            continue
        if act in ("fill", "select") and st.get("value") is None:
            errors.append(f"ordering.{where}[{i}]: {act} needs a value (e.g. \"{{qty}}\").")
            continue
        if act == "press" and not st.get("key"):
            errors.append(f"ordering.{where}[{i}]: press needs a key.")
            continue
        for fld in ("url", "value"):
            for ph in re.findall(r"\{([a-z_]+)\}", str(st.get(fld) or "")):
                if ph not in _PLACEHOLDERS:
                    errors.append(f"ordering.{where}[{i}]: unknown placeholder {{{ph}}} "
                                  f"(allowed: {', '.join('{' + p + '}' for p in _PLACEHOLDERS)}).")
        clean = {"action": act}
        for fld in ("url", "selector", "value", "key", "frame"):
            if st.get(fld) is not None:
                clean[fld] = str(st.get(fld))
        clean["ms"] = max(0, min(int(_num(st.get("ms")) or (1500 if act == "wait" else 0)), 30000))
        clean["optional"] = bool(st.get("optional"))
        out.append(clean)
    return out


def parse_recipe(ordering, hosts=None):
    """(recipe, errors) from portal_config.ordering. Shape:
        {cart_url, line_steps: [...], review_steps: [...], submit_steps: [...],
         cart_total_selector, cart_total_pattern,
         confirmation: {url_pattern, success_pattern, ref_pattern, total_pattern}}
    line_steps run ONCE PER LINE ({url} {qty} {sku} {name} {line_no}); review_steps go to the cart/checkout
    review; submit_steps (optional) are the vendor's final "place order" — run ONLY on an explicit human
    confirm in MetricsPro. When `hosts` is given, a literal goto host must be one of the vendor's hosts."""
    errors = []
    o = ordering if isinstance(ordering, dict) else {}
    if ordering not in (None, "") and not isinstance(ordering, dict):
        errors.append("ordering must be an object.")
    rec = {"cart_url": str(o.get("cart_url") or "") or None,
           "line_steps": _parse_steps(o.get("line_steps"), "line_steps", errors),
           "review_steps": _parse_steps(o.get("review_steps"), "review_steps", errors),
           "submit_steps": _parse_steps(o.get("submit_steps"), "submit_steps", errors),
           "cart_total_selector": str(o.get("cart_total_selector") or "") or None,
           "cart_total_pattern": str(o.get("cart_total_pattern") or "") or None}
    if rec["cart_url"] and urlparse(rec["cart_url"]).scheme not in ("http", "https"):
        errors.append("ordering.cart_url must be a full http(s) address.")
    conf = dict(DEFAULT_CONFIRMATION)
    conf["url_pattern"] = None
    c_in = o.get("confirmation") if isinstance(o.get("confirmation"), dict) else {}
    for k in ("url_pattern", "success_pattern", "ref_pattern", "total_pattern"):
        if c_in.get(k):
            conf[k] = str(c_in[k])
    rec["confirmation"] = conf
    for k in ("cart_total_pattern",):
        if rec[k] and not _regex_ok(rec[k]):
            errors.append(f"ordering.{k} is not a valid pattern.")
    for k, v in conf.items():
        if v and not _regex_ok(v):
            errors.append(f"ordering.confirmation.{k} is not a valid pattern.")
    if hosts:
        for where in ("line_steps", "review_steps", "submit_steps"):
            for st in rec[where]:
                u = st.get("url") or ""
                if st["action"] == "goto" and not u.startswith("{"):
                    h = _host(u)
                    if h and h not in hosts:
                        errors.append(f"ordering.{where}: goto host {h} is not this vendor's portal.")
        if rec["cart_url"] and _host(rec["cart_url"]) not in hosts:
            errors.append("ordering.cart_url is not on this vendor's portal.")
    return rec, errors


def recipe_status(recipe):
    """What the order session can do for this vendor, said plainly."""
    r = recipe or {}
    if r.get("line_steps"):
        return {"level": "full" if r.get("submit_steps") else "cart",
                "text": ("Adds every line to the vendor's cart and opens the review page; the final submit "
                         "runs when you press Confirm." if r.get("submit_steps") else
                         "Adds every line to the vendor's cart and opens the review page; you press the "
                         "vendor's own Place-order button in the live window.")}
    if r.get("cart_url") or r.get("review_steps"):
        return {"level": "open_cart", "text": "No add-to-cart steps are configured: the session signs in and opens "
                                              "the vendor's cart — add the lines yourself in the live window."}
    return {"level": "none", "text": "No ordering recipe is configured: the session signs in and stops there — "
                                     "add the lines and check out yourself in the live window."}


def render_step(step, ctx):
    """A recipe step with its {placeholders} filled from ctx (url, qty, sku, name, cart_url, portal_url,
    line_no). Inside a goto URL, sku/name/qty are URL-encoded; a whole-{url} placeholder is used as is."""
    out = dict(step)
    for fld in ("url", "value"):
        if out.get(fld) is None:
            continue
        s = str(out[fld])

        def rep(m, fld=fld):
            k = m.group(1)
            v = "" if ctx.get(k) is None else str(ctx.get(k))
            if fld == "url" and k in ("sku", "name", "qty", "line_no"):
                return quote(v, safe="")
            return v
        out[fld] = re.sub(r"\{([a-z_]+)\}", rep, s)
    return out


def url_allowed(url, hosts):
    """May the order session navigate here? http(s) on one of the vendor's own hosts only."""
    pu = urlparse(str(url or ""))
    return pu.scheme in ("http", "https") and bool(pu.hostname) and pu.hostname.lower() in (hosts or set())


def parse_cart_total(text, pattern=None):
    """The vendor's cart total from the review page's text: the configured pattern (group 1 = the amount),
    else the house wording (sub-total first, then total). None when the page does not state one."""
    t = str(text or "")
    pats = [pattern] if pattern else list(DEFAULT_CART_TOTAL)
    for p in pats:
        try:
            m = re.search(p, t, re.I)
        except re.error:
            continue
        if m:
            return core.parse_money(m.group(1) if m.groups() else m.group(0))
    return None


def detect_confirmation(url, text, conf=None):
    """Is this the vendor's ORDER CONFIRMATION page? PURE.
    confirmed = the configured url_pattern matches the page URL, OR the success wording is on the page.
    An order-number pattern alone never confirms (a cart can print "Order #"). Returns
    {confirmed, order_ref, total, matched}."""
    c = dict(DEFAULT_CONFIRMATION)
    c["url_pattern"] = None
    c.update({k: v for k, v in (conf or {}).items() if v})
    t = re.sub(r"\s+", " ", str(text or ""))
    matched = []
    if c.get("url_pattern") and re.search(c["url_pattern"], str(url or ""), re.I):
        matched.append("url")
    m = re.search(c["success_pattern"], t, re.I) if c.get("success_pattern") else None
    if m:
        matched.append("text: " + m.group(0)[:80])
    ref = None
    if matched and c.get("ref_pattern"):
        rm = re.search(c["ref_pattern"], t, re.I)
        if rm:
            ref = (rm.group(1) if rm.groups() else rm.group(0)).strip()
    total = None
    if matched and c.get("total_pattern"):
        tm = re.search(c["total_pattern"], t, re.I)
        if tm:
            total = core.parse_money(tm.group(1) if tm.groups() else tm.group(0))
    return {"confirmed": bool(matched), "order_ref": ref, "total": total, "matched": matched}


def manual_confirmation(ref, total=None, note=None, who=None, now=None):
    """(evidence, errors) for a typed-in confirmation number — always available as the fallback."""
    r = str(ref or "").strip()
    errs = []
    if not r:
        errs.append("Type the vendor's order / confirmation number.")
    elif len(r) > 60:
        errs.append("That confirmation number is too long.")
    t = _num(total) if total not in (None, "") else None
    if total not in (None, "") and t is None:
        errs.append("The order total must be a dollar amount.")
    ev = {"method": "manual", "order_ref": r or None, "total": t, "note": (str(note).strip()[:500] or None) if note else None,
          "captured_by": who, "captured_at": (now or datetime.now(timezone.utc)).isoformat()}
    return ev, errs


# ══ ATTENTION + SUMMARY ══════════════════════════════════════════════════════════════════════════════
def _parse_ts(v):
    if not v:
        return None
    if isinstance(v, datetime):
        return v if v.tzinfo else v.replace(tzinfo=timezone.utc)
    try:
        d = datetime.fromisoformat(str(v).replace("Z", "+00:00"))
        return d if d.tzinfo else d.replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def vendor_attention(vendor, login=None, last_seen_at=None, now=None):
    """Why a vendor needs the tenant's attention: [{code, severity: warn|info, text}]. PURE."""
    v = dict(vendor or {})
    now = now or datetime.now(timezone.utc)
    out = []
    if not v.get("is_active", True):
        return out
    portal = bool(v.get("is_price_source"))
    if portal and v.get("free_shipping_threshold") is None:
        out.append({"code": "no_threshold", "severity": "warn", "text": "Free-shipping threshold not set."})
    if portal and v.get("delivery_days_max") is None:
        out.append({"code": "no_delivery", "severity": "warn", "text": "Approximate delivery time not set."})
    if portal:
        if not v.get("data_source_id") or not login:
            out.append({"code": "no_login", "severity": "warn", "text": "No portal login saved."})
        else:
            if not login.get("has_password"):
                out.append({"code": "login_incomplete", "severity": "warn", "text": "The login has no password."})
            st = str(login.get("auth_status") or "").lower()
            if st in ("error", "needs_2fa", "expired", "failed"):
                out.append({"code": "login_error", "severity": "warn",
                            "text": "Login needs attention: " + (str(login.get("auth_message") or st))[:160]})
        seen = _parse_ts(last_seen_at)
        stale = int(_num((v.get("portal_config") or {}).get("stale_after_days")) or 7)
        if seen is None:
            out.append({"code": "no_catalog", "severity": "warn", "text": "Catalog never read — upload the kit's "
                        "products file or run a portal read."})
        else:
            age = (now - seen).days
            if age > stale:
                out.append({"code": "stale_catalog", "severity": "warn",
                            "text": f"Catalog prices are {age} days old (refresh after {stale})."})
        rec, _ = parse_recipe((v.get("portal_config") or {}).get("ordering"))
        rs = recipe_status(rec)
        if rs["level"] in ("none", "open_cart"):
            out.append({"code": "no_recipe", "severity": "info", "text": rs["text"]})
    return out


def summary_tiles(orders, attention_by_vendor, now=None):
    """The four tiles the store-operations dashboard shows. PURE.
    open_orders    supply POs still open (draft / submitted / partially received)
    spend_mtd      this month's submitted supply spend (the vendor's confirmed total when captured, else the PO total)
    savings_mtd    what this month's supply orders saved vs the best single vendor (the optimizer's figure)
    vendors_needing_attention  vendors with at least one warn-level reason"""
    now = now or datetime.now(timezone.utc)
    ym = (now.year, now.month)
    open_n, spend, savings = 0, 0.0, 0.0
    for o in orders or []:
        st = o.get("status")
        if st in OPEN_STATUSES:
            open_n += 1
        when = _parse_ts(o.get("submitted_at")) or _parse_ts(o.get("order_date"))
        in_month = bool(when and (when.year, when.month) == ym)
        if st in SPEND_STATUSES and in_month:
            amt = _num(o.get("vendor_order_total"))
            spend += amt if amt is not None else (_num(o.get("total")) or 0.0)
        if st != "cancelled":
            placed = _parse_ts(o.get("created_at")) or _parse_ts(o.get("order_date"))
            if placed and (placed.year, placed.month) == ym:
                savings += _num(((o.get("supply_meta") or {}).get("savings"))) or 0.0
    needing = sorted(vid for vid, reasons in (attention_by_vendor or {}).items()
                     if any(r.get("severity") == "warn" for r in reasons or []))
    return {"open_orders": open_n, "spend_mtd": round(spend, 2), "savings_mtd": round(savings, 2),
            "vendors_needing_attention": len(needing), "vendor_ids_needing_attention": needing}
