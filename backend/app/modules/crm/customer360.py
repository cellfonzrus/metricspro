"""Customer 360 — "type a phone number, see everything we know about this customer".

OWNER DIRECTIVE 2026-08-12 (sanjot@): "once you enter a phone number it should give you total access
about that customer if they bought the phone from us, gated by permission who views".

The data already exists, scattered across four modules that were each built for a different question:
POS knows who the customer IS, raw_sales knows what they BOUGHT, the asset ledger knows which DEVICE
left the building, and activations know which PLAN they are on. Nothing joined them, because none of
them share a customer key — but they all carry a PHONE NUMBER, and that is the join.

THREE GATES, all enforced here on the server (the UI mirror is convenience, never the gate):
  1. module `crm`               — you can open the CRM at all
  2. data grant `customer_360`  — you can look a customer up (DEFAULT-CLOSED)
  3. data grant `customer_360_financial` — you can see the $ (margin/cost). Without it you still get
     what/when/where/who-sold-it, which is the operational answer; the money is WITHHELD EXPLICITLY
     (listed in `withheld`), never silently blanked, because a blank margin reads as "$0 margin".

Plus the caller's store/market span narrows which rows come back — the same `core/scope.py` keyset
every other module uses, not a new mechanism.

EVERY lookup writes a `core.crm_lookup_audit` row, including denials. Reading a customer's purchase
history is a commercial/PII event and it leaves a trail.
"""
from __future__ import annotations

from datetime import datetime, timezone

from app.modules.crm.pipeline_core import mask_phone, normalize_phone

# Columns that are MONEY and therefore ride the `customer_360_financial` grant. Anything not on this
# list is operational (what/when/where/who) and rides only the `customer_360` grant.
MONEY_FIELDS = {
    "gp", "ext_price", "cost", "unit_price", "extended_price", "discount", "list_price",
    "total", "subtotal", "tax_total", "discount_total", "balance", "selling_price",
    "owed_to_vip", "total_owed", "total_reimbursed", "reimbursement", "commissions",
    "monthly_fee", "deposit_amount", "trade_in_credit", "credit_limit",
}


# ══════════════════════════════════════════════════════════════════════════════════════════════
# Gates (pure over a resolved caller dict — same shape as commcalc's device_commission_allowed)
# ══════════════════════════════════════════════════════════════════════════════════════════════

def _has_grant(caller, key: str) -> bool:
    if not caller:
        return False
    if caller.get("super_admin"):
        return True
    perms = caller.get("perms") or {}
    if (perms.get("scope") == "all") or ((caller.get("role") or "").lower() == "admin"):
        return True
    mods = perms.get("modules") or {}
    if isinstance(mods, dict):
        if bool(mods.get(key)):
            return True
    elif key in mods:                       # roles UI may store modules as a list
        return True
    return bool((perms.get("data") or {}).get(key))


def customer_360_allowed(caller, cfg=None) -> bool:
    """Who may run a lookup. DEFAULT-CLOSED: super-admin, company-wide scope and `admin` pass; anyone
    else needs the explicit `customer_360` grant.

    A tenant that wants the lookup open to its whole floor sets `crm_config.lookup_requires_grant =
    false` — then any caller who can open the CRM can look a customer up. That is a deliberate
    tenant choice with a default of "no", not a code branch on a tenant name (RULE TWO)."""
    if cfg is not None and not cfg.get("lookup_requires_grant", True):
        return bool(caller)
    return _has_grant(caller, "customer_360")


def customer_360_financial_allowed(caller) -> bool:
    """Who may see the $ inside a lookup. Always DEFAULT-CLOSED — there is no tenant toggle for
    margin, because "everyone can see cost" is not a posture any tenant should reach by accident."""
    return _has_grant(caller, "customer_360_financial")


