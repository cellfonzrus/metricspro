"""BILL-PAY DECLARATION EXCEPTIONS — the morning digest for DM and above (owner ask 2026-10-03).

Owner, verbatim: *"diff of the pos data and th rep defined data needs to be investigated why those
errors take place, the system should create that report and send it to the dm and all above via
whats app and email the next morning at 1030 am - nothing hardcoded"*.

WHAT THIS ALERTS ON, AND WHAT IT DELIBERATELY DOES NOT. The investigation behind §47.12 found that
nine store-days in ten disagreed between the rep's declared bill-payment cash and the POS figure, and
that the dominant cause was the customer service fee being excluded from the POS basis — a defect in
the comparison, not in anybody's declaration. That is FIXED at the source (`metric_recon
.pos_billpay_cash`), so this digest compares the corrected figures and carries only what is left:

  under_declared   the declaration is materially BELOW the POS basis — bill-pay cash the store took
                   and did not declare, so it was banked as store cash or not banked at all.
  over_declared    the declaration is materially ABOVE it — real sales cash labelled as bill-pay
                   cash, which removes it from what the DM is asked to collect.

AND THE ONE IT REFUSES TO ALERT ON:

  no_pos_figure    the sales feed covered no bill payments for that store-day, so there is nothing
                   to compare against. This is NEVER an alert. A missing feed is a pipeline failure
                   with its own surface (§20 import health), and treating it as a declaration error
                   would blame a store for a report that did not arrive — the silent-zero defect
                   class this file exists on the right side of. It is COUNTED in the digest footer so
                   a thin digest is never read as a healthy estate, which is the §15z rule verbatim.

AND THE TWO IT REFUSES BECAUSE THE BASIS ITSELF IS NOT SAFE TO COMPARE (owner ask 2026-10-03: "so
there is not balmnket error on a different tenant"). A store-day with real bill-pay activity and no
service-fee line means different things depending on whether the org charges such a fee, and the
org's DECLARED answer — not an inference from its data — decides which:

  fee_line_missing       the org says it DOES charge a bill-payment fee and none rang for this
                         store-day. The POS basis is therefore understated, so comparing it would
                         report a shortfall that is really a FEED defect. Refused, counted, and the
                         footer names it as a feed gap with the remedy — never charged to the rep.
  fee_policy_unanswered  nobody has said whether the org charges one. Neither reading of the missing
                         line can honestly be preferred, so the store-day is not compared, and the
                         footer asks the question rather than guessing an answer. This is the DEFAULT
                         for an unconfigured tenant, which is why a new tenant can never be
                         blanket-accused by another tenant's fee vocabulary.

Which states are safe to compare is NOT decided here: it is `metric_recon.billpay_basis_comparable`,
the one home for the fee policy, dereferenced. A second copy of that judgement at this call site is
the divergence the house rules forbid.

NOTHING HARDCODED (RULE TWO, and the owner's own words). The send time, the dollar tolerance, the
channels and the on/off switch are per-tenant config rows with house defaults; the recipients are
resolved from the org tree, never a typed list; and no carrier, tenant, store or product name appears
in this file. The 10:30 default is the mig-433 convention, not a number chosen here.

NO SECOND FAN-OUT, NO SECOND DEDUP, NO SECOND SCHEDULER. Recipients, one-digest-per-manager, the
unreachable-recipient skip, the ref_key spelling and the due-time rule all come from
`commcalc/manager_digest.py`; the dedup rows are the existing `storeops.alert_log` through the
existing `_lateness_already_sent` / `_lateness_record_sent` helpers. This module supplies only what
is genuinely per-alert: the classification, the thresholds and how a digest reads.

PURE: stdlib only — no DB, no framework, no network. Registered in docs/SYSTEM_DATA_FLOW_INDEX.md.
"""

ALERT_SCOPE = "billpay_declaration"

