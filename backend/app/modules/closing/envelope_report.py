"""Envelope report — PURE logic (owner directive 2026-09-02, item 2; mig 936).

The owner's words, verbatim: "a new report when all the envelopes can be filtered by using the
standard filters... user can put their comments after counting the actual cash marking it short
or over and if it is short then checkmark for assigning it to the sales rep as a chargeback if
the cash is coming back as short - all comments chargebacks or any discrepancy over or short must
be filterable with the date range with all our filters."

WHO FILLS IT, AND WHICH CASH (owner question 2026-10-01: "what is the purpose of the envelope
report and who is expected to fill that, which cash are we entering"). Stated here because the
answer was only inferable from `expected_cash` below, and the same screen is read by three roles:

  · WHO — **MANAGEMENT**, after the DM has collected the envelope. Not the rep, not the DM. ONE
    envelope carries THREE numbers recorded by three different people, and they are deliberately
    separate so a discrepancy has a direction and an owner:
        1. the REP DECLARES    -> `daily_closing.t_cash`            (the daily closing form)
        2. the DM COUNTS       -> `cash_pickup.actual_picked_amount` (mig 949, at pickup)
        3. MANAGEMENT COUNTS   -> `envelope_count.counted_amount`    (mig 936, THIS report)
    KNOWN GAP: `counted_by` defaults to the literal string "management" (router
    `save_envelope_count`), so the row records THAT management counted and never WHO — while the
    pickup side does name the person (`picked_up_by`). Naming the counter needs the signed-in
    identity threaded into that handler; until then the report cannot answer "counted by whom".

  · WHICH CASH — the WHOLE DRAWER. `expected_cash` is `t_cash` (fallback `store_cash`), which
    INCLUDES the bill-payment (ePay) cash: `epay_on_cash` is a breakdown INSIDE that figure, not a
    second envelope (owner verbatim 2026-09-02, "Total cash in store including Bill Payments";
    `deposit_recon.cash_for_basis` defines store_cash = t_cash - epay_on_cash BY DEFINITION, which
    is the NET figure other reports use — this one deliberately does NOT net it). So the counter
    must NOT subtract bill-payment cash before counting, and an envelope whose whole drawer is
    bill-pay cash still expects that full amount here. Example (B-559, 2026-09-06): t_cash 100 of
    which epay_on_cash 100 -> expected 100, counted 100, variance 0, status "match".

WHAT AN "ENVELOPE" IS HERE: one `commcalc.daily_closing` row (one rep, one store, one day — the
grain the envelope photo + declared cash already live at). The management count lands in
`commcalc.envelope_count` (mig 936, one row per closing row): counted amount, over/short status,
comment, and — when short and management ticks the checkbox — a CHARGEBACK against the sales rep.

CHARGEBACK WIRING — the EXISTING mechanism, never a parallel one: the assignment inserts a PARENT
row into `commcalc.ops_chargeback` (mig 504) with reason **'envelope_short'**, `applied_to`
'commission', `amount` = the actual shortage (not a flat policy fee — the cash that is missing).
From there everything downstream is the machinery that already exists:
  • the reason surfaces automatically in the Ops Chargeback Amounts policy editor
    (`ops_chargebacks.get_policy` unions "reasons in the wild");
  • decide (post/waive) goes through `ops_chargebacks.decide_chargeback` (management-gated);
  • POSTED commission-applied rows are settled by the commission module's
    `_settle_ops_chargebacks` / `_ops_chargeback_deductions` cascade (commission-agent domain —
    this module only ever creates parent rows, per the mig-504 contract).
Idempotency rides the mig-504 parent unique key (org, employee, store, reason, incident_date) —
one envelope-short chargeback per rep per store-day, matching the envelope grain exactly.

Everything here is PURE (rows in, dicts out) — proof: backend/harness_envelope_report.py.
"""

ENVELOPE_SHORT_REASON = "envelope_short"

STATUSES = ("short", "over", "match")


def _f(v):
    try:
        return round(float(v or 0), 2)
    except (TypeError, ValueError):
        return 0.0


