"""THE DAILY PORT-OUT FRAUD REPORT — a port-in that leaves again before it has paid for itself.

OWNER DIRECTIVE 2026-09-28, verbatim: *"port in is activation and port port out within 30 days is a
gnale of fraud or before the second payment is a signal of fraud , either customer initiated or sales
rep initiated as the phones are cheaper on new aCTIVATION WITH port in ,so it is also important to
report how much acessories were sold with that activation , if it is below $50 then it could be a
sales rep driven and that shoudl be on top of a daily fraud report being sent to all market managers
and above via whats app and email - with a big red mark and open urgently"*.

THE ECONOMIC CLAIM BEHIND IT, because it is what makes the signal a signal: a handset is discounted on
a NEW ACTIVATION WITH PORT-IN. A line that ports in, takes the discount and ports straight back out has
consumed the subsidy without the subscriber life that pays for it. Whether the customer or the rep
drove it is the question the accessory column is there to inform — a port-in rung with no accessory
attached looks more like a number moved to harvest a discount than like a person buying a phone.

═══ WHAT THIS MODULE IS NOT ═════════════════════════════════════════════════════════════════════════
It is NOT a second churn derivation. "Did this line stay?" already has a home —
`marketing/event_sales.line_feed_state` (index §23s.5), the three-state derivation whose third state,
`unmatched`, is never churn and never in a denominator. That function was EXTRACTED from
`event_sales.evaluate_line` in this same change precisely so this report could be ONE MORE CALLER of it
(CLAUDE.md, "one fact, one home, dereferenced — never copied"). This module adds exactly two things the
retention report does not answer: WHEN the port-out happened relative to OUR sale, and what was sold
alongside it.

Likewise it classifies nothing itself:
  · a line is a PORT-IN because `line_class.activation_class(row, rules) == 'port'` — the one
    activation predicate, per-org config (§19.31);
  · a line is an ACCESSORY because the caller passes the verdict from the org's own accessory
    resolution (`router._is_accessory` / `_is_setup_fee` over `_accessory_config`) — the same gate the
    Sales Report's `accessory_rev` uses, so "accessories sold with that activation" is the same dollar
    the rest of the platform calls accessory revenue.

═══ THE DATING PROBLEM, AND WHY IT IS A STATE RATHER THAN A GUESS ═══════════════════════════════════
The trigger is "within 30 days", so the report needs two dates.

THE ACTIVATION DATE IS OURS. It is the sale's `trans_date` — the owner's own standing ruling that *"the
source of truth is the transaction done in thr store"* (§19.31). It is present on every line we rang,
so the activation half of the window is never in doubt. Reading the carrier's `mi_activation_date`
instead would have thrown that away: it is carried on only 7,803 of 35,134 PORTED-OUT feed rows.

THE PORT-OUT DATE IS THE CARRIER'S, and it is frequently absent. Three ways to get it, in order, and
the report says which one answered:
  1. `mi_deactivation_date` — an explicit date. EXACT.
  2. `residual_transfer_out_date` — the residual leg's own out-date, the same event seen from the
     money side. EXACT.
  3. THE SNAPSHOT TRANSITION. `raw_mi` is a MONTHLY snapshot (§23s.5's grain note), so a line ACTIVE in
     one loaded month and PORTED-OUT in a later one ported out INSIDE that window. That BOUNDS the date
     without inventing one. A bound that lies entirely inside the window flags; a bound entirely
     outside it clears; a bound that STRADDLES the boundary is UNDECIDABLE and says so.

There is no fourth way, and the report does not manufacture one. A PORTED-OUT line with no date and no
earlier active snapshot is `undecidable` — counted, named, and reported in the headline beside the
flagged count, never dropped and never assumed innocent.

═══ THE NEGATIVE LIFESPAN — the trap this report would otherwise walk into ══════════════════════════
MEASURED on live house data 2026-09-28: of the port-in activations that can be dated on both ends, one
has a port-out date **17 days BEFORE** our sale. A bare `days <= 30` test flags that as the single worst
case in the report — it is "within 30 days" by arithmetic. It is not fraud; it is a mobile number that
belonged to somebody else before it belonged to this customer, and the feed row is that number's
earlier life. Recycled numbers are normal.

So a non-positive lifespan is `undecidable` with its own reason, NEVER flagged. This is the whole house
rule in miniature: the report never guesses a reason, and the absence of a business rule that explains
a row is itself reported rather than papered over.

RULE TWO. No carrier, tenant, product or store name appears here. The window, the accessory floor and
the second-payment boundary are per-org config with house defaults, resolved by `resolve_rules`.

stdlib only, DB-free, no money is computed or moved. Proof: `backend/harness_portout_fraud.py`.
"""
from datetime import date, timedelta

