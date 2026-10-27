"""WHICH COLUMN OF AN EXPORTED REPORT IS WHICH FACT — the one home (index §COMMCALC-38).

WHY THIS MODULE EXISTS. Every portal feed that lands a downloaded report faces the same question
before it can read a single figure: this export's header row says "Trans Date", "Ext Price",
"Center Number" — which of our facts is each of those? `pos_report_feed` answered it with a
normalizer, a longest-synonym-first header matcher and a header-row scorer. A second feed needed the
same three things against its OWN vocabulary, and the only thing standing between this repository and
a copied normalizer was that nobody had looked.

A copied normalizer is the patchwork the house rules forbid, and it fails in a specific, expensive
way: `norm` is what makes `'B-1115'` and `'b 1115'` one store key, and this platform has already been
bitten once by a store code compared under one rule here and another rule there (a phantom store
holding $289). Two normalizers is that defect waiting for its second chance.

SO: the MECHANISM lives here and the VOCABULARY lives with each feed. This module knows how to score
a header row and map its columns; it does not know what a POS report or an invoice is, and it names no
column, no vendor and no tenant. Each feed passes its own `base` table and the tenant's declared
extras from `portal_calibration.column_synonyms` (RULE TWO: an unseen header is a config row).

Proof: `backend/harness_report_columns.py` — DB-free, browser-free, and it fails the build if either
feed stops dereferencing this home or grows a normalizer of its own.
"""
import re


def norm(v) -> str:
    """Normalized matching spelling: lowercase, non-alphanumerics collapsed to single spaces.

    `'2nd Day Air '` and `'2ND-DAY  AIR'` are the same key; `'B-1115'` and `'b 1115'` are too, which
    is the store-code case trap this platform has already been bitten by once. THE one spelling rule
    — a second one is a store that exists twice.
    """
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9]+", " ", str(v or "").lower())).strip()


def merge_synonyms(base, extra=None):
    """The feed's house vocabulary with the tenant's declared extras in FRONT of it.

    In front, not behind: a tenant who declares a synonym is telling us what their export calls the
    thing, and a house default that happens to also match must not outrank them. PURE, and it never
    mutates `base` — a caller passing its module-level table would otherwise accumulate one tenant's
    vocabulary into every later tenant's.
    """
    out = {f: tuple(words) for f, words in dict(base or {}).items()}
    for field, words in (extra or {}).items():
        if not words:
            continue
        if isinstance(words, str):
            words = (words,)
        out[field] = tuple(words) + tuple(out.get(field, ()))
    return out


def map_headers(header_row, base, extra=None):
    """{field: column index} for the headers this vocabulary recognizes.

    LONGEST SYNONYM FIRST, so "net sales" wins over "sales" on a header that contains both, and a
    field already claimed by an earlier (longer) synonym is not re-claimed. Matching is on the
    normalized spelling, by equality or containment — an export that prints "Trans Date (local)"
    still reads as the date.
    """
    syn = merge_synonyms(base, extra)
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


def header_score(row, base, extra=None) -> int:
    """How many of this vocabulary's fields a row's cells look like.

    `portal_report_pull.read_table` uses it to tell the REAL header from the title banner a portal
    prints above it — the banner scores 0 or 1, the header scores several.
    """
    return len(map_headers(row, base, extra))


def cell(row, idx, default=None):
    """One cell, or `default`. The index may be absent (the column was not in this export) or past
    the end (a short row, which portals produce on subtotal lines). Never raises — a missing cell is
    a missing fact, not a failed import. `default` exists because the two feeds disagree about what a
    missing cell should read as (`""` for the POS feed, `None` for the invoice feed) and that is a
    legitimate difference; the INDEXING is what had no business existing twice."""
    if idx is None:
        return default
    try:
        if int(idx) < 0:
            return default
        return row[idx]
    except (IndexError, TypeError, ValueError):
        return default


def money_or_none(v):
    """A money cell as a float, with "the cell said nothing" kept DISTINCT from "it said zero".

    The parsing is `merchant_portals.money` and only ever that — a second money parser in this
    repository would be the patchwork the house rules forbid. What is added here is one rule: a BLANK
    cell returns None rather than 0.0. A supplier invoice needs that distinction, because 0.0 asserts
    the supplier charged nothing and a blank asserts nothing at all (§COMMCALC-33's lesson, in a
    second place). A caller that wants today's 0.0 behaviour keeps calling `merchant_portals.money`.
    """
    from app.modules.commcalc.merchant_portals import money as _money
    return None if not str(v if v is not None else "").strip() else _money(v)
