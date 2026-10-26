"""LOCK — a frontend LINT finding fails a check, and the gate cannot be un-wired, widened or silenced.

THE DEFECT (owner directive 2026-10-11, verbatim: *"Run the code style checker and fix the problems"*).
`frontend/package.json` has declared a `lint` script (`eslint`) all along and **nothing in CI ran it**.
Measured on `origin/main` (567e7050): the single grep hit for `npm run lint` across
`.github/workflows/` was zero, while `npm run lint` on a clean install reported **5953** problems
(5675 errors, 278 warnings) over **422** files. Exactly the §75 class one layer over: PR #438 found
that nothing ran `tsc`, and the SAME hole was still open for the linter beside it.

THE CLASS, NOT THE INSTANCE. The instance is 5953 findings. The class is: **a checker the repo
declares but never executes**. Every such script is a gate that reports nothing — §19.18's "a
mechanism that exists with no caller wired to it", and §19.25's "a gate that does not run is not a
gate". So this ships as a GATE plus the lock that keeps it one, not as a one-off cleanup.

WHY A RATCHET AND NOT A CLEAN GATE — and why that is NOT the decoration #438 refused to build.
#438 deliberately shipped NO pending file because the frontend type-checked clean, and a ratchet
holding zero entries teaches the next reader the gate is negotiable. Here the opposite is true:
`frontend/eslint_pending.txt` holds REAL entries, because 4903 of the 5953 findings are
`@typescript-eslint/no-explicit-any` and 666 are React-Compiler / hook rules whose fixes change
RUNTIME BEHAVIOUR in commission, payroll, closing and P&L pages. Money-touching behaviour changes are
surfaced for owner approval in this repo; a lint gate does not get to make 5569 of them unasked. So
the register pins today's debt, the gate refuses anything NEW, and the register may only shrink.

WHAT THIS LOCK FAILS THE BUILD FOR — the ways the gate could quietly stop being one:
  (a) NO ONE HOME FOR THE COMMAND. `frontend/package.json` must declare a `lint` script that runs
      `eslint`, and a `lint:gate` script that runs the ratchet. One spelling, for CI and for a
      developer alike.
  (b) THE GATE IS NOT RUN. Some workflow must run `npm run lint:gate`, in a job that installs the
      frontend's dependencies (`npm ci`) — ESLint cannot run without `node_modules`.
  (c) A SECOND COPY OF THE COMMAND. A workflow (or the ratchet runner) that spells `eslint` itself
      instead of dereferencing the package.json script is the "one fact, two homes" defect: the two
      spellings drift and the gate then checks something other than what a developer runs.
  (d) THE GATE SKIPS SILENTLY. The workflow's `pull_request` AND `push` path filters must cover
      `frontend/**`. `frontend/src/**` alone does NOT match `eslint.config.mjs`, `package.json`,
      `package-lock.json` or `frontend/tools/` — every one of which can introduce or hide a finding.
  (e) THE CONFIG LOSES ITS RULES. `frontend/eslint.config.mjs` must keep spreading BOTH
      `eslint-config-next/core-web-vitals` (the a11y + Next correctness rules) and
      `eslint-config-next/typescript` (the @typescript-eslint rules). Dropping either is how 5000
      findings become 50 without a single defect being fixed.
  (f) THE IGNORES ARE WIDENED. `globalIgnores` may only name build output. An entry under `src/`,
      `tools/`, `scratchpad/` or a bare `**` takes files out of the program, and the gate then
      reports green on them.
  (g) SUPPRESSIONS GROW. The total number of `eslint-disable` directives under `frontend/` may never
      exceed PINNED_SUPPRESSIONS, and no directive may be BLANKET — neither a rule-less
      `eslint-disable-line` / `eslint-disable-next-line` (which switches off every rule on that line)
      nor a file-level `/* eslint-disable */` with no rule list. This is the rule that makes "fix the
      problems" mean fix, rather than comment out. A directive naming specific rules is allowed and
      counted; the ceiling is what stops it being the cheap way out.
  (h) THE REGISTER GROWS. `frontend/eslint_pending.txt` must parse, and its pinned total may never
      exceed PINNED_TOTAL. Lower PINNED_TOTAL as the register shrinks; raising it is the one edit
      this lock exists to make someone argue for in a PR.
  (i) NEGATIVE CONTROLS — every rule above must be shown going RED over a synthetic input, so a rule
      that has stopped matching anything cannot sit here reading green.

Stdlib only, DB-free, and it does NOT run ESLint: it runs in the lint job BEFORE `npm ci`, so its
verdict is reported even when the lint step itself is what goes red (the §19.25 lesson — a lock
sitting behind the thing it guards does not report when that thing dies). The workflow is read as
text, the same way harness_frontend_typecheck_lock.py and harness_ci_pipefail_lock.py read it.

  python3 backend/harness_frontend_lint_lock.py
"""
import io
import json
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FE = os.path.join(ROOT, "frontend")
WORKFLOWS = os.path.join(ROOT, ".github", "workflows")

