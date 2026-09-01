"""Tiled-dashboard LAYOUT designer — per-module tile config (dashboard-builder Phase D1).

Every module gets a tiled dashboard; the TILE LAYOUT (which tiles, their titles/icons/descriptions
and the links inside each tile) is USER-DESIGNED per module, not hardcoded (owner spec 2026-09-01):

  · SUPER ADMIN designs for ALL modules and for ANY tenant. A layout saved on the HOUSE org
    (00000000-0000-0000-0000-000000000001) is the PLATFORM DEFAULT every tenant inherits.
  · TENANT ADMINS may override the layout for their OWN tenant only (permission-gated on the
    registered 'menu_layout' settings area — core.SETTING_AREAS).
  · Resolution: tenant row wins over the house row; no row anywhere -> null (the page renders its
    built-in default). Clearing a tenant row REVERTS to inheritance, never to "blank".

STORAGE — RULE TWO (config, never code): one JSON row per (org, module) in the EXISTING
commcalc.ui_label_override table (mig 068) under scope='tiles', key=<module key>, the JSON
serialized into the `label` TEXT column — the exact multiplexing precedent scope='layout' set for
the sidebar designer (router.py `set_nav_layout`). NO new migration, NO lineage seed (display
config, not a data feed).

Everything above the two thin loaders is PURE (stdlib only) and proven DB-free in
backend/harness_tile_layout.py: the sanitizer, the tenant>house resolver (malformed JSON degrades a
layer, never raises) and the WRITE-GATE decision table (`tile_write_gate`/`tile_write_org` — the
body/request never decides the org; the resolved caller + explicit target do).
"""
import json
import re

HOUSE_ORG = "00000000-0000-0000-0000-000000000001"

# Hard caps — a display config, so generous, but bounded (one TEXT cell per module).
MAX_TILES = 40
MAX_ITEMS_PER_TILE = 60
MAX_ITEMS_TOTAL = 400
_MODULE_KEY_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:/-]{0,59}$")


# ── PURE: input normalization ─────────────────────────────────────────────────────────────────────
def normalize_module_key(module):
    """Canonical module key ('payroll', 'commcalc', 'hr', …). Raises ValueError on garbage."""
    m = str(module or "").strip()
    if not _MODULE_KEY_RE.match(m):
        raise ValueError("module key required (1-60 chars: letters, digits, _ . : / -)")
    return m


def _clean_str(v, cap):
    """Trimmed string clamped to `cap`; '' for None/non-scalar (never raises)."""
    if v is None or isinstance(v, (dict, list, tuple, set)):
        return ""
    return str(v).strip()[:cap]


def _clean_item(raw):
    """One tile link {href, icon?, label?, desc?} — or None when malformed (dropped, per spec).
    href is the safety-relevant field: internal app path ONLY ('/…'), so a stored 'javascript:' or
    absolute URL can never be persisted and auto-followed (same posture as training.safe_href)."""
    if not isinstance(raw, dict):
        return None
    href = _clean_str(raw.get("href"), 300)
    if not href.startswith("/") or href.startswith("//"):
        return None
    out = {"href": href}
    icon = _clean_str(raw.get("icon"), 8)
    if icon:
        out["icon"] = icon
    label = _clean_str(raw.get("label"), 80)
    if label:
        out["label"] = label
    desc = _clean_str(raw.get("desc"), 200)
    if desc:
        out["desc"] = desc
    return out


def sanitize_tile_layout(payload):
    """PURE. Normalize one inbound tile layout to the canonical storable shape
    {version:1, tiles:[{title, icon?, desc?, items:[{href, icon?, label?, desc?}]}]} — or raise
    ValueError on structural garbage. Strings are trimmed and clamped; malformed ITEMS are dropped
    (a bad link never sinks the whole design); a malformed TILE (not a dict, or no title) raises —
    that is a broken payload, not a droppable detail. Caps: 40 tiles, 60 items/tile, 400 items
    total; over-cap raises (silent truncation would eat a user's design)."""
    if not isinstance(payload, dict):
        raise ValueError("layout must be an object {tiles:[...]}")
    tiles_in = payload.get("tiles")
    if not isinstance(tiles_in, list):
        raise ValueError("layout.tiles must be a list")
    if len(tiles_in) > MAX_TILES:
        raise ValueError(f"too many tiles (max {MAX_TILES})")
    tiles = []
    total_items = 0
    for i, t in enumerate(tiles_in):
        if not isinstance(t, dict):
            raise ValueError(f"tile #{i + 1} must be an object")
        title = _clean_str(t.get("title"), 80)
        if not title:
            raise ValueError(f"tile #{i + 1} needs a title")
        items_in = t.get("items")
        if items_in is None:
            items_in = []
        if not isinstance(items_in, list):
            raise ValueError(f"tile '{title}': items must be a list")
        if len(items_in) > MAX_ITEMS_PER_TILE:
            raise ValueError(f"tile '{title}': too many items (max {MAX_ITEMS_PER_TILE})")
        items = [it for it in (_clean_item(r) for r in items_in) if it is not None]
        total_items += len(items)
        if total_items > MAX_ITEMS_TOTAL:
            raise ValueError(f"too many items across tiles (max {MAX_ITEMS_TOTAL})")
        tile = {"title": title, "items": items}
        icon = _clean_str(t.get("icon"), 8)
        if icon:
            tile["icon"] = icon
        desc = _clean_str(t.get("desc"), 200)
        if desc:
            tile["desc"] = desc
        tiles.append(tile)
    return {"version": 1, "tiles": tiles}


