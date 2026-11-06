// ── The app lock's decision table ────────────────────────────────────────────────────────────────
// THE ONE HOME for "is the signed-in app locked, may we raise the Face ID / Touch ID prompt, may
// the owner retry, and have we trapped them". The screen (`src/auth/AppLockGate.tsx`) owns the
// pixels and the native call; every decision below is read from here.
//
// WHY IT LIVES OUTSIDE THE SCREEN (reported 2026-11-05, "the touch login from the phone is not
// working"). The auto-prompt used to be a `useEffect` that listed the unlock callback among its
// dependencies, and that callback was a `useCallback` over the in-flight flag. Finishing an attempt
// changed its identity, which re-ran the effect, which prompted again — so cancelling Face ID
// brought it straight back, over an overlay that had no other button. Deriving "may we prompt" from
// React identities is the defect; deriving it from the lock's own state is the fix, and keeping that
// state here is what makes the whole sequence provable with no device and no renderer.
//
// It is deliberately org-free. The lock is a property of the handset, not of the company signed in
// on it, so there is no tenant vocabulary to declare and none to get wrong.

/** Don't re-prompt for a quick app-switch: a glance at the clock is not leaving the app. */
export const APP_LOCK_GRACE_MS = 60_000

/**
 * Consecutive failures after which the overlay must offer a way out (sign out) instead of only an
 * Unlock button. A factor that has failed this often is not being cancelled by accident, and a
 * signed-in app with no escape is only escapable by deleting the app.
 */
export const APP_LOCK_SIGN_OUT_AFTER_FAILURES = 3

export type AppLockState = {
  /** The owner's setting, as read from the device. */
  enabled: boolean
  /** Only a signed-in app is ever locked; the sign-in screen is never covered. */
  signedIn: boolean
  locked: boolean
  /**
   * Bumped every time the app BECOMES locked. "One prompt per lock" is expressed as "promptedFor
   * has caught up with episode", which no React identity can accidentally reset.
   */
  episode: number
  promptedFor: number | null
  /** An attempt is in flight: the native prompt is on screen. */
  attempting: boolean
  /** Consecutive failures within the current lock. */
  failures: number
  /** When the app was last backgrounded, for the grace period. */
  awayAt: number | null
  /**
   * Whether the device has ANY usable factor (biometric enrolled, or a device passcode). `null`
   * until asked. `false` means no attempt can ever succeed, so prompting is pointless and the way
   * out must be offered immediately.
   */
  hasFactor: boolean | null
}

export type AppLockEvent =
  /** The stored setting came back from the device. */
  | { type: 'settingRead'; enabled: boolean }
  /** `getBiometricSupport()` came back. */
  | { type: 'factorSupport'; hasFactor: boolean }
  /** Auth resolved, or the owner signed in / out. */
  | { type: 'authStatus'; signedIn: boolean }
  | { type: 'backgrounded'; at: number }
  | { type: 'foregrounded'; at: number }
  /** The native prompt is being raised (auto or by a tap on Unlock). */
  | { type: 'attemptStarted' }
  | { type: 'attemptFinished'; ok: boolean }

export const initialAppLockState: AppLockState = {
  enabled: false,
  signedIn: false,
  locked: false,
  episode: 0,
  promptedFor: null,
  attempting: false,
  failures: 0,
  awayAt: null,
  hasFactor: null,
}

/** Enter a NEW lock episode: prompt-once is re-armed and the failure count starts over. */
const lock = (s: AppLockState): AppLockState => ({
  ...s,
  locked: true,
  episode: s.episode + 1,
  promptedFor: null,
  attempting: false,
  failures: 0,
  awayAt: null,
})

const unlock = (s: AppLockState): AppLockState => ({
  ...s,
  locked: false,
  attempting: false,
  failures: 0,
  awayAt: null,
})

