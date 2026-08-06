"""Agency module — endpoint logic (Phase 1: config + invoicing).

Master-Agent → Sub-Agent relationships and the money CONFIG around them: links (+ cycle guard + consent),
per-carrier scoping, the sub's store roster (consent-gated cross-org pull), commission holdback rules,
equipment-margin rules, the charge catalog, equipment-transfer intake (manual / CSV feed / OCR upload +
confirm), and agency invoice generation (draft delete-then-recompute → issue-freeze → void).

Every function takes an explicit `client` (the service-role Supabase client) and an `org_id` that is ALWAYS
the MASTER's org (RULE ONE: org_id is the middleware-rewritten query param on the router wrappers; here it is
just a value). Reads/writes are `.eq("org_id", org_id)`. The three sanctioned cross-org reads are documented
inline: the cycle-guard ancestor walk (structural ids only), the sub-tenant picker (tenant registry names),
and the consent-gated sub store roster pull.

MONEY-SAFETY: nothing here imports/reads rep_commissions / calculator / commission_engine — the agency module
never changes a rep's pay. The invoice math lives in agency_billing.py (pure + proven).

Tables: mig 220 (link/carrier/store/holdback/margin/charge), mig 222 (invoice/invoice_line/transfer). Every
DB touch is wrapped so a missing migration degrades to a clear 400 "run migration …" notice, never a 500.
"""
import io
import os
import uuid
import asyncio
import calendar as _cal
import concurrent.futures
from datetime import datetime, timezone
from fastapi import HTTPException

from app.core.database import get_supabase
from app.core.config import settings
from app.modules.commcalc import agency_billing as AB

AGENCY_BUCKET = "agency-docs"                       # N1: dedicated bucket, core-provisioned (NEEDS CORE)
_HOUSE = "00000000-0000-0000-0000-000000000001"

CONSENT_STATES = {"not_requested", "pending", "accepted", "declined", "revoked"}
LINK_STATUSES = {"draft", "active", "suspended", "ended"}
HOLDBACK_SCOPES = {"all", "ledger_bucket", "commission_component", "statement_line_type", "product_class", "carrier"}
LEDGER_BUCKETS = ["commission", "spiff", "equipment_rebate", "residual_monthly", "autopay_residual"]
COMMISSION_COMPONENTS = ["device_margin", "consumer_margin", "rebate", "mrc_net_discount", "fees_margin",
                         "spiff_m1", "spiff_m2", "spiff_m3", "spiff_m4", "spiff_m5", "spiff_m6",
                         "residual", "other_amount"]


def _c(client):
    return (client or get_supabase()).schema("commcalc")


def _now():
    return datetime.now(timezone.utc).isoformat()


def _pvariants(period):
    """Month-period spelling variants ('June 2026' ⇄ '2026-06'); passthrough for non-month values."""
    p = str(period or "").strip()
    if len(p) >= 7 and p[:4].isdigit() and p[4] == "-" and p[5:7].isdigit():
        mo, yr = int(p[5:7]), int(p[:4])
    else:
        parts = p.split()
        names = {m.lower(): i for i, m in enumerate(_cal.month_name) if m}
        if len(parts) == 2 and parts[0].lower() in names and parts[1].isdigit():
            mo, yr = names[parts[0].lower()], int(parts[1])
        else:
            return [p]
    if not (1 <= mo <= 12 and yr):
        return [p]
    return list({p, f"{_cal.month_name[mo]} {yr}", f"{yr}-{mo:02d}"})


def _mig(need, e):
    raise HTTPException(400, f"Agency tables not ready — run migration {need}_commission_agency_*.sql. [{e}]")


# ══ links ═══════════════════════════════════════════════════════════════════════════════════════════
def _get_link(client, org_id, link_id):
    try:
        rows = (_c(client).table("agency_link").select("*").eq("org_id", org_id).eq("id", link_id)
                .limit(1).execute().data) or []
    except Exception as e:
        _mig("220", e)
    if not rows:
        raise HTTPException(404, "link not found")
    return rows[0]


def _ancestor_orgs(client, master_org):
    """Structural cross-org walk for the cycle guard (RULE ONE exception, documented): the set of orgs that
    are masters (upstream) of `master_org`, following agency_link.sub_org_id → org_id. Reads ONLY id/org_id/
    sub_org_id — no money columns, no financial leak. Bounded by a visited set (cycle-safe)."""
    seen, frontier, anc = set(), [master_org], set()
    while frontier:
        cur = frontier.pop()
        if cur in seen:
            continue
        seen.add(cur)
        try:
            rows = (_c(client).table("agency_link").select("org_id,sub_org_id")
                    .eq("sub_org_id", cur).execute().data) or []
        except Exception:
            rows = []
        for r in rows:
            m = r.get("org_id")
            if m and m not in anc:
                anc.add(m)
                frontier.append(m)
    return anc


def list_links(client, org_id):
    try:
        links = (_c(client).table("agency_link").select("*").eq("org_id", org_id)
                 .order("created_at").execute().data) or []
        stores = (_c(client).table("agency_link_store").select("link_id").eq("org_id", org_id).execute().data) or []
        carr = (_c(client).table("agency_link_carrier").select("link_id,carrier_id").eq("org_id", org_id).execute().data) or []
    except Exception as e:
        _mig("220", e)
    scount, ccount = {}, {}
    for s in stores:
        scount[s.get("link_id")] = scount.get(s.get("link_id"), 0) + 1
    for c in carr:
        ccount[c.get("link_id")] = ccount.get(c.get("link_id"), 0) + 1
    for lk in links:
        lk["store_count"] = scount.get(lk.get("id"), 0)
        lk["carrier_count"] = ccount.get(lk.get("id"), 0)
    return {"ok": True, "org_id": org_id, "links": links}


