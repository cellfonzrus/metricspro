"""PEER SALES COMPARISON — stores of comparable FOOT TRAFFIC, ranked on what they do with it.

OWNER DIRECTIVE (2026-10-08, verbatim): *"need to create another module for sales comparison between
the performance of store who have similar bill payments, since bill payments define the number of
people coming in, the comparison should include the total boxes with a drill down into new / port /
byod / swap / upgrade / tablet etc of whatever that number is made of, then the next columns will be
aal and then family plans and total acc and Accessory per box … as if one can do why not the other."*

THE IDEA, stated once so the number is never a mystery. A store's sales are not comparable with
another store's in the raw — a high-street door sees ten times the people a side street does. The
owner's insight is that BILL PAYMENTS are the measurable proxy for people through the door: nobody
walks in to pay a bill because a salesperson persuaded them to. So stores are grouped into TRAFFIC
BANDS by their bill-payment count, and inside a band the comparison is fair — the same number of
people walked in, so a gap in boxes sold is a gap in SELLING, not in footfall.

THE PEER BASIS IS VISITS, NOT DOLLARS. A $400 bill payment and a $20 one are both one person in the
store, so the band is built on DISTINCT bill-payment TRANSACTIONS.

════════════════════════════════════════════════════════════════════════════════════════════════════
DUPLICATE CHECK (build gate, CLAUDE.md) — AND WHAT IT CHANGED ABOUT THIS MODULE
════════════════════════════════════════════════════════════════════════════════════════════════════
The first draft of this file counted boxes, the activation split, swaps, tablets, accessory dollars
and bill payments for itself out of the sale lines. Searching the index for each one showed that
EVERY SINGLE ONE of them is already computed, per (store × rep × day) cell, by
`router._sales_cell_agg` — THE shared sales aggregation behind the Sales Report, Executive MTD, Daily
Targets, Productivity, Stack Ranking, Review and the zero-sales report. A second derivation of them
here would have been precisely the defect the index rules forbid: two paths answering one question,
guaranteed to drift the first time a tenant edits its box departments.

So this module DOES NOT TOUCH A SALE LINE. It is handed `_sales_cell_agg`'s cells and does only the
work that is genuinely new: the grouping, the two ratios the owner named, and the peer gap. Every
number it reports is therefore the SAME number the Sales Report shows, by construction rather than by
agreement, and a tenant's box or bill-payment configuration reaches this report for free.

What each column dereferences, cell field by cell field:

  total boxes      `cell['box_count']`     — THE box count: config-driven `box_departments` (mig 218)
                                             plus the `box_count_buckets` opt-in (mig 231, owner
                                             2026-07-24 "customer phone = BYOD must count").
  new / port /
  byod / upgrade   `_prem` / `_port` /     — distinct-transaction sets from `line_class.activation_class`
                   `_byod` / `_upg`          (THE activation predicate, index §6). 'new' is the owner's
                                             word for the platform's `premium` class, mapped once here.
  swap             `cell['_swap']`         — `line_class.exclusion_class` (THE one home, owner 2026-09-27).
  tablet           `cell['_dev_tablet']`   — the device dimension (owner 2026-09-28).
  aal              `cell['_aal']`          — `line_class.is_add_a_line`, THE one home as of this PR
                                             (index §59). The only other copy on the platform was a bare
                                             contract_type substring in `asset/router._promo_type`, which
                                             now dereferences it too.
  bill payments    `cell['_billpay_exec']` — the Executive-MTD `bill_payment` predicate (mig 962) at
                                             TRANSACTION grain, added to the one home in this PR beside
                                             the line-grain `bill_qty` it rides on.
  total acc        `cell['accessory_rev']` — THE shared `_is_accessory` classifier.
  family plans     `commcalc.raw_dlar_store.family_plan_pct` — the CARRIER's own store KPI feed, already
                                             the one home for a store KPI (index §10). The sales feed
                                             carries no family-plan fact at all, so this column is the
                                             carrier's or it does not exist.
  market           `core.scope.market_by_code`, injected as `resolve_market`.

NEW HERE, and nowhere else: the traffic band, `boxes_per_billpay`, `accessory_per_box`, the peer gap,
and `lagging()` / `prompt_sentence()` — the ONE definition of "this store is behind", so the screen,
the action plan and the report cards cannot disagree about who is being coached.

════════════════════════════════════════════════════════════════════════════════════════════════════
HONESTY RULES, because this report accuses people
════════════════════════════════════════════════════════════════════════════════════════════════════
  · A store with NO bill-payment transactions is NOT banded and NOT compared. It lands in `unbanded`
    with the reason said out loud, because a zero there usually means the tenant's bill-payment
    vocabulary is not the one Executive MTD counts, not that nobody walked in.
  · A band holding ONE store carries NO gap. Being alone in a band is not under-performance, and a
    gap invented from a single store is exactly the false accusation this report must never make.
  · A column that cannot be answered comes back `None` with a stated reason, never 0 — family plan
    with no carrier feed, AAL with the vocabulary explicitly emptied.
  · The drill-down is reported BESIDE the total, never as a partition of it. A receipt carrying both
    an upgrade line and a port line counts in both parts; `box_count` is its own tally with its own
    rule (device departments + the configured buckets). Presenting the parts as summing to the total
    would be a lie in both directions, and the harness pins that they are not claimed to.

PURE. Every input is handed in, so `backend/harness_peer_comparison.py` drives it DB-free.
"""

