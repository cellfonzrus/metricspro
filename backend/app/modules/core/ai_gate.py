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
from app.core.config import settings
from app.modules.core import control_box as cbx

HOUSE_ORG = "00000000-0000-0000-0000-000000000001"     # CLAUDE.md house org (RULE TWO defaults)

_CONFIG_KEYS = ("enabled", "max_calls_per_hour", "daily_call_cap", "daily_token_cap",
                "max_input_chars")


def budget_config(client, org_id, purpose):
    """This org's row > the HOUSE row > `control_box.DEFAULT_AI_CONFIG`, for ONE purpose.
    RULE TWO: a tenant's AI ceiling is a config row, not a constant. A read failure falls back to
    the house/code ceiling — it can only ever make the ceiling the DEFAULT one, never unlimited."""
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
    return cfg


def recent_rows(client, org_id, purpose, limit=500):
    """This org's audit rows for ONE purpose — what the meter counts. Org-scoped. A read failure
    returns [] so the guard falls back to its house ceiling rather than failing open on the AUTH
    gate (authorization was already decided, and it is never decided here)."""
    try:
        return (client.schema("core").table("ai_call_audit")
                .select("allowed,created_at,input_tokens,output_tokens")
                .eq("org_id", org_id).eq("purpose", purpose)
                .order("created_at", desc=True).limit(limit).execute().data) or []
    except Exception:
        return []


def audit(client, row, label="ai-gate"):
    """Best-effort audit write — allowed AND refused attempts. A failed audit must not swallow the
    caller's answer, but it is printed (redacted) so a silently unauditable AI path is visible."""
    try:
        client.schema("core").table("ai_call_audit").insert(row).execute()
    except Exception as e:
        print("WARN [%s] AI audit write failed: %s" % (label, cbx.redact(e)), flush=True)


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
