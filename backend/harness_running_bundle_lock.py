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

# The App Store Connect app a production build is submitted to, DECLARED rather than merely required
# to look plausible. A bare "it is some digits" check would pass a different app's id, and submitting
# this binary to the wrong record is not a mistake a size check should let through — which is exactly
# what happened: the first value here, 94734403, was a different number from the same App Store
# Connect page and Apple refused the submission with "App Store Connect app for application
# identifier 94734403 was not found". Its error then NAMED the right one, against the right bundle:
#   Example applications visible to this API key:
#   - Metricspro (com.metricspro.app) (ID: 6804841051)
# Not a secret: it is the app's public App Store Connect identifier.
PINNED_ASC_APP_ID = "6804841051"

# THE TOOLCHAIN THIS APP IS BUILT WITH, DECLARED — never "latest". This is the pin the owner's THIRD
# crash report bought, and the drift it refuses is the whole story behind §CORE-29.3b and §CORE-38.
#
# Both iOS profiles asked EAS for `"image": "latest"`, so the toolchain moved whenever Expo's did and
# nothing in this repository recorded that it had. It moved to Xcode 27 — and an app built with the
# iOS 27 SDK that has not adopted the UIScene lifecycle is KILLED AT LAUNCH by UIKit, deliberately,
# before a line of JavaScript runs:
#
#   exception: EXC_BREAKPOINT (SIGTRAP)
#   0  UIKitCore  ___UIApplicationEvaluateRuntimeIssueForNoSceneLifecycleAdoption_block_invoke
#   3  UIKitCore  -[UIApplication workspace:didCreateScene:withTransitionContext:completion:]
#
# (TestFlight build 14, `DTAppStoreToolsBuild: 27B52`, iPhone OS 27.0.1, owner report 2026-11-12.)
# Expo SDK 52's iOS template does not adopt scenes, so EVERY build of this app made with Xcode 27
# traps. The same source built with Xcode 26 launches on the same phone — build 9 (2026-09-16) did.
# The trap is keyed to the SDK the binary was BUILT with, not to the iOS version on the device, which
# is why reading the app's own code could never find it and three diagnoses from the repository alone
# were wrong.
#
# Xcode 26 is also the floor Apple requires for App Store Connect uploads from 2026-04-28, so this
# pin is the one value that clears the trap AND stays submittable.
#
# NOT THE WHOLE FIX, and §CORE-29.3c says so: Apple will raise that floor again, and the durable
# answer is the Expo SDK upgrade, whose template adopts the scene lifecycle. A manifest-only adoption
# does NOT satisfy UIKit's check — a real scene delegate is required — so this is not a config line
# that can be added to an SDK 52 app.
PINNED_IOS_BUILD_IMAGE = "macos-sequoia-15.6-xcode-26.2"

# Image names that MOVE. Pinning exists to refuse exactly these: this app's toolchain may not change
# because somebody else shipped.
FLOATING_IMAGE_NAMES = ("latest", "default", "stable")

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

# R6.17 — the build button submits unattended, or it is a button that builds and stops. The fourth
# build SUCCEEDED and then failed at `eas submit` with "Set ascAppId in the submit profile"
# (2026-10-31), because mobile/eas.json's submit.production carried only `android.track`. An
# interactive submit from a Mac never writes it, so it stayed missing until a non-interactive one
# needed it. No secret: it is the app's public App Store Connect identifier.
import json as _json
try:
    eas = _json.loads((MOBILE / "eas.json").read_text(encoding="utf-8"))
except Exception as exc:  # a malformed eas.json is itself a build failure
    eas = {}
    check("R6.17 eas.json parses", False, f"{exc}")
asc = str(((eas.get("submit") or {}).get("production") or {}).get("ios", {}).get("ascAppId") or "")
check("R6.17 the iOS submit profile names the App Store Connect app we mean",
      asc == PINNED_ASC_APP_ID,
      f"submit.production.ios.ascAppId is {asc!r} but this harness declares {PINNED_ASC_APP_ID!r}. "
      "Without it a non-interactive `eas submit` cannot run; with the WRONG one the binary goes to "
      "another app's record. If the app record really changed, move the declaration and say why.")
control("R6.17", str({}.get("ascAppId") or "") == PINNED_ASC_APP_ID)


