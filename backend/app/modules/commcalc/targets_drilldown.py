"""WHERE DID THIS TARGET-VS-ACTUAL FIGURE COME FROM — the declaration for the five TARGETS reports
(§CORE-17, batch 13).

  /commcalc/targets               Daily Targets — the store summary, the per-category tiles and the
                                  day-by-day calendar for one store or one rep.
  /commcalc/targets/my            My Targets — a rep's own tiles, plus the DM's AREA roll-up.
  /commcalc/targets/accessories   Accessory Sales Targets — five tiles over the same store rows.
  /commcalc/targets/action-plan   Daily Action Plan — the team total and commission at risk.
  /commcalc/targets/report-cards  Manager Report Cards — what was assigned to a DM, checked off.

HOW MANY GRAINS THIS FAMILY REALLY HAS — MEASURED, NOT ASSUMED (§CORE-17.3l)
───────────────────────────────────────────────────────────────────────────
The batch was handed over as "expect fewer grains than routes — but prove it either way". Reading
the four producers (`targets_engine.compute_scope`, `aggregate_stores`, `GET /targets/{period}/
action-plan` and `manager_report_card.build`) gives **six**, because a grain fixes the ROW, the UNIT
and the PROVENANCE, and sharing needs all three to agree:

  1. a SCOPE-DAY's ACHIEVEMENT      one calendar day of one store, or of one rep inside it; counts or
                                    dollars; read out of the sales feeds (a DECLARED category out of
                                    the store's own daily closing instead)
  2. a SCOPE-DAY's SCHEDULED HOURS  the same row, in HOURS, out of the schedule — and a day the
                                    schedule does not reach yet has NO source at all, because its
                                    hours were estimated from the scope's weekly pattern (§CORE-17.3i)
  3. a STORE-MONTH's ACHIEVEMENT    one store row of the period summary; counts or dollars; the feeds
  4. a STORE-MONTH's DECLARED TARGET the same row, same unit, and a completely different provenance:
                                    a TARGET IS A DECLARED NUMBER, not a measurement, so its origin
                                    is the config row somebody typed it into
  5. a STORE-MONTH's GAP            the same row again, and a row that names BOTH of the above,
                                    because `need` is target − achieved PER STORE (§CORE-17.3k)
  6. a REP's COMMISSION AT RISK     one stored commission row per rep; money; read out of the
                                    calculated-commissions snapshot (§CORE-17.3j)

3, 4 and 5 are keyed by the SAME store row in the SAME unit and are still three grains: one is a
measurement of what was sold, one is a number a manager typed into a settings screen, and the third
names both and can therefore disagree with either. A single catalogue over them would have to claim
the sales feed produced a target, which is exactly the lie the tie-out would then assert for us.

ONE MODULE, AND WHY THAT IS NOT THE SIBLING DEFECT
──────────────────────────────────────────────────
What the five reports share is not a row shape but the thing a drill-down exists for — *which stored
relation was this target-vs-actual figure read out of* — and there are exactly FOUR answers in the
family: the sales feeds, the store's own daily closing (for a declared category), the target config
rows, and the schedule. Five files would be five answers to one question. The per-report difference
is a CATALOGUE, and every figure in every catalogue is the producer's own, dereferenced.

WHAT GETS NO PANEL HERE, AND WHICH RULING SAYS SO
─────────────────────────────────────────────────
  · **EVERY ATTAINMENT PERCENTAGE.** `% to goal`, the area card's `attainment_pct`, the report card's
    `score_pct` and the conversion rate. No set of records sums to a quotient (§CORE-17.3h). Both
    halves of each are offered instead — and the conversion rate already prints its own numerator
    and denominator ("12 boxes / 40 bill-pays") beside it on every screen that shows it.
  · **`need`, `today_target` and `pace` FOR ONE SCOPE.** `need` is `monthly − achieved_mtd` with both
    sides being that scope's own totals — subtracted at the TOTAL, so it does not drill
    (§CORE-17.3k). `today_target` is `base_today + the cumulative shortfall through yesterday` and
    `pace` is `need ÷ open days left`: figures composed from other figures, and a quotient
    (§CORE-17.3h as batch 10 extended it). The AREA versions of `need` DO drill, because there the
    subtraction happens per store and the rows are the stores (same test, opposite answer).
  · **A MONTHLY TARGET FOR ONE SCOPE.** It is a leaf: one column of one stored config row (or, for
    BYOD, that column times a percentage on the same row). Batch 10's leaf ruling. The AREA target
    drills, because it is Σ over the stores.
  · **A COUNT WHOSE LIST IS ON SCREEN.** The action plan's critical / warning / store counts, the
    report card's met / missed / no-target tallies and its store count: each is the cardinality of a
    table printed immediately below it (batch 11 precedent).
  · **A PER-STORE ROW ON A LIST REPORT.** The store summary's own cells and a report card's per-store
    check-off are rows, not rollups — and each one's View → already opens that store's own calendar,
    where the day-level panels of grains 1 and 2 are served. A second answer beside an existing one
    is the defect, not the fix. It also keeps a 29-store list payload from carrying 145 day-level
    panels nobody asked for.

ABSENCE IS NEVER ZERO, AND AN UNSET TARGET IS NEVER A ZERO TARGET
─────────────────────────────────────────────────────────────────
A day with no sale rows, a day that has not happened yet, a store nobody set a target for and a store
whose gap therefore cannot be measured are each SHOWN and NOT counted, with the reason in the
reader's words. The figures do not move by a cent — the producers add nothing for those rows either —
and only the row changes, which is the whole argument for putting it there.

LIVE DEFECTS THESE REPORTS HAVE TODAY — REPORTED, NOT FIXED, AND NO FIGURE MOVED
───────────────────────────────────────────────────────────────────────────────
1. **A rep whose commission subtotal was never computed is folded into "commission at risk" as
   $0.00.** `get_action_plan.rep_commission` returns `at_risk: 0.0` when `payout_slice_evidence`
   says the subtotal is not known (§COMMCALC-09 — a slice nobody computed is not $0.00 earned), so
   "nothing at risk" and "we never worked out what was at risk" print identically. The panel shows
   such a rep WITHOUT a figure and does not count them; a null adds zero to a sum either way, so the
   tile is identical to the cent.
2. **For a self-scoped rep the "commission at risk" tile reads $0 by design, and says so nowhere.**
   The endpoint deliberately zeroes the STORE roll-up for a rep (a store-wide figure is a cross-rep
   figure), but the tile still renders `$0` — which reads as "nothing is at risk" rather than "this
   total is not rolled up for you", even while the rep's own at-risk item sits below it. NOT fixed:
   what that tile should say is a decision about a figure the owner reads. The honest half is here —
   a deliberately suppressed total has no rows behind it, so NO panel is served for it at all.
3. **The Manager Report Cards page prints a table NAME into its column tooltips.**
   `manager_report_card.ITEMS[*]["source"]` is prose ending "commcalc.targets vs the period's
   actuals", and the page puts it in the `title` of every item column — a database name on a
   manager's screen, which §19.38 exists to prevent. NOT fixed here: that string is the module's own
   declared vocabulary and rewording it is a copy change across three surfaces, while this batch's
   job is the evidence. The panels this batch serves name the same relations through `sourceLabel()`,
   which is what the operator should have been reading all along.
4. **An unparseable `trans_date` on a sale row counts toward a scope's conversion and toward
   NOTHING else.** `scope_conversion` keeps a row whose date will not parse (`if d and d > today`),
   while `scope_actuals_by_day` drops it, so such a row moves the conversion rate and no per-day
   figure. Reported, not fixed: making the two agree moves a figure. It is also why the conversion
   numerator and denominator are not folded per day here.

AN EMPTY REPORT DRILLS TO NOTHING. `tiles()` skips a figure whose total key the report did not
produce; a report that produced its totals over NO ROWS AT ALL would still get a panel asserting a
figure behind an empty list, which ties only by accident and reads like a bug. Every entry point
below returns an empty mapping in that case.

💰 MOVES NO MONEY. Read-only presentation of figures the producers already computed. Books nothing,
pays nothing, writes nothing, and computes no business figure of its own: every amount it carries was
handed to it, or was folded by `targets_engine.aggregate_stores` — the one home the area roll-up and
every report card already use. PURE: no I/O, no framework import, no clock, no org / carrier / tenant
/ product / store name (RULE TWO).
Registered in docs/SYSTEM_DATA_FLOW_INDEX.md §CORE-17.3m.
"""
from app.modules.commcalc import carrier_pay_drilldown as _cpd
from app.modules.commcalc import data_lineage_registry as _lineage
from app.modules.commcalc import targets_engine as _te
from app.modules.core import report_drilldown

