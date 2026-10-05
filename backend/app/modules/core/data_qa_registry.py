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
#
# `self_safe` is the one new fact (owner directive 2026-10-05: a rep may ask the assistant about
# *"only their own commission, only their action plan"*). It DECLARES a property of the endpoint —
# that its handler narrows a self-scoped caller to their own rows SERVER-SIDE, naming the mechanism
# in `self_note` — and it is read only to decide which questions a rep is OFFERED. It is never a
# permission: no row is filtered, no field stripped and no name matched anywhere in this package;
# that work stays in `commcalc/payout_audience.py`, which is its one home, and
# `harness_data_qa_lock.py` fails the build if a second copy appears here.
#
# FAIL-CLOSED: a question that does not say `self_safe` is NOT offered to a rep. A new report is
# therefore manager-only until somebody has read its handler and said otherwise, which is the right
# default for a file that decides who may read pay.
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
    "my_commission": {
        "label": "My own commission for a month",
        "answers": ("What is my commission this month? What have I earned? What did I get paid "
                    "for? Which KPIs did I hit? How much is at risk? What is my tier?"),
        "path": "/api/v1/commcalc/commissions/{period}",
        "path_params": ("period",),
        "params": {},
        "rows_at": (),
        "grain": "one row per rep per month (a self-scoped caller gets only their own)",
        "module": None,
        "index": ("6i", "6j", "6m"),
        "self_safe": True,
        "self_note": ("the handler takes NO rep parameter; `_get_commissions_rows` narrows to the "
                      "caller's own rows via `_caller_rep_keys` + `payout_audience.mine_only`, and "
                      "a self rep it cannot map to a rep sees nothing"),
    },
    "my_commission_range": {
        "label": "My own commission across several months",
        "answers": ("What have I earned over the last few months? Is my commission going up or "
                    "down? Which month paid me most? How does this month compare to last?"),
        "path": "/api/v1/commcalc/commissions-range",
        "params": {"period_from": _p("period", required=True),
                   "period_to": _p("period", note="blank = through the current month")},
        "rows_at": ("rows",),
        "grain": "one row per rep per month over the range (a self-scoped caller gets only their own)",
        "module": None,
        "index": ("6g", "6j"),
        "self_safe": True,
        "self_note": ("each month is the SAME handler as my_commission, called with the caller's own "
                      "token, so the narrowing is inherited rather than repeated"),
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
        "self_safe": True,
        "self_note": ("`get_action_plan` substitutes the rep's OWN store keyset via "
                      "`_caller_self_keyset`, keeps only their own rep plans via "
                      "`payout_audience.mine_only`, drops a `rep=` that is not them, and suppresses "
                      "the cross-rep store roll-up"),
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
        # §13e: `/commcalc/stores` and `/storeops/stores` are the two RAW store vocabularies, and
        # unioning them offered 58 options for 31 real stores. `/core/filter-options` is the ONE home
        # that folds them through `core.scope.build_store_options` — one option per physical store,
        # every unchosen spelling kept in `also_known_as`. The assistant reads the folded list so it
        # cannot report one store twice or treat two spellings as two stores.
        "path": "/api/v1/core/filter-options",
        "params": {},
        "rows_at": ("stores", "rows"),
        "grain": "one row per physical store, with its other spellings in also_known_as",
        "module": None,
        # NOT self_safe. The route would narrow nothing for a rep, and the owner's directive is that
        # a rep may ask about *"only their own commission, only their action plan"* — so widening it
        # here, however harmless the rows look, would be this file deciding a policy it was told.
        "index": ("13", "13e"),
    },
    # ── WORKFORCE (owner 2026-10-05: *"i asked who is working in 509 nostrand"*) ─────────────────
    # The question that exposed the gap. Before these two rows the assistant had no scheduling read
    # at all, so "who is working at a store" had no registered answer and the ask bar fell through to
    # guessing at a report name. Both endpoints were narrowed per-person by `gate_person_rows` in
    # §14w, which is what makes them safe to offer a rep rather than managers only.
    "store_schedule": {
        "label": "Who is scheduled to work, by store and date",
        "answers": ("Who is working at a store today, or this week? Who is scheduled at a store on a "
                    "date? What shifts does somebody have? Who opens or closes?"),
        "path": "/api/v1/storeops/shifts",
        "params": {"store_code": _p("store_code", note="one store's code"),
                   "week_start": _p("date", note="first shift_date to include, inclusive"),
                   "week_end": _p("date", note="last shift_date to include, inclusive; for a "
                                              "single day set it equal to week_start")},
        "rows_at": (),
        "grain": ("one row per shift — employee x store x date; a person with two shifts in a day "
                  "has two rows"),
        "module": None,
        # The ENDPOINT does narrow a rep to their own shifts — `get_shifts` passes every row through
        # `storeops.gate_person_rows`, which reads `core.scope.visible_people_keyset` (§14w). It is
        # still NOT marked self_safe, because the owner's directive names exactly two things a rep
        # may ask the assistant, and a schedule is not one of them. Offering it would be this file
        # granting reach rather than recording it.
        # SCHEDULED, not clocked. `scheduled_hours` is the reliable column: this tenant has no
        # punched hours at all (0 of 640 July and 0 of 613 August shifts carry actual_hours > 0), so
        # an answer built on `actual_hours` would read as zero rather than as "not reported".
        "index": ("14", "14v", "14w"),
    },
    "visible_people": {
        "label": "The people this login may see",
        "answers": ("Who works at a store? Who is on my team? What is somebody's role? Who reports "
                    "to me?"),
        "path": "/api/v1/storeops/employees/visible",
        "params": {},
        "rows_at": ("employees", "rows"),
        "grain": "one row per employee this login may see, with their home store and role",
        "module": None,
        # Likewise NOT self_safe: the handler's reach ladder does narrow a self-scoped caller to
        # themselves via `core.scope.roster_keyset`, but a roster is not one of the two things the
        # owner said a rep may ask.
        # 8 active employees in the house org have NO home_store and are deliberately kept visible
        # rather than dropped (§29.6), so "who works at store X" will not account for everybody.
        "index": ("14", "29"),
    },
}


