"""A MULTI-STEP FLOW SOMEBODY CAN LEAVE HALFWAY — the one home for that fact (index §CORE-51).

WHAT WAS ASKED FOR (owner 2026-11-25, verbatim)
───────────────────────────────────────────────
*"The way we have set up teh closing workflow , it is very easy for a new person to start and then
pick up where tehy left off, need similar for the Employee set up in logical steps leading up to
onboaring and then simlar workflow for scheduling andn simlar workflow for runnign the payroll with
paying attention to details"*

THE CLASS, NOT THE INSTANCE. Three flows were asked for in one sentence. The instance is "employee
setup has no workflow"; the class is **"the platform has no answer to *how far through a flow is
this, and where do I pick it up*"** — and answering it once per flow is three homes for one
question, which is the sibling derivation the index rules forbid. The third copy would drift from
the first two, and a flow a new person abandons halfway is exactly the thing that must not drift.

So the rail, the lamps, the "resume here", the progress count and the board summary are derived
HERE, once. A flow declares its STEPS and answers a VERDICT per step; it decides nothing about how
those are displayed, ordered or counted.

WHAT IS ALREADY TRUE, AND SO IS NOT REBUILT HERE (the duplicate-check build gate)
─────────────────────────────────────────────────────────────────────────────────
  · `closing/unfinished_day` (§29.12) is the shape this was modelled on and is NOT a caller: it
    answers a different question — *what happened to one store-day's closing submits* — for a flow
    that has ONE step. Its four states are a store-day's states, not a flow's progress. Keeping them
    apart is deliberate: folding "turned away" into a generic rail would put a closing refusal in
    front of readers that have nothing to do with closings.
  · `commcalc/onboarding_intake.rail` (§30) is the nearest sibling and is EXCUSED, with the reason
    stated rather than left implicit: its statuses describe *an uploaded source's verification*
    (`verified` / `retired` — a source can be retired, a step cannot), and its rail carries a
    stage × instance dimension — twelve commission statements under one stage — that no per-subject
    flow has. Folding it in would widen this home to carry a dimension two of its three callers do
    not have. `harness_guided_workflow.py` §L fails the build if a FOURTH copy of this vocabulary
    appears, so the excuse cannot quietly become a precedent.
  · `core/onboarding.build_status` is per-TENANT module setup (one checklist per org, predicate-
    evaluated against tables). It is a task list, not a stepped flow, and it has no resume.

NO TENANT'S WORDS LIVE HERE, AND NO POLICY EITHER. This module names no role, no store, no carrier
and no step: every step and every reason is passed in by the flow that declared it (owner's
2026-10-30 rule — a vocabulary a tenant declares is a REQUIRED argument, never a default with one
tenant's words behind it). The only strings it owns are the five status words and their labels.

PURE: no I/O, no database client, no framework import. The caller does the read and hands facts in.
"""
from __future__ import annotations

# ── The vocabulary. Five statuses, and nothing may invent a sixth. ───────────────────────────────
#: Nobody has begun this step. No evidence of it exists at all.
NOT_STARTED = "not_started"
#: Something has been entered and the step is not finished. The honest halfway state.
IN_PROGRESS = "in_progress"
#: The step is waiting on a person, and the flow can SAY WHAT IS MISSING. Beats in_progress,
#: because "started" tells somebody nothing they can act on.
NEEDS_INPUT = "needs_input"
#: This step does not apply here, and the flow says why. NEVER rendered as done — a step that was
#: skipped and a step that was completed are different facts, and a tenant that runs no time clock
#: must not read as a tenant whose time clock is set up.
SKIPPED = "skipped"
#: Finished.
DONE = "done"

STATUSES = (NOT_STARTED, IN_PROGRESS, NEEDS_INPUT, SKIPPED, DONE)

#: What a person reads for each status. No database, table, schema or hosting name may appear here
#: (index §19.38) — these strings reach the screen.
LABELS = {
    NOT_STARTED: "Not started",
    IN_PROGRESS: "In progress",
    NEEDS_INPUT: "Needs your input",
    SKIPPED: "Not needed here",
    DONE: "Done",
}

