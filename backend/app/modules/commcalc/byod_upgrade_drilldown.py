"""WHERE DID THIS FIGURE COME FROM — the declaration for **Brought device then upgrade** (§CORE-17).

  /watchdog/byod-upgrade   every phone line that took a brought-device activation AND an upgrade at
                           one store on one day (§COMMCALC-43), with the carrier money standing
                           against the handset the store sold.

OWNER ASK 2026-10-28, verbatim: *"include the $ amount of the rebate and title it as chargeback
amount, make it one line summary top show the sales rep the store the phone sold and the phone
number meid and the $ amount and then drill down like other menus to get more details of both
transactions"*. "Like other menus" is this mechanism — the shared panel every other report opens —
rather than a panel of this screen's own, which is what it had.

HOW MANY GRAINS — MEASURED OFF THE FIGURES, NOT ASSUMED (§CORE-17.3l)
────────────────────────────────────────────────────────────────────
**TWO**, because two of the figures on this screen are Σ over the same row but out of DIFFERENT
feeds, and §CORE-17.3m says provenance alone separates a grain:

  1. a CASE's RUNG DOLLARS        money, out of the sales feed the period resolved to — what the
                                  store rang on the two receipts.
  2. a CASE's CHARGEBACK AMOUNT   money, out of the carrier's per-line payment feed — what the
                                  carrier has already paid against that handset.

One catalogue over both would make the tie-out assert that the sales feed produced a carrier
payment. They are one module and two declarations, which is what §CORE-17.3l means by six grains
never meaning six files.

WHAT GETS NO PANEL HERE, AND WHICH RULING SAYS SO
─────────────────────────────────────────────────
  · **THE CASE COUNT, THE EVENT COUNTS AND THE NUMBER-DAY COUNT** — the table below them IS that
    list, one row per case. A panel there would be the same rows printed twice (§CORE-17.3h's
    sibling: a figure whose rows are already on the screen is not explained by serving them again).
  · **`numbered_share`** — a RATE. No set of records sums to a quotient, so its tie-out is a check
    that cannot fail (§CORE-17.3h). Its numerator and denominator are the event counts beside it.
  · **`handset_lines` / `box_vocabulary_matched`** — a measurement of the org's CONFIG fit across
    the whole period, not of the cases. Its rows are every activation line in the period, which is
    not this report's row, and §CORE-17.3l forbids a panel whose rows are a different grain from the
    figure's.
  · **THE UNMEASURED-CHARGEBACK COUNT** — it counts the ABSENCE of carrier rows. There is nothing to
    serve behind it but the cases themselves, which the chargeback panel already lists, each with
    its own reason in words.
  · **NOTHING IS WITHHELD WHEN THE AMOUNT IS ABSENT.** The chargeback panel is served even when no
    payment was found, because its rows then NAME each handset and say, in words, which of the four
    absences this is — a link that explains an absence is worth more than no link, and far more than
    a $0.00. What it must never do is pick the wrong one of the four (see `chargeback_reason_for`).
  · **A $0.00 OVER NO COUNTED ROWS** — refused by the one home's own `served_tiles` rule, because
    such a panel ties 0 against 0 and reads as a measurement. A NON-zero figure over no counted rows
    KEEPS its panel so the tie-out publishes the disagreement.

A PANEL EXPLAINS THE FIGURE, NOT THE TABLE (§CORE-17.3o), DECIDED BY READING THE PAGE
────────────────────────────────────────────────────────────────────────────────────
`/watchdog/byod-upgrade` is **SERVER-FOLDED and NOT narrowed**. Its tiles print the server's own
`summary` object as served; the table below them is filtered in the browser by `ReportShell`, and the
tiles do not move when it is. So the panel stays WHOLE and its note says it covers the period — a
narrowed panel would make a tile read a different number under a filter, which is a figure moved.
`lib/drill-narrow` must not appear in this page, and the lock fails the build in both directions off
the page's own text.

WHY THE CHARGEBACK PANEL NAMES A FEED THIS REPORT DOES NOT READ ITSELF
─────────────────────────────────────────────────────────────────────
The amount is not computed here and not read here: §COMMCALC-44 is the one home for "the
device-financing dollars against this device", bound to the org's OWN declarations — the same verdict
the P&L books its reimbursement line on. The panel therefore names that feed and that relation,
because that is where the dollars came from, and a reader who follows it lands on the rows the P&L
is reading. Naming the sales feed instead would be the §CORE-17.3j defect: a figure attributed to the
feed the row was *found* in rather than the feed the figure was *read* from.
"""
from app.modules.core import report_drilldown

SCHEMA = "commcalc"

#: The carrier's per-line payment feed — the relation §COMMCALC-44 reads the chargeback amount out
#: of. Dereferenced from that home rather than spelled here, so a rename travels.
def _carrier_feed():
    from app.modules.commcalc import carrier_device_money as _cdm
    return (_cdm.FEED_TABLE,)


