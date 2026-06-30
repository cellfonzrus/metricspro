"""DB-backed commission CATEGORY catalog — the self-extending half of the any-carrier mapper (SaaS).

column_mapping.py holds the HARD-CODED Boost/Total defaults. This module layers a per-tenant CATALOG
(commcalc.commission_field_catalog, migration 066) ON TOP of those defaults so a tenant can introduce
categories we never shipped (a 7th-month spiff, a NAB bounty, a port-in incentive, a tablet bonus). When a
new category is created, add_field() also creates the matching PHYSICAL column on commcalc.carrier_commission
via the SECURITY DEFINER RPC commcalc.add_commission_column (migration 067) — so the table grows to fit the
file instead of dropping the data.

ADDITIVE + DEGRADES GRACEFULLY: every read falls back to the hard-coded defaults if migration 066 isn't
applied (catalog table missing → load_catalog returns []). BOOST-SAFE: only carrier_commission is ever
altered; the live Boost calc, rep_commissions and the legacy upload_file branches are untouched.
"""
import re

from app.modules.commcalc import column_mapping

ORG_HOUSE = "00000000-0000-0000-0000-000000000001"
CATALOG_TABLE = "commission_field_catalog"

# catalog data_type -> column_mapping transform key (what apply_transform understands)
_DTYPE_TO_TRANSFORM = {
    "number": "number", "numeric": "number", "amount": "number",
    "text": "text", "string": "text", "int": "int", "integer": "int",
    "date": "date10", "date10": "date10", "mdn": "mdn", "bool": "bool", "boolean": "bool",
}
# kinds the wizard offers (drives grouping + sensible is_amount default)
KINDS = ["identity", "comm_month", "spiff", "rebate", "residual", "margin", "fee", "bounty", "other"]
NON_AMOUNT_KINDS = {"identity"}


def transform_for(data_type):
    return _DTYPE_TO_TRANSFORM.get(str(data_type or "number").lower(), "text")


def sanitize_field(name):
    """A spreadsheet label / category name -> a safe physical column name matching the RPC's
    ^[a-z][a-z0-9_]{0,62}$ guard. Mirrors the SQL sanitiser so the round-trip is predictable."""
    s = re.sub(r"[^a-z0-9_]+", "_", str(name or "").strip().lower()).strip("_")
    if not s:
        s = "field"
    if not s[0].isalpha():
        s = "c_" + s
    return s[:63]


def load_catalog(client, org_id, report_key="carrier_commission"):
    """Catalog rows for (org, report_key), sorted for display. [] if migration 066 isn't applied yet."""
    try:
        rows = (client.schema("commcalc").table(CATALOG_TABLE).select("*")
                .eq("org_id", org_id).eq("report_key", report_key).execute().data) or []
        return sorted(rows, key=lambda r: (r.get("sort_order") or 100, r.get("label") or ""))
    except Exception:
        return []


def amount_fields(client, org_id, report_key="carrier_commission"):
    """Physical columns summed into carrier_commission.total_commission: catalog is_amount rows when the
    catalog exists, else the hard-coded CARRIER_COMMISSION_AMOUNTS tuple (pre-066 fallback)."""
    cat = load_catalog(client, org_id, report_key)
    if cat:
        return [r["target_field"] for r in cat if r.get("is_amount")]
    return list(column_mapping.CARRIER_COMMISSION_AMOUNTS)


def merged_target_fields(client, org_id, report_key):
    """Hard-coded defaults MERGED with catalog rows. Catalog rows add user-created categories and may
    relabel a default. Returns the same shape as column_mapping.target_fields() + catalog metadata
    (kind, is_amount, month_index, source). Used by the wizard + the mapping UI so new categories appear."""
    base = {}
    # pass (client, org_id) so the generic target_field_registry (070, C-Phase2) is merged UNDER the
    # commission catalog too — registry relabels/added fields show in the wizard, catalog rows overlay on top.
    for f in column_mapping.target_fields(report_key, client, org_id):
        amt = f["target_field"] in column_mapping.CARRIER_COMMISSION_AMOUNTS if report_key == "carrier_commission" else False
        base[f["target_field"]] = {**f, "kind": "other", "is_amount": amt, "month_index": None,
                                   "sort_order": 100, "source": "default"}
    for r in load_catalog(client, org_id, report_key):
        tf = r["target_field"]
        prev = base.get(tf, {})
        base[tf] = {
            "target_field": tf,
            "label": r.get("label") or prev.get("label") or tf,
            "transform": transform_for(r.get("data_type")),
            "required": prev.get("required", False),
            "default_source": prev.get("default_source", ""),
            "aliases": prev.get("aliases", []),
            "kind": r.get("kind") or prev.get("kind") or "other",
            "is_amount": bool(r.get("is_amount")),
            "month_index": r.get("month_index"),
            "sort_order": r.get("sort_order") or prev.get("sort_order") or 100,
            "source": "default" if r.get("is_seeded") else "catalog",
        }
    return sorted(base.values(), key=lambda x: (x.get("sort_order") or 100, x.get("label") or ""))


def add_field(client, org_id, report_key, label, kind="other", data_type="number",
              is_amount=None, month_index=None, target_field=None, sort_order=100):
    """Create a NEW commission category: (1) add the physical column on carrier_commission via the RPC,
    (2) upsert the catalog row. Returns the catalog row. Raises a clear, actionable error if migration
    067 (add_commission_column) isn't installed — never a bare 500."""
    tf = sanitize_field(target_field or label)
    data_type = data_type or "number"
    if is_amount is None:
        is_amount = kind not in NON_AMOUNT_KINDS
    table = column_mapping.TABLE_MAP.get(report_key, report_key)

    if table == "carrier_commission":
        try:
            client.schema("commcalc").rpc("add_commission_column",
                {"p_column": tf, "p_type": data_type, "p_table": table}).execute()
        except Exception as e:
            raise RuntimeError(
                f"Could not add column '{tf}' to carrier_commission. Run migration "
                f"067_dynamic_commission_column_fn.sql in Supabase first, then retry. [{e}]")

    row = {"org_id": org_id, "report_key": report_key, "target_field": tf, "label": label or tf,
           "kind": kind or "other", "data_type": data_type, "is_amount": bool(is_amount),
           "month_index": month_index, "sort_order": sort_order, "is_seeded": False}
    client.schema("commcalc").table(CATALOG_TABLE).upsert(
        row, on_conflict="org_id,report_key,target_field").execute()
    return row


def remove_field(client, org_id, report_key, target_field):
    """Remove a USER-CREATED catalog category (the physical column is left in place — non-destructive,
    and seeded defaults are protected). Returns True if a row was deleted."""
    res = (client.schema("commcalc").table(CATALOG_TABLE).delete()
           .eq("org_id", org_id).eq("report_key", report_key).eq("target_field", target_field)
           .eq("is_seeded", False).execute())
    return bool(res.data)
