"""Two-source metric reconciliation (owner directive 2026-08-26).

The owner's model: two independently-ingested sources for the same metric should PROVE the ingest is good
when they agree, and FLAG when they don't — an automatic back-end reconciliation, not a manual eyeball.

For ACTIVATIONS the two sources are:
  • PRIMARY (basis of truth): the b2b "Activation Details" custom import — distinct Serial#, Upgrade
    excluded from Total Activation (the b2b-consistent definition).
  • SECONDARY: the shared sales aggregation (`_sales_cell_agg` over raw_sales / the daily feed) — what
    Executive MTD / the Sales Report historically counted.

This module is PURE: router.py builds each side into a per-canonical-store bucket map and hands both here.
No DB, no framework — so it is trivially unit-testable and can be reused for other metric pairs later.

A store row is compared on Total Activation EXCLUDING Upgrade (activation+port+byod) so the two bases mean
the same thing. The result names, per store, whether the sources match, and classifies each divergence so
the caller can auto-remediate (re-run the sweep) or assign an upload — never a silent gap.
"""


def _excl_upgrade_total(slot):
    """Total Activation EXCLUDING Upgrade for one store bucket slot (the b2b-consistent definition)."""
    if not slot:
        return 0
    return int(slot.get("activation", 0)) + int(slot.get("port", 0)) + int(slot.get("byod", 0))


def _name_of(primary_slot, secondary_slot, key):
    for s in (primary_slot, secondary_slot):
        if s and s.get("_name"):
            return s["_name"]
    return key


def reconcile_activations(primary_by_store, secondary_by_store, tolerance=0,
                          source="activation_details", reconcile_with="sales_agg",
                          assigned_user=None):
    """Reconcile two per-store activation bucket maps.

    Each map: {canonical_store_key: {'activation','port','byod','upgrade', optional '_name'}}.
    `tolerance` = allowed absolute delta per store before it is flagged (0 = exact match).

    Returns a dict:
      status            'match' | 'mismatch' | 'no_primary' | 'no_secondary'
      source / reconcile_with / tolerance   (echoed config)
      totals            {'primary','secondary','delta'} on the excl-Upgrade basis
      counts            {'stores','matched','mismatched','missing_in_primary','missing_in_secondary'}
      stores            per-store rows that DIFFER beyond tolerance (sorted by |delta| desc), each:
                        {store, primary, secondary, delta, kind}
      remediation       {'action','assigned_user','reason'} — what to do about the mismatch, or None
    """
    primary_by_store = primary_by_store or {}
    secondary_by_store = secondary_by_store or {}
    keys = set(primary_by_store) | set(secondary_by_store)

    p_total = sum(_excl_upgrade_total(s) for s in primary_by_store.values())
    s_total = sum(_excl_upgrade_total(s) for s in secondary_by_store.values())

    rows = []
    matched = mismatched = miss_p = miss_s = 0
    for k in keys:
        ps, ss = primary_by_store.get(k), secondary_by_store.get(k)
        p, s = _excl_upgrade_total(ps), _excl_upgrade_total(ss)
        delta = p - s
        if abs(delta) <= tolerance:
            matched += 1
            continue
        if p == 0 and s > 0:
            kind = "missing_in_primary"      # secondary counts activations the primary (AD) has none of
            miss_p += 1
        elif s == 0 and p > 0:
            kind = "missing_in_secondary"    # AD counts activations the sales feed never captured
            miss_s += 1
        else:
            kind = "mismatch"                # both non-zero but disagree
            mismatched += 1
        rows.append({"store": _name_of(ps, ss, k), "primary": p, "secondary": s,
                     "delta": delta, "kind": kind})
    rows.sort(key=lambda r: -abs(r["delta"]))

    if not primary_by_store:
        status = "no_primary"
    elif not secondary_by_store:
        status = "no_secondary"
    elif not rows:
        status = "match"
    else:
        status = "mismatch"

    remediation = None
    if status in ("mismatch", "no_primary"):
        # A missing/short PRIMARY (the AD basis) is the auto-remediable case: re-run the email/FTP sweep to
        # re-ingest the Activation Details report; if that cannot close it, assign the named user to upload.
        primary_short = (status == "no_primary") or any(
            r["kind"] == "missing_in_primary" for r in rows)
        if primary_short:
            remediation = {"action": ("assign_upload" if assigned_user else "rerun_sweep"),
                           "assigned_user": assigned_user,
                           "reason": ("Activation Details is missing or short vs the sales feed — re-run the "
                                      "sweep to re-ingest it" + (", or assign the upload." if assigned_user
                                                                 else "."))}
        else:
            remediation = {"action": "review",
                           "assigned_user": assigned_user,
                           "reason": "Both sources have data but disagree per store — review the flagged "
                                     "stores; the primary (Activation Details) is the basis of truth."}

    return {
        "status": status,
        "source": source, "reconcile_with": reconcile_with, "tolerance": tolerance,
        "totals": {"primary": p_total, "secondary": s_total, "delta": p_total - s_total},
        "counts": {"stores": len(keys), "matched": matched, "mismatched": mismatched,
                   "missing_in_primary": miss_p, "missing_in_secondary": miss_s},
        "stores": rows,
        "remediation": remediation,
    }


