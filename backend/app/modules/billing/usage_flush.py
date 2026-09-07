"""THE USAGE FLUSHER — turns in-memory per-module counters into database rows, off the request path.

Owner directive 2026-09-05: *"it should bill each call on all modules"*. The counting itself is a dict
increment in `module_usage.UsageAccumulator` (see that module's docstring for why a row-per-call was
rejected). This is the other half: draining that accumulator periodically and folding the batch into
`core.module_usage_daily` through ONE additive RPC.

WHY A FLUSHER AND NOT A WRITE PER REQUEST. The request path must pay no I/O for billing. This
platform took a SEV-1 on 2026-07-30 from work done inline on the event loop, and `core.access_log` —
the only existing per-request writer — avoids it only by detaching its insert. A billing write per
call would be that same hazard, thousands of times a minute, forever. So:

    request path        → accumulator.add()          (a dict increment; no I/O, no await, no lock held
                                                      across anything)
    every FLUSH_SECONDS → accumulator.drain() → ONE  `core.bump_module_usage(batch)` RPC, on a worker
                                                      thread, never on the event loop

DURABILITY, stated honestly. A process killed between flushes loses at most one interval of counts,
so this UNDER-counts under a hard crash. For a usage bill that is the correct direction to be wrong —
never bill for calls we cannot evidence. A FAILED flush is restored into the accumulator and retried
on the next tick, so a transient database blip costs nothing.

The flush is ADDITIVE server-side (`calls = calls + excluded.calls`), which is what makes several
backend processes safe to run concurrently: they each fold their own counts in, nobody overwrites.
"""
import asyncio
import os

from app.modules.billing.module_usage import UsageAccumulator

# The ONE accumulator for this process. Imported by the access-log middleware.
ACCUMULATOR = UsageAccumulator()

try:
    FLUSH_SECONDS = max(5.0, float(os.getenv("USAGE_FLUSH_SECONDS") or 30))
except Exception:
    FLUSH_SECONDS = 30.0

# Above this many pending counter rows, flush early rather than waiting for the tick — bounds how
# much a crash can lose when a burst of tenants/modules is active.
try:
    FLUSH_MAX_ROWS = max(50, int(os.getenv("USAGE_FLUSH_MAX_ROWS") or 500))
except Exception:
    FLUSH_MAX_ROWS = 500

_TASK = None


def flush_now():
    """Drain and write one batch. BLOCKING (PostgREST) — always call via a worker thread.
    Returns the number of counter rows written. Never raises."""
    rows = ACCUMULATOR.drain()
    if not rows:
        return 0
    try:
        from app.core.database import get_supabase_admin
        get_supabase_admin().schema("core").rpc("bump_module_usage", {"p_rows": rows}).execute()
        return len(rows)
    except Exception as e:
        # Put the counts BACK so a database blip loses no usage; the next tick retries them.
        ACCUMULATOR.restore(rows)
        try:
            print("WARN [usage-flush] %d counter row(s) deferred to the next tick: %s"
                  % (len(rows), str(e)[:200]), flush=True)
        except Exception:
            pass
        return 0


def flush_ai_now():
    """Drain the AI usage/audit buffer (`billing/ai_meter`). BLOCKING — worker thread only.
    Returns rows written. Never raises.

    The meter normally drains itself the instant a call is recorded, detached to a worker thread.
    This tick is the BACKSTOP for the cases that detach cannot cover: a write that failed and was
    restored, a row recorded while the loop was too busy to schedule the hop, a process where
    nothing has called `dispatch()` recently. It shares this loop rather than starting a second
    one — one background writer for billing, which is why the flusher lives here."""
    try:
        from app.modules.billing import ai_meter
        return ai_meter.flush_now()
    except Exception as e:
        try:
            print("WARN [usage-flush] AI usage backstop failed: %s" % str(e)[:200], flush=True)
        except Exception:
            pass
        return 0


async def _loop():
    while True:
        try:
            await asyncio.sleep(FLUSH_SECONDS)
            if ACCUMULATOR.size():
                await asyncio.to_thread(flush_now)
            if _ai_pending():
                await asyncio.to_thread(flush_ai_now)
        except asyncio.CancelledError:
            break
        except Exception:
            # The flusher must outlive any single failure — a billing counter that stops counting
            # because of one bad tick is worse than a late one.
            pass


def _ai_pending():
    try:
        from app.modules.billing import ai_meter
        return ai_meter.size()
    except Exception:
        return 0


def start():
    """Start the background flusher. Idempotent; safe to call from a startup hook."""
    global _TASK
    if _TASK is not None and not _TASK.done():
        return "already running"
    try:
        _TASK = asyncio.ensure_future(_loop())
        return "flushing every %ss" % int(FLUSH_SECONDS)
    except Exception as e:
        return "not started: %s" % str(e)[:120]


async def stop():
    """Cancel the loop and flush what is pending — module counters AND buffered AI usage rows — so a
    graceful shutdown loses nothing."""
    global _TASK
    if _TASK is not None:
        _TASK.cancel()
        _TASK = None
    for fn in (flush_now, flush_ai_now):
        try:
            await asyncio.to_thread(fn)
        except Exception:
            pass