LINT_SCRIPT = "lint"
GATE_SCRIPT = "lint:gate"
REGISTER = "eslint_pending.txt"

# MEASURED on this tree, not estimated. Both may only ever be LOWERED.
#   python3 -c "import json,collections;d=json.load(open('/tmp/r.json'));print(sum(len(f['messages']) for f in d))"
PINNED_TOTAL = 5583        # findings pinned by frontend/eslint_pending.txt (5953 on main before the
                           # gate landed; 5917 when it did; 5645 once react/no-unescaped-entities
                           # and @typescript-eslint/no-unused-expressions were cleared to zero; 5643
                           # once the admin-assigned-password card moved out of /admin/security into
                           # its own component, which cleared two `any` annotations with it — §CORE-08;
                           # 5588 once the five form components that a user types into were hoisted to
                           # module scope, clearing 43 `react-hooks/static-components` findings in four
                           # pages plus two more — §19.56; 5584 once the Asset Lending page stopped
                           # folding its own lifetime device count and read the served total instead,
                           # which cleared the `any` that fold needed — §CORE-17.3l)
PINNED_SUPPRESSIONS = 92     # `eslint-disable` directives under frontend/ (110 on main before this
                             # change; 20 that ESLint itself reported as suppressing NOTHING were
                             # deleted, and 8 blanket ones were given the rule name they rely on).
                             # Pinned EXACTLY at the measured value, so the count cannot grow at all.

RUN_GATE = re.compile(r"npm\s+run\s+" + re.escape(GATE_SCRIPT) + r"\b")
# A bare `eslint` invocation, not as part of `npm run lint` and not `eslint.config`/`eslint_pending`.
BARE_ESLINT = re.compile(r"(?<!run )(?<!npx )\b(?:npx\s+)?eslint\b(?!\.config)(?!_pending)(?!-)")
JOB_KEY = re.compile(r"^  [A-Za-z0-9_-]+:\s*$", re.M)
DISABLE = re.compile(r"eslint-disable(?:-next-line|-line)?")
# A directive that names NO rule: `// eslint-disable-line` / `-next-line` with nothing after it, or a
# file-level `/* eslint-disable */`. `--` introduces a prose justification, not a rule name.
BLANKET = re.compile(
    r"eslint-disable(-next-line|-line)?[ \t]*(?=$|\*/|--|\n)", re.M)

P = F = 0


def check(label, ok, detail=""):
    global P, F
    if ok:
        P += 1
        print("  PASS  %s" % label)
    else:
        F += 1
        print("  FAIL  %s   %s" % (label, str(detail)[:700]))


def read(path):
    if not os.path.isfile(path):
        return None
    return io.open(path, encoding="utf-8", errors="replace").read()


def run_commands(text):
    """Every shell command a workflow actually RUNS, as (offset, command) pairs. Only `run:` values
    count — a job comment or a step `name:` may legitimately SAY `eslint` while running nothing.
    Single-line `run:` values and `run: |` block scalars are both handled (same reader as
    harness_frontend_typecheck_lock.py; a prose mention is not a second home, a `run:` line is)."""
    out, lines, i, off = [], (text or "").splitlines(keepends=True), 0, 0
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


