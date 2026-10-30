"""THE one home for "what tender stands against this sale, and what does that leave outstanding"
(§POS-13).

OWNER ASK 2026-10-30, verbatim (OWNER-QUOTE-BEGIN):
  "i need the pos POS — Cash Register to have the ability to edit the sale from the prior day and
   create log of the cash or credit mismatch"
(OWNER-QUOTE-END)

Editing a prior-day sale already worked when he asked — §POS-12 shipped the edit, the window is
DECLARED (`sale_amendment.WINDOW_SETTING`) and its house default of one day already admits
yesterday. What did not exist is the second half of the sentence, and it is the half that matters:

THE CLASS, NOT THE INSTANCE (CLAUDE.md, "A fix is a DESIGN fix or it is not a fix)
─────────────────────────────────────────────────────────────────────────────────
The instance is "an edit should warn about a cash mismatch". The class is:

  **A SALE'S TENDER WAS NEVER RECONCILED AGAINST THE SALE'S OWN FIGURE, BY ANY CALLER, ON ANY
  PATH.** `pos.sale_payments` records what was taken; `pos.sales.total` records what is owed; and
  nothing in the platform ever compared the two.

That is why the gap is not confined to the edit screen. BOTH writes that can move the two apart
leave the payment rows untouched, and the sibling check found them together:

  · `pos.amend_sale` (mig 1103) restates the totals — the money owed moves, the money taken does not.
  · `pos.void_sale`  (mig 725)  stands the sale down and puts the stock back — the sale stops being
    owed at all, and the money taken for it is still recorded as taken.

So this module answers for both, and neither spells the comparison itself.

THE THREE THINGS THAT MAKE THE ANSWER HONEST
────────────────────────────────────────────
1. **An unrecorded tender is not a $0.00 tender.** Measured against production 2026-10-30: of 4,605
   `pos.sales` rows, **4,603 carry no payment row at all** — they are imported receipt history, not
   register sales. A module that read absence as zero would have declared 4,603 mismatches worth the
   whole sales ledger on its first run. `tendered()` answers `present: False`, `outstanding()`
   answers `measured: False` with a reason, and `mismatch` is False — *not measurable is not a
   finding*. This is `cash_variance`'s "ABSENCE IS NEVER ZERO" applied to a second pair of figures.

2. **What the tender is compared against is `total − balance`, never the total.** `pos.sales.balance`
   (mig 725) is a legitimately part-tendered sale. No live row carries one today (0 of 4,605), which
   is exactly why it has to be handled now: the day one does, a reader comparing against the bare
   total would invent a shortage the size of the balance.

3. **Which method is short is not always knowable, and is never guessed.** Tendered on one method,
   the shortfall is that method's. Tendered on two, which one settles it is a decision a human makes
   at the till — so `attribution` answers `ambiguous: True` and NAMES both, and the sentence asks
   rather than asserting. Attributing it would be the $20,000 guess `cash_variance` exists to
   prevent, in miniature.

NO NEW VARIANCE MATH, AND NO FOURTH SET OF WORDS. The short/over/ok verdict is
`deposit_recon.status_for` — the platform's existing truth table, which already takes a tolerance —
called with the resolved band. A second truth table is the sibling derivation the index rules forbid.
`direction` is this module's own addition because it answers a question the verdict does not: which
way the money has to travel.

WHY THE HOUSE BAND IS ZERO, WHERE CASH VARIANCE'S IS $5. §72's band exists because two HUMANS
counted the same cash and will differ. A tender mismatch is arithmetic over two recorded figures:
there is no counting error to absorb, so every cent is real money and the house answer is 0.00. It
is still CONFIG (RULE TWO) — `pos_settings.tender_mismatch_band`, resolved by `declared_band` — and
`HOUSE_BAND` is the only dollar literal in this module.

TENDER METHOD NAMES ARE THE TENANT'S, NEVER THIS MODULE'S. `payment_method` is whatever the tenant
stores; nothing here translates it against a list of methods this file knows, and
`sale_receipt.method_label` remains the one home for writing one FOR A CUSTOMER. The word "cash" and
the word "credit" appear in no branch.

💰 MOVES NO MONEY. Pure derivation. It books nothing, pays nothing and writes nothing; the caller
records what it returns.

PURE: no I/O, no framework import, no clock, no org id, no tenant, carrier or product name.
Proof: `backend/harness_pos_sale_settlement.py`, `tests/unit/test_pos_sale_settlement.py`.
Registered in docs/SYSTEM_DATA_FLOW_INDEX.md §POS-13.
"""
from __future__ import annotations

