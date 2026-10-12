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


#: WHAT THE DM'S COUNT IS ON. `actual_picked_amount` is the cash physically in the bag — never a
#: netted figure, because nobody nets a bag they are counting. So the comparison is only sound when
#: the EXPECTED figure is on that same footing, which is what `billpay_netting.count_comparable`
#: rules from the row's stored `amount_basis` (mig 1039). One home for that judgement; this module
#: dereferences it and holds no basis opinion of its own.
COUNT_IS_WHOLE_ENVELOPE = True


def row_variance(row):
    """PURE: variance_fields for a pickup ROW (expected = its mig-034 `amount` snapshot, actual =
    mig-949 `actual_picked_amount`), or None when no actual is recorded.

    REFUSES ACROSS BASES (owner question 2026-10-10). `amount` is netted when the tenant nets by the
    POS figure, while the bag was netted by the rep's OWN declared figure — so subtracting them
    produces the POS-versus-declared gap wearing a shortage's clothes. 105 of 135 live 'pos'-basis
    rows read short or over for exactly that reason. Where the basis is not comparable this returns
    the same shape with `status='not_comparable'`, `variance=None` and a `reason` sentence, so a
    caller that renders the status gets the honest answer instead of a number — and a caller that
    reads `variance` arithmetically gets None rather than a misleading float.

    The basis vocabulary and the rule both live in `billpay_netting`; nothing is decided here.
    """
    r = row or {}
    if not has_actual(r):
        return None
    return gate_on_basis(variance_fields(r.get("amount"), r.get("actual_picked_amount")),
                         r.get("amount_basis"))


#: The status a refused comparison carries. A VALUE, so no caller spells it.
NOT_COMPARABLE = "not_comparable"


def gate_on_basis(vf, basis):
    """PURE: the variance triple, gated on whether the expected figure's basis may be compared with
    a count of the whole envelope. THE ONE PLACE that gate is applied, dereferenced by both the READ
    path (`row_variance`, the board and the pickup list) and the WRITE path (`count_patch`, the
    open-and-count save) — so a figure cannot be refused on one surface and scored on the other.

    `vf` is `variance_fields`' output (or None, passed straight through).

    On a comparable basis the triple is returned with `comparable=True` and `reason=None` — the
    pre-existing shape plus two keys, so an older reader is byte-identical. On any other basis the
    STATUS becomes `not_comparable` and `variance` becomes None: not 'match' and not 0.00, either of
    which would read as "it tied out". The expected and actual figures are KEPT, so a screen can
    still show what was expected and say why it cannot be scored.

    The rule itself lives in `billpay_netting.count_comparable` — nothing is decided here.
    """
    if vf is None:
        return None
    from . import billpay_netting as _bn
    out = dict(vf)
    out["amount_basis"] = str(basis).strip().lower() if str(basis or "").strip() else None
    out["comparable"] = _bn.count_comparable(basis)
    if out["comparable"]:
        out["reason"] = None
        return out
    out["variance"] = None
    out["status"] = NOT_COMPARABLE
    out["reason"] = _bn.count_incomparable_reason(basis)
    return out


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


# ── THE OPEN-AND-COUNT PATCH — ONE HOME (owner directive 2026-10-10) ───────────────────────────
#
# Owner, verbatim: *"if the cash is not opened in cash pickup or epay pick up it should [have] the
# option to open on this module and it should also give the field to enter the cash pick[ed] by the
# manag[e]ment or handed over to the manag[e]ment"*.
#
# THE CLASS, NOT THE INSTANCE. The instance is "the deposit-accountability board cannot open a
# sealed envelope". The class is that "the DM/management opened this envelope and counted it" was
# a fact only ONE writer could record — `_confirm_pickup_impl`, at confirm time — and that writer
# built the stored columns INLINE. A second surface recording the same fact would have had to spell
# `envelope_opened` / `actual_picked_amount` / the mig-1067 counter columns itself, which is the
# divergence the house rules forbid: two paths answering "was it opened, and what was in it".
#
# So the column set is a FUNCTION, dereferenced by every writer:
#   · closing/router._confirm_pickup_impl  — the DM's count at pickup time (the original caller)
#   · closing/router._open_count_impl      — opening a sealed envelope later, from the
#                                            deposit-accountability board (this directive)
# `harness_deposit_accountability.py` §J FAILS THE BUILD if either stops dereferencing it, or if a
# second copy of the column names appears in the router.
#
# WHO COUNTED IT (mig 1067). mig 949 recorded the count and mig 990 recorded that the seal was
# broken, but neither recorded WHO made the count or WHEN — the same gap the index already names on
# the management side (`envelope_count.counted_by` writing the literal string "management"). When
# the opener is not the DM who collected it, `picked_up_by` is the wrong name and overwriting it
# would destroy the collection record. `actual_counted_by` / `actual_counted_at` are that fact's own
# home; they are written only when the caller supplies a counter, and the writer retries without
# them on a pre-1067 schema (the mig-201 precedent every optional statement column here follows).
COUNT_COLUMNS = ("envelope_opened", "actual_picked_amount", "actual_counted_by", "actual_counted_at")


