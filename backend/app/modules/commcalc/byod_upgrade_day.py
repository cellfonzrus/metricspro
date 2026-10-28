"""
A BYOD ACTIVATION AND AN UPGRADE ON THE SAME NUMBER, SAME STORE, SAME DAY — the ONE home
(owner ask 2026-10-28, index §COMMCALC-42).

Owner, verbatim: *"Need to create a new flag report to check if a byod and an upgrade is done on the
same day for the same phone number at the same store, add standard filters and send options"*.

════════════════════════════════════════════════════════════════════════════════════════════════════
DUPLICATE CHECK (build gate, CLAUDE.md) — WHAT WAS SEARCHED AND WHAT IS REUSED
════════════════════════════════════════════════════════════════════════════════════════════════════
Searched `flag_registry.TYPES`, the index and `module_graph` for anything already answering "did one
phone line get two different kinds of activation in one day":

  MULTI_HANDSET_ONE_BUYER (§COMMCALC-05)  several handsets to one BUYER in one day. Adjacent, and
                                          deliberately NOT extended: its key is the bill-to NAME and
                                          its unit is a handset, while this one's key is the phone
                                          LINE and its unit is the activation event. One detector
                                          cannot carry two keys without lying about one of them.
                                          Its SHAPE is copied wholesale — the source-ownership rule,
                                          the injected predicates, the stated gaps.
  DUPLICATE_IMEI (§53)                    one DEVICE on several lines. The mirror question (one
                                          device / many lines vs one line / many activations), so it
                                          answers nothing here.
  §19.28 BYOD-Swap exclusion              already knows a BYOD line can be a re-paper rather than a
                                          new subscriber, and EXCLUDES it from the activation count.
                                          That is a PAY rule and it is not re-derived: the exclusion
                                          vocabulary is untouched by this module, which counts what
                                          was rung rather than what pays.

  REUSED rather than re-derived — this module writes almost no logic of its own:
  what ONE activation IS,       `line_class.activation_events` (index §19.28 / §59). It folds each
  its PHONE KEY and its CLASS   invoice's activation-type lines to events keyed on the line's own
                                phone number, and resolves each event's class by the org's declared
                                precedence. So "two events, one number" and "which of them is the
                                BYOD" are ONE answer, the same one every activation count and every
                                per-activation payout already uses.
  the phone number itself       `pos/customer_identity.norm_phone`, reached THROUGH that home. This
                                module contains no digit-stripping of its own; a second normaliser
                                would eventually disagree about a dialled 1.
  which classes make a pair     `flag_registry.rule_params` (RULE TWO) — a config row, so a tenant
                                who wants to watch byod+port instead edits a row.
  severity / area / label       `flag_registry`. One scale, one board.
  which line is a HANDSET       INJECTED as `is_box` by the caller, from the same per-org
                                `accessory_config.box_departments` set the box count uses. Never
                                read here and never defaulted — see `cases`.

  NEW here, and nowhere else: grouping activation EVENTS by (store, day, phone number), and the
  pair test over the classes present on one such number-day.

════════════════════════════════════════════════════════════════════════════════════════════════════
WHY THE UNIT IS AN EVENT AND NOT A LINE — the regression this module exists around
════════════════════════════════════════════════════════════════════════════════════════════════════
Every line of a receipt carries the receipt's contract type, so one upgrade sale lands as five or six
lines reading 'Upgrade' (the plan, autopay, the protection add-on, the handset, the set-up fee).
Counting LINES, the live house feed for Aug–Oct 2026 showed 456 number-days carrying both words. The
same feed read through `activation_events` shows **95**. The other 361 were one sale counted several
times.

And the sharper trap: 46 live lines read **'BYOD Upgrade'**, which is a single sale. `activation_class`
resolves it to `byod` (the org's precedence puts byod first), so it is ONE event and cannot pair with
itself. A detector that looked for the two WORDS rather than the two EVENTS would report a pair on
every one of those lines. That is why nothing here reads a contract type.

════════════════════════════════════════════════════════════════════════════════════════════════════
WHAT WAS MEASURED BEFORE THIS SHIPPED, AND WHAT IT CANNOT MEASURE
════════════════════════════════════════════════════════════════════════════════════════════════════
Live `commcalc.daily_sales_feed`, Aug–Oct 2026, read read-only on 2026-10-28:

  · House org: 5,005 activation events, **4,988 of them keyed on a phone number (99.7%)**, and
    **95 number-days carrying both a BYOD event and an upgrade event** (48 in October, 25 in
    September, 22 in August). Every single one of the 95 has the identical shape: the BYOD event
    carries NO handset line, and the upgrade event on a SEPARATE receipt the same day carries a promo
    handset ('… Q4 Promo Upgrade') plus a $35 upgrade set-up charge. $5,978.83 across all flagged
    lines. Concentrated: 31 of 95 on one store, and one rep on 190 of the flagged lines.
  · The other active tenant: **93.9% of its events key on a DEVICE, not a number**, because its feed
    leaves `mdn` empty and carries a device-shaped tracking number instead. This detector can
    therefore measure almost nothing for that tenant, and 0 cases there means NOTHING LOOKED AT —
    which is why `summary` reports `numbered_share` and every surface must render the two differently
    (§19.26, "missing beats wrong").

NOTHING HERE DECIDES THAT ANYTHING WRONG HAPPENED. A customer who activates a line on a phone they
brought and then buys a handset the same day is a good sale, and it reads identically. So a case
carries the EVIDENCE a human needs — the two receipts, the handsets on each side, the device, the
dollars, the rep — and the words stay descriptive.
"""
from collections import defaultdict

