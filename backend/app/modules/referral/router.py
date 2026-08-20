"""Referral API — /api/v1/referral/*  (QR referrals, activation-gated commission, anti-fraud).

OWNER DIRECTIVE 2026-08-13 (sanjot@): staff create a referral → a QR goes to the REFERRING party → the
referred customer comes back, the QR is scanned, the sale is done → once the LINE IS ACTIVATED, an
approval goes to the referrer that they earned commission (USER-DEFINED amount, USER-DEFINED payout
date). "Must be FOOLPROOF so nobody can scam the system." Capture the referred customer's NAME, PHONE
and product interest as checkbox bubbles: Phone, Activations, Tablet, BYOD, Home Internet, Accessories.

Tables: core.referral_* (migration 850). See that migration's header for why `core` and not a `referral`
schema.

Design notes (identical doctrine to crm/router.py):
  • Every DECISION (token signing, the state machine, every fraud check, commission math) lives in
    `referral_core` as a pure function; this file is I/O and HTTP only. That is what makes the module
    provable offline in harness_referral.py instead of "verified" by watching production.
  • org_id is a QUERY PARAM on every non-public endpoint (AGENT_CONTRACT §2) — the tenant middleware
    rewrites it from the caller's JWT. Every read filters it and every insert stamps it.
  • Every table read is wrapped: a missing migration degrades to an empty list / a named 400, never a
    500 that takes an unrelated page down with it.
  • The PUBLIC redemption endpoints (GET/POST /referral/redeem/{token}) are anonymous and authenticate
    ONLY with the HMAC capability token (mirroring notify's /dl/{token}). Any failure — bad token,
    unknown, expired, already-redeemed — returns an IDENTICAL 404, so an anonymous probe learns nothing
    (no enumeration oracle). They must be on the tenant-middleware public allowlist prefix
    /api/v1/referral/redeem (boundary-matched), which is where they are registered.
"""
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Header, HTTPException, Response

from app.core.database import get_supabase
from app.core.config import settings
from app.core.schemas import LaxModel
from app.modules.referral import referral_core as core

router = APIRouter(prefix="/referral", tags=["Referral"])

ORG_ID = "00000000-0000-0000-0000-000000000001"   # house org; middleware rewrites the query param

# Which timestamp column a given target state stamps (stamped once, when the state is entered).
_STATE_STAMP = {
    "sent": "sent_at", "redeemed": "redeemed_at", "sale_logged": "sale_logged_at",
    "activated": "activated_at", "commission_pending": "submitted_at",
    "approved": "approved_at", "paid": "paid_at",
}
_CLOSING_STATES = {"expired", "rejected", "void", "flagged_fraud"}


# ── Request bodies (Item 15 Pydantic rollout — lax so legacy callers never break) ──────────────
class CreateReferralIn(LaxModel):
    referrer_name: Any = None
    referrer_phone: Any = None
    referrer_email: Any = None
    customer_name: Any = None
    customer_phone: Any = None
    products: Any = None
    commission_amount: Any = None   # handler-validated via float() → 400; keep Any so Pydantic won't 422
    payout_date: Any = None
    store_code: Any = None
    market: Any = None
    notes: Any = None


class ReferralNoteIn(LaxModel):
    note: Any = None


class LogSaleIn(LaxModel):
    sale_ref: Any = None
    note: Any = None


class ActivateReferralIn(LaxModel):
    activation_ref: Any = None
    note: Any = None


class ReferralReasonIn(LaxModel):
    reason: Any = None
    note: Any = None


class ApproveReferralIn(LaxModel):
    commission_amount: Any = None   # handler-validated via float() → 400
    payout_date: Any = None
    note: Any = None


class RedeemSubmitIn(LaxModel):
    customer_name: Any = None
    customer_phone: Any = None
    products: Any = None


def sb():
    """Referral tables live in core.* (migration 850) — the schema PostgREST already serves."""
    return get_supabase().schema("core")


def _now():
    return datetime.now(timezone.utc)


def _iso(dt):
    return dt.isoformat() if hasattr(dt, "isoformat") else dt


def _secret():
    """The HMAC secret for referral QR tokens. SAME fail-closed ladder as notify/download_token.py: a
    dedicated download secret when set, else the 2FA HMAC secret, else the service key (all high-entropy,
    backend-only). With NONE configured we return None — there is NO literal fallback constant, so a
    token can never be forged from a public/guessable secret; sign returns None and the QR endpoints
    degrade (no QR minted), verify always returns None (the public endpoint 404s)."""
    s = (settings.NOTIFY_DOWNLOAD_SECRET or settings.AUTH_2FA_SECRET or settings.SUPABASE_SERVICE_KEY)
    return s.encode() if s else None


# ══════════════════════════════════════════════════════════════════════════════════════════════
# Caller identity, permissions, scope  (mirrors crm/router.py so Referral introduces no new vocabulary)
# ══════════════════════════════════════════════════════════════════════════════════════════════
def _caller(authorization: str, x_active_org: str = ""):
    """{org_id, role, super_admin, perms, id, employee_id, store_code, market} or None."""
    try:
        from app.modules.core.router import _uid_from_token, _resolve_caller
        uid = _uid_from_token(authorization)
        if not uid:
            return None
        c = _resolve_caller(get_supabase(), uid, (x_active_org or "").strip() or None)
        if not c:
            return None
        try:
            from app.core.tenant_middleware import caller_app_user
            u = caller_app_user(uid, "id,org_id,employee_id,store_code,market,full_name,email") or {}
        except Exception:
            u = {}
        return {**c, "id": u.get("id"), "employee_id": u.get("employee_id"),
                "store_code": u.get("store_code"), "market": u.get("market"),
                "full_name": u.get("full_name"), "email": u.get("email")}
    except Exception:
        return None


