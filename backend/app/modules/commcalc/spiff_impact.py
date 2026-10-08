"""SPIFF IMPACT — what ONE carrier pay type is worth to a store's commission revenue and its profit,
and which stores are not earning it on the sales they make.

OWNER ASK (2026-10-08, verbatim): *"create a report for management review to assess the affect of a
certain spiff on the overall commisison payout revenue for the store and what % does that help to
increase the profitablity and then report whoich stores are lakcing those sales in terms of % sales
which are contriuting to that profitability"*.

Three questions, in his order, and this module answers each with ONE number plus the reason it can or
cannot be answered:

  1. **What is this spiff worth to the store's commission payout revenue?** The spiff's dollars as a
     share of every carrier dollar that books to the SAME P&L line — so the denominator is the line
     the money actually lands on, never a line it was assumed to land on.
  2. **By what % does it raise profitability?** Net profit WITH it against net profit WITHOUT it:
     `spiff / (net_income - spiff)`. The carrier pays it on sales the store already made, so taking
     it away removes revenue and leaves every cost standing — which is why it is measured against the
     profit that survives without it, not against the profit that includes it.
  3. **Which stores are not earning it?** The spiff's PAID UNITS per 100 boxes sold, ranked inside the
     store's own traffic band — so the answer is "this store sells the same number of boxes to the
     same number of people and earns less of this spiff on them", not "this store is small".

════════════════════════════════════════════════════════════════════════════════════════════════════
DUPLICATE CHECK (build gate, CLAUDE.md) — EVERY NUMBER HERE IS SOMEBODY ELSE'S ALREADY
════════════════════════════════════════════════════════════════════════════════════════════════════
Searched before a line was written: §57 (pay category), §58 (the carrier dollar's classification),
§59 (peer traffic bands), §4 / §4b / §4e (the P&L and its per-scope totals), §4c (`pl_range`), §6
(rep commission and its spiff rates), §6a (`setup-fee/impact`, the nearest-named thing on the
platform), §31 (carrier earned vs employee paid), §19.52 (`device_reimb_recon`, the other reader of
this feed), and the reports list in §17/§18.

| the fact this report needs | the ONE home it DEREFERENCES |
|---|---|
| is this carrier dollar commission, spiff, residual or a reimbursement — and on whose authority | `commcalc/carrier_dollar_class.classify` (§58). Injected as `classify`; this module never looks at the string |
| which P&L line a component books to | `carrier_dollar_class.component_line` (§58), injected as `component_line` — config, never a branch |
| which category did the org DECLARE for a pay type | `commcalc/payment_category` (§57), reached through §58 — no tenth reader here |
| does this pay type's label NAME a month rung | `commission_ledger.parse_payment_month` (§4a), injected as `month_token`. That function's docstring is explicit that it answers "does this label SAY a month" and nothing else, which is exactly the question asked here |
| how many boxes did the store sell, and how many people came through its door | `router._sales_cell_agg` via `peer_comparison` (§59) — not one sale line is read here |
| which traffic band, and is this store BEHIND its band | `peer_comparison.band_of` / `_gaps` / `lagging` / `prompt_sentence` (§59.4) — THE one definition of behind. This module adds its metric THROUGH `peer_comparison.with_extra_metric` precisely so there is no second median, no second gap and no second verdict |
| revenue / gross profit / expenses / net income for a scope | `account/analysis.pl_totals` (§4, the one home) off the STORED per-store snapshot, read through `account/statement_filter.store_payloads` (§19.50's deduped read, extracted in this PR so both callers share it) |
| which store is this, spelled any way | `router._store_code_resolver` (§59's and Daily-Targets' resolver), injected as `store_of` |

**What is NEW here and nowhere else:** the share of the line, the profit-with-against-without
arithmetic, the paid-units-per-100-boxes measure, and the honesty rules below. Nothing else.

**NOT a sibling of `GET /commcalc/setup-fee/impact` (§6a).** That one asks "what would REP pay be at a
percentage nobody has set yet" — a forward-looking what-if on an unset rate, per rep. This one asks
"what did the CARRIER already pay for one of its own pay types, and what is that worth to the store's
books" — backward-looking, measured, per store. Different money, different grain, different question.

════════════════════════════════════════════════════════════════════════════════════════════════════
HONESTY RULES, because this report ranks stores and names them to their managers
════════════════════════════════════════════════════════════════════════════════════════════════════
  · **A spiff that does not book to the commission line says so.** Measured live on the house org,
    MOST of the carrier's promo pay types classify as REIMBURSEMENT (§58's ruling), so their dollars
    are not commission revenue at all. The report names the line the selected type books to and
    reports its share of THAT line. Presenting a reimbursement as commission would restate the very
    money §58 just moved.
  · **A profit LIFT is only reported from a profit.** `net_income - spiff` at or below zero cannot
    carry a percentage — 13 of 28 house stores were at a loss in September 2026 — so the lift is
    `None` with the reason said, and the DOLLARS (which are always true) are the answer. A percentage
    off a near-zero base is a number that lies loudly.
  · **A paid unit is not a sale when the pay type names a month rung.** The carrier pays its bounties
    in six monthly instalments, so "New Activation Bounty - Month 3" pays on activations made three
    months ago; its unit count against THIS month's boxes is a ratio of two different cohorts. The
    report still computes it, because the owner's question is a rate, but the caveat says plainly what
    the numerator is counting.
  · **A store whose spelling does not resolve is reported, never dropped and never merged.** Live
    2026-10-08 one house store's carrier statement spells an address that resolves to no store code
    while its P&L is stored under three spellings, so its profit is split and its carrier money is
    unjoinable. That is a store-identity defect to fix in the data, and it is REPORTED as one.
  · **Zero is a measurement here, missing is not.** A store the carrier paid nothing for this type
    shows $0.00 and 0 units — that IS the finding the owner asked for. A store with no P&L snapshot
    shows `None` and says so. The two never render the same.

PURE. Every input is handed in — the classification, the line routing, the month token, the store
key, the rolled-up sales and the P&L totals — so `backend/harness_spiff_impact.py` drives the whole
module DB-free under `env -i`.
"""