from app.modules.commcalc import flag_registry as _reg
# THE one home for "what is one activation, what phone line is it on, what class is it" (§19.28).
# It reaches `pos/customer_identity.norm_phone` for the number, which is why nothing here does.
from app.modules.commcalc import line_class as _lc

#: The flag this module writes. Registered in `flag_registry.TYPES`; nothing here spells its
#: severity, area or label.
FLAG_TYPE = "BYOD_AND_UPGRADE_SAME_NUMBER"

#: The config key, read through `flag_registry.rule_params`: the set of activation classes that must
#: ALL appear on one number-day for it to be a case.
#:
#: THERE IS NO "must be on separate receipts" KNOB, AND THAT IS A FINDING RATHER THAN AN OMISSION.
#: One was written and removed the same day, because mutation-testing the lock showed it could never
#: change an outcome: `activation_events` keys an event on (invoice, phone line), so two activations
#: of ONE number on ONE receipt are ONE event by construction and can never be a pair, whatever the
#: POS rang. Every case this detector can produce is therefore already on two or more receipts —
#: which is why `cases` reports `separate_receipts` as EVIDENCE and §H2 of the lock asserts the
#: structural fact instead. A config row a tenant can set and that cannot do anything is the same
#: class of defect as a fake zero: it reads as a control and is not one.
CLASSES_KEY = "classes"

#: The `source` value THIS writer owns, and the reason it is not "sales".
#:
#: `flag_persist.sync` RETIRES every open flag in the period carrying one of the caller's `sources`
#: that the run did not produce, and `router._CALC_FLAG_SOURCES` already claims `'sales'` for the
#: commission recalculation's own pass. A finding written here under `'sales'` would be resolved away
#: by the next recalculation of the month — the detector would appear to work and then silently empty
#: itself. A writer owns its own source string or it does not own its rows.
SOURCE = "byod_upgrade_watchdog"

#: Said out loud on the screen rather than left for someone to discover.
KEY_CANNOT_ANSWER = (
    "Groups the activations on one phone line at one store on one day, so it cannot see the same "
    "line activated across different days, at different stores, or recorded under a different "
    "number.",
    "A sale whose activation lines carry no phone number of their own cannot be grouped to a line "
    "at all. Those are counted separately and reported, and a company whose feed never carries the "
    "number has nothing measured here rather than nothing to find.",
    "Which kind of activation a line is comes from the company's own declared vocabulary. A sale "
    "the company has not taught the platform to recognise is not classified, so it joins no pair.",
    "It says what was rung on one day, not whether it was right. A line activated on a phone the "
    "customer brought and upgraded onto a new handset the same day is an ordinary sale and looks "
    "exactly like this.",
)


