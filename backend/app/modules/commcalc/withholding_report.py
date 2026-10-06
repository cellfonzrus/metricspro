"""COMMISSION WITHHOLDING — the activations the carrier took commission back on, and whether it
ever came back (owner ask 2026-10-06).

The owner's ask, verbatim: *"create a report for commisison withodimng which is a payment type in
commimssion details reports uploaded everymonth- ti should be under flags which shows the acrrier
has not paid the commimssion , need the standard filters as we enever got paid for these actiavtions
and have to see what is the apeal status and whether they got paid int eh following month s- also
need a report whch shows parrallely oif the epay payment was made for these activations"*.

Four questions, one row per activation:

    1. WHAT WAS TAKEN BACK   the debited legs on the processor feed, folded per activation.
    2. DID IT COME BACK      commission paid against that same activation AFTER the clawback date,
                             and in which months — "whether they got paid in the following months".
    3. WAS WE PAID AT ALL    the parallel leg: the processor payment made against that activation,
                             beside the clawback, never summed with it.
    4. WHERE IS THE APPEAL   the appeal state a manager set on the finding.

WHAT IS REUSED, AND WHAT WOULD HAVE BEEN A SIBLING (the §duplicate build gate)
─────────────────────────────────────────────────────────────────────────────
Nothing in here re-derives a join, a sign rule or a state machine that already has a home:

  · **"is this row money taken back"** → `commcalc/clawback.py` (which in turn dereferences
    `processor_ledger.FEED_SHAPES` for the sign and `pay_data_quality.PLACEABLE_CATEGORIES` for the
    pay categories). No category literal and no sign test appears in this file.
  · **"which commission belongs to this activation"** → `marketing/event_sales.py`'s
    `commission_event` / `index_commission_events` / `_line_event_uids`. That is the platform's one
    number-OR-device join, and critically it already carries the IMEI FENCE: the clawback leg of
    this very feed is keyed by IMEI with no number at all (349 house rows, 349 imei, 0 mdn — index
    §23s.8), and the same handset re-activated on a NEW number later must not have that later line's
    money credited to this one. Re-deriving the join here would have re-introduced exactly that bug.
  · **"what are the appeal states and which transitions are legal"** → `discrepancy_appeals.py`.
    Its `apply_appeal` builds the row patch and its column names are what migration 1060 adds to
    `commcalc.flags`, so ONE state machine drives both homes rather than two agreeing by luck.
  · **"did this finding survive the monthly re-upload"** → `flag_persist.py`. The findings are
    `commcalc.flags` rows, so a manager's ruling is never erased by next month's file and a
    condition that clears is RETIRED in place, not deleted.
  · **the filters** → the `StandardFilterBar` contract (period / store / market / rep).

ABSENCE IS NEVER A ZERO — the three states, twice
─────────────────────────────────────────────────
A clawback whose activation cannot be looked up is NOT reported as "never recovered", and one with
no payment row is NOT reported as "$0.00 paid". Both legs carry `event_sales`' honest three-state
census, because the alternative is a report that invents a loss. `summarize` keeps the unknowable
out of the headline and names it.

RULE TWO: no carrier, tenant, store or payment-type name appears in this file. The categories and
the types come from the org's own config rows, handed in by the caller.

MOVES NO MONEY. It books nothing, pays nobody, and re-declares nothing. Where the org's own
declarations are wrong about the money (a clawback declared as earnings), `clawback.declaration_findings`
REPORTS it for a ruling.

PURE: stdlib plus the three modules above. Proven DB-free by `backend/harness_withholding_report.py`.
Registered in docs/SYSTEM_DATA_FLOW_INDEX.md §54.
"""
from __future__ import annotations

from app.modules.commcalc import clawback as _cb

# ── The two three-state censuses. Same shape, same posture, different question. ──────────────────
RECOVERED = "recovered"
RECOVERED_PARTLY = "recovered_partly"
NOT_RECOVERED = "matched_not_recovered"
RECOVERY_UNKNOWN = "unmatchable"

RECOVERY_NOTES = {
    RECOVERED: ("Commission landed against this activation AFTER the money was taken back, for at "
                "least the amount taken. The carrier paid it in a later month."),
    RECOVERED_PARTLY: ("Some commission landed against this activation after the clawback, but "
                       "less than was taken. The balance is still out."),
    NOT_RECOVERED: ("The activation IS known to the commission feeds — it was looked up — and "
                    "nothing has been paid against it since the clawback. A MEASURED zero: "
                    "commission on a line keeps arriving for months, so it may stop being zero."),
    RECOVERY_UNKNOWN: ("The activation could not be looked up in any loaded commission feed, so "
                       "whether it was ever repaid is NOT KNOWN. It is never counted as unrecovered: "
                       "a total that reads an absent feed as a loss overstates the loss."),
}