def count_patch(item, declared=None, counted_at=None, basis=None):
    """PURE — THE ONE HOME for the stored shape of an open-and-count statement.

    `item` is either wire shape the pickup surfaces send (`actual_amount` from a page,
    `actual_picked_amount` from a direct/harness caller; `envelope_opened` as a JSON or
    stringified boolean; `counted_by` naming the person). `declared` is the envelope's declared
    snapshot, used only to score the variance for the caller's response — it is never stored by
    this function.

    Returns {"row": {...columns to write...}, "variance": {...} | None, "opened": bool}.

    THE KEY RULES, identical for every caller:
      · a column appears in `row` ONLY when the item actually carries that statement. A key the
        client never sent is never written, so an older frontend stays byte-identical and a blank
        is never recorded as a fake 0.00 (absence of a count, not a count of nothing).
      · `actual_amount` present-but-blank CLEARS the count to NULL — "I recorded nothing", the
        mig-949 blank-clears rule, not a zero.
      · a counted amount of 0.00 on an OPENED envelope is a real finding ("I opened it and it was
        empty") and is stored as 0.00, never dropped.
      · `actual_counted_at` is only ever set alongside a counter name, so a timestamp can never
        stand for a person.
    The variance triple is `envelope_report.count_fields` through `variance_fields` — the same
    truth table the management count and the pickup list already use."""
    it = dict(item or {})
    row, opened = {}, envelope_opened(it)
    if "envelope_opened" in it:
        row["envelope_opened"] = opened
    vf = None
    if "actual_amount" in it or "actual_picked_amount" in it:
        act = it.get("actual_amount", it.get("actual_picked_amount"))
        # GATED ON THE SAME BASIS RULE as the read path (owner question 2026-10-10): the reply this
        # builds is what the screen prints after a save, so scoring a pos-basis row here would
        # announce a shortage the board then refuses to show. `basis` absent -> the gate's own
        # "unknown" answer, never a score.
        vf = gate_on_basis(variance_fields(declared, act), basis)
        row["actual_picked_amount"] = vf["actual"] if vf else None
    who = str(it.get("counted_by") or "").strip()
    if who:
        row["actual_counted_by"] = who
        row["actual_counted_at"] = counted_at
    return {"row": row, "variance": vf, "opened": opened}


# ──────────────────────────────────────────────────────────────────────────────────────────────────
# A STORE-DAY'S VERDICT, not a store-day's arithmetic (owner report 2026-10-12, index §75)
# ──────────────────────────────────────────────────────────────────────────────────────────────────
# The owner was on the Management Recon screen when he asked where $552 came from. That screen does
# not show `row_variance` at all: it summed `actual_picked_amount` per store-day and subtracted the
# WHOLE DECLARED DRAWER (`daily_closing.t_cash`). Under bill-payment netting the bag never holds the
# drawer — it holds the drawer less the bill-pay cash — so the subtraction returns minus the rep's
# bill-pay cash on every netted store-day. Measured on the house org 2026-10-12: 278 of the 470
# counted store-days showed a "shortage" EXACTLY equal to the declared bill-pay cash, $90,280.01 of
# it, on the one surface that names the DM who did the counting.
#
# THE CLASS, not the instance: an envelope count's expected figure is THE ENVELOPE'S expected figure.
# It is `cash_pickup.amount` with its mig-1039 basis, which is what `row_variance` already reads.
# A caller that needs a store-day total therefore needs the ROWS' verdicts aggregated, never a fresh
# subtraction of two sums — because a sum has no basis, and the gate cannot run on it.
#
# So this is the one home for "what did this store-day's envelopes come to", and the management
# screen dereferences it exactly as the pickup list and the accountability board dereference
# `row_variance`. Nothing new is decided here: the per-row judgement is still `row_variance`'s, and
# the basis rule is still `billpay_netting`'s.

def day_variance(rows):
    """PURE: ONE verdict for the counted pickup envelopes of a single store-day, or None when
    nobody counted any of them (honest absence — never a fake zero, which would read as 100% short).

    Returns {expected, counted, variance, status, reason, comparable, counted_rows,
    not_comparable_rows}, the same vocabulary `row_variance` returns so a screen renders both the
    same way.

    FAILS CLOSED ON A MIXED DAY. If ANY counted envelope of the day is refused by the basis gate, the
    DAY is refused: `variance=None`, `status='not_comparable'`, and the reason is that envelope's.
    Summing the comparable ones and quietly dropping the rest would publish a number that is neither
    the day's shortage nor any envelope's — the shape of defect this whole mechanism exists to stop.
    The expected and counted totals are still returned, so a screen can show what was in play.

    Rows that carry no count are skipped, not zeroed: an uncounted envelope is not a tied-out one.
    """
    rs = [r for r in (rows or []) if has_actual(r)]
    if not rs:
        return None
    expected = counted = 0.0
    refused = []
    for r in rs:
        rv = row_variance(r) or {}
        expected = round(expected + _f(rv.get("declared")), 2)
        counted = round(counted + _f(rv.get("actual")), 2)
        if rv.get("comparable") is False:
            refused.append(rv)
    out = {"expected": expected, "counted": counted,
           "counted_rows": len(rs), "not_comparable_rows": len(refused)}
    if refused:
        out.update({"variance": None, "status": NOT_COMPARABLE, "comparable": False,
                    "reason": refused[0].get("reason")})
        return out
    cf = count_fields(expected, counted)
    out.update({"variance": cf["variance"], "status": cf["status"], "comparable": True,
                "reason": None})
    return out