#: The house answer to "below how many dollars is a tender difference noise rather than money".
#: ZERO, and deliberately unlike §72's $5: that band absorbs two humans counting the same cash, and
#: there is nothing to absorb here (see the module note). THE ONLY dollar literal in this module;
#: the per-org override is `pos.pos_settings.tender_mismatch_band`, resolved by `declared_band`.
HOUSE_BAND = 0.0

#: The setting an org declares its own band in. Named here so every caller reads the key from the one
#: home instead of spelling it beside its own default.
BAND_SETTING = "tender_mismatch_band"

#: Which way the money has to travel. The verdict (`short`/`over`/`ok`) says whether the figures
#: agree; these say what somebody has to DO about it, which is the question the till is asking.
COLLECT = "collect"   # less was tendered than is owed — the customer owes the difference
REFUND = "refund"     # more was tendered than is owed — the difference goes back
SETTLED = "settled"   # the two agree within the declared band
DIRECTIONS = (COLLECT, REFUND, SETTLED)

#: Why a comparison produced no number. Each is a sentence a screen can show as-is, and none of them
#: is a dollar figure — "we could not measure this" must never render as money.
UNMEASURED_REASONS = {
    "tender_unrecorded": "no payment was ever recorded against this sale, so what was taken cannot "
                         "be compared with what the sale comes to",
    "total_unrecorded": "this sale does not carry a total, so there is nothing to compare the "
                        "tender against",
}


def _f(v):
    try:
        return round(float(v or 0), 2)
    except (TypeError, ValueError):
        return 0.0


def _is_voided(sale) -> bool:
    """Whether this sale has been stood down. Read the same way `sale_amendment.refusal` reads it —
    status OR the stamp, because mig 725's `void_sale` sets both and a row touched by hand may carry
    only one."""
    s = sale or {}
    return str(s.get("status") or "").strip().lower() == "voided" or bool(s.get("voided_at"))


# ── the tender as recorded ───────────────────────────────────────────────────────────────────────
def tendered(payments) -> dict:
    """What was actually taken against this sale: the sum, and the split by method.

    `present: False` with `total: None` when nothing was recorded. That is NOT 0.00, and keeping the
    two apart is the single most important thing in this module — see the 4,603-row finding in the
    module note. Methods are returned SORTED so a log row and a screen never disagree about order.
    """
    rows = payments or []
    if not rows:
        return {"present": False, "total": None, "by_method": {}, "methods": [], "count": 0}
    by: dict[str, float] = {}
    for p in rows:
        method = str((p or {}).get("payment_method") or "").strip()
        if not method:
            method = "unrecorded"
        by[method] = round(by.get(method, 0.0) + _f((p or {}).get("amount")), 2)
    return {"present": True,
            "total": round(sum(by.values()), 2),
            "by_method": by,
            "methods": sorted(by),
            "count": len(rows)}


# ── what the tender is compared against ──────────────────────────────────────────────────────────
def expected(sale) -> dict:
    """What this sale expects to have been tendered: `total − balance`, or **nothing at all** when it
    has been voided.

    A voided sale is not a sale with a total of zero that happens to be flagged — it is a sale that
    is no longer owed, so what it expects in tender is 0.00 and the whole of what was taken for it is
    outstanding. That is how the `void_sale` sibling is answered by this same one home rather than by
    a second comparison written beside it.
    """
    s = sale or {}
    if _is_voided(s):
        return {"present": True, "amount": 0.0, "voided": True,
                "total": _f(s.get("total")), "balance": _f(s.get("balance"))}
    if s.get("total") in (None, ""):
        return {"present": False, "amount": None, "voided": False,
                "total": None, "balance": _f(s.get("balance"))}
    return {"present": True,
            "amount": round(_f(s.get("total")) - _f(s.get("balance")), 2),
            "voided": False, "total": _f(s.get("total")), "balance": _f(s.get("balance"))}


