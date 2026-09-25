"""THE TENANT VERTICAL — what kind of business a tenant is, and what that hides (mig 1020, index §35).

ONE fact, ONE home: `storeops.tenants.vertical` (NULL = the default vertical) over the vocabulary
`core.tenant_vertical`. Every gate reads it through `tenant_vertical()` / `vertical_context()`:
  · entitlements.module_enabled / effective_modules — a module whose `applies_to_vertical` excludes the
    tenant's vertical is not enabled (backend 403 via require_module, and off in tenant_modules);
  · GET /core/me → `tenant.vertical` — the frontend reads `hidden_modules`, `nav_hidden` and
    `uses_carriers` from it (lib/rbac.ts verticalOK) and spells no vertical itself.

No vertical key is branched on in code (RULE TWO): the keys appear only in the seed and in the byte-equal
mirror below, which `harness_tenant_vertical.py` §A parses back against the migration.
"""
from __future__ import annotations

import re

# ── MIRROR of mig 1020's seed (byte-equal; parsed back by the harness) ─────────────────────────────────
HOUSE_VERTICALS = [
    {"key": "wireless_retail", "label": "Wireless retail", "is_default": True, "uses_carriers": True,
     "nav_hidden": [], "sort_order": 10, "closing_hidden": []},
    {"key": "ups_store", "label": "The UPS Store (franchise)", "is_default": False, "uses_carriers": False,
     "nav_hidden": [
         "/pos/activations", "/pos/activation-report", "/hub/management-overview", "/commcalc/sales-report",
         "/commcalc/exec", "/commcalc/sales-comparison", "/commcalc/kpi-failing", "/commcalc/zero-sales",
         "/commcalc/flags", "/commcalc/accessory-flags", "/commcalc/chargebacks",
         "/commcalc/commission-discrepancy", "/commcalc/discrepancy", "/commcalc/recovery",
         "/commcalc/ingest-guard", "/hub/incentives", "/commcalc/pay-simulator", "/commcalc$",
         "/commcalc/custom-report", "/commcalc/activations", "/commcalc/schematic", "/commcalc/reports-index",
         "/commcalc/reports", "/commcalc/kpi", "/commcalc/device-history", "/commcalc/ma-handsets",
         "/commcalc/device-cost-recon", "/commcalc/inventory-sold-recon", "/commcalc/bill-payments",
         "/commcalc/productivity", "/commcalc/productivity-insights", "/commcalc/coaching",
         "/commcalc/sales-analyzer", "/commcalc/whatif", "/commcalc/comp-trend", "/commcalc/commission-ledger",
         "/commcalc/ma-commission", "/commcalc/ma-overview-recon", "/commcalc/commission-legs",
         "/commcalc/accessory-cost-audit", "/commcalc/expected-commission", "/commcalc/daily-commission",
         "/commcalc/imei-rebates", "/commcalc/carrier-vs-pay", "/commcalc/vendor-rebates",
         "/commcalc/sales-recon", "/commcalc/epay-fee-recon", "/commcalc/imei-recon", "/commcalc/carrier-recon",
         "/commcalc/agency", "/hub/incentive-payout-plans", "/commcalc/commission-structure",
         "/commcalc/payout-plans", "/commcalc/commission-plans", "/commcalc/management-incentive",
         "/commcalc/plan-installments", "/commcalc/payout-schedules", "/commcalc/settings",
         "/commcalc/carrier-mapping", "/commcalc/commission-category-map", "/commcalc/ma-product-class",
         "/commcalc/accessory-definition", "/commcalc/commission-import", "/hub/targets-coaching",
         "/commcalc/targets", "/commcalc/financing", "/commcalc/atu-opportunity", "/employee", "/commcalc/gp",
         "/accounts/device-purchases", "/accounts/device-payable", "/accounts/residual-per-sub", "/hub/assets",
         "/commcalc/asset$", "/commcalc/asset/marketplace-purchases", "/commcalc/asset/dashboard",
         "/commcalc/asset/owed-weekly", "/commcalc/asset/aging", "/commcalc/asset/missing-phones",
         "/commcalc/asset/aging-rebate", "/commcalc/asset/on-inventory", "/commcalc/processor-ledger",
         "/commcalc/asset/borrowed", "/commcalc/asset/lending", "/commcalc/asset/charges",
         "/commcalc/asset/inventory-recon", "/commcalc/asset/hotsheet-recon", "/hub/distributors",
         "/commcalc/distributors", "/commcalc/vip", "/closing/accessory-recon", "/closing/billpay-pickup",
         "/closing/epay-recon", "/onboarding/intake", "/commcalc/carrier-comm-file", "/commcalc/epay",
         "/commcalc/dlar", "/commcalc/gp-category-map",
     ], "sort_order": 20,
     # mig 1024 — closing-form inputs this vertical does not use (the form's own section keys).
     "closing_hidden": ["acc_sale", "bill_payments", "activation_counts", "tender:acima"]},
]