# ── THE BOX DRILL-DOWN, in the owner's order, each mapped to its cell field ───────────────────────
# (display key, label, the _sales_cell_agg set it reads). Nothing here is a predicate; every one of
# them is a set another home already filled.
BOX_PARTS = (
    ("new", "New activation", "_prem"),
    ("port", "Port-in", "_port"),
    ("byod", "BYOD", "_byod"),
    ("upgrade", "Upgrade", "_upg"),
    ("swap", "Swap", "_swap"),
    ("tablet", "Tablet", "_dev_tablet"),
)
BOX_PART_KEYS = tuple(k for k, _l, _f in BOX_PARTS)

# ── THE TRAFFIC BANDS (CONFIG, never code — RULE TWO) ─────────────────────────────────────────────
# House cuts, from the live distribution measured 2026-10-08 over September 2026 (28 banded stores,
# 83 to 616 bill-payment transactions in the month): they fall where the stores actually cluster, so
# no band is empty and none holds half the estate. The cuts are the LOWER bound of each band.
HOUSE_BANDS = (150, 250, 400)
BAND_MIN_PEERS = 2          # a band of one carries no gap — there is nobody to be behind


def resolve_bands(raw=None):
    """A tenant's band cuts → an ascending tuple of positive ints. Missing / junk → the house cuts.
    An explicitly EMPTY list is honoured and means ONE band holding every store (a tenant that wants
    the whole estate compared together). PURE."""
    if raw is None or not isinstance(raw, (list, tuple)):
        return tuple(HOUSE_BANDS)
    cuts = []
    for v in raw:
        try:
            n = int(float(str(v).replace(",", "").strip()))
        except (TypeError, ValueError):
            continue
        if n > 0 and n not in cuts:
            cuts.append(n)
    return tuple(sorted(cuts))


def band_of(billpay_txns, cuts=HOUSE_BANDS):
    """Which traffic band a store's bill-payment count falls in → (index, label). PURE.

    The band is named by the TRAFFIC, not by a judgement ("250–399 bill payments", never "mid-tier"):
    the whole report rests on the bands being a measurement, and a store cannot argue with its own
    count."""
    cuts = tuple(cuts or ())
    n = int(billpay_txns or 0)
    idx = 0
    for c in cuts:
        if n < c:
            break
        idx += 1
    lo = cuts[idx - 1] if idx else 0
    if idx >= len(cuts):
        return idx, f"{lo:,}+ bill payments"
    if not idx:
        return idx, f"Under {cuts[0]:,} bill payments"
    return idx, f"{lo:,}–{cuts[idx] - 1:,} bill payments"


# ── rolling the cells up to the store ─────────────────────────────────────────────────────────────
# The cell fields this module reads. Named once so a renamed field fails loudly in the lock rather
# than quietly reporting zeros (the §19.18 trap: a registry written but not wired).
CELL_SETS = ("_prem", "_port", "_byod", "_upg", "_swap", "_dev_tablet", "_aal", "_billpay_exec")
CELL_NUMBERS = ("box_count", "accessory_rev")