def get_link(client, org_id, link_id):
    link = _get_link(client, org_id, link_id)
    def rd(t, order="created_at"):
        try:
            return (_c(client).table(t).select("*").eq("org_id", org_id).eq("link_id", link_id)
                    .order(order).execute().data) or []
        except Exception:
            return []
    return {"ok": True, "link": link,
            "carriers": rd("agency_link_carrier"), "stores": rd("agency_link_store"),
            "holdback_rules": rd("agency_holdback_rule"), "equipment_margins": rd("agency_equipment_margin"),
            "charges": rd("agency_charge")}


def upsert_link(client, org_id, body, who=None):
    """Create/update a link. On create (or when the sub_org_id is set/changed) run the CYCLE GUARD."""
    sub_kind = (body.get("sub_kind") or "tenant").strip()
    sub_org_id = (body.get("sub_org_id") or None)
    sub_name = (body.get("sub_name") or "").strip()
    if sub_kind not in ("tenant", "external"):
        raise HTTPException(400, "sub_kind must be 'tenant' or 'external'")
    if sub_kind == "tenant" and not sub_org_id:
        raise HTTPException(400, "a tenant sub needs sub_org_id (pick an existing tenant)")
    if not sub_name:
        raise HTTPException(400, "sub_name is required")
    status = (body.get("status") or "draft").strip()
    if status not in LINK_STATUSES:
        raise HTTPException(400, f"status must be one of {sorted(LINK_STATUSES)}")
    # C7 (M4): only 'period_anchor' is supported in v1 — reject 'split_period' loudly rather than store an
    # ignored setting. The rate/margin/charge in effect on the period's LAST day governs the whole period.
    rate_change_mode = (body.get("rate_change_mode") or "period_anchor").strip()
    if rate_change_mode != "period_anchor":
        raise HTTPException(400, "rate_change_mode 'split_period' is not supported in v1 — use 'period_anchor' "
                                 "(the rate in effect on the period's last day governs the whole period)")

    # CYCLE GUARD (create, or when sub_org_id changes) — reject A→B→A and deeper chains.
    if sub_kind == "tenant" and sub_org_id:
        if str(sub_org_id) == str(org_id):
            raise HTTPException(400, "a master cannot be its own sub")
        anc = _ancestor_orgs(client, org_id)
        if str(sub_org_id) in {str(a) for a in anc}:
            raise HTTPException(400, "cycle rejected: that sub is already a master upstream of you")

    row = {
        "org_id": org_id, "sub_kind": sub_kind, "sub_org_id": sub_org_id, "sub_name": sub_name,
        "sub_contact_name": body.get("sub_contact_name"), "sub_contact_email": body.get("sub_contact_email"),
        "sub_contact_phone": body.get("sub_contact_phone"), "bill_company_id": body.get("bill_company_id") or None,
        "status": status, "taxable": bool(body.get("taxable")), "tax_rate": AB._f(body.get("tax_rate")),
        "default_proration_mode": (body.get("default_proration_mode") or "full"),
        "holdback_visible_to_sub": bool(body.get("holdback_visible_to_sub")),
        "rate_change_mode": rate_change_mode,
        "effective_start": body.get("effective_start") or None, "effective_end": body.get("effective_end") or None,
        "is_active": bool(body.get("is_active", True)), "notes": body.get("notes"), "updated_at": _now(),
    }
    try:
        if body.get("id"):
            _c(client).table("agency_link").update(row).eq("org_id", org_id).eq("id", body["id"]).execute()
            return {"ok": True, "id": body["id"]}
        row["id"] = str(uuid.uuid4())
        row["created_by"] = who
        row["created_at"] = _now()
        r = _c(client).table("agency_link").insert(row).execute()
        return {"ok": True, "link": (r.data[0] if r.data else row)}
    except HTTPException:
        raise
    except Exception as e:
        _mig("220", e)


def delete_link(client, org_id, link_id):
    """M3: REFUSE (409) when the link has ISSUED/PAID invoices — an issued financial document must not
    hard-delete (void it first). Draft/void invoices cascade-delete freely."""
    _get_link(client, org_id, link_id)
    try:
        invs = (_c(client).table("agency_invoice").select("status").eq("org_id", org_id)
                .eq("link_id", link_id).execute().data) or []
    except Exception:
        invs = []
    if any((i.get("status") in ("issued", "paid")) for i in invs):
        raise HTTPException(409, "cannot delete a link that has issued invoices — void them first "
                                 "(draft/void invoices delete with the link)")
    _c(client).table("agency_link").delete().eq("org_id", org_id).eq("id", link_id).execute()
    return {"ok": True}


def set_consent(client, org_id, link_id, status, who=None):
    """Master-side consent record. NOTE: Phase 3 replaces this with the SUB-side portal accept (the sub
    consents, per the account-linking privacy doctrine). Until then a master may record an OFFLINE consent
    (a signed paper agreement) here; the gate that matters — no cross-org roster pull without 'accepted' — is
    enforced in store_candidates()."""
    _get_link(client, org_id, link_id)
    if status not in CONSENT_STATES:
        raise HTTPException(400, f"status must be one of {sorted(CONSENT_STATES)}")
    patch = {"sub_consent_status": status, "sub_consent_at": _now(), "sub_consent_by": who, "updated_at": _now()}
    _c(client).table("agency_link").update(patch).eq("org_id", org_id).eq("id", link_id).execute()
    return {"ok": True, "sub_consent_status": status}


