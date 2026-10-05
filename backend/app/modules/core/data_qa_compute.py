"""THE ONE HOME for the arithmetic the data assistant is allowed to do on rows a report returned —
group, rank, pivot, compare, and describe a chart.

WHY IT IS A SEPARATE, PURE MODULE. The owner asked the assistant to "perform calculations, create a
pivot table or create graphs". A language model doing that arithmetic in prose is a model that can
be wrong about a sum and sound exactly as confident. So the model never adds anything up here: it
chooses a grouping and a measure, and THIS code does the arithmetic, deterministically, over rows
that came from the report itself. Every figure the user reads was computed by a function with a
DB-free proof harness (`backend/harness_data_qa_compute.py`), not by a model.

It is also why this is not "nl2sql". A pivot here cannot invent a measure: it can only sum, count or
average a column the report already returned.

PURITY: stdlib only. No database, no network, no `app.` imports.
"""

_AGGS = ("sum", "count", "avg", "min", "max")

# Money-ish strings arrive from reports as '$1,234.56', '(12.00)' (negative), '1 234,56' never.
_STRIP = str.maketrans({",": "", "$": "", "%": "", " ": ""})


def to_number(v):
    """A report cell as a float, or None when it is not a number. One home for the coercion, so a
    sum and a rank can never disagree about whether '(12.00)' is -12."""
    if v is None or isinstance(v, bool):
        return None
    if isinstance(v, (int, float)):
        return float(v)
    s = str(v).strip()
    if not s:
        return None
    neg = s.startswith("(") and s.endswith(")")
    if neg:
        s = s[1:-1]
    s = s.translate(_STRIP)
    if not s:
        return None
    try:
        n = float(s)
    except ValueError:
        return None
    return -n if neg else n


def numeric_columns(rows, columns=None):
    """The columns that hold numbers in at least one row — what may legitimately be a MEASURE. The
    model is shown this list so it cannot ask to sum a store name."""
    cols = list(columns or ())
    if not cols:
        seen = []
        for r in rows[:200]:
            if isinstance(r, dict):
                for c in r:
                    if c not in seen:
                        seen.append(c)
        cols = seen
    return tuple(c for c in cols
                 if any(to_number(r.get(c)) is not None for r in rows[:200] if isinstance(r, dict)))


def _key(row, by):
    return tuple("" if row.get(b) is None else str(row.get(b)) for b in by)


def group(rows, by, measures, agg="sum"):
    """Group `rows` by the columns `by` and aggregate each of `measures`.

    Returns a list of dicts: the grouping columns, then `<measure>` per measure, then `_rows` (how
    many rows fell in the group). `avg` ignores non-numeric cells rather than counting them as zero,
    because a blank gross-profit cell is "not reported", not "zero profit" — and averaging a blank
    as 0 is how a report starts lying quietly. Rows missing every measure still count in `_rows`."""
    if agg not in _AGGS:
        raise ValueError(f"agg must be one of {_AGGS}")
    by = [str(b) for b in (by or [])]
    measures = [str(m) for m in (measures or [])]
    order, buckets = [], {}
    for r in rows:
        if not isinstance(r, dict):
            continue
        k = _key(r, by)
        if k not in buckets:
            order.append(k)
            buckets[k] = {"_rows": 0, "_vals": {m: [] for m in measures}}
        b = buckets[k]
        b["_rows"] += 1
        for m in measures:
            n = to_number(r.get(m))
            if n is not None:
                b["_vals"][m].append(n)
    out = []
    for k in order:
        b = buckets[k]
        row = {col: k[i] for i, col in enumerate(by)}
        for m in measures:
            vals = b["_vals"][m]
            if agg == "count":
                row[m] = len(vals)
            elif not vals:
                row[m] = None
            elif agg == "sum":
                row[m] = round(sum(vals), 4)
            elif agg == "avg":
                row[m] = round(sum(vals) / len(vals), 4)
            elif agg == "min":
                row[m] = min(vals)
            else:
                row[m] = max(vals)
        row["_rows"] = b["_rows"]
        out.append(row)
    return out


def rank(rows, measure, *, descending=True, limit=None):
    """Sort `rows` by `measure`. Rows whose measure is not a number go LAST in both directions —
    "no figure reported" is never the best store and never the worst one, which is the whole of
    "who is pulling me down" being answered honestly."""
    measure = str(measure)
    have = [r for r in rows if isinstance(r, dict) and to_number(r.get(measure)) is not None]
    lack = [r for r in rows if isinstance(r, dict) and to_number(r.get(measure)) is None]
    have.sort(key=lambda r: to_number(r.get(measure)), reverse=bool(descending))
    out = have + lack
    return out[:int(limit)] if limit else out


