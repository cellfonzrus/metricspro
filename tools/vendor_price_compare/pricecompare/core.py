"""Pure logic for the vendor price comparison kit — no browser, no network, no files.

Everything here is deterministic so it can be proven DB-free (backend/harness_vendor_price_compare.py):
  * parse_money / parse_pack / classify_availability / normalize_sku — read one scraped product row
  * product_features / match_score — how alike two product rows are
  * group_products — cluster rows from N vendors into "the same product" groups
  * comparison_rows — one row per group: each vendor's price, the cheapest in-stock vendor, savings
  * plan_order — a shopping list (search text + qty) → the cheapest in-stock pick per line

No vendor name, URL or selector appears here: vendors are rows in vendors.json.
"""
from __future__ import annotations

import re
from difflib import SequenceMatcher

# ── Money ─────────────────────────────────────────────────────────────────────────────────────────
_MONEY_RE = re.compile(r"\$\s?(\d{1,3}(?:,\d{3})+|\d+)(\.\d{1,4})?")


def parse_money(text):
    """First dollar amount in `text` as a float, or None. '$1,234.50' → 1234.5."""
    if text is None:
        return None
    m = _MONEY_RE.search(str(text))
    if not m:
        return None
    return round(float(m.group(1).replace(",", "") + (m.group(2) or "")), 4)


def all_money(text):
    """Every dollar amount in `text`, in order."""
    return [round(float(a.replace(",", "") + (b or "")), 4) for a, b in _MONEY_RE.findall(str(text or ""))]


# ── Pack size ─────────────────────────────────────────────────────────────────────────────────────
_PACK_WORDS = r"(?:bundle|bdl|bndl|pack|pk|pkg|case|cs|box|bx|carton|ctn|roll|rl|sleeve|slv|bag|bg|lot)"
_PACK_PATTERNS = [
    re.compile(r"(\d{1,5})\s*(?:/|per)\s*" + _PACK_WORDS + r"\b", re.I),        # 25/bundle, 25 per case
    re.compile(r"(\d{1,5})\s*[a-z]+\s*(?:/|per)\s*" + _PACK_WORDS + r"\b", re.I),  # 36 rolls/case, 25 boxes per bundle
    re.compile(_PACK_WORDS + r"\s*(?:of|qty|quantity)?\s*[:=]?\s*(\d{1,5})\b", re.I),  # pack of 25, case qty: 50
    re.compile(r"(\d{1,5})\s*(?:ct|count|pcs|pieces|pc|units)\b", re.I),        # 25 ct, 100 pcs
    re.compile(r"(\d{1,5})\s*-?\s*" + _PACK_WORDS + r"\b", re.I),               # 25-pack, 10 pk
]


def parse_pack(text):
    """Units per sellable pack from product text, or None when the text does not say.

    A dimension like '12 x 12 x 12' is never read as a pack size."""
    t = re.sub(r"\d+(?:\.\d+)?\s*(?:\"|in\b|inch(?:es)?)?\s*[x×]\s*\d+(?:\.\d+)?(?:\s*(?:\"|in\b|inch(?:es)?)?\s*[x×]\s*\d+(?:\.\d+)?)?",
               " ", str(text or ""), flags=re.I)
    for pat in _PACK_PATTERNS:
        m = pat.search(t)
        if m:
            n = int(m.group(1))
            if 1 < n <= 100000:
                return n
    return None


# ── Availability ──────────────────────────────────────────────────────────────────────────────────
_OUT = re.compile(r"out\s*of\s*stock|sold\s*out|unavailable|not\s*available|discontinued|no\s*longer\s*available|temporarily\s*out", re.I)
_BACK = re.compile(r"back[\s-]?order|pre[\s-]?order|ships?\s+in\s+\d|special\s*order|call\s*for\s*availability", re.I)
_IN = re.compile(r"in\s*stock|available\s*now|ready\s*to\s*ship|ships?\s*(?:today|same\s*day)|\b\d+\s*(?:units?\s*)?(?:in\s*stock|available)", re.I)
_QTY = re.compile(r"(?:units?\s*in\s*stock|qty\s*(?:available|on\s*hand)|in\s*stock|available)\s*[:\-]?\s*(\d{1,6})\b|\b(\d{1,6})\s*(?:units?\s*)?(?:in\s*stock|available)", re.I)