# House defaults. A tenant row overrides each one; absent/unreadable config resolves to exactly
# these, so the alert behaves identically before its migration is applied.
HOUSE_CONFIG = {
    "enabled": False,          # SAFE BY DEFAULT: nothing sends on deploy (the mig-905 posture).
    "send_time": "10:30",      # tenant-local HH:MM; the mig-433 convention, normalised by the home.
    "tolerance": 1.00,         # dollars; below this a difference is rounding, not an exception.
    "channels": ("email", "whatsapp"),
    "lookback_days": 1,        # "the next morning" — yesterday's store-days.
    "max_rows": 25,            # per digest; the rest are counted, never silently dropped.
}

# The classification vocabulary, named once. A caller reads these; it never spells a status itself.
CLASS_UNDER = "under_declared"
CLASS_OVER = "over_declared"
CLASS_AGREE = "agree"
CLASS_NO_POS = "no_pos_figure"
# The two the fee policy refuses: the basis is unsafe to compare, and for two different reasons with
# two different owners (a broken feed, and an unanswered config question). They are NOT merged into
# one class, because a digest that cannot say which remedy applies sends the manager nowhere.
CLASS_FEE_MISSING = "fee_line_missing"
CLASS_FEE_POLICY_UNSET = "fee_policy_unanswered"
GAP_CLASSES = (CLASS_UNDER, CLASS_OVER, CLASS_AGREE, CLASS_NO_POS,
               CLASS_FEE_MISSING, CLASS_FEE_POLICY_UNSET)
# The two that are a person's problem. `no_pos_figure` is a PIPELINE problem and is refused here by
# name, the way `zero_sales.GAP_REFUSED` refuses counting an unmeasured day as a zero; the two fee
# classes are refused for the same reason — an unsafe basis is never a rep's error.
ALERTABLE = (CLASS_UNDER, CLASS_OVER)
REFUSED_AS_ALERT = (CLASS_AGREE, CLASS_NO_POS, CLASS_FEE_MISSING, CLASS_FEE_POLICY_UNSET)
# The refusals a manager must be TOLD about, in the order the footer states them. `agree` is not one
# of them: a matching store-day is good news, not something that could not be assessed.
UNASSESSED = (CLASS_NO_POS, CLASS_FEE_MISSING, CLASS_FEE_POLICY_UNSET)
# Not a class — a store-day that WAS compared and still says something about the config: a fee line
# rang where the org declared it charges none. Counted separately so the footer can name it without
# it ever being mistaken for a declaration exception.
CLASS_FEE_UNEXPECTED_ADVISORY = "fee_rang_though_policy_says_none"


def _f(v):
    try:
        return round(float(v or 0), 2)
    except (TypeError, ValueError):
        return 0.0


def _fee_conflicts_with_policy(fee_state):
    """Does this store-day's fee state contradict the org's declared policy while still leaving the
    basis comparable? Asked of the one home rather than spelled here. PURE."""
    from app.modules.commcalc import metric_recon as _mr
    return fee_state == _mr.FEE_STATE_UNEXPECTED


def resolve_config(tenant_row=None):
    """The tenant's alert config over the house defaults. Every field is validated, and an invalid
    value falls back to its house default rather than refusing to alert or alerting on everything.
    `channels` is normalised by `manager_digest`, the one home for that vocabulary. PURE."""
    from app.modules.commcalc import manager_digest as _md
    row = tenant_row if isinstance(tenant_row, dict) else {}
    out = dict(HOUSE_CONFIG)
    out["enabled"] = bool(row.get("billpay_declaration_alerts_enabled",
                                  HOUSE_CONFIG["enabled"]))
    out["send_time"] = _md.normalize_alert_time(
        row.get("billpay_declaration_alert_time") or HOUSE_CONFIG["send_time"])
    tol = row.get("billpay_declaration_alert_tolerance")
    try:
        tol = abs(float(tol))
    except (TypeError, ValueError):
        tol = None
    out["tolerance"] = HOUSE_CONFIG["tolerance"] if tol is None else round(tol, 2)
    out["channels"] = _md.normalize_channels(
        row.get("billpay_declaration_alert_channels") or HOUSE_CONFIG["channels"])
    look = row.get("billpay_declaration_alert_lookback_days")
    try:
        look = int(look)
    except (TypeError, ValueError):
        look = None
    out["lookback_days"] = HOUSE_CONFIG["lookback_days"] if not look or look < 1 else min(look, 31)
    return out


