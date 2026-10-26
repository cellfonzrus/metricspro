"""WHERE DID THIS FIGURE COME FROM — the declaration for DID THE DISTRIBUTOR PAY US WHAT WE EARNED
(§CORE-17, batch 23).

  /accounts/recon   Distributor Credit-Memo Reconciliation — the weekly incentive credits that
                    ARRIVED against the residual the carrier's own report says was EARNED.

ITS OWN FAMILY, ON PURPOSE. One question — *did the money that arrived match the money that was
earned, and where did it not?* — folded at ONE row (a store) in ONE unit (money) out of TWO
relations. It is deliberately NOT folded through `commcalc/carrier_pay_drilldown.py`, which owns
the carrier-earned-versus-employee-paid grain (§CORE-17.3k): that family compares what a CARRIER
earned against what a REP was PAID, out of the commission snapshot. This one compares what the
DISTRIBUTOR CREDITED against what the RESIDUAL REPORT earned, and the relations have nothing in
common. Folding it through that catalogue would claim a commission snapshot produced a figure the
credit memos produced.

**AN ACCUSATION NAMES ITS EVIDENCE** (§CORE-17.3f). This report's output is the word "underpaid"
beside a dollar figure, per store, and it writes those findings into the platform's flag stream. A
figure that carries that word and cannot name its rows is the shape §CORE-17 exists to end.

HOW MANY GRAINS — MEASURED OFF THE PANELS, NOT ASSUMED (§CORE-17.3l / §CORE-17.3n)
─────────────────────────────────────────────────────────────────────────────────
ONE row shape — a store — and THREE grains on it, separated by PROVENANCE ALONE:

  1. a STORE's CREDITED MONEY   money, out of the scraped credit memos
  2. a STORE's EARNED MONEY     money, out of the residual report — the SAME row and the SAME unit,
                                a different relation
  3. a STORE's DIFFERENCE       money, out of BOTH — folded PER ROW by the producer, which is what
                                makes a difference drillable at all (§CORE-17.3k)

WHAT GETS NO PANEL HERE, AND WHICH RULING SAYS SO
─────────────────────────────────────────────────
  · **"PER-STORE ATTRIBUTION %"** — a RATE (attributed ÷ total). §CORE-17.3h: no panel, and the two
    figures it divides are offered instead.
  · **THE STATUS CHIP** — a verdict against a tolerance, not a folded figure. The figure it judges
    (the difference) drills, and the tolerance is printed beside it.
  · **THE PER-STORE MEMO LIST AND THE BY-DAY ACCRUAL** — already the source rows, printed directly
    under the row they belong to. There is nothing above them to drill from.
  · **THE UNMATCHED-REP LIST** — the report's own list of rep names no store claimed, printed in
    full. Its figures are the rows.
  · **A 0 OVER ZERO COUNTED ROWS** — refused by the one home's `served_tiles`: a period with no
    memos and no residual rows drills to nothing rather than to a tie of 0 against 0.

THE ROW THE REPORT'S OWN FIGURE NEEDS, AND WHY IT IS NOT A RECOMPUTATION
───────────────────────────────────────────────────────────────────────
The company-wide earned figure counts **every** residual row, while the per-store rows count only
the rows whose rep name reached a store — the report says so itself, in words, and calls the
per-store side best-effort. So a panel folded over the store rows alone would come back NOT TIED
against the report's own headline, by exactly the money nobody could attribute.

That money is a REAL ROW of this report: the producer already folds it and the screen already prints
it ("… unattributed (still counted company-wide)"). So the panel carries it as ONE MORE ROW, taken
from the payload's own `mi_unattributed` — dereferenced, never re-derived — naming the residual
report with NO store key, because there is no store to key it on. On the difference panel the same
row is counted NEGATIVE, exactly as `statement_drilldown` counts the second side of the balance
sheet's imbalance, because the difference is credited minus earned and this money is on the earned
side. The panels then tie against the figures the screen prints, and the reason the store rows alone
do not is readable in the panel instead of being averaged away.

💰 MOVES NO MONEY. Read-only presentation of figures the producer already folded, dereferenced.
PURE: no I/O, no framework import, no clock, no org / carrier / tenant / product / store name
(RULE TWO), and no dollar literal, rate or threshold of its own.
Registered in docs/SYSTEM_DATA_FLOW_INDEX.md §CORE-17.3w.

───────────────────────────────────────────────────────────────────────────────────────────────────
THREE LIVE DEFECTS, REPORTED, NONE REPAIRED, NO FIGURE MOVED. Each carries a regression check in
`harness_batch23_drilldown.py` driven off the real producer.

 1. **THE REPORT NAMES ONLY THE FIFTEEN LARGEST UNATTRIBUTED REPS AND SAYS NOTHING ABOUT THE REST —
    A SILENT ROW CAP ON AN ACCUSATION'S EVIDENCE.** `reconcile` sorts the unattributed rep names by
    dollars and keeps the first fifteen, with no count and no remainder beside them, so a reader
    cleaning up the rep map cannot tell whether they have seen all of it. The unattributed TOTAL is
    served whole, so the panel's figure is complete and its single row carries that total; the note
    says in the reader's own words that the report names only the largest few behind it. REPORTED,
    not worked around: re-deriving the full list beside the report would be a second fold of a
    figure the report itself truncated.
 2. **A CREDIT MEMO WHOSE STORE RESOLVES NOWHERE IS BUCKETED UNDER A PLACEHOLDER STORE ROW.** The
    producer puts such a memo under a single labelled row rather than dropping it, so it lands in the
    company total and in that placeholder row. Those are REAL rows of this report and the panels
    count them exactly as the report does, naming the placeholder in the report's own words — a
    reader can then see how much of the credited total is money nobody has placed yet. Dropping them
    would make the panel disagree with the figure above it.
 3. **THE DIFFERENCE IS JUDGED AGAINST A TOLERANCE THE READER CHOOSES, AND THE FLAGS WRITTEN FROM IT
    ARE NOT.** `sync_flags` re-runs the reconciliation with whatever tolerance the request carried,
    so the same month can be flagged differently depending on the number in a text box on the
    screen. The panels explain the FIGURE and never the verdict, so nothing here depends on the
    tolerance; it is written down because a drill-down that showed the figure while the verdict moved
    underneath it would be evidence for the wrong thing.
"""
from app.modules.commcalc import data_lineage_registry as _lineage
from app.modules.core import report_drilldown

