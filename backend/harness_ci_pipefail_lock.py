"""LOCK — a harness CI runs actually runs, and when it fails CI fails.

THE DEFECT (found 2026-09-24). GitHub Actions runs a step with no `shell:` as `bash -e {0}` — WITHOUT pipefail. Every
gate here was written `python3 harness_x.py | tee -a "$GITHUB_STEP_SUMMARY"`, so the step's status was tee's, and a
harness that FAILED still passed CI. Every lock in carrier-vocab-guard / lineage-guard / org-scope-guard was silently
unenforced (a sweep of all 25 CI harnesses that day found them green, so nothing had slipped through — by luck).
An explicit `shell: bash` runs `bash --noprofile --norc -eo pipefail {0}`.

THE RULE: every workflow that runs a harness (`harness_*.py`) declares the top-level default

    defaults:
      run:
        shell: bash

THE SECOND DEFECT, SAME CLASS (found 2026-09-27). `harness_closing_filter_contract.py` landed in the
stdlib-only `carrier-vocab-guard` job, which installs nothing. It imports the real `closing.router`
to prove the three filter resolvers BEHAVIOURALLY, and that module imports fastapi — so the step died
with `ModuleNotFoundError: No module named 'fastapi'` before a single check ran. It passed on every
developer machine, because a developer machine has the backend installed. Same class as the pipefail
bug: **a gate that cannot run is not a gate**, whether it silently passes or loudly dies.

THE SECOND RULE: a harness whose MODULE-LEVEL imports reach something from `backend/requirements.txt`
is run only in a job that `pip install`s. The set of things that need a wheel is not restated here —
it is READ from requirements.txt, so a dependency added tomorrow is covered without touching this
file. Reachability is followed through first-party `app.*` modules, because that is how the defect
arrived: the harness imported `app`, and `app` imported fastapi. Two things are deliberately NOT
violations, since neither can break module load: an import guarded by `try:`, and a third-party
module the harness STUBS into `sys.modules` itself (`harness_tenant_vertical.py` does exactly that,
on purpose, and is correctly placed in the no-deps job).

THE THIRD DEFECT, SAME CLASS (found 2026-09-27, owner-directed fix). `harness_activation_bucketing.py`
had been RED on `main` for three weeks — PR #279 refactored `_activation_details_rules` to stop issuing
its own org-scoped query (the §4b.1 "ONE READ" duplicate), the guard still grepped that function's own
source for `.eq("org_id", org_id)`, and nothing noticed because **no workflow runs that harness**. The
guard was stale AND unexecuted; only the second fact let it rot silently.

Measured across the repo: **400 harnesses on disk, 65 run by a workflow, 335 run by nothing.** So the
third rule is a RATCHET, not a hard gate — wiring 335 harnesses into CI in one change would multiply
the build, and that is the owner's call, not a lock's. `backend/harness_unrun_pending.txt` is the debt
list: a NEW harness must be run by some workflow, the list may only SHRINK, and PINNED_MAX below must
equal its length. Same shape as `frontend/table_sort_pending.txt`, which the owner approved for the
sort rollout.

Stdlib only (the job that runs this installs nothing): run `python backend/harness_ci_pipefail_lock.py`.
"""
import ast
import io
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
WORKFLOWS = os.path.join(HERE, "..", ".github", "workflows")
RUNS_HARNESS = re.compile(r"^\s*(?:-\s*)?run:.*\bharness_\w+\.py", re.M)
DEFAULT_BASH = re.compile(r"^defaults:\s*\n\s+run:\s*\n\s+shell:\s*bash\s*$", re.M)


def violations(files):
    """files: {name: yaml text} → [plain sentences]; [] = the lock holds."""
    out = []
    for name in sorted(files):
        text = files[name]
        if not RUNS_HARNESS.search(text):
            continue
        if not DEFAULT_BASH.search(text):
            out.append(f"{name}: runs a harness but does not declare `defaults: run: shell: bash` — without pipefail "
                       "`python3 harness_x.py | tee …` passes even when the harness fails")
    return out