# The retention derivation's OWN vocabulary, imported rather than re-spelled. A reason word that means
# "we could not look this line up" must mean the same thing on both reports or the two will drift.
try:                                                            # pragma: no cover - import shape
    from app.modules.marketing.event_sales import (
        STATE_UNMATCHED, UNMATCHED_REASONS, REASON_NO_MDN, REASON_FEED_NOT_LOADED,
        line_feed_state, is_active_status,
    )
except Exception:                                               # pragma: no cover - harness path
    STATE_UNMATCHED, REASON_NO_MDN = "unmatched", "no_mdn_on_sale_row"
    REASON_FEED_NOT_LOADED, UNMATCHED_REASONS = "subscriber_feed_not_loaded", {}
    line_feed_state = is_active_status = None


# ══════════════════════════════════════════════════════════════════════════════════════════════════
# 1. CONFIG — RULE TWO. Per org, house defaults, no literal in a branch.
# ══════════════════════════════════════════════════════════════════════════════════════════════════
#: The owner's numbers as the HOUSE DEFAULT — a default, not a constant. A tenant whose plan bills on a
#: different cycle, or whose accessory mix makes $50 the wrong floor, overrides the row; nothing here
#: is reachable by editing code.
HOUSE_RULES = {
    # "port out within 30 days ... is a signal of fraud"
    "window_days": 30,
    # "how much acessories were sold with that activation , if it is below $50 then it could be a
    # sales rep driven". A FLOOR ON THE ATTRIBUTION, never on the trigger — see (3).
    "accessory_floor": 50.0,
    # "or before the second payment". DELIBERATELY UNSET AT HOUSE LEVEL — see SECOND_PAYMENT_NOTE.
    "second_payment_days": None,
    # Which activation classes this report watches. The owner named port-in; the mechanism is not
    # limited to it, and a tenant that wants new activations watched too sets this.
    "watch_classes": ["port"],
}

#: WHY THE SECOND-PAYMENT BOUNDARY IS NOT SILENTLY SET TO 30.
#: The owner gave TWO triggers — "within 30 days" OR "before the second payment". On a monthly plan
#: those nearly coincide, and that near-coincidence is exactly the temptation: implementing one and
#: calling it the other would let a report claim to answer a question it has never asked. The platform
#: holds no field on the sale line or the subscriber row that states when a line's second payment fell
#: due, and the installment schedule covers device financing, not the plan's MRC cycle. So the boundary
#: is a NAMED, RESOLVABLE rule that is UNCONFIGURED, and every payload says so. Set
#: `second_payment_days` and it becomes a second trigger; leave it and the report reports its own
#: silence. Index §19.32, and the house rule it follows: absence of a business rule is itself reported.
SECOND_PAYMENT_NOTE = (
    "The owner named a second trigger — a port-out BEFORE THE SECOND PAYMENT. No field on the sale "
    "line or the subscriber row states when a line's second payment fell due, so this report does NOT "
    "evaluate that trigger and does not quietly treat the %d-day window as a stand-in for it. Rows "
    "that a second-payment rule would have caught and the day window does not are NOT in the flagged "
    "count. Configure `second_payment_days` to switch the trigger on.")


def resolve_rules(raw=None):
    """The org's rules over the house defaults. An absent or unusable value falls back to house —
    missing beats wrong (§19.26), and a window of 0 or a negative floor would silently empty or flood
    the report rather than failing loudly."""
    out = dict(HOUSE_RULES)
    out["watch_classes"] = list(HOUSE_RULES["watch_classes"])
    r = raw if isinstance(raw, dict) else {}

    w = r.get("window_days")
    try:
        if w is not None and int(w) > 0:
            out["window_days"] = int(w)
    except (TypeError, ValueError):
        pass

    f = r.get("accessory_floor")
    try:
        if f is not None and float(f) >= 0:
            out["accessory_floor"] = float(f)
    except (TypeError, ValueError):
        pass

    s = r.get("second_payment_days")
    try:
        out["second_payment_days"] = int(s) if s is not None and int(s) > 0 else None
    except (TypeError, ValueError):
        out["second_payment_days"] = None

    c = r.get("watch_classes")
    if isinstance(c, (list, tuple)) and [str(x).strip() for x in c if str(x).strip()]:
        out["watch_classes"] = [str(x).strip() for x in c if str(x).strip()]
    return out


