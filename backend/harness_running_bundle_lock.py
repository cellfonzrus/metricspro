#!/usr/bin/env python3
"""An app can always say which code it is running (index §CORE-29) — DB-free, the real files.

THE DEFECT THIS LOCKS. Between 2026-09-11 and 2026-10-31 a merged, published, correct mobile fix did
not reach an installed phone and nothing anywhere said so: Expo was serving the update, the app kept
running the bundle built into the binary, and expo-updates does that in silence by design. Six weeks
went into guessing because the one surface that knew the truth never showed it.

THE CLASS, not the instance. The class is not "an over-the-air update failed". It is "the running app
could not state which code it was running". So the fix is a FACT with one home that every surface
dereferences, and this harness refuses the three ways that un-wires:

  * the rule stops being pure, so it can no longer be proved without a device;
  * a surface derives "which bundle am I on" for itself instead of reading the one home;
  * a value on that surface is allowed to be blank again, which is what hid this in the first place.

It also PINS the over-the-air compatibility policy. `runtimeVersion` is the only thing standing
between a build and an update it cannot run, so changing it must be a deliberate edit to this file
and not a quiet edit to a config. Same tripwire discipline as harness_db_resilience.py.

Every check carries a negative control: the check is re-run against text carrying the defect and must
FAIL there, so a check that has silently stopped looking is itself a failure.
"""
from __future__ import annotations

import pathlib
import re
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
MOBILE = ROOT / "mobile"

RULE = MOBILE / "src/lib/running-bundle.ts"
READS = MOBILE / "src/lib/useRunningBundle.ts"
TESTS = MOBILE / "src/lib/running-bundle.test.ts"
SETTINGS = MOBILE / "app/(app)/(tabs)/settings.tsx"
APP_CONFIG = MOBILE / "app.config.ts"

# The over-the-air compatibility policy, pinned. 'appVersion' ties the key to the hand-typed
# `version`, which carries no information about the native runtime: EAS will then SERVE an update a
# binary cannot run, and expo-updates discards it in silence. 'fingerprint' hashes the real native
# dependency set and refuses instead. Either is a defensible choice; neither may change by accident,
# so moving it means editing this literal and saying why in the PR.
PINNED_RUNTIME_POLICY = "appVersion"

# The minimum iOS the app declares, pinned for a different reason: EAS builds on a current Xcode,
# which REFUSES any pod declaring a deployment target below 15.0, and this app's dependency set still
# carries three that declare 9.0 and 12.0 (RNCAsyncStorage, ReachabilitySwift, SDWebImage). The first
# build from GitHub died on exactly that (2026-10-31, EAS d4d6b18d).
#
# CORRECTED after the SECOND failure (EAS d34458a2). `ios.deploymentTarget` raises the Podfile's
# platform and the APP target ONLY — it does NOT touch the targets CocoaPods generates per pod and
# per resource bundle, which inherit the pod's own declared minimum. The second build failed on the
# same three pods WITH this line already set. Reaching them takes the Podfile post-install that a
# CURRENT Expo template performs for itself and SDK 52's does not: plugins/with-pod-deployment-floor.js.
#
# BOTH halves are pinned, because losing either does not fail a test — it fails the BUILD, twenty
# minutes later, on Expo's machines, with an error that looks like a dependency problem.
PINNED_IOS_DEPLOYMENT_TARGET = "15.1"

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


# ── R1 · both halves exist, and the reading half dereferences the rule ────────────────────────────
rule_src = read(RULE)
reads_src = read(READS)
tests_src = read(TESTS)
settings_src = read(SETTINGS)
config_src = read(APP_CONFIG)

check("R1.1 the rule has a home", RULE.exists(), f"{RULE} is missing")
check("R1.2 the rule exports describeRunningBundle",
      "export function describeRunningBundle(" in rule_src)
check("R1.3 the rule exports the no-blank sentinel", "export const BUNDLE_UNKNOWN" in rule_src)
check("R1.4 the fact-gathering half exists", READS.exists(), f"{READS} is missing")

