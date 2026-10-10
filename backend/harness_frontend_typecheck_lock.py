"""LOCK — a frontend TYPE error fails a check, and the gate cannot be un-wired or widened.

THE DEFECT (owner directive 2026-10-10, verbatim: *"fix the frontend type error"*). A `SCREENS` entry
whose key sat outside the closed `ScreenKey` union in `components/ScreenLink.tsx` made `tsc` red. All
**49** GitHub checks were green on every head of PR #432 while the Vercel deploy was RED on every one
of them, and the error was finally found BY HAND with `npx tsc --noEmit`. Measured the same day:
nothing in `.github/workflows/` ran `tsc`, `typecheck` or `type-check` as a gate — the single grep hit
in the repo was a COMMENT describing a proof that transpiles one display module.

THE CLASS, NOT THE INSTANCE. The instance is one bad union member, and it is already fixed and merged.
The class is: **the frontend's type checker was not a gate at all**, so the only thing standing between
a type error and `main` was whether a human happened to run the compiler. Fixing the union and stopping
there would have left the next type error exactly as invisible. Vercel knew — and Vercel is not a
required check, so its red told nobody. The index's §19.18 class once more: a mechanism that exists
(TypeScript, `strict: true`) with no caller wired to it.

MEASURED ON `origin/main` (1c73ba09) BEFORE DESIGNING THIS: the frontend type-checks **CLEAN** — 0
errors over all 547 `.ts`/`.tsx` files under `frontend/src` (1309 files in the program), both with and
without Next's generated route types, and `@ts-nocheck` / `@ts-ignore` / `@ts-expect-error` appear
**0** times anywhere under `frontend/`. So this ships as a PLAIN GATE that must stay clean, and
deliberately NOT as a pinned debt list in the shape of `frontend/table_sort_pending.txt` /
`backend/harness_unrun_pending.txt`: there is no debt to register, and a ratchet file holding zero
entries is a decoration that teaches people the gate is negotiable.

WHAT THIS LOCK FAILS THE BUILD FOR — the ways the gate could quietly stop being one:
  (a) NO ONE HOME FOR THE COMMAND. `frontend/package.json` must declare a `typecheck` script that
      runs `tsc --noEmit`. One spelling, for CI and for a developer alike.
  (b) THE GATE IS NOT RUN. Some workflow must run `npm run typecheck`, in a job that installs the
      frontend's dependencies (`npm ci`) — `tsc` cannot run without `node_modules`, and the
      §19.25 lesson is that a gate which dies on its first line is not a gate.
  (c) A SECOND COPY OF THE COMMAND. A workflow that spells `tsc --noEmit` itself instead of
      dereferencing the package.json script is the "one fact, two homes" defect: the two spellings
      drift and the gate then checks something other than what a developer runs.
  (d) THE GATE SKIPS SILENTLY. The workflow's `pull_request` AND `push` path filters must cover
      `frontend/**`. `frontend/src/**` alone does NOT match `tsconfig.json`, `package.json`,
      `package-lock.json`, `next.config.ts` or `site-routing.ts` — every one of which can introduce
      or hide a type error. A gate that does not run on the file that broke it is worse than none,
      because it reports green.
  (e) STRICTNESS RELAXED TO REACH GREEN. `frontend/tsconfig.json` must keep `strict: true` and
      `noEmit: true`, must not exclude anything under `src`, and must still `include` the `.ts`/`.tsx`
      globs. Turning `strict` off is how a red tree becomes a green one without fixing anything.
  (f) THE BUILD TOLD TO IGNORE TYPES. `next.config.ts` must not set `typescript.ignoreBuildErrors`
      — that is the single switch that makes Vercel stop reporting the very error that started this.
  (g) A BLANKET SUPPRESSION. `@ts-nocheck` and `@ts-ignore` are banned outright under `frontend/`
      (both are at 0 today). Neither ever expires: a `@ts-ignore` keeps hiding whatever error the
      line grows into, forever, and `@ts-nocheck` switches off a whole file.
      `@ts-expect-error` is deliberately NOT banned — it FAILS the build once the error it names is
      gone, so it cannot rot, and banning the honest form pushes people to the dishonest one.
  (h) NEGATIVE CONTROLS — every rule above must be shown going RED over a synthetic input, so a rule
      that has stopped matching anything cannot sit here reading green.

Stdlib only, DB-free (it runs in the dependency-free `carrier-vocab-guard` job, and there is no
`pyyaml` in backend/requirements.txt — the workflow is read as text, the same way
`harness_ci_pipefail_lock.py` reads it).

  python3 backend/harness_frontend_typecheck_lock.py
"""
import io
import json
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FE = os.path.join(ROOT, "frontend")
WORKFLOWS = os.path.join(ROOT, ".github", "workflows")