# ── WHICH CASH THIS RECEIPT IS COUNTING (owner 2026-10-01) ────────────────────────────────────────
# Owner: "Management Envelope Receipt should have both reports epay and store cash, use a radio button
# or select box to choose."
#
# THE VOCABULARY IS NOT NEW AND IS NOT COPIED. "The cash figure a basis reconciles against" already has
# ONE home — `deposit_recon.cash_for_basis` — with exactly these three formulas (plus 'manual', which
# has no formula and is not offered here):
#     total_cash        = t_cash                      the whole drawer  (TODAY'S behaviour, the default)
#     store_cash        = max(t_cash - epay_on_cash, 0)   register cash, bill-pay excluded
#     bill_payment_cash = epay_on_cash                the bill-payment (ePay) cash only
# `expected_cash` now DEREFERENCES that function instead of keeping its own rule, so the receipt and the
# deposit recon can never disagree about what a basis means.
# ── THE COLUMNS THIS REPORT NEEDS OFF A daily_closing ROW — one home (owner bug 2026-10-02) ─────
# Owner: "the store pay and the epay in the management review report is not coming correct, the
# system shows nothing for epay cash - all entries are zero and the store cash shows total of both."
#
# THE CAUSE, and it was mine. The basis math reads `epay_on_cash`, but the endpoint's hand-written
# `.select(...)` never fetched it. PostgREST simply omits an unselected column, so `r.get("epay_on_cash")`
# was None on every row and `_f(None)` is 0.0 — which makes the two non-default bases WRONG IN EXACTLY
# THE WAY REPORTED and leaves the default one right:
#     bill_payment_cash = epay_cash            -> 0.00 on every envelope  ("nothing for epay cash")
#     store_cash        = t_cash - epay_cash   -> t_cash ("the store cash shows total of both")
#     total_cash        = t_cash               -> correct, which is why it shipped unnoticed
#
# THE CLASS: a pure function's input column was decided HERE and fetched THERE, with nothing tying the
# two together. So the column list is now a fact of this module — the module that knows what it reads —
# and the endpoint builds its select FROM it. A future basis input cannot silently read zero, because
# adding it here adds it to the query.
#
# AND THE SAME RULE FOR EVERY OTHER KEY, not just the basis inputs: a missing column is INVISIBLE here
# by construction, because `.get()` cannot tell "0 dollars" from "not asked for". So no caller spells a
# column list of its own — each selects `CLOSING_SELECT`, below — and the lock fails the build if one
# starts to, or if this module reads a row key this tuple does not declare.
CLOSING_COLUMNS = (
    "id", "close_date", "store_code", "store_name", "store_address", "employee_name",
    "t_cash",            # the canonical declared drawer (declared_total_cash) — mig-103+ rows only
    "store_cash",        # the legacy pre-mig-103 drawer, BILL PAY EXCLUDED (not the net basis)
    "epay_cash",         # the legacy pre-mig-103 bill-pay cash, REAL and SEPARATE in that era
    "epay_on_cash",      # the mig-103+ bill-payment cash INSIDE t_cash — the basis split's input
    "envelope_picture", "remarks",
)
# What every caller actually passes to `.select(...)`, so the query and this tuple cannot drift apart.
CLOSING_SELECT = ",".join(CLOSING_COLUMNS)
# The three the basis math cannot work without; the harness asserts each is in CLOSING_COLUMNS and
# that a row fetched with ONLY those columns still produces three DIFFERENT, correct bases.
BASIS_INPUT_COLUMNS = ("t_cash", "store_cash", "epay_cash", "epay_on_cash")

ENVELOPE_BASES = ("total_cash", "store_cash", "bill_payment_cash")
ENVELOPE_BASIS_DEFAULT = "total_cash"

# THE PROCESSOR'S NAME IS NOT SPELLED HERE (harness_carrier_vocab_guard, owner directive 2026-09-04).
# "What this tenant calls its bill-payment processor" already has ONE home — the report_labels
# `processor` term (mig 953: Boost preset 'ePay', Total preset 'VidaPay', registry neutral noun
# "payment processor"). The first cut of this selector wrote 'ePay' straight into the label, which is
# the cross-side vocabulary defect the guard exists to catch: a Total-side reader would have been told
# their drawer held "Bill payments (ePay)". So the label carries a {processor} SLOT and
# `basis_options()` fills it from the resolved term — and when nothing resolves, the slot is dropped
# rather than guessed, because a missing word is honest and another carrier's brand is not.
ENVELOPE_BASIS_TERM_KEY = "processor"
# Human labels for the selector — the ONE place they are worded, so the API and the screen agree.
ENVELOPE_BASIS_LABELS = {
    "total_cash": "Total cash (whole drawer, incl. bill payments)",
    "store_cash": "Store cash (bill payments excluded)",
    "bill_payment_cash": "Bill payments ({processor}) cash only",
}