def classify(declared, pos_basis, tolerance=None, fee_state=None):
    """One store-day → its class. `pos_basis` is `metric_recon.pos_billpay_cash`'s answer, so None
    means the feed covered no bill payments for that store-day. `fee_state` is
    `metric_recon.billpay_fee_state`'s answer for the same store-day — omitted (None) keeps the
    pre-1046 behaviour exactly, so every existing caller is byte-identical.

    ABSENCE IS NOT ZERO, and this is the whole point of the function. `pos_basis is None` is
    `no_pos_figure` — never compared, never alerted. A declaration of $0.00 against a POS basis of
    $0.00 agrees; a declaration of $0.00 against a REAL basis is the biggest exception there is, and
    the two must not collapse into each other.

    AN UNSAFE BASIS IS NOT A REP'S ERROR EITHER. When the org's declared fee policy says this
    store-day's basis cannot be trusted — a fee line the org says it charges did not ring, or nobody
    has said whether it charges one — the store-day is classed by the REASON rather than compared.
    Whether a state is safe is asked of the one home (`billpay_basis_comparable`), never decided here.
    PURE."""
    tol = HOUSE_CONFIG["tolerance"] if tolerance is None else abs(_f(tolerance))
    if pos_basis is None:
        return CLASS_NO_POS
    if fee_state is not None:
        from app.modules.commcalc import metric_recon as _mr
        if not _mr.billpay_basis_comparable(fee_state):
            # The two unsafe states, kept apart because their remedies are. Anything else unsafe a
            # future release adds falls back to the unanswered-question class rather than to a
            # comparison, which is the safe direction: it asks instead of accusing.
            return (CLASS_FEE_MISSING if fee_state == _mr.FEE_STATE_LINE_MISSING
                    else CLASS_FEE_POLICY_UNSET)
    gap = round(_f(declared) - _f(pos_basis), 2)
    if abs(gap) <= tol:
        return CLASS_AGREE
    return CLASS_UNDER if gap < 0 else CLASS_OVER


def gap_of(declared, pos_basis):
    """declared − pos_basis, or None when there is no basis to subtract. Never a fabricated 0.0, so a
    caller cannot render "no difference" for a store-day nobody could measure. PURE."""
    if pos_basis is None:
        return None
    return round(_f(declared) - _f(pos_basis), 2)


def alert_items(store_days, tolerance=None):
    """PURE. The findings for one tenant, newest-first and biggest-first within a day.

    `store_days` is an iterable of {store_code, close_date, declared, pos_basis, fee_cash?,
    bill_txns?, store_name?}. Returns {"items", "counts", "refused"}:

      items     the ALERTABLE findings only, each carrying its class, the two figures, the gap and
                the fee that was added back (so a manager can see the correction rather than wonder
                why the number moved).
      counts    every class, including the ones that are not alerts — the footer's material.
      refused   how many store-days could NOT be assessed, by reason. A thin digest must say why it
                is thin; this is the §15z footer rule, which exists so nobody reads silence as health.
    """
    tol = HOUSE_CONFIG["tolerance"] if tolerance is None else abs(_f(tolerance))
    items, counts = [], {c: 0 for c in GAP_CLASSES}
    advisories = {CLASS_FEE_UNEXPECTED_ADVISORY: 0}
    for sd in (store_days or []):
        d = sd if isinstance(sd, dict) else {}
        basis = d.get("pos_basis")
        state = d.get("fee_state")
        cls = classify(d.get("declared"), basis, tol, fee_state=state)
        counts[cls] += 1
        if state is not None and _fee_conflicts_with_policy(state):
            # COMPARED, and still worth saying: a fee line rang on a store-day whose org says it
            # charges none. The cash is real so the arithmetic stands; it is the CONFIG ROW that is
            # wrong, and a silent correct number would leave it wrong forever.
            advisories[CLASS_FEE_UNEXPECTED_ADVISORY] += 1
        if cls not in ALERTABLE:
            continue
        items.append({
            "store_code": d.get("store_code"),
            "store_name": d.get("store_name") or d.get("store_code"),
            "close_date": str(d.get("close_date") or "")[:10],
            "gap_class": cls,
            "declared": _f(d.get("declared")),
            "pos_basis": _f(basis),
            "gap": gap_of(d.get("declared"), basis),
            "fee_cash": None if d.get("fee_cash") is None else _f(d.get("fee_cash")),
            "fee_state": state,
            "bill_txns": d.get("bill_txns"),
        })
    items.sort(key=lambda it: (it["close_date"], -abs(it["gap"] or 0.0), it["store_code"] or ""),
               reverse=True)
    return {"items": items, "counts": counts, "advisories": advisories,
            "refused": {c: counts[c] for c in UNASSESSED}}


