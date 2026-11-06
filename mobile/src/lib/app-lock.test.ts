/**
 * THE APP LOCK'S DECISION TABLE — the one home for "is the app locked, and may we prompt again".
 *
 * REPORTED 2026-11-05: *"The touch login from the phone is not working."* There is no biometric
 * SIGN-IN in this app (the Supabase session lives in the Keychain, so the phone stays signed in);
 * the only Face ID / Touch ID surface is the app LOCK in Settings. What it did was worse than not
 * working: it could not be dismissed.
 *
 * THE DEFECT, AS A CLASS. The auto-prompt lived in a `useEffect` whose dependency list named the
 * unlock callback, and that callback was a `useCallback` over `checking`. So every attempt changed
 * its identity twice — once on start, once on finish — and the finish re-ran the effect, which
 * prompted again. Cancel Face ID and it came straight back, forever, over an overlay with no other
 * button. On a phone whose factor cannot succeed at all (nothing enrolled and no passcode) that is a
 * permanent lock-out of a signed-in app, escapable only by deleting it.
 *
 * The class is not "one dep array was wrong". It is that WHETHER TO PROMPT was derived from React
 * identities instead of from the lock's own state. So the answer moves here, to a reducer that can
 * be driven through the whole sequence with no device, no renderer and no timers, and the screen
 * becomes a dumb reader of it. `app-lock-gate-lock.test.ts` fails the build if the gate grows its
 * own copy of any of these decisions again.
 */
import { describe, expect, it } from 'vitest'

import {
  APP_LOCK_GRACE_MS,
  APP_LOCK_SIGN_OUT_AFTER_FAILURES,
  appLockReducer,
  canAttemptAppLock,
  describeDeviceFactor,
  initialAppLockState,
  shouldAutoPromptAppLock,
  shouldOfferAppLockSignOut,
  shouldShowAppLockUi,
  type AppLockEvent,
  type AppLockState,
} from './app-lock'

/** Drive the reducer through a sequence, the way the screen does. */
const run = (events: AppLockEvent[], from: AppLockState = initialAppLockState): AppLockState =>
  events.reduce(appLockReducer, from)

/** The state the app is in a moment after a cold start with the lock turned on. */
const LOCKED = run([
  { type: 'authStatus', signedIn: true },
  { type: 'settingRead', enabled: true },
])

describe('the lock itself', () => {
  it('does not lock an app whose owner never turned the lock on', () => {
    const s = run([
      { type: 'authStatus', signedIn: true },
      { type: 'settingRead', enabled: false },
    ])
    expect(shouldShowAppLockUi(s)).toBe(false)
    expect(shouldAutoPromptAppLock(s)).toBe(false)
  })

  it('locks a cold start once the owner is known to be signed in', () => {
    expect(shouldShowAppLockUi(LOCKED)).toBe(true)
    expect(shouldAutoPromptAppLock(LOCKED)).toBe(true)
  })

  it('never shows the overlay over the sign-in screen', () => {
    const s = run([{ type: 'settingRead', enabled: true }])
    expect(s.signedIn).toBe(false)
    expect(shouldShowAppLockUi(s)).toBe(false)
  })

  it('turning the lock off in Settings releases a lock that is already on', () => {
    const s = appLockReducer(LOCKED, { type: 'settingRead', enabled: false })
    expect(shouldShowAppLockUi(s)).toBe(false)
  })
})

describe('the grace period', () => {
  it('does not lock for a quick app-switch under the grace period', () => {
    const s = run(
      [
        { type: 'attemptStarted' },
        { type: 'attemptFinished', ok: true },
        { type: 'backgrounded', at: 1_000 },
        { type: 'foregrounded', at: 1_000 + APP_LOCK_GRACE_MS - 1 },
      ],
      LOCKED,
    )
    expect(shouldShowAppLockUi(s)).toBe(false)
  })

  it('locks again once the app has been away longer than the grace period', () => {
    const s = run(
      [
        { type: 'attemptStarted' },
        { type: 'attemptFinished', ok: true },
        { type: 'backgrounded', at: 1_000 },
        { type: 'foregrounded', at: 1_000 + APP_LOCK_GRACE_MS + 1 },
      ],
      LOCKED,
    )
    expect(shouldShowAppLockUi(s)).toBe(true)
    expect(shouldAutoPromptAppLock(s)).toBe(true)
  })

  it('a foreground with no recorded background never locks (a resumed splash is not a return)', () => {
    const unlocked = run([{ type: 'attemptStarted' }, { type: 'attemptFinished', ok: true }], LOCKED)
    const s = appLockReducer(unlocked, { type: 'foregrounded', at: 9_999_999 })
    expect(shouldShowAppLockUi(s)).toBe(false)
  })

  it('does not arm the grace clock while signed out', () => {
    const s = run([
      { type: 'settingRead', enabled: true },
      { type: 'backgrounded', at: 1_000 },
      { type: 'foregrounded', at: 1_000 + APP_LOCK_GRACE_MS + 1 },
      { type: 'authStatus', signedIn: true },
    ])
    // Locked because signing in locks, not because of the time away.
    expect(s.awayAt).toBeNull()
  })
})

