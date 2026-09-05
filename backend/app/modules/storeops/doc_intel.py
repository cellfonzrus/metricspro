"""Lease / insurance document intelligence — PURE core (migs 964-967, owner directive 2026-09-05).

OWNER DIRECTIVE 2026-09-05, verbatim: "For the insurance module we created, there should be a link
to upload the insurance policy and assign that policy to multiple stores as one insurance policy can
cover multiple stores, the uploaded policy should then be interpreted by the system using ai and the
fields filled for the following — Coverage period ... Coverage type - bop/ workers comp ...
Premium ... Policy number ... Summary of inclusions ... Extra items needed as per ai. Please to
upload the certificate of insurance of respective stores. Similarly for the lease which are uploaded
it should fill out the lease term, Rents as per lease in the years coming up, Exit clause, Lease
termination liabilities, Contact information, Notice address, Any other critical clauses with clause
number and translated in[to plain] English ... a notification when a coi is expir[ing] or th[e]
lease is getting over at least 60 days in advance or as per lease requirement."

═══════════════════════════════════════════════════════════════════════════════════════════════════
THE ONE RULE THIS MODULE ENFORCES: THE MODEL NEVER ORIGINATES A DOLLAR AMOUNT THAT SHIPS
═══════════════════════════════════════════════════════════════════════════════════════════════════
account/liabilities_due.py books rent and insurance premiums FROM storeops.store_lease, through the
documented §14 read contract (store_lease.rent_for_month / resolve_rent_due / rent_due_window and
insurance_premium on insurance_premium_due per insurance_premium_frequency). account/engine.py
states the house posture plainly: the AI "never originates a dollar amount that ships".

So an extracted premium, rent, escalation or due-date does NOT write to store_lease. Every
extraction lands as a DRAFT (storeops.document_extraction, mig 965) with, per field, its confidence
and the VERBATIM source snippet + page. `apply_plan` below is the only door to the live record, and
it refuses:
  • any key not in this module's FIELD_SPECS (the model cannot invent a target),
  • any ACH/banking key, always, under every flag (FORBIDDEN_TARGETS),
  • any MONEY_GUARDED field unless a human both selected it AND gave the money confirmation.
`harness_doc_intel.py` asserts each of those refusals.

WHAT ELSE LIVES HERE (all pure, all stdlib):
  • FIELD_SPECS — the extraction catalogue per subject kind (lease / insurance_policy /
    insurance_coi): key, label, value type, the column it may land in, money_guarded.
  • normalize_extraction — the model's JSON -> validated draft rows. Unknown keys are NEVER
    silently dropped into the void: they become "extra items" (the owner's "extra items needed as
    per ai") so nothing found is lost, but they can never patch a column.
  • coverage-type normalization against the PER-ORG vocabulary (RULE TWO: the owner's BOP /
    workers comp are two rows of tenant config, never a code enum — migration 964 seeds the house
    list on storeops.tenants.insurance_coverage_types).
  • resolve_notice_days + expiry_alerts — "at least 60 days in advance or as per lease requirement":
    the resolved window is MAX(the document's own requirement, the org floor), so a lease demanding
    90 or 180 days beats the 60-day floor and a lease demanding 30 never drops below it.

LEAF MODULE: no fastapi, no supabase, no anthropic import at module level. The AI call itself lives
in doc_intel_ai.py (which MUST be invoked via run_in_threadpool — see its header, SEV-1 2026-07-30).
Provable with the stdlib alone: backend/harness_doc_intel.py.
"""
import re
from datetime import date

# ── document kinds this module interprets ────────────────────────────────────────────────────────
SUBJECT_LEASE = "lease"
SUBJECT_POLICY = "insurance_policy"
SUBJECT_COI = "insurance_coi"
SUBJECT_KINDS = (SUBJECT_LEASE, SUBJECT_POLICY, SUBJECT_COI)