DEREF = re.compile(r"from\s+'\./running-bundle'")
check("R1.5 the gathering half READS the rule rather than restating it",
      bool(DEREF.search(reads_src)),
      "useRunningBundle.ts must import describeRunningBundle from ./running-bundle")
control("R1.5", bool(DEREF.search("const d = myOwnDescribe(facts)")))


# ── R2 · the rule stays pure, so it can be proved in CI with no device ───────────────────────────
rule_imports = re.findall(r"^\s*import\s[^\n]*?from\s+'([^']+)'", rule_src, re.M)
check("R2.1 the rule imports nothing", rule_imports == [],
      f"running-bundle.ts must stay dependency-free; it imports {rule_imports}")
control("R2.1", re.findall(r"^\s*import\s[^\n]*?from\s+'([^']+)'",
                           "import * as Updates from 'expo-updates'", re.M) == [])

check("R2.2 the rule is tested", TESTS.exists(), f"{TESTS} is missing")
# The three promises the rule makes, each named in the tests so a deleted promise is visible.
for phrase in ("never renders a blank value", "running the bundle that shipped",
               "behind", "separately, so a mismatch"):
    check(f"R2.3 the tests still cover: {phrase}", phrase in tests_src,
          "a promise of the rule lost its test")
control("R2.3", "never renders a blank value" in "describe('x', () => { it('works') })")


# ── R3 · no value may be blank again ─────────────────────────────────────────────────────────────
# The rule's own last resorts, read out of the source, must all be non-empty strings. A blank last
# resort is the original defect wearing a hat.
sentinels = re.findall(r"export const (BUNDLE_UNKNOWN|EMBEDDED_LABEL)\s*=\s*'([^']*)'", rule_src)
check("R3.1 the rule declares both last resorts", len(sentinels) == 2,
      f"expected BUNDLE_UNKNOWN and EMBEDDED_LABEL, found {[s[0] for s in sentinels]}")
for name, value in sentinels:
    check(f"R3.2 {name} is not blank", value.strip() != "",
          "an absent fact must read as words, never as an empty string")
control("R3.2", "" .strip() != "")

# `said()` is the one funnel every value passes through; its fallback argument may never be ''.
blank_fallbacks = re.findall(r"said\([^,]+,\s*''\s*\)", rule_src)
# Two uses of said(x, '') are legitimate INSIDE appBuild, which then funnels the pair through
# said(version || build, BUNDLE_UNKNOWN). Assert that funnel is still there rather than banning the
# idiom, so the check cannot be satisfied by renaming.
check("R3.4 the build line still funnels through the no-blank sentinel",
      "said(version || build, BUNDLE_UNKNOWN)" in rule_src,
      f"appBuild must end at BUNDLE_UNKNOWN; found {len(blank_fallbacks)} bare '' fallbacks")
control("R3.4", "said(version || build, BUNDLE_UNKNOWN)" in "return version || build")


# ── R4 · the surface dereferences, and does not read the native modules itself ───────────────────
check("R4.1 Settings shows the running bundle", "runningBundle" in settings_src,
      "the Settings screen must render the one home's description")
check("R4.2 Settings renders the rule's own lines", "bundle.lines.map" in settings_src,
      "render the lines the rule produced; do not pick fields out and re-label them")
check("R4.3 Settings surfaces the note", "bundle.note" in settings_src)
NATIVE_IMPORT_EARLY = re.compile(r"^\s*import\s[^\n]*?from\s+'expo-(updates|constants)'", re.M)
check("R4.4 Settings does not read the native modules itself",
      NATIVE_IMPORT_EARLY.search(settings_src) is None,
      "only useRunningBundle.ts may ask expo-updates / expo-constants about the running code")
control("R4.4", NATIVE_IMPORT_EARLY.search("import * as U from 'expo-updates'") is None)


