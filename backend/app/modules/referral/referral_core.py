"""Referral core — the PURE logic behind the QR-referral system and its anti-fraud spine.

OWNER DIRECTIVE 2026-08-13 (sanjot@): staff create a referral → a QR goes to the REFERRING party → the
referred customer comes back, the QR is scanned, the sale is done → once the LINE IS ACTIVATED, an
approval goes to the referrer that they earned commission (USER-DEFINED amount, USER-DEFINED payout
date). "Must be FOOLPROOF so nobody can scam the system."

Everything here is a plain function over plain values: no database, no network, no clock of its own
(callers pass `now`), no config import (callers pass the resolved config and — for token signing — the
secret bytes). That is deliberate: the token signing, the legal-transition state machine, and every
anti-fraud check are exactly the code that must not be "verified" by eyeballing production. They live
here and are proven offline by `backend/harness_referral.py`. The router (`referral/router.py`) does
the I/O and calls into this module for every decision.

The four pillars of "foolproof", each a pure, tested function in this file:
  1. UNFORGEABLE / UNGUESSABLE / EXPIRING / SINGLE-USE token — sign_token / verify_token mirror
     notify/download_token.py: HMAC-SHA256 capability over exactly ONE referral id, constant-time
     compare, fail-closed when no secret. Expiry + single-use are enforced by the row (redeem_expires_at
     + status past `sent`), never encoded in the token, so a link can't be extended by re-signing.
  2. IDENTITY GATES — self_referral_conflict (referrer ≠ customer), duplicate_conflict (already a
     customer / already an open-or-paid referral), velocity_exceeded (farming cap).
  3. STATE MACHINE — can_transition(from, to): commission can never be paid before it is approved,
     never approved before the line is activated. Illegal jumps are refused.
  4. SEGREGATION OF DUTIES — approval_conflict: a rep can never approve their own referral's payout.

Conventions:
  • `now` is always an aware UTC datetime.
  • phone keys use normalize_phone(), byte-identical to core.crm_lead.phone_norm / crm.normalize_phone
    (mig 800) and to core.referral.*_phone_norm (mig 850).
  • employee ids are the BUSINESS ids (storeops.employees.employee_id, text), like CRM/closing/payroll.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import re
from datetime import datetime, timedelta, timezone


# ══════════════════════════════════════════════════════════════════════════════════════════════
# Config defaults (mirrored by the referral_config column defaults in migration 850)
# ══════════════════════════════════════════════════════════════════════════════════════════════
DEFAULT_CONFIG = {
    "default_commission_amount": 25.00,
    "default_payout_offset_days": 30,
    "qr_expiry_hours": 168,           # 7d — how long the signed QR stays scannable
    "redemption_window_hours": 72,    # how long after creation the customer may redeem
    "max_referrals_per_referrer": 10,  # velocity cap per referrer per window (0 = no cap)
    "velocity_window_days": 30,
    "duplicate_match": "phone",       # 'phone' | 'none'
    "require_approval": True,
    "self_referral_block": True,
}

_INT_KEYS = ("default_payout_offset_days", "qr_expiry_hours", "redemption_window_hours",
             "max_referrals_per_referrer", "velocity_window_days")


def resolve_config(row) -> dict:
    """Merge a referral_config row onto the defaults. A missing table / missing row (the migration has
    not run yet) yields the pure defaults — the module still works, it just isn't tunable. Garbage
    numerics fall back to their default; negatives clamp to 0; a bad duplicate_match degrades to 'phone'.
    """
    cfg = dict(DEFAULT_CONFIG)
    for k, v in (row or {}).items():
        if v is None:
            continue
        cfg[k] = v
    for k in _INT_KEYS:
        try:
            cfg[k] = max(0, int(cfg.get(k)))
        except (TypeError, ValueError):
            cfg[k] = DEFAULT_CONFIG[k]
    try:
        cfg["default_commission_amount"] = max(0.0, float(cfg.get("default_commission_amount")))
    except (TypeError, ValueError):
        cfg["default_commission_amount"] = DEFAULT_CONFIG["default_commission_amount"]
    if str(cfg.get("duplicate_match") or "").lower() not in ("phone", "none"):
        cfg["duplicate_match"] = "phone"
    cfg["require_approval"] = _as_bool(cfg.get("require_approval"), True)
    cfg["self_referral_block"] = _as_bool(cfg.get("self_referral_block"), True)
    return cfg


def _as_bool(v, default: bool) -> bool:
    if v is None:
        return default
    if isinstance(v, bool):
        return v
    return str(v).strip().lower() in ("1", "true", "t", "yes", "y", "on")


# ══════════════════════════════════════════════════════════════════════════════════════════════
# Identity normalization — the join key for self-referral + duplicate + velocity checks
# ══════════════════════════════════════════════════════════════════════════════════════════════
_DIGITS = re.compile(r"[^0-9]")


def normalize_phone(value) -> str:
    """The 10-digit national number — byte-identical to crm.normalize_phone() and the SQL generated
    column core.referral.*_phone_norm (mig 850). Rules, in order: strip to digits; < 7 digits → ""
    (refuse to half-match); exactly 11 starting with 1 → drop the US country code; MORE than 10 → keep
    the FIRST 10 (after any leading 1), because an extension is written at the END."""
    digits = _DIGITS.sub("", str(value or ""))
    if len(digits) < 7:
        return ""
    if len(digits) == 11 and digits[0] == "1":
        return digits[1:]
    if len(digits) > 10:
        return digits[1:11] if digits[0] == "1" else digits[:10]
    return digits


def mask_phone(value) -> str:
    """'••••0134' — what an audit line stores instead of the full number."""
    n = normalize_phone(value)
    return f"••••{n[-4:]}" if n else "••••"


def normalize_name(value) -> str:
    """Collapse internal whitespace and trim. Names are captured by hand at a busy counter, so
    "  john   smith " and "John Smith" must not read as two different people downstream."""
    return " ".join(str(value or "").split()).strip()


def normalize_email(value) -> str:
    return str(value or "").strip().lower()


# ══════════════════════════════════════════════════════════════════════════════════════════════
# Product interest — the six 'bubble' options, verbatim from the directive
# ══════════════════════════════════════════════════════════════════════════════════════════════
# Order is the display order of the checkbox bubbles. Storage is the canonical label (the value here).
ALLOWED_PRODUCTS = ["Phone", "Activations", "Tablet", "BYOD", "Home Internet", "Accessories"]

# Accept a few obvious spellings/keys from a form and canonicalize; anything else is rejected, so a
# forged public POST cannot smuggle in an eighth product category.
_PRODUCT_ALIASES = {
    "phone": "Phone", "phones": "Phone",
    "activation": "Activations", "activations": "Activations",
    "tablet": "Tablet", "tablets": "Tablet",
    "byod": "BYOD", "bring your own device": "BYOD",
    "home internet": "Home Internet", "home_internet": "Home Internet",
    "internet": "Home Internet", "fwa": "Home Internet",
    "accessory": "Accessories", "accessories": "Accessories",
}


def normalize_products(values) -> list:
    """Canonicalize + DEDUPE a submitted product list to the allowed set, preserving display order.
    Unknown entries are dropped silently here (validate_products is the loud gate); this is the
    idempotent normalizer used before storage. Not a set → [] (a scalar is not a multi-select)."""
    if isinstance(values, (str, bytes)) or not hasattr(values, "__iter__"):
        return []
    seen, out = set(), []
    for v in values:
        canon = _PRODUCT_ALIASES.get(normalize_name(v).lower())
        if canon and canon not in seen:
            seen.add(canon)
            out.append(canon)
    return [p for p in ALLOWED_PRODUCTS if p in seen]  # stable in the canonical bubble order


def validate_products(values) -> tuple:
    """(ok, normalized, rejected). `rejected` names anything that was not one of the six bubbles, so
    the caller can 400 loudly ("Home Innternet" is a typo, not silently dropped intent)."""
    rejected = []
    if isinstance(values, (str, bytes)) or not hasattr(values or [], "__iter__"):
        return (False, [], ["(not a list)"]) if values not in (None, [], "") else (True, [], [])
    for v in values or []:
        if _PRODUCT_ALIASES.get(normalize_name(v).lower()) is None:
            rejected.append(str(v))
    return (len(rejected) == 0, normalize_products(values), rejected)


# ══════════════════════════════════════════════════════════════════════════════════════════════
# 1. THE TOKEN — unforgeable / unguessable / expiring / single-use  (mirrors notify/download_token.py)
# ══════════════════════════════════════════════════════════════════════════════════════════════
# A token is an HMAC-SHA256 CAPABILITY over exactly ONE referral id + its token_version. It grants
# redemption of that single referral and nothing else. Expiry (redeem_expires_at) and single-use
# (status flips past `sent` on redeem) are enforced by the ROW at redeem time, NEVER encoded in the
# token, so a link can never be extended by re-signing and a re-issue (bump token_version) revokes it.
_MSG_PREFIX = b"referral-redeem:"


def _b64u(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).decode().rstrip("=")


def _b64u_dec(s: str) -> bytes:
    pad = "=" * (-len(s) % 4)
    return base64.urlsafe_b64decode(s + pad)


def _sig(payload: str, secret: bytes) -> str:
    msg = _MSG_PREFIX + payload.encode()
    return _b64u(hmac.new(secret, msg, hashlib.sha256).digest())


def sign_token(referral_id, token_version, secret):
    """token = b64url("<id>:<ver>") + "." + b64url(HMAC(secret, "referral-redeem:<id>:<ver>")).

    The id + version ride in the token (encoded) so the endpoint needs no lookup table; the signature
    makes it unforgeable. Returns None when NO secret is configured (fail closed) — the caller then has
    no QR to deliver and says so, rather than minting a guessable one from a public constant."""
    if not secret:
        return None
    payload = f"{referral_id}:{int(token_version or 1)}"
    return f"{_b64u(payload.encode())}.{_sig(payload, secret)}"


def verify_token(token, secret):
    """Return (referral_id, token_version) iff `token` is a well-formed, correctly-signed referral
    token, else None. Constant-time on the signature (hmac.compare_digest); never raises; returns None
    when NO secret is configured (fail closed). Expiry/single-use/revocation are checked by the caller
    against the row — this only proves the token authentically references that one id+version."""
    try:
        if not secret:
            return None
        body, dot, sig = (token or "").partition(".")
        if not body or not dot or not sig:
            return None
        payload = _b64u_dec(body).decode()
        rid, _, ver = payload.partition(":")
        if not rid or not ver:
            return None
        if not hmac.compare_digest(sig, _sig(payload, secret)):
            return None
        return rid, int(ver)
    except Exception:
        return None


# ══════════════════════════════════════════════════════════════════════════════════════════════
# Expiry — redeem deadline is a ROW fact, computed at creation from config
# ══════════════════════════════════════════════════════════════════════════════════════════════
def redeem_deadline(created_at, cfg: dict):
    """The instant after which a QR may no longer be redeemed = created_at + the SHORTER of
    qr_expiry_hours and redemption_window_hours (both are real config knobs; whichever is stricter
    wins, so neither can be widened past the other). Returns an aware UTC datetime, or None if the
    creation time can't be parsed. A 0/absent hour value disables that particular bound."""
    base = _dt(created_at)
    if base is None:
        return None
    bounds = [h for h in (cfg.get("qr_expiry_hours"), cfg.get("redemption_window_hours"))
              if _pos_int(h) > 0]
    if not bounds:
        return None                      # both disabled → no expiry
    return base + timedelta(hours=min(_pos_int(b) for b in bounds))