def safe_float(v) -> float:
    try:
        return float(v or 0)
    except Exception:
        return 0.0


def _s(v) -> str:
    return str(v or "").strip()


def line_skip(row) -> bool:
    """PURE: the DISPLAY skip rule — a voided line or a Return is not a sale.

    The same two tests `router._line_skip` and the multi-handset sweep apply, kept to that pair
    deliberately: `gp_report.countable_sale_skip_reason` ALSO drops a line with a blank or 'admin'
    rep, which is a PAY question. An activation nobody is credited for still happened to a phone
    line, so that third rule would hide exactly the case this detector exists to raise.
    """
    return (_s((row or {}).get("voided")).lower() in ("true", "yes", "y", "1", "void", "voided")
            or _s((row or {}).get("trans_type")) == "Return")


def number_days(rows, rules, *, skip=None):
    """PURE: activation EVENTS folded to {(store, day, number): [events]}, plus what has no number.

    `rules` is the org's resolved `line_class` rules (`resolve_rules(...)`), REQUIRED and never
    defaulted here: the classes, the fields they are read from and the event precedence are all that
    org's declaration, and a silent house default would make this detector disagree with that
    company's own activation count.

    Each event carries its own `store` / `day` / `rows` for the caller, taken from its first line, so
    `cases` needs no second pass over the feed. Returns
    {"groups": {...}, "no_number": [events], "events": int}.
    """
    rows = rows if isinstance(rows, list) else list(rows or ())
    res = _lc.activation_events(rows, rules, skip=skip if skip is not None else line_skip)
    groups, loose = defaultdict(list), []
    for e in res.get("events") or ():
        lines = [rows[i] for i in (e.get("lines") or ()) if 0 <= i < len(rows)]
        first = lines[0] if lines else {}
        ev = {
            "cls": e.get("cls"),
            "trans_id": _s(e.get("trans_id")),
            "number": _s(e.get("key")) if e.get("key_kind") == "phone" else "",
            "key_kind": _s(e.get("key_kind")),
            "store": _s(first.get("store")),
            "day": _s(first.get("trans_date"))[:10],
            "rows": lines,
        }
        # An event the one home keyed on a DEVICE, or on the invoice because no line named either,
        # carries no phone line. It is kept and counted — never dropped, and never read as clean.
        if not ev["number"]:
            loose.append(ev)
            continue
        groups[(ev["store"], ev["day"], ev["number"])].append(ev)
    return {"groups": dict(groups), "no_number": loose,
            "events": len(res.get("events") or ())}


def _devices(rows, is_box):
    """PURE: the handset lines among `rows` by the CALLER's own box-department predicate.

    `is_box` is INJECTED and there is no default: a house-labelled guess would make this detector
    disagree with that store's own box count, and two answers to "is this line a handset" is the
    duplicate defect the index rules forbid. None yields None — not measured, which a surface must
    be able to tell from "no handset was sold".
    """
    if is_box is None:
        return None
    out = []
    for r in rows or ():
        try:
            if is_box(r):
                out.append(r)
        except Exception:
            continue
    return out