# Module → verticals it belongs to (mirror of the mig-1020 module_catalog rows; '{}'/absent = any).
HOUSE_MODULE_VERTICALS = {
    "franchise_ops": ["ups_store"],
    "supply_ordering": ["ups_store"],
    "royalty": ["ups_store"],
    "vip": ["wireless_retail"],
}


# ── PURE ──────────────────────────────────────────────────────────────────────────────────────────────
def normalise_vertical(row):
    return {
        "key": str(row.get("key") or "").strip(),
        "label": str(row.get("label") or row.get("key") or "").strip(),
        "is_default": bool(row.get("is_default")),
        "uses_carriers": row.get("uses_carriers") is not False,
        "nav_hidden": [str(h).strip() for h in (row.get("nav_hidden") or []) if str(h).strip()],
        "sort_order": int(row.get("sort_order") or 100),
        "closing_hidden": [str(h).strip() for h in (row.get("closing_hidden") or []) if str(h).strip()],
    }


def default_vertical(vocab):
    """The vocabulary row an unset tenant reads as — the one flagged is_default, else the first by order."""
    rows = sorted((normalise_vertical(r) for r in vocab or []), key=lambda r: (r["sort_order"], r["key"]))
    return next((r for r in rows if r["is_default"]), rows[0] if rows else None)


def resolve_vertical(declared, vocab):
    """The vocabulary row for a tenant's declared vertical. An unset OR unknown value reads as the default
    (an unknown key is reported in `unknown`, never silently turned into another vertical's gates)."""
    rows = [normalise_vertical(r) for r in vocab or []]
    key = str(declared or "").strip()
    hit = next((r for r in rows if r["key"] == key), None) if key else None
    base = hit or default_vertical(rows) or normalise_vertical({"key": "", "label": ""})
    out = dict(base)
    out["declared"] = key or None
    out["source"] = "tenant" if hit else ("unknown_value" if key else "default")
    return out


def module_applies(applies_to_vertical, vertical_key):
    """'{}' / None = any vertical. Else the tenant's vertical must be listed."""
    scope = [v for v in (applies_to_vertical or []) if v]
    return not scope or vertical_key in scope


def hidden_modules(module_scopes, vertical_key):
    """Module keys that do NOT apply to this vertical, from {module_key: applies_to_vertical}."""
    return sorted(k for k, scope in (module_scopes or {}).items() if not module_applies(scope, vertical_key))


def href_hidden(href, nav_hidden):
    """Does a nav_hidden list hide this page? 'x$' hides exactly x; 'x' hides x and everything under x/."""
    h = (href or "").split("?")[0].rstrip("/") or "/"
    for p in nav_hidden or []:
        if p.endswith("$"):
            if h == (p[:-1].rstrip("/") or "/"):
                return True
        elif h == p.rstrip("/") or h.startswith(p.rstrip("/") + "/"):
            return True
    return False


def payload(vertical, module_scopes, vocab):
    """What GET /core/me hands the frontend under tenant.vertical — everything the nav gate needs."""
    return {
        "key": vertical.get("key"),
        "label": vertical.get("label"),
        "source": vertical.get("source"),
        "uses_carriers": vertical.get("uses_carriers", True),
        "nav_hidden": list(vertical.get("nav_hidden") or []),
        "closing_hidden": list(vertical.get("closing_hidden") or []),
        "hidden_modules": hidden_modules(module_scopes, vertical.get("key")),
        "choices": [{"key": r["key"], "label": r["label"]} for r in
                    sorted((normalise_vertical(x) for x in vocab or []), key=lambda r: (r["sort_order"], r["key"]))],
    }