def _keyset(authorization: str, org_id: str):
    """None = unrestricted; else the UPPER store keyset the caller may see. The same helper closing/pos/
    crm already use — Referral introduces no second scoping vocabulary."""
    try:
        from app.modules.storeops.router import scope_keyset
        return scope_keyset(authorization, org_id)
    except Exception:
        return None


def _in_keyset(keyset, *vals) -> bool:
    if keyset is None:
        return True
    return any(str(v or "").strip().upper() in keyset for v in vals)


def _is_manager(caller) -> bool:
    return bool(caller and (caller.get("super_admin")
                            or (caller.get("perms") or {}).get("scope") in ("all", "market")))


def _can_edit_settings(caller) -> bool:
    """Who may change referral program config. Company-wide scope or an explicit `settings.referral`
    grant. A caller we could not resolve is DENIED, never defaulted open (mirrors crm._can_edit_settings)."""
    if not caller:
        return False
    if caller.get("super_admin"):
        return True
    perms = caller.get("perms") or {}
    s = perms.get("settings") or {}
    if "referral" in s:
        return bool(s["referral"])
    return (perms.get("scope") == "all") or ((caller.get("role") or "").lower() == "admin")


def _require_settings(caller):
    if not _can_edit_settings(caller):
        raise HTTPException(403, "Changing the referral program setup is permission-restricted — you "
                                 "need the 'referral' settings permission or a company-wide role.")


def _can_approve(caller) -> bool:
    """Who may APPROVE / REJECT / mark PAID a referral payout. A manager (company-wide or market scope)
    or an explicit settings.referral grant — approving money is never a plain rep's call. Segregation of
    duties (never your own referral) is enforced separately by referral_core.approval_conflict."""
    if not caller:
        return False
    if caller.get("super_admin"):
        return True
    perms = caller.get("perms") or {}
    s = perms.get("settings") or {}
    if "referral" in s:
        return bool(s["referral"])
    return _is_manager(caller) or ((caller.get("role") or "").lower() == "admin")


def _require_approver(caller):
    if not _can_approve(caller):
        raise HTTPException(403, "Approving a referral payout needs a manager role or the 'referral' "
                                 "settings permission.")


# ══════════════════════════════════════════════════════════════════════════════════════════════
# Config + lazy tenant seeding
# ══════════════════════════════════════════════════════════════════════════════════════════════
def _seed(org_id: str) -> None:
    """Self-provision this tenant's referral_config on first touch. `core.seed_referral_defaults` is
    ON CONFLICT DO NOTHING, so calling it on every config read is safe and never clobbers edited config.
    Best-effort: an un-run migration must not 500 the page."""
    try:
        get_supabase().schema("core").rpc("seed_referral_defaults", {"p_org": org_id}).execute()
    except Exception:
        pass


def _config_row(org_id: str) -> dict:
    try:
        rows = (sb().table("referral_config").select("*").eq("org_id", org_id).limit(1)
                .execute().data) or []
        return rows[0] if rows else {}
    except Exception:
        return {}


def _cfg(org_id: str) -> dict:
    return core.resolve_config(_config_row(org_id))


@router.get("/config")
def get_config(org_id: str = ORG_ID, authorization: str = Header(default=""),
               x_active_org: str = Header(default="")):
    _seed(org_id)
    caller = _caller(authorization, x_active_org)
    cfg = _cfg(org_id)
    cfg["can_edit"] = _can_edit_settings(caller)
    cfg["can_approve"] = _can_approve(caller)
    cfg["allowed_products"] = core.ALLOWED_PRODUCTS
    cfg["qr_signing_configured"] = _secret() is not None
    cfg["me"] = {"employee_id": (caller or {}).get("employee_id"),
                 "store_code": (caller or {}).get("store_code"),
                 "market": (caller or {}).get("market"),
                 "is_manager": _is_manager(caller)}
    return cfg


@router.put("/config")
def put_config(body: dict, org_id: str = ORG_ID, authorization: str = Header(default=""),
               x_active_org: str = Header(default="")):
    _require_settings(_caller(authorization, x_active_org))
    allowed = {"default_commission_amount", "default_payout_offset_days", "qr_expiry_hours",
               "redemption_window_hours", "max_referrals_per_referrer", "velocity_window_days",
               "duplicate_match", "require_approval", "self_referral_block"}
    # Whitelist the payload but REJECT unknown keys loudly rather than dropping them silently — the CRM
    # config write hit "Saved ✓ while the DB never saw the field" twice ([[config-write-whitelist-silent-drop]]).
    unknown = [k for k in body.keys() if k not in allowed and k != "org_id"]
    if unknown:
        raise HTTPException(400, f"Unknown referral setting(s): {', '.join(sorted(unknown))}. "
                                 f"Nothing was saved.")
    row = {k: v for k, v in body.items() if k in allowed}
    if not row:
        raise HTTPException(400, "Nothing to update.")
    row["org_id"] = org_id
    row["updated_at"] = _iso(_now())
    try:
        sb().table("referral_config").upsert(row, on_conflict="org_id").execute()
    except Exception:
        raise HTTPException(400, "run migration 850 first (core.referral_config)")
    return get_config(org_id, authorization, x_active_org)


# ══════════════════════════════════════════════════════════════════════════════════════════════
# Read helpers
# ══════════════════════════════════════════════════════════════════════════════════════════════
def _fetch(table: str, org_id: str, select: str = "*", limit: int = 2000, **eq):
    try:
        q = sb().table(table).select(select).eq("org_id", org_id)
        for k, v in eq.items():
            q = q.eq(k, v)
        return q.limit(limit).execute().data or []
    except Exception:
        return []


