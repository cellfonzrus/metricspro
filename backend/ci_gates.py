"""THE ONE HOME for "which workflow job runs this gate" (found 2026-11-04, index §CORE-34).

NAMED SO THE SUITE RUNNER DOES NOT COUNT IT. CI and the local sweep run `for f in harness_*.py`;
this is a library, not a proof, so it sits outside that glob — same reason as `harnesslib.py`.

WHY THIS EXISTS. On 2026-10-28, shipping the test framework, I told the owner in writing that this
repo had **"no frontend CI at all — not tests, not typecheck, not the lint ratchet."** That was
wrong. `npm run typecheck` and `npm run lint:gate` run on every frontend change, and have for weeks.
They live in `carrier-vocab-guard.yml` — a workflow whose name mentions neither, and which holds
**49 jobs**. I had read the workflow *filenames*, found nothing that sounded like the frontend, and
reported an absence.

THE CLASS, not the instance:

    **Whether a thing is gated is a fact about the workflows, and it was only answerable by reading
    all fifteen of them end to end.** A fact that expensive to look up does not get looked up — it
    gets guessed. And a guess about whether something is checked is worse than no answer at all,
    because it is indistinguishable from a measurement. Mine reached the owner as a measurement.

This is the §19.18 shape with the copies in people's heads rather than in files: the authoritative
answer existed (in the YAML), and every reader re-derived it badly. So the fact gets ONE home, and
the home is DERIVED from the workflows — never hand-written, because a hand-written list of what CI
checks is a second copy that drifts, which is the defect wearing a hat.

WHAT A "GATE" IS HERE. Anything a workflow runs in order to refuse a change: a `harness_*.py`, an
npm script (`typecheck`, `lint:gate`, `test`, `test:proofs`), or a pytest suite. Deliberately
including the npm scripts, because those are the ones I could not see.

TRIGGERS ARE PART OF THE ANSWER. A gate that runs only on a schedule is not gating a pull request,
so "is this checked?" must be answered with *when*. An index that omitted triggers would answer yes
where the truthful answer is "not on your PR".

Stdlib only — no PyYAML. The workflows here are regular enough to read line-wise, and a dependency
for a guard that must run everywhere is a cost without a return.
"""
from __future__ import annotations

import os
import re
from typing import Dict, List, Tuple

# ── what counts as a gate ────────────────────────────────────────────────────────────────────────
_HARNESS_SCRIPT = re.compile(r"\b(harness_[A-Za-z0-9_]+)\.py\b")
_HARNESS_MODULE = re.compile(r"-m\s+(harness_[A-Za-z0-9_]+)\b")
_NPM_RUN = re.compile(r"\bnpm\s+run\s+([A-Za-z0-9_:-]+)")
_PYTEST_MARK = re.compile(r"\bpytest\b[^\n|]*?-m\s+([A-Za-z0-9_]+)")
_PYTEST_BARE = re.compile(r"\bpython3?\s+-m\s+pytest\b")


def gates_in_step(run: str) -> List[str]:
    """Every gate a `run:` block executes, in the order it executes them, without duplicates.

    A gate NAME appearing in prose (`echo harness_alpha.py is nice`) is not a run of it, so the
    scan requires the shape of an invocation — the §CORE-33 lesson: read what the code DOES.
    """
    out: List[str] = []

    def add(gate: str) -> None:
        if gate not in out:
            out.append(gate)

    for line in str(run).split("\n"):
        # A harness runs as `python … harness_x.py` or `python -m harness_x`; anything else
        # mentioning the filename is prose.
        if re.search(r"\bpython3?\b", line):
            for m in _HARNESS_MODULE.finditer(line):
                add(m.group(1) + ".py")
            if re.search(r"\bpython3?\s+[^\n]*\.py", line) or "-m" in line:
                for m in _HARNESS_SCRIPT.finditer(line):
                    add(m.group(1) + ".py")
            if _PYTEST_BARE.search(line):
                mark = _PYTEST_MARK.search(line)
                add("pytest:" + mark.group(1) if mark else "pytest")
        for m in _NPM_RUN.finditer(line):
            add("npm:" + m.group(1))
    return out


