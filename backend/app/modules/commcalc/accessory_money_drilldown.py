"""WHERE DID THIS ACCESSORY FIGURE COME FROM — the declaration for the three ACCESSORY MONEY reports
(§CORE-17, batch 15).

  /commcalc/accessory-target-plan   Accessory Target Allocation — one company goal, split across the
                                   stores on what each store's own accessories-per-box says it can
                                   carry.
  /commcalc/accessory-cost-audit    Accessory Cost Audit — what a %-of-GP payout is actually being
                                   computed FROM, and what the same period would have paid under
                                   each of the owner's five options.
  /commcalc/accessory-flags         Accessory Flags — accessories rung out above the max threshold or
                                   below the allowed minimum, and the chargeback exposure that
                                   implies.

Chosen as one family because the three of them answer one owner question in three parts: *what
accessory money did this store make, at what cost, and against what target.*

HOW MANY GRAINS THIS FAMILY REALLY HAS — MEASURED OFF THE PRODUCERS, NOT ASSUMED (§CORE-17.3l)
─────────────────────────────────────────────────────────────────────────────────────────────
Reading the three producers (`accessory_target_plan.plan`, `accessory_cost_audit.audit` and
`router.accessory_flags`' own rollup) gives **EIGHT** grains in **THREE** row shapes and **ONE**
unit, because a grain fixes the ROW, the UNIT **and** the PROVENANCE, and sharing one catalogue needs
all three to agree:

  1. a STORE's ACCESSORY $ FOR A NAMED MONTH      money, out of the sales display union — four tiles
                                                  (two history months, the month to date and the
                                                  projected month end) and ONE grain, because all
                                                  four are the same read of the same rows. The
                                                  projection is an ESTIMATE over those same rows and
                                                  its note says so — the §CORE-17.3m posture batch 13
                                                  already took for a trending tile.
  2. a STORE's ACCESSORY TARGET IN FORCE          the same row, the same unit, and a completely
                                                  different provenance: a number somebody typed into
                                                  Target Settings. A TARGET IS A DECLARED NUMBER, so
                                                  a store nobody set one for is unmeasurable, never
                                                  a target of zero (§CORE-17.3m).
  3. a STORE's SUGGESTED SHARE OF THE GOAL        the same row again, in the same unit, and it was
                                                  read out of NOTHING: it is derived on this load
                                                  from the goal the user typed and is stored nowhere
                                                  until somebody presses Assign. A stated, countable
                                                  untraceable origin (§CORE-17.3i) — the honest
                                                  answer, and the one the owner needs before writing
                                                  it onto 29 stores.
  4. a STORE's EXTENSION                          the same row a fourth time, naming BOTH grain 2 and
                                                  grain 3, because the subtraction happens PER STORE
                                                  and can therefore disagree with either
                                                  (§CORE-17.3k).
  5. a REP's %-OF-BASIS PAYOUT FOR THE PERIOD     a different row shape; money; read out of the sale
                                                  lines the live plan engine matched, at the rate the
                                                  plan rule carries. SEVEN tiles on one grain — today,
                                                  the three computable options and the three per-rep
                                                  differences — because every one of them is the same
                                                  read, re-based by arithmetic the producer does
                                                  itself.
  6. a REP's PAYOUT WITH THE POS CATALOG'S COST   the same rep row, the same unit, and one more
                                                  relation: the product catalogue. Its own grain for
                                                  exactly that reason, and its note states the one
                                                  thing the figure does not admit on screen (below).
  7. a FLAGGED SALE LINE's PRICE                  the third row shape; money; out of the monthly sales
                                                  upload ALONE, which is what that report reads
                                                  (defect 4).
  8. a FLAGGED SALE LINE's DEFAULT CHARGEBACK     the same line, the same unit, and not a measurement
                                                  of the sale at all: one number off the org's own
                                                  flag-rules row, applied to every flagged line.

**Grains 1–4 are keyed by the SAME store row in the SAME unit and are still four grains; 5 and 6 are
the same rep row; 7 and 8 are the same sale line.** The general lesson, written down once: *two
figures on one row in one unit are two grains as soon as a DIFFERENT READ, or no read at all,
produced one of them.* A single catalogue over grains 1 and 3 would have to claim the sales feed
produced a suggestion nobody has stored — which is the lie the tie-out would then assert on our
behalf.

ONE MODULE, THREE CATALOGUES, AND WHY THAT IS NOT THE SIBLING DEFECT
────────────────────────────────────────────────────────────────────
What the three reports share is not a row shape but the thing a drill-down exists for — *which stored
relation was this accessory figure read out of* — and there are exactly FIVE answers in the family:
the sales display union, the monthly sales upload on its own, the product catalogue, the target
settings row and the org's flag-rules row. Three files would be three answers to one question. A
CATALOGUE, by contrast, is per ROW LIST: the plan's stores, the audit's reps and the flag report's
sale lines are three different populations, and one catalogue over all three would be a panel about
rows its report is not showing.

A PANEL EXPLAINS THE FIGURE, NOT THE TABLE (§CORE-17.3o, the ruling this batch settles)
───────────────────────────────────────────────────────────────────────────────────────
Two of these three screens filter their row table IN THE BROWSER and then print a tile the SERVER
folded over the whole range. §CORE-17.3d covered the other case — a screen that re-folds its tiles
over the filtered rows gets its panel narrowed to that same selection through `lib/drill-narrow`. It
does NOT apply here, and wiring it in would be a defect rather than a fix: narrowing these panels
would make each tile read a DIFFERENT NUMBER under a filter, which is a figure moved, and this batch
moves none.

So the panel follows the FIGURE it explains. Every panel here is folded over exactly the rows its
tile was folded over, ties to it to the cent, and its note SAYS that it covers the whole range rather
than the selection in the table below. The disagreement between a filtered table and an unfiltered
tile is a real defect and it is REPORTED (defects 5 and 6), never papered over by quietly re-folding
one of them.

WHAT GETS NO PANEL HERE, AND WHICH RULING SAYS SO
─────────────────────────────────────────────────
  · **EVERY RATE.** The plan's accessories-per-box, its required-$-per-box and the company rate; the
    audit's stored plan rate; a store's share-of-goal percentage. No set of records sums to a rate or
    a quotient (§CORE-17.3h).
  · **THE COMPANY GOAL ITSELF.** It is a declared number — a total somebody typed, or a percentage
    applied to one basis amount. A percentage of a total is subtracted-at-the-total's cousin
    (§CORE-17.3k) and has no rows of its own; the four BASIS amounts it can be computed from are each
    Σ over the stores and DO open, as grain 1's own tiles.
  · **Δ A ON THE COST AUDIT.** The page computes "A vs today" from two totals, and `by_rep` carries
    no per-rep `delta_a` to fold — subtracted at the TOTAL, so it does not drill. Δ B, Δ C and Δ R DO
    drill, because the producer folds each of them per rep (§CORE-17.3k, the same test with the
    opposite answer).
  · **A COUNT WHOSE LIST IS ON SCREEN.** The audit's matched / suspect / flagged-shown counts, the
    flag report's flags / items / transactions / over / under tiles, the plan's store counts. No key
    at all, never an empty panel.
  · **A RE-PARTITION THAT PRINTS ITS OWN TOTALS.** The audit's per-rule and per-item tables, and the
    flag report's By store / By rep / By store-and-employee cards. They re-partition precisely the
    rows the panels open to and already carry their own subtotals; a second answer beside an existing
    one is the defect.
  · **A THRESHOLD, A MARGIN AND A FEE RATE.** The flag report's over/under thresholds and default
    chargeback amount, and the audit's Option C margin, are numbers a person types to decide which
    rows are flagged or what to re-base onto. Config is not evidence (§CORE-17.3m) and they have no
    rows at all. The chargeback TOTAL does open — it is Σ over the flagged lines — and names the
    config row the per-line amount came out of rather than pretending the sale produced it.
  · **ONE FLAGGED TRANSACTION'S RECEIPT.** `GET /commcalc/accessory-flags/receipt` already opens a
    flagged line to every item on its transaction, and the page already links it from the row. A
    second answer beside an existing one is the defect, not the fix.

ABSENCE IS NEVER ZERO
─────────────────────
A store nobody set an accessory target for, a store the goal is not being split across and a store
nothing measurable was found for are each SHOWN and NOT counted, with the reason in the reader's own
words. The figures do not move by a cent — a null adds nothing to a sum either way, and the producers
add nothing for those rows either — and only the row changes, which is the whole argument for putting
it there.

AN EMPTY REPORT DRILLS TO NOTHING. A report that produced its totals over NO ROWS AT ALL would still
get a panel asserting a figure behind an empty list, which ties only by accident and reads like a
bug. Every entry point below returns an empty mapping in that case.

LIVE DEFECTS THESE THREE REPORTS HAVE TODAY — ALL SIX REPORTED, NO FIGURE MOVED
───────────────────────────────────────────────────────────────────────────────
1. **The cost audit reads a DIFFERENT sales universe from the engine that pays.** `audit()` calls
   `commission_engine.preview(...)` without `source_mode`, so it always reads the LEGACY universe
   (the monthly upload, falling back to the live feed), while the live calculator passes
   `router._sales_source_mode(...)` and a tenant configured for `union` (mig 306) is paid over the
   transaction-grain union. Measured live in that migration's own note: 3,235 rows against 9,161. The
   module's header claims the "today" column "can never drift from the money"; for a union-mode
   tenant it can, by construction. NOT fixed — changing that read moves every figure on the page.
   The panels name the two relations the legacy read actually walks, which is the truth about where
   that money came from.
2. **Neither the audit's payload nor the engine's records WHICH of those two relations answered.**
   `_read_sales` takes the monthly upload when it has rows and the live feed otherwise, and keeps no
   meta saying which. So grains 5 and 6 name BOTH with a note, rather than guessing one per row —
   the §CORE-17.3n posture (name what the read covers) where there is no producer meta to read.
3. **Option A's TOTAL silently folds in today's pay for every line the catalogue is silent about.**
   `_line_options` returns `option_a: None` for such a line (correctly — it is unknown, never
   guessed), and the accumulation then adds `amt if v is None else v`. So the figure under "A — POS
   catalog cost corrected" is part corrected pay and part today's pay, and nothing on the page says
   which rows are which. NOT fixed: the substitution is the producer's arithmetic. Grain 6's note
   states it, and the per-rep payload carries no signal for how much of a rep's figure is a
   correction, which the note also says rather than implying a precision it does not have.
4. **The Accessory Flags report reads the monthly sales upload ALONE.** The endpoint queries
   `raw_sales` directly instead of the display union every other sales-derived figure reads — the
   §CORE-17.3c class, in the read rather than in the label — while the page OPENS on the first of the
   current month, i.e. the open month, where a feed-led tenant's rows have not been promoted yet. So
   the chargeback tool can show nothing for sales the Sales Report shows. NOT fixed: changing that
   read moves a figure and the same rows drive a push into people's chargebacks. Grain 7 names the
   monthly upload alone, which is what it read.
5. **The flag report's five tiles and its three rollup cards are folded over the WHOLE range while
   the table below is filtered in the browser.** Market, store, rep and reason are all client-side,
   and `summary` / `by_store` / `by_rep` come off the server untouched by them. So "Total rung $" and
   the table disagree about which sales they are about the moment anything is picked. NOT fixed: the
   two honest repairs are to re-fold the tiles (which moves a figure) or to make the filters query
   parameters (which is a different report). See §CORE-17.3o.
6. **The cost audit's options table is the same shape.** `StandardFilterBar` narrows the per-rep and
   per-line tables in the browser; the five option totals are the server's own, over every rep.
   Reported for the same reason.
7. **The plan treats a store with no target row as a store whose target is $0.00, twice.**
   `current_target` is `safe_float(row.get('accessories_monthly'))`, so "nobody set one" and "set to
   zero" print identically — and `extension` then asks such a store for the WHOLE suggestion as
   though it were carrying nothing. The row-level half IS fixed here, because the producer already
   records `has_target_row`: grain 2 and grain 4 show such a store WITHOUT a figure and do not count
   it, and the totals are identical to the cent because a null adds nothing. The half that is only
   REPORTED is a store that HAS a row whose `accessories_monthly` is NULL — nothing in the payload
   distinguishes that from a stored zero, and inventing the distinction here would be a guess.

💰 MOVES NO MONEY. Read-only presentation of figures the producers already computed. Books nothing,
pays nothing, writes nothing, and computes no business figure of its own: every amount it carries was
handed to it. PURE: no I/O, no framework import, no clock, no org / carrier / tenant / product / store
name (RULE TWO), and no dollar literal, rate or threshold of its own.
Registered in docs/SYSTEM_DATA_FLOW_INDEX.md §CORE-17.3o.
"""
from app.modules.commcalc import accessory_cost_audit as _aca
from app.modules.commcalc import data_lineage_registry as _lineage
from app.modules.commcalc import targets_drilldown as _tdd
from app.modules.core import report_drilldown