#: Why a case has no chargeback amount. Two shapes, and they are NOT the same fact: one is a handset
#: nobody could identify (the org declared no device vocabulary, or the sale carries no device line),
#: the other is a handset we know and a carrier that has not paid for it yet.
#: FOUR shapes, not two, and the distinction is the whole point. Two of them are facts about the
#: SALE or the CARRIER; the other two are facts about the LOOKUP, and saying "the carrier has not
#: paid for this handset yet" when nothing was ever looked for is a confident statement about an
#: unmeasured thing — the fake-zero class in words rather than in a number (§19.26). Found by the
#: §H lock on 2026-10-28: with no routing declared, every row blamed the feed for an absence nobody
#: had looked for.
REASON_NO_DEVICE = "no handset could be identified on the upgrade, so there is no device to look up"
REASON_NOT_PAID_YET = "the carrier's feed carries no payment against this handset yet"
REASON_NOT_DECLARED = ("your company has not declared which carrier payments are device money, so "
                       "no payment could be called a rebate and none was looked for")
REASON_FEED_UNREAD = ("the carrier's payment feed could not be read for this period, so nothing was "
                      "looked at — this is not a carrier that has paid nothing")


def _rung(row):
    """PURE: a case's contribution to the rung-dollars figure."""
    return (row or {}).get("dollars")


def _chargeback(row):
    """PURE: a case's contribution to the chargeback figure — None when nothing was measured, which
    the one home renders as a row with its reason rather than as a zero."""
    return (row or {}).get("chargeback_amount")


def chargeback_reason_for(source=None):
    """A `reason(row)` closure bound to WHAT WAS LOOKED AT — the state §COMMCALC-44 reported.

    The order matters and is from the outside in: a lookup that never happened is stated before any
    claim about the handset or the carrier, because the inner facts are not known when the outer one
    failed. `source` is the home's own meta (`available`, `configured`); absent, it is treated as a
    lookup that did happen, which is the shape every pure test and fixture passes.
    """
    src = source if isinstance(source, dict) else {}
    unread = src.get("available") is False
    undeclared = (not unread) and src.get("configured") is False

    def reason(row):
        r = row or {}
        if r.get("chargeback_amount") is not None:
            return None
        if unread:
            return REASON_FEED_UNREAD
        if undeclared:
            return REASON_NOT_DECLARED
        return REASON_NO_DEVICE if not r.get("meid") else REASON_NOT_PAID_YET

    return reason


def _row_label(row):
    """PURE: how a case names itself in a panel — the one-line summary the owner asked for, in the
    order he asked for it."""
    r = row or {}
    bits = [r.get("rep") or "no rep named", r.get("store") or "no store",
            r.get("phone_sold") or "handset not identified", r.get("number") or "no number"]
    return " · ".join(str(b) for b in bits)


def _fields(row):
    """PURE: the facts a reader needs to find this case again in either feed."""
    r = row or {}
    return {"day": r.get("day"), "receipts": ", ".join(r.get("receipts") or ()),
            "meid": r.get("meid"), "rep": r.get("rep"), "phone sold": r.get("phone_sold")}


_RUNG_NOTE = ("What the store rang on the two receipts, out of the sales feed this period resolved "
              "to. Covers the whole period — filtering the table below does not move it.")
_CHARGEBACK_NOTE = ("The carrier money already recorded against the handset the store sold, read "
                    "through the one home the P&L's reimbursement line is booked on. It is not a "
                    "charge anybody has made. A case with no amount is one the carrier has not paid "
                    "against yet — a rebate usually lands months after the sale — and is listed "
                    "with that reason rather than counted as nothing.")


def catalogue(sales_table=None, *, source=None):
    """PURE: this report's panels, declared once.

    `sales_table` is the relation the period actually resolved to (the open month reads the daily
    feed, a closed month the archive — §19.16 owns that choice). It is passed in rather than guessed
    so the panel names the feed the figure was really read from.
    """
    common = {"row_label": _row_label, "keys": ("store", "day", "number"), "fields": _fields,
              "unit": report_drilldown.UNIT_MONEY, "schema": SCHEMA}
    return (
        {"key": "dollars", "total": "dollars", "label": "Rung on the flagged receipts",
         "value": _rung, "tables": tuple(t for t in (sales_table,) if t), "column": "ext_price",
         "note": _RUNG_NOTE, **common},
        # THE KEYS DIFFER FROM THE SALES PANEL'S, because the relation does. A case is addressed in
        # the sales feed by the store, the day and the line's number; in the carrier's payment feed
        # it is addressed by the DEVICE and the subscriber number, which are the two columns that
        # feed actually carries. Naming store/day there would hand a reader an address that relation
        # has no columns for.
        {"key": "chargeback_dollars", "total": "chargeback_dollars", "label": "Chargeback amount",
         "value": _chargeback, "tables": _carrier_feed(), "column": "amount",
         "reason": chargeback_reason_for(source), "note": _CHARGEBACK_NOTE,
         **{**common, "keys": ("meid", "number")}},
    )


def panels(cases, summary, *, sales_table=None, source=None,
           cap=report_drilldown.ROW_CAP):
    """The served panels for this report — `{tile key: panel}`, through the one home's own rules.

    `served_tiles` decides which figures may be explained at all (a 0 over no counted rows is not a
    measurement), so this module keeps no second copy of that rule.
    """
    return report_drilldown.tiles(catalogue(sales_table, source=source), list(cases or ()),
                                  dict(summary or {}), schema=SCHEMA, cap=cap)
