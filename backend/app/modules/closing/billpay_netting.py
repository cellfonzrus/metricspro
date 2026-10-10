"""THE CASH THAT IS COLLECTED SOMEWHERE ELSE — netting bill-pay cash out of the pickup envelope.

PURE (no DB, no network, stdlib only) so it can be proven offline — `backend/harness_billpay_netting.py`.

OWNER DIRECTIVE 2026-09-08
    "on the cash pick up it shows the full amount but it should only show the store cash amount to be
     picked up, as the epay amount is being declared and picked up on a different menu — this is
     duplicating the total cash."
and, asked which figure to net by:
    "it should be the total cash minus the epay cash, NOT as declared by the employee but as
     CALCULATED BY THE POS."

WHY THERE WAS AN OVERLAP AT ALL. The closing form's cash field is, verbatim per the owner's own
2026-09-02 directive, *"Total cash in store including Bill Payments"* — so `t_cash` is the whole
drawer and `epay_on_cash` is a SUBSET breakdown of it, never additional money. Cash Pickup collected
the whole drawer; the bill-pay pickup page separately offers the bill-pay cash for collection. Same
physical dollars, two screens.

WHY NOT THE DECLARED FIGURE. Measured live, house org, August 2026 (539 closings): declared
`epay_on_cash` totals $183,156.03 against $209,583.23 of `t_cash`, and on **147 rows (27.3%)** the
declared bill-pay cash EXCEEDS the total cash — 30-odd of them with `t_cash` $0.00 and $687-$891 of
bill-pay cash. A subset cannot exceed its whole, so the declaration cannot be trusted to net by. The
POS figure can: it is computed from the sales transactions, not typed by the person holding the cash.

WHAT COUNTS AS "THE POS FIGURE" is the caller's business (see `closing/router` — the sales-transaction
bill-pay leg first, the processor leg second, both already-shared resolutions). This module only takes
the number and splits it honestly.

FOUR STATES, NEVER TWO. An envelope's basis is one of:
    'pos'       a POS figure existed for that store-day and was netted out
    'declared'  the rep's OWN declared bill-pay cash was netted out of their own envelope
    'none'      no POS figure for that store-day — NOTHING is netted, and the caller says so
    'off'       the tenant has not switched netting on (RULE TWO: per-org config, house default off)
Subtracting a fabricated zero and calling it netted is the silent-zero defect this codebase keeps
paying for; 'none' exists so the DM is told the envelope is un-netted rather than shown a number that
merely looks reconciled.

WHY 'declared' EXISTS TOO (owner bug report 2026-10-02). The owner reported B-2612 / 2026-09-03 as a
defect: the drawer was $273, the rep declared $258 of it as bill-pay cash and the DM collected the
remaining $15, and Cash Pickup called that $258 SHORT — the same overlap this module was built for,
still visible because the 2026-09-08 switch has never been turned on. The report described the
DECLARED figures ("15 is declared as store cash and 258 as epay"), which is a different source from
the 2026-09-08 "as CALCULATED BY THE POS". Both are now sources this one mechanism can net by, chosen
per org by config (`cash_pickup_config.pickup_billpay_net_source`, mig 1038) — never by a second
code path, and never by a branch on a tenant's name (RULE TWO).

The caution that made the POS the original choice still stands and is not hidden by the new source:
on the declared source each envelope is still capped at its own cash (`declared_exceeds_cash` says
when that cap bit), and whenever a POS figure is ALSO available the result reports whether the POS
agrees (`pos_disagrees` / `pos_gap`), so a declaration the POS does not back is visible instead of
merely trusted.
"""

# The vocabulary of "which cash this envelope's amount is", in ONE place. The DB CHECK on
# `commcalc.cash_pickup.amount_basis` (mig 1039) is tied to this tuple by
# `backend/harness_billpay_netting.py`, so the database cannot hold a word this module has no
# behaviour for.
PICKUP_BASES = ("off", "none", "declared", "pos")
# Which SOURCE a tenant can choose. 'pos' is the house default, so an org that has not chosen behaves
# exactly as it did before mig 1038 existed.
NET_SOURCES = ("pos", "declared")
NET_SOURCE_DEFAULT = "pos"