# ══════════════════════════════════════════════════════════════════════════════════════════════════
# 2. THE STATES. Four, and the fourth is never a finding.
# ══════════════════════════════════════════════════════════════════════════════════════════════════
STATE_FLAGGED = "flagged"            # ported out INSIDE the window, on a date we can stand behind
STATE_CLEARED = "cleared"            # ported out, demonstrably OUTSIDE the window
STATE_RETAINED = "retained"          # looked up and has not ported out
STATE_UNDECIDABLE = "undecidable"    # could not be looked up, or could not be dated. NOT a finding.
STATES = (STATE_FLAGGED, STATE_CLEARED, STATE_RETAINED, STATE_UNDECIDABLE)

#: Dating reasons this report owns, on top of the lookup reasons it inherits from the retention
#: derivation. Each is a different real-world thing with a different fix.
REASON_NO_PORTOUT_DATE = "port_out_date_not_carried"
REASON_STRADDLES = "port_out_window_straddles_boundary"
REASON_BEFORE_ACTIVATION = "port_out_date_precedes_our_sale"
REASON_NOT_ELAPSED = "window_has_not_elapsed_yet"
REASON_NO_SALE_DATE = "no_trans_date_on_sale_row"

UNDECIDABLE_REASONS = {
    REASON_NO_PORTOUT_DATE: (
        "The feed says this line is PORTED-OUT but carries no deactivation date and no residual "
        "transfer-out date, and no earlier loaded snapshot shows it active — so there is nothing to "
        "measure 30 days from. The port-out is real; its DATE is not knowable from what we hold."),
    REASON_STRADDLES: (
        "The port-out date is known only to the month, from the snapshot the line went from active to "
        "ported-out in, and that month STRADDLES the window boundary — part of it is inside 30 days "
        "and part outside. Calling it either way would be a guess."),
    REASON_BEFORE_ACTIVATION: (
        "The feed's port-out date falls BEFORE the sale we are measuring from. That is a recycled "
        "mobile number — the feed row is the number's earlier life with a different subscriber — or "
        "the dates disagree. Either way it is not this sale porting out, and a bare 'within 30 days' "
        "test would have flagged it as the worst case in the report."),
    REASON_NOT_ELAPSED: (
        "The window has not run out yet: this line was sold too recently for the question to have an "
        "answer. It is not a clean line, it is an unanswered one, and it will be answered on its own."),
    REASON_NO_SALE_DATE: (
        "The sale row carries no transaction date, so there is no point to measure the window from."),
}


def reason_note(reason):
    """The prose for a reason from EITHER vocabulary — this report's own, or the retention
    derivation's lookup reasons, which it inherits verbatim rather than re-wording."""
    return (UNDECIDABLE_REASONS.get(reason)
            or (UNMATCHED_REASONS or {}).get(reason)
            or "")


# ══════════════════════════════════════════════════════════════════════════════════════════════════
# 3. THE ATTRIBUTION SIGNAL — and why it is not a trigger
# ══════════════════════════════════════════════════════════════════════════════════════════════════
# The owner: a port-in with under $50 of accessories "could be a sales rep driven". MEASURED on live
# house data 2026-09-28 before wiring it: 50.8% of ALL port-in invoices carry under $50 of accessories
# (36.0% carry none at all; median $47.70). So the accessory floor ALONE names half the port-ins in the
# business, and a report triggered on it would be noise with a red mark on it.
#
# It is therefore an ATTRIBUTION COLUMN on rows the port-out already flagged, exactly as the owner
# worded it ("it is ALSO important to report how much accessories were sold with that activation"):
# the port-out inside the window is the finding, the accessory dollar says which way to look first.
ATTRIB_REP_DRIVEN = "accessory_below_floor"
ATTRIB_CUSTOMER_DRIVEN = "accessory_at_or_above_floor"
ATTRIB_UNKNOWN = "accessory_not_measurable"