# ── house coverage-type vocabulary — the SEED for storeops.tenants.insurance_coverage_types (mig
#    964 column DEFAULT). Mirrored here so a pre-964 database still offers a sane list; a tenant's
#    own list always wins. RULE TWO: nothing in this file branches on any of these keys. ──────────
HOUSE_COVERAGE_TYPES = (
    {"key": "bop", "label": "Business Owner's Policy (BOP)"},
    {"key": "workers_comp", "label": "Workers' Compensation"},
    {"key": "general_liability", "label": "General Liability"},
    {"key": "property", "label": "Property"},
    {"key": "umbrella", "label": "Umbrella / Excess"},
    {"key": "cyber", "label": "Cyber Liability"},
    {"key": "epli", "label": "Employment Practices (EPLI)"},
    {"key": "auto", "label": "Commercial Auto"},
)

# ── expiry notice: the owner's floor, and the reminder ladder under it ───────────────────────────
HOUSE_NOTICE_DAYS = 60                       # "at least 60 days in advance" (owner 2026-09-05)
HOUSE_MILESTONES = (60, 30, 14, 7, 1)        # extra nudges BELOW the resolved window
MAX_NOTICE_DAYS = 730                        # a 2-year window is already absurd; clamp, never trust

# ── never, under any flag, writable from an extraction (mig 946 SENSITIVE columns) ───────────────
FORBIDDEN_TARGETS = frozenset({
    "ach_bank_name", "ach_routing_number", "ach_account_number", "ach_notes",
    "rent_payment_links", "org_id", "store_code", "id",
})

# ── the money guard: columns a money reader consumes (account/liabilities_due.py), plus every other
#    dollar amount an extraction could produce. A field targeting one of these can only ever land
#    with an explicit human money confirmation. ─────────────────────────────────────────────────
MONEY_GUARDED = frozenset({
    "current_rent", "rent_effective_from", "escalation_pct", "rent_schedule", "rent_due",
    "insurance_premium", "insurance_premium_due",
    "premium", "premium_due",
})


def _spec(key, label, vtype, target, table=None, guarded=None):
    return {"key": key, "label": label, "value_type": vtype, "target": target, "table": table,
            "money_guarded": bool(key in MONEY_GUARDED if guarded is None else guarded)}


# ── THE EXTRACTION CATALOGUE ─────────────────────────────────────────────────────────────────────
# `target` None = the field is captured in the draft for a human to read, but has no column to land
# in (it stays on the extraction row). Everything the owner listed appears here.
FIELD_SPECS = {
    SUBJECT_LEASE: (
        _spec("lease_start", "Lease term — start", "date", "lease_start", "store_lease"),
        _spec("lease_end", "Lease term — end", "date", "lease_end", "store_lease"),
        _spec("current_rent", "Current monthly rent", "money", "current_rent", "store_lease"),
        _spec("rent_effective_from", "Current rent effective from", "date", "rent_effective_from", "store_lease"),
        _spec("escalation_pct", "Annual escalation %", "number", "escalation_pct", "store_lease"),
        # "Rents as per lease in the years coming up"
        _spec("rent_schedule", "Rent schedule for the coming years", "schedule", "rent_schedule", "store_lease"),
        _spec("rent_due", "Rent due window", "due", "rent_due", "store_lease"),
        _spec("lease_notice_days", "Notice required (days)", "int", "lease_notice_days", "store_lease"),
        _spec("notice_address", "Notice address", "text", "notice_address", "store_lease"),
        _spec("lease_exit_clause", "Exit clause", "text", "lease_exit_clause", "store_lease"),
        _spec("lease_termination_liabilities", "Lease termination liabilities", "text",
              "lease_termination_liabilities", "store_lease"),
        # "Contact information"
        _spec("landlord_name", "Landlord name", "text", "landlord_name", "store_lease"),
        _spec("landlord_email", "Landlord email", "text", "landlord_email", "store_lease"),
        _spec("landlord_phone", "Landlord phone", "text", "landlord_phone", "store_lease"),
        _spec("site_contact_name", "Site contact name", "text", "site_contact_name", "store_lease"),
        _spec("site_contact_phone", "Site contact phone", "text", "site_contact_phone", "store_lease"),
    ),
    SUBJECT_POLICY: (
        _spec("policy_number", "Policy number", "text", "policy_number", "insurance_policy"),
        _spec("insurer", "Insurance company", "text", "insurer", "insurance_policy"),
        _spec("coverage_type", "Coverage type", "coverage_type", "coverage_type", "insurance_policy"),
        _spec("coverage_start", "Coverage period — start", "date", "coverage_start", "insurance_policy"),
        _spec("coverage_end", "Coverage period — end", "date", "coverage_end", "insurance_policy"),
        _spec("premium", "Premium", "money", "premium", "insurance_policy"),
        _spec("premium_frequency", "Premium billing frequency", "text", "premium_frequency", "insurance_policy"),
        _spec("premium_due", "Premium due date", "date", "premium_due", "insurance_policy"),
        _spec("inclusions_summary", "Summary of inclusions", "text", "inclusions_summary", "insurance_policy"),
        _spec("notice_days", "Notice required (days)", "int", "notice_days", "insurance_policy"),
    ),
    SUBJECT_COI: (
        _spec("insurance_company", "Insurance company", "text", "insurance_company", "store_lease"),
        _spec("insurance_policy_number", "Policy number", "text", "insurance_policy_number", "store_lease"),
        _spec("coi_expires", "Certificate expires", "date", "coi_expires", "store_lease"),
        _spec("insurance_premium", "Premium", "money", "insurance_premium", "store_lease"),
        _spec("insurance_premium_due", "Premium due date", "date", "insurance_premium_due", "store_lease"),
    ),
}