def normalize_net_source(v):
    """One of NET_SOURCES, defaulting to 'pos' — an unreadable config word must never silently
    change which cash a DM is told to collect, so it degrades to the behaviour that shipped."""
    v = str(v or "").strip().lower()
    return v if v in NET_SOURCES else NET_SOURCE_DEFAULT


def basis_allowed(source, enabled=True):
    """The bases a given config CAN produce — the fail-closed check for a basis a caller claims a
    stored pickup row used (see `cash_pickup.amount_basis`, mig 1039)."""
    if not enabled:
        return ("off",)
    return ("declared",) if normalize_net_source(source) == "declared" else ("none", "pos")


def _f(v) -> float:
    try:
        return float(v or 0)
    except Exception:
        return 0.0


def _r2(v) -> float:
    return round(v + 0.0, 2)


def net_store_day(rows, pos_billpay_cash=None, enabled=True, source=NET_SOURCE_DEFAULT):
    """Net one STORE-DAY's POS bill-pay cash across that day's envelopes.

    `rows`   [{'key': hashable, 't_cash': float, 'epay_on_cash': float}] — one entry per rep envelope.
    `pos_billpay_cash`  the POS-calculated bill-pay CASH for this store-day, or None when there is
             no POS figure for it (a missing feed, an unmapped store — not a zero).
    `enabled`  the tenant's netting switch.
    `source`   which figure to net BY — 'pos' (the default, the 2026-09-08 directive) or 'declared'
             (the rep's own declared bill-pay cash, the 2026-10-02 report). With 'declared' a POS
             figure is still used, when one is given, to report whether the POS agrees.

    Returns {'basis', 'pos_cash', 'declared_cash', 'netted_total', 'unallocated', 'rows': {key: {...}}}
    where each row carries `gross`, `billpay_netted`, `net`, `declared_billpay` and
    `declared_exceeds_cash`.

    THE ENVELOPE IS THE FLOOR. A rep's envelope can never net below zero — there is no such thing as
    negative cash in a bag. Whatever cannot be taken out of the rep it was allocated to is offered to
    the rows that still have headroom, and anything STILL left over comes back as `unallocated`:
    reported, never quietly discarded, because it means the POS says more bill-pay cash passed through
    that store-day than anybody declared holding.
    """
    out = {"basis": "off", "pos_cash": None, "declared_cash": 0.0, "netted_total": 0.0,
           "unallocated": 0.0, "source": normalize_net_source(source),
           "pos_disagrees": False, "pos_gap": None, "rows": {}}
    items = []
    for r in (rows or []):
        k = r.get("key")
        g = _f(r.get("t_cash"))
        d = _f(r.get("epay_on_cash"))
        items.append({"key": k, "gross": g, "declared": d})
        out["declared_cash"] = _r2(out["declared_cash"] + g)

    def _finish(basis):
        out["basis"] = basis
        for it in items:
            out["rows"][it["key"]] = {
                "gross": _r2(it["gross"]), "billpay_netted": 0.0, "net": _r2(it["gross"]),
                "declared_billpay": _r2(it["declared"]),
                "declared_exceeds_cash": it["declared"] > it["gross"] + 0.005,
            }
        return out

    if not enabled:
        return _finish("off")

    if pos_billpay_cash is not None:
        out["pos_cash"] = _r2(max(0.0, _f(pos_billpay_cash)))

    # ── SOURCE 'declared' — each rep's own declaration, out of their own envelope ────────────────
    # No allocation to do: a declaration already belongs to exactly one rep, so there is nothing to
    # split and nothing to spill. The envelope is still the floor (no negative cash in a bag), and
    # the cap biting is what `declared_exceeds_cash` reports — the 27%-of-August case the POS source
    # exists for stays VISIBLE here rather than being netted away.
    if out["source"] == "declared":
        declared_sum = 0.0
        for it in items:
            t = _r2(min(it["declared"], it["gross"]))
            out["rows"][it["key"]] = {
                "gross": _r2(it["gross"]), "billpay_netted": t, "net": _r2(it["gross"] - t),
                "declared_billpay": _r2(it["declared"]),
                "declared_exceeds_cash": it["declared"] > it["gross"] + 0.005,
            }
            out["netted_total"] = _r2(out["netted_total"] + t)
            declared_sum = _r2(declared_sum + it["declared"])
        if out["pos_cash"] is not None:
            out["pos_gap"] = _r2(declared_sum - out["pos_cash"])
            out["pos_disagrees"] = abs(out["pos_gap"]) > 0.005
        out["basis"] = "declared"
        return out

    if out["pos_cash"] is None:
        return _finish("none")

    pos = out["pos_cash"]

    # Weights: the reps' OWN declared bill-pay share first — it is the only signal for who handled the
    # bill payments — then the cash they hold, then an even split. The declaration decides the SPLIT,
    # never the AMOUNT: the amount is always the POS figure (the owner's rule).
    weights = [it["declared"] for it in items]
    if sum(weights) <= 0:
        weights = [it["gross"] for it in items]
    if sum(weights) <= 0:
        weights = [1.0] * len(items)
    total_w = sum(weights) or 1.0

    alloc = [pos * (w / total_w) for w in weights]
    # Cap at each envelope's own cash, then re-offer the spill to whoever still has headroom.
    taken = [0.0] * len(items)
    spill = 0.0
    for i, it in enumerate(items):
        take = min(alloc[i], it["gross"])
        spill += alloc[i] - take
        taken[i] = take
    for _pass in range(4):
        if spill <= 0.005:
            break
        headroom = [max(0.0, items[i]["gross"] - taken[i]) for i in range(len(items))]
        hr_total = sum(headroom)
        if hr_total <= 0.005:
            break
        moved = 0.0
        for i in range(len(items)):
            if headroom[i] <= 0:
                continue
            extra = min(headroom[i], spill * (headroom[i] / hr_total))
            taken[i] += extra
            moved += extra
        spill -= moved
        if moved <= 0.005:
            break

    for i, it in enumerate(items):
        t = _r2(min(taken[i], it["gross"]))
        out["rows"][it["key"]] = {
            "gross": _r2(it["gross"]), "billpay_netted": t, "net": _r2(it["gross"] - t),
            "declared_billpay": _r2(it["declared"]),
            "declared_exceeds_cash": it["declared"] > it["gross"] + 0.005,
        }
        out["netted_total"] = _r2(out["netted_total"] + t)
    out["unallocated"] = _r2(max(0.0, pos - out["netted_total"]))
    out["basis"] = "pos"
    return out