SCRIPT_NAME = "typecheck"
TSC_NOEMIT = re.compile(r"\btsc\b[^\n|&]*--noEmit")
RUN_TYPECHECK = re.compile(r"npm\s+run\s+" + SCRIPT_NAME + r"\b")
# A JOB key sits at exactly two spaces of indent; a step line does not.
JOB_KEY = re.compile(r"^  [A-Za-z0-9_-]+:\s*$", re.M)

P = F = 0


def check(label, ok, detail=""):
    global P, F
    if ok:
        P += 1
        print("  PASS  %s" % label)
    else:
        F += 1
        print("  FAIL  %s   %s" % (label, str(detail)[:600]))


def read(path):
    if not os.path.isfile(path):
        return None
    return io.open(path, encoding="utf-8", errors="replace").read()


def strip_jsonc(text):
    """tsconfig.json may carry // comments. Drop them before json.loads (crudely, but it only has to
    survive this repo's tsconfig, and a parse failure is itself reported)."""
    return re.sub(r"^\s*//.*$", "", text or "", flags=re.M)


# ── the rules, each a pure function so the negative controls can drive it ──────────────────────────
def one_home_violations(package_json_text):
    """(a)+(c) half: the command has ONE home, and it is the real type checker."""
    out = []
    try:
        scripts = (json.loads(package_json_text or "{}").get("scripts") or {})
    except ValueError as exc:
        return ["frontend/package.json does not parse (%s)" % exc]
    cmd = scripts.get(SCRIPT_NAME)
    if not cmd:
        out.append("frontend/package.json declares no `%s` script — the type check has no ONE home, "
                   "so CI and a developer would each spell the compiler invocation themselves and "
                   "drift. Add \"%s\": \"next typegen && tsc --noEmit\"." % (SCRIPT_NAME, SCRIPT_NAME))
    elif not TSC_NOEMIT.search(cmd):
        out.append("frontend/package.json's `%s` script is %r, which does not run `tsc --noEmit` — "
                   "the gate would pass without type-checking anything." % (SCRIPT_NAME, cmd))
    return out


def run_commands(text):
    """Every shell command a workflow actually RUNS, as (offset, command) pairs.

    Only `run:` values count. The first cut of this searched the whole YAML, and then flagged this
    very lock's own job comment and step `name:` — both of which legitimately SAY `tsc --noEmit` while
    running nothing. A prose mention is not a second home; a `run:` line is. Single-line `run:` values
    and `run: |` block scalars are both handled, which is every form this repo uses.
    """
    out, lines, i = [], (text or "").splitlines(keepends=True), 0
    off = 0
    while i < len(lines):
        line = lines[i]
        m = re.search(r"\brun:[ \t]*(.*?)[ \t]*$", line)
        if m:
            val = m.group(1)
            if val in ("|", "|-", ">", ">-", "|+"):
                indent = len(line) - len(line.lstrip())
                j, body = i + 1, []
                while j < len(lines):
                    nxt = lines[j]
                    if nxt.strip() and (len(nxt) - len(nxt.lstrip())) <= indent:
                        break
                    body.append(nxt)
                    j += 1
                out.append((off, "".join(body)))
                off += sum(len(x) for x in lines[i:j])
                i = j
                continue
            out.append((off + m.start(1), val))
        off += len(line)
        i += 1
    return out


