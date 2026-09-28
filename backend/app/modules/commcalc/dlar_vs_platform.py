"""CARRIER FEED REPORTS  vs  WHAT THE STORE'S TRANSACTIONS SAY — per metric, per rep, per store.
READ-ONLY, BOOKS NOTHING. Pure (rows + config in, rows out) — proof `backend/harness_dlar_vs_platform.py`.

OWNER DIRECTIVE 2026-09-27, verbatim: *"This brings us to another report the comparison of the dlar
report for numbers and the ones we are reporting in thr platform , they should be the same - the source
of truth is the transaction done in thr store so all reporting should have the same data , create a
report for the. Difference of thr incoming data from dlar and whatever you are using to assess the
difference and show it it in management dashboard"*.

THE DESIGN STATEMENT IS THE IMPORTANT HALF: **the transaction done in the store is the source of
truth; the carrier feed is a CLAIM ABOUT it.** So this report never picks a winner per row. Its job is
to make every disagreement visible and ATTRIBUTABLE, because the three causes are different problems
with different owners:

  1. A COUNTING DEFINITION difference — the carrier counts on its own basis. Index §19.28 (3): five
     August invoices carried a BYOD-Swap line and an Upgrade line on ONE phone line, and the carrier
     counts a swap as neither. Measured end to end here: 20 events − 5 BYOD-Swap − 2 Ineligible Port-In
     = 13, which is exactly the carrier's own denominator. This is arithmetic and it is EXACT, so it is
     attributed first, and the units it removes are named by kind.
  2. A VINTAGE difference — our stored slice of the feed is STALE. Both Elevate Go reports are
     month-to-date for the period the portal is CURRENTLY serving, so once a month rolls its slice is
     frozen at whatever day the last pull happened to be (August 2026's rep slice: 24 of 31 days). A
     gap explained by "our snapshot is 24 of 31 days" must NOT read as a counting disagreement. The
     as-of date is DEREFERENCED from `dlar_sweep.vintage` — never re-derived here.
  3. NEITHER — the residual. That is the interesting one and it is the reason the report exists.

WHAT IS REUSED RATHER THAN REBUILT (duplicate-check, CLAUDE.md build gate)
-------------------------------------------------------------------------
  • the PLATFORM side is the ONE activation/upgrade count — `line_class.activation_units` /
    `line_class.new_activation_units`, the same functions Executive MTD and the Boost pay engine
    count on (§19.31). This module takes those numbers as INPUT and never classifies a sale line
    itself: a report that derived its own count would become the third answer to "how many
    activations" and so the very defect it exists to expose.
  • the FEED side is `raw_dlar_rep` / `raw_dlar_store` AS LANDED — read, never recomputed, with its
    own grain and its own `as_of_date`.
  • the VINTAGE is `dlar_sweep.vintage(period, as_of)` (mig `1026`, §19.28) — passed in by the caller.
  • which feed column carries a KPI is `kpi_failing.REP_DLAR_COLUMNS` / `STORE_KPI_COLUMNS`, the
    existing one home at both grains. This module declares no second column map.
  • ABSENCE VOCABULARY is `carrier_vs_pay`'s, verbatim (`reported` / `measured_zero` /
    `not_reported`) — not a fourth spelling of the same three states.

WHY NOT AN EXISTING REPORT (checked, 2026-09-27): §31 `carrier_vs_pay` compares carrier money EARNED
with employee money PAID — money, not counts, and its two keys are deliberately never summed;
`sales_recon` compares our monthly upload with our daily feed (both sides ours, no carrier side);
`sales_comparison` is period-over-period change per category; `carrier_recon` is a parser for the
back-office rebate workbook. None holds a carrier-claim-vs-transaction comparison. It introduces no
table and no ingest path, so there is NO lineage-registry row (`books_to == []`).

RULE TWO: no carrier, tenant, store or product name appears here. Which metrics are compared is the
declaration below (derived from the existing feed maps); which units an exclusion removes is the org's
`line_class` config.
"""
from app.modules.commcalc import kpi_failing as _kf

