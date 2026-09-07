"""The outbound AI call that reads an uploaded lease / insurance policy / COI (owner 2026-09-05).

═══════════════════════════════════════════════════════════════════════════════════════════════════
SEV-1 2026-07-30 — READ THIS BEFORE CALLING ANYTHING HERE
═══════════════════════════════════════════════════════════════════════════════════════════════════
The synchronous Anthropic client called from an `async def` FastAPI endpoint runs its HTTP request
ON the single uvicorn event loop, so every other request — including /health — stalls until it
returns. The SDK defaults to a 600s timeout with 2 automatic retries (~30 minutes worst case), which
is how one Ask-AI request froze the whole backend. account/ai_limits.py documents the incident.

`extract_document` below is SYNCHRONOUS and reads a whole PDF, so it is the single worst call in
this module to get wrong. Both layers of the documented fix are mandatory and both are in place:

  1. PRIMARY — the route MUST invoke it via `run_in_threadpool` (storeops/router.py
     `post_document_extract` does; never call it straight from an `async def`).
  2. DEFENCE IN DEPTH — the explicit timeout + max_retries below cap one stalled call at
     DOC_INTEL_TIMEOUT_S x (1 + DOC_INTEL_MAX_RETRIES) instead of ~30 minutes.

Both are env-tunable (same convention as ACCOUNT_AI_TIMEOUT_S / AI_ASSIST_TIMEOUT_S), and a garbage
env value falls back to the default rather than breaking module import. 120s default: a 40-page
lease with adaptive thinking legitimately runs longer than a P&L narrative, and this is a background
"interpret this document" action, not an interactive one.

CONVERGED 2026-09-06 (migs 972 + 983) — this call now runs on the SHARED AI guard. The note that
used to sit here asked for exactly one thing: "the guard [needs] to grow a purpose whose
authorization check is the lease gate rather than super-admin — a small, deliberate change to ONE
shared decision function". That is what happened, and it was done as a GENERALISATION, not a hole:

  · `core/control_box.AI_PURPOSES` is now a registry, and each purpose NAMES the predicate that
    authorizes it. `control_box_triage` still means platform super-admin and nothing else;
    `lease_extraction` means `store_lease.can_see_lease` — the SAME management gate every route in
    this subsystem already applies, restated inside the ONE decision function so it is provable.
    The lease predicate does NOT fall back to super-admin: a purpose is satisfied on its own
    predicate or not at all.
  · A wider predicate on one purpose widens NOTHING ELSE. The rate limit, the per-org daily call and
    token budget, the bounded server-validated subject (this org's own document id, never free
    text), and the audit of EVERY attempt including refusals apply to this purpose exactly as they
    apply to the control box's. An unregistered purpose is refused, fail-closed.
  · `POST /storeops/document-extract` is the enforcement point (storeops/router.py): the lease gate
    still 403s first, the guard then decides, and every attempt lands in `core.ai_call_audit`
    org-scoped with tokens only (mig 718's `core.token_rates` remains the only $/MTok source).
  · DEGRADES AS BEFORE: no API key, or a tenant that switched AI off, still yields a clean empty
    `not_extracted` draft — never an exception. Rate limit / budget / authorization refusals are a
    403 with the reason and nothing else.

The two limits below stay declared HERE rather than imported from account/ai_limits.py: that
module's constants are documented as bounding the finance NARRATIVE calls, and a document extraction
legitimately needs a longer budget.

═══════════════════════════════════════════════════════════════════════════════════════════════════
WHAT IS AND IS NOT SENT TO THE MODEL
═══════════════════════════════════════════════════════════════════════════════════════════════════
SENT: the uploaded document bytes (the tenant's own lease/policy PDF or scan), the field catalogue
      from doc_intel.FIELD_SPECS, and the tenant's coverage-type vocabulary.
NEVER SENT: anything out of storeops.store_lease — above all the ACH columns (ach_bank_name,
      ach_routing_number, ach_account_number, ach_notes). There is no code path here that reads the
      lease row at all, which is the strongest form of that guarantee. Quoted snippets coming BACK
      are additionally masked for bank-ish digit runs by doc_intel.scrub_snippet.

WHAT COMES BACK IS A DRAFT, NOT A NUMBER. Everything returned lands in
storeops.document_extraction (mig 965) for human review; doc_intel.apply_plan is the only door to a
live column and it refuses money-guarded fields without an explicit human money confirmation. The
house posture in account/engine.py — the AI "never originates a dollar amount that ships" — holds.

DEGRADES CLEANLY: with no ANTHROPIC_API_KEY this returns status 'not_extracted' (a normal, empty
draft the UI explains), NEVER an exception. Any API error returns status 'failed' with the exception
TYPE only — never a raw payload echo.
"""
import base64
import json
import os