def classify_availability(text):
    """(status, qty) — status is 'in_stock' | 'backorder' | 'out_of_stock' | 'unknown'.

    'unknown' means the page did not say; it is never assumed to be in stock."""
    t = str(text or "")
    qty = None
    m = _QTY.search(t)
    if m:
        qty = int(m.group(1) or m.group(2))
    if _OUT.search(t):
        return "out_of_stock", 0 if qty is None else qty
    if qty == 0:
        return "out_of_stock", 0
    if _BACK.search(t):
        return "backorder", qty
    if _IN.search(t) or (qty or 0) > 0:
        return "in_stock", qty
    return "unknown", qty


# ── Identity / features ───────────────────────────────────────────────────────────────────────────
def normalize_sku(sku):
    """Upper-case, alphanumerics only: 'bx-12.12.12' → 'BX121212'. '' when nothing usable."""
    s = re.sub(r"[^A-Za-z0-9]", "", str(sku or "")).upper()
    return s if len(s) >= 3 and re.search(r"\d", s) else ""


_DIM_RE = re.compile(
    r"(\d+(?:\.\d+)?)\s*(?:\"|in\b|inch(?:es)?)?\s*[x×]\s*(\d+(?:\.\d+)?)(?:\s*(?:\"|in\b|inch(?:es)?)?\s*[x×]\s*(\d+(?:\.\d+)?))?",
    re.I)
_STOP = {"the", "a", "an", "and", "or", "for", "with", "of", "in", "inch", "inches", "each", "ea", "per",
         "new", "item", "size", "qty", "x", "pack", "pk", "case", "cs", "bundle", "bdl", "ct", "count", "pcs"}


def _num(s):
    f = float(s)
    return str(int(f)) if f == int(f) else str(f)


def dimensions(text):
    """Box/label dimensions as a sorted tuple of strings, or None. '12 x 10 x 8"' → ('10','12','8')."""
    m = _DIM_RE.search(str(text or ""))
    if not m:
        return None
    parts = [_num(p) for p in m.groups() if p]
    return tuple(sorted(parts))


_UNIT_WORDS = {"yds": "yd", "yard": "yd", "yards": "yd", "ft": "ft", "feet": "ft", "foot": "ft",
               "inch": "in", "inches": "in", "lbs": "lb", "pound": "lb", "pounds": "lb", "rolls": "roll"}


def _stem(w):
    w = _UNIT_WORDS.get(w, w)
    if len(w) > 3 and w.endswith("es") and w[-3] in "sxz":
        return w[:-2]
    if len(w) > 3 and w.endswith("s") and not w.endswith("ss"):
        return w[:-1]
    return w


def tokens(text):
    """Descriptive words (no sizes, prices or bare numbers), lower-cased and singular."""
    t = _DIM_RE.sub(" ", str(text or "").lower())
    t = re.sub(r"\$\s?\d[\d,]*(?:\.\d+)?", " ", t)
    t = re.sub(r"[^a-z0-9]+", " ", t)
    out = []
    for w in t.split():
        w = re.sub(r"^\d+(?=[a-z])", "", w)          # 110yd → yd, 2in → in (the number is in numbers())
        w = _stem(w)
        if w and w not in _STOP and len(w) > 1 and not w.isdigit():
            out.append(w)
    return out


def numbers(text):
    """Every number in the text except prices — sizes, lengths, weights ('2in', '110 yds' → {'2','110'})."""
    t = re.sub(r"\$\s?\d[\d,]*(?:\.\d+)?", " ", str(text or ""))
    return {_num(n) for n in re.findall(r"\d+(?:\.\d+)?", t)}


