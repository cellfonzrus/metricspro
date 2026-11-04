"""INCENTIVE-PLAN WORKFLOW — the ONE home for "which plan am I on, and what can replace it?"

Owner directive 2026-11-01, verbatim: *"the plan which is active in incentive payout when you click
on it it takes you to the page which has all kinds of Incentive structure listed which becomes
confusing even to me ... When we click on the plan it should show the one which is active and below
it should give the option to change the plan and when you click on change the plan it should show
what the available options are and then clicking on the available options the user should be able to
create a new plan — all of this should be a work flow than random data seen on the screen"*.

THE CLASS, NOT THE INSTANCE. The confusing screen is a symptom. The general fact that was wrong is
that **nothing in the platform answered "which plan am I on?"** — three surfaces showed the operator
the raw plan table and left the question to them:

  · `router.payout_plans_overview` counts live plans per carrier (and owned the live/belongs
    predicate privately),
  · the plan editor lists every plan with every setting beside it (the screen the owner means),
  · `payout_plans_diagnose` lists them a third time under "why is my report empty?".

Two paths answering one question is the duplicate defect the index rules forbid; three is the same
defect wearing a hat. So the predicates live HERE, `payout_plans_overview` dereferences them, and the
ordered steps of changing a plan are data this module returns rather than layout a screen invents.

WHAT THIS MODULE IS NOT. It computes no pay, reads no rate and writes nothing. The rates an employee
sees stay with `payout_structure` (§6p, the one document), the save stays with
`POST /commission-plans`, and importing a carrier's curves stays with `template_clone` (§mig 221).
Each option therefore carries the DOOR it belongs to (`endpoint`), so no caller — browser or
otherwise — has to decide which write path a choice implies.

MULTI-TENANT (project directive 2026-10-30). PURE: no DB, no client, and no tenant identifier of any
kind appears below — every answer is derived from the rows the caller hands in, and `engine` is a
REQUIRED positional argument, so a caller that has not resolved which engine pays this tenant
(`router._resolve_carrier_mode`, the same resolver the pay run uses) cannot answer at all. There is
no house default to fall back to, because "which engine pays you" is a tenant's own declaration and
not a policy.

MONEY-SAFE. A plan this workflow creates is INACTIVE with no assignments: a new plan cannot move a
single dollar until the operator assigns it and recalculates the period. That is deliberate — the
owner's ask is a guided way to *author* a plan, not a one-click way to change what reps are paid.

Proof: `backend/tests/unit/test_plan_workflow.py` (pure) +
`backend/tests/integration/test_incentive_plan_workflow_endpoint.py` (the real app over the
database double, every tenant); lock `backend/harness_plan_workflow_lock.py`.
"""

# The engines a tenant can be paid by. Mirrors `router._resolve_carrier_mode`'s two answers; it is
# NOT a second resolution of which one applies — the caller passes the resolved value.
ENGINES = ("boost", "plan")

# The ordered steps. The screen renders this sequence; it does not hold its own copy of it.
STEP_KEYS = ("active", "change", "create")

# Every starting point a new plan can be authored from. A kind outside this set is refused rather
# than silently becoming a blank plan (an unknown option that creates something is worse than one
# that is rejected).
OPTION_KINDS = ("copy_active", "copy_plan", "carrier_template", "blank")

# The two write doors a choice can lead to. Named here so a caller never routes by guesswork.
PLAN_SAVE_ENDPOINT = "/api/v1/commcalc/commission-plans"
TEMPLATE_CLONE_ENDPOINT = "/api/v1/commcalc/carrier-template/clone"

# The columns of a rule / tier that describe POLICY. Everything else on the row is identity
# (`id`, `plan_id`) or bookkeeping, and copying identity would make the save EDIT the plan the copy
# came from instead of creating a new one.
_RULE_IDENTITY = ("id", "plan_id", "created_at", "updated_at")
_TIER_IDENTITY = _RULE_IDENTITY

BOOST_HEADLINE = ("You are paid by the built-in KPI-tier engine (flat spiffs × KPI-count tier), "
                  "not by a configurable plan.")