# ── THE FIVE PROVENANCE ANSWERS THIS FAMILY HAS ──────────────────────────────────────────────────
#: What a store's accessory $ for a month was read out of: the registry's own display union, which is
#: the pair `router._fetch_actuals` walks for every period. DEREFERENCED from the targets family's
#: declaration, which already dereferences the registry — naming it a third time would be the copied
#: fact the registry exists to prevent.
SALES_FEEDS = _tdd.SALES_FEEDS

#: What the PAY ENGINE's legacy read walks, in ITS order: the monthly upload leads and the live feed
#: is the fallback — the reverse of the display union's lead, which is a real difference and not a
#: spelling of the same thing. Named separately for exactly that reason (defect 1).
PAY_SALES_LEAD = _lineage.MONTHLY_SALES
PAY_SALES_FALLBACK = _lineage.LIVE_SALES_FEED
PAY_SALES_FEEDS = (PAY_SALES_LEAD, PAY_SALES_FALLBACK)

#: The product catalogue Option A's corrected cost is looked up in. Dereferenced from the registry,
#: which already held the name four times as a literal.
PRODUCT_CATALOG = _lineage.PRODUCT_CATALOG

#: Where an accessory TARGET physically lives. A config row somebody typed into a settings screen, so
#: it is deliberately NOT in the lineage registry (its documented scope is external-feed ingest
#: tables). DEREFERENCED from the targets family, which named it once already.
TARGETS = _tdd.TARGETS