# clause categories the lease prompt asks for by name; anything else keeps the model's own category
CLAUSE_CATEGORIES = ("exit", "termination_liability", "renewal", "notice", "rent", "assignment",
                     "maintenance", "insurance", "default", "other")

_TEXT_MAX = 4000        # a stored narrative field (exit clause, inclusions summary)
_SNIPPET_MAX = 700      # a quoted source snippet


def spec_map(subject_kind):
    """{key: spec} for one subject kind; {} for an unknown kind (never raises)."""
    return {s["key"]: s for s in FIELD_SPECS.get(str(subject_kind or ""), ())}


def is_money_guarded(subject_kind, key):
    s = spec_map(subject_kind).get(str(key or ""))
    return bool(s and s["money_guarded"])


# ══════════════════════════════════════════════════════════════════════════════════════════════════
# PURE — value coercion (the model returns strings; nothing here guesses)
# ══════════════════════════════════════════════════════════════════════════════════════════════════
_DATE_RE = re.compile(r"(\d{4})[-/](\d{1,2})[-/](\d{1,2})")
_MDY_RE = re.compile(r"^\s*(\d{1,2})[-/](\d{1,2})[-/](\d{4})\s*$")
_MONEY_STRIP = re.compile(r"[,$\s]")
# a bank-ish digit run in a quoted snippet — masked before the snippet is ever stored/echoed
_BANKISH = re.compile(r"(?i)\b(routing|aba|account|acct|iban|swift)\b[^0-9]{0,20}(\d[\d\- ]{6,})")


def coerce_date(v):
    """Anything -> 'YYYY-MM-DD' or None. Accepts ISO and US M/D/YYYY; NEVER invents a missing part."""
    s = str(v or "").strip()
    if not s:
        return None
    m = _MDY_RE.match(s)
    if m:
        mo, dd, yy = int(m.group(1)), int(m.group(2)), int(m.group(3))
    else:
        m = _DATE_RE.search(s)
        if not m:
            return None
        yy, mo, dd = int(m.group(1)), int(m.group(2)), int(m.group(3))
    try:
        return date(yy, mo, dd).isoformat()
    except ValueError:
        return None