BOOST_CAVEAT = ("This tenant is paid by the built-in engine today, so a plan created here pays "
                "nothing until the carrier's engine is switched to configurable plans.")


def _s(v):
    return "" if v is None else str(v).strip()


# ── THE TWO PREDICATES EVERY SURFACE MUST SHARE ──────────────────────────────────────────────────
def plan_is_live(plan):
    """Is this plan switched on? `commcalc.commission_plan.is_active` defaults TRUE on the table, so
    an ABSENT key is live — reading a missing column as "off" would hide a paying plan."""
    return bool((plan or {}).get("is_active", True))


def plan_serves_carrier(plan, carrier_id):
    """Does this plan apply under the given carrier lens? A NULL `carrier_id` is the tenant's
    catch-all and serves every carrier; no lens asked for (`None`) puts every plan in scope."""
    if not carrier_id:
        return True
    pc = (plan or {}).get("carrier_id")
    return pc is None or _s(pc) == _s(carrier_id)


def live_plans_for_carrier(plans, carrier_id):
    """The plans a payout under this carrier could come from — live AND in scope, both predicates."""
    return [p for p in (plans or []) if plan_is_live(p) and plan_serves_carrier(p, carrier_id)]


# ── STEP 1: WHICH PLAN AM I ON ───────────────────────────────────────────────────────────────────
def summarise_plan(plan):
    """A plan as the workflow presents it: what it is, how big it is, and WHO it covers. No rate is
    restated here — the rates have one home (`payout_structure`) and this is not it."""
    p = plan or {}
    covers = [{"scope": _s(a.get("scope")) or "default",
               "value": (None if a.get("scope_value") in (None, "") else _s(a.get("scope_value")))}
              for a in (p.get("assignments") or [])]
    return {
        "id": p.get("id"),
        "name": _s(p.get("name")),
        "carrier_id": p.get("carrier_id"),
        "is_active": plan_is_live(p),
        "rule_count": len(p.get("rules") or []),
        "tier_count": len(p.get("tiers") or []),
        "assignment_count": len(covers),
        "covers": covers,
        "notes": (_s(p.get("notes")) or None),
    }


def active_plan(engine, plans, *, carrier_id=None):
    """THE plan this tenant pays by right now, or None.

    A plan is what the tenant pays only when it is live, in scope, AND assigned to somebody — an
    unassigned plan pays nobody however complete it looks, and presenting it as "active" is the
    plausible-looking zero that hides this whole class. Where several qualify (a per-market setup is
    normal) the one covering the MOST people leads, ties broken by name so the answer is stable.

    `engine` is required and honoured: a Boost-engine tenant is not paid by a `commission_plan` row
    at all, so there is no active plan to return even if rows exist."""
    if _s(engine) == "boost":
        return None
    candidates = [p for p in live_plans_for_carrier(plans, carrier_id) if (p.get("assignments") or [])]
    if not candidates:
        return None
    best = sorted(candidates,
                  key=lambda p: (-len(p.get("assignments") or []), _s(p.get("name")).lower()))[0]
    return summarise_plan(best)


def active_step(engine, plans, *, carrier_id=None):
    """Step 1 of the workflow: one sentence the operator can act on, and the plan behind it.

    Nothing declared means nothing identified, and the surface SAYS so (project directive
    2026-10-30): a plan-engine tenant with no assignment is told it pays nobody, never shown a plan
    it does not actually pay by."""
    eng = _s(engine)
    if eng == "boost":
        return {
            "key": "active", "engine": "boost", "built_in": True, "plan": None,
            "pays_nobody": False, "can_change": True,
            "headline": BOOST_HEADLINE,
            "rates_href": "/commcalc/settings",
        }
    plan = active_plan(eng, plans, carrier_id=carrier_id)
    if plan is None:
        live = live_plans_for_carrier(plans, carrier_id)
        detail = (f"{len(live)} plan(s) are set up but none is assigned to anyone"
                  if live else "no incentive plan is set up yet")
        return {
            "key": "active", "engine": eng, "built_in": False, "plan": None,
            "pays_nobody": True, "can_change": True,
            "headline": f"No active incentive plan — {detail}, so reps calculate to $0.",
            "rates_href": None,
        }
    return {
        "key": "active", "engine": eng, "built_in": False, "plan": plan,
        "pays_nobody": False, "can_change": True,
        "headline": (f"“{plan['name']}” is the active incentive plan — "
                     f"{plan['rule_count']} rule(s) covering {plan['assignment_count']} assignment(s)."),
        "rates_href": "/commcalc/commission-plans",
    }


