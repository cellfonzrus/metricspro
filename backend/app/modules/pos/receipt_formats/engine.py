"""Geometry engine — reconstruct a receipt's TABLE and header from word coordinates.

The parsers do not hardcode column x-positions. They declare the COLUMN LABELS a format uses (e.g.
"Product SKU", "Tracking #", "Your Total"); the engine finds the header row that carries those labels
in the actual PDF, reads each column's x-span FROM that row, and buckets every item word into the
column whose x-span contains it. So the same engine reconstructs any columnar receipt — a new format
just declares its labels. Words come from pdfplumber (see receipt_pdf.py); everything here is PURE so
the harness can feed synthetic word lists.

A "word" is a dict: {"text": str, "x0": float, "x1": float, "top": float, "bottom": float}. `top` is
made globally increasing across pages by the caller (page index * PAGE_STRIDE + top) so a table that
continues onto page 2 stays in order.
"""
from __future__ import annotations

import re
from collections import defaultdict

from . import base


def group_rows(words, ytol: float = 3.0) -> list[dict]:
    """Cluster words into rows by `top` (± ytol), each row's words sorted left→right."""
    buckets: dict[int, list] = defaultdict(list)
    for w in words or []:
        buckets[round(float(w["top"]) / ytol)].append(w)
    rows = []
    for k in sorted(buckets):
        ws = sorted(buckets[k], key=lambda w: w["x0"])
        rows.append({"top": min(w["top"] for w in ws), "words": ws})
    return rows


def row_text(row) -> str:
    return " ".join(w["text"] for w in row["words"])


def find_row(rows, *needles, start: int = 0):
    """Index of the first row (≥ start) whose text contains ALL needles (case-insensitive)."""
    for i in range(start, len(rows)):
        t = row_text(rows[i]).lower()
        if all(n.lower() in t for n in needles):
            return i
    return -1


def value_right_of(row, *label_tokens) -> str:
    """The text to the RIGHT of the last label token on a row — i.e. a 'Label: value' value."""
    words = row["words"]
    last = -1
    toks = [t.lower().rstrip(":") for t in label_tokens]
    for i, w in enumerate(words):
        if w["text"].lower().rstrip(":") in toks:
            last = i
    if last < 0:
        return ""
    return " ".join(w["text"] for w in words[last + 1:]).strip()


def column_bounds(header_row, col_specs) -> list[dict] | None:
    """From a detected header row, resolve each declared column's x-span. `col_specs` is the format's
    ordered columns: [{"key","label","kind","hdr":[header word tokens]}]. Each column spans from its
    own header's LEFT edge to the NEXT header's left edge — left edges, NOT centre midpoints, because a
    long product name legitimately runs right past its header's centre and must stay in its own column
    (it stops only where the next column's header begins). Returns None if the header labels aren't all
    present in order."""
    words = header_row["words"]
    edges = []
    used = 0
    for spec in col_specs:
        toks = [t.lower() for t in spec["hdr"]]
        pos = _match_run(words, toks, used)
        if pos is None:
            return None
        i0, i1 = pos
        used = i1
        edges.append((spec, words[i0]["x0"]))
    cols = []
    for idx, (spec, x0) in enumerate(edges):
        lo = -1e9 if idx == 0 else edges[idx][1]
        hi = 1e9 if idx == len(edges) - 1 else edges[idx + 1][1]
        cols.append({"key": spec["key"], "label": spec["label"], "kind": spec["kind"],
                     "align": "right" if spec["kind"] in (base.KIND_MONEY, base.KIND_TOTAL, base.KIND_QTY) else "left",
                     "_lo": lo, "_hi": hi})
    return cols


def _phrase_in(text: str, phrase: str) -> bool:
    """True if `phrase` appears in `text` with word boundaries at both ends (so 'total' does NOT match
    inside 'subtotal', and an item description that merely contains a totals keyword doesn't stop the
    table). Internal whitespace in the phrase matches any run of whitespace."""
    pat = r"(?<!\w)" + r"\s+".join(re.escape(p) for p in phrase.split()) + r"(?!\w)"
    return re.search(pat, text, re.I) is not None


def _match_run(words, toks, start):
    """Find i0,i1 so words[i0:i1] text (lowercased) equals the token sequence `toks`."""
    n = len(toks)
    for i in range(start, len(words) - n + 1):
        if all(words[i + j]["text"].lower().rstrip(":#") == toks[j].rstrip(":#") for j in range(n)):
            return i, i + n
    return None


def bucket_columns(row, cols) -> dict:
    """Assign each word in a row to the column whose x-span contains its centre; join per column."""
    out: dict[str, list] = {c["key"]: [] for c in cols}
    for w in row["words"]:
        cx = (w["x0"] + w["x1"]) / 2
        for c in cols:
            if c["_lo"] <= cx < c["_hi"]:
                out[c["key"]].append(w["text"])
                break
    return {k: " ".join(v).strip() for k, v in out.items()}


