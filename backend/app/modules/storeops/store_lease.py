"""Store lease / landlord / insurance capture — pure logic + management gate (mig 946).

OWNER DIRECTIVE 2026-09-03, verbatim: "when setting up the store the tenant should be able to add
the landlord information, rent payment links, or ACH information, rent payment due, Site contact,
site contact phone number, landlord email address and phone number, current rent, annual
escalations, in percentage or add monthly rents, insurance information, premium due, due date
company, also add a place to upload the current lease and insurance COI which can be downloaded at
any given time." And: "by default the rents are due in the 1st week of the month, should not be
hard coded but defined for stores when setting up the store."

WHAT LIVES HERE (routes live in storeops/router.py, beside the store-setup CRUD):
  • Rent math — `rent_for_month`: the explicit per-period schedule ("or add monthly rents") wins;
    else `current_rent` compounded by `escalation_pct` once per WHOLE anniversary-year since
    `rent_effective_from`; else `current_rent` as-is; nothing known -> None (never a fake 0).
  • Rent-due resolution — `resolve_rent_due`: per-store override -> per-org
    storeops.tenants.rent_due_default -> HOUSE_RENT_DUE = first week of the month. The house
    default is data all the way down (a column DEFAULT + this constant), never a branch a tenant
    can't configure away (RULE TWO). `rent_due_window` turns a resolved rule into concrete month
    dates for the sibling finance "rents due this week" reader.
  • The management gate — `can_see_lease`: ACH/banking details and lease documents are
    money-sensitive, so EVERY read/write of storeops.store_lease / store_document is gated at
    management level (mig-434 pay-visibility posture): allow-list
    storeops.tenants.lease_visible_roles, NULL = pay_visibility.DEFAULT_VISIBLE_ROLES ("market
    manager and above"); scope-'all' roles and the `store_lease_docs` data grant always pass.
    FAIL-CLOSED, with the same single open-app parity carve-out as pay_visibility.can_see_pay
    (no token at all while the login master switch is OFF).
  • Storage — private bucket `store-docs` (the closing envelope-photo precedent: raw path in the
    row, on-demand signed URL, org-scoped lookup). Uploads APPEND a storeops.store_document
    version; prior versions stay downloadable.

LEAF MODULE: no fastapi, no heavy imports at top level; DB/core imports are lazy so the pure core
is unit-provable with the stdlib alone (backend/harness_store_lease.py) and a core fault degrades
CLOSED instead of breaking store setup at import time.
"""
import base64
import calendar
import re
from datetime import date, datetime, timezone

from app.modules.storeops import pay_visibility as _payvis

# ── the grant key (rbac.ts DATA_GRANTS naming convention, like employee_pay_rates) ────────────────
LEASE_GRANT_KEY = "store_lease_docs"

# ── house default: rent due the FIRST WEEK of the month (owner 2026-09-03) — a seeded default
#    value mirrored by the mig-946 column DEFAULT on storeops.tenants.rent_due_default, never a
#    branch (RULE TWO). ──────────────────────────────────────────────────────────────────────────
HOUSE_RENT_DUE = {"kind": "week", "value": 1}

# ── the sensitive keys (ACH/banking) — strip-not-zero, pay_visibility.strip_pay rationale ─────────
ACH_FIELDS = ("ach_bank_name", "ach_routing_number", "ach_account_number", "ach_notes")

# ── document kinds + private bucket ───────────────────────────────────────────────────────────────
# DOC_KINDS stays the PER-STORE list: these are the only kinds POST /store-lease/doc accepts and the
# only keys GET /store-lease echoes. The master insurance POLICY document (mig 964) is a third kind
# on the SAME storeops.store_document table — one document covering many stores, so it hangs off
# policy_id with store_code NULL and is uploaded through the policy endpoint instead. Keeping it out
# of DOC_KINDS is what stops a policy ever being filed against a single store by accident.
DOC_KINDS = ("lease", "insurance_coi")
POLICY_DOC_KIND = "insurance_policy"
ALL_DOC_KINDS = DOC_KINDS + (POLICY_DOC_KIND,)
STORE_DOC_BUCKET = "store-docs"
PREMIUM_FREQUENCIES = ("annual", "semiannual", "quarterly", "monthly")


