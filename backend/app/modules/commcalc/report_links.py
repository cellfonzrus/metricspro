"""REPORT LINKS — every report a tenant loaded, linked to every other by the columns they share, with
the match counts shown each way (Stage D of the onboarding intake; index §30.11).

Owner, verbatim (2026-09-20): *"we need to link the different reports automatically with each other
with common columns."*

DUPLICATE CHECK (build gate, CLAUDE.md). Searched the index §11a (`inventory_sold_recon` — the
inventory ↔ sales ↔ activation pairing built in #254), §11 (`device_cost_recon.device_key` /
`norm_order` — the cross-source device and order keys), §13a (the canonical store resolution),
§30.6–30.8 (the intake's re-reads per kind, `KIND_FIELDS`, the identity decisions on
`verified_numbers`), §30.9 (the report-kind registry's `signature_fields`), §2 (`column_mapping.
TARGET_FIELDS` — the ONE vocabulary of canonical field names). NOT rebuilt here:

  · the DEVICE pairing — `inventory_sold_recon.line_pairings` (the rule `activation_index` folds: a
    line's own serial / IMEI, else THROUGH a sale line carrying its mobile number, else unpairable
    with the reason) is CALLED for the device field; `sales_mobile_index` is its bridge. A second
    copy would pair what that one refuses. Locked by `harness_report_links_lock.py`.
  · the KEY NORMALISERS — `device_cost_recon.device_key` (device), `inventory_sold_recon.mobile_key`
    (phone number), `device_cost_recon.norm_order` (invoice / order / transaction id) are INJECTED
    (`normalisers`), so this module stays pure and the harness proves the real pairing. This file
    defines NO normaliser of its own (the lock scans for one).
  · the COLUMN VOCABULARY — a link field names the `column_mapping.TARGET_FIELDS` spellings that
    carry it (`LINK_FIELDS`); `columns_for` reads a source's columns off the intake's own
    `kind_fields` (store / date / txn / rep per kind) plus the layout's TARGET_FIELDS names. Nothing
    is invented: a spelling TARGET_FIELDS does not know is dropped and reported (`link_vocabulary`).
  · the STORE and REP resolution — the identity decisions confirmed at 2.4 / 3.7 (`lands_as`,
    `resolved_name` on `verified_numbers.identity`) and, behind them, the §13a resolver — passed in
    per source as `store_key` / `rep_key` callables built by the router.
  · the ROWS — read through the intake's EXISTING re-read helpers per kind (the router), never a
    new query path.

WHAT THIS IS. For every PAIR of loaded reports in the run: the link fields both carry; for each, the
match counts EACH WAY ("412 of 521 commission lines match a sales line by phone number; 109
unmatched"), the pairing basis (direct / via a third report), the lines that carry no usable key,
the unmatched keys (capped, with counts) and the AMBIGUOUS keys (a key on several lines of the
other side — reported, not resolved). A pair with no shared field says so. A run with one report has
no links, and says so. Nothing is guessed: an unmatched line is unmatched.

THIS BOOKS NOTHING and stores nothing but an optional cache of its own result on the intake's own
state payload. PURE / stdlib only — proof `backend/harness_report_links.py`.
"""

# ── THE LINK VOCABULARY — target-field spellings, from column_mapping.TARGET_FIELDS ──────────────
# `field`: the link field id; `label`: the layman word the page shows; `target_fields`: the canonical
# TARGET_FIELDS names that carry it (an entry may be restricted to the layouts whose registry copy
# says so — the residual statement's line identity is "Account / mobile number", §30.10);
# `normaliser`: which injected key function compares it; `strength`: the order a cell's "strongest
# shared field" is picked in (a device key beats a phone number beats an invoice number beats a
# store beats a rep beats a date).
LINK_FIELDS = [
    {"field": "device", "label": "IMEI / serial", "normaliser": "device_key", "strength": 6,
     "target_fields": ("serial_1", "imei", "serial")},
    {"field": "mobile", "label": "phone number", "normaliser": "mobile_key", "strength": 5,
     "target_fields": ("mdn", ("account_id", ("commission_ledger__residual",)))},
    {"field": "order", "label": "invoice / order number", "normaliser": "norm_order", "strength": 4,
     "target_fields": ("trans_id", "order_number", "order_id", "transaction_id", "invoice_id")},
    {"field": "store", "label": "store", "normaliser": "store_key", "strength": 3, "target_fields": ("store",)},
    {"field": "rep", "label": "rep", "normaliser": "rep_key", "strength": 2,
     "target_fields": ("salesperson", "rep_user", "user_login", "rep_name", "user_name")},
    {"field": "date", "label": "date", "normaliser": "date_key", "strength": 1,
     "target_fields": ("trans_date", "settlement_date")},
]
FIELD_ORDER = [f["field"] for f in LINK_FIELDS]
FIELD_LABELS = {f["field"]: f["label"] for f in LINK_FIELDS}
FIELD_STRENGTH = {f["field"]: f["strength"] for f in LINK_FIELDS}
NORMALISER_NAMES = ("device_key", "mobile_key", "norm_order")     # the three injected homes