ATTRIBUTION_NOTES = {
    ATTRIB_REP_DRIVEN: (
        "Under the configured accessory floor. The owner's reading: a discounted port-in handset that "
        "left again, with little or nothing sold alongside it, leans REP-DRIVEN. It is a direction to "
        "look in, not a conclusion about a person."),
    ATTRIB_CUSTOMER_DRIVEN: (
        "At or above the floor. A real basket was sold with this activation, so the port-out leans "
        "CUSTOMER-DRIVEN rather than rep-driven. Still flagged — the subsidy still left."),
    ATTRIB_UNKNOWN: (
        "The accessory total for this invoice could not be measured (no invoice id on the sale line, "
        "so its other lines cannot be gathered). Reported as unknown, never as $0.00 — a zero here "
        "would read as 'sold nothing', which is the most incriminating value the column has."),
}


def attribution(accessory_total, rules):
    """Which way to look first. `None` accessory total is UNKNOWN, never 0.0 — the difference matters
    because 0.0 is this column's most accusing value and an unmeasured line has not earned it."""
    if accessory_total is None:
        return ATTRIB_UNKNOWN
    return (ATTRIB_REP_DRIVEN if float(accessory_total) < float(rules["accessory_floor"])
            else ATTRIB_CUSTOMER_DRIVEN)


# ══════════════════════════════════════════════════════════════════════════════════════════════════
# 4. DATING
# ══════════════════════════════════════════════════════════════════════════════════════════════════
BASIS_DEACTIVATION = "mi_deactivation_date"
BASIS_TRANSFER_OUT = "residual_transfer_out_date"
BASIS_SNAPSHOT = "snapshot_transition"


def _d(v):
    try:
        return date.fromisoformat(str(v)[:10])
    except (TypeError, ValueError):
        return None


def _month_bounds(period_key):
    """First and last day of a 'YYYY-MM' snapshot month."""
    try:
        y, m = int(str(period_key)[:4]), int(str(period_key)[5:7])
        first = date(y, m, 1)
    except (TypeError, ValueError):
        return None, None
    nxt = date(y + 1, 1, 1) if m == 12 else date(y, m + 1, 1)
    return first, nxt - timedelta(days=1)


def port_out_date(row, first_ported_key=None):
    """WHEN the line left → `(basis, exact_date, earliest, latest)`.

    An EXACT basis returns the same date as earliest and latest. The SNAPSHOT basis returns no exact
    date and the month's two ends, because that is honestly all a monthly snapshot knows. `(None, …)`
    when the feed carries neither.
    """
    d = _d((row or {}).get("mi_deactivation_date"))
    if d:
        return BASIS_DEACTIVATION, d, d, d
    d = _d((row or {}).get("residual_transfer_out_date"))
    if d:
        return BASIS_TRANSFER_OUT, d, d, d
    lo, hi = _month_bounds(first_ported_key) if first_ported_key else (None, None)
    if lo and hi:
        return BASIS_SNAPSHOT, None, lo, hi
    return None, None, None, None


def is_ported_out(status):
    """The feed's own port-out verdict. A port-out is a NAMED status, not merely 'not active' — an
    involuntary suspend is also not active and is emphatically not a port-out, so this must not be
    written as `not is_active_status(...)`."""
    return "ported" in str(status or "").strip().lower().replace("_", "-")