def cases(grouped, params, *, is_box=None):
    """PURE: the number-days whose events cover EVERY class in `params['classes']`, with evidence.

    Evidence only — there is no verdict field, by design (see the module docstring). Sorted by day
    descending then store then number, so the list is stable between runs and the newest day a
    manager can still act on is first.
    """
    want = [c for c in (((params or {}).get(CLASSES_KEY)) or ()) if _s(c)]
    if len(want) < 2:
        # Fewer than two classes cannot be a pair. Returning [] rather than flagging everything is
        # the honest reading of an emptied config row: "our company does not watch for this".
        return []
    out = []
    for (store, day, number), evs in (grouped.get("groups") or {}).items():
        by_cls = defaultdict(list)
        for e in evs:
            by_cls[e["cls"]].append(e)
        if not all(by_cls.get(c) for c in want):
            continue
        matched = [e for c in want for e in by_cls[c]]
        receipts = sorted({e["trans_id"] for e in matched if e["trans_id"]})
        lines = [r for e in matched for r in e["rows"]]
        per_cls = {}
        for c in want:
            cl = [r for e in by_cls[c] for r in e["rows"]]
            dev = _devices(cl, is_box)
            per_cls[c] = {
                "events": len(by_cls[c]),
                "receipts": sorted({e["trans_id"] for e in by_cls[c] if e["trans_id"]}),
                "lines": len(cl),
                "handsets": None if dev is None else len(dev),
                "devices": None if dev is None else sorted(
                    {_s(r.get("product_desc")) for r in dev if _s(r.get("product_desc"))}),
                "dollars": round(sum(safe_float(r.get("ext_price")) for r in cl), 2),
            }
        c = {
            "store": store, "day": day, "number": number,
            "classes": sorted(by_cls),
            "class_labels": [_lc.CLASS_LABELS.get(x, x) for x in want],
            "events": len(matched),
            "receipts": receipts,
            "receipt_count": len(receipts),
            # EVIDENCE, and structurally always true for a pair (see CLASSES_KEY): a per-receipt
            # check can see none of these. Carried anyway so a screen states it rather than a reader
            # having to know why.
            "separate_receipts": len(receipts) > 1,
            "dollars": round(sum(safe_float(r.get("ext_price")) for r in lines), 2),
            "all_zero_dollars": all(safe_float(r.get("ext_price")) == 0 for r in lines),
            "reps": sorted({_s(r.get("salesperson")) for r in lines if _s(r.get("salesperson"))}),
            "by_class": per_cls,
            # THE DRILL-DOWN: one entry per line of both sales — which activation it belongs to,
            # who sold it, for how much, on which receipt.
            "lines": [{
                "cls": e["cls"],
                "class_label": _lc.CLASS_LABELS.get(e["cls"], e["cls"]),
                "receipt": e["trans_id"],
                "rep": _s(r.get("salesperson")) or None,
                "item": _s(r.get("product_desc")) or None,
                "department": _s(r.get("department")) or None,
                "serial": _s(r.get("serial_1")) or None,
                "amount": round(safe_float(r.get("ext_price")), 2),
            } for e in matched for r in e["rows"]],
        }
        # The pair's two sides, surfaced flat so a table column, a sort and the sentences below can
        # read them without walking `by_class`. TWICE, on purpose, and NEITHER spelling is a class
        # word typed here:
        #   • POSITIONALLY (`first_*` / `second_*`), derived from the config's own order. Every
        #     piece of logic in this module reads these, so a tenant who declares a different pair
        #     gets the same sentences and the same signal rather than silently losing both.
        #   • BY CLASS NAME, derived from the class itself (`byod_handsets`, `upgrade_handsets` for
        #     the house pair). This is the DISPLAY convenience a column header wants, and it exists
        #     only because the name came out of the config — a literal here would be the RULE TWO
        #     branch the house forbids, and would quietly answer for the wrong class the first time
        #     somebody edited the row.
        # All of them are None, never 0, when no box predicate was injected.
        for pos, cls in (("first", want[0]), ("second", want[-1])):
            for field in ("handsets", "devices", "receipts"):
                c[f"{pos}_{field}"] = per_cls[cls][field]
                c[f"{cls}_{field}"] = per_cls[cls][field]
            c[f"{pos}_class"] = cls
        out.append(c)
    out.sort(key=lambda c: (c["day"], c["store"], c["number"]), reverse=True)
    return out


