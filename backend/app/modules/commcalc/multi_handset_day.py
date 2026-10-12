"""
SEVERAL HANDSETS TO ONE CUSTOMER IN ONE DAY — the ONE home (owner ask 2026-10-12, index
§COMMCALC-04).

Owner: *"add the famimly check to the regular activations also under the flags tile"* /
*"this wooulkd check the flags for ongoing actyivation not the test data uploaded for evauluation"*.
So: the live daily sales feed, never the prospect rows §COMMCALC-03 lands.

════════════════════════════════════════════════════════════════════════════════════════════════════
DUPLICATE CHECK (build gate, CLAUDE.md) — WHAT WAS SEARCHED AND WHAT IS REUSED
════════════════════════════════════════════════════════════════════════════════════════════════════
Searched `flag_registry.TYPES` and the index for anything already answering "too many handsets on
one buyer":

  DUPLICATE_IMEI (§53, fraud)    ONE device on SEVERAL lines. The inverse question — same area,
                                 opposite shape. Not extended: a detector cannot be both.
  RSK_ACTIVATIONS (§53, fraud)   counts activations on the RSK REGISTER per rep. About the till and
                                 the salesperson, not the buyer. Untouched.
  §COMMCALC-03 prospect eval     the SAME question on an UPLOADED carrier statement for a store we
                                 do not own. Deliberately NOT shared: that reads
                                 `prospect_feed_row`, keys on the carrier's own account column, and
                                 answers it weeks late from a paid statement. This reads the live
                                 POS feed the morning after. Two feeds, two account vocabularies,
                                 one question — and the honest split is two detectors, because
                                 neither feed can answer for the other. Stated here so the next
                                 reader does not "unify" them into a path that silently evaluates
                                 a prospect against live flags.

  REUSED rather than re-derived:
  which line is a HANDSET        the per-org `accessory_config.box_departments` set (mig 218), the
                                 SAME membership test `router._sales_cell_agg` counts boxes with.
                                 INJECTED by the caller, never re-read and never defaulted here —
                                 see `handset_rows`.
  severity / area / label        `flag_registry` (§53). One scale, one board.
  the threshold                  `flag_registry.rule_params` / `is_enabled`, so a tenant edits a
                                 config row and no code changes (RULE TWO).

  NEW here, and nowhere else: grouping handset lines by (store, day, buyer) ACROSS receipts, and
  the receipt breakdown inside a case.

════════════════════════════════════════════════════════════════════════════════════════════════════
WHY THE BUYER KEY IS THE NAME, AND WHY THAT IS NOT A GUESS
════════════════════════════════════════════════════════════════════════════════════════════════════
Measured against production 2026-10-12, the whole `commcalc.daily_sales_feed` (151,102 rows):

  · `customer_no` is UNUSABLE as the buyer key: absent on 59% of the rows that carry a device
    serial, and the rows that DO carry it are mostly `Other Carr. payments` and `System`. A check
    keyed on it reports a clean bill of health it has not earned.
  · `serial_1` is NOT a handset test: PLAN lines carry a serial too (1,062 'Total MAX 5G Plan $55',
    435 'Total Wireless Home Internet', the tablet plans). Keying on it counted one phone plus two
    plans as three devices. This is why the box-department set is dereferenced instead.
  · `customer` is free text and dirty — 'No Customer' (203 device rows), 'walk  in', 'BOOST MOBILE',
    'Back Order', inconsistent casing — so it is normalised and the junk values are DROPPED rather
    than grouped. A hundred unrelated walk-ins are not one buyer.

The key is therefore (org, store, DAY, normalised name). Same day and same store is what makes an
exact-name match defensible: it needs no fuzzy matching and invents no identity. The cost is stated
rather than hidden — see `KEY_CANNOT_ANSWER`.

════════════════════════════════════════════════════════════════════════════════════════════════════
WHY IT GROUPS ACROSS RECEIPTS
════════════════════════════════════════════════════════════════════════════════════════════════════
A per-receipt check was the obvious design and it is the wrong one. Measured on the same live feed:
501 cases of more than 2 handsets to one buyer at one store in one day, of which **191 are split
across several receipts** and invisible to a per-receipt test. The worst: 16 handsets across EIGHT
separate receipts (3248 W Lawrence Ave, 2026-09-18, every line $0.00). A threshold that a till can
evade by ringing a second sale is not a threshold.

════════════════════════════════════════════════════════════════════════════════════════════════════
WHAT THIS DOES NOT CONCLUDE
════════════════════════════════════════════════════════════════════════════════════════════════════
Nothing here decides that fraud happened, and no function in this file returns a verdict. A family
of five buying five phones is a good sale and reads identically to the thing the owner is worried
about. So a case carries the EVIDENCE a human needs to tell them apart — the handsets, the receipt
split, the dollars, the rep(s), the day — and the words stay descriptive. 129 of those 501 cases are
entirely $0.00, which is a promotional pattern worth a human's eye, not a proven offence.
"""
import re
from collections import defaultdict

from app.modules.commcalc import flag_registry as _reg