_KEY_RE = re.compile(r"^[a-z][a-z0-9_]{1,40}$")


def valid_choice(value, vocab):
    v = str(value or "").strip()
    return bool(_KEY_RE.match(v)) and any(normalise_vertical(r)["key"] == v for r in vocab or [])


# ── I/O (every read org-scoped; every failure degrades to the mirror / the default, never to "hide all") ─
_VOCAB_COLS = "key,label,is_default,uses_carriers,nav_hidden,sort_order,is_active"


def load_vocab(client):
    """(rows, registry_ready). Tries the full column set; a database with 1020 but not yet 1024 (no
    closing_hidden column) still reads the registry, with closing_hidden taken from the mirror by key."""
    mirror = {r["key"]: r for r in HOUSE_VERTICALS}
    for cols, add_closing in ((_VOCAB_COLS + ",closing_hidden", False), (_VOCAB_COLS, True)):
        try:
            rows = (client.schema("core").table("tenant_vertical").select(cols).execute().data) or []
        except Exception:
            continue
        rows = [r for r in rows if r.get("is_active", True) is not False]
        if rows:
            if add_closing:
                rows = [dict(r, closing_hidden=list(mirror.get(r.get("key"), {}).get("closing_hidden") or []))
                        for r in rows]
            return rows, True
    return [dict(r) for r in HOUSE_VERTICALS], False


def load_module_scopes(client):
    try:
        rows = (client.schema("core").table("module_catalog").select("key,applies_to_vertical")
                .execute().data) or []
        if rows:
            out = {r["key"]: list(r.get("applies_to_vertical") or []) for r in rows if r.get("key")}
            for k, v in HOUSE_MODULE_VERTICALS.items():       # a module the DB does not know yet → mirror
                out.setdefault(k, list(v))
            return out
    except Exception:
        pass
    return {k: list(v) for k, v in HOUSE_MODULE_VERTICALS.items()}


def declared_vertical(client, org_id):
    try:
        rows = (client.schema("storeops").table("tenants").select("vertical")
                .eq("org_id", org_id).limit(1).execute().data) or []
        return (rows[0].get("vertical") if rows else None) or None
    except Exception:
        return None


def tenant_vertical(client, org_id):
    """THE reader: the resolved vertical row for one tenant."""
    vocab, _ = load_vocab(client)
    return resolve_vertical(declared_vertical(client, org_id), vocab)


def vertical_context(client, org_id):
    """One read of everything the gates need: {vertical, vocab, module_scopes, registry_ready}."""
    vocab, ready = load_vocab(client)
    return {"vertical": resolve_vertical(declared_vertical(client, org_id), vocab), "vocab": vocab,
            "module_scopes": load_module_scopes(client), "registry_ready": ready}


def module_applies_to_tenant(client, org_id, module_key):
    ctx = vertical_context(client, org_id)
    return module_applies(ctx["module_scopes"].get(module_key), ctx["vertical"]["key"])


def me_payload(client, org_id):
    ctx = vertical_context(client, org_id)
    out = payload(ctx["vertical"], ctx["module_scopes"], ctx["vocab"])
    out["registry_ready"] = ctx["registry_ready"]
    out["closing_sections"] = closing_sections()   # what a company may override (Display Labels)
    return out


# ── PROGRAMMABLE: the super-admin Business Types editor (owner 2026-09-25: "no hard coded — all should be
#    programmable platform based"). Every field of a vertical — its label, whether it uses carriers, the pages
#    it hides, the closing-form inputs it hides, which modules belong to it — is edited on /admin/business-types
#    through the writers below; the house seed above is only the starting data.

# The closing-form inputs a vertical (or a tenant, via caps 'closing:<key>') may hide. The three form sections
# are the form's own vocabulary (components/ClosingSubmitForm.tsx); the tender entries are DERIVED from the
# built-in tender set (closing/tender_config.STANDARD_DEFS — one home, never re-listed here).
_FORM_SECTIONS = (
    ("acc_sale", "Accessory Sale $ box"),
    ("bill_payments", "Bill Payments section (processor on cash / credit / financing)"),
    ("activation_counts", "Built-in activation counts (used only when no Count Fields are defined)"),
)


