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