def _get_referral(org_id: str, referral_id: str) -> dict:
    rows = _fetch("referral", org_id, limit=1, id=referral_id)
    if not rows:
        raise HTTPException(404, "Referral not found.")
    return rows[0]


def _get_referral_safe(org_id: str, referral_id: str):
    """Non-raising referral fetch for the approvals engine adapter (which has no HTTP context to map a
    404 onto). Returns the row or None."""
    try:
        rows = _fetch("referral", org_id, limit=1, id=referral_id)
        return rows[0] if rows else None
    except Exception:
        return None


def _decorate(r: dict) -> dict:
    cfg = None  # amounts decorated by caller where it holds cfg; keep display-only fields here
    return {
        **r,
        "referrer_display": core.referrer_display(r),
        "customer_display": core.customer_display(r),
        "status_label": core.STATE_LABEL.get(r.get("status"), r.get("status")),
    }


def _audit(org_id: str, referral_id: str, action: str, from_status=None, to_status=None,
           reason: str = "", caller=None, actor_kind: str = "staff", meta: dict = None) -> None:
    """Append to the immutable trail. Best-effort by design: the business action already succeeded, and
    losing the audit line is strictly better than rolling back real work."""
    try:
        sb().table("referral_audit").insert({
            "org_id": org_id, "referral_id": referral_id, "action": action,
            "from_status": from_status, "to_status": to_status, "reason": (reason or "")[:2000],
            "actor_employee_id": (caller or {}).get("employee_id"),
            "actor_app_user_id": (caller or {}).get("id"),
            "actor_kind": actor_kind, "meta": meta or {},
        }).execute()
    except Exception:
        pass


def _apply_transition(org_id: str, referral: dict, to: str, caller=None, actor_kind: str = "staff",
                      reason: str = "", extra: dict = None, meta: dict = None) -> dict:
    """The ONE place a referral's status changes. Refuses an illegal jump (referral_core.can_transition),
    stamps the state's timestamp, writes the audit row, and persists. Returns the merged row."""
    frm = referral.get("status")
    if not core.can_transition(frm, to):
        raise HTTPException(400, core.transition_error(frm, to))
    now = _now()
    upd = {"status": to, "updated_at": _iso(now)}
    stamp = _STATE_STAMP.get(to)
    if stamp and not referral.get(stamp):
        upd[stamp] = _iso(now)
    if to in _CLOSING_STATES:
        upd["closed_at"] = _iso(now)
    upd.update(extra or {})
    try:
        sb().table("referral").update(upd).eq("org_id", org_id).eq("id", referral["id"]).execute()
    except Exception as e:
        raise HTTPException(400, f"Could not update the referral: {e}")
    _audit(org_id, referral["id"], "transition", frm, to, reason, caller, actor_kind, meta)
    return {**referral, **upd}


def _apply_referral_decision(org_id: str, referral: dict, decision: str, *, caller=None, note=None,
                             commission_amount=None, payout_date=None) -> dict:
    """The ONE shared effect for a referral commission decision (commission_pending → approved | rejected):
    the approve/reject state transition, the commission math + payout-date resolution, the audit line, and
    the referrer notification. Called by BOTH the /referrals/{id}/approve + /reject endpoints AND the
    unified approvals engine adapter, so an inbox decision and a legacy-board decision book the SAME money
    (amount + payout date are never recomputed anywhere else). The caller (endpoint or engine adapter) is
    responsible for the RBAC + segregation-of-duties gate BEFORE calling this. Returns
    {referral, commission_amount?, payout_date?}.

    `decision`: 'approve' or 'deny'/'reject'. On approve, `commission_amount`/`payout_date` override the
    referral's stored/tenant defaults when provided (the inbox path passes neither → pure defaults)."""
    cfg = _cfg(org_id)
    if (decision or "").lower() == "approve":
        amount = commission_amount
        if amount in (None, ""):
            amount = core.compute_commission(referral, cfg)
        else:
            try:
                amount = round(max(0.0, float(amount)), 2)
            except (TypeError, ValueError):
                raise HTTPException(400, "Commission amount must be a number.")
        ref_for_date = {**referral, "payout_date": payout_date or referral.get("payout_date")}
        pd = core.resolve_payout_date(ref_for_date, cfg, _now())
        extra = {"commission_amount": amount, "payout_date": pd,
                 "approver_employee_id": (caller or {}).get("employee_id"),
                 "approver_app_user_id": (caller or {}).get("id")}
        r = _apply_transition(org_id, referral, "approved", caller, "staff",
                              note or f"Approved ${amount} payable {pd}", extra=extra,
                              meta={"amount": amount, "payout_date": pd})
        _audit(org_id, referral["id"], "approve", "commission_pending", "approved",
               f"Approved ${amount} payable {pd}", caller, "staff",
               {"amount": amount, "payout_date": pd})
        _notify_referrer_approved(org_id, r, amount, pd)
        return {"referral": r, "commission_amount": amount, "payout_date": pd}
    reason = note or "Referral rejected"
    r = _apply_transition(org_id, referral, "rejected", caller, "staff", reason)
    _audit(org_id, referral["id"], "reject", None, "rejected", reason, caller, "staff")
    return {"referral": r}