def closing_sections():
    out = [{"key": k, "label": l, "kind": "section"} for k, l in _FORM_SECTIONS]
    try:
        from app.modules.closing.tender_config import STANDARD_DEFS
        out += [{"key": f"tender:{d[0]}", "label": f"Built-in tender: {d[1]}", "kind": "tender"} for d in STANDARD_DEFS]
    except Exception:
        pass
    return out


def validate_vertical(body, known_sections, existing_keys=None, creating=False):
    """(row, errors) — the vertical row a super-admin may save. PURE. Unknown closing keys, malformed hrefs
    and a malformed key are refused (never silently dropped)."""
    errs = []
    key = str(body.get("key") or "").strip()
    if creating:
        if not _KEY_RE.match(key):
            errs.append("key must be lower-case letters, digits and _ (2–41 chars), starting with a letter")
        elif existing_keys and key in existing_keys:
            errs.append(f"business type '{key}' already exists")
    label = str(body.get("label") or "").strip()
    if creating and not label:
        errs.append("label is required")
    row = {}
    if label:
        row["label"] = label[:120]
    if "uses_carriers" in body:
        row["uses_carriers"] = bool(body.get("uses_carriers"))
    if "sort_order" in body:
        try:
            row["sort_order"] = int(body.get("sort_order"))
        except (TypeError, ValueError):
            errs.append("sort_order must be a number")
    if "is_active" in body:
        row["is_active"] = bool(body.get("is_active"))
    if "nav_hidden" in body:
        hrefs = [str(h).strip() for h in (body.get("nav_hidden") or []) if str(h).strip()]
        bad = [h for h in hrefs if not re.match(r"^/[A-Za-z0-9_\-/\[\]]*\$?$", h)]
        if bad:
            errs.append(f"not a page address: {', '.join(bad[:5])}")
        row["nav_hidden"] = sorted(set(hrefs))
    if "closing_hidden" in body:
        keys = [str(k).strip() for k in (body.get("closing_hidden") or []) if str(k).strip()]
        unknown = [k for k in keys if k not in known_sections]
        if unknown:
            errs.append(f"unknown closing input: {', '.join(unknown)}")
        row["closing_hidden"] = [k for k in known_sections if k in keys]
    if creating:
        row["key"] = key
    return row, errs


def next_module_scope(current_scope, vertical_key, include, all_keys):
    """The module's applies_to_vertical after including / excluding one vertical. PURE.
    '{}' means every vertical, so excluding one from '{}' lists all the OTHERS explicitly; a list that comes
    to cover every vertical collapses back to '{}' (any — so a vertical created later gets the module too)."""
    cur = [v for v in (current_scope or []) if v]
    everyone = sorted(set(all_keys))
    members = set(everyone) if not cur else set(cur)
    if include:
        members.add(vertical_key)
    else:
        members.discard(vertical_key)
    return [] if members >= set(everyone) else sorted(members)


def admin_payload(client):
    """Everything the Business Types editor shows (super-admin only — gated by the endpoint)."""
    vocab, ready = load_vocab(client)
    try:
        mods = (client.schema("core").table("module_catalog").select("key,label,sort_order,applies_to_vertical")
                .order("sort_order").execute().data) or []
    except Exception:
        mods = []
    scopes = load_module_scopes(client)
    if not mods:
        from app.modules.core.entitlements import MODULE_CATALOG
        mods = [{"key": k, "label": v} for k, v in MODULE_CATALOG.items()]
    usage = {}
    try:
        for r in (client.schema("storeops").table("tenants").select("org_id,vertical").execute().data) or []:
            usage[r.get("vertical") or ""] = usage.get(r.get("vertical") or "", 0) + 1
    except Exception:
        pass
    rows = sorted((normalise_vertical(r) for r in vocab), key=lambda r: (r["sort_order"], r["key"]))
    default = default_vertical(rows)
    for r in rows:
        r["tenants"] = usage.get(r["key"], 0) + (usage.get("", 0) if default and r["key"] == default["key"] else 0)
    return {"registry_ready": ready, "verticals": rows, "closing_sections": closing_sections(),
            "modules": [{"key": m["key"], "label": m.get("label") or m["key"],
                         "applies_to_vertical": list(scopes.get(m["key"], m.get("applies_to_vertical") or []))}
                        for m in mods]}

