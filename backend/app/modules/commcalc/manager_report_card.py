"""MANAGER REPORT CARDS — what was assigned to a district manager, and whether the system says it was
met (owner directive 2026-10-08, index §59.9).

Owner, verbatim: *"create a report card for the Dm based on all the items assigned to them per store
and a check off by the system if those targets were met or not, the same report card will be made for
the market manager for all the goals assigned to the Dm but a higher level reporting so they are also
accoutable."*

PURE. No DB, no I/O, stdlib only — every input is handed in by the caller, which is what lets the
Daily Action Plan, the Peer Sales Comparison screen and these cards all be fed from ONE read.

WHAT THIS MODULE DOES NOT DO, and must never start doing:

  · It does not decide ATTAINMENT. `targets_engine.attainment_pct` is the one formula and the one
    contract (no target → None, never 0% and never 100%), and the roll-up is
    `targets_engine.aggregate_stores`, the same one the area view already uses. A card that computed
    its own percentage would let a DM's card and the DM's own Targets screen disagree about the same
    store on the same day.
  · It does not decide who is BEHIND THEIR PEERS. `peer_comparison.lagging` / `peer_action_item`
    (§59.4, §59.7) is that one home; a peer item arrives here already judged.
  · It does not decide WHO OWNS A STORE. `storeops/org_chain.dm_by_store` is the one org-tree walk,
    and its answer arrives here as `chain_by_store`. Where the tree names nobody, this module reports
    the store as unassigned WITH that reason — it never guesses an owner, and it never silently drops
    a store from the org's totals, because a store missing from a card reads as a store with nothing
    to answer for.

THE CHECK-OFF IS THREE-STATE, NOT TWO. `met` / `missed` / `no_target`. An item nobody set a target for
is not an item the store failed, and collapsing those two is how a report starts accusing people over
configuration. Every count below reports `no_target` separately for the same reason.
"""

# ── THE ITEMS, declared once ──────────────────────────────────────────────────────────────────────
# ONE registry of "what is assigned to a DM for a store". A fourth item is added HERE and every card,
# count and roll-up picks it up; there is no second list of items anywhere in the cards. Each item
# names where its target and its achievement come from, and nothing is computed in this file that the
# home it names could answer instead.
#
#   key        the item's stable key (an API field name; never shown to a person)
#   label      the words a manager reads
#   unit       'count' | 'money' | 'pct' — how a surface should format the two numbers
#   source     which injected fact answers it, for the reader of this file
MET, MISSED, NO_TARGET = "met", "missed", "no_target"
STATES = (MET, MISSED, NO_TARGET)

TARGET_CATEGORIES = ("activations", "upgrades", "byod", "accessories")

ITEMS = [
    {"key": "activations", "label": "Activations", "unit": "count",
     "source": "commcalc.targets vs the period's actuals (targets_engine)"},
    {"key": "upgrades", "label": "Upgrades", "unit": "count",
     "source": "commcalc.targets vs the period's actuals (targets_engine)"},
    {"key": "byod", "label": "BYOD", "unit": "count",
     "source": "commcalc.targets vs the period's actuals (targets_engine)"},
    {"key": "accessories", "label": "Accessories", "unit": "money",
     "source": "commcalc.targets vs the period's actuals (targets_engine)"},
    {"key": "conversion", "label": "Conversion (boxes per bill payment)", "unit": "pct",
     "source": "the store summary's own conversion block (targets_engine.scope_conversion)"},
    {"key": "peer_gap", "label": "Keeping up with stores of the same footfall", "unit": "count",
     "source": "peer_comparison.lagging (§59.4) — handed in already judged"},
]
ITEM_KEYS = tuple(i["key"] for i in ITEMS)
ITEM_BY_KEY = {i["key"]: i for i in ITEMS}


