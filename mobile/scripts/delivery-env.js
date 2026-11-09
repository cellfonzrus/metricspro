// ── WHAT EVERY DELIVERY PATH MUST AGREE ON ───────────────────────────────────────────────────────
//
// This app reaches a phone two ways, and until 2026-11-09 only one of them carried the app's
// configuration.
//
//   • `eas build` reads `build.<profile>.env` out of mobile/eas.json by itself. Correct.
//   • `eas update` does NOT, and there is no flag that makes it: `--environment` selects
//     SERVER-SIDE EAS environment variables, which this project does not use. Confirmed against
//     `eas update --help` (eas-cli, 2026-11-09).
//
// So every over-the-air update ever published from this repository was bundled with
// `process.env.EXPO_PUBLIC_*` unset, which makes app.config.ts emit `extra.supabaseUrl: ''`, which
// makes `createClient('')` throw `supabaseUrl is required.` at module scope — the app dies in its
// first second. It went unseen for weeks because the installed binary was on a different Expo
// runtime and was silently rejecting every update; the first build that accepted one crashed
// (TestFlight build 14, reported by the owner 2026-11-09).
//
// THE CLASS: a fact that two delivery paths need, declared in one of them. The fix is not to paste
// the values into the publish workflow as well — that is the second copy the house rules forbid.
// mobile/eas.json stays the ONE home, and this module is how everything else dereferences it.
//
// Pure string and object work on purpose: no network, no Expo, no EAS, no Mac. Proved by
// scripts/delivery-env.test.ts and locked by backend/harness_ota_config_parity.py.

const REQUIRED_EXTRA = require('../config/required-extra.json').required

/** Every build profile mobile/eas.json declares. */
function knownProfiles(easJson) {
  const build = easJson && typeof easJson === 'object' ? easJson.build : undefined
  if (!build || typeof build !== 'object') {
    throw new Error(
      'delivery-env: mobile/eas.json has no `build` section, so no profile can be resolved. ' +
        'The profile environment is read from `build.<profile>.env`.',
    )
  }
  return Object.keys(build)
}

/**
 * The environment mobile/eas.json declares for one build profile — the same values `eas build`
 * applies for that profile, so a publish made with these produces the same bundle a build would.
 *
 * Throws rather than returning `{}`: an empty environment is exactly the failure this module
 * exists to prevent, and a delivery path that silently gets nothing is how it shipped.
 */
function profileEnv(easJson, profile) {
  const profiles = knownProfiles(easJson)
  if (!profiles.includes(profile)) {
    throw new Error(
      `delivery-env: mobile/eas.json declares no build profile ${JSON.stringify(profile)}. ` +
        `It declares: ${profiles.join(', ')}.`,
    )
  }
  const env = easJson.build[profile].env
  if (!env || typeof env !== 'object' || Object.keys(env).length === 0) {
    throw new Error(
      `delivery-env: build profile ${JSON.stringify(profile)} in mobile/eas.json declares no ` +
        '`env`, so a bundle made from it would carry no configuration at all. Declare it there ' +
        '(one home) rather than passing values to the command.',
    )
  }
  return { ...env }
}

/**
 * The environment variables whose `extra` values came out blank — i.e. the reasons this bundle
 * must not be published. Names the variable to set, not the internal key, because the variable is
 * what a person can act on.
 */
function missingRequiredExtra(extra) {
  const got = extra && typeof extra === 'object' ? extra : {}
  return Object.entries(REQUIRED_EXTRA)
    .filter(([key]) => {
      const value = got[key]
      return typeof value !== 'string' || value.trim() === ''
    })
    .map(([, envVar]) => envVar)
}

module.exports = { REQUIRED_EXTRA, knownProfiles, profileEnv, missingRequiredExtra }
