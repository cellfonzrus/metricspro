"""SETUP NOTICE — the ONE home for "this feature's setup isn't finished" (owner 2026-09-29, index §19.36).

THE OWNER'S WORDS. Admin → Display Labels said *"Needs migration 068_ui_label_override.sql"*: "this
migration should not be mentioned in customer facing".

THE CLASS, NOT THE INSTANCE. An internal build/setup fact — a migration file name, "apply mig 1030",
"run migration …", a schema.table in a "not applied" hint, "run it in the Supabase SQL editor", a raw
PostgREST "relation … does not exist" — reached CUSTOMER-FACING copy. On 2026-09-29 the backend carried
464 such string literals across 68 modules (HTTPException details, payload `note` / `message` / `hint`
fields, `migration` keys a page printed), and the frontend ~130 rendered sites across ~60 pages. Fixing
them one at a time is the patchwork the house rules forbid: the 465th is written next week.

THE DESIGN — one fact, one home, applied at the boundary every caller already crosses:
  • `SETUP_NOTICE` — the one sentence a customer sees. Neutral, no internals, says what to do.
    The frontend's copy (`frontend/src/lib/setupNotice.tsx`) is LOCKED equal to this one.
  • `SETUP_INTERNAL` — the one detector of setup-internal text. Precise on purpose: the bare word
    "migration" is carrier DATA in wireless retail (a plan / port migration), so a hint is recognised by
    its SHAPE — a `NNN_name.sql` file, "migration 1017" / "mig 944", run / apply / needs / pending … a
    migration, "is not applied / has not been run", "SQL editor", "Supabase", and PostgREST's own
    not-applied errors (PGRST2xx, 42P01 / 42703, "relation … does not exist", "schema cache").
  • `neutralize_text` / `neutralize` — replace every SENTENCE that carries a hint with `SETUP_NOTICE`
    (collapsed once), walking a JSON payload's string values. A short message becomes the notice; a long
    document keeps every other sentence.
  • `SetupNoticeMiddleware` — THE boundary. Every JSON response of the API passes through it (an
    HTTPException's `detail` included: Starlette's ExceptionMiddleware is inner of all user middleware).
    A body carrying a hint is neutralized unless the caller is a platform super-admin — resolved by THE
    one gate, `core.router._require_super_admin` (never a second rung) — and the original is written to
    the server log. A super-admin sees the technical detail unchanged. A body with no hint (every normal
    response) is passed through after a handful of byte searches; nothing is parsed.
  • `report_registry.build_payload` — the one outbound report builder (scheduled / on-demand email and
    WhatsApp sends) — calls `neutralize` too: a report mailed to a tenant never goes through the HTTP
    boundary, and its recipients are never super-admins by construction.

WHAT IS NOT HERE. Code comments, docstrings, log lines and harnesses keep their migration numbers — that
is where they belong. `/openapi.json`, `/docs`, `/redoc` (developer surfaces, route docstrings) are left
verbatim. The lock is `harness_carrier_vocab_guard.py` §SETUP (CI `carrier-vocab-guard.yml`): no hint in
rendered frontend copy outside this home + named super-admin pages; every backend string literal that
names a migration is caught by `SETUP_INTERNAL` (so none can slip past the boundary); the middleware is
registered innermost; `build_payload` dereferences `neutralize`; the two sentences are equal.

PURE except `SetupNoticeMiddleware._is_super_admin` (lazy import of the one gate, run in a thread).
"""
import asyncio
import json
import re
import sys

SETUP_NOTICE = "This feature isn't switched on for your company yet. Contact support to enable it."

