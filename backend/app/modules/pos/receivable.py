"""THE one home for "what does a customer still owe us on a sale, and how late is it" (§POS-19).

OWNER ASK 2026-10-31: having applied the Terms tender (§POS-15) and the wholesale W-9 (§POS-16), the
owner asked for the two reports those two features imply — *"build them"*: the open terms balances,
and the W-9s about to lapse.

WHY A SEPARATE HOME, AND WHAT IT IS NOT ALLOWED TO DO
────────────────────────────────────────────────────
A terms sale creates the platform's first RECEIVABLE: money we have earned and not collected. The
platform already had one home for "what was tendered against this sale, and what does that leave
outstanding" — `sale_settlement` — and it answers a different question from this one. It compares
two recorded figures AS OF THE SALE; this module asks what is still owed AS OF TODAY, which needs a
clock and a due date and `sale_settlement` deliberately has neither (its proof harness fails the
build if a `datetime` appears in it).

So this module takes the figures FROM that home and adds only what collection needs: whether the
sale carries a deferred leg at all, what is still outstanding on it, when it fell due, and how late
that is. It spells NO money arithmetic of its own beyond the subtraction of a date from a date.

THE THREE THINGS THAT MAKE THE ANSWER HONEST
────────────────────────────────────────────
1. **A SALE WITH NO DEFERRED LEG IS NOT A RECEIVABLE OF $0.00 — IT IS NOT A RECEIVABLE.** Measured
   against production 2026-10-31: 4,605 `pos.sales` rows, of which 4,603 carry no payment row at
   all and **0 carry a terms leg**. A module that read "no terms leg" as "owes nothing, due today"
   would put 4,605 rows on a collections report. `assess()` answers `receivable: False` and carries
   no figure, and the report's population is the sales that HAVE a deferred leg — not every sale
   filtered down to those with a balance, which would admit a part-tendered cash sale that nobody
   agreed terms on.

2. **AN AGE WE CANNOT MEASURE IS NEVER A FRESH AGE.** A deferred leg with no due date is
   `BAND_UNKNOWN`, the answer `manager_followup.age_band` already gives for an item carrying no
   date — not "0-7", which is how a year-old debt hides in the newest bucket. Mig 1106's
   `sale_payments_terms_shape_check` means the database cannot store a terms leg without a due
   date, so this is a guard against hand-written and future rows rather than a path in use today.

3. **NOT YET DUE IS NOT LATE, AND IT IS NOT NOTHING.** A sale inside its terms is owed to us and is
   in nobody's collections queue. It gets its own state and its own total, so the report can show
   what is coming without inflating what is overdue.

WHAT THIS MODULE DOES NOT CLAIM, STATED OUT LOUD
────────────────────────────────────────────────
**Recording a payment against an open balance does not exist in the platform yet.** What is
outstanding is `pos.sales.balance` — the figure `pos.checkout` computes as "the sum of the legs that
did not settle" (`pos/router.checkout`, §POS-15) and the figure `sale_settlement.expected` already
subtracts. Until there is a route that takes a payment against a terms balance, a terms sale stays
open on this report until somebody amends it. That is a MISSING FEATURE and it is surfaced as one
(`COLLECTION_NOT_WIRED`, carried in the payload and shown in the report's notice bar) rather than
guessed at by treating a later over-tender as a payment — which would read every collected debt as
money owed BACK to the customer.

RULE TWO: no carrier, tenant, store or product name appears in this file, and no tender is spelled
here — whether a leg settles at the till is asked of `tender_vocabulary` through `sale_settlement`.
The aging bands are the house ladder `manager_followup.AGE_BANDS` already declares, dereferenced
rather than re-spelled: a second list of what "61-90 days" means is the duplicate defect the index
rules forbid, and these bands are a POLICY (a window), not a tenant's own vocabulary.

PURE: stdlib only — no DB, no framework, no network. Proof `backend/harness_pos_receivable.py`.
"""
import datetime as _dt

from . import sale_settlement as _settle
from ..commcalc import manager_followup as _age