# ── PURE: tenant > house resolution ───────────────────────────────────────────────────────────────
def _parse_row_json(raw):
    """One stored `label` cell -> layout dict, or None when absent/malformed. NEVER raises — a
    corrupt row must degrade to the next layer (house / built-in), not 500 every dashboard."""
    if not raw:
        return None
    try:
        v = json.loads(raw) if isinstance(raw, str) else raw
    except Exception:
        return None
    if not isinstance(v, dict) or not isinstance(v.get("tiles"), list):
        return None
    return v


def resolve_tile_layout(tenant_row_json, house_row_json):
    """PURE. (layout|None, 'tenant'|'house'|None): the tenant's saved layout wins; a missing OR
    malformed tenant row degrades to the house (platform default) row; nothing anywhere -> (None,
    None) and the page renders its built-in default. Mirrors training.resolve_tours precedence."""
    t = _parse_row_json(tenant_row_json)
    if t is not None:
        return t, "tenant"
    h = _parse_row_json(house_row_json)
    if h is not None:
        return h, "house"
    return None, None


# ── PURE: write-gate decision table (owner spec) ──────────────────────────────────────────────────
# caller = {org_id, super_admin, can_edit} resolved SERVER-SIDE (core._resolve_caller +
# _can_edit_setting(caller,'menu_layout'); super_admin includes the house-admin bootstrap rule of
# core._require_super_admin). The REQUEST BODY never decides the org — only the resolved caller,
# the explicit target and the org_id QUERY param (training._write_org pattern). Fail-closed: any
# unknown state forbids.
def tile_write_gate(caller, target, requested_org, house_org=HOUSE_ORG):
    """PURE. 'allow' | 'forbid_*' for one write attempt.
      target='house'   -> platform default (HOUSE row): super admin only.
      target='tenant'  -> requested_org != caller org: super admin only (design for ANY tenant);
                          own org: super admin OR the 'menu_layout' setting grant.
    """
    if not caller or not caller.get("org_id"):
        return "forbid_unauthenticated"
    is_super = bool(caller.get("super_admin"))
    target = str(target or "").strip().lower()
    if target == "house":
        return "allow" if is_super else "forbid_house_requires_super_admin"
    if target != "tenant":
        return "forbid_bad_target"
    req = str(requested_org or "").strip() or caller.get("org_id")
    if req != caller.get("org_id"):
        return "allow" if is_super else "forbid_foreign_org"
    return "allow" if (is_super or caller.get("can_edit")) else "forbid_no_setting_grant"


def tile_write_org(caller, target, requested_org, house_org=HOUSE_ORG):
    """PURE. The org a permitted write LANDS in. target='house' pins the HOUSE row regardless of
    any org param; otherwise a super admin's explicit org_id is honored (design for any tenant) and
    everyone else is pinned to their OWN membership org no matter what the request said."""
    if str(target or "").strip().lower() == "house":
        return house_org
    if caller and caller.get("super_admin"):
        return str(requested_org or "").strip() or caller.get("org_id") or house_org
    return (caller or {}).get("org_id") or house_org


# ── Thin loaders (org-scoped; the ONLY DB touch in this module) ───────────────────────────────────
def load_tile_layout(client, org_id, module):
    """Resolved (layout, resolved_from) for one (org, module) — BOTH the tenant row and the house
    (platform-default) row in ONE org-scoped query, then pure resolution."""
    module = normalize_module_key(module)
    org_id = str(org_id or "").strip() or HOUSE_ORG
    rows = (client.schema("commcalc").table("ui_label_override").select("org_id,label")
            .in_("org_id", list({org_id, HOUSE_ORG})).eq("scope", "tiles").eq("key", module)
            .execute().data) or []
    tenant_raw = next((r.get("label") for r in rows
                       if r.get("org_id") == org_id and org_id != HOUSE_ORG), None)
    house_raw = next((r.get("label") for r in rows if r.get("org_id") == HOUSE_ORG), None)
    return resolve_tile_layout(tenant_raw, house_raw)


def save_tile_layout(client, target_org, module, layout_or_none, now_iso=None):
    """Upsert (or delete) the ONE (target_org, 'tiles', module) row. None / empty-tiles layout =
    DELETE = revert to inheritance (house default, then built-in). `layout_or_none` must already be
    sanitize_tile_layout() output — callers gate + sanitize first."""
    module = normalize_module_key(module)
    tbl = client.schema("commcalc").table("ui_label_override")
    if not layout_or_none or not layout_or_none.get("tiles"):
        tbl.delete().eq("org_id", target_org).eq("scope", "tiles").eq("key", module).execute()
        return {"cleared": True}
    row = {"org_id": target_org, "scope": "tiles", "key": module,
           "label": json.dumps(layout_or_none)}
    if now_iso:
        row["updated_at"] = now_iso
    tbl.upsert(row, on_conflict="org_id,scope,key").execute()
    return {"cleared": False}