def coerce_number(v):
    """'$4,250.00' / '3.5%' / 4250 -> float, or None. Refuses anything with no digits."""
    if isinstance(v, bool):
        return None
    if isinstance(v, (int, float)):
        return float(v)
    s = _MONEY_STRIP.sub("", str(v or "")).replace("%", "")
    if s.startswith("(") and s.endswith(")"):        # (1,200) accounting negative
        s = "-" + s[1:-1]
    if not re.search(r"\d", s):
        return None
    try:
        return float(s)
    except ValueError:
        return None


def coerce_int(v):
    n = coerce_number(v)
    return None if n is None else int(round(n))


def scrub_snippet(text, limit=_SNIPPET_MAX):
    """Trim a quoted source snippet and MASK bank-ish digit runs inside it.

    The snippet is what lets a reviewer verify a field without reopening the PDF, so it is quoted
    verbatim — except that if the document happens to spell out a routing/account number next to the
    word 'routing'/'account', that run is masked before it is ever stored in
    storeops.document_extraction or returned by an endpoint. A policy number (no bank keyword in
    front of it) is untouched: masking it would defeat the whole point of the snippet."""
    s = re.sub(r"\s+", " ", str(text or "")).strip()
    if not s:
        return None
    s = _BANKISH.sub(lambda m: f"{m.group(1)} [redacted]", s)
    return s[:limit]


def normalize_coverage_types(raw):
    """Per-org [{key,label}] vocabulary -> validated tuple; anything unusable -> the house list."""
    out = []
    for e in (raw if isinstance(raw, (list, tuple)) else ()):
        if isinstance(e, dict):
            k = re.sub(r"[^a-z0-9_]+", "_", str(e.get("key") or "").strip().lower()).strip("_")
            label = str(e.get("label") or "").strip() or k.replace("_", " ").title()
        else:
            k = re.sub(r"[^a-z0-9_]+", "_", str(e or "").strip().lower()).strip("_")
            label = k.replace("_", " ").title()
        if k and not any(o["key"] == k for o in out):
            out.append({"key": k, "label": label})
    return tuple(out) or tuple(dict(e) for e in HOUSE_COVERAGE_TYPES)


def normalize_coverage_type(raw, types=None):
    """A model-written coverage type -> a configured key, or None.

    Matches the ORG'S OWN vocabulary (key, label, or a squashed form of either) — never a hardcoded
    'bop'/'workers comp' branch. An unmatched value returns None and the raw text survives as an
    extra item, so a tenant whose policy says 'Garagekeepers' sees it rather than losing it."""
    s = str(raw or "").strip().lower()
    if not s:
        return None
    squash = re.sub(r"[^a-z0-9]+", "", s)
    for t in normalize_coverage_types(types):
        if squash == re.sub(r"[^a-z0-9]+", "", t["key"]) or squash == re.sub(r"[^a-z0-9]+", "", t["label"]):
            return t["key"]
    for t in normalize_coverage_types(types):          # substring, longest key first
        kk = re.sub(r"[^a-z0-9]+", "", t["key"])
        if kk and (kk in squash or squash in kk):
            return t["key"]
    return None


def coerce_schedule(v):
    """"Rents as per lease in the years coming up" -> the mig-946 rent_schedule shape
    [{'effective_from','monthly_rent'}], ascending. Malformed entries are DROPPED, never guessed.
    Deliberately produces exactly what store_lease.normalize_rent_schedule accepts, so an accepted
    schedule flows into the EXISTING rent_for_month contract with no second derivation."""
    out = []
    for e in (v if isinstance(v, (list, tuple)) else ()):
        if not isinstance(e, dict):
            continue
        d = coerce_date(e.get("effective_from") or e.get("from") or e.get("start"))
        rent = coerce_number(e.get("monthly_rent") if e.get("monthly_rent") is not None else e.get("rent"))
        if d is None or rent is None or rent < 0:
            continue
        out.append({"effective_from": d, "monthly_rent": round(rent, 2)})
    out.sort(key=lambda e: e["effective_from"])
    return out or None