#: The plan rule that carries the RATE a %-of-basis payout is computed at, and the org's flag-rules
#: row that carries the default chargeback amount. Both are config rows, named here for the same
#: reason `TARGETS` is named in `targets_drilldown` rather than in the feed registry.
PLAN_RULES = "commission_rule"        # commcalc.commission_rule (mig 059)
FLAG_RULES = "flag_rules"             # commcalc.flag_rules (mig 041)

SCHEMA = "commcalc"

#: The columns a figure here is read out of, where a real column holds it. A suggestion has no column
#: because it has no table (grain 3).
COL_CURRENT_TARGET = "accessories_monthly"
COL_EXT_PRICE = "ext_price"
COL_CHARGEBACK = "accessory_chargeback_amount"

#: Why a drill row on these reports carries no number. Each is an entry in the ONE home's vocabulary
#: (`report_drilldown.UNMEASURED_REASONS`), named here so a catalogue cannot emit a reason the reader
#: would see as a bare field name — the harness asserts every one of them resolves to a sentence.
REASON_NO_TARGET = "target_not_set"
REASON_NOT_SELECTED = "not_in_selection"
REASON_NO_CAPACITY = "no_capacity_measured"


# ── NOTES — what each panel is, in the words the screen already uses ────────────────────────────

#: Said on every panel whose screen filters its own table in the browser (§CORE-17.3o). The reader is
#: told which population the figure is about instead of being left to assume it is the one below.
_WHOLE_RANGE = (
    " This figure, and these rows, cover the whole range the report asked for — not the selection the "
    "table below is filtered to. The tile is the server's own fold and narrowing it here would change "
    "the number you are reading.")