# ══════════════════════════════════════════════════════════════════════════════════════════════════
# 5. ONE LINE → ITS VERDICT
# ══════════════════════════════════════════════════════════════════════════════════════════════════
def evaluate(line, snapshots, rules, latest_key=None, as_of=None, accessory_total=None,
             first_ported_key=None):
    """ONE port-in activation → `{state, reason, days, basis, attribution, …}`. PURE.

    `line`      — our sale line: `trans_date`, `mdn`/`serial_1`, store, rep, invoice id.
    `snapshots` — `{period_key: {'index': …, 'loaded': bool}}`, the retention derivation's own shape.
    `first_ported_key` — the earliest loaded snapshot month in which this line reads PORTED-OUT, which
                  the caller knows because it holds every month; used only for the SNAPSHOT basis.

    EVERY exit that is not `flagged`/`cleared`/`retained` carries a reason. There is no silent drop.
    """
    sold = _d(line.get("trans_date"))
    out = {"state": None, "reason": None, "days": None, "basis": None, "status": None,
           "sold_on": sold.isoformat() if sold else None, "ported_on": None,
           "ported_between": None, "decidable": True,
           "accessory_total": accessory_total,
           "attribution": attribution(accessory_total, rules),
           "window_days": rules["window_days"]}

    if not sold:
        return _undecidable(out, REASON_NO_SALE_DATE)

    # THE LOOKUP — the shared derivation, not a copy of it.
    if line_feed_state is None:                                  # pragma: no cover - import guard
        return _undecidable(out, REASON_FEED_NOT_LOADED)
    if not str(line.get("mdn") or "").strip() and not str(line.get("serial_1") or "").strip():
        return _undecidable(out, REASON_NO_MDN)

    keys = sorted(k for k, v in (snapshots or {}).items() if v.get("loaded"))
    # WHICH SNAPSHOT TO ASK. The retention report asks the LATEST month, because its question is "is
    # this line still with us now". THIS report's question is "did this line EVER port out", and those
    # are different questions over a monthly feed: a line that ported out in April and is gone from
    # September's snapshot reads `dropped_out_of_the_feed` in the latest month and its port-out — the
    # very thing being looked for — is swallowed as an absence. MEASURED on live house data
    # 2026-09-28: asking only the latest month put 323 lines in that state. So when the caller knows a
    # month in which this line READS PORTED-OUT, the lookup is made THERE; the latest month is the
    # fallback for every line that never does.
    key = first_ported_key or latest_key or (keys[-1] if keys else None)
    v = line_feed_state(line, snapshots, key, earlier_keys=keys)
    if v["state"] == STATE_UNMATCHED:
        return _undecidable(out, v["reason"])

    out["status"] = v["status"]
    if not is_ported_out(v["status"]):
        out["state"] = STATE_RETAINED
        return out

    basis, exact, lo, hi = port_out_date(v["row"], first_ported_key=first_ported_key)
    out["basis"] = basis
    if not basis:
        return _undecidable(out, REASON_NO_PORTOUT_DATE)

    w = int(rules["window_days"])
    if exact is not None:
        out["ported_on"] = exact.isoformat()
        days = (exact - sold).days
        out["days"] = days
        # THE RECYCLED-NUMBER TRAP. A non-positive lifespan is not the worst case in the report; it is
        # not this sale's port-out at all. Never flagged — see the module docstring.
        if days <= 0:
            return _undecidable(out, REASON_BEFORE_ACTIVATION)
        out["state"] = STATE_FLAGGED if days <= w else STATE_CLEARED
        return out

    # SNAPSHOT BASIS — a bounded window, and the boundary decides honestly.
    out["ported_between"] = [lo.isoformat(), hi.isoformat()]
    lo_days, hi_days = (lo - sold).days, (hi - sold).days
    out["days"] = None
    if hi_days <= 0:
        return _undecidable(out, REASON_BEFORE_ACTIVATION)
    if hi_days <= w:
        out["state"] = STATE_FLAGGED            # the whole bound sits inside the window
        return out
    if lo_days > w:
        out["state"] = STATE_CLEARED            # the whole bound sits outside it
        return out
    return _undecidable(out, REASON_STRADDLES)  # it straddles — neither answer is earned


def _undecidable(out, reason):
    out["state"] = STATE_UNDECIDABLE
    out["reason"] = reason
    out["reason_note"] = reason_note(reason)
    out["decidable"] = False
    return out


def not_yet_elapsed(line, rules, as_of):
    """A line sold too recently for the window to have run out. Asked BEFORE `evaluate`, because a
    clean answer on an unelapsed window is not a clean line — it is an unasked question."""
    sold, today = _d(line.get("trans_date")), _d(as_of)
    if not sold or not today:
        return False
    return (today - sold).days < int(rules["window_days"])