def is_redeem_expired(referral: dict, cfg: dict, now: datetime) -> bool:
    """True once the redeem deadline has passed. Prefers the stored redeem_expires_at (stamped at
    create) and falls back to recomputing from created_at, so an old row without the column still
    expires. No parseable deadline at all → NOT expired (fail OPEN here is safe: the status machine and
    the token signature are the real gates; we do not want a clock-parse bug to silently kill every QR)."""
    deadline = _dt(referral.get("redeem_expires_at")) or redeem_deadline(referral.get("created_at"), cfg)
    return deadline is not None and now > deadline


# ══════════════════════════════════════════════════════════════════════════════════════════════
# 3. THE STATE MACHINE — legal transitions
# ══════════════════════════════════════════════════════════════════════════════════════════════
# created → sent → redeemed → sale_logged → activated → commission_pending → approved → paid
# Exception/terminal states: expired, rejected, void, flagged_fraud.
# INVARIANTS the map encodes (the headline money-safety rules):
#   • `paid` is reachable ONLY from `approved`  — you cannot pay before approval.
#   • `approved` is reachable ONLY from `commission_pending`, which is reachable ONLY from `activated`
#     — you cannot approve (or pay) before the line is activated.
#   • `flagged_fraud` can be entered from any live working state and only resolves to void/rejected.
#   • void is the universal escape hatch from any non-final state (an operator cancels a mistake).
_LEGAL = {
    "created":            {"sent", "void", "expired", "flagged_fraud"},
    "sent":               {"redeemed", "expired", "void", "flagged_fraud"},
    "redeemed":           {"sale_logged", "expired", "void", "flagged_fraud"},
    "sale_logged":        {"activated", "rejected", "void", "flagged_fraud"},
    "activated":          {"commission_pending", "rejected", "void", "flagged_fraud"},
    "commission_pending": {"approved", "rejected", "void", "flagged_fraud"},
    "approved":           {"paid", "void", "flagged_fraud"},
    "paid":               set(),          # terminal
    "expired":            {"void"},        # an expired QR can only be archived
    "rejected":           set(),          # terminal
    "void":               set(),          # terminal
    "flagged_fraud":      {"void", "rejected"},   # a flag resolves to a cancellation, never forward
}