# The metric this report ranks and prompts on, folded into §59's band machinery through
# `peer_comparison.with_extra_metric` so "behind" keeps exactly one definition.
SPIFF_METRIC = "spiff_per_100_boxes"
SPIFF_METRIC_LABEL = "Spiff-paid units per 100 boxes"

# The component a pay type must carry for the report to call it a spiff when nothing was requested.
# A TUPLE, in preference order, and every member is a component of §58's own vocabulary — no carrier,
# promo or product word appears anywhere in this file (RULE TWO).
DEFAULT_PICK_COMPONENTS = ("SPIFF", "COMMISSION")


def _f(v):
    try:
        return float(str(v).replace(",", "").strip())
    except (TypeError, ValueError):
        return 0.0


def _i(v):
    try:
        return int(float(str(v).replace(",", "").strip()))
    except (TypeError, ValueError):
        return 0


def _r2(v):
    return round(_f(v), 2)


def _pct(num, den):
    """num/den as a percentage, or None when there is no denominator. Never a 0 standing in for
    "cannot say" — the §59 rule, kept identical here on purpose."""
    if not den:
        return None
    return round(_f(num) / _f(den) * 100.0, 2)


def _fold(v):
    """The pay-type key, folded the way §57 folds it (trimmed, inner whitespace squashed, lowered).
    Dereferenced rather than restated: `pay_type_options` and `store_money` must agree about whether
    two spellings are one type, and §57 already owns that rule for the DECLARATION side."""
    from app.modules.commcalc.payment_category import _fold as _pc_fold
    return _pc_fold(v)


