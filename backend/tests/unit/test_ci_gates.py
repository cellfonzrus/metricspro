"""Unit tests for ci_gates — the ONE home for "which workflow job runs this gate".

WHY THIS EXISTS. On 2026-10-28 I told the owner this repo had "no frontend CI at all". It was
wrong: `npm run typecheck` and `npm run lint:gate` run on every frontend change — inside
`carrier-vocab-guard.yml`, a workflow whose name mentions neither, and which holds 49 jobs.

THE CLASS: **whether a thing is gated is a fact about the workflows, and it was only answerable by
reading all fifteen of them.** A fact that expensive to look up gets guessed instead, and a guess
about whether something is checked is worse than no answer: I guessed, in writing, to the owner.
So the fact gets one home, DERIVED from the workflows (never hand-written), and a check that fails
the build when the home goes stale.

These tests are the parsing rules. Written RED first.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from ci_gates import (  # noqa: E402
    jobs_of_workflow, gates_in_step, gate_index, workflow_triggers, render_index,
)


WF = """\
name: example-guard

on:
  pull_request:
  push:
    branches: [main]

defaults:
  run:
    shell: bash

jobs:
  first-thing:
    name: A human-readable job title
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v5
      - name: harness_alpha
        run: python backend/harness_alpha.py
  second-thing:
    # no `name:` — the job key is the only label it has
    runs-on: ubuntu-latest
    steps:
      - name: two gates in one job
        run: |
          python backend/harness_beta.py
          python3 backend/harness_gamma.py | tee "$GITHUB_STEP_SUMMARY"
  frontend-thing:
    name: Frontend type check
    steps:
      - run: npm run typecheck
      - run: npm run lint:gate
"""


def test_jobs_of_workflow_finds_every_job_with_its_display_name():
    jobs = jobs_of_workflow(WF)
    assert [j["key"] for j in jobs] == ["first-thing", "second-thing", "frontend-thing"]
    assert jobs[0]["name"] == "A human-readable job title"
    assert jobs[2]["name"] == "Frontend type check"


def test_a_job_with_no_name_falls_back_to_its_key_rather_than_being_nameless():
    jobs = jobs_of_workflow(WF)
    assert jobs[1]["name"] == "second-thing"


def test_a_comment_is_not_read_as_a_name():
    # The second job's only `name:`-looking line is inside a comment.
    assert jobs_of_workflow(WF)[1]["name"] == "second-thing"


def test_gates_in_step_reads_a_harness_however_python_is_spelled():
    assert gates_in_step("python backend/harness_alpha.py") == ["harness_alpha.py"]
    assert gates_in_step("python3 harness_beta.py") == ["harness_beta.py"]
    assert gates_in_step("cd backend && python -m harness_gamma") == ["harness_gamma.py"]


def test_gates_in_step_reads_several_gates_from_one_multiline_run():
    got = gates_in_step("python backend/harness_beta.py\npython3 backend/harness_gamma.py | tee x")
    assert got == ["harness_beta.py", "harness_gamma.py"]


def test_gates_in_step_reads_an_npm_script_as_a_gate_too():
    # The frontend's gates are npm scripts, not harnesses. They are gates all the same, and the
    # reason this index exists is that I could not see them.
    assert gates_in_step("npm run typecheck") == ["npm:typecheck"]
    assert gates_in_step("npm run lint:gate") == ["npm:lint:gate"]
    assert gates_in_step("npm run test -- --coverage") == ["npm:test"]


def test_gates_in_step_ignores_a_step_that_runs_no_gate():
    assert gates_in_step("pip install -r requirements.txt") == []
    assert gates_in_step("echo harness_alpha.py is nice") == []


def test_a_bare_pytest_invocation_is_a_gate():
    assert gates_in_step("python -m pytest tests -m unit") == ["pytest:unit"]
    assert gates_in_step("python -m pytest tests -m integration") == ["pytest:integration"]


def test_gate_index_maps_each_gate_to_every_job_that_runs_it():
    idx = gate_index({"example-guard.yml": WF})
    assert idx["harness_alpha.py"] == [("example-guard.yml", "first-thing", "A human-readable job title")]
    assert idx["harness_gamma.py"][0][1] == "second-thing"
    assert idx["npm:typecheck"][0][2] == "Frontend type check"


def test_gate_index_reports_a_gate_run_by_TWO_workflows():
    second = WF.replace("name: example-guard", "name: other-guard")
    idx = gate_index({"a.yml": WF, "b.yml": second})
    assert len(idx["harness_alpha.py"]) == 2
    assert {w for w, _k, _n in idx["harness_alpha.py"]} == {"a.yml", "b.yml"}


def test_workflow_triggers_says_when_a_workflow_actually_runs():
    # A gate that exists but only runs on a schedule is not gating a pull request. The index must
    # say so, or it answers "is this checked?" with a yes that does not hold for a PR.
    assert "pull_request" in workflow_triggers(WF)
    assert "push" in workflow_triggers(WF)
    sched = "name: x\non:\n  schedule:\n    - cron: \"0 6 * * 1\"\njobs:\n  a:\n    steps: []\n"
    assert workflow_triggers(sched) == ["schedule"]
    assert "pull_request" not in workflow_triggers(sched)


def test_render_index_is_deterministic_so_a_regenerate_is_a_no_op():
    a = render_index({"example-guard.yml": WF}, unrun=[], on_disk=["harness_alpha.py"])
    b = render_index({"example-guard.yml": WF}, unrun=[], on_disk=["harness_alpha.py"])
    assert a == b
    assert a.startswith("<!-- GENERATED")


def test_render_index_names_a_harness_that_no_workflow_runs():
    out = render_index({"example-guard.yml": WF}, unrun=["harness_lonely.py"],
                       on_disk=["harness_alpha.py", "harness_lonely.py"])
    assert "harness_lonely.py" in out
    assert "harness_alpha.py" in out


def test_render_index_flags_a_harness_on_disk_that_is_neither_run_nor_declared_unrun():
    # The gap the pipefail lock already fails on, surfaced in the index too so the two agree.
    out = render_index({"example-guard.yml": WF}, unrun=[],
                       on_disk=["harness_alpha.py", "harness_undeclared.py"])
    assert "harness_undeclared.py" in out