# ══════════════════════════════════════════════════════════════════════════════════════════════
# List + detail
# ══════════════════════════════════════════════════════════════════════════════════════════════
@router.get("/referrals")
def list_referrals(org_id: str = ORG_ID, status: str = "", store_code: str = "", market: str = "",
                   q: str = "", mine: bool = False, fraud_only: bool = False,
                   start: str = "", end: str = "", limit: int = 500,
                   authorization: str = Header(default=""), x_active_org: str = Header(default="")):
    _seed(org_id)
    caller = _caller(authorization, x_active_org)
    ks = _keyset(authorization, org_id)
    try:
        query = sb().table("referral").select("*").eq("org_id", org_id)
        if status:
            query = query.eq("status", status)
        if store_code:
            query = query.eq("store_code", store_code)
        if market:
            query = query.eq("market", market)
        if start:
            query = query.gte("created_at", start)
        if end:
            query = query.lte("created_at", f"{end}T23:59:59+00:00" if len(end) == 10 else end)
        rows = query.order("created_at", desc=True).limit(min(max(limit, 1), 2000)).execute().data or []
    except Exception:
        return {"rows": [], "total": 0, "note": "run migration 850 first (core.referral)"}

    if mine and (caller or {}).get("employee_id"):
        rows = [r for r in rows if r.get("created_by") == caller["employee_id"]]
    # Span narrowing — a referral with no store yet stays visible to scoped users (same posture as CRM
    # leads: a brand-new record nobody has routed must not be invisible to the people meant to work it).
    rows = [r for r in rows if not r.get("store_code") or _in_keyset(ks, r.get("store_code"))]
    if fraud_only:
        rows = [r for r in rows if r.get("fraud_flag") or r.get("status") == "flagged_fraud"]
    if q:
        needle = q.strip().lower()
        digits = core.normalize_phone(q)
        rows = [r for r in rows
                if needle in core.referrer_display(r).lower()
                or needle in core.customer_display(r).lower()
                or (digits and (core.normalize_phone(r.get("referrer_phone")) == digits
                                or core.normalize_phone(r.get("customer_phone")) == digits))
                or needle in str(r.get("referral_no") or "")]
    return {"rows": [_decorate(r) for r in rows], "total": len(rows)}


@router.get("/referrals/{referral_id}")
def get_referral(referral_id: str, org_id: str = ORG_ID, authorization: str = Header(default=""),
                 x_active_org: str = Header(default="")):
    r = _get_referral(org_id, referral_id)
    ks = _keyset(authorization, org_id)
    if r.get("store_code") and not _in_keyset(ks, r.get("store_code")):
        raise HTTPException(403, "This referral belongs to a store outside your access.")
    cfg = _cfg(org_id)
    try:
        audit = (sb().table("referral_audit").select("*").eq("org_id", org_id)
                 .eq("referral_id", referral_id).order("created_at", desc=True).limit(300)
                 .execute().data) or []
    except Exception:
        audit = []
    out = _decorate(r)
    out["commission_amount_effective"] = core.compute_commission(r, cfg)
    out["is_redeem_expired"] = core.is_redeem_expired(r, cfg, _now())
    caller = _caller(authorization, x_active_org)
    out["can_approve"] = _can_approve(caller) and not core.approval_conflict(
        (caller or {}).get("employee_id"), (caller or {}).get("id"), r)
    out["approval_conflict"] = core.approval_conflict(
        (caller or {}).get("employee_id"), (caller or {}).get("id"), r) if _can_approve(caller) else ""
    return {"referral": out, "audit": audit, "allowed_products": core.ALLOWED_PRODUCTS,
            "qr_signing_configured": _secret() is not None}


# ══════════════════════════════════════════════════════════════════════════════════════════════
# Create
# ══════════════════════════════════════════════════════════════════════════════════════════════
@router.post("/referrals")
def create_referral(body: CreateReferralIn, org_id: str = ORG_ID, authorization: str = Header(default=""),
                    x_active_org: str = Header(default="")):
    """Create a referral capturing the REFERRING party. The referred customer's details are captured
    later at the store when the QR is scanned (the public redeem endpoint). Runs the create-time fraud
    battery (self-referral / velocity) with whatever is known; a trip flags the referral rather than
    minting a QR for a farmer."""
    _seed(org_id)
    caller = _caller(authorization, x_active_org)
    cfg = _cfg(org_id)
    now = _now()

    referrer_phone = str(body.referrer_phone or "").strip()
    if not referrer_phone and not str(body.referrer_email or "").strip():
        raise HTTPException(400, "A referral needs the referring party's phone number (or email) so we "
                                 "can send them the QR.")

    # Product interest is captured at redeem, but a rep may pre-fill it; validate against the six bubbles.
    ok, products, rejected = core.validate_products(body.products)
    if not ok:
        raise HTTPException(400, f"Not a valid product option: {', '.join(rejected)}. "
                                 f"Choose from: {', '.join(core.ALLOWED_PRODUCTS)}.")

    # Commission amount / payout date are USER-DEFINED per referral, defaulting from config. Stored now
    # so the rep can set them up front; the actual payout is still approval-gated + activation-gated.
    amount = body.commission_amount
    try:
        amount = round(max(0.0, float(amount)), 2) if amount not in (None, "") else None
    except (TypeError, ValueError):
        raise HTTPException(400, "Commission amount must be a number.")

    row = {
        "org_id": org_id,
        "referrer_name": core.normalize_name(body.referrer_name) or None,
        "referrer_phone": referrer_phone or None,
        "referrer_email": (body.referrer_email or "").strip() or None,
        "customer_name": core.normalize_name(body.customer_name) or None,
        "customer_phone": (str(body.customer_phone or "").strip() or None),
        "products": products,
        "status": "created",
        "token_version": 1,
        "redeem_expires_at": None,   # stamped when the QR is sent
        "commission_amount": amount,
        "payout_date": body.payout_date or None,
        "store_code": body.store_code or (caller or {}).get("store_code"),
        "market": body.market or (caller or {}).get("market"),
        "created_by": (caller or {}).get("employee_id"),
        "created_by_app_user_id": (caller or {}).get("id"),
        "notes": body.notes,
    }
    try:
        created = (sb().table("referral").insert(row).execute().data or [{}])[0]
    except Exception as e:
        raise HTTPException(400, f"Could not save the referral: {e}")
    referral_id = created.get("id")
    _audit(org_id, referral_id, "create", None, "created", "Referral created", caller, "staff",
           {"products": products})

    # Create-time fraud battery. The customer phone is usually blank here (captured at redeem), so
    # self/duplicate typically pass; velocity is the live one — it stops a referrer from farming QRs.
    existing = _fetch("referral", org_id, limit=5000)
    reasons = core.run_fraud_checks(referrer_phone, row.get("customer_phone"), existing, cfg, now,
                                    exclude_id=referral_id)
    if reasons:
        joined = " ".join(reasons)
        _audit(org_id, referral_id, "fraud_check", "created", "flagged_fraud", joined, caller, "system",
               {"reasons": reasons})
        created = _apply_transition(org_id, created, "flagged_fraud", caller, "system", joined,
                                    extra={"fraud_flag": True, "fraud_reason": joined[:2000]})
        return {"referral": _decorate(created), "flagged": True, "reasons": reasons}

    return {"referral": _decorate(created), "flagged": False}