# States in which the referral is "live" (still progressing toward a payout) — used by fraud checks and
# the dashboard. Anything not here is closed one way or another.
LIVE_STATES = {"created", "sent", "redeemed", "sale_logged", "activated", "commission_pending", "approved"}
# States that count a referrer's phone as "already spent" against the duplicate + velocity gates: an
# open pipeline OR an already-paid one. A void/expired/rejected/fraud referral frees the number again.
BLOCKING_STATES = LIVE_STATES | {"paid"}


def can_transition(frm: str, to: str) -> bool:
    """PURE. Is moving a referral from state `frm` to state `to` legal? Unknown states → False. This is
    the one gate the router calls before EVERY status write; an illegal jump (pay-before-approve,
    approve-before-activate) is refused here, not caught later by a report."""
    return to in _LEGAL.get(str(frm or ""), set())


def transition_error(frm: str, to: str) -> str:
    """A human message for an illegal transition, naming what the referral would have to be first."""
    if can_transition(frm, to):
        return ""
    needs = {
        "paid": "approved", "approved": "commission_pending", "commission_pending": "activated",
        "activated": "sale_logged", "sale_logged": "redeemed", "redeemed": "sent", "sent": "created",
    }
    pre = needs.get(to)
    if pre:
        return (f"A referral must be '{pre}' before it can become '{to}'. This one is '{frm}'. "
                f"(A commission is never approved before the line is activated, nor paid before approval.)")
    return f"Cannot move a referral from '{frm}' to '{to}'."


