"""THE ONE HOME for "what does this business declare at the end of a day, and what is a unit worth".

Migration 1072. PURE — dicts in, dicts out, no DB, no network.

THE CLASS THIS FIXES (owner 2026-10-10, NY Logistics / UPS Store). A tenant's declarable categories
were expressed in three disconnected places that each carried their own copy of the vocabulary:

  1. `closing/count_config.py` (mig 501) — admin-defined, but COUNTS ONLY, store-day only, with no
     notion of what a unit is worth. "Printing Services, 10% of sales" and "Mailbox 1-Year, $20 each"
     have nowhere to live, and "which employee generated the most revenue today" is unanswerable
     because a declaration has no employee on it.
  2. The targets editor — four wireless categories hard-coded in the page AND as physical columns on
     `commcalc.targets`. A store that sells no activations cannot set a target.
  3. The incentive side — nothing at all. A rule that pays on "drop-offs" had no category to point at.

So: ONE registry says what the tenant declares; every surface DEREFERENCES it. Adding a category is a
config row, never code (RULE TWO), and it reaches the closing form, the incentive rules and the
targets editor with no change to any of them. `harness_metric_catalog_lock.py` fails the build if a
caller stops asking, or if a second copy of the vocabulary appears.

TWO PHYSICAL STORES, ONE READER (the `envelope_report.is_modern_row` precedent, index §29.7). A
count-only, store-level metric keeps writing `daily_closing.counts` jsonb exactly as mig 501 does —
byte-for-byte, so no existing tenant moves. Anything richer (an amount, or a per-employee
declaration) writes `commcalc.closing_metric_line`. NOBODY reads either store directly: callers ask
`declared(...)`, which folds both into one answer. Hand-rolling the fold is the duplicate defect.

MONEY POSTURE: this module measures, it never pays. `commcalc/daily_incentive.py` turns a day's
declared metrics into money, and persisting that is the router's job.
"""

# ── Vocabulary. These four constants ARE the vocabulary; nothing else may spell them. ─────────────

# How a category is measured. A metric may carry both (12 drop-offs worth $84.00).
VALUE_KINDS = ("count", "amount", "both")

# Who declares it. 'store' = one figure for the store-day (mig 501 behaviour). 'employee' = a line per
# person who worked, which is what makes "who generated the most revenue today" answerable.
DECLARED_BY = ("store", "employee")

# Which B2B count-recon bucket a count rolls into. Unchanged from mig 501 — flag-only, never a gate.
RECON_CLASSES = ("activation", "upgrade", "other")

# The default when a tenant has declared nothing: mig 501's hard-coded three, reproduced exactly.
# Mirrors count_config.STANDARD_DEFS; the lock harness asserts the two never drift.
STANDARD_KEYS = ("upgrade_count", "new_line_count", "postpaid_count")


def _num(v, default=0.0):
    try:
        if v in (None, ""):
            return float(default)
        return float(v)
    except (TypeError, ValueError):
        return float(default)


def _int(v, default=0):
    try:
        if v in (None, ""):
            return int(default)
        return int(float(v))
    except (TypeError, ValueError):
        return int(default)


def _key(v):
    return str(v or "").strip()


# ── The catalog ───────────────────────────────────────────────────────────────────────────────────