# ── THE OPTION LIST — the dropdown, derived from the window's own rows (§13c enumeration doctrine) ─
def pay_type_options(rows, classify, component_line=None, default_line="carrier_comm",
                     month_token=None, type_key="compensation_type",
                     amount_key="payment_amount", qty_key="quantity",
                     store_key="business_address"):
    """Every carrier pay type present in the window, with what it paid and what it IS. PURE.

    The list is derived from the tenant's OWN rows, never from a list in code, so a pay type the
    carrier invented this quarter is offerable the moment it lands (RULE TWO, the same posture §57.2
    takes for its category filter). Each entry carries §58's verdict — the component, the basis it
    rests on, whether the org DECLARED it or the platform inferred it — so a manager picking from
    this dropdown can see that the thing he calls a spiff is booked as a reimbursement.

    `classify(raw_type)` -> §58's dict. `component_line(component)` -> the P&L line, or the caller's
    default. `month_token(raw_type)` -> an int when the label names a month rung, else None.
    """
    agg = {}
    for r in rows or ():
        raw = (r or {}).get(type_key)
        key = _fold(raw)
        if not key:
            continue
        e = agg.get(key)
        if e is None:
            cls = classify(raw) or {}
            comp = cls.get("component")
            e = agg[key] = {
                "type": str(raw or "").strip(),
                "amount": 0.0, "units": 0, "stores": set(),
                "component": comp,
                "basis": cls.get("basis"),
                "declared": bool(cls.get("declared")),
                "inferred": bool(cls.get("inferred")),
                "twin_of": cls.get("twin_of"),
                "pl_line": (component_line(comp) if component_line else None) or default_line,
                "month_rung": (month_token(raw) if month_token else None),
            }
        e["amount"] = _r2(e["amount"] + _f((r or {}).get(amount_key)))
        e["units"] += _i((r or {}).get(qty_key))
        st = str((r or {}).get(store_key) or "").strip()
        if st:
            e["stores"].add(st)
    out = []
    for e in agg.values():
        e["stores"] = len(e["stores"])
        out.append(e)
    # biggest money first — the order a manager scans — then by name so the list is stable.
    out.sort(key=lambda e: (-abs(e["amount"]), str(e["type"]).lower()))
    return out


def default_selection(options, requested=None):
    """Which pay type the report opens on → `(type, selection_basis)`. PURE.

    A REQUESTED type always wins, matched through the folding rule so a cosmetic re-spelling still
    finds it; `requested_not_found` when the window has no such type, and the report then says so
    rather than silently showing something else.

    With nothing requested the report opens on the LARGEST-dollar type whose component is a spiff
    (falling back to commission), and `selection_basis` NAMES that it was a default — because a
    report that silently picks its own subject is a report nobody can check. §6a's precedent is
    stricter still (it refuses to quote a rate nobody entered); the difference is that here every
    choice is a MEASURED past payment, so opening on one misquotes nothing."""
    opts = list(options or ())
    if requested is not None and str(requested).strip():
        want = _fold(requested)
        for e in opts:
            if _fold(e.get("type")) == want:
                return e["type"], "requested"
        return None, "requested_not_found"
    for comp in DEFAULT_PICK_COMPONENTS:
        for e in opts:                      # already sorted by absolute dollars
            if e.get("component") == comp:
                return e["type"], f"default_largest_{comp.lower()}"
    if opts:
        return opts[0]["type"], "default_largest"
    return None, "none_available"


