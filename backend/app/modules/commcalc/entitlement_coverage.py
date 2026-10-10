"""AN ENTITLEMENT IS A COUNT OF MONTHS PER STORE — did every one of them arrive? (index §68)

OWNER, verbatim 2026-10-10: *"mdf / ramp up subsidy is being finsihed it is inly for 12 months, if we
did not get for 12 months for each store it should be flagged and the missing month and store to be
poimted out , make it into management overview"*.

WHAT THIS IS. A COMPLETENESS check at **store × month** grain over a declared entitlement window. It
moves no money, books nothing, pays nobody and recomputes no payout: it answers one question —
"which store-month inside a store's entitlement never arrived" — and names the store and the month.

WHY IT IS A NEW QUESTION AND NOT A SECOND COPY OF AN OLD ONE. The platform already answers two
neighbouring questions and this file DEREFERENCES both rather than re-deriving either:

  · `pay_data_quality.statement_month_coverage` (§19.54) — "does ONE feed's month cover every DAY it
    is owed", at DAY grain, for one month. Re-parameterising it to answer store × month across a
    multi-month window would make one function two mechanisms. It is read here for its SHAPE and its
    VOCABULARY, and its three verdict strings are IMPORTED below, never respelled.
  · `feed_period.month_state` / `.period_month_year` — the ONE calendar authority. Re-implementing the
    calendar is the failure mode of a report like this: `month_state` is what stops a month that has
    not finished being reported as missing, and it is the only clock in this file.

THE HARD FACT, AND WHY HALF THIS FILE IS ABOUT NOT ACCUSING ANYBODY. Nothing in any feed says when a
store's entitlement BEGAN, or that it is twelve months long. That is the owner's knowledge. So the
window is a per-org, per-store CONFIG DECLARATION (`term_months` a declared number, `store_windows` a
declared first month per canonical store key — RULE TWO: no store name and no month count in a
branch), and a store whose start month is NOT declared has an UNKNOWN window.

  **A store with no declared start month is `not_measured` WITH A NAMED REASON — never "12 months
  missing".** Inventing a window and reporting twelve absences against it would be a fabricated
  accusation against the carrier, which is the worst failure available here. A store whose 12th month
  has not happened yet is `not_due`, not missing.

THE ASYMMETRY THAT MATTERS MOST, and the precedent it copies. `device_reimb_recon` counts "claimed,
not paid, and that month's feed arrived short" SEPARATELY from "claimed, not paid, confirmed"
(`unconfirmed_claimed_not_paid_*` beside `claimed_not_paid_*`), never netted into one number. The same
split is built here, because **"this store did not get its month" and "we never received a feed for
that month" are different facts** and only the first is something a human should act on:

  · `missing`             — the month's entitlement run DEMONSTRABLY ARRIVED (other stores were paid
                            in it) and this store is not in it. A fact about the carrier.
  · `unconfirmed_missing` — the month is closed, in window, and NO row for this payment reached us for
                            ANY store. A fact about OUR INGEST. Counted apart, always.

Collapse the two and the report accuses the carrier of withholding a subsidy every time a portal pull
failed.

WHY "THE RUN ARRIVED" IS THE CONFIRMATION TEST AND NOT DAY COVERAGE. An entitlement like this is a
monthly LUMP SUM, one row per store on a single day early in the month (measured live, house org:
03-06, 04-07, 05-06, 06-08, 07-07, 08-06, 09-09, 10-07). `statement_month_coverage` reports every
closed month FAILED on its own final day (§19.54, the end-exclusive window — $111,949.22), and a
missing 31st cannot hide a lump sum that posts on the 6th. Gating on full day coverage would therefore
withhold EVERY finding this report exists to make. The honest confirmation at this grain is the feed's
own evidence that the month's run happened at all; the day-coverage verdict is still carried on the
payload so a reader sees it, and a month with no run at all is `unconfirmed_missing`, never a gap.

💰 MOVES NO MONEY. Pure (stdlib + the pure period parser), DB-free, no writer, no flag, no P&L line.
Proof: `backend/harness_mdf_entitlement_coverage.py`.
"""

from app.modules.commcalc.pay_data_quality import (
    VERDICT_FAILED, VERDICT_NOT_MEASURED, VERDICT_PASSED,
)