# ══════════════════════════════════════════════════════════════════════════════════════════════
# 2. IDENTITY / ANTI-FRAUD GATES
# ══════════════════════════════════════════════════════════════════════════════════════════════
def self_referral_conflict(referrer_phone, customer_phone, cfg: dict) -> str:
    """A reason string if the referrer and the referred customer are the same person (same normalized
    phone), else "". Disabled when cfg.self_referral_block is false. Blank phones never collide."""
    if not _as_bool(cfg.get("self_referral_block"), True):
        return ""
    r, c = normalize_phone(referrer_phone), normalize_phone(customer_phone)
    if r and c and r == c:
        return "Self-referral: the referring party and the referred customer share a phone number."
    return ""


def duplicate_conflict(customer_phone, existing_referrals: list, cfg: dict,
                       is_existing_customer: bool = False, exclude_id=None) -> str:
    """A reason string if this customer phone cannot be referred, else "".

    Two ways a number is "already ours" (both gated by cfg.duplicate_match == 'phone'):
      • it belongs to an existing customer (the caller resolves this against the POS/CRM customer
        master, conceptually the Customer-360 lookup, and passes the boolean);
      • it is already the referred customer on an OPEN-or-PAID referral (BLOCKING_STATES) — you cannot
        farm the same new customer through twice. A void/expired/rejected referral frees the number.
    `exclude_id` skips the referral being evaluated (so re-checking an existing row does not self-block).
    """
    if str(cfg.get("duplicate_match") or "phone").lower() == "none":
        return ""
    c = normalize_phone(customer_phone)
    if not c:
        return ""
    if is_existing_customer:
        return "This phone number already belongs to one of our customers — they can't be referred as new."
    for r in existing_referrals or []:
        if exclude_id is not None and r.get("id") == exclude_id:
            continue
        if normalize_phone(r.get("customer_phone")) == c \
                and str(r.get("status") or "") in BLOCKING_STATES:
            return (f"This customer is already on an open referral (#{r.get('referral_no') or r.get('id')}) "
                    f"— it can't be referred a second time.")
    return ""


