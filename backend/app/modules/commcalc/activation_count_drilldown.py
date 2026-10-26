"""WHERE DID THIS FIGURE COME FROM — the declaration for **Activations** (§CORE-17, batch 22).

  /commcalc/activations   the POS Activation Details report as the activation BASIS OF TRUTH: one
                          row per distinct device, rolled up by market and by store.

ITS OWN FAMILY, ON PURPOSE. Every figure on this screen is a COUNT OF DISTINCT DEVICES read out of
ONE relation — the self-serve custom-import capture the Activation Details export lands in. That is
not the grain any other drill-down in the programme folds: the sales-outcome families fold retail
sales LINES out of the sales union (§CORE-17.3r / §CORE-17.3u), the executive family folds a store
row of the calculated snapshot (§CORE-17.3q), and this report exists precisely BECAUSE those counts
and this one can disagree — reconciling the two is the panel at the bottom of the page. Folding this
report through any of their catalogues would assert that a sales relation produced a figure the POS
export produced.

HOW MANY GRAINS — MEASURED OFF THE PANELS, NOT ASSUMED (§CORE-17.3l)
───────────────────────────────────────────────────────────────────
**ONE.** A grain fixes the ROW, the UNIT and the PROVENANCE, and this report has one of each:

  1. a STORE ROW's DEVICE COUNT   count, out of the custom-import capture the Activation Details
                                  export lands in.

The By-Market table is NOT a second grain, and it gets no panel at all — for a reason that is a
DEFECT rather than a ruling. A market row's figure is Σ of the SAME store rows carrying its market
(the producer accumulates the store row, the market row and the grand total in one pass over the
same deduplicated devices), so a market panel would be this same catalogue over a subset of the same
row shape. What stops it is that **the producer destroys each market row's own label** before the
payload is built (defect 3), so there is no market for a panel to be opened from and nothing a panel
could honestly name as the subject it folded. Reported, not worked around: inventing the grouping
back would be a second derivation of a fold the report itself lost.

WHAT GETS NO PANEL HERE, AND WHICH RULING SAYS SO
─────────────────────────────────────────────────
  · **"DEVICES COUNTED"** — it is the SAME MEASUREMENT as "Incl. Upgrade", printed twice under two
    labels (defect 1). Serving both would be two panels for one figure, which is the duplicate
    derivation the index rules forbid; the chip links to the one that is served instead.
  · **THE RECONCILIATION PANEL** — its figures come from a DIFFERENT endpoint
    (`GET /commcalc/metric-recon/{period}`) comparing this report against the sales-derived count,
    and its per-store rows are already printed in full underneath it. A panel there would explain a
    figure this payload does not carry, which is the §4e grain defect; and the report's own side of
    that comparison is exactly what the panels above it open.
  · **EVERY ROW OF THE BY-MARKET TABLE** — defect 3: the market each row is about is blanked by the
    producer, so the report prints a dash where the market name belongs and there is no subject a
    panel could be keyed on. The company figures above it, which those rows sum to, all drill.
  · **THE TWO BUCKETS WITH NO COLUMN ON THE SCREEN** — the hidden upgrade family (excluded from the
    headline by design and shown in no column) and the catch-all bucket. A panel for a figure the
    screen does not print would be evidence for nothing.
  · **THE DEALER-CODE MAPPING TABLE** — a work queue, not a report figure (§CORE-17.3i). Its
    per-code activation counts are a different payload's and the table beside each is the list.
  · **THE ACTIVATION-BASIS TOGGLE** — a config control.
  · **A 0 OVER ZERO COUNTED ROWS**: no panel, through the one home's own `served_tiles` rule,
    because such a panel ties 0 against 0 and reads as a measurement. A NON-zero figure over no
    counted rows KEEPS its panel so the tie-out publishes the disagreement.

A PANEL EXPLAINS THE FIGURE, NOT THE TABLE (§CORE-17.3o), DECIDED BY READING THE PAGE
────────────────────────────────────────────────────────────────────────────────────
`/commcalc/activations` is **SERVER-FOLDED and NOT narrowed**. It prints the server's own `total`
object as served and folds nothing: its three controls are a QUERY PARAMETER (`include_upgrade`), a
TAB that chooses which of the two server-folded lists to print, and a refresh. `lib/drill-narrow`
must not appear in this page; `harness_batch22_drilldown.py` §G fails the build in both directions
off the page's own text.

THREE LIVE DEFECTS, REPORTED, NONE FIXED, NO FIGURE MOVED. Each carries a regression check.

 1. **"INCL. UPGRADE" AND "DEVICES COUNTED" ARE THE SAME NUMBER.** `activation_counts` increments
    `total_with_upgrade` once for every device it counts, unconditionally, and `counted` once for
    every device it counts — so the two are equal by construction on every period, for every tenant,
    and the screen prints them side by side as if they were two findings. REPORTED rather than
    changed: removing a chip is a screen decision, and the panel refusal above says which figure the
    reader is actually opening.
 2. **A DEVICE WHOSE DEALER CODE RESOLVES TO NO STORE IS COUNTED UNDER A PLACEHOLDER ROW.** The
    producer buckets such a device under a single dash row and gives it a blank market, so it lands
    in the company total, in the placeholder store row and in the `(no market)` market row. Those
    rows are REAL rows of this report and are counted here exactly as the report counts them — the
    panel names the placeholder in its own words so a reader can see how much of a total is devices
    nobody has mapped yet, and the page's own Dealer Code queue is where that gets fixed. Nothing is
    hidden and nothing is dropped: dropping them would make the panel disagree with the report.
 3. **EVERY ROW OF THE BY-MARKET TABLE HAS ITS MARKET NAME DESTROYED BEFORE IT IS SERVED.**
    `activation_counts._blank(label, key="market")` builds its row as a dict LITERAL whose first key
    is `key` and whose second is the fixed `"market": ""` — so for a market row the two are the SAME
    key, the later one wins, and the label is overwritten with the empty string. The roll-up itself
    is correct (the dictionary it is accumulated into is keyed on the market), but every row the
    endpoint returns under `markets` carries a blank market, and the screen prints a dash in place of
    every market name on its By Market tab. REPRODUCED in §F from the producer's own shape and
    REPORTED, not repaired: the repair is one line in the producer and it changes what that report
    renders, which is surfaced for the owner rather than slipped into a read-only drill-down PR. It
    is also why the By-Market rows get no panel — a panel cannot name a subject the report has thrown
    away.

💰 MOVES NO MONEY. Read-only presentation of counts the producer already folded. Books nothing, pays
nothing, writes nothing, and computes no business figure of its own.
PURE: no I/O, no framework import, no clock, no org / carrier / tenant / product / store name
(RULE TWO), and no dollar literal, rate or threshold of its own.
Registered in docs/SYSTEM_DATA_FLOW_INDEX.md §CORE-17.3v.
"""
from app.modules.commcalc import activation_bucketing as _act
from app.modules.commcalc import data_lineage_registry as _lineage
from app.modules.core import report_drilldown