EPAY_PAID = "paid"
EPAY_NONE = "no_payment_found"
EPAY_UNKNOWN = "feed_not_loaded"

EPAY_NOTES = {
    EPAY_PAID: "The processor made a payment against this activation at some point.",
    EPAY_NONE: ("No payment row for this activation exists in the loaded feed. Consistent with "
                "never having been paid, and stated as a measured zero rather than assumed."),
    EPAY_UNKNOWN: ("No processor feed is loaded for the window this activation falls in, so "
                   "whether it was paid is not known. A missing upload is reported, not absorbed."),
}

#: What identifies an activation across both legs. Device serial FIRST, because the clawback leg is
#: keyed by it and carries no number at all (index §23s.8). `key_basis` records which one won, the
#: same honesty `flag_persist` applies to a flag's identity.
KEY_IMEI = "imei"
KEY_MDN = "mdn"
KEY_NONE = "none"


def _norm(v):
    return str(v or "").replace(".0", "").strip()


def _f(v):
    try:
        return round(float(v or 0.0), 2)
    except (TypeError, ValueError):
        return 0.0


def _day(v):
    return str(v or "")[:10]


def activation_key(row, feed="epay"):
    """PURE. (key, basis) for one feed row. Device serial wins; a row with neither is named, not
    dropped — its legs fold under the payment type and date so the finding is still re-findable."""
    imei = _norm(row.get("imei"))
    if imei:
        return imei, KEY_IMEI
    mdn = _norm(row.get("mdn"))
    if mdn:
        return mdn, KEY_MDN
    return "%s|%s" % (_cb.row_type(row, feed), _day(row.get("payment_date"))), KEY_NONE


def withheld_findings(rows, category_of, feed="epay", pay_cats=None, store_of=None):
    """PURE. The processor feed → one finding per activation whose commission was taken back.

    Every leg is folded, so an activation charged back three times over four months is ONE finding
    carrying the total, the first and last date, and the types involved — a manager appeals an
    activation, not a ledger line.

    `store_of` optionally maps the feed's raw store string to a canonical one; the caller passes the
    app's own resolver rather than this module inventing a spelling-collapse.
    """
    cats = tuple(pay_cats if pay_cats is not None else _cb.pay_categories())
    shape = _cb.feed_shape(feed) or {}
    store_col = shape.get("store_col") or "business_address"
    out = {}
    for row in (rows or []):
        c = _cb.classify_row(row, category_of, feed=feed, pay_cats=cats)
        if not c["clawback"]:
            continue
        key, basis = activation_key(row, feed)
        raw_store = str(row.get(store_col) or "").strip()
        store = (store_of(raw_store) if callable(store_of) else raw_store) or raw_store
        day = _day(row.get(shape.get("date_col") or "payment_date"))
        f = out.get(key)
        if f is None:
            f = out[key] = {
                "key": key, "key_basis": basis,
                "imei": _norm(row.get("imei")), "mdn": _norm(row.get("mdn")),
                "store": store, "store_raw": raw_store,
                "rep": str(row.get("rep_username") or "").strip(),
                "withheld": 0.0, "legs": 0, "types": [],
                "first_withheld_on": day, "last_withheld_on": day,
                "periods": [],
            }
        f["withheld"] = round(f["withheld"] + c["amount"], 2)
        f["legs"] += 1
        if c["type"] and c["type"] not in f["types"]:
            f["types"].append(c["type"])
        per = str(row.get("period") or "").strip()
        if per and per not in f["periods"]:
            f["periods"].append(per)
        if day:
            if not f["first_withheld_on"] or day < f["first_withheld_on"]:
                f["first_withheld_on"] = day
            if day > f["last_withheld_on"]:
                f["last_withheld_on"] = day
        # A later leg can name the rep or store the earlier one left blank. Filling a blank is not
        # overwriting a fact.
        if not f["rep"]:
            f["rep"] = str(row.get("rep_username") or "").strip()
        if not f["store"]:
            f["store"] = store
        if not f["imei"]:
            f["imei"] = _norm(row.get("imei"))
        if not f["mdn"]:
            f["mdn"] = _norm(row.get("mdn"))
    for f in out.values():
        f["types"].sort()
        f["periods"].sort()
    return sorted(out.values(), key=lambda f: (-f["withheld"], f["key"]))


def _line_of(finding):
    """The shape `event_sales._line_event_uids` expects, so the platform's one join can be used
    unchanged — including its IMEI fence."""
    return {"mdn": finding.get("mdn") or "", "serial_1": finding.get("imei") or ""}