# Kinds whose landed rows are NOT a TARGET_FIELDS layout: the destination's own column names, as the
# intake's re-read returns them (pos_tender_summary mig 062; merchant_settlement_day mig 955; the
# activation feed as `_cr_resolve_activation_details` models it). The only place they are spelled.
DESTINATION_COLUMNS = {
    "x_report": {"store": "store", "date": "close_date"},
    "merchant_payments": {"store": "store_code", "date": "business_date"},
    "activations": {"device": "serial", "mobile": "mdn", "order": "trans_id", "date": "trans_date",
                    "store": "store", "rep": "salesperson"},
}
ACTIVATIONS_KIND = "activations"
ACTIVATIONS_INSTANCE_KEY = "activations:custom_import:activation_details"
BRIDGE_KINDS = ("sales", "pos")            # a report that carries BOTH a device key and a phone number per line

BASIS_DIRECT = "direct"                    # both reports carry the column
BASIS_VIA = "via_third_report"             # paired THROUGH a report that carries both keys (device via phone number)
NO_KEY = "no usable key"
SAMPLE_CAP = 25
NOTE_ONE_REPORT = "only one report is loaded — there is nothing to link it to yet"
NOTE_NO_REPORTS = "no report is loaded yet"
NOTE_NO_SHARED = "no common column"


def _s(v):
    return "" if v is None else str(v).strip()


def date_key(v):
    """The day of a date-ish value (ISO first 10 characters), or None. A date is the WEAKEST link —
    two reports sharing a day is a fact, not a line match — and the page says so."""
    s = _s(v)
    if len(s) < 10 or s[4] != "-" or s[7] != "-":
        return None
    return s[:10]


def link_vocabulary(target_fields):
    """The link vocabulary CHECKED against TARGET_FIELDS: every spelling a link field names must be a
    field of at least one layout, else it is dropped and listed under `unknown` (the lock asserts the
    list is empty). Returns {"fields": [...LINK_FIELDS with only known spellings...], "unknown": [...],
    "carried_by": {field: [layouts]}} — which layouts carry each field."""
    known = {}
    for layout, fields in (target_fields or {}).items():
        for f in fields or []:
            known.setdefault(f[0], set()).add(layout)
    out, unknown, carried = [], [], {}
    for lf in LINK_FIELDS:
        kept = []
        for tf in lf["target_fields"]:
            name, layouts = (tf, None) if isinstance(tf, str) else (tf[0], tuple(tf[1]))
            if name not in known:
                unknown.append({"field": lf["field"], "target_field": name})
                continue
            kept.append(tf)
            lays = known[name] if layouts is None else (known[name] & set(layouts))
            carried.setdefault(lf["field"], set()).update(lays)
        out.append({**lf, "target_fields": tuple(kept)})
    return {"fields": out, "unknown": unknown, "carried_by": {k: sorted(v) for k, v in carried.items()}}