# ── THE SECOND RULE — a harness that needs a wheel runs in a job that installs wheels ─────────────
# The import name usually IS the distribution name; these are the ones where it is not.
IMPORT_NAME = {
    "beautifulsoup4": "bs4", "python-dotenv": "dotenv", "python-dateutil": "dateutil",
    "python-multipart": "multipart", "pyjwt": "jwt", "pillow": "PIL", "python-jose": "jose",
    "pyyaml": "yaml", "google-api-python-client": "googleapiclient", "google-auth": "google",
    "opencv-python": "cv2", "attrs": "attr",
}
# Transitive dependencies nothing declares directly but every import of fastapi/supabase drags in.
# Listed because a harness can import THEM without requirements.txt ever naming them.
TRANSITIVE = ("pydantic", "starlette", "postgrest", "gotrue", "storage3", "realtime", "supafunc",
              "numpy", "anyio", "h11", "certifi", "urllib3", "charset_normalizer", "soupsieve")
def _stubbed_modules(src):
    """Every module name a harness INSTALLS INTO `sys.modules` itself, by any spelling.

    Was a regex for `sys.modules["x"] = `, which missed the two forms actually in use:
    `sys.modules.setdefault(name, ...)`, and a `for name in ("app.core", "app.core.database"):` loop
    whose body does the setdefault with a VARIABLE. `harness_tenant_vertical.py` uses exactly that, so
    the regex read it as un-stubbed the moment the import walk below grew deep enough to reach the DB
    client — a false positive on a correct harness.

    So: a file that never mentions `sys.modules` stubs nothing and costs one substring test. A file
    that DOES contributes every dotted-looking string literal in it. Deliberately generous, and that is
    the safe direction — over-reading a stub lets a real misplacement through, which the job's own CI
    run then catches loudly, while under-reading one reports a working harness as broken, and that is
    the failure that gets a lock switched off. Single parse, no per-node source slicing: the segment
    form of this made the lock take minutes across 400+ harnesses.
    """
    if not src or "sys.modules" not in src:
        return set()
    try:
        tree = ast.parse(src)
    except SyntaxError:
        return set()
    out = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            v = node.value.strip()
            if v and " " not in v and re.match(r"^[A-Za-z_][A-Za-z0-9_.]*$", v):
                out.add(v)
    return out


JOB_KEY = re.compile(r"^  ([A-Za-z0-9_-]+):\s*$", re.M)


def wheel_names(requirements_text):
    """requirements.txt → the set of IMPORT names that only exist after a pip install."""
    out = set(TRANSITIVE)
    for raw in (requirements_text or "").splitlines():
        line = raw.split("#")[0].strip()
        if not line or line.startswith("-"):
            continue
        dist = re.split(r"[<>=!\[;]", line)[0].strip().lower()
        if dist:
            out.add(IMPORT_NAME.get(dist, dist.replace("-", "_")))
    return out