# ══════════════════════════════════════════════════════════════════════════════════════════════
# The QR — sign a token, render the image server-side (segno)
# ══════════════════════════════════════════════════════════════════════════════════════════════
def _redeem_url(referral: dict) -> str:
    tok = core.sign_token(referral.get("id"), referral.get("token_version"), _secret())
    if not tok:
        return ""
    return settings.APP_PUBLIC_URL.rstrip("/") + "/r/" + tok


def _qr_png(url: str) -> bytes:
    """Render a QR PNG for `url` server-side. segno is pure-python (added to requirements.txt) and is
    imported LAZILY so an environment without it degrades to "no image" instead of failing every import
    (the offline harness must import this module without segno present)."""
    import io
    import segno
    buf = io.BytesIO()
    segno.make(url, error="m").save(buf, kind="png", scale=8, border=2)
    return buf.getvalue()


@router.get("/referrals/{referral_id}/qr.png")
def qr_image(referral_id: str, org_id: str = ORG_ID, authorization: str = Header(default=""),
             x_active_org: str = Header(default="")):
    r = _get_referral(org_id, referral_id)
    ks = _keyset(authorization, org_id)
    if r.get("store_code") and not _in_keyset(ks, r.get("store_code")):
        raise HTTPException(403, "This referral belongs to a store outside your access.")
    url = _redeem_url(r)
    if not url:
        raise HTTPException(400, "QR signing is not configured on this server (no download secret set).")
    try:
        png = _qr_png(url)
    except Exception:
        raise HTTPException(500, "Could not render the QR image (segno not installed?).")
    return Response(content=png, media_type="image/png",
                    headers={"Cache-Control": "no-store"})


@router.get("/referrals/{referral_id}/redeem-url")
def redeem_url(referral_id: str, org_id: str = ORG_ID, authorization: str = Header(default=""),
               x_active_org: str = Header(default="")):
    """The signed redeem URL as text (so the UI can render its own QR / copy the link). Same access
    gate as the image."""
    r = _get_referral(org_id, referral_id)
    ks = _keyset(authorization, org_id)
    if r.get("store_code") and not _in_keyset(ks, r.get("store_code")):
        raise HTTPException(403, "This referral belongs to a store outside your access.")
    url = _redeem_url(r)
    if not url:
        raise HTTPException(400, "QR signing is not configured on this server (no download secret set).")
    return {"url": url}


# ══════════════════════════════════════════════════════════════════════════════════════════════
# Send the QR to the referrer  (created → sent, best-effort notify)
# ══════════════════════════════════════════════════════════════════════════════════════════════
def _deliver_qr(org_id: str, referral: dict, url: str) -> dict:
    """Best-effort delivery of the QR to the referring party via the notify channels. Returns
    {whatsapp, email} booleans. A delivery failure NEVER aborts the send — the rep can always show/print
    the QR from the detail page. Mirrors crm._notify's asyncio.run-in-a-sync-endpoint pattern."""
    out = {"whatsapp": False, "email": False}
    try:
        png = _qr_png(url)
    except Exception:
        png = b""
    body_text = (f"You earned a referral QR! Show this to your friend — when they come in and activate, "
                 f"you get paid. Redeem: {url}")
    # WhatsApp (best-effort; the referrer is a customer, so it is likely out of the 24h window → the
    # approved link template carries the URL).
    phone = referral.get("referrer_phone")
    if phone:
        try:
            import asyncio
            from app.modules.notify.channels import whatsapp_meta
            if whatsapp_meta.is_configured():
                asyncio.run(whatsapp_meta.send_document(
                    phone, png, "image/png", "referral-qr.png", body_text))
                out["whatsapp"] = True
        except Exception:
            pass
    email = referral.get("referrer_email")
    if email:
        try:
            import asyncio
            import base64 as _b64
            from app.modules.notify.channels import email_resend
            if email_resend.is_configured():
                img = (f'<img src="data:image/png;base64,{_b64.b64encode(png).decode()}" '
                       f'alt="Referral QR" style="width:220px;height:220px"/>') if png else ""
                html = (f"<div style='font-family:system-ui,sans-serif'>"
                        f"<h2>Your referral QR code</h2>"
                        f"<p>Show this to your friend. When they come in and their line is activated, "
                        f"you earn your referral reward.</p>{img}"
                        f"<p><a href='{url}'>Open the referral link</a></p></div>")
                attach = [("referral-qr.png", png, "image/png")] if png else []
                asyncio.run(email_resend.send_email(email, "Your referral QR code", html, attach))
                out["email"] = True
        except Exception:
            pass
    return out