# ── reading a workflow ───────────────────────────────────────────────────────────────────────────
def _strip_comment(line: str) -> str:
    """The line without a trailing `#` comment. Conservative: a `#` inside quotes is left alone."""
    out, quote = [], None
    for ch in line:
        if quote:
            out.append(ch)
            if ch == quote:
                quote = None
            continue
        if ch in "\"'":
            quote = ch
            out.append(ch)
            continue
        if ch == "#":
            break
        out.append(ch)
    return "".join(out)


def jobs_of_workflow(text: str) -> List[dict]:
    """Every job in a workflow: `{key, name, steps}`.

    `name` falls back to the job KEY when the job declares none — a job with no label still has to
    be nameable, or the index cannot point at it. Comments are stripped first, so a commented-out
    `name:` is not read as the job's title.
    """
    lines = str(text).split("\n")
    try:
        start = next(i for i, l in enumerate(lines) if _strip_comment(l).rstrip() == "jobs:")
    except StopIteration:
        return []

    jobs: List[dict] = []
    current: dict | None = None
    for raw in lines[start + 1:]:
        line = _strip_comment(raw)
        if line.strip() == "":
            continue
        indent = len(line) - len(line.lstrip())
        if indent == 0:                       # a new top-level key ends the jobs block
            break
        m = re.match(r"^  ([A-Za-z0-9_-]+):\s*$", line)
        if m:
            current = {"key": m.group(1), "name": None, "steps": []}
            jobs.append(current)
            continue
        if current is None:
            continue
        nm = re.match(r"^    name:\s*(.+?)\s*$", line)
        if nm and current["name"] is None:
            current["name"] = nm.group(1).strip().strip('"').strip("'")
            continue
        current["steps"].append(raw)

    for job in jobs:
        if job["name"] is None:
            job["name"] = job["key"]
        job["steps"] = "\n".join(job["steps"])
    return jobs


def workflow_triggers(text: str) -> List[str]:
    """The event names under `on:` — `pull_request`, `push`, `schedule`, `workflow_dispatch`."""
    lines = str(text).split("\n")
    try:
        start = next(i for i, l in enumerate(lines)
                     if re.match(r"^on:\s*$", _strip_comment(l).rstrip()))
    except StopIteration:
        inline = next((l for l in lines if re.match(r"^on:\s*\[", _strip_comment(l))), None)
        if inline:
            return [t.strip() for t in re.findall(r"[A-Za-z_]+", inline.split("[", 1)[1])]
        return []
    out = []
    for raw in lines[start + 1:]:
        line = _strip_comment(raw)
        if line.strip() == "":
            continue
        if len(line) - len(line.lstrip()) == 0:
            break
        m = re.match(r"^  ([A-Za-z_]+):", line)
        if m:
            out.append(m.group(1))
    return out


def gate_index(workflows: Dict[str, str]) -> Dict[str, List[Tuple[str, str, str]]]:
    """`{gate: [(workflow file, job key, job name), …]}` over every workflow given.

    A gate run by TWO workflows lists both. That is not noise: two jobs running one gate is two
    places it can be switched off, and the index is where you would notice.
    """
    idx: Dict[str, List[Tuple[str, str, str]]] = {}
    for wf in sorted(workflows):
        for job in jobs_of_workflow(workflows[wf]):
            for gate in gates_in_step(job["steps"]):
                idx.setdefault(gate, []).append((wf, job["key"], job["name"]))
    return idx


# ── rendering the one home ───────────────────────────────────────────────────────────────────────
HEADER = "<!-- GENERATED by backend/harness_ci_gate_index.py --bless — DO NOT EDIT BY HAND. -->"