#: The flag this module writes. Registered in `flag_registry.TYPES`; nothing here spells its
#: severity, area or label — `_reg` owns all three.
FLAG_TYPE = "MULTI_HANDSET_ONE_BUYER"

#: House threshold key. "More than 2 handsets" is the owner's own number for the prospect report
#: (§COMMCALC-03), kept the same here so one question has one answer across both feeds. EXCLUSIVE:
#: 3 handsets flags, 2 does not.
THRESHOLD_KEY = "max_handsets"

#: Buyer names that are not a buyer. Normalised before comparison. These are the placeholder values
#: the live feed actually carries (measured 2026-10-12); grouping them would fuse hundreds of
#: unrelated sales into one false case, which is worse than missing them.
#: The `source` value THIS writer owns, and the reason it is not "sales".
#:
#: `flag_persist.sync` RETIRES every open flag in the period that carries one of the caller's
#: `sources` and was not produced by that run. `router._CALC_FLAG_SOURCES` already claims `'sales'`
#: for the commission recalculation's own pass, so a finding written here under `'sales'` would be
#: resolved away by the next recalculation of the same month — the detector would appear to work and
#: then silently empty itself. A writer owns its own source string or it does not own its rows.
SOURCE = "multi_handset_watchdog"

HOUSE_JUNK_BUYERS = ("no customer", "walk in", "cash customer", "boost mobile", "back order",
                     "n a", "na", "none", "test", "unknown")

#: Said out loud on the screen rather than left for someone to discover.
KEY_CANNOT_ANSWER = (
    "Groups by the buyer's name at one store on one day, so it cannot see the same person buying "
    "across different days, different stores, or under a differently-spelled name.",
    "Sales recorded with no buyer name, or a placeholder like 'No Customer', are not grouped at "
    "all — they are counted separately and reported as unattributable.",
    "A handset is a line in this company's own box departments. A line the company has not declared "
    "as a box department is not counted, and plan-only lines never are.",
)


def safe_float(v) -> float:
    try:
        return float(v or 0)
    except Exception:
        return 0.0


def normalise_buyer(raw) -> str:
    """PURE: a buyer name reduced to letters and single spaces, lowercased. '' when nothing is left.

    Deliberately crude — it folds 'Rene  Hernandez', 'rene hernandez' and 'RENE HERNANDEZ' together
    and nothing else. It does NOT fold initials, nicknames or misspellings: that would be inventing
    an identity, and a flag that accuses the wrong person is worse than one that misses.
    """
    return re.sub(r"[^a-z]+", " ", str(raw or "").lower()).strip()


def is_junk_buyer(name, junk=HOUSE_JUNK_BUYERS) -> bool:
    """PURE: is this normalised name a placeholder rather than a person?"""
    n = normalise_buyer(name)
    return (not n) or n in {normalise_buyer(j) for j in (junk or ())}


def handset_rows(rows, box_departments):
    """PURE: the lines that are a HANDSET SALE, by the caller's own box-department set.

    `box_departments` is REQUIRED and is the per-org set the caller already resolved
    (`_accessory_config(...)['box_departments']`, mig 218) — the same membership test
    `router._sales_cell_agg` counts boxes with. It is NOT defaulted here on purpose: a silent house
    default would make this detector disagree with that store's own box count, and two answers to
    "is this line a handset" is the duplicate defect the index rules forbid. An empty set yields no
    rows, and `coverage` says so, rather than quietly falling back to somebody else's labels.
    """
    want = {str(d).strip() for d in (box_departments or ()) if str(d).strip()}
    if not want:
        return []
    return [r for r in (rows or ()) if str((r or {}).get("department") or "").strip() in want]


def _case_key(row):
    return (str(row.get("store") or "").strip(),
            str(row.get("trans_date") or "")[:10],
            normalise_buyer(row.get("customer")))


def group_by_buyer_day(rows, box_departments, junk=HOUSE_JUNK_BUYERS):
    """PURE: handset lines folded to {(store, day, buyer): [rows]}, plus what could not be grouped.

    Returns {"groups": {...}, "unattributable": [rows], "handset_lines": int}. A row whose buyer is
    a placeholder goes to `unattributable` — counted and reported, never grouped and never silently
    dropped.
    """
    hs = handset_rows(rows, box_departments)
    groups, loose = defaultdict(list), []
    for r in hs:
        if is_junk_buyer(r.get("customer"), junk):
            loose.append(r)
            continue
        groups[_case_key(r)].append(r)
    return {"groups": dict(groups), "unattributable": loose, "handset_lines": len(hs)}


