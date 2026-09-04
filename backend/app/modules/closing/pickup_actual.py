"""Actual cash picked from the envelope — PURE logic (owner directive 2026-09-04; mig 949).

The owner's words, verbatim: "for cash pick up, one more column is needed actual cash picked
from envelope."

WHAT THIS IS: when the DM confirms a pickup on /closing/pickup, they can now also record the
ACTUAL cash physically taken out of the envelope, beside the system's declared/expected figure
(`cash_pickup.amount` — the mig-034 snapshot of store_cash + epay_cash at confirm time). The
value lands in `cash_pickup.actual_picked_amount` (mig 949; billpay_pickup mirror — the
parameterized machinery is shared, so the sibling gets it for free).

WHY A NEW COLUMN, NOT `envelope_count.counted_amount` (the duplicate-check verdict):
  • DIFFERENT ACTOR, DIFFERENT MOMENT: envelope_count (mig 936) is MANAGEMENT's later count in
    the envelope report (counted_by/counted_at + the envelope_short chargeback machinery). This
    is the DM's own count at the moment of pickup. Overloading counted_amount would let a DM
    pickup overwrite management's count (or block it), and the chargeback flow keys off
    counted_amount — conflating the two would move money on the wrong evidence.
  • DIFFERENT KEY: envelope_count keys on closing_row_id (daily_closing.id — REPLACED on every
    closing-sheet re-sync); cash_pickup deliberately keys the LOGICAL envelope
    (org, close_date, store, employee) so pickups survive re-uploads (mig-034 design note). The
    actual-picked figure belongs on the pickup row, same as the declared snapshot beside it.
  • SAME CONVENTION AS THE DEPOSIT STEP: the flow already pairs deposit_amount (what the slip
    says) with declared_amount (what the system says) on the pickup row (mig 089/942). Actual
    vs declared at pickup time mirrors that exactly.
WHAT IS REUSED (never a sibling derivation): the variance triple (variance + short/over/match)
is `envelope_report.count_fields` — the SAME truth table the envelope report's management count
uses, so "short" means the same thing on both surfaces.

THE MONEY-FLOW POSTURE (the important one): `_cash_position_core` — and through it the mig-938
balance-sheet store-cash line, Cash Position, Store Cash on Hand and the pickup page's by_store
panel — treats the pickup's outflow as `amount` (declared) today. That stays the DEFAULT,
byte-identical. The per-org knob `cash_pickup_config.pickup_actual_relieves_cash` (mig 949,
default false — the mig-942 billpay_relieves_cash precedent) flips the outflow to the ACTUAL
figure where one was recorded (declared where none was): flipping it moves the BS cash number,
so the seed is commented out under the owner-approval convention. The variance itself is
display + a flag everywhere (pickup list, deposit-accountability day view), never a booking.

Everything here is pure (rows in, dicts out) — proof: backend/harness_cash_pickup.py §6 +
harness_deposit_accountability.py §G. The one DB wrapper (the knob) follows the
billpay_relieves_cash adaptive fail-to-default posture.
"""

from .envelope_report import count_fields


def _f(v):
    try:
        return float(v or 0)
    except (TypeError, ValueError):
        return 0.0


def has_actual(row):
    """PURE: does this pickup row carry a recorded actual-picked figure? None/absent/'' = the DM
    recorded nothing (an old row, or a confirm without the new input) — NEVER coerced to 0.0
    (a fake 100%-short)."""
    v = (row or {}).get("actual_picked_amount")
    return v is not None and str(v).strip() != ""


def variance_fields(declared, actual):
    """PURE: the actual-vs-declared triple for one pickup row, or None when no actual was
    recorded (honest absence, never a fake zero). REUSES envelope_report.count_fields — the
    envelope report's own short/over/match truth table (variance = actual − declared, negative
    = short, tolerance 0: it ties out to the cent or it doesn't) — so 'short' means the same
    thing at pickup time as it does at management count time. Returns
    {actual, declared, variance, status}."""
    if actual is None or str(actual).strip() == "":
        return None
    cf = count_fields(declared, actual)
    return {"actual": cf["counted_amount"], "declared": cf["expected_amount"],
            "variance": cf["variance"], "status": cf["status"]}


def row_variance(row):
    """PURE: variance_fields for a pickup ROW (declared = its mig-034 `amount` snapshot,
    actual = mig-949 `actual_picked_amount`), or None when no actual is recorded."""
    r = row or {}
    if not has_actual(r):
        return None
    return variance_fields(r.get("amount"), r.get("actual_picked_amount"))


def outflow_amount(row, actual_wins):
    """PURE — THE MONEY GATE. The dollars this picked-up envelope relieves from the general
    cash movement (_cash_position_core → BS store-cash line, Cash Position, Store Cash on
    Hand). `actual_wins` False (the house default): the DECLARED snapshot (`amount`) —
    byte-identical to pre-949 behavior, always. True (the org flipped
    cash_pickup_config.pickup_actual_relieves_cash): the recorded ACTUAL where present,
    falling back to the declared snapshot where none was recorded (an unrecorded actual is
    absence of evidence, not evidence of zero cash)."""
    r = row or {}
    if actual_wins and has_actual(r):
        return _f(r.get("actual_picked_amount"))
    return _f(r.get("amount"))


# ── DB wrapper — the knob (mig 949), billpay_relieves_cash adaptive posture ─────────────────────
def actual_relieves_cash(client, org_id):
    """The mig-949 outflow knob for this org — ADAPTIVE: pre-949 schema, no config row, or any
    read failure resolve to False (today's behavior: the declared snapshot relieves the cash
    line). NEVER raises."""
    try:
        rows = (client.schema("commcalc").table("cash_pickup_config")
                .select("pickup_actual_relieves_cash").eq("org_id", org_id).limit(1)
                .execute().data) or []
        return bool(rows and rows[0].get("pickup_actual_relieves_cash"))
    except Exception:
        return False