def roll_up(cells, store_of=None):
    """`_sales_cell_agg`'s cells → {store: {box_count, accessory_rev, <set name>: n, …}}. PURE.

    A DISTINCT-transaction set cannot be summed across cells — the same receipt would count once per
    rep and once per day it touches — so the sets are UNIONED to the store and counted at the end.
    `box_count` and `accessory_rev` are per-line tallies and do sum.

    `store_of(cell)` lets the caller supply the canonical store (the resolver), defaulting to the
    cell's own `store`."""
    per = {}
    for cell in (cells or {}).values() if isinstance(cells, dict) else (cells or []):
        c = cell or {}
        store = (store_of(c) if store_of else str(c.get("store") or "")) or ""
        st = per.get(store)
        if st is None:
            st = per[store] = {k: set() for k in CELL_SETS}
            for k in CELL_NUMBERS:
                st[k] = 0
        for k in CELL_SETS:
            v = c.get(k)
            if v:
                st[k] |= set(v)
        st["box_count"] += int(c.get("box_count") or 0)
        st["accessory_rev"] += float(c.get("accessory_rev") or 0.0)
    out = {}
    for store, st in per.items():
        row = {k: len(st[k]) for k in CELL_SETS}
        row["box_count"] = int(st["box_count"])
        row["accessory_rev"] = round(float(st["accessory_rev"]), 2)
        out[store] = row
    return out


def _ratio(num, den):
    """num / den, or None when there is no denominator — never a 0 standing in for "cannot say"."""
    if not den:
        return None
    return round(float(num or 0) / float(den), 2)


def _median(vals):
    v = sorted(x for x in vals if x is not None)
    if not v:
        return None
    mid = len(v) // 2
    return v[mid] if len(v) % 2 else round((v[mid - 1] + v[mid]) / 2.0, 2)


# The metrics a store is RANKED and PROMPTED on inside its band. `higher_is_better` is True for every
# one of them today and is STATED rather than assumed, so a future metric where less is better (a
# return rate, a port-out rate) cannot silently invert the gap.
GAP_METRICS = (
    ("boxes", "Boxes", True),
    ("boxes_per_billpay", "Boxes per bill payment", True),
    ("aal", "Add-a-line", True),
    ("accessory_revenue", "Accessory $", True),
    ("accessory_per_box", "Accessory $ per box", True),
    ("family_plan_pct", "Family plan %", True),
)
# The metric the action plan prompts on by default: the conversion of footfall into a sale, which is
# the owner's actual question ("if one can do why not the other" on the same traffic).
DEFAULT_GAP_METRIC = "boxes_per_billpay"


def _gaps(store_row, peers, metrics=GAP_METRICS):
    """How far ONE store is from the BEST and the MEDIAN of its band, per ranked metric. PURE.

    `peers` is every store in the band INCLUDING this one — the median must include it, or a band of
    two has no median. Returns {} when the band is too small to have a peer.

    `metrics` is the ranked set, defaulting to this report's own. It is a PARAMETER so that another
    report can rank its stores inside the same bands WITHOUT a second median, a second gap rule or a
    second idea of "behind" — see `with_extra_metric`. Every caller that does not pass it is
    byte-identical to before the parameter existed."""
    if len(peers) < BAND_MIN_PEERS:
        return {}
    out = {}
    for key, label, higher in metrics:
        mine = store_row.get(key)
        vals = [p.get(key) for p in peers if p.get(key) is not None]
        if mine is None or not vals:
            continue
        best = max(vals) if higher else min(vals)
        med = _median(vals)
        out[key] = {
            "label": label, "mine": mine, "band_best": best, "band_median": med,
            "gap_to_best": round((best - mine) if higher else (mine - best), 2),
            "gap_to_median": (None if med is None else
                              round((med - mine) if higher else (mine - med), 2)),
            "behind_best": bool((mine < best) if higher else (mine > best)),
            "behind_median": bool(med is not None and ((mine < med) if higher else (mine > med))),
        }
    return out


UNBANDED_REASON = (
    "No bill-payment transactions landed for this store in the window, so there is no traffic "
    "measurement to band it by. Either the store took none, or this tenant's bill-payment vocabulary "
    "is not the one the Executive MTD counts — check Exec Metric Definitions before reading anything "
    "into it."
)
BAND_BASIS = ("Distinct bill-payment transactions — the Executive MTD Bill Payment predicate at "
              "transaction grain, so one visit counts once however many lines the receipt carries.")


