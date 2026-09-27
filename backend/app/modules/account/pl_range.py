"""THE P&L OVER A MONTH RANGE — one column per month plus a Total (owner 2026-09-26).

Owner, verbatim: *"also need the p&L report to be exported for multiple months … all these need to be
platform wide"*.

WHAT THIS IS — AND IS NOT. The P&L page (`/accounts/pl`) reads ONE month through
`GET /account/pl/{period}` → `router.pl_single_month` (the stored `account_statements` snapshot for the
company / store scope, or — under a store / market filter — `statement_filter.filtered_statement`, the
sum of the matching per-store snapshots). A range is that SAME single-month read, LOOPED over the months
THE one enumeration (`_period.month_range`) lists, in `router.get_pl_range`. There is no second P&L
derivation: this module never reads a table, never books a line, never recomputes a subtotal or a gross
profit. It only lays the per-month statements side by side.

  • Every month cell is COPIED from that month's statement (a line's `amount`, a drill row's `detail`
    value, a section's `subtotal`, the statement's `gross_profit` / `net_operating_income` /
    `net_income`) — so a month column equals the P&L page for that month, to the cent, by construction.
  • The only arithmetic is the Total column: the sum of the month cells of the SAME row, in exact decimal
    cents (`_sum_cents`), so the Total equals the sum of the months to the cent — never a float drift.
  • ABSENCE IS NOT ZERO. A month that was never computed is a column of BLANKS (None) and is named in
    `missing_months`; a line a month's statement does not carry is BLANK in that month. Blanks are left
    out of the Total — never counted as $0.00. A row with no number in any month has a None Total.

ROW ORDER = the P&L page's order: Revenue, Cost of Goods Sold, **Gross Profit**, Operating Expenses,
**Net Operating Income**, Other, **Net Income** (`TOTALS_AFTER`), each section's lines in statement order
with the page's drill rows (the line's `detail`) under the line, then the section subtotal. Lines and drill
rows that appear in only some months are merged into the order where they first appear (`_merge_order`),
so a line that exists in one month only still sits where the page shows it that month.

PURE (no I/O, no app imports): rows in, grid out — the proof harness (`backend/harness_pl_range.py`) drives
it with fixtures built by the REAL `engine._assemble` and the REAL `statement_filter.aggregate`, and
`backend/harness_pl_range_lock.py` fails the build if a multi-month P&L path computes lines without the
single-month read. Index §4c.
"""
from decimal import Decimal, InvalidOperation

# Each month is one full single-month read (the snapshot + its staleness probe). Two years — the reach of
# the section-wide period switcher (period-context: 24 months) — is the widest window one request serves.
MAX_MONTHS = 24

# The page's headline rows and the section each one follows (the order `accounts/pl/page.tsx` renders:
# revenue, cogs, GROSS PROFIT, opex, NET OPERATING INCOME, other, NET INCOME). The values are the keys the
# statement payload already carries — nothing here computes them.
TOTALS_AFTER = (("cogs", "gross_profit", "Gross Profit"),
                ("opex", "net_operating_income", "Net Operating Income"),
                ("other", "net_income", "Net Income"))


def _num(v):
    """A statement amount as a float, or None when the statement carries no number there."""
    if v is None or v == "":
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _sum_cents(vals):
    """Σ of the non-blank cells, in exact decimal cents (None when every cell is blank)."""
    got = [v for v in vals if v is not None]
    if not got:
        return None
    tot = Decimal("0")
    for v in got:
        try:
            tot += Decimal(repr(float(v))).quantize(Decimal("0.01"))
        except (InvalidOperation, ValueError):
            continue
    return float(tot.quantize(Decimal("0.01")))


def _merge_order(existing, incoming):
    """Merge `incoming` (one month's order) into `existing` (the order so far), in place: a key not yet
    seen is inserted right after the key that precedes it in `incoming` — so a line present in only some
    months sits where those months show it. Keys already placed never move."""
    pos = -1
    for k in incoming:
        if k in existing:
            pos = existing.index(k)
        else:
            existing.insert(pos + 1, k)
            pos += 1
    return existing


def _line_ids(lines):
    """(key, occurrence) per line — the identity of a line across months. The occurrence index keeps two
    lines that share a key inside one statement (journal lines key off a truncated label) apart."""
    seen, out = {}, []
    for ln in lines or []:
        k = str((ln or {}).get("key") or (ln or {}).get("label") or "")
        n = seen.get(k, 0)
        seen[k] = n + 1
        out.append((k, n))
    return out