def render_index(workflows: Dict[str, str], unrun: List[str], on_disk: List[str]) -> str:
    """The whole `docs/CI_GATES.md`, deterministic so a regenerate on an unchanged tree is a no-op.

    Three sections, because "is this gated?" has three possible answers and all three must be in
    one place or the reader goes back to guessing:
      · GATED — the gate, the workflow job that runs it, and on which events;
      · DECLARED UNRUN — on disk, run by nothing, and declared as debt;
      · UNACCOUNTED — on disk, run by nothing, and NOT declared. The pipefail lock fails on these;
        the index names them so the two never disagree about what is checked.
    """
    idx = gate_index(workflows)
    triggers = {wf: workflow_triggers(text) for wf, text in workflows.items()}
    unrun_set, disk_set = set(unrun), set(on_disk)
    run_harnesses = {g for g in idx if g.endswith(".py")}

    lines = [HEADER, "", "# CI gates — what this repository checks, and where", "",
             "**One generated answer to “is this gated, and on what?”** Derived from",
             "`.github/workflows/*.yml` by `backend/harness_ci_gate_index.py`, which fails the build",
             "when this file is stale. Never hand-edit it: a hand-written list of what CI checks is a",
             "second copy of a fact the workflows already hold, and it drifts.",
             "",
             "Why it exists: on 2026-10-28 a Claude session reported to the owner that this repo had",
             "“no frontend CI at all”. `npm run typecheck` and `npm run lint:gate` had been running on",
             "every frontend change for weeks — inside `carrier-vocab-guard.yml`, whose name says",
             "neither and which holds 49 jobs. Workflow filenames are not a map. This is. (§CORE-34)",
             ""]

    lines += ["## Workflows, and when they run", "",
              "| Workflow | Jobs | Runs on |", "|---|---|---|"]
    for wf in sorted(workflows):
        njobs = len(jobs_of_workflow(workflows[wf]))
        ev = ", ".join(f"`{e}`" for e in triggers[wf]) or "—"
        lines.append(f"| `{wf}` | {njobs} | {ev} |")
    lines.append("")

    lines += [f"## Gated — {len(idx)} gates", "",
              "A gate whose workflow does not run on `pull_request` is **not** gating your PR, and",
              "the Runs on column is how you can tell.", "",
              "| Gate | Workflow | Job | Runs on |", "|---|---|---|---|"]
    for gate in sorted(idx):
        for wf, key, name in idx[gate]:
            ev = ", ".join(f"`{e}`" for e in triggers.get(wf, [])) or "—"
            lines.append(f"| `{gate}` | `{wf}` | {name} (`{key}`) | {ev} |")
    lines.append("")

    declared = sorted(unrun_set & disk_set)
    lines += [f"## Declared unrun — {len(declared)} harnesses no workflow runs", "",
              "Declared debt (`backend/harness_unrun_pending.txt`), which may only SHRINK. These",
              "files assert things, and nothing executes them, so they assert nothing today.", ""]
    lines += [f"- `{h}`" for h in declared] or ["_none_"]
    lines.append("")

    unaccounted = sorted(disk_set - run_harnesses - unrun_set)
    lines += [f"## Unaccounted — {len(unaccounted)}", "",
              "On disk, run by no workflow, and not declared as debt.",
              "`harness_ci_pipefail_lock.py` fails the build on these; they are listed here so the",
              "lock and this index can never disagree about what is checked.", ""]
    lines += [f"- `{h}`" for h in unaccounted] or ["_none_"]
    lines.append("")
    return "\n".join(lines)


def read_workflows(root: str) -> Dict[str, str]:
    """`{filename: text}` for every workflow in `<root>/.github/workflows`."""
    d = os.path.join(root, ".github", "workflows")
    out = {}
    for name in sorted(os.listdir(d)):
        if name.endswith((".yml", ".yaml")):
            with open(os.path.join(d, name), "r", encoding="utf-8") as fh:
                out[name] = fh.read()
    return out
