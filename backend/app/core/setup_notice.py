"""SETUP NOTICE — the ONE home for "this feature's setup isn't finished" (owner 2026-09-29, index §19.36).

THE OWNER'S WORDS. Admin → Display Labels said *"Needs migration 068_ui_label_override.sql"*: "this
migration should not be mentioned in customer facing".

THE CLASS, NOT THE INSTANCE. An internal build/setup fact — a migration file name, "apply mig 1030",
"run migration …", a schema.table in a "not applied" hint, "run it in the Supabase SQL editor", a raw
PostgREST "relation … does not exist" — reached CUSTOMER-FACING copy: HTTPException details and payload
`note` / `hint` / `error` / `migration` fields on the backend, ~130 rendered sites on the frontend. Fixing
them one at a time is the patchwork the house rules forbid: the next one is written next week.

THE DESIGN — one fact, one home, applied at the boundary every caller already crosses:
  • `SETUP_NOTICE` — the one sentence a customer sees. The frontend's copy
    (`frontend/src/lib/setupNotice.tsx`) is LOCKED equal to this one.
  • `SETUP_INTERNAL` — the one detector of a setup hint. It recognises a hint by its SHAPE (a `NNN_x.sql`
    file; "(mig 111)"; run / apply / is / has / needs … migration; migration … not applied / has not been
    run / pending; a table "not applied"; "SQL editor"; "in Supabase"; PostgREST's not-applied errors) —
    never the bare word "migration", which is carrier DATA ("Port-in Migration", "Pending Migration").
  • `MESSAGE_KEYS` — WHERE a hint may be rewritten. THE BOUNDARY NEVER TOUCHES DATA (coordinator review
    2026-09-29): only the value of a message-shaped key is read — `detail` (an HTTPException's), and the
    keys the backend's own setup-hint strings are emitted under (measured over every backend string the
    detector matches; the lock re-measures on every build and FAILS a hint emitted under any other key).
    The walk descends dicts only: a LIST outside a message key is data (report rows, records) and is never
    entered, and a bare list / scalar body is never touched. Under a message key everything is message
    (a list of warning strings, a `detail` dict).
  • `neutralize_text` (per SENTENCE) / `neutralize` (the keyed walk, copy-on-write: an untouched payload
    is returned as the SAME object).
  • `SetupNoticeMiddleware` — THE boundary (registered innermost in main.py). A JSON body is parsed ONLY
    when a message key's value window carries a marker (`may_carry_hint`, a byte scan — a multi-MB report
    whose rows say "Migration" or "Miguel" is returned as the same bytes object, never json.loads'd). A
    rewritten body goes to a caller who is not the platform super admin (THE one gate,
    `core.router._require_super_admin`); the super admin sees it unchanged; the original goes to the log.
  • `report_registry.build_payload` — the one outbound report builder (mail / WhatsApp) — calls
    `neutralize` too, under the same key rules.

THE SIBLING CLASS — DATABASE / HOSTING NAMES (owner 2026-10-02: "hide database names from the users", index
§19.37). The same boundary carries a second detector, `SYSTEM_INTERNAL`: the RUNTIME error text of the database /
its REST layer / the hosting stack, interpolated into a message at run time (`detail=f"save failed: {e}"`) —
"duplicate key value violates unique constraint …", "permission denied for table …", "null value in column …",
an error dict `{'code': '23505', …}`, a `postgrest.exceptions.APIError`, a `*.supabase.co` host. Such a sentence
(and the rest of the text after it — the error is the tail: `'details': 'Key (org_id, code)=…'`) becomes the ONE
plain `SYSTEM_NOTICE` for a caller who is not the super admin. Not-applied errors (42P01 / 42703 / PGRST2xx /
"relation … does not exist") stay with `SETUP_INTERNAL` → `SETUP_NOTICE`. STATIC names in the backend's own
strings (a `commcalc.store_mapping`, a `raw_sales`, an env var `RESEND_API_KEY`, "Railway") are NOT rewritten
here — the boundary cannot tell a descriptive note from an error, so they are reworded AT SOURCE, and the lock
(`harness_carrier_vocab_guard.py` §INFRA) fails the build on any message-keyed string that still names one after
this boundary has run.

WHAT IS NOT HERE. Code comments, docstrings, log lines and harnesses keep their migration numbers.
`/openapi.json`, `/docs`, `/redoc`, `/health` pass verbatim. The lock is `harness_carrier_vocab_guard.py`
§SETUP (CI `carrier-vocab-guard.yml`).

PURE except `SetupNoticeMiddleware._is_super_admin` (lazy import of the one gate, run in a thread).
"""
import asyncio
import json
import re
import sys