# ── THE VERDICT VOCABULARY ────────────────────────────────────────────────────────────────────────
# The REPORT-level verdict is the shipped tri-state, IMPORTED above so the three strings cannot drift
# from `statement_month_coverage`'s (§19.49 / §19.54). `ok` is None for anything unjudgeable: an
# absence is not a zero, and a window nobody declared is never a pass.

#: The entitlement for this store-month arrived.
ROW_RECEIVED = "received"
#: In window, the month's run DEMONSTRABLY arrived for other stores, and this store is not in it.
ROW_MISSING = "missing"
#: In window and absent, but NO row for this payment reached us for ANY store that month. Our ingest,
#: not the carrier. Counted apart from `missing`, never netted into it.
ROW_UNCONFIRMED_MISSING = "unconfirmed_missing"
#: In window and not yet owed — the month has not finished, or its run has not happened yet.
ROW_NOT_DUE = "not_due"
#: Nothing could be said. `reason` names the absence. NEVER rendered as a gap.
ROW_NOT_MEASURED = VERDICT_NOT_MEASURED
ROW_STATUSES = (ROW_RECEIVED, ROW_MISSING, ROW_UNCONFIRMED_MISSING, ROW_NOT_DUE, ROW_NOT_MEASURED)

#: The org declared no entitlement payment category at all, so nothing is an entitlement here.
REASON_NOT_CONFIGURED = "no_entitlement_configured"
#: The store's first entitled month is not declared, so gap and expiry cannot be told apart.
REASON_WINDOW_NOT_DECLARED = "entitlement_window_not_declared"
#: ANOTHER canonical key that MAY BE THIS SAME PHYSICAL STORE (§64/§70's pairwise predicate) was paid
#: in this month, so the absence may be this store's own money under its other spelling. The verdict
#: is withheld for THAT STORE-MONTH ONLY and the other key is named — never for the store wholesale,
#: which is the §70 defect: the superseded per-address read withheld 23 true findings by asking
#: whether an address was "ambiguous" instead of whether the two addresses in hand may be one store.
REASON_STORE_AMBIGUOUS = "store_identity_ambiguous"
#: No row for this payment reached us for ANY store in this month.
REASON_FEED_MONTH_ABSENT = "entitlement_feed_month_absent"
ROW_REASONS = (REASON_NOT_CONFIGURED, REASON_WINDOW_NOT_DECLARED, REASON_STORE_AMBIGUOUS,
               REASON_FEED_MONTH_ABSENT)

#: What a human reads. No table, column or hosting name reaches a screen (§19.38).
ROW_STATUS_LABELS = {
    ROW_RECEIVED: "Received.",
    ROW_MISSING: ("Not received. Other stores were paid this month, so the payment run did happen "
                  "and this store was not in it."),
    ROW_UNCONFIRMED_MISSING: ("Not received, and no store was paid this month either — so this is a "
                              "month of ours that never arrived, not proof the carrier withheld it. "
                              "Re-pull the month, then read this row again."),
    ROW_NOT_DUE: "Not yet owed — this month has not finished, or its payment run has not happened yet.",
    ROW_NOT_MEASURED: "Not measured.",
}
ROW_REASON_LABELS = {
    REASON_NOT_CONFIGURED: ("No entitlement has been declared for this company, so no store-month "
                            "can be judged against one."),
    REASON_WINDOW_NOT_DECLARED: ("This store's FIRST entitled month has not been declared, so a "
                                 "month with no payment cannot be told apart from the entitlement "
                                 "having run out. Declare the start month and this is judged."),
    REASON_STORE_AMBIGUOUS: ("Two store records share this address, so a month with no payment may "
                             "be the same store under its other spelling."),
    REASON_FEED_MONTH_ABSENT: "No payment of this kind reached us for any store in this month.",
}

