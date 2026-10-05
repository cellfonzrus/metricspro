"""THE ONE HOME for "which question about this tenant's data can be answered, and by WHICH existing
report" — the semantic layer the in-app data assistant is allowed to see.

WHY THIS FILE EXISTS AT ALL (CLAUDE.md duplicate-check build gate)
------------------------------------------------------------------
The owner asked for an assistant that answers "which was my best store and how much revenue did it
make", "who is my best sales person", "who is pulling me down", "what do I need to pull sales up".
Every one of those numbers ALREADY has exactly one home in this platform, and those homes own rules
no second derivation would reproduce: the DISTINCT-`trans_id` counting rule, the void / return skip
set, `line_class` activation classification, the Activation-Details basis override, the P&L's line
bookings, the org tree's store span, the RBAC read scope.

So this assistant does NOT query tables, and it does NOT write SQL. It asks the SAME endpoints the
screens ask, as the SAME signed-in user, and this file is the registry of which endpoint answers
which question. That is why an answer here can never disagree with the screen the user is looking
at — there is no second derivation to drift (index §3's "ONE row-level pass", §4's P&L, §13's store
resolution all stay the single source).

WHAT IS DECLARED HERE AND WHAT IS DELIBERATELY NOT
--------------------------------------------------
DECLARED (a ruling nothing can infer): which questions exist, which endpoint serves each, which
query parameters a caller may set, which entitlement module gates it, where its rows sit in the
response envelope, and which index section documents it.

NOT DECLARED: the COLUMNS a report returns, and what any of them mean in dollars. Those are facts
about the endpoint, and the endpoint is their one home. Copying them here would be the
"one fact, two homes" defect the house rules forbid — the copy would rot the first time a report
gained a column. `data_qa_agent` therefore DISCOVERS the columns from the live response and tells
the model what it actually got.

"IT IMPROVES AS MORE DATA IS INGESTED" — HOW, EXACTLY
-----------------------------------------------------
Not by training a model. Two concrete mechanisms, both already in the platform:

  1. **More questions become answerable as feeds land.** `answerable()` is a PURE function of the
     tenant's enabled modules; the assistant is told which questions it may ask for THIS tenant and
     never offers one whose feed has not arrived. A tenant who starts ingesting POS X-reports gains
     the closing / cash questions on the day the module is switched on, with no code change.
  2. **A new report registers here in the same PR that builds it** (the index rule). The assistant's
     reach grows with the platform's reach, and the growth is reviewable in a diff instead of being
     an opaque property of a model.

PURITY: stdlib only, no database, no network, no `app.` imports — so the whole semantic layer is
provable with no database (`backend/harness_data_qa_registry.py`).
"""
import re

HOUSE_ORG = "00000000-0000-0000-0000-000000000001"     # CLAUDE.md house org (RULE TWO defaults)

# ── parameter kinds ──────────────────────────────────────────────────────────────────────────────
# The ONLY caller-supplied input that ever reaches an endpoint. Every value is matched against one of
# these patterns and refused otherwise, so nothing a user (or a model) types becomes free-form input
# to a report. There is no `sql` kind and there never will be: RULE TWO's sibling — a question is
# config, not a typed query.
PARAM_KINDS = {
    # '2026-09' or 'September 2026' — both spellings the reports already accept (`_pvariants`).
    "period": r"^(?:\d{4}-\d{2}|[A-Za-z]{3,9} \d{4})$",
    "date": r"^\d{4}-\d{2}-\d{2}$",
    "store_code": r"^[A-Za-z0-9][A-Za-z0-9 _.\-/]{0,39}$",
    "person": r"^[A-Za-z0-9][A-Za-z0-9 '_.\-]{0,59}$",
    "scope": r"^[a-z0-9_\-]{1,40}$",
    "flag": r"^(?:0|1|true|false)$",
}

_KIND_RE = {k: re.compile(v) for k, v in PARAM_KINDS.items()}

# A parameter may be given more than once (the reports' RULE FIVE repeated `stores=` / `reps=`).
_MULTI = "multi"


def _p(kind, *, required=False, multi=False, note=""):
    return {"kind": kind, "required": required, _MULTI: multi, "note": note}