def declared_band(value=None) -> float:
    """PURE: the dollar band in force, as the org declared it, with the house answer behind it.

    `value` is what the caller read from ITS OWN org's `pos.pos_settings`, so this module does no I/O
    and there is no path by which one tenant's declaration could answer for another's sale. A blank,
    negative or unparseable declaration is the HOUSE default, never zero-by-accident and never
    "forever": an operator clearing a cell must not silently change what counts as money.
    """
    if value in (None, ""):
        return HOUSE_BAND
    try:
        v = round(float(value), 2)
    except (TypeError, ValueError):
        return HOUSE_BAND
    return v if v >= 0 else HOUSE_BAND


def attribution(methods) -> dict:
    """WHICH tender a difference belongs to — and whether that is knowable at all.

    One method: the difference is that method's, unambiguously. Two or more: which of them settles it
    is a decision somebody makes at the till, and this module will not make it for them. `methods` is
    always returned so the screen can ask about the right ones.
    """
    ms = list(methods or [])
    if len(ms) == 1:
        return {"method": ms[0], "ambiguous": False, "methods": ms}
    return {"method": None, "ambiguous": len(ms) > 1, "methods": ms}


# ── the comparison. The only place tender and total are allowed to meet. ─────────────────────────
def outstanding(sale, payments, band=None) -> dict:
    """PURE: what was taken against what is owed → the variance, the verdict, and which way the money
    has to travel. Or a refusal with a reason, when one of the two was never recorded.

    `variance = tendered − expected`, so **negative is short** here as on every other recon surface
    in the platform, matching `deposit_recon.status_for` and §72.

    `mismatch` is the one key a caller should branch on to decide whether there is anything to log.
    It is True only when the comparison was MEASURED and the verdict is outside the band — an
    unmeasurable pair is not a finding, and logging it as one is how 4,603 rows of imported history
    would become 4,603 accusations.
    """
    from ..closing import deposit_recon
    dollar_band = declared_band(band)
    got, want = tendered(payments), expected(sale)
    out = {
        "tendered": got, "expected": want, "band": dollar_band,
        "variance": None, "verdict": None, "direction": None,
        "measured": False, "mismatch": False, "reason": None, "reason_label": None,
        "attribution": attribution(got["methods"]),
    }
    if not want["present"]:
        reason = "total_unrecorded"
    elif not got["present"]:
        reason = "tender_unrecorded"
    else:
        var = round(_f(got["total"]) - _f(want["amount"]), 2)
        # `status_for` returns 'ok' | 'short' | 'over' — adopted rather than respelled, so this
        # surface cannot drift from every other recon screen in the platform.
        verdict = deposit_recon.status_for(var, dollar_band)
        out.update({
            "variance": var,
            "verdict": verdict,
            "direction": SETTLED if verdict == "ok" else (COLLECT if var < 0 else REFUND),
            "measured": True,
            "mismatch": verdict != "ok",
        })
        return out
    out["reason"] = reason
    out["reason_label"] = UNMEASURED_REASONS[reason]
    return out


# ── the row that becomes the log ─────────────────────────────────────────────────────────────────
#: The columns the log row carries, named here so the migration, the endpoint and the report all
#: dereference one list instead of each spelling the same seven names.
RECORD_FIELDS = ("tender_total", "tender_expected", "tender_variance", "tender_verdict",
                 "tender_direction", "tender_measured", "tender_methods", "tender_reason",
                 "tender_attribution")


def record(sale, payments, band=None) -> dict:
    """The mismatch as the log stores it (`pos.sale_amendments`, mig 1104).

    An UNMEASURABLE comparison files its reason and NO figures. A row carrying both a reason and a
    variance would let a later reader pick whichever suited it, which is the fallback-basis defect
    §66 already closed on the commission side: a placement of last resort must never read as
    evidence.
    """
    out = outstanding(sale, payments, band=band)
    return {
        "tender_total": out["tendered"]["total"],
        "tender_expected": out["expected"]["amount"],
        "tender_variance": out["variance"],
        "tender_verdict": out["verdict"],
        "tender_direction": out["direction"],
        "tender_measured": out["measured"],
        "tender_methods": out["tendered"]["methods"],
        "tender_reason": out["reason"],
        "tender_attribution": out["attribution"],
    }