# ── THE MONEY, PER STORE — the selected type against the line it books to ─────────────────────────
def store_money(rows, selected, classify, component_line=None, default_line="carrier_comm",
                store_of=None, type_key="compensation_type", amount_key="payment_amount",
                qty_key="quantity", store_key="business_address"):
    """{store: {spiff_amount, spiff_units, line_total, carrier_total, …}} for ONE pay type. PURE.

    `line_total` is every carrier dollar at that store booking to the SAME P&L line as the selected
    type — the honest denominator for "what is this worth to the store's commission payout revenue",
    because a share of a line the money does not land on would answer a different question than the
    one it looks like it answers.

    `store_of(raw_spelling)` is the canonical store key; a spelling it cannot resolve is kept under
    its raw text and named in `unresolved`, never merged into another store and never dropped.
    """
    want = _fold(selected)
    seen, per, unresolved = {}, {}, set()
    sel_line = None
    for r in rows or ():
        raw_t = (r or {}).get(type_key)
        key = _fold(raw_t)
        if not key:
            continue
        cls = seen.get(key)
        if cls is None:
            c = classify(raw_t) or {}
            comp = c.get("component")
            cls = seen[key] = {
                "component": comp,
                "line": (component_line(comp) if component_line else None) or default_line}
        if key == want and sel_line is None:
            sel_line = cls["line"]
        raw_s = str((r or {}).get(store_key) or "").strip()
        resolved = (store_of(raw_s) if (store_of and raw_s) else raw_s)
        st = resolved or raw_s
        # A resolver that signals failure by returning nothing is honoured here; the platform's own
        # store resolver instead falls back to the cleaned raw spelling (its docstring says so), so
        # the UNJOINABLE case is caught in `build` where a store key with carrier money but no sales
        # row and no P&L snapshot is visible. Both paths end in the store being REPORTED on its own
        # row, never merged into another store and never dropped.
        if raw_s and store_of and not resolved:
            unresolved.add(raw_s)
        e = per.get(st)
        if e is None:
            e = per[st] = {"spiff_amount": 0.0, "spiff_units": 0, "line_total": 0.0,
                           "carrier_total": 0.0, "by_line": {}}
        amt = _f((r or {}).get(amount_key))
        e["carrier_total"] += amt
        e["by_line"][cls["line"]] = e["by_line"].get(cls["line"], 0.0) + amt
        if key == want:
            e["spiff_amount"] += amt
            e["spiff_units"] += _i((r or {}).get(qty_key))
    for st, e in per.items():
        e["carrier_total"] = _r2(e["carrier_total"])
        e["spiff_amount"] = _r2(e["spiff_amount"])
        e["by_line"] = {k: _r2(v) for k, v in e["by_line"].items()}
        # The line total is read off the SAME per-line tally the carrier total is built from, so the
        # share can never be a fraction of a number this report did not also report.
        e["line_total"] = _r2(e["by_line"].get(sel_line, 0.0)) if sel_line else 0.0
        e["share_of_line_pct"] = _pct(e["spiff_amount"], e["line_total"])
        e["share_of_carrier_pct"] = _pct(e["spiff_amount"], e["carrier_total"])
    return per, (sel_line or default_line), sorted(unresolved)


# ── THE PROFIT EFFECT — with it, against without it ───────────────────────────────────────────────
LOSS_BASE_REASON = ("This store's net profit without the spiff is not positive, so there is no "
                    "profit base to raise by a percentage. The dollar figure is the honest answer: "
                    "that is how much smaller the loss is because the carrier paid this.")
NO_SNAPSHOT_REASON = ("No stored P&L exists for this store in this period, so its profit cannot be "
                      "read. Run the period's Accounting compute and the column fills in. Blank "
                      "here means not computed, never $0.00.")
NEGATIVE_SPIFF_REASON = ("This pay type is a NEGATIVE amount for this store — the carrier took money "
                         "back — so it lowers profit rather than raising it, and the percentage is "
                         "reported as the reduction it is.")


def profit_effect(spiff_amount, totals):
    """What the spiff is worth to ONE store's profit. PURE.

    `totals` is `account/analysis.pl_totals(payload)` for that store, or None when no snapshot
    exists. Returns the four figures and, when a percentage cannot be honoured, the REASON — never a
    percentage computed off a base that cannot carry one.

      net_income           the stored P&L's bottom line, WITH the spiff in it
      net_income_ex_spiff  that line with the spiff's dollars taken back out
      profit_lift_pct      spiff / net_income_ex_spiff — "net profit is this much higher because of it"
      margin_points        spiff / revenue — what it is worth as a share of everything the store took
    """
    amt = _r2(spiff_amount)
    if not totals:
        return {"net_income": None, "revenue": None, "net_income_ex_spiff": None,
                "profit_lift_pct": None, "margin_points": None, "profit_reason": NO_SNAPSHOT_REASON}
    ni = _r2(totals.get("net_income"))
    rev = _r2(totals.get("revenue"))
    base = _r2(ni - amt)
    out = {"net_income": ni, "revenue": rev, "net_income_ex_spiff": base,
           "profit_lift_pct": None, "margin_points": _pct(amt, rev), "profit_reason": None}
    if base > 0:
        out["profit_lift_pct"] = _pct(amt, base)
        if amt < 0:
            out["profit_reason"] = NEGATIVE_SPIFF_REASON
    else:
        out["profit_reason"] = LOSS_BASE_REASON
    return out