def envelope_note(res, row_key):
    """One plain sentence for the DM about ONE envelope, or None when nothing needs saying."""
    row = (res or {}).get("rows", {}).get(row_key)
    if not row:
        return None
    basis = (res or {}).get("basis")
    if basis == "off":
        return None
    if basis == "none":
        return ("No POS bill-pay figure for this store-day, so nothing was netted out — this envelope "
                "still includes any bill-pay cash.")
    if basis == "declared":
        if row["billpay_netted"] <= 0:
            return None
        s = (f"${row['billpay_netted']:,.2f} of bill-pay cash, as declared on this closing, is "
             f"collected on the bill-pay screen and has been taken out of this envelope.")
        if row["declared_exceeds_cash"]:
            s += (" The declared bill-pay cash was MORE than the whole drawer, so only the drawer "
                  "was taken out.")
        if res.get("pos_disagrees"):
            s += (f" The POS figure for this store-day differs by ${abs(res['pos_gap']):,.2f}, so this "
                  "declaration is not backed by the sales data.")
        return s
    if row["billpay_netted"] > 0:
        s = (f"${row['billpay_netted']:,.2f} of POS bill-pay cash is collected on the bill-pay screen "
             f"and has been taken out of this envelope.")
        if res.get("unallocated", 0) > 0.005:
            s += (f" The POS says ${res['unallocated']:,.2f} more bill-pay cash passed through this "
                  "store-day than anyone declared holding.")
        return s
    return None