# ══════════════════════════════════════════════════════════════════════════════════════════════════
# PURE CORE — rent due resolution
# ══════════════════════════════════════════════════════════════════════════════════════════════════
def normalize_rent_due(raw):
    """{'kind':'week'|'day','value':N} -> validated dict, or None for anything malformed.
    week 1-5 (5 = whatever remains of the month), day 1-31 (clamped to month end at window time)."""
    if not isinstance(raw, dict):
        return None
    kind = str(raw.get("kind") or "").strip().lower()
    try:
        value = int(raw.get("value"))
    except (TypeError, ValueError):
        return None
    if kind == "week" and 1 <= value <= 5:
        return {"kind": "week", "value": value}
    if kind == "day" and 1 <= value <= 31:
        return {"kind": "day", "value": value}
    return None


def resolve_rent_due(store_override=None, tenant_default=None):
    """Per-store override -> per-org default -> HOUSE first-week. Garbage at any layer falls
    through to the next (a config problem can never make rent-due unresolvable)."""
    return (normalize_rent_due(store_override)
            or normalize_rent_due(tenant_default)
            or dict(HOUSE_RENT_DUE))


def rent_due_window(year, month, due):
    """A resolved rent-due rule -> ('YYYY-MM-DD' start, 'YYYY-MM-DD' end) inside that month.
    week N = days 7N-6 .. min(7N, month end) (week 5 = the clamped tail); day d = one day,
    clamped to the month end (day 31 in Feb = Feb 28/29). Malformed `due` resolves house-first."""
    due = normalize_rent_due(due) or dict(HOUSE_RENT_DUE)
    last = calendar.monthrange(int(year), int(month))[1]
    if due["kind"] == "day":
        d = min(due["value"], last)
        iso = date(int(year), int(month), d).isoformat()
        return iso, iso
    start = 7 * due["value"] - 6
    if start > last:                       # week 5 of a 28-day Feb — clamp to the real tail
        start = max(1, last - 6)
    end = min(7 * due["value"], last)
    return date(int(year), int(month), start).isoformat(), date(int(year), int(month), end).isoformat()


# ══════════════════════════════════════════════════════════════════════════════════════════════════
# PURE CORE — rent amount for a month (percentage escalation OR explicit schedule)
# ══════════════════════════════════════════════════════════════════════════════════════════════════
def _parse_date(v):
    try:
        return date.fromisoformat(str(v)[:10])
    except (TypeError, ValueError):
        return None


def normalize_rent_schedule(raw):
    """JSONB [{'effective_from':'YYYY-MM-DD','monthly_rent':N}, ...] -> validated, sorted-ascending
    list. Malformed entries are DROPPED (never guessed); anything non-list -> []."""
    out = []
    for e in (raw if isinstance(raw, list) else []):
        if not isinstance(e, dict):
            continue
        d = _parse_date(e.get("effective_from"))
        try:
            rent = float(e.get("monthly_rent"))
        except (TypeError, ValueError):
            continue
        if d is None or rent < 0:
            continue
        out.append({"effective_from": d.isoformat(), "monthly_rent": round(rent, 2)})
    out.sort(key=lambda e: e["effective_from"])
    return out


def _whole_years_between(d0, d1):
    """Whole anniversary-years from d0 to d1 (0 when d1 < first anniversary; never negative)."""
    if d1 <= d0:
        return 0
    years = d1.year - d0.year
    if (d1.month, d1.day) < (d0.month, d0.day):
        years -= 1
    return max(0, years)


def rent_for_month(year, month, current_rent=None, rent_effective_from=None,
                   escalation_pct=None, rent_schedule=None):
    """The monthly rent owed for (year, month) — the column contract the sibling finance
    "rents due / recurring expenses" reader computes from (mig 946 header documents the same rule):

      1. EXPLICIT SCHEDULE wins: the normalize_rent_schedule entry with the latest
         effective_from <= first-of-month. (Months BEFORE the first entry fall through to 2/3.)
      2. else current_rent compounded by escalation_pct% once per WHOLE year elapsed from
         rent_effective_from to the first of the month (anniversary-date arithmetic; no
         rent_effective_from -> no escalation applied, the entered rent is simply current).
      3. else current_rent as entered.
    Returns a 2dp float, or None when nothing is known (never a fake 0)."""
    first = date(int(year), int(month), 1)
    sched = normalize_rent_schedule(rent_schedule)
    best = None
    for e in sched:                        # ascending — the last match is the latest effective_from
        if e["effective_from"] <= first.isoformat():
            best = e
    if best is not None:
        return best["monthly_rent"]
    try:
        rent = float(current_rent)
    except (TypeError, ValueError):
        return None
    try:
        pct = float(escalation_pct)
    except (TypeError, ValueError):
        pct = 0.0
    eff = _parse_date(rent_effective_from)
    if pct and eff is not None:
        rent *= (1.0 + pct / 100.0) ** _whole_years_between(eff, first)
    return round(rent, 2)