def _f(v):
    """A number, or None — never a silent 0.0 for something absent. PURE."""
    if v is None or v == "":
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _state(target, achieved):
    """THE check-off, for an item expressed as target-vs-achieved. PURE.

    No target (None, 0 or negative) → `no_target`, matching `targets_engine.attainment_pct`'s own
    contract rather than a second opinion about what a missing target means.
    """
    t = _f(target)
    if t is None or t <= 0:
        return NO_TARGET
    a = _f(achieved) or 0.0
    return MET if a >= t else MISSED


def store_items(summary_row, peer_item=None, peer_compared=None):
    """The check-off rows for ONE store, in `ITEMS` order. PURE.

    `summary_row` is one element of `GET /targets/{period}/summary`'s `stores` list: it already carries
    `categories[cat] = {monthly, achieved_mtd, unit, …}` and a FLAT `conversion = {boxes, billpays,
    rate, target, meets_target}`, both computed by `targets_engine`. Nothing is recomputed here — and
    for conversion not even the comparison, because the engine already published its own verdict in
    `meets_target` and a card that re-compared `rate` against `target` would be a second opinion about
    a question already answered. (The shape is read from the live payload, not assumed: an earlier
    draft of this module looked for `conversion.store` — a nesting that does not exist — and silently
    reported every store's conversion as untargeted.)
    `peer_item` is `peer_comparison.peer_action_item`'s output for this store, or None when the store
    is not behind its traffic band (or could not be compared). `peer_compared` separates those two:
    True = the comparison ran and placed this store in a band, so "no item" means "kept up"; False or
    None = it could not be compared, so the store gets `no_target`, never a pass mark it did not earn.

    Returns [{key, label, unit, state, target, achieved, detail}]. `detail` is a sentence only where
    the item has one to add — for the peer item it IS `peer_comparison`'s own wording, never a second
    phrasing of the same finding.
    """
    row = summary_row or {}
    cats = row.get("categories") or {}
    conv = row.get("conversion") or {}
    out = []
    for item in ITEMS:
        k = item["key"]
        if k in TARGET_CATEGORIES:
            c = cats.get(k) or {}
            target, achieved = c.get("monthly"), c.get("achieved_mtd")
            detail = None
        elif k == "conversion":
            target, achieved = conv.get("target"), conv.get("rate")
            detail = None
            if _f(target) and _f(achieved) is not None:
                detail = f"{conv.get('boxes')} boxes on {conv.get('billpays')} bill payments"
            # DEREFERENCE the engine's own verdict where it published one, rather than re-comparing.
            if conv.get("meets_target") is not None and _f(target) and _f(achieved) is not None:
                out.append({"key": k, "label": item["label"], "unit": item["unit"],
                            "state": MET if conv.get("meets_target") else MISSED,
                            "target": _f(target), "achieved": _f(achieved), "detail": detail})
                continue
        else:  # peer_gap — a store is 'missed' when the ONE home says it is behind, 'met' when not.
            # There is no number to compare here: the comparison is the target. A store that could
            # not be compared at all (no band, no peers, no bill payments) is `no_target`, with the
            # reason carried in the payload's own `peer_meta`, never a pass mark it did not earn.
            if peer_item is None:
                out.append({"key": k, "label": item["label"], "unit": item["unit"],
                            "state": MET if peer_compared else NO_TARGET,
                            "target": None, "achieved": None,
                            "detail": None if peer_compared
                                      else "not compared against its traffic band"})
                continue
            p = peer_item.get("peer") or {}
            out.append({"key": k, "label": item["label"], "unit": item["unit"],
                        "state": MISSED, "target": p.get("band_median"), "achieved": p.get("mine"),
                        "detail": peer_item.get("detail"), "severity": peer_item.get("severity")})
            continue
        out.append({"key": k, "label": item["label"], "unit": item["unit"],
                    "state": _state(target, achieved),
                    "target": _f(target), "achieved": _f(achieved), "detail": detail})
    return out