def lookup_sub_tenant(client, org_id, query):
    """M1 — EXACT-MATCH sub-tenant lookup (anti-enumeration doctrine). A browse-all list of storeops.tenants
    would enumerate every tenant to any org user (cross-tenant relationship disclosure). Instead the caller
    types the tenant's EXACT slug OR an exact org-admin email; only an exact match returns that ONE tenant
    (name + org_id). No listing, and a NON-match / self / cycle all return the SAME empty result (no oracle
    that a slug/email exists). Gated by _can_edit_agency at the router. Re-enabling browse-all is a Gate-2
    owner decision."""
    q = (query or "").strip()
    if not q:
        return {"ok": True, "tenant": None}
    tt = (client or get_supabase()).schema("storeops")
    match = None
    # (1) exact slug
    try:
        rows = (tt.table("tenants").select("org_id,name,slug,is_active").eq("slug", q).execute().data) or []
        match = next((t for t in rows if t.get("is_active", True)), None)
    except Exception:
        match = None
    # (2) exact org-admin email → that admin's org → its tenant
    if not match and "@" in q:
        for em in ({q, q.lower()}):
            try:
                us = (tt.table("app_users").select("org_id,role,is_active").eq("email", em).execute().data) or []
            except Exception:
                us = []
            admin_orgs = [u.get("org_id") for u in us if u.get("is_active", True)
                          and (u.get("role") or "").lower() in ("admin", "owner")]
            for oid in admin_orgs:
                try:
                    tr = (tt.table("tenants").select("org_id,name,slug,is_active").eq("org_id", oid).execute().data) or []
                except Exception:
                    tr = []
                match = next((t for t in tr if t.get("is_active", True)), None)
                if match:
                    break
            if match:
                break
    if not match:
        return {"ok": True, "tenant": None}
    anc = {str(a) for a in _ancestor_orgs(client, org_id)}
    if str(match.get("org_id")) == str(org_id) or str(match.get("org_id")) in anc:
        return {"ok": True, "tenant": None}   # self / would-be cycle → uniform empty (no enumeration oracle)
    return {"ok": True, "tenant": {"org_id": match.get("org_id"), "name": match.get("name"), "slug": match.get("slug")}}


def set_carriers(client, org_id, link_id, carrier_ids, who=None):
    """Replace the link's carrier scope set (zero rows = all carriers)."""
    _get_link(client, org_id, link_id)
    try:
        _c(client).table("agency_link_carrier").delete().eq("org_id", org_id).eq("link_id", link_id).execute()
        for cid in (carrier_ids or []):
            _c(client).table("agency_link_carrier").insert({
                "id": str(uuid.uuid4()), "org_id": org_id, "link_id": link_id, "carrier_id": cid,
                "is_active": True, "created_by": who, "created_at": _now()}).execute()
    except Exception as e:
        _mig("220", e)
    return {"ok": True, "count": len(carrier_ids or [])}


# ══ stores roster ════════════════════════════════════════════════════════════════════════════════════
def list_stores(client, org_id, link_id):
    _get_link(client, org_id, link_id)
    try:
        rows = (_c(client).table("agency_link_store").select("*").eq("org_id", org_id)
                .eq("link_id", link_id).order("created_at").execute().data) or []
    except Exception as e:
        _mig("220", e)
    return {"ok": True, "stores": rows}


def store_candidates(client, org_id, link_id):
    """CONSENT-GATED cross-org read: when the sub is a TENANT and it has ACCEPTED consent, pull the sub's own
    storeops.stores so the master can pick its roster. Otherwise return consented=False and NO stores — the
    master must enter the roster manually (zero cross-org leak of an unconsented sub's stores)."""
    link = _get_link(client, org_id, link_id)
    if link.get("sub_kind") != "tenant" or not link.get("sub_org_id"):
        return {"ok": True, "consented": False, "reason": "external sub — manual entry only", "stores": []}
    if link.get("sub_consent_status") != "accepted":
        return {"ok": True, "consented": False, "reason": "sub has not accepted consent — manual entry only",
                "stores": []}
    try:
        rows = ((client or get_supabase()).schema("storeops").table("stores")
                .select("id,store_code,address,market,is_active")
                .eq("org_id", link["sub_org_id"]).execute().data) or []
    except Exception:
        rows = []
    # `is_active` is NULLABLE: `.get("is_active", True)` returns the default only when the KEY is
    # ABSENT — a row whose column exists but is NULL returned None (falsy) and was WRONGLY dropped.
    # Same NULL-safe predicate as `_store_active` / storeops' `_inactive_ids_from`: only an EXPLICIT
    # false is inactive (owner defect 2026-08-06).
    stores = [{"store_id": r.get("id"), "store_code": r.get("store_code"), "store_address": r.get("address"),
               "market": r.get("market")} for r in rows if r.get("is_active") is not False]
    return {"ok": True, "consented": True, "stores": stores}


def upsert_store(client, org_id, link_id, body, who=None):
    _get_link(client, org_id, link_id)
    kind = (body.get("store_kind") or "storeops").strip()
    row = {"org_id": org_id, "link_id": link_id, "store_kind": kind,
           "store_id": body.get("store_id"), "store_code": body.get("store_code"),
           "store_address": body.get("store_address"), "store_label": body.get("store_label"),
           "effective_start": body.get("effective_start") or None, "effective_end": body.get("effective_end") or None,
           "is_active": bool(body.get("is_active", True))}
    if not (row["store_code"] or row["store_address"] or row["store_label"] or row["store_id"]):
        raise HTTPException(400, "a roster store needs a code, address, label, or store_id")
    try:
        if body.get("id"):
            _c(client).table("agency_link_store").update(row).eq("org_id", org_id).eq("id", body["id"]).execute()
            return {"ok": True, "id": body["id"]}
        row["id"] = str(uuid.uuid4())
        row["created_by"] = who
        row["created_at"] = _now()
        r = _c(client).table("agency_link_store").insert(row).execute()
        return {"ok": True, "store": (r.data[0] if r.data else row)}
    except HTTPException:
        raise
    except Exception as e:
        _mig("220", e)


def delete_store(client, org_id, link_id, sid):
    _c(client).table("agency_link_store").delete().eq("org_id", org_id).eq("link_id", link_id).eq("id", sid).execute()
    return {"ok": True}


# ══ holdback rules ═══════════════════════════════════════════════════════════════════════════════════
def list_holdback_rules(client, org_id, link_id):
    _get_link(client, org_id, link_id)
    try:
        rows = (_c(client).table("agency_holdback_rule").select("*").eq("org_id", org_id)
                .eq("link_id", link_id).order("priority").execute().data) or []
    except Exception as e:
        _mig("220", e)
    return {"ok": True, "rules": rows}


