"""COST CENTERS + PROFIT CENTERS — a new finance dimension over the ONE statement engine (owner 2026-09-25, mig 1022,
index §37). PURE at the top.

OWNER, verbatim (abridged): *"create cost centers … there are detailed cost centers assigned by ups and dedicated
profit centers."*

THE DIMENSION (nothing existed — duplicate check in index §37): one per-org table `commcalc.finance_center`
(center_type 'cost' | 'profit', code, name, parent_code, external_ref — the franchisor-assigned number, entered or
imported by the tenant), plus two maps:
  · `commcalc.profit_center_store` — store → profit center. A PROFIT CENTER IS A SET OF STORES, so its P&L is a SCOPE
    of the existing statement engine: `profit_center_scopes()` hands `statement_engine._scopes` one more scope family
    (`profit_center:<code>`, the stores mapped to the center and its descendants) exactly like `company:<id>`. There is
    no sibling P&L: the same `engine._assemble`, the same journal grain rule, the same snapshots, the same filter
    composition (`statement_filter.scope_predicate` knows the prefix and fails CLOSED).
  · `commcalc.pl_line_cost_center` — P&L line (optionally one drill-down detail label of it) → cost center. A COST
    CENTER IS A SET OF LINES, so its view is a REGROUPING of an assembled statement (`cost_center_view()`): every dollar
    of the statement lands in exactly one center or in "untagged", and the view's totals are pinned equal to the
    statement's by the harness. It never computes a number of its own.
No franchisor, vertical or tenant is spelled here (RULE TWO): codes and names are the tenant's data.
"""
from __future__ import annotations

import re
from decimal import Decimal, ROUND_HALF_UP

CENTER_TYPES = ("cost", "profit")
SCOPE_PREFIX = "profit_center:"
PL_SECTION_ORDER = ("revenue", "cogs", "opex", "other")
_CODE_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 ._/-]{0,39}$")
CENT = Decimal("0.01")


def _s(v):
    return "" if v is None else str(v).strip()


def _d(v):
    try:
        return Decimal(str(v or 0)).quantize(CENT, rounding=ROUND_HALF_UP)
    except Exception:
        return Decimal("0.00")


def valid_code(code):
    return bool(_CODE_RE.match(_s(code)))


def normalise_center(row):
    return {"center_type": _s(row.get("center_type")) or "cost", "code": _s(row.get("code")),
            "name": _s(row.get("name")) or _s(row.get("code")), "parent_code": _s(row.get("parent_code")) or None,
            "external_ref": _s(row.get("external_ref")) or None, "is_active": row.get("is_active") is not False,
            "notes": _s(row.get("notes")) or None}


def centers_of(rows, center_type):
    return [c for c in (normalise_center(r) for r in rows or []) if c["center_type"] == center_type and c["code"]]


def tree_problems(centers):
    """A parent that does not exist, and a cycle, in words — reported, never silently re-parented."""
    by = {c["code"]: c for c in centers}
    out = []
    for c in centers:
        if c["parent_code"] and c["parent_code"] not in by:
            out.append(f"'{c['code']}' names a parent '{c['parent_code']}' that does not exist")
        seen, cur = set(), c
        while cur and cur["parent_code"]:
            if cur["code"] in seen:
                out.append(f"'{c['code']}' is in a parent cycle")
                break
            seen.add(cur["code"])
            cur = by.get(cur["parent_code"])
    return sorted(set(out))


def descendants(code, centers):
    """`code` and every center under it (cycle-safe)."""
    kids = {}
    for c in centers:
        if c["parent_code"]:
            kids.setdefault(c["parent_code"], []).append(c["code"])
    out, stack = set(), [code]
    while stack:
        k = stack.pop()
        if k in out:
            continue
        out.add(k)
        stack.extend(kids.get(k, []))
    return out


