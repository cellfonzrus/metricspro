"""Tenant-configurable closing tenders + smart value→tender mapping (migration 111).

Doctrine: an EMPTY config falls back to the hardcoded CANON_TENDERS / _canon_tender that live in
router.py, so a tenant that hasn't opted in behaves byte-for-byte identically. Used by the 3-way
recon, the sales / X-report bucketing, the closing form, and the smart-detect wizard.
"""


def load_tender_config(client, org_id):
    """(defs, map_rows) for a tenant. defs = active tender field definitions (empty → use hardcoded);
    map_rows = the raw-label→tender rules."""
    try:
        defs = (client.schema("commcalc").table("closing_tender_def").select("*")
                .eq("org_id", org_id).eq("is_active", True).order("sort_order").execute().data) or []
    except Exception:
        defs = []   # table not migrated yet → hardcoded fallback
    try:
        maps = (client.schema("commcalc").table("closing_tender_map").select("*")
                .eq("org_id", org_id).execute().data) or []
    except Exception:
        maps = []
    return defs, maps


def tender_axis(defs, canon_tenders, canon_labels):
    """The recon axis: the tenant's tender keys + labels when defined, else the hardcoded 7.
    Returns (keys, labels, recon_class_by_key, include_in_total_by_key)."""
    if defs:
        keys = [d.get("tender_key") for d in defs if d.get("tender_key")]
        labels = {d.get("tender_key"): (d.get("label") or d.get("tender_key")) for d in defs}
        rclass = {d.get("tender_key"): (d.get("recon_class") or "other") for d in defs}
        intotal = {d.get("tender_key"): bool(d.get("include_in_total", True)) for d in defs}
        return keys, labels, rclass, intotal
    keys = list(canon_tenders)
    labels = dict(canon_labels)
    # hardcoded recon classes for the built-in 7 (cash gate = cash; card gate = credit + ext_cc)
    rclass = {"cash": "cash", "credit": "card", "ext_cc": "card", "gift": "other",
              "store_acct": "other", "zelle": "other", "acima": "other"}
    return keys, labels, rclass, {k: True for k in keys}


def make_resolver(map_rows, report, hardcoded, axis_keys):
    """f(raw)->tender_key for a given report leg ('x_report'|'sales'). Uses the tenant map (rules for
    this report + 'both'), tested by ascending priority (specific before generic); when no rule matches
    it falls back to `hardcoded` (the built-in _canon_tender). A fallback key not in the tenant's axis
    is dropped (None) so a custom-only tenant doesn't get phantom standard buckets."""
    axis = set(axis_keys or [])
    rules = []
    for r in map_rows:
        rep = (r.get("report") or "both")
        if rep not in (report, "both"):
            continue
        labels = [str(x).strip().lower() for x in (r.get("source_labels") or []) if str(x).strip()]
        if not labels:
            continue
        rules.append((r.get("priority") if r.get("priority") is not None else 100,
                      r.get("tender_key"), (r.get("match_mode") or "substring"), labels))
    rules.sort(key=lambda x: x[0])

    def resolve(raw):
        t = (raw or "").strip().lower()
        if not t:
            return None
        for _pri, key, mode, labels in rules:
            if mode == "exact":
                if t in labels:
                    return key
            elif any(lab in t for lab in labels):
                return key
        fb = hardcoded(raw)                     # built-in substring rules
        if fb is None:
            return None
        return fb if fb in axis else None       # only keep a fallback that exists on this tenant's axis

    return resolve


def suggest_for_labels(raw_labels, keys, labels, hardcoded):
    """Smart suggestion per distinct raw POS label → the best tender on the tenant's axis + confidence
    ('exact' = a built-in rule matched an axis tender, 'fuzzy' = label/key token overlap, '' = none).
    Mirrors column_mapping.suggest's confidence tiers so the wizard can colour the dropdowns."""
    axis = set(keys or [])
    tokens = {k: (str(labels.get(k) or k) + " " + str(k)).lower().replace("_", " ") for k in keys}
    out = []
    for raw in raw_labels:
        r = str(raw or "").strip()
        if not r:
            continue
        suggested, conf = "", ""
        fb = hardcoded(r)
        if fb and fb in axis:
            suggested, conf = fb, "exact"
        else:
            low = r.lower()
            for k in keys:
                toks = [w for w in tokens[k].split() if len(w) > 2]
                if any(w in low or low in w for w in toks):
                    suggested, conf = k, "fuzzy"
                    break
        out.append({"raw_label": r, "suggested_tender": suggested, "confidence": conf})
    return out


# The 7 built-in tenders as seedable definitions (recon_class drives the cash/credit gate + 2-way recon).
STANDARD_DEFS = [
    ("cash", "Cash", "cash", True), ("credit", "Credit", "card", True),
    ("ext_cc", "External Credit Card", "card", True), ("gift", "Gift Card", "other", True),
    ("store_acct", "Store Account", "other", True), ("zelle", "Zelle / CashApp", "other", True),
    ("acima", "ACIMA (lease)", "other", True),
]


# ── Step-2 sample-file leg detection (2026-07-15 fix) ───────────────────────────────────────────
# BUG: the tender-config wizard's "upload a sample" always dumped every distinct value into the SALES
# leg — a tenant with no ingested X-report data (e.g. a new/incompletely-onboarded tenant like Luxelink)
# had NO way to map the x_report leg at all, since x_labels could only ever come from already-ingested
# commcalc.pos_tender_summary rows. Fixed by making Step 2 leg-aware: classify the uploaded sample by
# its OWN column shape (reusing the real X-Report importer's signature) instead of assuming 'sales'.
_SALES_SIG = {"salesperson", "trans id"}          # mirrors commcalc.router.SIGNATURES['sales']
_XR_STORE_K = {"store", "location", "store_name", "storename", "site", "register"}
_XR_TENDER_K = {"tender_type", "tender type", "tender", "payment_type", "payment type",
                "payment", "type", "media"}