def _ca(slot):
    """(count, amount) from a bill-payment store slot; missing slot → (0, 0.0)."""
    if not slot:
        return 0, 0.0
    return int(slot.get("count", 0)), float(slot.get("amount", 0.0) or 0.0)


def reconcile_bill_payments(report_by_store, sales_by_store, processor_by_store,
                            tolerance_amt=0.0, tolerance_cnt=0, processor="",
                            reconcile_with="sales_agg", assigned_user=None):
    """THREE-way bill-payment reconciliation (owner 2026-08-26): the b2b "Bill Payment Transactions" report
    is the BASIS OF TRUTH, reconciled against (a) the shared sales aggregation and (b) the carrier's payment
    PROCESSOR — ePay (Boost) or VidaPay (Total). Each *_by_store maps a store key -> {'count','amount',
    optional '_name'}. Compared on AMOUNT (the money settled) with `tolerance_amt`, and on COUNT with
    `tolerance_cnt`. `processor_by_store` may be empty (processor feed absent / unknown) → a two-way
    report-vs-sales recon, said so in the result rather than silently.

    Returns totals for all three sides, per-store rows that diverge (sorted by |amount delta|), a status,
    and a remediation. PURE; no I/O."""
    report_by_store = report_by_store or {}
    sales_by_store = sales_by_store or {}
    processor_by_store = processor_by_store or {}
    have_proc = bool(processor_by_store)
    keys = set(report_by_store) | set(sales_by_store) | set(processor_by_store)

    def _tot(m):
        c = sum(_ca(v)[0] for v in m.values())
        a = round(sum(_ca(v)[1] for v in m.values()), 2)
        return {"count": c, "amount": a}
    t_report, t_sales, t_proc = _tot(report_by_store), _tot(sales_by_store), _tot(processor_by_store)

    rows = []
    matched = flagged = 0
    for k in keys:
        rc, ra = _ca(report_by_store.get(k))
        sc, sa = _ca(sales_by_store.get(k))
        pc, pa = _ca(processor_by_store.get(k))
        d_sales_amt = round(ra - sa, 2)
        d_proc_amt = round(ra - pa, 2)
        bad_sales = abs(d_sales_amt) > tolerance_amt or abs(rc - sc) > tolerance_cnt
        bad_proc = have_proc and (abs(d_proc_amt) > tolerance_amt or abs(rc - pc) > tolerance_cnt)
        if not bad_sales and not bad_proc:
            matched += 1
            continue
        flagged += 1
        kinds = []
        if bad_sales:
            kinds.append("sales_mismatch" if sa or sc else "missing_in_sales")
        if bad_proc:
            kinds.append("processor_mismatch" if pa or pc else "missing_in_processor")
        name = None
        for m in (report_by_store, sales_by_store, processor_by_store):
            if m.get(k) and m[k].get("_name"):
                name = m[k]["_name"]; break
        rows.append({"store": name or k,
                     "report": {"count": rc, "amount": ra},
                     "sales": {"count": sc, "amount": sa},
                     "processor": {"count": pc, "amount": pa},
                     "delta_sales_amount": d_sales_amt,
                     "delta_processor_amount": (d_proc_amt if have_proc else None),
                     "kind": "+".join(kinds)})
    rows.sort(key=lambda r: -max(abs(r["delta_sales_amount"]),
                                 abs(r["delta_processor_amount"] or 0)))

    if not report_by_store:
        status = "no_report"
    elif not rows:
        status = "match"
    else:
        status = "mismatch"

    remediation = None
    if status == "no_report":
        remediation = {"action": ("assign_upload" if assigned_user else "rerun_sweep"),
                       "assigned_user": assigned_user,
                       "reason": ("The Bill Payment Transactions report (basis of truth) is missing — re-run "
                                  "the sweep to re-ingest it" + (", or assign the upload." if assigned_user
                                                                 else "."))}
    elif status == "mismatch":
        proc_missing = have_proc and any("missing_in_processor" in r["kind"] for r in rows)
        remediation = {"action": ("rerun_processor_sweep" if (not have_proc or proc_missing) else "review"),
                       "assigned_user": assigned_user,
                       "reason": (("The processor feed (" + (processor or "ePay/VidaPay") + ") is missing or "
                                   "short — re-run the processor sweep to reconcile.") if (not have_proc or proc_missing)
                                  else "The bill-payment sources have data but disagree per store — review the "
                                       "flagged stores; the Bill Payment Transactions report is the basis of truth.")}

    return {
        "status": status,
        "source": "bill_payments", "reconcile_with": reconcile_with, "processor": (processor or None),
        "processor_present": have_proc,
        "tolerance_amt": tolerance_amt, "tolerance_cnt": tolerance_cnt,
        "totals": {"report": t_report, "sales": t_sales, "processor": t_proc},
        "counts": {"stores": len(keys), "matched": matched, "flagged": flagged},
        "stores": rows,
        "remediation": remediation,
    }