describe('the prompt fires once per lock, which is the reported defect', () => {
  it('prompts once and not again while that attempt is in flight', () => {
    const s = appLockReducer(LOCKED, { type: 'attemptStarted' })
    expect(shouldAutoPromptAppLock(s)).toBe(false)
    expect(canAttemptAppLock(s)).toBe(false)
  })

  it('does NOT re-prompt after the owner cancels Face ID — the overlay waits for a tap', () => {
    const cancelled = run([{ type: 'attemptStarted' }, { type: 'attemptFinished', ok: false }], LOCKED)
    expect(shouldShowAppLockUi(cancelled)).toBe(true)
    expect(shouldAutoPromptAppLock(cancelled)).toBe(false) // the loop that trapped the phone
    expect(canAttemptAppLock(cancelled)).toBe(true) // ... but Unlock still works
  })

  it('cannot be made to re-prompt by cancelling over and over', () => {
    let s = LOCKED
    for (let i = 0; i < 20; i += 1) {
      s = run([{ type: 'attemptStarted' }, { type: 'attemptFinished', ok: false }], s)
      expect(shouldAutoPromptAppLock(s)).toBe(false)
    }
    expect(shouldShowAppLockUi(s)).toBe(true)
  })

  it('a successful factor unlocks and clears the attempt history', () => {
    const s = run([{ type: 'attemptStarted' }, { type: 'attemptFinished', ok: true }], LOCKED)
    expect(shouldShowAppLockUi(s)).toBe(false)
    expect(s.failures).toBe(0)
    expect(s.attempting).toBe(false)
  })

  it('prompts again for the NEXT lock, because that is a new episode', () => {
    const s = run(
      [
        { type: 'attemptStarted' },
        { type: 'attemptFinished', ok: false },
        { type: 'backgrounded', at: 1_000 },
        { type: 'foregrounded', at: 1_000 + APP_LOCK_GRACE_MS + 1 },
      ],
      LOCKED,
    )
    expect(shouldAutoPromptAppLock(s)).toBe(true)
  })

  it('an attemptFinished nobody started cannot unlock the app', () => {
    const s = appLockReducer(LOCKED, { type: 'attemptFinished', ok: true })
    expect(shouldShowAppLockUi(s)).toBe(true)
  })
})

describe('no phone is ever trapped', () => {
  it('offers a way out once the factor has failed enough times to look broken', () => {
    expect(shouldOfferAppLockSignOut(LOCKED)).toBe(false)
    let s = LOCKED
    for (let i = 0; i < APP_LOCK_SIGN_OUT_AFTER_FAILURES; i += 1) {
      s = run([{ type: 'attemptStarted' }, { type: 'attemptFinished', ok: false }], s)
    }
    expect(shouldOfferAppLockSignOut(s)).toBe(true)
  })

  it('a device with no usable factor at all still reaches the way out', () => {
    // hasFactor:false is what getBiometricSupport reports for no hardware AND no passcode: every
    // attempt returns false immediately, so the owner must be told rather than re-prompted.
    let s = appLockReducer(LOCKED, { type: 'factorSupport', hasFactor: false })
    expect(shouldAutoPromptAppLock(s)).toBe(false) // nothing to prompt with
    expect(shouldOfferAppLockSignOut(s)).toBe(true)
    s = run([{ type: 'attemptStarted' }, { type: 'attemptFinished', ok: false }], s)
    expect(shouldShowAppLockUi(s)).toBe(true)
  })

  it('signing out from the overlay drops the lock with it', () => {
    const s = appLockReducer(LOCKED, { type: 'authStatus', signedIn: false })
    expect(shouldShowAppLockUi(s)).toBe(false)
    expect(s.failures).toBe(0)
  })
})

describe('the lock is device-local, so it carries no tenant anything', () => {
  it('has no org, tenant or carrier input in its state at all', () => {
    // The all-tenants directive: a feature must not have one tenant's vocabulary behind a default.
    // This one is answerable with no org at all, which is the strongest form of that — pinned so a
    // later change cannot quietly make the lock depend on who the signed-in company is.
    expect(Object.keys(initialAppLockState).join(' ')).not.toMatch(/org|tenant|carrier|company/i)
  })
})

describe('what factor this handset has', () => {
  it('a Face ID iPhone with Face ID enrolled', () => {
    expect(describeDeviceFactor({ enrolledLevel: 3, faceId: true, fingerprint: false })).toEqual({
      hasFactor: true,
      label: 'Face ID',
    })
  })

  it('a fingerprint phone with a print enrolled', () => {
    expect(describeDeviceFactor({ enrolledLevel: 3, faceId: false, fingerprint: true })).toEqual({
      hasFactor: true,
      label: 'Touch ID',
    })
  })

  it('a phone with a sensor it has never enrolled falls back to the PASSCODE, not to nothing', () => {
    // The old read said hasHardware:true and labelled it "Passcode", which was right, but it also
    // said "has a factor" purely from the hardware — on a phone with neither print nor passcode
    // that is a promise it cannot keep.
    expect(describeDeviceFactor({ enrolledLevel: 1, faceId: true, fingerprint: false })).toEqual({
      hasFactor: true,
      label: 'Passcode',
    })
  })

  it('a passcode-only phone with NO biometric sensor can still lock', () => {
    // The direction `hasHardwareAsync()` got wrong the other way: no sensor, but the passcode
    // fallback authenticates, so the lock works and must be offered.
    expect(describeDeviceFactor({ enrolledLevel: 1, faceId: false, fingerprint: false })).toEqual({
      hasFactor: true,
      label: 'Passcode',
    })
  })

  it('a phone with nothing enrolled and no passcode has NO factor', () => {
    expect(describeDeviceFactor({ enrolledLevel: 0, faceId: true, fingerprint: true })).toEqual({
      hasFactor: false,
      label: 'None',
    })
  })

  it('an unknown weak biometric level is still a factor', () => {
    expect(describeDeviceFactor({ enrolledLevel: 2, faceId: false, fingerprint: false })).toEqual({
      hasFactor: true,
      label: 'Biometrics',
    })
  })
})
