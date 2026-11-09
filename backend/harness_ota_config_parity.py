#!/usr/bin/env python3
"""Every path that ships this app carries its configuration (index §CORE-38) — DB-free, the real files.

THE DEFECT THIS LOCKS. On 2026-11-09 the owner's TestFlight app crashed the instant it opened. The
binary was fine. Three days earlier an unrelated merge had auto-published an over-the-air update,
and that update was bundled with NO configuration: `extra.supabaseUrl` was `''`, so
`createClient('')` threw `supabaseUrl is required.` at module scope and the app died in its first
second, on every launch, with no way to recover from inside it.

THE CAUSE, stated once. This app reaches a phone two ways and only one of them was carrying the
app's build-time environment:

  * `eas build` reads `build.<profile>.env` out of mobile/eas.json by itself.
  * `eas update` does NOT, and no flag makes it — `--environment` selects server-side EAS
    variables, which this project does not use (confirmed against `eas update --help`, 2026-11-09).

So EVERY over-the-air update ever published from this repository had a blank configuration. It went
unseen for weeks because the installed binary was on a different Expo runtime and was silently
rejecting all of them; the first build that accepted one crashed.

THE CLASS, not the instance: a fact two delivery paths need, declared inside one of them. The fix is
NOT to paste the values into the publish workflow as well — that is the second copy the house rules
forbid, and a second copy is what diverged. mobile/eas.json stays the ONE home and the publish
DEREFERENCES it through mobile/scripts/with-profile-env.mjs.

WHAT THIS REFUSES, so the patchwork cannot come back:

  * the required set growing a second home, or a delivery path restating an EXPO_PUBLIC_* value;
  * a delivery path that publishes or builds without the parity gate in front of it;
  * a NEW delivery workflow appearing un-gated (the set is DERIVED from the workflows and compared
    with a DECLARED set, per §CORE-10, so going blind fails the build);
  * the app going back to reading a missing value as '' and crashing somewhere else instead.

Every check carries a negative control: the check is re-run against text carrying the defect and must
FAIL there, so a check that has silently stopped looking is itself a failure.
"""
from __future__ import annotations

import json
import pathlib
import re
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
MOBILE = ROOT / "mobile"
WORKFLOWS = ROOT / ".github/workflows"

DECLARATION = MOBILE / "config/required-extra.json"
APP_READER = MOBILE / "src/config/env.ts"
SUPABASE = MOBILE / "src/api/supabase.ts"
RULE = MOBILE / "scripts/delivery-env.js"
TESTS = MOBILE / "scripts/delivery-env.test.ts"
WRAPPER = MOBILE / "scripts/with-profile-env.mjs"
GATE = MOBILE / "scripts/check-extra.mjs"
VITEST = MOBILE / "vitest.config.ts"
EAS_JSON = MOBILE / "eas.json"

# What this app cannot run without, DECLARED here as well so the declaration cannot quietly shrink.
# Compared for EQUALITY, never as a lower bound: a count or a "contains" check would pass a
# declaration that had lost supabaseAnonKey, and the bundle would ship and crash exactly as before.
PINNED_REQUIRED = {
    "supabaseUrl": "EXPO_PUBLIC_SUPABASE_URL",
    "supabaseAnonKey": "EXPO_PUBLIC_SUPABASE_ANON_KEY",
    "apiUrl": "EXPO_PUBLIC_API_URL",
}

# The build profile a store build and the production channel both use. Named so a profile that loses
# its `env` is a failure here rather than a crash on a phone.
PINNED_PROFILE = "production"

# Every workflow that ships this app's code to a device. DERIVED below from the workflows that
# actually invoke `eas build` or `eas update`, and compared with this set: a new delivery workflow
# fails the build until it is declared here AND gated, which is the §CORE-10 discipline. A guard
# that silently stops covering a subject is worse than no guard.
DECLARED_DELIVERY_WORKFLOWS = {
    "mobile-ota-update.yml",
    "mobile-build-and-submit.yml",
}

failures: list[str] = []
checks = 0


def check(name: str, ok: bool, detail: str = "") -> bool:
    global checks
    checks += 1
    if not ok:
        failures.append(f"{name}: {detail}" if detail else name)
    return ok


def control(name: str, ok_on_defect: bool) -> None:
    """A check must FAIL on text carrying the defect, or the check has stopped looking."""
    check(f"{name} [negative control]", not ok_on_defect,
          "the check passed text that carries the defect — it has stopped looking")