# ── R5 · no second copy anywhere in the app ──────────────────────────────────────────────────────
SOURCES = sorted(
    p for p in list(MOBILE.glob("src/**/*.ts")) + list(MOBILE.glob("src/**/*.tsx"))
    + list(MOBILE.glob("app/**/*.ts")) + list(MOBILE.glob("app/**/*.tsx"))
    if "node_modules" not in p.parts
)
check("R5.0 the app's sources were found", len(SOURCES) > 20, f"only {len(SOURCES)} files scanned")

NATIVE_IMPORT = re.compile(r"^\s*import\s[^\n]*?from\s+'expo-updates'", re.M)
native_readers = [p for p in SOURCES if NATIVE_IMPORT.search(read(p))]
check("R5.1 exactly one file imports expo-updates",
      native_readers == [READS],
      "the one home for the native reads is useRunningBundle.ts; also found "
      + ", ".join(str(p.relative_to(MOBILE)) for p in native_readers if p != READS))

own_derivation = [
    p for p in SOURCES
    if p not in (RULE, READS, TESTS) and re.search(r"isEmbeddedLaunch|EMBEDDED_LABEL", read(p))
]
check("R5.2 nobody derives 'am I on the embedded bundle' for themselves",
      own_derivation == [],
      "re-derived in " + ", ".join(str(p.relative_to(MOBILE)) for p in own_derivation))
control("R5.1", NATIVE_IMPORT.search("import * as U from 'expo-updates'") is None)
control("R5.2", not re.search(r"isEmbeddedLaunch|EMBEDDED_LABEL",
                              "const stale = Updates.isEmbeddedLaunch"))


# ── R6 · the over-the-air compatibility policy is declared once, and pinned ──────────────────────
policies = re.findall(r"runtimeVersion:\s*\{\s*policy:\s*'([^']+)'\s*\}", config_src)
check("R6.1 the runtime policy is declared exactly once", len(policies) == 1,
      f"found {len(policies)} declarations of runtimeVersion in app.config.ts")
check("R6.2 the runtime policy still matches its pin",
      policies[:1] == [PINNED_RUNTIME_POLICY],
      f"app.config.ts declares {policies!r} but this harness pins {PINNED_RUNTIME_POLICY!r}. "
      "Changing the policy changes which builds can receive an update: re-verify the consequence, "
      "then move PINNED_RUNTIME_POLICY and say why in the PR.")
control("R6.2", re.findall(r"runtimeVersion:\s*\{\s*policy:\s*'([^']+)'\s*\}",
                           "runtimeVersion: { policy: 'nativeVersion' }") == [PINNED_RUNTIME_POLICY])

check("R6.3 the updates URL is still declared", "updates: { url:" in config_src,
      "without updates.url nothing is ever offered to the app")

targets = re.findall(r"deploymentTarget:\s*'([^']+)'", config_src)
check("R6.4 the iOS deployment floor is declared exactly once", len(targets) == 1,
      f"found {len(targets)} ios.deploymentTarget declarations in app.config.ts")
check("R6.5 the iOS deployment floor still matches its pin",
      targets[:1] == [PINNED_IOS_DEPLOYMENT_TARGET],
      f"app.config.ts declares {targets!r} but this harness pins "
      f"{PINNED_IOS_DEPLOYMENT_TARGET!r}. Below 15.0 the build fails on Expo's current Xcode; "
      "raising it drops older iPhones. Either way, say why in the PR and move the pin.")
control("R6.5", re.findall(r"deploymentTarget:\s*'([^']+)'", "deploymentTarget: '12.0'")
        == [PINNED_IOS_DEPLOYMENT_TARGET])
check("R6.6 the plugin that applies the floor is still installed",
      "expo-build-properties" in config_src
      and '"expo-build-properties"' in (MOBILE / "package.json").read_text(encoding="utf-8"),
      "ios.deploymentTarget does nothing without the expo-build-properties config plugin")
control("R6.6", "expo-build-properties" in "plugins: ['expo-router']")