def build(count_defs, *, tender_defs=None):
    """The tenant's declared-metric catalog, in display order.

    `count_defs` are `closing_count_field_def` rows (the editor's own list — pass the ACTIVE ones for
    a live surface, all of them for the editor). `tender_defs` are `closing_tender_def` rows; a tender
    is a MONEY metric a rule may pay a percentage of (the day's card volume, say), so it joins the
    catalog read-only — a tender is edited on its own screen, never here.

    Each entry:
      key           the metric key a rule or a target points at
      label         what the rep and the admin see
      value_kind    'count' | 'amount' | 'both'
      declared_by   'store' | 'employee'
      unit_price    $ per unit, or 0.0 — how a counted category earns its way into revenue
      in_revenue    does this metric's money join the day's gross revenue
      recon_class   'activation' | 'upgrade' | 'other'
      source        'count_field' | 'tender' — which registry owns the row
      editable      can an admin change it on the Count Fields screen
      is_standard    one of mig 501's built-in three (writes a physical daily_closing column)
    """
    out, seen = [], set()
    for i, d in enumerate(count_defs or []):
        key = _key(d.get("field_key"))
        if not key or key in seen:
            continue
        seen.add(key)
        vk = _key(d.get("value_kind")).lower() or "count"
        if vk not in VALUE_KINDS:
            vk = "count"
        db = _key(d.get("declared_by")).lower() or "store"
        if db not in DECLARED_BY:
            db = "store"
        rc = _key(d.get("recon_class")).lower() or "other"
        if rc not in RECON_CLASSES:
            rc = "other"
        unit_price = _num(d.get("unit_price"))
        # A counted category with a unit price earns revenue unless the admin says otherwise; an
        # amount-valued one is revenue by construction. `in_revenue` False is how a tenant declares a
        # volume it tracks but does not sell (envelopes opened, say).
        default_rev = vk in ("amount", "both") or unit_price > 0
        in_rev = d.get("in_revenue")
        out.append({
            "key": key,
            "label": _key(d.get("label")) or key,
            "value_kind": vk,
            "declared_by": db,
            "unit_price": unit_price,
            "in_revenue": default_rev if in_rev is None else bool(in_rev),
            "recon_class": rc,
            "source": "count_field",
            "editable": True,
            "is_standard": bool(d.get("is_standard")) or key in STANDARD_KEYS,
            "sort_order": _int(d.get("sort_order"), i),
        })
    for j, t in enumerate(tender_defs or []):
        key = _key(t.get("tender_key"))
        if not key:
            continue
        ck = "tender:" + key
        if ck in seen:
            continue
        seen.add(ck)
        out.append({
            "key": ck,
            "label": (_key(t.get("label")) or key) + " (tender)",
            "value_kind": "amount",
            "declared_by": "store",
            "unit_price": 0.0,
            "in_revenue": False,     # a tender is HOW money arrived, not additional revenue
            "recon_class": "other",
            "source": "tender",
            "editable": False,
            "is_standard": False,
            "sort_order": 1000 + _int(t.get("sort_order"), j),
        })
    out.sort(key=lambda m: (m["sort_order"], m["label"]))
    # Every entry carries WHERE its value lives, so no caller — backend or screen — has to work it
    # out. A surface that decides for itself has reproduced the fold; the lock fails the build on it.
    for m in out:
        m["storage"] = storage_for(m)
    return out


def by_key(catalog):
    return {m["key"]: m for m in (catalog or [])}


def keys(catalog):
    return [m["key"] for m in (catalog or [])]


def revenue_keys(catalog):
    """The metrics whose money IS the day's gross revenue — the base every revenue target and every
    percent-of-amount rule measures against. ONE definition, so a target and a payout can never
    disagree about what 'sales' means."""
    return [m["key"] for m in (catalog or []) if m.get("in_revenue")]


def employee_keys(catalog):
    return [m["key"] for m in (catalog or []) if m.get("declared_by") == "employee"]


def is_employee_declared(catalog, key):
    m = by_key(catalog).get(_key(key))
    return bool(m and m.get("declared_by") == "employee")


def storage_for(metric):
    """WHERE a declared value lives. 'counts_jsonb' = mig 501's `daily_closing.counts` (and, for the
    built-in three, their physical columns); 'metric_line' = `commcalc.closing_metric_line` (mig 1072).

    This is the ONE rule for the two physical stores. A caller that decides for itself has reproduced
    the fold, which is the defect this module exists to prevent."""
    if not metric:
        return "counts_jsonb"
    if metric.get("declared_by") == "employee":
        return "metric_line"
    if metric.get("value_kind") in ("amount", "both"):
        return "metric_line"
    return "counts_jsonb"


# ── The reader: one day, both stores, one answer ──────────────────────────────────────────────────

def _blank(metric):
    return {"key": metric["key"], "label": metric["label"], "units": 0, "amount": 0.0,
            "value_kind": metric["value_kind"], "declared_by": metric["declared_by"],
            "in_revenue": bool(metric.get("in_revenue")), "declared": False}