#: The statuses that still need somebody. `resume_key` looks only at these.
OPEN_STATUSES = (NOT_STARTED, IN_PROGRESS, NEEDS_INPUT)

#: The statuses that settle a step, either way. A settled dependency stops blocking what follows.
SETTLED_STATUSES = (DONE, SKIPPED)


# ── Declaring a flow's steps ─────────────────────────────────────────────────────────────────────
def step(key: str, title: str, *, why: str = "", required: bool = True,
         depends_on=(), screen: str = "") -> dict:
    """One step of a flow.

    `why` is the one line a new person reads under the title. `screen` is where the work is actually
    done — the door that already exists; this home never becomes a second place to do the work.
    `required` False means the flow can finish without it, and the flow may answer `skipped`.
    """
    return {"key": str(key or "").strip(), "title": str(title or "").strip(),
            "why": str(why or "").strip(), "required": bool(required),
            "depends_on": tuple(str(d) for d in (depends_on or ())),
            "screen": str(screen or "").strip()}


def declare(steps) -> tuple:
    """Validate a flow's steps and freeze their order.

    A step may only depend on a step declared BEFORE it. That single rule makes a cycle impossible
    to express, and makes the declared order the real order — so the rail a person reads top to
    bottom is the order the work can actually be done in.
    """
    out, seen = [], set()
    for s in steps or ():
        key = s.get("key") or ""
        if not key:
            raise ValueError("a step needs a key")
        if not s.get("title"):
            raise ValueError(f"step {key!r} needs a title — it is what a person reads")
        if key in seen:
            raise ValueError(f"duplicate step key {key!r}")
        for dep in s.get("depends_on") or ():
            if dep not in seen:
                raise ValueError(
                    f"step {key!r} depends_on {dep!r}, which is not declared before it")
        seen.add(key)
        out.append(dict(s))
    if not out:
        raise ValueError("a flow needs at least one step")
    return tuple(out)


def step_keys(steps) -> tuple:
    return tuple(s["key"] for s in steps)


# ── One step's verdict, as the flow answers it ───────────────────────────────────────────────────
def verdict(*, done: bool = False, started: bool = False, needs_input=(), skipped: bool = False,
            detail=None, action: str = "") -> dict:
    """What the flow found for one step.

    `needs_input` is a list of sentences a person can act on ("No pay rate on file") — never a bare
    string and never an empty one, because "needs input" with nothing named is the state this whole
    package exists to abolish.
    """
    if isinstance(needs_input, str):
        raise ValueError("needs_input is a list of sentences, not one string")
    reasons = tuple(str(r).strip() for r in (needs_input or ()))
    if any(not r for r in reasons):
        raise ValueError("every needs_input reason must say what is missing")
    return {"done": bool(done), "started": bool(started), "needs_input": reasons,
            "skipped": bool(skipped), "detail": dict(detail or {}), "action": str(action or "")}


def status_of(v) -> str:
    """THE predicate: one verdict, one status. Nothing else decides this."""
    if v.get("done"):
        return DONE
    if v.get("skipped"):
        return SKIPPED
    if v.get("needs_input"):
        return NEEDS_INPUT
    if v.get("started"):
        return IN_PROGRESS
    return NOT_STARTED


# ── The rail ─────────────────────────────────────────────────────────────────────────────────────
def _check(steps, verdicts) -> None:
    """A flow must answer EVERY step it declared, and no step it did not.

    A missing verdict is a refusal rather than a silent `not_started`: a flow that forgot to look
    would otherwise render as a flow nobody has begun, which is a plausible-looking answer and
    therefore the kind that hides (owner's 2026-10-30 rule).
    """
    declared = set(step_keys(steps))
    given = set(verdicts or {})
    for missing in sorted(declared - given):
        raise ValueError(f"no verdict for declared step {missing!r}")
    for extra in sorted(given - declared):
        raise ValueError(f"verdict for {extra!r}, which this flow did not declare")
    for s in steps:
        if s["required"] and (verdicts[s["key"]] or {}).get("skipped"):
            raise ValueError(f"step {s['key']!r} is required and cannot be skipped")


