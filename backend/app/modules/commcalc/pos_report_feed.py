"""THE ONE HOME for "what does a line on an external POS report mean in THIS tenant's own declared
vocabulary". PURE — rows in, dicts out; stdlib plus the platform's portal value readers.

THE OWNER'S WORDS (2026-10-12), and the part of them this module answers: *"the reports should be
checked against what the employees declare daily … but i dont wnat them to be hard coded it should be
a design effect"*.

THE CLASS. A portal report arrives as the PORTAL's words — one row per worker per product category
per day, labelled however that vendor labels things. What the business declares at closing is the
TENANT's words, and §76 made those a config registry (`closing/metric_catalog.py`): a tenant adds a
category and every surface picks it up. Joining the two is one question — "which declared category is
this report line?" — and the wrong answers to it are both worse than no answer:

  • SPELL THE CATEGORIES IN CODE. Then the tenant who adds a category tomorrow has no report line, the
    tenant on a different portal has none at all, and the vendor's wording is frozen into the repo.
    This is exactly what the owner's last sentence forbids, and RULE TWO forbids it house-wide.
  • GUESS. A report line that resembles two categories, resolved to the closer one, produces a
    comparison that reads as evidence and is not. A line this module cannot place is REPORTED as
    unmapped — never dropped, never guessed, and never used to invent a category nobody declared.

So the match is resolved in a stated order, and every row carries the BASIS it was matched on, so a
consumer can tell a declared mapping from a text coincidence. The §66 rule applies and is why `basis`
exists at all: a surface that reads a value without reading its basis treats a last-resort placement
as evidence.

THE MATCH LADDER (`match_metric`), best first:
  1. `line_map`   — the tenant SAID this portal line is this category (config: a per-source
                    `portal_calibration.line_map`, edited on the data-source screen). Authoritative.
  2. `label`      — the portal's wording equals a declared category's own label, normalized. This is
                    the "design effect": name the category what the portal calls it and nothing needs
                    mapping at all.
  3. `label_part` — the portal's wording CONTAINS exactly one declared category's label (or the
                    reverse). Accepted only when exactly one candidate matches; two candidates is
                    `ambiguous`, which is an unmapped line with a reason, not a coin toss.
  4. unmatched    — reported with its label and the money it carried, so the admin can map it or add
                    the category. Its figures are NOT silently folded into another category.

A metric key is never invented here. The catalog is the tenant's, built by `closing/metric_catalog`
from the count-field and tender registries; this module only ever points at a key that catalog
already has.

Proof: `backend/harness_pos_report_feed.py`. The value readers (`money`, `iso_date`) are dereferenced
from `merchant_portals` — the platform's existing PURE portal-export readers, proven by
`harness_merchant_portals.py`. An eleventh private money parser is not a thing this repo needs.
"""
import re

from app.modules.commcalc.merchant_portals import iso_date, money

# ── the columns a POS report line can carry ──────────────────────────────────────────────────────
# House defaults. A tenant whose export carries a header we have not seen adds it per source in
# `portal_calibration.column_synonyms` (RULE TWO: config, not a code change). Matching is normalized
# and LONGEST-SYNONYM-FIRST, so "net sales" wins over "sales" on a header containing both.
#
# NOTHING in this table is a category. These are the AXES of a report — when it happened, where, who,
# what the line was called, how many and how much. The categories themselves are never spelled here.
COLUMN_SYNONYMS = {
    "business_date": ("business date", "transaction date", "trans date", "date", "sale date",
                      "activity date", "post date", "day"),
    "store_code": ("store code", "store number", "store no", "store id", "location code",
                   "location number", "center", "center number", "franchise id"),
    "store_label": ("store", "store name", "location", "location name", "branch", "site",
                    "business name", "dba"),
    "employee": ("employee", "employee name", "worker", "worker name", "user", "user name",
                 "cashier", "salesperson", "sales person", "associate", "rep", "clerk", "operator",
                 "team member"),
    "line_label": ("product category", "category", "product", "product name", "item", "item name",
                   "description", "service", "service type", "department", "class", "report line",
                   "type"),
    "units": ("quantity", "qty", "units", "unit", "count", "items", "pieces", "transactions",
              "transaction count", "number of items", "no of items"),
    "amount": ("net sales", "net amount", "extended price", "ext price", "total amount", "amount",
               "sales", "sales amount", "revenue", "total", "line total", "net"),
}