def strip_money(rows: list, allowed: bool) -> tuple:
    """Return (rows, withheld_field_names). When the caller lacks the money grant the $ keys are
    REMOVED (not zeroed) and named in `withheld`, so the UI can say "hidden" instead of showing a
    zero that reads as a real number."""
    if allowed:
        return rows, []
    withheld, out = set(), []
    for r in rows or []:
        clean = {}
        for k, v in (r or {}).items():
            if k in MONEY_FIELDS:
                withheld.add(k)
                continue
            clean[k] = v
        out.append(clean)
    return out, sorted(withheld)


def _section(rows=None, available=True, reason=None, withheld=None) -> dict:
    return {"available": bool(available), "reason": reason, "rows": rows or [],
            "count": len(rows or []), "withheld": withheld or []}


def _unavailable(reason: str) -> dict:
    return _section(available=False, reason=reason)


# ══════════════════════════════════════════════════════════════════════════════════════════════
# Section builders — each degrades to available:false, NEVER a 500 (AGENT_CONTRACT §5)
# ══════════════════════════════════════════════════════════════════════════════════════════════

def _in_span(value, keyset) -> bool:
    """`keyset is None` means the caller is unscoped (company-wide) — everything passes. An EMPTY
    set means "scoped to nothing", which is a real state and correctly returns nothing. Conflating
    the two is the bug recorded in [[span-keyset-vocabulary-split]]."""
    if keyset is None:
        return True
    v = str(value or "").strip().upper()
    return bool(v) and v in keyset


def identity_section(client, org_id: str, phone: str) -> dict:
    """The POS customer master — the closest thing this platform has to a Salesforce Account."""
    try:
        rows = (client.schema("pos").table("customers")
                .select("id,cust_number,account_type,company_name,first_name,last_name,email,"
                        "phone_primary,phone_secondary,city,state,zip,referral_source,is_active,"
                        "created_at")
                .eq("org_id", org_id)
                .or_(f"phone_primary.ilike.%{phone[-7:]},phone_secondary.ilike.%{phone[-7:]}")
                .limit(25).execute().data) or []
    except Exception as e:
        return _unavailable(f"Customer records are not reachable ({type(e).__name__}).")
    # The ilike prefilter is a cheap index-friendly narrowing; normalize properly in Python so a
    # differently-formatted number in the same row still matches (and a 7-digit collision does not).
    hits = [r for r in rows
            if normalize_phone(r.get("phone_primary")) == phone
            or normalize_phone(r.get("phone_secondary")) == phone]
    return _section(hits)


def purchases_section(client, org_id: str, phone: str, keyset, money_ok: bool) -> dict:
    """What they bought, from `commcalc.raw_sales`, matched on `mdn`.

    `mdn` and not `customer_no`: the customer number is empty on a large share of rows, which is the
    trap already recorded in [[autopay-marker-blank-department-trap]]. The phone is the reliable key.
    """
    try:
        rows = (client.schema("commcalc").table("raw_sales")
                .select("trans_id,trans_date,store,salesperson,department,category,product_desc,"
                        "contract_type,serial_1,mdn,ext_price,gp,customer,email,period")
                .eq("org_id", org_id).eq("mdn", phone)
                .order("trans_date", desc=True).limit(500).execute().data) or []
    except Exception as e:
        return _unavailable(f"Sales history is not reachable ({type(e).__name__}).")
    rows = [r for r in rows if _in_span(r.get("store"), keyset)]
    rows, withheld = strip_money(rows, money_ok)
    return _section(rows, withheld=withheld)