# The SHORT form of the same words, for a column header (owner bug 2026-10-02: every basis now gets its
# own column, and a sentence-long label is not a column header). Same {processor} slot rule, same one
# home — a basis is turned into words HERE and nowhere else, long form or short.
ENVELOPE_BASIS_SHORT_LABELS = {
    "total_cash": "Total cash",
    "store_cash": "Store cash",
    "bill_payment_cash": "Bill payments ({processor})",
}


def basis_label(basis, processor_term="", short=False):
    """PURE: the label for one basis, with the tenant's own word for the bill-payment processor.

    `processor_term` is `report_labels.carrier_term(client, org_id, ENVELOPE_BASIS_TERM_KEY)`. Empty or
    blank -> the parenthetical is REMOVED, never filled with a brand or left as a raw '{processor}'.
    `short=True` gives the column-header form of the same words (same slot rule).
    """
    key = normalize_envelope_basis(basis)
    tpl = (ENVELOPE_BASIS_SHORT_LABELS if short else ENVELOPE_BASIS_LABELS)[key]
    if "{processor}" not in tpl:
        return tpl
    t = str(processor_term or "").strip()
    return tpl.replace("{processor}", t) if t else tpl.replace(" ({processor})", "")


def basis_options(processor_term=""):
    """PURE: the [{key, label, short}] list the selector and the per-basis columns render, in
    ENVELOPE_BASES order.

    Built here rather than in the endpoint so the screen spells no basis key and no basis label, and
    so there is exactly one place that turns a basis into words.
    """
    return [{"key": b, "label": basis_label(b, processor_term),
             "short": basis_label(b, processor_term, short=True)} for b in ENVELOPE_BASES]


def normalize_envelope_basis(b):
    """One of ENVELOPE_BASES, defaulting to total_cash.

    Deliberately NOT deposit_recon._normalize_basis: that one folds an unknown word to 'manual', whose
    cash_for_basis value is 0.0 — so a typo'd basis would render an entire receipt as zeros and look
    like a store that took no cash. Here an unrecognised basis falls back to the DEFAULT (what the
    report showed before a selector existed), which is the honest degrade.
    """
    b = str(b or "").strip().lower()
    return b if b in ENVELOPE_BASES else ENVELOPE_BASIS_DEFAULT


# ── THE TWO ERAS OF ONE DRAWER (owner bug report 2026-10-02, Cash Pickup) ──────────────────────────
# A closing row spells "how much cash was in the drawer, and how much of it was bill payments" in one
# of TWO ways, and WHICH way is a property of the row, not of the reader:
#
#   mig-103+ (`t_cash` is present)   `t_cash` IS the whole drawer, bill payments included — the form's
#                                    own field is "Total cash in store including Bill Payments" (owner
#                                    2026-09-02). `epay_on_cash` is the bill-pay SUBSET inside it, and
#                                    `create_row` zeroes the legacy `epay_cash`/`epay_cc` columns so
#                                    nothing is counted twice.
#   pre-mig-103 (`t_cash` is NULL)   `store_cash` and `epay_cash` are two REAL, SEPARATE amounts whose
#                                    SUM is the drawer, and `epay_on_cash` does not exist at all.
#
# Measured live 2026-10-02: 89 rows carry no `t_cash`, 78 of those carry `epay_cash` > 0 and NONE of
# them carries `epay_on_cash`; and no row has `t_cash` together with `epay_cash` > 0. So the two eras
# never overlap and the era test below is exact, not a heuristic.
#
# THE CLASS THIS FIXES: that era rule was re-derived by hand at five call sites (the pickup envelope,
# the bill-pay envelope, the cash-recon declared figure, the per-tender display, the envelope-short
# alert), three of which answered only for ONE era — so a pre-mig-103 bill-pay envelope read $0.00
# while $172 of bill-pay cash sat in it. The rule now lives HERE, once, and every caller reads it.
# The columns mig 103 introduced. They move as a BLOCK — live 2026-10-02: no row carries `t_cash`
# without `t_credit`, no row carries another `t_*` without `t_cash`, and no row carries
# `epay_on_cash` without `t_cash` — so "any of these is present" is one era test, not several. It is
# spelled here because three modules were each testing the era their own way (`has_t` over the seven
# tender columns here, a bare `t_cash` check there), and two tests for one fact is the divergence the
# house rules call a defect.
TENDER_COLUMNS = ("t_cash", "t_credit", "t_ext_cc", "t_gift", "t_store_acct", "t_zelle", "t_acima")
# The bill-pay SUBSET columns of that era. Part of the era test, not just of the split: a row that
# carries one of these is a mig-103+ row whatever else was left out of the query.
MODERN_BILLPAY_COLUMNS = ("epay_on_cash", "epay_on_credit", "epay_on_acima")
MODERN_COLUMNS = TENDER_COLUMNS + MODERN_BILLPAY_COLUMNS