# ── the three states a SIDE can be in (vocabulary reused verbatim from carrier_vs_pay / §31) ────────
STATE_REPORTED = "reported"
STATE_MEASURED_ZERO = "measured_zero"
STATE_NOT_REPORTED = "not_reported"
STATES = (STATE_REPORTED, STATE_MEASURED_ZERO, STATE_NOT_REPORTED)
STATE_NOTES = {
    STATE_REPORTED: "the side carries a value for this metric.",
    STATE_MEASURED_ZERO: "the side WAS looked up and its value is zero. A real zero, stated as measured.",
    STATE_NOT_REPORTED: ("the side does not carry this metric at all — the feed column stopped arriving, "
                         "or the platform has no transaction-derived counterpart. The figure is None, "
                         "NEVER 0, and the difference is None rather than the whole of the other side."),
}

# ── why a difference exists. The last one is the finding. ───────────────────────────────────────────
CAUSE_DEFINITION = "counting_definition"   # exact: units an exclusion kind removes from our count
CAUSE_VINTAGE = "feed_vintage"             # our stored slice does not reach the period's last day
CAUSE_GRAIN = "grain"                      # the two sides are not counting the same population
CAUSE_UNATTRIBUTED = "unattributed"        # neither — the one to look at
CAUSES = (CAUSE_DEFINITION, CAUSE_VINTAGE, CAUSE_GRAIN, CAUSE_UNATTRIBUTED)
CAUSE_NOTES = {
    CAUSE_DEFINITION: ("the two sides count different units. The named exclusion kinds account for this "
                       "many units exactly — arithmetic, not an estimate."),
    CAUSE_VINTAGE: ("our stored slice of the feed is as of a date BEFORE the period ended, so the two "
                    "sides did not see the same days and the difference is NOT DECIDABLE. It is not "
                    "attributed to staleness and it is not called a finding: it waits on a fresh pull."),
    CAUSE_GRAIN: ("the feed row and the platform figure are not over the same population (the feed is per "
                  "rep per DOOR; the platform counts a rep across every store they rang a sale at)."),
    CAUSE_UNATTRIBUTED: ("the feed slice is COMPLETE and no counting definition explains this "
                         "difference. THIS is the row to look at."),
}

# ── WHICH METRICS ARE COMPARED — one declaration. `feed` names the column(s) on the landed feed row in
#    fallback order; `platform` names the key the caller supplies from the ONE activation count (None =
#    the platform has NO transaction-derived counterpart, which is itself a finding worth printing:
#    it says out loud which carrier numbers this platform cannot check at all).
#    `kind`: 'count' (an integer of units) or 'rate' (a percentage — never differenced as if it were a
#    count, and never attributed to an exclusion, because a ratio's gap is not a unit gap).
METRIC_REP = (
    # key,            label,                  feed columns,                        platform key,        kind
    ("activations",   "New activations",      ("gross_adds",),                     "new_activations",   "count"),
    ("upgrades",      "Upgrades",             ("upgrades",),                       "upgrades",          "count"),
    ("ready_bounty",  "Ready App bounty",     ("boost_ready_bounty",),             None,                "count"),
    ("tablets",       "Tablet activations",   ("tablet_ga",),                      "tablets",           "count"),
    ("ga_prepaid",    "Prepaid activations",  ("ga_prepaid",),                     None,                "count"),
    ("ga_postpaid",   "Postpaid activations", ("ga_postpaid",),                    None,                "count"),
    ("boostapp",      "Carrier App rate",     ("boost_app_pct",),                  "boostapp_rate",     "rate"),
    ("atu",           "ATU rate",             ("atu_pct",),                        None,                "rate"),
    ("protect",       "Protect rate",         ("device_insurance_pct", "protect_pct"), None,             "rate"),
    ("byod_rate",     "BYOD rate",            ("byod_pct",),                       "byod_rate",         "rate"),
)
METRIC_STORE = (
    ("activations",   "New activations",      ("gross_adds",),                     "new_activations",   "count"),
    ("upgrades",      "Upgrades",             ("total_upgrades",),                 "upgrades",          "count"),
    ("total_acts",    "Total activity",       ("total_acts",),                     "total_acts",        "count"),
    ("byod_rate",     "BYOD rate",            ("byod_pct",),                       "byod_rate",         "rate"),
    ("tmr3",          "3MR rate",             ("tmr3",),                           None,                "rate"),
    ("familyplan",    "Family Plan rate",     ("family_plan_pct",),                None,                "rate"),
    ("aal",           "AAL conversion",       ("aal_conversion",),                 None,                "rate"),
)
GRAIN_REP, GRAIN_STORE = "rep", "store"
METRICS = {GRAIN_REP: METRIC_REP, GRAIN_STORE: METRIC_STORE}