# ── THE RATE — paid units against boxes sold ──────────────────────────────────────────────────────
def units_per_100_boxes(units, boxes):
    """The owner's "% sales which are contributing" — paid units per 100 boxes sold. PURE.

    None when the store sold no boxes: a rate with no denominator is not a zero. Expressed per 100 so
    the column reads as the percentage the owner asked for, and it can legitimately exceed 100 when
    the pay type pays several instalments on one sale — which is what the month-rung caveat is for."""
    if not boxes:
        return None
    return round(_f(units) / _f(boxes) * 100.0, 2)


# ── CAVEATS — what this table cannot answer for this tenant, said ABOVE the numbers ───────────────
def _source_phrase(entry, twin):
    """Where an undeclared type's component came FROM, in words. PURE."""
    if entry.get("inferred") and twin:
        return f"an inference from an earlier-period declaration ({twin})"
    return "the platform's own keyword fallback"


def caveats(selected_entry, *, commission_line=None, unresolved_stores=None,
            stores_without_pl=None, pl_computed_at=None, stores_at_a_loss=0, stores_measured=0):
    """One sentence per thing a reader would otherwise take for performance. PURE.

    Every one of these renders as a plausible number on the screen: a share of a line the money does
    not land on, a blank profit column, a unit rate counting instalments on older sales. They are
    facts about the data and the configuration, so the report says so next to the column rather than
    letting a manager coach a rep on one.
    """
    out = []
    e = selected_entry or {}
    line = e.get("pl_line")
    if line and commission_line and line != commission_line:
        out.append({"column": "share_of_line_pct", "severity": "different_line", "message": (
            f"This pay type does not book to the commission line. Every dollar of it lands on "
            f"‘{line}’, so the share below is its share of THAT line, and these dollars are "
            f"not part of the store's commission payout revenue. Which line a component books to is "
            f"the org's own configuration — see the carrier-dollar classification.")})
    if e and not e.get("declared"):
        basis = str(e.get("basis") or "unknown")
        twin = e.get("twin_of")
        out.append({"column": "component", "severity": "not_declared", "message": (
            f"This org has not declared a pay category for this type, so what it IS rests on "
            f"{_source_phrase(e, twin)} (basis: {basis}), not on the org's own word. Declaring the "
            f"type turns every figure on this screen from an inference into a fact.")})
    rung = e.get("month_rung")
    if rung:
        out.append({"column": SPIFF_METRIC, "severity": "different_cohort", "message": (
            f"This pay type's label names month {rung}, so the carrier is paying an instalment on "
            f"sales made in an earlier month. The units counted here are instalment payments received "
            f"in this period, while the boxes are sales made IN it — the rate compares two "
            f"different cohorts and can exceed 100%. It is still the right ranking (every store's "
            f"numerator is the same kind of thing), but it is not “this month's sales that "
            f"earned it”.")})
    n = len(unresolved_stores or ())
    if n:
        out.append({"column": "store", "severity": "unresolved_identity", "message": (
            f"{n} store spelling(s) on the carrier statement resolve to no store code, so their "
            f"carrier money cannot be joined to their sales or their P&L and they are reported on "
            f"their own rows: {', '.join(sorted(unresolved_stores)[:4])}"
            f"{' …' if n > 4 else ''}. That is a store-identity gap in the data, not a sales "
            f"result — fix the mapping rather than reading anything into the row."),
            "stores": sorted(unresolved_stores)})
    m = len(stores_without_pl or ())
    if m:
        out.append({"column": "net_income", "severity": "no_snapshot", "message": (
            f"{m} store(s) carry carrier money in this period but have no stored P&L, so their "
            f"profit columns are blank: {', '.join(sorted(stores_without_pl)[:4])}"
            f"{' …' if m > 4 else ''}. Blank means not computed, never zero."),
            "stores": sorted(stores_without_pl)})
    if pl_computed_at:
        out.append({"column": "net_income", "severity": "as_computed", "message": (
            f"The profit columns are the STORED per-store P&L for this period, computed "
            f"{pl_computed_at}. They do not recompute when this screen loads, so a configuration or "
            f"feed change made after that time is not in them — press Recompute on the P&L and "
            f"reload.")})
    if stores_at_a_loss and stores_measured:
        out.append({"column": "profit_lift_pct", "severity": "loss_base", "message": (
            f"{stores_at_a_loss} of {stores_measured} store(s) do not have a positive net profit "
            f"once this spiff is taken out, so no lift percentage can be computed for them and the "
            f"column is blank with the reason on the row. The dollars still apply: that is how much "
            f"smaller each one's loss is.")})
    return out