def is_modern_row(closing_row):
    """Is this a mig-103+ row, rather than a pre-mig-103 one?

    ANY mig-103 column present answers yes. A row fetched with only SOME of them (e.g.
    CLOSING_SELECT, which needs `t_cash` and `epay_on_cash`) still answers correctly, and a row
    carrying a bill-pay subset column but no tender column — which no live row does, but which the
    receipt's own fixtures exercise — is correctly read as modern rather than having its
    `epay_on_cash` ignored as if the column did not exist in its era.
    """
    r = closing_row or {}
    return any(r.get(k) is not None for k in MODERN_COLUMNS)


def declared_total_cash(closing_row):
    """The row's WHOLE declared drawer, bill payments included, in either era (see above).

    mig-103+ -> `t_cash`, falling back to legacy `store_cash` (the day-1 rule, unchanged).
    pre-mig-103 -> `store_cash` + `epay_cash`, because in that era those are two separate amounts,
    not a figure and its subset.

    BYTE-IDENTICAL to the previous rule for every mig-103+ row — the fallback is the same one, and
    live 2026-10-02 no row has `t_cash` set together with `epay_cash` > 0. What changes is the
    pre-mig-103 rows: they stop understating the drawer by their whole bill-pay leg (78 live rows,
    one of them by $744.00).
    """
    r = closing_row or {}
    v = _f(r.get("t_cash")) or _f(r.get("store_cash"))
    if is_modern_row(r):
        return v
    return round(v + _f(r.get("epay_cash")), 2)


def declared_billpay_cash(closing_row):
    """How much of that drawer the rep declared as BILL-PAYMENT cash, in either era.

    mig-103+ -> `epay_on_cash` (the subset column). pre-mig-103 -> the legacy `epay_cash`, which in
    that era is the real separate bill-pay amount already inside `declared_total_cash`'s sum. The
    same subset-of-the-drawer meaning in both eras, so every caller can subtract it from
    `declared_total_cash` without knowing which era it is holding.

    This is where the 2026-10-02 bug-report class lived: five call sites read `epay_on_cash` raw, a
    column no pre-mig-103 row has, so all 89 of those rows declared $0.00 of bill-pay cash.
    """
    r = closing_row or {}
    if is_modern_row(r):
        return _f(r.get("epay_on_cash"))
    return _f(r.get("epay_cash"))


def expected_cash(closing_row, basis=ENVELOPE_BASIS_DEFAULT):
    """The cash this envelope SHOULD hold on `basis` — via deposit_recon.cash_for_basis, the one home.

    basis='total_cash' (the default) is BYTE-IDENTICAL to this function before the selector existed, so
    every existing caller and every stored count keeps its meaning.
    """
    from . import deposit_recon          # function-level: keeps this module's import list empty
    r = closing_row or {}
    return deposit_recon.cash_for_basis(declared_total_cash(r), declared_billpay_cash(r),
                                        normalize_envelope_basis(basis))