def product_features(p):
    """Everything matching needs, computed once per product row."""
    text = " ".join(str(p.get(k) or "") for k in ("name", "description"))
    toks = tokens(text)
    return {
        "sku": normalize_sku(p.get("sku")),
        "mfr": normalize_sku(p.get("mfr_part")),
        "dims": dimensions(text),
        "tokens": set(toks),
        "numbers": numbers(text) - ({str(p["pack_qty"])} if p.get("pack_qty") else set()),
        "pack": p.get("pack_qty"),
        "joined": " ".join(sorted(set(toks))),
    }


def match_score(fa, fb):
    """(score 0..1, method) for two product_features dicts."""
    for k in ("mfr", "sku"):
        if fa[k] and fa[k] == fb[k]:
            return 1.0, "same part number" if k == "mfr" else "same item number"
    if fa["dims"] and fb["dims"] and fa["dims"] != fb["dims"]:
        return 0.0, "different size"
    ta, tb = fa["tokens"], fb["tokens"]
    if not ta or not tb:
        return 0.0, "no name"
    shared = ta & tb
    jac = len(shared) / len(ta | tb)
    seq = SequenceMatcher(None, fa["joined"], fb["joined"]).ratio()
    na, nb = fa["numbers"], fb["numbers"]
    num = len(na & nb) / len(na | nb) if (na or nb) else 0.5
    if na and nb and not (na & nb):
        return round(0.3 * jac, 3), "different numbers"
    score = 0.5 * jac + 0.3 * seq + 0.2 * num
    if fa["dims"] and fa["dims"] == fb["dims"]:
        score = min(0.98, score + 0.25)
        if shared:
            score = max(score, 0.8 if fa["pack"] and fa["pack"] == fb["pack"] else 0.6)
        return round(score, 3), "same size + similar name"
    return round(score, 3), "similar name"


def confidence_label(score):
    if score >= 0.999:
        return "exact"
    if score >= 0.75:
        return "likely"
    if score >= 0.45:
        return "possible — check"
    return "no match"


# ── Grouping across vendors ───────────────────────────────────────────────────────────────────────
def group_products(products, threshold=0.45):
    """Cluster product rows (each has 'vendor') into groups holding at most one row per vendor.

    Greedy and deterministic: rows are visited in input order; each joins the best-scoring existing
    group that has no row from its vendor yet (blocked by shared token / size / part number so it
    stays fast for thousands of rows), otherwise starts a new group.
    Returns a list of {"members": {vendor: row}, "score": float, "method": str}."""
    groups = []
    index = {}  # blocking key → set(group idx)

    def keys_for(f):
        ks = {"t:" + t for t in f["tokens"] if len(t) > 2}
        if f["dims"]:
            ks.add("d:" + "x".join(f["dims"]))
        for k in ("sku", "mfr"):
            if f[k]:
                ks.add("p:" + f[k])
        return ks

    for p in products:
        f = product_features(p)
        vendor = p.get("vendor")
        cands = set()
        for k in keys_for(f):
            cands |= index.get(k, set())
        best, best_s, best_m = None, 0.0, ""
        for gi in sorted(cands):
            g = groups[gi]
            if vendor in g["members"]:
                continue
            s, m = min((match_score(f, gf) for gf in g["_feats"]), key=lambda x: x[0])
            if s > best_s:
                best, best_s, best_m = gi, s, m
        if best is not None and best_s >= threshold:
            g = groups[best]
            g["members"][vendor] = p
            g["_feats"].append(f)
            g["score"] = min(g["score"], best_s)
            g["method"] = best_m if g["method"] == "only one vendor" else g["method"]
            gi = best
        else:
            groups.append({"members": {vendor: p}, "_feats": [f], "score": 1.0, "method": "only one vendor"})
            gi = len(groups) - 1
        for k in keys_for(f):
            index.setdefault(k, set()).add(gi)
    for g in groups:
        g.pop("_feats", None)
    return groups