_MONEY_FIELDS = ("amount",)

# The basis strings. A consumer MUST be able to tell a declared mapping from a text coincidence, so
# these are a shared vocabulary, not local prose. `component_is_evidence`'s lesson (index §66), in a
# second place that needed it.
BASIS_LINE_MAP = "line_map"          # the tenant declared this mapping
BASIS_LABEL = "label"                # the portal's wording IS the category's label
BASIS_LABEL_PART = "label_part"      # one-and-only-one containment match
BASIS_UNMATCHED = "unmatched"        # nothing claimed it
BASIS_AMBIGUOUS = "ambiguous"        # two categories could claim it — so neither does
BASES = (BASIS_LINE_MAP, BASIS_LABEL, BASIS_LABEL_PART, BASIS_UNMATCHED, BASIS_AMBIGUOUS)

# Which bases may be read as a tenant-declared fact about this line. An ALLOWLIST, so a basis added
# later reads as NOT declared until somebody rules on it — the §66 shape, deliberately.
DECLARED_BASES = (BASIS_LINE_MAP, BASIS_LABEL)


def basis_is_declared(basis) -> bool:
    """Did the tenant SAY this line is this category (as opposed to the text happening to match)?"""
    return str(basis or "") in DECLARED_BASES


def norm(v) -> str:
    """Normalized matching spelling: lowercase, non-alphanumerics collapsed to single spaces.
    `'2nd Day Air '` and `'2ND-DAY  AIR'` are the same key; `'B-1115'` and `'b 1115'` are too, which
    is the store-code case trap this platform has already been bitten by once."""
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9]+", " ", str(v or "").lower())).strip()


def map_headers(header_row, synonyms=None):
    """{field: column index} for the headers we recognize. Longest synonym first; a field already
    claimed by an earlier (longer) synonym is not re-claimed."""
    syn = dict(COLUMN_SYNONYMS)
    for field, extra in (synonyms or {}).items():
        if field in syn and extra:
            syn[field] = tuple(extra) + tuple(syn[field])
        elif extra:
            syn[field] = tuple(extra)
    pairs = []
    for field, words in syn.items():
        for w in words:
            pairs.append((len(w), w, field))
    pairs.sort(reverse=True)
    out = {}
    for i, cell in enumerate(header_row or []):
        h = norm(cell)
        if not h:
            continue
        for _n, w, field in pairs:
            if field in out:
                continue
            if h == norm(w) or norm(w) in h:
                out[field] = i
                break
    return out


def header_score(row, synonyms=None) -> int:
    """How many of our fields this row's cells look like — what `portal_report_pull.read_table` uses
    to tell the real header from the title banner printed above it."""
    return len(map_headers(row, synonyms))


# ── the match ladder ─────────────────────────────────────────────────────────────────────────────
def _catalog_labels(catalog):
    """{normalized label: [metric keys]} — a list, because two categories CAN share a label and that
    is precisely the case this module must refuse to resolve."""
    out = {}
    for m in (catalog or []):
        k = str(m.get("key") or "").strip()
        if not k:
            continue
        for text in (m.get("label"), m.get("key")):
            n = norm(text)
            if n:
                out.setdefault(n, [])
                if k not in out[n]:
                    out[n].append(k)
    return out


def _norm_line_map(line_map, catalog_keys):
    """{normalized portal label: metric key} from the tenant's config, dropping any entry that points
    at a category the catalog does not have. A mapping to a deleted category is not honoured silently:
    `normalize` reports it, because a rule or a target may still be pointing at it."""
    out, stale = {}, []
    for raw, key in (line_map or {}).items():
        n, k = norm(raw), str(key or "").strip()
        if not n or not k:
            continue
        if k in catalog_keys:
            out[n] = k
        else:
            stale.append({"line": str(raw), "metric_key": k})
    return out, stale