# ── CONFIG (RULE TWO) ─────────────────────────────────────────────────────────────────────────────
# House defaults behind `commcalc.commission_org_config.mdf_entitlement_config` (mig 1068). No
# carrier, tenant, store or product name appears in any branch, and the month COUNT is a declared
# number, not a literal in a condition.
#
# `entitlement_categories` IS A DEREFERENCE, NOT A SECOND DECLARATION. "Which payment types are this
# money" already has ONE home — the org's own `commcalc.payment_categories` rows (`description` = the
# carrier's payment type, `category` = the org's label for it), which is what §58's classification and
# the Gross Profit report's `mdf` column already read. This config names the CATEGORY LABEL to follow;
# the payment types themselves are never listed here, so adding a type is one row in the home that
# already exists. Measured live (house org): category 'MDF' holds 'Ramp Up Subsidy' (44 rows,
# $287,500.00) and '4.0 $1K Ramp Up Bonus' (0 rows).
#
# `entitlement_categories` IS EMPTY IN CODE, DELIBERATELY. An org that has declared nothing reports
# `no_entitlement_configured` on every store and calls no month missing (§19.48/§19.49 house shape):
# the absence is the finding, and nothing false is reported in the meantime.
CODE_DEFAULT = {
    "enabled": True,
    #: How many months the entitlement runs, per store. DECLARED, never a literal in a branch.
    "term_months": 12,
    #: The org's OWN category label(s) from `payment_categories` whose payments are the entitlement.
    "entitlement_categories": [],
    #: {canonical store key -> 'YYYY-MM'} the store's FIRST entitled month. A store absent from here
    #: has an UNKNOWN window and is never accused of a missing month.
    "store_windows": {},
    #: {canonical store key -> monthly amount}. Optional: with no row the store's own observed
    #: payments price a gap when they agree, and a gap nothing can price is reported UNPRICED.
    "monthly_amounts": {},
    #: Which column carries what, per feed, so a tenant that spells one differently changes a row.
    "columns": {
        "line_store": "business_address",
        "line_type": "payment_type",
        "line_amount": "amount",
        "line_date": "payment_date",
        "statement_store": "business_address",
        "statement_type": "compensation_type",
        "statement_amount": "payment_amount",
        "statement_date": "begin_date",
    },
}

CONFIG_COLUMN = "mdf_entitlement_config"
CONFIG_MIGRATION = "1068_mdf_entitlement_config.sql"


def _s(v) -> str:
    return str(v if v is not None else "").strip()


def _f(v) -> float:
    try:
        return round(float(v or 0), 2)
    except Exception:
        return 0.0


def month_key(value) -> str | None:
    """PURE: 'YYYY-MM' from a date, a timestamp or a month spelling; None when it is not a month.

    The PARSE is `feed_period.period_month_year`'s — the one home for every period spelling a
    commcalc table stores ('October 2026', '2026-10', '2026-10-06'). Nothing here slices a string.
    """
    from app.modules.commcalc import feed_period as _fp
    mo, yr = _fp.period_month_year(_s(value))
    return f"{yr:04d}-{mo:02d}" if mo and yr else None


def entitlement_window(start_month, term_months) -> list:
    """PURE: every month of a declared entitlement, oldest first; [] when it is not declared.

    THE set a store's completeness is judged against, and the reason an undeclared start month can
    only ever produce an empty window: with no first month there is no window, and with no window
    there is nothing to call missing.
    """
    start = month_key(start_month)
    try:
        n = int(term_months)
    except Exception:
        return []
    if not start or n <= 0:
        return []
    yr, mo = int(start[:4]), int(start[5:7])
    out = []
    for i in range(min(n, 600)):
        m = mo + i
        out.append(f"{yr + (m - 1) // 12:04d}-{(m - 1) % 12 + 1:02d}")
    return out