def cases(grouped, threshold):
    """PURE: the buyer-days carrying MORE THAN `threshold` handsets, with their evidence.

    Evidence only — there is no verdict field, by design (see the module docstring). Sorted by
    handset count descending, then by the key, so the list is stable between runs.
    """
    thr = max(0, int(threshold or 0))
    out = []
    for (store, day, buyer), lines in (grouped.get("groups") or {}).items():
        if len(lines) <= thr:
            continue
        receipts = defaultdict(list)
        for r in lines:
            receipts[str(r.get("trans_id") or "").strip()].append(r)
        devices = defaultdict(lambda: {"lines": 0, "serials": []})
        for r in lines:
            d = devices[str(r.get("product_desc") or "").strip() or "(no model on the row)"]
            d["lines"] += 1
            s = str(r.get("serial_1") or "").strip()
            if s:
                d["serials"].append(s)
        out.append({
            "store": store, "day": day, "buyer": buyer,
            "buyer_as_recorded": sorted({str(r.get("customer") or "").strip() for r in lines})[:3],
            "handsets": len(lines),
            "receipts": sorted(receipts),
            "receipt_count": len(receipts),
            # THE part a per-receipt check cannot see. 191 of 501 live cases are split like this.
            "split_across_receipts": len(receipts) > 1,
            "handsets_per_receipt": {k: len(v) for k, v in sorted(receipts.items())},
            "dollars": round(sum(safe_float(r.get("ext_price")) for r in lines), 2),
            "all_zero_dollars": all(safe_float(r.get("ext_price")) == 0 for r in lines),
            "devices": [{"model": m, **v} for m, v in sorted(devices.items())],
            "reps": sorted({str(r.get("salesperson") or "").strip()
                            for r in lines if str(r.get("salesperson") or "").strip()}),
        })
    out.sort(key=lambda c: (-c["handsets"], c["store"], c["day"], c["buyer"]))
    return out


def summary(grouped, found):
    """PURE: the counts a screen states before any case list — including what was NOT grouped."""
    return {
        "handset_lines": int(grouped.get("handset_lines") or 0),
        "buyer_days": len(grouped.get("groups") or {}),
        "cases": len(found),
        "handsets_in_cases": sum(c["handsets"] for c in found),
        "split_cases": sum(1 for c in found if c["split_across_receipts"]),
        "zero_dollar_cases": sum(1 for c in found if c["all_zero_dollars"]),
        "unattributable_lines": len(grouped.get("unattributable") or []),
        "cannot_answer": list(KEY_CANNOT_ANSWER),
    }


def describe(case) -> str:
    """PURE: the one sentence a manager reads. Descriptive — it never says fraud, abuse or scheme."""
    n, r = case["handsets"], case["receipt_count"]
    bit = f"{n} handsets" if n != 1 else "1 handset"
    where = f"on {r} separate receipts" if r > 1 else "on one receipt"
    money = "every line at $0.00" if case["all_zero_dollars"] else f"${case['dollars']:,.2f} in total"
    name = (case["buyer_as_recorded"] or [case["buyer"]])[0]
    return f"{bit} to {name} {where} at {case['store']} on {case['day']}, {money}."


def coaching_note(case) -> str:
    """PURE: what to actually DO about it — a check to perform, not an accusation to repeat."""
    if case["split_across_receipts"]:
        return ("Confirm these were one genuine sale and not split to stay under a per-sale limit, "
                "and check the buyer's ID against the activations.")
    return "Confirm the buyer's ID against the activations and that each line was wanted."


def findings(rows, box_departments, *, org_id=None, period=None, period_month=None,
             period_year=None, rule_rows=None, junk=HOUSE_JUNK_BUYERS):
    """PURE: watchdog rows for the Flags board, or [] when the tenant has this switched off.

    `rule_rows` is this org's `commcalc.watchdog_rule` rows, read by the caller — the threshold and
    the on/off switch come from `flag_registry`, so changing either is a config edit. Returns
    (findings, summary) so a screen can state the gaps even when nothing flagged; a bare [] would
    read as "all clear" when it can also mean "no box departments declared".

    The row shape is `flag_persist._ROW_FIELDS`, not a shape of this module's own invention. The
    store goes in `store_address` because `c["store"]` is the POS's free-text spelling, and
    `flag_store_resolver.stamp_flags` is the ONE home that turns that into a `store_code` a district
    manager's board can filter on — a code guessed here would be a second store resolver.
    `evidence` is carried for a screen and is NOT a column: `flag_persist._clean` drops it on write.
    """
    grouped = group_by_buyer_day(rows, box_departments, junk)
    if not _reg.is_enabled(FLAG_TYPE, rule_rows):
        return [], {**summary(grouped, []), "enabled": False}
    thr = _reg.rule_params(FLAG_TYPE, rule_rows).get(THRESHOLD_KEY)
    found = cases(grouped, thr)
    out = []
    for c in found:
        models = [d["model"] for d in (c.get("devices") or [])]
        serials = [s for d in (c.get("devices") or []) for s in (d.get("serials") or []) if s]
        out.append({
            "org_id": org_id,
            "period": period, "period_month": period_month, "period_year": period_year,
            "flag_type": FLAG_TYPE,
            "source": SOURCE,
            "store_address": c["store"],
            "transaction_date": c["day"],
            "amount": c["dollars"],
            "epay_salesperson": (c["reps"][0] if len(c["reps"]) == 1 else None),
            "imei": (serials[0] if len(serials) == 1 else None),
            "phone_model": (models[0] if len(models) == 1 else None),
            "description": describe(c),
            "coaching_note": coaching_note(c),
            "evidence": c,
        })
    return _reg.stamp(out), {**summary(grouped, found), "enabled": True, "threshold": thr}