def key_parts(item):
    """The per-alert tail of the dedup key: the store, the day the finding is about, and its class.
    One store-day escalates once per day per recipient, and a store-day that changes CLASS is a
    different finding — which it is: "declared too little" becoming "declared too much" is news."""
    return ((item or {}).get("store_code"), (item or {}).get("close_date"),
            (item or {}).get("gap_class"))


def _money(v):
    return "${:,.2f}".format(_f(v))


def _line(it):
    gap = it.get("gap") or 0.0
    if it.get("gap_class") == CLASS_UNDER:
        verb = "not declared"
        amount = _money(abs(gap))
    else:
        verb = "declared but not in the sales data"
        amount = _money(abs(gap))
    return (it.get("store_name") or it.get("store_code"), it.get("close_date"), verb, amount,
            _money(it.get("declared")), _money(it.get("pos_basis")))


# THE REFUSAL WORDING, ONE HOME. Each unassessed class gets ONE sentence naming what could not be
# judged, why, and whose problem it is — written here once and read by both renderings of the digest,
# because two renderings that word the same morning differently is a digest nobody trusts. `{n}` is
# the count and `{s}` the plural suffix.
UNASSESSED_NOTES = {
    CLASS_NO_POS: ("{n} store-day{s} could not be assessed — the sales data carried no bill payments "
                   "for them, so there was nothing to compare. That is a data-feed gap, not a "
                   "declaration problem, and it is not counted above."),
    CLASS_FEE_MISSING: ("{n} store-day{s} could not be assessed — this company is set to charge a "
                        "bill-payment fee, and no fee line came through on the sales data for those "
                        "days, which makes the comparison figure too low. That is a data-feed gap to "
                        "chase with whoever sends the sales reports, not anything a store did."),
    CLASS_FEE_POLICY_UNSET: ("{n} store-day{s} could not be assessed — nobody has recorded yet "
                             "whether this company charges customers a bill-payment fee, and those "
                             "days show bill payments with no fee line. Until that is answered the "
                             "comparison figure cannot be trusted either way, so no store is being "
                             "asked about them. It is one setting, under Classification settings."),
}

# An ADVISORY was compared normally and still says something — about the configuration, not a store.
ADVISORY_NOTES = {
    CLASS_FEE_UNEXPECTED_ADVISORY: ("On {n} store-day{s} a bill-payment fee line did come through "
                                    "even though this company is recorded as not charging one. The "
                                    "figures above are right either way — that fee is real cash — "
                                    "but the setting should be corrected."),
}


def _notes_from(registry, source, order):
    """PURE. [(key, count)] for every key in `order` the source actually counted, in that order.
    One traversal shared by both the unassessed and the advisory footers, so neither can grow a
    second copy of "which of these do we mention"."""
    out = []
    for key in order:
        n = (source or {}).get(key) or 0
        if n and key in registry:
            out.append((key, int(n)))
    return out


def unassessed_counts(counts=None):
    """PURE. The refusals this digest must state, in `UNASSESSED` order."""
    return _notes_from(UNASSESSED_NOTES, counts, UNASSESSED)


def advisory_counts(advisories=None):
    """PURE. The advisories this digest must state."""
    return _notes_from(ADVISORY_NOTES, advisories, tuple(ADVISORY_NOTES))


def _sentence(registry, cls, n):
    tpl = registry.get(cls)
    if not tpl:
        return ""
    return tpl.format(n=int(n or 0), s="" if int(n or 0) == 1 else "s")