# ── STEPS 2 + 3: WHAT CAN REPLACE IT ─────────────────────────────────────────────────────────────
def _plural(n, word):
    return f"{n} {word}{'' if n == 1 else 's'}"


def options(engine, plans, *, templates=None, carrier_id=None):
    """Every starting point a new plan can be authored from, each self-describing.

    The screen renders these; it invents no labels and holds no list of its own. Each option carries
    `creates` ("plan" or "schedules") so the operator is told what the choice makes, and `caveat`
    where the choice would not actually pay yet."""
    eng = _s(engine)
    caveat = BOOST_CAVEAT if eng == "boost" else None
    active = active_plan(eng, plans, carrier_id=carrier_id)
    out = []

    if active is not None:
        out.append({
            "kind": "copy_active", "key": "copy_active",
            "label": f"Start from the plan you pay today (“{active['name']}”)",
            "detail": (f"Copies {_plural(active['rule_count'], 'rule')} and "
                       f"{_plural(active['tier_count'], 'tier')}. Nobody is assigned to the copy, "
                       "so pay does not change until you assign it."),
            "creates": "plan", "source_plan_id": active["id"], "caveat": caveat,
        })

    active_id = active["id"] if active else None
    for p in sorted(plans or [], key=lambda x: _s(x.get("name")).lower()):
        if p.get("id") == active_id or not plan_serves_carrier(p, carrier_id):
            continue
        s = summarise_plan(p)
        state = "" if s["is_active"] else " (switched off)"
        out.append({
            "kind": "copy_plan", "key": f"copy_plan:{s['id']}",
            "label": f"Start from “{s['name']}”{state}",
            "detail": (f"Copies {_plural(s['rule_count'], 'rule')} and "
                       f"{_plural(s['tier_count'], 'tier')} from a plan already set up here."),
            "creates": "plan", "source_plan_id": s["id"], "caveat": caveat,
        })

    for t in (templates or []):
        src_org, src_car = _s(t.get("source_org_id")), _s(t.get("source_carrier_id"))
        if not src_org or not src_car:
            continue
        name = _s(t.get("carrier_name")) or _s(t.get("carrier_code")) or "shared carrier"
        out.append({
            "kind": "carrier_template", "key": f"carrier_template:{src_org}:{src_car}",
            "label": f"Import the shared “{name}” payout template",
            "detail": (f"Brings {_plural(int(t.get('schedule_count') or 0), 'payout schedule')} and "
                       f"{_plural(int(t.get('line_count') or 0), 'curve line')} into this company as "
                       "your own editable copy."),
            "creates": "schedules", "source_plan_id": None,
            "caveat": caveat or ("This is your own company's template." if t.get("is_own") else None),
        })

    out.append({
        "kind": "blank", "key": "blank",
        "label": "Start from a blank plan",
        "detail": "An empty plan you add rules to yourself. Created switched off, assigned to nobody.",
        "creates": "plan", "source_plan_id": None, "caveat": caveat,
    })
    return out


def change_step(engine, plans, *, templates=None, carrier_id=None):
    """Step 2/3: the one action ("change the plan") and, behind it, what the options are."""
    opts = options(engine, plans, templates=templates, carrier_id=carrier_id)
    return {
        "key": "change", "action_label": "Change the plan",
        "headline": "Pick what the new plan should start from.",
        "options": opts, "option_count": len(opts),
    }