def rail(steps, verdicts) -> list:
    """One row per step, in declared order — everything a screen needs and nothing it has to derive."""
    _check(steps, verdicts)
    settled, out = set(), []
    for s in steps:
        v = verdicts[s["key"]]
        st = status_of(v)
        blocked = tuple(d for d in s["depends_on"] if d not in settled)
        out.append({"key": s["key"], "title": s["title"], "why": s["why"],
                    "required": s["required"], "screen": s["screen"],
                    "status": st, "label": LABELS[st],
                    "needs_input": v["needs_input"], "blocked_by": blocked,
                    "detail": v["detail"], "action": v["action"]})
        if st in SETTLED_STATUSES:
            settled.add(s["key"])
    return out


def resume_key(steps, verdicts) -> str | None:
    """Where this subject picks up: the first open step nothing is blocking.

    A step whose dependency is unsettled is never offered, even when something was typed into it —
    sending a new person into step 4 while step 2 is empty is how a flow gets abandoned.
    """
    for row in rail(steps, verdicts):
        if row["status"] in OPEN_STATUSES and not row["blocked_by"]:
            return row["key"]
    return None


def progress(steps, verdicts) -> dict:
    """How far along, counting the required steps separately from the optional ones."""
    rows = rail(steps, verdicts)
    req = [r for r in rows if r["required"]]
    return {"total": len(rows),
            "done": sum(1 for r in rows if r["status"] in SETTLED_STATUSES),
            "required_total": len(req),
            "required_done": sum(1 for r in req if r["status"] in SETTLED_STATUSES)}


def blocking(steps, verdicts) -> tuple:
    """The required steps that are not settled — what stands between here and finished."""
    return tuple(r["key"] for r in rail(steps, verdicts)
                 if r["required"] and r["status"] not in SETTLED_STATUSES)


def is_finished(steps, verdicts) -> bool:
    """THE predicate a caller honours. Every REQUIRED step settled; an optional one need not be."""
    return not blocking(steps, verdicts)


def describe(steps, verdicts) -> dict:
    """One subject's whole flow: the status, where to pick up, the progress and the rail."""
    rows = rail(steps, verdicts)
    stuck = blocking(steps, verdicts)
    resume = resume_key(steps, verdicts)
    titles = {r["key"]: r["title"] for r in rows}
    if not stuck:
        status = DONE
    elif any(r["status"] == NEEDS_INPUT for r in rows):
        status = NEEDS_INPUT
    elif all(r["status"] == NOT_STARTED for r in rows):
        status = NOT_STARTED
    else:
        status = IN_PROGRESS
    return {"status": status, "label": LABELS[status],
            "resume": resume, "resume_title": titles.get(resume, ""),
            "progress": progress(steps, verdicts), "blocking": stuck, "steps": rows}


def summarize(describes) -> dict:
    """The board over many subjects: how many at each status, and where they are getting stuck."""
    rows = list(describes or ())
    by_status = {s: 0 for s in STATUSES}
    stuck_count, stuck_title = {}, {}
    for d in rows:
        by_status[d["status"]] = by_status.get(d["status"], 0) + 1
        key = d.get("resume")
        if key:
            stuck_count[key] = stuck_count.get(key, 0) + 1
            stuck_title[key] = d.get("resume_title") or key
    order = sorted(stuck_count.items(), key=lambda kv: (-kv[1], kv[0]))
    return {"total": len(rows),
            "by_status": by_status,
            "finished": by_status[DONE],
            "open": len(rows) - by_status[DONE],
            "stuck_at": tuple({"key": k, "title": stuck_title[k], "count": n} for k, n in order)}
