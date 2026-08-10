"""Worst-case-latency limits for the closing module's outbound AI call.

There is exactly one: `router._ocr_deposit_amount`, which reads the amount off a bank deposit slip
with Claude vision when a rep uploads it.

It shipped with a BARE `Anthropic(api_key=...)` — no timeout, no max_retries — so it inherited the
SDK defaults of 600s x 2 retries, ~30 MINUTES worst case for a single stuck slip.

This is NOT the 2026-07-30 event-loop SEV-1. Its caller `record_deposit` is a synchronous `def`
endpoint, so uvicorn already runs it on the threadpool and the event loop is never blocked. The
exposure is THREAD-POOL STARVATION instead: each stuck OCR call pins one of ~40 worker threads for
its whole duration, and the workload is exactly the shape that stacks them up — every store closes
its day inside the same hour, so the uploads arrive together rather than spread out. Enough
simultaneous slow calls and the backend has no free worker for any request, including ones that never
touch Claude. The symptom reads as "the whole app is slow", with nothing in the logs blaming OCR.

60s (not finance's 90s): this call sends one image with `max_tokens=300` and no extended thinking, so
it is inherently short — a call still running at 60s is stuck, not thinking. 60s x 2 attempts = ~120s
worst case, down from ~1800s.

Env-tunable with no deploy, same convention as the finance module's ai_limits and helpdesk's
AI_ASSIST_* pair; a garbage env value falls back to the default rather than breaking module import.

NOTE: a timeout costs only the CONVENIENCE of auto-reading the slip. `_ocr_deposit_amount` already
degrades to (None, {"error": ...}), and `record_deposit` then keeps the rep's manually-entered
deposit_amount. No dollar figure depends on this call succeeding.
"""
import os

try:
    CLOSING_OCR_TIMEOUT_S = max(1.0, float(os.getenv("CLOSING_OCR_TIMEOUT_S") or 60))
except Exception:
    CLOSING_OCR_TIMEOUT_S = 60.0

try:
    CLOSING_OCR_MAX_RETRIES = max(0, int(os.getenv("CLOSING_OCR_MAX_RETRIES") or 1))
except Exception:
    CLOSING_OCR_MAX_RETRIES = 1