# ── THE FOUR PROVENANCE ANSWERS THIS FAMILY HAS ──────────────────────────────────────────────────
#: What a BUILT-IN category's achievement is read out of: the registry's own display union, which is
#: the pair `_fetch_actuals` walks for every period. Dereferenced, never spelled here.
SALES_FEEDS = tuple(_lineage.SALES_DISPLAY_SOURCES)

#: What a DECLARED category's achievement is read out of instead. `closing/router._declared_by_store_day`
#: — the ONE reader of a declaration — reads BOTH of these for every range it answers, unconditionally,
#: which is the §CORE-17.3j test for naming a table at all. `daily_closing` comes from the registry;
#: its post-mig-1072 sibling is not an external feed, so it is named here (see TARGETS below).
CLOSING_METRIC_LINE = "closing_metric_line"
DECLARED_SOURCES = (_lineage.DAILY_CLOSING, CLOSING_METRIC_LINE)

#: Where a TARGET physically lives. These are config rows a person typed into a settings screen, not
#: feeds, so they are NOT in the lineage registry — its documented scope is external-feed ingest
#: tables, and adding them there would put a config row in the feed watchdog's population. Named here
#: once, the same posture `carrier_pay_drilldown.REP_COMMISSIONS` takes for the commission snapshot.
TARGETS = "targets"                 # commcalc.targets (mig 006) — a built-in's monthly column
TARGET_LINE = "target_line"         # commcalc.target_line (mig 1074) — a declared category's row
STORE_ROSTER = "stores"             # storeops.stores — the accessory target a store row seeds

#: The schedule the whole weighting is spread over. DEREFERENCED from the lineage registry
#: (`SCHEDULE`), the one home for "which relation records a store's scheduled day" — this file and
#: `account/liabilities_drilldown` each held a literal copy of it until batch 18 re-keyed both.
SHIFTS = _lineage.SCHEDULE

#: The calculated-commission snapshot the action plan's at-risk figure is read out of. DEREFERENCED
#: from the commission batch's own declaration (§CORE-17.3k) rather than spelled a second time —
#: naming a fact is half the fix and the other half is not making a second copy of it.
REP_COMMISSIONS = _cpd.REP_COMMISSIONS

#: Which schema each relation lives in. `tile_panel` takes ONE schema per tile, so a tile whose rows
#: can name relations in two schemas (the target tiles: a store with no target row of its own seeds
#: its accessory target from the roster instead) names the tables and NO schema, rather than
#: asserting one of the two for both.
SCHEMA_COMMCALC = "commcalc"
SCHEMA_STOREOPS = "storeops"

#: The columns a figure on these reports is read out of, where a real column holds it. A built-in's
#: target column is DEREFERENCED from the engine's own map; BYOD has no column of its own (it is
#: activations × `byod_pct`) and a declared category's target is a `target_line` value.
COL_BYOD_PCT = "byod_pct"
COL_TARGET_VALUE = "target_value"
COL_MONTHLY_TARGET = "monthly_target"
COL_SCHEDULED_HOURS = "scheduled_hours"

#: Why a drill row on these reports carries no number. Both are entries in the ONE home's vocabulary
#: (`report_drilldown.UNMEASURED_REASONS`); they are named here so a catalogue cannot emit a reason
#: the reader would see as a bare field name, and the harness asserts both resolve to a sentence.
REASON_NOT_RECORDED = "not_recorded"
REASON_FUTURE_DAY = "day_not_yet_reached"
REASON_NO_TARGET = "target_not_set"
REASON_NO_DM = "no_dm_in_org_tree"
REASON_NOT_COMPUTED = "not_computed"