# ── THE PAYLOAD ───────────────────────────────────────────────────────────────────────────────────
BASIS_NOTE = (
    "The spiff dollars and units are the carrier's own compensation statement for the period, "
    "classified by the org's declared pay categories (an inference says so), which is the same "
    "classification the P&L books these dollars with — so the totals on this screen and the "
    "P&L's carrier lines are the same money by construction. The profit figures are the stored "
    "per-store P&L. The boxes, the bill-payment traffic and the band are the shared sales "
    "aggregation every other sales report reads.")

LIFT_NOTE = (
    "“Profit lift” is the spiff measured against the profit that survives WITHOUT it "
    "(net profit − spiff), because the carrier pays it on sales the store already made: take it "
    "away and the revenue goes while every cost stays. A store with no positive profit without it "
    "carries no percentage — the dollars are the answer there.")


def build(money, peer_payload, pl_by_store, *, selected, selected_entry, selection_basis,
          options=None, commission_line=None, period=None, window_label=None,
          unresolved_stores=None, pl_computed_at=None, params=None):
    """THE payload. PURE.

    `money`         — `store_money`'s per-store dict, keyed the same way the peer payload is.
    `peer_payload`  — §59's payload AFTER `peer_comparison.with_extra_metric` has folded this
                      report's metric in, so `rows[*]['gaps'][SPIFF_METRIC]` and `lagging()` are
                      §59's own machinery and this module computes no median and no gap.
    `pl_by_store`   — {store: analysis.pl_totals(payload)} for the period, or {} — a store absent
                      from it has NO snapshot, which is reported, not read as zero.
    """
    peer_rows = {r.get("store"): r for r in ((peer_payload or {}).get("rows") or [])}
    peer_unbanded = {r.get("store"): r for r in ((peer_payload or {}).get("unbanded") or [])}
    pls = pl_by_store or {}

    rows, no_pl, unjoined, at_a_loss = [], [], [], 0
    for store in sorted(set(money or {}) | set(peer_rows) | set(peer_unbanded)):
        m = (money or {}).get(store) or {}
        p = peer_rows.get(store) or peer_unbanded.get(store) or {}
        totals = pls.get(store)
        if store in (money or {}) and totals is None:
            no_pl.append(store)
        eff = profit_effect(m.get("spiff_amount", 0.0), totals)
        boxes = p.get("boxes")
        row = {
            "store": store,
            "market": p.get("market") or "",
            "band": p.get("band"),
            "band_label": p.get("band_label"),
            "billpay_txns": p.get("billpay_txns"),
            "boxes": boxes,
            "spiff_amount": _r2(m.get("spiff_amount", 0.0)),
            "spiff_units": _i(m.get("spiff_units", 0)),
            "line_total": _r2(m.get("line_total", 0.0)),
            "carrier_total": _r2(m.get("carrier_total", 0.0)),
            "share_of_line_pct": m.get("share_of_line_pct"),
            "share_of_carrier_pct": m.get("share_of_carrier_pct"),
            SPIFF_METRIC: (p.get(SPIFF_METRIC) if SPIFF_METRIC in p
                           else units_per_100_boxes(m.get("spiff_units", 0), boxes)),
            "gaps": (p.get("gaps") or {}).get(SPIFF_METRIC),
            "peers": p.get("peers"),
        }
        row.update(eff)
        if row["net_income"] is not None and row["net_income_ex_spiff"] is not None \
                and row["net_income_ex_spiff"] <= 0:
            at_a_loss += 1
        if not p:
            unjoined.append(store)
            row["reason"] = ("This store has carrier money in the period but no sales rows, so it "
                             "has no boxes and no traffic band and cannot be compared with its "
                             "peers. Check the sales feed for the period.")
        rows.append(row)

    rows.sort(key=lambda r: -abs(_f(r.get("spiff_amount"))))

    # ESTATE TOTALS — summed from the rows above, never from a second read, so the headline and the
    # table can never disagree. The estate lift uses the estate's own base for the same reason a
    # store's does: a sum of per-store percentages is not a percentage of anything.
    e_amt = _r2(sum(_f(r["spiff_amount"]) for r in rows))
    e_units = sum(_i(r["spiff_units"]) for r in rows)
    e_line = _r2(sum(_f(r["line_total"]) for r in rows))
    e_carrier = _r2(sum(_f(r["carrier_total"]) for r in rows))
    e_ni = [r["net_income"] for r in rows if r["net_income"] is not None]
    e_rev = [r["revenue"] for r in rows if r["revenue"] is not None]
    e_boxes = [r["boxes"] for r in rows if r["boxes"] is not None]
    ni_sum = _r2(sum(e_ni)) if e_ni else None
    base = _r2(ni_sum - e_amt) if ni_sum is not None else None
    estate = {
        "spiff_amount": e_amt, "spiff_units": e_units,
        "line_total": e_line, "carrier_total": e_carrier,
        "share_of_line_pct": _pct(e_amt, e_line),
        "share_of_carrier_pct": _pct(e_amt, e_carrier),
        "net_income": ni_sum, "revenue": _r2(sum(e_rev)) if e_rev else None,
        "net_income_ex_spiff": base,
        "profit_lift_pct": (_pct(e_amt, base) if (base is not None and base > 0) else None),
        "margin_points": (_pct(e_amt, sum(e_rev)) if e_rev else None),
        "boxes": sum(_i(b) for b in e_boxes) if e_boxes else None,
        SPIFF_METRIC: units_per_100_boxes(e_units, sum(_i(b) for b in e_boxes) if e_boxes else 0),
        "stores": len(rows),
        "stores_with_pl": len(e_ni),
        "stores_at_a_loss": at_a_loss,
        "profit_reason": (None if (base is not None and base > 0) else
                          (LOSS_BASE_REASON if base is not None else NO_SNAPSHOT_REASON)),
    }

    # WHO IS BEHIND — §59's `lagging()` on this report's metric. Not re-decided here; the whole
    # reason `with_extra_metric` exists is that this list must be the same verdict the peer screen
    # and the action plan would reach on any metric.
    from app.modules.commcalc import peer_comparison as _pc
    lag = _pc.lagging(peer_payload or {}, metric=SPIFF_METRIC) if peer_payload else []
    for item in lag:
        item["prompt"] = _pc.prompt_sentence(item)
        mm = (money or {}).get(item.get("store")) or {}
        item["spiff_amount"] = _r2(mm.get("spiff_amount", 0.0))
        item["spiff_units"] = _i(mm.get("spiff_units", 0))

    return {
        "period": period,
        "window_label": window_label,
        "selected": selected,
        "selected_entry": selected_entry,
        "selection_basis": selection_basis,
        "commission_line": commission_line,
        "pl_line": (selected_entry or {}).get("pl_line"),
        "options": list(options or ()),
        "metric": SPIFF_METRIC,
        "metric_label": SPIFF_METRIC_LABEL,
        "estate": estate,
        "rows": rows,
        "lagging": lag,
        "bands": (peer_payload or {}).get("bands") or [],
        "band_cuts": (peer_payload or {}).get("band_cuts") or [],
        "band_basis": (peer_payload or {}).get("band_basis"),
        "caveats": caveats(selected_entry,
                           commission_line=commission_line,
                           unresolved_stores=sorted(set(unresolved_stores or ()) | set(unjoined)),
                           stores_without_pl=no_pl,
                           pl_computed_at=pl_computed_at,
                           stores_at_a_loss=at_a_loss,
                           stores_measured=len(e_ni)),
        "basis_note": BASIS_NOTE,
        "lift_note": LIFT_NOTE,
        "params": params or {},
        "note": (None if rows else
                 "Nothing is on the carrier's compensation statement for this period, so there is no "
                 "pay type to assess. Check that the statement has been uploaded for the month."),
    }