def declared_components(closing_row):
    """PURE: all three cash figures for ONE row, keyed by basis — {total_cash, store_cash,
    bill_payment_cash}.

    The report shows the components BESIDE the chosen basis so "why is this 0?" is answerable on the
    screen rather than only in a docstring, and the SPLIT itself is `deposit_recon.cash_components`
    (§47.9) — the same one DM Verify dereferences — rather than arithmetic written here a second time.
    """
    from . import deposit_recon          # function-level: keeps this module's import list empty
    r = closing_row or {}
    return deposit_recon.cash_components(declared_total_cash(r), declared_billpay_cash(r),
                                         bases=ENVELOPE_BASES)


def count_fields(expected, counted, tolerance=0.0):
    """PURE: variance + over/short/match status for a counted envelope. variance = counted −
    expected (negative ⇒ short). |variance| ≤ tolerance ⇒ 'match' (tolerance 0 by default — a
    counted envelope either ties out to the cent or it doesn't)."""
    exp, cnt = _f(expected), _f(counted)
    var = round(cnt - exp, 2)
    tol = abs(_f(tolerance))
    if abs(var) <= tol:
        status = "match"
    elif var < 0:
        status = "short"
    else:
        status = "over"
    return {"expected_amount": exp, "counted_amount": cnt, "variance": var, "status": status}


# ── WHAT A STORED COUNT RECORDS ABOUT THE MONEY — one home (owner 2026-10-02, mig 1036) ─────────
# The class #348 reported open rather than fixed: a stored count row recorded an AMOUNT but not
# which QUESTION the amount answered. Two rows reading "short $230" could be a shortage in the
# whole drawer and a shortage in the bill-payment cash — self-consistent each (each carries the
# `expected_amount` it was scored against) and indistinguishable on a report.
#
# `count_fields` is NOT where the basis goes. It is the platform's short/over truth table, shared
# by the deposit recon and the external-credit tally (harness_external_credit_recon §E pins that
# reuse), whose "expected" is not a cash basis at all; putting an envelope-basis key in its return
# would push this module's vocabulary into two reports that have no use for it.
#
# So the home is HERE, one function above the truth table: `count_row_fields` produces the amounts
# AND the basis in one call, from the closing row and the basis the counter saw. The save handler
# calls it instead of composing `expected_cash` + `count_fields` itself, so a stored amount cannot
# be written without the basis it was scored on — by construction, not by someone remembering.
# LOCKED: harness_envelope_receipt_basis.py §K.
COUNT_BASIS_COLUMN = "basis"            # commcalc.envelope_count.basis (mig 1036)


def count_row_fields(closing_row, counted, tolerance=0.0, basis=None):
    """PURE: everything a `commcalc.envelope_count` row records about the money — the expected
    snapshot, what was counted, the variance, the short/over verdict, AND which cash basis the
    count was scored on. The amounts come from `count_fields` (the shared truth table, unchanged);
    the basis is normalized through `normalize_envelope_basis`, so an absent or unrecognised one
    degrades to the historical default rather than to a formula-less word.

    Returned as the insert body's own keys, so the caller spreads it and cannot drop the basis."""
    expected = expected_cash(closing_row, basis)
    out = dict(count_fields(expected, counted, tolerance))
    out[COUNT_BASIS_COLUMN] = normalize_envelope_basis(basis)
    return out


def counted_basis(count_row):
    """PURE: which cash basis a STORED count was scored on → {"basis", "recorded", "label"}.

    `recorded` is the honest half. A row written before mig 1036 carries no basis, and a NULL is
    NOT backfilled: turning "nobody recorded this" into "somebody chose total_cash" is the absence-
    is-never-zero defect. So a missing value resolves to the historical default — what the handler
    of that era scored against by construction, having had no basis to use — and says
    `recorded: false`, which is what lets a screen word it as "not recorded" instead of asserting a
    choice nobody made. An unrecognised word resolves the same way, for the same reason.

    `label` is this tenant's own wording for the basis and needs the processor term; callers that
    have it pass it to `basis_label`. Left as the neutral key here so this function stays pure of
    label resolution — see `basis_options()`."""
    raw = str((count_row or {}).get(COUNT_BASIS_COLUMN) or "").strip().lower()
    known = raw in ENVELOPE_BASES
    return {"basis": raw if known else ENVELOPE_BASIS_DEFAULT,
            "recorded": known,
            "label": raw if known else ENVELOPE_BASIS_DEFAULT}