def summary(grouped, found, *, is_box=None):
    """PURE: the counts a screen states before any case list — including what was NOT looked at.

    `is_box` is the SAME predicate `cases` was given, and `handset_lines` is what it matched across
    EVERY activation event in the period, not only the flagged ones. That figure exists because a
    declared box vocabulary cannot be trusted to fit the feed: `router._accessory_config` falls back
    to the house department labels when an org has declared none (it has done since mig 218, to keep
    the house box numbers byte-identical), so a tenant whose POS spells its departments differently
    gets a set that matches NOTHING and every handset column reads a confident 0. Counting what the
    predicate actually matched turns that from a fake zero into a stated gap — it is measured rather
    than declared, which is the only version a surface can rely on.

    `numbered_share` is the honest headline for a tenant whose feed carries no phone number: 0 cases
    out of 0 measurable events is NOT a clean period, and a screen cannot say which without this.
    It is None, never 0.0, when there were no events at all — nothing was received, so no share of
    anything was measured.
    """
    groups = grouped.get("groups") or {}
    loose = grouped.get("no_number") or []
    evs = int(grouped.get("events") or 0)
    numbered = evs - len(loose)
    # None when nothing was received at all; a real share once there is a denominator.
    share = None if evs <= 0 else round(numbered / evs, 4)
    # POSITIONAL, never by class name: the pair is a config row, so the signal has to survive a
    # tenant declaring a different one (see `cases`).
    dev_known = found and all(c.get("second_handsets") is not None
                              and c.get("first_handsets") is not None for c in found)
    all_rows = [r for evs in groups.values() for e in evs for r in e["rows"]]
    box_lines = _devices(all_rows, is_box)
    return {
        "events": evs,
        "events_with_number": numbered,
        "events_without_number": len(loose),
        "numbered_share": share,
        "number_days": len(groups),
        # How many lines the box-department predicate matched across the WHOLE period. None when no
        # predicate was given; 0 while there were activation events means the declared vocabulary
        # does not fit this feed, and every handset figure on the screen is unreliable rather than
        # zero. The screen is expected to say so.
        "handset_lines": None if box_lines is None else len(box_lines),
        "box_vocabulary_matched": (None if box_lines is None
                                   else (None if not all_rows else bool(box_lines))),
        "cases": len(found),
        # More than the two activations the pair needs — a number rung three or more times in one
        # day at one store, which is a sharper thing to open first.
        "multi_event_cases": sum(1 for c in found if c["events"] > len(c.get("classes") or ())),
        "zero_dollar_cases": sum(1 for c in found if c["all_zero_dollars"]),
        "dollars": round(sum(c["dollars"] for c in found), 2),
        # THE live discriminator, counted: the shape all 95 measured cases carry — nothing handed
        # over on the first activation, a handset handed over on the second. None where no box
        # predicate was injected: not measured, which is not "none like that".
        "cases_upgrade_device_only": (
            None if not dev_known else
            sum(1 for c in found
                if c["first_handsets"] == 0 and (c["second_handsets"] or 0) > 0)),
        "cannot_answer": list(KEY_CANNOT_ANSWER),
    }


def describe(case) -> str:
    """PURE: the one sentence a manager reads. Descriptive — it never says fraud, abuse or scheme."""
    labels = " and ".join(case.get("class_labels") or case.get("classes") or ())
    where = (f"on {case['receipt_count']} separate receipts" if case["separate_receipts"]
             else "on one receipt")
    money = ("every line at $0.00" if case["all_zero_dollars"]
             else f"${case['dollars']:,.2f} in total")
    return (f"{labels} on the same number {case['number']} at {case['store']} on {case['day']}, "
            f"{where}, {money}{_device_phrase(case)}.")


def _device_phrase(case) -> str:
    """PURE: the clause naming what left the shelf on each side, or '' when nothing was measured.

    Says "no handset" only when handsets were actually LOOKED FOR and none were found. An unmeasured
    signal produces no clause at all, because a sentence that omits a signal is honest and one that
    asserts its absence would not be.
    """
    b, u = case.get("first_handsets"), case.get("second_handsets")
    if b is None or u is None:
        return ""
    if b == 0 and u > 0:
        dev = (case.get("second_devices") or [None])[0]
        return (f", with no handset on the first activation and {u} on the second"
                + (f" ({dev})" if dev else ""))
    return f", with {b} handset(s) on the first activation and {u} on the second"