def count_referrals_in_window(referrer_phone, referrals: list, window_days: int, now: datetime,
                              exclude_id=None) -> int:
    """How many NON-cancelled referrals this referrer has created inside the rolling window. Counts
    BLOCKING_STATES only (a void/expired/rejected attempt does not consume the referrer's allowance)."""
    r = normalize_phone(referrer_phone)
    if not r:
        return 0
    try:
        wd = max(0, int(window_days))
    except (TypeError, ValueError):
        wd = 0
    cutoff = now - timedelta(days=wd) if wd else None
    n = 0
    for row in referrals or []:
        if exclude_id is not None and row.get("id") == exclude_id:
            continue
        if normalize_phone(row.get("referrer_phone")) != r:
            continue
        if str(row.get("status") or "") not in BLOCKING_STATES:
            continue
        created = _dt(row.get("created_at"))
        if cutoff is not None and (created is None or created < cutoff):
            continue
        n += 1
    return n


def velocity_exceeded(referrer_phone, referrals: list, cfg: dict, now: datetime,
                      exclude_id=None) -> str:
    """A reason string if creating one more referral would put this referrer over the velocity cap,
    else "". A cap of 0 disables the limit. This is the anti-farming gate."""
    try:
        cap = int(cfg.get("max_referrals_per_referrer") or 0)
    except (TypeError, ValueError):
        cap = 0
    if cap <= 0:
        return ""
    window = int(cfg.get("velocity_window_days") or 0)
    current = count_referrals_in_window(referrer_phone, referrals, window, now, exclude_id=exclude_id)
    if current >= cap:
        win = f" in the last {window} day(s)" if window else ""
        return (f"Velocity limit: this referrer already has {current} active referral(s){win} "
                f"(cap {cap}). Wait for one to close before creating another.")
    return ""


def run_fraud_checks(referrer_phone, customer_phone, referrals: list, cfg: dict, now: datetime,
                     is_existing_customer: bool = False, exclude_id=None) -> list:
    """The full anti-scam battery, run at CREATE (customer phone may be blank then — those checks
    simply pass) and again at REDEEM (when the customer phone arrives). Returns a list of reason
    strings; an empty list means clean. The router turns a non-empty list into a `flagged_fraud`
    transition WITH the reasons attached, rather than silently failing — a trip is recorded, not hidden.
    """
    reasons = []
    for check in (
        self_referral_conflict(referrer_phone, customer_phone, cfg),
        duplicate_conflict(customer_phone, referrals, cfg, is_existing_customer, exclude_id),
        velocity_exceeded(referrer_phone, referrals, cfg, now, exclude_id),
    ):
        if check:
            reasons.append(check)
    return reasons


# ══════════════════════════════════════════════════════════════════════════════════════════════
# 4. SEGREGATION OF DUTIES + MONEY
# ══════════════════════════════════════════════════════════════════════════════════════════════
def approval_conflict(caller_employee_id, caller_app_user_id, referral: dict) -> str:
    """A reason string if this caller must NOT approve this referral's payout, else "". A rep can never
    approve a referral they themselves created — the person who books the work is never the person who
    signs off the money. Matches on either the business employee id or the app-user uuid, so spoofing
    one identity while holding the other still trips."""
    emp = str(caller_employee_id or "").strip()
    uid = str(caller_app_user_id or "").strip()
    r_emp = str(referral.get("created_by") or "").strip()
    r_uid = str(referral.get("created_by_app_user_id") or "").strip()
    if emp and r_emp and emp == r_emp:
        return "Segregation of duties: you created this referral, so you can't approve its payout."
    if uid and r_uid and uid == r_uid:
        return "Segregation of duties: you created this referral, so you can't approve its payout."
    return ""


def compute_commission(referral: dict, cfg: dict) -> float:
    """The payout amount for a referral: its own user-defined commission_amount when set (>= 0),
    otherwise the tenant default. Never negative, always rounded to cents."""
    amt = referral.get("commission_amount")
    val = None
    try:
        if amt is not None and str(amt) != "":
            val = float(amt)
    except (TypeError, ValueError):
        val = None
    if val is None:
        try:
            val = float(cfg.get("default_commission_amount"))
        except (TypeError, ValueError):
            val = float(DEFAULT_CONFIG["default_commission_amount"])
    return round(max(0.0, val), 2)