def looks_like_item(cells, cols) -> bool:
    """An item row must carry a money value in a money column and something in the code/desc column."""
    money_keys = [c["key"] for c in cols if c["kind"] in (base.KIND_MONEY, base.KIND_TOTAL)]
    has_money = any(base.money(cells.get(k)) is not None for k in money_keys)
    lead = [c["key"] for c in cols if c["kind"] in (base.KIND_CODE, base.KIND_DESC)]
    has_lead = any(str(cells.get(k) or "").strip() for k in lead)
    return has_money and has_lead


def extract_table(rows, col_specs, stop_labels) -> tuple[list[dict], list[dict], int]:
    """Find the header row for `col_specs`, then bucket every following item row until a `stop_labels`
    row (totals/footer). Returns (columns, items, stop_row_index). Columns keep no private x keys."""
    # locate the header row: the first row that yields valid column bounds
    hdr_i, cols = -1, None
    for i, r in enumerate(rows):
        c = column_bounds(r, col_specs)
        if c:
            hdr_i, cols = i, c
            break
    if cols is None:
        return [], [], len(rows)
    items = []
    stop = len(rows)
    for j in range(hdr_i + 1, len(rows)):
        t = row_text(rows[j])
        if any(_phrase_in(t, lbl) for lbl in stop_labels):
            stop = j
            break
        cells = bucket_columns(rows[j], cols)
        if looks_like_item(cells, cols):
            items.append(base.item(cells, cols))
    public_cols = [{"key": c["key"], "label": c["label"], "kind": c["kind"], "align": c["align"]} for c in cols]
    return public_cols, items, stop


def extract_totals(rows, total_specs, start: int = 0, end: int | None = None) -> list[dict]:
    """Read the known totals/tax lines. `total_specs`: [{"key","label","match":[tokens]}]. For each,
    find its row in [start,end) and take the LAST money on that row. Order follows total_specs, and a
    spec with no matching row is skipped (a format lists every total it *might* show)."""
    end = len(rows) if end is None else end
    out = []
    for spec in total_specs:
        for i in range(start, end):
            t = row_text(rows[i])
            if all(_phrase_in(t, m) for m in spec["match"]):
                monies = base.find_money(row_text(rows[i]))
                amt = base.money(monies[-1]) if monies else None
                out.append({"key": spec["key"], "label": spec["label"], "amount": amt,
                            "editable": bool(spec.get("editable"))})
                break
    return out


def collect_block(rows, anchor_tokens, *, max_lines: int = 6, stop_tokens=()) -> list[str]:
    """The text lines of a block that starts at the row containing `anchor_tokens` (exclusive) and runs
    until a blank line, a `stop_tokens` row, or max_lines. Used for Bill To / Ship To / store address."""
    i = find_row(rows, *anchor_tokens)
    if i < 0:
        return []
    out = []
    for j in range(i + 1, min(i + 1 + max_lines, len(rows))):
        t = row_text(rows[j]).strip()
        if not t:
            break
        if stop_tokens and any(s.lower() in t.lower() for s in stop_tokens):
            break
        out.append(t)
    return out


def block_lines(rows, anchor_tokens, *, x_lo=-1e9, x_hi=1e9, max_lines=6, stop_tokens=()) -> list[str]:
    """Lines of a block starting AFTER the row containing `anchor_tokens`, keeping only the words whose
    x-centre falls in [x_lo, x_hi) — so a Bill-To block on the left is captured without pulling in the
    Ship-To block that shares the same rows on the right. Stops at a blank line, a stop_tokens row, or
    max_lines."""
    i = find_row(rows, *anchor_tokens)
    if i < 0:
        return []
    out = []
    for j in range(i + 1, min(i + 1 + max_lines, len(rows))):
        picks = [w["text"] for w in rows[j]["words"] if x_lo <= (w["x0"] + w["x1"]) / 2 < x_hi]
        t = " ".join(picks).strip()
        if not t:
            break
        if stop_tokens and any(s.lower() in t.lower() for s in stop_tokens):
            break
        out.append(t)
    return out


def left_lines(rows, *, x_hi, top_lo, top_hi) -> list[str]:
    """Left-column lines (word centre < x_hi) for rows whose top is in (top_lo, top_hi). Used for the
    store address block that sits top-left while header fields sit top-right on the same rows."""
    out = []
    for r in rows:
        if top_lo < r["top"] < top_hi:
            picks = [w["text"] for w in r["words"] if (w["x0"] + w["x1"]) / 2 < x_hi]
            t = " ".join(picks).strip()
            if t:
                out.append(t)
    return out


def footer_from(rows, start_label) -> str | None:
    """Everything from the row containing `start_label` to the end — the legal/return-policy text,
    captured verbatim so the reprint is faithful (never hardcoded)."""
    i = find_row(rows, start_label)
    if i < 0:
        return None
    lines = [row_text(rows[k]).strip() for k in range(i, len(rows))]
    lines = [ln for ln in lines if ln and not re.match(r"^Page \d+ of \d+", ln)]
    return "\n".join(lines) or None
