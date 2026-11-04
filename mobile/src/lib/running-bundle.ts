// ── "What code is this app actually running?" — the ONE home (index §CORE-29) ──────────────────────
//
// WHY THIS EXISTS. Between 2026-09-11 and 2026-10-31 a merged, published, correct fix did not reach
// an installed phone, and NOTHING anywhere said so. Expo was serving the update; the app kept
// running the bundle built into the binary. expo-updates does that on purpose when an update cannot
// be applied — it reverts to the embedded bundle so the app still opens — and it does it in silence.
// Six weeks were spent guessing because the one surface that knew the truth never showed it.
//
// The class of defect is not "an OTA failed". It is "the app could not say which code it was
// running". So the fix is not a retry: it is a fact, derived in ONE place, that every surface
// wanting to show it DEREFERENCES rather than re-deriving. harness_running_bundle_lock.py fails the
// build if a second derivation appears or if a caller stops reading this module.
//
// This module is deliberately PURE: it takes the facts as an argument and imports nothing. The
// expo-updates and expo-constants reads live at the call site (see useRunningBundle), which is what
// lets the rule be proved in CI with no device, no build and no network.

/** The facts a running app can observe about its own code. All of them may be absent. */
export type RunningBundleFacts = {
  /** expo-updates is compiled in AND configured (Updates.isEnabled). */
  updatesEnabled: boolean
  /** This launch is running the bundle shipped inside the binary (Updates.isEmbeddedLaunch). */
  isEmbeddedLaunch: boolean
  /** The id of the update being run, when it is not the embedded one (Updates.updateId). */
  updateId: string | null
  /** The compatibility key the binary was stamped with (Updates.runtimeVersion). */
  runtimeVersion: string | null
  /** The release channel the binary listens to (Updates.channel). */
  channel: string | null
  /** When the running update was published (Updates.createdAt). */
  createdAt: Date | null
  /** The binary's marketing version (Constants.nativeAppVersion). */
  nativeAppVersion: string | null
  /** The binary's build number (Constants.nativeBuildVersion). */
  nativeBuildVersion: string | null
  /** The Expo SDK the RUNNING BUNDLE was written for (Constants.expoConfig?.sdkVersion). */
  bundleSdkVersion: string | null
}

export type BundleLine = { label: string; value: string }

export type RunningBundleDescription = {
  /** Label/value pairs to render. Never empty, and no value is ever blank. */
  lines: BundleLine[]
  /**
   * True when this build CAN take updates and is nevertheless running the bundle built into it.
   * That is the silent-revert symptom, and it is the only state a user needs to act on.
   */
  behind: boolean
  /** A plain-words explanation when something is wrong, and the empty string when nothing is. */
  note: string
}

/** What a missing fact reads as. Never the empty string: a blank is what hid this for six weeks. */
export const BUNDLE_UNKNOWN = 'not reported'
export const EMBEDDED_LABEL = 'The bundle built into the app'

function said(value: string | null | undefined, fallback: string): string {
  const text = String(value ?? '').trim()
  return text === '' ? fallback : text
}

function shortId(updateId: string | null | undefined): string {
  const text = String(updateId ?? '').trim()
  return text === '' ? '' : text.slice(0, 8)
}

function published(facts: RunningBundleFacts): string {
  if (facts.isEmbeddedLaunch) return 'with the app'
  const at = facts.createdAt
  if (!at || Number.isNaN(at.getTime())) return BUNDLE_UNKNOWN
  // Locale-free and unambiguous: a date a person can read back to us over the phone.
  return at.toISOString().replace('T', ' ').slice(0, 16) + ' UTC'
}

function appBuild(facts: RunningBundleFacts): string {
  const version = said(facts.nativeAppVersion, '')
  const build = said(facts.nativeBuildVersion, '')
  if (version && build) return `${version} (${build})`
  return said(version || build, BUNDLE_UNKNOWN)
}

/**
 * Describe the running code. Three promises, each covered by a test:
 *   1. No value is ever blank — an absent fact says so in words.
 *   2. It never claims an update is running when the embedded bundle is.
 *   3. When the app is behind, it says so AND says why, in words a store manager can act on.
 */
export function describeRunningBundle(facts: RunningBundleFacts): RunningBundleDescription {
  const wired = facts.updatesEnabled && said(facts.channel, '') !== ''
  const behind = wired && facts.isEmbeddedLaunch

  const id = shortId(facts.updateId)
  const running = facts.isEmbeddedLaunch || id === '' ? EMBEDDED_LABEL : `Update ${id}`

  const lines: BundleLine[] = [
    { label: 'Running', value: running },
    { label: 'Published', value: published(facts) },
    { label: 'App build', value: appBuild(facts) },
    { label: 'Bundle SDK', value: said(facts.bundleSdkVersion, BUNDLE_UNKNOWN) },
    { label: 'Runtime', value: said(facts.runtimeVersion, BUNDLE_UNKNOWN) },
    { label: 'Channel', value: said(facts.channel, 'none') },
  ]

  let note = ''
  if (behind) {
    note =
      'This app is set up to receive updates, but it is running the code it was installed with — ' +
      'an update either has not arrived yet or has not been applied. Close the app completely and ' +
      'reopen it twice. If this line does not change, the update is not compatible with this build ' +
      'and a new build is needed; send this screen to support.'
  } else if (!facts.updatesEnabled) {
    note =
      'Over-the-air updates are not set up in this copy of the app, so it will only change when a ' +
      'new version is installed.'
  }

  return { lines, behind, note }
}