# ══════════════════════════════════════════════════════════════════════════════════════════════════
# PURE CORE — the management gate truth table + sensitive-field strip
# ══════════════════════════════════════════════════════════════════════════════════════════════════
def resolve_lease_access(caller_role, caller_scope, visible_roles=None, has_grant=False):
    """May a caller with (role, scope, grant) read/write lease + ACH + documents? PURE, fails
    CLOSED on unknowns — the mig-434 'manager_up' posture with the lease allow-list:
      scope 'all' (company-wide role)                         -> True
      `store_lease_docs` grant                                -> True
      role in `visible_roles` (or the built-in market-manager-and-above default when NULL) -> True
      unknown/empty role, anything else                       -> False"""
    if str(caller_scope or "").strip().lower() == "all":
        return True
    if has_grant:
        return True
    role = _payvis._norm_role(caller_role)
    if not role:
        return False
    allow = {_payvis._norm_role(r) for r in (visible_roles or _payvis.DEFAULT_VISIBLE_ROLES)}
    allow.discard("")
    return role in allow


def strip_sensitive(row):
    """Delete the ACH keys from a lease payload (defense-in-depth for any future ungated list
    surface — today every route is gated whole). Deletes, never zeroes; tolerant; idempotent."""
    try:
        if isinstance(row, dict):
            for k in ACH_FIELDS:
                row.pop(k, None)
    except Exception:
        pass
    return row


# ══════════════════════════════════════════════════════════════════════════════════════════════════
# DB / CALLER WRAPPERS (lazy imports; every failure degrades CLOSED)
# ══════════════════════════════════════════════════════════════════════════════════════════════════
def tenant_lease_config(org_id, client=None):
    """(lease_visible_roles_or_None, rent_due_default_or_None) from storeops.tenants — ADAPTIVE:
    a pre-946 database, a missing tenant row, or any read failure resolve to (None, None) = the
    built-in market-manager-and-above allow-list + the house first-week default. A config problem
    can only ever make the gate MORE closed / the default the house one, never open anything."""
    try:
        client = client or _payvis._default_client()
        rows = (_payvis._storeops(client).table("tenants")
                .select("lease_visible_roles,rent_due_default")
                .eq("org_id", org_id).limit(1).execute().data) or []
    except Exception:
        return None, None
    if not rows:
        return None, None
    raw = rows[0].get("lease_visible_roles")
    roles = [str(r) for r in raw if str(r or "").strip()] if isinstance(raw, (list, tuple)) else []
    return (roles or None), normalize_rent_due(rows[0].get("rent_due_default"))


def can_see_lease(authorization, org_id=None, client=None):
    """May this caller read/write the store lease/landlord/ACH/insurance surfaces? The full gate —
    caller resolution is EXACTLY pay_visibility.can_see_pay's path (core _uid_from_token +
    _resolve_caller, org_id as the acting-org hint) and FAILS CLOSED the same way: an
    unverifiable token, an unresolvable login, or any resolver fault denies. ONE carve-out, for
    platform parity: NO token at all while the login master switch is OFF (the open app's normal
    state) is allowed — the same rule caller_scope / hr._require_hr_or_admin / can_see_pay apply."""
    try:
        auth = authorization if isinstance(authorization, str) else ""
        client = client or _payvis._default_client()
        uid, caller, resolver_broke = None, None, False
        if auth.strip():
            try:
                from app.modules.core.router import _uid_from_token, _resolve_caller
                uid = _uid_from_token(auth)
                if uid:
                    caller = _resolve_caller(client, uid, org_id or None)
            except Exception:
                resolver_broke = True
        if resolver_broke:
            return False                    # unverifiable token -> hide (fail closed)
        org = org_id or (caller or {}).get("org_id")
        visible, _due = tenant_lease_config(org, client) if org else (None, None)
        if caller is None:
            # No resolvable identity: unauthenticated + login enforcement OFF = open-app parity;
            # anything else stays hidden.
            return (uid is None) and (not _payvis._login_enforced(client))
        if caller.get("super_admin"):
            return True
        perms = caller.get("perms") or {}
        return resolve_lease_access(caller.get("role"), perms.get("scope"), visible,
                                    _payvis.grant_allowed(caller, LEASE_GRANT_KEY))
    except Exception:
        return False