# ── NOTES — what each panel is, in the words the screen already uses ─────────────────────────────

_ACHIEVED_NOTE = (
    "One row per day. The figure counts every day with sales up to today — including days this scope "
    "was not scheduled, which is why the panel can hold days the calendar below does not. A day that "
    "recorded nothing, and a day that has not happened yet, are shown WITHOUT a figure and are not "
    "counted: a day nobody sold on is not a day worth zero.")

_HOURS_NOTE = (
    "One row per open day, out of the schedule. The figure counts only the days a shift is actually "
    "entered for; a day filled in from this scope's weekly pattern is shown for context, is NOT "
    "counted, and names no source at all — nobody has scheduled it yet, so there is nothing to trace.")

_AREA_ACHIEVED_NOTE = (
    "One row per store in your area, as the store table below lists them. The figure is the straight "
    "sum of those rows — nothing is re-weighted — so a store that sold nothing contributes nothing.")

_AREA_TARGET_NOTE = (
    "One row per store, and each figure is the number somebody TYPED into Target Settings for that "
    "store, not a measurement. A store nobody set this target for is shown WITHOUT a figure and is "
    "not counted — an unset target is not a target of zero.")

_AREA_NEED_NOTE = (
    "One row per store: that store's own target minus what it has achieved, floored at zero. The "
    "figure is the sum of those per-store gaps, which is why it drills at all — a gap computed from "
    "two area totals would have no rows behind it. A store with no target set has no measurable gap "
    "and is shown without one.")

_AREA_TRENDING_NOTE = (
    "One row per store. Each figure is a PROJECTION, not a measurement: the month to date scaled up "
    "to the whole month by the same computation Executive MTD uses, over the same sales rows. A store "
    "the sales feed holds nothing for projects nothing.")

_TEAM_NOTE = (
    "One row per store in this plan. The plan only covers stores that have a monthly target set and "
    "are inside your span, so a store missing from these rows is missing for a reason that has "
    "nothing to do with its selling — the page says how many above.")

_AT_RISK_NOTE = (
    "One row per rep, as the commission calculation stored it: the payout forfeited below the full "
    "KPI tier. A rep whose subtotal the calculation never worked out is shown WITHOUT a figure and "
    "is not counted — a payout nobody computed is not a payout of $0.00 at risk.")

_CARD_ACHIEVED_NOTE = (
    "One row per store on this card. The figure is the straight sum of the same per-store numbers the "
    "check-off below is built from, so the card and its own evidence are one fold.")

_CARD_TARGET_NOTE = (
    "One row per store on this card: what was ASSIGNED to it, as typed into Target Settings. A store "
    "with no target for this item is the check-off's `no target` state — shown here without a figure "
    "and counted in nothing, because nobody set it rather than the store having failed it.")

_NOBODY_NOTE = (
    "One row per store the org tree cannot place under a district manager. These stores have targets "
    "and results, and they appear on nobody's card — assigning them in the org chart is what puts "
    "them on one. The figure is how much of the month's work that is.")

_BY_DM_NOTE = (
    "One row per district manager beneath you, each carrying their own roll-up. A store the org tree "
    "places under no district manager cannot be attributed to any of them: those stores are listed "
    "here WITHOUT a figure and are counted in nothing, because the number that is missing is not "
    "zero — it is unattributable until the org chart says who owns the store.")


# ══════════════════════════════════════════════════════════════════════════════════════════════════
# §0  THE AXIS, AND WHAT EACH CATEGORY'S FIGURES ARE MADE OF.
# ══════════════════════════════════════════════════════════════════════════════════════════════════

def unit_of(axis_unit):
    """The engine's unit vocabulary ('dollars' / 'count') in the drill-down's ('money' / 'count').

    Deliberately the SAME two words `manager_report_card.items_for` already translates to, which are
    `report_drilldown.UNIT_MONEY` / `UNIT_COUNT` themselves — so this is a dereference of the shared
    vocabulary and not a third spelling of it (asserted by the harness). PURE."""
    return (report_drilldown.UNIT_MONEY if str(axis_unit or "") == "dollars"
            else report_drilldown.UNIT_COUNT)


def achieved_sources(cat_source):
    """Which relations a category's ACHIEVED figure was read out of, off the axis's own `source`.

    `targets_engine.source_of` is the one home for that fact — the four built-ins are answered by the
    sales feeds, a declared category by the store's own daily closing — so this reads it rather than
    deciding again. An axis entry whose source is neither names NOTHING, which is reported as an
    origin that cannot be traced rather than guessed at (§CORE-17.3i). PURE."""
    if cat_source == _te.SOURCE_SALES_FEED:
        return SALES_FEEDS
    if cat_source == _te.SOURCE_DECLARED:
        return DECLARED_SOURCES
    return ()


def target_origin(cat):
    """(tables, column, schema) for a category's DECLARED TARGET — where the number was typed in.

    `targets_engine.target_store` is the one home for "which physical store holds this category's
    target", so the three answers it gives are the three answers here: a built-in's own column on the
    targets row, BYOD's derivation from that row's activations column and percentage, and a declared
    category's `target_line` row. PURE."""
    store = _te.target_store(cat)
    if store == "column":
        return (TARGETS,), _te.BUILTIN_TARGET_COLUMN[cat], SCHEMA_COMMCALC
    if store == "derived":
        return (TARGETS,), COL_BYOD_PCT, SCHEMA_COMMCALC
    return (TARGET_LINE,), COL_TARGET_VALUE, SCHEMA_COMMCALC


def axis_of(catalog=None):
    """This tenant's target axis, asked of its one home. PURE."""
    return list(_te.target_axis(catalog) or ())


# ══════════════════════════════════════════════════════════════════════════════════════════════════
# §1  GRAIN 1 + 2 — A SCOPE-DAY. `GET /targets/{period}/calendar`, for one store or one rep.
# ══════════════════════════════════════════════════════════════════════════════════════════════════