def column_caveats(payload, *, device_dimension=None, box_count_buckets=None,
                   unresolved_stores=None):
    """What this report CANNOT answer for this tenant, and why — one sentence each, from facts the
    caller read off the org's own config. PURE.

    This exists because every one of these shows up on the screen as a plausible number: a tablet
    column of 0, a box count that quietly omits BYOD, a blank family-plan cell. A person reading the
    table would take all three for performance. They are configuration, and the report says so next
    to the column rather than letting a manager coach a rep on them.

    Each caveat is derived from a measured fact, never guessed:
      · `device_dimension`    — `line_class.devices_configured(line_rules)`. Off = `_dev_tablet` is
                                empty for every cell by design, so the Tablet column is NOT zero
                                tablets sold, it is "this tenant has not declared which products are
                                tablets". Measured on the house org 2026-10-08: off.
      · `box_count_buckets`   — `acfg['box_count_buckets']`. Empty = a BYOD transaction carries no
                                device-department line and so adds no box, which contradicts the
                                owner's own ruling, given twice (2026-07-24 and again 2026-10-08:
                                "byod and tablets count towards the total boxes"). Measured
                                2026-10-08: EMPTY on Cellfonz R Us (which is also the house-default
                                row, so NY LOGISTICS inherits it), but ['byod'] on Luxelink and
                                Vzone — the same question answered two ways across tenants. On
                                September 2026 the tick moves Cellfonz boxes 1,159 -> 1,760 (+52%)
                                and raises the Daily-Targets conversion rate with them. TABLETS need
                                nothing here: 'TABLET - XP' is already a box department, so a tablet
                                has always counted as a box (48 such lines in September).
      · `unresolved_stores`   — stores whose raw key did not resolve to a store code, so a carrier
                                KPI keyed on the code cannot reach them.
    """
    out = []
    if device_dimension is False:
        out.append({
            "column": "tablet", "severity": "cannot_answer",
            "message": ("The Tablet column reads 0 for every store because this tenant has not "
                        "declared which products are tablets — the device dimension is off, so no "
                        "sale is ever tagged as one. That is a setting, not a sales result. Tablets "
                        "ARE already in the box total (a tablet sale carries a tablet "
                        "device-department line, which is what a box is counted from), so this is "
                        "the SPLIT that cannot be shown, never a missing box. Declaring the device "
                        "dimension fills the column in.")})
    if box_count_buckets is not None and not box_count_buckets:
        out.append({
            "column": "boxes", "severity": "understated",
            "message": ("Total boxes currently counts device-department lines only. A BYOD sale "
                        "carries no device line, so BYOD boxes are NOT in this total — on any screen "
                        "on the platform, not just this one. The 2026-07-24 ruling was that a "
                        "customer-phone / BYOD activation should count as a box; the tick "
                        "\u201cCount BYOD / customer-phone toward total boxes sold\u201d, in the Sales "
                        "Report\u2019s Classification settings, is what applies it.")})
    n = len(unresolved_stores or ())
    if n:
        out.append({
            "column": "family_plan_pct", "severity": "partial",
            "message": (f"{n} store(s) could not be matched to a store code, so the carrier's "
                        f"family-plan and AAL-conversion figures cannot be attached to them and show "
                        f"blank: {', '.join(sorted(unresolved_stores)[:6])}"
                        f"{' …' if n > 6 else ''}. Blank here means unmatched, never 0%."),
            "stores": sorted(unresolved_stores)})
    return out