_XR_AMT_K = {"amount", "total", "value", "net", "net amount", "amt"}


def classify_sample_file(content: bytes, filename: str, leg: str = "auto"):
    """Classify an uploaded tender-config sample as the 'sales' leg or the 'x_report' leg and pull the
    distinct raw tender values out of it, so Step 2's "Upload a sample" can populate EITHER leg — not
    just sales.

    Detection tiers (checked in order for leg='auto'; an explicit leg skips straight to its own tier
    but still degrades sanely if the file doesn't actually match):
      1. The REAL B2B Soft X-Report: a multi-sheet workbook, one sheet per store, each holding a
         'Tendered Amounts' matrix. Reuses `commcalc.router._parse_xreport` verbatim (lazy import —
         mirrors the existing commcalc<->closing lazy-import boundary used for `_send_alert` /
         `classify_contract_type`) so a sample is classified EXACTLY the way the real ingest path
         (`POST /commcalc/upload/x_report`) would treat it — same filename date-range validation, same
         'Tender Types'/'Net'/'Refunds|Sub Net' header signature.
      2. The Sales Transaction Details signature (Salesperson + Trans ID columns — mirrors
         commcalc.router.SIGNATURES['sales']) -> 'sales'.
      3. A flat/generic single-sheet X-report export (any POS) — a Tender-ish column PLUS a
         Store-ish or Amount-ish column, and NOT the sales signature -> 'x_report'. Mirrors the
         flexible column names the generic (non-B2B-Soft) X-report importer in commcalc.router
         accepts, kept local here as a detection-only heuristic (not itself an ingest path).
      4. Anything else with a bare 'tender'-named column -> 'sales' (today's original behaviour,
         byte-identical for a plain tender-column file with no X-report signature).

    `leg` ('sales'|'x_report'|'auto') lets the caller force a leg (Step 2's explicit upload buttons);
    an unrecognized value falls back to 'auto'. Returns (detected_leg, {raw_label, ...}, detail_str).
    Raises ValueError only for a genuinely bad file (unreadable, or an X-Report filename covering more
    than one day — the same validation the real ingest enforces)."""
    leg = (leg or "auto").strip().lower()
    if leg not in ("sales", "x_report"):
        leg = "auto"
    fn = filename or ""

    if leg in ("auto", "x_report"):
        try:
            from app.modules.commcalc.router import _parse_xreport  # lazy: avoids a commcalc<->closing cycle
        except Exception:
            _parse_xreport = None
        if _parse_xreport is not None:
            try:
                xr = _parse_xreport(content, fn, fallback_date=None)
            except ValueError:
                raise   # a real X-Report shape with a bad (multi-day) filename range — surface it
            except Exception:
                xr = []
            if xr:
                store_labels = {str(t).strip() for (_s, _d, t, _a) in xr if str(t).strip()}
                stores = {s for (s, _d, _t, _a) in xr}
                return "x_report", store_labels, f"B2B multi-sheet X-Report ({len(stores)} store sheet(s))"

    import pandas as pd
    import io
    try:
        df = (pd.read_excel(io.BytesIO(content)) if fn.lower().endswith((".xlsx", ".xls"))
              else pd.read_csv(io.BytesIO(content)))
    except Exception as e:
        raise ValueError(f"could not read sample file: {e}")
    cols = [str(c).strip() for c in df.columns]
    lcols = {c.lower() for c in cols}
    tcol = next((c for c in cols if "tender" in c.lower()), None)

    is_sales_shape = _SALES_SIG.issubset(lcols)
    is_flat_xreport_shape = (not is_sales_shape) and bool(_XR_TENDER_K & lcols) and \
        bool((_XR_STORE_K | _XR_AMT_K) & lcols)

    if leg == "x_report" or (leg == "auto" and is_flat_xreport_shape):
        if tcol is None:
            tcol = next((c for c in cols if c.lower() in _XR_TENDER_K), None)
        labels = ({str(v).strip() for v in df[tcol].dropna().unique() if str(v).strip()}
                  if tcol is not None else set())
        detail = ("flat X-report-shaped columns" if is_flat_xreport_shape else
                   ("no Tender-like column found" if tcol is None else "explicit X-Report leg"))
        return "x_report", labels, detail

    labels = ({str(v).strip() for v in df[tcol].dropna().unique() if str(v).strip()}
              if tcol is not None else set())
    detail = ("Sales Transaction Details columns" if is_sales_shape else
               ("Tender column (no X-report signature)" if tcol is not None else "no Tender column found"))
    return "sales", labels, detail


def db_sales_tender_labels(client, org_id):
    """Distinct raw Tender Type values already ingested for the SALES leg, org-scoped. UNIONs
    commcalc.raw_sales (the monthly authoritative upload) with commcalc.daily_sales_feed (mig 047, the
    daily B2B email feed a feed-only tenant like Luxelink relies on) — previously read raw_sales ONLY,
    so a tenant whose sales live solely in the daily feed got an empty sales leg with nothing to map.
    Each table read is independently guarded so one failing/un-migrated table doesn't blank the other."""
    labels = set()
    for table in ("raw_sales", "daily_sales_feed"):
        try:
            rows = (client.schema("commcalc").table(table).select("tender_type")
                    .eq("org_id", org_id).limit(20000).execute().data) or []
            labels |= {str(r.get("tender_type")).strip() for r in rows if str(r.get("tender_type") or "").strip()}
        except Exception:
            pass
    return labels