def shortage_amount(variance):
    """The chargeback dollar for a short envelope: the missing cash itself, positive. 0 for an
    over/match variance — an overage is never anyone's chargeback."""
    v = _f(variance)
    return round(-v, 2) if v < 0 else 0.0


def chargeback_parent_row(org_id, closing_row, employee_id, employee_name, amount):
    """PURE: the ops_chargeback PARENT insert dict for an envelope shortage assigned to the sales
    rep. status 'pending' — management still decides post/waive through the existing decide flow;
    applied_to 'commission' (the rep's commissionable pay; the mig-504 cascade handles overflow
    per the org's policy). Never called with amount ≤ 0 (guarded here anyway)."""
    amt = _f(amount)
    if amt <= 0:
        return None
    r = closing_row or {}
    return {
        "org_id": org_id,
        "employee_id": str(employee_id or ""),
        "employee_name": employee_name or r.get("employee_name"),
        "store_code": r.get("store_code") or "",
        "reason": ENVELOPE_SHORT_REASON,
        "incident_date": str(r.get("close_date") or "")[:10],
        "amount": amt,
        "status": "pending",
        "applied_to": "commission",
        "notes": "Envelope counted short at management count",
    }


def report_row(closing_row, count_row, chargeback, ver_row, market,
               basis=ENVELOPE_BASIS_DEFAULT):
    """PURE: one envelope-report line — the closing row's identity + declared money + envelope
    photo ref, the management count (when one exists), and the linked chargeback status.

    `basis` (owner 2026-10-01) picks WHICH cash the line is about — see ENVELOPE_BASES. It changes
    `declared_cash` only; ALL the bases ride beside it in `declared` (keyed by basis key, so the screen
    can render a column per `basis_options()` entry without spelling a basis word) so a counter can see
    why the figure is what it is, and the short/over math stays in `count_fields` (one place).
    """
    r = closing_row or {}
    c = count_row or {}
    cb = chargeback or {}
    v = ver_row or {}
    counted = c.get("counted_amount")
    comp = declared_components(r)
    out = {
        "closing_row_id": r.get("id"),
        "close_date": str(r.get("close_date") or "")[:10],
        "store_code": r.get("store_code"),
        "store_address": r.get("store_address") or r.get("store_name") or r.get("store_code"),
        "market": market or "(no market)",
        "employee_name": r.get("employee_name"),
        "declared_cash": comp[normalize_envelope_basis(basis)],
        # The components, always, whatever the basis — so "why is this 0?" is answerable on screen
        # (owner bug 2026-10-02: they were reported but never rendered, so a 0 had no explanation).
        "basis": normalize_envelope_basis(basis),
        # Keyed BY BASIS, so the screen renders one column per `basis_options()` entry — header from
        # the server's own resolved label — and therefore spells no basis key and no basis word
        # itself. A future basis gets its column for free, and cannot get a stale header.
        "declared": comp,
        # The two flat keys the payload has carried since 2026-10-01, kept for existing readers.
        "declared_total_cash": comp["total_cash"],
        "declared_billpay_cash": comp["bill_payment_cash"],
        "envelope_picture": r.get("envelope_picture"),
        "remarks": r.get("remarks"),
        "dm_verified": bool(v.get("verified")),
        "counted": counted is not None,
        "counted_amount": _f(counted) if counted is not None else None,
        "expected_amount": _f(c.get("expected_amount")) if c.get("expected_amount") is not None else None,
        "variance": _f(c.get("variance")) if c.get("variance") is not None else None,
        "status": c.get("status") or "uncounted",
        "comment": c.get("comment"),
        "counted_by": c.get("counted_by"),
        "counted_at": c.get("counted_at"),
        # WHICH CASH THE STORED COUNT COUNTED (mig 1036). Distinct from `basis` above, which is the
        # basis this LINE is being VIEWED on — they differ exactly when management is reading the
        # receipt on one basis and the count on file was taken on another, which is the confusion
        # this column exists to end. `recorded: false` means the row predates the column.
        "counted_basis": counted_basis(c) if counted is not None else None,
        "chargeback_id": c.get("chargeback_id"),
        "chargeback_status": cb.get("status"),
        "chargeback_amount": _f(cb.get("amount")) if cb.get("amount") is not None else None,
    }
    return out


