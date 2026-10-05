"""The in-app data assistant's TWO endpoints (index §52).

    GET  /core/data-qa/status   — may this login use it, is it configured, and what can it answer
                                  for THIS tenant right now
    POST /core/data-qa          — ask a question; get a sentence, plus any tables and charts

Both are thin: every ruling lives somewhere else and is dereferenced here, which is the point.

  - WHO may ask            → `control_box.AI_PURPOSES['data_qa']` via `core/ai_gate` (the shared guard)
  - WHAT may be asked      → `core/data_qa_registry.DATA_QUESTIONS` (the semantic layer)
  - WHICH STORES come back → the report endpoints' own RBAC, because the read is made with the
                             caller's own token through the app itself (`data_qa_agent._fetch`)
  - THE ARITHMETIC         → `core/data_qa_compute` (pure, proof-harnessed)
  - THE SPEND              → `billing/ai_meter` (purpose `data_qa`, registered in `ai_usage`)

So this file contains no permission rule, no number, and no SQL — and `harness_data_qa_lock.py`
fails the build if that stops being true.

SEV-1 2026-07-30: the handler is `async def` and awaits the agent, which awaits both the model and
the in-process report reads. Nothing here runs a blocking client on the event loop.
"""
from datetime import date, datetime, timezone

from fastapi import APIRouter, Header, HTTPException, Request

from app.core.config import settings
from app.core.database import get_supabase
from app.core.schemas import LaxModel
from app.modules.core import ai_gate as _gate
from app.modules.core import data_qa_agent as _agent
from app.modules.core import data_qa_registry as reg
from app.modules.core.entitlements import module_enabled as _module_enabled

# NO prefix: this router is included INTO `core/router.py`'s router, which already
# carries `/core`, so the paths below resolve to /api/v1/core/data-qa*.
router = APIRouter(tags=["Core / Data assistant"])
ORG_ID = "00000000-0000-0000-0000-000000000001"

MODULE_KEY = "ai_assistant"       # the same entitlement the existing in-app assistant is gated on


def _sb():
    return get_supabase()


def _tenant_context(client, org_id):
    """The tenant's display name and its ENABLED module keys.

    The module list is what makes the assistant grow with the tenant's data instead of with a code
    change: `registry.answerable()` offers only the questions whose module is on, so the day a
    tenant starts ingesting a feed and its module is switched on, the matching questions appear.
    A read failure returns None, which keeps every question offered — the endpoints remain the gate,
    so the failure can only lose the "we don't have that feed yet" nicety, never open anything."""
    name = "your company"
    try:
        rows = (client.schema("storeops").table("tenants").select("name")
                .eq("org_id", org_id).limit(1).execute().data) or []
        if rows and rows[0].get("name"):
            name = rows[0]["name"]
    except Exception:
        pass
    mods = None
    try:
        rows = (client.schema("storeops").table("tenant_modules")
                .select("module_key,is_enabled").eq("org_id", org_id).execute().data) or []
        mods = sorted(r["module_key"] for r in rows if r.get("is_enabled") and r.get("module_key"))
    except Exception:
        mods = None
    return name, mods


@router.get("/data-qa/status")
def data_qa_status(org_id: str = ORG_ID, authorization: str = Header(default="")):
    """Whether this login can use the data assistant, and what it can answer for this tenant.

    `questions` is the catalog itself (label + the owner-worded "answers" + parameters), so the UI
    can show real examples that GROW as the tenant's modules come on, rather than a hard-coded list
    of suggestions that goes stale. `allowed` is the shared guard's own verdict, asked here so the
    panel can explain itself instead of the user discovering a refusal by typing a question."""
    client = _sb()
    enabled = _module_enabled(org_id, MODULE_KEY)
    _name, mods = _tenant_context(client, org_id)
    caller = _gate.resolve_caller(client, authorization, org_id)
    decision, _cfg = _gate.decide(client, org_id=org_id, purpose=_agent.PURPOSE, caller=caller,
                                  subject="status")
    return {"module_enabled": bool(enabled),
            "configured": bool(settings.ANTHROPIC_API_KEY),
            "allowed": bool(decision.get("allow")),
            "reason": None if decision.get("allow") else decision.get("reason"),
            "model": settings.DATA_QA_MODEL,
            "questions": reg.catalog(mods)}


class DataQaIn(LaxModel):
    message: str = ""
    question: str = ""
    history: list = []


@router.post("/data-qa")
async def data_qa(body: DataQaIn, request: Request, org_id: str = ORG_ID,
                  authorization: str = Header(default="")):
    """Ask a question about this tenant's own data.

    The module entitlement is checked first and raises; everything after it DEGRADES to a sentence,
    because an operator asking "which store is best" must never be shown a stack trace — and because
    a refusal, an unconfigured key and a slow model are different facts the user can act on
    differently.

    `org_id` is the acting tenant the caller's token resolved to (TenantScopeMiddleware), never a
    value this handler trusts from a body — and it is not passed to the reports either: they resolve
    it from the same token, the same way the browser's call does."""
    if not _module_enabled(org_id, MODULE_KEY):
        raise HTTPException(403, "The in-app assistant is not enabled for this company.")
    question = (body.message or body.question or "").strip()
    if not question:
        raise HTTPException(400, "message required")

    client = _sb()
    tenant_name, mods = _tenant_context(client, org_id)
    caller = _gate.resolve_caller(client, authorization, org_id)
    today = datetime.now(timezone.utc).date()

    return await _agent.answer(
        request.app, org_id=org_id, question=question, history=(body.history or []),
        tenant_name=tenant_name, enabled_modules=mods, authorization=authorization,
        caller=caller, client=client, today=today)