def upsert_holdback_rule(client, org_id, link_id, body, who=None):
    _get_link(client, org_id, link_id)
    sk = (body.get("scope_kind") or "all").strip()
    if sk not in HOLDBACK_SCOPES:
        raise HTTPException(400, f"scope_kind must be one of {sorted(HOLDBACK_SCOPES)}")
    method = (body.get("method") or "percent").strip()
    if method not in ("flat", "percent"):
        raise HTTPException(400, "method must be 'flat' or 'percent'")
    row = {"org_id": org_id, "link_id": link_id, "scope_kind": sk,
           "scope_value": (body.get("scope_value") or None), "carrier_id": body.get("carrier_id") or None,
           "method": method, "value": AB._f(body.get("value")),
           "percent_basis": (body.get("percent_basis") or "scope_gross"),
           "flat_per": (body.get("flat_per") or "activation"),
           "priority": int(body.get("priority") or 100),
           "effective_start": body.get("effective_start") or None, "effective_end": body.get("effective_end") or None,
           "is_active": bool(body.get("is_active", True)), "notes": body.get("notes"), "updated_at": _now()}
    try:
        if body.get("id"):
            _c(client).table("agency_holdback_rule").update(row).eq("org_id", org_id).eq("id", body["id"]).execute()
            return {"ok": True, "id": body["id"]}
        row["id"] = str(uuid.uuid4())
        row["created_by"] = who
        row["created_at"] = _now()
        r = _c(client).table("agency_holdback_rule").insert(row).execute()
        return {"ok": True, "rule": (r.data[0] if r.data else row)}
    except HTTPException:
        raise
    except Exception as e:
        _mig("220", e)


def delete_holdback_rule(client, org_id, link_id, rid):
    _c(client).table("agency_holdback_rule").delete().eq("org_id", org_id).eq("link_id", link_id).eq("id", rid).execute()
    return {"ok": True}


def _accessory_classes(client, org_id):
    """Best-effort product-class options from the org's accessory config taxonomy (degrade to base classes)."""
    classes = ["device", "accessory"]
    try:
        from app.modules.commcalc.router import _accessory_config
        cfg = _accessory_config(client, org_id)
        for k in ("categories_list", "departments_list", "box_departments_list"):
            for v in (cfg.get(k) or []):
                if v and v not in classes:
                    classes.append(v)
    except Exception:
        pass
    return classes


def scope_options(client, org_id):
    """Picker prefill (RULE THREE / Q4): every holdback/margin scope value comes from REAL org data, never
    free text. ledger_bucket = the mig-071 canonical five; commission_component = carrier_commission's
    component columns; statement_line_type = DISTINCT observed line types across the org's statement/ledger
    rows; product_class = the accessory-config taxonomy. Degrades to empty lists if a table is absent."""
    def distinct(table, cols, cap=500):
        vals = set()
        try:
            rows = (_c(client).table(table).select(",".join(cols)).eq("org_id", org_id)
                    .limit(cap).execute().data) or []
            for r in rows:
                for cnm in cols:
                    v = (r.get(cnm) or "").strip() if isinstance(r.get(cnm), str) else r.get(cnm)
                    if v:
                        vals.add(v)
        except Exception:
            pass
        return vals
    stmt = set()
    stmt |= distinct("carrier_commission", ["activation_type", "sub_type"])
    stmt |= distinct("commission_ledger", ["order_type", "category"])
    stmt |= distinct("raw_ma_commission", ["activation_type", "sub_type"])
    carriers = []
    try:
        carriers = (_c(client).table("carrier").select("id,name").eq("org_id", org_id).execute().data) or []
    except Exception:
        carriers = []
    return {"ok": True,
            "ledger_bucket": LEDGER_BUCKETS,
            "commission_component": COMMISSION_COMPONENTS,
            "statement_line_type": sorted(stmt),
            "product_class": _accessory_classes(client, org_id),
            "carriers": [{"id": c.get("id"), "name": c.get("name")} for c in carriers]}


# ══ equipment margins ════════════════════════════════════════════════════════════════════════════════
def list_margins(client, org_id, link_id):
    _get_link(client, org_id, link_id)
    try:
        rows = (_c(client).table("agency_equipment_margin").select("*").eq("org_id", org_id)
                .eq("link_id", link_id).order("priority").execute().data) or []
    except Exception as e:
        _mig("220", e)
    return {"ok": True, "margins": rows}


def upsert_margin(client, org_id, link_id, body, who=None):
    _get_link(client, org_id, link_id)
    ev = (body.get("equip_class_value") or "").strip()
    if not ev:
        raise HTTPException(400, "equip_class_value is required (pick a product class)")
    method = (body.get("method") or "percent").strip()
    if method not in ("flat", "percent"):
        raise HTTPException(400, "method must be 'flat' or 'percent'")
    row = {"org_id": org_id, "link_id": link_id, "equip_class_kind": (body.get("equip_class_kind") or "product_class"),
           "equip_class_value": ev, "carrier_id": body.get("carrier_id") or None, "method": method,
           "value": AB._f(body.get("value")), "markup_basis": (body.get("markup_basis") or "cost"),
           "priority": int(body.get("priority") or 100),
           "effective_start": body.get("effective_start") or None, "effective_end": body.get("effective_end") or None,
           "is_active": bool(body.get("is_active", True)), "notes": body.get("notes"), "updated_at": _now()}
    try:
        if body.get("id"):
            _c(client).table("agency_equipment_margin").update(row).eq("org_id", org_id).eq("id", body["id"]).execute()
            return {"ok": True, "id": body["id"]}
        row["id"] = str(uuid.uuid4())
        row["created_by"] = who
        row["created_at"] = _now()
        r = _c(client).table("agency_equipment_margin").insert(row).execute()
        return {"ok": True, "margin": (r.data[0] if r.data else row)}
    except HTTPException:
        raise
    except Exception as e:
        _mig("220", e)


def delete_margin(client, org_id, link_id, mid):
    _c(client).table("agency_equipment_margin").delete().eq("org_id", org_id).eq("link_id", link_id).eq("id", mid).execute()
    return {"ok": True}