_MONTH_NOTE = (
    "One row per store in this window. Each figure is that store's accessory sales for the month — "
    "accessory revenue plus the device set-up fee, the same accessory basis the Accessory Sales "
    "Targets tracker uses — read out of the same sales rows the Sales Report shows.")

_PROJECTED_NOTE = (
    "One row per store, and each figure is a PROJECTION rather than a measurement: the month to date "
    "scaled to the whole month by the same computation Executive MTD uses, over the same sales rows. "
    "A store the sales rows hold nothing for projects nothing.")

_TARGET_NOTE = (
    "One row per store, and each figure is the number somebody TYPED into Target Settings for this "
    "month — not a measurement of anything. A store with no target row at all is shown WITHOUT a "
    "figure and is not counted: an unset target is not a target of zero, and a store without one is "
    "not a store failing it.")

_SUGGESTION_NOTE = (
    "One row per store, and NONE of these figures has been stored anywhere. Each is this store's "
    "share of the company goal, worked out on this load from its own accessories-per-box history "
    "against the boxes it is projected to sell — which is why every row reports an origin that "
    "cannot be traced. Pressing Assign is what turns a suggestion into the target row the panel "
    "above opens. A store outside the selection, and a store nothing measurable was found for, are "
    "shown here without a figure and counted in nothing.")