def coerce_due(v):
    """{'kind':'week'|'day','value':N} (the mig-946 rent_due shape), or a bare day number."""
    if isinstance(v, dict):
        kind = str(v.get("kind") or "").strip().lower()
        n = coerce_int(v.get("value"))
        if kind == "week" and n and 1 <= n <= 5:
            return {"kind": "week", "value": n}
        if kind == "day" and n and 1 <= n <= 31:
            return {"kind": "day", "value": n}
        return None
    n = coerce_int(v)
    return {"kind": "day", "value": n} if n and 1 <= n <= 31 else None


def coerce_value(value_type, raw, coverage_types=None):
    """One dispatch point for every field type. Returns None for anything it cannot vouch for."""
    t = str(value_type or "text")
    if t == "date":
        return coerce_date(raw)
    if t in ("money", "number"):
        n = coerce_number(raw)
        return None if n is None else round(n, 2)
    if t == "int":
        n = coerce_int(raw)
        return None if n is None else n
    if t == "schedule":
        return coerce_schedule(raw)
    if t == "due":
        return coerce_due(raw)
    if t == "coverage_type":
        return normalize_coverage_type(raw, coverage_types)
    s = re.sub(r"[ \t]+", " ", str(raw or "")).strip()
    return s[:_TEXT_MAX] or None


def _confidence(v):
    n = coerce_number(v)
    if n is None:
        return None
    if n > 1:                       # a model that answers "85" means 85%
        n = n / 100.0
    return round(min(1.0, max(0.0, n)), 3)


# ══════════════════════════════════════════════════════════════════════════════════════════════════
# PURE — the model's JSON -> a validated DRAFT
# ══════════════════════════════════════════════════════════════════════════════════════════════════
def normalize_extraction(raw, subject_kind, coverage_types=None):
    """Map an extraction payload onto FIELD_SPECS. NEVER raises; unknown input degrades to empty.

    Returns {'fields': [...], 'clauses': [...], 'extra_items': [...], 'contacts': [...]}.
      fields      — one entry per RECOGNIZED key whose value survived coercion, carrying
                    label/value/value_type/confidence/source_text/source_page/money_guarded/target.
      extra_items — the owner's "extra items needed as per ai": the model's own open list PLUS every
                    key it returned that this catalogue does not know. Nothing found is lost, and
                    nothing unrecognized can ever reach a column.
      clauses     — critical clauses with their clause NUMBER and plain-English translation.
      contacts    — contact rows for storeops.document_contact (only once a human accepts them).
    """
    data = raw if isinstance(raw, dict) else {}
    specs = spec_map(subject_kind)
    src_fields = data.get("fields")
    if isinstance(src_fields, dict):                       # tolerate {key: {...}} as well as a list
        src_fields = [dict(v or {}, key=k) for k, v in src_fields.items()]
    fields, extras, seen = [], [], set()
    for e in (src_fields if isinstance(src_fields, list) else ()):
        if not isinstance(e, dict):
            continue
        key = str(e.get("key") or "").strip()
        raw_val = e.get("value")
        spec = specs.get(key)
        if spec is None:
            if key and raw_val not in (None, ""):
                extras.append({"label": key.replace("_", " ").title(),
                               "value": str(raw_val)[:_TEXT_MAX],
                               "note": "Not a field this record stores — review only.",
                               "source_page": coerce_int(e.get("source_page")),
                               "source_text": scrub_snippet(e.get("source_text"))})
            continue
        if key in seen:
            continue
        val = coerce_value(spec["value_type"], raw_val, coverage_types)
        if val is None or val == "":
            if spec["value_type"] == "coverage_type" and str(raw_val or "").strip():
                extras.append({"label": spec["label"],
                               "value": str(raw_val)[:_TEXT_MAX],
                               "note": "Coverage type is not in this company's list — add it in "
                                       "settings, or pick the closest one.",
                               "source_page": coerce_int(e.get("source_page")),
                               "source_text": scrub_snippet(e.get("source_text"))})
            continue
        seen.add(key)
        fields.append({
            "key": key, "label": spec["label"], "value": val, "value_type": spec["value_type"],
            "target": (f'{spec["table"]}.{spec["target"]}' if spec["target"] else None),
            "money_guarded": spec["money_guarded"],
            "confidence": _confidence(e.get("confidence")),
            "source_text": scrub_snippet(e.get("source_text")),
            "source_page": coerce_int(e.get("source_page")),
        })

    for e in (data.get("extra_items") if isinstance(data.get("extra_items"), list) else ()):
        if isinstance(e, dict):
            label = str(e.get("label") or e.get("title") or "").strip()
            value = str(e.get("value") or e.get("detail") or "").strip()
            if label or value:
                extras.append({"label": (label or "Noted")[:200], "value": value[:_TEXT_MAX],
                               "note": str(e.get("note") or "")[:400] or None,
                               "source_page": coerce_int(e.get("source_page")),
                               "source_text": scrub_snippet(e.get("source_text"))})
        elif str(e or "").strip():
            extras.append({"label": "Noted", "value": str(e)[:_TEXT_MAX], "note": None,
                           "source_page": None, "source_text": None})

    clauses = []
    for e in (data.get("clauses") if isinstance(data.get("clauses"), list) else ()):
        if not isinstance(e, dict):
            continue
        plain = str(e.get("plain_english") or e.get("plain") or "").strip()
        num = str(e.get("clause_number") or e.get("number") or "").strip()
        if not plain and not num:
            continue
        cat = str(e.get("category") or "").strip().lower().replace(" ", "_")
        clauses.append({
            "clause_number": num[:40] or None,
            "title": str(e.get("title") or "").strip()[:200] or None,
            "category": cat if cat in CLAUSE_CATEGORIES else (cat[:40] or "other"),
            "plain_english": plain[:_TEXT_MAX] or None,
            "source_text": scrub_snippet(e.get("source_text")),
            "source_page": coerce_int(e.get("source_page")),
        })

    contacts = []
    for e in (data.get("contacts") if isinstance(data.get("contacts"), list) else ()):
        if not isinstance(e, dict):
            continue
        email = str(e.get("email") or "").strip()[:200]
        phone = str(e.get("phone") or "").strip()[:60]
        name = str(e.get("name") or "").strip()[:160]
        if not (email or phone or name):
            continue
        contacts.append({"name": name or None, "email": (email if "@" in email else None),
                         "phone": phone or None, "role": str(e.get("role") or "").strip()[:80] or None})

    return {"fields": fields, "clauses": clauses, "extra_items": extras, "contacts": contacts}


