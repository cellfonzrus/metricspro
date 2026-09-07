"""DM verified-correction overlay (owner directive 2026-08-20, helpdesk TKT-1030). MONEY-CRITICAL.

When a District Manager corrects a store-day's money figures in DM Verify, the six corrections land in
`commcalc.daily_closing_verification` (`dm_store_cash`, `dm_store_cc`, `dm_epay_cash`, `dm_epay_cc`,
`dm_acc_sale`, `dm_other`) but — until this module — NOTHING downstream read them, so the rep's original
figures stayed authoritative everywhere (the exact ticket: "management cannot see the updated corrected
entries; the system overrides his entries").

This module makes a DM's correction authoritative for the STORE-DAY, applied ONLY once the store-day is
`verified = true`. Every consumer that sums a store-day's money re-sums the rep rows independently, and
the money lives in TWO column families that must stay consistent:

  • legacy day-1 columns   : store_cash, store_cc, epay_cash, epay_cc, acc_sale, other_account
  • canonical tender columns: t_cash, t_credit, t_ext_cc, t_gift, t_store_acct, t_zelle, t_acima
                              (+ informational epay_on_cash / epay_on_cc / epay_on_acima)

For a modern (mig103+) row `create_row` writes the TOTAL into t_cash/store_cash and HARD-ZEROES
epay_cash (folding ePay-on-cash into the cash total), so `money_recon`'s
`closing_cash = epay_cash + store_cash` never double-counts. The overlay preserves that invariant: a
`dm_*` correction is the DM's corrected TOTAL, so it replaces BOTH families' figure AND zeroes the folded
sibling (epay_cash / epay_cc / t_ext_cc / t_store_acct / t_gift). Only fields the DM actually set
(`dm_* is not None`) are overridden; every other field keeps its rep-summed value.

Grain: this is a STORE-DAY total. It is applied to store-day AGGREGATES only (dashboards, recon, deposit
expectation, cash position, the MI cash-deposit gate). Per-rep detail views keep each rep's raw submitted
figures — a store-day correction cannot be attributed to one rep — and surface a "DM-corrected (store)"
badge instead (owner ruling 2026-08-20).
"""


def _f(v):
    try:
        return round(float(v or 0), 2)
    except (TypeError, ValueError):
        return 0.0


def _norm(code):
    return str(code or "").strip().upper()


def build_overlay_map(client, org_id, close_dates):
    """{(NORM store_code, 'YYYY-MM-DD'): dm_row} for every VERIFIED store-day in `close_dates`.

    Only `verified = true` rows are returned — an in-progress correction never moves reported money.
    Best-effort: any read failure yields an empty map (the caller then shows raw rep figures, i.e. exactly
    today's behavior), never a raise."""
    dates = [str(d)[:10] for d in (close_dates or []) if d]
    if not dates:
        return {}
    # Two select lists: mig-961 (with dm_ext_cc) and the legacy mig-935 six. A pre-961 database
    # answers the first with "column does not exist", so every attempt retries on the legacy list —
    # an un-migrated org keeps its overlay instead of silently losing every DM correction.
    _SEL_961 = ("store_code,close_date,verified,dm_store_cash,dm_store_cc,dm_epay_cash,"
                "dm_epay_cc,dm_acc_sale,dm_other,dm_ext_cc")
    _SEL_935 = ("store_code,close_date,verified,dm_store_cash,dm_store_cc,dm_epay_cash,"
                "dm_epay_cc,dm_acc_sale,dm_other")

    def _read(sel, narrow):
        q = (client.schema("commcalc").table("daily_closing_verification").select(sel)
             .eq("org_id", org_id).eq("verified", True))
        if narrow:
            return (q.in_("close_date", dates).execute().data) or []
        return [r for r in ((q.execute().data) or []) if str(r.get("close_date"))[:10] in set(dates)]

    rows = None
    # narrow in_() read first (usually a small date set), then the broad read filtered in Python;
    # each shape tried on the mig-961 select list, then the legacy one.
    for narrow in (True, False):
        for sel in (_SEL_961, _SEL_935):
            try:
                rows = _read(sel, narrow)
                break
            except Exception:
                continue
        if rows is not None:
            break
    if rows is None:
        return {}
    out = {}
    for r in rows:
        if r.get("verified"):
            out[(_norm(r.get("store_code")), str(r.get("close_date"))[:10])] = r
    return out


def has_correction(dm_row):
    """True when the DM actually corrected at least one figure on this verified store-day (so a caller
    can render the 'DM-corrected (store)' badge / audit note only when a real override applies)."""
    if not dm_row:
        return False
    return any(dm_row.get(k) is not None for k in
               ("dm_store_cash", "dm_store_cc", "dm_epay_cash", "dm_epay_cc", "dm_acc_sale",
                "dm_other", "dm_ext_cc"))