from app.modules.storeops import doc_intel as _di

try:
    DOC_INTEL_TIMEOUT_S = max(1.0, float(os.getenv("DOC_INTEL_TIMEOUT_S") or 120))
except Exception:
    DOC_INTEL_TIMEOUT_S = 120.0

try:
    DOC_INTEL_MAX_RETRIES = max(0, int(os.getenv("DOC_INTEL_MAX_RETRIES") or 1))
except Exception:
    DOC_INTEL_MAX_RETRIES = 1

# Model is CONFIG, not code (RULE TWO) — env-tunable with no deploy, like every other model choice
# on the platform. Read from the env here rather than app/core/config.py so this module stays a leaf
# and no shared file changes.
DOC_INTEL_MODEL = (os.getenv("DOC_INTEL_MODEL") or "claude-opus-5").strip()

MAX_OUTPUT_TOKENS = 16000
_IMAGE_TYPES = ("image/png", "image/jpeg", "image/webp")

# The ONE shape of "nothing was read, and that is fine" — a clean, empty, reviewable draft rather
# than an error. Used by every not-extracted branch below AND by the route when the shared guard
# refuses softly (no key configured, or the tenant switched AI off), so those two paths cannot drift
# into two different-looking answers for the same user-visible situation.
NO_KEY_MESSAGE = ("Automatic reading is switched off (no AI key configured). "
                  "The document is saved — enter the fields by hand.")


def not_extracted_draft(error=None, model="none"):
    """An empty draft with a human-readable reason. NEVER raises, never partially fills anything."""
    return {"fields": [], "clauses": [], "extra_items": [], "contacts": [],
            "status": "not_extracted", "model": model, "error": error or NO_KEY_MESSAGE,
            "usage": {}}


# ── the response contract (structured outputs — the model cannot answer in free prose) ───────────
def _schema():
    return {
        "type": "object",
        "properties": {
            "fields": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "key": {"type": "string"},
                        "value": {"type": "string"},
                        "confidence": {"type": "number"},
                        "source_text": {"type": "string"},
                        "source_page": {"type": "integer"},
                    },
                    "required": ["key", "value", "confidence", "source_text", "source_page"],
                    "additionalProperties": False,
                },
            },
            "clauses": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "clause_number": {"type": "string"},
                        "title": {"type": "string"},
                        "category": {"type": "string"},
                        "plain_english": {"type": "string"},
                        "source_text": {"type": "string"},
                        "source_page": {"type": "integer"},
                    },
                    "required": ["clause_number", "title", "category", "plain_english",
                                 "source_text", "source_page"],
                    "additionalProperties": False,
                },
            },
            "extra_items": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "label": {"type": "string"},
                        "value": {"type": "string"},
                        "note": {"type": "string"},
                        "source_page": {"type": "integer"},
                    },
                    "required": ["label", "value", "note", "source_page"],
                    "additionalProperties": False,
                },
            },
            "contacts": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "name": {"type": "string"},
                        "email": {"type": "string"},
                        "phone": {"type": "string"},
                        "role": {"type": "string"},
                    },
                    "required": ["name", "email", "phone", "role"],
                    "additionalProperties": False,
                },
            },
        },
        "required": ["fields", "clauses", "extra_items", "contacts"],
        "additionalProperties": False,
    }


_VALUE_FORMAT_HELP = {
    "date": "an ISO date, YYYY-MM-DD",
    "money": "a plain number, no currency symbol or thousands separators",
    "number": "a plain number",
    "int": "a whole number",
    "schedule": 'a JSON array string: [{"effective_from":"YYYY-MM-DD","monthly_rent":0000.00}, ...]',
    "due": 'a JSON object string: {"kind":"week"|"day","value":N}',
    "coverage_type": "one of the coverage types listed above, copied exactly",
    "text": "plain text",
}