# ══════════════════════════════════════════════════════════════════════════════════════════════════
# PURE — THE MONEY GATE: the only door from a draft to a live record
# ══════════════════════════════════════════════════════════════════════════════════════════════════
def apply_plan(subject_kind, fields, accepted_keys, confirm_money=False):
    """Turn a human's accept list into column patches. PURE — no DB, no side effects.

    Returns {'patch': {table: {column: value}}, 'applied': [...], 'refused': [{key, reason}]}.

    REFUSES, in this order (each proved by harness_doc_intel.py):
      1. a key the catalogue doesn't define for this subject   -> 'unknown_field'
      2. a key not present in this extraction                  -> 'not_in_extraction'
      3. a field with no column to land in                     -> 'no_target'
      4. an ACH/banking or identity column, ALWAYS             -> 'forbidden_target'
      5. a MONEY_GUARDED field without the money confirmation  -> 'money_confirmation_required'
    Rule 4 has no override flag on purpose: nothing read out of a document may ever rewrite banking
    details. Rule 5 is what keeps account/liabilities_due.py reading only human-accepted dollars.
    """
    specs = spec_map(subject_kind)
    by_key = {f.get("key"): f for f in (fields or []) if isinstance(f, dict)}
    want = [str(k) for k in (accepted_keys or [])]
    patch, applied, refused = {}, [], []
    for key in want:
        spec = specs.get(key)
        if spec is None:
            refused.append({"key": key, "reason": "unknown_field"})
            continue
        f = by_key.get(key)
        if f is None or f.get("value") in (None, ""):
            refused.append({"key": key, "reason": "not_in_extraction"})
            continue
        if not spec["target"]:
            refused.append({"key": key, "reason": "no_target"})
            continue
        if spec["target"] in FORBIDDEN_TARGETS:
            refused.append({"key": key, "reason": "forbidden_target"})
            continue
        if spec["money_guarded"] and not confirm_money:
            refused.append({"key": key, "reason": "money_confirmation_required"})
            continue
        patch.setdefault(spec["table"], {})[spec["target"]] = f["value"]
        applied.append({"key": key, "target": f'{spec["table"]}.{spec["target"]}',
                        "value": f["value"], "money_guarded": spec["money_guarded"],
                        "confidence": f.get("confidence")})
    return {"patch": patch, "applied": applied, "refused": refused}