def reconcile_billpay_coverage(billpay_by_store_day, collected_by_store_day, tolerance_amt=1.0,
                               assigned_user=None):
    """Coverage recon (owner directive 2026-09-02, item 5a, verbatim): "the total of epay/vida pay
    for bill payments collected in the store should be equal to or less than the total of cash and
    card collected in the store." Join grain is (store, DAY).

    billpay_by_store_day maps (store_key, 'YYYY-MM-DD') -> {'amount', optional '_name'} (the
    processor/declared bill-pay total); collected_by_store_day maps the same key ->
    {'cash', 'card', optional '_name'} (what the store declared at closing, DM-corrected where
    verified). A day is an EXCEPTION when billpay > cash + card + tolerance — bill payments were
    processed that the drawer money can't cover (mis-tagged tender, unrecorded collection, or a
    payment run on store credit). billpay ≤ collected is FINE by design (customers also buy
    product), so nothing flags in that direction. PURE."""
    billpay_by_store_day = billpay_by_store_day or {}
    collected_by_store_day = collected_by_store_day or {}
    keys = set(billpay_by_store_day) | set(collected_by_store_day)
    rows, covered, exceptions = [], 0, 0
    tot_bp, tot_col = 0.0, 0.0
    for k in keys:
        bp = float((billpay_by_store_day.get(k) or {}).get("amount", 0.0) or 0.0)
        c = collected_by_store_day.get(k) or {}
        cash = float(c.get("cash", 0.0) or 0.0)
        card = float(c.get("card", 0.0) or 0.0)
        collected = round(cash + card, 2)
        tot_bp = round(tot_bp + bp, 2)
        tot_col = round(tot_col + collected, 2)
        excess = round(bp - collected, 2)
        if excess <= tolerance_amt:
            covered += 1
            continue
        exceptions += 1
        st, day = (k if isinstance(k, tuple) and len(k) == 2 else (k, ""))
        name = ((billpay_by_store_day.get(k) or {}).get("_name")
                or (collected_by_store_day.get(k) or {}).get("_name") or st)
        rows.append({"store": name, "day": day, "billpay": round(bp, 2), "cash": round(cash, 2),
                     "card": round(card, 2), "collected": collected, "excess": excess})
    rows.sort(key=lambda r: -r["excess"])
    status = ("no_data" if not keys else ("covered" if not rows else "exceptions"))
    remediation = None
    if rows:
        remediation = {"action": "review", "assigned_user": assigned_user,
                       "reason": "Bill payments exceed the cash + card the store declared on those "
                                 "days — the pass-through money isn't covered by what was collected. "
                                 "Check tender tagging on the closing sheet and whether every "
                                 "bill-payment collection was actually rung in."}
    return {"status": status, "tolerance_amt": tolerance_amt,
            "totals": {"billpay": tot_bp, "collected": tot_col,
                       "coverage_pct": (round(100.0 * tot_bp / tot_col, 1) if tot_col else None)},
            "counts": {"store_days": len(keys), "covered": covered, "exceptions": exceptions},
            "store_days": rows, "remediation": remediation}