RATE_TOLERANCE = 0.05     # a rate agrees within this many percentage points (float/rounding noise only)


def _num(v):
    """float or None — '', None, non-numeric → None. THE same reading rule as `kpi_failing._num`:
    no data is never zero."""
    return _kf._num(v)


def side(value):
    """A raw value → (number|None, state). `None`/blank → `not_reported` (the column is not carrying
    anything); a real 0 → `measured_zero`; anything else → `reported`. PURE."""
    n = _num(value)
    if n is None:
        return None, STATE_NOT_REPORTED
    return n, (STATE_MEASURED_ZERO if n == 0 else STATE_REPORTED)


def feed_value(row, columns):
    """The first of `columns` the landed feed row actually carries → (number|None, state, column|None).
    The fallback ORDER is the one `kpi_failing` already declares, so this module holds no second map."""
    for col in columns or ():
        n, st = side((row or {}).get(col))
        if st != STATE_NOT_REPORTED:
            return n, st, col
    return None, STATE_NOT_REPORTED, ((columns or (None,))[0])


def attribute(delta, *, kind, excluded_by_kind=None, vintage=None, grain_note=None):
    """WHY the two sides differ → (causes, residual, decidable). PURE, and deliberately not exclusive: a
    row may be part definition and part something else, and collapsing that to one label hides half.

    `delta`            = platform − feed (already computed; None when either side is not_reported).
    `excluded_by_kind` = {exclusion kind: units} for THIS metric — the EXACT arithmetic.
    `vintage`          = a `dlar_sweep.vintage(...)` dict for the feed's own grain.

    ORDER, and the one judgement call in this module:
      1. DEFINITION first, because it is exact arithmetic over named units.
      2. Whatever is left is then tested for DECIDABILITY. **An incomplete feed slice does not EXPLAIN a
         residual — it makes the comparison undecidable**, and this module says so rather than attributing
         the gap to staleness. Measured why: the reference rep's stored August slice says 4 activations
         and the carrier's own finalised August says 13, so the slice did not grow in proportion to the
         days it was missing (24 of 31 days, but 4 of 13 activations). Any rule that scaled a partial
         snapshot up by its day coverage would have called that residual unexplained and sent somebody
         hunting a counting bug that is not there; any rule that let staleness swallow the whole residual
         would hide a real counting bug behind a stale feed. Neither is honest. A short slice is reported
         as `feed_vintage` with `decidable: False` and the row is kept OUT of the unattributed headline
         until the feed is re-pulled — which is the owner's action on live data, not ours.
      3. Only when the slice DOES reach the period's last day is a surviving residual `unattributed`.
         That is the finding this report exists to produce."""
    if delta is None:
        return [], None, False
    causes, residual = [], float(delta)
    if kind == "count":
        # the platform counts MORE than the feed by units the carrier's own basis drops. Each named kind
        # accounts for that many units exactly; a kind can never explain more than the gap itself.
        for k, n in sorted((excluded_by_kind or {}).items()):
            try:
                n = int(n or 0)
            except (TypeError, ValueError):
                continue
            if n <= 0 or residual <= 0:
                continue
            take = min(float(n), residual)
            residual -= take
            causes.append({"cause": CAUSE_DEFINITION, "kind": k, "units": take, "exact": True,
                           "decidable": True,
                           "detail": ("%g unit(s) the carrier's own basis excludes as '%s' and this "
                                      "platform still counts" % (take, k))})
    tol = RATE_TOLERANCE if kind == "rate" else 0
    if abs(residual) <= tol:
        return causes, round(residual, 2), True
    v = vintage or {}
    decidable = True
    if v.get("complete") is False:
        causes.append({"cause": CAUSE_VINTAGE, "exact": False, "decidable": False,
                       "as_of": v.get("as_of"), "days_short": v.get("days_short"),
                       "basis": v.get("basis"), "residual": round(residual, 2),
                       "detail": ("our stored slice of the feed is as of %s — %s day(s) short of the "
                                  "period. The remaining difference of %g cannot be judged against a "
                                  "partial snapshot: re-pull the report and compare again."
                                  % (v.get("as_of"), v.get("days_short"), residual))})
        decidable = False
    elif grain_note:
        causes.append({"cause": CAUSE_GRAIN, "exact": False, "decidable": False,
                       "residual": round(residual, 2), "detail": grain_note})
        decidable = False
    else:
        causes.append({"cause": CAUSE_UNATTRIBUTED, "exact": False, "decidable": True,
                       "detail": CAUSE_NOTES[CAUSE_UNATTRIBUTED], "residual": round(residual, 2)})
    return causes, round(residual, 2), decidable