# ══════════════════════════════════════════════════════════════════════════════════════════════════
# PURE — the expiry notice window and the alerts it produces
# ══════════════════════════════════════════════════════════════════════════════════════════════════
def org_notice_floor(cfg, kind):
    """storeops.tenants.doc_expiry_notice_days {'lease':60,'insurance':60} -> the floor for a kind.
    Anything missing/garbage falls back to HOUSE_NOTICE_DAYS — a config fault can only ever leave
    the owner's 60-day floor in place, never remove it."""
    k = "lease" if str(kind) == SUBJECT_LEASE else "insurance"
    try:
        n = coerce_int((cfg or {}).get(k))
    except Exception:
        n = None
    return HOUSE_NOTICE_DAYS if not n or n < 1 else min(int(n), MAX_NOTICE_DAYS)


def resolve_notice_days(doc_notice_days=None, org_floor=None, kind=SUBJECT_LEASE):
    """"at least 60 days in advance or as per lease requirement" (owner 2026-09-05).

        resolved = MAX(the document's own requirement, the org floor)

    MAX, not "override": a lease demanding 90 or 180 days' notice must BEAT the floor, and a lease
    demanding 30 must not drag us below it. `org_floor` may be the raw tenants config dict or an
    already-resolved integer."""
    floor = org_floor if isinstance(org_floor, int) else org_notice_floor(org_floor, kind)
    if not isinstance(floor, int) or floor < 1:
        floor = HOUSE_NOTICE_DAYS
    own = coerce_int(doc_notice_days)
    if own is None or own < 1:
        return min(floor, MAX_NOTICE_DAYS)
    return min(max(int(own), int(floor)), MAX_NOTICE_DAYS)


def milestones_for(notice_days):
    """The reminder ladder for a resolved window: 0 ('expired'), every house nudge strictly below
    the window, then the window itself. ASCENDING on purpose — the alert that fires is the FIRST
    milestone the countdown has reached, i.e. the TIGHTEST one crossed. Scanning descending would
    re-pick the widest milestone every day and, with it already in the alert log, silence the 30/14/7
    nudges entirely."""
    n = int(notice_days or HOUSE_NOTICE_DAYS)
    return tuple(sorted({n} | {m for m in HOUSE_MILESTONES if m < n} | {0}))


def _days_between(today, when):
    a, b = coerce_date(today), coerce_date(when)
    if not a or not b:
        return None
    return (date.fromisoformat(b) - date.fromisoformat(a)).days


def contact_window(contact, resolved_days):
    """A contact's own lead time never shortens the resolved window — only lengthens it."""
    own = coerce_int((contact or {}).get("notice_days"))
    return max(int(resolved_days), int(own)) if own and own > 0 else int(resolved_days)


