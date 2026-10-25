"""A CHARGEBACK AGAINST A PERSON NAMES ITS INCIDENT — the declaration for the DM-verify chargeback
strip (§CORE-17).

Four figures sit above the "Missed verifications & chargebacks" panel — pending, posted, waived, and
"to be foregone from commission" — and each one is money taken, or about to be taken, out of a named
district manager's pay. Until now none of them opened to the incidents behind it: a DM could read
that $450 was going to be deducted and have no way to see which store-days that was.

ONE ROW IS ONE INCIDENT. A chargeback row is the penalty for a specific store-day whose closing was
filed and never DM-verified, so it is addressed by its own id and labelled by the store, the day and
the person — because a figure that costs someone money must say whose days it is counting.

WHERE THE MONEY LIVES, STATED PRECISELY. The amount is on the chargeback row itself
(`ops_chargeback`), which is also where the decision (pending / posted / waived) and who made it
live. It is NOT on the closing row and not on a verification row — there is no verification row,
which is the whole reason the chargeback exists. So this names one table and says so, rather than
listing the closing tables a reader might expect and could not find the figure in.

"TO BE FOREGONE" IS PENDING + POSTED, AND IS ITS OWN FOLD. `ops_chargebacks.totals` composes it
there, once, and this declaration reads the same two statuses off the same rows — a waived
chargeback is forgiven and counted in neither. Composing it on the screen from two other tiles would
have produced a figure with no rows of its own.
"""
from app.modules.core import report_drilldown

#: One incident's address. The chargeback's own id, because the (person, store, day, reason) key it
#: is unique on is an implementation detail of the sweep, while the id is what the decide endpoint
#: and the screen both use.
ROW_KEYS = ("id",)

#: The one table that holds a chargeback's amount, status and decision.
CHARGEBACK = ("ops_chargeback",)

#: The statuses "to be foregone" covers: pending has not been taken yet, posted already has, and a
#: waived one is forgiven. Read off here once so the figure and its rows cannot disagree.
FOREGONE_STATUSES = ("pending", "posted")


def _status(r):
    return (r or {}).get("status") or "pending"


def _label(r):
    r = r or {}
    return " · ".join(str(x) for x in (
        r.get("store_code") or "(no store)",
        str(r.get("incident_date") or "?")[:10],
        r.get("employee_name") or "(no name)",
    ))


def _row_fields(r):
    r = r or {}
    return {"store_code": r.get("store_code"),
            "employee_name": r.get("employee_name"),
            "state": _status(r),
            "decided_by": r.get("decided_by"),
            "period": r.get("posted_ref")}


def _amount_when(*statuses):
    """A figure's contribution: the chargeback's amount when it is in one of these states, and None
    when it is not — so a waived chargeback appears under "pending" as context, not as $0.00."""
    def read(r):
        return (r or {}).get("amount") if _status(r) in statuses else None
    return read


def catalogue():
    common = {"row_label": _label, "keys": ROW_KEYS, "schema": "commcalc", "fields": _row_fields,
              "tables": CHARGEBACK}
    return (
        {"key": "pending", "label": "Chargebacks not yet taken from anyone's pay",
         "total": "pending", "value": _amount_when("pending"),
         "in_figure": (lambda r: _status(r) == "pending"), **common},
        {"key": "posted", "label": "Chargebacks already deducted",
         "total": "posted", "value": _amount_when("posted"),
         "in_figure": (lambda r: _status(r) == "posted"),
         "note": ("Posted means the deduction has happened, against the commission period on the "
                  "row."), **common},
        {"key": "waived", "label": "Chargebacks forgiven",
         "total": "waived", "value": _amount_when("waived"),
         "in_figure": (lambda r: _status(r) == "waived"), **common},
        {"key": "to_be_foregone", "label": "Total that will come out of commission",
         "total": "to_be_foregone", "value": _amount_when(*FOREGONE_STATUSES),
         "in_figure": (lambda r: _status(r) in FOREGONE_STATUSES),
         "note": ("The pending and already-posted ones together. A waived chargeback is forgiven, "
                  "so it is shown below but counted in neither."), **common},
    )


def drilldowns(rows, totals, cap=None):
    return report_drilldown.tiles(catalogue(), rows, totals,
                                  cap=cap or report_drilldown.ROW_CAP)