def build_prompt(subject_kind, coverage_types=None):
    """The instruction text. PURE and importable, so the harness can assert that the catalogue and
    the prompt never drift apart (every FIELD_SPECS key is named, and no key outside it is)."""
    specs = _di.FIELD_SPECS.get(subject_kind, ())
    lines = [f'  - "{s["key"]}" — {s["label"]}; value as {_VALUE_FORMAT_HELP.get(s["value_type"], "plain text")}'
             + ("  [MONEY — extra care: quote the exact figure and its sentence]" if s["money_guarded"] else "")
             for s in specs]
    vocab = ", ".join(f'{t["key"]} ({t["label"]})' for t in _di.normalize_coverage_types(coverage_types))
    what = {
        _di.SUBJECT_LEASE: "a commercial retail LEASE",
        _di.SUBJECT_POLICY: "a commercial INSURANCE POLICY",
        _di.SUBJECT_COI: "a CERTIFICATE OF INSURANCE (COI)",
    }.get(subject_kind, "a commercial document")

    p = [
        f"You are reading {what} for a multi-store retail company. Extract only what the document "
        "actually says. This is a data-entry aid: a human reviews every value you return before any "
        "of it is saved, and any figure you cannot point at in the text must be omitted, never "
        "estimated, inferred from a similar document, or filled in from general knowledge.",
        "",
        "RULES",
        "  1. Omit a field entirely rather than guess. An omitted field costs a human 20 seconds; a "
        "     confidently wrong rent or premium costs real money.",
        "  2. For every field give `source_text`: the VERBATIM sentence or clause you took it from "
        "     (up to ~40 words), and `source_page`: the 1-based page it appears on (0 if unknown). "
        "     A reviewer must be able to confirm the value from your quote without reopening the file.",
        "  3. `confidence` is 0..1 — your own honest read: 1.0 only when the document states the "
        "     value explicitly and unambiguously.",
        "  4. Do not transcribe bank account, routing, IBAN or card numbers into any field or quote.",
        "",
        "FIELDS TO EXTRACT (use these keys exactly; skip any the document does not state):",
    ]
    p += lines
    if subject_kind in (_di.SUBJECT_POLICY, _di.SUBJECT_COI):
        p += ["", f"COVERAGE TYPES this company recognizes (copy one exactly): {vocab}.",
              "If the policy's coverage is not in that list, do NOT force a match — leave "
              "coverage_type out and put the document's own wording in extra_items."]
    if subject_kind == _di.SUBJECT_LEASE:
        p += [
            "",
            "CLAUSES — also return `clauses`: every clause that materially affects money, exit, or "
            "obligations. ALWAYS include, when the lease has them: the exit / early-termination "
            "clause (category \"exit\"), termination liabilities (category \"termination_liability\"), "
            "renewal/extension options (\"renewal\"), and the notice clause (\"notice\"). Then any "
            "OTHER clause a store operator would regret not knowing (personal guaranty, relocation, "
            "co-tenancy, percentage rent, CAM/tax escalations, holdover penalties, assignment "
            "restrictions, repair obligations, insurance requirements, default and cure).",
            "For each: its `clause_number` exactly as printed (e.g. \"14.3\"), a short `title`, a "
            "`category`, the verbatim `source_text`, the `source_page`, and `plain_english` — a "
            "2-3 sentence translation into plain English a store manager understands, stating what "
            "it obliges the company to do and what it costs if triggered. No legalese in that field.",
            "",
            "CONTACTS — return `contacts` for every person or company the lease names for notices, "
            "rent, or property management (landlord, property manager, attorney), with role.",
        ]
    else:
        p += ["", "CONTACTS — return `contacts` for the broker, agent, or carrier representative the "
                  "document names, with role. `clauses` may be empty for an insurance document, but "
                  "use it for any condition, exclusion or warranty that would void coverage."]
    p += [
        "",
        "EXTRA ITEMS — `extra_items` is for anything important that has no field above: notable "
        "exclusions, deductibles/limits, additional-insured or waiver-of-subrogation requirements, "
        "unusual obligations, missing signatures or pages, or a value you found but were unsure "
        "where to put. Label each one plainly.",
        "",
        "Return ONLY the JSON object described by the schema.",
    ]
    return "\n".join(p)