# ── MAY A COUNT OF THE WHOLE ENVELOPE BE COMPARED WITH AN AMOUNT ON THIS BASIS? ─────────────────
# THE ONE HOME for that judgement (owner question 2026-10-10, B-3PL / 2026-10-02: "declared on
# envelope is 552 but written on envelope 671, actual cash is 671 — where is the 552 coming from").
#
# WHAT WAS WRONG, MEASURED. `pickup_actual.row_variance` subtracted the envelope's stored `amount`
# from `actual_picked_amount` and called the difference short or over. That was sound when it was
# written: `amount` was the WHOLE drawer (its own docstring says "store_cash + epay_cash") and the
# bag held the whole drawer. Then this module's netting was switched on, which made `amount` *the
# drawer minus the POS bill-payment cash* — while the bag still holds whatever the rep physically
# left in it, and A REP NETS BY HIS OWN DECLARED FIGURE, NOT THE POS ONE. Two different
# subtractions, compared with each other, every day since.
#
# Live evidence (house org, 2026-10-10): of 998 cash pickups, 660 carry a DM count; 135 of those are
# on the 'pos' basis and 105 of them read short or over — 59 short ($1,365.50) and 46 over
# ($3,682.30). Each of those differences is the POS-versus-declared gap on bill-payment cash, which
# is a real fact about a rep's declaration and is NOT cash missing from a bag. B-3PL / 2026-10-02 is
# one of the 46: expected $552.50 (the $2,646.00 drawer less $2,093.50 of POS bill-payment cash),
# counted $671.00 (the drawer less the rep's own declared $1,975.00), "over" by exactly the
# $118.50 the rep under-declared.
#
# THE CLASS, NOT THE INSTANCE. §72 already says a variance between two cash figures is meaningless
# unless both count the same cash. This is the same class one level down, and the distinction §72's
# vocabulary CANNOT express: here both figures count the same cash (store cash), and they still
# disagree because they were netted FROM DIFFERENT SOURCES. §72's bases name WHICH CASH; a pickup
# basis names WHOSE FIGURE was taken out. So this judgement belongs beside the basis words it reads,
# not folded into §72's cash bases, which would make 'pos' and 'declared' look identical.
#
# THE RULE, derived from what each basis means rather than from which rows look wrong:
#   'off'      nothing was netted      → both figures are the whole drawer          → COMPARABLE
#   'none'     no POS figure existed   → nothing was netted, same as above          → COMPARABLE
#   'declared' netted by the REP's own figure → the bag was netted the same way     → COMPARABLE
#   'pos'      netted by the POS figure → the bag was netted by the rep's figure    → NOT COMPARABLE
#   a basis NOT RECORDED (NULL, '': 456 live rows predate mig 1039) → resolves to 'off' → COMPARABLE
#   a word that is not in PICKUP_BASES at all                       → resolves to 'off' → COMPARABLE
#
# WHY AN UNRECORDED BASIS IS NOT "UNKNOWN" HERE, AND WHY THAT IS NOT MY CALL TO MAKE. The platform
# ALREADY ruled on a missing pickup basis, in `router.pickup_basis_label`: unrecorded resolves to
# `off` — total cash — worded honestly as "total cash (not recorded — collected before the basis was
# stored)". That is correct on the facts: netting did not exist before mig 1038/1039, so an amount
# written before the column was added IS the whole drawer. Inventing a second answer here would be
# the copied-fact defect CLAUDE.md forbids, and it would also be WRONG IN PRACTICE: it would refuse
# 226 live shortage findings worth $59,321.53 that are perfectly sound. So the resolution is MOVED
# into this module (`resolved_basis`) and `pickup_basis_label` dereferences it — one fact, one home,
# both callers reading it.
#
# THE ALLOWLIST IS ON THE RESOLVED BASIS, not the raw one — the
# `carrier_dollar_class.component_is_evidence` posture (§66) applied where it belongs. A basis word
# a future migration adds therefore resolves to `off` exactly as `pickup_basis_label` already words
# it, instead of the two surfaces disagreeing about the same row.
COUNT_COMPARABLE_BASES = ("off", "none", "declared")