def month_statement(resp):
    """The statement payload of ONE single-month read, or None when that month is not computed."""
    r = resp or {}
    if not r.get("computed"):
        return None
    st = r.get("statement")
    return st if isinstance(st, dict) else None


def assemble(months, per_month):
    """`months` = the canonical month list, oldest first (`_period.month_range`); `per_month` =
    {month: the single-month P&L read's response, exactly as `GET /account/pl/{month}` returns it}.

    Returns
      {"months": [...],
       "month_status": [{"period", "computed", "stale", "computed_at", "newest_ingest_at",
                         "filtered", "matched_stores", "scope_label"}, …],
       "computed_months": [...], "missing_months": [...],
       "scope_label": the latest computed month's label (else None),
       "rows": [{"kind": "section"|"line"|"detail"|"subtotal"|"total", "section", "key", "label",
                 "line_kind", "amounts": [one per month, None = blank], "total"}, …],
       "notes": [{"note", "months": [...]}, …]}
    """
    months = list(months or [])
    stmts = [month_statement(per_month.get(m)) for m in months]

    # ── the section / line / drill ORDER: merged across the computed months ──────────────────────────
    sec_order, sec_name = [], {}
    line_order, line_label, line_kind = {}, {}, {}
    detail_order = {}
    for st in stmts:
        if st is None:
            continue
        secs = [s for s in (st.get("sections") or []) if isinstance(s, dict)]
        _merge_order(sec_order, [s.get("type") for s in secs])
        for s in secs:
            t = s.get("type")
            if s.get("name"):
                sec_name[t] = s.get("name")         # the latest computed month's wording wins
            lines = s.get("lines") or []
            ids = _line_ids(lines)
            _merge_order(line_order.setdefault(t, []), ids)
            for lid, ln in zip(ids, lines):
                line_label[(t, lid)] = ln.get("label") or lid[0]
                line_kind[(t, lid)] = ln.get("kind")
                _merge_order(detail_order.setdefault((t, lid), []), list((ln.get("detail") or {}).keys()))

    # ── per-month lookups (copies of the statement's own numbers) ────────────────────────────────────
    def _index(st):
        if st is None:
            return None
        secs = {}
        for s in st.get("sections") or []:
            if not isinstance(s, dict):
                continue
            lines = s.get("lines") or []
            secs[s.get("type")] = {"subtotal": _num(s.get("subtotal")),
                                   "lines": dict(zip(_line_ids(lines), lines))}
        return secs

    idx = [_index(st) for st in stmts]

    def _row(kind, section, key, label, amounts, line_kind_=None):
        return {"kind": kind, "section": section, "key": key, "label": label, "line_kind": line_kind_,
                "amounts": amounts, "total": _sum_cents(amounts)}

    rows = []
    anchors = {a: (k, lbl) for a, k, lbl in TOTALS_AFTER}
    placed = set()

    def _headline(key, label):
        rows.append(_row("total", None, key, label,
                         [None if st is None else _num(st.get(key)) for st in stmts]))
        placed.add(key)

    for t in sec_order:
        rows.append({"kind": "section", "section": t, "key": t, "label": sec_name.get(t) or t,
                     "line_kind": None, "amounts": [None] * len(months), "total": None})
        for lid in line_order.get(t, []):
            cells, lines_m = [], []
            for ix in idx:
                ln = None if ix is None else ((ix.get(t) or {}).get("lines") or {}).get(lid)
                lines_m.append(ln)
                cells.append(None if ln is None else _num(ln.get("amount")))
            rows.append(_row("line", t, lid[0], line_label.get((t, lid)), cells, line_kind.get((t, lid))))
            for dk in detail_order.get((t, lid), []):
                dcells = [None if ln is None else _num((ln.get("detail") or {}).get(dk)) for ln in lines_m]
                rows.append(_row("detail", t, lid[0], str(dk), dcells))
        rows.append(_row("subtotal", t, t, "Subtotal — " + (sec_name.get(t) or t),
                         [None if ix is None or t not in ix else ix[t]["subtotal"] for ix in idx]))
        if t in anchors:
            _headline(*anchors[t])
    for _a, key, label in TOTALS_AFTER:                # an anchor section no month carries: still shown
        if key not in placed:
            _headline(key, label)

    # ── status + notes (what the page says beside the numbers) ───────────────────────────────────────
    status, scope_label = [], None
    for m, st in zip(months, stmts):
        r = per_month.get(m) or {}
        if st is not None and st.get("scope_label"):
            scope_label = st.get("scope_label")
        status.append({"period": m, "computed": st is not None, "stale": bool(r.get("stale")),
                       "computed_at": r.get("computed_at"), "newest_ingest_at": r.get("newest_ingest_at"),
                       "filtered": bool(r.get("filtered")), "matched_stores": r.get("matched_stores"),
                       "scope_label": (st or {}).get("scope_label")})
    notes, note_ix = [], {}

    def _note(text, m):
        text = str(text or "").strip()
        if not text:
            return
        if text not in note_ix:
            note_ix[text] = len(notes)
            notes.append({"note": text, "months": []})
        if m not in notes[note_ix[text]]["months"]:
            notes[note_ix[text]]["months"].append(m)

    for m, st in zip(months, stmts):
        if st is None:
            continue
        for n in st.get("notes") or []:
            _note(n, m)
        for s in st.get("sections") or []:
            for ln in (s or {}).get("lines") or []:
                if ln.get("note"):
                    _note(f"{ln.get('label')}: not measured — {ln.get('note')}", m)
                cs = ln.get("commission_source")
                if isinstance(cs, dict) and cs.get("words"):
                    _note(f"{ln.get('label')}: {cs.get('words')}", m)

    computed = [m for m, st in zip(months, stmts) if st is not None]
    return {"months": months, "month_status": status, "computed_months": computed,
            "missing_months": [m for m in months if m not in computed], "scope_label": scope_label,
            "rows": rows, "notes": notes}