def build(cells, *, bands=None, store_kpis=None, resolve_market=None, store_of=None,
          period=None, window_label=None, aal_configured=None, kpi_feed=None, params=None,
          caveats=None):
    """THE payload. PURE apart from the two injected resolvers.

    `cells`      — `router._sales_cell_agg(rows, acfg, exec_cfg=…)`. exec_cfg MUST be supplied by the
                   caller, because `_billpay_exec` (the peer basis) is only populated when it is; a
                   caller that forgets gets every store unbanded with the reason said, never a
                   silent zero.
    `store_kpis` — {store: {'family_plan_pct': …, 'aal_conversion': …}} from `raw_dlar_store`, keyed
                   the same way `store_of` keys the cells. Absent = the carrier does not report it.
    `kpi_feed`   — a sentence naming where the KPI columns came from, or why they are blank.
    """
    cuts = bands if isinstance(bands, tuple) else resolve_bands(bands)
    rolled = roll_up(cells, store_of)
    kpis = store_kpis or {}

    built, unbanded = [], []
    for store in sorted(s for s in rolled if s):
        r = rolled[store]
        boxes = int(r["box_count"])
        acc = r["accessory_rev"]
        billpay = int(r["_billpay_exec"])
        kpi = kpis.get(store) or {}
        row = {
            "store": store,
            "market": (resolve_market(store) if resolve_market else "") or "",
            "billpay_txns": billpay,
            "boxes": boxes,
            "parts": {k: r[f] for k, _l, f in BOX_PARTS},
            # the owner's two ratios. boxes_per_billpay is the conversion of footfall into a sale —
            # the number the whole band comparison exists to expose.
            "boxes_per_billpay": _ratio(boxes, billpay),
            "aal": (None if aal_configured is False else int(r["_aal"])),
            "accessory_revenue": acc,
            "accessory_per_box": _ratio(acc, boxes),
            "family_plan_pct": kpi.get("family_plan_pct"),
            "aal_conversion_pct": kpi.get("aal_conversion"),
        }
        if billpay <= 0:
            row["reason"] = UNBANDED_REASON
            unbanded.append(row)
            continue
        row["band"], row["band_label"] = band_of(billpay, cuts)
        built.append(row)

    by_band = {}
    for r in built:
        by_band.setdefault(r["band"], []).append(r)
    for r in built:
        r["gaps"] = _gaps(r, by_band[r["band"]])
        r["peers"] = len(by_band[r["band"]]) - 1

    bands_out = []
    for idx in sorted(by_band):
        peers = sorted(by_band[idx], key=lambda x: -x["boxes"])
        by_band[idx] = peers
        bands_out.append({
            "band": idx, "label": peers[0]["band_label"], "stores": len(peers),
            "comparable": len(peers) >= BAND_MIN_PEERS,
            "billpay_low": min(p["billpay_txns"] for p in peers),
            "billpay_high": max(p["billpay_txns"] for p in peers),
            "boxes_best": max(p["boxes"] for p in peers),
            "boxes_median": _median([p["boxes"] for p in peers]),
            "leader": peers[0]["store"],
            "note": (None if len(peers) >= BAND_MIN_PEERS else
                     "Only one store falls in this traffic band, so it has no peer to be compared "
                     "with and carries no gap."),
        })

    built.sort(key=lambda r: (r["band"], -r["boxes"]))
    unbanded.sort(key=lambda r: -r["boxes"])
    return {
        "period": period,
        "window_label": window_label,
        "band_cuts": list(cuts),
        "band_basis": BAND_BASIS,
        "box_parts": [{"key": k, "label": l} for k, l, _f in BOX_PARTS],
        "parts_are_not_a_partition": (
            "The drill-down sits beside the box total, not inside it. Boxes count device-department "
            "lines plus the activation buckets this tenant configured; a receipt naming two "
            "activation types counts in both parts. The parts are not expected to sum to the total."),
        "gap_metrics": [{"key": k, "label": l, "higher_is_better": h} for k, l, h in GAP_METRICS],
        "default_gap_metric": DEFAULT_GAP_METRIC,
        "bands": bands_out,
        "rows": built,
        "unbanded": unbanded,
        "kpi_feed": kpi_feed,
        "aal_configured": (True if aal_configured is None else bool(aal_configured)),
        # What the table cannot answer for this tenant, column by column — see column_caveats.
        "caveats": list(caveats or ()),
        "params": params or {},
        "note": (None if built else
                 "No store in this window has a bill-payment transaction to band by, so there is "
                 "nothing to compare. Bill payments come from the same sales lines the Executive MTD "
                 "counts — check the period, and that the daily feed has loaded."),
    }


