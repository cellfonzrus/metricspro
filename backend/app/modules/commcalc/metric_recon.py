"""Two-source metric reconciliation (owner directive 2026-08-26).

The owner's model: two independently-ingested sources for the same metric should PROVE the ingest is good
when they agree, and FLAG when they don't — an automatic back-end reconciliation, not a manual eyeball.

For ACTIVATIONS the two sources are:
  • PRIMARY (basis of truth): the b2b "Activation Details" custom import — distinct Serial#, Upgrade
    excluded from Total Activation (the b2b-consistent definition).
  • SECONDARY: the shared sales aggregation (`_sales_cell_agg` over raw_sales / the daily feed) — what
    Executive MTD / the Sales Report historically counted.

This module is PURE: router.py builds each side into a per-canonical-store bucket map and hands both here.
No DB, no framework — so it is trivially unit-testable and can be reused for other metric pairs later.

A store row is compared on Total Activation EXCLUDING Upgrade (activation+port+byod) so the two bases mean
the same thing. The result names, per store, whether the sources match, and classifies each divergence so
the caller can auto-remediate (re-run the sweep) or assign an upload — never a silent gap.
"""


def _excl_upgrade_total(slot):
    """Total Activation EXCLUDING Upgrade for one store bucket slot (the b2b-consistent definition)."""
    if not slot:
        return 0
    return int(slot.get("activation", 0)) + int(slot.get("port", 0)) + int(slot.get("byod", 0))


def _name_of(primary_slot, secondary_slot, key):
    for s in (primary_slot, secondary_slot):
        if s and s.get("_name"):
            return s["_name"]
    return key


def reconcile_activations(primary_by_store, secondary_by_store, tolerance=0,
                          source="activation_details", reconcile_with="sales_agg",
                          assigned_user=None):
    """Reconcile two per-store activation bucket maps.

    Each map: {canonical_store_key: {'activation','port','byod','upgrade', optional '_name'}}.
    `tolerance` = allowed absolute delta per store before it is flagged (0 = exact match).

    Returns a dict:
      status            'match' | 'mismatch' | 'no_primary' | 'no_secondary'
      source / reconcile_with / tolerance   (echoed config)
      totals            {'primary','secondary','delta'} on the excl-Upgrade basis
      counts            {'stores','matched','mismatched','missing_in_primary','missing_in_secondary'}
      stores            per-store rows that DIFFER beyond tolerance (sorted by |delta| desc), each:
                        {store, primary, secondary, delta, kind}
      remediation       {'action','assigned_user','reason'} — what to do about the mismatch, or None
    """
    primary_by_store = primary_by_store or {}
    secondary_by_store = secondary_by_store or {}
    keys = set(primary_by_store) | set(secondary_by_store)

    p_total = sum(_excl_upgrade_total(s) for s in primary_by_store.values())
    s_total = sum(_excl_upgrade_total(s) for s in secondary_by_store.values())

    rows = []
    matched = mismatched = miss_p = miss_s = 0
    for k in keys:
        ps, ss = primary_by_store.get(k), secondary_by_store.get(k)
        p, s = _excl_upgrade_total(ps), _excl_upgrade_total(ss)
        delta = p - s
        if abs(delta) <= tolerance:
            matched += 1
            continue
        if p == 0 and s > 0:
            kind = "missing_in_primary"      # secondary counts activations the primary (AD) has none of
            miss_p += 1
        elif s == 0 and p > 0:
            kind = "missing_in_secondary"    # AD counts activations the sales feed never captured
            miss_s += 1
        else:
            kind = "mismatch"                # both non-zero but disagree
            mismatched += 1
        rows.append({"store": _name_of(ps, ss, k), "primary": p, "secondary": s,
                     "delta": delta, "kind": kind})
    rows.sort(key=lambda r: -abs(r["delta"]))

    if not primary_by_store:
        status = "no_primary"
    elif not secondary_by_store:
        status = "no_secondary"
    elif not rows:
        status = "match"
    else:
        status = "mismatch"

    remediation = None
    if status in ("mismatch", "no_primary"):
        # A missing/short PRIMARY (the AD basis) is the auto-remediable case: re-run the email/FTP sweep to
        # re-ingest the Activation Details report; if that cannot close it, assign the named user to upload.
        primary_short = (status == "no_primary") or any(
            r["kind"] == "missing_in_primary" for r in rows)
        if primary_short:
            remediation = {"action": ("assign_upload" if assigned_user else "rerun_sweep"),
                           "assigned_user": assigned_user,
                           "reason": ("Activation Details is missing or short vs the sales feed — re-run the "
                                      "sweep to re-ingest it" + (", or assign the upload." if assigned_user
                                                                 else "."))}
        else:
            remediation = {"action": "review",
                           "assigned_user": assigned_user,
                           "reason": "Both sources have data but disagree per store — review the flagged "
                                     "stores; the primary (Activation Details) is the basis of truth."}

    return {
        "status": status,
        "source": source, "reconcile_with": reconcile_with, "tolerance": tolerance,
        "totals": {"primary": p_total, "secondary": s_total, "delta": p_total - s_total},
        "counts": {"stores": len(keys), "matched": matched, "mismatched": mismatched,
                   "missing_in_primary": miss_p, "missing_in_secondary": miss_s},
        "stores": rows,
        "remediation": remediation,
    }