@router.post("/referrals/{referral_id}/send")
def send_qr(referral_id: str, body: ReferralNoteIn = None, org_id: str = ORG_ID,
            authorization: str = Header(default=""), x_active_org: str = Header(default="")):
    """Mark the QR delivered to the referrer (created → sent) and stamp the redemption deadline. Fires a
    best-effort notify. Re-sending an already-sent referral just re-delivers without changing state."""
    body = body or ReferralNoteIn()
    caller = _caller(authorization, x_active_org)
    cfg = _cfg(org_id)
    r = _get_referral(org_id, referral_id)
    url = _redeem_url(r)
    if not url:
        raise HTTPException(400, "QR signing is not configured on this server (no download secret set).")

    if r.get("status") == "created":
        deadline = core.redeem_deadline(r.get("created_at") or _iso(_now()), cfg)
        r = _apply_transition(org_id, r, "sent", caller, "staff", "QR delivered to the referrer",
                              extra={"redeem_expires_at": _iso(deadline) if deadline else None})
    elif r.get("status") != "sent":
        raise HTTPException(400, core.transition_error(r.get("status"), "sent"))

    delivered = _deliver_qr(org_id, r, url)
    _audit(org_id, referral_id, "notify", r.get("status"), r.get("status"),
           f"QR delivery attempted (whatsapp={delivered['whatsapp']}, email={delivered['email']})",
           caller, "staff", delivered)
    return {"referral": _decorate(r), "delivered": delivered, "url": url}


# ══════════════════════════════════════════════════════════════════════════════════════════════
# Staff lifecycle steps: log-sale → activate → submit → approve → pay  (+ reject / void / flag)
# ══════════════════════════════════════════════════════════════════════════════════════════════
@router.post("/referrals/{referral_id}/log-sale")
def log_sale(referral_id: str, body: LogSaleIn = None, org_id: str = ORG_ID,
             authorization: str = Header(default=""), x_active_org: str = Header(default="")):
    body = body or LogSaleIn()
    caller = _caller(authorization, x_active_org)
    r = _get_referral(org_id, referral_id)
    extra = {}
    if body.sale_ref:
        extra["sale_ref"] = str(body.sale_ref)[:200]
    r = _apply_transition(org_id, r, "sale_logged", caller, "staff",
                          body.note or "Sale logged against the referral", extra=extra)
    return {"referral": _decorate(r)}


@router.post("/referrals/{referral_id}/activate")
def activate(referral_id: str, body: ActivateReferralIn = None, org_id: str = ORG_ID,
             authorization: str = Header(default=""), x_active_org: str = Header(default="")):
    """Mark the referred customer's line ACTIVATED — the gate that makes commission eligible. NOTE: this
    is a human attestation with an optional activation reference; there is no automated verification
    against a carrier activation feed yet (that data source does not exist in this system), so the
    approval step (a different person) is the real control, not this flag."""
    body = body or ActivateReferralIn()
    caller = _caller(authorization, x_active_org)
    r = _get_referral(org_id, referral_id)
    extra = {}
    if body.activation_ref:
        extra["activation_ref"] = str(body.activation_ref)[:200]
    r = _apply_transition(org_id, r, "activated", caller, "staff",
                          body.note or "Line activated for the referred customer", extra=extra)
    return {"referral": _decorate(r)}


@router.post("/referrals/{referral_id}/submit")
def submit_for_approval(referral_id: str, body: ReferralNoteIn = None, org_id: str = ORG_ID,
                        authorization: str = Header(default=""), x_active_org: str = Header(default="")):
    """Send an activated referral to the approval queue (activated → commission_pending). A rep can do
    this; the APPROVAL itself (money) is gated separately."""
    body = body or ReferralNoteIn()
    caller = _caller(authorization, x_active_org)
    r = _get_referral(org_id, referral_id)
    r = _apply_transition(org_id, r, "commission_pending", caller, "staff",
                          body.note or "Submitted for commission approval")
    # Intimation into the UNIFIED approvals inbox — the gated commission decision now surfaces centrally
    # (this module sends no approver email of its own, so let the engine notify). Best-effort.
    try:
        from app.modules.approvals import engine as _approvals
        cfg = _cfg(org_id)
        amt = core.compute_commission(r, cfg)
        _approvals.create_request(
            org_id, type="referral", source_table="referral", source_id=referral_id,
            title=f"Referral commission ${amt:.2f} — {core.referrer_display(r)}",
            summary=(f"Referred customer: {core.customer_display(r)}"),
            payload={"commission_amount": amt, "referral_no": r.get("referral_no"),
                     "referrer": core.referrer_display(r), "customer": core.customer_display(r)},
            requested_by=r.get("created_by"), requested_by_name=core.referrer_display(r),
            store_code=r.get("store_code"), market=r.get("market"), priority="normal")
    except Exception:
        pass
    return {"referral": _decorate(r)}


@router.post("/referrals/{referral_id}/approve")
def approve(referral_id: str, body: ApproveReferralIn = None, org_id: str = ORG_ID,
            authorization: str = Header(default=""), x_active_org: str = Header(default="")):
    """Approve the referral payout with a USER-DEFINED amount + payout date (commission_pending →
    approved). Permission-gated AND segregation-of-duties-gated: the approver can never be the rep who
    created the referral. Activation-gating is already enforced by the state machine — you cannot reach
    commission_pending without passing through `activated`."""
    body = body or ApproveReferralIn()
    caller = _caller(authorization, x_active_org)
    _require_approver(caller)
    r = _get_referral(org_id, referral_id)

    conflict = core.approval_conflict((caller or {}).get("employee_id"), (caller or {}).get("id"), r)
    if conflict:
        raise HTTPException(403, conflict)

    # User-defined amount + date fall back to the referral's stored values, then tenant defaults — all
    # via the ONE shared effect, so the unified Approvals inbox books identical money.
    out = _apply_referral_decision(org_id, r, "approve", caller=caller, note=body.note,
                                   commission_amount=body.commission_amount, payout_date=body.payout_date)
    try:
        from app.modules.approvals import engine as _approvals
        _approvals.sync_source_decision(org_id, type="referral", source_table="referral",
                                        source_id=referral_id, decision="approve",
                                        actor=(caller or {}).get("email"))
    except Exception:
        pass
    return {"referral": _decorate(out["referral"]), "commission_amount": out["commission_amount"],
            "payout_date": out["payout_date"]}