# ══ charges ══════════════════════════════════════════════════════════════════════════════════════════
def list_charges(client, org_id, link_id):
    _get_link(client, org_id, link_id)
    try:
        rows = (_c(client).table("agency_charge").select("*").eq("org_id", org_id)
                .eq("link_id", link_id).order("created_at").execute().data) or []
    except Exception as e:
        _mig("220", e)
    return {"ok": True, "charges": rows}


def upsert_charge(client, org_id, link_id, body, who=None):
    _get_link(client, org_id, link_id)
    label = (body.get("label") or "").strip()
    if not label:
        raise HTTPException(400, "label is required")
    method = (body.get("method") or "flat").strip()
    if method not in ("flat", "percent"):
        raise HTTPException(400, "method must be 'flat' or 'percent'")
    cadence = (body.get("cadence") or "monthly").strip()
    if cadence not in ("monthly", "one_time", "per_invoice"):
        raise HTTPException(400, "cadence must be 'monthly', 'one_time', or 'per_invoice'")
    row = {"org_id": org_id, "link_id": link_id, "label": label, "method": method,
           "value": AB._f(body.get("value")), "percent_basis": (body.get("percent_basis") or None),
           "cadence": cadence, "proration_mode": (body.get("proration_mode") or "default"),
           "link_store_id": body.get("link_store_id") or None,
           "effective_start": body.get("effective_start") or None, "effective_end": body.get("effective_end") or None,
           "is_active": bool(body.get("is_active", True)), "notes": body.get("notes"), "updated_at": _now()}
    try:
        if body.get("id"):
            _c(client).table("agency_charge").update(row).eq("org_id", org_id).eq("id", body["id"]).execute()
            return {"ok": True, "id": body["id"]}
        row["id"] = str(uuid.uuid4())
        row["created_by"] = who
        row["created_at"] = _now()
        r = _c(client).table("agency_charge").insert(row).execute()
        return {"ok": True, "charge": (r.data[0] if r.data else row)}
    except HTTPException:
        raise
    except Exception as e:
        _mig("220", e)


def delete_charge(client, org_id, link_id, cid):
    _c(client).table("agency_charge").delete().eq("org_id", org_id).eq("link_id", link_id).eq("id", cid).execute()
    return {"ok": True}


# ══ equipment transfers (intake) ══════════════════════════════════════════════════════════════════════
def list_transfers(client, org_id, link_id, period=None):
    _get_link(client, org_id, link_id)
    try:
        q = (_c(client).table("agency_equipment_transfer").select("*").eq("org_id", org_id).eq("link_id", link_id))
        if period:
            q = q.in_("period", _pvariants(period))
        rows = (q.order("created_at").execute().data) or []
    except Exception as e:
        _mig("222", e)
    return {"ok": True, "transfers": rows}


def _new_transfer(org_id, link_id, d, source, confirm_status, who):
    return {"id": str(uuid.uuid4()), "org_id": org_id, "link_id": link_id,
            "link_store_id": d.get("link_store_id") or None, "carrier_id": d.get("carrier_id") or None,
            "period": d.get("period"), "transfer_date": d.get("transfer_date") or None,
            "equip_class_value": (d.get("equip_class_value") or ""), "product_ref": d.get("product_ref"),
            "product_desc": d.get("product_desc"), "qty": AB._f(d.get("qty")), "unit_cost": AB._f(d.get("unit_cost")),
            "source": source, "doc_path": d.get("doc_path"), "doc_name": d.get("doc_name"),
            "ocr_confidence": d.get("ocr_confidence"), "ocr_model": d.get("ocr_model"),
            "confirm_status": confirm_status,
            "confirmed_by": (who if confirm_status == "confirmed" else None),
            "confirmed_at": (_now() if confirm_status == "confirmed" else None),
            "billed_invoice_id": None, "notes": d.get("notes"), "created_by": who,
            "created_at": _now(), "updated_at": _now()}


def add_transfer(client, org_id, link_id, body, who=None):
    """Manual entry → source='manual', confirm_status='confirmed' (a human typed it, so it's confirmed)."""
    _get_link(client, org_id, link_id)
    if not (body.get("equip_class_value") or "").strip():
        raise HTTPException(400, "equip_class_value is required")
    row = _new_transfer(org_id, link_id, body, "manual", "confirmed", who)
    try:
        r = _c(client).table("agency_equipment_transfer").insert(row).execute()
        return {"ok": True, "transfer": (r.data[0] if r.data else row)}
    except Exception as e:
        _mig("222", e)


def ingest_csv(client, org_id, link_id, records, who=None):
    """Feed stub: a list of dicts (parsed from a purchase/transfer CSV) → confirmed 'feed' rows. Attributes a
    row to a roster store by matching store_code. Returns the count inserted."""
    _get_link(client, org_id, link_id)
    roster = (list_stores(client, org_id, link_id).get("stores")) or []
    by_code = {str(s.get("store_code") or "").strip().lower(): s.get("id") for s in roster if s.get("store_code")}
    inserted = []
    for rec in (records or []):
        d = dict(rec)
        code = str(d.get("store_code") or "").strip().lower()
        if code and not d.get("link_store_id"):
            d["link_store_id"] = by_code.get(code)
        row = _new_transfer(org_id, link_id, d, "feed", "confirmed", who)
        try:
            _c(client).table("agency_equipment_transfer").insert(row).execute()
            inserted.append(row)
        except Exception as e:
            _mig("222", e)
    return {"ok": True, "count": len(inserted), "transfers": inserted}


def parse_csv_bytes(data):
    """Parse an uploaded CSV/XLSX into transfer records. Tolerant header mapping."""
    import pandas as pd
    try:
        df = pd.read_csv(io.BytesIO(data))
    except Exception:
        df = pd.read_excel(io.BytesIO(data))
    df.columns = [str(c).strip().lower() for c in df.columns]
    alias = {"class": "equip_class_value", "equip_class": "equip_class_value", "product": "product_desc",
             "description": "product_desc", "quantity": "qty", "cost": "unit_cost", "unit cost": "unit_cost",
             "store": "store_code", "store code": "store_code", "date": "transfer_date"}
    recs = []
    for _, r in df.iterrows():
        d = {}
        for col, val in r.items():
            key = alias.get(col, col)
            if key in ("equip_class_value", "product_desc", "product_ref", "store_code", "period", "transfer_date",
                       "qty", "unit_cost", "carrier_id"):
                d[key] = val if not (isinstance(val, float) and pd.isna(val)) else None
        recs.append(d)
    return recs


