"""Carrier-aware REPORT COLUMN LABELS + report-banner terminology (owner directive 2026-09-02).

WHY: the same activation-report column means a different program per carrier — the Total side's
device-financing column is "Edge", the Boost side's is "ACIMA" — and the Exec-MTD unrecognized-
contract-type warning names a b2bsoft MTD reconciliation that is meaningless for a tenant whose
feed is not b2bsoft-shaped. Both are DISPLAY TERMINOLOGY, so per RULE TWO they are config rows,
never carrier branches in code:

  · CARRIER PRESETS live as HOUSE-org rows in the EXISTING commcalc.ui_label_override table
    (mig 068 — the scope-multiplexed display-label store; same reuse precedent as scope='tiles'
    in tile_layout.py) under scope='report_col:<carrier>' / 'report_banner:<carrier>',
    key=<column key|banner key>, label=<text|'on'|'off'>. Seeded by mig 945 (Boost: edge→ACIMA +
    ct-gap banner off; Total: edge→Edge + banner on, the explicit statement of today's behavior).
  · TENANT OVERRIDES are rows at the tenant's own org under the UN-suffixed scopes
    ('report_col' / 'report_banner') — "they can change if they want to".
  · RESOLUTION (pure, proven in backend/harness_report_labels.py):
        tenant override  >  house carrier preset (for the org's carrier)  >  built-in default.
    LAZY auto-assign: a NEW tenant that picks its carrier at setup (commcalc.carrier, mig 038 —
    the existing "Carrier Selection" onboarding step) gets the carrier's label set the moment the
    resolver runs — no setup hook, and an org with no carrier row / no preset rows is
    BYTE-IDENTICAL to today (built-in defaults render).

Frontend consumption is the mig-932 gp acc_label pattern: the payload carries the resolved label
map per carrier; the pages (Exec MTD, Activations) render headers from it with their built-in
header as the fallback, so exports and grids can never disagree.

Everything except the loader is PURE (stdlib only). The loader takes the client as a parameter and
degrades to empty maps (built-ins render) when mig 068/945 are absent — never raises.
"""
import re

HOUSE_ORG = "00000000-0000-0000-0000-000000000001"

# Scope bases in commcalc.ui_label_override (mig 068). '<base>' at the tenant org = override;
# '<base>:<carrier>' at the HOUSE org = that carrier's preset.
SCOPE_COL = "report_col"
SCOPE_BANNER = "report_banner"

# ── The label-able report columns (key → built-in default header). These are the Exec MTD /
#    Activations activation-report columns; the SETTINGS UI lists exactly this registry
#    (pick-don't-type — a tenant can only relabel a real column, never invent a phantom key).
LABELABLE_COLUMNS = (
    ("total_activation", "Total Activation"),
    ("activation", "Activation"),
    ("port", "Port"),
    ("byod", "BYOD"),
    ("tablet", "Tablet"),
    ("home_internet", "Home Internet"),
    ("edge", "Edge"),
    ("upgrade", "Upgrade"),
    ("total_phones", "Total Phones"),
    ("trending_box", "Trending Box"),
    ("bill_payment_qty", "Bill Payment Qty"),
    ("amount", "$"),
    ("conv", "Conv."),
    ("acc_sales", "Acc. Sales"),
    ("apb", "APB"),
    ("trending_acc_sales", "Trending Acc. Sales"),
    ("activation_fee", "Activation Fee"),
    ("total_protect", "Total Protect"),
    ("setup_fee", "Set-up Fee"),
    ("setup_fee_dealer_share", "Dealer share"),
    ("setup_fee_employee_pay", "Employee pay"),
    ("acc_plus_setup", "Acc.+Set-up (target basis)"),
)
DEFAULT_COLUMN_LABELS = dict(LABELABLE_COLUMNS)

# ── The gate-able report banners (key → default 'on'|'off'). 'on' with no rows anywhere = today's
#    behavior, so an un-migrated / preset-less org is byte-identical.
#    unrecognized_ct_recon: the Exec-MTD "Some activations aren't being counted … lower than the
#    b2bsoft MTD report" warning — carrier terminology, OFF in the Boost preset (mig 945).
BANNERS = {
    "unrecognized_ct_recon": {
        "default": "on",
        "title": "Unrecognized contract-type warning (b2bsoft MTD reconciliation)",
    },
}
DEFAULT_BANNER_STATES = {k: v["default"] for k, v in BANNERS.items()}

