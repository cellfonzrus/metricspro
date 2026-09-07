"""THE ONE I/O SEAM for the shared AI-call guard (mig 972) — config, meter, decision, audit.

`core/control_box.ai_guard_decision` is the PURE decision. This module is the small amount of
DATABASE work every adopter of that decision needs, and it exists so that work is written ONCE:

    from app.modules.core import ai_gate as _gate
    caller   = _gate.resolve_caller(client, authorization, org_id)      # or your own capability dict
    decision, cfg = _gate.decide(client, org_id=org, purpose="…", caller=caller, subject=…)
    if not decision["allow"]:
        _gate.audit(client, cbx.ai_audit_row(org, caller, decision.get("subject_key"), decision,
                                             purpose="…"))
        …degrade…
    …call the model…
    _gate.audit(client, cbx.ai_audit_row(org, caller, decision["subject_key"], decision,
                                         usage=_gate.usage_from_response(resp), model=…, error=…,
                                         purpose="…"))

WHY THIS FILE EXISTS AT ALL (CLAUDE.md duplicate-check build gate). `core/control_box_api.py`
already had `_ai_config` / `_recent_ai_rows` / `_audit`, but they were private to the control box
and hard-coded to its purpose. Three call sites now share the guard, and three private copies of
"resolve the org's ceiling, count the last 24h, write the audit row" is exactly the drift the build
gate exists to stop — the second copy is where a budget silently stops being enforced. So the
control box's helpers now DELEGATE here; nothing was forked, and there is still exactly one place
that reads `core.ai_budget_config` and one that writes `core.ai_call_audit`.

WHAT THIS MODULE DOES NOT DO: it never decides. Every authorization, purpose, input, rate and budget
question is answered by the pure function, so the guard stays provable with no database
(`backend/harness_ai_guard_purposes.py`). This module only fetches what that function is given and
records what it decided.

METERING IS NOT AUTHORIZATION (§21). `billing/ai_meter.record()` observes that a call happened so a
tenant can be billed; it performs no permission check and grants nothing. This module is the other
half: who may SPEND. Both may be present at one call site — they answer different questions.

ORG SCOPING: every read and write here is `.eq("org_id", …)`, and the org handed in is the acting
org the caller's token resolved to, never a value from a request body.
"""
import asyncio
import os
import threading
from datetime import datetime, timedelta, timezone

from app.core.config import settings
from app.modules.core import control_box as cbx

HOUSE_ORG = "00000000-0000-0000-0000-000000000001"     # CLAUDE.md house org (RULE TWO defaults)

_CONFIG_KEYS = ("enabled", "max_calls_per_hour", "daily_call_cap", "daily_token_cap",
                "max_input_chars")


# ── the ceiling cache ────────────────────────────────────────────────────────────────────────────
# Every guarded AI call read the ceiling from the database: TWO PostgREST round trips for a tenant
# org (house row, then the tenant's), on top of the usage read and the audit write, all before the
# model was even called. Owner 2026-09-06: *"fix the slowness for to ai billing"*. A ceiling is
# configuration that changes by hand, perhaps monthly, so it is cached for a few seconds per
# (org, purpose).
#
# WHAT THE CACHE CAN AND CANNOT DO: it can delay a ceiling CHANGE by up to the TTL. It cannot fail
# open — a miss reads the database, a read failure still falls back to the house/code ceiling, and
# nothing here decides authorization (that is `ai_guard_decision`, always evaluated fresh). Set
# `AI_BUDGET_CACHE_SECONDS=0` to disable it entirely.
try:
    CONFIG_TTL_S = max(0.0, float(os.getenv("AI_BUDGET_CACHE_SECONDS") or 30))
except Exception:
    CONFIG_TTL_S = 30.0

_CFG_LOCK = threading.Lock()
_CFG_CACHE = {}          # (org_id, purpose) -> (expires_at_monotonic, cfg)