SETUP_NOTICE = "This feature isn't switched on for your company yet. Contact support to enable it."
# The sibling sentence (§19.37): a runtime database / hosting error. Says what the customer can do, names nothing.
SYSTEM_NOTICE = "Something went wrong saving or loading this. Check the entry and try again, or contact support if it keeps happening."

# ── WHERE: the message-shaped keys (the ONLY values the boundary reads) ─────────────────────────────────
# Measured 2026-09-29 over every backend string SETUP_INTERNAL matches (the lock re-measures each build):
# `detail` (243 — HTTPException + raised errors), `note` (54), `error` (23), `hint` (19), `message`, `reason`,
# `setup_note`, `not_ready_note`, `ledger_note`, `save_errors`, `warnings`, `remediation`, `subtitle` (a
# report's), `words`. Deliberately NOT here: `notes`, `label`, `status`, `name`, `description` — keys a
# customer's own record carries (their hint literals were reworded instead). A key that names a migration
# identifier (`migration`, `sync_migration`, `config_migrations_missing`, …) is internal by construction.
MESSAGE_KEYS = frozenset({
    "detail", "message", "error", "hint", "note", "reason", "setup_note", "not_ready_note", "ledger_note",
    "save_errors", "warnings", "remediation", "subtitle", "words",
})
_MIGRATION_KEY = re.compile(r"(?:^|_)migrations?(?:_missing)?$")


def is_message_key(key) -> bool:
    return isinstance(key, str) and (key in MESSAGE_KEYS or _MIGRATION_KEY.search(key) is not None)


# ── WHAT: the one detector ──────────────────────────────────────────────────────────────────────────────
# A migration NUMBER is 000–1999 (optionally a letter: 268b) — years (2025) are never read as one.
_NO = r"(?:\d{3}|1\d{3})[a-z]?\b"
_MIG = r"mig(?:ration)?s?"
SETUP_INTERNAL = re.compile("|".join([
    r"\b\d{3,4}[a-z]?_[a-z0-9_]+\.sql\b",                                                  # 068_ui_label_override.sql
    r"\([^()]{0,80}?\b" + _MIG + r"\s*#?\s*" + _NO,                                        # (mig 111) / (closing_…, mig 1012)
    r"\b(?:run|running|re-?run|apply|applying|applied|ran|needs?|requires?|until|once|after|awaiting"
    r"|pending\s+(?:a|the))\s+(?:the\s+|a\s+|this\s+|any\s+|its\s+|database\s+)?(?:\w+\s+){0,2}?"
    r"(?-i:mig(?:ration)?s?)\b",                                                             # run the migration (lower-case)
    r"\b(?:is|has|have|was)\s+(?:the\s+)?(?:database\s+)?" + _MIG + r"\s*#?\s*" + _NO,       # is migration 071 applied
    r"\b" + _MIG + r"\b(?:\s+#?" + _NO + r")?[^.;\n]{0,60}?(?:\bnot\s+(?:yet\s+)?(?:been\s+)?(?:applied|run)\b"
    r"|\bun-?run\b|\bunapplied\b|\bapplied\s*\?|\brun\s*\?|\bhas(?:n['’]t|\s+not)\s+(?:been\s+)?(?:applied|run)\b"
    r"|\bhasn['’]t\s+run\b|\bis(?:n['’]t|\s+not)\s+applied\b|\bmust\s+(?:also\s+)?be\s+applied\b|\bmay\s+not\s+be"
    r"\s+applied\b|\bhas\s+(?:been\s+)?run\b|\bpending\b|\bfirst\b)",                      # migration 431 not applied
    r"\b(?:table|column|schema)s?\b[^.;\n]{0,40}?\bnot\s+(?:yet\s+)?(?:been\s+)?(?:applied|created)\b",
    r"\bSQL\s+editor\b",
    r"(?-i:\bin\s+(?:the\s+)?Supabase\b|\bSupabase\s+SQL\b)",
    r"\bPGRST\d{3}\b|\b42P01\b|\b42703\b",
    r"\brelation\s+\\?\"?[\w.]+\\?\"?\s+does\s+not\s+exist\b",
    r"\bcolumn\s+\\?\"?[\w.]+\\?\"?\s+(?:of\s+relation\s+\\?\"?[\w.]+\\?\"?\s+)?does\s+not\s+exist\b",
    r"\bCould\s+not\s+find\s+the\s+(?:table|function|column)\b",
    r"\bschema\s+cache\b",
]), re.I)