def _earned(metric, units, amount):
    """A metric's money. An amount declared outright wins; otherwise units x the declared unit price.
    A counted category with no unit price earns nothing — it is a volume, not a sale."""
    if amount:
        return round(float(amount), 2)
    up = _num(metric.get("unit_price"))
    if up and units:
        return round(float(units) * up, 2)
    return 0.0


def declared(catalog, closing_row, metric_lines=None):
    """What a store-day declared, folded across both physical stores.

    Returns {metric_key: {units, amount, declared, label, ...}} for EVERY metric on the catalog —
    a metric nobody declared is present with zeros and `declared: False`, so a caller can tell
    "declared nothing" from "has no such category", which a sparse dict cannot.

    `closing_row` is a `commcalc.daily_closing` row (or None). `metric_lines` are
    `closing_metric_line` rows for this store-day; per-employee lines are SUMMED into the store-day
    figure here, so a store-level consumer never has to know a metric was declared per person.
    """
    row = closing_row or {}
    jcounts = row.get("counts") if isinstance(row.get("counts"), dict) else {}
    out = {}
    for m in (catalog or []):
        out[m["key"]] = _blank(m)
    for m in (catalog or []):
        if storage_for(m) != "counts_jsonb":
            continue
        k = m["key"]
        raw = row.get(k) if k in STANDARD_KEYS else jcounts.get(k)
        if raw in (None, ""):
            continue
        e = out[k]
        e["units"] = _int(raw)
        e["amount"] = _earned(m, e["units"], 0.0)
        e["declared"] = True
    cat = by_key(catalog)
    for ln in (metric_lines or []):
        k = _key(ln.get("metric_key"))
        m = cat.get(k)
        if not m:
            continue                      # a line for a retired category is not revenue, and not an error
        e = out[k]
        e["units"] = _int(e["units"]) + _int(ln.get("units"))
        e["amount"] = round(_num(e["amount"]) + _earned(m, _int(ln.get("units")), _num(ln.get("amount"))), 2)
        e["declared"] = True
    return out


def by_employee(catalog, metric_lines, *, closing_row=None):
    """Per-person declarations for a store-day: {employee_name: {metric_key: {units, amount}}}.

    Only metrics the catalog marks `declared_by='employee'` appear. A store-level metric is the
    store's, not any one person's — attributing it would invent a fact nobody declared.

    `closing_row` is accepted so a caller can pass the same pair it passes `declared()` without
    special-casing; it is deliberately unused, because a `daily_closing` row carries exactly one
    `employee_name` (whoever submitted) and crediting the day's store figures to the submitter is the
    attribution bug this whole module exists to prevent.
    """
    cat = by_key(catalog)
    out = {}
    for ln in (metric_lines or []):
        k = _key(ln.get("metric_key"))
        m = cat.get(k)
        if not m or m.get("declared_by") != "employee":
            continue
        who = _key(ln.get("employee_name"))
        if not who:
            continue
        slot = out.setdefault(who, {})
        e = slot.setdefault(k, {"units": 0, "amount": 0.0, "label": m["label"],
                                "in_revenue": bool(m.get("in_revenue"))})
        e["units"] = _int(e["units"]) + _int(ln.get("units"))
        e["amount"] = round(_num(e["amount"]) + _earned(m, _int(ln.get("units")), _num(ln.get("amount"))), 2)
    return out


def gross_revenue(catalog, declared_map):
    """The store-day's declared gross revenue: the sum over `revenue_keys`. THE definition — a target
    and a payout rule that both ask here can never disagree about what a day's sales were."""
    rk = set(revenue_keys(catalog))
    return round(sum(_num((declared_map or {}).get(k, {}).get("amount")) for k in rk), 2)


def employee_revenue(catalog, per_employee):
    """{employee_name: their declared revenue} from `by_employee` output. Same definition as
    `gross_revenue`, one person at a time — this is the "who generated the most today" answer."""
    out = {}
    for who, metrics in (per_employee or {}).items():
        out[who] = round(sum(_num(v.get("amount")) for v in metrics.values() if v.get("in_revenue")), 2)
    return out


def units_of(declared_map, key):
    return _int((declared_map or {}).get(_key(key), {}).get("units"))


def amount_of(declared_map, key):
    return round(_num((declared_map or {}).get(_key(key), {}).get("amount")), 2)
