"""THE CONNECTOR REGISTRY — which connectors exist, and what POS / carrier each APPLIES TO. PURE core.

OWNER (2026-09-21, a Verizon tenant whose declared POS is RQ), verbatim, on the Inventory Values
page's chip "🔌 RQ portal connection / last: error / The b2bsoft (wsreports.b2bsoft.com) portal client
is not reverse-engineered yet …": *"it says rq connection but refers to b2b reports"*.

THE CLASS (CLAUDE.md "A fix is a DESIGN fix", measured before building). Connectors — portal sweeps
(`*_sweep_config`), data-source logins (`data_source.processor`), the mailbox / FTP pulls — carried
NO POS / carrier scope in any registry: `commcalc/upload`'s AUTO_SOURCES tile ids, `commcalc/
connectors`' sweep-kind picker, `connector_route_policy` (mig 998) and `_CONNECTOR_SLUG_SOURCE` all
key on the connector SLUG and presume the connector applies to every tenant. So the one portal-sweep
connector that exists (a b2bsoft one) was offered to a tenant that declared RQ, labelled with the
tenant's own POS term (#262's `usePosTerm`) over a vendor-specific form, and its stub error — which
spelled the vendor — reached the page as the connector's last status. The health scan, the control
box lamp and the login-popup attention providers judged it the same way.

Report kinds solved the SAME class in mig 1010: `applies_to_pos` / `applies_to_carrier` as data,
visible = applies ∩ declaration AND defined, ONE visibility function on every surface, a
build-failing lock. This module gives connectors that shape and REUSES that mechanism:

  · `commcalc.connector_registry` (mig 1014) — one row per connector per org, keyed on the slug the
    platform already dispatches on (data_source.processor / sweep_kind / connector_route_policy
    .connector / b2b_sweep_config.connector), with `aliases` for the second spelling the dispatchers
    accept, its `label` + `host` (what copy dereferences), `applies_to_pos[]` / `applies_to_carrier[]`
    codes ({} = any), `defined_by`. House rows = the platform default; a tenant row overrides per key
    (the mig-207 shape, through `report_kinds.merge_rows`).
  · `HOUSE_CONNECTORS` — the byte-equal CODE MIRROR of the 1014 seed (parsed back by
    `harness_connector_scope_lock.py`), so every surface answers honestly BEFORE the migration is
    applied and says `registry_ready:false`. The three merchant card portals mirror their label / host
    from `merchant_portals.PORTALS` — one home for those facts, never a copy.
  · THE DECLARATION is `report_kinds.tenant_declaration` — THE one reader; nothing here reads the
    pos_system term or the carrier rows itself.
  · THE PREDICATE is `report_kinds.visible_kinds` / `hidden_kinds`, called with the `connector:` cap
    namespace (`CAP_PREFIX`) — the super-admin widening rides the SAME ui_label_override 'cap' scope
    as `kind:<key>` / `carrier:` / `pos:`. Frontend twin: `lib/connectors.ts::useConnectors` over
    `carrier-scope.reportKindsVisible(rows, declaration, caps, 'connector:')`.
  · `scope(rows, declaration, slug)` — what EVERY connector surface asks: does this slug apply to this
    tenant, and what is it called. A slug the registry does not know is a tenant-defined connector and
    is NEVER withheld (`registered: False`, `applies: True`) — the registry hides only what it knows.
  · `neutral_line(pos, pos_declared)` — the ONE sentence a surface renders when no reports-portal
    connector is defined for the declared POS; the frontend twin carries the same words and the lock
    pins them equal.

RULE TWO. No POS, carrier or vendor name is DECIDED ON here: `HOUSE_CONNECTORS` carries them as DATA
(the seed's mirror, after the `HOUSE_CONNECTOR_KEYS` marker the vocabulary guard splits on), and every
caller passes a slug read off its own row. Copy in `b2b_sweep.py` / `vidapay_sweep.py` receives the
row's `label` / `host` and spells nothing.

NOT MONEY-TOUCHING. Nothing here reads or writes a rate, plan, payout or any calculation input; it
decides which connectors a tenant is SHOWN. Sweep dispatch is untouched (mig 998 owns the route gate).
"""
from urllib.parse import urlparse

from app.modules.commcalc import report_kinds as _rk
from app.modules.commcalc import merchant_portals as _mp

HOUSE_ORG = _rk.HOUSE_ORG
MIGRATION = "1014_connector_registry.sql"
TABLE = "connector_registry"
CAP_PREFIX = "connector:"                    # ui_label_override scope 'cap' namespace (beside kind:/carrier:/pos:)
KINDS = ("portal", "mailbox", "ftp", "google_sa")
_COLS = ("key", "aliases", "label", "host", "kind", "applies_to_pos", "applies_to_carrier", "defined_by",
         "sort_order", "is_active")