def _module_level_imports(src):
    """The imports that run when the module is LOADED — the only ones that can raise ImportError
    before check one. Function bodies are lazy; `try:` blocks are guarded; both are skipped."""
    try:
        tree = ast.parse(src)
    except SyntaxError:
        return []
    found = []

    def take(node):
        if isinstance(node, ast.Import):
            found.extend(a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            # BOTH the package AND each name under it. `from app.core import import_batches` carries
            # its real target in `names`, not in `module`: yielding only `app.core` dead-ends the walk
            # on an EMPTY `app/core/__init__.py` and never reaches `import_batches.py`, so the wheel it
            # pulls in (supabase, via app.core.database) was invisible. That hole let
            # harness_sweep_failures_visible.py into the stdlib-only job on 2026-10-01 — this lock said
            # green and CI said ModuleNotFoundError, which is the one thing a lock must never do.
            # A name that is a plain symbol rather than a submodule simply resolves to no file and is
            # skipped by `_first_party`, so adding them costs nothing and misses nothing.
            found.append(node.module)
            found.extend("%s.%s" % (node.module, a.name) for a in node.names)

    def walk(body):
        for node in body:
            take(node)
            if isinstance(node, (ast.If, ast.With)):          # `if`/`with` still execute on load
                walk(node.body)
                walk(getattr(node, "orelse", []) or [])
            # ast.Try, ast.FunctionDef, ast.ClassDef: deliberately not descended into

    walk(tree.body)
    return found


def _first_party(module, read_source):
    """An `app.…` module → its source, or None when it is not a file in this backend."""
    rel = os.path.join(*module.split("."))
    for candidate in (rel + ".py", os.path.join(rel, "__init__.py")):
        src = read_source(candidate)
        if src is not None:
            return candidate, src
    return None


def _lazy_imports(src):
    """The imports inside function bodies. These cannot break module LOAD, which is why
    `_module_level_imports` skips them — but a harness that imports the app inside a helper dies on
    the first check that calls it, which is the same defect arriving later.

    FOUND 2026-10-04, the fourth of this class: `harness_order_transport.py` drives the real
    `_push_accessory_pos`, and did its `import app.modules.storevisit.router` inside that test
    helper. This lock said green; CI said ModuleNotFoundError on line one of §E. A `try:` guard
    still excuses it, as it does above.
    """
    try:
        tree = ast.parse(src)
    except SyntaxError:
        return []
    guarded = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Try):
            for n in ast.walk(node):
                guarded.add(id(n))
    found = []
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for n in ast.walk(node):
            if id(n) in guarded:
                continue
            if isinstance(n, ast.Import):
                found.extend(a.name for a in n.names)
            elif isinstance(n, ast.ImportFrom) and n.level == 0 and n.module:
                found.append(n.module)
                found.extend("%s.%s" % (n.module, a.name) for a in n.names)
    return found


def unrunnable(harness_name, read_source, wheels):
    """→ "<file> imports <module>" for the first module-level chain that needs a wheel, else None.
    Follows `app.*` imports, because the defect arrived transitively: harness → app → fastapi."""
    head_src = read_source(harness_name)
    if head_src is None:
        return None
    stubs = _stubbed_modules(head_src)
    # The HEAD harness contributes its lazy imports as well as its module-level ones; everything
    # reached from there is followed by module load, as before.
    seen, queue = set(), [(harness_name, head_src, True)]
    while queue:
        where, src, is_head = queue.pop(0)
        mods = _module_level_imports(src)
        if is_head:
            mods = mods + _lazy_imports(src)
        for module in mods:
            head = module.split(".")[0]
            if head in wheels or module in wheels:
                if head in stubs or module in stubs:
                    continue                                   # the harness supplies its own stub
                return "%s imports %s" % (where, module)
            if head == "app" and module in stubs:
                # The harness replaced this first-party module in `sys.modules`, so the REAL file is
                # never imported and whatever it pulls in is never needed. Descending into it anyway is
                # what made harness_tenant_vertical read as broken: it stubs `app.core.database`
                # precisely so the DB client (and supabase) stays out of a stdlib-only run.
                continue
            if head == "app" and module not in seen:
                seen.add(module)
                nxt = _first_party(module, read_source)
                if nxt:
                    queue.append((nxt[0], nxt[1], False))
    return None


def misplaced(files, read_source, wheels):
    """files: {workflow: yaml} → [plain sentences]; [] = every harness can actually import."""
    out = []
    for name in sorted(files):
        text = files[name]
        if "jobs:" not in text or "harness_" not in text:
            continue
        marks = [(m.start(), m.group(1)) for m in JOB_KEY.finditer(text)
                 if m.start() > text.index("jobs:")]
        for i, (pos, job) in enumerate(marks):
            body = text[pos: marks[i + 1][0] if i + 1 < len(marks) else len(text)]
            if "pip install" in body:
                continue
            for m in re.finditer(r"\b(harness_\w+\.py)", body):
                why = unrunnable(m.group(1), read_source, wheels)
                if why:
                    out.append("%s job `%s` runs %s but installs nothing — %s, so the step dies with "
                               "ModuleNotFoundError before check one. Move it to a job that runs "
                               "`pip install -r backend/requirements.txt`."
                               % (name, job, m.group(1), why))
    return out