def unit_price(p):
    price = p.get("price")
    pack = p.get("pack_qty")
    if price is None:
        return None
    return round(price / pack, 4) if pack else price


def _rank_key(p):
    """Cheapest first, in-stock before unknown before backorder; out of stock last."""
    order = {"in_stock": 0, "unknown": 1, "backorder": 2, "out_of_stock": 3}
    up = unit_price(p)
    return (order.get(p.get("availability") or "unknown", 1), up if up is not None else float("inf"))


def comparison_rows(groups, vendors):
    """One dict per group that has ≥2 vendors, plus the list of single-vendor rows.

    Compares per-unit price when every member states its pack size, otherwise the listed price —
    and says which basis it used."""
    compared, singles = [], []
    for g in groups:
        mem = g["members"]
        if len(mem) < 2:
            singles.extend(mem.values())
            continue
        priced = [p for p in mem.values() if p.get("price") is not None]
        basis = "per unit" if priced and all(p.get("pack_qty") for p in priced) else "listed price"
        val = (lambda p: unit_price(p)) if basis == "per unit" else (lambda p: p.get("price"))
        ranked = sorted(priced, key=lambda p: (_rank_key(p)[0] == 3, val(p)))
        row = {"product": max((p.get("name") or "" for p in mem.values()), key=len),
               "match": confidence_label(g["score"]), "match_score": g["score"], "match_method": g["method"],
               "basis": basis, "best_vendor": None, "best_price": None, "next_price": None,
               "savings": None, "savings_pct": None, "note": ""}
        for v in vendors:
            p = mem.get(v)
            row[v] = p
        if ranked:
            best = ranked[0]
            row["best_vendor"] = best["vendor"]
            row["best_price"] = val(best)
            others = [val(p) for p in priced if p is not best]
            if others:
                nxt = min(others)
                row["next_price"] = nxt
                row["savings"] = round(nxt - val(best), 4)
                row["savings_pct"] = round((nxt - val(best)) / nxt, 4) if nxt else None
            if best.get("availability") != "in_stock":
                row["note"] = "cheapest option is not confirmed in stock"
            cheapest_any = min(priced, key=val)
            if cheapest_any is not best:
                row["note"] = f"{cheapest_any['vendor']} is cheaper but out of stock"
        compared.append(row)
    compared.sort(key=lambda r: -(r["savings"] or 0))
    return compared, singles


# ── Shopping list → order plan ────────────────────────────────────────────────────────────────────
def search_score(query, p):
    q = set(tokens(query))
    qd = dimensions(query)
    f = product_features(p)
    if qd and f["dims"] and qd != f["dims"]:
        return 0.0
    qs = normalize_sku(query)
    if qs and qs in (f["sku"], f["mfr"]):
        return 1.0
    if not q:
        return 0.0
    hit = len(q & f["tokens"]) / len(q)
    if qd and f["dims"] == qd:
        hit = min(1.0, hit + 0.3)
    return round(hit, 3)


def plan_order(shopping_list, products, min_score=0.6):
    """For each {"search", "qty"} line, the best match per vendor and the cheapest in-stock pick."""
    plan = []
    for line in shopping_list:
        q, qty = line.get("search") or "", int(line.get("qty") or 1)
        per_vendor = {}
        for p in products:
            s = search_score(q, p)
            if s < min_score or p.get("price") is None:
                continue
            cur = per_vendor.get(p["vendor"])
            if cur is None or (s, -p["price"]) > (cur[0], -cur[1]["price"]):
                per_vendor[p["vendor"]] = (s, p)
        options = sorted((p for _, p in per_vendor.values()), key=_rank_key)
        pick = next((p for p in options if p.get("availability") != "out_of_stock"), None)
        plan.append({"search": q, "qty": qty, "options": options, "pick": pick,
                     "line_total": round(pick["price"] * qty, 2) if pick else None,
                     "note": "" if pick else ("no match found" if not options else "all matches out of stock")})
    return plan