def is_setup_internal(text) -> bool:
    """True when `text` carries an internal setup fact a customer must not see."""
    return isinstance(text, str) and bool(text) and SETUP_INTERNAL.search(text) is not None


# ── WHAT (the sibling, §19.37): a RUNTIME database / hosting error interpolated into a message ─────────
# By SHAPE — the database's own error sentences, its error-dict repr, its client's exception names, the hosting
# hosts. Never a bare word a customer's message may hold ("duplicate", "permission", "denied", "Postgres" alone).
SYSTEM_INTERNAL = re.compile("|".join([
    r"\bduplicate\s+key\s+value\b",
    r"\bviolates\s+(?:unique|foreign\s+key|check|not-null|exclusion)\s+constraint\b",
    r"\bviolates\s+row-level\s+security\b",
    r"\bnull\s+value\s+in\s+column\b",
    r"\bpermission\s+denied\s+for\s+(?:table|schema|relation|sequence|function|view|database)\b",
    r"\binvalid\s+input\s+(?:syntax|value)\s+for\s+(?:type|enum)\b",
    r"\bvalue\s+too\s+long\s+for\s+type\b",
    r"\bout\s+of\s+range\s+for\s+type\b",
    r"\bcanceling\s+statement\s+due\s+to\s+statement\s+timeout\b",
    r"\bdeadlock\s+detected\b",
    r"\bSQLSTATE\b",
    r"""['"]code['"]\s*:\s*['"](?:\d{2}[0-9A-Z]{3}|PGRST\d{3})['"]""",               # {'code': '23505', …}
    r"\b(?:postgrest|psycopg2?|supabase)(?:\.[a-z_]+)*\.(?:\w*Error|\w*Exception)\b",  # postgrest.exceptions.APIError
    r"\bAPIError\(",
    r"\b[\w-]+\.(?:supabase\.co|railway\.app|vercel\.app)\b",                         # a hosting host
]), re.I)


def is_system_internal(text) -> bool:
    """True when `text` carries a runtime database / hosting error a customer must not see (§19.37)."""
    return isinstance(text, str) and bool(text) and SYSTEM_INTERNAL.search(text) is not None


_SENTENCE = re.compile(r"(?<=[.!?])\s+(?=\S)")


def neutralize_text(text):
    """Every sentence of `text` that carries a setup hint becomes SETUP_NOTICE (once); a sentence that carries a
    runtime database / hosting error becomes SYSTEM_NOTICE and ENDS the text (the error is the tail — its `details`
    / `hint` sentences follow it); every other sentence is kept. Text with neither is returned unchanged (the
    same object)."""
    if not (is_setup_internal(text) or is_system_internal(text)):
        return text
    out, noticed = [], False
    for part in _SENTENCE.split(text):
        if is_setup_internal(part):
            if not noticed:
                out.append(SETUP_NOTICE)
                noticed = True
        elif is_system_internal(part):
            out.append(SYSTEM_NOTICE)
            noticed = True
            break
        else:
            out.append(part)
    if not noticed:                                  # the hint spanned a sentence break — whole text
        return SYSTEM_NOTICE if is_system_internal(text) and not is_setup_internal(text) else SETUP_NOTICE
    return " ".join(out)


def _message(v, hits):
    """A message key's value: every string in it (a list of warnings, a `detail` dict) is message."""
    if isinstance(v, str):
        n = neutralize_text(v)
        if n is not v:
            hits.append(v)
        return n
    if isinstance(v, list):
        new = [_message(x, hits) for x in v]
        return v if all(a is b for a, b in zip(new, v)) else new
    if isinstance(v, dict):
        new = {k: _message(x, hits) for k, x in v.items()}
        return v if all(new[k] is v[k] for k in v) else new
    return v


def _record(d, hits):
    """A dict: message keys are read; a nested dict is walked; a LIST is data and never entered."""
    changed = {}
    for k, v in d.items():
        if is_message_key(k):
            n = _message(v, hits)
        elif isinstance(v, dict):
            n = _record(v, hits)
        else:
            continue
        if n is not v:
            changed[k] = n
    if not changed:
        return d
    return {k: changed.get(k, v) for k, v in d.items()}