# ── OCR intake: outbound-AI event-loop safety (SEV-1 2026-07-30 bug class) ──────────────────────
# The Anthropic SDK defaults to a 600s timeout with 2 automatic retries (≈30 min worst case). A SYNC
# client call made from inside an `async def` FastAPI handler blocks the ONE uvicorn event loop for
# that whole window, so a single stalled request froze EVERY endpoint on 2026-07-30 (Ask-AI). This
# module uses the ASYNC client with an explicit short timeout + bounded retries. Env-tunable so the
# operator can widen/narrow without a code deploy; a garbage env value falls back to the default
# rather than breaking module import. Worst case for one OCR call =
# AGENCY_AI_TIMEOUT_S x (1 + AGENCY_AI_MAX_RETRIES), which stays well under Railway's 300s cutoff.
try:
    AGENCY_AI_TIMEOUT_S = max(1.0, float(os.getenv("AGENCY_AI_TIMEOUT_S") or 60))
except Exception:
    AGENCY_AI_TIMEOUT_S = 60.0
try:
    AGENCY_AI_MAX_RETRIES = max(0, int(os.getenv("AGENCY_AI_MAX_RETRIES") or 1))
except Exception:
    AGENCY_AI_MAX_RETRIES = 1
# Hard wall for the sync bridge below: the SDK's own budget plus a small margin, so the bridge can
# never outlive the request even if the SDK's timeout misfires.
_AGENCY_AI_WALL_S = AGENCY_AI_TIMEOUT_S * (1 + AGENCY_AI_MAX_RETRIES) + 5


async def _ocr_parse_transfer_async(data, filename, mimetype):
    """Async form of the OCR extraction — this is the real implementation; `_ocr_parse_transfer`
    is a sync bridge onto it. Same inputs, same (rows, model, confidence) 3-tuple, same degradation
    (no key → 'deterministic'; any failure → 'error'). An `async def` caller should await THIS
    function directly: awaiting is the only thing that actually hands the event loop back while the
    model thinks."""
    if not settings.ANTHROPIC_API_KEY:
        return ([], "deterministic", None)
    try:
        import base64
        # SEV-1 2026-07-30 — this MUST be the ASYNC client and MUST be awaited. Do NOT reintroduce
        # `Anthropic(` here: the sync client blocks the event loop for the entire HTTP call.
        from anthropic import AsyncAnthropic
        cli = AsyncAnthropic(api_key=settings.ANTHROPIC_API_KEY,
                             timeout=AGENCY_AI_TIMEOUT_S, max_retries=AGENCY_AI_MAX_RETRIES)
        media = "application/pdf" if (mimetype or "").endswith("pdf") or str(filename).lower().endswith(".pdf") else "image/png"
        block = {"type": "document" if media == "application/pdf" else "image",
                 "source": {"type": "base64", "media_type": media, "data": base64.b64encode(data).decode()}}
        prompt = ("Extract the equipment line items from this transfer/purchase invoice as STRICT JSON: "
                  '{"lines":[{"equip_class_value":"device|accessory","product_desc":"","qty":0,"unit_cost":0}]}. '
                  "equip_class_value must be 'device' for phones/handsets and 'accessory' otherwise. Return ONLY the JSON.")
        msg = await cli.messages.create(model=getattr(settings, "ACCOUNT_ENGINE_MODEL", "claude-3-5-sonnet-latest"),
                                        max_tokens=1500,
                                        messages=[{"role": "user", "content": [block, {"type": "text", "text": prompt}]}])
        text = "".join(b.text for b in msg.content if getattr(b, "type", "") == "text").strip()
        import json as _json
        text = text[text.find("{"): text.rfind("}") + 1]
        rows = (_json.loads(text) or {}).get("lines") or []
        return (rows, getattr(settings, "ACCOUNT_ENGINE_MODEL", "claude"), 0.9)
    except Exception:
        return ([], "error", None)


def _ocr_parse_transfer(data, filename, mimetype):
    """OCR a vendor/transfer invoice into transfer rows via Claude (accounts-module ANTHROPIC precedent).
    Returns (rows, model, confidence). Degrades to ([], 'deterministic', None) with no key / on any error —
    callers then create nothing and surface the notice. Kept separable so the row-landing path is testable
    without the live API (the proof harness stubs this).

    SYNC BRIDGE onto `_ocr_parse_transfer_async` — the signature and return contract are unchanged so
    existing sync callers keep working. Off the event loop (scripts, a FastAPI threadpool worker) this
    runs the coroutine directly. Called from a thread that already has a running loop, it runs the
    coroutine on a private worker loop and waits with a hard wall: that does NOT free the caller's loop
    (only `await _ocr_parse_transfer_async(...)` at the call site can), but it caps the stall at
    ~AGENCY_AI_TIMEOUT_S x (1 + AGENCY_AI_MAX_RETRIES) instead of the SDK's ~30 minutes."""
    try:
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            return asyncio.run(_ocr_parse_transfer_async(data, filename, mimetype))
        ex = concurrent.futures.ThreadPoolExecutor(max_workers=1, thread_name_prefix="agency-ocr")
        try:
            fut = ex.submit(lambda: asyncio.run(_ocr_parse_transfer_async(data, filename, mimetype)))
            return fut.result(timeout=_AGENCY_AI_WALL_S)
        finally:
            ex.shutdown(wait=False)      # never block the caller waiting on a timed-out worker
    except Exception:
        return ([], "error", None)


