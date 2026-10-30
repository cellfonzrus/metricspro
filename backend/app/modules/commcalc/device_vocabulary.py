"""WHICH DEPARTMENTS THIS ORG CALLS A DEVICE — the ONE place a caller asks (index §COMMCALC-49).

WHY THIS FILE EXISTS (owner-reported defect 2026-10-30). `demand_projection.is_device_line` used to
default its department vocabulary to one tenant's POS labels. Three features asked it and none passed
a vocabulary, so for a tenant filing handsets under a different department name the Accessory Order
Plan, the device order forecast and the PO recommendations each reported no device sales at all —
1,034 handsets in one month, read as zero, silently. Owner: *"it should be universal add on of a
feture ... all features should be for all tenanats"*.

**THE DECLARATION ALREADY EXISTED.** `commcalc.accessory_config.box_departments` (mig 218) is the
org's own admin-editable list of which POS departments are a "box" — a device unit. The box count,
the product mix, the GP report (`gp_report.device_department_test`) and the multi-handset flag
(`multi_handset_day.handset_rows`) all read it. The duplicate check missed it because it searched for
copies of the velocity ARITHMETIC and of `_is_device_line`, not for the question those answer. So this
module adds NO new fact and NO new table: it is the one DEREFERENCE of a declaration already in place.

ONE READ, THROUGH THE EXISTING READER. The config read is `router._accessory_config`, which is cached
per org and already applies the house fallback (`router._BOX_DEPTS`) for an org that has declared
nothing. Asking it here rather than re-reading `accessory_config` is the difference between a
dereference and a second copy — and the house fallback keeps its single home in the router, so this
module holds no department literal of its own (`harness_demand_projection.py` §B1 fails the build if
one appears).

THE IMPORT IS LAZY, and that is the point of the file. `demand_projection` is PURE — it imports
nothing, which is what lets `/payables/forecast` and `/asset/po/recommendations` dereference it from
any load order (the import-cycle worry that once justified a second copy of the arithmetic). Reading
config is not pure, so it lives HERE instead, with the `commcalc.router` import inside the function:
`asset.purchase_orders` and `payables.router` can call it without either of them importing the
commcalc router at module scope.
"""


def declared_device_departments(client, org_id):
    """The org's OWN device/box departments as a tuple, or `()` when nothing can be read.

    `()` is an honest answer and not an error: the caller passes it to `is_device_line`, which then
    identifies nothing, and the surface reports that it found no device sales. That is a condition a
    human fixes in the admin screen. It is NOT backfilled with another tenant's vocabulary, which is
    the defect this module exists to close."""
    try:
        from app.modules.commcalc.router import _accessory_config
        cfg = _accessory_config(client, org_id) or {}
    except Exception:
        return ()
    return tuple(sorted({str(d).strip() for d in (cfg.get("box_departments") or ()) if str(d).strip()}))