#: WHERE THE ACTIVATION DETAILS EXPORT LANDS — the self-serve custom-import capture, and the ONE
#: provenance on this whole report. DEREFERENCED from the lineage registry, the one home for a
#: feed's name.
CUSTOM_CAPTURE = _lineage.CUSTOM_CAPTURE

SCHEMA = "commcalc"

#: WHICH PAYLOAD COLUMN HOLDS EACH ACTIVATION BUCKET'S COUNT. DEREFERENCED from
#: `activation_bucketing`, which is the one home for this report's bucket vocabulary and already
#: owns which buckets the headline excludes. It was spelled as a literal map inside
#: `commcalc/router._ACT_BUCKET_FIELD`, and that copy was re-keyed onto this constant in the SAME
#: commit (string-identical values, no fold changed) — naming a fact is half a design fix and
#: deleting every copy is the other half (§19.18).
BUCKET_FIELD = _act.BUCKET_FIELD

#: WHICH BUCKETS THE HEADLINE TOTAL EXCLUDES, in the producer's own words — read so a NOTE can say
#: what the headline leaves out, in the producer's own vocabulary. This file compares nothing
#: against it.
TOTAL_ACTIVATION_EXCLUDED = _act.TOTAL_ACTIVATION_EXCLUDED

#: WHICH FIGURES CAN BE EXPLAINED AT ALL — the one home's own rule (§CORE-17.3u), bound by identity.
_served = report_drilldown.served_tiles

#: The two headline keys the producer folds. The PAYLOAD's own spellings, read here once so the page
#: and this declaration ask for a panel by the same string.
TOTAL_ACTIVATION = "total_activation"
TOTAL_WITH_UPGRADE = "total_with_upgrade"

#: THE BUCKETS THE SCREEN ACTUALLY PRINTS A COLUMN FOR, as that screen's own column list prints
#: them. Deliberately NOT every entry of `BUCKET_FIELD`: the hidden upgrade family has no column by
#: design (it is excluded from the headline like `Upgrade` and shown in no column of its own), and
#: the catch-all bucket has none either. A panel for a figure the screen does not show would be
#: evidence for nothing, so those two get no key at all.
SHOWN_BUCKETS = ("New Activation", "Port", "BYOD", "Tablet", "Home Internet", "Edge", "Upgrade")


# ── NOTES — what each panel is, in the words the screen already uses ───────────────────────────

_ROW_NOTE = (
    "One row per store, exactly as the By Store table lists them, and over the whole period the "
    "report was asked for. Every figure is a count of DISTINCT DEVICES: one device spans several "
    "lines in the export — its service plan and its insurance share a serial number — and the "
    "report counts it once.")