def neutralize(payload):
    """(payload', originals). Only message-key values of the top-level object and its nested dicts are
    read — never a list outside a message key, never a bare list / scalar body. Copy-on-write: a payload
    with nothing to rewrite comes back as the SAME object and `originals == []`."""
    hits = []
    if not isinstance(payload, dict):
        return payload, hits
    return _record(payload, hits), hits


# ── the byte prefilter: parse ONLY when a message key's value carries a marker ──────────────────────────
# Measured on a 5.6 MB report body of 40,000 rows saying "Port-in Migration" / "Miguel" (the lock's P-series
# re-measures every build): no message key → one regex pass (~30 ms here, about half of what the endpoint's own
# json encoding of that body costs), no parse, the SAME bytes object returned.
_MARKERS = (b".sql", b"mig", b"sql editor", b"supabase", b"pgrst", b"42p01", b"42703", b"does not exist",
            b"could not find the", b"schema cache", b"not applied", b"not yet applied", b"not been applied",
            b"not created", b"not yet created", b"not been created",
            # §19.37 — SYSTEM_INTERNAL's shapes (lower-case; the window is lower-cased before the search)
            b"duplicate key", b"violates ", b"null value in column", b"permission denied for", b"invalid input",
            b"too long for type", b"out of range for type", b"statement timeout", b"deadlock detected", b"sqlstate",
            b"'code'", b'\\"code\\"', b"postgrest", b"psycopg", b"apierror", b"railway.app",
            b"vercel.app")
_KEY_AT = re.compile(rb'"(?:' + b"|".join(re.escape(k.encode()) for k in sorted(MESSAGE_KEYS)) + rb')"')
_MIGKEY_TOKENS = (b'migration"', b'migrations"', b'migrations_missing"')
_MIGKEY_PREFIX = re.compile(rb"[a-z_]*")
_WS = (b" ", b"\t", b"\n", b"\r")
_WINDOW = 4096          # the bytes read after a message key — a setup hint is a sentence, not a report
_KEY_CAP = 256          # more message-named keys than this = rows carrying e.g. a `note` column: just parse


def _colon_after(body, j):
    while body[j:j + 1] in _WS:
        j += 1
    return j + 1 if body[j:j + 1] == b":" else -1


def _message_value_starts(body):
    """Byte offsets where a MESSAGE key's value starts, or None when there are more than _KEY_CAP."""
    ends, probes = [], 0
    for m in _KEY_AT.finditer(body):                 # one C-speed pass for the fixed keys
        e = _colon_after(body, m.end())
        if e != -1:
            ends.append(e)
            if len(ends) > _KEY_CAP:
                return None
    for tok in _MIGKEY_TOKENS:                       # `migration`, `sync_migration`, `config_migrations_missing` …
        i = body.find(tok)
        while i != -1:
            probes += 1
            if probes > _KEY_CAP * 4:
                return None
            e = _colon_after(body, i + len(tok))
            if e != -1:
                q = body.rfind(b'"', max(0, i - 64), i)
                if q != -1 and _MIGKEY_PREFIX.fullmatch(body, q + 1, i):
                    ends.append(e)
            i = body.find(tok, i + 1)
    return ends


def may_carry_hint(body: bytes) -> bool:
    """True only when some MESSAGE key's value (its first _WINDOW bytes) carries a marker. A body whose rows
    say "Port-in Migration" / "Miguel" but which has no message key carrying a marker is never parsed."""
    starts = _message_value_starts(body)
    if starts is None:
        return True
    return any(any(m in body[e:e + _WINDOW].lower() for m in _MARKERS) for e in starts)


def log_neutralized(where, originals):
    """The technical detail goes to the SERVER LOG (stderr, captured by the platform) — never lost."""
    try:
        for o in originals[:20]:
            print(f"[setup-notice] {where}: {str(o)[:400]}", file=sys.stderr, flush=True)
    except Exception:
        pass


# ── THE BOUNDARY ────────────────────────────────────────────────────────────────────────────────────
_SKIP_PATHS = ("/openapi.json", "/docs", "/redoc", "/health")
_loads = json.loads      # a seam: the lock counts parses to prove a data-only body is never parsed


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
                chunks = state["chunks"]
                body = chunks[0] if len(chunks) == 1 else b"".join(chunks)
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
            if not body or not may_carry_hint(body):
                return body
            new, originals = neutralize(_loads(body))
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