def _upload_agency_doc(link_id, filename, data, content_type):
    """Best-effort put into the agency-docs bucket (N1). Returns (path|None, ok, notice). Degrades cleanly:
    if the bucket is not provisioned yet, returns (None, False, notice) so the caller keeps working without
    retaining the file."""
    safe = (filename or "file").replace("/", "_")
    path = f"agency/{link_id}/{uuid.uuid4().hex}_{safe}"
    try:
        c = get_supabase()
        try:
            c.storage.get_bucket(AGENCY_BUCKET)
        except Exception:
            c.storage.create_bucket(AGENCY_BUCKET)   # may fail without infra perms → degrade below
        c.storage.from_(AGENCY_BUCKET).upload(path, data, {"content-type": content_type or "application/octet-stream"})
        return (path, True, None)
    except Exception:
        return (None, False, "agency-docs bucket not provisioned — file not retained (NEEDS CORE). "
                             "Parsed rows were still created for confirmation.")


def ingest_ocr(client, org_id, link_id, period, rows, doc_path, doc_name, model, confidence, who=None):
    """Land OCR-parsed rows as confirm_status='unconfirmed' (N3: must be human-confirmed before billing)."""
    _get_link(client, org_id, link_id)
    inserted = []
    for rec in (rows or []):
        d = dict(rec)
        d["period"] = d.get("period") or period
        d["doc_path"] = doc_path
        d["doc_name"] = doc_name
        d["ocr_model"] = model
        d["ocr_confidence"] = confidence if d.get("ocr_confidence") is None else d.get("ocr_confidence")
        row = _new_transfer(org_id, link_id, d, "ocr", "unconfirmed", who)
        try:
            _c(client).table("agency_equipment_transfer").insert(row).execute()
            inserted.append(row)
        except Exception as e:
            _mig("222", e)
    return {"ok": True, "count": len(inserted), "transfers": inserted}


def confirm_transfer(client, org_id, tid, decision, who=None):
    """Set a transfer's confirm_status (N3). decision ∈ {'confirm','reject'}. RBAC is applied by the router
    wrapper (the 'agency' setting gate); this is the state transition only. A transfer already consumed by an
    issued invoice cannot be un-confirmed."""
    try:
        rows = (_c(client).table("agency_equipment_transfer").select("*").eq("org_id", org_id).eq("id", tid)
                .limit(1).execute().data) or []
    except Exception as e:
        _mig("222", e)
    if not rows:
        raise HTTPException(404, "transfer not found")
    t = rows[0]
    if t.get("billed_invoice_id"):
        raise HTTPException(400, "already billed on an issued invoice — void that invoice first")
    status = "confirmed" if decision == "confirm" else "rejected"
    patch = {"confirm_status": status, "confirmed_by": who, "confirmed_at": _now(), "updated_at": _now()}
    _c(client).table("agency_equipment_transfer").update(patch).eq("org_id", org_id).eq("id", tid).execute()
    return {"ok": True, "confirm_status": status}


# ══ invoices ═════════════════════════════════════════════════════════════════════════════════════════
def _get_invoice(client, org_id, invoice_id):
    try:
        rows = (_c(client).table("agency_invoice").select("*").eq("org_id", org_id).eq("id", invoice_id)
                .limit(1).execute().data) or []
    except Exception as e:
        _mig("222", e)
    if not rows:
        raise HTTPException(404, "invoice not found")
    return rows[0]


def generate_invoice(client, org_id, link_id, period, who=None):
    """C3: draft delete-then-recompute for (link, period). A non-draft invoice is immutable (no-op). Consumes
    CONFIRMED, UNCONSUMED transfers (billed_invoice_id IS NULL, NOT narrowed by the transfer's own period →
    N3 roll-forward). Idempotent: a draft regeneration re-selects the same set (transfers are stamped at
    ISSUE, not draft)."""
    link = _get_link(client, org_id, link_id)
    try:
        existing = (_c(client).table("agency_invoice").select("*").eq("org_id", org_id).eq("link_id", link_id)
                    .in_("period", _pvariants(period)).execute().data) or []
    except Exception as e:
        _mig("222", e)
    draft = next((i for i in existing if i.get("status") == "draft"), None)
    if draft is None:
        # Only an ISSUED/PAID invoice is immutable and blocks a re-draft. A VOID invoice does NOT block
        # (a re-draft can supersede it) — symmetric with void releasing transfers + one_time (m2).
        blocking = next((i for i in existing if i.get("status") in ("issued", "paid")), None)
        if blocking:
            return {"ok": True, "immutable": True, "invoice": blocking,
                    "notice": f"a {blocking.get('status')} invoice already exists for this period — void it to re-draft"}

    # m1: before recomputing a draft, RELEASE any transfer stamped to THIS draft (e.g. a crashed prior issue
    # left partial stamps) so the recompute re-selects it — otherwise those units silently drop (under-bill).
    if draft is not None:
        try:
            _c(client).table("agency_equipment_transfer").update({"billed_invoice_id": None, "updated_at": _now()}
                                                                 ).eq("org_id", org_id).eq("billed_invoice_id", draft["id"]).execute()
        except Exception:
            pass

    # config + confirmed unconsumed transfers
    stores = (list_stores(client, org_id, link_id).get("stores")) or []
    charges = (list_charges(client, org_id, link_id).get("charges")) or []
    margins = (list_margins(client, org_id, link_id).get("margins")) or []
    try:
        transfers = (_c(client).table("agency_equipment_transfer").select("*").eq("org_id", org_id)
                     .eq("link_id", link_id).eq("confirm_status", "confirmed").execute().data) or []
    except Exception:
        transfers = []
    transfers = [t for t in transfers if not t.get("billed_invoice_id")]

    # one_time charges: bill only if not already on a NON-VOID other invoice for this link (any period). m2:
    # a one_time on a VOIDED invoice is excluded from billed_ids → it can bill again (symmetric with the
    # transfer release on void), so a voided charge is never silently lost.
    onetime = [c for c in charges if (c.get("cadence") == "one_time")]
    if onetime:
        billed_ids = set()
        try:
            all_inv = (_c(client).table("agency_invoice").select("id,status").eq("org_id", org_id)
                       .eq("link_id", link_id).execute().data) or []
            other_inv = [i.get("id") for i in all_inv
                         if i.get("status") != "void" and not (draft and i.get("id") == draft.get("id"))]
            if other_inv:
                ls = (_c(client).table("agency_invoice_line").select("source_id,invoice_id").eq("org_id", org_id)
                      .in_("invoice_id", other_inv).execute().data) or []
                billed_ids = {l.get("source_id") for l in ls}
        except Exception:
            billed_ids = set()
        for c in onetime:
            c["_bill_one_time"] = c.get("id") not in billed_ids

    payload = AB.compute_invoice_lines(link, stores, charges, margins, transfers, period)

    header = {
        "org_id": org_id, "link_id": link_id, "period": period,
        "period_start": payload["period_start"], "period_end": payload["period_end"], "status": "draft",
        "equipment_margin_total": payload["equipment_margin_total"], "store_fee_total": payload["store_fee_total"],
        "other_charge_total": payload["other_charge_total"], "holdback_total_memo": payload["holdback_total_memo"],
        "subtotal": payload["subtotal"], "taxable_snapshot": payload["taxable_snapshot"],
        "tax_rate_snapshot": payload["tax_rate_snapshot"], "tax_total": payload["tax_total"],
        "total": payload["total"], "regenerated_at": _now(), "updated_at": _now()}
    try:
        if draft is None:
            header["id"] = str(uuid.uuid4())
            header["created_by"] = who
            header["created_at"] = _now()
            _c(client).table("agency_invoice").insert(header).execute()
            invoice_id = header["id"]
        else:
            invoice_id = draft["id"]
            _c(client).table("agency_invoice").update(header).eq("org_id", org_id).eq("id", invoice_id).execute()
            _c(client).table("agency_invoice_line").delete().eq("org_id", org_id).eq("invoice_id", invoice_id).execute()
        for ln in payload["lines"]:
            row = dict(ln)
            row["id"] = str(uuid.uuid4())
            row["org_id"] = org_id
            row["invoice_id"] = invoice_id
            row["created_at"] = _now()
            _c(client).table("agency_invoice_line").insert(row).execute()
    except Exception as e:
        _mig("222", e)
    return {"ok": True, "invoice_id": invoice_id, "totals": {k: payload[k] for k in
            ("equipment_margin_total", "store_fee_total", "other_charge_total", "subtotal", "tax_total", "total")},
            "line_count": len(payload["lines"])}


