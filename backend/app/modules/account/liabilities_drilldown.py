"""FOUR REPORTS COMPOSED INTO SIX TILES, AND THE COMPOSITION WAS INVISIBLE — the declaration for
/accounts/liabilities-due (§CORE-17, batch 10).

The §CORE-17 audit ranked this report third worst of the 136, for a reason the screen says out loud:
*"Composed from the balance-sheet, payroll and store-lease machinery — never a second derivation."*
Every figure on it is assembled from ANOTHER report's derivation — the distributor payables off the
mig-933/954 balance-sheet machinery, payroll and its tax twin off the StoreOps payroll path, rents
and premiums off the mig-946 lease helpers — and until now a reader was shown six totals with no way
to the stores, the people or the leases inside them. The composition being honest made it no more
visible.

WHAT EACH TILE OPENS TO, AND WHAT IT NAMES
──────────────────────────────────────────
Unlike the two statement screens in the same batch, this endpoint reads LIVE and therefore KNOWS
which feed answered — so these panels name the real one, per row:

- **Owed to the distributor** and **due this week** — the per-store rows the endpoint already serves,
  and the company-wide remainder as a row of its own rather than a figure hidden in a total. The feed
  is the one the RESOLVED BASIS chose (`PAYABLE_BASIS_FEEDS`, keyed on the stored config value the
  endpoint reports as `distributor.basis` — never a branch on a carrier, RULE TWO). An unknown basis
  names NO table and says so.
- **Payroll due this week** and **payroll tax due** — one row per person per pay period, out of
  `liabilities_due.aggregate_payroll`'s OWN fold (the loop that produces the totals, extended to keep
  its cells — not a second arithmetic). Each row names the table the HOURS came from: the time clock
  where they clocked in, the hand-entered hours where somebody typed them, and the schedule where
  `payroll_raw`'s scheduled-hours fallback answered because there was no punch at all. That last one
  is why provenance is read per row here: three people on one tile can have three different answers.
- **Rents due this week** and **insurance premiums due** — one row per lease occurrence. A store whose
  rent is not set is SHOWN, with its reason, and NOT counted — which is exactly what the report does
  (`sum_known` excludes it and counts it separately). A rent nobody recorded is not a rent of $0.00.

THE TAX ROWS NAME THE WITHHOLDING SETUP, and say in words why. `payroll_tax_estimate.compute_pay`
reads each person's W-4 settings to decide what is withheld, and `payroll_raw` consults
`payroll_settings` for every employee — filling its own defaults where no row exists. So the table is
named on the tax rows and the panel states that a person with no setup row was estimated on the
defaults. The tax figures are ESTIMATES by construction: the panel says that too, because a reader
owed an estimate should know it is one.

**THE SCREEN FILTERS ITS OWN ROWS, SO THE PANELS CARRY THEIR ROW KEYS** (§CORE-17.3d). Every filter
on this report is client-side over the already span-scoped rows, so each row carries the store it
belongs to and the panel is narrowed through `lib/drill-narrow` by the SAME predicate the tables
filter by — and each tile then reads its number OFF its own narrowed panel. That closes a live defect
this screen had: under a store or market filter the two distributor tiles kept showing the
UNFILTERED total while the table beneath them showed the filtered rows, and the rent and insurance
tiles narrowed. One fold now serves both, so they cannot disagree again.

A company-wide row names no store and therefore cannot be filtered: `narrowPanel` keeps a row with no
key rather than silently shrinking the figure, which is the honest behaviour and the same one the
table has (it shows the company-wide line whatever is selected).

💰 MOVES NO MONEY. Read-only presentation of figures the endpoint already served. PURE: no I/O, no
framework import, no clock, no org / carrier / tenant / product name (RULE TWO).
Registered in docs/SYSTEM_DATA_FLOW_INDEX.md §CORE-17.3j.
"""
from app.modules.commcalc import data_lineage_registry as _lineage
from app.modules.core import report_drilldown