#: The states a sale's deferred leg can be in. `not_receivable` is the 4,605-row answer.
NOT_RECEIVABLE = "not_receivable"
OPEN_NOT_DUE = "not_due"
OPEN_PAST_DUE = "past_due"
CLEARED = "cleared"
VOIDED = "voided"
STATES = (NOT_RECEIVABLE, OPEN_NOT_DUE, OPEN_PAST_DUE, CLEARED, VOIDED)

#: The aging ladder and its unmeasurable answer — the house ones, DEREFERENCED, never respelled.
#: A second list of what "61-90 days" means is the duplicate defect the index rules forbid, and the
#: bands are a POLICY (a window) rather than a tenant's own vocabulary, so a house answer is right.
BANDS = _age.AGE_BANDS
BAND_LABELS = tuple(lbl for (lbl, _lo, _hi) in _age.AGE_BANDS)
BAND_UNKNOWN = _age.BAND_UNKNOWN

#: Said in the payload and on the screen, because a report must not look complete when the thing it
#: is reporting on cannot yet be actioned inside the platform.
COLLECTION_NOT_WIRED = (
    "Taking a payment against an open balance is not built yet, so a sale stays on this list until "
    "somebody edits it. Nothing here is a guess: the figure is what the register recorded as "
    "deferred."
)

#: Why a sale carries no receivable. A reason, never a $0.00.
NOT_RECEIVABLE_REASONS = {
    "no_deferred_leg": "every tender on this sale settled at the till, so there is nothing to "
                       "collect",
    "tender_unrecorded": "no payment was ever recorded against this sale, so there is no agreement "
                         "to collect against",
}


def _f(v):
    try:
        return round(float(v or 0), 2)
    except (TypeError, ValueError):
        return 0.0


def _date(value):
    try:
        return _dt.date.fromisoformat(str(value or "")[:10])
    except (TypeError, ValueError):
        return None


def due_date(payments):
    """The day this sale's money falls due: the EARLIEST due date across its deferred legs.

    Earliest, not latest, because a sale is late the moment any part of it is — a report that waited
    for the last leg would let the first one age silently. `None` when no deferred leg carries one.
    """
    days = [_date((p or {}).get("due_date")) for p in (payments or [])
            if not _settle.settles_now(str((p or {}).get("payment_method") or "").strip())]
    found = [d for d in days if d is not None]
    return min(found) if found else None


def terms_days(payments):
    """The agreed day count on the leg that falls due first, so a row can say "30-day terms"."""
    legs = [p for p in (payments or [])
            if not _settle.settles_now(str((p or {}).get("payment_method") or "").strip())]
    legs = [p for p in legs if _date(p.get("due_date")) is not None]
    if not legs:
        return None
    first = min(legs, key=lambda p: _date(p.get("due_date")))
    try:
        return int(str(first.get("terms_days")).strip())
    except (TypeError, ValueError):
        return None


def assess(sale, payments, *, as_of) -> dict:
    """PURE: what this sale still leaves the customer owing, when it fell due, and how late.

    `as_of` is a `datetime.date` the CALLER resolved — this module reads no clock of its own, so a
    report and a regression test see the same day.

    `receivable` is the one key a caller branches on. False means this sale belongs nowhere on a
    collections report, and the row carries no figure at all.
    """
    got = _settle.tendered(payments)
    voided = _settle.sale_is_voided(sale)
    out = {"receivable": False, "state": NOT_RECEIVABLE, "reason": None,
           "deferred": 0.0, "outstanding": 0.0, "due_date": None, "terms_days": None,
           "days_past_due": None, "band": None, "voided": voided,
           "deferred_methods": got.get("deferred_methods") or []}
    if not got["present"]:
        out["reason"] = NOT_RECEIVABLE_REASONS["tender_unrecorded"]
        return out
    deferred = _f(got.get("deferred_total"))
    if deferred <= 0:
        out["reason"] = NOT_RECEIVABLE_REASONS["no_deferred_leg"]
        return out
    out["deferred"] = deferred
    out["due_date"] = (due_date(payments).isoformat() if due_date(payments) else None)
    out["terms_days"] = terms_days(payments)
    # A voided sale is not owed. It is still SHOWN — it carried a promise to pay that the platform
    # stood down — but it is not money to chase, so it never joins an overdue total.
    if voided:
        out["state"] = VOIDED
        out["reason"] = "this sale was voided, so the balance is no longer owed"
        return out
    # What is still owed is the register's own figure for the legs that did not settle. See the
    # module note: there is no payment-application route yet, and inventing one from an over-tender
    # would read a collected debt as money owed back.
    outstanding = _f((sale or {}).get("balance"))
    out["outstanding"] = outstanding
    if outstanding <= 0:
        out["state"] = CLEARED
        out["reason"] = "the balance on this sale has been brought to zero"
        return out
    out["receivable"] = True
    due = _date(out["due_date"])
    if due is None:
        out["state"] = OPEN_PAST_DUE
        out["band"] = BAND_UNKNOWN
        out["reason"] = ("this sale carries a payment that settles later but no due date, so how "
                         "late it is cannot be measured")
        return out
    late = (as_of - due).days
    out["days_past_due"] = late
    if late <= 0:
        out["state"] = OPEN_NOT_DUE
        out["band"] = None
        return out
    out["state"] = OPEN_PAST_DUE
    out["band"] = _age.age_band(late)
    return out


