"""Request-body size cap — H5 (unbounded uploads), 2026-08-05 security audit.

STEWARD CHANGE (`backend/app/core/**` is on the SHARED list). Registered once in `main.py`; no module
router changes, no per-endpoint edits. Every module's upload route inherits it.

WHAT WAS WRONG. There was NO limit of any kind on a request body. Roughly 25 endpoints do
`contents = await file.read()` and hand the bytes to `pd.read_excel(...)`, so a single unauthenticated
POST of an arbitrarily large body — or a small, highly-compressed xlsx — buffers without bound and
takes the (single-worker) container down for every tenant.

WHAT THIS DOES.
  1. Rejects on the DECLARED `Content-Length` before a single byte of body is read (the normal case).
  2. Counts bytes as they actually arrive, for chunked / `Transfer-Encoding` bodies where the header
     lies or is absent. Over the cap ⇒ the request is aborted with the same 413.
Both paths answer with a plain 413 that NAMES the limit and the knob, so an operator who hits it
knows exactly what to change.

THE NUMBER (`MAX_UPLOAD_MB`, default 64) AND ITS EVIDENCE.
  · Largest upload this app is DOCUMENTED to ingest today: the full-month "Sales Transaction Details"
    workbook the mailbox feed receives — **7 MB** (HANDOFF.md, the hourly-duplicate note).
  · Largest KNOWN dataset re-uploaded in one request: the asset ledger wipe-and-reload, 43,849 rows.
    Rebuilt at that size with high-entropy cell text (worst case for xlsx shared-string compression):
    **9.49 MB**.
  · A deliberately hostile synthetic 50,000-row x 78-column sales workbook — larger than anything the
    product has ever seen — measures **28.3 MB**.
  · Phone-camera evidence photos (storevisit / closing envelope) run well under 15 MB.
  ⇒ 64 MB is 9.1x the largest documented real upload, 6.7x the worst-case asset reload and 2.3x the
    hostile synthetic. Nothing in use today is anywhere near it, and "unbounded" becomes "bounded".

KNOBS (env, no code change):
  · `MAX_UPLOAD_MB` — the cap in MiB. Raise it if a genuine upload ever grows past it.
  · `MAX_UPLOAD_MB=0` — break-glass OFF. Restores the exact pre-2026-08-05 behaviour.

NOT A ZIP-BOMB DEFENCE ON ITS OWN. It bounds the INPUT; the 10-100x inflation of a crafted xlsx is
bounded separately by pandas, whose openpyxl reader already loads every workbook with
`read_only=True, data_only=True, keep_links=False` (pandas 2.2.3, `io/excel/_openpyxl.py`) — see the
harness, which asserts that rather than trusting it.
"""
import asyncio
import os
import time

_DEFAULT_MB = 64
_LOG_MIN_GAP = 60.0          # seconds between failure_log rows; a flood must not hammer the DB
_last_log = [0.0]            # list = mutable module state without a `global`


def max_upload_bytes() -> int:
    """The cap in bytes. <= 0 disables the middleware entirely (break-glass)."""
    try:
        mb = float(os.environ.get("MAX_UPLOAD_MB", _DEFAULT_MB))
    except (TypeError, ValueError):
        mb = _DEFAULT_MB
    if mb <= 0:
        return 0
    return int(mb * 1024 * 1024)


def _too_large_body(limit: int) -> bytes:
    mb = limit / (1024 * 1024)
    return (
        b'{"detail":"That file is larger than this server accepts (limit '
        + f"{mb:.0f}".encode()
        + b' MB). Split the export into smaller files, or ask an administrator to raise '
          b'MAX_UPLOAD_MB.","code":"request_too_large"}'
    )


async def _reject_413(send, limit: int) -> None:
    body = _too_large_body(limit)
    await send({"type": "http.response.start", "status": 413,
                "headers": [(b"content-type", b"application/json"),
                            (b"content-length", str(len(body)).encode())]})
    await send({"type": "http.response.body", "body": body})