# R6.7 — the half expo-build-properties does not reach. Without this plugin the three old pods keep
# their own iOS 9 / 12 floors and the build dies in fastlane, which is what happened twice.
POD_FLOOR_PLUGIN = MOBILE / "plugins/with-pod-deployment-floor.js"
plugin_src = read(POD_FLOOR_PLUGIN)
check("R6.7 the per-pod floor plugin exists", POD_FLOOR_PLUGIN.exists(),
      f"{POD_FLOOR_PLUGIN} is missing; the app target's floor alone does not reach the pods")
check("R6.8 the app registers the per-pod floor plugin",
      "./plugins/with-pod-deployment-floor" in config_src,
      "a plugin that is not in app.config.ts `plugins` never runs")
control("R6.8", "./plugins/with-pod-deployment-floor" in "plugins: ['expo-router']")

floors = re.findall(r"floor:\s*'([^']+)'", config_src)
check("R6.9 the per-pod floor matches the app's own floor",
      floors[:1] == [PINNED_IOS_DEPLOYMENT_TARGET],
      f"the plugin is given {floors!r} while the app declares "
      f"{PINNED_IOS_DEPLOYMENT_TARGET!r}; two floors that disagree is the duplicate defect")
control("R6.9", re.findall(r"floor:\s*'([^']+)'", "{ floor: '12.0' }")
        == [PINNED_IOS_DEPLOYMENT_TARGET])

# It must RAISE, never lower: a pod declaring iOS 16 keeps 16, or its own code could break.
check("R6.10 the plugin only raises a floor", "current.to_f <" in plugin_src,
      "the post-install must compare before writing, so a higher pod floor is preserved")
# And it must be idempotent: EAS re-runs prebuild on some retries, and two stacked post_install
# hooks is a Podfile that does not parse.
check("R6.11 the plugin is idempotent", "MARKER" in plugin_src and "includes(MARKER)" in plugin_src,
      "a second prebuild must not stack a second post_install hook")
plugin_tests = read(MOBILE / "plugins/with-pod-deployment-floor.test.ts")
check("R6.12 the plugin's rule is tested", plugin_tests != "",
      "the Podfile rule is pure string work and must be proved without a Mac")
control("R6.10", "current.to_f <" in "config.build_settings['X'] = '15.1'")

# R6.13-R6.15 — the regression that cost build 3. The first version of this plugin APPENDED its own
# `post_install do |installer|`, which removed the Xcode errors and broke the build one phase
# earlier: EAS 3037ddb8 died in "Install pods", because Expo's Podfile already declares one inside
# the target block and CocoaPods will not run with two. Verified by generating the real Podfile with
# `npx expo prebuild --platform ios` — two hooks, lines 47 and 70.
emits_a_hook = re.search(r"'post_install do \|installer\|',", plugin_src)
check("R6.13 the plugin never WRITES a post_install hook",
      emits_a_hook is None,
      "the floor must be injected into Expo's existing hook; a second hook does not install")
control("R6.13", re.search(r"'post_install do \|installer\|',",
                           "    'post_install do |installer|',") is None)

check("R6.14 the plugin injects at the existing hook",
      "HOOK_OPENER" in plugin_src and "indexOf(HOOK_OPENER)" in plugin_src,
      "find Expo's own post_install and inject inside it")
check("R6.15 the plugin THROWS when the anchor is gone",
      "throw new Error(" in plugin_src,
      "a template change must fail loudly at prebuild, never ship a Podfile without the floor")
check("R6.16 the one-hook promise is the test's own assertion",
      "countHooks" in plugin_tests and "toBe(1)" in plugin_tests,
      "the regression needs a test that counts hooks, not just one that checks the loop exists")
control("R6.16", "countHooks" in "expect(out).toContain('targets.each')")


if failures:
    print(f"FAIL — {len(failures)} of {checks} checks failed\n")
    for f in failures:
        print(f"  ✗ {f}")
    sys.exit(1)

print(f"PASS — {checks} checks")
print("  the running bundle has one home, the surface dereferences it, no value can be blank,")
print(f"  and the over-the-air compatibility policy is pinned at {PINNED_RUNTIME_POLICY!r}.")