def normalize_config(raw, base=None) -> dict:
    """PURE. Coerce a stored blob into a complete, in-range config. Never raises.

    Every clamp is one-directional-safe: a typo can only make this report say LESS. It can never
    invent a window for a store that has none, and it can never turn an unconfigured org into one
    that flags stores.
    """
    cfg = {k: (dict(v) if isinstance(v, dict) else list(v) if isinstance(v, list) else v)
           for k, v in (base or CODE_DEFAULT).items()}
    if not isinstance(raw, dict):
        return cfg
    if "enabled" in raw:
        cfg["enabled"] = raw.get("enabled") is not False
    if "term_months" in raw:
        try:
            # Out of range keeps the HOUSE default: a typo can only make this report say less, and a
            # term of zero would silently judge nothing while looking configured.
            v = int(raw.get("term_months"))
            if 1 <= v <= 600:
                cfg["term_months"] = v
        except Exception:
            pass
    cats = raw.get("entitlement_categories")
    if isinstance(cats, (list, tuple)):
        cfg["entitlement_categories"] = sorted({_s(c) for c in cats if _s(c)})
    win = raw.get("store_windows")
    if isinstance(win, dict):
        # A start month that does not parse is DROPPED, not guessed — the store then has an unknown
        # window and is reported `not_measured`, which is the safe direction.
        cfg["store_windows"] = {_s(k): month_key(v) for k, v in win.items()
                                if _s(k) and month_key(v)}
    amts = raw.get("monthly_amounts")
    if isinstance(amts, dict):
        cfg["monthly_amounts"] = {_s(k): _f(v) for k, v in amts.items() if _s(k) and _f(v) > 0}
    cols = raw.get("columns")
    if isinstance(cols, dict):
        for k, v in cols.items():
            if k in cfg["columns"] and _s(v):
                cfg["columns"][k] = _s(v)
    return cfg


def config_from_rows(tenant_blob, house_blob=None) -> dict:
    """PURE. This org's config: house defaults <- the HOUSE org's stored blob <- the tenant's own.

    The platform's standing two-level config shape (`report_pull_map`, mig 207). The caller does the
    two reads; the MERGE lives here so there is one statement of precedence a lock can pin.
    """
    return normalize_config(tenant_blob, base=normalize_config(house_blob))


def entitlement_type_matcher(cfg, declarations, category_of=None):
    """PURE. fn(raw_payment_type) -> bool: is this payment one of the declared entitlement's?

    `declarations` is the org's OWN declaration map and `category_of` is the function that reads it —
    both §57's (`payment_category.load_map` / `.category_of`), injected. The ONE folding rule for a
    payment-type key lives there, because nine call sites once folded it nine different ways, so it
    travels with the map instead of being re-implemented here. With no reader injected a plain
    case-flattened lookup is used, which is what a fixture hands in.

    This function keeps NO list of payment types and knows no carrier's vocabulary: it compares the
    row's declared CATEGORY against the categories the config names. With nothing declared it matches
    nothing, so not one dollar is called an entitlement.
    """
    want = {_s(c).casefold() for c in (cfg or {}).get("entitlement_categories") or () if _s(c)}
    if not want:
        return lambda _t: False
    read = category_of or (lambda m, t: {_s(k).casefold(): v
                                         for k, v in (m or {}).items()}.get(_s(t).casefold()))

    def matches(raw_type):
        return _s(read(declarations, raw_type)).casefold() in want
    return matches


def _blank_totals() -> dict:
    return {
        "stores": 0, "store_months_judged": 0,
        "windows_declared": 0, "windows_undeclared": 0,
        "received_store_months": 0, "received_total": 0.0,
        # THE SPLIT. Two different facts, two counters, never one number (the §19.53 precedent).
        "missing_store_months": 0, "missing_amount": 0.0,
        "missing_open_month_store_months": 0,
        "unpriced_missing_store_months": 0,
        "unconfirmed_missing_store_months": 0, "unconfirmed_missing_amount": 0.0,
        "not_due_store_months": 0,
        "not_measured_store_months": 0,
        # Named absences on a store whose window is UNDECLARED: evidence, never an accusation.
        "undeclared_absence_store_months": 0,
        "undeclared_absence_amount_at_observed_rate": 0.0,
    }


