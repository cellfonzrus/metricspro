"""CARRIER STATEMENT EARNED  vs  EMPLOYEE PAID — per rep, per month. READ-ONLY, BOOKS NOTHING.

OWNER DIRECTIVE 2026-09-20: *"the basis for calculation for all carriers is a similar feed source"* —
this report is the evidence surface for that question. It puts, side by side and NEVER netted:

  • CARRIER EARNED  — what the master-agent / carrier STATEMENT says was earned on the activations
                      that this rep rang out (dealer REVENUE).
  • EMPLOYEE PAID   — what the rep was actually paid for the same month (an employee EXPENSE).

THEY ARE TWO DIFFERENT LEDGERS AND THIS MODULE NEVER SUMS OR NETS THEM. The gap between them is
reported as a NAMED difference (`carrier_earned_minus_employee_paid`) so a reader can see margin per
rep, and the payload says out loud that adding the two together is meaningless.

WHAT IS REUSED RATHER THAN REBUILT (duplicate-check, CLAUDE.md build gate)
-------------------------------------------------------------------------
Everything on the carrier side already exists. This module adds a ROLLUP, not a second statement
reader, and it introduces NO table and NO ingest path:

  • the SOLD universe, the device→rep attribution, the paid/unpaid evidence test and the business-rule
    attribution come from `ma_recon` (§15 "B2B ↔ MA activation recon"), verbatim — the caller hands
    this module the very rows `ma_recon.reconcile_ma_activations` produces for the Pay Discrepancy
    report. One sold universe, one definition of "paid", one rule matcher.
  • the per-device carrier MONEY comes from `sale_installment_engine._ma_gate_index`, the mig-308
    index that already nets a device's base + adjustment rows per column (so a clawback nets out).
  • the direction of an MA amount (`ma_payout_sign`) comes from the org's mig-223/308
    `installment_gate_source_config` ladder via `ma_recon.load_gate_cfg` — the SAME knob the payout
    gate reads, so the recon and the payout can never disagree about which sign means "paid to us".
  • the employee side is `commcalc.rep_commissions` as already written — this module reads it, never
    recomputes it, never writes it.

WHY NOT `commcalc.carrier_commission`: that mig-065 table is the statement landing for a DIFFERENT
(direct-carrier-file) shape and it holds ZERO rows for every live org — building the report on it
would render a permanently empty carrier side while the tenant's real statement sits, fully ingested,
in `raw_ma_commission`. Measured 2026-09-20: `carrier_commission` 0 rows house + 0 rows LuxeLink;
`raw_ma_commission` 2,780 rows LuxeLink. `rep_commissions.carrier_statement_comm` is fed from that
same empty table by `_apply_new_engines`, which is why it reads $0.00 on every live row.

ABSENCE IS NEVER A FINDING (§23s.8 / §25.8 precedent, vocabulary reused verbatim from
`marketing/event_sales.py`)
----------------------------------------------------------------------------------------------------
A rep's earned figure is one of THREE states, never a bare number:

  'reported'      the statement feed is loaded AND this rep's activations carry statement money.
  'measured_zero' the statement feed IS loaded and this rep's activations WERE looked up, and the
                  statement pays nothing against them. A real zero, stated as measured.
  'not_reported'  no statement feed is loaded for the period (or this rep's activations carry no
                  device key to look up). The earned figure is None — NEVER 0.00. A missing upload is
                  reported, never absorbed into the number.

RULE TWO. Which statement columns count as dealer earnings is CONFIG, never a branch: the per-org
`commission_catalog` amount fields for the MA report key, with the engine's own column tuple as the
HOUSE DEFAULT. No carrier, tenant, market or product name appears anywhere in this file.

Everything here is PURE (rows + config in, rows out) — no DB, no framework — so
`backend/harness_carrier_vs_pay.py` proves the rules without a database.
"""