def _doc_block(raw_bytes, content_type):
    """One content block for the uploaded file. PDFs go as a document block; scans as an image."""
    ct = str(content_type or "").lower()
    b64 = base64.b64encode(raw_bytes).decode("ascii")
    if ct == "application/pdf":
        return {"type": "document",
                "source": {"type": "base64", "media_type": "application/pdf", "data": b64}}
    if ct in _IMAGE_TYPES:
        return {"type": "image", "source": {"type": "base64", "media_type": ct, "data": b64}}
    return None


def extract_document(raw_bytes, content_type, subject_kind, coverage_types=None):
    """Read one document -> a NORMALIZED DRAFT dict:
        {status, model, fields, clauses, extra_items, contacts, error}
    status: 'draft' (usable), 'not_extracted' (no API key / unsupported file type — a clean empty
    draft, not an error), or 'failed' (the call errored; `error` carries the exception TYPE only).

    ⚠ SYNCHRONOUS AND SLOW — call it from a worker thread (`run_in_threadpool`), never from an
    `async def`. See this module's header (SEV-1 2026-07-30). Never raises."""
    empty = {"fields": [], "clauses": [], "extra_items": [], "contacts": [], "usage": {}}
    kind = str(subject_kind or "")
    if kind not in _di.SUBJECT_KINDS:
        return not_extracted_draft("This document kind isn't one the reader understands.")
    block = _doc_block(raw_bytes or b"", content_type)
    if block is None:
        return not_extracted_draft(
            "Automatic reading needs a PDF or an image (PNG/JPG/WebP). "
            "The document is stored and downloadable either way — fill the fields in by hand.")
    try:
        from app.core.config import settings
        api_key = (getattr(settings, "ANTHROPIC_API_KEY", "") or "").strip()
    except Exception:
        api_key = ""
    if not api_key:
        return not_extracted_draft(NO_KEY_MESSAGE)
    try:
        from anthropic import Anthropic
        client = Anthropic(api_key=api_key, timeout=DOC_INTEL_TIMEOUT_S,
                           max_retries=DOC_INTEL_MAX_RETRIES)
        msg = client.messages.create(
            model=DOC_INTEL_MODEL,
            max_tokens=MAX_OUTPUT_TOKENS,
            thinking={"type": "adaptive"},
            output_config={"effort": "high", "format": {"type": "json_schema", "schema": _schema()}},
            messages=[{"role": "user", "content": [block,
                                                   {"type": "text",
                                                    "text": build_prompt(kind, coverage_types)}]}],
        )
        from app.modules.billing import ai_meter as _ai_meter
        _ai_meter.record("doc_intel_extraction", DOC_INTEL_MODEL, msg)  # usage metering only (mig 973/974) — no auth implication
        # Tokens for the GUARD's audit row (mig 972/983) — tokens only, never a cost: mig 718's
        # `core.token_rates` is the single $/MTok source. Metering and authorization stay separate
        # mechanisms that happen to read the same response object.
        _u = getattr(msg, "usage", None)
        used = {"input_tokens": int(getattr(_u, "input_tokens", 0) or 0),
                "output_tokens": int(getattr(_u, "output_tokens", 0) or 0)}
        if getattr(msg, "stop_reason", None) == "refusal":
            return dict(empty, status="failed", model=DOC_INTEL_MODEL, usage=used,
                        error="The reader declined this document. Enter the fields by hand.")
        text = "".join(b.text for b in msg.content if getattr(b, "type", "") == "text").strip()
        raw = json.loads(text) if text else {}
    except Exception as e:
        # Type only — never echo a payload or a key into a stored row (or into the audit row: the
        # route passes this same type-only string to `ai_audit_row`, which redacts it again).
        return dict(empty, status="failed", model=DOC_INTEL_MODEL, usage={},
                    error=f"The document couldn't be read automatically ({type(e).__name__}). "
                          f"It is saved — enter the fields by hand or try again.")
    out = _di.normalize_extraction(raw, kind, coverage_types)
    return dict(out, status="draft", model=DOC_INTEL_MODEL, error=None, usage=used)