# ── THE EXPORT LAYOUT — one home for every renderer (the page's Excel / CSV / PDF / Print / Send, and the
# scheduled-report registry's server-side xlsx / pdf). Each renderer maps these {header, key, money} columns
# onto its own cell writer; none of them lays the grid out a second time. ──────────────────────────────────
def month_column_key(i):
    return f"m{i}"


def export_sheet(grid, name="P&L by month"):
    """The grid as ONE export sheet: Section · Line · one money column per month · Total. Drill rows sit
    under their line (Line = '↳ <detail>'), each section ends with its subtotal, and the page's headline
    rows (Gross Profit, Net Operating Income, Net Income) sit where the page shows them. A month never
    computed is headed '<month> (not computed)' and its cells are blank. Cells are the grid's own values —
    nothing is re-derived here."""
    g = grid or {}
    months = list(g.get("months") or [])
    missing = set(g.get("missing_months") or [])
    cols = [{"header": "Section", "key": "section"}, {"header": "Line", "key": "line"}]
    for i, m in enumerate(months):
        cols.append({"header": m + (" (not computed)" if m in missing else ""),
                     "key": month_column_key(i), "money": True})
    cols.append({"header": "Total", "key": "total", "money": True})
    sec_title = {r.get("section"): r.get("label") for r in (g.get("rows") or []) if r.get("kind") == "section"}
    rows = []
    for r in g.get("rows") or []:
        kind = r.get("kind")
        if kind == "section":
            continue                                     # the Section column carries the title
        title = sec_title.get(r.get("section")) or r.get("section")
        if kind == "total":
            section, line = "Totals", r.get("label")
        elif kind == "detail":
            section, line = title, "    ↳ " + str(r.get("label"))
        elif kind == "subtotal":
            section, line = title, "  " + str(r.get("label"))
        else:
            section, line = title, r.get("label")
        out = {"section": section, "line": line, "kind": kind, "total": r.get("total")}
        for i, v in enumerate(r.get("amounts") or []):
            out[month_column_key(i)] = v
        rows.append(out)
    return {"name": name, "columns": cols, "rows": rows}


def notes_sheet(grid, name="Notes"):
    """What the page says beside the numbers, so an export never loses it: every month never computed, every
    stale month, and the months' own notes (statement notes, 'not measured' lines, commission-source words)."""
    g = grid or {}
    rows = [{"months": m, "note": "Not computed — this month's column is blank and is not in the Total. "
                                  "Compute the period on the Account dashboard to include it."}
            for m in (g.get("missing_months") or [])]
    for st in g.get("month_status") or []:
        if st.get("stale"):
            rows.append({"months": st.get("period"),
                         "note": "STALE — newer data landed after this month's statement was computed."})
    for n in g.get("notes") or []:
        rows.append({"months": ", ".join(n.get("months") or []), "note": n.get("note")})
    return {"name": name, "columns": [{"header": "Month(s)", "key": "months"}, {"header": "Note", "key": "note"}],
            "rows": rows}