def issue_invoice(client, org_id, invoice_id, who=None):
    """Freeze a draft → 'issued' and STAMP billed_invoice_id on the consumed transfers (idempotency)."""
    inv = _get_invoice(client, org_id, invoice_id)
    if inv.get("status") != "draft":
        raise HTTPException(400, f"only a draft can be issued (this is '{inv.get('status')}')")
    try:
        lines = (_c(client).table("agency_invoice_line").select("transfer_id,source_type").eq("org_id", org_id)
                 .eq("invoice_id", invoice_id).execute().data) or []
    except Exception:
        lines = []
    tids = [l.get("transfer_id") for l in lines if l.get("source_type") == "equipment_margin" and l.get("transfer_id")]
    # m4: a transfer on this draft may have been REJECTED/UN-confirmed since the draft was computed. Refuse
    # to issue a stale bill (409) — the operator must regenerate the draft first (which drops the line).
    if tids:
        try:
            trows = (_c(client).table("agency_equipment_transfer").select("id,confirm_status,billed_invoice_id")
                     .eq("org_id", org_id).in_("id", tids).execute().data) or []
        except Exception:
            trows = []
        stale = [t for t in trows if t.get("confirm_status") != "confirmed"
                 or (t.get("billed_invoice_id") and t.get("billed_invoice_id") != invoice_id)]
        if stale:
            raise HTTPException(409, "a transfer on this invoice is no longer confirmed (or was billed "
                                     "elsewhere) — regenerate the draft, then issue")
    for tid in tids:
        _c(client).table("agency_equipment_transfer").update({"billed_invoice_id": invoice_id, "updated_at": _now()}
                                                             ).eq("org_id", org_id).eq("id", tid).execute()
    _c(client).table("agency_invoice").update({"status": "issued", "issued_at": _now(), "updated_at": _now()}
                                              ).eq("org_id", org_id).eq("id", invoice_id).execute()
    return {"ok": True, "status": "issued", "consumed_transfers": len(tids)}


def void_invoice(client, org_id, invoice_id, who=None):
    """Void an invoice and RELEASE its consumed transfers (billed_invoice_id → NULL) so they bill again."""
    inv = _get_invoice(client, org_id, invoice_id)
    if inv.get("status") == "void":
        return {"ok": True, "status": "void"}
    try:
        _c(client).table("agency_equipment_transfer").update({"billed_invoice_id": None, "updated_at": _now()}
                                                             ).eq("org_id", org_id).eq("billed_invoice_id", invoice_id).execute()
    except Exception:
        pass
    _c(client).table("agency_invoice").update({"status": "void", "updated_at": _now()}
                                              ).eq("org_id", org_id).eq("id", invoice_id).execute()
    return {"ok": True, "status": "void"}


def list_invoices(client, org_id, link_id=None, period=None):
    try:
        q = _c(client).table("agency_invoice").select("*").eq("org_id", org_id)
        if link_id:
            q = q.eq("link_id", link_id)
        if period:
            q = q.in_("period", _pvariants(period))
        rows = (q.order("created_at").execute().data) or []
    except Exception as e:
        _mig("222", e)
    return {"ok": True, "invoices": rows}


def get_invoice(client, org_id, invoice_id):
    inv = _get_invoice(client, org_id, invoice_id)
    try:
        lines = (_c(client).table("agency_invoice_line").select("*").eq("org_id", org_id)
                 .eq("invoice_id", invoice_id).order("sort").execute().data) or []
    except Exception:
        lines = []
    return {"ok": True, "invoice": inv, "lines": lines}