# ── the rules, each a pure function so the negative controls can drive it ──────────────────────────
def one_home_violations(package_json_text):
    """(a): the linter has ONE home, and the gate has one too."""
    out = []
    try:
        scripts = (json.loads(package_json_text or "{}").get("scripts") or {})
    except ValueError as exc:
        return ["frontend/package.json does not parse (%s)" % exc]
    lint = scripts.get(LINT_SCRIPT)
    if not lint:
        out.append("frontend/package.json declares no `%s` script — the linter has no ONE home, so CI "
                   "and a developer would each spell the eslint invocation themselves and drift."
                   % LINT_SCRIPT)
    elif not re.search(r"\beslint\b", lint):
        out.append("frontend/package.json's `%s` script is %r, which does not run eslint — the gate "
                   "would pass without linting anything." % (LINT_SCRIPT, lint))
    gate = scripts.get(GATE_SCRIPT)
    if not gate:
        out.append("frontend/package.json declares no `%s` script — the ratchet gate has no ONE home. "
                   "Add \"%s\": \"node tools/eslint-ratchet.mjs\"." % (GATE_SCRIPT, GATE_SCRIPT))
    elif "eslint-ratchet" not in gate:
        out.append("frontend/package.json's `%s` script is %r, which does not run the ratchet runner."
                   % (GATE_SCRIPT, gate))
    elif re.search(r"\beslint\b(?!-ratchet)", gate):
        out.append("frontend/package.json's `%s` script spells eslint itself (%r) instead of letting "
                   "the ratchet dereference the `%s` script — a second home for the invocation."
                   % (GATE_SCRIPT, gate, LINT_SCRIPT))
    return out


def gate_violations(files):
    """(b)+(c): some workflow RUNS the gate, in a job that installs node_modules, and no workflow
    keeps a second copy of the raw linter invocation. files: {name: yaml text} -> [sentences]."""
    out = []
    runners = {n: t for n, t in files.items() if RUN_GATE.search(t)}
    if not runners:
        out.append("no workflow runs `npm run %s` — the frontend linter is not a gate, which is "
                   "exactly the hole this lock was written for: the `lint` script existed for months "
                   "and nothing executed it." % GATE_SCRIPT)
    for name, text in sorted(runners.items()):
        runs = run_commands(text)
        pos = next(o for o, c in runs if RUN_GATE.search(c))
        starts = [m.start() for m in JOB_KEY.finditer(text) if m.start() < pos]
        job_at = starts[-1] if starts else 0
        body = " ; ".join(c for o, c in runs if job_at <= o < pos)
        if "npm ci" not in body and "npm install" not in body:
            out.append("%s runs `npm run %s` in a job that never installs the frontend's dependencies "
                       "— ESLint cannot run without node_modules, so the step dies before it reads a "
                       "single file." % (name, GATE_SCRIPT))
    for name, text in sorted(files.items()):
        for _, cmd in run_commands(text):
            for m in BARE_ESLINT.finditer(cmd):
                if re.search(r"npm\s+run\s+\S*lint", cmd):
                    continue
                out.append("%s RUNS `%s` itself instead of `npm run %s` — a second home for the linter "
                           "invocation, which will drift from the one a developer runs."
                           % (name, m.group(0).strip(), GATE_SCRIPT))
    return out


def filter_violations(files, covering="frontend/**"):
    """(d): the workflow that runs the gate must be TRIGGERED by any frontend change, on both
    pull_request and push."""
    out = []
    for name, text in sorted(files.items()):
        if not RUN_GATE.search(text):
            continue
        for trigger in ("pull_request", "push"):
            m = re.search(r"^  %s:\s*$" % trigger, text, re.M)
            if not m:
                if not re.search(r"^  %s:" % trigger, text, re.M):
                    out.append("%s runs the lint gate but has no `%s:` trigger — the gate would not "
                               "fire on a %s at all." % (name, trigger, trigger))
                continue
            nxt = re.search(r"^  [a-z_]+:\s*$", text[m.end():], re.M)
            block = text[m.end(): m.end() + (nxt.start() if nxt else len(text))]
            if "paths:" not in block:
                continue          # no paths filter = fires on everything, which covers frontend
            if ('"%s"' % covering) not in block and ("- %s" % covering) not in block:
                out.append("%s's `%s` path filter does not name `%s` — a change to "
                           "frontend/eslint.config.mjs, package.json, package-lock.json or "
                           "frontend/tools/ would SKIP the lint gate and the PR would report green. "
                           "A gate that skips silently is not a gate." % (name, trigger, covering))
    return out


REQUIRED_CONFIGS = ("eslint-config-next/core-web-vitals", "eslint-config-next/typescript")


