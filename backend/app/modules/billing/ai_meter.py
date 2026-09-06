"""THE AI METERING SEAM — one line at a call site records that tenant's AI spend.

OWNER DIRECTIVE 2026-09-05: *"For every tenant ai usage counter needs to be built…"*. **For every
tenant** is the hard part: a counter fed only by the call sites that happen to be wired UNDER-REPORTS
real spend and UNDER-BILLS the tenant, while looking authoritative. So this module exists to make
wiring a call site as close to free as possible — import, call, done:

    from app.modules.billing import ai_meter
    ...
    resp = await cli.messages.create(...)
    ai_meter.record("helpdesk_ai_assist", settings.ACCOUNT_ENGINE_MODEL, resp)

METERING IS NOT AUTHORIZATION. This is the distinction the owner's guard depends on, and it is why
`record()` deliberately performs NO permission check and grants NO permission:

  · `core/control_box.ai_guard_decision` decides WHO MAY SPEND the key. It is fail-closed,
    purpose-locked, rate- and budget-limited, and this module does not touch it. Since 2026-09-06
    each PURPOSE names its own authorizing predicate (super-admin for the control box; the helpdesk
    module + market/company scope for remediation triage, mig 982) — a wider predicate on one
    purpose widens nothing else, and an unregistered purpose is refused.
  · `record()` only observes that a call HAPPENED, so the tenant can be billed for it.

Adding `record()` to a call site therefore cannot widen anyone's access — it cannot say yes to
anything. That separation is what let every call site be metered TODAY while the question of the
guard's authorization surface was still open. (The insurance/lease extraction was the live example:
its authorization is `can_see_lease`, not super-admin. Since mig 983 it is guarded too — by a purpose
whose PREDICATE is `can_see_lease` — so its authorization is still exactly what it was, and metering
still had nothing to do with granting it.)

THREE PROPERTIES THIS MUST HAVE, because it is called from inside other people's code:

  1. IT NEVER RAISES. A metering failure must never break the feature being metered. Billing accuracy
     matters, but not more than the P&L narrative, the OCR, or the helpdesk reply that the tenant is
     actually waiting on. Every failure is swallowed and logged.
  2. IT NEVER BLOCKS MEANINGFULLY. One small insert against an indexed table, after the model call
     that just took seconds. No AI call site is on a hot loop.
  3. IT NEVER NEEDS A SIGNATURE CHANGE. Most AI helpers in this codebase (`_narrate`, `_missed_days`,
     `_ocr_receipt`, …) are small functions with no `org_id` in scope, called from endpoints that DO
     have one. Threading org_id through nine helper signatures across four other agents' modules
     would be a large, conflict-prone change. Instead the org is read from
     `tenant_middleware.acting_org()` — the contextvar the middleware ALREADY sets, per request, from
     the verified JWT, and which already exists precisely so a handler never has to guess the tenant
     (it was added after a cross-tenant leak). An explicit `org_id=` argument always wins when the
     caller has one.

WHAT IT WRITES: one row in `core.ai_call_audit` (mig 972) — the same table the control-box guard
meters into, which is why there is ONE meter for the platform rather than one per module.
"""
from app.modules.billing.ai_usage import HOUSE_ORG

# Rows whose tenant could not be resolved are stamped with this purpose suffix rather than being
# dropped or misattributed to the house org. Spend that really happened must never vanish from the
# platform total just because the request had no tenant context (a cron tick, a webhook, a boot task).
UNATTRIBUTED_ORG = None


def _acting_org():
    """The tenant THIS request acts as, from the middleware's already-validated contextvar. None when
    there is no request context (cron, startup, worker thread)."""
    try:
        from app.core.tenant_middleware import acting_org
        return acting_org()
    except Exception:
        return None


def usage_of(response):
    """(input_tokens, output_tokens) from an Anthropic response, defensively. PURE-ish, never raises.

    Reads `response.usage`; a shape change upstream costs us the token counts for that call, not an
    exception inside somebody's OCR path."""
    try:
        u = getattr(response, "usage", None)
        if u is None:
            return 0, 0
        return int(getattr(u, "input_tokens", 0) or 0), int(getattr(u, "output_tokens", 0) or 0)
    except Exception:
        return 0, 0


def record(purpose, model=None, response=None, *, org_id=None, subject_key=None,
           input_tokens=None, output_tokens=None, error=None, allowed=True, actor=None):
    """Record ONE outbound AI call for per-tenant usage billing. NEVER raises. Returns True if stored.

    `purpose` must match an entry in `ai_usage.AI_CALL_SITES` — an unregistered purpose still records
    (spend is never dropped) but is reported as `unregistered` by `ai_usage.coverage`, so a call site
    wired without being declared is visible rather than silently folded into the bill.

    Pass `response` (the Anthropic message) to have token counts read from it, or pass
    `input_tokens`/`output_tokens` directly. A FAILED call is still worth recording with
    `error=` — a burst of failures is real spend on retries and a real signal."""
    try:
        ti, to = (int(input_tokens or 0), int(output_tokens or 0))
        if response is not None and not (ti or to):
            ti, to = usage_of(response)
        org = org_id or _acting_org() or UNATTRIBUTED_ORG
        if not org:
            # No tenant context. We still want the platform total to be right, so the row is stamped
            # to the HOUSE org — the platform's own tenant — rather than dropped. It is attributed to
            # the operator, never invented onto a paying tenant.
            org = HOUSE_ORG
        # Redaction is shared with the control box: an error string has historically carried
        # connection URLs and tokens, and this text is stored.
        from app.modules.core.control_box import redact
        from app.core.database import get_supabase_admin
        row = {
            "org_id": org,
            "purpose": str(purpose or "unknown")[:120],
            "subject_key": (str(subject_key)[:80] if subject_key else None),
            "actor_uid": (actor or {}).get("id") if isinstance(actor, dict) else actor,
            "allowed": bool(allowed),
            "deny_code": None,
            "model": (str(model)[:120] if model else None),
            "input_tokens": max(0, ti),
            "output_tokens": max(0, to),
            "error": (redact(error)[:300] or None) if error else None,
        }
        get_supabase_admin().schema("core").table("ai_call_audit").insert(row).execute()
        return True
    except Exception as e:                       # metering must never break the feature it measures
        try:
            print("WARN [ai-meter] usage not recorded for purpose=%s: %s"
                  % (purpose, str(e)[:200]), flush=True)
        except Exception:
            pass
        return False