#: WHICH FEED THE RESOLVED DISTRIBUTOR-PAYABLE BASIS READ. Keyed on the stored config value
#: (`balance_sheet.resolve_payable_basis`'s own vocabulary, which the endpoint reports as
#: `distributor.basis`), so a new basis is a ROW here plus its derivation, never a branch in a
#: report. An unrecognised basis resolves to NO table and the drill reports an untraceable origin —
#: the same posture as `data_lineage_registry.billpay_processor_feed` (§CORE-17.3a).
PAYABLE_BASIS_FEEDS = {
    "marketplace_due": ("raw_ma_daily_tx",),
    "asset_ledger": ("asset_ledger",),
}

#: Where a person's pay rate and their identity live. Named on every payroll row: the gross cannot be
#: computed without it. DEREFERENCED from the lineage registry (`EMPLOYEE_ROSTER`), which is the one
#: home for that name — this file held a literal copy of it until batch 24 re-keyed it, exactly as
#: batch 20 re-keyed the time clock and batch 18 the schedule below.
EMPLOYEES = _lineage.EMPLOYEE_ROSTER
#: The three places HOURS can come from, named only where they answered for that person. The TIME
#: CLOCK is DEREFERENCED from the lineage registry (`TIME_CLOCK`), which is the one home for "which
#: relation records an hour somebody actually worked" — this file held a literal copy of it until
#: batch 20 re-keyed it, exactly as batch 18 re-keyed the schedule two lines below.
TIMELOG = _lineage.TIME_CLOCK
#: Hand-entered hours. DEREFERENCED from the lineage registry (`MANUAL_HOURS`) as of batch 24, for
#: the same reason as the two names around it.
MANUAL_HOURS = _lineage.MANUAL_HOURS
#: The schedule. DEREFERENCED from the lineage registry (`SCHEDULE`), which is the one home
#: for "which relation records a store's scheduled day" — this file and
#: `commcalc/targets_drilldown` each held a literal copy until batch 18 re-keyed both.
SHIFTS = _lineage.SCHEDULE
#: What decides the withheld half of the tax estimate.
PAYROLL_SETTINGS = "payroll_settings"
#: Where a store's rent and its insurance premium are recorded.
LEASE = "store_lease"

#: The payroll row's own word for "there was no punch at all, so the schedule answered" —
#: `payroll_raw`'s `basis` field, dereferenced rather than re-spelled.
SCHEDULED_BASIS = "scheduled"

_COMPANY_WIDE_LABEL = "Company-wide — accounts not mapped to a store"

_PAYROLL_NOTE = ("One row per person in each pay period whose payday falls inside the week. The "
                 "hours each row names are the ones that actually answered for that person: the "
                 "time clock, hand-entered hours, or the schedule where there was no punch at all.")

_TAX_NOTE = ("An ESTIMATE, not a recorded liability: employer FICA plus everything withheld, as the "
             "payroll tax calculator computes it from these hours and each person's withholding "
             "setup. Where somebody has no setup recorded the calculator's defaults answered.")

_RENT_NOTE = ("One row per store per month whose rent-due window falls inside this week. A store "
              "whose rent is not set is shown WITHOUT a figure and is not counted — the report "
              "counts those stores separately, because a rent nobody recorded is not a rent of "
              "$0.00.")

_INSURANCE_NOTE = ("One row per premium occurrence inside this week, per the store's own recorded "
                   "premium and frequency.")


# ── the payload's own row lists, named rather than rebuilt ────────────────────────────────────────

def _store_rows(block):
    """A distributor block's per-store rows, plus its company-wide remainder as a ROW. The remainder
    is a figure the screen shows in a line of its own, so it belongs in the panel as a row of its
    own — folded into the total and left unexplained is how it became invisible."""
    block = block or {}
    out = [dict(r or {}) for r in (block.get("by_store") or [])]
    cw = report_drilldown.money(block.get("company_wide"))
    if cw:
        out.append({"store": None, "market": None, "amount": cw, "company_wide": True})
    return out


def _payroll_rows(payload):
    """Every person in every pay period whose payday falls this week — `aggregate_payroll`'s own
    cells, with the period they belong to carried alongside so a row can be read and found again."""
    out = []
    for p in ((payload or {}).get("payroll") or {}).get("due") or []:
        for r in (p or {}).get("rows") or []:
            out.append(dict(r or {}, _payday=(p or {}).get("payday"),
                            _start=(p or {}).get("start"), _end=(p or {}).get("end")))
    return out