def resolve_payout_date(referral: dict, cfg: dict, approved_on: datetime):
    """The date the referrer gets paid: the referral's own user-defined payout_date when set, else the
    approval date + default_payout_offset_days. Returns an ISO date string (YYYY-MM-DD)."""
    explicit = referral.get("payout_date")
    if explicit:
        d = _dt(explicit)
        if d is not None:
            return d.date().isoformat()
        s = str(explicit)[:10]
        if re.match(r"^\d{4}-\d{2}-\d{2}$", s):
            return s
    try:
        offset = max(0, int(cfg.get("default_payout_offset_days") or 0))
    except (TypeError, ValueError):
        offset = DEFAULT_CONFIG["default_payout_offset_days"]
    return (approved_on + timedelta(days=offset)).date().isoformat()


# ══════════════════════════════════════════════════════════════════════════════════════════════
# Display + dashboard math
# ══════════════════════════════════════════════════════════════════════════════════════════════
def referrer_display(referral: dict) -> str:
    return (normalize_name(referral.get("referrer_name"))
            or referral.get("referrer_phone") or "Unknown referrer")


def customer_display(referral: dict) -> str:
    return (normalize_name(referral.get("customer_name"))
            or referral.get("customer_phone") or "—")


# The pipeline order the funnel renders in (working states only; exception states are tallied separately).
FUNNEL_ORDER = ["created", "sent", "redeemed", "sale_logged", "activated",
                "commission_pending", "approved", "paid"]
STATE_LABEL = {
    "created": "Created", "sent": "QR Sent", "redeemed": "Redeemed", "sale_logged": "Sale Logged",
    "activated": "Activated", "commission_pending": "Pending Approval", "approved": "Approved",
    "paid": "Paid", "expired": "Expired", "rejected": "Rejected", "void": "Void",
    "flagged_fraud": "Flagged Fraud",
}


def funnel(referrals: list) -> list:
    """Count per working state, in pipeline order. Empty states are still returned — an empty state is
    information (that is where the program stalls)."""
    counts = {s: 0 for s in FUNNEL_ORDER}
    for r in referrals or []:
        s = str(r.get("status") or "")
        if s in counts:
            counts[s] += 1
    return [{"status": s, "label": STATE_LABEL.get(s, s), "count": counts[s]} for s in FUNNEL_ORDER]


def summarize(referrals: list, cfg: dict) -> dict:
    """The dashboard rollup: funnel, exception tallies, and the two money numbers an operator watches —
    $ awaiting approval and $ approved-but-unpaid. Amounts use compute_commission so an unset per-row
    amount still counts at the tenant default."""
    by_status = {}
    pending_approval_amt = 0.0
    approved_unpaid_amt = 0.0
    paid_amt = 0.0
    fraud = 0
    for r in referrals or []:
        s = str(r.get("status") or "")
        by_status[s] = by_status.get(s, 0) + 1
        amt = compute_commission(r, cfg)
        if s == "commission_pending":
            pending_approval_amt += amt
        elif s == "approved":
            approved_unpaid_amt += amt
        elif s == "paid":
            paid_amt += amt
        if s == "flagged_fraud" or r.get("fraud_flag"):
            fraud += 1
    return {
        "total": len(referrals or []),
        "funnel": funnel(referrals),
        "by_status": by_status,
        "pending_approval_count": by_status.get("commission_pending", 0),
        "pending_approval_amount": round(pending_approval_amt, 2),
        "approved_unpaid_count": by_status.get("approved", 0),
        "approved_unpaid_amount": round(approved_unpaid_amt, 2),
        "paid_count": by_status.get("paid", 0),
        "paid_amount": round(paid_amt, 2),
        "fraud_flag_count": fraud,
        "expired_count": by_status.get("expired", 0),
    }


# ══════════════════════════════════════════════════════════════════════════════════════════════
# helpers
# ══════════════════════════════════════════════════════════════════════════════════════════════
def _pos_int(v) -> int:
    try:
        return max(0, int(v))
    except (TypeError, ValueError):
        return 0


def _dt(value):
    """Parse a timestamp (from the DB or a body) into aware UTC. Returns None on anything unusable — a
    row with a garbage date must be SKIPPED, never crash a check. Mirrors crm.pipeline_core._dt."""
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    s = str(value).strip().replace(" ", "T")
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    try:
        d = datetime.fromisoformat(s)
    except ValueError:
        try:
            d = datetime.fromisoformat(s[:19])
        except ValueError:
            try:
                d = datetime.fromisoformat(s[:10])   # a bare date
            except ValueError:
                return None
    return d if d.tzinfo else d.replace(tzinfo=timezone.utc)
