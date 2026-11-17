"""THE WEEKLY REVIEW DECK — the pack as a presentation's CONTENT. PURE: no DB, no FastAPI, no clock,
and nothing here knows what a .pptx is.

OWNER ASK 2026-11-17, verbatim: *"The descriptive ppt is missing like one made before"* — the deck
built for the 17 Nov Zoom (`/mnt/project-files/luxelink-zoom-2026-11-17/`) explained every figure in
a sentence and the in-app review showed the figure alone.

TWO FILES, TWO JOBS, on purpose:

  · THIS file decides WHAT the deck says — the slides, their order, their words and their tables. It
    is a fold over the pack, so it is provable without a database and without PowerPoint.
  · `notify/render.py` decides what a deck LOOKS LIKE — it is already the house's one home for
    "turn a payload into a file" (xlsx, pdf), which is what the Send bar and the scheduled sends
    deliver through. A second file writer living in commcalc would be the duplicate defect, and the
    standing weekly email could not reuse it.

THE WORDS ARE NOT WRITTEN HERE EITHER. Every sentence comes from `weekly_review.narrative` /
`.headline`, already attached to the pack the screen renders — so the deck, the page and the export
cannot describe one figure three ways.
"""

#: Which columns a slide shows for each item's rows, and the heading over each. The screen's table
#: and the deck's table are the same question asked twice, so the shape is declared ONCE, here, and
#: the frontend reads it from the payload rather than keeping its own copy.
#:
#: `(heading, field)` — the field is the row key as the fold serves it.
ITEM_COLUMNS = {
    "profit": (("Store", "address"), ("Revenue", "revenue"), ("Net", "net_profit")),
    "accessory": (("Store", "store_code"), ("Target", "target"), ("Sold", "achieved"),
                  ("Gap", "gap")),
    "commission": (("Rep", "rep"), ("Store", "store_code"), ("Payout", "payout")),
    "rep_watch": (("Rep", "rep"), ("Store", "store_code"), ("Why", "verdict")),
    "aging": (("Carrier", "label"), ("Sell within", "sell_within_days"), ("Devices", "devices"),
              ("Past the window", "missed"), ("Owed", "owed")),
    "payroll": (("Store", "store_code"), ("Payroll", "amount")),
}

#: How many rows of an item's table a slide carries. A slide is not a spreadsheet: the point is the
#: worst few, and the exported file carries every row.
ROWS_PER_SLIDE = 12

#: Which way "worst first" runs for each item, so the rows a manager needs are the rows they see.
#: `(field, ascending)`; an item that is not here keeps the fold's own order.
ITEM_SORT = {
    "profit": ("net_profit", True),
    "accessory": ("gap", True),
    "commission": ("payout", False),
    "aging": ("missed", False),
    "payroll": ("amount", False),
}


def _f(v):
    try:
        if v is None or v == "":
            return None
        return float(v)
    except (TypeError, ValueError):
        return None


def item_rows(item, limit=ROWS_PER_SLIDE):
    """The rows a slide shows for one item: worst first where that is defined, capped.

    PURE. Sorting happens BEFORE the cap, because a cap applied to an unsorted list shows an
    arbitrary twelve and calls them the worst twelve.
    """
    rows = list((item or {}).get("rows") or [])
    spec = ITEM_SORT.get((item or {}).get("key"))
    if spec:
        field, ascending = spec
        rows.sort(key=lambda r: (_f(r.get(field)) is None, _f(r.get(field)) or 0.0),
                  reverse=not ascending)
    return rows[:max(0, int(limit or 0))]


def item_table(item, limit=ROWS_PER_SLIDE):
    """{headers, rows} for one item, or None where the item declares no columns (growth has no
    rows of its own — its figures are in its sentences)."""
    cols = ITEM_COLUMNS.get((item or {}).get("key"))
    if not cols:
        return None
    rows = item_rows(item, limit)
    if not rows:
        return None
    return {"headers": [h for h, _k in cols],
            "rows": [[r.get(k) for _h, k in cols] for r in rows]}


def _item_slide(item):
    """One slide per review item: the figure, what it says, and the rows behind it."""
    from app.modules.commcalc import weekly_review as _wrev
    measured = item.get("state") == _wrev.MEASURED and item.get("figure") is not None
    return {
        "kind": "item",
        "eyebrow": item.get("label") or "",
        "title": _wrev.figure_words(item) if measured else "Not measured",
        "bullets": list(item.get("narrative") or []),
        "table": item_table(item) if measured else None,
        # PROVENANCE ON THE SLIDE ITSELF. A review pack that cannot say which report owns a figure
        # invites the meeting to argue about which screen is right.
        "note": f"Source: {item.get('source') or ''}",
        "state": item.get("state"),
    }


def review_slides(review, period="", *, section=False):
    """The slides for ONE review (the company pack, or one market's)."""
    if not review:
        return []
    scope = review.get("scope") or {}
    out = []
    if section:
        out.append({"kind": "section", "eyebrow": "Market", "title": scope.get("label") or "",
                    "bullets": list(review.get("headline") or []), "table": None, "note": ""})
    for item in (review.get("items") or []):
        out.append(_item_slide(item))
    return out


def deck_payload(pack, *, company_label="", markets=True):
    """THE WHOLE DECK as content: a title slide, the agenda, the company's items, then each market.

    PURE — hand it the pack `/commcalc/weekly-review` serves and it answers without touching
    anything. `markets=False` produces the consolidated deck alone, which is the one the Monday
    meeting opens.
    """
    if not pack:
        return {"filename": "weekly-performance-review", "title": "Weekly Performance Review",
                "subtitle": "", "slides": []}
    week = pack.get("week") or {}
    company = pack.get("company")
    period = pack.get("period") or ""
    label = str(company_label or "").strip()
    title = "Weekly Performance Review"
    subtitle = " · ".join([x for x in (label, f"week {week.get('label') or ''}", period) if x])

    slides = [{"kind": "title", "eyebrow": label or "Performance review", "title": title,
               "bullets": list((company or {}).get("headline") or []),
               "table": None,
               "note": f"Generated {pack.get('as_of') or ''} · figures recomputed on open, "
                       f"never read back from a stored copy"}]
    agenda = [f"{i.get('label')} — {i.get('window')}" for i in (pack.get("items") or [])]
    if agenda:
        slides.append({"kind": "agenda", "eyebrow": "What this pack reviews", "title": "Agenda",
                       "bullets": agenda, "table": None,
                       "note": "Each item quotes the report that owns its figure — this pack "
                               "derives nothing of its own."})
    slides.extend(review_slides(company, period))
    if markets:
        for m in (pack.get("markets") or []):
            slides.extend(review_slides(m, period, section=True))
        unplaced = pack.get("unplaced")
        if unplaced and (unplaced.get("scope") or {}).get("store_codes"):
            slides.extend(review_slides(unplaced, period, section=True))
    base = "-".join(x for x in ["weekly-performance-review",
                                str(week.get("week_ending") or "")[:10]] if x)
    return {"filename": base, "title": title, "subtitle": subtitle, "slides": slides}