def _sum(values):
    """Σ of figures THE ENDPOINT ALREADY SERVED. Not a fold of rows: the screen prints this same sum
    over the same per-period totals, and the tie-out below is what checks the rows against it."""
    return round(sum(report_drilldown.money(v) for v in values), 2)


def _payroll_total(payload, field):
    due = ((payload or {}).get("payroll") or {}).get("due") or []
    if not ((payload or {}).get("payroll") or {}).get("allowed"):
        return None
    if field == "gross":
        return _sum([(p or {}).get("gross_total") for p in due])
    return _sum([((p or {}).get("tax") or {}).get("total") for p in due])


def _gated(block, key):
    """A section the server's gate refused carries no dollars at all, so it has no figure and offers
    no panel — the restriction is already stated on the screen. Fail closed, here too."""
    block = block or {}
    return block.get(key) if block.get("allowed") else None


# ── labels, fields and provenance, per row ────────────────────────────────────────────────────────

def _store_label(r):
    r = r or {}
    if r.get("company_wide"):
        return _COMPANY_WIDE_LABEL
    return " · ".join(str(x) for x in (r.get("store") or "(no store)", r.get("market") or "")
                      if str(x or "").strip())


def _store_fields(r):
    r = r or {}
    return {"store": None if r.get("company_wide") else r.get("store"),
            "market": None if r.get("company_wide") else r.get("market"),
            "detail": _COMPANY_WIDE_LABEL if r.get("company_wide") else None}


def _payable_tables(basis):
    """The feed the RESOLVED basis read — the same answer for every row on these two tiles, because
    one basis answered the whole question. An unrecognised basis names nothing."""
    tables = PAYABLE_BASIS_FEEDS.get(str(basis or "").strip()) or ()

    def read(_r):
        return tables
    return read


def _payable_reason(basis):
    known = bool(PAYABLE_BASIS_FEEDS.get(str(basis or "").strip()))

    def read(_r):
        return None if known else "feed_unresolved"
    return read


def _person_label(r):
    r = r or {}
    return " · ".join(str(x) for x in (
        r.get("name") or r.get("employee_id") or "(unnamed)",
        "payday %s" % (r.get("_payday") or "?"),
    ))


def _person_fields(r):
    r = r or {}
    return {"store": r.get("store"), "market": None,
            "detail": "%s hrs · %s" % (r.get("total_hours"), r.get("basis") or "hours")}


def _hours_tables(r):
    """WHICH TABLE ANSWERED FOR THIS PERSON'S HOURS — read per row, because three people on one tile
    can have three different answers. `payroll_raw` falls back to the SCHEDULE for somebody with no
    punch and no hand-entered hours, and that row's gross is an estimate off a shift nobody worked
    yet; saying "the time clock" for it would be false."""
    r = r or {}
    out = [EMPLOYEES]
    if report_drilldown.money(r.get("clocked_hours")):
        out.append(TIMELOG)
    if report_drilldown.money(r.get("manual_hours")):
        out.append(MANUAL_HOURS)
    if str(r.get("basis") or "") == SCHEDULED_BASIS:
        out.append(SHIFTS)
    return tuple(out)


def _tax_tables(r):
    return _hours_tables(r) + (PAYROLL_SETTINGS,)


def _lease_label(r):
    r = r or {}
    return " · ".join(str(x) for x in (
        r.get("store_code") or "(no store)",
        r.get("month") or r.get("due_date") or "",
    ) if str(x or "").strip())


def _rent_fields(r):
    r = r or {}
    return {"store": r.get("store_code"), "market": r.get("market"),
            "detail": "due %s → %s" % (r.get("due_start"), r.get("due_end"))}


def _insurance_fields(r):
    r = r or {}
    return {"store": r.get("store_code"), "market": r.get("market"),
            "detail": " · ".join(str(x) for x in (r.get("due_date"), r.get("company"),
                                                  r.get("frequency")) if x)}


def _amount_recorded(r):
    return (r or {}).get("amount") is not None


