"""The in-app data assistant's AGENT LOOP — the one place a model is given tools over this tenant's
own reports.

THE SHAPE, AND WHY IT IS THIS SHAPE
-----------------------------------
Owner directive (2026-10-05): *"We need to create a self generated AI inside our platform that is
smart enough to answer any questions related to the data ingested into the system, perform
calculations, create a pivot table or create graphs or answer a question like which was my best
store and how much revenue did it make or who is best sales person or who is pulling me down, or
what is needed to pull sales up."*

Four rulings follow from the house rules, and the loop below is what they add up to.

1. **The model never writes a query.** It picks a QUESTION from `data_qa_registry`, and the registry
   turns that into the same endpoint the screen calls. There is no SQL seam to inject into, and no
   second derivation of any number — so an answer here cannot disagree with the report it came from
   (index §3's single aggregation pass, §4's P&L, §13's store resolution).
2. **The model never does the arithmetic.** It chooses a grouping, a measure and a chart; the sums,
   ranks, pivots and percentages are computed by `data_qa_compute`, which has a DB-free proof. A
   figure in an answer was added up by code, not by a language model.
3. **The model never widens what the user may see.** Every report read is made in-process with the
   CALLER'S OWN bearer token, so `TenantScopeMiddleware` resolves the org and the endpoint's own
   RBAC (`storeops.caller_scope` → `scope_keyset`) decides which stores come back. The assistant is
   exactly as blind as the person using it. It is also read-only by construction: the registry holds
   no non-GET path, and `_fetch` refuses anything else.
4. **The model never spends unbounded money.** The call is authorized, rate-limited, budgeted and
   audited through the SHARED AI guard (`core/ai_gate` + the `data_qa` purpose in
   `control_box.AI_PURPOSES`, mig 1055) — the same door the control box, remediation triage and
   lease extraction already go through. No private copy of "is this tenant allowed to spend".

SEV-1 2026-07-30 DISCIPLINE. The Anthropic client here is the ASYNC one and every call is awaited,
with an explicit timeout and a single retry. A synchronous client would block the FastAPI event loop
for the whole model call and freeze every endpoint including `/health`. Do NOT reintroduce
`Anthropic(` in this file. The in-process report reads are awaited too (ASGI transport), so a slow
report yields the loop rather than holding it.
"""
import asyncio
import json
import os

from app.core.config import settings
from app.modules.core import control_box as cbx
from app.modules.core import ai_gate as _gate
from app.modules.core import data_qa_compute as calc
from app.modules.core import data_qa_registry as reg

PURPOSE = "data_qa"

# Bounds on ONE question. Env-tunable so an operator can widen without a deploy; a garbage value
# falls back to the default rather than breaking module import (the helpdesk assistant's precedent).


def _envf(name, default, lo):
    try:
        return max(lo, float(os.getenv(name) or default))
    except Exception:
        return float(default)


def _envi(name, default, lo):
    try:
        return max(lo, int(os.getenv(name) or default))
    except Exception:
        return int(default)


MODEL_TIMEOUT_S = _envf("DATA_QA_TIMEOUT_S", 60, 1)
MODEL_MAX_RETRIES = _envi("DATA_QA_MAX_RETRIES", 1, 0)
REPORT_TIMEOUT_S = _envf("DATA_QA_REPORT_TIMEOUT_S", 45, 1)
MAX_ROUNDS = _envi("DATA_QA_MAX_ROUNDS", 8, 1)          # model turns that may call a tool
MAX_REPORTS = _envi("DATA_QA_MAX_REPORTS", 6, 1)        # report reads per question
MAX_ROWS_TO_MODEL = _envi("DATA_QA_MAX_ROWS_TO_MODEL", 60, 5)
MAX_TOKENS = _envi("DATA_QA_MAX_TOKENS", 4096, 256)