def ancestors(code, centers):
    by = {c["code"]: c for c in centers}
    out, cur, seen = [], by.get(code), set()
    while cur and cur["parent_code"] and cur["code"] not in seen:
        seen.add(cur["code"])
        out.append(cur["parent_code"])
        cur = by.get(cur["parent_code"])
    return out


def store_map_index(map_rows, resolve=None):
    """{canonical store key: profit center code} — the store is resolved through the P&L's OWN store resolver
    (coa.store_resolver), so a profit center holds exactly the store key the statement books under."""
    out, dupes = {}, {}
    for r in map_rows or []:
        raw = _s(r.get("store_ref"))
        code = _s(r.get("profit_center_code"))
        if not raw or not code:
            continue
        key = (resolve(raw) if resolve else raw) or raw
        if key in out and out[key] != code:
            dupes.setdefault(key, sorted({out[key], code}))
            continue
        out[key] = code
    return out, dupes


def profit_center_stores(code, centers, store_index):
    """The canonical stores of profit center `code` and every center under it."""
    fam = descendants(code, centers)
    return {s for s, c in store_index.items() if c in fam}


def profit_center_scopes(centers, store_index):
    """[(scope_key, label, stores_in_scope, include_company_wide=False)] for every ACTIVE profit center — the tuple
    shape `statement_engine._scopes` yields. No profit center ⇒ [] ⇒ the engine's scope list is byte-identical."""
    out = []
    for c in sorted((c for c in centers if c["center_type"] == "profit" and c["is_active"]), key=lambda c: c["code"]):
        label = f"Profit center {c['code']} — {c['name']}" if c["name"] != c["code"] else f"Profit center {c['code']}"
        out.append((SCOPE_PREFIX + c["code"], label, profit_center_stores(c["code"], centers, store_index), False))
    return out


def center_for_external_ref(ref, centers, center_type="profit"):
    """The center whose code or external_ref equals `ref` (case-insensitive) — how a franchisor report's center number
    finds its profit center. Ambiguous ⇒ None (never a guess)."""
    r = _s(ref).lower()
    if not r:
        return None
    hits = [c for c in centers if c["center_type"] == center_type and (c["code"].lower() == r or _s(c["external_ref"]).lower() == r)]
    return hits[0] if len(hits) == 1 else None


def store_for_center(ref, centers, store_index):
    """A report's center number → the ONE store mapped to its profit center, else None (reported, not guessed)."""
    c = center_for_external_ref(ref, centers)
    if not c:
        return None, "no profit center carries this code or external reference"
    stores = sorted(s for s, code in store_index.items() if code == c["code"])
    if len(stores) == 1:
        return stores[0], None
    return None, ("profit center %s has no store mapped" % c["code"]) if not stores else \
        ("profit center %s maps %d stores — pick the store on the report" % (c["code"], len(stores)))


# ── the cost-center view: a REGROUPING of an assembled P&L ─────────────────────────────────────────
def tag_index(tag_rows):
    """{(pl_line_key, detail_label|None): cost_center_code}."""
    out = {}
    for r in tag_rows or []:
        k, cc = _s(r.get("pl_line_key")), _s(r.get("cost_center_code"))
        if k and cc:
            out[(k, _s(r.get("detail_label")) or None)] = cc
    return out