def invalidate_budget_cache(org_id=None, purpose=None):
    """Forget cached ceilings. Called when a ceiling is written so an operator's change is live at
    once rather than after the TTL."""
    with _CFG_LOCK:
        if org_id is None and purpose is None:
            _CFG_CACHE.clear()
            return
        for k in [k for k in _CFG_CACHE
                  if (org_id is None or k[0] == org_id) and (purpose is None or k[1] == purpose)]:
            _CFG_CACHE.pop(k, None)


def budget_config(client, org_id, purpose, *, use_cache=True):
    """This org's row > the HOUSE row > `control_box.DEFAULT_AI_CONFIG`, for ONE purpose.
    RULE TWO: a tenant's AI ceiling is a config row, not a constant. A read failure falls back to
    the house/code ceiling — it can only ever make the ceiling the DEFAULT one, never unlimited."""
    key = (org_id or HOUSE_ORG, purpose)
    if use_cache and CONFIG_TTL_S > 0:
        now = _monotonic()
        with _CFG_LOCK:
            hit = _CFG_CACHE.get(key)
        if hit and hit[0] > now:
            return dict(hit[1])
    cfg = dict(cbx.DEFAULT_AI_CONFIG)
    scopes = [HOUSE_ORG] if (org_id or HOUSE_ORG) == HOUSE_ORG else [HOUSE_ORG, org_id]
    for scope in scopes:
        try:
            rows = (client.schema("core").table("ai_budget_config").select("*")
                    .eq("org_id", scope).eq("purpose", purpose).limit(1).execute().data) or []
        except Exception:
            rows = []
        for r in rows:
            for k in _CONFIG_KEYS:
                if r.get(k) is not None:
                    cfg[k] = r[k]
    if use_cache and CONFIG_TTL_S > 0:
        with _CFG_LOCK:
            _CFG_CACHE[key] = (_monotonic() + CONFIG_TTL_S, dict(cfg))
    return cfg


def _monotonic():
    try:
        import time
        return time.monotonic()
    except Exception:
        return 0.0


def recent_rows(client, org_id, purpose, limit=500):
    """This org's audit rows for ONE purpose — what the meter counts. Org-scoped. A read failure
    returns [] so the guard falls back to its house ceiling rather than failing open on the AUTH
    gate (authorization was already decided, and it is never decided here).

    Two things this does beyond the obvious select:

      · A 24-HOUR FLOOR. `rollup_usage` counts a 1h window and a 24h window; a row older than that
        contributes nothing, so fetching it is pure latency. Bounding the read server-side keeps the
        payload small for a busy org instead of shipping 500 rows to discard most of them (owner
        2026-09-06, "fix the slowness for to ai billing"). `limit` remains the hard ceiling.
      · THE IN-FLIGHT ROWS. Since the audit write is buffered off the event loop
        (`billing/ai_meter`), a call made moments ago may not be in the table yet. Those rows are
        SPENT and must count against the cap, or a burst inside one drain interval would slip past
        the per-hour limit. Folding them in here keeps the guard deciding on the same facts it
        always did."""
    out = []
    try:
        cutoff = (datetime.now(timezone.utc) - timedelta(hours=24)).isoformat()
        out = (client.schema("core").table("ai_call_audit")
               .select("allowed,created_at,input_tokens,output_tokens")
               .eq("org_id", org_id).eq("purpose", purpose)
               .gte("created_at", cutoff)
               .order("created_at", desc=True).limit(limit).execute().data) or []
    except Exception:
        out = []
    try:
        from app.modules.billing import ai_meter as _meter
        now = datetime.now(timezone.utc).isoformat()
        for r in _meter.pending_rows(org_id=org_id, purpose=purpose):
            out.append({"allowed": r.get("allowed"),
                        "created_at": r.get("created_at") or now,
                        "input_tokens": r.get("input_tokens") or 0,
                        "output_tokens": r.get("output_tokens") or 0})
    except Exception:
        # A meter that cannot be read costs the guard the in-flight rows only — never the stored
        # ones, and never the decision.
        pass
    return out