# ── THE PROMPT — one definition of "behind", so every surface coaches the same stores ─────────────
def lagging(payload, metric=DEFAULT_GAP_METRIC, min_gap=0.0):
    """Every store BEHIND ITS BAND'S MEDIAN on `metric`, worst gap first. THE one definition of
    "lagging" for this report, read by the screen, the action plan and both report cards. PURE."""
    leaders = {b.get("band"): b.get("leader") for b in (payload or {}).get("bands") or []}
    out = []
    for r in (payload or {}).get("rows") or []:
        g = (r.get("gaps") or {}).get(metric)
        if not g or not g.get("behind_median"):
            continue
        if (g.get("gap_to_median") or 0) < min_gap:
            continue
        out.append({"store": r["store"], "market": r.get("market"),
                    "band": r.get("band"), "band_label": r.get("band_label"),
                    "billpay_txns": r.get("billpay_txns"), "metric": metric,
                    "label": g.get("label"), "mine": g.get("mine"),
                    "band_median": g.get("band_median"), "band_best": g.get("band_best"),
                    "gap_to_median": g.get("gap_to_median"), "gap_to_best": g.get("gap_to_best"),
                    "leader": leaders.get(r.get("band"))})
    out.sort(key=lambda x: -(x.get("gap_to_median") or 0))
    return out


def prompt_sentence(item):
    """The coaching sentence for one lagging store — the owner's "if one can do why not the other",
    said with the two numbers that make it arguable rather than as an accusation. PURE."""
    i = item or {}
    mine, med, best, lead = i.get("mine"), i.get("band_median"), i.get("band_best"), i.get("leader")
    if mine is None or med is None:
        return ""
    tail = (f" {lead} is at {best:,.2f} on the same traffic." if lead and best is not None else "")
    return (f"{i.get('store')} is at {mine:,.2f} {str(i.get('label') or '').lower()} against a "
            f"{med:,.2f} median for stores in its own traffic band ({i.get('band_label')})."
            f"{tail} The same number of people walked in, so this gap is sell-through, not footfall.")


# ── ONE CALLER-SUPPLIED METRIC, RANKED THROUGH THIS MODULE'S OWN MACHINERY ───────────────────────
# WHY THIS EXISTS. §60's spiff-impact report asks "which stores are lacking the sales that earn this
# spiff" — which is this module's question ("behind its own traffic band") asked about a number this
# module does not compute. The alternatives were both the defect the index rules forbid: re-implement
# the median, the gap, `lagging()` and `prompt_sentence()` over there (two definitions of behind,
# certain to drift the first time BAND_MIN_PEERS or the median rule changes), or teach this module to
# read the carrier statement (a second derivation of money that §58 already owns).
#
# So instead the caller hands in ITS number per store, and the band, the median, the gap, the verdict
# and the sentence all stay here. A report that uses this cannot disagree with the peer screen about
# who is behind, because it is the same code deciding.
def with_extra_metric(payload, key, label, values, higher_is_better=True):
    """Fold ONE caller-supplied per-store metric into a built payload and re-rank the bands. PURE.

    `values` is {store: number-or-None}. A store missing from it, or carrying None, keeps NO value
    for the metric and so is skipped by `_gaps` exactly as a store with no family-plan figure is —
    absence is never ranked as a zero.

    Mutates and returns `payload` (it is the caller's own freshly-built dict). `gap_metrics` gains
    the entry so a surface can render the column, and `lagging(payload, metric=key)` /
    `prompt_sentence` then work on it unchanged — which is the whole point."""
    if not payload or not key:
        return payload
    metrics = tuple(GAP_METRICS) + ((key, label or key, bool(higher_is_better)),)
    vals = values or {}
    for bucket in ("rows", "unbanded"):
        for r in payload.get(bucket) or ():
            v = vals.get(r.get("store"))
            r[key] = (None if v is None else round(float(v), 2))
    by_band = {}
    for r in payload.get("rows") or ():
        by_band.setdefault(r.get("band"), []).append(r)
    for r in payload.get("rows") or ():
        r["gaps"] = _gaps(r, by_band[r.get("band")], metrics=metrics)
    gm = payload.setdefault("gap_metrics", [])
    if not any(g.get("key") == key for g in gm):
        gm.append({"key": key, "label": label or key, "higher_is_better": bool(higher_is_better)})
    for b in payload.get("bands") or ():
        peers = by_band.get(b.get("band")) or []
        pv = [p.get(key) for p in peers if p.get(key) is not None]
        b[f"{key}_best"] = (max(pv) if pv else None)
        b[f"{key}_median"] = _median(pv)
    return payload