def day_rows(result):
    """THE EVIDENCE BEHIND ONE SCOPE'S MTD FIGURES, as one row per day.

    TWO POPULATIONS, UNIONED ON PURPOSE. `compute_scope`'s `calendar` is the scope's OPEN days — the
    days it is scheduled — while `achieved_mtd` counts every day with sales up to today, *including
    days the scope was not scheduled* (a walk-in, a coverage shift nobody entered). A panel folded
    over the calendar alone would therefore be short of the figure it explains, and the tie-out would
    have said so; so the rows are the union, and each row says which side it came from.

    `achieved_days` is the per-day evidence `compute_scope` records in the SAME loop that sums
    `achieved_mtd`, at full precision — the `base_raw` precedent, for the same reason: a panel that
    added up the DISPLAYED per-day figures would drift from the month it explains.

    Returns rows ordered by date: {date, scheduled, has_actual, is_future, cats:{cat: value}}. PURE."""
    result = result or {}
    today = str(result.get("today") or "")
    ev = {str((d or {}).get("date") or ""): (d or {}) for d in (result.get("achieved_days") or [])}
    cal = {str((d or {}).get("date") or ""): (d or {}) for d in (result.get("calendar") or [])}
    out = []
    for key in sorted(set(ev) | set(cal)):
        if not key:
            continue
        e, c = ev.get(key) or {}, cal.get(key) or {}
        out.append({
            "date": key,
            # A day with sales but no shift is NOT on the calendar, and that is worth seeing.
            "scheduled": key in cal,
            "has_actual": key in ev,
            "is_future": bool(today and key > today),
            "hours": c.get("hours_raw", c.get("hours")),
            "projected": bool(c.get("projected")),
            "cats": dict(e.get("cats") or {}),
        })
    return out


def _day_label(r):
    r = r or {}
    bits = [str(r.get("date") or "")]
    if not r.get("scheduled"):
        bits.append("not scheduled")
    elif r.get("projected"):
        bits.append("estimated from the weekly pattern")
    return " · ".join(b for b in bits if b)


def _day_fields(r):
    r = r or {}
    return {"hours": r.get("hours"),
            "day": "future" if r.get("is_future") else ("open" if r.get("scheduled") else "off-schedule")}


def _achieved_reader(cat):
    return lambda r: (dict((r or {}).get("cats") or {})).get(cat)


def _achieved_tables(sources):
    """A day that RECORDED something names the feeds it was read out of. A day that recorded
    nothing — and a day that has not happened yet — names NOTHING: there is no row in any feed to
    trace, so the panel reports a stated, countable untraceable origin rather than claiming a source
    for an absence (§CORE-17.3i, the batch-9 ruling). PURE."""
    def read(r):
        return tuple(sources) if (r or {}).get("has_actual") else ()
    return read


def _day_reason(r):
    """Why a day carries no achievement. A day nobody sold on and a day that has not happened yet are
    different facts and a reader needs both; neither is a zero. PURE."""
    r = r or {}
    if r.get("has_actual"):
        return None
    return REASON_FUTURE_DAY if r.get("is_future") else REASON_NOT_RECORDED


def _hours_value(r):
    v = (r or {}).get("hours")
    return None if v is None else v


def _hours_in_figure(r):
    """`scheduled_hours_total` is the CONCRETE schedule only — the projected days are in
    `effective_hours_total` beside it. So a projected day is shown and not counted. PURE."""
    r = r or {}
    return bool(r.get("scheduled")) and not r.get("projected") and r.get("hours") is not None


def _hours_tables(r):
    """A day with a shift entered names the schedule. A day PROJECTED from the weekly pattern, and a
    day with sales but no shift at all, name NOTHING: no shift row exists for either, so there is
    nothing to trace, and that is the answer rather than a guessed one (§CORE-17.3i). PURE."""
    r = r or {}
    return () if (r.get("projected") or not r.get("scheduled")) else (SHIFTS,)


def _hours_reason(r):
    """Why a day carries no hours: nobody has scheduled this scope on it. A day the pattern filled
    in DOES carry a figure (it is simply not counted), so it needs no reason. PURE."""
    r = r or {}
    return None if r.get("hours") is not None else REASON_NOT_RECORDED


def _day_keys(date_column, store_code, rep):
    """How a reader FINDS one of these days again in the relation it came out of: the date column
    that relation spells, the store, and — for a rep scope — the person. The date column differs
    between the two provenance families (a sale carries `trans_date`, a declaration `close_date`), so
    it is a parameter rather than one name asserted for both. PURE."""
    def read(r):
        return {date_column: (r or {}).get("date"), "store_code": store_code, "rep_name": rep}
    return read


#: Which column each provenance family addresses a day by.
COL_TRANS_DATE = "trans_date"
COL_CLOSE_DATE = "close_date"
COL_SHIFT_DATE = "shift_date"


def scope_day_catalogue(axis, store_code=None, rep=None):
    """The per-category ACHIEVED panels for one scope, plus its scheduled hours. ONE row shape, two
    units, two provenance families — which is why it is one catalogue with a tile per category and
    not one catalogue per category.

    `total` is read off the scope result's own `categories[cat]` block, so a category the scope did
    not produce has no panel instead of a panel asserting a figure."""
    out = []
    for a in (axis or ()):
        cat = a.get("key")
        if not cat:
            continue
        srcs = achieved_sources(a.get("source"))
        _datecol = (COL_CLOSE_DATE if a.get("source") == _te.SOURCE_DECLARED else COL_TRANS_DATE)
        out.append({
            "key": "achieved_%s" % cat, "cat": cat,
            "label": "%s achieved so far" % (a.get("label") or cat),
            "total": "achieved_mtd",
            "value": _achieved_reader(cat), "row_label": _day_label,
            "keys": _day_keys(_datecol, store_code, rep),
            "fields": _day_fields, "reason": _day_reason, "tables": _achieved_tables(srcs),
            "schema": SCHEMA_COMMCALC if srcs else None,
            "unit": unit_of(a.get("unit")), "note": _ACHIEVED_NOTE,
        })
    out.append({
        "key": "scheduled_hours", "cat": None, "label": "Hours scheduled this month",
        "total": "scheduled_hours_total",
        "value": _hours_value, "row_label": _day_label,
        "keys": _day_keys(COL_SHIFT_DATE, store_code, rep),
        "fields": _day_fields, "reason": _hours_reason,
        "tables": _hours_tables, "column": COL_SCHEDULED_HOURS,
        "schema": SCHEMA_STOREOPS, "in_figure": _hours_in_figure,
        "unit": report_drilldown.UNIT_HOURS, "note": _HOURS_NOTE,
    })
    return tuple(out)