def audit(client, row, label="ai-gate"):
    """Record ONE attempted AI call — allowed AND refused. Never raises, never blocks the event loop.

    The row is handed to `billing/ai_meter`'s buffer, which drains on a worker thread. That is the
    same sink `ai_meter.record()` uses, so this module's docstring claim — one place reads
    `core.ai_budget_config`, one place writes `core.ai_call_audit` — is now literally true, and the
    guard's audit stopped being a synchronous PostgREST insert inside three `async def` endpoints
    (remediation `_ai_diagnose`, storeops `post_document_extract`, control-box `ai_triage`), which is
    the freeze `harness_agency_ocr_async` was left red for.

    `client` is accepted and unused: the sink resolves its own admin client on the worker thread, and
    removing the parameter would touch six call sites in three other modules for no behaviour."""
    try:
        from app.modules.billing import ai_meter as _meter
        ok = _meter.enqueue(row)
        _meter.dispatch()
        if not ok:
            print("WARN [%s] AI audit row not buffered (no org_id, or the meter is full)"
                  % label, flush=True)
        return ok
    except Exception as e:
        print("WARN [%s] AI audit write failed: %s" % (label, cbx.redact(e)), flush=True)
        return False


def has_key():
    """Is an Anthropic key configured on THIS backend? Read server-side only; the key itself never
    leaves the process and never reaches a response body, a log line or a client-visible error."""
    try:
        return bool((getattr(settings, "ANTHROPIC_API_KEY", "") or "").strip())
    except Exception:
        return False


def decide(client, *, org_id, purpose, caller, subject=None, known_keys=(), lamp=None,
           has_api_key=None):
    """(decision, cfg) for ONE attempted AI call. Reads the org's ceiling and its recent usage, then
    hands both to the PURE `control_box.ai_guard_decision` — which is what actually decides."""
    cfg = budget_config(client, org_id, purpose)
    usage = cbx.rollup_usage(recent_rows(client, org_id, purpose))
    decision = cbx.ai_guard_decision(
        caller, purpose=purpose, subject=subject, known_keys=known_keys, lamp=lamp,
        config=cfg, usage=usage,
        has_key=has_key() if has_api_key is None else bool(has_api_key))
    return decision, cfg


async def decide_async(client, **kw):
    """`decide()` for an `async def` caller. Awaits the same function on a worker thread.

    `decide` is two PostgREST reads. Called bare from a coroutine those reads occupy the single
    uvicorn event loop and stall every other request on the process — the SEV-1 defect class of
    2026-07-30. There is no async PostgREST client here, so the hop is the fix; the decision itself
    is unchanged, still made by the pure function, still fail-closed.

    A failure inside the hop is NOT swallowed: a guard that cannot decide must refuse, and `decide`
    already degrades to the house ceiling on a read failure rather than raising."""
    return await asyncio.to_thread(lambda: decide(client, **kw))


def usage_from_response(resp):
    """{input_tokens, output_tokens} off an Anthropic response, tolerant of any shape. Tokens ONLY:
    `core.token_rates` (mig 718) is the one $/MTok source, so no cost is computed or stored here."""
    u = getattr(resp, "usage", None)
    return {"input_tokens": int(getattr(u, "input_tokens", 0) or 0),
            "output_tokens": int(getattr(u, "output_tokens", 0) or 0)}


def resolve_caller(client, authorization, org_id=None):
    """The signed-in caller as the guard's predicates expect them: {org_id, role, super_admin, perms,
    id, email}. REUSES `core.router._uid_from_token` + `_resolve_caller` — the ONE definition of who
    a token is — and only adds the uid/email the audit row needs.

    FAILS CLOSED: an unverifiable token, an unprovisioned login or any resolver fault returns None,
    and `ai_guard_decision(None, …)` refuses. A caller with an extra capability (e.g. the lease
    gate's verdict) merges it into this dict; the predicate reads the flag, never the database."""
    try:
        from app.modules.core.router import _uid_from_token, _resolve_caller
        from app.modules.core.membership import list_memberships, pick_membership
        uid = _uid_from_token(authorization or "")
        if not uid:
            return None
        caller = _resolve_caller(client, uid, (org_id or "").strip() or None)
        if not caller:
            return None
        row = pick_membership(list_memberships(client, uid), (org_id or "").strip() or None) or {}
        return {**caller, "id": uid, "email": row.get("email")}
    except Exception:
        return None