@router.post("/referrals/{referral_id}/pay")
def mark_paid(referral_id: str, body: ReferralNoteIn = None, org_id: str = ORG_ID,
              authorization: str = Header(default=""), x_active_org: str = Header(default="")):
    """Record the payout as PAID (approved → paid). Permission-gated. The state machine guarantees this
    is only reachable from `approved`, so nothing is ever paid that was not explicitly approved first."""
    body = body or ReferralNoteIn()
    caller = _caller(authorization, x_active_org)
    _require_approver(caller)
    r = _get_referral(org_id, referral_id)
    r = _apply_transition(org_id, r, "paid", caller, "staff",
                          body.note or "Referral payout marked paid")
    _audit(org_id, referral_id, "pay", "approved", "paid",
           body.note or "Marked paid", caller, "staff")
    return {"referral": _decorate(r)}


@router.post("/referrals/{referral_id}/reject")
def reject(referral_id: str, body: ReferralReasonIn = None, org_id: str = ORG_ID,
           authorization: str = Header(default=""), x_active_org: str = Header(default="")):
    body = body or ReferralReasonIn()
    caller = _caller(authorization, x_active_org)
    _require_approver(caller)
    r = _get_referral(org_id, referral_id)
    reason = body.reason or body.note or "Referral rejected"
    out = _apply_referral_decision(org_id, r, "deny", caller=caller, note=reason)
    try:
        from app.modules.approvals import engine as _approvals
        _approvals.sync_source_decision(org_id, type="referral", source_table="referral",
                                        source_id=referral_id, decision="deny",
                                        actor=(caller or {}).get("email"), note=reason)
    except Exception:
        pass
    return {"referral": _decorate(out["referral"])}


@router.post("/referrals/{referral_id}/void")
def void(referral_id: str, body: ReferralReasonIn = None, org_id: str = ORG_ID,
         authorization: str = Header(default=""), x_active_org: str = Header(default="")):
    body = body or ReferralReasonIn()
    caller = _caller(authorization, x_active_org)
    r = _get_referral(org_id, referral_id)
    reason = body.reason or body.note or "Referral voided"
    r = _apply_transition(org_id, r, "void", caller, "staff", reason)
    return {"referral": _decorate(r)}


@router.post("/referrals/{referral_id}/flag")
def flag_fraud(referral_id: str, body: ReferralReasonIn = None, org_id: str = ORG_ID,
               authorization: str = Header(default=""), x_active_org: str = Header(default="")):
    """Manually flag a referral as suspected fraud. Sets flagged_fraud WITH a reason (never a silent
    kill). Allowed from any live working state by the state machine."""
    body = body or ReferralReasonIn()
    caller = _caller(authorization, x_active_org)
    r = _get_referral(org_id, referral_id)
    reason = body.reason or body.note or "Manually flagged as suspected fraud"
    r = _apply_transition(org_id, r, "flagged_fraud", caller, "staff", reason,
                          extra={"fraud_flag": True, "fraud_reason": reason[:2000]})
    _audit(org_id, referral_id, "fraud_check", None, "flagged_fraud", reason, caller, "staff")
    return {"referral": _decorate(r)}


def _notify_referrer_approved(org_id: str, referral: dict, amount: float, payout_date: str) -> None:
    """Best-effort: tell the referring party their commission was approved. Owner directive: 'an
    approval goes to the referring party that they have generated commission'."""
    body = (f"Good news! Your referral was approved — you've earned ${amount:.2f}, payable on "
            f"{payout_date}. Thanks for the referral!")
    try:
        if referral.get("referrer_email"):
            import asyncio
            import html as _h
            from app.modules.notify.channels import email_resend
            if email_resend.is_configured():
                html = f"<div style='font-family:system-ui,sans-serif'><p>{_h.escape(body)}</p></div>"
                asyncio.run(email_resend.send_email(referral["referrer_email"],
                                                    "Your referral was approved 🎉", html, []))
    except Exception:
        pass
    try:
        if referral.get("referrer_phone"):
            import asyncio
            from app.modules.notify.channels import whatsapp_meta
            if whatsapp_meta.is_configured():
                asyncio.run(whatsapp_meta.send_document(
                    referral["referrer_phone"], b"", "text/plain", "approved.txt", body))
    except Exception:
        pass


# ══════════════════════════════════════════════════════════════════════════════════════════════
# Dashboard
# ══════════════════════════════════════════════════════════════════════════════════════════════
@router.get("/dashboard")
def dashboard(org_id: str = ORG_ID, authorization: str = Header(default=""),
              x_active_org: str = Header(default="")):
    _seed(org_id)
    ks = _keyset(authorization, org_id)
    cfg = _cfg(org_id)
    try:
        rows = _fetch("referral", org_id, limit=5000)
    except Exception:
        rows = []
    rows = [r for r in rows if not r.get("store_code") or _in_keyset(ks, r.get("store_code"))]
    summary = core.summarize(rows, cfg)
    summary["pending_approvals"] = [_decorate(r) for r in rows if r.get("status") == "commission_pending"]
    summary["pending_payouts"] = [_decorate(r) for r in rows if r.get("status") == "approved"]
    summary["fraud_flags"] = [_decorate(r) for r in rows
                              if r.get("status") == "flagged_fraud" or r.get("fraud_flag")]
    return summary