def gate_violations(files):
    """(b)+(c): some workflow RUNS the one home, in a job that installs the frontend's deps, and no
    workflow keeps a second copy of the raw compiler invocation.

    files: {workflow name: yaml text} → [plain sentences]; [] = the gate holds.
    """
    out = []
    runners = {n: t for n, t in files.items() if RUN_TYPECHECK.search(t)}
    if not runners:
        out.append("no workflow runs `npm run %s` — the frontend type checker is not a gate, which is "
                   "exactly the hole that let a red Vercel deploy sit under 49 green checks." % SCRIPT_NAME)
    for name, text in sorted(runners.items()):
        # The job running it must install node_modules, or tsc cannot start. The job BOUNDARY is a
        # key at exactly two spaces of indent (`  frontend-typecheck:`) — `rfind("\n  ")` was wrong
        # here, because a step line (`      - run: …`) also starts with a newline and two spaces, so
        # it sliced the body down to the step itself and read every correctly-wired job as having no
        # install. The negative control at (h) caught it; that is what the controls are for.
        runs = run_commands(text)
        pos = next(o for o, c in runs if RUN_TYPECHECK.search(c))
        starts = [m.start() for m in JOB_KEY.finditer(text) if m.start() < pos]
        job_at = starts[-1] if starts else 0
        body = " ; ".join(c for o, c in runs if job_at <= o < pos)
        if "npm ci" not in body and "npm install" not in body:
            out.append("%s runs `npm run %s` in a job that never installs the frontend's dependencies "
                       "— tsc cannot run without node_modules, so the step dies before it checks a "
                       "single file." % (name, SCRIPT_NAME))
    for name, text in sorted(files.items()):
        for _, cmd in run_commands(text):
            m = TSC_NOEMIT.search(cmd)
            if m:
                out.append("%s RUNS `%s` itself instead of `npm run %s` — a second home for the "
                           "compiler invocation, which will drift from the one a developer runs."
                           % (name, m.group(0).strip(), SCRIPT_NAME))
    return out


def filter_violations(files, covering="frontend/**"):
    """(d): the workflow that runs the gate must be TRIGGERED by any frontend change, on both
    pull_request and push. Read as text: the path lists here are flat `- "glob"` lines under
    `paths:`, so the trigger block each one belongs to is found by position."""
    out = []
    for name, text in sorted(files.items()):
        if not RUN_TYPECHECK.search(text):
            continue
        for trigger in ("pull_request", "push"):
            m = re.search(r"^  %s:\s*$" % trigger, text, re.M)
            if not m:
                # no filter at all for this trigger means it either does not fire, or fires always.
                if not re.search(r"^  %s:" % trigger, text, re.M):
                    out.append("%s runs the type-check gate but has no `%s:` trigger — the gate would "
                               "not fire on a %s at all." % (name, trigger, trigger))
                continue
            nxt = re.search(r"^  [a-z_]+:\s*$", text[m.end():], re.M)
            block = text[m.end(): m.end() + (nxt.start() if nxt else len(text))]
            if "paths:" not in block:
                continue          # no paths filter = fires on everything, which covers frontend
            if ('"%s"' % covering) not in block and ("- %s" % covering) not in block:
                out.append("%s's `%s` path filter does not name `%s` — a change to frontend/"
                           "tsconfig.json, package.json, package-lock.json, next.config.ts or "
                           "site-routing.ts would SKIP the type check and the PR would report green. "
                           "A gate that skips silently is not a gate." % (name, trigger, covering))
    return out


def tsconfig_violations(tsconfig_text):
    """(e): the strictness cannot be dialled down, and src cannot be excluded, to reach green."""
    out = []
    try:
        cfg = json.loads(strip_jsonc(tsconfig_text or "{}"))
    except ValueError as exc:
        return ["frontend/tsconfig.json does not parse (%s)" % exc]
    opts = cfg.get("compilerOptions") or {}
    if opts.get("strict") is not True:
        out.append("frontend/tsconfig.json no longer sets `strict: true` — relaxing the compiler is "
                   "how a red tree is made green without fixing anything.")
    if opts.get("noEmit") is not True:
        out.append("frontend/tsconfig.json no longer sets `noEmit: true` — the gate would start "
                   "writing build output instead of only checking types.")
    inc = cfg.get("include") or []
    for glob in ("**/*.ts", "**/*.tsx"):
        if glob not in inc:
            out.append("frontend/tsconfig.json's `include` no longer names `%s` — files dropped out "
                       "of the program are not checked, and the gate reports green on them." % glob)
    for ex in (cfg.get("exclude") or []):
        if ex.strip("./").startswith("src"):
            out.append("frontend/tsconfig.json excludes %r — excluding source to reach green is the "
                       "patchwork this lock exists to stop." % ex)
    return out