# ── THE REGISTRY ─────────────────────────────────────────────────────────────────────────────────
# `answers` is written in the OWNER'S words on purpose: it is what the model matches a user's
# question against, so it must read like a person asking, not like a column name.
#
# `module` is the entitlement key the endpoint itself already gates on (or None when it does not
# gate). It is recorded here so the assistant can say "that is not switched on for you" instead of
# making the user watch a 403 come back. It NEVER grants anything — the endpoint remains the gate.
DATA_QUESTIONS: dict[str, dict] = {
    "sales_by_store_rep_day": {
        "label": "Sales done, per store, per rep, per day",
        "answers": ("Which store sold the most and how much revenue did it make? Which salesperson "
                    "is best or worst? What were units, revenue and gross profit for a store, a rep "
                    "or a day? Who is pulling the numbers down?"),
        "path": "/api/v1/commcalc/sales-report",
        "params": {"period": _p("period", note="blank = the current month")},
        "rows_at": ("rows",),
        "grain": "one row per store x salesperson x date",
        "module": None,
        "index": ("3",),
    },
    "sales_movement_summary": {
        "label": "What moved this month against last month (plain English)",
        "answers": ("Are we up or down this month? What changed versus last month? Which store led "
                    "the change? How is accessory and gross profit trending?"),
        "path": "/api/v1/commcalc/sales-report/narrative",
        "params": {"period": _p("period"), "today": _p("date")},
        "rows_at": (),                      # a summary object, not rows
        "grain": "one summary for the tenant",
        "module": None,
        "index": ("3",),
    },
    "executive_mtd": {
        "label": "Executive month-to-date summary",
        "answers": ("How are we doing month to date? Activations, upgrades, new activations, "
                    "accessories per store and per rep, and how that compares with last month."),
        "path": "/api/v1/commcalc/exec-mtd/{period}",
        "path_params": ("period",),
        "params": {"today": _p("date"), "date_from": _p("date"), "date_to": _p("date"),
                   "stores": _p("store_code", multi=True), "markets": _p("scope", multi=True),
                   "reps": _p("person", multi=True)},
        "rows_at": ("stores", "reps", "rows"),
        "grain": "per store and per rep, month to date",
        "module": None,
        "index": ("3", "19.31"),
    },
    "executive_mtd_movement": {
        "label": "Month-to-date movement (plain English)",
        "answers": "Summarise month-to-date against the same days of last month.",
        "path": "/api/v1/commcalc/exec-mtd/{period}/narrative",
        "path_params": ("period",),
        "params": {"today": _p("date"), "stores": _p("store_code", multi=True),
                   "markets": _p("scope", multi=True), "reps": _p("person", multi=True)},
        "rows_at": (),
        "grain": "one summary for the filtered selection",
        "module": None,
        "index": ("3",),
    },
    "daily_targets_summary": {
        "label": "Daily targets against actuals",
        "answers": ("Are we on target? Which stores are behind and by how much? What is the pace "
                    "needed for the rest of the month?"),
        "path": "/api/v1/commcalc/targets/{period}/summary",
        "path_params": ("period",),
        "params": {"today": _p("date")},
        "rows_at": ("stores", "rows"),
        "grain": "per store, for the period",
        "module": None,
        "index": ("5",),
    },
    "action_plan": {
        "label": "What to do to pull sales up",
        "answers": ("What do I need to do to pull sales up? Where is the catch-up? Which rep has "
                    "commission at risk? What should each store focus on today?"),
        "path": "/api/v1/commcalc/targets/{period}/action-plan",
        "path_params": ("period",),
        "params": {"today": _p("date"), "store_code": _p("store_code"), "rep": _p("person"),
                   "include_inactive": _p("flag")},
        "rows_at": ("stores", "reps", "rows"),
        "grain": "prioritised focus areas per store and per rep",
        "module": None,
        "index": ("5",),
    },
    "profit_and_loss": {
        "label": "Profit & loss for a month",
        "answers": ("What was my profit last month? What did a store make or lose? Where is the "
                    "money going — which expense lines are biggest?"),
        "path": "/api/v1/account/pl/{period}",
        "path_params": ("period",),
        "params": {"scope": _p("scope", note="consolidated, or a company / market / store scope"),
                   "stores": _p("store_code"), "markets": _p("scope")},
        "rows_at": ("lines", "rows"),
        "grain": "one row per P&L line for the chosen scope",
        "module": "finance",
        "index": ("4", "4c"),
    },
    "profit_and_loss_range": {
        "label": "Profit & loss over several months",
        "answers": "Show the P&L month by month. Which month was best or worst, and what changed?",
        "path": "/api/v1/account/pl-range",
        "params": {"period_from": _p("period", required=True), "period_to": _p("period"),
                   "scope": _p("scope"), "stores": _p("store_code"), "markets": _p("scope")},
        "rows_at": ("lines", "rows"),
        "grain": "one row per P&L line, one column per month",
        "module": "finance",
        "index": ("4c",),
    },
    "store_roster": {
        "label": "The stores this login may see",
        "answers": "Which stores do I have? What are they called? Which market is a store in?",
        "path": "/api/v1/commcalc/stores",
        "params": {},
        "rows_at": ("stores", "rows"),
        "grain": "one row per store",
        "module": None,
        "index": ("13",),
    },
}


# ── pure helpers (every one of these is what the harness proves) ─────────────────────────────────
def keys():
    """Every registered question key, in a stable order."""
    return tuple(sorted(DATA_QUESTIONS))