def pos_sales_section(client, org_id: str, customer_ids: list, keyset, money_ok: bool) -> dict:
    """Register receipts for this customer, when the tenant is running the built-in POS."""
    if not customer_ids:
        return _section([])
    try:
        sales = (client.schema("pos").table("sales")
                 .select("id,transaction_id,store_code,employee_id,receipt_type,status,total,"
                         "subtotal,tax_total,discount_total,is_activation_sale,created_at,voided_at")
                 .eq("org_id", org_id).in_("customer_id", customer_ids)
                 .order("created_at", desc=True).limit(200).execute().data) or []
    except Exception as e:
        return _unavailable(f"POS receipts are not reachable ({type(e).__name__}).")
    sales = [s for s in sales if _in_span(s.get("store_code"), keyset)]
    try:
        ids = [s["id"] for s in sales if s.get("id")][:100]
        items = (client.schema("pos").table("sale_items")
                 .select("sale_id,description,product_type,serial_number,qty,unit_price,"
                         "extended_price,discount")
                 .eq("org_id", org_id).in_("sale_id", ids).limit(1000).execute().data) or [] if ids else []
    except Exception:
        items = []
    by_sale = {}
    for it in items:
        by_sale.setdefault(it.get("sale_id"), []).append(it)
    for s in sales:
        line_rows, _ = strip_money(by_sale.get(s.get("id")) or [], money_ok)
        s["items"] = line_rows
    sales, withheld = strip_money(sales, money_ok)
    return _section(sales, withheld=withheld)


def activations_section(client, org_id: str, phone: str, keyset, money_ok: bool) -> dict:
    """Which line, which plan, which carrier — the answer to "what are they on today?"."""
    try:
        rows = (client.schema("pos").table("activations")
                .select("activation_number,activation_date,store_code,employee_id,carrier,"
                        "plan_code,plan_description,monthly_fee,contract_type,cell_number,"
                        "phone_model,phone_serial,sim_card,status,promotion_offered")
                .eq("org_id", org_id)
                .or_(f"cell_number.ilike.%{phone[-7:]},mobile_phone.ilike.%{phone[-7:]}")
                .order("activation_date", desc=True).limit(100).execute().data) or []
    except Exception as e:
        return _unavailable(f"Activations are not reachable ({type(e).__name__}).")
    rows = [r for r in rows
            if normalize_phone(r.get("cell_number")) == phone and _in_span(r.get("store_code"), keyset)]
    rows, withheld = strip_money(rows, money_ok)
    return _section(rows, withheld=withheld)


def devices_section(client, org_id: str, phone: str, imeis: list, keyset, money_ok: bool) -> dict:
    """The physical devices — matched on the ledger's own phone_number AND on any IMEI seen in the
    purchase history, because a device bought on one line often ends up on another."""
    rows, seen = [], set()
    try:
        by_phone = (client.schema("commcalc").table("asset_ledger")
                    .select("esn_imei,phone_number,device_model,category,status,date_sold,store,"
                            "market,acquired_date,selling_price,on_inventory")
                    .eq("org_id", org_id).eq("phone_number", phone).limit(50).execute().data) or []
    except Exception as e:
        return _unavailable(f"Device ledger is not reachable ({type(e).__name__}).")
    for r in by_phone:
        key = r.get("esn_imei")
        if key and key not in seen:
            seen.add(key)
            rows.append(r)
    clean_imeis = [i for i in (imeis or []) if i and i not in seen][:50]
    if clean_imeis:
        try:
            by_imei = (client.schema("commcalc").table("asset_ledger")
                       .select("esn_imei,phone_number,device_model,category,status,date_sold,store,"
                               "market,acquired_date,selling_price,on_inventory")
                       .eq("org_id", org_id).in_("esn_imei", clean_imeis).limit(50).execute().data) or []
            for r in by_imei:
                key = r.get("esn_imei")
                if key and key not in seen:
                    seen.add(key)
                    rows.append(r)
        except Exception:
            pass
    rows = [r for r in rows if _in_span(r.get("store"), keyset)]
    rows, withheld = strip_money(rows, money_ok)
    return _section(rows, withheld=withheld)