# ── THE THIRD RULE — a harness no workflow runs is a file, not a gate ─────────────────────────────
PENDING_FILE = os.path.join(HERE, "harness_unrun_pending.txt")
PINNED_MAX = 325          # must equal the number of entries in harness_unrun_pending.txt


def read_pending(text):
    """The debt file's text → the set of harness filenames on it. `#` lines are the rationale."""
    return {ln.strip() for ln in (text or "").splitlines()
            if ln.strip() and not ln.lstrip().startswith("#")}


def harnesses_run_by(files):
    """{workflow: yaml} → every harness_*.py filename any workflow mentions."""
    out = set()
    for text in files.values():
        out |= set(re.findall(r"\b(harness_\w+\.py)", text))
    return out


def unrun(on_disk, files, pending):
    """→ [plain sentences]; [] = nobody added an unexecuted harness and the debt did not grow."""
    out = []
    ran = harnesses_run_by(files)
    orphans = sorted(set(on_disk) - ran - pending)
    for name in orphans:
        out.append("backend/%s is run by no workflow — a harness nothing executes is not a gate, it is "
                   "a file, and it will rot unnoticed (that is exactly how harness_activation_bucketing "
                   "sat red on main for three weeks). Add a step for it, or, if that is genuinely for "
                   "later, add it to backend/harness_unrun_pending.txt and raise PINNED_MAX." % name)
    if len(pending) > PINNED_MAX:
        out.append("harness_unrun_pending.txt has %d entries but PINNED_MAX is %d — the debt list may "
                   "only SHRINK. Wire the harness up instead of registering it."
                   % (len(pending), PINNED_MAX))
    # A name on the list that IS now run, or no longer exists, is stale: it must be deleted so the
    # count keeps meaning something.
    stale = sorted((pending & ran) | (pending - set(on_disk)))
    for name in stale:
        out.append("%s is on harness_unrun_pending.txt but is now run by a workflow (or no longer "
                   "exists) — delete the line and lower PINNED_MAX; a debt list that does not shrink "
                   "when the debt is paid stops measuring anything." % name)
    return out


def _reader(root):
    def read(rel):
        path = os.path.join(root, rel)
        if not os.path.isfile(path):
            return None
        return io.open(path, encoding="utf-8", errors="replace").read()
    return read


def _load():
    return {f: open(os.path.join(WORKFLOWS, f), encoding="utf-8").read()
            for f in os.listdir(WORKFLOWS) if f.endswith((".yml", ".yaml"))}