def unassessed_sentence(cls, n):
    """PURE. The ONE sentence for an unassessed class, read by both renderings."""
    return _sentence(UNASSESSED_NOTES, cls, n)


def advisory_sentence(cls, n):
    """PURE. The ONE sentence for an advisory, read by both renderings."""
    return _sentence(ADVISORY_NOTES, cls, n)


def build_digest(name, items, counts=None, max_rows=None, label=None, advisories=None):
    """One recipient's digest: {"subject", "html", "text"}. `text` is the WhatsApp body — the same
    facts in the same order, because two renderings of one digest that disagree is a digest nobody
    trusts. Rows beyond `max_rows` are COUNTED in a trailing line, never dropped in silence. PURE."""
    cap = HOUSE_CONFIG["max_rows"] if max_rows is None else max(1, int(max_rows))
    counts = counts or {}
    shown, hidden = items[:cap], max(0, len(items) - cap)
    total = round(sum(abs(it.get("gap") or 0.0) for it in items), 2)
    under = sum(1 for it in items if it.get("gap_class") == CLASS_UNDER)
    over = len(items) - under
    scope = label or "yesterday"
    subject = "Bill-pay declaration exceptions — {n} store-day{s}, {amt} ({scope})".format(
        n=len(items), s="" if len(items) == 1 else "s", amt=_money(total), scope=scope)

    rows_html = "".join(
        "<tr><td>{0}</td><td>{1}</td><td>{4}</td><td>{5}</td><td><b>{3}</b> {2}</td></tr>".format(
            *_line(it)) for it in shown)
    foot = []
    if hidden:
        foot.append("<p>and {0} more store-day{1} not listed here.</p>".format(
            hidden, "" if hidden == 1 else "s"))
    # The §15z footer rule: say what could NOT be assessed, so a short digest is never mistaken for
    # a clean estate — and say it ONCE, from `UNASSESSED_NOTES`, so the HTML and the WhatsApp text
    # cannot drift into two different accounts of the same morning.
    for _cls, _n_sd in unassessed_counts(counts):
        foot.append("<p><i>{0}</i></p>".format(unassessed_sentence(_cls, _n_sd)))
    for _cls, _n_sd in advisory_counts(advisories):
        foot.append("<p><i>{0}</i></p>".format(advisory_sentence(_cls, _n_sd)))
    agree = counts.get(CLASS_AGREE) or 0
    if agree:
        foot.append("<p><i>{0} store-day{1} matched and are not listed.</i></p>".format(
            agree, "" if agree == 1 else "s"))
    html = (
        "<p>Hello {name},</p>"
        "<p>These store-days have a bill-payment cash declaration that does not match the sales "
        "data, after the customer service fee is accounted for. {u} where cash was not declared, "
        "{o} where more was declared than the sales data shows.</p>"
        "<table border=1 cellpadding=6 cellspacing=0>"
        "<tr><th>Store</th><th>Day</th><th>Declared</th><th>Sales data</th><th>Difference</th></tr>"
        "{rows}</table>{foot}"
    ).format(name=name or "there", u=under, o=over, rows=rows_html, foot="".join(foot))

    text_rows = "\n".join(
        "- {0} {1}: declared {4}, sales data {5} — {3} {2}".format(*_line(it)) for it in shown)
    text_foot = []
    if hidden:
        text_foot.append("and {0} more not listed.".format(hidden))
    for _cls, _n_sd in unassessed_counts(counts):
        text_foot.append(unassessed_sentence(_cls, _n_sd))
    for _cls, _n_sd in advisory_counts(advisories):
        text_foot.append(advisory_sentence(_cls, _n_sd))
    text = ("Bill-pay declaration exceptions ({scope}): {n} store-day{s}, {amt} total.\n"
            "{rows}\n{foot}").format(scope=scope, n=len(items),
                                     s="" if len(items) == 1 else "s", amt=_money(total),
                                     rows=text_rows, foot=" ".join(text_foot)).strip()
    return {"subject": subject, "html": html, "text": text}