def read(path: pathlib.Path) -> str:
    return path.read_text(encoding="utf-8") if path.exists() else ""


def code_only(src: str) -> str:
    """The source with // line comments stripped, so a check cannot be satisfied by prose."""
    return "\n".join(re.sub(r"//.*$", "", line) for line in src.splitlines())


# ── D1 · the required set has ONE home, and it has not shrunk ─────────────────────────────────────
check("D1.1 the declaration exists", DECLARATION.exists(), f"{DECLARATION} is missing")

declared: dict[str, str] = {}
if DECLARATION.exists():
    try:
        declared = json.loads(read(DECLARATION)).get("required", {})
    except json.JSONDecodeError as exc:  # a declaration that does not parse is no declaration
        check("D1.2 the declaration parses", False, str(exc))

check("D1.2 the declaration parses", isinstance(declared, dict) and bool(declared))
check("D1.3 the declared set is exactly the pinned set", declared == PINNED_REQUIRED,
      f"mobile/config/required-extra.json declares {sorted(declared)} but this harness pins "
      f"{sorted(PINNED_REQUIRED)}. Changing what the app requires is a deliberate edit to both.")
control("D1.3", {"supabaseUrl": "EXPO_PUBLIC_SUPABASE_URL"} == PINNED_REQUIRED)

check("D1.4 every declared value names an environment variable",
      all(re.fullmatch(r"EXPO_PUBLIC_[A-Z0-9_]+", v or "") for v in declared.values()),
      f"values must be EXPO_PUBLIC_* names; got {sorted(declared.values())}")
control("D1.4", all(re.fullmatch(r"EXPO_PUBLIC_[A-Z0-9_]+", v) for v in ["supabaseUrl"]))


# ── D2 · the app dereferences the declaration, and refuses to run without it ──────────────────────
app_src = read(APP_READER)
app_code = code_only(app_src)

DEREF_JSON = re.compile(r"from\s+'(\.\./)+config/required-extra\.json'")
check("D2.1 the app READS the declaration rather than restating it",
      bool(DEREF_JSON.search(app_src)),
      "src/config/env.ts must import config/required-extra.json")
control("D2.1", bool(DEREF_JSON.search("const REQUIRED = { supabaseUrl: 'EXPO_PUBLIC_SUPABASE_URL' }")))

check("D2.2 the app names no environment variable of its own",
      not any(v in app_code for v in PINNED_REQUIRED.values()),
      "src/config/env.ts must not restate an EXPO_PUBLIC_* name in code — the declaration is the "
      "one home")
control("D2.2", not any(v in "const u = process.env.EXPO_PUBLIC_SUPABASE_URL"
                        for v in PINNED_REQUIRED.values()))

# The defect, precisely: a missing value was warned about and read as ''. A warning on a phone is
# seen by nobody, and the crash then surfaced three imports away naming Supabase instead of the
# variable. It must THROW here, where the name is known.
check("D2.3 a missing value throws where it is read", "throw new Error(" in app_code,
      "src/config/env.ts must throw when a required value is blank")
control("D2.3", "throw new Error(" in code_only("console.warn('missing'); return ''"))

check("D2.4 the app does not warn-and-continue on a missing value",
      "console.warn" not in app_code,
      "a console.warn on a phone is seen by nobody; this is what let a blank config reach a device")
control("D2.4", "console.warn" not in code_only("console.warn(`[config] Missing ${name}.`)"))

check("D2.5 the Supabase client dereferences the one reader",
      bool(re.search(r"import\s*\{[^}]*\bENV\b[^}]*\}\s*from\s*'@/config/env'", read(SUPABASE))),
      "src/api/supabase.ts must read ENV from src/config/env.ts, so the throw happens first")
control("D2.5", bool(re.search(r"import\s*\{[^}]*\bENV\b[^}]*\}\s*from\s*'@/config/env'",
                               "const url = Constants.expoConfig.extra.supabaseUrl")))


# ── D3 · the delivery rule is pure, dereferences the declaration, and is tested ───────────────────
rule_src = read(RULE)
tests_src = read(TESTS)

check("D3.1 the delivery rule has a home", RULE.exists(), f"{RULE} is missing")
for name in ("profileEnv", "missingRequiredExtra", "knownProfiles", "REQUIRED_EXTRA"):
    check(f"D3.2 the rule exports {name}", f"{name}" in rule_src.split("module.exports")[-1],
          f"{RULE} must export {name}")
control("D3.2", "profileEnv" in "module.exports = { somethingElse }".split("module.exports")[-1])