def reconcile_billpay_cash(actual_by_store, declared_by_store, tolerance_amt=1.0, assigned_user=None):
    """Reconcile ACTUAL bill-payment CASH (the Bill Payment Transactions report, tender = cash) against the
    cash employees DECLARED at daily closing (daily_closing.epay_on_cash), per store — the wiring the owner
    asked for: "the bill pay reconciliation should also be wired in the daily cash being declared by the
    employees." Join grain is (store, period); over/short per store.

    Each *_by_store maps store key -> {'amount', optional '_name'}. delta = declared - actual: positive means
    the rep declared MORE bill-payment cash than the transactions show (over / possible mis-tag); negative
    means LESS (short / cash unaccounted). PURE."""
    actual_by_store = actual_by_store or {}
    declared_by_store = declared_by_store or {}
    keys = set(actual_by_store) | set(declared_by_store)
    rows, matched, over, short = [], 0, 0, 0
    for k in keys:
        a = float((actual_by_store.get(k) or {}).get("amount", 0.0) or 0.0)
        d = float((declared_by_store.get(k) or {}).get("amount", 0.0) or 0.0)
        delta = round(d - a, 2)
        if abs(delta) <= tolerance_amt:
            matched += 1
            continue
        kind = "over" if delta > 0 else "short"
        over += 1 if delta > 0 else 0
        short += 1 if delta < 0 else 0
        name = (actual_by_store.get(k) or declared_by_store.get(k) or {}).get("_name") or k
        rows.append({"store": name, "actual_cash": round(a, 2), "declared_cash": round(d, 2),
                     "delta": delta, "kind": kind})
    rows.sort(key=lambda r: -abs(r["delta"]))
    status = ("no_data" if not keys else ("match" if not rows else "mismatch"))
    remediation = None
    if rows:
        remediation = {"action": "review", "assigned_user": assigned_user,
                       "reason": "Declared bill-payment cash and actual bill-payment transactions disagree — "
                                 "review the flagged stores (a short means cash collected but not declared; an "
                                 "over means declared cash the transactions don't support)."}
    return {
        "status": status, "tolerance_amt": tolerance_amt,
        "totals": {"actual_cash": round(sum(float((v or {}).get("amount", 0.0) or 0.0)
                                            for v in actual_by_store.values()), 2),
                   "declared_cash": round(sum(float((v or {}).get("amount", 0.0) or 0.0)
                                              for v in declared_by_store.values()), 2)},
        "counts": {"stores": len(keys), "matched": matched, "over": over, "short": short},
        "stores": rows, "remediation": remediation,
    }
