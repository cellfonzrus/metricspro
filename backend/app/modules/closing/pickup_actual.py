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


# ── THE OPENED-ENVELOPE GATE (owner directive 2026-09-08; mig 990) ─────────────────────────────
# Owner: "it should have a check box asking if the cash envelope was opened", reported alongside
# "if the declared cash pick by the dm is less then the sheet does not update the actual cash picked
# up, it only shows the envelope amount".
#
# Those are ONE defect. mig 949 gave the DM somewhere to record the actual count, but recording it
# was optional and nothing ever asked — so a DM could open an envelope, find it short, confirm, and
# leave `actual_picked_amount` NULL. The row then reads "not recorded" and only the declared
# envelope amount stands, which is exactly what the owner is seeing.
#
# The flag is the DM's ASSERTION about what they physically did; the amount is the EVIDENCE. Pairing
# them closes the hole: an opened envelope cannot be confirmed without its count. A SEALED envelope
# still needs no count — a blank there is correct (nobody opened it), and the declared snapshot
# rightly stands until management's own count (mig 936). Inferring "opened" from
# `actual_picked_amount IS NOT NULL` would collapse those two states into one, which is the whole
# reason the short cash went unrecorded.


def envelope_opened(row):
    """PURE: did the DM open this envelope (mig 990)? Absent column / None / '' = NOT opened —
    the pre-990 and pre-checkbox behavior, and the honest default (nobody asserted they opened
    it). Accepts the string forms a form/query round-trip produces ('true'/'false'/'1'/'0'), so
    a JSON boolean and a stringified one can never disagree."""
    v = (row or {}).get("envelope_opened")
    if isinstance(v, str):
        return v.strip().lower() in ("true", "t", "1", "yes", "on")
    return bool(v)


def opened_without_count(item):
    """PURE — THE CONFIRM GATE. True when this pickup item claims the envelope was OPENED but
    carries no actual count. That combination is the defect the owner reported, so the confirm
    is refused rather than recorded as a blank.

    Accepts either wire shape the confirm endpoint takes (`actual_amount` from the page,
    `actual_picked_amount` from a direct/harness caller) through the same has_actual rule, so
    the gate can never disagree with what gets stored.

    NOT opened -> never blocked (a sealed envelope needs no count — the declared snapshot
    stands). Opened WITH a count -> never blocked, including a count of 0.00, which on an
    opened envelope is a real and serious finding ("I opened it and it was empty") rather than
    an absence."""
    it = item or {}
    if not envelope_opened(it):
        return False
    probe = dict(it)
    if "actual_picked_amount" not in probe and "actual_amount" in probe:
        probe["actual_picked_amount"] = probe.get("actual_amount")
    return not has_actual(probe)


def gate_items(items):
    """PURE: the confirm payload's items -> the list of (index, item) that trip
    opened_without_count. Empty list = the batch may be confirmed. Returned as a LIST (not a
    bool) so the caller can name WHICH envelopes are missing their count — a DM confirming
    twenty envelopes must not be told only that "one" of them is wrong."""
    out = []
    for i, it in enumerate(items or []):
        if opened_without_count(it):
            out.append((i, it or {}))
    return out


def gate_message(offenders):
    """PURE: the 400 text for a blocked confirm — names the envelopes, so the DM knows exactly
    which box to fill in. Empty offenders -> None (nothing to say)."""
    if not offenders:
        return None
    names = []
    for _, it in offenders:
        who = (str(it.get("employee_name") or "").strip()
               or str(it.get("store_name") or "").strip()
               or str(it.get("store_code") or "").strip() or "envelope")
        day = str(it.get("close_date") or "").strip()
        names.append(f"{who}{f' ({day})' if day else ''}")
    return ("Enter the actual cash counted for the envelope(s) marked opened: "
            + ", ".join(names) + ".")


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