def crm_section(client, org_id: str, phone: str) -> dict:
    """This customer's own CRM history — leads, where they sit, who owns them."""
    try:
        rows = (client.schema("core").table("crm_lead")
                .select("id,lead_no,first_name,last_name,phone,email,status,stage_id,pipeline_id,"
                        "owner_employee_id,agency_id,store_code,value_estimate,score,priority,"
                        "created_at,last_activity_at,next_action_at,converted_customer_id")
                .eq("org_id", org_id).eq("phone_norm", phone)
                .order("created_at", desc=True).limit(50).execute().data) or []
    except Exception as e:
        return _unavailable(f"CRM history is not reachable ({type(e).__name__}).")
    return _section(rows)


def tickets_section(client, org_id: str, phone: str) -> dict:
    """Open support cases mentioning this number — best effort; the case table is not phone-keyed."""
    try:
        rows = (client.schema("storeops").table("support_case")
                .select("id,subject,status,priority,created_at")
                .eq("org_id", org_id).ilike("subject", f"%{phone[-7:]}%")
                .order("created_at", desc=True).limit(20).execute().data) or []
    except Exception as e:
        return _unavailable(f"Support cases are not reachable ({type(e).__name__}).")
    return _section(rows)


# ══════════════════════════════════════════════════════════════════════════════════════════════
# Next-best-action — the reason a lookup is a SALES tool and not just a search box
# ══════════════════════════════════════════════════════════════════════════════════════════════

def _months_since(value, now: datetime):
    from app.modules.crm.pipeline_core import _dt
    d = _dt(value)
    if d is None:
        return None
    return int((now - d).days / 30.44)


def suggested_actions(sections: dict, now: datetime) -> list:
    """Concrete, one-click next steps derived from what the sections actually found. Each carries a
    `lead` payload the UI posts straight to /crm/leads with the customer pre-filled."""
    out = []
    devices = (sections.get("devices") or {}).get("rows") or []
    purchases = (sections.get("purchases") or {}).get("rows") or []
    activations = (sections.get("activations") or {}).get("rows") or []
    crm = (sections.get("crm") or {}).get("rows") or []

    if any((l.get("status") or "open") == "open" for l in crm):
        out.append({"key": "open_lead", "severity": "info",
                    "label": "There is already an open lead for this number",
                    "detail": "Work the existing lead instead of creating a second one."})

    newest = None
    for d in devices:
        m = _months_since(d.get("date_sold") or d.get("acquired_date"), now)
        if m is not None and (newest is None or m < newest):
            newest = m
    if newest is not None and newest >= 20:
        out.append({"key": "upgrade", "severity": "opportunity",
                    "label": f"Device is about {newest} months old — upgrade candidate",
                    "detail": "Most customers are eligible and ready to upgrade around 24 months.",
                    "lead": {"interest_key": "upgrade"}})

    if purchases and not activations:
        out.append({"key": "no_activation", "severity": "info",
                    "label": "Bought from us, but no activation on record",
                    "detail": "Confirm which line this device is on — it may be on another number.",
                    "lead": {"interest_key": "new_line"}})

    has_accessory = any("accessor" in str(p.get("department") or p.get("category") or "").lower()
                        for p in purchases)
    if purchases and not has_accessory:
        out.append({"key": "accessory", "severity": "opportunity",
                    "label": "No accessory on record",
                    "detail": "Case, screen protection and charging are the easiest attach.",
                    "lead": {"interest_key": "accessory"}})

    if len(activations) == 1:
        out.append({"key": "add_line", "severity": "opportunity",
                    "label": "Single line — add-a-line opportunity",
                    "detail": "Ask who else is on their plan or in the household.",
                    "lead": {"interest_key": "add_line"}})

    if not purchases and not activations and not devices:
        out.append({"key": "unknown", "severity": "info",
                    "label": "No purchase history for this number",
                    "detail": "New to us — log them as a lead so the follow-up starts.",
                    "lead": {"interest_key": "new_line"}})
    return out


# ══════════════════════════════════════════════════════════════════════════════════════════════
# Audit
# ══════════════════════════════════════════════════════════════════════════════════════════════