# ── THE ONE DETECTOR ────────────────────────────────────────────────────────────────────────────────
# A migration NUMBER is 000–1999 (optionally a letter: 268b) — years (2025) are never read as one.
_MIG_NO = r"(?:\d{3}|1\d{3})[a-z]?\b"
SETUP_INTERNAL = re.compile("|".join([
    r"\b\d{3,4}[a-z]?_[a-z0-9_]+\.sql\b",                                        # 068_ui_label_override.sql
    r"(?<![\w-])mig(?:ration)?s?\s*[#(]?\s*" + _MIG_NO,                        # migration 1017 / mig 944 / (migration 071)
    r"\b(?:run|running|re-?run|apply|applying|applied|ran)\s+(?:the\s+|a\s+|this\s+|any\s+)?(?:[\w-]+\s+){0,2}migrations?\b",
    r"\b(?:needs?|requires?|until|once|after|pending|awaiting)\s+(?:the\s+|a\s+|its\s+)?(?:[\w-]+\s+){0,2}migrations?\b",
    r"\bmigrations?\b[^.;\n]{0,60}?(?:\bnot\s+(?:yet\s+)?(?:been\s+)?(?:applied|run)\b|\bpending\b|\bun-?run\b"
    r"|\bapplied\s*\?|\brun\s*\?|\bhas(?:n['’]t|\s+not)\s+(?:been\s+)?(?:applied|run)\b|\bis(?:n['’]t|\s+not)\s+applied\b"
    r"|\bmust\s+(?:also\s+)?be\s+applied\b|\bmay\s+not\s+be\s+applied\b)",
    r"\b(?:has|have|is|was)\s+(?:the\s+)?(?:[\w-]+\s+){0,2}migrations?\b[^.;\n]{0,30}?\b(?:run|applied)\b",
    r"\b(?:table|column|schema)s?\b[^.;\n]{0,40}?\bnot\s+(?:yet\s+)?(?:been\s+)?(?:applied|created)\b",  # "registry table not applied yet"
    r"\bSQL\s+editor\b",
    r"(?-i:\bSupabase\b)",                                                         # the proper noun, never `supabase.auth`
    r"\bPGRST\d{3}\b|\b42P01\b|\b42703\b",
    r"\brelation\s+\\?\"?[\w.]+\\?\"?\s+does\s+not\s+exist\b",
    r"\bcolumn\s+\\?\"?[\w.]+\\?\"?\s+(?:of\s+relation\s+\\?\"?[\w.]+\\?\"?\s+)?does\s+not\s+exist\b",
    r"\bCould\s+not\s+find\s+the\s+(?:table|function|column)\b",
    r"\bschema\s+cache\b",
]), re.I)
# Cheap byte prefilter for the boundary, over the LOWER-CASED body: a body containing none of these cannot
# match SETUP_INTERNAL, so ~all normal responses are never parsed. Every alternative of the detector has
# an anchor here (every migration phrase contains "mig"); the lock proves each alternative's sample trips it.
_BYTE_MARKERS = (b".sql", b"mig", b"sql editor", b"supabase", b"pgrst", b"42p01", b"42703",
                 b"does not exist", b"could not find the", b"schema cache", b"not applied", b"not yet applied",
                 b"not been applied", b"not created", b"not yet created", b"not been created")


def is_setup_internal(text) -> bool:
    """True when `text` carries an internal setup fact a customer must not see."""
    return isinstance(text, str) and bool(text) and SETUP_INTERNAL.search(text) is not None


_SENTENCE = re.compile(r"(?<=[.!?])\s+(?=\S)")


def neutralize_text(text):
    """Every sentence of `text` that carries a setup hint becomes SETUP_NOTICE (once); every other
    sentence is kept. Text without a hint is returned unchanged (the same object)."""
    if not is_setup_internal(text):
        return text
    out, noticed = [], False
    for part in _SENTENCE.split(text):
        if is_setup_internal(part):
            if not noticed:
                out.append(SETUP_NOTICE)
                noticed = True
        else:
            out.append(part)
    if not noticed:                                  # the hint spanned a sentence break — whole text
        return SETUP_NOTICE
    return " ".join(out)