NEUTRAL_LABEL = "reports portal"             # copy's fallback when no registry row names the connector


def _c(**kw):
    row = {"aliases": [], "host": None, "kind": "portal", "applies_to_pos": [], "applies_to_carrier": [],
           "defined_by": "house", "is_active": True, "notes": None}
    row.update(kw)
    return row


def _merchant(key, sort_order, notes):
    """A merchant card portal's row, its label + host READ from merchant_portals.PORTALS (one home)."""
    p = _mp.PORTALS[key]
    return _c(key=key, label=p["label"], host=urlparse(p["base_url"]).netloc, sort_order=sort_order, notes=notes)


# ── THE HOUSE SEED'S MIRROR (byte-equal to mig 1014 — the lock parses the SQL and compares) ───────
# Vendor names, hosts and POS / carrier CODES are DATA here; the vocabulary guard scans this module for
# a vendor spelling only BEFORE the HOUSE_CONNECTOR_KEYS marker below.
HOUSE_CONNECTORS = [
    _c(key="b2bsoft", aliases=["b2b"], label="B2B Soft wsreports", host="wsreports.b2bsoft.com",
       applies_to_pos=["b2bsoft"], sort_order=10,
       notes="The POS reports portal (daily Sales Transaction Details, Inventory Aging). Applies to the POS whose portal it is; its pull route is closed by mig 998."),
    _c(key="vidapay", aliases=["total_access"], label="VidaPay master-agent portal", host="www.vidapaycrm.com",
       applies_to_carrier=["total"], sort_order=20,
       notes="The master-agent (MA) portal family; total_access is the same portal under its other name."),
    _c(key="epay", label="ePay Owner Portal", host="ownerportal.epayworldwide.com", applies_to_carrier=["boost"], sort_order=30,
       notes="MI & ATU, comprehensive comp, payment detail — the same carrier scope its report kinds carry in mig 1010."),
    _c(key="dlar", label="Carrier KPI portal (DLAR)", host="boostelevatego.com", sort_order=40,
       notes="Store + rep KPI reports. Any-scope because its report kinds (dlar_rep / dlar_store) are any-scope; narrow it here, never in code."),
    _c(key="vip", label="Distributor portal", host="www.vipwireless.com", applies_to_carrier=["boost"], sort_order=50,
       notes="Distributor invoices, PayGo, asset ledger — the same carrier scope its report kinds carry in mig 1010."),
    _merchant("payanywhere", 60, "Merchant card portal (mig 955). Any tenant may run a standalone card terminal."),
    _merchant("transfirst", 70, "Merchant card portal (mig 955)."),
    _merchant("businesstrack", 80, "Merchant card portal (mig 955)."),
    _c(key="mailbox", aliases=["email_sweep"], label="Email-ingested reports (mailbox)", kind="mailbox", sort_order=100,
       notes="The IMAP attachment sweep. Any POS or vendor that emails report files."),
    _c(key="ftp", label="FTP / SFTP drop", kind="ftp", sort_order=110, notes="The FTP-pull sweep."),
    _c(key="google_closing", label="Daily-closing responses sheet", host="docs.google.com", kind="google_sa", sort_order=120,
       notes="The closing-form responses sheet (service account)."),
]
HOUSE_CONNECTOR_KEYS = [r["key"] for r in HOUSE_CONNECTORS]


# ── rows ──────────────────────────────────────────────────────────────────────────────────────────
def slug(v):
    """A connector slug in its matching form — lower-case, trimmed (the route policy's normaliser)."""
    return str(v or "").strip().lower()


def normalise_row(row):
    """A registry row as the engine reads it — arrays as lists, codes squashed, defaults filled. The
    output carries exactly the fields `report_kinds.visible_kinds` reads (`key`, `is_active`,
    `applies_to_pos`, `applies_to_carrier`, `defined_by`) plus this registry's own."""
    out = _c()
    out.update({k: row.get(k) for k in _COLS if k in row})
    out["org_id"] = row.get("org_id") or HOUSE_ORG
    out["defined_by_org"] = row.get("defined_by_org")
    out["notes"] = row.get("notes")
    out["key"] = slug(out.get("key"))
    out["aliases"] = [slug(a) for a in _rk._list(out.get("aliases")) if slug(a)]
    out["label"] = _rk._s(out.get("label")) or out["key"]
    out["host"] = _rk._s(out.get("host")) or None
    out["kind"] = out.get("kind") if out.get("kind") in KINDS else "portal"
    out["applies_to_pos"] = [_rk.code(c) for c in _rk._list(out.get("applies_to_pos")) if _rk.code(c)]
    out["applies_to_carrier"] = [_rk.code(c) for c in _rk._list(out.get("applies_to_carrier")) if _rk.code(c)]
    out["is_active"] = out.get("is_active") is not False
    out["sort_order"] = int(out.get("sort_order") or 100)
    out["defined_by"] = out.get("defined_by") if out.get("defined_by") in _rk.DEFINED_BY else "house"
    return out