#: WHERE EACH FIGURE WAS ACTUALLY READ OUT OF. DEREFERENCED from the lineage registry, the one home
#: for a relation's name.
CREDIT_MEMOS = _lineage.DISTRIBUTOR_CREDIT_MEMOS
RESIDUAL_REPORT = _lineage.MI_RESIDUAL

SCHEMA = "commcalc"

#: WHICH FIGURES CAN BE EXPLAINED AT ALL — the one home's own rule, bound by IDENTITY.
_served = report_drilldown.served_tiles

#: THE PRODUCER'S OWN COLUMN SPELLINGS, so a reader opening a row can find the cell the figure came
#: out of and not merely the relation. The earned side is TWO columns added together — the report
#: pays them combined and the producer sums them per row — which is stated as such rather than
#: reduced to one of the two.
MEMO_COLUMN = "grand_total"
EARNED_COLUMNS = "actual_mi_payout + actual_atu_payout"


# ── NOTES — in the words the screen already uses ───────────────────────────────────────────────

_STORE_NOTE = (
    "One row per store, exactly as the By store table lists them, with both sides canonicalised to "
    "the same store spelling so a store cannot split into two rows.")

_PLACEHOLDER_NOTE = (
    " A credit memo whose store matched nothing is kept under its own labelled row rather than "
    "dropped, so it is in this figure and visible.")

_BEST_EFFORT_NOTE = (
    " The earned side carries no store of its own: it is attributed to a store through the rep name "
    "on each row, which the report calls best-effort for that reason. The money no rep name placed "
    "is carried below as its own row, counted, so this figure is the whole of the period and not "
    "only the part that could be placed — and that row is complete even though the report names "
    "only its largest few rep names beside it.")