def _amount_reason(r):
    return None if _amount_recorded(r) else "amount_not_recorded"


# ── THE CATALOGUE — one declaration, six figures ──────────────────────────────────────────────────

def catalogue(*, basis=None):
    """Every figure on the tile strip, declared: which of the payload's own row lists it was folded
    from, which field holds a row's contribution, which tables answered, and which rows the figure
    counts. The declaration computes nothing — `rows` and `total` are accessors over the payload the
    endpoint already built."""
    store_common = {"row_label": _store_label, "keys": ("store",), "fields": _store_fields,
                    "schema": "commcalc", "value": "amount"}
    person_common = {"row_label": _person_label, "keys": ("employee_id",),
                     "fields": _person_fields, "schema": "public"}
    lease_common = {"row_label": _lease_label, "keys": ("store_code",), "schema": "storeops",
                    "value": "amount", "in_figure": _amount_recorded, "reason": _amount_reason}
    return (
        {"key": "distributor_outstanding", "label": "Owed to the distributor (outstanding)",
         "rows": lambda p: _store_rows(((p or {}).get("distributor") or {}).get("outstanding")),
         "total": lambda p: (((p or {}).get("distributor") or {}).get("outstanding") or {})
             .get("total"),
         "tables": _payable_tables(basis), "reason": _payable_reason(basis),
         "note": ("Every store with an open balance right now, plus the accounts that are not mapped "
                  "to a store. This is the same derivation the Balance Sheet books, asked for today."),
         **store_common},
        {"key": "distributor_due_week", "label": "Distributor payments due this week",
         "rows": lambda p: _store_rows(((p or {}).get("distributor") or {}).get("due_this_week")),
         "total": lambda p: (((p or {}).get("distributor") or {}).get("due_this_week") or {})
             .get("total"),
         "tables": _payable_tables(basis), "reason": _payable_reason(basis),
         "note": "Only the rows whose own due date falls inside this week.",
         **store_common},
        {"key": "payroll_due_gross", "label": "Payroll due this week (gross)",
         "rows": _payroll_rows, "total": lambda p: _payroll_total(p, "gross"),
         "value": "gross", "tables": _hours_tables, "note": _PAYROLL_NOTE, **person_common},
        {"key": "payroll_due_tax", "label": "Payroll tax due this week",
         "rows": _payroll_rows, "total": lambda p: _payroll_total(p, "tax"),
         "value": "tax_total", "tables": _tax_tables, "note": _TAX_NOTE, **person_common},
        {"key": "rents_due", "label": "Rents due this week",
         "rows": lambda p: ((p or {}).get("rents") or {}).get("rows") or [],
         "total": lambda p: _gated((p or {}).get("rents"), "total"),
         "tables": (LEASE,), "fields": _rent_fields, "note": _RENT_NOTE, **lease_common},
        {"key": "insurance_due", "label": "Insurance premiums due this week",
         "rows": lambda p: ((p or {}).get("insurance") or {}).get("rows") or [],
         "total": lambda p: _gated((p or {}).get("insurance"), "total"),
         "tables": (LEASE,), "fields": _insurance_fields, "note": _INSURANCE_NOTE, **lease_common},
    )


def drilldowns(payload, *, cap=None):
    """PURE: one panel per tile, over the payload the endpoint has already assembled.

    A figure the endpoint did not produce — a section its gate refused, a basis that is off — has no
    panel at all rather than a panel asserting $0.00 behind nothing, which is `tiles()`'s own rule
    applied to six different row lists. Each panel's figure is the endpoint's OWN total, so the
    tie-out is a real comparison: the rows and the figure come from different places and can disagree.
    """
    payload = payload or {}
    basis = ((payload.get("distributor") or {}).get("basis"))
    out = {}
    for spec in catalogue(basis=basis):
        spec = dict(spec)
        key, label = spec.pop("key"), spec.pop("label")
        rows, total = spec.pop("rows")(payload), spec.pop("total")(payload)
        if total is None:
            continue
        spec.setdefault("fields", None)
        out[key] = report_drilldown.tile_panel(
            label, total, rows, cap=cap or report_drilldown.ROW_CAP, **spec)
    return out