export function appLockReducer(s: AppLockState, e: AppLockEvent): AppLockState {
  switch (e.type) {
    case 'settingRead': {
      const next = { ...s, enabled: e.enabled }
      if (!e.enabled) return unlock(next)
      return next.signedIn && !next.locked ? lock(next) : next
    }

    case 'factorSupport':
      return { ...s, hasFactor: e.hasFactor }

    case 'authStatus': {
      if (!e.signedIn) return unlock({ ...s, signedIn: false })
      const next = { ...s, signedIn: true }
      return next.enabled && !next.locked ? lock(next) : next
    }

    case 'backgrounded':
      // Only arm the clock for an app that could be locked on return.
      if (!s.enabled || !s.signedIn) return s
      return { ...s, awayAt: e.at }

    case 'foregrounded': {
      if (!s.enabled || !s.signedIn || s.awayAt === null) return { ...s, awayAt: null }
      const away = e.at - s.awayAt
      return away > APP_LOCK_GRACE_MS ? lock(s) : { ...s, awayAt: null }
    }

    case 'attemptStarted':
      // Guard a double fire (a tap landing on top of the auto-prompt) so the native prompt is never
      // raised twice, and mark this episode as prompted whichever of the two got here first.
      if (!s.locked || s.attempting) return s
      return { ...s, attempting: true, promptedFor: s.episode }

    case 'attemptFinished':
      // An attempt nobody started cannot unlock the app.
      if (!s.attempting) return s
      if (e.ok) return unlock(s)
      // THE FIX: a failed or cancelled attempt leaves the app locked and `promptedFor` alone, so
      // nothing re-prompts. The owner taps Unlock when they are ready.
      return { ...s, attempting: false, failures: s.failures + 1 }

    default:
      return s
  }
}

// ── Selectors: the screen asks, it never decides ─────────────────────────────────────────────────

/** Cover the UI? Only a signed-in app with the lock on and a live lock. */
export const shouldShowAppLockUi = (s: AppLockState): boolean => s.enabled && s.locked && s.signedIn

/** Raise the native prompt without being asked? Once per lock episode, and never with no factor. */
export const shouldAutoPromptAppLock = (s: AppLockState): boolean =>
  shouldShowAppLockUi(s) && !s.attempting && s.promptedFor !== s.episode && s.hasFactor !== false

/** May the owner tap Unlock? Whenever nothing is in flight. */
export const canAttemptAppLock = (s: AppLockState): boolean => !s.attempting

/** Show the way out: a factor that cannot work, or one that keeps refusing. */
export const shouldOfferAppLockSignOut = (s: AppLockState): boolean =>
  s.hasFactor === false || s.failures >= APP_LOCK_SIGN_OUT_AFTER_FAILURES

// ── What factor this handset actually has ────────────────────────────────────────────────────────
// The SAME question, one home. Asking `hasHardwareAsync()` alone gets this wrong in both directions:
// a phone with a fingerprint sensor and nothing enrolled reports hardware and cannot authenticate,
// and a passcode-only Android with no sensor reports none while `authenticateAsync` would succeed on
// the passcode fallback. `getEnrolledLevelAsync()` is the signal that answers it, so the mapping from
// its level to "can this phone unlock, and what do we call the factor" lives here and is tested.

/** Mirrors expo-local-authentication's `SecurityLevel`, kept as plain numbers so this module stays
 *  importable in a test runner with no native modules. */
export const FACTOR_LEVEL_NONE = 0
export const FACTOR_LEVEL_SECRET = 1

export type DeviceFactorFacts = {
  /** `SecurityLevel` from `getEnrolledLevelAsync()`. */
  enrolledLevel: number
  faceId: boolean
  fingerprint: boolean
}

export type DeviceFactor = {
  /** False means no attempt can ever succeed: nothing enrolled AND no device passcode. */
  hasFactor: boolean
  label: 'Face ID' | 'Touch ID' | 'Biometrics' | 'Passcode' | 'None'
}

export function describeDeviceFactor(f: DeviceFactorFacts): DeviceFactor {
  if (f.enrolledLevel <= FACTOR_LEVEL_NONE) return { hasFactor: false, label: 'None' }
  if (f.enrolledLevel === FACTOR_LEVEL_SECRET) return { hasFactor: true, label: 'Passcode' }
  if (f.faceId) return { hasFactor: true, label: 'Face ID' }
  if (f.fingerprint) return { hasFactor: true, label: 'Touch ID' }
  return { hasFactor: true, label: 'Biometrics' }
}
