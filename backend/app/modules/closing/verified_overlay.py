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
    try:
        q = (client.schema("commcalc").table("daily_closing_verification")
             .select("store_code,close_date,verified,dm_store_cash,dm_store_cc,dm_epay_cash,"
                     "dm_epay_cc,dm_acc_sale,dm_other")
             .eq("org_id", org_id).eq("verified", True))
        # in_() on the (usually small) date set; fall back to a broad read filtered in Python on error.
        rows = (q.in_("close_date", dates).execute().data) or []
    except Exception:
        try:
            rows = [r for r in ((client.schema("commcalc").table("daily_closing_verification")
                                 .select("store_code,close_date,verified,dm_store_cash,dm_store_cc,"
                                         "dm_epay_cash,dm_epay_cc,dm_acc_sale,dm_other")
                                 .eq("org_id", org_id).eq("verified", True).execute().data) or [])
                    if str(r.get("close_date"))[:10] in set(dates)]
        except Exception:
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
               ("dm_store_cash", "dm_store_cc", "dm_epay_cash", "dm_epay_cc", "dm_acc_sale", "dm_other"))


def apply_overlay(agg, dm_row):
    """Mutate a store-day AGGREGATE dict in place, replacing the rep-summed figures with the DM's verified
    corrections. Maps each correction onto BOTH column families and zeroes the folded sibling so no recon
    formula double-counts. A key is only touched if it already exists in `agg` (so a cash-only aggregate
    is unaffected by a credit correction) AND the DM set that `dm_*` field. Returns `agg`.

    Field mapping (dm_* = the DM's corrected TOTAL for that bucket):
      dm_store_cash → store_cash, t_cash          ; zero epay_cash               (cash total)
      dm_store_cc   → store_cc,   t_credit        ; zero epay_cc, t_ext_cc       (credit total)
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
        _set(("store_cc", "t_credit"), _f(dm_row["dm_store_cc"])); _zero(("epay_cc", "t_ext_cc"))
    if dm_row.get("dm_other") is not None:
        _set(("other_account", "t_zelle"), _f(dm_row["dm_other"])); _zero(("t_store_acct", "t_gift"))
    if dm_row.get("dm_acc_sale") is not None:
        _set(("acc_sale",), _f(dm_row["dm_acc_sale"]))
    if dm_row.get("dm_epay_cash") is not None:
        _set(("epay_on_cash",), _f(dm_row["dm_epay_cash"]))
    if dm_row.get("dm_epay_cc") is not None:
        _set(("epay_on_cc",), _f(dm_row["dm_epay_cc"]))
    return agg
