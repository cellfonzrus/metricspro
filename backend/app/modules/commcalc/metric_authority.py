"""WHICH SOURCE ANSWERS "what did this store-day do" — the one home. PURE (no DB, no network).

THE OWNER'S WORDS (Sanjot, 2026-11-17): *"daily numbers shouold come from the daily sales report i
just uploaded"*, and *"let the feed be the authority till all reps are fully trained to enter daily
data, keep teh switch closed but open later when needed"*.

THE CLASS. Two sources have always answered the same question, and nothing could choose between
them:

  * THE DECLARATION — what a rep typed at closing. `commcalc.closing_metric_line` (mig 1072), folded
    by `closing/metric_catalog.declared` / `by_employee`, and the only thing `daily_incentive` could
    ever read. A store whose reps are not yet trained declares nothing, and the engine then pays
    nothing — correctly, because a gate it cannot verify fails closed, but uselessly.
  * THE FEED — what the store's own POS export says it sold, per worker, per declared category.
    `commcalc.portal_metric_day` (mig 1078, §COMMCALC-07). Deliberately never merged into the
    declaration table, because the whole point of the pair is that they can DISAGREE.

Choosing is a tenant's decision about its own operation, so it is a DECLARATION and never a default.
`SOURCE_DECLARATION` is what every tenant has today, which is why an absent declaration reads as that
and moves nobody: an absent config value that silently started paying off a feed would be a money
move nobody approved (the §STOREOPS-07 lesson, 2026-11-12).

NO SECOND READER, AND NO SECOND FOLD. `lines_from_feed` PROJECTS portal rows onto the exact shape
`metric_catalog` already takes — `{metric_key, units, amount, employee_name}` — so the catalog's fold,
its per-employee split, its figure rule and `daily_incentive` itself are all untouched. Writing a
parallel reader for the feed side would be the duplicate defect: two paths answering one question,
guaranteed to drift.

A GUESS MAY NOT BECOME A PAYMENT. `pos_report_feed` matches a report line to a declared category on a
ladder and stamps every row with the BASIS it matched on. Only a basis the tenant DECLARED
(`basis_is_declared` — an allowlist, so a basis added later reads as NOT declared until somebody
rules on it) is evidence here. A `label_part` containment match is a text coincidence; it is reported
by the feed and it does not pay. This is §66's rule in the place it decides money.
"""
from app.modules.commcalc import pos_report_feed as _feed

# ── Vocabulary. These constants ARE the vocabulary; nothing else may spell them. ─────────────────
SOURCE_DECLARATION = "declaration"   # what the reps typed at closing
SOURCE_FEED = "feed"                 # what the store's own POS export says
SOURCES = (SOURCE_DECLARATION, SOURCE_FEED)
SOURCE_DEFAULT = SOURCE_DECLARATION
SOURCE_LABELS = {
    SOURCE_DECLARATION: "What the reps declare at closing",
    SOURCE_FEED: "What the store's own sales report says",
}


def resolve(raw):
    """The declared authority. Blank/None reads as the house default — which is today's behaviour
    for every existing tenant, so an unanswered question changes nothing. Anything else is REFUSED
    rather than guessed: a mistyped value that silently fell back would move money. PURE."""
    v = str(raw or "").strip().lower() or SOURCE_DEFAULT
    if v not in SOURCES:
        raise ValueError("metric authority must be one of %s" % ", ".join(SOURCES))
    return v


def is_feed(raw) -> bool:
    return resolve(raw) == SOURCE_FEED


def lines_from_feed(portal_rows, *, business_date=None):
    """`portal_metric_day` rows → the `closing_metric_line` shape `metric_catalog` already reads.

    Only rows whose `metric_basis` the tenant DECLARED are carried: a containment match is a
    coincidence and a coincidence may not pay. A row with no metric key is not a line.

    `business_date` narrows to one day when the caller passed a wider window; omitted, every row
    given is used (the caller has already scoped the read). PURE.
    """
    want = str(business_date) if business_date else None
    out = []
    for r in (portal_rows or []):
        key = str((r or {}).get("metric_key") or "").strip()
        if not key:
            continue
        if not _feed.basis_is_declared((r or {}).get("metric_basis")):
            continue
        if want and str(r.get("business_date") or "")[:10] != want[:10]:
            continue
        out.append({
            "metric_key": key,
            "units": r.get("units") or 0,
            "amount": r.get("amount") or 0.0,
            "employee_name": (str(r.get("employee_name") or "").strip() or None),
            "store_code": r.get("store_code"),
            "close_date": str(r.get("business_date") or "")[:10] or None,
            "metric_basis": r.get("metric_basis"),
        })
    return out


def excluded_from_feed(portal_rows):
    """The rows the feed carried that may NOT pay, with the basis that disqualified them — so a
    surface can say "we saw 9 of these and none of them is evidence" rather than showing a silent
    zero. PURE."""
    out = []
    for r in (portal_rows or []):
        if not str((r or {}).get("metric_key") or "").strip():
            continue
        if _feed.basis_is_declared((r or {}).get("metric_basis")):
            continue
        out.append({"metric_key": r.get("metric_key"), "line_label": r.get("line_label"),
                    "basis": r.get("metric_basis"), "units": r.get("units") or 0,
                    "amount": r.get("amount") or 0.0})
    return out


def source_sentence(source, *, rows=None, excluded=0) -> str:
    """One line naming WHICH source answered, for every surface that shows a figure from here. A
    surface that prints a number without its source treats one of two disagreeing answers as the
    only one. PURE."""
    s = resolve(source)
    if s == SOURCE_DECLARATION:
        base = "%s: these figures are what the reps declared at closing." % SOURCE_DECLARATION
        return base if rows is None else "%s %d declared line(s)." % (base, int(rows or 0))
    base = ("%s: these figures are read from the store's own sales report, not from a rep's "
            "declaration." % SOURCE_FEED)
    if rows is not None:
        base += " %d line(s) from the report." % int(rows or 0)
    if excluded:
        base += (" %d further line(s) matched a category only by wording, so they are reported and "
                 "not counted." % int(excluded))
    return base