# ── STEP 4: CREATE IT ────────────────────────────────────────────────────────────────────────────
def _copy_children(plan):
    rules, tiers = [], []
    for r in (plan.get("rules") or []):
        rules.append({k: v for k, v in r.items() if k not in _RULE_IDENTITY})
    for t in (plan.get("tiers") or []):
        tiers.append({k: v for k, v in t.items() if k not in _TIER_IDENTITY})
    return rules, tiers


def draft_from_option(option_key, *, plans=None, name="", carrier_id=None):
    """The exact request a chosen option implies — which door, and the body for it.

    It WRITES NOTHING. The plan save and the template cloner remain the only writers; this derives
    what to send them, in one place, so the browser never assembles a money-config body of its own.
    An unknown option, or a copy of a plan that is not there, is REFUSED (`ValueError`) rather than
    silently becoming a blank plan."""
    key = _s(option_key)
    nm = _s(name)
    plans = plans or []

    if key.startswith("carrier_template:"):
        parts = key.split(":", 2)
        if len(parts) != 3 or not parts[1] or not parts[2]:
            raise ValueError(f"malformed carrier-template option: {option_key!r}")
        return {"kind": "carrier_template", "endpoint": TEMPLATE_CLONE_ENDPOINT,
                "body": {"source_org_id": parts[1], "source_carrier_id": parts[2],
                         "dry_run": False}}

    if not nm:
        raise ValueError("a new plan needs a name")

    source = None
    if key == "copy_active":
        # dereferenced, never re-derived: the same `active_plan` the screen was shown.
        chosen = active_plan("plan", plans, carrier_id=carrier_id)
        if chosen is None:
            raise ValueError("there is no active plan to copy")
        source = next((p for p in plans if _s(p.get("id")) == _s(chosen["id"])), None)
        if source is None:
            raise ValueError("there is no active plan to copy")
    elif key.startswith("copy_plan:"):
        pid = key.split(":", 1)[1]
        source = next((p for p in plans if _s(p.get("id")) == _s(pid)), None)
        if source is None:
            raise ValueError(f"no such plan to copy: {pid!r}")
    elif key != "blank":
        raise ValueError(f"unknown option: {option_key!r}")

    rules, tiers = _copy_children(source) if source else ([], [])
    body = {
        "name": nm,
        "carrier_id": carrier_id or (source or {}).get("carrier_id") or None,
        "base_tier_metric": (source or {}).get("base_tier_metric") or None,
        # A new plan is created switched OFF and assigned to NOBODY: saving it cannot move pay.
        "is_active": False,
        "notes": (_s((source or {}).get("notes")) or None),
        "rules": rules, "tiers": tiers, "assignments": [],
    }
    return {"kind": "copy_plan" if source else "blank", "endpoint": PLAN_SAVE_ENDPOINT,
            "body": body, "copied_from": (source or {}).get("id")}


# ── THE WORKFLOW, ASSEMBLED ──────────────────────────────────────────────────────────────────────
def create_step(engine, plans, *, carrier_id=None):
    """Step 3: what happens when the operator names the plan. Stated so the screen does not promise
    something the save does not do — the two sentences here are the save's actual behaviour."""
    return {
        "key": "create", "action_label": "Create the new plan",
        "headline": "Name it, and it is created switched off with nobody assigned.",
        "then": ["Add or edit its rules.",
                 "Assign it to the markets, stores or employees it should pay.",
                 "Switch it on, then run the calculation for the period."],
        "creates_inactive": True,
    }


def workflow(engine, plans, *, templates=None, carrier_id=None):
    """The whole thing: engine, then the three ordered steps. The screen renders `steps` in order."""
    eng = _s(engine)
    if eng not in ENGINES:
        raise ValueError(f"engine must be one of {ENGINES}, got {engine!r}")
    return {
        "engine": eng,
        "carrier_id": carrier_id or None,
        "steps": [
            active_step(eng, plans, carrier_id=carrier_id),
            change_step(eng, plans, templates=templates, carrier_id=carrier_id),
            create_step(eng, plans, carrier_id=carrier_id),
        ],
    }