def entitlement_store_months(received, cfg, months_with_feed=None, today=None,
                             same_store=None, stores=None):
    """PURE. **The "expected but absent" home at STORE x MONTH grain over an entitlement window.**

    Dereference this rather than growing a second one; nothing else in the platform answers this
    grain (`statement_month_coverage` answers DAY grain within one month — §19.54).

      `received`         {(store, 'YYYY-MM'): amount} — every entitlement payment that DID arrive, at
                         canonical store keys from the ONE identity home (§64). Never derived here.
      `cfg`              `normalize_config`'s shape: `term_months`, `store_windows`, `monthly_amounts`,
                         `entitlement_categories` (empty = nothing declared).
      `months_with_feed` the months whose entitlement run DEMONSTRABLY arrived. Defaults to the months
                         `received` carries, which is the same evidence stated from the same rows.
      `today`            injected, so a proof stands on a fixed date and nothing reads a hidden clock.
      `same_store`       `fn(a, b) -> bool`: may these TWO canonical keys be ONE physical store? §64's
                         own pairwise predicate (`store_identity.may_be_one_store` over
                         `coa.shared_store_identity_pairs`), INJECTED — this module states no identity
                         rule and keeps no key of its own. With nothing injected nothing is withheld,
                         which is the right direction to fail in (§70).
      `stores`           extra stores to judge (e.g. the estate's roster), so a store that received
                         NOTHING at all is still judged when its window is declared.

    Returns `{rows, by_store, totals, verdict, ok, reason, ...}`. One row per store-month judged, plus
    one row per OBSERVED absence on a store whose window is undeclared — `not_measured` with the
    reason named and the evidence (what it did receive, who was paid that month) on the row, because
    a store that stopped receiving may have reached the end of its term and saying otherwise would be
    a fabricated accusation.
    """
    cfg = cfg or CODE_DEFAULT
    configured = bool(cfg.get("entitlement_categories")) and cfg.get("enabled") is not False
    term = int(cfg.get("term_months") or 0)
    windows = dict(cfg.get("store_windows") or {})
    declared_amounts = dict(cfg.get("monthly_amounts") or {})
    may_be_same = same_store if callable(same_store) else (lambda _a, _b: False)

    got = {}
    for k, v in (received or {}).items():
        try:
            st, m = _s(k[0]), month_key(k[1])
        except Exception:
            continue
        if st and m:
            got[(st, m)] = round(got.get((st, m), 0.0) + _f(v), 2)

    feed_months = {m for m in (month_key(x) for x in (months_with_feed or ())) if m} \
        if months_with_feed is not None else {m for (_st, m) in got}

    from app.modules.commcalc import feed_period as _fp

    all_stores = sorted({st for (st, _m) in got} | {_s(s) for s in (stores or ()) if _s(s)}
                        | {_s(k) for k in windows if _s(k)})

    def paid_siblings(st, month):
        """Canonical keys paid in `month` that MAY BE this same physical store (§64/§70).

        Asked PER STORE-MONTH, never per store: withholding a store wholesale on the strength of its
        address having a twin is exactly the read §70 superseded. Only a month in which a possible
        twin WAS paid is unjudgeable, and that twin is named on the row.
        """
        return sorted(s2 for (s2, m2) in got if m2 == month and s2 != st and may_be_same(st, s2))
    rows, totals = [], _blank_totals()
    totals["stores"] = len(all_stores)

    for st in all_stores:
        own = sorted(m for (s, m) in got if s == st)
        obs = sorted({got[(st, m)] for m in own})
        # The store's monthly rate: its declaration first, else its OWN payments when they agree.
        # Payments that disagree price nothing — a gap nothing can price is reported UNPRICED, never
        # valued at $0.00 (`statement_month_coverage.unpriced_missing_days`'s own rule).
        rate = declared_amounts.get(st) or (obs[0] if len(obs) == 1 else None)
        rate_basis = ("declared" if declared_amounts.get(st)
                      else "the store's own payments" if rate else None)
        start = windows.get(st)
        win = entitlement_window(start, term) if (configured and start) else []

        def row(month, status, reason=None, **extra):
            r = {"store": st, "month": month, "status": status,
                 "status_label": ROW_STATUS_LABELS.get(status, ""),
                 "reason": reason, "reason_label": ROW_REASON_LABELS.get(reason or "", ""),
                 "month_state": _fp.month_state(month, today) if month else None,
                 "amount": None, "monthly_rate": rate, "monthly_rate_basis": rate_basis,
                 "window_start": start, "term_months": term if configured else None,
                 "entitlement_month": None, "received_months": len(own),
                 "first_received": own[0] if own else None,
                 "last_received": own[-1] if own else None,
                 "stores_paid_in_month": None, "same_store_paid": []}
            r.update(extra)
            rows.append(r)
            return r

        if not configured:
            totals["not_measured_store_months"] += 1
            row(None, ROW_NOT_MEASURED, REASON_NOT_CONFIGURED)
            continue
        if not win:
            totals["windows_undeclared"] += 1
            # NOT "term months missing". Only what was OBSERVED: a month whose run arrived, after this
            # store's first payment, with no payment for it. Gap or expiry cannot be told apart, so
            # the verdict is withheld and the reason is the finding.
            for m in sorted(x for x in feed_months if own and x > own[0] and (st, x) not in got):
                twins = paid_siblings(st, m)
                if twins:
                    # This month's money may be this very store under its other spelling (§64/§70).
                    totals["not_measured_store_months"] += 1
                    row(m, ROW_NOT_MEASURED, REASON_STORE_AMBIGUOUS, same_store_paid=twins,
                        stores_paid_in_month=sum(1 for (s2, m2) in got if m2 == m and s2 != st))
                    continue
                totals["undeclared_absence_store_months"] += 1
                if rate:
                    totals["undeclared_absence_amount_at_observed_rate"] = round(
                        totals["undeclared_absence_amount_at_observed_rate"] + rate, 2)
                totals["not_measured_store_months"] += 1
                row(m, ROW_NOT_MEASURED, REASON_WINDOW_NOT_DECLARED,
                    stores_paid_in_month=sum(1 for (s2, m2) in got if m2 == m and s2 != st))
            # What this store DID receive is a fact whatever its window is, so the months it was paid
            # are still listed. Without them the report would show only the stores with a problem and
            # a reader could not see the grid the owner asked about at all.
            for m in own:
                totals["received_store_months"] += 1
                totals["received_total"] = round(totals["received_total"] + got[(st, m)], 2)
                row(m, ROW_RECEIVED, None, amount=got[(st, m)])
            if not own:
                totals["not_measured_store_months"] += 1
                row(None, ROW_NOT_MEASURED, REASON_WINDOW_NOT_DECLARED)
            continue

        totals["windows_declared"] += 1
        for i, m in enumerate(win, start=1):
            amt = got.get((st, m))
            state = _fp.month_state(m, today)
            totals["store_months_judged"] += 1
            if amt is not None:
                totals["received_store_months"] += 1
                totals["received_total"] = round(totals["received_total"] + amt, 2)
                row(m, ROW_RECEIVED, None, amount=amt, entitlement_month=i)
            elif m not in feed_months and state != _fp.CLOSED:
                # The month has not finished and its run has not happened. Not owed yet — reporting
                # it would cry wolf every morning (`statement_month_coverage`'s OPEN-month rule).
                totals["not_due_store_months"] += 1
                row(m, ROW_NOT_DUE, None, entitlement_month=i)
            elif m in feed_months and paid_siblings(st, m):
                # A key that MAY BE this same physical store was paid this month, so "this store did
                # not get its month" cannot be told from "it was paid under its other spelling".
                totals["not_measured_store_months"] += 1
                row(m, ROW_NOT_MEASURED, REASON_STORE_AMBIGUOUS, entitlement_month=i,
                    same_store_paid=paid_siblings(st, m),
                    stores_paid_in_month=sum(1 for (s2, m2) in got if m2 == m and s2 != st))
            elif m in feed_months:
                totals["missing_store_months"] += 1
                if rate:
                    totals["missing_amount"] = round(totals["missing_amount"] + rate, 2)
                else:
                    totals["unpriced_missing_store_months"] += 1
                if state != _fp.CLOSED:
                    totals["missing_open_month_store_months"] += 1
                row(m, ROW_MISSING, None, entitlement_month=i,
                    stores_paid_in_month=sum(1 for (s2, m2) in got if m2 == m and s2 != st))
            else:
                # Closed, in window, and NOTHING of this kind reached us for ANY store that month.
                # OUR ingest, not the carrier. Its own counter, never netted into `missing`.
                totals["unconfirmed_missing_store_months"] += 1
                if rate:
                    totals["unconfirmed_missing_amount"] = round(
                        totals["unconfirmed_missing_amount"] + rate, 2)
                row(m, ROW_UNCONFIRMED_MISSING, REASON_FEED_MONTH_ABSENT, entitlement_month=i)

    by_store = _by_store(rows, totals)
    verdict, ok, reason = _report_verdict(totals, configured)
    return {"rows": rows, "by_store": by_store, "totals": totals,
            "verdict": verdict, "ok": ok, "reason": reason,
            "configured": configured, "term_months": term if configured else None,
            "months_with_feed": sorted(feed_months),
            "entitlement_categories": sorted(cfg.get("entitlement_categories") or ())}