def config_violations(config_text):
    """(e)+(f): the rule sets stay spread in, and the ignores stay limited to build output."""
    out = []
    if config_text is None:
        return ["frontend/eslint.config.mjs is missing — there is no config for the gate to run."]
    for need in REQUIRED_CONFIGS:
        if need not in config_text:
            out.append("frontend/eslint.config.mjs no longer pulls in `%s`. Dropping a shared config "
                       "deletes whole rule families (a11y and Next correctness, or the "
                       "@typescript-eslint rules) — thousands of findings disappear without one "
                       "defect being fixed." % need)
    for need in REQUIRED_CONFIGS:
        base = need.rsplit("/", 1)[-1].replace("-", "")
        if need in config_text and not re.search(r"\.\.\.\s*\w+", config_text):
            out.append("frontend/eslint.config.mjs imports `%s` but spreads nothing (`...name`) — an "
                       "imported-but-unused config contributes no rules." % need)
            break
        del base
    ignores = re.search(r"globalIgnores\(\s*\[(.*?)\]\s*\)", config_text or "", re.S)
    if ignores:
        for raw in re.findall(r"""["']([^"']+)["']""", ignores.group(1)):
            g = raw.strip()
            if g in ("**", "**/*") or g.startswith(("src/", "tools/", "scratchpad/",
                                                    "**/src/", "./src")):
                out.append("frontend/eslint.config.mjs globally ignores %r. Widening the ignores takes "
                           "source out of the program, and the gate then reports green on it — the "
                           "patchwork this lock exists to stop." % raw)
    return out


def suppression_violations(sources, ceiling=None):
    """(g): suppressions may not grow, and none may be BLANKET.

    sources: {rel path: text}. Returns [sentences]; [] = the ceiling holds.
    """
    out = []
    total = 0
    for rel in sorted(sources):
        text = sources[rel] or ""
        total += len(DISABLE.findall(text))
        for m in BLANKET.finditer(text):
            line = text[:m.start()].count("\n") + 1
            out.append("%s:%d carries a BLANKET `%s` that names no rule — it switches off EVERY rule "
                       "there, including ones nobody has written yet, and it never expires. Name the "
                       "rule, or fix the finding." % (rel, line, m.group(0).strip()))
    if ceiling is not None and total > ceiling:
        out.append("there are now %d `eslint-disable` directives under frontend/, over the pinned "
                   "ceiling of %d. Reaching green by commenting the linter out is the one route this "
                   "gate exists to close: fix the finding, or argue the rule is wrong for this "
                   "codebase in the PR. Lower PINNED_SUPPRESSIONS when the count drops."
                   % (total, ceiling))
    return out


