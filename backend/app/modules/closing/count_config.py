"""Tenant-configurable closing-sheet activation-COUNT fields (migration 501).

Doctrine: an EMPTY config falls back to the hardcoded 3 fields (upgrade_count / new_line_count /
postpaid_count) that live as physical columns on commcalc.daily_closing, so a tenant that hasn't
opted in behaves byte-for-byte identically. Mirrors closing/tender_config.py (mig 111), which mirrors
commcalc/target_registry.py (mig 070).

recon_class buckets a field into the B2B count-mismatch recon shown on the DM verify view + the
/closing/recon sheet ('activation' | 'upgrade' | 'other'). That recon is ALWAYS a flag (informational,
never a block) — this module never touches the cash/credit close gate.
"""

# The 3 built-in fields as seedable definitions: (field_key, label, recon_class, sort_order).
# field_key IS the physical daily_closing column name for these three — never remapped.
STANDARD_DEFS = [
    ("upgrade_count", "Upgrades", "upgrade", 10),
    ("new_line_count", "New Lines", "activation", 20),
    ("postpaid_count", "Postpaid", "activation", 30),
]

# The field_keys that map to physical daily_closing columns. Any other field_key a tenant defines is
# CUSTOM and is stored in daily_closing.counts (jsonb) instead.
STD_FIELD_KEYS = {k for (k, _lbl, _rc, _so) in STANDARD_DEFS}

TABLE = "closing_count_field_def"


def load_count_config(client, org_id):
    """Active count-field definitions for a tenant, sorted for display. [] (→ hardcoded fallback) if
    migration 501 isn't applied or the tenant hasn't configured anything."""
    try:
        rows = (client.schema("commcalc").table(TABLE).select("*")
                .eq("org_id", org_id).eq("is_active", True).order("sort_order").execute().data) or []
        return rows
    except Exception:
        return []   # table not migrated yet, or tenant has no rows → hardcoded fallback


def count_axis(defs):
    """(keys, labels, recon_class_by_key) — the tenant's count fields in display order, else the
    hardcoded 3. Same shape/behaviour split as tender_config.tender_axis."""
    if defs:
        keys = [d.get("field_key") for d in defs if d.get("field_key")]
        labels = {d.get("field_key"): (d.get("label") or d.get("field_key")) for d in defs}
        rclass = {d.get("field_key"): (d.get("recon_class") or "other") for d in defs}
        return keys, labels, rclass
    keys = [k for (k, _lbl, _rc, _so) in STANDARD_DEFS]
    labels = {k: lbl for (k, lbl, _rc, _so) in STANDARD_DEFS}
    rclass = {k: rc for (k, _lbl, rc, _so) in STANDARD_DEFS}
    return keys, labels, rclass


def row_value(row, field_key) -> int:
    """One count field's value off a daily_closing row: the physical column for a standard field_key,
    else daily_closing.counts (jsonb, mig 501) for a custom one. Missing/absent → 0."""
    if field_key in STD_FIELD_KEYS:
        v = row.get(field_key)
    else:
        counts = row.get("counts") or {}
        v = counts.get(field_key) if isinstance(counts, dict) else None
    try:
        return int(float(v)) if v not in (None, "") else 0
    except Exception:
        return 0