_MEMO_NOTE = _STORE_NOTE + (
    " Counted here is what the distributor actually credited for the period, excluding the separate "
    "report it pays on its own." + _PLACEHOLDER_NOTE)

_EARNED_NOTE = _STORE_NOTE + (
    " Counted here is what the carrier's residual report says was earned for the period — the two "
    "amounts it pays combined, added per row." + _BEST_EFFORT_NOTE)

_DIFF_NOTE = _STORE_NOTE + (
    " Each row is that store's own difference — credited less earned — so this figure is the sum of "
    "per-store differences and not a difference of two sums. A negative row is a store the "
    "distributor paid LESS than the report says was earned. The unplaced earned money is counted "
    "here too, on the earned side, which is why it appears below with the sign reversed."
    + _PLACEHOLDER_NOTE)

#: HOW THE UNPLACED ROW NAMES ITSELF — the reader's words for "the report says this was earned and
#: no rep name on it reached one of our stores".
UNATTRIBUTED_LABEL = "Earned, but no rep name reached a store"


# ══════════════════════════════════════════════════════════════════════════════════════════════════
# §A  READERS over one store row. Accessors and names only.
# ══════════════════════════════════════════════════════════════════════════════════════════════════

def _column(field):
    """PURE: a reader for one already-folded figure on one row."""
    def _read(r):
        return (r or {}).get(field)
    return _read


def _label(r):
    """PURE: the store exactly as the table names it — or, for the one row that has no store, the
    reader's own words for why it has none."""
    r = r or {}
    if r.get(UNPLACED_FLAG):
        return UNATTRIBUTED_LABEL
    return str(r.get("store") or "").strip() or None


def _keys(r):
    """PURE: the keys that address one row in the relations it names. The unplaced row addresses NO
    store, so it carries a blank the one home drops — a key that is present means something, and
    inventing a store here would be the guess this mechanism exists to refuse."""
    r = r or {}
    if r.get(UNPLACED_FLAG):
        return {}
    return {"store": str(r.get("store") or "").strip()}


def _fields(r):
    """PURE: what else a reader needs beside one row — the report's own verdict and how many memos
    stand behind the credited side, or, for the unplaced row, how much of its own evidence the report
    actually names (defect 1)."""
    r = r or {}
    if r.get(UNPLACED_FLAG):
        return {"rep names the report lists": r.get(UNPLACED_REPS)}
    return {"status": (str(r.get("status") or "").strip() or None),
            "memos": len(r.get("memos") or ())}


# ══════════════════════════════════════════════════════════════════════════════════════════════════
# §B  THE UNPLACED ROW — the report's own figure, carried as a ROW rather than re-derived.
# ══════════════════════════════════════════════════════════════════════════════════════════════════

#: THE FIELDS THIS MODULE ADDS TO ONE SYNTHETIC ROW. Named constants rather than bare strings so the
#: readers above and the builder below cannot drift apart, and prefixed so they can never collide
#: with a field the producer serves.
UNPLACED_FLAG = "_drill_unplaced"
UNPLACED_REPS = "_drill_unplaced_reps"
UNATTRIBUTED_FIELD = "mi_unattributed"


def unplaced_row(company_wide):
    """PURE: the earned money no rep name placed, as ONE ROW in the same shape as the producer's own
    store rows — or `None` when the report records none.

    The amount is the payload's own `mi_unattributed`, DEREFERENCED, and it is put on the EARNED side
    and NEGATED on the difference side, because the difference is credited minus earned. That is the
    construction `statement_drilldown` already uses for the second side of the balance sheet's
    imbalance, so the two halves really do add up to the gap.

    IT IS A ROW, NOT A PATCH ON A BUILT PANEL. Handing it to the one home with the other rows means
    the tie-out, the sources list, the row cap and the true row count are all the one home's own
    arithmetic over the whole set — editing a panel after it was folded would have put this module in
    charge of a capped list it cannot honestly re-total.

    A report that recorded no unplaced money gets no row at all: a $0.00 row would read as a finding.
    """
    amount = (company_wide or {}).get(UNATTRIBUTED_FIELD)
    if amount is None:
        return None
    amount = report_drilldown.money(amount)
    if not amount:
        return None
    reps = (company_wide or {}).get("top_unattributed_reps") or ()
    return {UNPLACED_FLAG: True, UNPLACED_REPS: len(reps), "store": "",
            MEMO_TOTAL: None, EARNED_TOTAL: amount, DIFF: -amount}