def register_violations(register_text, ceiling):
    """(h): the pinned debt register parses and may only shrink."""
    out = []
    if register_text is None:
        return ["frontend/%s is missing — it is the pinned lint-debt register and the gate cannot "
                "run without it." % REGISTER]
    total = 0
    for n, raw in enumerate(register_text.splitlines(), 1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        m = re.match(r"^(\d+)\s+(\S+)\s+(.+)$", line)
        if not m:
            out.append("frontend/%s line %d is not `<count> <rule> <path>`: %r" % (REGISTER, n, line))
            continue
        total += int(m.group(1))
    if total > ceiling:
        out.append("frontend/%s now pins %d findings, over PINNED_TOTAL=%d. The register may only "
                   "SHRINK — raising the ceiling re-admits lint debt that was already paid off, and "
                   "is the one edit this lock exists to make someone argue for in a PR."
                   % (REGISTER, total, ceiling))
    return out


# ── the real tree ─────────────────────────────────────────────────────────────────────────────────
PKG = read(os.path.join(FE, "package.json"))
CFG = read(os.path.join(FE, "eslint.config.mjs"))
REG = read(os.path.join(FE, REGISTER))
WF = {f: read(os.path.join(WORKFLOWS, f))
      for f in sorted(os.listdir(WORKFLOWS)) if f.endswith((".yml", ".yaml"))}

SRC = {}
for dp, dirs, fs in os.walk(FE):
    dirs[:] = [d for d in dirs if d not in ("node_modules", ".next", "out", "build")]
    for f in fs:
        if f.endswith((".ts", ".tsx", ".mts", ".js", ".jsx", ".mjs", ".cjs")):
            rel = os.path.relpath(os.path.join(dp, f), ROOT).replace(os.sep, "/")
            SRC[rel] = read(os.path.join(dp, f))

print("\nFrontend lint gate — a lint finding fails a check, and the gate cannot be un-wired or silenced\n")
print("  %d frontend source files scanned · %d workflows read\n" % (len(SRC), len(WF)))

check("(pre) frontend/package.json is readable", PKG is not None)
check("(pre) frontend/eslint.config.mjs is readable", CFG is not None)
check("(pre) frontend/%s is readable" % REGISTER, REG is not None)

for label, fn, arg in (
    ("(a) the linter has ONE home (`%s`) and the gate has one (`%s`)" % (LINT_SCRIPT, GATE_SCRIPT),
     one_home_violations, PKG),
    ("(b,c) a workflow RUNS the gate, in a job that installs node_modules, and nothing keeps a second copy",
     gate_violations, WF),
    ("(d) the gate's workflow is triggered by ANY frontend change, on pull_request AND push",
     filter_violations, WF),
    ("(e,f) the config keeps both shared rule sets and ignores only build output",
     config_violations, CFG),
):
    bad = fn(arg)
    for v in bad:
        print("    x " + v)
    check(label, bad == [])

bad = suppression_violations(SRC, PINNED_SUPPRESSIONS)
for v in bad:
    print("    x " + v)
check("(g) no BLANKET eslint-disable, and suppressions stay at or under %d" % PINNED_SUPPRESSIONS,
      bad == [])

bad = register_violations(REG, PINNED_TOTAL)
for v in bad:
    print("    x " + v)
check("(h) frontend/%s parses and pins at most %d findings" % (REGISTER, PINNED_TOTAL), bad == [])

# ── (i) negative controls — every rule must be able to go RED ─────────────────────────────────────
GOOD_WF = ('on:\n  pull_request:\n    paths:\n      - "frontend/**"\n  push:\n    paths:\n'
           '      - "frontend/**"\n\njobs:\n  frontend-lint:\n    steps:\n'
           '      - run: npm ci\n      - run: npm run lint:gate\n')
GOOD_PKG = '{"scripts":{"lint":"eslint","lint:gate":"node tools/eslint-ratchet.mjs"}}'
GOOD_CFG = ('import nextVitals from "eslint-config-next/core-web-vitals";\n'
            'import nextTs from "eslint-config-next/typescript";\n'
            'export default defineConfig([...nextVitals, ...nextTs, '
            'globalIgnores([".next/**","out/**"])]);\n')

check("(i) no `lint` script → RED", bool(one_home_violations('{"scripts":{"build":"next build"}}')))
check("(i) a `lint` script that does not run eslint → RED",
      bool(one_home_violations('{"scripts":{"lint":"echo ok","lint:gate":"node tools/eslint-ratchet.mjs"}}')))
check("(i) no `lint:gate` script → RED", bool(one_home_violations('{"scripts":{"lint":"eslint"}}')))
check("(i) a gate script spelling eslint itself → RED",
      bool(one_home_violations('{"scripts":{"lint":"eslint","lint:gate":"eslint -f json"}}')))
check("(i) …and the real shape → green", one_home_violations(GOOD_PKG) == [])

check("(i) no workflow running the gate → RED",
      bool(gate_violations({"x.yml": "jobs:\n  g:\n    steps:\n      - run: npm run typecheck\n"})))
check("(i) the gate in a job that installs nothing → RED",
      bool(gate_violations({"x.yml": 'on:\n  pull_request:\n    paths:\n      - "frontend/**"\n'
                                     '  push:\n    paths:\n      - "frontend/**"\n\njobs:\n  g:\n'
                                     "    steps:\n      - run: npm run lint:gate\n"})))
check("(i) a workflow spelling `npx eslint` itself (a second home) → RED",
      bool(gate_violations({"x.yml": GOOD_WF + "      - run: npx eslint src\n"})))
check("(i) …and the wired shape → green", gate_violations({"x.yml": GOOD_WF}) == [])
check("(i) a job NAME or comment that merely mentions eslint is not a second home → green",
      gate_violations({"x.yml": GOOD_WF.replace("  frontend-lint:",
          "  # we run eslint here\n  frontend-lint:\n    name: lint (eslint over the frontend)")}) == [])
check("(i) …but a `run:` block scalar hiding it IS → RED",
      bool(gate_violations({"x.yml": GOOD_WF + "      - run: |\n          npx eslint .\n"})))

check("(i) a pull_request filter that covers only frontend/src → RED",
      bool(filter_violations({"x.yml": GOOD_WF.replace('      - "frontend/**"\n  push:',
                                                       '      - "frontend/src/**"\n  push:')})))
check("(i) a push filter that covers only frontend/src → RED",
      bool(filter_violations({"x.yml": GOOD_WF.replace(
          '  push:\n    paths:\n      - "frontend/**"',
          '  push:\n    paths:\n      - "frontend/src/**"')})))
check("(i) …and both filters naming frontend/** → green", filter_violations({"x.yml": GOOD_WF}) == [])
check("(i) a workflow that does not run the gate is not judged on its filters → green",
      filter_violations({"x.yml": 'on:\n  push:\n    paths:\n      - "backend/**"\n\njobs:\n  g:\n'
                                  "    steps:\n      - run: echo hi\n"}) == [])

check("(i) the typescript rule set dropped from the config → RED",
      bool(config_violations(GOOD_CFG.replace('import nextTs from "eslint-config-next/typescript";\n', ""))))
check("(i) the core-web-vitals rule set dropped → RED",
      bool(config_violations(GOOD_CFG.replace(
          'import nextVitals from "eslint-config-next/core-web-vitals";\n', ""))))
check("(i) src/** added to globalIgnores → RED",
      bool(config_violations(GOOD_CFG.replace('".next/**","out/**"', '".next/**","src/**"'))))
check("(i) a bare ** added to globalIgnores → RED",
      bool(config_violations(GOOD_CFG.replace('".next/**","out/**"', '".next/**","**"'))))
check("(i) …and the real config shape → green", config_violations(GOOD_CFG) == [])

check("(i) a rule-less `// eslint-disable-line` → RED",
      bool(suppression_violations({"frontend/src/a.ts": "const x = 1 // eslint-disable-line\n"})))
check("(i) a rule-less `// eslint-disable-next-line` → RED",
      bool(suppression_violations({"frontend/src/a.ts": "// eslint-disable-next-line\nx()\n"})))
check("(i) a file-level `/* eslint-disable */` → RED",
      bool(suppression_violations({"frontend/src/a.ts": "/* eslint-disable */\nx()\n"})))
check("(i) a directive that NAMES its rule → green",
      suppression_violations({"frontend/src/a.ts":
                              "// eslint-disable-next-line react-hooks/exhaustive-deps\nx()\n"}) == [])
check("(i) …and one that names a rule plus a prose reason → green",
      suppression_violations({"frontend/src/a.ts":
                              "// eslint-disable-next-line @next/next/no-img-element -- a data: URL\nx()\n"}) == [])
check("(i) suppressions over the ceiling → RED",
      bool(suppression_violations({"frontend/src/a.ts":
                                   "// eslint-disable-line no-console\n" * 3}, ceiling=2)))
check("(i) …and at the ceiling → green",
      suppression_violations({"frontend/src/a.ts":
                              "// eslint-disable-line no-console\n" * 2}, ceiling=2) == [])

check("(i) a register over PINNED_TOTAL → RED",
      bool(register_violations("# h\n9 some/rule path/a.ts\n", 5)))
check("(i) a malformed register line → RED",
      bool(register_violations("# h\nnot-a-count rule path\n", 99)))
check("(i) …and a register at the ceiling → green",
      register_violations("# h\n3 some/rule path/a.ts\n2 other/rule path/b.ts\n", 5) == [])

# ── wired, or it is not a lock ────────────────────────────────────────────────────────────────────
ALL_WF = "\n".join(t or "" for t in WF.values())
check("(wired) this lock itself is run by a workflow",
      "harness_frontend_lint_lock.py" in ALL_WF)
check("(wired) a change to this lock re-runs the workflow that runs it",
      '"backend/harness_frontend_lint_lock.py"' in ALL_WF)
check("(wired) a change to the register re-runs the gate",
      '"frontend/eslint_pending.txt"' in ALL_WF)

print("\n%d passed, %d failed" % (P, F))
if F:
    print("\n  The lint gate is the only thing between a new lint finding and main. Re-wire it rather\n"
          "  than relaxing it — run `cd frontend && npm run lint:gate` to see what ESLint says, and\n"
          "  `npm run lint` for the raw report.")
    sys.exit(1)
print("OK — `npm run lint` has one home, a workflow runs the ratchet over it on every frontend\n"
      "change, and the rule sets, the ignores, the suppression ceiling and the pinned register are\n"
      "all locked.")