# ── The three states an EARNED figure can be in. The third is the one that keeps the report honest.
EARNED_STATE_REPORTED = "reported"
EARNED_STATE_MEASURED_ZERO = "measured_zero"
EARNED_STATE_NOT_REPORTED = "not_reported"

EARNED_REASON_NO_FEED = "carrier_statement_not_loaded"
EARNED_REASON_NO_DEVICE_KEY = "no_device_key_on_sale_row"
EARNED_REASON_ABSENT = "absent_from_the_loaded_statement"
EARNED_REASON_PAID = "statement_carries_money_against_these_activations"
EARNED_REASON_PREATTRIBUTED = "processor_payment_feed_already_attributed_to_the_rep"

EARNED_STATE_NOTES = {
    EARNED_STATE_REPORTED: (
        "The carrier statement is loaded and carries money against activations attributed to this "
        "rep. The figure is what the statement says, netted across base and adjustment rows."),
    EARNED_STATE_MEASURED_ZERO: (
        "The carrier statement IS loaded and this rep's activations WERE looked up in it, and it "
        "pays nothing against them. This is a MEASURED zero — stated rather than silently absent — "
        "and because statements keep arriving for months it may stop being zero."),
    EARNED_STATE_NOT_REPORTED: (
        "Nothing about this rep's carrier earnings is known. It is NEVER shown as $0.00 earned: a "
        "zero and an un-uploaded statement are different answers, and a report that prints the "
        "first when it means the second invents a margin that nobody measured."),
}

EARNED_REASONS = {
    EARNED_REASON_NO_FEED: (
        "No carrier statement is loaded for this period, so no activation could be looked up. A "
        "missing upload is reported, never absorbed into the number."),
    EARNED_REASON_NO_DEVICE_KEY: (
        "This rep's sold activations carry no device serial, so there is no key to look the "
        "statement up by. Nothing about their earnings is known — which is not the same as their "
        "having earned nothing."),
    EARNED_REASON_ABSENT: (
        "The statement is loaded and these devices are not in it. That is consistent with nothing "
        "having been paid, but the statement does not SAY so, so it is reported as a measured zero "
        "with its device count, not as an authoritative $0.00."),
    EARNED_REASON_PREATTRIBUTED: (
        "The dealer commission for this tenant arrives as a processor PAYMENT feed that is already "
        "aggregated per rep, so no device-level attribution is involved. The figure is what that "
        "feed paid against this rep's login for the period."),
    EARNED_REASON_PAID: (
        "The statement carries money against these devices, netted across base and adjustment rows "
        "so a clawback reduces the figure rather than being counted as a second payout."),
}

# ── WHICH FEED CARRIES THE EARNED SIDE ─────────────────────────────────────────────────────────────
# The two live tenant shapes carry dealer commission in completely different places, and NEITHER is a
# branch in this module: the caller resolves the shape from config and hands the rows in.
#
#   'statement_device'  a master-agent STATEMENT, one row per device, money in per-month columns.
#                       Attribution to a rep goes device -> sale -> rep (ma_recon + the mig-308 index).
#   'preattributed_rep' a processor PAYMENT feed already aggregated per rep by the calculator (the
#                       ePay `Commission` payment category -> `rep_commissions.boost_commission`).
#                       There is nothing to attribute: the figure is already per rep.
#
# A tenant on the second shape is NOT "unreported" — its earned side simply arrives pre-attributed.
# Treating it as unreported (which this report did before the shape was passed) told a Boost tenant
# that nothing was known about earnings while a measured per-rep figure sat one column away.
EARNED_SHAPE_STATEMENT_DEVICE = "statement_device"
EARNED_SHAPE_PREATTRIBUTED_REP = "preattributed_rep"