# ── R7 · the toolchain is DECLARED, and cannot drift ──────────────────────────────────────────────
ios_profiles = {name: (prof.get("ios") or {})
                for name, prof in (eas.get("build") or {}).items()}

# DERIVED from eas.json and compared with a DECLARED set, per §CORE-10: a new build profile cannot
# inherit a floating toolchain unnoticed, because its appearance fails this check first.
DECLARED_IOS_PROFILES = {"development", "preview", "production"}
check("R7.1 the iOS build profiles are exactly the declared ones",
      set(ios_profiles) == DECLARED_IOS_PROFILES,
      f"mobile/eas.json declares profiles {sorted(ios_profiles)}; this harness declares "
      f"{sorted(DECLARED_IOS_PROFILES)}. A new profile must be added here and given a pinned image.")
control("R7.1", {"production"} == DECLARED_IOS_PROFILES)

# `development` runs on a developer's own machine and reaches no tester, so it is excused BY NAME
# rather than by silence. Every profile that makes a binary somebody else installs must pin.
SHIPPING_IOS_PROFILES = ("preview", "production")
for profile_name in SHIPPING_IOS_PROFILES:
    image = str((ios_profiles.get(profile_name) or {}).get("image") or "")
    check(f"R7.2 the {profile_name} profile pins its build image",
          image == PINNED_IOS_BUILD_IMAGE,
          f"mobile/eas.json build.{profile_name}.ios.image is {image!r} but this harness pins "
          f"{PINNED_IOS_BUILD_IMAGE!r}. An app built with the iOS 27 SDK is killed at launch by UIKit "
          "until it adopts the UIScene lifecycle, which Expo SDK 52 does not.")
control("R7.2", "latest" == PINNED_IOS_BUILD_IMAGE)

floating = sorted(name for name, ios in ios_profiles.items()
                  if str(ios.get("image") or "") in FLOATING_IMAGE_NAMES)
check("R7.3 no iOS profile asks for a moving toolchain", floating == [],
      f"these profiles name a floating image: {floating}. This app's toolchain may not change "
      "because somebody else shipped — that is what killed build 14.")
control("R7.3", sorted(n for n, i in {"production": {"image": "latest"}}.items()
                       if str(i.get("image") or "") in FLOATING_IMAGE_NAMES) == [])

# Xcode 26 is both the floor Apple requires for uploads and the last version without the trap, so the
# pin must name 26 specifically: a pin that drifted forward to 27 would be "declared" and still dead.
check("R7.4 the pinned image names Xcode 26", "xcode-26" in PINNED_IOS_BUILD_IMAGE,
      f"{PINNED_IOS_BUILD_IMAGE!r} must name Xcode 26 — 27 traps at launch, and anything below 26 is "
      "refused by App Store Connect from 2026-04-28.")
control("R7.4", "xcode-26" in "macos-tahoe-26.5-xcode-27.0")


# ── R8 · the build REPORTS what built it, and refuses to submit when it disagrees ─────────────────
#
# R7 pins the toolchain, and a pin is a REQUEST. It is not evidence that the binary leaving for
# TestFlight was built with what was asked for, and the drift that caused §CORE-29.3c was invisible
# precisely because no build this repository ever made said what it had been built with. So the
# binary is read, not the config: `DTXcode` in the built Info.plist is the answer.
#
# ONE HOME: mobile/scripts/binary-toolchain.py. The workflow dereferences it rather than carrying its
# own plist reader, and this section refuses the ways that un-wires.
TOOLCHAIN_READER = MOBILE / "scripts/binary-toolchain.py"
BUILD_WORKFLOW = ROOT / ".github/workflows/mobile-build-and-submit.yml"
reader_src = read(TOOLCHAIN_READER)
build_wf = read(BUILD_WORKFLOW)

check("R8.1 the toolchain reader exists", bool(reader_src),
      f"{TOOLCHAIN_READER.relative_to(ROOT)} is missing — there is nothing that can say what built a "
      "binary, so a pin cannot be verified against the thing it produced.")
control("R8.1", bool(""))