def cost_center_view(pl_payload, tags, centers=None, rollup=True):
    """Every line of an assembled P&L (engine._assemble output) placed in ONE cost center or 'untagged':
      · a detail label tagged on its own goes to that label's center;
      · the rest of the line goes to the line's tag, else untagged.
    `rollup` adds each center's figures to its ancestors (a parent's total includes its children). Totals per center:
    revenue, cogs, opex, other, gross_profit, net_income. The untagged bucket + every LEAF assignment tie to the
    statement to the cent (`tie` in the result)."""
    cc = {c["code"]: c for c in (centers or []) if c["center_type"] == "cost"}
    buckets = {}

    def bucket(code):
        if code not in buckets:
            c = cc.get(code)
            buckets[code] = {"code": code, "name": (c or {}).get("name") or ("Untagged" if code is None else code),
                             "known": code is None or code in cc,
                             "parent_code": (c or {}).get("parent_code"),
                             "sections": {s: Decimal("0.00") for s in PL_SECTION_ORDER}, "lines": []}
        return buckets[code]

    stmt = {s: Decimal("0.00") for s in PL_SECTION_ORDER}
    for sec in (pl_payload or {}).get("sections") or []:
        st = sec.get("type")
        if st not in stmt:
            continue
        for ln in sec.get("lines") or []:
            amt = _d(ln.get("amount"))
            stmt[st] += amt
            key = ln.get("key")
            rest = amt
            for dl, dv in (ln.get("detail") or {}).items():
                code = tags.get((key, dl))
                if code:
                    v = _d(dv)
                    b = bucket(code)
                    b["sections"][st] += v
                    b["lines"].append({"key": key, "label": f"{ln.get('label')} — {dl}", "section": st, "amount": float(v)})
                    rest -= v
            if rest:
                code = tags.get((key, None))
                b = bucket(code)
                b["sections"][st] += rest
                b["lines"].append({"key": key, "label": ln.get("label"), "section": st, "amount": float(rest)})
    leaf_total = {s: sum((b["sections"][s] for b in buckets.values()), Decimal("0.00")) for s in PL_SECTION_ORDER}
    tie = all(leaf_total[s] == stmt[s] for s in PL_SECTION_ORDER)
    rolled = {k: {s: v for s, v in b["sections"].items()} for k, b in buckets.items()}
    if rollup and centers:
        for code, b in list(buckets.items()):
            if code is None:
                continue
            for anc in ancestors(code, list(cc.values())):
                ab = bucket(anc)
                rolled.setdefault(anc, {s: Decimal("0.00") for s in PL_SECTION_ORDER})
                for s in PL_SECTION_ORDER:
                    rolled[anc][s] += b["sections"][s]

    def fig(sec):
        rev, cogs, opex, other = (sec[s] for s in PL_SECTION_ORDER)
        gp = rev - cogs
        return {"revenue": float(rev), "cogs": float(cogs), "opex": float(opex), "other": float(other),
                "gross_profit": float(gp), "net_income": float(gp - opex - other)}
    out = []
    for code, b in sorted(buckets.items(), key=lambda kv: (kv[0] is None, kv[0] or "")):
        out.append({"code": code, "name": b["name"], "known": b["known"], "parent_code": b["parent_code"],
                    "own": fig(b["sections"]), "rolled_up": fig(rolled.get(code, b["sections"])), "lines": b["lines"]})
    return {"centers": out, "statement": fig(stmt), "tie": tie,
            "unknown_codes": sorted(k for k, b in buckets.items() if k is not None and not b["known"])}


# ── I/O (every read org-scoped) ──────────────────────────────────────────────────────────────────────
def load_centers(client, org_id):
    try:
        return [normalise_center(r) for r in (client.schema("commcalc").table("finance_center").select("*")
                                              .eq("org_id", org_id).execute().data) or []]
    except Exception:
        return []


def load_store_map(client, org_id):
    try:
        return (client.schema("commcalc").table("profit_center_store").select("store_ref,profit_center_code")
                .eq("org_id", org_id).execute().data) or []
    except Exception:
        return []


def load_line_tags(client, org_id):
    try:
        return (client.schema("commcalc").table("pl_line_cost_center").select("pl_line_key,detail_label,cost_center_code")
                .eq("org_id", org_id).execute().data) or []
    except Exception:
        return []


def load_profit_scopes(client, org_id, resolve=None):
    """The profit-center scope family for `statement_engine._scopes` (org-scoped; [] on any failure)."""
    centers = load_centers(client, org_id)
    if not any(c["center_type"] == "profit" for c in centers):
        return []
    idx, _d = store_map_index(load_store_map(client, org_id), resolve)
    return profit_center_scopes(centers, idx)