def recovery(findings, index, *, as_of, feeds_loaded=(), counted_categories=None):
    """PURE. For each finding, the commission paid against that SAME activation AFTER the money was
    taken back — "whether they got paid in the following months".

    `index` is `event_sales.index_commission_events(...)` over the org's commission events. The
    lookup itself is `event_sales._line_event_uids`, so the IMEI fence applies and a row reachable
    by both keys counts once.

    STRICTLY AFTER the clawback's LAST leg: commission that landed before the carrier took the
    money back is not a recovery of it, and counting it as one would turn a real loss into a
    balanced row. Events with no date are reported separately (`undated_paid`) and never counted as
    a recovery, because "it may have been before" is not evidence.
    """
    from app.modules.marketing.event_sales import (
        COUNTED_CATEGORIES, _line_event_uids, STREAM_COMMISSION,
    )
    counted = set(counted_categories or COUNTED_CATEGORIES)
    loaded = list(feeds_loaded or ())
    by_uid = (index or {}).get("by_uid") or {}
    claimed = set()
    out = []
    for f in (findings or []):
        row = dict(f)
        cutoff = _day(f.get("last_withheld_on"))
        uids = [u for u in _line_event_uids(_line_of(f), index or {}) if u not in claimed]
        reachable = bool(_line_event_uids(_line_of(f), index or {}))

        if not (f.get("imei") or f.get("mdn")):
            row.update({"recovery_state": RECOVERY_UNKNOWN, "recovered": None,
                        "recovered_months": {}, "undated_paid": 0.0, "events_after": 0,
                        "recovery_reason": "the clawback row carries neither a number nor a device serial"})
            out.append(row)
            continue
        if not reachable:
            reason = ("no commission feed is loaded for this window" if not loaded
                      else "the activation is in no loaded commission feed")
            row.update({"recovery_state": RECOVERY_UNKNOWN, "recovered": None,
                        "recovered_months": {}, "undated_paid": 0.0, "events_after": 0,
                        "recovery_reason": reason})
            out.append(row)
            continue

        recovered = 0.0
        undated = 0.0
        months = {}
        n_after = 0
        for u in uids:
            ev = by_uid.get(u) or {}
            if (ev.get("category") or "") not in counted:
                continue
            amt = _f(ev.get("amount"))
            if amt <= 0:
                continue
            paid_on = _day(ev.get("paid_on"))
            if not paid_on:
                undated = round(undated + amt, 2)
                continue
            if cutoff and paid_on <= cutoff:
                continue
            claimed.add(u)
            recovered = round(recovered + amt, 2)
            n_after += 1
            mkey = paid_on[:7]
            months[mkey] = round(months.get(mkey, 0.0) + amt, 2)

        withheld = _f(f.get("withheld"))
        state = (RECOVERED if recovered >= withheld > 0
                 else RECOVERED_PARTLY if recovered > 0
                 else NOT_RECOVERED)
        row.update({
            "recovery_state": state,
            "recovered": recovered,
            "still_out": round(max(withheld - recovered, 0.0), 2),
            "recovered_months": dict(sorted(months.items())),
            "undated_paid": undated,
            "events_after": n_after,
            "recovery_reason": None,
            "stream": STREAM_COMMISSION,
        })
        out.append(row)
    return {"rows": out, "as_of": as_of, "feeds_loaded": loaded,
            "state_notes": dict(RECOVERY_NOTES),
            "cutoff_note": (
                "A recovery is commission that landed STRICTLY AFTER the last leg of the clawback. "
                "Money paid before it is not a recovery of it. Payments carrying no date are "
                "reported as undated and never counted as a recovery.")}