_ON_OFF = ("on", "off")


# ── PURE: carrier identity ────────────────────────────────────────────────────────────────────────
def normalize_carrier_code(code_or_name):
    """Canonical lowercase carrier code from a commcalc.carrier row's code or name. MIRRORS the
    frontend's rbac.carrierCode() so the preset scope written here is the scope the active-carrier
    lens looks up ('Boost Mobile'→'boost', 'Total Wireless'/null-code+'Total…'→'total')."""
    raw = str(code_or_name or "").strip().lower()
    if not raw:
        return ""
    if "boost" in raw:
        return "boost"
    if "total" in raw or "vidapay" in raw:
        return "total"
    if "cricket" in raw:
        return "cricket"
    return re.sub(r"\s+", "-", raw)


def carrier_codes(carrier_rows):
    """Ordered, de-duplicated normalized codes for an org's commcalc.carrier rows."""
    out = []
    for r in carrier_rows or []:
        c = normalize_carrier_code((r or {}).get("code") or (r or {}).get("name"))
        if c and c not in out:
            out.append(c)
    return out


def default_carrier(carrier_rows):
    """The org's default carrier code: the is_default row, else the sole row, else the first row,
    else '' (no carrier chosen yet → no preset applies). Mirrors rbac.defaultActiveCarrier minus
    its UI-only 'boost' fallback — the RESOLVER must never assume a carrier the org never picked."""
    rows = [r for r in (carrier_rows or []) if r]
    for r in rows:
        if r.get("is_default"):
            c = normalize_carrier_code(r.get("code") or r.get("name"))
            if c:
                return c
    codes = carrier_codes(rows)
    return codes[0] if codes else ""


def preset_scope(base, carrier):
    """The house-preset scope string for one carrier ('report_col' + 'boost' → 'report_col:boost')."""
    return f"{base}:{normalize_carrier_code(carrier)}"


# ── PURE: row parsing + resolution ────────────────────────────────────────────────────────────────
def parse_label_rows(rows, org_id, carriers, house_org=HOUSE_ORG):
    """Split raw ui_label_override rows into tenant OVERRIDES and per-carrier house PRESETS.

    Returns {"overrides": {"columns": {...}, "banners": {...}},
             "presets": {carrier: {"columns": {...}, "banners": {...}}}}.
    Only known banner keys with 'on'/'off' values are kept (a junk row can never crash a report or
    invent a banner state); column keys are kept as stored (unknown keys are inert — no column
    renders them). Overrides are read from the org's OWN rows (the house org may hold overrides for
    itself too — they never leak into other tenants' resolution, which reads only preset scopes
    from the house org)."""
    carriers = [normalize_carrier_code(c) for c in (carriers or [])]
    overrides = {"columns": {}, "banners": {}}
    presets = {c: {"columns": {}, "banners": {}} for c in carriers if c}
    col_scopes = {preset_scope(SCOPE_COL, c): c for c in carriers if c}
    ban_scopes = {preset_scope(SCOPE_BANNER, c): c for c in carriers if c}
    for r in rows or []:
        r = r or {}
        org, scope = str(r.get("org_id") or ""), str(r.get("scope") or "")
        key, label = str(r.get("key") or "").strip(), str(r.get("label") or "").strip()
        if not key or not label:
            continue
        if org == org_id and scope == SCOPE_COL:
            overrides["columns"][key] = label
        elif org == org_id and scope == SCOPE_BANNER:
            if key in BANNERS and label.lower() in _ON_OFF:
                overrides["banners"][key] = label.lower()
        elif org == house_org and scope in col_scopes:
            presets[col_scopes[scope]]["columns"][key] = label
        elif org == house_org and scope in ban_scopes:
            if key in BANNERS and label.lower() in _ON_OFF:
                presets[ban_scopes[scope]]["banners"][key] = label.lower()
    return {"overrides": overrides, "presets": presets}