def pivot(rows, row_by, col_by, measure, agg="sum"):
    """A pivot table: `row_by` down the side, the distinct values of `col_by` across the top, each
    cell `agg` of `measure`. Returns `{"columns", "rows", "totals"}` where every row carries a
    `_total` and `totals` is the column footer — so the table the user sees adds up, rather than the
    model being trusted to add a row of numbers in prose.

    Column order is the order the values were first SEEN in the rows, not alphabetical: a report
    that returns months in order pivots into months in order."""
    row_by = [str(r) for r in (row_by or [])]
    col_by = str(col_by)
    measure = str(measure)
    cols, cell, rorder, rkeys = [], {}, [], {}
    for r in rows:
        if not isinstance(r, dict):
            continue
        c = "" if r.get(col_by) is None else str(r.get(col_by))
        if c not in cols:
            cols.append(c)
        rk = _key(r, row_by)
        if rk not in rkeys:
            rorder.append(rk)
            rkeys[rk] = True
        n = to_number(r.get(measure))
        if n is not None:
            cell.setdefault((rk, c), []).append(n)

    def _agg(vals):
        if agg == "count":
            return len(vals)
        if not vals:
            return None
        if agg == "sum":
            return round(sum(vals), 4)
        if agg == "avg":
            return round(sum(vals) / len(vals), 4)
        return min(vals) if agg == "min" else max(vals)

    if agg not in _AGGS:
        raise ValueError(f"agg must be one of {_AGGS}")
    body, footer = [], {c: [] for c in cols}
    for rk in rorder:
        row = {col: rk[i] for i, col in enumerate(row_by)}
        tot = []
        for c in cols:
            vals = cell.get((rk, c), [])
            row[c] = _agg(vals)
            tot += vals
            footer[c] += vals
        row["_total"] = _agg(tot)
        body.append(row)
    totals = {c: _agg(footer[c]) for c in cols}
    totals["_total"] = _agg([v for c in cols for v in footer[c]])
    return {"columns": list(cols), "rows": body, "totals": totals,
            "measure": measure, "agg": agg, "row_by": row_by, "col_by": col_by}


def compare(rows, label_col, measure, *, baseline_col=None):
    """Each row's `measure` against either a second column on the same row (`baseline_col`) or the
    group's own mean. Returns label, value, baseline, `delta`, `pct` (None when the baseline is zero
    — a share of nothing is not 0% and not infinity, it is unanswerable) and `share` of the total.

    This is what "who is pulling me down" actually asks: not the smallest number, but the one
    furthest below what the rest of the estate is doing."""
    label_col, measure = str(label_col), str(measure)
    vals = [(str(r.get(label_col) or ""), to_number(r.get(measure)),
             to_number(r.get(baseline_col)) if baseline_col else None)
            for r in rows if isinstance(r, dict)]
    present = [v for _l, v, _b in vals if v is not None]
    total = round(sum(present), 4) if present else 0.0
    mean = (sum(present) / len(present)) if present else None
    out = []
    for label, v, b in vals:
        base = b if baseline_col else mean
        delta = round(v - base, 4) if (v is not None and base is not None) else None
        pct = round((v - base) / base * 100, 2) if (delta is not None and base) else None
        share = round(v / total * 100, 2) if (v is not None and total) else None
        out.append({"label": label, "value": v, "baseline": base,
                    "delta": delta, "pct": pct, "share": share})
    return {"rows": out, "total": total, "mean": round(mean, 4) if mean is not None else None,
            "measure": measure, "baseline": baseline_col or "mean of the rows compared"}


_CHARTS = ("bar", "line", "pie", "horizontal_bar")


def chart_spec(rows, kind, label_col, measures, *, title="", limit=40):
    """A chart as DATA, never as a picture: `{kind, title, labels, series:[{name, values}]}` which
    the frontend renders. The backend draws nothing and the model draws nothing — so a chart cannot
    show a number the table does not, and a blank cell stays blank instead of becoming a zero-height
    bar that reads as "we sold nothing here"."""
    if kind not in _CHARTS:
        raise ValueError(f"kind must be one of {_CHARTS}")
    label_col = str(label_col)
    measures = [str(m) for m in (measures or [])]
    if kind == "pie" and len(measures) > 1:
        measures = measures[:1]            # a pie has one measure by construction
    use = [r for r in rows if isinstance(r, dict)][:int(limit)]
    labels = [str(r.get(label_col) or "") for r in use]
    series = [{"name": m, "values": [to_number(r.get(m)) for r in use]} for m in measures]
    return {"kind": kind, "title": title or (", ".join(measures) + f" by {label_col}"),
            "label_col": label_col, "labels": labels, "series": series,
            "truncated": len([r for r in rows if isinstance(r, dict)]) > len(use)}


def describe(rows, columns=None, *, sample=3):
    """What the model is told about a result it just fetched: how many rows, which columns, which of
    those hold numbers, and a few real rows. Bounded on purpose — the model reasons about the SHAPE
    and asks for the arithmetic it wants; it never has to hold a whole report in its head, which is
    where a model starts approximating a total."""
    rows = [r for r in rows if isinstance(r, dict)]
    cols = tuple(columns or ())
    if not cols:
        seen = []
        for r in rows[:200]:
            for c in r:
                if c not in seen:
                    seen.append(c)
        cols = tuple(seen)
    return {"row_count": len(rows), "columns": list(cols),
            "numeric_columns": list(numeric_columns(rows, cols)),
            "sample_rows": rows[:max(0, int(sample))]}