_EXTENSION_NOTE = (
    "One row per store: the share it is being asked to carry minus the target it already has. The "
    "figure is the sum of those per-store differences, which is why it drills at all — a difference "
    "taken between two company totals would have no rows behind it. Each row therefore names BOTH "
    "sides and can disagree with either. A store with no target row is shown without a figure here "
    "too, because the extension the report computes for it treats its missing target as zero.")

_OPTION_NOTE = (
    "One row per rep, as the table below lists them. Each figure is Σ of the per-line amounts the "
    "live commission-plan engine produced for that rep over the lines a %-of-basis rule matched, at "
    "the rate the plan rule carries. The options are re-bases of those same per-line numbers, so "
    "they open to the same rows — nothing is paid or re-paid by looking at them." + _WHOLE_RANGE)

_OPTION_A_NOTE = (
    "One row per rep. This option can only be computed for items the product catalogue already "
    "carries a cost for; for every other line the report folds in what the rep is paid TODAY "
    "instead, so this total is part corrected pay and part current pay. The payload does not record "
    "how much of any one rep's figure is which, and this panel does not guess — the item list on the "
    "page is the worksheet that says which items have a catalogue cost at all." + _WHOLE_RANGE)

_DELTA_NOTE = (
    "One row per rep: that rep's own option total minus what they are paid today. The figure is the "
    "sum of those per-rep differences, which is why it drills — the same difference taken between "
    "two period totals would have no rows behind it." + _WHOLE_RANGE)

_FLAG_PRICE_NOTE = (
    "One row per flagged accessory line, as the table below lists them. Each figure is what the "
    "register rang that line out at, read out of the monthly sales upload — which is the only sales "
    "relation this report reads." + _WHOLE_RANGE)

_FLAG_CHARGEBACK_NOTE = (
    "One row per flagged accessory line, and the figure on each is NOT a measurement of that sale: "
    "it is the default chargeback amount set once for the whole organisation, applied to every "
    "flagged line. Changing that default changes this total without a single sale changing. The "
    "amount a rep is actually charged is whatever is typed on the row before it is pushed." +
    _WHOLE_RANGE)


# ══════════════════════════════════════════════════════════════════════════════════════════════════
# §A  THE ACCESSORY TARGET ALLOCATION — four grains on one store row.
# ══════════════════════════════════════════════════════════════════════════════════════════════════

def _store_label(r):
    """PURE: the store as the report's own table prints it — its address, else its code."""
    return str((r or {}).get("address") or (r or {}).get("store_code") or "") or None


def _store_fields(r):
    """PURE: what else the reader needs on a plan row to judge the figure beside it."""
    r = r or {}
    return {"market": r.get("market"), "boxes used": r.get("expected_boxes"),
            "rate basis": r.get("acc_per_box_basis"),
            "boxes basis": r.get("expected_boxes_basis"),
            "in selection": "yes" if r.get("selected") else "no",
            "target row": "yes" if r.get("has_target_row") else "no"}


def _month_value(field):
    """PURE: a reader for one of the four month columns. A store absent from the sales rows for that
    month arrives here already coerced to 0.0 by the producer, and the payload records nothing that
    would distinguish it from a store that attached nothing — so this reads the figure as handed
    rather than inventing the distinction (see the header)."""
    def _read(r):
        return (r or {}).get(field)
    return _read


def has_target_row(r):
    """PURE: does this store have a target row for the month at all?

    The producer's own `has_target_row`, which is why grain 2 can tell an unset target from a zero
    one. A row the producer did not stamp is treated as HAVING one, because refusing to count a
    figure on a guess would move the tile."""
    return bool((r or {}).get("has_target_row", True))


def _current_target(r):
    """PURE: the target in force, or None when nobody set one (§CORE-17.3m)."""
    return (r or {}).get("current_target") if has_target_row(r) else None


def _target_tables(r):
    """PURE: the target settings row — and NO table at all for a store that has none, because the
    $0.00 the producer shows for it was read out of nothing."""
    return (TARGETS,) if has_target_row(r) else ()


def _target_reason(r):
    return None if has_target_row(r) else REASON_NO_TARGET


def _suggested(r):
    return (r or {}).get("suggested_target")


def _extension(r):
    """PURE: the extension exactly as the producer folded it — None only where there is no suggestion
    at all, which is also the only case its own total skips."""
    if _suggested(r) is None:
        return None
    return (r or {}).get("extension")