def neutralize(payload, _hits=None):
    """A JSON-shaped payload with every string VALUE passed through `neutralize_text`. Keys are kept.
    Returns (new_payload, originals) — `originals` lists each replaced string for the server log."""
    hits = [] if _hits is None else _hits
    if isinstance(payload, str):
        new = neutralize_text(payload)
        if new is not payload:
            hits.append(payload)
        return new, hits
    if isinstance(payload, dict):
        return {k: neutralize(v, hits)[0] for k, v in payload.items()}, hits
    if isinstance(payload, list):
        return [neutralize(v, hits)[0] for v in payload], hits
    if isinstance(payload, tuple):
        return tuple(neutralize(v, hits)[0] for v in payload), hits
    return payload, hits


def body_may_carry_hint(body: bytes) -> bool:
    """The boundary's prefilter — a few substring searches, no parse."""
    low = body.lower()
    return any(m in low for m in _BYTE_MARKERS)


def log_neutralized(where, originals):
    """The technical detail goes to the SERVER LOG (stderr, captured by the platform) — never lost."""
    try:
        for o in originals[:20]:
            print(f"[setup-notice] {where}: {str(o)[:400]}", file=sys.stderr, flush=True)
    except Exception:
        pass


# ── THE BOUNDARY ────────────────────────────────────────────────────────────────────────────────────
_SKIP_PATHS = ("/openapi.json", "/docs", "/redoc", "/health")


class SetupNoticeMiddleware:
    """Pure ASGI. Registered INNERMOST (before GZip in main.py) so it sees the uncompressed JSON body,
    and inner of TenantScope. Buffers only `application/json` responses (the API sends each as one body
    message); anything else streams through untouched. Never breaks a response: any fault here passes
    the original body through."""

    def __init__(self, app):
        self.app = app

    @staticmethod
    def _is_super_admin(headers) -> bool:
        """THE one gate (`core.router._require_super_admin`) — the same answer `/me` gives the sidebar.
        Anything but an explicit yes is a no: hiding the detail is the safe failure."""
        try:
            from app.modules.core.router import _require_super_admin
            _require_super_admin(headers.get("authorization", ""), headers.get("x-active-org", ""))
            return True
        except Exception:
            return False

    async def __call__(self, scope, receive, send):
        if scope.get("type") != "http" or (scope.get("path") or "").startswith(_SKIP_PATHS):
            return await self.app(scope, receive, send)
        state = {"start": None, "json": False, "chunks": []}

        async def _send(msg):
            t = msg.get("type")
            if t == "http.response.start":
                ctype = b""
                for k, v in msg.get("headers") or []:
                    if k.lower() == b"content-type":
                        ctype = v.lower()
                state["json"] = b"application/json" in ctype
                if not state["json"]:
                    return await send(msg)
                state["start"] = msg                  # held until the body is known
                return None
            if t == "http.response.body" and state["json"]:
                state["chunks"].append(msg.get("body", b""))
                if msg.get("more_body"):
                    return None
                body = b"".join(state["chunks"])
                new = await self._rewrite(scope, body)
                start = state["start"]
                if new is not body:                   # only a REWRITTEN body changes the headers
                    start = dict(start)
                    start["headers"] = [(k, v) for k, v in (start.get("headers") or []) if k.lower() != b"content-length"]
                    start["headers"].append((b"content-length", str(len(new)).encode()))
                await send(start)
                return await send({"type": "http.response.body", "body": new, "more_body": False})
            return await send(msg)

        await self.app(scope, receive, _send)

    async def _rewrite(self, scope, body):
        try:
            if not body or not body_may_carry_hint(body):
                return body
            data = json.loads(body)
            new, originals = neutralize(data)
            if not originals:
                return body
            headers = {k.decode().lower(): v.decode() for k, v in (scope.get("headers") or [])}
            try:
                sup = await asyncio.to_thread(self._is_super_admin, headers)
            except Exception:
                sup = False                           # a gate fault HIDES the detail — never leaks it
            if sup:
                return body                           # the platform team sees the technical detail
            log_neutralized(f"{scope.get('method', '')} {scope.get('path', '')}", originals)
            return json.dumps(new, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        except Exception:
            return body