# The difference is NAMED, so nobody reads it as a balance.
DIFFERENCE_LABEL = "carrier_earned_minus_employee_paid"
DIFFERENCE_NOTE = (
    "Carrier earned is DEALER REVENUE; employee paid is a PAYROLL EXPENSE. They are two different "
    "ledgers on two different feeds. This report never sums them and never nets them into a single "
    "figure: the subtraction below is a margin indicator, not a balance, and it is only computed "
    "when BOTH sides are measured.")
DIFFERENCE_STATE_COMPUTED = "computed"
DIFFERENCE_STATE_UNAVAILABLE = "unavailable"


def normalize_rep(name):
    """The rep key both sides agree on: upper-cased, whitespace-collapsed. `rep_commissions` stores
    the POS spelling in `epay_salesperson` and `ma_recon` carries the SAME POS spelling in
    `rep_username` (it reads `raw_sales.salesperson`), so this is a fold, not a fuzzy match — no
    name bridge is invented here. PURE."""
    return " ".join(str(name or "").strip().split()).upper()


def earnings_columns(configured, house_default):
    """RULE TWO: which statement columns count as DEALER EARNINGS. `configured` is the org's own
    catalog amount-field list; anything falsy falls back to the house default tuple. Order is
    preserved and duplicates removed so the payload can show exactly what was summed. PURE."""
    src = [str(c).strip() for c in (configured or []) if str(c or "").strip()]
    if not src:
        src = [str(c).strip() for c in (house_default or []) if str(c or "").strip()]
    seen, out = set(), []
    for c in src:
        if c not in seen:
            seen.add(c)
            out.append(c)
    return out