def question(key):
    """The one entry for `key`. KeyError is deliberate — a typo must not read as "no such data"."""
    return DATA_QUESTIONS[str(key)]


def answerable(enabled_modules):
    """The question keys answerable for a tenant whose enabled module keys are `enabled_modules`.

    PURE, and the honest form of "the assistant improves as more data is ingested": a question whose
    module is not switched on is not offered, so the assistant never promises a number the tenant
    has no feed for. `None` means "the entitlement is unknown here" and keeps every question — the
    endpoint is still the gate, so this can only ever narrow what is OFFERED, never widen what is
    ALLOWED."""
    if enabled_modules is None:
        return keys()
    have = {str(m).strip() for m in enabled_modules if str(m or "").strip()}
    return tuple(k for k in keys()
                 if not DATA_QUESTIONS[k].get("module") or DATA_QUESTIONS[k]["module"] in have)


def catalog(enabled_modules=None):
    """What the model is shown: key, label, the owner-worded `answers`, the grain, and the
    parameters it may set. The endpoint path is deliberately ABSENT — the model picks a question,
    never a URL, so no model output is ever interpreted as a route."""
    out = []
    for k in answerable(enabled_modules):
        q = DATA_QUESTIONS[k]
        params = {}
        for name, spec in sorted((q.get("params") or {}).items()):
            params[name] = {"kind": spec["kind"], "required": bool(spec.get("required")),
                            "repeatable": bool(spec.get(_MULTI)), "note": spec.get("note") or ""}
        for name in q.get("path_params") or ():
            params[name] = {"kind": "period", "required": True, "repeatable": False,
                            "note": "which month"}
        out.append({"question": k, "label": q["label"], "answers": q["answers"],
                    "grain": q["grain"], "parameters": params})
    return out


def validate(key, given):
    """PURE validation of one tool call: `(path, query, errors)`.

    `path` comes from the REGISTRY with path params substituted after matching their pattern, so a
    model cannot reach an endpoint that is not registered, cannot add a parameter the registry does
    not list, and cannot put anything but a pattern-matched value in one. `org_id` is never accepted
    from a caller: the request is made with the signed-in user's own token and the tenant middleware
    resolves the org, exactly as it does for the screen."""
    errors = []
    try:
        q = question(key)
    except KeyError:
        return None, {}, [f"'{key}' is not a registered question"]

    given = dict(given or {})
    path = q["path"]
    for name in q.get("path_params") or ():
        raw = given.pop(name, None)
        vals = raw if isinstance(raw, list) else ([] if raw is None else [raw])
        val = str(vals[0]).strip() if vals else ""
        if not val:
            errors.append(f"'{name}' is required")
            continue
        if not _KIND_RE["period"].match(val):
            errors.append(f"'{name}' must be a month like 2026-09 or September 2026")
            continue
        path = path.replace("{" + name + "}", val)

    specs = q.get("params") or {}
    query = {}
    for name, raw in sorted(given.items()):
        if name in ("org_id", "authorization"):
            errors.append(f"'{name}' is never set by a caller")
            continue
        spec = specs.get(name)
        if not spec:
            errors.append(f"'{name}' is not a parameter of '{key}'")
            continue
        vals = [v for v in (raw if isinstance(raw, list) else [raw]) if str(v or "").strip()]
        if not vals:
            continue
        if not spec.get(_MULTI) and len(vals) > 1:
            errors.append(f"'{name}' takes one value")
            continue
        rx = _KIND_RE[spec["kind"]]
        bad = [v for v in vals if not rx.match(str(v).strip())]
        if bad:
            errors.append(f"'{name}' is not a valid {spec['kind']}: {str(bad[0])[:40]!r}")
            continue
        query[name] = [str(v).strip() for v in vals] if spec.get(_MULTI) else str(vals[0]).strip()

    for name, spec in sorted(specs.items()):
        if spec.get("required") and name not in query:
            errors.append(f"'{name}' is required")

    if "{" in path:
        errors.append("a required month was not supplied")
    return (None if errors else path), query, errors


def rows_from(key, payload):
    """The row list inside a report's response envelope, using the envelope keys the registry
    declares. Returns `[]` for a summary-shaped question. The envelope IS a fact about the endpoint,
    so it is declared; the row's COLUMNS are not — the caller reads those off the rows themselves."""
    q = question(key)
    if isinstance(payload, list):
        return payload
    if not isinstance(payload, dict):
        return []
    for at in q.get("rows_at") or ():
        v = payload.get(at)
        if isinstance(v, list):
            return v
    return []


def columns_of(rows):
    """The column names actually present in `rows`, in first-seen order. This is the DISCOVERY that
    keeps the registry from holding a second copy of each report's shape."""
    seen, out = set(), []
    for r in rows[:200]:
        if isinstance(r, dict):
            for c in r:
                if c not in seen:
                    seen.add(c)
                    out.append(c)
    return tuple(out)