def expiry_alerts(today, subjects, org_notice_cfg=None, already_sent=None):
    """Which expiry notices are due today. PURE — the sweep does the sending.

    `subjects`: [{kind, ref, label, expires_on, own_notice_days, contacts:[{name,email,phone,
    notice_days, notify_expiry}]}]. `already_sent`: a set of dedupe keys the alert log already has
    (storeops.alert_log, mig 433 — reused, no second alert-state table).

    One alert per subject per MILESTONE, addressed only to the contacts whose own window reaches
    that milestone, so a broker asking for 120 days' notice gets the early one and everybody gets
    the 30/14/7/expired nudges. A subject with no expiry date, or no contact who wants notice, is
    silently skipped — never a guessed date, never mail to nobody."""
    sent = set(already_sent or ())
    out = []
    for s in (subjects or []):
        if not isinstance(s, dict):
            continue
        kind = str(s.get("kind") or "")
        ref = str(s.get("ref") or "")
        days_out = _days_between(today, s.get("expires_on"))
        if days_out is None or not ref:
            continue
        resolved = resolve_notice_days(s.get("own_notice_days"), org_notice_cfg, kind)
        contacts = [c for c in (s.get("contacts") or [])
                    if isinstance(c, dict) and c.get("email") and c.get("notify_expiry") is not False]
        if not contacts:
            continue
        windows = {id(c): contact_window(c, resolved) for c in contacts}
        ladder = milestones_for(max([resolved] + list(windows.values())))
        fired = next((m for m in ladder if days_out <= m), None)
        if fired is None:
            continue
        recipients = [c for c in contacts if windows[id(c)] >= fired or fired <= 0]
        if not recipients:
            continue
        key = f"{kind}:{ref}:{s.get('expires_on')}:m{fired}"
        if key in sent:
            continue
        out.append({
            "subject_kind": kind, "subject_ref": ref,
            "label": str(s.get("label") or ref),
            "expires_on": coerce_date(s.get("expires_on")),
            "days_out": days_out, "milestone": fired,
            "notice_days": resolved,
            "expired": days_out <= 0,
            "dedupe_key": key,
            "alert_scope": ("doc_expiry_lease" if kind == SUBJECT_LEASE else "doc_expiry_insurance"),
            "recipients": [{"name": c.get("name"), "email": c.get("email")} for c in recipients],
        })
    out.sort(key=lambda a: (a["days_out"], a["subject_kind"], a["subject_ref"]))
    return out


# ══════════════════════════════════════════════════════════════════════════════════════════════════
# PURE — the notice email body (plain, no template engine; the sweep just sends it)
# ══════════════════════════════════════════════════════════════════════════════════════════════════
_KIND_LABEL = {SUBJECT_LEASE: "Lease", SUBJECT_POLICY: "Insurance policy", SUBJECT_COI: "Certificate of insurance"}


def _esc(s):
    return (str(s or "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"))


def alert_email(alert):
    """(subject, html) for one expiry alert. States the deadline, the notice requirement it came
    from, and what to do — no dollar amounts (an expiry notice is not a bill)."""
    kind = _KIND_LABEL.get(alert.get("subject_kind"), "Document")
    label = _esc(alert.get("label"))
    when = _esc(alert.get("expires_on"))
    days = int(alert.get("days_out") or 0)
    if alert.get("expired"):
        subject = f"{kind} EXPIRED — {alert.get('label')} ({alert.get('expires_on')})"
        lead = f"<b>{kind} for {label} expired on {when}.</b>"
    else:
        subject = f"{kind} expires in {days} days — {alert.get('label')} ({alert.get('expires_on')})"
        lead = f"<b>{kind} for {label} expires on {when} — {days} days from now.</b>"
    return subject, (
        f"<div style='font-family:system-ui,Segoe UI,Arial,sans-serif;font-size:14px;color:#111'>"
        f"<p>{lead}</p>"
        f"<p>This notice goes out {int(alert.get('notice_days') or HOUSE_NOTICE_DAYS)} days ahead — "
        f"the longer of this document's own notice requirement and the company minimum.</p>"
        f"<p>Renew, extend, or serve notice before the date above. If it has already been handled, "
        f"update the record so these reminders stop.</p>"
        f"<p style='color:#666;font-size:12px'>Sent automatically by MetricsPro. Reminders repeat at "
        f"30, 14, 7 and 1 day out, and once on expiry.</p></div>")
