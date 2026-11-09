import { missingRequiredExtra } from '../../scripts/delivery-env.js'

// ── A LAUNCH FAULT IS DESCRIBED, NEVER THROWN ────────────────────────────────────────────────────
//
// THE DEFECT THIS EXISTS FOR, and it is the one that turned a one-line slip into a dead app. On
// 2026-11-06 a published bundle carried no Supabase address, `createClient('')` threw while the
// bundle was still being evaluated, and the app died about a second after launch.
//
// The fatal part was not the blank value. It was the THROW. `expo-updates` downloads the next
// bundle in the BACKGROUND after launch, so a process that dies in its first second kills its own
// download, every time — the app cannot replace the very bundle that is breaking it, however many
// times it is opened, and the only way out is deleting and reinstalling. I told the owner the
// corrected update would arrive on the next launch; it could not, and that was wrong.
//
// So the rule for the startup path is: NOTHING on it may throw. A fault is a VALUE the UI renders,
// the process stays alive, and expo-updates finishes its download and repairs the app by itself.
//
// Pure on purpose — imports no React, no Expo and no native module — so it is provable in CI with
// no device. The set of required values is DEREFERENCED from config/required-extra.json through
// scripts/delivery-env.js, the same one home the delivery gate reads, never a list of its own.
// Locked by backend/harness_ota_config_parity.py (section S).

export const STARTUP_FAULT_TITLE = 'MetricsPro cannot start'

export type StartupFault = {
  /** The environment variables whose values are missing, named as they are set. */
  missing: string[]
  title: string
  /** Plain words for whoever is holding the phone, safe to screenshot. */
  detail: string
}

/**
 * What is wrong with this bundle's configuration, or `null` when nothing is.
 *
 * Never throws and never returns a blank string: a surface that renders this must always have
 * something to show, because the alternative is the blank screen that hid the first fault.
 */
export function describeStartupFault(extra: unknown): StartupFault | null {
  const missing = missingRequiredExtra(extra)
  if (missing.length === 0) return null
  const names = missing.join(', ')
  return {
    missing,
    title: STARTUP_FAULT_TITLE,
    detail:
      `This copy of the app was published without ${names}, so it cannot reach your data. ` +
      'Nothing is wrong with your phone or your account, and nothing you can do here will fix ' +
      'it. The app will repair itself as soon as a corrected update arrives — leave it open for a ' +
      'minute, then close and open it again.',
  }
}