def write_audit(client, org_id: str, *, phone, caller, allowed: bool, sections: dict,
                matched_customer_id=None, matched_lead_id=None) -> None:
    """One row per lookup, allowed or denied. Best-effort: an audit failure must never be the reason
    a rep can't help a customer — but it is logged loudly enough to notice if it starts failing."""
    try:
        client.schema("core").table("crm_lookup_audit").insert({
            "org_id": org_id,
            "actor_app_user_id": (caller or {}).get("id"),
            "actor_employee_id": (caller or {}).get("employee_id"),
            "phone_masked": mask_phone(phone),
            "matched_customer_id": matched_customer_id,
            "matched_lead_id": matched_lead_id,
            "allowed": bool(allowed),
            "sections": {k: {"available": v.get("available"), "count": v.get("count"),
                             "withheld": v.get("withheld")}
                         for k, v in (sections or {}).items()},
        }).execute()
    except Exception:
        pass


def build_360(client, org_id: str, raw_phone: str, *, caller, money_ok: bool, keyset) -> dict:
    """Assemble every section for a phone number. The caller has already passed the lookup gate."""
    phone = normalize_phone(raw_phone)
    now = datetime.now(timezone.utc)
    if not phone:
        return {"phone": None, "error": "Enter at least 7 digits of a phone number.",
                "sections": {}, "suggested_actions": []}

    ident = identity_section(client, org_id, phone)
    customer_ids = [r.get("id") for r in ident.get("rows") or [] if r.get("id")]
    purchases = purchases_section(client, org_id, phone, keyset, money_ok)
    imeis = [r.get("serial_1") for r in purchases.get("rows") or [] if r.get("serial_1")]

    sections = {
        "identity": ident,
        "crm": crm_section(client, org_id, phone),
        "purchases": purchases,
        "pos_sales": pos_sales_section(client, org_id, customer_ids, keyset, money_ok),
        "activations": activations_section(client, org_id, phone, keyset, money_ok),
        "devices": devices_section(client, org_id, phone, imeis, keyset, money_ok),
        "tickets": tickets_section(client, org_id, phone),
    }

    first_purchase = min((p.get("trans_date") for p in purchases.get("rows") or []
                          if p.get("trans_date")), default=None)
    last_purchase = max((p.get("trans_date") for p in purchases.get("rows") or []
                         if p.get("trans_date")), default=None)
    name = None
    for r in ident.get("rows") or []:
        name = " ".join(x for x in [r.get("first_name"), r.get("last_name")] if x) or r.get("company_name")
        break
    if not name:
        for p in purchases.get("rows") or []:
            if p.get("customer"):
                name = p["customer"]
                break

    summary = {
        "name": name,
        "is_customer": bool(customer_ids) or bool(purchases.get("rows")),
        "purchase_count": purchases.get("count") or 0,
        "device_count": sections["devices"].get("count") or 0,
        "line_count": sections["activations"].get("count") or 0,
        "open_leads": sum(1 for l in sections["crm"].get("rows") or []
                          if (l.get("status") or "open") == "open"),
        "first_purchase": first_purchase,
        "last_purchase": last_purchase,
        "lifetime_value": (round(sum(float(p.get("ext_price") or 0)
                                     for p in purchases.get("rows") or []), 2) if money_ok else None),
    }

    matched_lead_id = next((l.get("id") for l in sections["crm"].get("rows") or []), None)
    write_audit(client, org_id, phone=raw_phone, caller=caller, allowed=True, sections=sections,
                matched_customer_id=customer_ids[0] if customer_ids else None,
                matched_lead_id=matched_lead_id)

    return {
        "phone": phone,
        "phone_masked": mask_phone(raw_phone),
        "summary": summary,
        "sections": sections,
        "money_visible": bool(money_ok),
        "suggested_actions": suggested_actions(sections, now),
    }