# ══════════════════════════════════════════════════════════════════════════════════════════════════
# 6. THE REPORT — and its headline, which must confess what it cannot see
# ══════════════════════════════════════════════════════════════════════════════════════════════════
def report(lines, snapshots, rules, latest_key=None, as_of=None, accessory_by_invoice=None,
           first_ported_by_line=None):
    """The whole daily report. PURE.

    THE HEADLINE RULE, and it is the reason this function exists rather than a bare list comprehension:
    **the count it could not decide is printed beside the count it flagged, always, at the same size.**
    A fraud report that shows "15 suspicious" while silently unable to see a quarter of the population
    is worse than no report — it converts a blind spot into a clean bill of health. So `summary`
    carries `flagged`, `undecidable`, `coverage_pct` and `undecidable_by_reason`, and a consumer
    rendering the flagged number without the undecidable one is rendering half a fact.
    """
    acc = accessory_by_invoice or {}
    firsts = first_ported_by_line or {}
    rows, by_reason, by_store, by_rep = [], {}, {}, {}

    for line in (lines or []):
        inv = str(line.get("trans_id") or "")
        total = acc.get(inv) if inv in acc else None
        if as_of and not_yet_elapsed(line, rules, as_of):
            v = _undecidable({"state": None, "reason": None, "days": None, "basis": None,
                              "status": None, "sold_on": str(line.get("trans_date") or "")[:10] or None,
                              "ported_on": None, "ported_between": None, "decidable": True,
                              "accessory_total": total, "attribution": attribution(total, rules),
                              "window_days": rules["window_days"]}, REASON_NOT_ELAPSED)
        else:
            v = evaluate(line, snapshots, rules, latest_key=latest_key, as_of=as_of,
                         accessory_total=total, first_ported_key=firsts.get(_line_key(line)))
        row = {**{k: line.get(k) for k in ("store", "salesperson", "trans_id", "trans_date",
                                           "line_class", "product_desc")}, **v}
        rows.append(row)
        if v["state"] == STATE_UNDECIDABLE:
            by_reason[v["reason"]] = by_reason.get(v["reason"], 0) + 1
        if v["state"] == STATE_FLAGGED:
            by_store[row.get("store") or ""] = by_store.get(row.get("store") or "", 0) + 1
            by_rep[row.get("salesperson") or ""] = by_rep.get(row.get("salesperson") or "", 0) + 1

    n = len(rows)
    counts = {s: sum(1 for r in rows if r["state"] == s) for s in STATES}
    decided = n - counts[STATE_UNDECIDABLE]
    flagged_rows = [r for r in rows if r["state"] == STATE_FLAGGED]
    return {
        "rows": rows,
        "flagged": flagged_rows,
        "summary": {
            "port_in_activations": n,
            "flagged": counts[STATE_FLAGGED],
            "cleared": counts[STATE_CLEARED],
            "retained": counts[STATE_RETAINED],
            "undecidable": counts[STATE_UNDECIDABLE],
            "decidable": decided,
            "coverage_pct": (round(100.0 * decided / n, 1) if n else None),
            "undecidable_by_reason": dict(by_reason),
            "flagged_by_store": dict(by_store),
            "flagged_by_rep": dict(by_rep),
            "flagged_below_accessory_floor": sum(
                1 for r in flagged_rows if r["attribution"] == ATTRIB_REP_DRIVEN),
            "urgent": bool(counts[STATE_FLAGGED]),
        },
        "rules": dict(rules),
        "basis": basis_note(rules),
        "undecidable_reasons": {k: reason_note(k) for k in sorted(by_reason)},
    }


def _line_key(line):
    m = str(line.get("mdn") or "").replace(".0", "").strip()
    return m or ("s:" + str(line.get("serial_1") or "").replace(".0", "").strip())


def basis_note(rules):
    """What the report is and is not, travelling with every payload so a red-marked WhatsApp message
    can never be read as more certain than the data under it."""
    return {
        "headline": ("Port-in activations that ported OUT again within %d days — the discounted "
                     "handset left before the subscriber life that pays for it." % rules["window_days"]),
        "activation_date": ("OURS: the store's own transaction date, per the owner's standing ruling "
                            "that the transaction in the store is the source of truth. It is present "
                            "on every line we rang, so the window's start is never in doubt."),
        "port_out_date": ("THE CARRIER'S, and often absent. Taken from mi_deactivation_date, else the "
                          "residual transfer-out date, else BOUNDED by the monthly snapshot the line "
                          "went from active to ported-out in. A port-out we cannot date is reported as "
                          "UNDECIDABLE, never assumed innocent and never assumed guilty."),
        "accessory": ("The invoice's accessory total through the org's OWN accessory definition — the "
                      "same gate the Sales Report's accessory revenue uses. It is an attribution "
                      "column on an already-flagged row, NOT a trigger: measured live, under-$50 alone "
                      "names about half of all port-ins and would be noise."),
        "second_payment": SECOND_PAYMENT_NOTE % rules["window_days"],
        "not_a_verdict": ("A flagged row is a line to LOOK AT. It names no fraud and accuses no person; "
                          "customer-initiated and rep-initiated port-outs look identical here, which is "
                          "why the accessory column is reported beside it rather than in place of it."),
        "reuses": ("The lookup and its three states are marketing/event_sales.line_feed_state — the "
                   "same derivation the event subscriber-retention report uses (index §23s.5). This "
                   "report adds the dating and the accessory column; it does not re-answer 'did this "
                   "line stay?'."),
    }