def _by_store(rows, totals) -> list:
    """PURE. One roll-up per store from the SAME rows the detail renders, so the two cannot disagree."""
    out = {}
    for r in rows:
        t = out.setdefault(r["store"], {
            "store": r["store"], "window_start": r["window_start"],
            "monthly_rate": r["monthly_rate"], "received": 0, "received_total": 0.0,
            "missing": 0, "missing_months": [], "unconfirmed_missing": 0,
            "unconfirmed_missing_months": [], "not_due": 0, "not_measured": 0,
            "undeclared_absence_months": [], "reasons": [],
            # What the store received ACROSS THE FEED, in or out of any window — so a store with no
            # declared window still shows that it was being paid, and until when.
            "received_months_observed": r.get("received_months"),
            "first_received": r.get("first_received"), "last_received": r.get("last_received")})
        s = r["status"]
        if s == ROW_RECEIVED:
            t["received"] += 1
            t["received_total"] = round(t["received_total"] + _f(r.get("amount")), 2)
        elif s == ROW_MISSING:
            t["missing"] += 1
            t["missing_months"].append(r["month"])
        elif s == ROW_UNCONFIRMED_MISSING:
            t["unconfirmed_missing"] += 1
            t["unconfirmed_missing_months"].append(r["month"])
        elif s == ROW_NOT_DUE:
            t["not_due"] += 1
        else:
            t["not_measured"] += 1
            if r.get("month") and r.get("reason") == REASON_WINDOW_NOT_DECLARED:
                t["undeclared_absence_months"].append(r["month"])
        if r.get("reason") and r["reason"] not in t["reasons"]:
            t["reasons"].append(r["reason"])
    return sorted(out.values(),
                  key=lambda t: (-t["missing"], -len(t["undeclared_absence_months"]), t["store"]))