def compare(feed_row, platform, *, grain=GRAIN_REP, metrics=None, excluded_units=None,
            vintage=None, grain_note=None):
    """ONE entity (a rep row or a store row) → the per-metric difference rows. PURE, never raises.

    `platform` = {platform key: value} computed by the caller through the ONE activation count. A key
    absent from it is `not_reported` on the platform side — never zero.
    `excluded_units` = {metric key: {exclusion kind: units}} — PER METRIC, because a swap leaving the
    activation count says nothing about the upgrade count, and one entity-wide map would attribute the
    same units to every row on the page.

    Each row: {metric, label, kind, feed, feed_state, feed_column, platform, platform_state, delta,
               agree, causes, residual, attributed, decidable}"""
    out = []
    for (key, label, cols, pkey, kind) in (metrics if metrics is not None else METRICS.get(grain, ())):
        fv, fst, fcol = feed_value(feed_row, cols)
        pv, pst = (None, STATE_NOT_REPORTED) if not pkey else side((platform or {}).get(pkey))
        delta = None
        if fst != STATE_NOT_REPORTED and pst != STATE_NOT_REPORTED:
            delta = round(float(pv) - float(fv), 4)
        tol = RATE_TOLERANCE if kind == "rate" else 0
        agree = (delta is not None and abs(delta) <= tol)
        causes, residual, decidable = ([], None, True) if agree else attribute(
            delta, kind=kind,
            excluded_by_kind=((excluded_units or {}).get(key) if kind == "count" else None),
            vintage=vintage, grain_note=grain_note)
        out.append({
            "metric": key, "label": label, "kind": kind,
            "feed": fv, "feed_state": fst, "feed_column": fcol,
            "platform": pv, "platform_state": pst,
            "delta": delta, "agree": agree,
            "causes": causes, "residual": residual,
            # DECIDABLE = the two sides were comparable on equal footing. An undecidable row is neither
            # "explained" nor "a finding": it is waiting on a fresh pull, and it never inflates either count.
            "decidable": bool(delta is not None and decidable),
            "attributed": bool(delta is not None and decidable
                               and not any(c["cause"] == CAUSE_UNATTRIBUTED for c in causes)),
            "comparable": bool(delta is not None),
        })
    return out