def tally(item_rows):
    """{met, missed, no_target, checked, score_pct} over any list of check-off rows. PURE.

    `checked` counts only the items that HAD a target, and `score_pct` is met ÷ checked — so a manager
    whose stores have no targets set scores None, not 0% and not 100%. Same doctrine as
    `attainment_pct`: absence is never performance.
    """
    counts = {s: 0 for s in STATES}
    for r in (item_rows or []):
        st = (r or {}).get("state")
        if st in counts:
            counts[st] += 1
    checked = counts[MET] + counts[MISSED]
    return {**counts, "checked": checked,
            "score_pct": (round(100.0 * counts[MET] / checked, 1) if checked else None)}


def _mgr_key(m):
    """A manager's stable identity — the employee id, never the name (two people share a name). PURE."""
    eid = str((m or {}).get("employee_id") or "").strip()
    return eid or None


def _mgr_label(m):
    """What to call a manager on a card: their name, else their employee id. PURE."""
    m = m or {}
    return str(m.get("name") or m.get("employee_id") or "").strip()


def build(summary_rows, chain_by_store, peer_items=None, aggregate=None, peer_compared=None):
    """THE cards. PURE.

    `summary_rows`    the `stores` list from `/targets/{period}/summary` (already scope-filtered by
                      the caller, so a card never shows a store its reader may not see).
    `chain_by_store`  `org_chain.dm_by_store`'s output: {STORE_CODE: {district, dm, above}}.
    `peer_items`      `peer_comparison.peer_items_by_store`'s map, keyed UPPER-CASE, or None.
    `peer_compared`   the store codes the comparison actually placed in a band (any iterable), so a
                      store it could not compare is marked `no_target` rather than passing by default.
                      None → no store is treated as compared.
    `aggregate`       `targets_engine.aggregate_stores`, injected rather than imported so this module
                      stays DB-free and import-free, and so the roll-up on a card is provably the SAME
                      function the area view uses. None → cards carry no `collective` block.

    Returns {dm_cards, manager_cards, unassigned, coverage, items}.
      dm_cards       one per district manager the tree names, each with a per-store check-off.
      manager_cards  one per manager ABOVE a district (the market manager and anyone over them) —
                     the higher-level view: a row per DM beneath them, never a wall of stores.
      unassigned     the stores the tree could not place, with the reason. Never dropped.
      coverage       how much of the org the tree can actually answer for.
    """
    rows = [r for r in (summary_rows or []) if r]
    chains = {str(k).upper(): v for k, v in (chain_by_store or {}).items()}
    peers = {str(k).upper(): v for k, v in (peer_items or {}).items()}
    compared = {str(c).upper() for c in (peer_compared or [])}

    # ── per store: the check-off, and who owns it ────────────────────────────────────────────────
    per_store, unassigned = [], []
    dm_stores: dict = {}
    for r in rows:
        code = str(r.get("store_code") or "").strip()
        ch = chains.get(code.upper()) or {}
        items = store_items(r, peers.get(code.upper()), code.upper() in compared)
        cell = {"store_code": code, "store": r.get("store") or r.get("address") or code,
                "market": r.get("market") or None,
                "district": ((ch.get("district") or {}) or {}).get("name"),
                "items": items, "tally": tally(items), "_row": r}
        per_store.append(cell)
        dms = [m for m in (ch.get("dm") or []) if _mgr_key(m)]
        if not dms:
            # WHY the reason is spelled out rather than left blank: "no DM is assigned to this store"
            # and "the org tree was never configured" are different facts, and only the second is
            # something the owner can fix in one place. Measured 2026-10-08: the tree resolves a DM
            # for 6 of 29 house stores and 0 of 20 Luxelink stores, so this list is most of the org
            # today — which is exactly why it is reported and not hidden.
            unassigned.append({"store_code": code, "store": cell["store"],
                               "market": cell["market"],
                               "reason": ("no manager is recorded for this store's district"
                                          if ch.get("district")
                                          else "this store is not under a district in the org tree"),
                               "tally": cell["tally"]})
            continue
        for m in dms:
            dm_stores.setdefault(_mgr_key(m), {"mgr": m, "stores": []})["stores"].append(cell)

    def _card(mgr, cells, kind, level=None):
        items = [i for c in cells for i in c["items"]]
        card = {"kind": kind, "employee_id": _mgr_key(mgr), "name": _mgr_label(mgr),
                "email": (mgr or {}).get("email"), "level": level or (mgr or {}).get("level"),
                "stores": len(cells), "tally": tally(items),
                "store_codes": sorted(c["store_code"] for c in cells)}
        if aggregate is not None:
            card["collective"] = aggregate([c["_row"] for c in cells])
        return card

    dm_cards = []
    for key in sorted(dm_stores):
        g = dm_stores[key]
        card = _card(g["mgr"], g["stores"], "dm")
        card["store_rows"] = [{k: v for k, v in c.items() if k != "_row"} for c in g["stores"]]
        dm_cards.append(card)
    dm_cards.sort(key=lambda c: (c["tally"]["missed"] * -1, c["name"]))

    # ── the level above: a card per manager over a district, rolled up BY DM ─────────────────────
    # "a higher level reporting so they are also accountable" — so the rows are the DMs beneath them,
    # with each DM's own check-off totals, rather than the same store list one level up. Every manager
    # above a district gets one, so a regional or an owner is covered without another mechanism.
    above_dms: dict = {}
    for key, g in dm_stores.items():
        seen = set()
        for c in g["stores"]:
            for m in (chains.get(c["store_code"].upper()) or {}).get("above") or []:
                mk = _mgr_key(m)
                if not mk or (mk, key) in seen:
                    continue
                seen.add((mk, key))
                slot = above_dms.setdefault(mk, {"mgr": m, "dms": {}})
                slot["dms"].setdefault(key, g)
    dm_card_by_key = {c["employee_id"]: c for c in dm_cards}
    manager_cards = []
    for mk in sorted(above_dms):
        slot = above_dms[mk]
        cells = [c for g in slot["dms"].values() for c in g["stores"]]
        card = _card(slot["mgr"], cells, "manager")
        card["dm_rows"] = sorted(
            ({"employee_id": k, "name": dm_card_by_key[k]["name"],
              "stores": dm_card_by_key[k]["stores"], "tally": dm_card_by_key[k]["tally"]}
             for k in slot["dms"] if k in dm_card_by_key),
            key=lambda d: (d["tally"]["missed"] * -1, d["name"]))
        card["dms"] = len(card["dm_rows"])
        manager_cards.append(card)
    manager_cards.sort(key=lambda c: (c["tally"]["missed"] * -1, c["name"]))

    placed = sum(c["stores"] for c in dm_cards) if dm_cards else 0
    coverage = {
        "stores": len(per_store),
        "stores_with_a_dm": len(per_store) - len(unassigned),
        "stores_without_a_dm": len(unassigned),
        "dms": len(dm_cards),
        "managers_above": len(manager_cards),
        # A store under two DMs is counted once per card, so this says plainly when that happened
        # instead of leaving two cards that do not add up to the org.
        "store_card_placements": placed,
    }
    return {"dm_cards": dm_cards, "manager_cards": manager_cards,
            "unassigned": unassigned, "coverage": coverage, "items": ITEMS}


def coverage_note(coverage):
    """ONE sentence for a surface to print above the cards when the org tree cannot answer. PURE.

    None when every store has a DM — a note that fires unconditionally is noise, and a card that
    stays silent while most of the org is unassigned is worse. The note names the fix (the org tree),
    never the symptom.
    """
    c = coverage or {}
    missing = int(c.get("stores_without_a_dm") or 0)
    total = int(c.get("stores") or 0)
    if not missing or not total:
        return None
    return (f"{missing} of {total} stores have no district manager in the org tree, so their items "
            f"are listed below but appear on nobody's card. Assigning them in the org chart is what "
            f"puts them on one.")