def _extension_measured(r):
    """PURE: COUNTED BUT NOT TRUSTED, which is a third state and the honest one here (defect 7).

    For a store with no target row the producer subtracts a target nobody set, so the extension it
    reports is the whole suggestion — a real number the tile really counts, and not one the reader
    should read as "this store is being asked for that much more than it carries". Dropping the row
    instead would make the panel disagree with a tile it has no business disagreeing with: the
    producer DID add that figure, unlike the absences elsewhere in this family, where a null adds
    nothing to either sum."""
    if _extension(r) is None:
        return None
    return has_target_row(r)


def suggestion_reason(r):
    """PURE: why a store has no suggested share — the producer's own two shapes, each in the one
    home's vocabulary. A store outside the multi-select was never re-planned (its current target is
    reserved out of the goal instead); a SELECTED store with no number is one nothing measurable was
    found for, which is the producer's own `unweighted` list seen from the row."""
    if _suggested(r) is not None:
        return None
    return None if (r or {}).get("selected") else REASON_NOT_SELECTED


def _assigned_reason(r):
    if _suggested(r) is not None:
        return None
    return suggestion_reason(r) or REASON_NO_CAPACITY


def _extension_reason(r):
    """PURE: an extension is missing for whichever reason its two sides are missing — no suggestion
    at all, or a store with no target row to subtract from (defect 7)."""
    if _suggested(r) is None:
        return _assigned_reason(r)
    return None if has_target_row(r) else REASON_NO_TARGET


def plan_catalogue(month_labels=None):
    """PURE: the Accessory Target Allocation's panels, declared once.

    `month_labels` is `{'m1': 'Sep 2026', 'm2': 'Aug 2026'}` off the report's own
    `history_periods`, so the panel is titled with the month the column is titled with rather than a
    second spelling of it. A label the report did not send falls back to the column's plain words.
    """
    ml = dict(month_labels or {})
    common = {"row_label": _store_label, "keys": ("store_code",), "fields": _store_fields,
              "unit": report_drilldown.UNIT_MONEY, "schema": SCHEMA}
    out = [
        {"key": "m2_acc", "total": "m2_acc",
         "label": "Accessory sales — %s" % (ml.get("m2") or "two months ago"),
         "value": _month_value("m2_acc"), "tables": SALES_FEEDS, "note": _MONTH_NOTE, **common},
        {"key": "m1_acc", "total": "m1_acc",
         "label": "Accessory sales — %s" % (ml.get("m1") or "last month"),
         "value": _month_value("m1_acc"), "tables": SALES_FEEDS, "note": _MONTH_NOTE, **common},
        {"key": "mtd_acc", "total": "mtd_acc", "label": "Accessory sales — month to date",
         "value": _month_value("mtd_acc"), "tables": SALES_FEEDS, "note": _MONTH_NOTE, **common},
        {"key": "projected_acc", "total": "projected_acc",
         "label": "Accessory sales projected to month end",
         "value": _month_value("projected_acc"), "tables": SALES_FEEDS,
         "note": _PROJECTED_NOTE, **common},
        {"key": "current_target", "total": "current_target", "label": "Accessory targets set now",
         "value": _current_target, "tables": _target_tables, "column": COL_CURRENT_TARGET,
         "reason": _target_reason, "note": _TARGET_NOTE, **common},
    ]
    return tuple(out)


def plan_goal_catalogue():
    """PURE: the two panels that exist ONLY once a company goal has been entered.

    A goal nobody typed makes every suggestion `None`, and a $0.00 "being assigned" over rows that
    all carry no number is not a figure — it is the absence of one. §CORE-17.3m's ruling applied to
    the figure a target produces, rather than to the target: these keys are not served at all in that
    case, so the tile offers no drill and the page keeps the em dash it already prints.
    """
    common = {"row_label": _store_label, "keys": ("store_code",), "fields": _store_fields,
              "unit": report_drilldown.UNIT_MONEY, "schema": SCHEMA}
    return (
        {"key": "assigned", "total": "assigned", "label": "Accessory target being assigned",
         # NO TABLE, DELIBERATELY: a suggestion is derived on this load and stored nowhere, so every
         # row reports an origin that cannot be traced (§CORE-17.3i). The one home turns an empty
         # tuple into exactly that, rather than into an empty origin list.
         "value": _suggested, "tables": (), "reason": _assigned_reason,
         "note": _SUGGESTION_NOTE, **common},
        {"key": "extension", "total": "extension", "label": "Total extension over current targets",
         "value": _extension, "measured": _extension_measured,
         # THE ROW NAMES BOTH SIDES, because the row IS the subtraction (§CORE-17.3k). One of those
         # sides is the suggestion, which has no table — so a store with no target row either names
         # NOTHING, which is the truth about a subtraction between two figures nobody stored.
         "tables": _target_tables, "reason": _extension_reason,
         "note": _EXTENSION_NOTE, **common},
    )