def status_filter(rows, status):
    """PURE: the report's discrepancy filter — 'short' | 'over' | 'match' | 'uncounted' |
    'discrepancy' (short OR over) | 'commented' (any comment) | 'chargeback' (one assigned).
    Unknown/blank ⇒ rows unchanged (never a silent drop on a typo'd filter)."""
    s = str(status or "").strip().lower()
    if s in STATUSES or s == "uncounted":
        return [r for r in rows if r.get("status") == s]
    if s == "discrepancy":
        return [r for r in rows if r.get("status") in ("short", "over")]
    if s == "commented":
        return [r for r in rows if (r.get("comment") or "").strip()]
    if s == "chargeback":
        return [r for r in rows if r.get("chargeback_id")]
    return rows


def by_employee(rows):
    """PURE: the SAME envelopes, rolled up per employee — owner 2026-09-26, "report by user".

    Answers "who is short, how often, and by how much" across a date range, where the per-envelope
    report answers it one store-day at a time.

    IT DOES NOT RE-DERIVE ANYTHING. Each group's numbers come from calling `totals()` on that
    group, so a per-employee figure can never disagree with the tiles above it — two summarisers
    over one set of rows is the divergence the house rules forbid, and it would show up here as a
    rollup that does not add to the total.

    IDENTITY IS THE NAME, because that is the only identity a closing row carries
    (`commcalc.daily_closing` has `employee_name` and no employee id). Two people with the same
    name therefore merge into one line, and renaming somebody splits their history at the rename.
    Stated rather than hidden: this is a roster limitation, not something to paper over here, and
    `stores` on each line makes a merge visible when it happens.

    Ordered worst-first — biggest dollar shortage, then most envelopes short, then most envelopes —
    so the line that needs attention is the line at the top. An employee whose envelopes were never
    counted has nothing measured and sorts to the bottom, never to the top as a false clean sheet.
    """
    groups = {}
    for r in rows or []:
        key = (r.get("employee_name") or "").strip() or "(unnamed)"
        groups.setdefault(key, []).append(r)
    out = []
    for name, grp in groups.items():
        t = totals(grp)
        dates = sorted(d for d in (str(g.get("close_date") or "")[:10] for g in grp) if d)
        out.append({
            "employee_name": name,
            **t,
            # Over MINUS short: the employee's net effect on the till across the range. Kept beside
            # the two gross figures, never instead of them — a rep $50 short one day and $50 over the
            # next nets to zero while having twice failed to hand over the right cash.
            "net_variance": round(_f(t["over_total"]) - _f(t["short_total"]), 2),
            "uncounted": t["envelopes"] - t["counted"],
            "stores": sorted({str(g.get("store_address") or g.get("store_code") or "") for g in grp} - {""}),
            "first_close": dates[0] if dates else None,
            "last_close": dates[-1] if dates else None,
        })
    out.sort(key=lambda e: (-_f(e["short_total"]), -e["short"], -e["envelopes"], e["employee_name"]))
    return out


def totals(rows):
    """PURE: the report's summary tiles."""
    out = {"envelopes": len(rows), "counted": 0, "short": 0, "over": 0, "match": 0,
           "short_total": 0.0, "over_total": 0.0, "chargebacks": 0, "chargeback_total": 0.0}
    for r in rows:
        st = r.get("status")
        if r.get("counted"):
            out["counted"] += 1
        if st in ("short", "over", "match"):
            out[st] += 1
        v = r.get("variance")
        if st == "short" and v is not None:
            out["short_total"] = round(out["short_total"] + (-_f(v)), 2)
        if st == "over" and v is not None:
            out["over_total"] = round(out["over_total"] + _f(v), 2)
        if r.get("chargeback_id"):
            out["chargebacks"] += 1
            out["chargeback_total"] = round(out["chargeback_total"] + _f(r.get("chargeback_amount")), 2)
    return out