def columns_for(kind, layout, kind_fields, target_fields):
    """{link_field: column | (columns...)} — the columns of a source's re-read rows that carry each
    link field: the intake's own `kind_fields` entry for the kind (store / date / txn / rep / key /
    key2), plus the layout's TARGET_FIELDS spellings for the rest; a matrix kind or the activation
    feed uses its destination's columns. `device` may name several columns (imei, serial): the first
    that yields a key is the line's key."""
    kind = _s(kind).lower()
    if kind in DESTINATION_COLUMNS:
        return dict(DESTINATION_COLUMNS[kind])
    out = {}
    kf = kind_fields or {}
    if kf.get("store"):
        out["store"] = kf["store"]
    if kf.get("date"):
        out["date"] = kf["date"]
    if kf.get("txn"):
        out["order"] = kf["txn"]
    if kf.get("rep"):
        out["rep"] = kf["rep"]
    dev = tuple(kf[k] for k in ("key", "key2") if kf.get(k))
    if dev:
        out["device"] = dev
    names = {f[0] for f in (target_fields or {}).get(layout) or []}
    for lf in LINK_FIELDS:
        cols = []
        for tf in lf["target_fields"]:
            name, layouts = (tf, None) if isinstance(tf, str) else (tf[0], tuple(tf[1]))
            if name in names and (layouts is None or layout in layouts):
                cols.append(name)
        if not cols or lf["field"] in out:
            continue
        out[lf["field"]] = tuple(cols) if lf["field"] == "device" and len(cols) > 1 else cols[0]
    return out


def _cols(spec):
    return tuple(spec) if isinstance(spec, (tuple, list)) else (spec,)


def _first_key(row, spec, fn):
    for c in _cols(spec):
        k = fn(row.get(c))
        if k:
            return k
    return None


def line_keys(source, normalisers):
    """Per LINE of a source: {"line": id, "keys": {field: key | None}} — each link field's key through
    its ONE normaliser (device_key / mobile_key / norm_order injected; store_key / rep_key per source
    from the confirmed identity decisions; date_key here). A line whose value yields no key carries
    None for that field: it is counted as "no usable key", never matched."""
    cols = source.get("columns") or {}
    fns = {
        "device": normalisers["device_key"], "mobile": normalisers["mobile_key"], "order": normalisers["norm_order"],
        "store": source.get("store_key") or (lambda v: None), "rep": source.get("rep_key") or (lambda v: None),
        "date": date_key,
    }
    id_col = cols.get("order")
    out = []
    for i, r in enumerate(source.get("rows") or []):
        r = r or {}
        line = (_s(r.get(_cols(id_col)[0])) if id_col else "") or f"row {i + 1}"
        keys = {}
        for field, spec in cols.items():
            if field not in fns:
                continue
            keys[field] = _first_key(r, spec, fns[field])
        out.append({"line": line, "keys": keys})
    return out


def _via_device_keys(source, bridge_sources, normalisers, line_pairings, mobile_index):
    """The device key per line of `source` THROUGH the bridge reports (a sale line carrying the same
    phone number) — `inventory_sold_recon.line_pairings`, the ONE pairing rule, called as-is. Returns
    [(key | None, pairing | None, reason | None, candidates)] aligned with the source's rows."""
    cols = source.get("columns") or {}
    dev = _cols(cols.get("device")) if cols.get("device") else ()
    rows = []
    for r in source.get("rows") or []:
        r = r or {}
        serial = next((r.get(c) for c in dev if _s(r.get(c))), None)
        rows.append({"serial": serial, "mdn": r.get(cols.get("mobile")) if cols.get("mobile") else None,
                     "trans_id": r.get(_cols(cols.get("order"))[0]) if cols.get("order") else None})
    pairs = line_pairings(rows, normalisers["device_key"], None, "serial", "mdn", "trans_id", mobile_index=mobile_index)
    return [(p["key"], p["pairing"], p["reason"], p.get("candidates")) for p in pairs]