# ══════════════════════════════════════════════════════════════════════════════════════════════════
# THE ACTION-PLAN ITEM (index §59.7, owner 2026-10-08: "it should trigger in teh action plan for the
# sales reps their managers and dm and market manager to prompt them to increase the sales for those
# laggin stores as if one can do why not the other").
#
# WHY THIS LIVES HERE AND NOT IN THE ACTION PLAN. "Which stores are behind their peers" already has
# ONE home — `lagging()` above — and the coaching sentence has one home in `prompt_sentence()`. The
# action plan DEREFERENCES both; it does not re-decide who is behind, because the screen, the plan
# and (next) the two report cards disagreeing about which stores are lagging is exactly the drift
# these rules forbid. All this function does is put that one verdict into the shape the plan's
# renderer already consumes: {severity, metric, title, detail}.
#
# SEVERITY IS A MEASUREMENT, NOT A MOOD — and the first draft of this rule was neither.
#
# THE BUG, KEPT AS A COMMENT BECAUSE IT IS INSTRUCTIVE. The first rule was "`critical` when the store
# is further from the band's BEST than from its median". That is not a measurement, it is ARITHMETIC:
# if mine < median <= best then (best - mine) >= (median - mine) ALWAYS. So every lagging store came
# back `critical` — all 12 of them on live September data — and the check that was supposed to prove
# the rule (§J6) passed because it asserted a tautology. A severity whose every case is the worst case
# carries no information, and it would have added 12 to a "critical items" count that means "today".
#
# THE RULE, CHOSEN FROM THE MEASURED SPREAD. How far below the median, as a SHARE of the median —
# which does discriminate. Live September 2026, the 12 lagging stores ran from 5.3% to 42.1% below
# their own band median, and a 25% cut splits them 6/6: B-2509 at 42% below is a real gap to coach,
# B-3PL at 5.3% below is noise and must not shout. So:
#   · critical — more than CRITICAL_SHORTFALL below the band median.
#   · warning  — behind the median by less than that.
# A store with no leader to point at (a band of one, or a band whose best IS its median) is never
# `critical` whatever its shortfall: there is no proof anybody did better on that traffic, so the plan
# does not imply there is.
PEER_METRIC_LABEL = "peer_gap"
CRITICAL_SHORTFALL = 0.25


def peer_action_item(item):
    """ONE lagging row (from `lagging()`) → the action plan's own item shape, or None.

    PURE. Returns None for a row that cannot support a prompt — no numbers, or no band median — so a
    caller can map this over `lagging()` and filter, and never renders an item that accuses a store
    on a figure the report could not compute."""
    i = item or {}
    sentence = prompt_sentence(i)
    if not sentence:
        return None
    med, best, lead = i.get("band_median"), i.get("band_best"), i.get("leader")
    gap_med = i.get("gap_to_median") or 0.0
    gap_best = i.get("gap_to_best") or 0.0
    # Somebody must demonstrably have done better on the same traffic for this to be `critical` —
    # otherwise the owner's "if one can do why not the other" has no "one".
    has_leader = bool(lead) and best is not None and med is not None and best > med
    shortfall = (gap_med / med) if med else 0.0
    sev = "critical" if (has_leader and shortfall > CRITICAL_SHORTFALL) else "warning"
    label = str(i.get("label") or "").lower()
    title = (f"Behind {lead} on the same traffic — {label}" if has_leader
             else f"Behind its traffic band on {label}")
    return {"severity": sev, "metric": PEER_METRIC_LABEL,
            "title": title, "detail": sentence,
            "shortfall_pct": round(shortfall * 100.0, 1),
            # The numbers the sentence is built from, so a surface can render its own layout without
            # re-deriving the verdict (and so the report cards can tick it off against a target).
            "peer": {"band": i.get("band"), "band_label": i.get("band_label"),
                     "billpay_txns": i.get("billpay_txns"),
                     "mine": i.get("mine"), "band_median": med, "band_best": best,
                     "gap_to_median": gap_med, "gap_to_best": gap_best,
                     "leader": lead, "gap_metric": i.get("metric")}}


def peer_items_by_store(payload, metric=DEFAULT_GAP_METRIC, min_gap=0.0):
    """{STORE: item} for every lagging store that can support a prompt, keyed UPPER-CASE because the
    action plan keys on `store_code.upper()`. PURE — and it is `lagging()` that decides who is in
    here, so a caller cannot widen or narrow the definition of behind by accident."""
    out = {}
    for row in lagging(payload, metric=metric, min_gap=min_gap):
        it = peer_action_item(row)
        if not it:
            continue
        key = str(row.get("store") or "").strip().upper()
        if key:
            out[key] = it
    return out
