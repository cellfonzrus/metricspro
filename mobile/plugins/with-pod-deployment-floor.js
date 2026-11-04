// ── Raise IPHONEOS_DEPLOYMENT_TARGET on every pod CocoaPods builds ────────────────────────────────
//
// WHY THIS EXISTS, and why `expo-build-properties` alone was not enough. Two builds failed on
// 2026-10-31 (EAS `d4d6b18d`, then `d34458a2` WITH `ios.deploymentTarget: '15.1'` already set) with
// the same three errors:
//
//   IPHONEOS_DEPLOYMENT_TARGET is set to 9.0  … target 'RNCAsyncStorage-RNCAsyncStorage_resources'
//   IPHONEOS_DEPLOYMENT_TARGET is set to 12.0 … target 'ReachabilitySwift-ReachabilitySwift'
//   IPHONEOS_DEPLOYMENT_TARGET is set to 9.0  … target 'SDWebImage-SDWebImage'
//   the range of supported deployment target versions is 15.0 to 27.1.x
//
// `ios.deploymentTarget` sets the Podfile's `platform :ios` and the APP target. It does not touch the
// targets CocoaPods GENERATES for each pod and for each pod's resource bundle — those inherit the
// pod's own declared minimum, and all three of these libraries still declare iOS 9 or 12. Expo's
// Podfile template gained a post-install that rewrites them, but not in SDK 52, which is what this
// app is pinned to. So the lever exists; this app is simply too old to have it.
//
// This plugin backports exactly that post-install, and nothing else. It is NOT a workaround invented
// here: it is what a current Expo Podfile already does for itself, which is also why it becomes
// redundant — and should be DELETED — the moment the app is upgraded off SDK 52 (index §CORE-29.3b).
//
// It is deliberately a `withDangerousMod` on the Podfile rather than a patch to a vendored `ios/`
// directory: the app is managed and EAS prebuilds it on every build, so there is no checked-in
// Podfile to edit and a vendored one would have to be maintained by hand forever.
//
// IT MUST EXTEND THE EXISTING HOOK, NOT ADD A SECOND ONE. The first version of this plugin appended
// its own `post_install do |installer|` at the end of the Podfile. That removed the Xcode errors and
// broke the build one phase earlier instead — EAS `3037ddb8` failed in "Install pods". Expo's
// Podfile ALREADY declares a `post_install` inside the target block (it is where
// `react_native_post_install` runs and where resource-bundle code signing is turned off), and a
// Podfile with two of them does not install. Verified by running `npx expo prebuild --platform ios`
// in this repository and counting: two hooks, lines 47 and 70. So the loop is injected INSIDE the
// existing block, and `exactlyOneHook` below is asserted by both the unit tests and the harness.

const { withDangerousMod } = require('expo/config-plugins')
const fs = require('fs')
const path = require('path')

// The marker makes the mod idempotent. A prebuild that runs twice (EAS does this on some retries)
// must not stack two copies of the hook, and a missing marker is how we would notice if a future
// Expo version changed the Podfile enough that the anchor below stopped matching.
const MARKER = '# with-pod-deployment-floor'

// The last line of Expo's own post_install block — the anchor we inject before. Deliberately a
// narrow, distinctive string: if a future Expo template changes it, `raiseEveryPodFloor` throws and
// the build fails LOUDLY at prebuild rather than silently shipping a Podfile without the floor.
const HOOK_OPENER = 'post_install do |installer|'

/** The Ruby that raises the floor on every generated target, indented to sit inside the hook. */
function floorLoop(floor) {
  return [
    '',
    `    ${MARKER} — every pod and every generated resource bundle, not just the app target.`,
    '    # Backports what a current Expo Podfile does for itself; delete this with the SDK upgrade.',
    '    installer.pods_project.targets.each do |target|',
    '      target.build_configurations.each do |config|',
    "        current = config.build_settings['IPHONEOS_DEPLOYMENT_TARGET']",
    `        if current.nil? || current.to_f < ${floor}`,
    `          config.build_settings['IPHONEOS_DEPLOYMENT_TARGET'] = '${floor}'`,
    '        end',
    '      end',
    '    end',
  ].join('\n')
}

function raiseEveryPodFloor(podfile, floor) {
  // Idempotent: EAS re-runs prebuild on some retries, and the loop must not be injected twice.
  if (podfile.includes(MARKER)) return podfile

  const at = podfile.indexOf(HOOK_OPENER)
  if (at === -1) {
    throw new Error(
      `with-pod-deployment-floor: no \`${HOOK_OPENER}\` in the Podfile. Expo's template has ` +
        'changed; inject the floor into whatever replaced it rather than adding a second hook, ' +
        'which does not install (EAS 3037ddb8).',
    )
  }
  // Insert immediately after the opener line, so the loop runs inside the EXISTING hook.
  const endOfLine = podfile.indexOf('\n', at)
  const cut = endOfLine === -1 ? podfile.length : endOfLine
  return podfile.slice(0, cut) + floorLoop(floor) + podfile.slice(cut)
}

/** How many `post_install` hooks the Podfile declares. More than one and CocoaPods will not run. */
function countHooks(podfile) {
  return podfile.split(HOOK_OPENER).length - 1
}

/**
 * @param {import('expo/config').ExpoConfig} config
 * @param {{ floor?: string }} props
 */
module.exports = function withPodDeploymentFloor(config, props = {}) {
  const floor = props.floor || '15.1'
  return withDangerousMod(config, [
    'ios',
    async (cfg) => {
      const podfile = path.join(cfg.modRequest.platformProjectRoot, 'Podfile')
      // A missing Podfile is a real condition worth failing on, not something to paper over: it
      // means prebuild did not produce an iOS project and the build would fail later and vaguer.
      if (!fs.existsSync(podfile)) {
        throw new Error(`with-pod-deployment-floor: no Podfile at ${podfile}`)
      }
      fs.writeFileSync(podfile, raiseEveryPodFloor(fs.readFileSync(podfile, 'utf8'), floor))
      return cfg
    },
  ])
}

// Exported for the unit test, which proves the rule without a build, a Mac or a network.
module.exports.raiseEveryPodFloor = raiseEveryPodFloor
module.exports.countHooks = countHooks
module.exports.MARKER = MARKER
module.exports.HOOK_OPENER = HOOK_OPENER
