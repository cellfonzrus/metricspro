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

THREE STATES, NEVER TWO. An envelope's basis is one of:
    'pos'   a POS figure existed for that store-day and was netted out
    'none'  no POS figure for that store-day — NOTHING is netted, and the caller says so
    'off'   the tenant has not switched netting on (RULE TWO: per-org config, house default off)
Subtracting a fabricated zero and calling it netted is the silent-zero defect this codebase keeps
paying for; 'none' exists so the DM is told the envelope is un-netted rather than shown a number that
merely looks reconciled.
"""


def _f(v) -> float:
    try:
        return float(v or 0)
    except Exception:
        return 0.0


def _r2(v) -> float:
    return round(v + 0.0, 2)


def net_store_day(rows, pos_billpay_cash=None, enabled=True):
    """Net one STORE-DAY's POS bill-pay cash across that day's envelopes.

    `rows`   [{'key': hashable, 't_cash': float, 'epay_on_cash': float}] — one entry per rep envelope.
    `pos_billpay_cash`  the POS-calculated bill-pay CASH for this store-day, or None when there is
             no POS figure for it (a missing feed, an unmapped store — not a zero).
    `enabled`  the tenant's netting switch.

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
           "unallocated": 0.0, "rows": {}}
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
    if pos_billpay_cash is None:
        return _finish("none")

    pos = max(0.0, _f(pos_billpay_cash))
    out["pos_cash"] = _r2(pos)

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
    if row["billpay_netted"] > 0:
        s = (f"${row['billpay_netted']:,.2f} of POS bill-pay cash is collected on the bill-pay screen "
             f"and has been taken out of this envelope.")
        if res.get("unallocated", 0) > 0.005:
            s += (f" The POS says ${res['unallocated']:,.2f} more bill-pay cash passed through this "
                  "store-day than anyone declared holding.")
        return s
    return None