SYSTEM = """You are the data assistant inside MetricsPro, answering questions about THIS company's own
operating data for the person signed in. The tenant is "{tenant_name}".

HOW YOU ANSWER
You do not have a database and you do not write queries. You have a catalog of QUESTIONS, each of
which runs one of this platform's own reports — the same report the user sees on screen. Use
`run_question` to fetch one, then use `group_rank`, `pivot_table`, `compare_rows` or `make_chart` to
do the arithmetic. Never add, average, rank or convert numbers yourself: call a tool and report what
it returns. If you state a figure you did not get back from a tool, you are guessing, and guessing
about money here is worse than saying you do not know.

WHAT YOU CAN SEE
Report results are already scoped to what this user is permitted to see. If a store is missing from
a result, say the result covers the stores available to them rather than asserting the store does not
exist. Never mention another company.

WHEN A QUESTION NEEDS A PERIOD
If the user does not say which month, use the current month ({today_month}) and say which month you
used. Today is {today}.

ANSWERING WELL
- Lead with the answer in one or two sentences, with the figure and the month.
- Show a small table or a chart when it helps; `pivot_table` and `make_chart` results are rendered
  for the user, so refer to them rather than re-typing every number.
- "Who is pulling me down" means who is furthest BELOW what the rest are doing — use `compare_rows`,
  not just the smallest number.
- "What do I need to pull sales up" has a report of its own (the action plan). Prefer it over
  improvising advice.
- A blank or missing figure means NOT REPORTED, never zero. Say a feed looks absent rather than
  reporting $0 as fact.
- If no registered question can answer what was asked, say so plainly and name the closest thing you
  can show. Do not invent a report.

STYLE: plain, practical sentences for a retail operator. No preamble."""


# ── the report read: the caller's own token, the platform's own endpoint ─────────────────────────
async def _fetch(app, path, query, authorization):
    """GET one registered report in-process, AS THE CALLER. Returns `(status, payload)`.

    ASGI transport rather than an outbound HTTP hop: the request runs through the same middleware
    stack (tenant scope, rate limit, hardening) and the same handler as the browser's call, so the
    assistant inherits every gate instead of re-implementing any of them. Nothing but GET is ever
    issued — the registry holds no write path and this is the only place a path is used."""
    import httpx
    headers = {"accept": "application/json"}
    if authorization:
        headers["authorization"] = authorization
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://data-qa.internal",
                                 timeout=REPORT_TIMEOUT_S) as cli:
        r = await cli.get(path, params=query, headers=headers)
    try:
        return r.status_code, r.json()
    except Exception:
        return r.status_code, {"detail": (r.text or "")[:400]}


# ── the tool surface the model is given ─────────────────────────────────────────────────────────
def tool_defs(enabled_modules=None, caller_is_self=False):
    """The tools, with the question catalog inlined into `run_question`'s description so the model
    chooses from the registry rather than being told to guess a name. `strict` keeps the arguments
    schema-valid, which matters because every argument is then re-validated by the registry.

    `caller_is_self` is passed STRAIGHT THROUGH to the registry: a rep is never even shown a
    question they may not run, which is kinder than a refusal and, more to the point, means the
    narrowing is not a sentence in a prompt that a model could be talked out of. The enforcement is
    `_run_tool`'s `authorized_questions` check, which is built from the same call."""
    cat = reg.catalog(enabled_modules, caller_is_self)
    listing = "\n".join(
        f"- {c['question']}: {c['label']}. Answers: {c['answers']} Grain: {c['grain']}. "
        f"Parameters: {json.dumps(c['parameters'], sort_keys=True)}"
        for c in cat)
    return [
        {"name": "run_question", "strict": True,
         "description": ("Run one of this platform's reports and get its rows back. Returns a "
                         "`result` id plus the row count, the columns found, which columns hold "
                         "numbers, and a few sample rows — pass the `result` id to the other tools "
                         "to compute over the full result.\n\nAvailable questions:\n" + listing),
         "input_schema": {"type": "object", "additionalProperties": False,
                          "required": ["question"],
                          "properties": {
                              "question": {"type": "string",
                                           "enum": [c["question"] for c in cat] or ["none"],
                                           "description": "the question key from the list above"},
                              "parameters": {"type": "object",
                                             "description": ("that question's parameters; omit for "
                                                             "defaults. Repeatable parameters take "
                                                             "an array."),
                                             "additionalProperties": True}}}},
        {"name": "group_rank", "strict": True,
         "description": ("Group a result's rows by one or more columns, total a measure over each "
                         "group, and order the groups. This is how you find a best or worst store, "
                         "rep, day or market."),
         "input_schema": {"type": "object", "additionalProperties": False,
                          "required": ["result", "group_by", "measures"],
                          "properties": {
                              "result": {"type": "string"},
                              "group_by": {"type": "array", "items": {"type": "string"},
                                           "description": "column names to group by"},
                              "measures": {"type": "array", "items": {"type": "string"},
                                           "description": "numeric column names to aggregate"},
                              "agg": {"type": "string",
                                      "enum": ["sum", "count", "avg", "min", "max"]},
                              "order_by": {"type": "string",
                                           "description": "which measure to sort on"},
                              "descending": {"type": "boolean"},
                              "limit": {"type": "integer"}}}},
        {"name": "pivot_table", "strict": True,
         "description": ("Build a pivot table from a result: chosen columns down the side, the "
                         "values of one column across the top, a measure in the cells. Row totals "
                         "and a column footer are computed. The table is shown to the user."),
         "input_schema": {"type": "object", "additionalProperties": False,
                          "required": ["result", "rows_by", "columns_by", "measure"],
                          "properties": {
                              "result": {"type": "string"},
                              "rows_by": {"type": "array", "items": {"type": "string"}},
                              "columns_by": {"type": "string"},
                              "measure": {"type": "string"},
                              "agg": {"type": "string",
                                      "enum": ["sum", "count", "avg", "min", "max"]},
                              "title": {"type": "string"}}}},
        {"name": "compare_rows", "strict": True,
         "description": ("Compare each row's measure against a baseline — either another column on "
                         "the same row, or the average of the rows compared. Returns the gap, the "
                         "percentage and each row's share of the total. Use this for 'who is ahead' "
                         "and 'who is pulling me down'."),
         "input_schema": {"type": "object", "additionalProperties": False,
                          "required": ["result", "label_column", "measure"],
                          "properties": {
                              "result": {"type": "string"},
                              "label_column": {"type": "string"},
                              "measure": {"type": "string"},
                              "baseline_column": {"type": "string"},
                              "limit": {"type": "integer"}}}},
        {"name": "make_chart", "strict": True,
         "description": ("Describe a chart over a result's rows. It is rendered for the user, so "
                         "you do not need to list the values in your answer."),
         "input_schema": {"type": "object", "additionalProperties": False,
                          "required": ["result", "kind", "label_column", "measures"],
                          "properties": {
                              "result": {"type": "string"},
                              "kind": {"type": "string",
                                       "enum": ["bar", "horizontal_bar", "line", "pie"]},
                              "label_column": {"type": "string"},
                              "measures": {"type": "array", "items": {"type": "string"}},
                              "title": {"type": "string"},
                              "limit": {"type": "integer"}}}},
    ]