def main():
    passed = failed = 0

    def check(label, ok):
        nonlocal passed, failed
        print(("  PASS  " if ok else "  FAIL  ") + label)
        passed += ok
        failed += (not ok)

    files = _load()
    real = violations(files)
    for v in real:
        print("  ✗ " + v)
    check("every workflow that runs a harness runs it under pipefail", real == [])
    check("the lock sees at least one harness-running workflow", any(RUNS_HARNESS.search(t) for t in files.values()))

    # negative controls — each must go RED
    gate = "jobs:\n  g:\n    steps:\n      - run: python3 harness_x.py | tee -a out\n"
    check("a harness workflow with no default shell → RED", bool(violations({"x.yml": gate})))
    check("a default shell of sh → RED",
          bool(violations({"x.yml": "defaults:\n  run:\n    shell: sh\n\n" + gate})))
    for name, text in files.items():
        if RUNS_HARNESS.search(text):
            stripped = DEFAULT_BASH.sub("", text)
            check(f"{name} with its default shell removed → RED", bool(violations({name: stripped})))
    # and stays green where it should
    check("the declared default → green", violations({"x.yml": "defaults:\n  run:\n    shell: bash\n\n" + gate}) == [])
    check("a workflow that runs no harness needs nothing → green",
          violations({"d.yml": "jobs:\n  d:\n    steps:\n      - run: echo hi | tee x\n"}) == [])

    # ── THE SECOND RULE ───────────────────────────────────────────────────────────────────────────
    read = _reader(HERE)
    wheels = wheel_names(read("requirements.txt") or "")
    check("the wheel set is READ from requirements.txt, not restated here",
          {"fastapi", "supabase", "pandas", "bs4", "dateutil"} <= wheels)
    bad = misplaced(files, read, wheels)
    for v in bad:
        print("  ✗ " + v)
    check("every harness CI runs can actually import in the job that runs it", bad == [])
    check("the second rule sees the jobs (it found harness steps to judge)",
          any("harness_" in t and "jobs:" in t for t in files.values()))

    # the regression this rule exists for: that harness DOES need the wheels, and the workflow
    # therefore must keep running it in the job that installs them.
    check("harness_closing_filter_contract is correctly identified as needing the backend installed",
          unrunnable("harness_closing_filter_contract.py", read, wheels) is not None)
    check("it is the fastapi chain through the closing router that is named",
          "fastapi" in (unrunnable("app/modules/closing/router.py", read, wheels) or ""))
    check("harness_tenant_vertical, which stubs fastapi itself, is NOT flagged",
          unrunnable("harness_tenant_vertical.py", read, wheels) is None)
    check("this lock itself needs nothing installed",
          unrunnable("harness_ci_pipefail_lock.py", read, wheels) is None)

    # negative controls, on synthetic sources — the RULE is under test, not today's tree
    def fake(sources):
        return lambda rel: sources.get(rel)
    nodeps = "jobs:\n  g:\n    steps:\n      - run: python3 harness_z.py | tee -a out\n"
    withdeps = ("jobs:\n  g:\n    steps:\n      - run: pip install -r backend/requirements.txt\n"
                "      - run: python3 harness_z.py | tee -a out\n")
    W = {"fastapi", "pandas"}
    check("a plain third-party import in a no-deps job → RED",
          bool(misplaced({"x.yml": nodeps}, fake({"harness_z.py": "import fastapi\n"}), W)))
    check("the SAME harness in a job that pip installs → green",
          misplaced({"x.yml": withdeps}, fake({"harness_z.py": "import fastapi\n"}), W) == [])
    check("a chain THROUGH app.* → RED  ← how the real defect arrived",
          bool(misplaced({"x.yml": nodeps}, fake({
              "harness_z.py": "from app.modules.closing.router import r\n",
              "app/modules/closing/router.py": "from fastapi import APIRouter\n"}), W)))
    check("a try-guarded import cannot break the load → green",
          misplaced({"x.yml": nodeps}, fake({
              "harness_z.py": "try:\n    import pandas\nexcept ImportError:\n    pandas = None\n"}), W) == [])
    # RE-POINTED 2026-10-04, the ruling NARROWED rather than the lock loosened. "Lazy" was taken to
    # mean "harmless", because a function-body import cannot break module load. It can still break
    # the harness: harness_order_transport.py imported the app inside a test helper and died on §E's
    # first check while this lock read green. So a lazy import in the HARNESS is now a violation; a
    # try-guarded one is still excused, and so is one inside an app module the harness reaches, which
    # really is only run if that code path runs.
    check("a lazy import in the HARNESS still needs the wheel → RED",
          bool(misplaced({"x.yml": nodeps}, fake({
              "harness_z.py": "def f():\n    import pandas\n    return pandas\n"}), W)))
    check("and so does one reaching the app transitively from a helper → RED",
          bool(misplaced({"x.yml": nodeps}, fake({
              "harness_z.py": "def f():\n    import app.modules.closing.router as r\n    return r\n",
              "app/modules/closing/router.py": "from fastapi import APIRouter\n"}), W)))
    check("a try-guarded lazy import is still excused → green",
          misplaced({"x.yml": nodeps}, fake({
              "harness_z.py": "def f():\n    try:\n        import pandas\n    except ImportError:\n        pandas = None\n"}), W) == [])
    check("a lazy import INSIDE a reached app module is still lazy → green",
          misplaced({"x.yml": nodeps}, fake({
              "harness_z.py": "import app.modules.closing.helper\n",
              "app/modules/closing/helper.py": "def f():\n    import pandas\n    return pandas\n"}), W) == [])
    check("a stub the harness installs itself → green",
          misplaced({"x.yml": nodeps}, fake({
              "harness_z.py": 'import sys, types\nsys.modules["fastapi"] = types.ModuleType("fastapi")\n'
                              "from app.x import y\n",
              "app/x.py": "from fastapi import APIRouter\n"}), W) == [])
    check("a stub does NOT excuse a DIFFERENT wheel → RED",
          bool(misplaced({"x.yml": nodeps}, fake({
              "harness_z.py": 'import sys, types\nsys.modules["fastapi"] = types.ModuleType("fastapi")\n'
                              "import pandas\n"}), W)))
    check("an import under `if` still runs on load → RED",
          bool(misplaced({"x.yml": nodeps}, fake({
              "harness_z.py": "import os\nif os.environ.get('X'):\n    import pandas\n"}), W)))
    check("`from pkg import submodule` is followed to the SUBMODULE  <- the hole this lock had",
          bool(misplaced({"x.yml": nodeps}, fake({
              "harness_z.py": "from app.core import import_batches\n",
              "app/core/__init__.py": "",
              "app/core/import_batches.py": "from app.core.database import get_supabase\n",
              "app/core/database.py": "from pandas import DataFrame\n"}), W)))
    check("...and the real harness that exposed it is now correctly identified",
          unrunnable("harness_sweep_failures_visible.py", read, wheels) is not None)
    check("a package __init__ is followed too",
          bool(misplaced({"x.yml": nodeps}, fake({
              "harness_z.py": "from app.pkg import thing\n",
              "app/pkg/__init__.py": "import pandas\n"}), W)))
    check("a harness the workflow names but the repo does not have is not a violation → green",
          misplaced({"x.yml": nodeps}, fake({}), W) == [])
    check("stdlib only → green",
          misplaced({"x.yml": nodeps}, fake({"harness_z.py": "import os, re, sys, json\n"}), W) == [])

    # ── THE THIRD RULE ────────────────────────────────────────────────────────────────────────────
    on_disk = sorted(f for f in os.listdir(HERE)
                     if f.startswith("harness_") and f.endswith(".py"))
    pending = read_pending(read("harness_unrun_pending.txt") or "")
    if len(pending) != PINNED_MAX:
        print("  ✗ harness_unrun_pending.txt has %d entries, PINNED_MAX is %d"
              % (len(pending), PINNED_MAX))
    check("the debt list parsed and is pinned exactly", len(pending) == PINNED_MAX)
    orphaned = unrun(on_disk, files, pending)
    for v in orphaned:
        print("  ✗ " + v)
    ran_now = harnesses_run_by(files)
    check("no harness is unexecuted and unregistered (the list may only shrink)", orphaned == [])
    check("the harness fixed in this change is now RUN, not merely registered",
          "harness_activation_bucketing.py" in ran_now
          and "harness_activation_bucketing.py" not in pending)
    print("  ·  %d harnesses on disk, %d run by a workflow, %d registered as debt"
          % (len(on_disk), len(on_disk) - len(pending), len(pending)))

    # negative controls
    wf = {"x.yml": "jobs:\n  g:\n    steps:\n      - run: python3 harness_a.py\n"}
    check("a NEW harness nobody runs → RED", bool(unrun(["harness_a.py", "harness_new.py"], wf, set())))
    check("the same harness once registered as debt → green",
          unrun(["harness_a.py", "harness_new.py"], wf, {"harness_new.py"}) == [])
    check("a registered harness that IS now run must be de-registered → RED",
          bool(unrun(["harness_a.py"], wf, {"harness_a.py"})))
    check("a registered harness that no longer exists must be de-registered → RED",
          bool(unrun(["harness_a.py"], wf, {"harness_gone.py"})))
    check("a harness run by ANY workflow, not just this one, counts → green",
          unrun(["harness_a.py", "harness_b.py"],
                dict(wf, **{"y.yml": "  - run: python3 harness_b.py\n"}), set()) == [])
    check("comment lines in the debt file are not entries",
          read_pending("# why this file exists\nharness_a.py\n") == {"harness_a.py"})

    print(f"\n{passed} passed, {failed} failed")
    if failed:
        sys.exit(1)
    print("OK — a harness runs, can import where it runs, and fails the build when it fails.")


if __name__ == "__main__":
    main()