def plan_drilldowns(result, *, cap=None):
    """PURE: every panel for `GET /commcalc/accessory-target-plan/{period}`.

    `result` is `accessory_target_plan.plan`'s own output as the endpoint serves it. Every figure is
    the producer's `totals` entry, dereferenced — so a panel can never be a second opinion about the
    tile above it.
    """
    rows = [r for r in ((result or {}).get("rows") or ()) if r]
    totals = dict((result or {}).get("totals") or {})
    if not rows or not totals:
        return {}
    cat = list(plan_catalogue((result or {}).get("history_periods")))
    if ((result or {}).get("goal") or {}).get("goal") is not None:
        cat += list(plan_goal_catalogue())
    return report_drilldown.tiles(tuple(cat), rows, totals,
                                  cap=cap or report_drilldown.ROW_CAP)


# ══════════════════════════════════════════════════════════════════════════════════════════════════
# §B  THE ACCESSORY COST AUDIT — two grains on one rep row, separated by one more read.
# ══════════════════════════════════════════════════════════════════════════════════════════════════

#: The three options whose DIFFERENCE the producer folds per rep. Option A is absent on purpose: the
#: page subtracts it at the total and `by_rep` carries no `delta_a` to fold (§CORE-17.3k).
DELTA_OPTIONS = ("option_b", "option_c", "option_r")

#: The option keys whose provenance is the engine's own read. DEREFERENCED from the producer's own
#: tuple minus the one option that reads a second relation, so a new option added there cannot be
#: silently left out of this catalogue — the harness fails the build if the two drift apart.
ENGINE_OPTIONS = tuple(k for k in _aca.OPTION_KEYS if k != "option_a")


def _rep_label(r):
    """PURE: the rep as the report's own table prints them, with the store that answers 'which one'."""
    r = r or {}
    parts = [str(r.get("rep") or "").strip(), str(r.get("store") or "").strip()]
    return " · ".join(p for p in parts if p) or None


def _rep_fields(r):
    r = r or {}
    return {"store": r.get("store"), "market": r.get("market"), "plan": r.get("plan_name"),
            "lines": r.get("matched_lines"), "suspect lines": r.get("suspect_lines")}


def _opt_value(key):
    def _read(r):
        return (r or {}).get(key)
    return _read


def option_label(key):
    """PURE: the owner-facing name of an option, DEREFERENCED from the producer's own
    `OPTION_LABELS` rather than spelled a second time here. A key the producer does not label falls
    back to the key, which is visible rather than wrong."""
    return _aca.OPTION_LABELS.get(key, key)


def audit_catalogue():
    """PURE: the cost audit's panels — seven tiles on the engine's own read, one on the catalogue's.

    Every option is Σ over the SAME rep rows and differs only in which field holds the figure, which
    is why they are one catalogue with a tile each and not one catalogue per option.
    """
    common = {"row_label": _rep_label, "keys": ("rep",), "fields": _rep_fields,
              "unit": report_drilldown.UNIT_MONEY, "schema": SCHEMA}
    out = [{"key": k, "total": k, "label": option_label(k), "value": _opt_value(k),
            "tables": PAY_SALES_FEEDS + (PLAN_RULES,), "note": _OPTION_NOTE, **common}
           for k in ENGINE_OPTIONS]
    out.append({"key": "option_a", "total": "option_a", "label": option_label("option_a"),
                "value": _opt_value("option_a"),
                "tables": PAY_SALES_FEEDS + (PLAN_RULES, PRODUCT_CATALOG),
                "note": _OPTION_A_NOTE, **common})
    for k in DELTA_OPTIONS:
        out.append({"key": "delta_%s" % k.split("_")[-1], "total": "delta_%s" % k.split("_")[-1],
                    "label": "%s — difference against today" % option_label(k),
                    "value": _opt_value("delta_%s" % k.split("_")[-1]),
                    "tables": PAY_SALES_FEEDS + (PLAN_RULES,), "note": _DELTA_NOTE, **common})
    return tuple(out)