def entity_row(*, key, label, store=None, feed_row=None, platform=None, grain=GRAIN_REP,
               excluded_units=None, vintage=None, grain_note=None, metrics=None):
    """One dashboard row: the entity, its per-metric comparison, and the counts a table sorts on."""
    mets = compare(feed_row, platform, grain=grain, metrics=metrics,
                   excluded_units=excluded_units, vintage=vintage, grain_note=grain_note)
    diff = [m for m in mets if m["comparable"] and not m["agree"]]
    return {
        "key": key, "label": label, "store": store or "", "grain": grain,
        "metrics": mets,
        "compared": sum(1 for m in mets if m["comparable"]),
        "agreeing": sum(1 for m in mets if m["agree"]),
        "differing": len(diff),
        "unattributed": sum(1 for m in diff if m["decidable"] and not m["attributed"]),
        "undecidable": sum(1 for m in diff if not m["decidable"]),
        "not_reported_feed": sum(1 for m in mets if m["feed_state"] == STATE_NOT_REPORTED),
        "not_reported_platform": sum(1 for m in mets if m["platform_state"] == STATE_NOT_REPORTED),
        "feed_row_present": feed_row is not None,
        "vintage": vintage or None,
    }


def summarize(rows, *, period=None, vintage=None, stopped_columns=None, feed_rows_per_entity=None):
    """The dashboard headline over `entity_row` results. `stopped_columns` = columns the feed sent for
    NOBODY over the whole slice (the §19.28 content-arrival axis) — a definition describing nobody's
    data, reported rather than printed as a quiet 0. PURE."""
    rows = list(rows or [])
    by_metric = {}
    for r in rows:
        for m in r["metrics"]:
            b = by_metric.setdefault(m["metric"], {
                "metric": m["metric"], "label": m["label"], "kind": m["kind"],
                "compared": 0, "agreeing": 0, "differing": 0, "unattributed": 0, "undecidable": 0,
                "feed_not_reported": 0, "platform_not_reported": 0, "net_delta": 0.0})
            if m["feed_state"] == STATE_NOT_REPORTED:
                b["feed_not_reported"] += 1
            if m["platform_state"] == STATE_NOT_REPORTED:
                b["platform_not_reported"] += 1
            if not m["comparable"]:
                continue
            b["compared"] += 1
            if m["agree"]:
                b["agreeing"] += 1
            else:
                b["differing"] += 1
                b["net_delta"] += m["delta"] or 0.0
                if not m["decidable"]:
                    b["undecidable"] += 1
                elif not m["attributed"]:
                    b["unattributed"] += 1
    metrics = sorted(by_metric.values(),
                     key=lambda b: (-b["unattributed"], -b["undecidable"], -b["differing"], b["metric"]))
    for b in metrics:
        b["net_delta"] = round(b["net_delta"], 2)
    dup = {k: n for k, n in (feed_rows_per_entity or {}).items() if (n or 0) > 1}
    return {
        "period": period,
        "entities": len(rows),
        "entities_differing": sum(1 for r in rows if r["differing"]),
        "entities_unattributed": sum(1 for r in rows if r["unattributed"]),
        "entities_undecidable": sum(1 for r in rows if r["undecidable"]),
        "cells_compared": sum(r["compared"] for r in rows),
        "cells_differing": sum(r["differing"] for r in rows),
        "cells_unattributed": sum(r["unattributed"] for r in rows),
        "cells_undecidable": sum(r["undecidable"] for r in rows),
        "by_metric": metrics,
        "vintage": vintage or None,
        # a column the feed stopped sending for EVERYBODY is one fact about the feed, not N findings
        "stopped_columns": sorted(stopped_columns or ()),
        # the feed's own grain is per rep per DOOR: a rep with several rows cannot be compared against a
        # single platform figure without saying so. Reported, never silently collapsed to one row.
        "entities_with_several_feed_rows": dup,
        "state_notes": dict(STATE_NOTES),
        "cause_notes": dict(CAUSE_NOTES),
        "source_of_truth": ("the transaction done in the store (owner 2026-09-27). The feed is a claim "
                            "about it; this report attributes every disagreement and never nets them."),
        "books_to": [],          # READ-ONLY: this report books nothing, anywhere.
    }