# ══════════════════════════════════════════════════════════════════════════════════════════════════
# STORAGE — private `store-docs` bucket (closing envelope-photo precedent: raw path in the row,
# on-demand signed URL through an org-scoped, gated endpoint; never a public URL)
# ══════════════════════════════════════════════════════════════════════════════════════════════════
_DOC_CONTENT_TYPES = {           # data-url header token -> (extension, content type)
    "application/pdf": ("pdf", "application/pdf"),
    "image/png": ("png", "image/png"),
    "image/jpeg": ("jpg", "image/jpeg"),
    "image/jpg": ("jpg", "image/jpeg"),
    "image/webp": ("webp", "image/webp"),
}
MAX_DOC_BYTES = 15 * 1024 * 1024


def _safe_name(file_name):
    s = re.sub(r"[^A-Za-z0-9._-]+", "_", str(file_name or "").strip()).strip("._")
    return s[:80] or "document"


def decode_doc_data_url(data_url):
    """base64 data-url -> (raw bytes, extension, content_type). Raises ValueError with a
    user-showable message on anything malformed/oversized/unsupported (route turns it into a 400)."""
    s = str(data_url or "")
    if "," not in s:
        raise ValueError("No file received — choose the document and try again.")
    header, b64 = s.split(",", 1)
    mime = header.split(";")[0].replace("data:", "").strip().lower()
    ext_ct = _DOC_CONTENT_TYPES.get(mime)
    if not ext_ct:
        raise ValueError("Unsupported file type — upload a PDF or an image (PNG/JPG/WebP).")
    try:
        raw = base64.b64decode(b64)
    except Exception:
        raise ValueError("That file couldn't be read — re-select the document and try again.")
    if not raw:
        raise ValueError("No file received — choose the document and try again.")
    if len(raw) > MAX_DOC_BYTES:
        raise ValueError("That file is over 15 MB — upload a smaller scan.")
    return raw, ext_ct[0], ext_ct[1]


def _ensure_doc_bucket(client=None):
    c = client or _payvis._default_client()
    try:
        c.storage.get_bucket(STORE_DOC_BUCKET)
    except Exception:
        try:
            c.storage.create_bucket(STORE_DOC_BUCKET)
        except Exception:
            pass
    return c


def upload_store_doc(org_id, store_code, doc_kind, file_name, data_url, client=None):
    """Upload one lease/COI document to the private store-docs bucket -> (storage_path, size,
    content_type). Raises ValueError (bad input) or the underlying storage error (route surfaces
    it as a 502 — the envelope-photo raise_on_error lesson: never report a storage fault as
    'no file provided')."""
    raw, ext, ctype = decode_doc_data_url(data_url)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S%f")
    path = f"{org_id}/{str(store_code).strip()}/{doc_kind}/{stamp}_{_safe_name(file_name)}"
    if not path.lower().endswith("." + ext):
        path += "." + ext
    c = _ensure_doc_bucket(client)
    c.storage.from_(STORE_DOC_BUCKET).upload(path, raw, {"content-type": ctype, "upsert": "false"})
    return path, len(raw), ctype


def download_store_doc(path, client=None):
    """Read one store-docs object back as raw bytes, or None on any failure.

    ONLY caller today: the AI document reader (doc_intel_ai.extract_document), which needs the file
    itself, not a URL. Deliberately goes through the SAME private bucket + raw-path pattern as
    signed_doc_url — the path always comes from an org-scoped row lookup by document id, never from
    a caller — so this adds no new way to reach a file. Returns None rather than raising: a storage
    fault must degrade to "couldn't read it automatically", never a 500."""
    p = str(path or "")
    if not p or "/" not in p:
        return None
    try:
        c = client or _payvis._default_client()
        raw = c.storage.from_(STORE_DOC_BUCKET).download(p)
        return bytes(raw) if raw else None
    except Exception:
        return None


def signed_doc_url(path, client=None, expires=3600):
    """Sign a store-docs storage path on demand (1h). None on failure — the route 502s cleanly
    instead of echoing a raw private path that never loads (the 2026-08-18 envelope lesson)."""
    p = str(path or "")
    if not p or "/" not in p:
        return None
    try:
        c = client or _payvis._default_client()
        res = c.storage.from_(STORE_DOC_BUCKET).create_signed_url(p, expires)
        url = (res.get("signedURL") or res.get("signed_url")) if isinstance(res, dict) else res
        return url if url and str(url).startswith("http") else None
    except Exception:
        return None