def coaching_note(case) -> str:
    """PURE: what to actually DO about it — a check to perform, not an accusation to repeat.

    The shape every one of the 95 measured cases carries (no handset on the first activation, a
    handset on the second) is the one worth pulling the paperwork for first, so the note says so when
    BOTH signals were measured and BOTH read that way. It still asks for a check; it does not
    announce a conclusion.
    """
    if case.get("first_handsets") == 0 and (case.get("second_handsets") or 0) > 0:
        return ("The line was activated with no handset and then upgraded onto one the same day. "
                "Pull both receipts and confirm the customer really brought a device in, and that "
                "the second sale was what they asked for.")
    if case["separate_receipts"]:
        return ("Two activations were rung on this number on one day, on separate receipts. Confirm "
                "with the store what the second one was for.")
    return "Confirm with the store what each of these activations on one number was for."


def findings(rows, rules, *, org_id=None, period=None, period_month=None, period_year=None,
             rule_rows=None, is_box=None, skip=None):
    """PURE: watchdog rows for the Flags board, or [] when the tenant has this switched off.

    `rule_rows` is this org's `commcalc.watchdog_rule` rows, read by the caller — the class pair and
    the on/off switch come from `flag_registry`, so changing either is a config edit. Returns
    (findings, summary) so a screen can state the gaps even when nothing flagged; a bare [] would
    read as "all clear" when it can also mean "this feed carries no phone numbers".

    The row shape is `flag_persist._ROW_FIELDS`, not a shape of this module's own invention. The
    store goes in `store_address` because the grouped value is the POS's free-text spelling, and
    `flag_store_resolver.stamp_flags` is the ONE home that turns that into a `store_code` a district
    manager's board can filter on — a code guessed here would be a second store resolver.
    `evidence` is carried for a screen and is NOT a column: `flag_persist._clean` drops it on write.
    """
    grouped = number_days(rows, rules, skip=skip)
    if not _reg.is_enabled(FLAG_TYPE, rule_rows):
        return [], {**summary(grouped, [], is_box=is_box), "enabled": False}
    params = _reg.rule_params(FLAG_TYPE, rule_rows)
    found = cases(grouped, params, is_box=is_box)
    out = []
    for c in found:
        devices = [d for k in ("first_devices", "second_devices") for d in (c.get(k) or ())]
        serials = [l["serial"] for l in (c.get("lines") or ()) if l.get("serial")]
        out.append({
            "org_id": org_id,
            "period": period, "period_month": period_month, "period_year": period_year,
            "flag_type": FLAG_TYPE,
            "source": SOURCE,
            "store_address": c["store"],
            "transaction_date": c["day"],
            "amount": c["dollars"],
            # The phone line this is about reaches the flags table through the column that already
            # means a subscriber number on every other finding, so the board and the port-out
            # queues name a line the same way.
            "mdn": c["number"],
            # The handset counts ride the only free-text column left on the row, spelled as a
            # sentence rather than a bare number so the board cannot render it as money that moved.
            "customer_plan": (None if c.get("second_handsets") is None else
                              f"handsets {c.get('first_handsets')} then {c.get('second_handsets')}"),
            "epay_salesperson": (c["reps"][0] if len(c["reps"]) == 1 else None),
            "imei": (serials[0] if len(serials) == 1 else None),
            "phone_model": (devices[0] if len(devices) == 1 else None),
            "description": describe(c),
            "coaching_note": coaching_note(c),
            "evidence": c,
        })
    return _reg.stamp(out), {**summary(grouped, found, is_box=is_box), "enabled": True,
                             "classes": list(params.get(CLASSES_KEY) or ())}