def _report_verdict(totals, configured):
    """PURE. The shipped tri-state (§19.49). A confirmed gap FAILS; nothing unjudgeable may PASS."""
    if not configured:
        return (VERDICT_NOT_MEASURED, None,
                "No entitlement has been declared for this company, so no store-month was judged. "
                "Declare which payment category is the entitlement, how many months it runs and each "
                "store's first entitled month, and this report judges every one of them.")
    if totals["missing_store_months"]:
        return (VERDICT_FAILED, False,
                f"{totals['missing_store_months']} store-month(s) inside a declared entitlement never "
                f"arrived while the month's payment run did"
                + (f", worth {totals['missing_amount']:.2f}" if totals["missing_amount"] else "")
                + (f" plus {totals['unpriced_missing_store_months']} nothing can price"
                   if totals["unpriced_missing_store_months"] else "")
                + (f". A further {totals['unconfirmed_missing_store_months']} store-month(s) are "
                   f"absent in months no payment reached us at all — our ingest, counted apart and "
                   f"not included above" if totals["unconfirmed_missing_store_months"] else "")
                + (f". {totals['undeclared_absence_store_months']} absence(s) on stores whose first "
                   f"entitled month is not declared are reported NOT MEASURED, not as gaps"
                   if totals["undeclared_absence_store_months"] else "") + ".")
    if (totals["unconfirmed_missing_store_months"] or totals["not_measured_store_months"]
            or not totals["store_months_judged"]):
        return (VERDICT_NOT_MEASURED, None,
                f"No store-month inside a declared entitlement is missing from a month whose payment "
                f"run arrived. Not everything could be judged: "
                f"{totals['unconfirmed_missing_store_months']} store-month(s) fall in months no "
                f"payment reached us for any store, and {totals['windows_undeclared']} store(s) have "
                f"no declared first entitled month"
                + (f" — including {totals['undeclared_absence_store_months']} observed absence(s), "
                   f"which cannot be told apart from the entitlement running out"
                   if totals["undeclared_absence_store_months"] else "") + ".")
    return (VERDICT_PASSED, True,
            f"Every one of the {totals['store_months_judged']} store-month(s) inside a declared "
            f"entitlement either arrived or is not yet owed: {totals['received_store_months']} "
            f"received, {totals['not_due_store_months']} still to come.")