# ══════════════════════════════════════════════════════════════════════════════════════════════
# PUBLIC redemption — anonymous, token-gated, uniform 404 on any failure (no enumeration oracle)
# ══════════════════════════════════════════════════════════════════════════════════════════════
# These two routes are on the tenant-middleware public allowlist prefix /api/v1/referral/redeem. The
# TOKEN is the only auth. Any failure (bad/forged token, unknown id, version mismatch, expired,
# already-redeemed, wrong state) returns an IDENTICAL 404 with no detail — a probe learns nothing.
_NOT_FOUND = HTTPException(404, "Not found")


def _resolve_token(token: str):
    """Return the referral row iff the token authentically references a referral that is READY TO
    REDEEM (status == 'sent', matching token_version, not expired), else None. Never distinguishes the
    failure mode to the caller."""
    parsed = core.verify_token(token, _secret())
    if not parsed:
        return None
    rid, ver = parsed
    try:
        rows = (get_supabase().schema("core").table("referral").select("*").eq("id", rid).limit(1)
                .execute().data) or []
    except Exception:
        return None
    if not rows:
        return None
    r = rows[0]
    if int(r.get("token_version") or 1) != int(ver):
        return None
    if r.get("status") != "sent":            # single-use: past `sent` = already redeemed / closed
        return None
    if core.is_redeem_expired(r, core.resolve_config({}), _now()):
        return None
    return r


@router.get("/redeem/{token}")
def redeem_view(token: str):
    """What the customer's phone loads when they scan the QR. Returns ONLY what an anonymous customer
    needs to fill the intake form — the six product bubbles and (optionally) the store — and never the
    referrer's PII. Any failure is a uniform 404."""
    r = _resolve_token(token)
    if not r:
        raise _NOT_FOUND
    return {
        "ok": True,
        "allowed_products": core.ALLOWED_PRODUCTS,
        "prefill_products": r.get("products") or [],
        "store_code": r.get("store_code"),
    }


@router.post("/redeem/{token}")
def redeem_submit(token: str, body: RedeemSubmitIn):
    """The customer submits their NAME, PHONE and product bubbles at the store. Captures the intake,
    runs the anti-fraud battery WITH the customer phone now known, and moves the referral to `redeemed`
    (or `flagged_fraud` if a check trips). The response is UNIFORM regardless of the fraud outcome, so a
    scammer cannot probe the checks; a genuine customer just sees a thank-you either way. Any token
    failure is a uniform 404."""
    r = _resolve_token(token)
    if not r:
        raise _NOT_FOUND
    org_id = r.get("org_id")
    cfg = _cfg(org_id)
    now = _now()

    name = core.normalize_name(body.customer_name)
    phone = str(body.customer_phone or "").strip()
    if not phone or not core.normalize_phone(phone):
        raise HTTPException(400, "Please enter a valid phone number.")
    ok, products, rejected = core.validate_products(body.products)
    if not ok:
        raise HTTPException(400, f"Not a valid product option: {', '.join(rejected)}.")

    intake = {"customer_name": name or None, "customer_phone": phone, "products": products}
    # Persist the intake BEFORE deciding fraud, so the captured details are never lost even if the next
    # step trips — the audit trail then shows exactly what was submitted.
    try:
        sb().table("referral").update(intake).eq("org_id", org_id).eq("id", r["id"]).execute()
    except Exception:
        raise HTTPException(404, "Not found")   # uniform — never leak a DB error to the public caller
    r = {**r, **intake}
    _audit(org_id, r["id"], "redeem", "sent", "sent", "Customer intake captured", None, "customer",
           {"products": products, "phone_masked": core.mask_phone(phone)})

    # Anti-fraud with the customer phone now known: self-referral, duplicate customer / open referral,
    # velocity. An existing-customer check reuses the CRM/POS customer master conceptually.
    existing = _fetch("referral", org_id, limit=5000)
    is_customer = _is_existing_customer(org_id, phone)
    reasons = core.run_fraud_checks(r.get("referrer_phone"), phone, existing, cfg, now,
                                    is_existing_customer=is_customer, exclude_id=r["id"])
    if reasons:
        joined = " ".join(reasons)
        try:
            _apply_transition(org_id, r, "flagged_fraud", None, "system", joined,
                              extra={"fraud_flag": True, "fraud_reason": joined[:2000]})
        except HTTPException:
            pass
        _audit(org_id, r["id"], "fraud_check", "sent", "flagged_fraud", joined, None, "system",
               {"reasons": reasons})
        return {"ok": True}                    # UNIFORM response — no oracle

    try:
        _apply_transition(org_id, r, "redeemed", None, "customer", "Redeemed at the store")
    except HTTPException:
        pass
    return {"ok": True}


def _is_existing_customer(org_id: str, phone: str) -> bool:
    """Best-effort "is this already our customer?" against the POS customer master (conceptually the
    same lookup CRM's Customer 360 uses). A missing pos schema / un-run migration degrades to False —
    the duplicate-customer gate simply does not fire, rather than crashing the public endpoint."""
    p = core.normalize_phone(phone)
    if not p:
        return False
    try:
        rows = (get_supabase().schema("pos").table("customers")
                .select("id,phone_primary").eq("org_id", org_id)
                .ilike("phone_primary", f"%{p[-7:]}").limit(20).execute().data) or []
        return any(core.normalize_phone(c.get("phone_primary")) == p for c in rows)
    except Exception:
        return False