def resolve_columns(overrides, preset, defaults=None):
    """Fully-resolved column-label map for ONE carrier: default < carrier preset < tenant override.
    `defaults` None → DEFAULT_COLUMN_LABELS. Pure; inputs never mutated."""
    out = dict(DEFAULT_COLUMN_LABELS if defaults is None else defaults)
    out.update({k: v for k, v in (preset or {}).items() if str(v).strip()})
    out.update({k: v for k, v in (overrides or {}).items() if str(v).strip()})
    return out


def resolve_banners(overrides, preset):
    """Resolved 'on'/'off' per known banner key, same precedence as resolve_columns. Unknown or
    junk values never survive (parse_label_rows already filtered; filtered again here so the
    function is safe on hand-built dicts too)."""
    out = dict(DEFAULT_BANNER_STATES)
    for src in (preset or {}), (overrides or {}):
        for k, v in src.items():
            if k in BANNERS and str(v).lower() in _ON_OFF:
                out[k] = str(v).lower()
    return out


def banner_on(resolved_banners, key):
    """True when banner `key` should render. Unknown key → True (a NEW banner key defaults to
    today's always-shown behavior until it is registered in BANNERS)."""
    return str((resolved_banners or {}).get(key, "on")).lower() != "off"


def build_payload(parsed, carriers, default_code):
    """The GET /report-labels response body from parse_label_rows() output: per-carrier RESOLVED
    maps (what the pages render) + the raw override/preset layers (what the settings UI edits)."""
    overrides, presets = parsed["overrides"], parsed["presets"]
    columns, banners = {}, {}
    # Column maps carry ONLY the keys a preset/override actually names (defaults={}) — each page
    # keeps its OWN built-in header as the fallback (Exec MTD says 'Activation', the Activations
    # page says 'New Activation'), so a preset-less org renders byte-identical to today.
    for c in carriers:
        p = presets.get(c) or {"columns": {}, "banners": {}}
        columns[c] = resolve_columns(overrides["columns"], p["columns"], defaults={})
        banners[c] = resolve_banners(overrides["banners"], p["banners"])
    # '_' = the no-preset resolution (this org's overrides only). The frontend's last fallback, so
    # a tenant override still applies when the org has no carrier row yet.
    columns["_"] = resolve_columns(overrides["columns"], {}, defaults={})
    banners["_"] = resolve_banners(overrides["banners"], {})
    return {
        "carriers": carriers,
        "default_carrier": default_code,
        "columns": columns,                       # resolved, per carrier — render from this
        "banners": banners,                       # resolved 'on'/'off', per carrier
        "overrides": overrides,                   # this org's own rows (settings UI layer 1)
        "presets": presets,                       # house carrier presets (settings UI layer 2)
        "editable_columns": [{"key": k, "default": d} for k, d in LABELABLE_COLUMNS],
        "banner_keys": [{"key": k, "default": v["default"], "title": v["title"]}
                        for k, v in BANNERS.items()],
    }


# ── Thin loader (the ONLY DB touch in this module; client injected; degrades, never raises) ───────
def load_report_labels(client, org_id, house_org=HOUSE_ORG):
    """Resolved report labels for one org. Two org-scoped reads: the org's carrier rows (mig 038 —
    the SAME table the onboarding 'Carrier Selection' step writes, which is what makes preset
    auto-assign LAZY: pick a carrier, the labels follow) and the relevant ui_label_override rows
    (tenant override scopes + the house preset scopes for exactly this org's carriers). Any failure
    (mig 068 absent, no carriers) → empty/default maps = built-in labels, byte-identical."""
    org_id = str(org_id or "").strip() or house_org
    try:
        crows = (client.schema("commcalc").table("carrier").select("name,code,is_default")
                 .eq("org_id", org_id).execute().data) or []
    except Exception:
        crows = []
    codes = carrier_codes(crows)
    scopes = [SCOPE_COL, SCOPE_BANNER]
    for c in codes:
        scopes += [preset_scope(SCOPE_COL, c), preset_scope(SCOPE_BANNER, c)]
    try:
        rows = (client.schema("commcalc").table("ui_label_override")
                .select("org_id,scope,key,label")
                .in_("org_id", sorted({org_id, house_org})).in_("scope", scopes)
                .execute().data) or []
    except Exception:
        rows = []
    parsed = parse_label_rows(rows, org_id, codes, house_org=house_org)
    return build_payload(parsed, codes, default_carrier(crows))
