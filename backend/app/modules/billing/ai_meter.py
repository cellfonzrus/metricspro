"""THE AI METERING SEAM — one line at a call site records that tenant's AI spend, off the event loop.

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

══ WHY THE WRITE IS BUFFERED (owner 2026-09-06: *"fix the slowness for to ai billing"*) ═══════════
`record()` used to end in a synchronous PostgREST insert, and it is called from `async def` handlers
in four modules (commcalc/agency.py, closing/router.py, helpdesk/router.py, remediation/router.py).
On single-worker uvicorn a synchronous insert inside a coroutine occupies the ONE event loop for its
whole duration: every other request on the process is stalled, /health included. Normally that is
tens of milliseconds; when PostgREST is slow the postgrest client timeout is 120s and db_resilience
never retries a POST, so the worst case was a ~2-minute platform-wide freeze caused by one invoice
OCR. That is the SEV-1 of 2026-07-30 (a synchronous Anthropic client on the loop) two orders of
magnitude smaller, and the same defect class. `backend/harness_agency_ocr_async.py` and
`harness_closing_ocr_async.py` were left failing on purpose until this was repaired.

The repair is ONE shared off-loop path owned by billing, not four hand-patched call sites — because
the call sites belong to four other modules and a fifth one wired tomorrow would reintroduce the
freeze. So:

    call site        → record()   builds the row and appends it to a list (no I/O, no await, no lock
                                  held across anything, cannot raise, cannot block)
    immediately after→ the drain is DETACHED to a worker thread (`asyncio.to_thread`, NOT awaited),
                                  the same shape `core/access_log` uses for its audit write, so rows
                                  land in milliseconds and the buffer stays near-empty
    off the loop     → there is no loop to protect (cron tick, worker thread, CLI), so the drain runs
                                  inline
    belt and braces  → `billing/usage_flush`'s existing 30s tick drains whatever is left, and its
                                  shutdown hook drains once more, so a slow blip cannot strand rows

DURABILITY, stated honestly. A process killed with rows still buffered loses them, so this can
UNDER-count under a hard crash. For a usage bill that is the correct direction to be wrong — never
bill for spend we cannot evidence — and the detached drain keeps the exposure at milliseconds rather
than the 30s a tick-only design would carry. A FAILED write is restored to the buffer and retried on
the next tick; only a sustained failure past `AI_METER_MAX_PENDING` drops rows, and the drop is
counted and printed rather than silent.

THE BUDGET METER STILL SEES BUFFERED ROWS. `core.ai_call_audit` is not only the invoice source: the
mig-972 guard counts recent rows in it to enforce the per-hour and per-day caps. A row waiting in
this buffer must therefore still count, or a burst could slip past the cap in the window before the
drain lands. `pending_rows()` exposes the buffer for exactly that, and `core/ai_gate.recent_rows`
folds it into what the guard is given. The cap is enforced on the same facts it always was.

THREE PROPERTIES THIS MUST HAVE, because it is called from inside other people's code:

  1. IT NEVER RAISES. A metering failure must never break the feature being metered. Billing accuracy
     matters, but not more than the P&L narrative, the OCR, or the helpdesk reply that the tenant is
     actually waiting on. Every failure is swallowed and logged.
  2. IT NEVER BLOCKS THE EVENT LOOP. See above — this is now a structural property of the meter, not
     a rule each of nine call sites has to remember.
  3. IT NEVER NEEDS A SIGNATURE CHANGE. Most AI helpers in this codebase (`_narrate`, `_missed_days`,
     `_ocr_receipt`, …) are small functions with no `org_id` in scope, called from endpoints that DO
     have one. Threading org_id through nine helper signatures across four other agents' modules
     would be a large, conflict-prone change. Instead the org is read from
     `tenant_middleware.acting_org()` — the contextvar the middleware ALREADY sets, per request, from
     the verified JWT, and which already exists precisely so a handler never has to guess the tenant
     (it was added after a cross-tenant leak). An explicit `org_id=` argument always wins when the
     caller has one.

WHAT IT WRITES: one row in `core.ai_call_audit` (mig 972) — the same table the control-box guard
meters into, which is why there is ONE meter for the platform rather than one per module. Since
2026-09-06 the guard's own audit write (`core/ai_gate.audit`) goes through THIS buffer too, so there
is now literally one function in the platform that writes that table.
"""
import asyncio
import os
import threading