def next_config_violations(next_config_text):
    """(f): the ONE switch that makes the Next build stop reporting type errors."""
    out = []
    if re.search(r"ignoreBuildErrors\s*:\s*true", next_config_text or ""):
        out.append("frontend/next.config.ts sets `typescript.ignoreBuildErrors: true` — that switches "
                   "off the type check in the production build, i.e. it silences the exact signal "
                   "(a red Vercel deploy) that was the only thing reporting this defect.")
    return out


BLANKET = ("@ts-nocheck", "@ts-ignore")


def suppression_violations(sources):
    """(g): sources is {rel path: text}. A blanket suppression never expires."""
    out = []
    for rel in sorted(sources):
        for mark in BLANKET:
            if mark in (sources[rel] or ""):
                out.append("%s uses `%s`, which never expires — it will go on hiding whatever error "
                           "that line grows into. Fix the type, or use `@ts-expect-error`, which "
                           "fails the build once the error is gone." % (rel, mark))
    return out


# ── the real tree ─────────────────────────────────────────────────────────────────────────────────
PKG = read(os.path.join(FE, "package.json"))
TSCONFIG = read(os.path.join(FE, "tsconfig.json"))
NEXTCFG = read(os.path.join(FE, "next.config.ts"))
WF = {f: read(os.path.join(WORKFLOWS, f))
      for f in sorted(os.listdir(WORKFLOWS)) if f.endswith((".yml", ".yaml"))}

SRC = {}
for dp, dirs, fs in os.walk(FE):
    dirs[:] = [d for d in dirs if d not in ("node_modules", ".next")]
    for f in fs:
        if f.endswith((".ts", ".tsx", ".mts")):
            rel = os.path.relpath(os.path.join(dp, f), ROOT).replace(os.sep, "/")
            SRC[rel] = read(os.path.join(dp, f))

print("\nFrontend type-check gate — a type error fails a check, and the gate cannot be un-wired\n")
print("  %d frontend .ts/.tsx/.mts files scanned · %d workflows read\n" % (len(SRC), len(WF)))

check("(pre) frontend/package.json is readable", PKG is not None)
check("(pre) frontend/tsconfig.json is readable", TSCONFIG is not None)

for label, fn, arg in (
    ("(a) the type check has ONE home: package.json's `%s` script, running tsc --noEmit" % SCRIPT_NAME,
     one_home_violations, PKG),
    ("(b,c) a workflow RUNS that one home, in a job that installs node_modules, and nothing keeps a second copy",
     gate_violations, WF),
    ("(d) the gate's workflow is triggered by ANY frontend change, on pull_request AND push",
     filter_violations, WF),
    ("(e) tsconfig keeps strict + noEmit and excludes no source",
     tsconfig_violations, TSCONFIG),
    ("(f) next.config.ts does not tell the build to ignore type errors",
     next_config_violations, NEXTCFG),
    ("(g) no blanket @ts-nocheck / @ts-ignore anywhere under frontend/",
     suppression_violations, SRC),
):
    bad = fn(arg)
    for v in bad:
        print("    x " + v)
    check(label, bad == [])

# ── (h) negative controls — every rule must be able to go RED ─────────────────────────────────────
GOOD_WF = ('on:\n  pull_request:\n    paths:\n      - "frontend/**"\n  push:\n    paths:\n'
           '      - "frontend/**"\n\njobs:\n  frontend-typecheck:\n    steps:\n'
           '      - run: npm ci\n      - run: npm run typecheck\n')
check("(h) no `typecheck` script → RED", bool(one_home_violations('{"scripts":{"lint":"eslint"}}')))
check("(h) a `typecheck` script that does not run tsc --noEmit → RED",
      bool(one_home_violations('{"scripts":{"typecheck":"echo ok"}}')))
check("(h) …and the real shape → green",
      one_home_violations('{"scripts":{"typecheck":"next typegen && tsc --noEmit"}}') == [])
check("(h) no workflow running the gate → RED",
      bool(gate_violations({"x.yml": "jobs:\n  g:\n    steps:\n      - run: npm run lint\n"})))
check("(h) the gate in a job that installs nothing → RED",
      bool(gate_violations({"x.yml": 'on:\n  pull_request:\n    paths:\n      - "frontend/**"\n  push:\n'
                                     '    paths:\n      - "frontend/**"\n\njobs:\n  g:\n    steps:\n'
                                     "      - run: npm run typecheck\n"})))