_FEED_NOTE = (
    " The rows come out of the POS Activation Details report as it was uploaded or routed in. "
    "Cancelled, voided and returned lines are in none of these figures, and a device whose dealer "
    "code is not linked to a store yet is counted under a placeholder row rather than dropped — the "
    "Dealer Code queue on this page is where that gets linked.")

_HEADLINE_NOTE = _ROW_NOTE + _FEED_NOTE + (
    " This is the headline definition: it counts every bucket EXCEPT the upgrade families, which is "
    "the POS's own definition of a Total Activation and the one the two sources reconcile on.")

_WITH_UPGRADE_NOTE = _ROW_NOTE + _FEED_NOTE + (
    " This one counts every device the report counted, upgrades included — which is also what the "
    "Devices counted chip shows, because the two are the same measurement under two labels.")

_BUCKET_NOTE = _ROW_NOTE + _FEED_NOTE + (
    " Counted here is only this one contract type. Which type a device falls under is this company's "
    "own classification of the export's contract-type wording, so a column that reads low may be a "
    "classification rather than a sales result.")

# ══════════════════════════════════════════════════════════════════════════════════════════════════
# §A  READERS over one store row. Accessors and names only.
# ══════════════════════════════════════════════════════════════════════════════════════════════════

def _column(field):
    """PURE: a reader for one already-folded count on one store row."""
    def _read(r):
        return (r or {}).get(field)
    return _read


def market_of(row):
    """PURE: the market a store row carries, as the row carries it. BLANK where the store's market
    resolved nowhere, which is a real state of this payload and not an absence of one."""
    return str((row or {}).get("market") or "").strip()


def _label(r):
    """PURE: the store exactly as the table names it."""
    v = str((r or {}).get("store") or "").strip()
    return v or None


def _fields(r):
    """PURE: what else a reader needs beside one store's row."""
    r = r or {}
    return {"market": (market_of(r) or None),
            "total activation": r.get(TOTAL_ACTIVATION),
            "incl. upgrade": r.get(TOTAL_WITH_UPGRADE),
            "upgrades": r.get(BUCKET_FIELD.get("Upgrade"))}


# ══════════════════════════════════════════════════════════════════════════════════════════════════
# §B  THE CATALOGUE — one store row, one unit, one provenance.
# ══════════════════════════════════════════════════════════════════════════════════════════════════

def catalogue():
    """PURE: every figure this report prints, folded over its own store rows — the two headline
    totals and one panel per contract-type column the screen shows."""
    common = {"row_label": _label, "keys": ("store",), "fields": _fields, "schema": SCHEMA,
              "tables": (CUSTOM_CAPTURE,), "unit": report_drilldown.UNIT_COUNT}
    out = [
        dict(common, key=TOTAL_ACTIVATION, total=TOTAL_ACTIVATION, label="Total activation",
             value=_column(TOTAL_ACTIVATION), column=TOTAL_ACTIVATION, note=_HEADLINE_NOTE),
        dict(common, key=TOTAL_WITH_UPGRADE, total=TOTAL_WITH_UPGRADE,
             label="Total including upgrades", value=_column(TOTAL_WITH_UPGRADE),
             column=TOTAL_WITH_UPGRADE, note=_WITH_UPGRADE_NOTE),
    ]
    for bucket in SHOWN_BUCKETS:
        field = BUCKET_FIELD.get(bucket)
        if not field:
            continue
        out.append(dict(common, key=field, total=field, label=bucket, value=_column(field),
                        column=field, note=_BUCKET_NOTE))
    return tuple(out)


#: EVERY FIGURE KEY THIS DECLARATION SERVES, derived from the catalogue it serves them from rather
#: than listed a second time.
SERVED_KEYS = tuple(t["key"] for t in catalogue())


def activation_drilldowns(payload, *, cap=None):
    """PURE: every panel for `GET /commcalc/activation-counts/{period}`.

    The keys are the payload's own field names, so the page can offer a link beside every number it
    prints that a row list exists for. Each figure is the payload's own `total` entry dereferenced,
    so a panel can never be a second opinion about the figure above it.

    THE BY-MARKET ROWS GET NO PANEL, AND THE REASON IS A DEFECT RATHER THAN A RULING — see defect 3:
    the producer destroys each market row's own label, so there is no market for a panel to be
    opened from and nothing a panel could honestly name as the subject it folded.

    A period the Activation Details export has not landed for has no store rows and drills to
    nothing.
    """
    payload = payload or {}
    rows = [r for r in (payload.get("stores") or ()) if r]
    totals = dict(payload.get("total") or {})
    if not rows or not totals:
        return {}
    return dict(_served(catalogue(), rows,
                        {k: v for k, v in totals.items() if k in SERVED_KEYS},
                        cap or report_drilldown.ROW_CAP))