def merge_rows(rows, org_id):
    """House + this org's rows merged per key — `report_kinds.merge_rows`, this registry's normaliser."""
    return _rk.merge_rows(rows, org_id, normalise=normalise_row)


def house_mirror():
    return [dict(normalise_row({**r, "org_id": HOUSE_ORG}), _source="house") for r in HOUSE_CONNECTORS]


def resolve(rows, connector):
    """The registry row for a slug — by `key` or by any of its `aliases`; None when unregistered."""
    s = slug(connector)
    if not s:
        return None
    for r in rows or []:
        if r.get("key") == s or s in (r.get("aliases") or []):
            return r
    return None


# ── THE PREDICATE — report_kinds', called, not copied ─────────────────────────────────────────────
def visible(rows, declaration, caps=None, org_id=None):
    """The connectors this tenant is shown, with provenance: `report_kinds.visible_kinds` over the
    merged rows with the `connector:` cap namespace. THE rule every connector surface dereferences."""
    return _rk.visible_kinds(rows, declaration, caps, org_id, None, cap_prefix=CAP_PREFIX)


def hidden(rows, declaration, caps=None):
    """The connectors withheld, and why — `report_kinds.hidden_kinds` with the `connector:` namespace."""
    return _rk.hidden_kinds(rows, declaration, caps, cap_prefix=CAP_PREFIX)


def scope(rows, declaration, connector, caps=None, org_id=None):
    """WHAT EVERY CONNECTOR SURFACE ASKS about one slug: does it apply to this tenant, and what is it
    called. PURE.

    Returns {applies, registered, key, label, host, kind, why, provenance}:
      · unregistered slug → applies True, registered False: a tenant-defined connector (a processor
        typed by hand) is the tenant's own row and is never withheld by a registry that does not know it;
      · registered and visible → applies True with the row's provenance;
      · registered and not visible → applies False, `why` from `hidden` (the declaration it misses)."""
    row = resolve(rows, connector)
    if row is None:
        return {"applies": True, "registered": False, "key": slug(connector) or None, "label": None, "host": None,
                "kind": None, "why": "not in the connector registry — a tenant-defined connector is never withheld",
                "miss": None, "provenance": None}
    base = {"registered": True, "key": row["key"], "label": row["label"], "host": row.get("host"), "kind": row.get("kind")}
    for v in visible(rows, declaration, caps, org_id):
        if v["key"] == row["key"]:
            return {**base, "applies": True, "why": "", "miss": None, "provenance": v.get("provenance")}
    why = next((h["why"] for h in hidden(rows, declaration, caps) if h["key"] == row["key"]), "not visible")
    # WHICH axis the declaration misses — so a surface can pick the POS sentence (the neutral line) or
    # the carrier one without parsing `why`. 'override' = hidden by the super-admin cap.
    # each axis through THE predicate (report_kinds.applies) with the other axis blanked — no second
    # intersection is written here
    pos_miss = not _rk.applies({"applies_to_pos": row["applies_to_pos"], "applies_to_carrier": []}, declaration or {})
    car_miss = not _rk.applies({"applies_to_pos": [], "applies_to_carrier": row["applies_to_carrier"]}, declaration or {})
    miss = "both" if (pos_miss and car_miss) else ("pos" if pos_miss else ("carrier" if car_miss else "override"))
    return {**base, "applies": False, "why": why, "miss": miss, "provenance": None}


def label_of(row_or_scope, fallback=NEUTRAL_LABEL):
    """The name copy prints for a connector — the registry's label, else the neutral noun. Never a
    vendor spelled by the caller."""
    return _rk._s((row_or_scope or {}).get("label")) or fallback


def host_of(row_or_scope):
    return _rk._s((row_or_scope or {}).get("host")) or None


# ── THE NEUTRAL LINE — one sentence, one home (frontend twin: lib/connectors.ts neutralConnectorLine) ──
def neutral_line(pos, pos_declared, noun="reports-portal"):
    """What a surface says when NO connector of this kind is defined for the tenant's declared POS.
    `pos` is the tenant's POS term (report_labels.pos_term / usePosTerm) — never a vendor spelled here."""
    if pos_declared:
        return (f"No {noun} connection is defined for {pos} yet — when {pos} has a reports portal, it can be "
                f"added under Connectors.")
    return (f"No POS is declared for this tenant yet, so no {noun} connection is offered. Declare your POS in "
            f"the Implementation wizard and the connection defined for it will appear here.")