check("(h) a workflow spelling `tsc --noEmit` itself (a second home) → RED",
      bool(gate_violations({"x.yml": GOOD_WF + "      - run: npx tsc --noEmit\n"})))
check("(h) …and the wired shape → green", gate_violations({"x.yml": GOOD_WF}) == [])
check("(h) a job NAME or comment that merely mentions tsc --noEmit is not a second home → green",
      gate_violations({"x.yml": GOOD_WF.replace("  frontend-typecheck:",
          "  # we run tsc --noEmit here\n  frontend-typecheck:\n    name: type check (tsc --noEmit)")}) == [])
check("(h) …but a `run:` block scalar hiding it IS → RED",
      bool(gate_violations({"x.yml": GOOD_WF + "      - run: |\n          npx tsc --noEmit\n"})))
check("(h) a pull_request filter that covers only frontend/src → RED",
      bool(filter_violations({"x.yml": GOOD_WF.replace('      - "frontend/**"\n  push:',
                                                       '      - "frontend/src/**"\n  push:')})))
check("(h) a push filter that covers only frontend/src → RED",
      bool(filter_violations({"x.yml": GOOD_WF[::-1][::-1].replace(
          '  push:\n    paths:\n      - "frontend/**"',
          '  push:\n    paths:\n      - "frontend/src/**"')})))
check("(h) …and both filters naming frontend/** → green", filter_violations({"x.yml": GOOD_WF}) == [])
check("(h) a workflow that does not run the gate is not judged on its filters → green",
      filter_violations({"x.yml": 'on:\n  push:\n    paths:\n      - "backend/**"\n\njobs:\n  g:\n'
                                  "    steps:\n      - run: echo hi\n"}) == [])
check("(h) strict turned off → RED",
      bool(tsconfig_violations('{"compilerOptions":{"strict":false,"noEmit":true},'
                               '"include":["**/*.ts","**/*.tsx"]}')))
check("(h) noEmit dropped → RED",
      bool(tsconfig_violations('{"compilerOptions":{"strict":true},'
                               '"include":["**/*.ts","**/*.tsx"]}')))
check("(h) a source glob dropped from include → RED",
      bool(tsconfig_violations('{"compilerOptions":{"strict":true,"noEmit":true},'
                               '"include":["**/*.ts"]}')))
check("(h) src excluded to reach green → RED",
      bool(tsconfig_violations('{"compilerOptions":{"strict":true,"noEmit":true},'
                               '"include":["**/*.ts","**/*.tsx"],"exclude":["src/app/broken"]}')))
check("(h) …and the real tsconfig shape → green",
      tsconfig_violations('{"compilerOptions":{"strict":true,"noEmit":true},'
                          '"include":["**/*.ts","**/*.tsx"],"exclude":["node_modules"]}') == [])
check("(h) ignoreBuildErrors → RED",
      bool(next_config_violations("const c = { typescript: { ignoreBuildErrors: true } }")))
check("(h) …and ignoreBuildErrors: false → green",
      next_config_violations("const c = { typescript: { ignoreBuildErrors: false } }") == [])
check("(h) a @ts-ignore → RED", bool(suppression_violations({"frontend/src/a.ts": "// @ts-ignore\nx()"})))
check("(h) a @ts-nocheck → RED", bool(suppression_violations({"frontend/src/a.ts": "// @ts-nocheck\n"})))
check("(h) a @ts-expect-error is the honest form and stays green",
      suppression_violations({"frontend/src/a.ts": "// @ts-expect-error reason\nx()"}) == [])

# ── wired, or it is not a lock ────────────────────────────────────────────────────────────────────
ALL_WF = "\n".join(t or "" for t in WF.values())
check("(wired) this lock itself is run by a workflow",
      "harness_frontend_typecheck_lock.py" in ALL_WF)
check("(wired) a change to this lock re-runs the workflow that runs it",
      '"backend/harness_frontend_typecheck_lock.py"' in ALL_WF)

print("\n%d passed, %d failed" % (P, F))
if F:
    print("\n  The frontend type-check gate is the only thing between a type error and main: there is\n"
          "  no other check that compiles the frontend. Re-wire it rather than relaxing it — run\n"
          "  `cd frontend && npm run typecheck` to see what tsc actually says.")
    sys.exit(1)
print("OK — `npm run typecheck` has one home, a workflow runs it on every frontend change, and the\n"
      "strictness, the path filter and the absence of blanket suppressions are all locked.")