def summarise(rows) -> dict:
    """The rollup a report's header shows, over rows `assess` produced.

    Per-band totals are keyed by the house ladder's own labels, so a band that exists in
    `manager_followup.AGE_BANDS` and nowhere on this screen is a zero rather than a missing key.
    """
    bands = {lbl: 0.0 for (lbl, _lo, _hi) in _age.AGE_BANDS}
    bands[BAND_UNKNOWN] = 0.0
    out = {"sales": 0, "receivables": 0, "not_receivable": 0, "cleared": 0, "voided": 0,
           "open_total": 0.0, "not_due_total": 0.0, "past_due_total": 0.0,
           "past_due_count": 0, "oldest_days_past_due": None, "bands": bands}
    # KEYED OFF `state`, NEVER OFF A SEPARATE `receivable` FLAG. The flag and the state would be two
    # answers to one question, and a row carrying `receivable: True` with state `cleared` would be
    # counted as owed. This also lets the endpoint hand its OWN row shape straight to the rollup —
    # which it does, and the first draft did not: the report's rows carried no flag, so every total
    # read $0.00 against rows that plainly showed a balance (caught by the integration test).
    for r in rows or []:
        r = r or {}
        out["sales"] += 1
        state = r.get("state")
        if state == CLEARED:
            out["cleared"] += 1
        elif state == VOIDED:
            out["voided"] += 1
        if state not in (OPEN_NOT_DUE, OPEN_PAST_DUE):
            if state not in (CLEARED, VOIDED):
                out["not_receivable"] += 1
            continue
        amount = _f(r.get("outstanding"))
        out["receivables"] += 1
        out["open_total"] = round(out["open_total"] + amount, 2)
        if state == OPEN_NOT_DUE:
            out["not_due_total"] = round(out["not_due_total"] + amount, 2)
            continue
        out["past_due_count"] += 1
        out["past_due_total"] = round(out["past_due_total"] + amount, 2)
        band = r.get("band") or BAND_UNKNOWN
        bands[band] = round(bands.get(band, 0.0) + amount, 2)
        late = r.get("days_past_due")
        if isinstance(late, int) and (out["oldest_days_past_due"] is None
                                      or late > out["oldest_days_past_due"]):
            out["oldest_days_past_due"] = late
    return out


def sentence(row) -> str:
    """One line a screen or a digest can show as-is. Claims no figure the row does not carry."""
    r = row or {}
    if not r.get("receivable"):
        return r.get("reason") or "there is nothing to collect on this sale"
    money = f"${r['outstanding']:,.2f}"
    if r.get("state") == OPEN_NOT_DUE:
        when = r.get("due_date") or "a date nobody recorded"
        return f"{money} is owed on this sale, due {when}."
    late = r.get("days_past_due")
    if late is None:
        return f"{money} is owed on this sale and how late it is cannot be measured."
    day = "day" if late == 1 else "days"
    return f"{money} is owed on this sale and is {late} {day} past due."