def match_metric(label, *, catalog=None, line_map=None):
    """(metric_key | None, basis) for one portal report line. PURE. See the ladder in the docstring.

    `line_map` is already-normalized `{norm label: key}` when it comes from `normalize`; a raw dict is
    accepted too, so this is callable on its own (the editor previews a mapping with it)."""
    n = norm(label)
    if not n:
        return None, BASIS_UNMATCHED
    keys = {str(m.get("key") or "").strip() for m in (catalog or []) if m.get("key")}
    lm = dict(line_map or {})
    if lm and any(norm(k) != k for k in lm):
        lm, _ = _norm_line_map(lm, keys)
    if n in lm:
        return lm[n], BASIS_LINE_MAP
    labels = _catalog_labels(catalog)
    exact = labels.get(n) or []
    if len(exact) == 1:
        return exact[0], BASIS_LABEL
    if len(exact) > 1:
        return None, BASIS_AMBIGUOUS
    hits = []
    for ln, ks in labels.items():
        if len(ln) < 4:
            continue                      # a two-letter label matches half the report
        if ln in n or n in ln:
            for k in ks:
                if k not in hits:
                    hits.append(k)
    if len(hits) == 1:
        return hits[0], BASIS_LABEL_PART
    if len(hits) > 1:
        return None, BASIS_AMBIGUOUS
    return None, BASIS_UNMATCHED


# ── the normalizer ───────────────────────────────────────────────────────────────────────────────
def _cell(row, idx):
    if idx is None or idx < 0 or idx >= len(row):
        return ""
    return row[idx]


def _units(v):
    s = str(v if v is not None else "").strip()
    if not s:
        return 0
    try:
        return int(round(money(s)))
    except Exception:
        return 0


def normalize(table, *, catalog=None, line_map=None, calibration=None, org_id=None, source_id=None,
              connector=None, report_key=None, resolve_store=None, store_code_default=None):
    """One exported POS report → `{rows, skipped, warnings, unmapped, stale_map}`. PURE.

    A row is produced per (day, store, employee, metric). `employee_name` is kept EXACTLY as the
    portal spelled it and is never resolved to a person here — "which spelling is which employee" is
    `storeops` territory with its own one home, and a feed that guessed would be the §STOREOPS-01
    defect in a new place. The consumer joins on it.

    `resolve_store(raw) -> canonical` is the platform's ONE store resolver
    (`account/store_identity.build_store_resolver`, loaded by the caller). Omitted ⇒ the report's own
    spelling is kept and the row is still STORED, because a figure the portal published is evidence
    whether or not we can place it; an unplaced row is reported and excluded from store totals rather
    than being attributed to nobody and read as zero.
    """
    cal = dict(calibration or {})
    syn = cal.get("column_synonyms") or {}
    rows_out, skipped, warnings, unmapped = [], [], [], {}
    table = [list(r) for r in (table or []) if r is not None]
    if not table:
        return {"rows": [], "skipped": [], "warnings": ["The export was empty."], "unmapped": [],
                "stale_map": []}
    idx = map_headers(table[0], syn)
    if "line_label" not in idx:
        return {"rows": [], "skipped": [], "unmapped": [], "stale_map": [],
                "warnings": ["No column on this export names the report line (the category). Map the "
                             "export's own header on this source's column synonyms."]}
    keys = {str(m.get("key") or "").strip() for m in (catalog or []) if m.get("key")}
    lm, stale = _norm_line_map(dict(line_map or {}) or (cal.get("line_map") or {}), keys)
    for lineno, raw in enumerate(table[1:], start=2):
        label = str(_cell(raw, idx.get("line_label")) or "").strip()
        day = iso_date(_cell(raw, idx.get("business_date")))
        if not label and not day:
            continue                                      # a blank or total row, not a line
        if not day:
            skipped.append({"line": lineno, "why": "no date on the row", "label": label})
            continue
        if not label:
            skipped.append({"line": lineno, "why": "no report line on the row"})
            continue
        key, basis = match_metric(label, catalog=catalog, line_map=lm)
        units = _units(_cell(raw, idx.get("units")))
        amount = round(money(_cell(raw, idx.get("amount"))), 2)
        if not key:
            u = unmapped.setdefault(norm(label), {"line": label, "basis": basis, "units": 0,
                                                  "amount": 0.0, "rows": 0})
            u["units"] += units
            u["amount"] = round(u["amount"] + amount, 2)
            u["rows"] += 1
            continue
        store_raw = (str(_cell(raw, idx.get("store_code")) or "").strip()
                     or str(_cell(raw, idx.get("store_label")) or "").strip()
                     or str(store_code_default or "").strip())
        store_code = None
        if store_raw:
            store_code = store_raw
            if resolve_store is not None:
                try:
                    store_code = resolve_store(store_raw) or None
                except Exception:
                    store_code = None
        rows_out.append({
            "org_id": org_id, "source_id": source_id, "portal_key": connector,
            "report_key": report_key, "business_date": day,
            "store_code": store_code, "store_label": store_raw or None,
            "employee_name": (str(_cell(raw, idx.get("employee")) or "").strip() or None),
            "metric_key": key, "metric_basis": basis, "line_label": label,
            "units": units, "amount": amount, "source_line": lineno,
        })
    if stale:
        warnings.append("%d mapped report line(s) point at a category this tenant no longer declares."
                        % len(stale))
    if unmapped:
        warnings.append("%d report line(s) are not mapped to a declared category — map them on this "
                        "source, or add the category. Their figures are reported, not counted."
                        % len(unmapped))
    return {"rows": dedupe(rows_out), "skipped": skipped[:50], "warnings": warnings,
            "unmapped": sorted(unmapped.values(), key=lambda x: -x["amount"])[:50],
            "stale_map": stale[:50]}