def _log_oversize(path: str, method: str, declared, limit: int) -> None:
    """Best-effort, THROTTLED `core.failure_log` row so an operator can SEE a legitimate upload being
    turned away (the whole risk of this change) instead of guessing. Wrapped end to end: a missing
    mig 112, or the DB being down, must never turn a 413 into a 500."""
    now = time.time()
    if now - _last_log[0] < _LOG_MIN_GAP:
        return
    _last_log[0] = now
    try:
        from app.core.database import get_supabase
        org = os.environ.get("PLATFORM_ORG_ID", "00000000-0000-0000-0000-000000000001")
        get_supabase().schema("core").table("failure_log").insert({
            "org_id": org, "category": "upload", "severity": "warning",
            "source": f"{method} {path}"[:200],
            "message": f"Upload rejected: body larger than the {limit // (1024 * 1024)} MB cap"[:1000],
            "detail": {"path": path, "method": method, "limit_bytes": limit,
                       "declared_content_length": declared},
            "remediation": ("If this was a REAL business file, raise the Railway env var MAX_UPLOAD_MB "
                            "(currently %d) and redeploy; MAX_UPLOAD_MB=0 disables the cap entirely. "
                            "If it was not, this is the DoS guard doing its job."
                            % (limit // (1024 * 1024))),
        }).execute()
    except Exception:
        pass


class _BodyTooLarge(BaseException):
    """Raised out of the wrapped `receive` when a streaming body passes the cap.

    Deliberately a BaseException, NOT an Exception: FastAPI's form parser catches bare `Exception`
    and rewrites it to a generic 400, which would hide the real reason the upload was refused."""


class BodySizeLimitMiddleware:
    """Pure ASGI (same shape as TenantScopeMiddleware — reliable, and it never buffers a body)."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope.get("type") != "http":
            return await self.app(scope, receive, send)
        limit = max_upload_bytes()
        if limit <= 0:                                  # break-glass: MAX_UPLOAD_MB=0
            return await self.app(scope, receive, send)
        method = (scope.get("method") or "GET").upper()
        if method in ("GET", "HEAD", "OPTIONS"):        # no body to police
            return await self.app(scope, receive, send)

        path = scope.get("path", "")
        headers = scope.get("headers") or []

        # (1) Declared size — reject before reading anything at all.
        declared = None
        for k, v in headers:
            if k.lower() == b"content-length":
                try:
                    declared = int(v)
                except (TypeError, ValueError):
                    declared = None
                break
        if declared is not None and declared > limit:
            await asyncio.to_thread(_log_oversize, path, method, declared, limit)
            return await _reject_413(send, limit)

        # (2) Actual bytes — covers a chunked body and a lying Content-Length.
        #
        # WHY THE 413 IS SENT FROM INSIDE `receive` AND NOT FROM AN `except` CLAUSE.
        # FastAPI wraps its form parsing in a bare `except Exception` and rewrites ANY failure to
        # `400 {"detail":"There was an error parsing the body"}` (fastapi/routing.py). An exception
        # raised out of `receive` therefore never reaches this middleware on a multipart upload —
        # measured, and the reason harness section E7c exists. So the middleware ANSWERS FIRST
        # (its own 413), then swallows whatever the aborted app tries to send afterwards. The
        # exception is still raised to unwind the handler promptly, and it derives from
        # BaseException specifically so a well-meaning `except Exception` cannot turn a refused
        # upload into a confusing 500/400.
        seen = [0]
        answered = [False]
        started = [False]

        async def guarded_receive():
            message = await receive()
            if message.get("type") == "http.request":
                seen[0] += len(message.get("body") or b"")
                if seen[0] > limit:
                    if not answered[0]:
                        answered[0] = True
                        await asyncio.to_thread(_log_oversize, path, method, declared, limit)
                        if not started[0]:
                            await _reject_413(send, limit)
                    raise _BodyTooLarge()
            return message

        async def guarded_send(message):
            if answered[0]:
                return                       # we already answered 413; drop the app's late output
            if message.get("type") == "http.response.start":
                started[0] = True
            await send(message)

        try:
            return await self.app(scope, guarded_receive, guarded_send)
        except _BodyTooLarge:
            return None                      # already answered above
        except BaseException:
            # Some frameworks convert our abort into their own error before it can propagate. If we
            # have already answered, that is expected and harmless — swallow it. Otherwise re-raise.
            if answered[0]:
                return None
            raise