def _direction(a_lines, a_keys, b_index, field, via_info=None):
    """Counts for `a` → `b` on one field. `a_keys` = the key per a-line (None = no usable key);
    `b_index` = {key: number of b-lines carrying it}. A key on >1 b-line is AMBIGUOUS: counted as
    matched AND flagged with the count, never resolved to one line."""
    n = len(a_keys)
    with_key = sum(1 for k in a_keys if k)
    matched = unmatched = ambiguous = 0
    unmatched_keys, ambiguous_keys = {}, {}
    for line, k in zip(a_lines, a_keys):
        if not k:
            continue
        c = b_index.get(k, 0)
        if c:
            matched += 1
            if c > 1:
                ambiguous += 1
                ambiguous_keys.setdefault(k, {"key": k, "lines": 0, "other_side_lines": c})["lines"] += 1
        else:
            unmatched += 1
            u = unmatched_keys.setdefault(k, {"key": k, "lines": 0, "sample_line": line})
            u["lines"] += 1
    out = {"lines": n, "with_key": with_key, "no_key": n - with_key, "matched": matched, "unmatched": unmatched,
           "ambiguous": ambiguous, "distinct_keys": len({k for k in a_keys if k}),
           "unmatched_distinct": len(unmatched_keys), "ambiguous_distinct": len(ambiguous_keys),
           "unmatched_sample": sorted(unmatched_keys.values(), key=lambda u: (-u["lines"], u["key"]))[:SAMPLE_CAP],
           "ambiguous_sample": sorted(ambiguous_keys.values(), key=lambda u: (-u["other_side_lines"], u["key"]))[:SAMPLE_CAP]}
    if via_info is not None:
        out.update(via_info)
    return out


def sentence(direction, a_label, b_label, field):
    """The layman sentence for one direction: "412 of 521 commission lines match a sales line by
    phone number; 109 unmatched; 0 carry no phone number"."""
    d = direction
    lab = FIELD_LABELS.get(field, field)
    parts = [f"{d['matched']:,} of {d['lines']:,} {a_label} lines match a {b_label} line by {lab}"]
    if d["unmatched"]:
        parts.append(f"{d['unmatched']:,} unmatched")
    if d["no_key"]:
        parts.append(f"{d['no_key']:,} carry no {lab}")
    if d.get("via_lines"):
        parts.append(f"{d['via_lines']:,} paired through {d.get('via_label')}")
    if d["ambiguous"]:
        parts.append(f"{d['ambiguous']:,} match several {b_label} lines (ambiguous — listed, not resolved)")
    return "; ".join(parts)


def _index(keys):
    idx = {}
    for k in keys:
        if k:
            idx[k] = idx.get(k, 0) + 1
    return idx


def pair(a, b, a_lines, b_lines, normalisers, line_pairings=None, bridges=(), mobile_index=None):
    """The link between two sources: per shared field, the counts each way, the basis and the
    samples. `a_lines` / `b_lines` = `line_keys(...)`. `bridges` = the sources (other than a and b)
    that carry both a device key and a phone number; `mobile_index` = their merged
    `sales_mobile_index`, so a line WITHOUT a device key but WITH a phone number is paired through
    them by `inventory_sold_recon.line_pairings` — stated per direction, never guessed."""
    ca, cb = a.get("columns") or {}, b.get("columns") or {}
    shared = [f for f in FIELD_ORDER if f in ca and f in cb]
    can_via = bool(bridges) and line_pairings is not None and mobile_index is not None
    # the device field is ALSO reachable through the bridges when one side has device keys and the
    # other carries (at least) a phone number — the activation ↔ inventory case of §11a
    if can_via and "device" not in shared and ("device" in ca or "device" in cb) \
            and ("device" in ca or "mobile" in ca) and ("device" in cb or "mobile" in cb):
        shared.insert(0, "device")
    fields = []
    for f in shared:
        basis, via = BASIS_DIRECT, None
        a_keys = [l["keys"].get(f) for l in a_lines]
        b_keys = [l["keys"].get(f) for l in b_lines]
        via_info = {"a": None, "b": None}
        if f == "device" and can_via:
            bridge_label = " / ".join(s["label"] for s in bridges)
            for side, src, keys in (("a", a, a_keys), ("b", b, b_keys)):
                if not (src.get("columns") or {}).get("mobile"):
                    continue
                res = _via_device_keys(src, bridges, normalisers, line_pairings, mobile_index)
                n_via, reasons = 0, {}
                for i, (k, pairing, reason, _cands) in enumerate(res):
                    if keys[i]:
                        continue                     # its own key wins; the bridge serves the keyless line
                    if k and pairing == "mobile_number_via_sales":
                        keys[i] = k
                        n_via += 1
                    elif reason:
                        reasons[reason] = reasons.get(reason, 0) + 1
                via_info[side] = {"via_lines": n_via, "via_label": bridge_label, "via_reasons": reasons}
            if any(v and v["via_lines"] for v in via_info.values()):
                basis = BASIS_VIA
                via = {"through": [s["instance_key"] for s in bridges], "through_label": bridge_label,
                       "how": "the phone number on the sale line names the unit (same line or same transaction)"}
        a_to_b = _direction([l["line"] for l in a_lines], a_keys, _index(b_keys), f, via_info["a"])
        b_to_a = _direction([l["line"] for l in b_lines], b_keys, _index(a_keys), f, via_info["b"])
        fields.append({
            "field": f, "label": FIELD_LABELS[f], "basis": basis, "via": via,
            "a_column": ca.get(f), "b_column": cb.get(f),
            "a_to_b": a_to_b, "b_to_a": b_to_a,
            "sentence_a_to_b": sentence(a_to_b, a["label"], b["label"], f),
            "sentence_b_to_a": sentence(b_to_a, b["label"], a["label"], f),
            "any_match": bool(a_to_b["matched"] or b_to_a["matched"]),
        })
    # the strongest field: among the fields with a match either way, a DIRECT pairing before one made
    # through a third report, then by field strength; None when nothing matched
    strongest = next((x["field"] for x in sorted(fields, key=lambda x: (x["basis"] != BASIS_DIRECT, -FIELD_STRENGTH[x["field"]]))
                      if x["any_match"]), None)
    return {"a": a["instance_key"], "b": b["instance_key"], "a_label": a["label"], "b_label": b["label"],
            "shared": [x["field"] for x in fields], "strongest": strongest,
            "linked": any(x["any_match"] for x in fields),
            "note": None if fields else NOTE_NO_SHARED, "fields": fields}