def scope_drilldowns(result, catalog=None, *, cap=None):
    """PURE: one panel per category achieved, plus the scheduled hours, for ONE scope.

    The subject is the scope the endpoint was asked about — a store, or a rep inside it — so a panel
    can never hold a different set of days than the tile it opened from."""
    result = result or {}
    rows = day_rows(result)
    if not rows:
        return {}
    cats = result.get("categories") or {}
    subject = result.get("rep") or result.get("store_code")
    kind = "rep" if result.get("rep") else "store_code"
    out = {}
    for spec in scope_day_catalogue(axis_of(catalog), result.get("store_code"),
                                    result.get("rep")):
        spec = dict(spec)
        key, label, cat = spec.pop("key"), spec.pop("label"), spec.pop("cat")
        total_key = spec.pop("total")
        amount = ((cats.get(cat) or {}).get(total_key) if cat else result.get(total_key))
        if amount is None:
            continue
        out[key] = report_drilldown.tile_panel(
            label, amount, rows, subject=subject, subject_kind=kind,
            cap=cap or report_drilldown.ROW_CAP, **spec)
    return out


# ══════════════════════════════════════════════════════════════════════════════════════════════════
# §2  GRAINS 3–5 — A STORE-MONTH. The area roll-up, the accessory tiles, the team total, a card.
# ══════════════════════════════════════════════════════════════════════════════════════════════════
#
# ONE ROW SHAPE, THREE GRAINS, TWO ROW DICTS. The summary endpoint shapes a store row as
# `categories[cat] = {monthly, achieved_mtd, need, …}`; the action plan shapes the same store-month as
# a `metrics` LIST of the same numbers. Two readers, one catalogue — because what differs is the
# dict a producer happened to build, not the row a figure is Σ over.

def _store_label(r):
    r = r or {}
    return str(r.get("address") or r.get("store") or r.get("store_code") or "(no store)")


def _store_fields(r):
    r = r or {}
    return {"store": r.get("store_code"), "market": r.get("market") or None}


def cat_block(row, cat):
    """One store row's figures for ONE category, whichever of the two shapes the producer used. PURE."""
    row = row or {}
    cats = row.get("categories")
    if isinstance(cats, dict):
        return dict(cats.get(cat) or {})
    for m in (row.get("metrics") or ()):
        if (m or {}).get("cat") == cat:
            # The action plan's own field names for the same three figures.
            return {"monthly": m.get("target"), "achieved_mtd": m.get("achieved"),
                    "need": m.get("need"), "unit": m.get("unit")}
    return {}


def _achieved_of(cat):
    return lambda r: cat_block(r, cat).get("achieved_mtd")


def _trending_of(field):
    return lambda r: (r or {}).get(field)


def has_target(row, cat):
    """Did somebody actually set this category's target for this store? PURE.

    A ZERO IS NOT A TARGET. `derive_monthly_by_cat` returns 0 for a category with no row and no
    column value, and `targets_engine.attainment_pct` already refuses to call that 0% — the same
    contract applies to a drill row, so a store with no target set carries no figure here either."""
    return report_drilldown.money(cat_block(row, cat).get("monthly")) > 0


def _target_of(cat):
    def read(r):
        return cat_block(r, cat).get("monthly") if has_target(r, cat) else None
    return read


def _need_of(cat):
    def read(r):
        # A gap is only measurable against a target somebody set. Without one, `need` is the
        # engine's 0.0 and counting it would assert a store is on top of a target it does not have.
        return cat_block(r, cat).get("need") if has_target(r, cat) else None
    return read


def _no_target_reason(cat):
    return lambda r: None if has_target(r, cat) else REASON_NO_TARGET


def target_tables(row):
    """Which relation this store's target was typed into — read PER ROW, because the answer differs
    per row. `GET /targets/{period}/summary` seeds a store with no `targets` row of its own from the
    ROSTER's own `monthly_target`, so claiming the targets table for every row would name a row that
    does not exist. The producer records which it was (`has_target_row`), and this reads that stored
    fact rather than guessing. PURE."""
    row = row or {}
    if row.get("has_target_row") is False:
        return (STORE_ROSTER,)
    return (TARGETS,)


def _target_column(cat):
    _t, col, _s = target_origin(cat)
    return col


def _target_schema(cat):
    """NO SCHEMA on a target tile whose rows can name relations in two of them — see SCHEMA_COMMCALC.
    A declared category's target has only one home, so it keeps its schema."""
    if _te.target_store(cat) == "line":
        return SCHEMA_COMMCALC
    return None


def store_month_catalogue(axis, *, achieved=True, target=True, need=True):
    """The store-month panels, declared once for every report that folds over store rows.

    `total` names the key inside the roll-up's own `categories[cat]` block, so a figure the roll-up
    did not produce has no panel. Which of the three families a report asks for is the report's
    business: the area card shows all three, the accessory tiles show all three plus the projection,
    and the action plan's team total shows all three without the projection."""
    out = []
    for a in (axis or ()):
        cat = a.get("key")
        if not cat:
            continue
        unit = unit_of(a.get("unit"))
        srcs = achieved_sources(a.get("source"))
        common = {"row_label": _store_label, "keys": ("store_code",), "fields": _store_fields,
                  "unit": unit}
        if achieved:
            out.append({"key": "achieved_%s" % cat, "cat": cat, "total": "achieved_mtd",
                        "label": "%s achieved" % (a.get("label") or cat),
                        "value": _achieved_of(cat), "tables": srcs,
                        "schema": SCHEMA_COMMCALC if srcs else None,
                        "note": _AREA_ACHIEVED_NOTE, **common})
        if target:
            out.append({"key": "target_%s" % cat, "cat": cat, "total": "monthly",
                        "label": "%s assigned" % (a.get("label") or cat),
                        "value": _target_of(cat), "tables": target_tables,
                        "column": _target_column(cat), "schema": _target_schema(cat),
                        "reason": _no_target_reason(cat),
                        "note": _AREA_TARGET_NOTE, **common})
        if need:
            # THE ROW NAMES BOTH SIDES, because the row IS the subtraction (§CORE-17.3k). A row that
            # named only the feed would present a gap as a measurement of what was sold.
            gap_tables = (lambda r, _s=srcs: tuple(t for t in (target_tables(r) + _s) if t))
            out.append({"key": "need_%s" % cat, "cat": cat, "total": "need",
                        "label": "%s still needed" % (a.get("label") or cat),
                        "value": _need_of(cat), "tables": gap_tables, "schema": None,
                        "reason": _no_target_reason(cat),
                        "note": _AREA_NEED_NOTE, **common})
    return tuple(out)