def _diff_tables(row):
    """PURE: which relations one row of the DIFFERENCE panel names. A store row's difference is
    between the two, and the unplaced row is earned money only — naming the credit memos on it would
    assert a credit that does not exist."""
    if (row or {}).get(UNPLACED_FLAG):
        return (RESIDUAL_REPORT,)
    return (CREDIT_MEMOS, RESIDUAL_REPORT)


# ══════════════════════════════════════════════════════════════════════════════════════════════════
# §C  THE CATALOGUE — one store row, one unit, three provenances.
# ══════════════════════════════════════════════════════════════════════════════════════════════════

MEMO_TOTAL = "memo_total"
EARNED_TOTAL = "mi_atu_total"
DIFF = "diff"


def catalogue():
    """PURE: every figure the company-wide header prints that is Σ(one field) over the store rows."""
    common = {"row_label": _label, "keys": _keys, "fields": _fields, "schema": SCHEMA,
              "unit": report_drilldown.UNIT_MONEY}
    return (
        dict(common, key=MEMO_TOTAL, total=MEMO_TOTAL, label="Distributor credit memos",
             value=_column(MEMO_TOTAL), column=MEMO_COLUMN, tables=(CREDIT_MEMOS,),
             note=_MEMO_NOTE),
        dict(common, key=EARNED_TOTAL, total=EARNED_TOTAL, label="MI + ATU earned",
             value=_column(EARNED_TOTAL), column=EARNED_COLUMNS, tables=(RESIDUAL_REPORT,),
             note=_EARNED_NOTE),
        # THE DIFFERENCE DRILLS BECAUSE THE PRODUCER FOLDS IT PER ROW (§CORE-17.3k).
        dict(common, key=DIFF, total=DIFF, label="Difference",
             value=_column(DIFF), tables=_diff_tables, note=_DIFF_NOTE),
    )


#: EVERY FIGURE KEY THIS DECLARATION SERVES, derived from the catalogue rather than listed twice.
SERVED_KEYS = tuple(t["key"] for t in catalogue())

#: WHICH FIGURES THE UNPLACED EARNED MONEY IS PART OF. The credited side never holds it: that money
#: is on the earned side of the comparison, and putting it under the memos would be an invented
#: credit.
UNPLACED_IN = (EARNED_TOTAL, DIFF)


def recon_drilldowns(payload, *, cap=None):
    """PURE: every panel for `GET /account/recon/{period}`.

    The keys are the payload's own field names, so the page can offer a link beside every figure it
    prints that a row list exists for. Each figure is the payload's own `company_wide` entry
    DEREFERENCED — a panel can never be a second opinion about the figure above it.

    The three figures are folded over TWO different row lists, which is why this is three calls into
    the one home and not one: the credited figure is the store rows, and the earned figure and the
    difference are those rows PLUS the money no rep name placed (see §B). A panel over the wrong one
    of the two would come back not tied, by exactly that money.

    A period with no memos loaded has no store rows and drills to nothing.
    """
    payload = payload or {}
    rows = [r for r in (payload.get("stores") or ()) if r]
    cw = dict(payload.get("company_wide") or {})
    if not rows or not cw:
        return {}
    cap = cap or report_drilldown.ROW_CAP
    extra = unplaced_row(cw)
    with_unplaced = rows + [extra] if extra is not None else rows
    out = {}
    for spec in catalogue():
        key = spec["key"]
        if key not in cw:
            continue
        out.update(_served((spec,), with_unplaced if key in UNPLACED_IN else rows,
                           {key: cw.get(key)}, cap))
    return out
