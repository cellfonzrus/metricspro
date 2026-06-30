"""Generic per-tenant TARGET FIELD REGISTRY — C-Phase2 of the any-carrier mapper (SaaS).

column_mapping.TARGET_FIELDS holds the HARD-CODED canonical fields for the seeded Boost report keys
(sales, comp_report, mi_report, payment_detail, carrier_commission). This module layers a per-tenant
REGISTRY (commcalc.target_field_registry, migration 070) ON TOP of those defaults for ANY report_key —
so a tenant can introduce a report type we never shipped (expenses, a chart-of-accounts feed, a product
catalog), relabel a default field, or add header aliases for better auto-detect — with no code change.
It is the generalisation of commission_catalog's merge (066), minus the commission semantics and the DDL.

ADDITIVE + DEGRADES GRACEFULLY: every read falls back to the hard-coded defaults if migration 070 isn't
applied (table missing → load_registry returns []). BOOST-SAFE: this only changes the FIELD LIST the
mapping UI offers + auto-suggests; it performs NO DDL (it never alters a physical table, unlike the
commission catalog) and never touches the live calc, rep_commissions, or the legacy upload branches.
"""
import re
from datetime import datetime, timezone

ORG_HOUSE = "00000000-0000-0000-0000-000000000001"
REGISTRY_TABLE = "target_field_registry"

# the column_mapping transforms a registry field may use (mirrors column_mapping.TRANSFORMS keys)
VALID_TRANSFORMS = {"text", "number", "int", "date10", "mdn", "upper", "lower", "bool"}


def sanitize_field(name):
    """A label / field name -> a safe canonical field name (mirrors commission_catalog.sanitize_field)."""
    s = re.sub(r"[^a-z0-9_]+", "_", str(name or "").strip().lower()).strip("_")
    if not s:
        s = "field"
    if not s[0].isalpha():
        s = "c_" + s
    return s[:63]


def _norm_aliases(aliases):
    """Accept a list OR a comma/newline-separated string; return a clean list of non-empty strings."""
    if isinstance(aliases, str):
        parts = re.split(r"[,\n]", aliases)
    else:
        parts = list(aliases or [])
    return [str(a).strip() for a in parts if str(a).strip()]


def table_ready(client):
    """True if migration 070 is applied (the registry table is queryable). Drives the UI's run-migration hint."""
    try:
        client.schema("commcalc").table(REGISTRY_TABLE).select("org_id").limit(1).execute()
        return True
    except Exception:
        return False


def load_registry(client, org_id, report_key=None):
    """Registry rows for (org[, report_key]), sorted for display. [] if migration 070 isn't applied."""
    try:
        q = client.schema("commcalc").table(REGISTRY_TABLE).select("*").eq("org_id", org_id)
        if report_key:
            q = q.eq("report_key", report_key)
        rows = q.execute().data or []
        return sorted(rows, key=lambda r: (r.get("sort_order") or 100,
                                           r.get("label") or r.get("target_field") or ""))
    except Exception:
        return []


def registry_tuples(client, org_id, report_key):
    """Registry rows as column_mapping field-tuples: (tf, label, transform, required, default_src, aliases).
    Same shape as a row of column_mapping.TARGET_FIELDS so the merge is a drop-in overlay."""
    out = []
    for r in load_registry(client, org_id, report_key):
        tf = r.get("target_field")
        if not tf:
            continue
        out.append((
            tf,
            r.get("label") or tf,
            r.get("transform") or "text",
            bool(r.get("required")),
            r.get("default_source") or "",
            _norm_aliases(r.get("aliases")),
        ))
    return out


def registry_report_keys(client, org_id):
    """Distinct report_keys with at least one registry row (so tenant-introduced report types show in
    the report-key picker / readiness matrix even though they aren't in the hard-coded TARGET_FIELDS)."""
    try:
        rows = (client.schema("commcalc").table(REGISTRY_TABLE).select("report_key")
                .eq("org_id", org_id).execute().data) or []
        return sorted({r.get("report_key") for r in rows if r.get("report_key")})
    except Exception:
        return []


def add_field(client, org_id, report_key, label, transform="text", required=False,
              default_source="", aliases=None, sort_order=100, target_field=None):
    """Create/update one registry field. NO DDL — this is purely the canonical field list. Returns the row.
    is_seeded stays false (only the SQL seed, if any, would set it true) so it remains user-removable."""
    tf = sanitize_field(target_field or label)
    transform = transform if transform in VALID_TRANSFORMS else "text"
    row = {"org_id": org_id, "report_key": report_key, "target_field": tf,
           "label": label or tf, "transform": transform, "required": bool(required),
           "default_source": default_source or "", "aliases": _norm_aliases(aliases),
           "sort_order": int(sort_order or 100), "is_seeded": False,
           "updated_at": datetime.now(timezone.utc).isoformat()}
    client.schema("commcalc").table(REGISTRY_TABLE).upsert(
        row, on_conflict="org_id,report_key,target_field").execute()
    return row


def remove_field(client, org_id, report_key, target_field):
    """Remove a USER-CREATED registry field (seeded defaults are protected). Returns True if a row was deleted."""
    res = (client.schema("commcalc").table(REGISTRY_TABLE).delete()
           .eq("org_id", org_id).eq("report_key", report_key).eq("target_field", target_field)
           .eq("is_seeded", False).execute())
    return bool(res.data)