# ── pure helpers (every one of these is what the harness proves) ─────────────────────────────────
def keys():
    """Every registered question key, in a stable order."""
    return tuple(sorted(DATA_QUESTIONS))


def question(key):
    """The one entry for `key`. KeyError is deliberate — a typo must not read as "no such data"."""
    return DATA_QUESTIONS[str(key)]


def self_safe_keys():
    """The question keys whose endpoint narrows a self-scoped caller to their own rows server-side.

    Read off `self_safe`, so adding a question cannot accidentally widen this set: an entry that
    says nothing is absent. PURE."""
    return tuple(k for k in keys() if DATA_QUESTIONS[k].get("self_safe") is True)


def answerable(enabled_modules, caller_is_self=False):
    """The question keys answerable for a tenant whose enabled module keys are `enabled_modules`,
    for a caller who is (or is not) self-scoped.

    PURE, and the honest form of "the assistant improves as more data is ingested": a question whose
    module is not switched on is not offered, so the assistant never promises a number the tenant
    has no feed for. `None` means "the entitlement is unknown here" and keeps every question — the
    endpoint is still the gate, so this can only ever narrow what is OFFERED, never widen what is
    ALLOWED.

    `caller_is_self=True` narrows further, to the `self_safe` questions only (owner directive
    2026-10-05: a rep gets *"only their own commission, only their action plan"*). Two things this
    is NOT: it is not the security boundary — each endpoint narrows its own rows and would refuse a
    rep anyway — and it is not a judgement about the caller, only about which endpoints have been
    read and shown to narrow. Belt and braces, in that order: this keeps a rep from being OFFERED a
    question whose answer would be an empty report or a refusal."""
    if enabled_modules is None:
        offered = keys()
    else:
        have = {str(m).strip() for m in enabled_modules if str(m or "").strip()}
        offered = tuple(k for k in keys()
                        if not DATA_QUESTIONS[k].get("module") or DATA_QUESTIONS[k]["module"] in have)
    if caller_is_self:
        safe = set(self_safe_keys())
        offered = tuple(k for k in offered if k in safe)
    return offered


def catalog(enabled_modules=None, caller_is_self=False):
    """What the model is shown: key, label, the owner-worded `answers`, the grain, and the
    parameters it may set. The endpoint path is deliberately ABSENT — the model picks a question,
    never a URL, so no model output is ever interpreted as a route."""
    out = []
    for k in answerable(enabled_modules, caller_is_self):
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