# ── what the till says ───────────────────────────────────────────────────────────────────────────
def sentence(sale, out) -> str:
    """One line the till shows, so a mismatch is a decision at the counter and not a discovery at
    close. A SENTENCE rather than a code, for the same reason `sale_amendment.refusal` is one: every
    caller shows it to a cashier, and a second mapping from code to words is a second chance for two
    screens to word one thing differently.

    It never prints a dollar figure it did not measure, and never names a single method when the sale
    carried two — it asks which one instead.
    """
    o = out or {}
    num = (sale or {}).get("transaction_id") or "?"
    if not o.get("measured"):
        return (f"What was tendered on transaction #{num} cannot be checked against what it now "
                f"comes to: {o.get('reason_label') or 'one of the two figures was never recorded'}.")
    if not o.get("mismatch"):
        return (f"Transaction #{num} still adds up: what was tendered matches what it comes to, so "
                "there is nothing to collect or refund.")
    amount = abs(_f(o.get("variance")))
    attr = o.get("attribution") or {}
    ms = attr.get("methods") or []
    if o.get("direction") == COLLECT:
        verb, noun = "collect", "from the customer"
    else:
        verb, noun = "refund", "to the customer"
    if attr.get("method"):
        where = f"on the {attr['method'].replace('_', ' ')} it was tendered on"
    elif ms:
        joined = " and ".join(m.replace("_", " ") for m in ms)
        where = (f"— it was tendered on {joined}, so which of them this settles has to be recorded "
                 "at the till")
    else:
        where = "— the tender method was not recorded"
    return (f"Transaction #{num} now leaves ${amount:,.2f} to {verb} {noun} {where}. "
            "It is logged either way.")


# ── the rollup the log report leads with ─────────────────────────────────────────────────────────
def summarise(rows) -> dict:
    """PURE: the assessed rows of the log report → the counts and the money, by state.

    EVERY DOLLAR FIGURE HERE IS MEASURED MONEY ONLY. A row that could not be measured raises
    `not_measurable` and contributes to no total — not to `net_variance`, not to `to_collect`, not to
    `to_refund`. That is not a nicety: 4,603 of the 4,605 live sales are unmeasurable (module note),
    so a rollup that folded them in at 0.00 would read "4,603 sales settled" on a screen whose whole
    job is to say which sales are not.

    `to_collect` and `to_refund` are ABSOLUTE amounts, because a manager reads them as two separate
    piles of money to deal with. `net_variance` keeps the sign, so it still answers "is the drawer
    expected over or short overall".
    """
    out = {"sales": 0, "settled": 0, "mismatches": 0, "not_measurable": 0,
           "to_collect": 0.0, "to_refund": 0.0, "net_variance": 0.0,
           "voided_with_tender": 0, "edited": 0}
    collect = refund = net = 0.0
    for r in rows or []:
        row = r or {}
        out["sales"] += 1
        if row.get("was_edited"):
            out["edited"] += 1
        if not row.get("measured"):
            out["not_measurable"] += 1
            continue
        net += _f(row.get("variance"))
        if not row.get("mismatch"):
            out["settled"] += 1
            continue
        out["mismatches"] += 1
        if row.get("was_voided"):
            out["voided_with_tender"] += 1
        if row.get("direction") == COLLECT:
            collect += abs(_f(row.get("variance")))
        elif row.get("direction") == REFUND:
            refund += abs(_f(row.get("variance")))
    out["to_collect"] = round(collect, 2)
    out["to_refund"] = round(refund, 2)
    out["net_variance"] = round(net, 2)
    return out


def blockers(rows) -> list:
    """PURE: what the report could NOT measure, and how many times — commonest first.

    The §72 discipline: a surface that cannot measure something SAYS SO, in its own words, rather
    than reconstructing the missing figure as zero and reporting a number it does not have. Each
    entry is `{reason, count}` and the reason is already a sentence, so the screen shows it as-is.
    """
    counts: dict[str, int] = {}
    for r in rows or []:
        row = r or {}
        if row.get("measured"):
            continue
        reason = str(row.get("reason") or "").strip() or "this sale could not be assessed"
        counts[reason] = counts.get(reason, 0) + 1
    return [{"reason": k, "count": v}
            for k, v in sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))]