def _store_month_panels(catalogue, rows, collective, *, subject=None, subject_kind=None,
                        note_by_key=None, cap=None):
    """PURE: every declared store-month panel whose figure the roll-up actually produced."""
    out = {}
    for spec in (catalogue or ()):
        spec = dict(spec)
        key, label, cat = spec.pop("key"), spec.pop("label"), spec.pop("cat")
        total_key = spec.pop("total")
        amount = ((collective or {}).get("categories") or {}).get(cat, {}).get(total_key)
        if amount is None:
            continue
        if note_by_key and key in note_by_key:
            spec["note"] = note_by_key[key]
        out[key] = report_drilldown.tile_panel(
            label, amount, rows, subject=subject, subject_kind=subject_kind,
            cap=cap or report_drilldown.ROW_CAP, **spec)
    return out


def area_drilldowns(stores, collective, catalog=None, *, cap=None):
    """PURE: the AREA roll-up's panels — `GET /targets/{period}/summary`'s `collective` block, opened
    to the stores it is the sum of.

    These are the panels the DM's area card, the Accessory Targets tiles and (narrowed to the stores
    on screen) the accessory page's own strip all read. The figure is `aggregate_stores`' own, so a
    panel can never be a second opinion about the total above it."""
    rows = [r for r in (stores or ()) if r]
    if not rows or not collective:
        return {}
    out = _store_month_panels(store_month_catalogue(axis_of(catalog)), rows, collective, cap=cap)
    # THE PROJECTION. Σ over the stores of a per-store projected month-end — a row field, so it
    # drills; but every row is an estimate rather than a measurement, and the note says so.
    trend = (collective or {}).get("trending") or {}
    for key, label, field, total_key, unit in (
            ("trending_accessory_dollars", "Accessory $ trending to month end",
             "trending_acc_target", "acc_target", report_drilldown.UNIT_MONEY),
            ("trending_box_count", "Boxes trending to month end",
             "trending_box", "box", report_drilldown.UNIT_COUNT)):
        amount = trend.get(total_key)
        if amount is None:
            continue
        out[key] = report_drilldown.tile_panel(
            label, amount, rows, value=_trending_of(field), row_label=_store_label,
            keys=("store_code",), fields=_store_fields, tables=SALES_FEEDS,
            schema=SCHEMA_COMMCALC, unit=unit, note=_AREA_TRENDING_NOTE,
            cap=cap or report_drilldown.ROW_CAP)
    return out


# ── The action plan's TEAM TOTAL — a figure the page used to reduce for itself. ──────────────────

def team_totals(stores, catalog=None):
    """THE ONE FOLD for the Daily Action Plan's "your team total" — per category, the target, the
    achieved and the gap across every store in the plan.

    The page reduced this in TypeScript, so the figure had no server-folded evidence at all
    (§CORE-17.3b) and nothing could check it. It is folded HERE, by
    `targets_engine.aggregate_stores` — the one home the area roll-up and every report card already
    use — over the plan's own store rows, so the number is identical and there is now exactly one of
    it. Returns the aggregate's own shape, so `area_drilldowns`' catalogue reads it unchanged. PURE."""
    rows = [r for r in (stores or ()) if r]
    if not rows:
        return {}
    axis = axis_of(catalog)
    # The plan shapes a store-month as a `metrics` list; the aggregate reads `categories`. Translated
    # here rather than in the aggregate, because the aggregate is the one home and this is the
    # plan's own row shape.
    shaped = []
    for r in rows:
        cats = {}
        for a in axis:
            cat = a.get("key")
            blk = cat_block(r, cat)
            if blk:
                cats[cat] = dict(blk, unit=blk.get("unit") or a.get("unit") or "count")
        shaped.append(dict(r, categories=cats))
    return _te.aggregate_stores(shaped)


def team_drilldowns(stores, collective, catalog=None, *, cap=None):
    """PURE: the team total's panels, over the plan's own store rows."""
    rows = [r for r in (stores or ()) if r]
    if not rows or not collective:
        return {}
    cat = store_month_catalogue(axis_of(catalog))
    return _store_month_panels(cat, rows, collective,
                               note_by_key={s["key"]: _TEAM_NOTE for s in cat}, cap=cap)


# ══════════════════════════════════════════════════════════════════════════════════════════════════
# §3  GRAIN 6 — A REP'S COMMISSION AT RISK. `GET /targets/{period}/action-plan`.
# ══════════════════════════════════════════════════════════════════════════════════════════════════

def at_risk_rows(stores):
    """One row per rep the plan carries a commission row for, across every store in it. PURE.

    The tile is Σ(store) of Σ(rep), so the rows are the reps — which is also the only grain at which
    the figure can be checked: a store roll-up restated as its own total would be the same number
    twice in one payload."""
    out = []
    for s in (stores or ()):
        for rp in ((s or {}).get("reps") or ()):
            comm = (rp or {}).get("commission")
            if not comm:
                continue
            out.append({"rep": (rp or {}).get("rep"), "store_code": (s or {}).get("store_code"),
                        "address": (s or {}).get("address"), "commission": comm})
    return out


def _at_risk_label(r):
    r = r or {}
    return " · ".join(str(x) for x in (r.get("rep") or "(unnamed rep)",
                                       r.get("address") or r.get("store_code") or "") if str(x or ""))