check("R8.2 the reader reads the BINARY, not the config",
      "plistlib" in reader_src and "DTXcode" in reader_src,
      "the reader must open the built Info.plist and read DTXcode. Anything derived from eas.json "
      "re-states the request and proves nothing about the answer.")
control("R8.2", "plistlib" in 'image = eas["build"]["production"]["ios"]["image"]')

# The reader's rule is PURE and provable here, so the pipeline's correctness does not wait on a Mac.
# DTXcode drops the dot: Xcode 26.2 is 2620, 27.0 is 2700, and a future 100.1 would be 10010.
_reader_ns: dict = {}
try:
    exec(compile(reader_src, str(TOOLCHAIN_READER), "exec"), _reader_ns)
except Exception as exc:  # a reader that cannot be imported cannot be proved
    check("R8.3 the reader's rule can be proved without a device", False, f"{exc}")
    _xcode_major = None
else:
    _xcode_major = _reader_ns.get("xcode_major")

if _xcode_major is None:
    check("R8.3 the reader's rule can be proved without a device", False,
          "xcode_major is not a module-level function, so the rule cannot be exercised here")
else:
    cases = {"2620": "26", "2700": "27", "1620": "16", "10010": "100"}
    wrong = {k: _xcode_major(k) for k, v in cases.items() if _xcode_major(k) != v}
    check("R8.3 the reader's rule can be proved without a device", wrong == {},
          f"xcode_major misread {wrong} — DTXcode is the version with its dot removed, so the major "
          f"is every digit but the last two.")
    control("R8.3", _xcode_major("2620") == "2")

    # An unreadable DTXcode must read as UNKNOWN, never as a number that happens to compare equal.
    # Zero and "" are the plausible-looking answers that hide this class (owner directive 2026-10-30).
    blanks = [v for v in ("", "?", "26", "abc", "  ") if _xcode_major(v) != ""]
    check("R8.4 an unreadable DTXcode reads as unknown, not as a number", blanks == [],
          f"xcode_major returned a value for {blanks} — a binary whose toolchain cannot be read must "
          "stop the submission, not pass a comparison by accident.")
    # The defect is a rule that answers anyway. "" or "0" is exactly the plausible-looking number
    # that would compare equal to nothing and let the submission through.
    _lenient = lambda value: value[:-2] or "0"  # noqa: E731 — a defective stand-in, not a helper
    control("R8.4", [v for v in ("", "?", "26", "abc", "  ") if _lenient(v) != ""] == [])

check("R8.5 the reader REFUSES on a mismatch",
      "--expect-xcode" in reader_src and "return 1" in reader_src,
      "reporting a mismatch is not enough: a submission cannot be recalled, so the reader must exit "
      "non-zero and stop the job.")
control("R8.5", "--expect-xcode" in "print(facts)")

# The workflow must DEREFERENCE the one home rather than carry a second copy of the rule.
check("R8.6 the build workflow dereferences the reader",
      "scripts/binary-toolchain.py" in build_wf,
      f"{BUILD_WORKFLOW.relative_to(ROOT)} must run the reader. A plist parsed inline in YAML is the "
      "second copy CLAUDE.md forbids.")
control("R8.6", "scripts/binary-toolchain.py" in "python3 - <<'PY'\nimport plistlib\nPY")

check("R8.7 the workflow carries no second plist reader",
      "plistlib" not in build_wf,
      f"{BUILD_WORKFLOW.relative_to(ROOT)} parses a plist itself. One fact, one home, dereferenced.")
control("R8.7", "plistlib" not in "import plistlib")

# ORDER IS THE GATE. A failing step aborts its job, so a check that runs AFTER the submission would
# report a dead binary that had already left for Apple.
_wf_steps = [line for line in build_wf.splitlines() if line.strip().startswith("- name:")]
_names = [line.split("- name:", 1)[1].strip() for line in _wf_steps]
try:
    _i_tool = _names.index("what actually built it")
    _i_submit = _names.index("send it to testers")
    _ordered = _i_tool < _i_submit
except ValueError:
    _ordered = False
check("R8.8 the toolchain check runs BEFORE the submission", _ordered,
      f"step order is {_names} — 'what actually built it' must precede 'send it to testers', or a "
      "binary iOS will kill at launch reaches testers before anybody reads it.")