class _Workspace:
    """The results fetched while answering ONE question.

    The model is handed an ID, never the rows: the arithmetic therefore always runs over the rows the
    report returned, and a model cannot quietly retype a row on the way to a total. It also keeps
    the context small enough that long reports stay answerable."""

    def __init__(self):
        self.results = {}
        self.reports = 0
        self.charts = []
        self.tables = []
        self.sources = []

    def put(self, question, rows, meta):
        rid = f"r{len(self.results) + 1}"
        self.results[rid] = {"question": question, "rows": rows, "meta": meta}
        return rid

    def rows(self, rid):
        got = self.results.get(str(rid))
        if not got:
            raise KeyError(f"no such result '{rid}' — run_question first")
        return got["rows"]


async def _run_tool(name, args, ws, *, app, authorized_questions, authorization, enabled_modules):
    """Execute one tool call. Returns the JSON-able result handed back to the model.

    Every failure comes back as `{"error": ...}` rather than raising: a model that asked for a
    column that does not exist should be told so and given the chance to ask correctly, which is
    what makes the loop self-correcting instead of returning a half-answer."""
    if name == "run_question":
        q = str(args.get("question") or "")
        if q not in authorized_questions:
            return {"error": f"'{q}' is not available for this company",
                    "available": list(authorized_questions)}
        if ws.reports >= MAX_REPORTS:
            return {"error": "too many reports for one question; answer from what you have"}
        path, query, errors = reg.validate(q, args.get("parameters") or {})
        if errors:
            return {"error": "; ".join(errors)}
        ws.reports += 1
        status, payload = await _fetch(app, path, query, authorization)
        if status == 403:
            return {"error": "this report is not permitted for the signed-in user"}
        if status == 404:
            return {"error": "that report has nothing for the period asked for"}
        if status >= 400:
            detail = payload.get("detail") if isinstance(payload, dict) else None
            return {"error": f"the report could not be produced ({status})",
                    "detail": str(detail)[:200] if detail else None}
        rows = reg.rows_from(q, payload)
        meta = {"question": q, "parameters": query}
        rid = ws.put(q, rows, meta)
        ws.sources.append({"question": q, "label": reg.question(q)["label"], "parameters": query,
                           "row_count": len(rows)})
        out = {"result": rid, **calc.describe(rows)}
        if not rows:
            # A summary-shaped report (a narrative, a single P&L envelope) has no rows. Hand the
            # model the envelope itself, bounded, rather than an empty table that reads as "no data".
            out["summary"] = _bounded(payload)
        return out

    rid = str(args.get("result") or "")
    try:
        rows = ws.rows(rid)
    except KeyError as e:
        return {"error": str(e)}

    try:
        if name == "group_rank":
            grouped = calc.group(rows, args.get("group_by") or [], args.get("measures") or [],
                                 agg=str(args.get("agg") or "sum"))
            order_by = str(args.get("order_by") or "")
            if order_by:
                grouped = calc.rank(grouped, order_by,
                                    descending=args.get("descending", True) is not False)
            limit = int(args.get("limit") or MAX_ROWS_TO_MODEL)
            return {"group_count": len(grouped),
                    "groups": grouped[:min(limit, MAX_ROWS_TO_MODEL)]}

        if name == "pivot_table":
            table = calc.pivot(rows, args.get("rows_by") or [], str(args.get("columns_by") or ""),
                               str(args.get("measure") or ""), agg=str(args.get("agg") or "sum"))
            table["title"] = str(args.get("title") or "")[:120]
            ws.tables.append(table)
            shown = dict(table)
            shown["rows"] = table["rows"][:MAX_ROWS_TO_MODEL]
            shown["shown_to_user"] = True
            shown["row_count"] = len(table["rows"])
            return shown

        if name == "compare_rows":
            cmp = calc.compare(rows, str(args.get("label_column") or ""),
                               str(args.get("measure") or ""),
                               baseline_col=(str(args["baseline_column"])
                                             if args.get("baseline_column") else None))
            limit = int(args.get("limit") or MAX_ROWS_TO_MODEL)
            cmp["rows"] = cmp["rows"][:min(limit, MAX_ROWS_TO_MODEL)]
            return cmp

        if name == "make_chart":
            spec = calc.chart_spec(rows, str(args.get("kind") or "bar"),
                                   str(args.get("label_column") or ""),
                                   args.get("measures") or [],
                                   title=str(args.get("title") or "")[:120],
                                   limit=int(args.get("limit") or 40))
            ws.charts.append(spec)
            return {"shown_to_user": True, "kind": spec["kind"], "title": spec["title"],
                    "points": len(spec["labels"])}
    except ValueError as e:
        return {"error": str(e)}
    except Exception as e:                       # a bad column name must not 500 the question
        return {"error": f"{type(e).__name__}: {str(e)[:160]}"}

    return {"error": f"unknown tool '{name}'"}


