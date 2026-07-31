"""Event-loop / worst-case-latency limits for the finance module's outbound AI calls.

SEV-1 2026-07-30: the synchronous Anthropic client called from an `async def` FastAPI endpoint runs
its HTTP request ON the single uvicorn event loop, so every other request — including /health —
stalls until it returns. The SDK defaults to a 600s timeout with 2 automatic retries (~30 minutes
worst case), which is how one Ask-AI request froze the entire backend.

The finance module has two such calls: `engine._narrate` (P&L narrative) and `recon._missed_days`
(VIP credit-memo missed-days note). Both are fixed in two layers:

  1. PRIMARY — their (synchronous) callers `engine.compute_and_store` and `recon.reconcile` are
     invoked via `run_in_threadpool` from `account/router.py`, so the blocking work never touches
     the event loop. That is what stops one slow call taking the platform down.
  2. DEFENCE IN DEPTH — the limits below cap a single stalled call at
     ACCOUNT_AI_TIMEOUT_S x (1 + ACCOUNT_AI_MAX_RETRIES). Without them a hung call would pin a
     worker thread and leave the caller's HTTP request open for ~30 minutes.

Both are env-tunable so the operator can widen or tighten with no deploy (same convention as
helpdesk's AI_ASSIST_TIMEOUT_S / AI_ASSIST_MAX_RETRIES), and a garbage env value falls back to the
default rather than breaking module import.

Default 90s (not the 30s used for the interactive Ask-AI assistant): both finance calls use extended
thinking (`thinking={"type": "adaptive"}`, medium effort) over a large JSON payload and legitimately
run long, and neither is interactive. 90s x 2 attempts = ~180s worst case, down from ~1800s. Raise
ACCOUNT_AI_TIMEOUT_S if narratives start reporting "(Narrative unavailable: APITimeoutError.)".

NOTE: these bound the NARRATIVE only. Every P&L / Balance-Sheet / recon FIGURE is deterministic and
computed before any AI call; a timeout costs commentary, never a dollar amount.
"""
import os

try:
    ACCOUNT_AI_TIMEOUT_S = max(1.0, float(os.getenv("ACCOUNT_AI_TIMEOUT_S") or 90))
except Exception:
    ACCOUNT_AI_TIMEOUT_S = 90.0

try:
    ACCOUNT_AI_MAX_RETRIES = max(0, int(os.getenv("ACCOUNT_AI_MAX_RETRIES") or 1))
except Exception:
    ACCOUNT_AI_MAX_RETRIES = 1