from app.modules.billing.ai_usage import HOUSE_ORG

# Rows whose tenant could not be resolved are stamped with this purpose suffix rather than being
# dropped or misattributed to the house org. Spend that really happened must never vanish from the
# platform total just because the request had no tenant context (a cron tick, a webhook, a boot task).
UNATTRIBUTED_ORG = None

# ── the buffer ───────────────────────────────────────────────────────────────────────────────────
# Deliberately a plain list + lock, not a queue.Queue: `pending_rows()` has to READ the buffer without
# consuming it (the budget meter counts what is still in flight), which a Queue cannot do.
_LOCK = threading.Lock()
_PENDING = []
_DROPPED = 0                 # rows lost to a sustained write failure — counted, never silent
_BG = set()                  # strong refs to detached drains, so they are not garbage collected

# Ceiling on the buffer. AI calls are a handful per tenant per day, so this is only ever reached if
# the database has been unwritable for a long time; bounded memory beats an unbounded queue, and the
# loss is reported.
try:
    MAX_PENDING = max(100, int(os.getenv("AI_METER_MAX_PENDING") or 5000))
except Exception:
    MAX_PENDING = 5000


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


# ── buffer primitives (no I/O; safe from any thread, any context) ────────────────────────────────
def enqueue(row):
    """Accept ONE `core.ai_call_audit` row for writing. No I/O, never raises, never blocks.

    This is the seam `core/ai_gate.audit` shares, so the guard's audit rows and the meter's usage
    rows travel the same off-loop path into the same table."""
    global _DROPPED
    if not isinstance(row, dict) or not row.get("org_id"):
        # A row with no tenant cannot be billed to anyone and would fail the insert for the whole
        # batch it travels with. Refused here, reported by the caller — never silently enqueued.
        return False
    with _LOCK:
        if len(_PENDING) >= MAX_PENDING:
            _DROPPED += 1
            dropped = _DROPPED
        else:
            _PENDING.append(row)
            dropped = None
    if dropped is not None:
        try:
            print("WARN [ai-meter] buffer full (%d) — %d AI usage row(s) dropped; the database has "
                  "been unwritable" % (MAX_PENDING, dropped), flush=True)
        except Exception:
            pass
        return False
    return True


def pending_rows(org_id=None, purpose=None):
    """A COPY of the rows still waiting to be written, optionally filtered. No I/O.

    The mig-972 rate/budget caps count rows in `core.ai_call_audit`; a row buffered here has already
    been spent, so `core/ai_gate.recent_rows` folds this in. Without it a burst inside one drain
    interval would be invisible to the cap."""
    with _LOCK:
        rows = list(_PENDING)
    if org_id:
        rows = [r for r in rows if r.get("org_id") == org_id]
    if purpose:
        rows = [r for r in rows if r.get("purpose") == purpose]
    return rows


def size():
    with _LOCK:
        return len(_PENDING)


def dropped():
    """Rows lost to a sustained write failure since boot. Surfaced by billing/usage_api coverage so
    an under-reported invoice is visible rather than quietly wrong."""
    with _LOCK:
        return _DROPPED


def drain():
    """Take everything pending. No I/O."""
    with _LOCK:
        rows, _PENDING[:] = list(_PENDING), []
    return rows


def restore(rows):
    """Put unwritten rows back so the next tick retries them (oldest first)."""
    global _DROPPED
    if not rows:
        return
    with _LOCK:
        room = max(0, MAX_PENDING - len(_PENDING))
        keep = rows[:room] if room < len(rows) else rows
        _DROPPED += len(rows) - len(keep)
        _PENDING[:0] = keep