def _bounded(payload, *, limit=4000):
    """A summary envelope, bounded, with row lists dropped (they went to the model as a result)."""
    if isinstance(payload, dict):
        slim = {k: v for k, v in payload.items() if not isinstance(v, (list, dict))}
        for k, v in payload.items():
            if isinstance(v, dict):
                slim[k] = {kk: vv for kk, vv in v.items() if not isinstance(vv, (list, dict))}
        text = json.dumps(slim, default=str, sort_keys=True)
    else:
        text = json.dumps(payload, default=str)[:limit]
    return json.loads(text) if len(text) <= limit else {"truncated": text[:limit]}


# ── the loop ────────────────────────────────────────────────────────────────────────────────────
async def answer(app, *, org_id, question, history, tenant_name, enabled_modules, authorization,
                 caller, client, today, caller_is_self=False):
    """Answer one data question. Returns the reply payload the endpoint serves.

    Degrades, never raises: no key, AI switched off for the tenant, a refused guard decision, a
    model timeout and a report 403 all come back as a sentence the user can act on, because an
    operator asking "which store is best" must never be shown a stack trace.
    """
    from datetime import timezone as _tz
    answerable = set(reg.answerable(enabled_modules, caller_is_self))

    decision, _cfg = await _gate.decide_async(client, org_id=org_id, purpose=PURPOSE,
                                              caller=caller, subject=question)
    if not decision.get("allow"):
        _gate.audit(client, cbx.ai_audit_row(org_id, caller, decision.get("subject_key"), decision,
                                             purpose=PURPOSE))
        # `ai_guard_decision` returns the human sentence in `reason` — the ONE wording for every
        # refusal of every purpose. No second copy of "what we say when we refuse" lives here.
        return {"reply": decision.get("reason") or "The assistant is not available right now.",
                "allowed": False, "deny_code": decision.get("code"),
                "charts": [], "tables": [], "sources": []}

    if not settings.ANTHROPIC_API_KEY:
        return {"reply": "The assistant is not configured on this backend yet — ask an admin to set "
                         "the AI key. Every report it reads is still available from its own page.",
                "allowed": True, "configured": False, "charts": [], "tables": [], "sources": []}

    system = (SYSTEM.replace("{tenant_name}", tenant_name or "your company")
              .replace("{today}", today.isoformat())
              .replace("{today_month}", f"{today.year}-{today.month:02d}"))

    msgs = []
    for h in (history or [])[-8:]:
        role = str((h or {}).get("role") or "").lower()
        content = str((h or {}).get("content") or "").strip()
        if role in ("user", "assistant") and content:
            msgs.append({"role": role, "content": content[:4000]})
    msgs.append({"role": "user", "content": question[:4000]})

    ws = _Workspace()
    tools = tool_defs(enabled_modules, caller_is_self)
    usage_in = usage_out = 0
    reply, err = "", None

    from anthropic import AsyncAnthropic
    cli = AsyncAnthropic(api_key=settings.ANTHROPIC_API_KEY, timeout=MODEL_TIMEOUT_S,
                         max_retries=MODEL_MAX_RETRIES)
    try:
        for _round in range(MAX_ROUNDS):
            resp = await cli.messages.create(
                model=settings.DATA_QA_MODEL, max_tokens=MAX_TOKENS,
                system=system, tools=tools, messages=msgs,
            )
            u = getattr(resp, "usage", None)
            usage_in += int(getattr(u, "input_tokens", 0) or 0)
            usage_out += int(getattr(u, "output_tokens", 0) or 0)
            text = "".join(getattr(b, "text", "") for b in resp.content
                           if getattr(b, "type", None) == "text").strip()
            calls = [b for b in resp.content if getattr(b, "type", None) == "tool_use"]
            if not calls:
                reply = text
                break
            msgs.append({"role": "assistant", "content": resp.content})
            results = []
            for c in calls:
                # Tool inputs are parsed JSON from the SDK; re-validated by the registry regardless.
                out = await _run_tool(c.name, dict(c.input or {}), ws, app=app,
                                      authorized_questions=answerable,
                                      authorization=authorization,
                                      enabled_modules=enabled_modules)
                results.append({"type": "tool_result", "tool_use_id": c.id,
                                "content": json.dumps(out, default=str)[:60000],
                                **({"is_error": True} if isinstance(out, dict) and "error" in out
                                   else {})})
            msgs.append({"role": "user", "content": results})
        else:
            reply = (reply or "").strip() or (
                "I ran out of steps before finishing that one. Try asking for one figure at a time "
                "— for example the best store for a single month.")
    except Exception as e:
        err = f"{type(e).__name__}: {str(e)[:200]}"
        slow = type(e).__name__ in ("APITimeoutError", "APIConnectionError")
        reply = ("The assistant is taking too long to answer right now — please try again in a "
                 "minute." if slow else
                 "The assistant hit an error working that out. The underlying reports are still "
                 "available from their own pages.")

    _gate.audit(client, cbx.ai_audit_row(org_id, caller, decision.get("subject_key"), decision,
                                         usage={"input_tokens": usage_in,
                                                "output_tokens": usage_out},
                                         model=settings.DATA_QA_MODEL, error=err,
                                         purpose=PURPOSE))
    try:
        from app.modules.billing import ai_meter as _ai_meter
        # usage metering only (migs 972/973) — observes that spend happened; grants nothing. The
        # whole tool loop is ONE recorded call, because one question is what the tenant asked for.
        _ai_meter.record(PURPOSE, settings.DATA_QA_MODEL, org_id=org_id,
                         subject_key=decision.get("subject_key"),
                         input_tokens=usage_in, output_tokens=usage_out, error=err,
                         actor=(caller or {}).get("id"))
    except Exception:
        pass

    return {"reply": reply or "I could not produce an answer for that — try rephrasing it.",
            "allowed": True, "configured": True,
            "charts": ws.charts, "tables": ws.tables, "sources": ws.sources,
            "reports_read": ws.reports, "error": err}