def audit_totals(result):
    """PURE: the figures the audit's tiles actually show, in one dict for the one home to fold over.

    The five option totals are the producer's own; the three differences are its own `deltas`, lifted
    under the keys the rep rows carry them under so a tile and its rows cannot be two subtractions.
    A delta the producer did not express as a NUMBER — Option A's, which it serves as the words
    'partial' / 'unknown' — is left out entirely, so no panel is served for it.
    """
    out = dict((result or {}).get("totals") or {})
    deltas = (result or {}).get("deltas") or {}
    for k in DELTA_OPTIONS:
        v = deltas.get(k)
        if isinstance(v, bool) or not isinstance(v, (int, float)):
            continue
        out["delta_%s" % k.split("_")[-1]] = v
    return out


def audit_drilldowns(result, *, cap=None):
    """PURE: every panel for `GET /commcalc/accessory-cost-audit/{period}`.

    An audit that is not `ready` — no plan configured — and an audit where no %-of-basis rule matched
    a line have no rep rows and therefore no panels at all.
    """
    rows = [r for r in ((result or {}).get("by_rep") or ()) if r]
    if not (result or {}).get("ready") or not rows:
        return {}
    return report_drilldown.tiles(audit_catalogue(), rows, audit_totals(result),
                                  cap=cap or report_drilldown.ROW_CAP)


# ══════════════════════════════════════════════════════════════════════════════════════════════════
# §C  THE ACCESSORY FLAGS — two grains on one sale line, one of them not about the sale at all.
# ══════════════════════════════════════════════════════════════════════════════════════════════════

def _flag_label(r):
    """PURE: the flagged line as the table below prints it — the date, the store, the person and the
    item, which together are what a reader is looking for when they ask which sale this was."""
    r = r or {}
    date = str(r.get("trans_date") or "")[:10]
    parts = [date, str(r.get("store") or "").strip(), str(r.get("rep") or "").strip(),
             str(r.get("item_desc") or "").strip()]
    return " · ".join(p for p in parts if p) or None


def _flag_fields(r):
    r = r or {}
    return {"store": r.get("store"), "employee": r.get("rep"), "sku": r.get("sku"),
            "department": r.get("department"), "category": r.get("category"),
            "phone model": r.get("phone_model"),
            "why flagged": ("under the minimum" if r.get("flag_reason") == "under"
                            else "over the maximum"),
            "already pushed": "yes" if r.get("already_flagged") else "no"}


def _flag_price(r):
    return (r or {}).get("ext_price")


def _flag_chargeback(r):
    return (r or {}).get("chargeback_amount")


def flags_catalogue():
    """PURE: the two money tiles on the Accessory Flags dashboard.

    The other three tiles are COUNTS of the table below and get no key at all; the By-store, By-rep
    and By-store-per-employee cards re-partition these same rows and already print their own totals.
    """
    common = {"row_label": _flag_label, "keys": ("trans_id", "sku"), "fields": _flag_fields,
              "unit": report_drilldown.UNIT_MONEY, "schema": SCHEMA}
    return (
        {"key": "total", "total": "total", "label": "Accessory $ rung out in flagged sales",
         "value": _flag_price, "tables": (PAY_SALES_LEAD,), "column": COL_EXT_PRICE,
         "note": _FLAG_PRICE_NOTE, **common},
        {"key": "chargeback_total", "total": "chargeback_total",
         "label": "Chargeback exposure at the default amount",
         # NOT the sale's relation: this figure was read out of the org's own flag-rules row.
         "value": _flag_chargeback, "tables": (FLAG_RULES,), "column": COL_CHARGEBACK,
         "note": _FLAG_CHARGEBACK_NOTE, **common},
    )


def flags_drilldowns(payload, *, cap=None):
    """PURE: every panel for `GET /commcalc/accessory-flags`.

    `payload` is the endpoint's own response. A range with no flagged sale drills to nothing.
    """
    rows = [r for r in ((payload or {}).get("rows") or ()) if r]
    summary = dict((payload or {}).get("summary") or {})
    if not rows or not summary:
        return {}
    return report_drilldown.tiles(flags_catalogue(), rows, summary,
                                  cap=cap or report_drilldown.ROW_CAP)