# ── the grain ────────────────────────────────────────────────────────────────────────────────────
KEY_FIELDS = ("business_date", "store_code", "employee_name", "metric_key")


def row_key(r):
    return tuple(norm(r.get(f)) if f != "business_date" else str(r.get(f) or "")
                 for f in KEY_FIELDS)


def dedupe(rows):
    """SUM rows that share the landing grain. A report can print several lines for one category in a
    day — one per product inside it, or one per transaction — and the table's grain is the day, so
    they must be summed BEFORE the upsert. Upserting them one by one would let the last line overwrite
    the rest and a category's day would read as one product's total. (The settlement feed learned this
    the same way; `dedupe_settlement` is the same lesson at a different grain.)"""
    out, order = {}, []
    for r in rows or []:
        k = row_key(r)
        if k not in out:
            out[k] = dict(r)
            order.append(k)
            continue
        cur = out[k]
        cur["units"] = (cur.get("units") or 0) + (r.get("units") or 0)
        cur["amount"] = round((cur.get("amount") or 0.0) + (r.get("amount") or 0.0), 2)
        # The BASIS of a folded row is the WEAKEST of the lines folded into it: if one line was only a
        # text coincidence, the sum is not a tenant-declared fact either.
        if BASES.index(str(r.get("metric_basis"))) > BASES.index(str(cur.get("metric_basis"))):
            cur["metric_basis"] = r.get("metric_basis")
    return [out[k] for k in order]


def totals_by_store_day(rows):
    """{(store_code, business_date, metric_key): {units, amount}} — the shape a comparison reads.
    Rows with no resolved store are EXCLUDED (and were reported by `normalize`): folding them into a
    store's total would make that store read high, and dropping them silently would make it read low.
    Which is why they are neither."""
    out = {}
    for r in rows or []:
        sc = r.get("store_code")
        if not sc:
            continue
        k = (sc, str(r.get("business_date") or ""), str(r.get("metric_key") or ""))
        agg = out.setdefault(k, {"units": 0, "amount": 0.0, "declared_basis": True})
        agg["units"] += r.get("units") or 0
        agg["amount"] = round(agg["amount"] + (r.get("amount") or 0.0), 2)
        if not basis_is_declared(r.get("metric_basis")):
            agg["declared_basis"] = False
    return out