def not_applicable_copy(sc, pos, pos_declared):
    """The sentence a surface that drives ONE connector prints when the registry withholds it — the
    neutral line when the DECLARED POS is what it misses (the owner's case), else the carrier /
    override reason from the registry. Frontend twin: lib/connectors.ts connectorNotApplicableCopy."""
    if (sc or {}).get("miss") in ("pos", "both"):
        return neutral_line(pos, pos_declared)
    why = _rk._s((sc or {}).get("why")) or "it is not offered to this tenant"
    return (f"This connector is not offered to this tenant — {why}. Widen its scope in the connector registry "
            f"if this tenant does use it.")


# ── payload ───────────────────────────────────────────────────────────────────────────────────────
def payload(rows, ready, declaration, caps, org_id, pos=None, pos_declared=False):
    """The GET /commcalc/connector-registry body — everything a connector surface needs, computed ONCE."""
    vis = visible(rows, declaration, caps, org_id)
    public = [{k: r.get(k) for k in _COLS + ("provenance", "provenance_text", "defined_by_org")} for r in vis]
    caps_conn = {k: v for k, v in (caps or {}).items() if str(k).startswith(CAP_PREFIX)}
    return {
        "registry_ready": ready, "migration": MIGRATION,
        "declaration": declaration,
        "connectors": public,
        "hidden": hidden(rows, declaration, caps),
        "caps": caps_conn,
        "all_keys": [{"key": r["key"], "aliases": r["aliases"], "label": r["label"], "kind": r["kind"],
                      "applies_to_pos": r["applies_to_pos"], "applies_to_carrier": r["applies_to_carrier"]}
                     for r in rows if r["is_active"]],
        "pos": {"term": pos, "declared": bool(pos_declared)},
        "neutral_line": neutral_line(pos, pos_declared) if pos is not None else None,
    }


# ── LOADERS (org-scoped; house + this org only) ───────────────────────────────────────────────────
def load_registry(client, org_id):
    """(rows merged per key, ready) — house rows + this org's rows from mig 1014, else the mirror."""
    try:
        rows = (client.schema("commcalc").table(TABLE).select("*")
                .in_("org_id", sorted({HOUSE_ORG, str(org_id)})).execute().data) or []
        if not rows:
            return house_mirror(), False
        return merge_rows(rows, org_id), True
    except Exception:
        return house_mirror(), False


def load_caps(client, org_id):
    """This org's `connector:` cap overrides (ui_label_override scope 'cap' — the SAME rows GET
    /nav-config and `_intake_caps` read; only this registry's namespace is kept). Org-scoped."""
    caps = {}
    try:
        for r in (client.schema("commcalc").table("ui_label_override").select("key,label")
                  .eq("org_id", org_id).eq("scope", "cap").execute().data) or []:
            k = str(r.get("key") or "")
            if k.startswith(CAP_PREFIX):
                v = str(r.get("label") or "").lower()
                caps[k] = True if v == "show" else (False if v == "hide" else None)
    except Exception:
        caps = {}
    return caps


def scope_context(client, org_id):
    """EVERYTHING `scope` needs for one org, read ONCE — for an endpoint to hand down to providers on
    the attention context (never read inside a cheap provider: the house-default read is an
    inheritance read, `org_id IN (tenant, house)`, which the login-popup isolation pin forbids there).
    Returns {rows, ready, declaration, caps, org_id}; on any failure the mirror + an unknown
    declaration, which shows LESS (design §7), never more."""
    rows, ready = load_registry(client, org_id)
    try:
        decl = _rk.tenant_declaration(client, org_id)
    except Exception:
        decl = {"pos": [], "carriers": [], "reasons": ["declaration could not be read"]}
    return {"rows": rows, "ready": ready, "declaration": decl, "caps": load_caps(client, org_id), "org_id": str(org_id)}


def scope_for(ctx, connector):
    """`scope` over a context from `scope_context`. A missing / broken context is INERT: applies True."""
    if not isinstance(ctx, dict) or not ctx.get("rows"):
        return {"applies": True, "registered": False, "key": slug(connector) or None, "label": None, "host": None,
                "kind": None, "why": "no connector-scope context", "miss": None, "provenance": None}
    return scope(ctx["rows"], ctx.get("declaration") or {}, connector, ctx.get("caps"), ctx.get("org_id"))


def scope_of(client, org_id, connector):
    """One-call form for a single-slug call site (a public sweep config, a stub's error message)."""
    return scope_for(scope_context(client, org_id), connector)