def bridges_for(sources):
    """The reports that can carry a keyless line to a device: a sales-kind source with BOTH a device
    column and a phone-number column."""
    return [s for s in sources if _s(s.get("kind")).lower() in BRIDGE_KINDS
            and (s.get("columns") or {}).get("device") and (s.get("columns") or {}).get("mobile")]


def _bridge_index(bridge, normalisers, sales_mobile_index):
    cols = bridge["columns"]
    dev = _cols(cols["device"])
    rows = [{"serial_1": next((r.get(c) for c in dev if _s(r.get(c))), None), "mdn": r.get(cols["mobile"]),
             "trans_id": r.get(_cols(cols.get("order"))[0]) if cols.get("order") else None}
            for r in (bridge.get("rows") or [])]
    return sales_mobile_index(rows, normalisers["device_key"], "serial_1", "mdn", "trans_id")


def _merge_indexes(indexes):
    out = {}
    for idx in indexes:
        for m, keys in idx.items():
            for k, via in keys.items():
                out.setdefault(m, {}).setdefault(k, via)
    return out


def report(sources, normalisers, line_pairings=None, sales_mobile_index=None):
    """THE REPORT over the run's loaded sources. `sources` = [{instance_key, kind, label, layout,
    rows, columns, store_key, rep_key, read_ok, note}] as the router prepares them (rows through the
    intake's own re-reads); `normalisers` = {device_key, mobile_key, norm_order} from their homes;
    `line_pairings` / `sales_mobile_index` = inventory_sold_recon's (the pairing core). Returns
    {"sources": [...per source: lines, columns carried, linked_to, shared_with, read_ok, note],
     "pairs": [...pair(...)...], "fields": the vocabulary labels, "note": ...}."""
    srcs = [s for s in sources or [] if s.get("read_ok", True)]
    skipped = [{"instance_key": s.get("instance_key"), "label": s.get("label"), "note": s.get("note")}
               for s in sources or [] if not s.get("read_ok", True)]
    lines = {s["instance_key"]: line_keys(s, normalisers) for s in srcs}
    bridges = bridges_for(srcs) if (line_pairings is not None and sales_mobile_index is not None) else []
    bridge_idx = {br["instance_key"]: _bridge_index(br, normalisers, sales_mobile_index) for br in bridges}
    pairs = []
    for i in range(len(srcs)):
        for j in range(i + 1, len(srcs)):
            a, b = srcs[i], srcs[j]
            br = [s for s in bridges if s["instance_key"] not in (a["instance_key"], b["instance_key"])]
            pairs.append(pair(a, b, lines[a["instance_key"]], lines[b["instance_key"]], normalisers,
                              line_pairings, br, _merge_indexes(bridge_idx[s["instance_key"]] for s in br) if br else None))
    out_sources = []
    for s in srcs:
        mine = [p for p in pairs if s["instance_key"] in (p["a"], p["b"])]
        out_sources.append({"instance_key": s["instance_key"], "kind": s.get("kind"), "label": s["label"],
                            "layout": s.get("layout"), "lines": len(s.get("rows") or []),
                            "columns": {f: (list(c) if isinstance(c, tuple) else c) for f, c in (s.get("columns") or {}).items()},
                            "fields": [f for f in FIELD_ORDER if f in (s.get("columns") or {})],
                            "linked_to": sum(1 for p in mine if p["linked"]),
                            "shared_with": sum(1 for p in mine if p["shared"]),
                            "read_ok": True, "note": s.get("note")})
    note = None
    if not srcs:
        note = NOTE_NO_REPORTS
    elif len(srcs) == 1:
        note = NOTE_ONE_REPORT
    return {"sources": out_sources, "skipped": skipped, "pairs": pairs,
            "fields": [{"field": f["field"], "label": f["label"], "strength": f["strength"]} for f in LINK_FIELDS],
            "bridges": [s["instance_key"] for s in bridges], "note": note}