def at_risk_value(row):
    """What this rep has at risk, or None where the calculation never worked out their subtotal.

    LIVE DEFECT 1, SURFACED NOT FIXED. `rep_commission` returns `at_risk: 0.0` both for a rep safely
    at the full tier and for a rep whose `subtotal` `payout_slice_evidence` says is not known
    (§COMMCALC-09). The two are not the same fact: the first is a measured zero and the second is a
    figure nobody computed. The producer records which, in `subtotal`/`payout_slice_basis`, so this
    reads that stored verdict — and a null adds zero to a sum either way, so the tile does not move
    by a cent. PURE."""
    comm = (row or {}).get("commission") or {}
    if comm.get("subtotal") is None:
        return None
    return comm.get("at_risk")


def _at_risk_reason(row):
    return None if at_risk_value(row) is not None else REASON_NOT_COMPUTED


def _at_risk_keys(r):
    """The commission row is addressed by the rep the calculation stored it under, inside the store
    the plan is on. PURE."""
    r = r or {}
    return {"storeops_name": r.get("rep"), "store_code": r.get("store_code")}


def _at_risk_fields(r):
    comm = (r or {}).get("commission") or {}
    return {"tier": comm.get("tier"), "kpis met": comm.get("kpis_met"),
            "of": comm.get("total_kpis"), "basis": comm.get("payout_slice_basis")}


def at_risk_drilldowns(stores, total, *, rolled_up=True, cap=None):
    """PURE: the "commission at risk" tile, opened to the reps behind it.

    `rolled_up` is False when the endpoint DELIBERATELY zeroed the store roll-up — a self-scoped rep
    gets their own at-risk item and no cross-rep total. A figure that was suppressed rather than
    measured has no rows behind it, so no panel is served at all: a panel over the reps would then be
    evidence for a number the report is not claiming (live defect 2)."""
    if not rolled_up or total is None:
        return {}
    rows = at_risk_rows(stores)
    if not rows:
        return {}
    return {"commission_at_risk": report_drilldown.tile_panel(
        "Commission at risk", total, rows, value=at_risk_value, row_label=_at_risk_label,
        keys=_at_risk_keys, fields=_at_risk_fields, reason=_at_risk_reason,
        tables=(REP_COMMISSIONS,), schema=SCHEMA_COMMCALC, unit=report_drilldown.UNIT_MONEY,
        note=_AT_RISK_NOTE, cap=cap or report_drilldown.ROW_CAP)}


# ══════════════════════════════════════════════════════════════════════════════════════════════════
# §4  THE MANAGER REPORT CARDS — the same store-month grain, keyed by who answers for it.
# ══════════════════════════════════════════════════════════════════════════════════════════════════

def _card_item(row, cat):
    for it in ((row or {}).get("items") or ()):
        if (it or {}).get("key") == cat:
            return it or {}
    return {}


def _card_achieved(cat):
    return lambda r: _card_item(r, cat).get("achieved")


def _card_target(cat):
    def read(r):
        it = _card_item(r, cat)
        # The check-off's own three-state verdict decides this, not a second comparison: `no_target`
        # is its word for "nobody set this", and a 0 beside it is the absence, not a target.
        return None if it.get("state") == "no_target" else it.get("target")
    return read


def _card_reason(cat):
    return lambda r: (REASON_NO_TARGET if _card_item(r, cat).get("state") == "no_target" else None)


def _card_label(r):
    r = r or {}
    return str(r.get("store") or r.get("store_code") or "(no store)")


def _card_fields(r):
    r = r or {}
    return {"store": r.get("store_code"), "market": r.get("market") or None,
            "district": r.get("district") or None}


def card_catalogue(items):
    """The per-card panels, one pair per CHECK-OFF ITEM that is a target category.

    `items` is the response's own `items` list (`manager_report_card.items_for`), so the panels and
    the columns on the card are the same list in the same order — and its `unit` is already the
    drill-down's own vocabulary. The two items that are NOT target categories get no panel: a
    conversion rate is a quotient (§CORE-17.3h) and the peer verdict has no number at all — "the
    comparison IS the target", in the card module's own words."""
    out = []
    for it in (items or ()):
        cat = (it or {}).get("key")
        if not cat or (it or {}).get("unit") not in (report_drilldown.UNIT_MONEY,
                                                     report_drilldown.UNIT_COUNT):
            continue
        if cat in ("conversion", "peer_gap"):
            continue
        common = {"row_label": _card_label, "keys": ("store_code",), "fields": _card_fields,
                  "unit": it.get("unit"), "schema": None}
        out.append({"key": "achieved_%s" % cat, "cat": cat, "total": "achieved_mtd",
                    "label": "%s achieved" % (it.get("label") or cat),
                    "value": _card_achieved(cat), "tables": SALES_FEEDS,
                    "note": _CARD_ACHIEVED_NOTE, **common})
        out.append({"key": "target_%s" % cat, "cat": cat, "total": "monthly",
                    "label": "%s assigned" % (it.get("label") or cat),
                    "value": _card_target(cat), "tables": target_tables,
                    "column": _target_column(cat), "reason": _card_reason(cat),
                    "note": _CARD_TARGET_NOTE, **common})
    return tuple(out)


def card_drilldowns(card, items, *, cap=None):
    """PURE: one card's panels, over the stores ON that card, against that card's own roll-up."""
    card = card or {}
    rows = [r for r in (card.get("store_rows") or ()) if r]
    if not rows or not card.get("collective"):
        return {}
    return _store_month_panels(card_catalogue(items), rows, card.get("collective"),
                               subject=card.get("name"), subject_kind="district_manager", cap=cap)


def unassigned_rows(summary_rows, unassigned):
    """The period summary rows of the stores the org tree could not place. PURE.

    Selected by the codes `manager_report_card.build` already reported as unassigned, so this is the
    card builder's own partition read back rather than a second opinion about who owns a store."""
    codes = {str((u or {}).get("store_code") or "").strip().upper() for u in (unassigned or ())}
    codes.discard("")
    return [r for r in (summary_rows or ())
            if str((r or {}).get("store_code") or "").strip().upper() in codes]