def epay_leg(findings, rows, feed="epay", feed_loaded=True):
    """PURE. The parallel view the owner asked for: beside each clawback, the processor payment that
    was actually MADE against the same activation.

    CREDITS ONLY, and never netted against the clawback. They are two different facts — what the
    processor paid us, and what it took back — and a single net number hides both. The report shows
    them side by side and `summarize` keeps them in separate totals.
    """
    shape = _cb.feed_shape(feed) or {}
    paid_by_key = {}
    for row in (rows or []):
        debit = _cb.debit_of(row, feed)
        if debit > 0:
            continue                          # the clawback leg; the other half of this report
        amount = _f(row.get(shape.get("amount_col") or "amount"))
        credit = abs(amount) if amount else 0.0
        if credit <= 0:
            continue
        for key in {k for k in (_norm(row.get("imei")), _norm(row.get("mdn"))) if k}:
            d = paid_by_key.setdefault(key, {"amount": 0.0, "rows": 0, "last_paid_on": "",
                                             "types": []})
            d["amount"] = round(d["amount"] + credit, 2)
            d["rows"] += 1
            day = _day(row.get(shape.get("date_col") or "payment_date"))
            if day > d["last_paid_on"]:
                d["last_paid_on"] = day
            t = _cb.row_type(row, feed)
            if t and t not in d["types"]:
                d["types"].append(t)

    out = []
    for f in (findings or []):
        row = dict(f)
        keys = [k for k in (f.get("imei"), f.get("mdn")) if k]
        hit = None
        for k in keys:
            if k in paid_by_key:
                hit = paid_by_key[k]
                break
        if not keys:
            row.update({"epay_state": EPAY_UNKNOWN, "epay_paid": None, "epay_rows": 0,
                        "epay_last_paid_on": "", "epay_types": []})
        elif not feed_loaded:
            row.update({"epay_state": EPAY_UNKNOWN, "epay_paid": None, "epay_rows": 0,
                        "epay_last_paid_on": "", "epay_types": []})
        elif hit:
            row.update({"epay_state": EPAY_PAID, "epay_paid": hit["amount"],
                        "epay_rows": hit["rows"], "epay_last_paid_on": hit["last_paid_on"],
                        "epay_types": sorted(hit["types"])})
        else:
            row.update({"epay_state": EPAY_NONE, "epay_paid": 0.0, "epay_rows": 0,
                        "epay_last_paid_on": "", "epay_types": []})
        out.append(row)
    return {"rows": out, "state_notes": dict(EPAY_NOTES),
            "parallel_note": (
                "What the processor PAID against the activation, beside what it took back. The two "
                "are never summed into one figure: a net number hides both the payment and the "
                "clawback.")}


def summarize(rows, appeal_of=None):
    """PURE. The report's cards. Three rules, each one the reason a card exists:

      · the UNKNOWABLE is named, never folded. `still_out` counts only findings whose activation was
        actually looked up; `unknown_withheld` carries the rest with its own count.
      · the PAID leg is its own total. It is never netted against the clawback total.
      · appeal buckets count findings, and `no_appeal` is a real bucket — "nobody has ruled" is the
        honest default on a fresh finding and must be visible, not implied by subtraction.
    """
    from app.modules.commcalc.discrepancy_appeals import APPEAL_STATES

    cards = {
        "findings": 0, "withheld": 0.0, "recovered": 0.0, "still_out": 0.0,
        "unknown_withheld": 0.0, "unknown_findings": 0,
        "epay_paid": 0.0, "epay_unknown_findings": 0, "epay_none_findings": 0,
        "undated_paid": 0.0,
        "by_recovery_state": {}, "by_appeal": {"no_appeal": 0},
        "stores": 0, "reps": 0,
    }
    for s in APPEAL_STATES:
        cards["by_appeal"][s] = 0
    stores, reps = set(), set()

    for r in (rows or []):
        cards["findings"] += 1
        withheld = _f(r.get("withheld"))
        cards["withheld"] = round(cards["withheld"] + withheld, 2)
        state = r.get("recovery_state") or RECOVERY_UNKNOWN
        cards["by_recovery_state"][state] = cards["by_recovery_state"].get(state, 0) + 1
        if state == RECOVERY_UNKNOWN:
            cards["unknown_withheld"] = round(cards["unknown_withheld"] + withheld, 2)
            cards["unknown_findings"] += 1
        else:
            cards["recovered"] = round(cards["recovered"] + _f(r.get("recovered")), 2)
            cards["still_out"] = round(cards["still_out"] + _f(r.get("still_out")), 2)
        cards["undated_paid"] = round(cards["undated_paid"] + _f(r.get("undated_paid")), 2)

        epay_state = r.get("epay_state")
        if epay_state == EPAY_PAID:
            cards["epay_paid"] = round(cards["epay_paid"] + _f(r.get("epay_paid")), 2)
        elif epay_state == EPAY_UNKNOWN:
            cards["epay_unknown_findings"] += 1
        elif epay_state == EPAY_NONE:
            cards["epay_none_findings"] += 1

        appeal = (appeal_of(r) if callable(appeal_of) else r.get("appeal_status")) or ""
        appeal = str(appeal).strip().lower()
        key = appeal if appeal in cards["by_appeal"] else "no_appeal"
        cards["by_appeal"][key] += 1

        if r.get("store"):
            stores.add(str(r["store"]).strip().lower())
        if r.get("rep"):
            reps.add(str(r["rep"]).strip().lower())

    cards["stores"] = len(stores)
    cards["reps"] = len(reps)
    cards["still_out_note"] = (
        "What is still out counts only the activations that could be looked up. %s finding(s) "
        "carrying %s could not be, and are reported separately rather than counted as a loss."
        % (cards["unknown_findings"], ("$%.2f" % cards["unknown_withheld"]))
        if cards["unknown_findings"] else None)
    return cards