def matrix(rep):
    """The Stage-4 grid: rows × cols = the sources; a cell = the strongest shared field and the
    match counts each way (no samples — those are the pair detail)."""
    cells = {}
    for p in rep.get("pairs") or []:
        best = next((f for f in p["fields"] if f["field"] == p["strongest"]), None) if p["strongest"] else None
        if best is None and p["fields"]:
            best = max(p["fields"], key=lambda f: FIELD_STRENGTH[f["field"]])
        cell = {"a": p["a"], "b": p["b"], "shared": p["shared"], "strongest": p["strongest"], "linked": p["linked"],
                "note": p["note"],
                "field": best["field"] if best else None, "label": best["label"] if best else None,
                "basis": best["basis"] if best else None,
                "a_to_b": ({"matched": best["a_to_b"]["matched"], "lines": best["a_to_b"]["lines"], "no_key": best["a_to_b"]["no_key"]}
                           if best else None),
                "b_to_a": ({"matched": best["b_to_a"]["matched"], "lines": best["b_to_a"]["lines"], "no_key": best["b_to_a"]["no_key"]}
                           if best else None)}
        cells[f"{p['a']}|{p['b']}"] = cell
    return {"sources": [{"instance_key": s["instance_key"], "label": s["label"], "kind": s["kind"], "lines": s["lines"],
                         "linked_to": s["linked_to"], "shared_with": s["shared_with"], "fields": s["fields"]}
                        for s in rep.get("sources") or []],
            "skipped": rep.get("skipped") or [], "cells": cells, "note": rep.get("note"),
            "fields": rep.get("fields"), "bridges": rep.get("bridges") or []}


def detail(rep, a, b):
    """One pair's full detail (either order), or None."""
    for p in rep.get("pairs") or []:
        if {p["a"], p["b"]} == {a, b}:
            return p
    return None


def fingerprint(stage_rows):
    """What the cached result was computed FROM: every stage-2/3 instance's key, its verified_at and
    the rows it landed. A cache whose fingerprint differs from the live rows is STALE and said so."""
    out = []
    for r in stage_rows or []:
        if _s(r.get("stage")) not in ("2", "3"):
            continue
        vn = r.get("verified_numbers") or {}
        out.append([_s(r.get("instance_key")), _s(r.get("verified_at")), vn.get("rows_landed")])
    return sorted(out)


def linked_note(src):
    """The one-line Stage-4 note per row: "linked to N other reports"."""
    if src is None:
        return None
    n = int(src.get("linked_to") or 0)
    m = int(src.get("shared_with") or 0)
    if n:
        return f"linked to {n} other report{'s' if n != 1 else ''}"
    if m:
        return f"shares a column with {m} other report{'s' if m != 1 else ''} — no line matched"
    return "no column in common with another report"