def _f(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return 0.0


def device_earned(agg, columns, payout_sign):
    """Dealer earnings on ONE device, IN THE PAYOUT DIRECTION, from the mig-308 netted index entry.

    `agg` is `_ma_gate_index[device_key]` — {column -> net amount already summed across that device's
    base and adjustment rows}. `payout_sign` is the org's `ma_payout_sign` (-1 on a feed where a
    negative amount means money TO the dealer). A device whose net flips against the payout direction
    (a clawback that exceeds the original payout) returns a NEGATIVE figure: the report shows the
    claw-back rather than clamping it to zero, because clamping would overstate what we earned.
    Returns (amount, present) — `present` is False when the device is not in the statement at all,
    which is what separates a measured zero from an absence. PURE."""
    if agg is None:
        return 0.0, False
    sign = 1.0 if _f(payout_sign) > 0 else -1.0
    total = 0.0
    for c in (columns or []):
        total += _f(agg.get(c)) * sign
    return round(total, 2), True


def rollup_by_rep(recon_rows, ma_index, pay_rows, *, columns, payout_sign,
                  statement_loaded, market_for=None, preattributed_earned=None,
                  earned_shape=EARNED_SHAPE_STATEMENT_DEVICE):
    """THE report. PURE.

    recon_rows      rows exactly as `ma_recon.reconcile_ma_activations` returns them (every sold
                    activation, 'ok' rows included — this report needs the paid population too).
    ma_index        `sale_installment_engine._ma_gate_index(ma_rows)` for the same window.
    pay_rows        `commcalc.rep_commissions` rows for the org + period, as stored.
    columns         earnings_columns(...) — the config-resolved statement money columns.
    payout_sign     the org's `ma_payout_sign`.
    statement_loaded  did ANY statement row load for this window? The single fact that decides
                    whether an empty earned figure is a measured zero or an absence.
    market_for      optional store -> market resolver (the canonical one; never re-derived here).
    preattributed_earned  {normalized rep key -> amount} for a tenant whose dealer commission feed is
                    ALREADY per rep (the ePay payment-category shape). When given, it is the earned
                    side for every rep it names and the device walk is not consulted for them — there
                    is no second derivation, because on that shape there is no device attribution to
                    do. A rep absent from the map is still governed by `statement_loaded`.
    earned_shape    which of the two shapes the caller resolved, echoed into meta so the page can
                    state the basis instead of the reader guessing.

    Returns {'rows': [...], 'totals': {...}, 'meta': {...}}. Earned and paid are separate keys in
    every row and in the totals; nothing in this function adds one to the other."""
    per = {}

    def _slot(key, name):
        s = per.get(key)
        if s is None:
            s = per[key] = {
                "rep": name, "rep_key": key, "store": "", "market": "",
                "activations_sold": 0, "activations_in_statement": 0,
                "activations_without_device_key": 0, "activations_paid_evidence": 0,
                "_earned": 0.0, "_any_present": False,
                "carrier_earned": None, "carrier_earned_state": EARNED_STATE_NOT_REPORTED,
                "carrier_earned_reason": EARNED_REASON_NO_FEED,
                "employee_paid": None, "employee_paid_state": "not_calculated",
                "difference": None, "difference_state": DIFFERENCE_STATE_UNAVAILABLE,
                "difference_label": DIFFERENCE_LABEL,
                "open_no_rule": 0, "explained": 0,
            }
        return s

    for r in (recon_rows or []):
        key = normalize_rep(r.get("rep_username"))
        if not key:
            continue
        s = _slot(key, str(r.get("rep_username") or "").strip())
        s["activations_sold"] += 1
        if not s["store"]:
            s["store"] = str(r.get("store") or "").strip()
        status = str(r.get("status") or "").strip().lower()
        if status == "ok":
            s["activations_paid_evidence"] += 1
        elif status == "open":
            s["open_no_rule"] += 1
        else:
            s["explained"] += 1
        dev = str(r.get("imei") or "").strip()
        if not dev:
            s["activations_without_device_key"] += 1
            continue
        amt, present = device_earned((ma_index or {}).get(dev), columns, payout_sign)
        if present:
            s["activations_in_statement"] += 1
            s["_any_present"] = True
            s["_earned"] += amt

    # EMPLOYEE SIDE — read as stored. Never recomputed here, never written.
    for p in (pay_rows or []):
        key = normalize_rep(p.get("epay_salesperson") or p.get("storeops_name"))
        if not key:
            continue
        s = _slot(key, str(p.get("epay_salesperson") or p.get("storeops_name") or "").strip())
        s["employee_paid"] = round(_f(s["employee_paid"]) + _f(p.get("total_payout")), 2)
        s["employee_paid_state"] = "calculated"
        if not s["store"]:
            s["store"] = str(p.get("store") or "").strip()

    rows = []
    for key in sorted(per):
        s = per[key]
        if market_for and s["store"]:
            try:
                s["market"] = str(market_for(s["store"]) or "").strip()
            except Exception:
                s["market"] = ""
        # ── THE THREE STATES ──────────────────────────────────────────────────────────────────
        pre = (preattributed_earned or {}).get(key)
        if pre is not None:
            # Already attributed per rep by the feed itself. A zero here IS measured: the feed named
            # this rep and paid nothing against them.
            s["carrier_earned"] = round(_f(pre), 2)
            s["carrier_earned_state"] = (EARNED_STATE_REPORTED if round(_f(pre), 2) != 0.0
                                         else EARNED_STATE_MEASURED_ZERO)
            s["carrier_earned_reason"] = EARNED_REASON_PREATTRIBUTED
        elif not statement_loaded:
            s["carrier_earned"] = None
            s["carrier_earned_state"] = EARNED_STATE_NOT_REPORTED
            s["carrier_earned_reason"] = EARNED_REASON_NO_FEED
        elif s["activations_sold"] and s["activations_sold"] == s["activations_without_device_key"]:
            s["carrier_earned"] = None
            s["carrier_earned_state"] = EARNED_STATE_NOT_REPORTED
            s["carrier_earned_reason"] = EARNED_REASON_NO_DEVICE_KEY
        elif s["_any_present"] and round(s["_earned"], 2) != 0.0:
            s["carrier_earned"] = round(s["_earned"], 2)
            s["carrier_earned_state"] = EARNED_STATE_REPORTED
            s["carrier_earned_reason"] = EARNED_REASON_PAID
        elif s["activations_sold"]:
            s["carrier_earned"] = round(s["_earned"], 2)
            s["carrier_earned_state"] = EARNED_STATE_MEASURED_ZERO
            s["carrier_earned_reason"] = (EARNED_REASON_PAID if s["_any_present"]
                                          else EARNED_REASON_ABSENT)
        else:
            # Paid an employee with no sold activation in this window at all — the carrier side was
            # never looked up for them, so it is an absence, not a zero.
            s["carrier_earned"] = None
            s["carrier_earned_state"] = EARNED_STATE_NOT_REPORTED
            s["carrier_earned_reason"] = EARNED_REASON_ABSENT
        if (s["carrier_earned"] is not None and s["employee_paid"] is not None):
            s["difference"] = round(s["carrier_earned"] - s["employee_paid"], 2)
            s["difference_state"] = DIFFERENCE_STATE_COMPUTED
        s.pop("_earned", None)
        s.pop("_any_present", None)
        rows.append(s)

    reported = [r for r in rows if r["carrier_earned_state"] == EARNED_STATE_REPORTED]
    measured_zero = [r for r in rows if r["carrier_earned_state"] == EARNED_STATE_MEASURED_ZERO]
    not_reported = [r for r in rows if r["carrier_earned_state"] == EARNED_STATE_NOT_REPORTED]
    paid_rows = [r for r in rows if r["employee_paid"] is not None]
    totals = {
        # Two separate totals. Deliberately NOT combined, and the key names say which ledger.
        "carrier_earned_reported": round(sum(_f(r["carrier_earned"]) for r in reported), 2),
        "carrier_earned_reps_reported": len(reported),
        "carrier_earned_reps_measured_zero": len(measured_zero),
        "carrier_earned_reps_not_reported": len(not_reported),
        "employee_paid_total": round(sum(_f(r["employee_paid"]) for r in paid_rows), 2),
        "employee_paid_reps": len(paid_rows),
        "activations_sold": sum(r["activations_sold"] for r in rows),
        "activations_in_statement": sum(r["activations_in_statement"] for r in rows),
        "activations_without_device_key": sum(r["activations_without_device_key"] for r in rows),
        "open_no_rule": sum(r["open_no_rule"] for r in rows),
        # The difference total covers ONLY the reps where both sides are measured, and says how many.
        "difference_over_measured_reps": round(
            sum(_f(r["difference"]) for r in rows if r["difference_state"] == DIFFERENCE_STATE_COMPUTED), 2),
        "difference_measured_reps": sum(
            1 for r in rows if r["difference_state"] == DIFFERENCE_STATE_COMPUTED),
    }
    meta = {
        "books_to": [],                      # READ-ONLY: this report books nothing, anywhere.
        "earned_shape": earned_shape,
        "preattributed_reps": len(preattributed_earned or {}),
        "earnings_columns": list(columns or []),
        "payout_sign": (1 if _f(payout_sign) > 0 else -1),
        "statement_loaded": bool(statement_loaded),
        "difference_label": DIFFERENCE_LABEL,
        "difference_note": DIFFERENCE_NOTE,
        "earned_state_notes": EARNED_STATE_NOTES,
        "earned_reasons": EARNED_REASONS,
        "basis_note": (
            "The sold universe, the device→rep attribution and the paid/unpaid evidence are the SAME "
            "ones the Pay Discrepancy report uses (ma_recon), so the two reports can never disagree "
            "about what was sold or what counts as paid."),
    }
    if not statement_loaded and not (preattributed_earned or {}):
        meta["carrier_side"] = (
            "NOT REPORTED — no carrier statement rows loaded for this window. Every earned figure "
            "below is absent, not zero. Load the master-agent commission statement for this period "
            "and the carrier column populates.")
    return {"rows": rows, "totals": totals, "meta": meta}