def apply_overlay(agg, dm_row):
    """Mutate a store-day AGGREGATE dict in place, replacing the rep-summed figures with the DM's verified
    corrections. Maps each correction onto BOTH column families and zeroes the folded sibling so no recon
    formula double-counts. A key is only touched if it already exists in `agg` (so a cash-only aggregate
    is unaffected by a credit correction) AND the DM set that `dm_*` field. Returns `agg`.

    Field mapping (dm_* = the DM's corrected TOTAL for that bucket):
      dm_store_cash → store_cash, t_cash          ; zero epay_cash               (cash total)
      dm_store_cc   → store_cc,   t_credit        ; zero epay_cc, t_ext_cc       (credit total)
                      …unless dm_ext_cc is also set (mig 961), in which case the SAME corrected
                      total is SPLIT: t_credit = dm_store_cc − dm_ext_cc, t_ext_cc = dm_ext_cc
                      (store_cc keeps the full corrected total; epay_cc still zeroed). The card
                      total never moves — only the split becomes known.
      dm_other      → other_account, t_zelle      ; zero t_store_acct, t_gift    (Zelle/CashApp/other)
      dm_acc_sale   → acc_sale                                                    (accessory gross)
      dm_epay_cash  → epay_on_cash                 (informational ePay-on-cash split; not the cash total)
      dm_epay_cc    → epay_on_cc                    (informational ePay-on-credit split)
    """
    if not dm_row:
        return agg

    def _set(keys, val):
        for k in keys:
            if k in agg:
                agg[k] = val

    def _zero(keys):
        for k in keys:
            if k in agg:
                agg[k] = 0.0

    if dm_row.get("dm_store_cash") is not None:
        _set(("store_cash", "t_cash"), _f(dm_row["dm_store_cash"])); _zero(("epay_cash",))
    if dm_row.get("dm_store_cc") is not None:
        # EXTERNAL-CREDIT SPLIT (mig 961). `dm_store_cc` is the DM's corrected COMBINED card total.
        # Historically the folded siblings were zeroed, which destroyed the external-credit-machine
        # split on every corrected day. When the DM also states `dm_ext_cc` (how much of that total
        # ran through the external machine) the split becomes KNOWN, and the TOTAL is preserved to
        # the cent in both branches — t_credit + t_ext_cc == dm_store_cc either way — so no
        # consumer of the card total moves. `dm_ext_cc` NULL ⇒ byte-identical to pre-961.
        _cc_total = _f(dm_row["dm_store_cc"])
        _ext = _f(dm_row["dm_ext_cc"]) if dm_row.get("dm_ext_cc") is not None else None
        if _ext is None:
            _set(("store_cc", "t_credit"), _cc_total); _zero(("epay_cc", "t_ext_cc"))
        else:
            _set(("store_cc",), _cc_total)
            _set(("t_credit",), round(_cc_total - _ext, 2))
            _set(("t_ext_cc",), _ext)
            _zero(("epay_cc",))
    if dm_row.get("dm_other") is not None:
        _set(("other_account", "t_zelle"), _f(dm_row["dm_other"])); _zero(("t_store_acct", "t_gift"))
    if dm_row.get("dm_acc_sale") is not None:
        _set(("acc_sale",), _f(dm_row["dm_acc_sale"]))
    if dm_row.get("dm_epay_cash") is not None:
        _set(("epay_on_cash",), _f(dm_row["dm_epay_cash"]))
    if dm_row.get("dm_epay_cc") is not None:
        _set(("epay_on_cc",), _f(dm_row["dm_epay_cc"]))
    return agg


def overlay_cash_reader(agg, dm_row):
    """Overlay for the DEPOSIT / CASH-POSITION reader shape, where the aggregate is
    `{"t_cash": <total cash>, "epay_cash": <ePay-ON-cash subset>, ...}`.

    ⚠️ Here `epay_cash` is the ePay bill-payment PORTION of the cash (a subset, used downstream as
    `bill_payment_cash`, with the physical store-cash basis derived as `t_cash − epay_cash`). It is NOT
    a folded sibling ADDED to the total — so, unlike `apply_overlay`, it must NEVER be zeroed when the
    cash total is corrected (that would silently move the deposit-cash basis). `dm_store_cash` corrects
    the TOTAL cash (`t_cash`); `dm_epay_cash` corrects the ePay-on-cash portion (`epay_cash`)."""
    if not dm_row:
        return agg
    if dm_row.get("dm_store_cash") is not None and "t_cash" in agg:
        agg["t_cash"] = _f(dm_row["dm_store_cash"])
    if dm_row.get("dm_epay_cash") is not None and "epay_cash" in agg:
        agg["epay_cash"] = _f(dm_row["dm_epay_cash"])
    return agg


def overlay_tender_legs(agg, dm_row):
    """Overlay for the 3-way TENDER-recon shape, where the aggregate is keyed by tender KEY
    ('cash', 'credit', 'ext_cc', 'gift', 'store_acct', 'zelle', 'acima') summed from the t_* columns.
    Maps the DM's corrected TOTALS onto the matching legs and zeroes the folded siblings so a leg total
    is never double-counted (credit folds ext_cc; zelle folds store_acct + gift). Accessory and ePay-split
    corrections have no tender leg in this recon and are ignored."""
    if not dm_row:
        return agg

    def _set(k, v):
        if k in agg:
            agg[k] = v

    def _zero(*ks):
        for k in ks:
            if k in agg:
                agg[k] = 0.0

    if dm_row.get("dm_store_cash") is not None:
        _set("cash", _f(dm_row["dm_store_cash"]))
    if dm_row.get("dm_store_cc") is not None:
        _set("credit", _f(dm_row["dm_store_cc"])); _zero("ext_cc")
    if dm_row.get("dm_other") is not None:
        _set("zelle", _f(dm_row["dm_other"])); _zero("store_acct", "gift")
    return agg