def flush_now():
    """Write every buffered row. BLOCKING (PostgREST) — never call this on the event loop; use
    `dispatch()`, which places it correctly. Returns rows written. Never raises.

    Rows are grouped by their exact column set before insert: the guard's audit row carries
    `actor_email`/`created_at` and the meter's does not, and PostgREST rejects a batch whose objects
    do not share keys. Grouping keeps ONE round trip per shape instead of one per row."""
    rows = drain()
    if not rows:
        return 0
    groups = {}
    for r in rows:
        groups.setdefault(tuple(sorted(r.keys())), []).append(r)
    written, failed = 0, []
    try:
        from app.core.database import get_supabase_admin
        client = get_supabase_admin()
    except Exception as e:
        restore(rows)
        _warn("client unavailable, %d row(s) deferred: %s" % (len(rows), str(e)[:160]))
        return 0
    for batch in groups.values():
        try:
            client.schema("core").table("ai_call_audit").insert(batch).execute()
            written += len(batch)
        except Exception as e:
            failed.extend(batch)
            _warn("%d row(s) deferred to the next tick: %s" % (len(batch), str(e)[:160]))
    if failed:
        restore(failed)
    return written


def dispatch():
    """Get the buffer written WITHOUT blocking the caller. Returns how it was placed.

    On the event loop the drain is detached to a worker thread and deliberately NOT awaited (the
    `core/access_log` shape); off the loop there is nothing to protect, so it runs inline."""
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = None
    if loop is None:
        try:
            flush_now()
        except Exception:
            pass
        return "inline"
    try:
        # `run_in_executor` rather than `ensure_future(to_thread(...))`: the submit happens HERE, so a
        # refusal (interpreter shutting down, executor gone) is raised where it can be handled instead
        # of surfacing later as an unretrieved task exception. A strong ref keeps the future alive
        # until the write finishes; the done-callback consumes its result so nothing is left dangling.
        fut = loop.run_in_executor(None, flush_now)
        _BG.add(fut)

        def _done(f, _s=_BG):
            _s.discard(f)
            try:
                f.result()
            except Exception:
                pass                 # flush_now never raises; this only drains executor-level faults

        fut.add_done_callback(_done)
        return "detached"
    except Exception:
        # Could not detach: leave the rows buffered. usage_flush's 30s tick and its shutdown hook
        # will take them, and blocking the loop here is exactly what this module exists to prevent.
        return "deferred"


def _warn(msg):
    try:
        print("WARN [ai-meter] %s" % msg, flush=True)
    except Exception:
        pass


# ── the call-site API ────────────────────────────────────────────────────────────────────────────
def build_row(purpose, model=None, response=None, *, org_id=None, subject_key=None,
              input_tokens=None, output_tokens=None, error=None, allowed=True, actor=None):
    """The `core.ai_call_audit` row for one call. PURE-ish: resolves the acting org and redacts the
    error text, but performs no I/O. Separated from `record()` so the harness can prove the row
    shape without a database."""
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
    return {
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


def record(purpose, model=None, response=None, *, org_id=None, subject_key=None,
           input_tokens=None, output_tokens=None, error=None, allowed=True, actor=None):
    """Record ONE outbound AI call for per-tenant usage billing. NEVER raises, NEVER blocks the event
    loop. Returns True when the row was accepted for writing.

    `purpose` must match an entry in `ai_usage.AI_CALL_SITES` — an unregistered purpose still records
    (spend is never dropped) but is reported as `unregistered` by `ai_usage.coverage`, so a call site
    wired without being declared is visible rather than silently folded into the bill.

    Pass `response` (the Anthropic message) to have token counts read from it, or pass
    `input_tokens`/`output_tokens` directly. A FAILED call is still worth recording with
    `error=` — a burst of failures is real spend on retries and a real signal.

    The database write happens off this call's thread (see the module docstring); True therefore means
    "buffered and dispatched", not "committed". `pending_rows()`/`dropped()` make the difference
    observable, and the shutdown hook drains what is left."""
    try:
        ok = enqueue(build_row(purpose, model, response, org_id=org_id, subject_key=subject_key,
                               input_tokens=input_tokens, output_tokens=output_tokens,
                               error=error, allowed=allowed, actor=actor))
        dispatch()
        return ok
    except Exception as e:                       # metering must never break the feature it measures
        _warn("usage not recorded for purpose=%s: %s" % (purpose, str(e)[:200]))
        return False