check("D3.3 the rule READS the declaration rather than restating it",
      "require('../config/required-extra.json')" in rule_src)
control("D3.3", "require('../config/required-extra.json')" in
        "const REQUIRED_EXTRA = { apiUrl: 'EXPO_PUBLIC_API_URL' }")

# Pure on purpose: it decides what every delivery path ships, so it must be provable in CI with no
# Mac, no EAS account and no network.
rule_requires = re.findall(r"require\('([^']+)'\)", rule_src)
check("D3.4 the rule reaches nothing but the declaration",
      rule_requires == ["../config/required-extra.json"],
      f"scripts/delivery-env.js must stay pure; it requires {rule_requires}")
control("D3.4", re.findall(r"require\('([^']+)'\)",
                           "const { execSync } = require('node:child_process')")
        == ["../config/required-extra.json"])

check("D3.5 the rule is tested", TESTS.exists(), f"{TESTS} is missing")
for name in ("profileEnv", "missingRequiredExtra"):
    check(f"D3.6 the tests exercise {name}", name in tests_src)
control("D3.6", "missingRequiredExtra" in "it('works', () => expect(profileEnv(EAS, 'x')).toEqual({}))")

check("D3.7 the test runner collects scripts/",
      "'scripts/**/*.test.ts'" in read(VITEST),
      "mobile/vitest.config.ts must include scripts/**/*.test.ts or the rule is proved by nothing")
control("D3.7", "'scripts/**/*.test.ts'" in "include: ['src/**/*.test.ts', 'plugins/**/*.test.ts']")


# ── D4 · the runners dereference the rule, and the gate actually refuses ──────────────────────────
wrapper_src = read(WRAPPER)
gate_src = read(GATE)

check("D4.1 the wrapper exists", WRAPPER.exists(), f"{WRAPPER} is missing")
check("D4.2 the wrapper READS profileEnv from the rule",
      "profileEnv" in wrapper_src and "require('./delivery-env.js')" in wrapper_src)
control("D4.2", "profileEnv" in "const env = JSON.parse(process.env.EAS_ENV)")

# Spawning rather than printing shell exports: nothing is quoted, escaped or eval'd, so a value
# containing a space, a quote or a `$` cannot change the command that runs.
check("D4.3 the wrapper spawns the command rather than building a shell string",
      "spawn(" in wrapper_src and not re.search(r"\bexec\(|\beval\(", code_only(wrapper_src)))
control("D4.3", "spawn(" in "exec(`${cmd} ${args.join(' ')}`)")

check("D4.4 the gate exists", GATE.exists(), f"{GATE} is missing")
check("D4.5 the gate READS missingRequiredExtra from the rule",
      "missingRequiredExtra" in gate_src and "require('./delivery-env.js')" in gate_src)
control("D4.5", "missingRequiredExtra" in "if (!config.extra.supabaseUrl) process.exit(1)")

check("D4.6 the gate evaluates the real app config",
      "expo" in gate_src and "'config'" in gate_src,
      "check-extra.mjs must run `expo config` — reading eas.json alone would not catch a config "
      "that drops the value on the way through app.config.ts")
control("D4.6", "'config'" in "const extra = require('../eas.json').build.production.env")

check("D4.7 the gate fails the job when something is blank", "process.exit(1)" in gate_src)
control("D4.7", "process.exit(1)" in "console.warn('missing!')")


# ── D5 · every delivery path is gated, and none of them holds a second copy ───────────────────────
workflow_files = sorted(WORKFLOWS.glob("*.yml")) if WORKFLOWS.exists() else []
check("D5.1 the workflows directory was found", bool(workflow_files), f"{WORKFLOWS} is empty")

# DERIVED, then compared with the declared set — the §CORE-10 rule. A delivery workflow that appears
# without being declared here fails the build; so does a declared one that stops delivering.
# `eas build:list` / `eas update:list` are READ-ONLY listings (mobile-ota-diagnose.yml is nothing
# but those), so the colon form is deliberately not a delivery. Comment lines are stripped first:
# this file's own workflows discuss `eas update` in prose, and prose must not count as a delivery.
DELIVERS = re.compile(r"\beas\s+(build|update)(?![:\w])")


def logical_lines(src: str) -> list[str]:
    """YAML lines with `#` comments dropped and backslash continuations joined into one line."""
    kept = [line for line in src.splitlines() if not line.lstrip().startswith("#")]
    joined: list[str] = []
    for line in kept:
        if joined and joined[-1].rstrip().endswith("\\"):
            joined[-1] = joined[-1].rstrip()[:-1] + " " + line.strip()
        else:
            joined.append(line)
    return joined