control("R8.8", ["send it to testers", "what actually built it"].index("what actually built it")
        < ["send it to testers", "what actually built it"].index("send it to testers"))

# And the result must reach the OWNER, who reads the issue comment and not the job summary.
check("R8.9 the result says what built it where the owner will read it",
      "steps.toolchain.outputs.dtxcode" in build_wf,
      "the posted report must carry the Xcode the binary was built with. The whole defect was a "
      "toolchain nobody could see.")
control("R8.9", "steps.toolchain.outputs.dtxcode" in "echo '- build step: ok'")

# R8.10 — the REGRESSION, end to end, with no device and no network. A source-text lock proves the
# pipeline is wired; it does not prove the wiring works. So an .ipa's real layout is built here — a
# BINARY plist, the format Apple actually ships, which a naive XML reader cannot open — and the
# reader is run against the exact defect that shipped: a binary built with Xcode 27 while eas.json
# asked for 26. It must REFUSE, and it must accept the binary that is correct.
import plistlib as _plistlib
import tempfile as _tempfile
import contextlib as _contextlib
import io as _io

_main = _reader_ns.get("main") if _xcode_major is not None else None
if _main is None:
    check("R8.10 the reader refuses the binary that actually shipped (end to end)", False,
          "main() is not a module-level function, so the pipeline cannot be exercised here")
else:
    def _ipa(dtxcode: str, sdk: str) -> str:
        """An unpacked .ipa carrying a real binary Info.plist."""
        root = _tempfile.mkdtemp()
        app = pathlib.Path(root) / "Payload" / "MetricsPro.app"
        app.mkdir(parents=True)
        with open(app / "Info.plist", "wb") as fh:
            _plistlib.dump({"DTXcode": dtxcode, "DTXcodeBuild": "x", "DTSDKName": sdk,
                            "DTPlatformVersion": sdk.replace("iphoneos", ""),
                            "MinimumOSVersion": PINNED_IOS_DEPLOYMENT_TARGET,
                            "CFBundleVersion": "16", "CFBundleShortVersionString": "0.1.0"},
                           fh, fmt=_plistlib.FMT_BINARY)
        return root

    def _run(dtxcode: str, sdk: str, want: str) -> int:
        with _contextlib.redirect_stdout(_io.StringIO()), _contextlib.redirect_stderr(_io.StringIO()):
            return _main([_ipa(dtxcode, sdk), "--expect-xcode", want])

    _pinned_major = (re.search(r"xcode-(\d+)", PINNED_IOS_BUILD_IMAGE) or [None, ""])[1]
    _shipped = _run("2700", "iphoneos27.0", _pinned_major)   # build 14, the one that died on launch
    _correct = _run("2620", "iphoneos26.2", _pinned_major)   # what the pin asks for
    check("R8.10 the reader refuses the binary that actually shipped (end to end)",
          _shipped == 1 and _correct == 0,
          f"against the pinned Xcode {_pinned_major!r}: a DTXcode 2700 binary exited {_shipped} "
          f"(must be 1) and a DTXcode 2620 binary exited {_correct} (must be 0). This is the defect "
          "that reached the owner's phone three times; it may not pass.")
    control("R8.10", _run("2700", "iphoneos27.0", "27") == 1 and _run("2620", "iphoneos26.2", "27") == 0)

    # A binary whose plist cannot be read must STOP the submission, not be waved through. "Nothing
    # declared means nothing identified, and the surface says so" (owner directive 2026-10-30).
    _unreadable = _run("", "iphoneos26.2", _pinned_major)
    check("R8.11 a binary whose toolchain cannot be read is refused", _unreadable == 1,
          f"an empty DTXcode exited {_unreadable} — a blank is the plausible-looking answer that "
          "hid this class, so it must refuse rather than compare against nothing.")
    control("R8.11", _run("2620", "iphoneos26.2", _pinned_major) == 1)



if failures:
    print(f"FAIL — {len(failures)} of {checks} checks failed\n")
    for f in failures:
        print(f"  ✗ {f}")
    sys.exit(1)

print(f"PASS — {checks} checks")
print("  the running bundle has one home, the surface dereferences it, no value can be blank,")
print(f"  and the over-the-air compatibility policy is pinned at {PINNED_RUNTIME_POLICY!r}.")