def unassigned_drilldowns(summary_rows, unassigned, catalog=None, *, cap=None):
    """PURE: how much of the month is on NOBODY's card, opened to the stores it is.

    THE ORG-CHART GAP, IN THE OWNER'S OWN UNITS. Measured 2026-10-08 the tree names a DM for 6 of 29
    house stores and 0 of 20 at the other tenant, so most of the org lands here — which is a setup
    gap for the owner to fix in the org chart, not a code defect, and the page already says so in
    words. This puts a number on it: these stores' targets and results exist and answer to nobody.

    The figure is folded by `targets_engine.aggregate_stores`, the SAME one home every card's own
    roll-up comes from, over the rows this panel lists."""
    rows = unassigned_rows(summary_rows, unassigned)
    if not rows:
        return {}
    coll = _te.aggregate_stores(rows)
    cat = store_month_catalogue(axis_of(catalog), need=False)
    return _store_month_panels(cat, rows, coll, subject_kind="org_tree",
                               note_by_key={s["key"]: _NOBODY_NOTE for s in cat}, cap=cap), coll


# ── A MARKET MANAGER'S ROLL-UP, BY DISTRICT MANAGER — and what it cannot measure. ───────────────

def by_dm_rows(card, dm_cards, unassigned):
    """One row per DM beneath this manager, plus the stores no DM owns. PURE.

    THE SECOND HALF IS THE POINT. A manager card rolls up BY DM, so a store the org tree places under
    no district manager reaches no card at all — it is not zero on this manager's roll-up, it is
    unattributable. Those stores travel here as rows WITHOUT a figure, carrying
    `no_dm_in_org_tree`, so the reader sees what is missing from the number instead of being shown a
    total that quietly excludes it."""
    by_key = {str((c or {}).get("employee_id") or ""): (c or {}) for c in (dm_cards or ())}
    out = []
    for d in ((card or {}).get("dm_rows") or ()):
        key = str((d or {}).get("employee_id") or "")
        full = by_key.get(key) or {}
        out.append({"kind": "dm", "employee_id": key, "name": (d or {}).get("name"),
                    "stores": (d or {}).get("stores"), "collective": full.get("collective") or {}})
    for u in (unassigned or ()):
        out.append({"kind": "unplaced", "employee_id": None,
                    "name": str((u or {}).get("store") or (u or {}).get("store_code") or ""),
                    "store_code": (u or {}).get("store_code"), "collective": None})
    return out


def _by_dm_value(cat, total_key):
    def read(r):
        coll = (r or {}).get("collective")
        if not coll:
            return None
        return ((coll.get("categories") or {}).get(cat) or {}).get(total_key)
    return read


def _by_dm_label(r):
    r = r or {}
    if r.get("kind") == "unplaced":
        return "%s — on nobody's card" % (r.get("name") or r.get("store_code") or "(no store)")
    return "%s · %s store(s)" % (r.get("name") or "(unnamed)", r.get("stores"))


def _by_dm_reason(r):
    return None if (r or {}).get("collective") else REASON_NO_DM


def _by_dm_tables(r):
    """A DM's own roll-up names the feeds its stores' achievement was read out of. A store the tree
    could not place names NOTHING — there is no attribution to trace, which is the finding. PURE."""
    return SALES_FEEDS if (r or {}).get("collective") else ()


def _by_dm_fields(r):
    r = r or {}
    return {"district manager": (r.get("name") if r.get("kind") == "dm" else None),
            "why": (report_drilldown.UNMEASURED_REASONS[REASON_NO_DM]
                    if r.get("kind") == "unplaced" else None)}


def manager_card_drilldowns(card, dm_cards, unassigned, items, *, cap=None):
    """PURE: a market manager's roll-up, opened to the DMs beneath them — and to the stores that
    answer to none of them.

    The tie-out here is a REAL comparison and not a restatement: the figure is the manager card's own
    `collective` over every store beneath them, while the rows are each DM's own roll-up, so a store
    the tree places under TWO district managers makes the rows add up to more than the figure and the
    panel says so out loud (the card module reports the same thing as `store_card_placements`)."""
    card = card or {}
    coll = card.get("collective")
    rows = by_dm_rows(card, dm_cards, unassigned)
    if not coll or not rows:
        return {}
    out = {}
    for it in (items or ()):
        cat = (it or {}).get("key")
        if not cat or cat in ("conversion", "peer_gap"):
            continue
        if (it or {}).get("unit") not in (report_drilldown.UNIT_MONEY, report_drilldown.UNIT_COUNT):
            continue
        amount = ((coll.get("categories") or {}).get(cat) or {}).get("achieved_mtd")
        if amount is None:
            continue
        out["by_dm_achieved_%s" % cat] = report_drilldown.tile_panel(
            "%s achieved across your district managers" % (it.get("label") or cat),
            amount, rows, value=_by_dm_value(cat, "achieved_mtd"), row_label=_by_dm_label,
            keys=("employee_id",), fields=_by_dm_fields, reason=_by_dm_reason,
            tables=_by_dm_tables, schema=SCHEMA_COMMCALC, unit=it.get("unit"),
            subject=card.get("name"), subject_kind="manager", note=_BY_DM_NOTE,
            cap=cap or report_drilldown.ROW_CAP)
    return out


def report_card_drilldowns(cards, summary_rows, catalog=None, *, cap=None):
    """PURE: every panel the Manager Report Cards page needs, in one call.

    Returns `(per_card, response_level, nobody_collective)`:
      · `per_card`   {employee_id: {key: panel}} for the DM cards and the manager cards, so each card
                     carries only its own evidence.
      · `response_level` the "on nobody's card" panels.
      · `nobody_collective` the roll-up those panels explain, which the page prints beside them."""
    cards = cards or {}
    items = cards.get("items") or ()
    dm_cards = list(cards.get("dm_cards") or ())
    unassigned = list(cards.get("unassigned") or ())
    per_card = {}
    for c in dm_cards:
        panels = card_drilldowns(c, items, cap=cap)
        if panels:
            per_card[str(c.get("employee_id") or "")] = panels
    for c in (cards.get("manager_cards") or ()):
        panels = manager_card_drilldowns(c, dm_cards, unassigned, items, cap=cap)
        if panels:
            per_card[str(c.get("employee_id") or "")] = panels
    nobody, coll = (unassigned_drilldowns(summary_rows, unassigned, catalog, cap=cap)
                    if unassigned else ({}, {}))
    return per_card, nobody, coll