def delivery_commands(src: str, verb: str) -> list[str]:
    pattern = re.compile(rf"\beas\s+{verb}(?![:\w])")
    return [line for line in logical_lines(src) if pattern.search(line)]


derived = {p.name for p in workflow_files
           if any(DELIVERS.search(line) for line in logical_lines(read(p)))}
check("D5.2 the delivery workflows are exactly the declared ones",
      derived == DECLARED_DELIVERY_WORKFLOWS,
      f"workflows invoking `eas build`/`eas update`: {sorted(derived)}; declared: "
      f"{sorted(DECLARED_DELIVERY_WORKFLOWS)}. A new delivery path must be declared here and gated.")
control("D5.2", {"mobile-ota-update.yml"} == DECLARED_DELIVERY_WORKFLOWS)

for name in sorted(DECLARED_DELIVERY_WORKFLOWS):
    src = read(WORKFLOWS / name)
    check(f"D5.3 {name} runs the parity gate", "scripts/check-extra.mjs" in src,
          f"{name} must run `node scripts/with-profile-env.mjs <profile> node scripts/check-extra.mjs` "
          "before it ships anything")
    # `eas build` applies the profile env itself; `eas update` does not, which is the whole defect.
    # Every publish command in the file must sit on the same command line as the wrapper.
    for invocation in delivery_commands(src, "update"):
        check(f"D5.4 {name} publishes through the wrapper",
              "with-profile-env.mjs" in invocation,
              f"this `eas update` is not run through scripts/with-profile-env.mjs, so it would "
              f"bundle no configuration at all: {invocation.strip()[:120]}")
control("D5.3", "scripts/check-extra.mjs" in "run: eas update --branch production")
control("D5.4", any("with-profile-env.mjs" in c
                    for c in delivery_commands("        run: eas update --branch production\n",
                                               "update")))

# The second-copy ban, stated as code: no workflow may carry an EXPO_PUBLIC_* value of its own.
SECOND_COPY = re.compile(r"EXPO_PUBLIC_[A-Z0-9_]+\s*[:=]\s*\S")
offenders = sorted(p.name for p in workflow_files if SECOND_COPY.search(read(p)))
check("D5.5 no workflow carries a copy of the app's configuration", offenders == [],
      f"these workflows assign an EXPO_PUBLIC_* value: {offenders}. mobile/eas.json is the one "
      "home; dereference it with scripts/with-profile-env.mjs.")
control("D5.5", SECOND_COPY.search("          EXPO_PUBLIC_API_URL: https://api.example") is None)


# ── D6 · eas.json remains the one home, and the shipping profile is populated ─────────────────────
eas: dict = {}
try:
    eas = json.loads(read(EAS_JSON))
except json.JSONDecodeError as exc:
    check("D6.1 mobile/eas.json parses", False, str(exc))
check("D6.1 mobile/eas.json parses", bool(eas))

profile_env = ((eas.get("build") or {}).get(PINNED_PROFILE) or {}).get("env") or {}
check(f"D6.2 the {PINNED_PROFILE} profile declares every required variable",
      set(PINNED_REQUIRED.values()) <= set(profile_env),
      f"mobile/eas.json build.{PINNED_PROFILE}.env declares {sorted(profile_env)}; missing "
      f"{sorted(set(PINNED_REQUIRED.values()) - set(profile_env))}")
control("D6.2", set(PINNED_REQUIRED.values()) <= {"EXPO_PUBLIC_API_URL"})

check(f"D6.3 no required variable in {PINNED_PROFILE} is blank",
      all(str(profile_env.get(v, "")).strip() != "" for v in PINNED_REQUIRED.values()),
      "a declared-but-empty value ships exactly the bundle this harness exists to refuse")
control("D6.3", all(str({"EXPO_PUBLIC_API_URL": ""}.get(v, "")).strip() != ""
                    for v in ["EXPO_PUBLIC_API_URL"]))


# ── report ────────────────────────────────────────────────────────────────────────────────────────
if failures:
    print(f"FAIL — {len(failures)} of {checks} checks", file=sys.stderr)
    for f in failures:
        print(f"  • {f}", file=sys.stderr)
    sys.exit(1)

print(f"PASS — {checks} checks")
print("  the app's build-time configuration has one home, every path that ships this app")
print("  dereferences it, and a bundle that carries none of it cannot be published.")