#: What an unrecorded or unrecognised pickup basis resolves to. The pre-netting reality: before the
#: column existed, a pickup amount was the whole drawer.
BASIS_WHEN_UNRECORDED = "off"

#: Why a basis is not comparable, in the words a person reading the screen needs. One sentence,
#: because "not comparable" on its own invites somebody to go looking for missing cash.
COUNT_INCOMPARABLE_REASON = {
    "pos": ("the expected figure nets bill-payment cash by the POS, but the envelope was netted by "
            "the rep's own declared figure — the difference is the POS-versus-declared gap, not "
            "cash missing from the bag"),
}


def basis_recorded(basis):
    """PURE: did this row actually RECORD a basis (mig 1039), or is it absent/unrecognised?"""
    return str(basis or "").strip().lower() in PICKUP_BASES


def resolved_basis(basis):
    """PURE: the basis to reason with — the recorded one, else `BASIS_WHEN_UNRECORDED`.

    THE ONE HOME for "what does a missing pickup basis mean", dereferenced by
    `router.pickup_basis_label` (which words it for a screen) and by `count_comparable` (which
    decides whether a count may be scored against it). Whether it was RECORDED stays separately
    answerable via `basis_recorded`, so a screen can say "as far as can be told" rather than
    claiming somebody declared it.
    """
    b = str(basis or "").strip().lower()
    return b if b in PICKUP_BASES else BASIS_WHEN_UNRECORDED


def count_comparable(basis):
    """PURE: may a count of the WHOLE envelope be scored against an amount stored on `basis`?

    Decided on the RESOLVED basis, so this and `pickup_basis_label` can never disagree about the
    same row. See the note above for the derivation of each case.
    """
    return resolved_basis(basis) in COUNT_COMPARABLE_BASES


def count_incomparable_reason(basis):
    """PURE: the one-sentence WHY for a basis `count_comparable` rejects, or None when it accepts."""
    b = resolved_basis(basis)
    if count_comparable(b):
        return None
    return COUNT_INCOMPARABLE_REASON.get(b) or (
        f"the expected figure is on the '{b}' basis, which has not been ruled comparable with a "
        "count of the whole envelope")


# ── WHICH PICKUP ENVELOPE IS EVER NETTED (the sibling half of the fix) ──────────────────────────
# `amount_basis` exists on `commcalc.cash_pickup` ONLY (mig 1039). The bill-payment envelope has no
# such column, and adding one would be a migration for a fact that is already settled by
# construction: netting moves bill-pay cash OUT of the cash envelope and INTO the bill-payment one,
# so the bill-payment envelope's amount is never reduced by anything. Its basis is knowable without
# storing it — and a knowable fact read from ONE home beats a column nobody writes.
#
# Without this, the basis gate would read the bill-pay envelope's missing column as "unknown" and
# refuse every one of its counts — a guard breaking the sibling it was supposed to protect, which is
# precisely the failure "find the siblings before you ship" exists to catch.
BASIS_NOT_NETTED = "off"

#: The one pickup table whose amount netting can reduce. A VALUE, not a branch in a caller.
NETTED_PICKUP_TABLE = "cash_pickup"


def pickup_amount_basis_for(table, stored_basis=None):
    """PURE: the basis of a pickup row's `amount`, for the basis gate.

    The CASH envelope carries its own stored basis (mig 1039; None where the row predates it). Any
    other pickup envelope is by construction never netted, so its amount is on the un-netted basis
    whatever column its table lacks. ONE home, so neither the confirm path nor the late open decides
    this for itself.
    """
    t = str(table or "").strip()
    if not t:
        # FAIL CLOSED: a caller that cannot name its own table has not established that its amount
        # was never netted, and "probably un-netted" is how a shortage gets scored against somebody.
        return stored_basis
    if t == NETTED_PICKUP_TABLE:
        return stored_basis
    return BASIS_NOT_NETTED
